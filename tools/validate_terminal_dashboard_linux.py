"""Qualify the current terminal source in a fresh, unprivileged Linux fixture.

The existing sealed venv is used only as a dependency source. The wheel, new
venv, TLS fixture, and journals stay private; only named reports are exported.
Run this with a Python from the qualified headless dependency environment.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import zipfile


MARKERS = (b"-----BEGIN PRIVATE KEY", b"-----BEGIN RSA PRIVATE KEY",
           b"-----BEGIN EC PRIVATE KEY", b"-----BEGIN ENCRYPTED PRIVATE KEY",
           b"Authorization: Bearer", b'"access_token":', b'"refresh_token":', b'"password":')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def public_campaign(raw):
    """Whitelist measurements/checks and explicitly redacted viewport evidence."""
    result = {name: raw[name] for name in ("status", "duration_s", "artifact_version", "fixture_only",
              "physical_action_performed", "hardware_qualified") if name in raw}
    result["schema"] = "orion.terminal-dashboard.campaign-public.v1"
    result["provenance"] = "isolated, self-created simulated full-institutional-topology fixture; physical output locked"
    result["private_state_exported"] = False
    result["redaction"] = "identity fields removed; process metadata, audit bodies and credential paths omitted"
    for name in ("pause", "backpressure", "shutdown"):
        if name in raw:
            result[name] = raw[name]
    if "watch" in raw:
        result["watch"] = {name: raw["watch"][name] for name in
                           ("exit_code", "duration_s", "presentation_step_calls", "read_latency")}
    if "presentation" in raw:
        result["presentation"] = {name: raw["presentation"][name] for name in
                                 ("render_latency", "views", "viewport_sizes", "selection_preserved")}
    if "operator_independence" in raw:
        result["operator_independence"] = {name: raw["operator_independence"][name] for name in
            ("revoked_operator_http_status", "anonymous_rejected", "held_tick_before_revocation",
             "received_tick_new_operator", "server_restarted")}
    if "dense_history" in raw:
        result["dense_history"] = raw["dense_history"]
    original = raw.get("pty", {})
    result["pty"] = {name: original[name] for name in ("schema", "status", "screen_parser", "screen_assertions",
        "actual_curses", "checks", "viewport_sizes", "key_latency_capacity", "capture_character_limit", "parser_qualification",
        "render_benchmark", "live_paint_observation", "digit_views", "tab_views", "selection_preserved_on_resize",
        "current_viewport_foreground_colors", "pause", "reload_static_completed", "reload_pending_observed",
        "details_scroll", "raw_scroll", "outage", "no_color", "vt100_control", "rep_sequences_consumed", "csi_sequences_consumed",
        "reservation_transition_parser", "reservation_transition_csi_counts", "dense_time_chart", "term", "keyboard_interrupt", "exit_code",
        "termios_restored", "sessions", "key_presses", "key_exchanges", "ansi_bytes_consumed", "key_latency",
        "key_observations", "duration_s", "captures", "captured_characters", "failed_check", "error_type")
        if name in original}
    if "render_benchmark" in result["pty"] and "protocol_sessions" in result["pty"]["render_benchmark"]:
        result["pty"]["render_benchmark"] = {name: value for name, value in result["pty"]["render_benchmark"].items()
                                             if name != "protocol_sessions"}
        result["projection_notes"] = ["Historical protocol_sessions default counter omitted: snapshot schema did not contain that field."]
    previews = []
    for frame in original.get("public_previews", []):
        if frame["label"] not in {"startup", "digit overview", "digit links", "digit nodes", "digit satellites",
                                   "digit mixed", "resize 80x24", "resize 120x40", "resize 200x60",
                                   "time chart 120x40", "time chart 200x60", "time chart 80x24"}:
            continue
        frame = copy.deepcopy(frame)
        line = frame["lines"][1]["text"]
        if line.startswith("Peer: ") and " | received age " in line:
            start, end = 6, line.index(" | received age ")
            original_identity = line[start:end]
            if original_identity not in {"not authenticated", "identity not published by mixed status"}:
                replacement = "<test identity redacted>"
                if end - start < len(replacement):
                    raise ValueError("identity field too short for explicit redaction")
                frame["lines"][1]["text"] = line[:start] + replacement.ljust(end - start) + line[end:]
        previews.append(frame)
    result["pty"]["public_previews"] = previews
    result["secrets_in_public_output"] = False
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mixed-fixture", type=Path)
    parser.add_argument("--vt100-control", action="store_true", help="Independent Reservations/scroll cycle with stock pyte")
    parser.add_argument("--minimum-history-samples", type=int, default=0)
    parser.add_argument("--time-chart", action="store_true", help="Focused real temporal-chart PTY validation")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--work", type=Path, help="Continue only a prepare-only fixture created by this tool")
    args = parser.parse_args(argv)
    if not 0 <= args.minimum_history_samples <= 120:
        parser.error("minimum-history-samples must be between 0 and 120")
    if args.time_chart and (args.vt100_control or args.minimum_history_samples < 64):
        parser.error("time-chart requires at least64history samples and cannot combine with vt100-control")
    if sys.platform != "linux" or os.geteuid() == 0 or sys.flags.optimize:
        raise RuntimeError("non-optimized, unprivileged Linux Python is required")
    repository = args.repository.resolve(strict=True)
    if args.output.exists():
        raise ValueError("public output destination must be new")
    os.umask(0o077)
    work = args.work.resolve(strict=True) if args.work else Path(tempfile.mkdtemp(prefix="orionq-terminal-dashboard-", dir="/var/tmp"))
    work.chmod(0o700)
    public = work / "public"
    project, venv = work / "project", work / "venv"
    report = {"schema": "orion.terminal-dashboard.validation.v1", "status": "running",
              "native_work": str(work), "uid": os.geteuid(), "gid": os.getegid(),
              "physical_action_performed": False, "hardware_validated": False,
              "source_profile": "fresh headless wheel; no sealed release modified", "commands": []}

    def run(name, command, *, timeout=240):
        print(name, flush=True)
        result = subprocess.run([str(item) for item in command], cwd=work, text=True,
                                capture_output=True, timeout=timeout)
        (public / (name + ".log")).write_text(result.stdout + result.stderr, encoding="utf-8")
        report["commands"].append({"name": name, "exit_code": result.returncode})
        if result.returncode:
            raise RuntimeError(name + " failed; private log retained")

    try:
        if args.work:
            original = json.loads((public / "report.json").read_text())
            if original["status"] != "PREPARED" or original["uid"] != os.geteuid():
                raise ValueError("only this user's prepared fixture can continue")
            report = original
            report["status"] = "running"
        else:
            public.mkdir()
            run("prepare", [sys.executable, repository / "packaging/core/prepare_source.py",
                            "--repository", repository, "--destination", project])
            source_hashes = {path.relative_to(project).as_posix(): sha(path)
                             for path in sorted((project / "src").rglob("*"))
                             if path.is_file() and "__pycache__" not in path.parts}
            (public / "source-hashes.json").write_text(json.dumps(source_hashes, indent=2) + "\n")
            shutil.copyfile(project / "core-artifact.json", public / "core-artifact.json")
            run("venv", [sys.executable, "-m", "venv", venv])
            site = venv / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
            dependency_paths = list(dict.fromkeys([sysconfig.get_path("purelib")] +
                [entry for entry in sys.path if entry and "site-packages" in Path(entry).parts]))
            # A .pth can itself refer to another qualified venv. Python does not
            # recursively process .pth files in an added directory, so preserve
            # the effective dependency search paths explicitly.
            (site / "qualified-dependencies.pth").write_text("\n".join(dependency_paths) + "\n")
            report["dependency_paths"] = dependency_paths
            run("wheel", [venv / "bin/python", "-m", "pip", "wheel", "--no-deps",
                          "--no-build-isolation", "--no-cache-dir", "--wheel-dir", work / "wheel", project])
            wheel, = (work / "wheel").glob("*.whl")
            # Dependency paths expose an older distribution of the same version;
            # install a fresh local copy without uninstalling that sealed venv.
            run("install", [venv / "bin/python", "-m", "pip", "install", "--no-deps",
                            "--ignore-installed", "--no-cache-dir", wheel])
            compared = []
            with zipfile.ZipFile(wheel) as archive:
                for relative, expected in source_hashes.items():
                    if not relative.endswith(".py"):
                        continue
                    name = relative.removeprefix("src/")
                    if hashlib.sha256(archive.read(name)).hexdigest() != expected or sha(site / name) != expected:
                        raise RuntimeError("installed wheel/source binding differs")
                    compared.append(name)
            report["artifact"] = {"version": json.loads((project / "core-artifact.json").read_text())["version"],
                                  "wheel_filename": wheel.name, "wheel_sha256": sha(wheel),
                                  "installed_source_modules_compared": len(compared),
                                  "sealed_release_modified": False}
            report["source_sha256"] = source_hashes
        # Readiness is a source binding, not just a package version string.
        sources = report["source_sha256"]
        changed = [name for name, expected in sources.items()
                   if not (repository / name).is_file() or sha(repository / name) != expected]
        if changed:
            raise RuntimeError("repository source changed since private snapshot")
        if args.prepare_only:
            report["status"] = "PREPARED"
        else:
            harness = work / "harness"
            harness.mkdir()
            # Compose test tools only. No application modules are replaced or
            # injected into the installed wheel or either child process.
            copies = [("run_terminal_live_campaign.py", "run_terminal_live_campaign.py"),
                      ("terminal_dashboard_pty_probe.py", "terminal_pty_probe.py")]
            if args.vt100_control:
                copies = [("run_terminal_live_campaign.py", "run_terminal_live_campaign.py"),
                          ("terminal_dashboard_pty_probe.py", "terminal_dashboard_pty_probe.py"),
                          ("terminal_dashboard_vt100_control.py", "terminal_pty_probe.py")]
            elif args.time_chart:
                copies = [("run_terminal_live_campaign.py", "run_terminal_live_campaign.py"),
                          ("terminal_dashboard_pty_probe.py", "terminal_dashboard_pty_probe.py"),
                          ("terminal_time_chart_pty_probe.py", "terminal_pty_probe.py")]
            for source, name in copies:
                shutil.copyfile(repository / "tools" / source, harness / name)
            report["harness_sha256"] = {source: sha(repository / "tools" / source) for source in
                ("run_terminal_live_campaign.py", "terminal_pty_probe.py", "terminal_dashboard_pty_probe.py",
                 "validate_terminal_dashboard_linux.py")}
            if args.vt100_control:
                report["harness_sha256"]["terminal_dashboard_vt100_control.py"] = sha(repository / "tools/terminal_dashboard_vt100_control.py")
            elif args.time_chart:
                report["harness_sha256"]["terminal_time_chart_pty_probe.py"] = sha(repository / "tools/terminal_time_chart_pty_probe.py")
            command = [venv / "bin/python", "-I", harness / "run_terminal_live_campaign.py",
                       "--repository", repository, "--state", work / "campaign-private",
                       "--report", public / "terminal-campaign.json", "--pty"]
            if args.mixed_fixture:
                command.extend(("--mixed-fixture", args.mixed_fixture.resolve(strict=True)))
            if args.minimum_history_samples:
                command.extend(("--minimum-history-samples", args.minimum_history_samples))
            run("campaign", command, timeout=600 if args.minimum_history_samples else 300)
            campaign = json.loads((public / "terminal-campaign.json").read_text())
            if campaign["status"] != "PASS" or campaign["pty"]["status"] != "PASS":
                raise RuntimeError("terminal campaign did not pass")
            report["campaign"] = {"filename": "terminal-campaign.json", "sha256": sha(public / "terminal-campaign.json"),
                                  "pty_checks": len(campaign["pty"]["checks"]),
                                  "secrets_in_public_output": campaign["secrets_in_public_output"]}
            report["status"] = "PASS"
    except Exception as error:
        report.update(status="FAIL", error_type=type(error).__name__)
    finally:
        (public / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if report["status"] in {"PASS", "PREPARED"}:
        allowed = ["source-hashes.json", "core-artifact.json"]
        payloads = []
        for name in allowed:
            path = public / name
            info = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size > 3_000_000:
                raise ValueError("unexpected public evidence file")
            data = path.read_bytes()
            if any(marker in data for marker in MARKERS):
                raise ValueError("private material marker in public evidence")
            payloads.append((name, data))
        public_report = {name: report[name] for name in ("schema", "status", "physical_action_performed",
            "hardware_validated", "source_profile", "commands", "artifact", "harness_sha256") if name in report}
        public_report.update(private_state_exported=False, private_logs_exported=False,
                             runtime="Ubuntu 24.04 Linux x86_64 / Python 3.12 / unprivileged UID 10001")
        if (public / "terminal-campaign.json").is_file():
            campaign_projection = public_campaign(json.loads((public / "terminal-campaign.json").read_text()))
            data = (json.dumps(campaign_projection, indent=2, allow_nan=False) + "\n").encode()
            if any(marker in data for marker in MARKERS):
                raise ValueError("private material marker in projected public evidence")
            payloads.append(("terminal-campaign.json", data))
            public_report["campaign"] = {"filename": "terminal-campaign.json", "sha256": hashlib.sha256(data).hexdigest(),
                                          "projection": "whitelisted metrics/checks; identity redacted; no raw capture export"}
        payloads.append(("report.json", (json.dumps(public_report, indent=2) + "\n").encode()))
        args.output.mkdir(parents=True)
        for name, data in payloads:
            (args.output / name).write_bytes(data)
    print(json.dumps({"status": report["status"], "public": str(public), "work": str(work)}), flush=True)
    return 0 if report["status"] in {"PASS", "PREPARED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
