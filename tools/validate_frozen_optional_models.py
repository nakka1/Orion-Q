"""Invoke only the packaged EXE, from isolated cwd/PATH, with optional external weights."""
import argparse
import json
from pathlib import Path

from quantum_autopilot.windows_acceptance import digest, invoke
from quantum_autopilot.model_intake.catalog import model_options


def validate(exe, output, *, qkd_zip=None, qnet_zip=None):
    exe = Path(exe).resolve(strict=True)
    if bool(qkd_zip) != bool(qnet_zip):
        raise ValueError("both reviewed package paths or neither required")
    packages = {} if qkd_zip is None else {"qkd": Path(qkd_zip).resolve(strict=True), "qnet": Path(qnet_zip).resolve(strict=True)}
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"passed": False, "executable_sha256": digest(exe), "checks": {},
              "restricted_path": True, "independent_machine_verified": False}
    expected_models = {option.model_id for option in model_options() if option.status == "offline_only"}
    try:
        for mode in ("without_packages", "external_packages") if packages else ("without_packages",):
            destination = output / mode
            args = ["--optional-model-test", "--output-dir", destination]
            if mode == "external_packages":
                args += ["--qkd-zip", packages["qkd"], "--qnet-zip", packages["qnet"]]
            invoke(exe, args, output, mode, 120)
            result = json.loads((destination / "optional-models-report.json").read_text(encoding="utf-8"))
            models = result.get("models", [])
            if (result.get("passed") is not True or result.get("frozen") is not True or result.get("mode") != mode
                    or result.get("executable_sha256") != report["executable_sha256"]
                    or result.get("physical_action_performed") is not False
                    or result.get("runtime_policy_activated") is not False
                    or len(models) != len(expected_models)
                    or {model.get("model_id") for model in models} != expected_models):
                raise ValueError("invalid frozen optional model report")
            report["checks"][mode] = True
        report["passed"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    with (output / "report.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--qkd-zip", type=Path)
    parser.add_argument("--qnet-zip", type=Path)
    args = parser.parse_args()
    result = validate(args.exe, args.output_dir, qkd_zip=args.qkd_zip, qnet_zip=args.qnet_zip)
    print(json.dumps(result))
    raise SystemExit(0 if result["passed"] else 2)
