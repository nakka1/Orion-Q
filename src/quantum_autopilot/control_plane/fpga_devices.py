"""Identified FPGA acquisition adapters and durable, supervised software devices.

The simulated adapter transports the running source's raw receiver acquisitions;
it never generates a node state, QBER, key rate, or a controller result. The
engine then uses its ordinary paired classical/quantum intake and LinkBank.
The physical adapter is an explicit unavailable contract, with no SDK, socket,
register write, bitstream programming, or physical actuation implementation.
"""
from __future__ import annotations

from collections import deque
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from uuid import uuid4

from .channels import ChannelPair, identifier
from .sqlite_transaction import connect


SCHEMA = "orion.fpga-devices.v1"
CAPABILITIES = ("raw_receiver_acquisition", "classical_envelope_v1", "bounded_telemetry",
                "configuration_readback", "simulated_fault_injection")
FAULTS = ("timeout", "disconnect", "invalid_payload", "overrun")
MAX_DEVICES = 32
MAX_RECEIPTS = 500


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _number(value, name, lower, upper):
    if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"{name} must be finite in [{lower}, {upper}]")


class FpgaDeviceError(ValueError):
    pass


class FpgaNoResponse(FpgaDeviceError):
    pass


@dataclass(frozen=True)
class FpgaDeviceConfiguration:
    device_id: str
    node_id: str
    label: str = "FPGA acquisition device"
    adapter_id: str = "simulated"
    enabled: bool = True
    connected: bool = True
    clock_hz: float = 100_000_000.0
    pipeline_cycles: int = 256
    timeout_s: float = 2.0
    retry_ticks: int = 3
    auto_reconnect: bool = True
    revision: int = 1
    schema: str = SCHEMA

    def __post_init__(self):
        identifier(self.device_id)
        identifier(self.node_id)
        if not isinstance(self.label, str) or not 1 <= len(self.label.strip()) <= 160:
            raise ValueError("device label must contain 1..160 characters")
        if self.adapter_id not in ("simulated", "physical-contract") or self.schema != SCHEMA:
            raise ValueError("unknown FPGA adapter or configuration schema")
        for field in ("enabled", "connected", "auto_reconnect"):
            if type(getattr(self, field)) is not bool:
                raise ValueError(f"{field} must be boolean")
        _number(self.clock_hz, "clock_hz", 1_000, 1_000_000_000)
        _number(self.timeout_s, "timeout_s", .01, 30)
        for field, maximum in (("pipeline_cycles", 1_000_000_000), ("retry_ticks", 120), ("revision", 2**31 - 1)):
            if type(getattr(self, field)) is not int or not 1 <= getattr(self, field) <= maximum:
                raise ValueError(f"{field} must be an integer in [1, {maximum}]")

    @classmethod
    def from_dict(cls, record):
        if not isinstance(record, dict):
            raise ValueError("FPGA configuration must be an object")
        try:
            return cls(**record)
        except TypeError as error:
            raise ValueError("unknown or missing FPGA configuration fields") from error

    def to_dict(self):
        return asdict(self)


