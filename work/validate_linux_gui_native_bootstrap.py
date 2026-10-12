"""Qualify the fresh GUI bundle without changing application modules or old releases."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import difflib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import zipfile


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_tree(root, rows):
    expected = {row["path"] for row in rows}
    require(len(expected) == len(rows), "Duplicate bundle paths")
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            require(path.resolve(strict=True).is_relative_to(root),
                    "Bundle symlink must resolve inside its exact distribution")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    require(actual == expected, "Bundle tree differs from its exact manifest")
    for row in rows:
        path = root / row["path"]
        require(path.stat().st_size == row["bytes"] and digest(path) == row["sha256"],
                "Bundle bytes changed: " + row["path"])
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--core-bundle", required=True, type=Path)
    parser.add_argument("--wheel", required=True, type=Path)
    args = parser.parse_args()
    require(sys.platform == "linux" and os.geteuid() != 0 and not sys.flags.optimize,
            "Native Linux non-root validation without optimization is required")
    require(os.environ.get("PYTHONOPTIMIZE", "") in {"", "0"}, "Optimization disables validation gates")
    os.umask(0o077)
    repository, release, core_bundle, wheel = (
        path.resolve(strict=True) for path in
        (args.repository, args.release, args.core_bundle, args.wheel))
    evidence = release / "evidence"
    final_path = evidence / "gui-bootstrap-qualification.json"
    require(not final_path.exists(), "Existing qualification is preserved")
    began = time.monotonic()
    build = read_json(release / "build-report.json")
    require(build["status"] == "PASS" and build["version"] == "5.64.2"
            and build["profile"] == "qt-gui-cpu-ml", "Current GUI build is not accepted")
    require(build["physical_action_performed"] is False and build["hardware_validated"] is False,
            "Software validation cannot claim hardware qualification")
    self_test = read_json(evidence / "gui-self-test-checks.json")
    require(self_test["passed"] is True and self_test["check_count"] == 39
            and len(self_test["checks"]) == 39 and all(v is True for v in self_test["checks"].values()),
            "The actual GUI must pass all 39 self-test checks")
    startup = read_json(evidence / "gui-startup.json")
    require(startup["passed"] is True and startup["platform"] == "offscreen"
            and len(startup["cases"]) == 15 and all(row["passed"] is True for row in startup["cases"]),
            "The actual GUI must pass all 15 startup checks")

    snapshot = read_json(evidence / "gui-public-input-scan.json")
    require(snapshot["status"] == "PASS", "Public GUI source snapshot was not scanned")
    auxiliary_relative = "src/quantum_twin.egg-info/SOURCES.txt"
    auxiliary_before = "adcdc6c19cf0ba37e522defb50e6101a02086b82571daaa89fda47057ad44c69"
    auxiliary_after = "3ffea0d07a1a7c4d7b116271ccdb4d361d7e305ada3c3ae27e57eae6df617a95"
    matched_snapshot_files = 0
    auxiliary_delta = None
    for row in snapshot["files"]:
        path = repository / row["path"]
        require(path.is_file(), "GUI snapshot source is missing: " + row["path"])
        current_sha = digest(path)
        if current_sha != row["sha256"]:
            require(row["path"] == auxiliary_relative and row["sha256"] == auxiliary_before
                    and current_sha == auxiliary_after and auxiliary_delta is None,
                    "GUI build snapshot is stale relative to checkout: " + row["path"])
            auxiliary_delta = {"path": auxiliary_relative, "snapshot_sha256": auxiliary_before,
                               "current_sha256": auxiliary_after,
                               "classification": "Only the generated setuptools source inventory changed during Windows egg_info; runtime code and dependency metadata are unchanged"}
        else:
            matched_snapshot_files += 1
    require(auxiliary_delta is not None and matched_snapshot_files == len(snapshot["files"]) - 1,
            "The authorized exact auxiliary metadata delta was not observed")
    provider_relative = "src/quantum_autopilot/console/service_provider.py"
    provider_row = next(row for row in snapshot["files"] if row["path"] == provider_relative)

    installed = read_json(evidence / "installed-wheel-source.json")
    wheel_sha = digest(wheel)
    require(installed["status"] == "PASS" and installed["wheel_sha256"] == wheel_sha
            == build["wheel_sha256_before"] == build["wheel_sha256_after"], "Selected wheel identity changed")
    wheel_source_count = 0
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        require(not any(name.startswith(("quantum_autopilot/console/", "quantum_autopilot/desktop/"))
                        for name in names), "Headless wheel unexpectedly includes GUI code")
        for row in installed["files"]:
            path = repository / "src" / row["path"]
            require(digest(path) == row["sha256"]
                    == hashlib.sha256(archive.read(row["path"])).hexdigest(),
                    "Installed wheel source differs from current headless checkout: " + row["path"])
            wheel_source_count += 1

    gui_rows = read_json(evidence / "bundle-files.json")["files"]
    require(verify_tree(release / "OrionQ", gui_rows) == build["bundle_file_count"],
            "Public GUI distribution manifest changed")
    headless_release = repository / "release/linux/5.64.2-native-headless-release"
    headless_build = read_json(headless_release / "build-report.json")
    core_rows = read_json(headless_release / "evidence/bundle-files.json")["files"]
    require(headless_build["status"] == "PASS" and len(core_rows) == 670,
            "The previously qualified headless bundle is not available")
    verify_tree(core_bundle, core_rows)

    archive_path = release / build["archive"]["name"]
    archive_sha = digest(archive_path)
    require(archive_sha == build["archive"]["sha256"]
            and archive_path.stat().st_size == build["archive"]["bytes"], "GUI archive identity changed")
    work = Path(tempfile.mkdtemp(prefix="orionq-gui-bootstrap-verification-", dir="/var/tmp"))
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(work, filter="data")
    gui_bundle = work / "OrionQ"
    verified_count = verify_tree(gui_bundle, gui_rows)
    gui = gui_bundle / "OrionQ"
    require(gui.stat().st_mode & 0o777 == 0o755, "GUI archive entrypoint must preserve mode 0755")
    require(gui.read_bytes()[:4] == b"\x7fELF" and digest(gui) == build["commands"][0]["sha256"],
            "GUI ELF differs from the accepted build")

    spec = importlib.util.spec_from_file_location("bootstrap_frozen_reader", repository / "tools/verify_frozen_source.py")
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    frozen = reader.verify(gui, source_root=repository)
    upstream = evidence / "gui-frozen-source-windows-verifier.json"
    write_json(upstream, frozen)
    excluded = {"executable_name", "windows_version_matches"}
    require(set(frozen["checks"]) == {
        "executable_name", "windows_version_matches", "all_required_modules_match",
        "all_required_resources_match", "distribution_metadata_matches", "executable_unchanged"},
        "Static verifier check contract changed")
    require(len(frozen["modules"]) == 52 and len(frozen["resources"]) == 25
            and all(value is True for key, value in frozen["checks"].items() if key not in excluded)
            and frozen["errors"] == ["Windows PE identity validation requires a Windows host"]
            and frozen["source_version"] == "5.64.2", "Native Linux static identity did not match current sources")
    static = {
        "schema": "orion.native-linux-frozen-source-static.v1", "status": "PASS",
        "artifact_version": "5.64.2", "module_count": 52, "resource_count": 25,
        "checks": {key: value for key, value in frozen["checks"].items() if key not in excluded},
        "excluded_checks": {
            "executable_name": {"status": "NOT_APPLICABLE", "reason": "Windows name OrionQ.exe does not apply to Linux OrionQ"},
            "windows_version_matches": {"status": "NOT_APPLICABLE", "reason": "PE version resources do not apply to Linux ELF"}},
        "upstream_report_name": upstream.name, "upstream_report_sha256": digest(upstream),
        "upstream_verifier_overall_passed": frozen["passed"], "upstream_verifier_errors": frozen["errors"],
        "executable_sha256": digest(gui), "frozen_code_executed": False,
        "gui_started": False, "operator_state_accessed": False, "physical_action_performed": False,
        "scope": "Static ELF identity; native GUI execution is a separate gate"}
    write_json(evidence / "gui-frozen-source-linux.json", static)
    fingerprint = reader._source_helpers()[0]
    carchive = reader._archive_reader(gui)
    pyz_name = next(name for name, row in carchive.toc.items() if row[-1] == "z")
    pyz = carchive.open_embedded_archive(pyz_name)
    module_name = "quantum_autopilot.console.service_provider"
    code = compile((repository / provider_relative).read_text(encoding="utf-8"), provider_relative,
                   "exec", optimize=1, dont_inherit=True)
    source_fingerprint = fingerprint(code)
    require(source_fingerprint == fingerprint(pyz.extract(module_name)),
            "Frozen ServiceProvider does not contain the startup correction")
    metadata_directory = Path(frozen["distribution_metadata"][0]["path"]).parent
    require(metadata_directory.name == "quantum_twin.egg-info", "Unexpected active runtime metadata format")
    metadata_matches = []
    for name in ("PKG-INFO", "requires.txt"):
        source_path = repository / "src/quantum_twin.egg-info" / name
        require(digest(metadata_directory / name) == digest(source_path),
                "Packaged runtime metadata differs from checkout: " + name)
        metadata_matches.append({"path": name, "sha256": digest(source_path), "passed": True})
    packaged_sources = metadata_directory / "SOURCES.txt"
    require(digest(packaged_sources) == auxiliary_before, "Packaged auxiliary source inventory differs from captured snapshot")
    auxiliary_delta["textual_diff"] = list(difflib.unified_diff(
        packaged_sources.read_text(encoding="utf-8").splitlines(),
        (repository / auxiliary_relative).read_text(encoding="utf-8").splitlines(),
        fromfile="captured/quantum_twin.egg-info/SOURCES.txt",
        tofile="current/quantum_twin.egg-info/SOURCES.txt", lineterm=""))
    write_json(evidence / "gui-bootstrap-source-binding.json", {
        "schema": "orion.native-linux-gui-bootstrap-source-binding.v1", "status": "PASS",
        "module": module_name, "source_sha256": provider_row["sha256"],
        "code_fingerprint": source_fingerprint, "executable_sha256": digest(gui),
        "snapshot_file_count": len(snapshot["files"]), "snapshot_files_matching_checkout": matched_snapshot_files,
        "all_snapshot_files_match_checkout": False, "only_authorized_auxiliary_delta": auxiliary_delta,
        "runtime_metadata_matches_checkout": metadata_matches,
        "headless_wheel_sha256": wheel_sha, "headless_wheel_source_count": wheel_source_count,
        "headless_wheel_matches_current_headless_sources": True, "headless_wheel_excludes_gui": True,
        "headless_bundle_file_count": len(core_rows), "headless_core_rebuilt": False,
        "frozen_code_executed_by_static_check": False})

    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    commands = []
    for argument in ("--version", "--help"):
        result = subprocess.run([str(gui), argument], cwd=work, env=environment, capture_output=True,
                                timeout=120, shell=False)
        require(result.returncode == 0, "Extracted GUI CLI did not complete")
        if argument == "--version":
            require(result.stdout.strip() == b"OrionQ 5.64.2", "Extracted GUI version differs")
        commands.append({"argument": argument, "exit_code": result.returncode,
                         "output_sha256": hashlib.sha256(result.stdout).hexdigest()})
    write_json(evidence / "archive-smoke.json", {
        "schema": "orion.native-linux-archive-smoke.v1", "status": "PASS", "uid": os.geteuid(),
        "profile": "qt-gui-cpu-ml", "archive_name": archive_path.name, "archive_sha256": archive_sha,
        "verified_file_count": verified_count, "all_file_hashes_match": True,
        "contained_symlink_count": sum(path.is_symlink() for path in gui_bundle.rglob("*")),
        "entrypoint_mode": "0755", "commands": commands, "display_backend": "offscreen",
        "hardware_validated": False, "physical_display_or_mouse_validated": False,
        "duration_s": time.monotonic() - began})

    remote_report = evidence / "gui-remote-smoke-final.json"
    state = Path("/var/tmp") / ("orionq-gui-bootstrap-mtls-" + uuid.uuid4().hex)
    remote = subprocess.run([sys.executable, "-I", str(repository / "tools/run_linux_gui_remote_smoke.py"),
        "--core", str(core_bundle / "orion-core"), "--gui", str(gui),
        "--repository", str(core_bundle / "_internal"), "--state", str(state),
        "--report", str(remote_report), "--duration-s", "12"], cwd=work, capture_output=True,
        timeout=180, shell=False)
    require(remote.returncode == 0 and remote_report.is_file(), "Actual GUI/core mTLS fixture did not pass")
    remote_value = read_json(remote_report)
    require(remote_value["status"] == "PASS" and remote_value["gui_sha256"] == digest(gui)
            and remote_value["core_sha256"] == headless_build["commands"][0]["sha256"]
            and remote_value["tls"] == "TLSv1.3-mTLS" and remote_value["application_modules_replaced"] is False,
            "Actual GUI/core identity or authenticated communication differs")
    verify_tree(core_bundle, core_rows)
    verify_tree(gui_bundle, gui_rows)
    verify_tree(release / "OrionQ", gui_rows)
    require(digest(wheel) == wheel_sha and digest(archive_path) == archive_sha,
            "Validation changed an input artifact")
    report = {
        "schema": "orion.native-linux-gui-bootstrap-qualification.v1", "status": "PASS",
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "version": "5.64.2",
        "release_root": str(release), "build_report_sha256": digest(release / "build-report.json"),
        "validation_script_sha256": digest(Path(__file__)), "self_test_checks": 39, "startup_checks": 15,
        "validation_script_source": Path(__file__).resolve().relative_to(repository).as_posix(),
        "static_modules": 52, "static_resources": 25, "startup_provider_extra_module_verified": True,
        "gui_executable_sha256": digest(gui), "archive_sha256": archive_sha,
        "gui_bundle_file_count": len(gui_rows), "headless_bundle_file_count": len(core_rows),
        "headless_core_rebuilt": False, "wheel_preserved": True,
        "authenticated_gui_read_operations": remote_value["authenticated_gui_read_operations"],
        "backend_ticks": [remote_value[key] for key in (
            "backend_tick_before_gui", "backend_tick_during_gui", "backend_tick_after_gui")],
        "reports": [{"path": path.name, "sha256": digest(path)} for path in (
            evidence / "gui-self-test-checks.json", evidence / "gui-startup.json",
            evidence / "gui-frozen-source-linux.json", evidence / "gui-bootstrap-source-binding.json",
            evidence / "archive-smoke.json", remote_report)],
        "duration_s": time.monotonic() - began, "physical_action_performed": False,
        "hardware_validated": False, "physical_display_or_mouse_validated": False,
        "scope": "Actual native Qt offscreen GUI/core software validation on the WSL2 build host; no TRL promotion"}
    write_json(final_path, report)
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
