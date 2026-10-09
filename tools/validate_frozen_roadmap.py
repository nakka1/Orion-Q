"""Exercise reviewed catalog exports and isolated diagnostic in the real executable."""
import argparse
import json
import sys
from pathlib import Path

from quantum_autopilot.windows_acceptance import digest, invoke


def validate(exe, output):
    exe, output = Path(exe).resolve(strict=True), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "orion.frozen-roadmap-check.v1", "executable_sha256": digest(exe),
              "passed": False, "independent_machine_verified": False, "checks": {}, "error": None}
    try:
        for speed, status in ((30, "BASELINE_RECOMMENDED"), (120, "INCONCLUSIVE"), (250, "MODEL_RECOMMENDED")):
            target = output / f"comparison-{speed}"
            invoke(exe, ["--compare-controllers", "--profile", f"qnet.compensation.calibrado.ridge|{speed}",
                         "--output", target], output, f"comparison-{speed}", 120)
            from quantum_autopilot.control_plane.controller_evidence import verify_report
            record = verify_report(json.loads((target / "comparison.json").read_bytes()))
            if record["status"] != status or record["policy_activated"] or record["physical_action_performed"]:
                raise ValueError("frozen comparison does not match reviewed evidence")
            report["checks"][f"comparison_{speed}"] = True
        invoke(exe, ["--diagnose-runtime", "--output", output / "runtime", "--samples", "20", "--warmup", "2"],
               output, "runtime", 120)
        diagnostic = json.loads((output / "runtime/diagnostic.json").read_bytes())
        if (diagnostic["samples"] != 20 or diagnostic["failed_attempts"] != 0
                or diagnostic["environment"]["frozen"] is not True or diagnostic["adequate_p99_sampling"]
                or diagnostic["physical_action_performed"]):
            raise ValueError("frozen diagnostic was not approved")
        report["checks"]["isolated_runtime_20_cycles"] = True
        report["runtime_summary"] = {key: diagnostic[key] for key in
                                      ("samples", "failed_attempts", "budget_ms", "budget_exceeded",
                                       "adequate_p99_sampling", "latency_ms")}
        report["passed"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"[-2000:]
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = validate(args.exe, args.output_dir)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["passed"] else 1)
