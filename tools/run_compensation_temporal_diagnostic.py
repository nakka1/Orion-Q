"""Single-seed software diagnostic, not a robustness campaign or physics validation."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from quantum_autopilot.console.synthetic_compensation import SyntheticCompensationStream
from quantum_autopilot.control_plane.compensation_temporal import TemporalCompensationEvaluation, export_compensation_temporal_report
from quantum_autopilot.control_plane.optional_compensation import MODEL_IDS, evaluate_optional_compensation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qnet-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=False, exist_ok=False)
    # Fixed before inspecting scores. No seed/model selection, tuning or early stopping.
    seed, batches = 1337, 32
    envelope = SimpleNamespace(input_kind="synthetic", campaign="polarisation-temporal-diagnostic-1337",
                               topology=SimpleNamespace(nodes=[SimpleNamespace(id="artificial-node")]))
    summaries = []
    for model_id in MODEL_IDS:
        tracker = TemporalCompensationEvaluation()
        producer = SyntheticCompensationStream()
        for tick in range(batches):
            states = producer.push(envelope, seed, tick)
            state = next(item for item in states if item.metadata["compensation_operating_point"] == model_id.split(".")[2])
            tracker.observe(state)
            result = evaluate_optional_compensation(state, model_id=model_id, archive_path=args.qnet_zip)
            tracker.register(result)
        report = tracker.report(include_records=False)
        if (report["registered_predictions"], report["paired"], report["pending"], report["missed_target"]) != (32, 30, 2, 0):
            raise ValueError("unexpected target coverage")
        export_compensation_temporal_report(tracker.serialized(), args.output / f"{model_id}.json")
        summaries.append(report)
    with (args.output / "summary.json").open("x", encoding="utf-8") as stream:
        json.dump({"schema": "orion.compensation-temporal-diagnostic.v1", "seed": seed, "batches": batches,
                   "scope": "single-seed artificial diagnostic; no significance, tuning, closed-loop or physical validation",
                   "results": summaries}, stream, indent=2, allow_nan=False)
    print("4 checkpoints: 32 committed, 30 paired, 2 pending each. Inspect all scores, including regressions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
