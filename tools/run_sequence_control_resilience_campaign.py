"""Trace native SeQUeNCe observations through real mTLS control and recovery.

The injection below is explicitly a source-health fixture. It cannot assert a
link, pair, key, or physical action. Recovery uses another verified native run
and fresh sessions, passing through normalization and the same PS/PL pipeline.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
import selectors
import subprocess
from pathlib import Path
import time

import run_mixed_controller_campaign as campaign
from quantum_autopilot.control_plane.mixed_contracts import ControllerObservation, digest
from quantum_autopilot.lab.adapters import EmulatedPhotonAdapter
from quantum_autopilot.lab.mixed_sources import normalize_acquisition, normalize_sequence_callbacks
from quantum_autopilot.lab.photon import PhotonConfig
from quantum_autopilot.service.client import CoreClient


def native_capture(path):
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    raw_path = path / "native-events.jsonl"
    raw_bytes = raw_path.read_bytes()
    if (summary["metadata"]["commit"] != "3133c66d80a898c6c3cc9af9eec3fa94fdbe2233"
            or summary["metadata"]["product_imports"] or summary["metadata"]["shims"]
            or hashlib.sha256(raw_bytes).hexdigest() != summary["raw_events_sha256"]):
        raise ValueError("clean independent native capture required")
    return summary, [json.loads(line) for line in raw_bytes.decode().splitlines()]


def recovered_records(native, configuration, destination):
    summary, raw = native_capture(native)
    sessions = {node["node_id"]: node["session_id"] for node in configuration["nodes"]}
    adapter = EmulatedPhotonAdapter("N01-recovery-emulation")
    adapter.connect()
    adapter.configure(PhotonConfig(seed=2026101101, duration_ps=10**10, clock_domain="fixture-aligned-clock"))
    adapter.calibrate()
    acquisition = adapter.acquire("node-one-recovery-emulated")
    records = normalize_acquisition(acquisition, node_id="N01", session_id=sessions["N01"])
    for node_id in summary["node_ids"]:
        records.extend(normalize_sequence_callbacks([row for row in raw if row["node_id"] == node_id],
            node_id=node_id, session_id=sessions[node_id], configuration_id=summary["configuration_id"],
            clock_domain="fixture-aligned-clock", source_id=summary["source_id"],
            adapter_id=summary["adapter_id"], delivery_evidence=raw))
    publications, processing, comparisons = campaign.process_observations(records, destination / "recovery-node-processing")
    records.extend(publications)
    campaign._persist_record(destination / "recovery-acquisition.json", asdict(acquisition))
    (destination / "recovery-observations.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in records), encoding="utf-8")
    return records, processing, comparisons



class NativeControlProcess:
    """Independent simulator on a trusted owned pipe; never import product there."""
    def __init__(self, python, source, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        self.log = (output.parent / "native-process.log").open("x", encoding="utf-8")
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.update(GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="core.autocrlf", GIT_CONFIG_VALUE_0="true",
            GIT_CONFIG_KEY_1="safe.directory", GIT_CONFIG_VALUE_1=str(source.resolve()), GIT_OPTIONAL_LOCKS="0")
        self.process = subprocess.Popen([str(python), str(Path(__file__).with_name("run_mixed_sequence_nodes.py")),
            "--sequence-src", str(source), "--output", str(output), "--seed", "2026101101", "--pulses", "128",
            "--interactive"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, bufsize=1, env=environment)
        self.responses = []
        try:
            ready = self.read(timeout=180)
            if ready.get("stage") != "ready":
                raise RuntimeError("native control process did not retain its timeline")
            self.pid = ready["pid"]
        except BaseException:
            self.close()
            raise

    def read(self, *, timeout):
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout):
                raise TimeoutError("native control process did not answer within its declared deadline")
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("independent native control process exited")
        response = json.loads(line)
        self.responses.append(response)
        return response

    def command(self, request):
        self.process.stdin.write(json.dumps(request, allow_nan=False) + "\n")
        self.process.stdin.flush()
        response = self.read(timeout=60)
        if response.get("stage") != "executed" or response.get("pid") != self.pid or not response.get("same_timeline_and_nodes"):
            raise RuntimeError("native control command did not complete on its original timeline")
        path = Path(response["events_path"])
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != response["events_sha256"]:
            raise ValueError("native control callbacks changed after publication")
        return response, [json.loads(line) for line in content.decode().splitlines()]

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write('{"type":"stop"}\n')
                self.process.stdin.flush()
                self.process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                stream.close()
        self.log.close()

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path)
    parser.add_argument("--recovery-native", type=Path)
    parser.add_argument("--closed-loop-native", action="store_true")
    parser.add_argument("--sequence-src", type=Path)
    parser.add_argument("--sequence-python", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output must be a fresh evidence directory")
    native_process = None
    if args.closed_loop_native:
        if args.sequence_src is None or args.sequence_python is None:
            parser.error("closed loop requires the pinned simulator and its independent Python")
        args.native = args.output / "native-live"
        native_process = NativeControlProcess(args.sequence_python, args.sequence_src, args.native)
        args.recovery_native = args.native
    elif args.native is None or args.recovery_native is None:
        parser.error("recorded mode requires native and recovery-native captures")
    native_summary, _ = native_capture(args.native)
    recovery_summary, _ = native_capture(args.recovery_native)
    destination = args.output / "controller"
    trace, checks, details = [], {}, {}
    instances = []

    class RecordingClient(CoreClient):
        def __init__(self, *values, **kwargs):
            super().__init__(*values, **kwargs)
            self.role = "operator" if not instances else "auditor"
            instances.append(self)

        def traced(self, operation, request, callback):
            start = time.perf_counter()
            response = callback()
            trace.append({"operation": operation, "role": self.role, "request": request,
                          "response": response, "wall_ms": 1000 * (time.perf_counter() - start)})
            return response

        def mixed_configure(self, request):
            return self.traced("mixed_configure", request, lambda: super(RecordingClient, self).mixed_configure(request))

        def mixed_ingest(self, request):
            # Complete inputs persist separately; retain every event ID/hash here.
            compact = {"records": [{"event_id": row["observation"]["event_id"],
                                    "kind": row["observation"]["kind"],
                                    "record_sha256": row["provenance"]["record_sha256"]} for row in request["records"]]}
            return self.traced("mixed_ingest", compact, lambda: super(RecordingClient, self).mixed_ingest(request))

        def mixed_demand(self, request):
            return self.traced("mixed_demand", request, lambda: super(RecordingClient, self).mixed_demand(request))

        def mixed_status(self):
            return self.traced("mixed_status", None, lambda: super(RecordingClient, self).mixed_status())

        def mixed_release(self, request):
            response = self.traced("mixed_release", request, lambda: super(RecordingClient, self).mixed_release(request))
            if self.role == "operator" and request["request_id"] == "native-demand-11":
                resilience(self)
            return response

    def ingest_native_stage(operator, raw, *, fresh=False, configuration=None):
        state = instances[1].mixed_status()["nodes"]
        if configuration is None:
            configuration = json.loads((destination / "configuration.json").read_text(encoding="utf-8"))
        sessions = {node["node_id"]: node["session_id"] for node in configuration["nodes"]}
        all_raw = [json.loads(line) for line in (args.native / "all-events-latest.jsonl").read_text(encoding="utf-8").splitlines()]
        records = []
        for node_id in native_summary["node_ids"]:
            node_raw = [row for row in raw if row["node_id"] == node_id]
            if not node_raw and not fresh:
                continue
            converted = normalize_sequence_callbacks(node_raw, node_id=node_id, session_id=sessions[node_id],
                configuration_id=native_summary["configuration_id"], clock_domain="fixture-aligned-clock",
                source_id=native_summary["source_id"], adapter_id=native_summary["adapter_id"],
                sequence=0 if fresh else state[node_id]["sequence"], delivery_evidence=all_raw)
            records.extend(converted if fresh else converted[1:])
        for offset in range(0, len(records), 128):
            operator.mixed_ingest({"records": records[offset:offset + 128]})
        return records

    def execute_reserved_route(operator, receipt, label, *, configuration=None):
        if native_process is None:
            return
        latest = next(row for row in instances[1].mixed_status()["reservations"] if row["request_id"] == receipt["request_id"])
        if latest["status"] != "reserved" or latest["receipt_sha256"] != receipt["receipt_sha256"]:
            raise ValueError("reservation changed before native dispatch")
        stage, raw = native_process.command({"type": "execute_control", "receipt": latest})
        received = [row for row in raw if row.get("command_id") == receipt["request_id"] and row["event"] == "classical_received"]
        delivered = [row for row in raw if row.get("command_id") == receipt["request_id"] and row.get("command_delivered")]
        expected = list(zip(receipt["route"], receipt["route"][1:]))
        observed = [(row["sender"], row["node_id"]) for row in received]
        checks[label] = (observed == expected and len(delivered) == 1
                         and delivered[0]["node_id"] == receipt["request"]["target"]
                         and all(a["callback_at_ps"] <= b["callback_at_ps"] for a, b in zip(received, received[1:])))
        details[label] = {"reservation": receipt, "native_stage": stage, "native_hops": received, "native_delivery": delivered}
        normalized = ingest_native_stage(operator, raw, configuration=configuration)
        (destination / (label + "-normalized.jsonl")).write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in normalized), encoding="utf-8")

    def resilience(operator):
        observer = instances[1]
        configuration = json.loads((destination / "configuration.json").read_text(encoding="utf-8"))
        active = operator.mixed_demand({"request_id": "resilience-active", "source": "N02", "target": "N06",
                                       "operation": "classical_control", "ttl_s": 600})
        checks["native_route_reserved_before_fault"] = active["status"] == "reserved" and "N05" in active["route"]
        execute_reserved_route(operator, active, "native_route_executed_before_fault")
        state = observer.mixed_status()["nodes"]["N05"]
        session = next(node["session_id"] for node in configuration["nodes"] if node["node_id"] == "N05")
        raw = {"event": "fault", "node_id": "N05", "time_ps": state["timestamp_ps"] + 1,
               "reason": "identified_source_outage_fixture", "injected": True}
        observation = ControllerObservation("identified-fault-N05", "N05", session, state["sequence"] + 1,
                                            raw["time_ps"], "fixture-aligned-clock", "fault", {"reason": raw["reason"]})
        provenance = {"source_id": "sequence-control-fault-fixture", "adapter_id": "identified-fault-injection-v1",
                      "origin": "simulated", "record_sha256": digest(raw)}
        injected = {"observation": observation.to_dict(), "provenance": provenance}
        campaign._persist_record(destination / "identified-source-fault.json", {"raw": raw, "record": injected, "submitted": native_process is None})
        if native_process is not None:
            stage, actual_fault = native_process.command({"type": "fault_source", "node_id": "N05"})
            ingest_native_stage(operator, actual_fault)
            details["native_fault"] = {"stage": stage, "callbacks": actual_fault}
        else:
            operator.mixed_ingest({"records": [injected]})
        failed = observer.mixed_status()
        active_after = next(item for item in failed["reservations"] if item["request_id"] == "resilience-active")
        checks["fault_invalidates_existing_reservation"] = active_after["status"] == "invalidated_observation"
        blocked = operator.mixed_demand({"request_id": "faulted-endpoint", "source": "N05", "target": "N06",
                                        "operation": "classical_control", "ttl_s": 600})
        checks["faulted_endpoint_is_blocked"] = blocked["status"] == "blocked"
        alternate = operator.mixed_demand({"request_id": "alternate-after-fault", "source": "N02", "target": "N06",
                                          "operation": "classical_control", "ttl_s": 600})
        checks["remaining_native_ring_routes_around_fault"] = alternate["status"] == "reserved" and "N05" not in alternate["route"]
        if alternate["status"] == "reserved":
            execute_reserved_route(operator, alternate, "native_alternate_route_executed")
            operator.mixed_release({"request_id": "alternate-after-fault"})
        state = observer.mixed_status()["nodes"]["N05"]
        reverify_raw = {**raw, "event": "session_started", "time_ps": state["timestamp_ps"] + 1,
                        "configuration_id": state["configuration_id"], "verified": True}
        reverify = ControllerObservation("same-session-reverify-N05", "N05", session, state["sequence"] + 1,
            reverify_raw["time_ps"], "fixture-aligned-clock", "session_started",
            {"configuration_id": state["configuration_id"], "verified": True})
        reverify_record = {"observation": reverify.to_dict(), "provenance": {**provenance, "record_sha256": digest(reverify_raw)}}
        campaign._persist_record(destination / "identified-same-session-reverification.json", {"raw": reverify_raw, "record": reverify_record})
        operator.mixed_ingest({"records": [reverify_record]})
        retry = operator.mixed_demand({"request_id": "same-session-remains-blocked", "source": "N05", "target": "N06",
                                      "operation": "classical_control", "ttl_s": 600})
        checks["same_session_cannot_resurrect_fault"] = retry["status"] == "blocked"
        recovered = deepcopy(configuration)
        for node in recovered["nodes"]:
            node["session_id"] += "-recovered"
        if native_process is None:
            records, processing, comparisons = recovered_records(args.recovery_native, recovered, destination)
        else:
            native_stage, recovery_raw = native_process.command({"type": "recovery_snapshot"})
            details["native_recovery"] = {"stage": native_stage, "callbacks_path": native_stage["events_path"]}
            # A new session is verified from fresh callbacks on the retained timeline.
            snapshot_state = {node["node_id"]: node for node in recovered["nodes"]}
            all_raw = [json.loads(line) for line in (args.native / "all-events-latest.jsonl").read_text(encoding="utf-8").splitlines()]
            adapter = EmulatedPhotonAdapter("N01-closed-loop-recovery-emulated")
            adapter.connect()
            adapter.configure(PhotonConfig(seed=2026101101, duration_ps=10**10, clock_domain="fixture-aligned-clock"))
            adapter.calibrate()
            records = normalize_acquisition(adapter.acquire("N01-closed-loop-recovery"), node_id="N01",
                                            session_id=snapshot_state["N01"]["session_id"])
            for node_id in native_summary["node_ids"]:
                records.extend(normalize_sequence_callbacks([row for row in recovery_raw if row["node_id"] == node_id],
                    node_id=node_id, session_id=snapshot_state[node_id]["session_id"],
                    configuration_id=native_summary["configuration_id"], clock_domain="fixture-aligned-clock",
                    source_id=native_summary["source_id"], adapter_id=native_summary["adapter_id"], delivery_evidence=all_raw))
            pubs, processing, comparisons = campaign.process_observations(records, destination / "recovery-node-processing")
            records.extend(pubs)
            (destination / "recovery-observations.jsonl").write_text(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in records), encoding="utf-8")
        campaign._persist_record(destination / "recovery-configuration.json", recovered)
        operator.mixed_configure(recovered)
        empty = observer.mixed_status()
        checks["new_sessions_reset_to_no_input"] = all(node["status"] == "no_input" for node in empty["nodes"].values())
        for offset in range(0, len(records), 128):
            operator.mixed_ingest({"records": records[offset:offset + 128]})
        recovered_state = observer.mixed_status()
        checks["fresh_native_observations_restore_ten_nodes"] = all(
            recovered_state["nodes"][node_id]["status"] == "ready" for node_id in recovery_summary["node_ids"])
        checks["recovery_uses_same_pspl_pipeline"] = len(processing) == 11 and all(comparisons.values())
        restored = operator.mixed_demand({"request_id": "fresh-session-restored", "source": "N05", "target": "N06",
                                         "operation": "classical_control", "ttl_s": 600})
        checks["fresh_sessions_can_reserve_again"] = restored["status"] == "reserved"
        execute_reserved_route(operator, restored, "native_recovered_route_executed", configuration=recovered)
        released = operator.mixed_release({"request_id": "fresh-session-restored"})
        checks["fresh_reservation_is_released"] = released["status"] == "released"
        checks["controller_policy_remains_source_blind"] = recovered_state["source_blind_policy"] is True
        details.update(before_fault=active, fault_state=failed, blocked=blocked, alternate=alternate,
                       same_session_retry=retry, after_recovery=restored, recovery_release=released)

    original = campaign.CoreClient
    campaign.CoreClient = RecordingClient
    exit_code = 2
    try:
        exit_code = campaign.main(["--native", str(args.native), "--output", str(destination),
                                   "--repository", str(args.repository)])
    finally:
        campaign.CoreClient = original
        if native_process is not None:
            native_process.close()
        if destination.exists():
            (destination / "authenticated-api-trace.jsonl").write_text(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in trace), encoding="utf-8")
    report = {"schema": "orion.sequence-control.resilience.v1", "status": "PASS" if exit_code == 0 and all(checks.values()) else "FAIL",
              "checks": checks, "details": details, "native_sha256": native_summary["raw_events_sha256"],
              "recovery_native_sha256": recovery_summary["raw_events_sha256"], "api_call_count": len(trace),
              "physical_action_performed": False, "swabian_hardware_present": False, "swabian_sdk_executed": False,
              "entanglement_established": False, "key_material_generated": False,
              "native_route_execution_performed": native_process is not None,
              "native_process_responses": native_process.responses if native_process is not None else [],
              "limits": ["Control-slot reservations from native observations, not execution commands returned to the native timeline.",
                         "The source outage is an explicitly identified injected fixture.",
                         "Recovery uses a separate native execution and new acquisition sessions.",
                         "Fixture optical parameters are declared assumptions, not a characterized physical link."]}
    if native_process is not None:
        report["limits"] = ["Only authenticated classical control routes execute; no quantum pair/key operation is produced.",
            "The simulator command pipe is a trusted local fixture, not a qualified hardware command transport.",
            "Reservations allocate controller control slots, not native quantum-memory protocol resources.",
            "The simulator timeline/nodes and RNG remain in one independent process across observation, routing, outage and recovery.",
            "The source outage/restart are explicitly simulated device operations; no physical equipment is connected."]
    campaign._persist_record(args.output / "resilience.json", report)
    print(json.dumps({"status": report["status"], "checks": checks}))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
