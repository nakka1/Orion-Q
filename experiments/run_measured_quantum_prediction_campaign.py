"""Run the strict paired-WDM/measured-QBER/SKR temporal campaign."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.measured_quantum_prediction import (
    MEASURED_QUANTUM_TARGETS,
    MeasuredQuantumPredictionConfig,
    run_measured_quantum_prediction_campaign,
    write_measured_quantum_prediction_artifacts,
)
from quantum_twin.evaluation.wdm_information import SUPPORTED_MODELS


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Forecast measured future QBER/SKR from synchronized WDM telemetry."
    )
    parser.add_argument("--data", type=Path, required=True, help="CSV or Parquet")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--targets", nargs="+", choices=MEASURED_QUANTUM_TARGETS,
        default=list(MEASURED_QUANTUM_TARGETS),
    )
    parser.add_argument("--horizons", nargs="+", type=int, default=[1, 4])
    parser.add_argument(
        "--models", nargs="+", choices=SUPPORTED_MODELS,
        default=["edge_lstm", "edge_gru", "edge_tcn"],
    )
    parser.add_argument("--seeds", nargs="+", type=int, default=[42])
    parser.add_argument("--window-size", type=int, default=20)
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--min-train-fraction", type=float, default=0.5)
    parser.add_argument("--purge-steps", type=int, default=0)
    parser.add_argument("--cross-group-test-fraction", type=float, default=0.3)
    parser.add_argument("--bootstrap-replicates", type=int, default=2_000)
    parser.add_argument("--bootstrap-block-length", type=int, default=20)
    parser.add_argument("--familywise-alpha", type=float, default=0.05)
    parser.add_argument("--hidden-size", type=int, default=16)
    parser.add_argument("--max-epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--no-predictions", action="store_true")
    args = parser.parse_args()

    config = MeasuredQuantumPredictionConfig(
        targets=tuple(args.targets),
        horizons=tuple(args.horizons),
        models=tuple(args.models),
        seeds=tuple(args.seeds),
        window_size=args.window_size,
        n_splits=args.folds,
        min_train_fraction=args.min_train_fraction,
        requested_purge_steps=args.purge_steps,
        cross_group_test_fraction=args.cross_group_test_fraction,
        bootstrap_replicates=args.bootstrap_replicates,
        bootstrap_block_length=args.bootstrap_block_length,
        familywise_alpha=args.familywise_alpha,
        hidden_size=args.hidden_size,
        max_epochs=args.max_epochs,
        batch_size=args.batch_size,
        early_stopping_patience=args.patience,
        learning_rate=args.learning_rate,
    )
    result = run_measured_quantum_prediction_campaign(
        args.data, manifest_path=args.manifest, config=config
    )
    manifest = write_measured_quantum_prediction_artifacts(
        result, args.output_dir, include_predictions=not args.no_predictions
    )
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "claim_scope": result.report["claim_scope"],
        "positive_evidence": int(
            result.evidence["evidence_for_incremental_wdm_information"].sum()
        ),
        "tests": int(len(result.evidence)),
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
