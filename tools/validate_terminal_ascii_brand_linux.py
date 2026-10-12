"""Run focused branding qualification in a fresh, unprivileged Linux fixture.

The existing preparation tool and campaign are reused without editing them.
Only named projections leave the private fixture; no credentials or journals do.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def project_campaign(raw, base_projection):
    result = base_projection(raw)
    result["schema"] = "orion.terminal-ascii-brand.campaign-public.v1"
    original = raw.get("pty", {})
    for name in ("branding", "loading", "ascii_output", "no_color", "termios_restored",
                 "key_observations", "key_latency", "viewport_sizes", "exit_codes"):
        if name in original:
            result["pty"][name] = original[name]
    previews = []
    for frame in original.get("public_previews", []):
        if frame["label"] not in {"brand loading 120x40", "brand overview 120x40",
                                   "brand help 120x40", "brand help 200x60",
                                   "brand no color 200x60", "brand loading 200x60", "brand loading 200x90",
                                   "brand help 200x90", "brand no color 200x90",
                                   "brand ascii 200x90", "brand ascii 200x40"}:
            continue
        frame = copy.deepcopy(frame)
        line = frame["lines"][1]["text"]
        if line.startswith("Peer: ") and " | received age " in line:
            start, end = 6, line.index(" | received age ")
            identity = line[start:end]
            if identity not in {"not authenticated", "identity not published by mixed status"}:
                replacement = "<test identity redacted>"
                if end - start < len(replacement):
                    raise ValueError("identity field is too short for explicit redaction")
                frame["lines"][1]["text"] = line[:start] + replacement.ljust(end - start) + line[end:]
        previews.append(frame)
    result["pty"]["public_previews"] = previews
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prepared-work", type=Path, help="Reuse only an owned PREPARED wheel/venv; always create fresh campaign state")
    parser.add_argument("--headless-only", action="store_true", help="Diagnostic finite ASCII read; no PTY claim")
    args = parser.parse_args(argv)
    if sys.platform != "linux" or os.geteuid() == 0 or sys.flags.optimize:
        raise RuntimeError("non-optimized, unprivileged Linux Python is required")
    repository = args.repository.resolve(strict=True)
    if args.output.exists():
        raise ValueError("public output destination must be new")
    os.umask(0o077)
    private = Path(tempfile.mkdtemp(prefix="orionq-terminal-ascii-brand-", dir="/var/tmp"))
    private.chmod(0o700)
    report = {"schema": "orion.terminal-ascii-brand.validation.v1", "status": "running",
              "runtime": "Ubuntu 24.04 Linux x86_64 / Python 3.12 / unprivileged UID 10001",
              "physical_action_performed": False, "hardware_validated": False,
              "source_profile": "fresh headless wheel; no sealed release modified",
              "commands": [], "private_state_exported": False, "private_logs_exported": False}
    prepare_tool = repository / "tools/validate_terminal_dashboard_linux.py"
    spec = importlib.util.spec_from_file_location("ascii_brand_projection", prepare_tool)
    projection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(projection)
    prepared, campaign_path = None, None

    def run(name, command, timeout):
        print(name, flush=True)
        value = subprocess.run([str(item) for item in command], cwd=private, text=True,
                               capture_output=True, timeout=timeout)
        (private / (name + ".log")).write_text(value.stdout + value.stderr, encoding="utf-8")
        report["commands"].append({"name": name, "exit_code": value.returncode})
        return value

    try:
        if args.prepared_work:
            prepared = args.prepared_work.resolve(strict=True)
            report["commands"].append({"name": "reuse_owned_prepared_wheel", "exit_code": 0})
            report["preparation_reused"] = True
        else:
            value = run("prepare_private_wheel", [sys.executable, "-I", prepare_tool,
                        "--repository", repository, "--output", private / "preparation-projection", "--prepare-only"], 240)
            if value.returncode:
                raise RuntimeError("private preparation failed")
            receipt = json.loads(value.stdout.splitlines()[-1])
            prepared = Path(receipt["work"]).resolve(strict=True)
        if prepared.parent != Path("/var/tmp") or prepared.stat().st_uid != os.geteuid():
            raise ValueError("preparation fixture ownership/path differs")
        binding = json.loads((prepared / "public/report.json").read_text())
        if binding["status"] != "PREPARED" or binding["uid"] != os.geteuid():
            raise ValueError("prepared fixture binding differs")
        if any(sha(repository / name) != expected for name, expected in binding["source_sha256"].items()):
            raise RuntimeError("repository changed since private snapshot")
        report["artifact"] = binding["artifact"]
        site = prepared / f"venv/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        matched, resources = [], {}
        wheel = prepared / "wheel" / binding["artifact"]["wheel_filename"]
        with zipfile.ZipFile(wheel) as archive:
            for source, expected in binding["source_sha256"].items():
                name = source.removeprefix("src/")
                if name not in archive.namelist():
                    continue
                if hashlib.sha256(archive.read(name)).hexdigest() != expected or sha(site / name) != expected:
                    raise RuntimeError("installed wheel/resource/source binding differs")
                matched.append(name)
                if not name.endswith(".py"):
                    resources[name] = expected
        report["artifact"]["wheel_source_files_compared"] = len(matched)
        report["packaged_resource_sha256"] = resources
        harness = private / "harness"
        harness.mkdir()
        copies = [("run_terminal_live_campaign.py", "run_terminal_live_campaign.py"),
                  ("terminal_dashboard_pty_probe.py", "terminal_dashboard_pty_probe.py"),
                  ("terminal_ascii_brand_pty_probe.py", "terminal_pty_probe.py")]
        for source, name in copies:
            shutil.copyfile(repository / "tools" / source, harness / name)
        report["probe_entrypoint"] = "headless_probe" if args.headless_only else "probe"
        if args.headless_only:
            with (harness / "terminal_pty_probe.py").open("a", encoding="utf-8") as stream:
                stream.write("\nprobe = headless_probe\n")
        report["executed_probe_sha256"] = sha(harness / "terminal_pty_probe.py")
        report["harness_sha256"] = {name: sha(repository / "tools" / name) for name in
            ("validate_terminal_ascii_brand_linux.py", "validate_terminal_dashboard_linux.py",
             "run_terminal_live_campaign.py", "terminal_dashboard_pty_probe.py", "terminal_ascii_brand_pty_probe.py")}
        campaign_path = private / "ascii-brand-campaign.json"
        value = run("focused_campaign", [prepared / "venv/bin/python", "-I", harness / "run_terminal_live_campaign.py",
                    "--repository", repository, "--state", private / "campaign-private",
                    "--report", campaign_path, "--pty", "--frames", "4"], 300)
        if value.returncode:
            raise RuntimeError("focused campaign failed")
        campaign = json.loads(campaign_path.read_text())
        if campaign["status"] != "PASS" or campaign["pty"]["status"] != "PASS":
            raise RuntimeError("focused campaign did not pass")
        report["status"] = "PASS"
    except Exception as error:
        report.update(status="FAIL", error_type=type(error).__name__)
    finally:
        (private / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    payloads = []
    if prepared:
        for name in ("source-hashes.json", "core-artifact.json"):
            path = prepared / "public" / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 3_000_000:
                raise ValueError("unexpected evidence path")
            payloads.append((name, path.read_bytes()))
    if campaign_path and campaign_path.is_file():
        public_campaign = project_campaign(json.loads(campaign_path.read_text()), projection.public_campaign)
        data = (json.dumps(public_campaign, indent=2, allow_nan=False) + "\n").encode()
        payloads.append(("terminal-campaign.json", data))
        report["campaign"] = {"filename": "terminal-campaign.json", "sha256": hashlib.sha256(data).hexdigest(),
                              "projection": "whitelisted metrics/checks and identity-redacted current viewports"}
    payloads.append(("report.json", (json.dumps(report, indent=2, allow_nan=False) + "\n").encode()))
    for name, data in payloads:
        if any(marker in data for marker in projection.MARKERS):
            raise ValueError("private material marker in public evidence: " + name)
    args.output.mkdir(parents=True)
    for name, data in payloads:
        (args.output / name).write_bytes(data)
    print(json.dumps({"status": report["status"], "work": str(private), "fixture": str(prepared)}), flush=True)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
