"""Bounded backend pipeline evidence, not a claim of TRL 5 or physical readiness.

Only the observation source is simulated. The production ProtocolRuntime owns
validation, protocol processing, resources, classical receipt and persistence.
The published snapshot is the frontend contract; no GUI is instantiated.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import replace
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys
import time

from quantum_autopilot.network_engine.institutional import brazil_institutions
from quantum_autopilot.quantum_protocols.contracts import Acquisition, canonical, digest
from quantum_autopilot.quantum_protocols.runtime import ProtocolRuntime
from quantum_autopilot.quantum_protocols.sources import MeasurementFileSource, SimulatedAcquisitionSource
from quantum_autopilot.quantum_protocols.topology import expand_global

SCHEMA = "orion.trl5.pipeline-campaign.v1"
SESSIONS = ("RIO-ENTANGLEMENT", "RIO-TF", "RIO-SATELLITE")
STAGES = ("acquired", "validated", "synchronized", "protocol_processed",
          "classical_transmitted", "classical_received", "metrics_computed")
TECHNOLOGIES = {"entanglement_swapping", "quantum_memory", "purification", "tf_qkd", "satellite_doppler"}
FAULTS = {"wrong_quantum": "wrong_channel", "wrong_classical": "wrong_channel",
          "wrong_binding": "wrong_binding", "unsynchronized": "unsynchronized",
          "late": "timeout", "precomputed": "precomputed_result", "absent": "timeout"}
SOURCE_FILES = (
    "tools/run_trl5_pipeline_campaign.py",
    "src/quantum_autopilot/network_engine/institutional.py",
    "src/quantum_autopilot/network_engine/engine.py",
    "src/quantum_autopilot/quantum_protocols/contracts.py",
    "src/quantum_autopilot/quantum_protocols/runtime.py",
    "src/quantum_autopilot/quantum_protocols/sources.py",
    "src/quantum_autopilot/quantum_protocols/registry.py",
    "src/quantum_autopilot/quantum_protocols/protocols.py",
    "src/quantum_autopilot/quantum_protocols/physics.py",
    "src/quantum_autopilot/quantum_protocols/topology.py",
    "src/quantum_autopilot/quantum_protocols/satellite_simulation.py",
    "src/quantum_autopilot/quantum_protocols/satellite_processing.py",
)


def _json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _selected(topology, sessions=SESSIONS):
    """Retain production nodes/links/parameters; bound scheduled sessions only."""
    return replace(topology, nodes=tuple(replace(node, telemetry={
        **node.telemetry, "technology_sessions": [config for config in
        node.telemetry.get("technology_sessions", ()) if config["session_id"] in sessions]
    }) for node in topology.nodes))


class RecordingSource:
    """Persist each actual simulator observation, including injected negatives.

    Fault injection belongs to this diagnostic source, never to production
    processing or the UI. No final protocol outcome is supplied by this source.
    """
    source_id = SimulatedAcquisitionSource.source_id
    origin = "simulated"
    physical_validated = False

    def __init__(self, path, seed, fault=None):
        if fault is not None and fault not in FAULTS:
            raise ValueError("unknown diagnostic fault")
        self.simulator = SimulatedAcquisitionSource(seed)
        self.fault = fault
        self.path = Path(path)
        self.handle = self.path.open("x", encoding="utf-8", newline="\n")
        self.observations = 0
        self.calls = 0

    def acquire(self, command, config, environment):
        self.calls += 1
        if self.fault == "absent":
            return None
        observation = self.simulator.acquire(command, config, environment)
        if observation is None:
            return None
        record = observation.to_dict()
        if self.fault == "wrong_quantum":
            record["quantum_channel_ids"] = ["diagnostic-wrong-quantum"]
        elif self.fault == "wrong_classical":
            record["classical_channel_ids"] = ["diagnostic-wrong-classical"]
        elif self.fault == "wrong_binding":
            record["command_id"] += ":wrong"
        elif self.fault == "unsynchronized":
            record["clock_offset_s"] = config.parameters.get("sync_tolerance_s", 1e-6) * 2
        elif self.fault == "late":
            record["observed_at_s"] = command.deadline_s + 0.01
        elif self.fault == "precomputed":
            record["raw"] = {**record["raw"], "final_fidelity": 1.0}
        self.handle.write(canonical(record) + "\n")
        self.observations += 1
        return Acquisition.from_dict(record)

    def close(self):
        if not self.handle.closed:
            self.handle.flush()
            os.fsync(self.handle.fileno())
            self.handle.close()


def _advance(runtime, topology, seconds, deadline):
    snapshots = []
    for tick in range(1, seconds + 1):
        if time.monotonic() > deadline:
            raise TimeoutError("bounded pipeline campaign deadline exceeded")
        snapshot = runtime.advance(topology, tick)
        # Preserve the production event watermark and drain bounded backpressure.
        for _ in range(8):
            if snapshot["clock_s"] >= tick:
                break
            snapshot = runtime.advance(topology, tick)
        if snapshot["clock_s"] < tick:
            raise RuntimeError("runtime backlog did not drain within diagnostic budget")
        snapshots.append(snapshot)
    return snapshots


def _load_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def verify_receipts(rows):
    previous = ""
    for row in rows:
        if row["previous_sha256"] != previous or digest({key: value for key, value in row.items()
                                                        if key != "receipt_sha256"}) != row["receipt_sha256"]:
            return False
        previous = row["receipt_sha256"]
    return bool(rows)


def _causality(rows):
    grouped = defaultdict(list)
    for row in rows:
        if row["command_id"]:
            grouped[row["command_id"]].append(row)
    completed = 0
    valid = True
    for command_rows in grouped.values():
        names = [row["event"] for row in command_rows]
        if "metrics_computed" not in names:
            continue
        completed += 1
        if any(stage not in names for stage in STAGES):
            valid = False
            continue
        positions = [names.index(stage) for stage in STAGES]
        valid &= positions == sorted(positions)
        sent = command_rows[names.index("classical_transmitted")]
        received = command_rows[names.index("classical_received")]
        valid &= bool(sent["detail"]["classical_channel_ids"] and sent["detail"]["associated_quantum_ids"])
        valid &= received["at_s"] >= sent["detail"]["arrival_at_s"] > sent["at_s"]
        valid &= all(command_rows[names.index(a)]["at_s"] <= command_rows[names.index(b)]["at_s"]
                     for a, b in zip(STAGES, STAGES[1:]))
    return bool(valid), completed


def _normalized(value):
    # Only acquisition transport provenance differs for explicitly synthetic replay.
    ignored = {"origin", "source_id", "observation_origin", "observation_source_id"}
    if isinstance(value, dict):
        return {key: _normalized(item) for key, item in value.items() if key not in ignored}
    if isinstance(value, (tuple, list)):
        return [_normalized(item) for item in value]
    return value


def scientific_fingerprint(snapshot):
    return digest({name: {"status": state["status"], "counters": state["counters"],
                         "metrics": _normalized(state["metrics"])}
                   for name, state in sorted(snapshot["sessions"].items())})


def campaign_fingerprint(report):
    """Science/outcomes only: no paths, runtime timing, host or source file hash."""
    return digest({"seed": report["seed"], "duration_simulated_s": report["duration_simulated_s"],
                   "scenarios": {name: {"science": state["scientific_fingerprint"],
                                         "event_counts": state["event_counts"],
                                         "completed_commands": state["completed_commands"]}
                                 for name, state in sorted(report["scenarios"].items())},
                   "negative_cases": report.get("negative_cases", {}),
                   "replay": report.get("replay", {}), "communication": report.get("communication", {})})


def _finish(runtime, root, snapshots, source=None):
    snapshot = runtime.snapshot()
    _json(root / "snapshot.json", snapshot)
    _json(root / "snapshots.json", snapshots)
    runtime.export_events(root / "receipts.jsonl")
    rows = _load_rows(root / "receipts.jsonl")
    with closing(sqlite3.connect(runtime.journal_path)) as connection:
        stored = [json.loads(row[0]) for row in connection.execute(
            "SELECT payload_json FROM technology_events WHERE topology_id=? ORDER BY sequence",
            (runtime.topology_id,))]
        configurations = connection.execute("SELECT count(*) FROM technology_configuration").fetchone()[0]
    causal, completed = _causality(rows)
    contract_checks = {
        "receipt_hash_chain_valid": verify_receipts(rows),
        "export_matches_persisted_receipts": rows == stored,
        "configuration_persisted": configurations == len(snapshot["sessions"]),
        "causal_stage_order_and_paired_classical_receive": causal,
        "frontend_snapshot_contract": snapshot["schema"] == "orion.technologies-live.v1"
            and canonical(json.loads(canonical(snapshot))) == canonical(snapshot),
        "no_physical_actuation": snapshot["physical_actuation"] is False
            and all(row["physical_actuation"] is False for row in rows),
        "no_certified_secret_material": all(state["metrics"].get("certified_secret_bits", 0) == 0
                                             for state in snapshot["sessions"].values()),
        "no_unreported_event_backlog": snapshot["backlog_before_clock"] == 0,
    }
    result = {"checks": {key: bool(value) for key, value in contract_checks.items()},
              "snapshot_path": str((root / "snapshot.json").name), "receipt_count": len(rows),
              "event_counts": dict(Counter(row["event"] for row in rows)),
              "completed_commands": completed, "scientific_fingerprint": scientific_fingerprint(snapshot),
              "final_sessions": snapshot["sessions"], "physical_action_performed": False}
    if source is not None:
        result["source"] = {"origin": source.origin, "source_id": source.source_id,
                            "observations_recorded": source.observations, "acquire_calls": source.calls,
                            "raw_path": source.path.name, "fault": source.fault}
    return result, rows


def _run(root, topology, seed, scenario, seconds, deadline, *, fault=None, replay_path=None):
    root.mkdir()
    source = (MeasurementFileSource(replay_path) if replay_path else
              RecordingSource(root / "raw-source-simulated.jsonl", seed, fault))
    try:
        runtime = ProtocolRuntime(root / "protocol.sqlite", topology.topology_id, source=source)
        runtime.sync(topology)
        for name in runtime.sessions:
            runtime.configure(name, scenario=scenario, seed=seed)
        snapshots = _advance(runtime, topology, seconds, deadline)
        result, rows = _finish(runtime, root, snapshots, source if isinstance(source, RecordingSource) else None)
    finally:
        if isinstance(source, RecordingSource):
            source.close()
    return result, rows, snapshots


def _environment():
    versions = {}
    for package in ("numpy", "scipy", "cryptography", "PySide6"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return {"python": platform.python_version(), "platform": platform.platform(),
            "machine": platform.machine(), "processor": platform.processor(),
            "logical_cpu_count": os.cpu_count(), "versions": versions,
            "gui_started": False, "execution": "source_python", "frozen": False}


def run_campaign(output_dir, *, seed=20261009, seconds=35, timeout_s=180):
    if type(seed) is not int or seed < 0:
        raise ValueError("non-negative integer seed required")
    if type(seconds) is not int or not 30 <= seconds <= 60:
        raise ValueError("simulated duration must be an integer in [30,60]")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 1 <= timeout_s <= 600:
        raise ValueError("diagnostic deadline must be in [1,600] seconds")
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=False)
    repository = Path(__file__).resolve().parents[1]
    source_hashes = {name: hashlib.sha256((repository / name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    started = time.monotonic()
    deadline = started + timeout_s
    report = {"schema": SCHEMA, "scope": "bounded_first_campaign", "seed": seed,
              "duration_simulated_s": seconds, "environment": _environment(), "source_hashes": source_hashes,
              "coverage": {"frontend": "snapshot_contract_only", "full_global_network": False,
                           "gui": False, "hardware": False, "sessions": list(SESSIONS),
                           "technologies": sorted(TECHNOLOGIES)},
              "ml_control_enabled": False, "model_inference_invoked": False,
              "acceptance_scope": "pipeline_integrity_and_observed_outcomes_not_final_communication",
              "physical_action_performed": False, "operator_state_accessed": False,
              "raw_source_scope": "new isolated simulated observations; file replay retains synthetic provenance",
              "checks": {}, "scenarios": {}, "errors": [],
              "limitations": ["not a TRL 5 assessment", "no full global routing or GUI validation",
                              "no physical adapter, key delivery, RF reception or optical qualification",
                              "simulated time follows production causal order; no wall-clock real-time guarantee"]}
    try:
        topology, matrix = expand_global(brazil_institutions())
        topology = _selected(topology)
        _json(root / "topology.json", topology.to_dict())
        _json(root / "connection-matrix.json", matrix)
        report["topology"] = {"nodes": len(topology.nodes), "links": len(topology.links),
                              "sha256": digest(topology.to_dict()), "active_session_count": len(SESSIONS)}
        outputs = {}
        for scenario in ("nominal", "degraded", "unavailable", "recovery"):
            result, rows, snapshots = _run(root / scenario, topology, seed, scenario, seconds, deadline)
            report["scenarios"][scenario] = result
            outputs[scenario] = (rows, snapshots)
            for key, value in result["checks"].items():
                report["checks"][f"{scenario}.{key}"] = value
        nominal = report["scenarios"]["nominal"]
        families = {row["detail"]["technology"] for row in outputs["nominal"][0]
                    if row["event"] == "protocol_processed"}
        report["checks"]["nominal.five_technologies_processed"] = families == TECHNOLOGIES
        report["checks"]["nominal.real_pipeline_completions"] = nominal["completed_commands"] > 0
        delivery_counts = nominal["final_sessions"][SESSIONS[0]]["counters"]
        delivered = delivery_counts.get("delivered_pairs", 0)
        rejected = delivery_counts.get("delivery_rejected", 0)
        rejection_receipts = [{"command_id": row["command_id"], "at_s": row["at_s"], **row["detail"]}
                              for row in outputs["nominal"][0] if row["event"] == "delivery_rejected"]
        report["communication"] = {"end_to_end_entanglement": {
            "delivered_pairs": delivered, "rejected_deliveries": rejected,
            "demonstrated": delivered > 0, "rejection_evidence": rejection_receipts,
            "impediment": None if delivered else "No final pair delivered; inspect actual retrieval/fidelity rejection receipts"}}
        # Acceptance covers truthful processing, including refusal. A refusal is
        # never promoted to demonstrated final quantum communication.
        report["checks"]["nominal.delivery_outcome_assessed"] = delivered + rejected > 0 and len(rejection_receipts) == rejected
        normal_arms = nominal["final_sessions"]["RIO-TF"]["metrics"].get("arm_transmissions", [])
        bad_arms = report["scenarios"]["degraded"]["final_sessions"]["RIO-TF"]["metrics"].get("arm_transmissions", [])
        report["checks"]["degraded.physical_losses_reduce_transmission"] = bool(normal_arms and len(normal_arms) == len(bad_arms)
                                                                                 and all(b < a / 10 for a, b in zip(normal_arms, bad_arms)))
        unavailable = report["scenarios"]["unavailable"]
        report["checks"]["unavailable.no_success_without_observations"] = unavailable["completed_commands"] == 0 and unavailable["source"]["observations_recorded"] == 0
        report["checks"]["unavailable.explicit_timeout_and_retry"] = all(state["status"] == "unavailable"
            and state["counters"].get("errors", 0) > 0 and state["counters"].get("retries", 0) > 0 for state in unavailable["final_sessions"].values())
        recovery_rows = outputs["recovery"][0]
        report["checks"]["recovery.outage_then_causal_resumption"] = any(row["event"] == "operation_error" and 10 <= row["at_s"] < 20 for row in recovery_rows) and any(
            row["event"] == "metrics_computed" and row["at_s"] >= 20 for row in recovery_rows)
        repeat, _, _ = _run(root / "nominal-repeat", topology, seed, "nominal", seconds, deadline)
        report["reproducibility"] = {"first": nominal["scientific_fingerprint"], "second": repeat["scientific_fingerprint"],
                                     "ignored_fields": ["origin", "source_id", "observation_origin", "observation_source_id"],
                                     "scope": "all final scientific metrics/counters/status; no wall-clock timing or file paths"}
        report["checks"]["same_seed.scientific_fingerprint_equal"] = nominal["scientific_fingerprint"] == repeat["scientific_fingerprint"]
        tf = _selected(topology, ("RIO-TF",))
        tf_original, original_rows, _ = _run(root / "tf-original", tf, seed, "nominal", 4, deadline)
        replay_path = root / "tf-original" / "synthetic-replay-measurement-file.jsonl"
        with replay_path.open("x", encoding="utf-8", newline="\n") as handle:
            for record in _load_rows(root / "tf-original" / "raw-source-simulated.jsonl"):
                handle.write(canonical({**record, "origin": "measurement_file", "source_id": "measurement_file"}) + "\n")
        replay, replay_rows, _ = _run(root / "tf-replay", tf, seed, "nominal", 4, deadline, replay_path=replay_path)
        report["replay"] = {"original_origin": "simulated", "replay_transport_origin": "measurement_file",
                            "physical_measurement": False, "original_fingerprint": tf_original["scientific_fingerprint"],
                            "replay_fingerprint": replay["scientific_fingerprint"]}
        report["checks"]["replay.identical_processing_and_scientific_results"] = tf_original["scientific_fingerprint"] == replay["scientific_fingerprint"] and [
            row["event"] for row in original_rows] == [row["event"] for row in replay_rows]
        report["negative_cases"] = {}
        for fault, expected in FAULTS.items():
            result, rows, _ = _run(root / ("negative-" + fault), tf, seed, "nominal", 5, deadline, fault=fault)
            codes = {row["detail"]["code"] for row in rows if row["event"] == "operation_error"}
            passed = expected in codes and result["completed_commands"] == 0 and not any(row["event"] == "protocol_processed" for row in rows)
            report["negative_cases"][fault] = {"expected_code": expected, "observed_codes": sorted(codes),
                                               "passed": passed, "completed_commands": result["completed_commands"]}
            report["checks"]["negative." + fault] = passed
        report["checks"]["source_files_unchanged"] = all(hashlib.sha256((repository / name).read_bytes()).hexdigest() == value for name, value in source_hashes.items())
    except (OSError, ValueError, RuntimeError, TimeoutError, KeyError) as exc:
        report["errors"].append({"type": type(exc).__name__, "message": str(exc)})
    report["elapsed_s"] = time.monotonic() - started
    report["scientific_fingerprint_sha256"] = campaign_fingerprint(report)
    report["scientific_fingerprint_scope"] = "scenario scientific fingerprints/event counts/completions, contract negatives, synthetic replay and observed communication; excludes wall-clock durations, host, source hashes and paths"
    report["check_count"] = len(report["checks"])
    report["passed"] = bool(report["checks"]) and all(value is True for value in report["checks"].values()) and not report["errors"]
    report = json.loads(canonical(report))
    _json(root / "report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path, help="new isolated evidence directory")
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--seconds", type=int, default=35, help="simulated seconds per scenario, [30,60]")
    parser.add_argument("--timeout-seconds", type=float, default=180)
    args = parser.parse_args(argv)
    try:
        report = run_campaign(args.output_dir, seed=args.seed, seconds=args.seconds, timeout_s=args.timeout_seconds)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"report": str(args.output_dir / "report.json"), "passed": report["passed"],
                      "check_count": report["check_count"], "scope": report["scope"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
