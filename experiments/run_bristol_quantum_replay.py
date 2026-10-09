"""Run Bristol field telemetry through the original Bell-fidelity model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.bristol_quantum_replay import (
    BristolQuantumReplayConfig,
    run_bristol_quantum_replay,
    write_bristol_quantum_replay_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--horizons", nargs="+", type=int, default=[1, 4])
    parser.add_argument(
        "--models", nargs="+",
        choices=["ridge", "hist_gradient_boosting", "edge_lstm", "edge_gru", "edge_tcn"],
        default=["ridge", "edge_lstm", "edge_gru", "edge_tcn"],
    )
    parser.add_argument("--seeds", nargs="+", type=int, default=[42])
    parser.add_argument("--window-size", type=int, default=20)
    parser.add_argument("--folds", type=int, default=2)
    parser.add_argument("--bootstrap-replicates", type=int, default=20_000)
    parser.add_argument("--bootstrap-block-length", type=int, default=16)
    parser.add_argument("--aer-grid-points", type=int, default=257)
    parser.add_argument("--hidden-size", type=int, default=8)
    parser.add_argument("--max-epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--patience", type=int, default=4)
    parser.add_argument("--no-predictions", action="store_true")
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()

    config = BristolQuantumReplayConfig(
        horizons=tuple(args.horizons),
        models=tuple(args.models),
        seeds=tuple(args.seeds),
        window_size=args.window_size,
        n_splits=args.folds,
        bootstrap_replicates=args.bootstrap_replicates,
        bootstrap_block_length=args.bootstrap_block_length,
        aer_grid_points=args.aer_grid_points,
        hidden_size=args.hidden_size,
        max_epochs=args.max_epochs,
        batch_size=args.batch_size,
        early_stopping_patience=args.patience,
    )
    result = run_bristol_quantum_replay(args.dataset_dir, config=config)
    manifest = write_bristol_quantum_replay_artifacts(
        result,
        args.output_dir,
        include_predictions=not args.no_predictions,
        make_plots=not args.no_plots,
    )
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "claim_scope": result.report["claim_scope"],
        "mapping_audit": result.report["mapping_audit"],
        "evidence": json.loads(result.evidence.to_json(orient="records")),
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
