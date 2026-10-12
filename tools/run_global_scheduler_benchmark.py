"""Measure cold GlobalOperationManager admission of the installed Linux fixture.

No ProtocolRuntime.advance(), quantum acquisition, hardware allocation or
scientific result generation runs. Each trial uses a fresh private journal.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time


def _distribution(values):
    values = sorted(values)
    return {"count": len(values), "min_ms": min(values), "p50_ms": statistics.median(values),
            "p95_ms": values[min(len(values) - 1, (len(values) * 95 + 99) // 100 - 1)],
            "max_ms": max(values)}


def _measure(call):
    import resource
    before = resource.getrusage(resource.RUSAGE_SELF)
    wall, cpu = time.perf_counter(), time.process_time()
    result = call()
    cpu_ms, wall_ms = (time.process_time() - cpu) * 1000, (time.perf_counter() - wall) * 1000
    after = resource.getrusage(resource.RUSAGE_SELF)
    return result, {"wall_ms": wall_ms, "process_cpu_ms": cpu_ms,
                    "user_cpu_ms": (after.ru_utime - before.ru_utime) * 1000,
                    "system_cpu_ms": (after.ru_stime - before.ru_stime) * 1000,
                    "minor_faults": after.ru_minflt - before.ru_minflt,
                    "major_faults": after.ru_majflt - before.ru_majflt,
                    "voluntary_context_switches": after.ru_nvcsw - before.ru_nvcsw,
                    "involuntary_context_switches": after.ru_nivcsw - before.ru_nivcsw,
                    "block_input_operations": after.ru_inblock - before.ru_inblock,
                    "block_output_operations": after.ru_oublock - before.ru_oublock,
                    "process_peak_rss_before_kib": before.ru_maxrss,
                    "process_peak_rss_after_kib": after.ru_maxrss}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--expected-sessions", type=int, default=38)
    parser.add_argument("--expected-version", default="5.64.1")
    args = parser.parse_args(argv)
    if platform.system() != "Linux" or not 1 <= args.trials <= 30 or args.report.exists():
        parser.error("Linux, bounded trials and a fresh report path are required")
    os.umask(0o077)
    from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
    from quantum_autopilot.network_engine.global_operations import GlobalOperationManager
    from quantum_autopilot.network_engine.institutional import ensure_institutional_workspace
    from quantum_autopilot.quantum_protocols.contracts import canonical
    from quantum_autopilot.quantum_protocols.runtime import ProtocolRuntime
    from quantum_autopilot.quantum_protocols.sources import NoAcquisitionSource

    version = importlib.metadata.version("quantum-twin")
    installed_module = Path(sys.modules[ProtocolRuntime.__module__].__file__).resolve()
    if version != args.expected_version or "site-packages" not in installed_module.parts:
        parser.error("the requested installed wheel version must be used, without source-tree imports")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    private = args.report.parent.parent / "private"
    private.mkdir(mode=0o700, exist_ok=True)
    started = time.perf_counter()
    report = {"schema": "orion.global-scheduler-benchmark.v1", "status": "running",
              "artifact_version": version, "installed_runtime_module": str(installed_module),
              "python": sys.executable, "python_version": platform.python_version(),
              "platform": platform.platform(), "machine": platform.machine(), "cpu_count": os.cpu_count(),
              "uid": os.geteuid(), "gid": os.getegid(),
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "measurement": "cold prepare() admission and its durable journal writes, after sync()",
              "settings": {"trials": args.trials, "expected_protocol_sessions": args.expected_sessions,
                           "fresh_journal_per_trial": True, "network_tick": 0, "runtime_clock_s": 0.0,
                           "live_links": "empty; no acquired live channel evidence", "source": "NoAcquisitionSource"},
              "truth": {"fixture_only": True, "protocol_runtime_advance_calls": 0,
                        "physical_action_performed": False, "hardware_qualified": False,
                        "shared_physical_quantum_allocation_implemented": False,
                        "distributed_deadlock_prevention_qualified": False,
                        "quantum_resource_pools_advanced": False, "scientific_results_generated": False},
              "limitations": ["Timing covers local session admission and bookkeeping, not physical memory scheduling.",
                              "Dedicated hypothetical per-session banks do not arbitrate shared hardware.",
                              "Establishing is a control state; no pair, key or physical delivery is demonstrated.",
                              "RSS is the process high-water mark including imports and setup, not isolated prepare allocation.",
                              "Fresh journals are private and removed after each trial; no private journal is exported."],
              "trials": []}
    try:
        for trial in range(1, args.trials + 1):
            report["stage"] = f"trial_{trial}_setup"
            with tempfile.TemporaryDirectory(prefix=f"trial-{trial:02d}-", dir=private) as scratch:
                journal = Path(scratch) / "fixture.sqlite3"
                store = SimulatedTopologyStore(journal)
                (topology, created), setup = _measure(lambda: ensure_institutional_workspace(store))
                assert created
                runtime = ProtocolRuntime(journal, topology.topology_id, source=NoAcquisitionSource())
                _, sync = _measure(lambda: runtime.sync(topology))
                session_ids = sorted(runtime.sessions)
                assert len(session_ids) == args.expected_sessions
                assert all(session.config.parameters.get("resource_bank_scope") == "dedicated_per_session"
                           for session in runtime.sessions.values())
                manager = GlobalOperationManager(journal, topology.topology_id)
                report["stage"] = f"trial_{trial}_prepare"
                snapshot, admission = _measure(lambda: manager.prepare(topology, runtime, {}, 0))
                services = [row for row in snapshot["demands"] if row["demand_id"].startswith("SERVICE-")]
                assert len(services) == args.expected_sessions
                assert sorted(row["session_id"] for row in services) == session_ids
                assert runtime.clock_s == 0 and len(runtime._queue) == args.expected_sessions
                assert all(row[4] == "sample" for row in runtime._queue)  # sync schedules samples only
                assert all(not session.pending and not session.pairs and not session.counts.get("observations")
                           for session in runtime.sessions.values())
                assert all(row.get("last_result") is None and row["successful_attempts"] == 0 for row in snapshot["demands"])
                configs = {identifier: runtime.sessions[identifier].config.to_dict() for identifier in session_ids}
                report["trials"].append({"trial": trial, "setup": setup, "sync": sync, "prepare": admission,
                                         "topology_nodes": len(topology.nodes), "topology_links": len(topology.links),
                                         "protocol_sessions": len(session_ids), "service_demands": len(services),
                                         "all_demands": len(snapshot["demands"]),
                                         "service_state_counts": dict(Counter(row["state"] for row in services)),
                                         "all_demand_state_counts": snapshot["state_counts"],
                                         "services": [{"demand_id": row["demand_id"], "session_id": row["session_id"],
                                                       "state": row["state"], "reason": row["reason"]}
                                                      for row in sorted(services, key=lambda row: row["demand_id"])],
                                         "configuration_sha256": hashlib.sha256(canonical(configs).encode()).hexdigest(),
                                         "quantum_acquisition_commands": sum(len(session.pending) for session in runtime.sessions.values()),
                                         "quantum_observations": sum(session.counts.get("observations", 0) for session in runtime.sessions.values()),
                                         "delivered_pairs": sum(session.counts.get("delivered_pairs", 0) for session in runtime.sessions.values()),
                                         "certified_secret_bits": snapshot["certified_secret_bits"],
                                         "physical_action_performed": snapshot["physical_actuation"]})
        report["aggregate"] = {phase: {"wall": _distribution([row[phase]["wall_ms"] for row in report["trials"]]),
                                      "process_cpu": _distribution([row[phase]["process_cpu_ms"] for row in report["trials"]])}
                               for phase in ("setup", "sync", "prepare")}
        report.update(status="PASS", stage="complete")
    except Exception as error:
        report.update(status="FAIL", error_type=type(error).__name__)
    report["duration_s"] = time.perf_counter() - started
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "report": str(args.report),
                      "completed_trials": len(report["trials"]), "aggregate": report.get("aggregate", {})}, allow_nan=False))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
