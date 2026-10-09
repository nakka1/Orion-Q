"""Run the post-discovery QKD monotonic dose-order falsification audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.qkd_dose_order_falsification import (
    QKDDoseOrderFalsificationConfig,
    run_qkd_dose_order_falsification_campaign,
    write_qkd_dose_order_falsification_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qkd-multihead-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ridge-alpha", type=float, default=1.0)
    parser.add_argument("--bootstrap-replicates", type=int, default=20_000)
    parser.add_argument("--familywise-alpha", type=float, default=0.05)
    parser.add_argument("--skip-input-manifest-verification", action="store_true")
    args = parser.parse_args()
    config = QKDDoseOrderFalsificationConfig(
        ridge_alpha=args.ridge_alpha,
        bootstrap_replicates=args.bootstrap_replicates,
        familywise_alpha=args.familywise_alpha,
        verify_input_manifest=not args.skip_input_manifest_verification,
    )
    result = run_qkd_dose_order_falsification_campaign(
        args.qkd_multihead_dir, config=config
    )
    manifest = write_qkd_dose_order_falsification_artifacts(
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
