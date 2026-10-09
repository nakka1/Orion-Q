"""Print one read-only QKD claim-assurance decision as JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_autopilot.qkd import (
    ClaimRegistryAdapter,
    QKDOperatingPoint,
    build_assurance_workload,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("claim_id", help="formal claim identifier, for example C-MEASURED")
    parser.add_argument(
        "--use",
        choices=("display", "decision_support", "automation"),
        default="display",
        dest="requested_use",
    )
    parser.add_argument(
        "--operating-point-json",
        type=Path,
        help=(
            "JSON object satisfying QKDOperatingPoint; grid scope is computed "
            "and cannot be supplied manually"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    operating_point = None
    if args.operating_point_json is not None:
        payload = json.loads(args.operating_point_json.read_text(encoding="utf-8"))
        operating_point = QKDOperatingPoint.from_dict(payload)
    workload = build_assurance_workload(
        args.claim_id,
        requested_use=args.requested_use,
        operating_point=operating_point,
    )
    print(ClaimRegistryAdapter().assess(workload).to_json())


if __name__ == "__main__":
    main()
