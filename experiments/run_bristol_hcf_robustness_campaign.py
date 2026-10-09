"""Run robustness and falsification audits for the public Bristol HCF data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.bristol_hcf_robustness import (
    BristolHCFRobustnessConfig,
    run_bristol_hcf_robustness_campaign,
    write_bristol_hcf_robustness_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-replicates", type=int, default=50_000)
    parser.add_argument("--familywise-alpha", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    result = run_bristol_hcf_robustness_campaign(
        args.dataset_dir,
        config=BristolHCFRobustnessConfig(
            bootstrap_replicates=args.bootstrap_replicates,
            familywise_alpha=args.familywise_alpha,
            seed=args.seed,
        ),
    )
    manifest = write_bristol_hcf_robustness_artifacts(result, args.output_dir)
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "robustness_summary": json.loads(
            result.robustness_summary.to_json(orient="records")
        ),
        "temporal_permutation_control_identifiable": result.periodicity_audit[
            "temporal_permutation_control_identifiable"
        ],
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
