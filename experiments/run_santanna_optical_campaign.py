"""Run the leakage-safe, multi-horizon Sant'Anna optical campaign."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.optical_campaign import (
    CampaignConfig,
    run_optical_campaign,
    write_campaign_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate recorded optical targets with causal temporal features, "
            "purged rolling-origin validation and paired block bootstrap."
        )
    )
    parser.add_argument("--csv", required=True, help="Sant'Anna HardFailure/SoftFailure CSV")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--targets", nargs="+", choices=["osnr_db", "pre_fec_ber", "wdm_failure"],
        default=["osnr_db", "pre_fec_ber"],
    )
    parser.add_argument("--horizons", nargs="+", type=int, default=[1, 3, 10, 30])
    parser.add_argument("--seeds", nargs="+", type=int, default=[42])
    parser.add_argument("--lags", nargs="+", type=int, default=[1, 2, 3, 5, 10, 30])
    parser.add_argument("--rolling-windows", nargs="+", type=int, default=[3, 5, 10, 30])
    parser.add_argument(
        "--models", nargs="+",
        choices=["ewma", "kalman", "ridge", "hist_gradient_boosting"],
        default=["ewma", "kalman", "ridge", "hist_gradient_boosting"],
    )
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--min-train-fraction", type=float, default=0.5)
    parser.add_argument("--purge-steps", type=int, default=20)
    parser.add_argument("--holdout-test-fraction", type=float, default=0.2)
    parser.add_argument("--no-device-holdout", action="store_true")
    parser.add_argument("--bootstrap-replicates", type=int, default=2_000)
    parser.add_argument("--bootstrap-block-length", type=int, default=30)
    parser.add_argument("--familywise-alpha", type=float, default=0.05)
    parser.add_argument(
        "--blank-failure-is-normal", action="store_true",
        help="Required for wdm_failure; records the authors' blank=normal convention.",
    )
    parser.add_argument("--no-predictions", action="store_true")
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()

    config = CampaignConfig(
        targets=tuple(args.targets),
        horizons=tuple(args.horizons),
        seeds=tuple(args.seeds),
        lag_steps=tuple(args.lags),
        rolling_windows=tuple(args.rolling_windows),
        models=tuple(args.models),
        n_splits=args.folds,
        min_train_fraction=args.min_train_fraction,
        requested_purge_steps=args.purge_steps,
        holdout_test_fraction=args.holdout_test_fraction,
        include_device_holdout=not args.no_device_holdout,
        bootstrap_replicates=args.bootstrap_replicates,
        bootstrap_block_length=args.bootstrap_block_length,
        familywise_alpha=args.familywise_alpha,
    )
    result = run_optical_campaign(
        args.csv,
        config=config,
        blank_failure_is_normal=args.blank_failure_is_normal,
    )
    manifest = write_campaign_artifacts(
        result,
        args.output_dir,
        include_predictions=not args.no_predictions,
        make_plots=not args.no_plots,
    )
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "claim_scope": result.report["claim_scope"],
        "selections": json.loads(result.tcc_table.to_json(orient="records")),
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
