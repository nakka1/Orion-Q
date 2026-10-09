"""Export a hash-bound measured-WDM optical-health artifact.

This command intentionally makes no quantum prediction.  It records the latest
per-device optical state and, when supplied, attaches the registered temporal
forecast result for the exact same CSV hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from quantum_autopilot.evidence_lanes import MeasuredOpticalHealthAdapter


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--forecast-report", type=Path, default=None)
    parser.add_argument("--expected-dataset-sha256", default=None)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/evidence_lanes/measured_optical_health"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = MeasuredOpticalHealthAdapter(
        args.csv,
        forecast_report_path=args.forecast_report,
        expected_dataset_sha256=args.expected_dataset_sha256,
    ).snapshot()
    report = {
        "study": "measured WDM optical-health evidence lane",
        "scientific_boundary": (
            "Recorded BER/OSNR/power is exposed as optical evidence only; no QBER, "
            "SKR or Bell fidelity is inferred and no control action is authorized."
        ),
        "snapshot": snapshot.to_dict(),
    }
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "1.0.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifacts": {
            "report.json": _sha256_file(report_path),
            "source_dataset": snapshot.provenance.dataset_hash,
            **(
                {"forecast_report": snapshot.provenance.artifact_hashes["optical_forecast_report"]}
                if "optical_forecast_report" in snapshot.provenance.artifact_hashes
                else {}
            ),
        },
        "physical_action_performed": False,
        "metric_conversion_performed": False,
    }
    manifest_path = output_dir / "artifact_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"report={report_path}")
    print(f"manifest={manifest_path}")
    print(
        "rows={rows} devices={devices} predictive_result={positive}/{tested}".format(
            rows=snapshot.quality["rows"],
            devices=snapshot.quality["devices"],
            positive=snapshot.evidence_positive_count,
            tested=snapshot.evidence_test_count,
        )
    )
    print("quantum_metric_inferred=false physical_action_performed=false")


if __name__ == "__main__":
    main()
