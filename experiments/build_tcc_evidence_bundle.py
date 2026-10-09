"""Build the portable, licence-aware evidence bundle for the TCC claims."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.tcc_evidence_bundle import build_tcc_evidence_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-registry",
        type=Path,
        default=Path("docs/thesis/tcc_claim_registry.json"),
        help=(
            "Full output-referencing registry. If this is already portable, "
            "its generation_registry is used automatically."
        ),
    )
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument(
        "--bundle-dir", type=Path, default=Path("evidence/tcc/v1.0.0")
    )
    parser.add_argument(
        "--portable-registry",
        type=Path,
        default=Path("docs/thesis/tcc_claim_registry.json"),
    )
    parser.add_argument("--bundle-version", default="1.0.0")
    parser.add_argument("--generated-at", default="2026-09-03")
    args = parser.parse_args()

    result = build_tcc_evidence_bundle(
        args.source_registry,
        repository_root=args.repository_root,
        bundle_dir=args.bundle_dir,
        portable_registry_path=args.portable_registry,
        bundle_version=args.bundle_version,
        generated_at=args.generated_at,
    )
    print(json.dumps({
        "bundle_dir": result.bundle_dir.as_posix(),
        "portable_registry": result.portable_registry_path.as_posix(),
        "manifest": result.manifest_path.as_posix(),
        "tables": result.table_count,
        "selected_unique_rows": result.selected_row_count,
        "evidence_items": result.evidence_item_count,
        "validation": result.validation_report,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
