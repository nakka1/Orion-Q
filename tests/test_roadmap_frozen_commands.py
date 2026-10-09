"""Frozen command dispatch stays outside the operator desktop and live state."""
import importlib.util
from pathlib import Path

import pytest

from quantum_autopilot.control_plane import controller_evidence, runtime_diagnostic


@pytest.mark.parametrize("flag,module", [("--compare-controllers", controller_evidence),
                                        ("--diagnose-runtime", runtime_diagnostic)])
def test_launcher_early_dispatch_without_desktop(flag, module, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "packaging/console_launcher.py"
    spec = importlib.util.spec_from_file_location("roadmap_launcher", path)
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    calls = []
    monkeypatch.setattr(module, "main", lambda argv: calls.append(argv) or 19)
    assert launcher.launch([flag, "--output", "outside-product"]) == 19
    assert calls == [["--output", "outside-product"]]


def test_embedded_runtime_provenance_without_source_or_git(monkeypatch):
    monkeypatch.setattr(runtime_diagnostic.sys, "frozen", True, raising=False)
    monkeypatch.setattr(runtime_diagnostic.subprocess, "run", lambda *a, **k: pytest.fail("frozen runtime invoked git"))
    environment = runtime_diagnostic.environment()
    assert environment["source_hash_origin"] == "embedded_build_manifest"
    assert "src/quantum_autopilot/control_plane/runtime.py" in environment["source_sha256"]


def test_packaging_requires_roadmap_checks_for_the_same_executable(tmp_path):
    path = Path(__file__).resolve().parents[1] / "tools/package_console_portable.py"
    spec = importlib.util.spec_from_file_location("roadmap_packager", path)
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    exe = tmp_path / "test.exe"
    exe.write_bytes(b"fixture")
    report = {"schema": "orion.frozen-roadmap-check.v1", "passed": True,
              "executable_sha256": packager.digest(exe),
              "checks": {name: True for name in
                         ("comparison_30", "comparison_120", "comparison_250", "isolated_runtime_20_cycles")}}
    packager.validate_roadmap(report, exe)
    report["checks"]["comparison_120"] = False
    with pytest.raises(ValueError):
        packager.validate_roadmap(report, exe)
    report["checks"]["comparison_120"] = True
    exe.write_bytes(b"changed")
    with pytest.raises(ValueError):
        packager.validate_roadmap(report, exe)
