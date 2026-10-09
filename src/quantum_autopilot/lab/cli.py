"""Run a traceable ORION-Q laboratory bench without opening a physical device."""
from __future__ import annotations

import argparse
import json
import multiprocessing
from pathlib import Path

from .service import LaboratoryService


def main(argv=None):
    parser = argparse.ArgumentParser(description="ORION-Q laboratory: emulated acquisition, replay and mTLS key delivery")
    parser.add_argument("--experiment", choices=("photon", "qkd", "all"), default="all")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    service = LaboratoryService(args.output)
    result = service.run({"experiment": args.experiment})
    print(json.dumps({"run_id": result["run_id"], "status": result["status"], "mode": result["mode"],
                      "receipt": str(service.root / result["run_id"] / "receipt.json")}, indent=2))
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
