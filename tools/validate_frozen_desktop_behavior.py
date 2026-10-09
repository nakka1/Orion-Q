"""Qualify the exact frozen Windows executable's icon and single-instance behavior."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


REQUIRED_CASES = (
    "resource-icon-32", "resource-icon-256", "app-window-taskbar-identity",
    "initial-load-activation", "second-instance-exclusion", "repeated-activation",
    "minimized-restore", "crash-restart",
)
REPORT_SCHEMA = "orion.frozen-desktop-behavior.v1"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_native_report(record, executable_sha256):
    """Reject incomplete, source-mode, stale-binary and non-Windows evidence."""
    cases = record.get("cases", [])
    names = [item.get("case") for item in cases]
    identity = next((item for item in cases if item.get("case") == "app-window-taskbar-identity"), {})
    if not (
        record.get("schema") == "orion.desktop-behavior.v1" and record.get("passed") is True
        and record.get("frozen") is True and record.get("operator_state_accessed") is False
        and record.get("executable_sha256") == executable_sha256
        and record.get("executable_sha256_after") == executable_sha256
        and len(names) == len(REQUIRED_CASES) and set(names) == set(REQUIRED_CASES)
        and all(item.get("passed") is True for item in cases)
        and identity.get("application_id") == "ORION-Q.Desktop"
        and identity.get("window", {}).get("taskbar_eligible") is True
        and identity.get("window", {}).get("visible") is True
    ):
        raise ValueError("frozen desktop icon and instance qualification is incomplete or belongs to another executable")
    return cases


def verify_gate(report, exe, *, evidence_root):
    """Verify outer and native evidence belong to this release and exact binary."""
    executable_sha256 = digest(exe)
    cases = report.get("cases", [])
    names = [item.get("case") for item in cases]
    if not (
        report.get("schema") == REPORT_SCHEMA and report.get("passed") is True
        and report.get("operator_state_accessed") is False
        and report.get("executable_sha256") == executable_sha256
        and report.get("executable_sha256_after") == executable_sha256
        and len(names) == len(REQUIRED_CASES) and set(names) == set(REQUIRED_CASES)
        and all(item.get("passed") is True for item in cases)
    ):
        raise ValueError("frozen desktop qualification gate is incomplete or belongs to another executable")
    reference = report.get("native_report")
    if not isinstance(reference, str) or not reference:
        raise ValueError("frozen desktop qualification has no native evidence report")
    root = Path(evidence_root).resolve()
    native = Path(reference)
    if not native.is_absolute():
        native = root / native
    native = native.resolve()
    if not native.is_relative_to(root) or native == root:
        raise ValueError("frozen desktop native evidence is outside this release's evidence directory")
    try:
        record = json.loads(native.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("frozen desktop native evidence report is missing or unreadable") from error
    native_cases = validate_native_report(record, executable_sha256)
    if native_cases != cases:
        raise ValueError("frozen desktop outer cases differ from the preserved native evidence")
    return native_cases


def validate(exe, output):
    exe, output = Path(exe).resolve(strict=True), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    original = digest(exe)
    report = {"schema": REPORT_SCHEMA, "passed": False, "executable_sha256": original,
              "executable_sha256_after": None, "cases": [], "error": None,
              "operator_state_accessed": False, "independent_machine_verified": False,
              "taskbar_shell_pixels_verified": False}
    try:
        if sys.platform != "win32":
            raise RuntimeError("this qualification requires the actual Windows Qt platform")
        environment = dict(os.environ, QT_QPA_PLATFORM="windows", QT_SCALE_FACTOR="1",
                           PYINSTALLER_RESET_ENVIRONMENT="1")
        for key in list(environment):
            if key.upper() in {"PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"}:
                del environment[key]
        system = environment.get("SystemRoot", r"C:\Windows")
        environment["PATH"] = os.pathsep.join((str(Path(system) / "System32"), system))
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        native_output = output / "native"
        command = [str(exe), "--desktop-behavior-test", str(native_output)]
        with (output / "stdout.log").open("w", encoding="utf-8") as stdout, (output / "stderr.log").open("w", encoding="utf-8") as stderr:
            process = subprocess.Popen(command, cwd=output, env=environment, stdout=stdout, stderr=stderr,
                                       startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                code = process.wait(timeout=150)
            except subprocess.TimeoutExpired:
                # Kill the coordinator's own descendants too, including a
                # primary window stuck in native startup. The PID comes only
                # from this Popen; unrelated ORION-Q windows are never targeted.
                subprocess.run([str(Path(system) / "System32/taskkill.exe"), "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                    startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
                raise TimeoutError("frozen desktop behavior diagnostic exceeded 150 seconds") from None
        target = native_output / "report.json"
        if code != 0 or not target.is_file():
            failure = json.loads(target.read_text(encoding="utf-8")).get("error") if target.is_file() else "no native report"
            raise RuntimeError(f"frozen desktop behavior process failed with exit {code}: {failure}")
        record = json.loads(target.read_text(encoding="utf-8"))
        report["cases"] = validate_native_report(record, original)
        report["native_report"] = str(target)
        report["passed"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    report["executable_sha256_after"] = digest(exe)
    if report["executable_sha256_after"] != original:
        report.update(passed=False, error="the executable changed during frozen desktop qualification")
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = ["ORION-Q frozen desktop qualification", f"Passed: {report['passed']}",
             f"Executable SHA-256: {original}", "Isolated MockProvider fixture; no operator state or physical acquisition."]
    lines.extend(f"{'PASS' if case['passed'] else 'FAIL'}: {case['case']}" for case in report["cases"])
    if report["error"]:
        lines.append("Error: " + report["error"])
    (output / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.exe, args.output_dir)
    print(f"Frozen desktop qualification: {'PASS' if result['passed'] else 'FAIL'}", flush=True)
    if result["error"]:
        print(result["error"], flush=True)
    raise SystemExit(0 if result["passed"] else 1)
