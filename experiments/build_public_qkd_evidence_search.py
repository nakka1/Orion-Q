"""Build the public-QKD search registry and audit published aggregates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.public_qkd_evidence import (
    run_public_qkd_evidence_audit,
    write_public_qkd_evidence_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--aggregate-csv",
        type=Path,
        default=Path("data/reference/gavignet_2024_published_aggregate.csv"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_public_qkd_evidence_audit(args.aggregate_csv)
    manifest = write_public_qkd_evidence_artifacts(result, args.output_dir)
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "report": result.report,
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
