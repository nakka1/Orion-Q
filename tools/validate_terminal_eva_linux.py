"""Qualify local EVA presentation against a private authenticated Linux service.

Only named metrics and masked/redacted current viewports leave the fixture.
The preparation, campaign and parser helpers are reused without changing them.
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
import signal
import subprocess
import sys
import tempfile
import zipfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def owned_process_tree(root_pid):
    """Record only ancestry/starttime of our Linux processes for bounded cleanup."""
    processes = {}
    for directory in Path("/proc").iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            if directory.stat().st_uid != os.geteuid():
                continue
            fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
            processes[int(directory.name)] = (int(fields[1]), fields[19])
        except (OSError, ValueError, IndexError):
            continue
    selected = {root_pid}
    while True:
        children = {pid for pid, (parent, _) in processes.items() if parent in selected}
        enlarged = selected | children
        if enlarged == selected:
            break
        selected = enlarged
    return {pid: processes[pid][1] for pid in selected if pid in processes}


def signal_owned(processes, signum):
    """Do not signal a PID reused by another process after the ancestry snapshot."""
    for pid, starttime in processes.items():
        try:
            directory = Path("/proc") / str(pid)
            fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
            if directory.stat().st_uid == os.geteuid() and fields[19] == starttime:
                os.kill(pid, signum)
        except (OSError, ValueError, IndexError):
            continue


def project_campaign(raw, base_projection):
    result = base_projection(raw)
    result["schema"] = "orion.terminal-eva.campaign-public.v1"
    original = raw.get("pty", {})
    for name in ("palette", "backend_independence", "ascii_output", "termios_restored", "key_observations",
                 "key_latency", "viewport_sizes", "exit_codes", "typed_characters", "input_privacy_scope"):
        if name in original:
            result["pty"][name] = original[name]
    result["redaction"] = "Peer identity and local test account explicitly redacted; password remains masked"
    previews = []
    for frame in original.get("public_previews", []):
        if frame["label"] not in {"eva standard 120x40", "eva masked form 120x40", "eva notice 120x40",
                                   "eva notice 200x60", "eva dashboard 120x40", "eva plain 120x40",
                                   "eva unplugged 120x40", "eva no color notice 120x40", "eva ascii notice 120x40"}:
            continue
        frame = copy.deepcopy(frame)
        for row in frame["lines"]:
            line = row["text"]
            if line.startswith("Peer: ") and " | received age " in line:
                start, end = 6, line.index(" | received age ")
                replacement = "<test identity redacted>"
                if end - start < len(replacement):
                    raise ValueError("identity field is too short for explicit redaction")
                row["text"] = line[:start] + replacement.ljust(end - start) + line[end:]
            elif line.startswith("Display login:"):
                row["text"] = "Display login: <local test account redacted>".ljust(frame["columns"])[:frame["columns"]]
        if any("thirdpilot" in row["text"] or "THIRDPILOT" in row["text"] for row in frame["lines"]):
            raise ValueError("unmasked display phrase must not be exported")
        previews.append(frame)
    result["pty"]["public_previews"] = previews
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prepared-work", type=Path, help="Reuse only an owned PREPARED source-bound wheel; fresh campaign state")
    args = parser.parse_args(argv)
    if sys.platform != "linux" or os.geteuid() == 0 or sys.flags.optimize:
        raise RuntimeError("non-optimized, unprivileged Linux Python is required")
    repository = args.repository.resolve(strict=True)
    if args.output.exists():
        raise ValueError("public output destination must be new")
    os.umask(0o077)
    private = Path(tempfile.mkdtemp(prefix="orionq-terminal-eva-", dir="/var/tmp"))
    private.chmod(0o700)
    report = {"schema": "orion.terminal-eva.validation.v1", "status": "running",
              "runtime": "Ubuntu 24.04 Linux x86_64 / Python 3.12 / unprivileged UID 10001",
              "physical_action_performed": False, "hardware_validated": False,
              "source_profile": "fresh headless wheel; local theme never authenticates; sealed release unchanged",
              "commands": [], "private_state_exported": False, "private_logs_exported": False}
    prepare_tool = repository / "tools/validate_terminal_dashboard_linux.py"
    spec = importlib.util.spec_from_file_location("eva_projection", prepare_tool)
    projection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(projection)
    prepared, campaign_path = None, None

    def run(name, command, timeout):
        print(name, flush=True)
        process = subprocess.Popen([str(item) for item in command], cwd=private, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            descendants = owned_process_tree(process.pid)
            # Let the campaign's existing finally blocks restore its PTYs and service.
            process.send_signal(signal.SIGINT)
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                signal_owned(descendants, signal.SIGTERM)
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    signal_owned(descendants, signal.SIGKILL)
                    stdout, stderr = process.communicate(timeout=5)
            signal_owned(descendants, signal.SIGTERM)
            report["timeout_cleanup"] = {"cooperative_interrupt_requested": True,
                                         "owned_processes_tracked": len(descendants)}
        value = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        (private / (name + ".log")).write_text(stdout + stderr, encoding="utf-8")
        report["commands"].append({"name": name, "exit_code": value.returncode, "timed_out": timed_out})
        if timed_out:
            raise RuntimeError("private command exceeded its deadline")
        return value

    try:
        if args.prepared_work:
            prepared = args.prepared_work.resolve(strict=True)
            report["commands"].append({"name": "reuse_owned_prepared_wheel", "exit_code": 0})
            report["preparation_reused"] = True
        else:
            value = run("prepare_private_wheel", [sys.executable, "-I", prepare_tool, "--repository", repository,
                        "--output", private / "preparation-projection", "--prepare-only"], 240)
            if value.returncode:
                raise RuntimeError("private preparation failed")
            prepared = Path(json.loads(value.stdout.splitlines()[-1])["work"]).resolve(strict=True)
        if prepared.parent != Path("/var/tmp") or prepared.stat().st_uid != os.geteuid():
            raise ValueError("preparation fixture ownership/path differs")
        binding = json.loads((prepared / "public/report.json").read_text())
        if binding["status"] != "PREPARED" or binding["uid"] != os.geteuid():
            raise ValueError("prepared fixture binding differs")
        if any(sha(repository / name) != expected for name, expected in binding["source_sha256"].items()):
            raise RuntimeError("repository changed since private snapshot")
        report["artifact"] = binding["artifact"]
        site = prepared / f"venv/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        wheel = prepared / "wheel" / binding["artifact"]["wheel_filename"]
        if sha(wheel) != binding["artifact"]["wheel_sha256"]:
            raise RuntimeError("prepared wheel artifact hash differs")
        matched, resources = [], {}
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
        for source, name in (("run_terminal_live_campaign.py", "run_terminal_live_campaign.py"),
                             ("terminal_dashboard_pty_probe.py", "terminal_dashboard_pty_probe.py"),
                             ("terminal_eva_pty_probe.py", "terminal_pty_probe.py")):
            shutil.copyfile(repository / "tools" / source, harness / name)
        report["harness_sha256"] = {name: sha(repository / "tools" / name) for name in
            ("validate_terminal_eva_linux.py", "validate_terminal_dashboard_linux.py", "run_terminal_live_campaign.py",
             "terminal_dashboard_pty_probe.py", "terminal_eva_pty_probe.py")}
        report["executed_probe_sha256"] = sha(harness / "terminal_pty_probe.py")
        campaign_path = private / "eva-campaign.json"
        value = run("focused_campaign", [prepared / "venv/bin/python", "-I", harness / "run_terminal_live_campaign.py",
                    "--repository", repository, "--state", private / "campaign-private", "--report", campaign_path,
                    "--pty", "--frames", "4"], 300)
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
        data = (json.dumps(project_campaign(json.loads(campaign_path.read_text()), projection.public_campaign),
                           indent=2, allow_nan=False) + "\n").encode()
        payloads.append(("terminal-campaign.json", data))
        report["campaign"] = {"filename": "terminal-campaign.json", "sha256": hashlib.sha256(data).hexdigest(),
                              "projection": "whitelisted metrics/checks and masked/redacted current viewports"}
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
