"""Measure real backend steps on fresh, isolated demonstration journals.

No UI/authentication or hardware qualification is claimed by this simulation
benchmark. All input validation, protocol processing and durable writes remain.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def run(args):
    from quantum_autopilot.qkd.dashboard_api import demo_service
    from quantum_autopilot.network_engine import routing

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    baseline = args.before_routing
    if baseline:
        # Explicit trusted pre-change snapshot supplied by the investigator.
        # The whole saved Router is used, preserving its stopping semantics.
        namespace = dict(vars(routing))
        exec(compile(baseline.read_text(encoding="utf-8"), str(baseline), "exec"), namespace)
        routing.Router = namespace["Router"]
    source = baseline or Path(routing.__file__)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    profiler = cProfile.Profile() if args.profile else None
    started = time.perf_counter()
    service = demo_service(ROOT, output / "state", seed=args.seed, preset=args.preset)
    startup_ms = (time.perf_counter() - started) * 1000
    print(json.dumps({"phase": "startup", "ms": startup_ms}), flush=True)
    samples = []
    if profiler:
        profiler.enable()
    try:
        for _ in range(args.steps):
            started = time.perf_counter()
            state = service.network_step()
            elapsed_ms = (time.perf_counter() - started) * 1000
            print(json.dumps({"phase": "step", "tick": state["tick"], "ms": elapsed_ms}), flush=True)
            samples.append({"tick": state["tick"], "wall_ms": elapsed_ms,
                "engine_ms": service.network_engine.timings["last_step_ms"],
                "demands": [{"id": d.get("demand_id"), "status": d.get("status"),
                             "plan": d.get("plan")} for d in state["demands"]]})
    finally:
        if profiler:
            profiler.disable()
            profiler.dump_stats(str(output / "profile.pstats"))
        service._release_controller_io_resources()
    values = sorted(sample["wall_ms"] for sample in samples)
    report = {"schema": "orion.network-step-benchmark.v1", "preset": args.preset,
        "seed": args.seed, "steps": args.steps, "startup_ms": startup_ms,
        "median_ms": statistics.median(values), "p95_ms": values[min(len(values)-1, int(.95*len(values)))],
        "max_ms": max(values), "steps_over_1s": sum(v > 1000 for v in values),
        "nodes": len(state["nodes"]), "links": len(state["links"]),
        "satellites": len(service.network_engine._satellites),
        "python": sys.version, "platform": platform.platform(), "profile": bool(profiler),
        "routing_source": str(source), "routing_source_sha256": source_hash,
        "physical_action_performed": False, "samples": samples,
        "limits": ["source backend simulation benchmark; no UI frame/GPU or hardware qualification",
                   "fresh journal on the stated local filesystem; elapsed times include durable writes"]}
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in ("samples", "python", "limits")}))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--preset", choices=("salvador_metro", "brazil-institutions-v1"), default="brazil-institutions-v1")
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261004)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--before-routing", type=Path, help="explicit trusted saved pre-change source; diagnostics only")
    args = parser.parse_args(argv)
    if not 1 <= args.steps <= 1000 or args.seed < 0:
        parser.error("steps must be 1..1000 and seed nonnegative")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
