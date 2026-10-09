"""Run the frozen exploratory classical/quantum Pareto audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.classical_quantum_pareto import (
    ClassicalQuantumParetoConfig,
    run_classical_quantum_pareto_campaign,
    write_classical_quantum_pareto_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qkd-multihead-dir", type=Path, required=True)
    parser.add_argument(
        "--aggregate-csv",
        type=Path,
        default=Path("data/reference/gavignet_2024_published_aggregate.csv"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--skip-input-manifest-verification", action="store_true")
    parser.add_argument("--bootstrap-replicates", type=int, default=20_000)
    args = parser.parse_args()
    result = run_classical_quantum_pareto_campaign(
        args.qkd_multihead_dir,
        args.aggregate_csv,
        config=ClassicalQuantumParetoConfig(
            bootstrap_replicates=args.bootstrap_replicates,
            verify_input_manifest=not args.skip_input_manifest_verification,
        ),
    )
    manifest = write_classical_quantum_pareto_artifacts(result, args.output_dir)
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "report": result.report,
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
