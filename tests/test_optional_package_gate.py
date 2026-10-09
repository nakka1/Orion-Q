"""Portable packaging requires all unloaded-session checks for the same frozen EXE."""
import importlib.util
from pathlib import Path

import pytest

from quantum_autopilot.model_intake.catalog import model_options

pytestmark = pytest.mark.unit
spec = importlib.util.spec_from_file_location("package_optional_test", Path(__file__).parents[1]/"tools/package_console_portable.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


@pytest.fixture
def report(tmp_path):
    exe = tmp_path / "app.exe"
    exe.write_bytes(b"synthetic-test-executable")
    return exe, {"schema": "orion.optional-models-acceptance.v1", "passed": True, "frozen": True,
                 "mode": "without_packages", "physical_action_performed": False, "runtime_policy_activated": False,
                 "executable_sha256": packager.digest(exe),
                 "models": [{"model_id": option.model_id, "unloaded": True} for option in model_options()
                            if option.status == "offline_only"]}


def test_same_executable_and_all_unloaded_sessions_required(report):
    exe, data = report
    packager.validate_optional(data, exe)
    exe.write_bytes(b"changed")
    with pytest.raises(ValueError):
        packager.validate_optional(data, exe)


@pytest.mark.parametrize("field,value", [("schema", "wrong"), ("passed", False), ("passed", 1), ("frozen", False),
                                       ("mode", "external_packages"), ("physical_action_performed", True),
                                       ("runtime_policy_activated", True), ("models", []), ("models", None)])
def test_bad_approval_refused(report, field, value):
    exe, data = report
    data[field] = value
    with pytest.raises(ValueError):
        packager.validate_optional(data, exe)


def test_duplicate_or_loaded_entry_refused(report):
    exe, data = report
    data["models"][0]["unloaded"] = False
    with pytest.raises(ValueError):
        packager.validate_optional(data, exe)
    data["models"][0] = data["models"][1].copy()
    with pytest.raises(ValueError):
        packager.validate_optional(data, exe)
