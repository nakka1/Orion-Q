"""Native release version selection must preserve isolation and old artifacts."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


def builder():
    spec = importlib.util.spec_from_file_location("native_linux_version_fixture", ROOT / "tools/build_linux_native.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_new_canonical_version_selects_new_release_and_preserves_explicit_older_wheel(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "pyproject.toml").write_text('[project]\nversion = "5.65.0"\n', encoding="utf-8")
    tool = builder()
    assert tool.selected_version(repository) == "5.65.0"
    assert tool.selected_version(repository, "5.64.2") == "5.64.2"
    assert tool.permitted_destination(repository, repository / "release/linux/5.65.0-native-gui", "5.65.0")
    assert tool.permitted_destination(repository, repository / "release/linux/5.64.2-native-gui", "5.64.2")
    assert not tool.permitted_destination(repository, repository / "release/linux/5.64.2-native-gui", "5.65.0")


@pytest.mark.parametrize("name", ["5.65.00-native", "5.65.0evil", "5.65.0/native", "../5.65.0-native", "../../5.65.0-native"])
def test_version_prefix_confusion_and_directory_escape_are_rejected(tmp_path, name):
    destination = tmp_path / "release/linux" / name
    assert not builder().permitted_destination(tmp_path, destination, "5.65.0")


def test_output_symlink_cannot_leave_repository_release_root(tmp_path):
    release = tmp_path / "release/linux"
    release.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (release / "5.65.0-native").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit directory symlinks")
    assert not builder().permitted_destination(tmp_path, release / "5.65.0-native", "5.65.0")


@pytest.mark.parametrize("version", ["development", "../5.65.0", "5.65.0/../../outside", "5.65.0evil", ""])
def test_unsafe_or_undeclared_expected_version_is_rejected(tmp_path, version):
    with pytest.raises(ValueError, match="numeric release version"):
        builder().selected_version(tmp_path, version)


@pytest.mark.skipif(os.name != "posix", reason="resume ownership/path contract requires actual Linux")
def test_resuming_frozen_bundle_never_reuses_existing_validation_fixture(tmp_path, monkeypatch):
    tool = builder()
    repository = tmp_path / "repository"
    (repository / "packaging/core/systemd").mkdir(parents=True)
    (repository / "packaging/core/core.example.json").write_text("{}")
    (repository / "packaging/core/systemd/orionq-core.service").write_text("[Service]\n")
    wheel = tmp_path / "fixture.whl"
    wheel.write_bytes(b"test-only wheel placeholder")
    destination = repository / "release/linux/5.65.0-resume-fixture-test"
    monkeypatch.setattr(tool.importlib.metadata, "version", lambda _: "5.65.0")
    monkeypatch.setattr(tool, "validate_installed_wheel", lambda *_: {"test_fixture": True})
    monkeypatch.setattr(tool.subprocess, "check_output", lambda command, **_: "[]" if "list" in command else "6.22.3")
    monkeypatch.setattr(tool.subprocess, "run", lambda command, **_: subprocess.CompletedProcess(command, 0, "usage: fixture", ""))
    with tempfile.TemporaryDirectory(prefix="orionq-native-linux-resume-test-", dir="/var/tmp") as name:
        work = Path(name)
        bundle = work / "dist/OrionQ-headless"
        bundle.mkdir(parents=True)
        for command in tool.COMMANDS:
            (bundle / command).write_bytes(b"\x7fELFtest-only executable placeholder")
        old = work / "gateway-fixture"
        old.mkdir()
        sentinel = old / "old-state-must-survive"
        sentinel.write_text("preserved")
        calls = []

        def gateway(bundle_path, selected_work, report_directory):
            assert bundle_path == bundle
            assert selected_work != work and selected_work.parent == work
            assert selected_work.is_dir() and selected_work.stat().st_mode & 0o777 == 0o700
            (selected_work / "gateway-fixture").mkdir()
            calls.append(selected_work)

        def campaign(repository_path, bundle_path, selected_work, report_directory):
            assert selected_work == calls[0]
            assert sentinel.read_text() == "preserved"
            return {"status": "PASS", "pty": {"status": "PASS", "key_presses": 1}}

        monkeypatch.setattr(tool, "validate_gateway", gateway)
        monkeypatch.setattr(tool, "native_campaign", campaign)
        assert tool.main(["--repository", str(repository), "--wheel", str(wheel),
            "--expected-version", "5.65.0", "--profile", "headless", "--resume-headless", str(work),
            "--output", str(destination)]) == 0
        assert sentinel.read_text() == "preserved"
        assert len(calls) == 1
