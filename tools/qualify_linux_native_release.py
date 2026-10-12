"""Verify freshly extracted Linux artifacts, frozen sources and GUI/core mTLS.

The Wayland test uses WSLg's compositor when available; it is a programmatic
startup/resize/close test, not qualification on an independent Linux desktop.
All operational fixtures and process logs stay in owned /var/tmp directories.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--evidence", type=Path, help="fresh evidence directory within this version's Linux campaign")
    parser.add_argument("--gui-release", type=Path, help="GUI build directory in this version's Linux release tree")
    parser.add_argument("--headless-release", type=Path, help="headless build directory in this version's Linux release tree")
    parser.add_argument("--source-freeze", type=Path, help="source manifest within this version's campaign")
    parser.add_argument("--campaign-date", default="20261011", help="YYYYMMDD campaign suffix; default preserves earlier releases")
    args = parser.parse_args(argv)
    require(re.fullmatch(r"[0-9]{8}", args.campaign_date) is not None, "Campaign date must be YYYYMMDD")
    datetime.strptime(args.campaign_date, "%Y%m%d")
    require(sys.platform == "linux" and os.geteuid() != 0 and not sys.flags.optimize,
            "Actual Linux, non-root UID, enabled validation gates are required")
    repository = args.repository.resolve(strict=True)
    version = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    require(isinstance(version, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is not None,
            "Canonical numeric release version is required")
    campaign_root = repository / f"outputs/release-{version}-{args.campaign_date}"
    linux_evidence_root = campaign_root / "linux"
    evidence = (args.evidence or linux_evidence_root).resolve()
    require(evidence.is_relative_to(linux_evidence_root.resolve()), "Evidence must stay within this version's Linux campaign")
    require(not (evidence / "linux-release-qualification.json").exists(), "Existing evidence is preserved")
    freeze_path = (args.source_freeze or campaign_root / "windows/source-freeze.json").resolve(strict=True)
    require(freeze_path.is_relative_to(campaign_root.resolve()), "Freeze must belong to this version's campaign")
    releases = {profile: (getattr(args, profile + "_release") or repository / f"release/linux/{version}-native-{profile}-{args.campaign_date}").resolve(strict=True)
                for profile in ("headless", "gui")}
    release_root = (repository / "release/linux").resolve()
    require(all(path.parent == release_root and path.name.startswith(version + "-") for path in releases.values()),
            "Build directories must belong to this version's Linux release tree")
    evidence.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    private = Path(tempfile.mkdtemp(prefix="orionq-native-final-", dir="/var/tmp"))
    began = time.monotonic()
    freeze = read(freeze_path)
    require(freeze["version"] == version, "Source manifest version differs")
    require(all(sha(repository / item["path"]) == item["sha256"] for item in freeze["files"]), "Frozen source changed")
    bundles, receipts = {}, {}
    for profile in ("headless", "gui"):
        release = releases[profile]
        build = read(release / "build-report.json")
        require(build["status"] == "PASS" and build["version"] == version, "Build did not pass: " + profile)
        archive = release / build["archive"]["name"]
        require(sha(archive) == build["archive"]["sha256"], "Archive hash differs")
        with gzip.open(archive, "rb") as stream:
            while stream.read(1024 * 1024):
                pass
        extracted = private / profile
        extracted.mkdir(mode=0o700)
        with tarfile.open(archive, "r:gz") as stream:
            members = stream.getmembers()
            paths = [item.name for item in members]
            require(len(paths) == len(set(paths)), "Duplicate archive members")
            require(all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in paths), "Unsafe archive path")
            require(all(not item.isdev() and not item.isfifo() for item in members), "Unexpected archive entry type")
            stream.extractall(extracted, filter="data")
        bundle = extracted / ("OrionQ-headless" if profile == "headless" else "OrionQ")
        manifest = read(release / "evidence/bundle-files.json")["files"]
        actual = set()
        symlinks = []
        for file in bundle.rglob("*"):
            if file.is_symlink():
                require(file.resolve(strict=True).is_relative_to(bundle), "Symlink leaves distribution")
                symlinks.append(file.relative_to(bundle).as_posix())
            if file.is_file():
                actual.add(file.relative_to(bundle).as_posix())
        require(actual == {row["path"] for row in manifest}, "Recovered tree differs from manifest")
        require(all((bundle / row["path"]).stat().st_size == row["bytes"] and sha(bundle / row["path"]) == row["sha256"]
                    for row in manifest), "Recovered file bytes differ")
        mode_count = 0
        for member in members:
            if member.isfile() or member.isdir():
                require((extracted / member.name).stat().st_mode & 0o777 == member.mode & 0o777,
                        "Recovered mode differs: " + member.name)
                mode_count += 1
        for item in build["commands"]:
            command = bundle / item["name"]
            require(command.stat().st_mode & 0o111 != 0 and sha(command) == item["sha256"], "Entrypoint differs")
            result = subprocess.run([str(command), "--help"], cwd=private, capture_output=True, text=True,
                                    timeout=30, shell=False, env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
            require(result.returncode == 0 and "usage:" in result.stdout, "Recovered entrypoint failed")
        receipt = {"schema": "orion.linux-archive-recovery.v1", "status": "PASS", "version": version,
            "profile": profile, "archive_sha256": sha(archive), "archive_bytes": archive.stat().st_size,
            "gzip_crc_verified": True, "recovered_file_count": len(manifest), "all_file_bytes_verified": True,
            "verified_mode_count": mode_count, "contained_symlinks": symlinks,
            "entrypoints": build["commands"], "fresh_private_extraction": True, "private_state_exported": False}
        write(evidence / f"archive-{profile}-smoke.json", receipt)
        bundles[profile], receipts[profile] = bundle, receipt
    reader_spec = importlib.util.spec_from_file_location("linux_static_reader", repository / "tools/verify_frozen_source.py")
    reader = importlib.util.module_from_spec(reader_spec)
    reader_spec.loader.exec_module(reader)
    raw = reader.verify(bundles["gui"] / "OrionQ", source_root=repository)
    applicable = {name: value for name, value in raw["checks"].items() if name not in {"executable_name", "windows_version_matches"}}
    require(len(raw["modules"]) == len(reader.MODULES)
            and len(raw["resources"]) == len(reader.RESOURCES) and all(applicable.values())
            and raw["errors"] == ["Windows PE identity validation requires a Windows host"], "Frozen Linux source comparison failed")
    write(evidence / "gui-frozen-source-linux.json", {"schema": "orion.linux-frozen-source.v1", "status": "PASS",
        "version": version, "module_count": len(raw["modules"]), "resource_count": len(raw["resources"]),
        "checks": applicable, "modules": raw["modules"], "resources": raw["resources"],
        "excluded_checks": {"executable_name": "NOT_APPLICABLE: ELF OrionQ, not Windows .exe",
                            "windows_version_matches": "NOT_APPLICABLE: ELF has no PE resource"},
        "executable_sha256": sha(bundles["gui"] / "OrionQ"), "frozen_code_executed": False})
    remote_tool = repository / "tools/run_linux_gui_remote_smoke.py"
    with (private / "remote.log").open("xb") as log:
        result = subprocess.run([sys.executable, "-I", str(remote_tool), "--core", str(bundles["headless"] / "orion-core"),
            "--gui", str(bundles["gui"] / "OrionQ"), "--repository", str(bundles["headless"] / "_internal"),
            "--state", str(private.parent / (private.name + "-remote")), "--report", str(evidence / "gui-remote-smoke.json"),
            "--expected-version", version], cwd=private, stdout=log, stderr=log, timeout=120, shell=False)
    require(result.returncode == 0 and read(evidence / "gui-remote-smoke.json")["status"] == "PASS", "Native GUI/core mTLS failed")
    plugin_root = bundles["gui"] / "_internal/PySide6/Qt/plugins/platforms"
    plugin_reports = {}
    # The PyInstaller bootloader exposes its relocated shared libraries before
    # Qt loads a platform plugin. A bare ldd can falsely call bundled Qt libs
    # missing when their original wheel RPATH no longer matches this layout.
    library_directories = [path for path in (bundles["gui"] / "_internal",
                           bundles["gui"] / "_internal/PySide6/Qt/lib") if path.is_dir()]
    loader_environment = dict(os.environ, LD_LIBRARY_PATH=":".join(map(str, library_directories)))
    wayland_plugin = next((name for name in ("libqwayland.so", "libqwayland-generic.so")
                           if (plugin_root / name).is_file()), None)
    for plugin in (wayland_plugin, "libqxcb.so"):
        if plugin is None:
            continue
        path = plugin_root / plugin
        require(path.is_file(), "Required Qt platform plugin is missing: " + plugin)
        result = subprocess.run(["ldd", str(path)], capture_output=True, text=True, timeout=15,
                                env=loader_environment)
        plugin_reports[plugin] = {"path": path.relative_to(bundles["gui"]).as_posix(),
                                 "ldd_exit_code": result.returncode,
                                 "bundled_loader_paths": [value.relative_to(bundles["gui"]).as_posix()
                                                          for value in library_directories],
                                 "missing_libraries": [line.strip() for line in result.stdout.splitlines() if "not found" in line]}
    display = {"DISPLAY": os.environ.get("DISPLAY"), "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY"),
               "wslg_runtime_available": Path("/mnt/wslg/runtime-dir").is_dir(), "plugins": plugin_reports}
    wayland = {"status": "NOT_AVAILABLE", "reason": "WSLg Wayland display or required libraries unavailable"}
    if (display["wslg_runtime_available"] and display["WAYLAND_DISPLAY"] and wayland_plugin
            and plugin_reports[wayland_plugin]["ldd_exit_code"] == 0
            and not plugin_reports[wayland_plugin]["missing_libraries"]):
        report_path = private / "wayland-startup.json"
        environment = dict(os.environ, QT_QPA_PLATFORM="wayland", XDG_RUNTIME_DIR="/mnt/wslg/runtime-dir")
        with (private / "wayland-startup.log").open("xb") as log:
            result = subprocess.run([str(bundles["gui"] / "OrionQ"), "--startup-test", str(report_path),
                "--startup-size", "1600x900", "--state-root", str(private / "wayland-state")],
                cwd=private, env=environment, stdout=log, stderr=log, timeout=150, shell=False)
        value = read(report_path) if report_path.exists() else {}
        wayland = {"status": "PASS" if result.returncode == 0 and value.get("passed") is True else "FAIL",
            "executable_sha256": sha(bundles["gui"] / "OrionQ"),
            "exit_code": result.returncode, "platform": value.get("platform"), "passed": value.get("passed"),
            "cases": value.get("cases", []), "heartbeat_max_ms": value.get("heartbeat_max_ms"),
            "heartbeat_p95_ms": value.get("heartbeat_p95_ms"), "snapshot_count": value.get("snapshot_count"),
            "monitor_geometry": value.get("monitor_geometry"), "device_pixel_ratio": value.get("device_pixel_ratio"),
            "errors": value.get("errors", []), "shutdown": "StartupMonitor window.close() and app.quit(); normal zero-exit required",
            "physical_mouse_drag_verified": False, "independent_linux_desktop_verified": False,
            "compositor": "WSLg on Windows host"}
        write(evidence / "gui-wayland-startup.json", wayland)
        require(wayland["status"] == "PASS" and wayland["platform"] == "wayland", "Wayland native startup failed")
    require(all(sha(repository / row["path"]) == row["sha256"] for row in freeze["files"]), "Validation altered source")
    final = {"schema": "orion.linux-native-release-qualification.v1", "status": "PASS", "version": version,
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "duration_s": time.monotonic() - began,
        "uid": os.geteuid(), "archives": receipts, "display": display, "wayland": wayland,
        "source_freeze_file_count": len(freeze["files"]), "source_unchanged": True,
        "source_freeze_path": freeze_path.relative_to(repository).as_posix(), "source_freeze_sha256": sha(freeze_path),
        "validator_sha256": sha(Path(__file__)), "physical_action_performed": False, "hardware_validated": False,
        "limitations": ["Ubuntu 24.04.5 WSL2 x86_64/glibc 2.39 only; no independent Linux workstation or ARM/BSP qualification",
            "Remote mTLS GUI fixture ends GUI with SIGTERM; separate Wayland startup test closes programmatically",
            "Missing xcb dependencies are reported; Wayland qualification does not prove X11 operation",
            "Physical FPGA AXI/DMA, Swabian and satellite reception remain unqualified"]}
    write(evidence / "linux-release-qualification.json", final)
    print(json.dumps({"status": "PASS", "version": version, "report": str(evidence / "linux-release-qualification.json")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
