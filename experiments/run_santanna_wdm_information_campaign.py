"""Run the Sant'Anna incremental WDM-information experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.wdm_information import (
    SUPPORTED_MODELS,
    WDMInformationConfig,
    run_wdm_information_campaign,
    write_wdm_information_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Test whether causally aligned WDM telemetry improves BER/OSNR "
            "forecasts beyond the target's own history."
        )
    )
    parser.add_argument("--csv", required=True, help="Sant'Anna failure-campaign CSV")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--targets", nargs="+", choices=["osnr_db", "pre_fec_ber"],
        default=["osnr_db", "pre_fec_ber"],
    )
    parser.add_argument("--horizons", nargs="+", type=int, default=[1, 3, 10, 30])
    parser.add_argument("--models", nargs="+", choices=SUPPORTED_MODELS, default=list(SUPPORTED_MODELS))
    parser.add_argument("--seeds", nargs="+", type=int, default=[42])
    parser.add_argument("--window-size", type=int, default=20)
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--min-train-fraction", type=float, default=0.5)
    parser.add_argument("--purge-steps", type=int, default=0)
    parser.add_argument("--alignment-tolerance-s", type=float, default=5.0)
    parser.add_argument("--bootstrap-replicates", type=int, default=2_000)
    parser.add_argument("--bootstrap-block-length", type=int, default=30)
    parser.add_argument("--familywise-alpha", type=float, default=0.05)
    parser.add_argument("--hidden-size", type=int, default=16)
    parser.add_argument("--max-epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--no-predictions", action="store_true")
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()

    config = WDMInformationConfig(
        targets=tuple(args.targets),
        horizons=tuple(args.horizons),
        models=tuple(args.models),
        seeds=tuple(args.seeds),
        window_size=args.window_size,
        n_splits=args.folds,
        min_train_fraction=args.min_train_fraction,
        requested_purge_steps=args.purge_steps,
        alignment_tolerance_s=args.alignment_tolerance_s,
        bootstrap_replicates=args.bootstrap_replicates,
        bootstrap_block_length=args.bootstrap_block_length,
        familywise_alpha=args.familywise_alpha,
        hidden_size=args.hidden_size,
        max_epochs=args.max_epochs,
        batch_size=args.batch_size,
        early_stopping_patience=args.patience,
        learning_rate=args.learning_rate,
    )
    result = run_wdm_information_campaign(args.csv, config=config)
    manifest = write_wdm_information_artifacts(
        result,
        args.output_dir,
        include_predictions=not args.no_predictions,
        make_plots=not args.no_plots,
    )
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "claim_scope": result.report["claim_scope"],
        "positive_evidence": int(
            result.evidence["evidence_for_incremental_wdm_information"].sum()
        ),
        "tests": int(len(result.evidence)),
        "evidence": json.loads(result.evidence.to_json(orient="records")),
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
