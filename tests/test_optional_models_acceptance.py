"""Frozen diagnostic entry, explicit package use and honest no-package acceptance."""
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from quantum_autopilot import optional_models_acceptance as acceptance  # noqa: E402

pytestmark = pytest.mark.unit


def test_without_packages_never_verifies_or_opens_weights(tmp_path, monkeypatch):
    from quantum_autopilot.console import model_archive_check
    from quantum_autopilot.model_intake import catalog
    monkeypatch.setattr(model_archive_check, "verify_model_archive", lambda *a: pytest.fail("automatic package access"))
    monkeypatch.setattr(catalog, "open_optional_model", lambda *a, **kw: pytest.fail("automatic weight load"))
    report = acceptance.run_checks(tmp_path / "new")
    assert report["passed"], report
    assert len(report["models"]) == 11 and all(m["unloaded"] for m in report["models"])
    assert report["mode"] == "without_packages" and not report["frozen"]
    assert not report["independent_machine_verified"] and not report["physical_action_performed"]
    assert json.loads((tmp_path / "new/optional-models-report.json").read_text()) == report


def test_output_never_overwritten_and_half_paths_refused(tmp_path):
    with pytest.raises(FileExistsError):
        acceptance.run_checks(tmp_path)
    with pytest.raises(ValueError):
        acceptance.run_checks(tmp_path / "new", qkd_zip="qkd.zip")
    assert not (tmp_path / "new").exists()


def test_bad_package_fails_closed(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not reviewed")
    report = acceptance.run_checks(tmp_path / "new", qkd_zip=bad, qnet_zip=bad)
    assert not report["passed"] and report["mode"] == "external_packages"
    assert "hash" in report["error"]
    assert not list((tmp_path / "new").glob("q*.json"))


def test_dispatch_precedes_desktop(monkeypatch):
    spec = importlib.util.spec_from_file_location("optional_launcher", Path(__file__).parents[1]/"packaging/console_launcher.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    monkeypatch.setattr(acceptance, "main", lambda argv: 42 if argv == ["--output-dir", "new"] else 1)
    monkeypatch.setitem(sys.modules, "quantum_autopilot.desktop", None)
    assert launcher.launch(["--optional-model-test", "--output-dir", "new"]) == 42


def test_timeout_is_not_passed():
    class Job:
        active = True
    class App:
        def processEvents(self):
            pass
    from unittest.mock import patch
    with patch.object(acceptance.time, "monotonic", side_effect=[0, 31]):
        with pytest.raises(TimeoutError):
            acceptance._wait(App(), Job())


def test_frozen_output_cannot_change_program_package(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app.exe"))
    with pytest.raises(ValueError, match="outside"):
        acceptance.run_checks(tmp_path / "diagnostics")
    assert not (tmp_path / "diagnostics").exists()
