"""Build the QC-PEB dataset capability matrix and evidence ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.qc_peb import write_qc_peb_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = write_qc_peb_artifacts(args.output_dir)
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
