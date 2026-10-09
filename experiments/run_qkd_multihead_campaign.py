"""Run exploratory DualHead/TriHead transfer on measured QKD sweeps."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.qkd_multihead import (
    QKDMultiHeadConfig,
    run_qkd_multihead_campaign,
    write_qkd_multihead_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate QKD DualHead/TriHead without fabricating a temporal axis."
    )
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seeds", nargs="+", type=int, default=[13, 42, 97])
    parser.add_argument("--hidden-size", type=int, default=10)
    parser.add_argument("--epochs", type=int, default=180)
    parser.add_argument("--learning-rate", type=float, default=3e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--pareto-dominance-weight", type=float, default=0.25)
    parser.add_argument("--pareto-margin", type=float, default=0.05)
    parser.add_argument("--bootstrap-replicates", type=int, default=20_000)
    parser.add_argument("--familywise-alpha", type=float, default=0.05)
    parser.add_argument("--no-predictions", action="store_true")
    args = parser.parse_args()

    config = QKDMultiHeadConfig(
        seeds=tuple(args.seeds),
        hidden_size=args.hidden_size,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        pareto_dominance_weight=args.pareto_dominance_weight,
        pareto_margin=args.pareto_margin,
        bootstrap_replicates=args.bootstrap_replicates,
        familywise_alpha=args.familywise_alpha,
    )
    result = run_qkd_multihead_campaign(args.dataset_dir, config=config)
    manifest = write_qkd_multihead_artifacts(
        result, args.output_dir, include_predictions=not args.no_predictions
    )
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "claim_scope": result.report["claim_scope"],
        "positive_comparisons": int(
            result.comparisons["significant_positive_improvement"].sum()
        ),
        "comparisons": int(len(result.comparisons)),
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
