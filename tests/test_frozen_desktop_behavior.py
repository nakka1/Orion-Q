"""Validation must fail for wrong glyphs, alpha, binary identity or missing cases."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

from quantum_autopilot.console.desktop_validation import compare_icon_pixels, main, _within_output


def _gate():
    path = Path(__file__).resolve().parents[1] / "tools/validate_frozen_desktop_behavior.py"
    spec = importlib.util.spec_from_file_location("frozen_desktop_validation_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record():
    gate = _gate()
    cases = [{"case": name, "passed": True} for name in gate.REQUIRED_CASES]
    identity = next(item for item in cases if item["case"] == "app-window-taskbar-identity")
    identity.update(application_id="ORION-Q.Desktop", window={"visible": True, "taskbar_eligible": True})
    return {"schema": "orion.desktop-behavior.v1", "passed": True, "frozen": True,
            "operator_state_accessed": False, "executable_sha256": "candidate-hash",
            "executable_sha256_after": "candidate-hash", "cases": cases}


def test_frozen_gate_accepts_complete_evidence_for_same_binary():
    record = _record()
    assert _gate().validate_native_report(record, "candidate-hash") == record["cases"]


@pytest.mark.parametrize("field,value", [("schema", "other"), ("passed", False), ("frozen", False),
    ("operator_state_accessed", True), ("executable_sha256", "old-binary"),
    ("executable_sha256_after", "modified-binary")])
def test_frozen_gate_rejects_unqualified_or_stale_binary_evidence(field, value):
    record = _record()
    record[field] = value
    with pytest.raises(ValueError, match="qualification"):
        _gate().validate_native_report(record, "candidate-hash")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "failed", "wrong-app-id", "no-taskbar", "hidden"])
def test_frozen_gate_rejects_missing_cases_and_incorrect_native_identity(mutation):
    record = copy.deepcopy(_record())
    if mutation == "missing":
        record["cases"].pop()
    elif mutation == "duplicate":
        record["cases"][-1] = record["cases"][0]
    elif mutation == "failed":
        record["cases"][0]["passed"] = False
    else:
        identity = record["cases"][2]
        if mutation == "wrong-app-id":
            identity["application_id"] = "Python.Generic"
        else:
            identity["window"]["taskbar_eligible" if mutation == "no-taskbar" else "visible"] = False
    with pytest.raises(ValueError, match="qualification"):
        _gate().validate_native_report(record, "candidate-hash")


def _image(colour):
    image = QImage(8, 8, QImage.Format.Format_RGBA8888)
    image.fill(QColor(colour))
    return image


def test_native_pixel_check_detects_generic_icon_wrong_colour_and_coverage():
    expected = _image("transparent")
    expected.setPixelColor(3, 3, QColor("#69dab5"))
    assert compare_icon_pixels(expected, expected)["passed"]
    assert not compare_icon_pixels(_image("blue"), expected)["passed"]
    wrong_colour = expected.copy()
    wrong_colour.setPixelColor(3, 3, QColor("red"))
    assert not compare_icon_pixels(wrong_colour, expected)["passed"]
    wrong_alpha = expected.copy()
    wrong_alpha.setPixelColor(3, 3, QColor(105, 218, 181, 128))
    assert not compare_icon_pixels(wrong_alpha, expected)["passed"]


def test_native_pixel_check_ignores_invisible_rgb_and_allows_only_small_channel_rounding():
    expected = _image("transparent")
    expected.setPixelColor(3, 3, QColor(105, 218, 181, 255))
    actual = expected.copy()
    actual.setPixelColor(0, 0, QColor(255, 0, 0, 0))
    actual.setPixelColor(3, 3, QColor(107, 216, 183, 255))
    assert compare_icon_pixels(actual, expected)["passed"]
    actual.setPixelColor(3, 3, QColor(108, 218, 181, 255))
    assert not compare_icon_pixels(actual, expected)["passed"]
    assert not compare_icon_pixels(_image("transparent"), _image("transparent"))["passed"]


def test_diagnostic_fixture_cannot_escape_isolated_output(tmp_path):
    output = tmp_path / "diagnostic"
    assert _within_output(output, output / "primary/state") == output / "primary/state"
    with pytest.raises(ValueError, match="child"):
        _within_output(output, output / "../operator-state")
    with pytest.raises(ValueError, match="child"):
        _within_output(output, output)


@pytest.mark.parametrize("options", [["--state-root", "operator"], ["--provider", "service"],
    ["--ui", "web"], ["--_case-name", "primary"], ["--_load-delay-ms", "1000"]])
def test_diagnostic_parser_rejects_operator_inputs_and_unscoped_internal_options(tmp_path, options):
    with pytest.raises(SystemExit) as failure:
        main([str(tmp_path / "diagnostic"), *options])
    assert failure.value.code == 2


def test_internal_child_cannot_be_launched_without_coordinator_session(tmp_path):
    assert main([str(tmp_path), "--_role", "secondary", "--_case-name", "secondary"]) == 1
    result = (tmp_path / "secondary/result.json").read_text(encoding="utf-8")
    assert "coordinator session" in result


def _gate_fixture(tmp_path):
    gate = _gate()
    exe = tmp_path / "ORION-Q.exe"
    exe.write_bytes(b"qualified desktop candidate")
    root = tmp_path / "release-evidence"
    root.mkdir()
    native_path = root / "native-report.json"
    record = _record()
    record["executable_sha256"] = record["executable_sha256_after"] = gate.digest(exe)
    native_path.write_text(json.dumps(record), encoding="utf-8")
    outer = {"schema": gate.REPORT_SCHEMA, "passed": True, "operator_state_accessed": False,
             "executable_sha256": gate.digest(exe), "executable_sha256_after": gate.digest(exe),
             "cases": copy.deepcopy(record["cases"]), "native_report": str(native_path)}
    return gate, exe, root, native_path, record, outer


def test_package_gate_verifies_preserved_native_evidence_inside_release(tmp_path):
    gate, exe, root, native_path, record, outer = _gate_fixture(tmp_path)
    assert gate.verify_gate(outer, exe, evidence_root=root) == record["cases"]
    outer["native_report"] = native_path.name
    assert gate.verify_gate(outer, exe, evidence_root=root) == record["cases"]


def test_portable_packager_requires_desktop_gate_before_writing_any_archive(tmp_path, monkeypatch):
    from tools import package_console_portable as packager
    release = tmp_path / "release"
    product = release / "OrionQ"
    product.mkdir(parents=True)
    exe = product / "OrionQ.exe"
    exe.write_bytes(b"candidate")
    worker = release / "worker.json"
    worker.write_text(json.dumps({"passed": True, "sha256": packager.digest(exe)}), encoding="utf-8")
    branding = release / "branding-validation"
    branding.mkdir()
    (branding / "branding-report.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(packager, "validate_branding", lambda *args, **kwargs: None)
    mission = release / "satellite-mission-validation"
    mission.mkdir()
    (mission / "report.json").write_text("{}", encoding="utf-8")
    # Mission qualification is independent; isolate the new packaging gate.
    monkeypatch.setattr(packager, "validate_satellite_mission", lambda *args, **kwargs: None)
    with pytest.raises(FileNotFoundError, match="desktop-behavior-validation"):
        packager.package(release, worker)
    assert not list(release.glob("*.zip"))


@pytest.mark.parametrize("mutation", ["missing-reference", "missing-file", "outside-release", "stale-native",
    "source-native", "different-cases", "stale-outer", "changed-binary", "failed-outer", "operator-state"])
def test_package_gate_rejects_missing_foreign_stale_or_source_native_evidence(tmp_path, mutation):
    gate, exe, root, native_path, record, outer = _gate_fixture(tmp_path)
    if mutation == "missing-reference":
        outer.pop("native_report")
    elif mutation == "missing-file":
        native_path.unlink()
    elif mutation == "outside-release":
        outside = tmp_path / "other-release.json"
        outside.write_text(json.dumps(record), encoding="utf-8")
        outer["native_report"] = str(outside)
    elif mutation in {"stale-native", "source-native", "different-cases"}:
        if mutation == "stale-native":
            record["executable_sha256"] = "old-candidate"
        elif mutation == "source-native":
            record["frozen"] = False
        else:
            record["cases"][0]["different-evidence"] = True
        native_path.write_text(json.dumps(record), encoding="utf-8")
    elif mutation == "stale-outer":
        outer["executable_sha256_after"] = "previous-candidate"
    elif mutation == "changed-binary":
        exe.write_bytes(b"changed after qualification")
    elif mutation == "failed-outer":
        outer["passed"] = False
    elif mutation == "operator-state":
        outer["operator_state_accessed"] = True
    with pytest.raises(ValueError, match="frozen desktop"):
        gate.verify_gate(outer, exe, evidence_root=root)
