"""Run the frozen post-discovery QKD Pareto falsification audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.qkd_pareto_falsification import (
    QKDParetoFalsificationConfig,
    run_qkd_pareto_falsification_campaign,
    write_qkd_pareto_falsification_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qkd-multihead-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--randomization-replicates", type=int, default=20_000)
    parser.add_argument("--bootstrap-replicates", type=int, default=20_000)
    parser.add_argument("--skip-input-manifest-verification", action="store_true")
    args = parser.parse_args()
    config = QKDParetoFalsificationConfig(
        randomization_replicates=args.randomization_replicates,
        bootstrap_replicates=args.bootstrap_replicates,
        verify_input_manifest=not args.skip_input_manifest_verification,
    )
    result = run_qkd_pareto_falsification_campaign(
        args.qkd_multihead_dir, config=config
    )
    manifest = write_qkd_pareto_falsification_artifacts(result, args.output_dir)
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "report": result.report,
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