class FpgaDeviceRegistry:
    """Versioned configurations and hash-bound receipts in the existing journal."""
    def __init__(self, path, topology_id):
        self.path, self.topology_id = Path(path), identifier(topology_id)
        with closing(connect(self.path)) as connection, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS fpga_devices (
                  topology_id TEXT NOT NULL, device_id TEXT NOT NULL,
                  revision INTEGER NOT NULL, configuration_json TEXT NOT NULL,
                  configuration_sha256 TEXT NOT NULL,
                  PRIMARY KEY(topology_id,device_id));
                CREATE TABLE IF NOT EXISTS fpga_device_receipts (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT, topology_id TEXT NOT NULL,
                  device_id TEXT NOT NULL, tick INTEGER NOT NULL, kind TEXT NOT NULL,
                  at_utc TEXT NOT NULL, record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS fpga_receipts_recent
                  ON fpga_device_receipts(topology_id,sequence DESC);
            """)

    def configurations(self):
        with closing(connect(self.path)) as connection:
            rows = connection.execute("SELECT * FROM fpga_devices WHERE topology_id=? ORDER BY device_id",
                                      (self.topology_id,)).fetchall()
        records = []
        for row in rows:
            record = json.loads(row["configuration_json"])
            if _digest(record) != row["configuration_sha256"]:
                raise FpgaDeviceError("persisted FPGA configuration fails its hash")
            config = FpgaDeviceConfiguration.from_dict(record)
            if (config.device_id, config.revision) != (row["device_id"], row["revision"]):
                raise FpgaDeviceError("FPGA configuration index differs from its record")
            records.append(config)
        return records

    def save(self, config, expected_revision):
        if type(expected_revision) is not int or expected_revision < 0 or config.revision != expected_revision + 1:
            raise FpgaDeviceError("expected FPGA revision is required; reload the device")
        record = config.to_dict()
        with closing(connect(self.path)) as connection, connection:
            if not connection.in_transaction:
                connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM fpga_devices WHERE topology_id=? AND device_id=?",
                                     (self.topology_id, config.device_id)).fetchone()
            current = 0 if row is None else row["revision"]
            if row is not None and _digest(json.loads(row["configuration_json"])) != row["configuration_sha256"]:
                raise FpgaDeviceError("persisted FPGA configuration fails its hash")
            if current != expected_revision:
                raise FpgaDeviceError("FPGA revision conflict; no configuration was changed")
            for existing in connection.execute("SELECT device_id,configuration_json FROM fpga_devices WHERE topology_id=?",
                                                (self.topology_id,)):
                if existing["device_id"] != config.device_id and json.loads(existing["configuration_json"])["node_id"] == config.node_id:
                    raise FpgaDeviceError("only one receiver FPGA may own a node's acquisitions")
            if row is None and connection.execute("SELECT COUNT(*) FROM fpga_devices WHERE topology_id=?",
                                                 (self.topology_id,)).fetchone()[0] >= MAX_DEVICES:
                raise FpgaDeviceError("FPGA device limit reached")
            connection.execute("INSERT INTO fpga_devices VALUES (?,?,?,?,?) ON CONFLICT(topology_id,device_id) "
                               "DO UPDATE SET revision=excluded.revision,configuration_json=excluded.configuration_json,"
                               "configuration_sha256=excluded.configuration_sha256",
                               (self.topology_id, config.device_id, config.revision, _canonical(record), _digest(record)))
        return record

    def record(self, device_id, tick, kind, body):
        record = {"schema": "orion.fpga-receipt.v1", "device_id": device_id, "topology_id": self.topology_id,
                  "tick": tick, "at_utc": _now(), "kind": kind, "body": body,
                  "physical_action_performed": False}
        digest = _digest(record)
        with closing(connect(self.path)) as connection, connection:
            connection.execute("INSERT INTO fpga_device_receipts "
                               "(topology_id,device_id,tick,kind,at_utc,record_json,record_sha256) VALUES(?,?,?,?,?,?,?)",
                               (self.topology_id, device_id, tick, kind, record["at_utc"], _canonical(record), digest))
            # Retention is bounded per workspace; every retained record re-verifies.
            connection.execute("DELETE FROM fpga_device_receipts WHERE topology_id=? AND sequence NOT IN "
                               "(SELECT sequence FROM fpga_device_receipts WHERE topology_id=? "
                               "ORDER BY sequence DESC LIMIT ?)", (self.topology_id, self.topology_id, MAX_RECEIPTS))
        return {**record, "record_sha256": digest}

    def recent(self, limit=20):
        if type(limit) is not int or not 1 <= limit <= MAX_RECEIPTS:
            raise ValueError("FPGA receipt limit must be 1..500")
        with closing(connect(self.path)) as connection:
            rows = connection.execute("SELECT * FROM fpga_device_receipts "
                                      "WHERE topology_id=? ORDER BY sequence DESC LIMIT ?",
                                      (self.topology_id, limit)).fetchall()
        result = []
        for row in rows:
            record = json.loads(row["record_json"])
            if _digest(record) != row["record_sha256"]:
                raise FpgaDeviceError("persisted FPGA feedback fails its hash")
            if any(record[name] != row[name] for name in ("topology_id", "device_id", "tick", "kind", "at_utc")):
                raise FpgaDeviceError("FPGA feedback index differs from its record")
            result.append({**record, "record_sha256": row["record_sha256"]})
        return result


class PhysicalFpgaAdapter:
    """Unavailable read-only physical contract; no transport or writes are opened."""
    mode = "physical-contract"

    def capabilities(self):
        return {"adapter_id": self.mode, "available": False, "physical_adapter_validated": False,
                "capabilities": [], "required_contract": "orion.fpga-acquisition.v1",
                "identity": None, "firmware": None, "bitstream_sha256": None,
                "measured_latency_s": None, "physical_action_performed": False,
                "reason": "No validated physical FPGA transport, identity or device telemetry is supplied."}

    def connect(self, config):
        raise FpgaDeviceError(self.capabilities()["reason"])

    def acquire(self, *args, **kwargs):
        raise FpgaDeviceError("physical FPGA acquisition is unavailable")

    def disconnect(self):
        pass


class SimulatedFpgaAdapter:
    """Transfer real raw acquisitions from the simulator, never finished metrics."""
    mode = "simulated"

    def __init__(self):
        self.config = None
        self.session_id = None
        self.sequence = 0

    def capabilities(self):
        return {"adapter_id": self.mode, "available": True, "physical_adapter_validated": False,
                "capabilities": list(CAPABILITIES), "firmware": "orion-fpga-simulator.v1",
                "bitstream_sha256": None, "physical_action_performed": False,
                "scope": "software device model; no synthesized bitstream or hardware timing claim"}

    def connect(self, config):
        if config.adapter_id != self.mode:
            raise FpgaDeviceError("FPGA adapter identity differs from its configuration")
        self.config, self.session_id = config, uuid4().hex
        return {**self.capabilities(), "identity": config.device_id, "session_id": self.session_id,
                "configuration_sha256": _digest(config.to_dict())}

    def disconnect(self):
        self.config = self.session_id = None

    def acquire(self, item, link, pair, tick, *, fault=None):
        if self.config is None:
            raise FpgaDeviceError("FPGA must complete its connection handshake before acquisition")
        if fault in ("timeout", "disconnect"):
            raise FpgaNoResponse("simulated FPGA classical acquisition response absent")
        if fault == "overrun":
            raise FpgaDeviceError("simulated FPGA acquisition FIFO overrun; no frame delivered")
        # This bounded simulator has one acquisition slot per source period. A
        # configured pipeline that cannot complete that period cannot hand its
        # raw samples to the common receiver. Timeout supervision advances on
        # network time; no CPU sleep or invented completion is used.
        latency = self.config.pipeline_cycles / self.config.clock_hz
        if latency > self.config.timeout_s:
            raise FpgaNoResponse("modeled FPGA pipeline latency exceeds the response timeout")
        if latency > item.qkd.period_s:
            raise FpgaDeviceError("modeled FPGA pipeline overruns the raw acquisition period; no frame delivered")
        from ..network_engine.channel_io import validated_observation
        copied, size, observation_hash = validated_observation(item, link)
        if not isinstance(item.quantum_record, dict):
            raise FpgaDeviceError("FPGA transport requires the complete raw quantum acquisition record")
        copied = replace(copied, quantum_record=dict(item.quantum_record))
        self.sequence += 1
        header = {"schema": "orion.fpga-acquisition.v1", "device_id": self.config.device_id,
                  "node_id": self.config.node_id, "session_id": self.session_id,
                  "sequence": self.sequence, "tick": tick, "link_id": link.link_id,
                  "classical_channel_id": pair.classical.channel_id,
                  "quantum_channel_id": pair.quantum.channel_id, "association_id": pair.association_id,
                  "observations_sha256": observation_hash, "quantum_record_sha256": _digest(item.quantum_record),
                  "configuration_sha256": _digest(self.config.to_dict()),
                  "modeled_pipeline_latency_s": latency,
                  "origin": "simulated", "physical_action_performed": False}
        if fault == "invalid_payload":
            header["association_id"] = "incorrect-association"
        return copied, header, size + len(_canonical(header).encode("utf-8"))


class FpgaDeviceRuntime:
    """Device lifecycle, acquisition supervision, processing readback and receipts."""
    def __init__(self, path, topology_id, *, security=None):
        self.registry = FpgaDeviceRegistry(path, topology_id)
        self.security = security
        self._configs = {c.device_id: c for c in self.registry.configurations()}
        self._states, self._adapters, self._faults, self._pending = {}, {}, {}, {}
        self._history = {}
        self._recent = deque(self.registry.recent(), maxlen=20)
        self._tick = 0
        self._network_time_s = 0.0
        for config in self._configs.values():
            self._initialize_state(config)

    def _initialize_state(self, config):
        self._states[config.device_id] = {
            "connection_state": "disconnected", "acquisition_state": "no_input", "session_id": None,
            "last_received_tick": None, "last_received_at_utc": None,
            "accepted_frames": 0, "rejected_frames": 0, "timeouts": 0, "reconnections": 0,
            "last_error": None, "next_retry_tick": None, "host_read_ms": None,
            "temperature_c": None, "measured_fpga_latency_s": None, "timing_margin_s": None,
            "last_receipts": [], "last_feedback": None, "last_response_tick": self._tick,
            "last_sequence": 0, "bound_links": [],
            "last_response_network_s": self._network_time_s,
        }
        self._history.setdefault(config.device_id, deque(maxlen=120))

    def _authorize(self, action, config):
        if self.security is None:
            return None
        resource = "workspace:" + self.registry.path.resolve().parent.as_posix() + "/nodes/" + config.node_id
        return self.security.authorize(action, resource)

    def _record(self, config, kind, body, *, identity=None, action=None):
        receipt = self.registry.record(config.device_id, self._tick, kind, body)
        self._recent.appendleft(receipt)
        self._states[config.device_id]["last_feedback"] = receipt
        if identity is not None and action is not None:
            resource = "workspace:" + self.registry.path.resolve().parent.as_posix() + "/nodes/" + config.node_id
            self.security.audit.record(identity, action, resource, "completed",
                                       {"artifact_sha256": receipt["record_sha256"], "mode": config.adapter_id
                                        if config.adapter_id == "simulated" else "physical"})
        return receipt

    @staticmethod
    def _bindings(config, topology):
        # The existing optical record's receiver is its right endpoint. Bind the
        # FPGA to that real receiver, never rewrite the endpoint to fit a device.
        return [link for link in topology.links if link.right_node_id == config.node_id and link.technology is None]

    def _validate_binding(self, config, topology):
        if topology.node(config.node_id) is None:
            raise FpgaDeviceError("FPGA node must exist in the persisted network inventory")
        if any(c.node_id == config.node_id and c.device_id != config.device_id for c in self._configs.values()):
            raise FpgaDeviceError("only one receiver FPGA may own a node's acquisitions")
        links = self._bindings(config, topology)
        if not links:
            raise FpgaDeviceError("FPGA node requires at least one configured incoming statistical optical span")
        for link in links:
            pair = ChannelPair.from_dict(link.channels)
            pair.validate_link(link.link_id, link.right_node_id)
        return links

    def register(self, payload, topology):
        if not isinstance(payload, dict) or set(payload) != {"device", "expected_revision"}:
            raise FpgaDeviceError("register_fpga_device requires device and expected_revision")
        config = FpgaDeviceConfiguration.from_dict(payload["device"])
        self._validate_binding(config, topology)
        identity = self._authorize("adapters.configure", config)
        self.registry.save(config, payload["expected_revision"])
        old_adapter = self._adapters.pop(config.device_id, None)
        if old_adapter is not None:
            old_adapter.disconnect()
        self._configs[config.device_id] = config
        self._initialize_state(config)
        self._states[config.device_id]["bound_links"] = [link.link_id for link in self._bindings(config, topology)]
        return self._record(config, "configuration", {"configuration": config.to_dict(),
                                                     "configuration_sha256": _digest(config.to_dict())},
                            identity=identity, action="adapters.configure")

    def configure(self, payload, topology):
        if not isinstance(payload, dict) or set(payload) != {"device_id", "expected_revision", "changes"}:
            raise FpgaDeviceError("configure_fpga_device requires device_id, expected_revision and changes")
        config = self._configs.get(payload["device_id"])
        if config is None:
            raise FpgaDeviceError("unknown FPGA device")
        changes = payload["changes"]
        forbidden = {"device_id", "node_id", "revision", "schema", "adapter_id"}
        if not isinstance(changes, dict) or not changes or set(changes) & forbidden:
            raise FpgaDeviceError("device identity is immutable; select valid configuration fields")
        try:
            updated = replace(config, **changes, revision=config.revision + 1)
        except TypeError as error:
            raise FpgaDeviceError("unknown FPGA configuration field") from error
        return self.register({"device": updated.to_dict(), "expected_revision": payload["expected_revision"]}, topology)

    def connection(self, payload, topology):
        if not isinstance(payload, dict) or set(payload) != {"device_id", "expected_revision", "connected"}:
            raise FpgaDeviceError("set_fpga_connection requires device_id, expected_revision and connected")
        return self.configure({"device_id": payload["device_id"], "expected_revision": payload["expected_revision"],
                               "changes": {"connected": payload["connected"]}}, topology)

    def fault(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"device_id", "kind", "duration_ticks"}:
            raise FpgaDeviceError("inject_fpga_fault requires device_id, kind and duration_ticks")
        config = self._configs.get(payload["device_id"])
        if config is None or config.adapter_id != "simulated":
            raise FpgaDeviceError("fault injection is available only for registered simulated FPGA devices")
        if payload["kind"] not in FAULTS or type(payload["duration_ticks"]) is not int \
                or not 1 <= payload["duration_ticks"] <= 120:
            raise FpgaDeviceError("unknown FPGA fault or duration outside 1..120 network ticks")
        identity = self._authorize("adapters.configure", config)
        self._faults[config.device_id] = (payload["kind"], self._tick + payload["duration_ticks"])
        return self._record(config, "simulated_fault", dict(payload), identity=identity, action="adapters.configure")

    def seed_demo(self, topology):
        if self._configs:
            return 0
        candidates = [node for node in topology.nodes if "demonstration_repeater" in node.telemetry
                      and any(link.right_node_id == node.node_id and link.technology is None for link in topology.links)]
        for index, node in enumerate(candidates[:3], 1):
            config = FpgaDeviceConfiguration(f"FPGA-DEMO-{index}", node.node_id,
                                             label=f"Simulated FPGA · {node.node_id}")
            self.register({"device": config.to_dict(), "expected_revision": 0}, topology)
        return min(3, len(candidates))

    def transfer_frame(self, frame, topology):
        """Pass raw input through identified device/classical envelope validation."""
        from ..security.identity import AuthenticationError, AuthorizationError
        self._tick = frame.tick
        self._network_time_s = frame.network_time_s
        self._pending = {}
        links = dict(frame.links)
        for config in self._configs.values():
            state = self._states[config.device_id]
            bindings = self._bindings(config, topology)
            state["bound_links"] = [link.link_id for link in bindings]
            incoming = {link.link_id: links.pop(link.link_id) for link in bindings if link.link_id in links}
            state["last_receipts"] = []
            if not config.enabled or not config.connected:
                state.update(connection_state="disabled" if not config.enabled else "disconnected",
                             acquisition_state="no_input", last_error=None)
                continue
            if config.adapter_id == "physical-contract":
                state.update(connection_state="unavailable", acquisition_state="no_input",
                             last_error=PhysicalFpgaAdapter().capabilities()["reason"])
                continue
            if frame.kind != "simulated":
                state.update(connection_state="source_incompatible", acquisition_state="no_input",
                             last_error="simulated FPGA accepts only the declared simulated raw input source")
                continue
            retry = state["next_retry_tick"]
            if retry is not None and (not config.auto_reconnect or frame.tick < retry):
                continue
            fault, until = self._faults.get(config.device_id, (None, -1))
            if frame.tick > until:
                self._faults.pop(config.device_id, None)
                fault = None
            try:
                identity = self._authorize("adapters.acquire", config)
                adapter = self._adapters.get(config.device_id)
                if adapter is None:
                    adapter = SimulatedFpgaAdapter()
                    handshake = adapter.connect(config)
                    self._adapters[config.device_id] = adapter
                    state.update(connection_state="connected", session_id=handshake["session_id"],
                                 acquisition_state="awaiting_input", next_retry_tick=None, last_error=None, last_sequence=0)
                    state["reconnections"] += 1
                    self._record(config, "handshake", handshake, identity=identity, action="adapters.acquire")
                pending = []
                read_started = time.perf_counter()
                for link in bindings:
                    item = incoming.get(link.link_id)
                    if item is None:
                        continue
                    pair = ChannelPair.from_dict(link.channels)
                    copied, header, size = adapter.acquire(item, link, pair, frame.tick, fault=fault)
                    expected = (config.device_id, config.node_id, link.link_id, pair.association_id,
                                pair.classical.channel_id, pair.quantum.channel_id, frame.tick, adapter.session_id)
                    actual = tuple(header[name] for name in ("device_id", "node_id", "link_id", "association_id",
                                                            "classical_channel_id", "quantum_channel_id", "tick", "session_id"))
                    if actual != expected or header["physical_action_performed"] is not False:
                        raise FpgaDeviceError("FPGA envelope identity, channel association or session differs from configuration")
                    from ..network_engine.channel_io import validated_observation
                    _, _, readback_hash = validated_observation(copied, link)
                    if header["observations_sha256"] != readback_hash or header["quantum_record_sha256"] != _digest(copied.quantum_record):
                        raise FpgaDeviceError("FPGA payload does not verify against its acquisition envelope")
                    if type(header["sequence"]) is not int or header["sequence"] <= state["last_sequence"]:
                        raise FpgaDeviceError("duplicate or out-of-order FPGA acquisition sequence")
                    state["last_sequence"] = header["sequence"]
                    if not pair.classical.enabled or size > pair.classical.max_message_bytes:
                        raise FpgaDeviceError("FPGA envelope exceeds or disables the configured classical channel")
                    links[link.link_id] = copied
                    pending.append({"link_id": link.link_id, "header": header, "header_sha256": _digest(header),
                                    "classical_payload_bytes": size})
                state["host_read_ms"] = (time.perf_counter() - read_started) * 1000
                if pending:
                    state.update(last_response_tick=frame.tick, acquisition_state="received",
                                 last_response_network_s=frame.network_time_s,
                                 last_error=None, last_received_tick=frame.tick, last_received_at_utc=_now())
                    self._pending[config.device_id] = pending
                else:
                    state["acquisition_state"] = "no_input"
            except FpgaNoResponse as error:
                for link in bindings:
                    links.pop(link.link_id, None)
                state.update(acquisition_state="no_input", last_error=str(error))
                if (frame.network_time_s - state["last_response_network_s"]) >= config.timeout_s:
                    self._fail(config, "timeout", str(error))
            except (FpgaDeviceError, ValueError, TypeError, KeyError, AttributeError, AuthenticationError, AuthorizationError) as error:
                for link in bindings:
                    links.pop(link.link_id, None)
                self._fail(config, "authorization_denied" if isinstance(error, (AuthenticationError, AuthorizationError))
                           else "rejected", str(error))
        return replace(frame, links=links)

    def _fail(self, config, kind, error):
        state = self._states[config.device_id]
        adapter = self._adapters.pop(config.device_id, None)
        if adapter is not None:
            adapter.disconnect()
        changed = state["connection_state"] != kind or state["last_error"] != error
        state.update(connection_state=kind, acquisition_state="rejected" if kind == "rejected" else "no_input",
                     session_id=None, last_error=error, next_retry_tick=self._tick + config.retry_ticks)
        state["rejected_frames"] += 1
        if kind == "timeout":
            state["timeouts"] += 1
        if changed:
            self._record(config, kind, {"error": error, "next_retry_tick": state["next_retry_tick"]})

    def processing_feedback(self, channel_receipts, processed_links, decisions):
        """Only publish success after the common intake and processing read back."""
        for device_id, pending in self._pending.items():
            config, state = self._configs[device_id], self._states[device_id]
            identity = self._authorize("adapters.acquire", config)
            rows, accepted = [], 0
            for acquisition in pending:
                link_id = acquisition["link_id"]
                channel = deepcopy(channel_receipts.get(link_id, {}))
                live = processed_links.get(link_id)
                processed = channel.get("status") == "received" and live is not None \
                    and live.provenance.get("input") not in (None, "none", "withheld")
                if processed:
                    accepted += 1
                rows.append({**acquisition, "channel_receipt": channel, "processing_status": "processed" if processed else "rejected",
                             "link_status": None if live is None else live.status,
                             "qber": None if not processed else live.qber,
                             "skr_bps": None if not processed else live.skr_bps,
                             "decisions": [decision.decision_id for decision in decisions if decision.subject == link_id]})
            state["last_receipts"] = rows
            state["accepted_frames"] += accepted
            state["rejected_frames"] += len(rows) - accepted
            state["acquisition_state"] = "processed" if accepted == len(rows) else "rejected"
            state["last_error"] = next((row["channel_receipt"].get("error") for row in rows
                                         if row["processing_status"] != "processed"), None)
            receipt = self._record(config, "processing_feedback", {"acquisitions": rows,
                "configuration_sha256": _digest(config.to_dict()), "origin": "simulated",
                "modeled_pipeline_latency_s": config.pipeline_cycles / config.clock_hz,
                "measured_fpga_latency_s": None, "host_read_ms": state["host_read_ms"]})
            if self.security is not None:
                resource = "workspace:" + self.registry.path.resolve().parent.as_posix() + "/nodes/" + config.node_id
                self.security.audit.record(identity, "adapters.acquire", resource, "completed",
                                           {"artifact_sha256": receipt["record_sha256"], "mode": "simulated"})
            self._history[device_id].append({"at_network_s": self._network_time_s, "accepted_frames": accepted,
                                            "host_read_ms": state["host_read_ms"],
                                            "modeled_pipeline_latency_s": config.pipeline_cycles / config.clock_hz})

    def reset_acquisition(self):
        """A new input source must perform a fresh device handshake and acquisition."""
        self.close()
        self._pending.clear()
        for config in self._configs.values():
            self._initialize_state(config)

    def snapshot(self):
        return {"schema": SCHEMA, "tick": self._tick, "network_time_s": self._network_time_s, "devices": [
            {"configuration": config.to_dict(), "configuration_sha256": _digest(config.to_dict()),
             "capabilities": (SimulatedFpgaAdapter() if config.adapter_id == "simulated" else PhysicalFpgaAdapter()).capabilities(),
             "mode": config.adapter_id, "state": deepcopy(self._states[config.device_id]),
             "history": list(self._history[config.device_id]), "physical_action_performed": False}
            for config in self._configs.values()], "recent_feedback": list(self._recent),
            "physical_action_performed": False, "scope": "identified software FPGA acquisition; physical adapter unavailable"}

    def close(self):
        for adapter in self._adapters.values():
            adapter.disconnect()
        self._adapters.clear()
