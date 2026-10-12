"""Measure the selected Linux GUI's default Qt display startup in a private fixture."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    if sys.platform != "linux" or os.geteuid() == 0 or sys.flags.optimize or args.report.exists():
        parser.error("unprivileged Linux with enabled validation gates and fresh report required")
    gui = args.gui.resolve(strict=True)
    with gui.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            parser.error("actual ELF required")
    os.umask(0o077)
    private = Path(tempfile.mkdtemp(prefix="orionq-qt-default-", dir="/var/tmp"))
    environment = dict(os.environ)
    environment.pop("QT_QPA_PLATFORM", None)
    with (private / "startup.log").open("xb") as log:
        value = subprocess.run([str(gui), "--startup-test", str(private / "startup.json"),
            "--startup-size", "1600x900", "--state-root", str(private / "state")],
            env=environment, cwd=private, stdout=log, stderr=log, timeout=150, shell=False)
    report_path = private / "startup.json"
    startup = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    passed = value.returncode == 0 and startup.get("passed") is True
    report = {"schema": "orion.linux-default-qt-display.v1", "status": "PASS" if passed else "UNAVAILABLE",
        "executable_sha256": sha(gui), "probe_sha256": sha(Path(__file__)), "exit_code": value.returncode,
        "qpa_platform_override": False, "DISPLAY": environment.get("DISPLAY"),
        "WAYLAND_DISPLAY": environment.get("WAYLAND_DISPLAY"), "XDG_RUNTIME_DIR": environment.get("XDG_RUNTIME_DIR"),
        "platform": startup.get("platform"), "cases": startup.get("cases", []),
        "monitor_geometry": startup.get("monitor_geometry"), "device_pixel_ratio": startup.get("device_pixel_ratio"),
        "heartbeat_p95_ms": startup.get("heartbeat_p95_ms"), "heartbeat_max_ms": startup.get("heartbeat_max_ms"),
        "physical_mouse_drag_verified": False, "independent_linux_desktop_verified": False,
        "private_state_exported": False, "private_logs_exported": False,
        "physical_action_performed": False, "hardware_validated": False,
        "failure_scope": None if passed else "Default Qt platform cannot start in this host display environment; explicit Wayland qualification is separate"}
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "report": str(args.report), "private_work": str(private)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
