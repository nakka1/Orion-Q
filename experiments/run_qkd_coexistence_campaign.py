"""Analyse measured QKD outcomes under controlled classical-signal injection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.qkd_coexistence import (
    QKDCoexistenceConfig,
    run_qkd_coexistence_campaign,
    write_qkd_coexistence_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test measured QBER/SKR dependence on optical coexistence conditions."
    )
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-replicates", type=int, default=50_000)
    parser.add_argument("--familywise-alpha", type=float, default=0.05)
    parser.add_argument("--ridge-alpha", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()

    config = QKDCoexistenceConfig(
        bootstrap_replicates=args.bootstrap_replicates,
        familywise_alpha=args.familywise_alpha,
        ridge_alpha=args.ridge_alpha,
        seed=args.seed,
    )
    result = run_qkd_coexistence_campaign(args.dataset_dir, config=config)
    manifest = write_qkd_coexistence_artifacts(
        result, args.output_dir, make_plots=not args.no_plots
    )
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "claim_scope": result.report["claim_scope"],
        "primary_measured_effects": json.loads(
            result.primary_contrasts.to_json(orient="records")
        ),
        "predictive_comparisons": json.loads(
            result.predictive_comparisons.to_json(orient="records")
        ),
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
