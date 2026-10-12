"""Linux-only terminal campaign against an independently running, installed core.

Private disposable provisioning is test setup. During presentation only real
HTTPS/mTLS GETs are issued: the CLI server's clock drives the full simulated
network. No hardware, firmware qualification or hard realtime claim is made.
"""
from __future__ import annotations

import argparse
import base64
from collections import deque
from contextlib import contextmanager
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import platform
import select
import signal
import ssl
import statistics
import subprocess
import sys
from threading import Lock
import time

from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from quantum_autopilot.network_engine.institutional import ensure_institutional_workspace
from quantum_autopilot.security import SecurityRuntime
from quantum_autopilot.security.pki import OperationalPKI
from quantum_autopilot.service.client import CoreClient, CoreRequestError
from quantum_autopilot.service.config import load_config
from quantum_autopilot.service.runtime import mounted_provider
from quantum_autopilot.service.terminal_live import Publication, SnapshotPump, TerminalState, VIEWS, render, watch
from quantum_autopilot.service.tls import load_leaf


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def observations(raw):
    live = raw["live"]
    histories = {row["link_id"]: row.get("history", {}) for row in live.get("links", [])}
    series = live.get("technologies", {}).get("protocol_series", [])
    return {"tick": live["tick"], "publication_revision": live.get("publication_revision"),
            "network_time_s": live.get("network_time_s"), "topology_version": live.get("topology_version"),
            "link_history_samples": sum(len(value.get("t_s", [])) for value in histories.values()),
            "link_history_sha256": digest(histories), "protocol_series_count": len(series),
            "protocol_samples": sum(len(row.get("samples", [])) for row in series),
            "protocol_series_sha256": digest(series), "event_count": len(live.get("events", [])),
            "nodes": len(live.get("nodes", [])), "links": len(live.get("links", [])),
            "satellites": len(live.get("satellites", {}))}


def dense_history_records(raw, minimum):
    """Identify actual, finite backend histories; never create chart values."""
    found = []
    for row in raw["live"].get("links", []):
        history = row.get("history", {})
        times = history.get("t_s", [])
        series = [history.get(name, []) for name in ("fidelity", "qber", "skr_bps")]
        if (len(times) < minimum or any(len(values) != len(times) for values in series)
                or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                       or not math.isfinite(value) for value in times)
                or any(a >= b for a, b in zip(times, times[1:]))):
            continue
        valid = [all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                     for value in values) for values in zip(*series)]
        if sum(valid) >= minimum:
            found.append({"link_id": row["link_id"], "received_samples": len(times), "finite_triplets": sum(valid),
                          "first_t_s": times[0], "last_t_s": times[-1], "status": row.get("status"),
                          "fidelity_span": max(history["fidelity"]) - min(history["fidelity"])
                          if all(valid) else None})
    return found


class ReadObserver:
    """Bounded instrumentation; exposes only the monitor's read contracts."""
    def __init__(self, client):
        self.client, self.phase = client, "warmup"
        self.records, self.snapshots = deque(maxlen=128), deque(maxlen=2)
        self.inflight = self.max_inflight = 0
        self._lock = Lock()

    def _read(self, kind, call):
        start, phase = time.monotonic(), self.phase
        with self._lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        result = None
        try:
            result = call()
            return result
        finally:
            row = {"kind": kind, "phase": phase, "duration_ms": (time.monotonic() - start) * 1000,
                   "received": result is not None}
            if kind == "console_snapshot" and result is not None:
                row.update(observations(result))
            with self._lock:
                self.inflight -= 1
                self.records.append(row)
                if kind == "console_snapshot" and result is not None:
                    self.snapshots.append(result)

    def console_snapshot(self, *, include_static=True):
        return self._read("console_snapshot", lambda: self.client.console_snapshot(include_static=include_static))

    def mixed_status(self):
        return self._read("mixed_status", self.client.mixed_status)


