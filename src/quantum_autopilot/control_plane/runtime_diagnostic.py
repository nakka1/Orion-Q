"""Bounded host-time diagnostics on an isolated simulated ControllerRuntime."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import statistics
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from importlib.resources import files
from threading import Event, Thread
from time import perf_counter_ns
from uuid import uuid4


def percentiles(values):
    if not values:
        return None
    ordered = sorted(values)

    def percentile(fraction):
        position = fraction * (len(ordered) - 1)
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {"n": len(values), "mean": statistics.fmean(values), "p50": percentile(0.5),
            "p95": percentile(0.95), "p99": percentile(0.99), "max": ordered[-1]}


class CycleTrace:
    def __init__(self):
        self.stages = {}

    @contextmanager
    def measure(self, name):
        start = perf_counter_ns()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + (perf_counter_ns() - start) / 1e6


class _Measured:
    def __init__(self, target, method, stage, get_trace):
        self.target, self.method, self.stage, self.get_trace = target, method, stage, get_trace

    def __getattr__(self, name):
        member = getattr(self.target, name)
        if name != self.method:
            return member

        def measured(*args, **kwargs):
            with self.get_trace().measure(self.stage):
                return member(*args, **kwargs)
        return measured


def _load(stop):
    while not stop.is_set():
        sum(math.sqrt(i) for i in range(1000))


def environment():
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        provenance = json.loads(files(__package__).joinpath("data/runtime_diagnostic_manifest_v1.json").read_bytes())
        if provenance["schema"] != "orion.runtime-diagnostic-build.v1":
            raise ValueError("unsupported frozen diagnostic provenance")
        origin = "embedded_build_manifest"
    else:
        root = Path(__file__).resolve().parents[3]
        names = [f"src/quantum_autopilot/control_plane/{name}.py" for name in
                 ("runtime_diagnostic", "runtime", "policy", "safety", "executors", "journal", "contracts", "sqlite_transaction")]
        revision = subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", "rev-parse", "HEAD"],
                                  cwd=root, capture_output=True, text=True, check=False).stdout.strip() or None
        provenance = {"revision": revision, "source_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}}
        origin = "source_files_at_start"
    versions = {}
    for name in ("numpy", "qiskit", "qiskit-aer"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {"python": sys.version, "os": platform.platform(), "machine": platform.machine(),
            "processor": platform.processor(), "frozen": frozen, "source_hash_origin": origin,
            "revision": provenance["revision"], "source_sha256": provenance["source_sha256"], "dependencies": versions}


def run_diagnostic(output, *, samples=1000, warmup=20, budget_ms=10.0,
                   condition="idle", runtime_factory=None):
    """Measure software cycles; exceptions and missed budgets remain in attempts.

    Instrumentation is confined to this private runtime, never a live console.
    The declared minimum for reporting p99 as adequately sampled is 1000 cycles.
    """
    if type(samples) is not int or not 1 <= samples <= 5000:
        raise ValueError("samples must be an integer between 1 and 5000")
    if type(warmup) is not int or not 0 <= warmup <= 200:
        raise ValueError("warmup must be an integer between 0 and 200")
    if isinstance(budget_ms, bool) or not isinstance(budget_ms, (int, float)):
        raise ValueError("budget_ms must be numeric")
    if not math.isfinite(budget_ms) or budget_ms <= 0:
        raise ValueError("budget_ms must be positive and finite")
    if condition not in {"idle", "background_math_thread"}:
        raise ValueError("unsupported diagnostic condition")
    from .contracts import NetworkStateSnapshot
    from .runtime import ControllerRuntime

    host = environment()
    started_at = datetime.now(timezone.utc).isoformat()
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    runtime = (runtime_factory or ControllerRuntime)(output / "controller.sqlite")
    current = [CycleTrace()]
    for attribute, method, stage in (("policy", "decide", "policy"), ("supervisor", "assess", "supervision"),
                                     ("simulated_executor", "execute", "execution"),
                                     ("journal", "append", "persistence")):
        setattr(runtime, attribute, _Measured(getattr(runtime, attribute), method, stage, lambda: current[0]))
    run_id = uuid4().hex
    stop = Event()
    worker = Thread(target=_load, args=(stop,), daemon=True) if condition != "idle" else None
    if worker is not None:
        worker.start()
    attempts = []
    try:
        for index in range(warmup + samples):
            current[0] = CycleTrace()
            start = perf_counter_ns()
            error, action = None, None
            try:
                with current[0].measure("ingestion"):
                    snapshot = NetworkStateSnapshot.from_dict({
                        "request_id": f"{run_id}-{index}", "node_id": "diagnostic-repeater",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(), "state_version": 1,
                        "predicted_fidelity": 0.9, "prediction_uncertainty": 0.01, "telemetry_age_s": 0.0,
                        "memory_state": "STORING", "available_pair_fidelities": [0.9, 0.9],
                        "memory_ages_s": [0.0, 0.0], "telemetry": {"source": "runtime_diagnostic"},
                        "integrity_verified": True, "operator_interlock": True, "out_of_distribution": False,
                        "provenance": {"evidence_kind": "simulated", "source": "runtime_diagnostic",
                                       "metadata": {"evidence_lane": "bell_fidelity_control",
                                                    "target_semantics": "simulated_future_bell_fidelity"}}})
                result = runtime.cycle(snapshot, mode="simulation")
                action = result["record"]["intent"]["action"]
            except Exception as exc:
                error = type(exc).__name__
            total = (perf_counter_ns() - start) / 1e6
            if index >= warmup:
                attempts.append({"index": index - warmup, "total_ms": total,
                                 "stages_ms": current[0].stages, "error": error, "action": action,
                                 "budget_exceeded": total > budget_ms})
    finally:
        stop.set()
        if worker is not None:
            worker.join(timeout=2)
    report = {"schema": "orion.runtime-diagnostic.v1", "evidence": "software_performance",
              "started_at_utc": started_at, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "isolated_simulated_repeater_cycle", "physical_action_performed": False,
              "condition": condition, "samples": samples, "warmup": warmup,
              "budget_ms": budget_ms, "budget_semantics": "declared diagnostic target, not a hardware guarantee",
              "p99_minimum_samples": 1000, "adequate_p99_sampling": samples >= 1000,
              "failed_attempts": sum(a["error"] is not None for a in attempts),
              "budget_exceeded": sum(a["budget_exceeded"] for a in attempts),
              "budget_exceeded_fraction": sum(a["budget_exceeded"] for a in attempts) / samples,
              "latency_ms": percentiles([a["total_ms"] for a in attempts]),
              "stages_ms": {stage: percentiles([a["stages_ms"][stage] for a in attempts
                                                 if stage in a["stages_ms"]])
                            for stage in ("ingestion", "policy", "supervision", "execution", "persistence")},
              "environment": host,
              "limitations": ["Instrumentation overhead is included and has not been isolated.",
                              "Stage totals omit lock waits, hashing and other runtime overhead included in total_ms.",
                              "One artificial repeater state; not polarisation inference or a complete network benchmark.",
                              "Host activity is not controlled; idle means no imposed diagnostic load.",
                              "Virtual timestamps and device deadlines are not measured hardware timing."],
              "attempts": attempts}
    with (output / "diagnostic.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--budget-ms", type=float, default=10.0)
    parser.add_argument("--condition", choices=["idle", "background_math_thread"], default="idle")
    args = parser.parse_args(argv)
    try:
        report = run_diagnostic(args.output, samples=args.samples, warmup=args.warmup,
                                budget_ms=args.budget_ms, condition=args.condition)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Diagnostic refused: {exc}\n")
    if sys.stdout is not None:
        print(json.dumps({key: report[key] for key in
                          ("condition", "samples", "failed_attempts", "budget_exceeded", "latency_ms")}, indent=2))
    return 1 if report["failed_attempts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
