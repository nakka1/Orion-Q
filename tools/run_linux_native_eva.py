"""Qualify the frozen Linux operator's EVA presentation through a real PTY.

Reuse the inspected source campaign and EVA probe, changing process entrypoints
and argument parsing only. Python interpreter flags are explicitly inapplicable
to the ELF entrypoint; their ASCII-control coverage belongs to the wheel test.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def replace_exact(source, old, new):
    if source.count(old) != 1:
        raise ValueError("The source harness contract changed")
    return source.replace(old, new)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if sys.platform != "linux" or os.geteuid() == 0 or sys.flags.optimize:
        parser.error("unprivileged Linux without Python optimization is required")
    repository = args.repository.resolve(strict=True)
    bundle = args.bundle.resolve(strict=True)
    output = args.output.resolve()
    version = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    provenance_path = output.with_name(output.name.replace("campaign", "provenance", 1))
    if (output.exists() or provenance_path.exists()
            or not output.parent.is_relative_to((repository / f"outputs/release-{version}-20261011/linux").resolve())
            or re.fullmatch(r"native-eva-campaign(?:-[a-z0-9-]+)?\.json", output.name) is None):
        parser.error("fresh, version-bound public campaign and provenance report paths required")
    for name in ("orion-core", "orion-operator"):
        with (bundle / name).open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                parser.error("actual ELF entrypoints required")
    binding = []
    for name in ("terminal_live.py", "terminal_brand.py", "terminal_brand_art.py", "terminal_effect.py"):
        relative = "quantum_autopilot/service/" + name
        expected = sha(repository / "src" / relative)
        if sha(bundle / "_internal" / relative) != expected:
            raise ValueError("Frozen presentation source differs from checkout")
        binding.append({"path": relative, "sha256": expected})
    os.umask(0o077)
    private = Path(tempfile.mkdtemp(prefix="orionq-native-eva-", dir="/var/tmp"))
    campaign_source = repository / "tools/run_terminal_live_campaign.py"
    campaign = campaign_source.read_text(encoding="utf-8")
    campaign = replace_exact(campaign,
        'cli = [sys.executable, "-m", "quantum_autopilot.service.cli", "--config", str(config_path)]',
        f'cli = [{str(bundle / "orion-core")!r}, "--config", str(config_path)]')
    campaign = replace_exact(campaign,
        'pty_command = [sys.executable, "-I", "-m", "quantum_autopilot.service.operator_cli",',
        f'pty_command = [{str(bundle / "orion-operator")!r},')
    campaign = replace_exact(campaign,
        'report.update(status="blocked", error_type=type(error).__name__)',
        'report.update(status="blocked", error_type=type(error).__name__, error_status=getattr(error, "status", None))')
    runner = private / "run_terminal_live_campaign.py"
    runner.write_text(campaign, encoding="utf-8")
    dashboard = repository / "tools/terminal_dashboard_pty_probe.py"
    (private / dashboard.name).write_bytes(dashboard.read_bytes())
    probe_source = repository / "tools/terminal_eva_pty_probe.py"
    probe = replace_exact(probe_source.read_text(encoding="utf-8"),
        'cli_args = parser().parse_args(command[4:])', 'cli_args = parser().parse_args(command[1:])')
    start = probe.index('        ascii_command = ["/usr/bin/env", "LC_ALL=C"')
    end = probe.index('        terminal = _PTY(command)\n        terminals.append(terminal)\n        require(terminal.wait', start)
    probe = probe[:start] + '''        report["ascii_output"] = {
            "status": "NOT_APPLICABLE",
            "reason": "Frozen ELF does not expose Python -X/-I/-c interpreter controls; equivalent wheel ASCII launch controls are qualified separately",
            "native_utf8_and_no_color_pty_verified": True}
''' + probe[end:]
    probe_path = private / "terminal_pty_probe.py"
    probe_path.write_text(probe, encoding="utf-8")
    raw_path = private / "campaign.json"
    with (private / "campaign.log").open("xb") as stream:
        result = subprocess.run([sys.executable, "-I", str(runner), "--repository", str(bundle / "_internal"),
            "--state", str(private / "fresh-state"), "--report", str(raw_path), "--pty", "--frames", "4"],
            cwd=private, stdout=stream, stderr=stream, timeout=300, shell=False)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location("native_eva_projection", repository / "tools/validate_terminal_eva_linux.py")
    projection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(projection)
    spec = importlib.util.spec_from_file_location("native_dashboard_projection", repository / "tools/validate_terminal_dashboard_linux.py")
    dashboard_projection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dashboard_projection)
    public = projection.project_campaign(raw, dashboard_projection.public_campaign)
    passed = result.returncode == 0 and raw["status"] == raw.get("pty", {}).get("status") == "PASS"
    public.update(native_frozen_entrypoints=True, status="PASS" if passed else "FAIL",
                  artifact_version=raw.get("artifact_version"), physical_action_performed=False,
                  hardware_validated=False)
    if not passed:
        public["failure"] = {"stage": raw.get("stage"), "error_type": raw.get("error_type"),
                             "http_status": raw.get("error_status"), "acceptance_checks_relaxed": False}
    provenance = {"schema": "orion.native-eva.provenance.v1", "status": public["status"],
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "uid": os.geteuid(),
        "native_entrypoints": [{"name": name, "sha256": sha(bundle / name)} for name in ("orion-core", "orion-operator")],
        "source_binding": binding, "application_modules_replaced": False,
        "source_harness_sha256": sha(campaign_source), "adapted_harness_sha256": sha(runner),
        "source_probe_sha256": sha(probe_source), "adapted_probe_sha256": sha(probe_path),
        "dashboard_helper_sha256": sha(dashboard), "validation_tool_sha256": sha(Path(__file__)),
        "adaptations": ["native ELF subprocess entrypoints", "ELF arguments begin at argv[1]",
            "failure diagnostics retain HTTP status only, never response bodies",
            "Python interpreter ASCII-control subscenario reported NOT_APPLICABLE, never counted as passed"],
        "private_state_exported": False, "private_logs_exported": False}
    output.write_text(json.dumps(public, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": public["status"], "report": str(output), "private_work": str(private)}), flush=True)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