def distribution(values):
    values = sorted(values)
    return {"count": len(values), "p50_ms": statistics.median(values),
            "p95_ms": values[min(len(values) - 1, max(0, (len(values) * 95 + 99) // 100 - 1))],
            "max_ms": max(values)} if values else {"count": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=6)
    parser.add_argument("--interval-s", type=float, default=1.)
    parser.add_argument("--client-timeout-s", type=float, default=15.)
    parser.add_argument("--pty", action="store_true", help="also exercise the installed curses CLI in a real Linux PTY; requires pyte")
    parser.add_argument("--minimum-history-samples", type=int, default=0,
                        help="wait for this many real backend link-history samples before presentation (0 preserves normal warmup)")
    parser.add_argument("--mixed-fixture", type=Path,
                        help="replay an identified, previously passed mixed-controller fixture through the authenticated API")
    args = parser.parse_args(argv)
    if (platform.system() != "Linux" or args.state.exists() or args.report.exists()
            or not 4 <= args.frames <= 30 or not 1 <= args.interval_s <= 10
            or not 1 <= args.client_timeout_s <= 30 or not 0 <= args.minimum_history_samples <= 120):
        parser.error("actual Linux, fresh private state/report and bounded campaign settings are required")
    os.umask(0o077)
    state = args.state.resolve()
    state.mkdir(parents=True, mode=0o700)
    private = state / "fixture-secrets"
    private.mkdir(mode=0o700)
    fixture_secrets = []
    for name, length in (("admin", 48), ("service", 48), ("master", 32)):
        value = os.urandom(length)
        fixture_secrets.append(value)
        path = private / name
        path.write_bytes(value)
        path.chmod(0o600)
    backend, config_path, client_leaf = state / "backend", state / "core.json", state / "client-leaf.json"
    scope = "workspace:" + backend.as_posix()
    settings = {"schema": "orion.core-service.v1", "repository_root": str(args.repository.resolve()),
                "state_root": str(backend),
                "identity": {"subject": "terminal-campaign-core", "credential_file": str(private / "service")},
                "provisioning_identity": {"subject": "terminal-campaign-admin", "credential_file": str(private / "admin")},
                "vault": {"key_id": "fixture-master", "key_file": str(private / "master")},
                "http": {"host": "127.0.0.1", "port": 0, "allowed_hosts": ["127.0.0.1", "localhost"],
                         "allowed_origins": [], "requests_per_minute": 600},
                "tls": {"root": str(state / "certificates"), "server_leaf": str(state / "server-leaf.json")},
                "clock": {"enabled": True, "period_s": 1., "stop_timeout_s": 15.}}
    config_path.write_text(json.dumps(settings), encoding="utf-8")
    cli = [sys.executable, "-m", "quantum_autopilot.service.cli", "--config", str(config_path)]
    process, logs, started = None, [], time.monotonic()
    report = {"schema": "orion.terminal-live-campaign.v1", "status": "running", "fixture_only": True,
              "physical_action_performed": False, "hardware_qualified": False,
              "artifact_python": sys.executable, "python_version": platform.python_version(),
              "platform": platform.platform(), "machine": platform.machine(), "cpu_count": os.cpu_count(),
              "uid": os.geteuid(), "gid": os.getegid(), "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "settings": {"source": "simulated", "seed": 17, "scenario": "quiet", "clock_period_s": 1.,
                           "frames": args.frames, "interval_s": args.interval_s, "client_timeout_s": args.client_timeout_s,
                           "warmup_deadline_s": 360. if args.minimum_history_samples else 60.,
                           "minimum_history_samples": args.minimum_history_samples, "startup_deadline_s": 30.},
              "criteria": {"presentation_step_calls": 0, "max_inflight_reads": 1,
                           "ticks_and_published_histories_change_during_watch": True,
                           "core_continues_without_operator_authority": True,
                           "all_satellite_details_accessible": True, "secret_material_in_public_output": False},
              "limitations": ["Local TLS processes and full simulated input, not hardware or FPGA firmware qualification.",
                              "Render timings measure Python text layout, not GPU or hard realtime guarantees.",
                              "Display captures are bounded viewport projections; omitted fields are not invented.",
                              "A configured orbital prediction is not received satellite telemetry."]}

    def run(*arguments):
        result = subprocess.run([*cli, *arguments], capture_output=True, text=True, timeout=45)
        logs.extend((result.stdout, result.stderr))
        if result.returncode:
            raise RuntimeError("fixture CLI command failed")
        return json.loads(result.stdout)

    try:
        report["stage"] = "private_provisioning"
        run("provision", "--server-names", "localhost", "127.0.0.1")
        run("grant-certificate", "terminal-campaign-observer", "--roles", "operator", "--scopes", scope,
            scope + "/*", "security:identity", "--services", "dashboard", "--output", str(client_leaf))
        config = load_config(config_path)
        administrator_runtime = SecurityRuntime(config.security_root, profile="operational", identity_profile="service",
            bootstrap=False, secret_provider=mounted_provider(config))
        administrator = administrator_runtime.authenticate_service("terminal-campaign-admin", (private / "admin").read_bytes())
        with administrator_runtime.identity_scope(administrator):
            pki = OperationalPKI(config.tls["root"], administrator_runtime)
            first_leaf = load_leaf(client_leaf)
            context = pki.client_context(first_leaf)
        # Packaged blueprint and initial fixture data are prepared offline;
        # no local engine is created by the monitor or by this campaign.
        topology, created = ensure_institutional_workspace(SimulatedTopologyStore(config.journal))
        report["blueprint"] = {"created": created, "topology_version": topology.topology_version,
                               "nodes": len(topology.nodes), "links": len(topology.links),
                               "preparation": "packaged ensure_institutional_workspace; fresh private journal"}
        settings.pop("provisioning_identity")
        config_path.write_text(json.dumps(settings), encoding="utf-8")
        checked = run("check")
        assert checked["identity_profile"] == "service" and checked["audit_valid"]
        report["stage"] = "service_startup"
        process = subprocess.Popen([*cli, "serve"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ready, deadline = None, time.monotonic() + 30
        while time.monotonic() < deadline and process.poll() is None:
            if select.select([process.stdout], [], [], .1)[0]:
                line = process.stdout.readline()
                logs.append(line)
                ready = json.loads(line)
                break
        assert ready is not None and ready["status"] == "listening" and ready["processing_clock_enabled"] is True
        url = f"https://localhost:{ready['port']}"
        client = CoreClient(url, context, timeout_s=args.client_timeout_s)
        health = client.health()
        assert health["physical_read_only"] is True and health["processing_clock"]["period_s"] == 1
        report["artifact_version"] = health["autopilot_version"]
        report["tls"] = "TLSv1.3-mTLS"
        client.network_command("configure_network_input", {"kind": "simulated", "seed": 17, "scenario": "quiet"})
        mixed_expected = None
        if args.mixed_fixture:
            fixture = args.mixed_fixture.resolve(strict=True)
            paths = {name: fixture / name for name in
                     ("configuration.json", "normalized-observations.jsonl", "report.json")}
            if any(path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024
                   for path in paths.values()):
                raise ValueError("bounded regular mixed fixture files are required")
            fixture_report = json.loads(paths["report.json"].read_text(encoding="utf-8"))
            mixed_configuration = json.loads(paths["configuration.json"].read_text(encoding="utf-8"))
            record_bytes = paths["normalized-observations.jsonl"].read_bytes()
            mixed_records = [json.loads(line) for line in record_bytes.decode("utf-8").splitlines()]
            if (fixture_report.get("status") != "PASS" or fixture_report.get("fixture_only") is not True
                    or fixture_report.get("physical_action_performed") is not False
                    or digest(mixed_configuration) != fixture_report.get("configuration_sha256")
                    or len(mixed_records) != fixture_report.get("observation_count")
                    or not 1 <= len(mixed_records) <= 10000
                    or any(record.get("provenance", {}).get("origin") != "simulated" for record in mixed_records)):
                raise ValueError("identified simulated mixed fixture evidence is required")
            client.mixed_configure(mixed_configuration)
            for offset in range(0, len(mixed_records), 128):
                client.mixed_ingest({"records": mixed_records[offset:offset + 128]})
            control = client.mixed_demand({"request_id": "terminal-native-control", "source": "N02", "target": "N03",
                                          "operation": "classical_control", "ttl_s": 600})
            bridge = client.mixed_demand({"request_id": "terminal-unavailable-bridge", "source": "N01", "target": "N11",
                                         "operation": "classical_control", "ttl_s": 600})
            mixed_expected = client.mixed_status()
            assert len(mixed_expected["nodes"]) == 11 and control["status"] == "reserved" and bridge["status"] == "blocked"
            assert control["quantum_result"] is None and bridge["quantum_result"] is None
            report["mixed_fixture"] = {"mode": "identified replay through authenticated configure/ingest/demand/status API",
                "fixture_only": True, "physical_action_performed": False, "fresh_sequence_execution": False,
                "configuration_sha256": digest(mixed_configuration),
                "normalized_records_sha256": hashlib.sha256(record_bytes).hexdigest(),
                "fixture_report_sha256": hashlib.sha256(paths["report.json"].read_bytes()).hexdigest(),
                "observation_count": len(mixed_records), "node_ids": sorted(mixed_expected["nodes"]),
                "reservation_receipts": [{key: receipt[key] for key in ("request_id", "status", "quantum_result")}
                                         for receipt in (control, bridge)]}
        report["stage"] = "full_profile_warmup"
        observer = ReadObserver(client)
        deadline = time.monotonic() + (360 if args.minimum_history_samples else 60)
        warmed = None
        dense = []
        history_started, next_history_progress = time.monotonic(), 0.
        while time.monotonic() < deadline:
            warmed = observer.console_snapshot(include_static=False)
            dense = dense_history_records(warmed, args.minimum_history_samples) if args.minimum_history_samples else []
            if args.minimum_history_samples and time.monotonic() >= next_history_progress:
                # Private progress contains counts only, never snapshot bodies,
                # credentials, raw audit records or process metadata.
                progress = {"schema": "orion.terminal-history-warmup-progress.v1",
                            "requested_minimum_samples": args.minimum_history_samples,
                            "tick": warmed["live"]["tick"], "eligible_link_count": len(dense),
                            "largest_received_history": max((len(row.get("history", {}).get("t_s", []))
                                for row in warmed["live"].get("links", [])), default=0),
                            "elapsed_s": time.monotonic() - history_started}
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.with_suffix(".warmup-progress.json").write_text(json.dumps(progress) + "\n", encoding="utf-8")
                next_history_progress = time.monotonic() + 10.
            if warmed["live"]["tick"] >= 2 and (not args.minimum_history_samples or dense):
                break
            time.sleep(.5)
        assert warmed is not None and warmed["live"]["tick"] >= 2
        assert not args.minimum_history_samples or dense
        if args.minimum_history_samples:
            report["dense_history"] = {"requested_minimum_samples": args.minimum_history_samples,
                "eligible_links": dense, "received_tick": warmed["live"]["tick"],
                "network_time_s": warmed["live"]["network_time_s"],
                "acquisition": "independent service clock and authenticated GETs; no monitor stepping or UI state injection"}
        assert len(warmed["live"]["nodes"]) >= len(topology.nodes) and warmed["live"]["satellites"]
        observer.phase = "watch"
        report["stage"] = "live_terminal"
        text = io.StringIO()
        presentation_started = time.monotonic()
        result = watch(observer, frames=args.frames, interval_s=args.interval_s, view="overview", stream=text)
        watch_records = [row for row in observer.records if row["phase"] == "watch"]
        report["watch"] = {"exit_code": result, "duration_s": time.monotonic() - presentation_started,
                           "presentation_step_calls": 0, "publications": watch_records,
                           "read_latency": distribution([row["duration_ms"] for row in watch_records])}
        captures = [text.getvalue()]
        assert result == 0 and len(watch_records) >= args.frames
        assert len({row["tick"] for row in watch_records if row["received"]}) >= 2
        assert len({row["link_history_sha256"] for row in watch_records if row["received"]}) >= 2
        observer.phase = "inspection"
        report["stage"] = "all_views_and_satellites"
        latest, mixed = observer.console_snapshot(include_static=True), observer.mixed_status()
        terminal = TerminalState()
        assert terminal.admit(Publication(latest, mixed, False, False, True, 0, time.monotonic()))
        durations, view_counts, satellite_details = [], {}, []
        for view in VIEWS:
            terminal.view, terminal.details = view, False
            for columns, rows in ((80, 24), (120, 40), (200, 60)):
                start = time.perf_counter()
                rendered = render(terminal, columns=columns, rows=rows)
                durations.append((time.perf_counter() - start) * 1000)
                assert len(rendered.splitlines()) <= rows
                captures.append(f"\n--- {view} {columns}x{rows} ---\n" + rendered)
            view_counts[view] = len(terminal.records())
        terminal.view, terminal.details = "satellites", True
        for identifier, satellite in sorted(latest["live"]["satellites"].items()):
            terminal.selections["satellites"] = identifier
            for columns, rows in ((80, 24), (120, 40), (200, 60)):
                start = time.perf_counter()
                rendered = render(terminal, columns=columns, rows=rows)
                durations.append((time.perf_counter() - start) * 1000)
                assert terminal.selections["satellites"] == identifier and len(rendered.splitlines()) <= rows
            assert "HOUSEKEEPING / SCIENCE" in rendered
            captures.append(f"\n--- satellite {identifier} 200x60 ---\n" + rendered)
            satellite_details.append({"satellite_id": identifier,
                                      "housekeeping_valid": satellite.get("housekeeping_valid"),
                                      "position_scope": satellite.get("position_scope"),
                                      "transmitting_links": len(satellite.get("transmitting_links", [])),
                                      "bridge_states": [{"session_id": row.get("session_id"), "status": row.get("status")}
                                                        for row in satellite.get("bridges", [])]})
        report["presentation"] = {"render_latency": distribution(durations), "views": view_counts,
                                  "viewport_sizes": [[80, 24], [120, 40], [200, 60]],
                                  "satellite_details": satellite_details,
                                  "selection_preserved": True}
        if args.pty:
            report["stage"] = "interactive_pty"
            # Offline fixture provisioning only: a private, ready-to-use TLS
            # client key matches the operator CLI's external-key contract.
            from cryptography.hazmat.primitives import serialization
            with administrator_runtime.identity_scope(administrator):
                key = serialization.load_pem_private_key(Path(first_leaf["private_key"]).read_bytes(),
                    password=administrator_runtime.vault.get_bytes(first_leaf["password_ref"]))
                encoded_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption())
            operator_key = private / "pty-client.key"
            with operator_key.open("xb") as stream:
                stream.write(encoded_key)
            operator_key.chmod(0o600)
            fixture_secrets.append(encoded_key)
            spec = importlib.util.spec_from_file_location("_orion_terminal_pty_probe", Path(__file__).with_name("terminal_pty_probe.py"))
            probe_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(probe_module)

            @contextmanager
            def outage():
                # Pause the isolated fixture service to reproduce an unavailable
                # read. Always resume it; neither core nor source code is changed.
                os.kill(process.pid, signal.SIGSTOP)
                try:
                    yield
                finally:
                    os.kill(process.pid, signal.SIGCONT)

            pty_command = [sys.executable, "-I", "-m", "quantum_autopilot.service.operator_cli",
                "--url", url, "--ca", first_leaf["ca"], "--cert", first_leaf["certificate"],
                "--key", str(operator_key), "--timeout-s", "2", "watch", "--interval-s", "1"]
            pty_report, pty_captures = probe_module.probe(pty_command, snapshot=latest,
                pause_check=lambda: client.console_snapshot(include_static=False)["live"]["tick"], outage=outage,
                mixed_expected=mixed_expected)
            report["pty"] = pty_report
            captures.extend(pty_captures)
            assert pty_report["status"] == "PASS"
        # Start this independent four-second pause measurement from a fresh
        # publication; the earlier PTY campaign may have taken many ticks.
        fresh = observer.console_snapshot(include_static=False)
        assert terminal.admit(Publication(fresh, None, False, False, False, 0, time.monotonic()))
        terminal.key("p")
        held_tick = terminal.visible["live"]["tick"]
        observer.phase = "presentation_paused"
        time.sleep(4)
        advanced = observer.console_snapshot(include_static=False)
        assert advanced["live"]["tick"] > held_tick
        assert terminal.admit(Publication(advanced, None, False, False, False, 0, time.monotonic()))
        assert terminal.visible["live"]["tick"] == held_tick
        terminal.key("p")
        assert terminal.visible["live"]["tick"] == advanced["live"]["tick"]
        report["pause"] = {"held_display_tick": held_tick, "received_tick_after_4s": advanced["live"]["tick"]}
        observer.phase = "backpressure"
        report["stage"] = "bounded_backpressure"
        pump = SnapshotPump(observer)
        try:
            assert pump.request(include_static=False, reservations=False)
            rejected = sum(not pump.request(include_static=False, reservations=False) for _ in range(100))
            publication, deadline = None, time.monotonic() + args.client_timeout_s + 5
            while publication is None and time.monotonic() < deadline:
                publication = pump.take()
                time.sleep(.01)
            assert publication is not None and not publication.error and rejected == 100
            report["backpressure"] = {"additional_requests_rejected": rejected, "max_inflight_reads": observer.max_inflight,
                                      "request_slot_capacity": 1, "publication_slot_capacity": 1,
                                      "observer_record_capacity": observer.records.maxlen,
                                      "retained_snapshot_capacity": observer.snapshots.maxlen}
            assert observer.max_inflight == 1
        finally:
            assert pump.close(timeout_s=.2)
        report["stage"] = "operator_revocation_and_return"
        before_revocation = publication.snapshot["live"]["tick"]
        returning_subject = "terminal-campaign-returning-operator"
        with administrator_runtime.identity_scope(administrator):
            administrator_runtime.grant_identity(returning_subject, ("operator",),
                (scope, scope + "/*", "security:identity"), source="certificate")
            returning_leaf = pki.provision(returning_subject, kind="service", services=["dashboard"])
            returning_context = pki.client_context(returning_leaf)
            pki.revoke(first_leaf["serial"], reason="fixture_operator_departure")
        rejected_status = None
        try:
            client.console_snapshot(include_static=False)
        except CoreRequestError as error:
            rejected_status = error.status
        assert rejected_status == 401
        observer.phase = "revoked_watch"
        stale_text = io.StringIO()
        assert watch(observer, frames=1, interval_s=1, stream=stale_text) == 3
        assert "STALE" in stale_text.getvalue()
        captures.append("\n--- revoked operator presentation ---\n" + stale_text.getvalue())
        anonymous_context = ssl.create_default_context(cafile=str(first_leaf["ca"]))
        anonymous_context.minimum_version = anonymous_context.maximum_version = ssl.TLSVersion.TLSv1_3
        anonymous_rejected = False
        try:
            CoreClient(url, anonymous_context, timeout_s=args.client_timeout_s).health()
        except CoreRequestError:
            anonymous_rejected = True
        assert anonymous_rejected
        # No authorized user client is connected during this presentation gap.
        # The existing process and its explicit service identity continue.
        time.sleep(4)
        returning_client = CoreClient(url, returning_context, timeout_s=args.client_timeout_s)
        returned = returning_client.console_snapshot(include_static=False)
        assert returned["live"]["tick"] > before_revocation
        assert returned["security"]["subject"] == returning_subject
        assert process.poll() is None
        report["operator_independence"] = {"revoked_operator_http_status": rejected_status,
            "anonymous_rejected": anonymous_rejected, "held_tick_before_revocation": before_revocation,
            "received_tick_new_operator": returned["live"]["tick"], "returning_identity": returning_subject,
            "same_server_pid": process.pid, "server_restarted": False,
            "departure_model": "client disconnected and its certificate revoked; no service identity revocation"}
        report["stage"] = "shutdown_and_authenticated_audit"
        shutdown_started = time.monotonic()
        process.terminate()
        stdout, stderr = process.communicate(timeout=20)
        logs.extend((stdout, stderr))
        assert process.returncode == 0
        report["shutdown"] = {"exit_code": process.returncode, "duration_s": time.monotonic() - shutdown_started}
        audit_status = administrator_runtime.audit.verify()
        assert audit_status["valid"]
        counts = {}
        with administrator_runtime.audit.path.open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if row["identity"] in {"terminal-campaign-core", "terminal-campaign-observer", returning_subject}:
                    identity_counts = counts.setdefault(row["identity"], {})
                    identity_counts[row["operation"]] = identity_counts.get(row["operation"], 0) + 1
        assert set(counts) == {"terminal-campaign-core", "terminal-campaign-observer", returning_subject}
        report["authenticated_audit"] = {"valid": True, "records": audit_status["count"], "operation_counts_by_identity": counts}
        public_text = "\n".join(captures)
        combined = "".join(logs) + public_text + json.dumps(report)
        assert all(value.hex() not in combined and base64.b64encode(value).decode() not in combined for value in fixture_secrets)
        assert "-----BEGIN PRIVATE KEY-----" not in combined and "-----BEGIN ENCRYPTED PRIVATE KEY-----" not in combined
        args.report.parent.mkdir(parents=True, exist_ok=True)
        capture_path = args.report.with_suffix(".txt")
        with capture_path.open("x", encoding="utf-8") as stream:
            stream.write(public_text)
        report.update(status="PASS", stage="complete", secrets_in_public_output=False,
                      capture_filename=capture_path.name, capture_sha256=hashlib.sha256(public_text.encode()).hexdigest())
    except Exception as error:
        report.update(status="blocked", error_type=type(error).__name__)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        report["duration_s"] = time.monotonic() - started
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
