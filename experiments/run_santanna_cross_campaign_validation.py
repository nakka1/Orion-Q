"""Confirm the frozen HardFailure optical models on SoftFailure."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.optical_transfer import (
    run_santanna_cross_campaign_validation,
    write_transfer_artifacts,
)


DEFAULT_PROTOCOL = (
    Path(__file__).resolve().parent
    / "configs"
    / "santanna_hard_to_soft_frozen_selection.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fit the preregistered optical models on HardFailure only and "
            "evaluate them once on the independent SoftFailure campaign."
        )
    )
    parser.add_argument("--hard-csv", required=True)
    parser.add_argument("--soft-csv", required=True)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--include-all-predictions", action="store_true")
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()

    result = run_santanna_cross_campaign_validation(
        args.hard_csv,
        args.soft_csv,
        args.protocol,
    )
    manifest = write_transfer_artifacts(
        result,
        args.output_dir,
        include_all_predictions=args.include_all_predictions,
        make_plots=not args.no_plots,
    )
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "claim_scope": result.report["claim_scope"],
        "confirmatory_tests": json.loads(
            result.confirmatory_tests.to_json(orient="records")
        ),
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
