"""Run the raw-reading IrelandQCI hierarchical robustness audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.qkd_hierarchical_robustness import (
    QKDHierarchicalRobustnessConfig,
    run_qkd_hierarchical_robustness_campaign,
    write_qkd_hierarchical_robustness_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-replicates", type=int, default=20_000)
    parser.add_argument("--block-lengths", nargs="+", type=int, default=[1, 5, 10])
    parser.add_argument("--familywise-alpha", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=24_071)
    args = parser.parse_args()
    config = QKDHierarchicalRobustnessConfig(
        bootstrap_replicates=args.bootstrap_replicates,
        block_lengths=tuple(args.block_lengths),
        familywise_alpha=args.familywise_alpha,
        seed=args.seed,
    )
    result = run_qkd_hierarchical_robustness_campaign(
        args.dataset_dir, config=config
    )
    manifest = write_qkd_hierarchical_robustness_artifacts(
        result, args.output_dir
    )
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "primary_result": result.report["primary_result"],
        "scientific_conclusion": result.report["scientific_conclusion"],
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
