"""Run purged temporal baselines on the public Dryad STIKE 168-hour series."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.dryad_stike_temporal import (
    DryadSTIKETemporalConfig,
    run_dryad_stike_temporal_campaign,
    write_dryad_stike_temporal_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--horizons-hours", type=int, nargs="+", default=[1, 6, 24])
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--test-hours", type=int, default=12)
    parser.add_argument("--minimum-train-samples", type=int, default=48)
    parser.add_argument("--ridge-alpha", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    config = DryadSTIKETemporalConfig(
        lookback_hours=args.lookback_hours,
        horizons_hours=tuple(args.horizons_hours),
        folds=args.folds,
        test_hours=args.test_hours,
        minimum_train_samples=args.minimum_train_samples,
        ridge_alpha=args.ridge_alpha,
        seed=args.seed,
    )
    result = run_dryad_stike_temporal_campaign(args.dataset_dir, config=config)
    manifest = write_dryad_stike_temporal_artifacts(result, args.output_dir)
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "central_wdm_thesis_tested": result.report["central_wdm_thesis_tested"],
        "comparisons": json.loads(result.comparisons.to_json(orient="records")),
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
