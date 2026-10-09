"""Validate the formal TCC claim registry against frozen evidence artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.thesis_claims import (
    validate_claim_registry,
    write_claim_validation_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("docs/thesis/tcc_claim_registry.json"),
    )
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument(
        "--output-dir", type=Path, default=Path("outputs/tcc-claim-validation")
    )
    args = parser.parse_args()
    result = validate_claim_registry(
        args.registry, repository_root=args.repository_root
    )
    manifest = write_claim_validation_artifacts(result, args.output_dir)
    print(json.dumps({
        "report": result.report,
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))
    if not result.report["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
