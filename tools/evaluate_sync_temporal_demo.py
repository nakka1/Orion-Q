"""Explicit bounded software diagnostic on an artificial stream, not a benchmark."""
import argparse
import hashlib
import json
from pathlib import Path

from quantum_autopilot.console.synthetic import artificial_snapshot
from quantum_autopilot.console.synthetic_sync import SyntheticSyncStream
from quantum_autopilot.control_plane.optional_sync import MODEL_ID, evaluate_optional_sync
from quantum_autopilot.control_plane.sync_temporal import TemporalSyncEvaluation, export_temporal_report


def run(archive, *, seed=1337, frames=128):
    if type(frames) is not int or not 10 <= frames <= 263:
        raise ValueError("frames must be an integer in 10..263")
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("seed must be uint32")
    producer = SyntheticSyncStream()
    tracker = TemporalSyncEvaluation(max_predictions=frames - 7)
    for tick in range(frames):
        view = artificial_snapshot(seed=seed, tick=tick)
        state = producer.push(view, seed, tick)
        if state is None:
            continue
        tracker.observe(state)
        forecast = evaluate_optional_sync(state, model_id=MODEL_ID, archive_path=archive)
        if forecast.status != "evaluated":
            raise ValueError(forecast.reason + ": " + forecast.detail)
        tracker.register(forecast)
    report = tracker.report()
    if (report["paired"], report["pending"], report["missed_target"]) != (frames - 9, 2, 0):
        raise ValueError("unexpected pairing coverage in deterministic replay")
    root = Path(__file__).resolve().parents[1]
    sources = ["src/quantum_autopilot/console/synthetic_sync.py",
               "src/quantum_autopilot/control_plane/sync_temporal.py",
               "src/quantum_autopilot/control_plane/optional_sync.py",
               "src/quantum_autopilot/model_intake/inference.py"]
    report["diagnostic"] = {
        "seed": seed, "emitted_frames": frames, "warmup_frames_without_prediction": 7,
        "execution": "sequential replay without worker latency; not wall-clock UI performance",
        "selection": "single fixed seed/duration; no search, training or tuning",
        "generator": "hand-authored sinusoid plus seeded noise, independent of model and WDM",
        "source_sha256": {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in sources},
    }
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qkd-zip", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--frames", type=int, default=128)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output already exists; choose a new file")
    report = run(args.qkd_zip, seed=args.seed, frames=args.frames)
    export_temporal_report(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), args.output)
    print(json.dumps({key: report[key] for key in ("paired", "pending", "missed_target", "model_mae_s",
                                                  "persistence_mae_s", "model_rmse_s", "persistence_rmse_s")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
