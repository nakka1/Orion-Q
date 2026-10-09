"""Create a CSV/Parquet acquisition bundle for synchronized WDM/QKD data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.quantum.paired_telemetry import (
    create_paired_quantum_acquisition_bundle,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the paired quantum telemetry contract.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--formats", nargs="+", choices=["csv", "parquet"], default=["csv"])
    parser.add_argument("--dataset-id", default="REPLACE_ME")
    args = parser.parse_args()
    bundle = create_paired_quantum_acquisition_bundle(
        args.output_dir,
        formats=tuple(args.formats),
        dataset_id=args.dataset_id,
    )
    print(json.dumps({
        "data_files": [str(path.resolve()) for path in bundle.data_files],
        "manifest": str(bundle.manifest_path.resolve()),
        "data_dictionary": str(bundle.dictionary_path.resolve()),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
