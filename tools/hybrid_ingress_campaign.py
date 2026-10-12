"""Existing authenticated OGS ingress exercised with explicitly synthetic packets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def run_campaign(output_dir, seeds=(101, 202, 303)):
    sys.path.insert(0, str(ROOT / "src"))
    from quantum_autopilot.quantum_protocols.satellite_validation import validate_satellite_pipeline
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    checks = {}
    runs = []
    for seed in seeds:
        directory = output / f"seed-{seed}"
        result = validate_satellite_pipeline(directory, seed=seed)
        checks.update({f"seed-{seed}/{name}": value for name, value in result["checks"].items()})
        checks[f"seed-{seed}/completed_without_exception"] = result.get("passed") is True and "error" not in result
        path = directory / "report.json"
        runs.append({"seed": seed, "passed": result["passed"], "check_count": result["check_count"],
                     "report_path": f"seed-{seed}/report.json",
                     "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "error": result.get("error")})
    report = {"schema": "orionq.hybrid-ingress-validation.v1",
              "status": "partial_verified" if all(checks.values()) else "failed",
              "checks": checks, "runs": runs, "seeds": list(seeds),
              "evidence_kind": "seeded synthetic input through authenticated local WebSocket and unchanged NetworkEngine",
              "scope": "housekeeping, tracking and TDC streams, classical receipt, processing, persistence and publication",
              "limitations": ["No RF/TDC hardware or live satellite payload", "No independent mission ICD qualification",
                              "Conditional BBM92 only; not decoy-BB84 or SNS", "Laboratory high-yield case uses hypothetical 6 m aperture"],
              "physical_hardware_validated": False, "security_certified": False,
              "physical_actuation": False, "certified_secret_bits": 0}
    with (output / "report.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    return report
