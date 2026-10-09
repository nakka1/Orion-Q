"""Run the public Bristol HCF measured-QKD coexistence campaign."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.bristol_hcf_coexistence import (
    BristolHCFCoexistenceConfig,
    run_bristol_hcf_coexistence_campaign,
    write_bristol_hcf_coexistence_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Test the effect of a real classical-DWDM on/off condition on measured "
            "QBER/SKR using unexposed links as contemporaneous controls."
        )
    )
    parser.add_argument(
        "--dataset-dir", type=Path, required=True,
        help="Extracted Bristol data root, or the 4U_OnOff.csv file itself.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-replicates", type=int, default=50_000)
    parser.add_argument("--familywise-alpha", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()

    config = BristolHCFCoexistenceConfig(
        bootstrap_replicates=args.bootstrap_replicates,
        familywise_alpha=args.familywise_alpha,
        seed=args.seed,
    )
    result = run_bristol_hcf_coexistence_campaign(args.dataset_dir, config=config)
    manifest = write_bristol_hcf_coexistence_artifacts(
        result, args.output_dir, make_plots=not args.no_plots
    )
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "claim_scope": result.report["claim_scope"],
        "primary_results": json.loads(result.primary_results.to_json(orient="records")),
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
