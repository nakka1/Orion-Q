"""Explicit, authenticated mission reception; recovery never reopens a socket."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from ..control_plane.sqlite_transaction import connect


def identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", value):
        raise ValueError("explicit " + label + " identifier required")
    return value


class MissionReceiverController:
    def __init__(self, engine):
        self.engine, self.server, self.store, self.error = engine, None, None, None
        with closing(connect(engine.journal_path)) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='network_mission_receiver'"
            ).fetchone()
            row = (
                None
                if not table
                else connection.execute(
                    "SELECT configuration_json FROM network_mission_receiver WHERE topology_id=?",
                    (engine.topology_id,),
                ).fetchone()
            )
        if row is not None:
            try:
                self.store = self._store(json.loads(row["configuration_json"]))
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
                self.error = type(exc).__name__

    def _store(self, config):
        from ..quantum_protocols.mission_ingress import MissionFrameStore

        binding = hashlib.sha256(
            (self.engine.topology_id + "\0" + config["source_id"] + "\0" + config["origin"]).encode()
        ).hexdigest()[:16]
        path = Path(self.engine.journal_path)
        return MissionFrameStore(
            path.with_name(path.name + ".satellite-" + binding + ".sqlite"),
            source_id=config["source_id"],
            origin=config["origin"],
            allowed_satellite_ids=config["satellite_ids"],
            allowed_mission_bindings=config.get("mission_bindings"),
        )

    def descriptor(self):
        if self.store is None:
            return (
                None
                if self.error is None
                else {
                    "state": "unavailable",
                    "error_type": self.error,
                    "latest": None,
                    "listener_active": False,
                    "physical_adapter_validated": False,
                }
            )
        active = self.server is not None and self.server.thread.is_alive()
        snapshot = (
            self.store.presentation_snapshot()
            if hasattr(self.store, "presentation_snapshot")
            else self.store.snapshot()
        )
        return {
            **snapshot,
            "listener_active": active,
            "transport_state": "listening" if active else "stopped",
            "url": f"ws://127.0.0.1:{self.server.port}/satellite/v1/" if active else None,
            "physical_actuation": False,
        }

    def provision(self, payload):
        security = self.engine.security
        if security is None:
            raise ValueError("common security runtime required for satellite sessions")
        if set(payload) - {"source_id", "session_reference", "ttl_seconds"}:
            raise ValueError("unknown satellite session configuration field")
        security.authorize("security.manage", "security:satellite_gateway")
        source_id = identifier(payload.get("source_id", "ogs_gateway"), "satellite source")
        reference = payload.get("session_reference", "orion-ogs-session")
        if not isinstance(reference, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", reference):
            raise ValueError("vault session reference must be a simple identifier")
        ttl = payload.get("ttl_seconds", 3600)
        if type(ttl) not in (int, float) or not 1 <= ttl <= 86400:
            raise ValueError("satellite session lifetime must be 1..86400 seconds")
        if (source_id == "ogs_gateway" and self.engine._ogs_server is not None) or (
            self.server is not None and self.store.source_id == source_id
        ):
            raise ValueError("stop this receiver before replacing its gateway session")
        subject = "service:satellite:" + source_id
        security.grant_identity(subject, ["operator"], ["source:" + source_id + "/*"])
        token = security.issue_service_session(subject, ttl_seconds=ttl)
        security.vault.put_bytes(reference, token.encode("ascii"))
        return {
            "status": "provisioned",
            "source_id": source_id,
            "subject": subject,
            "credential_ref": "vault:" + reference,
            "ttl_seconds": ttl,
            "physical_action_performed": False,
        }

    def configure(self, payload):
        from ..quantum_protocols.mission_ingress import MissionWebSocketServer

        if self.engine.security is None:
            raise ValueError("common authenticated security runtime required for mission ingress")
        if self.server is not None:
            raise ValueError("mission receiver already listening; stop before changing configuration")
        if set(payload) - {"kind", "source_id", "origin", "satellite_ids", "credential_ref", "port"}:
            raise ValueError("unknown mission receiver configuration field")
        source_id = identifier(payload.get("source_id", "mission_gateway"), "mission source")
        origin = payload.get("origin", "physical")
        if origin not in ("physical", "measurement_file", "simulated"):
            raise ValueError("explicit mission origin required")
        ids = payload.get("satellite_ids")
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 256
            or any(not isinstance(x, str) for x in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("configure 1..256 unique registered satellite identifiers")
        configured = {
            node.node_id: node.telemetry.get("communicator", {})
            for node in self.engine._read_topology().nodes
            if node.telemetry.get("communicator", {}).get("physical_type") == "satellite"
        }
        if any(item not in configured for item in ids):
            raise ValueError("mission source must be bound to registered satellite communicators")
        bindings = {}
        from ..quantum_protocols.satellite_missions import default_registry

        registry = default_registry()
        for item in ids:
            record = configured[item]
            mission = record.get("mission", {})
            if not mission or "classical_telemetry" not in record.get("capabilities", []):
                raise ValueError("satellite mission binding and classical_telemetry capability required")
            mode = record.get("integration", {}).get("mode")
            compatible = {
                "physical": ("physical", "measurement_stream"),
                "measurement_file": ("measurement_file",),
                "simulated": ("simulated",),
            }
            if mode not in compatible[origin]:
                raise ValueError("mission origin must match registered satellite integration mode")
            decoder = registry.resolve(mission["mission_id"])
            if mission["decoder_profile"] not in decoder.profiles:
                raise ValueError("registered mission decoder profile unavailable")
            bindings[item] = dict(mission)
        port = payload.get("port", 8766)
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("mission listener port must be an integer in 0..65535")
        config = {
            "source_id": source_id,
            "origin": origin,
            "satellite_ids": ids,
            "mission_bindings": bindings,
        }
        store = self._store(config)
        server = MissionWebSocketServer(
            store,
            time.time,
            port=port,
            security=self.engine.security,
            credential_ref=payload.get("credential_ref", ""),
        )
        try:
            with closing(connect(self.engine.journal_path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS network_mission_receiver "
                    "(topology_id TEXT PRIMARY KEY, configuration_json TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO network_mission_receiver VALUES(?,?) ON CONFLICT(topology_id) "
                    "DO UPDATE SET configuration_json=excluded.configuration_json",
                    (self.engine.topology_id, json.dumps(config, sort_keys=True, allow_nan=False)),
                )
        except Exception:
            server.close()
            raise
        self.store, self.server, self.error = store, server, None
        return {
            "status": "listening",
            **config,
            "url": f"ws://127.0.0.1:{server.port}/satellite/v1/",
            "physical_adapter_validated": False,
            "physical_action_performed": False,
        }

    def close(self):
        if self.server is not None:
            self.server.close()
            self.server = None

    def guard_communicator_change(self, payload):
        if self.server is None:
            return
        replacement = payload.get("record", {})
        node_id = replacement.get("node_id")
        if node_id not in self.store.allowed_satellite_ids:
            return
        current = self.engine._read_topology().node(node_id).telemetry.get("communicator", {})

        def binding(record):
            return (
                record.get("mission"),
                record.get("integration", {}).get("mode"),
                "classical_telemetry" in record.get("capabilities", []),
            )

        if binding(current) != binding(replacement):
            raise ValueError("stop mission reception before changing satellite identity/source binding")
