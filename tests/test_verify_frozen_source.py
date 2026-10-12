"""Static release verification rejects stale/tampered packages without execution."""
from __future__ import annotations

from importlib.util import MAGIC_NUMBER, module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from quantum_autopilot.console.branding_validation import code_fingerprint
from quantum_autopilot.product import product_version


pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location("verify_frozen_source", ROOT / "tools/verify_frozen_source.py")
TOOL = module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)
REQUIRED_MODULES = TOOL.MODULES


@pytest.fixture
def package(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source = source_root / "src/quantum_autopilot/sample.py"
    source.parent.mkdir(parents=True)
    # Both branches would fail if any code under inspection were executed.
    body = 'assert False, "inspection must not execute"\nraise RuntimeError("bundled execution forbidden")\n'
    source.write_text(body, encoding="utf-8")
    code = compile(body, "/different/frozen/location.py", "exec", dont_inherit=True, optimize=1)
    exe = tmp_path / "portable/OrionQ.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"PYZ\0" + MAGIC_NUMBER + b"frozen-test-fixture")
    internal = exe.parent / "_internal"
    metadata = internal / "quantum_twin.dist-info/METADATA"
    metadata.parent.mkdir(parents=True)
    version = product_version()
    metadata.write_text(f"Metadata-Version: 2.1\nName: quantum-twin\nVersion: {version}\n", encoding="utf-8")
    relative = "quantum_protocols/data/sources_v1.json"
    resource = source_root / "src/quantum_autopilot" / relative
    resource.parent.mkdir(parents=True)
    resource.write_text('{"origin":"identified fixture"}', encoding="utf-8")
    bundled = internal / "quantum_autopilot" / relative
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(resource.read_bytes())
    expected_pe = {"ProductName": "OrionQ", "OriginalFilename": "OrionQ.exe",
                   "FileVersion": version, "ProductVersion": version}

    class Archive:
        _start_offset = 0
        toc = {"PYZ.pyz": (0, 32, 32, 0, "z")}
        modules = {"quantum_autopilot.sample": code}
        opened = False

        def open_embedded_archive(self, name):
            self.opened = True
            return self

        def extract(self, name):
            return self.modules[name]

    archive = Archive()
    monkeypatch.setattr(TOOL, "MODULES", ("quantum_autopilot.sample",))
    monkeypatch.setattr(TOOL, "RESOURCES", (relative,))
    monkeypatch.setattr(TOOL, "_archive_reader", lambda path: archive)
    monkeypatch.setattr(TOOL, "_source_helpers", lambda: (
        code_fingerprint, lambda path: expected_pe,
        "quantum-twin", "OrionQ.exe", "OrionQ", version,
    ))
    monkeypatch.setattr(TOOL.sys, "platform", "win32")
    return {"source_root": source_root, "source": source, "exe": exe, "archive": archive,
            "bundled": bundled, "metadata": metadata, "version": version, "pe": expected_pe}


def verify(package):
    return TOOL.verify(package["exe"], source_root=package["source_root"])


def test_matching_package_is_compared_without_execution_or_state_access(package):
    before = package["exe"].read_bytes()
    result = verify(package)
    assert result["passed"] and all(result["checks"].values())
    assert result["source_version"] == product_version()
    assert result["modules"][0]["source_code_sha256"] == result["modules"][0]["frozen_code_sha256"]
    assert not result["frozen_code_executed"] and not result["operator_state_accessed"]
    assert package["exe"].read_bytes() == before


def test_changed_frozen_logic_is_rejected(package):
    package["archive"].modules["quantum_autopilot.sample"] = compile("value = 'stale'", "frozen", "exec", optimize=1)
    result = verify(package)
    assert not result["passed"] and not result["checks"]["all_required_modules_match"]


def test_missing_hybrid_module_is_rejected(package):
    package["archive"].modules.clear()
    result = verify(package)
    assert not result["passed"]
    assert "KeyError" in result["modules"][0]["error"]


@pytest.mark.parametrize("frozen_state", ["matching", "stale", "missing"])
def test_communicator_diagnostic_is_compared_without_executing_code(package, monkeypatch, frozen_state):
    module = "quantum_autopilot.console.communicators_validation"
    assert module in REQUIRED_MODULES
    source = package["source_root"] / "src/quantum_autopilot/console/communicators_validation.py"
    source.parent.mkdir()
    body = 'channel_contract = "current"\nraise RuntimeError("diagnostic inspection must not execute")\n'
    source.write_text(body, encoding="utf-8")
    monkeypatch.setattr(TOOL, "MODULES", ("quantum_autopilot.sample", module))
    if frozen_state != "missing":
        frozen_body = body if frozen_state == "matching" else body.replace('"current"', '"legacy"')
        package["archive"].modules[module] = compile(
            frozen_body, "frozen-communicator-diagnostic", "exec", dont_inherit=True, optimize=1)
    before = package["exe"].read_bytes()

    result = verify(package)

    comparison = next(row for row in result["modules"] if row["module"] == module)
    assert comparison["passed"] is (frozen_state == "matching")
    assert result["passed"] is (frozen_state == "matching")
    if frozen_state == "missing":
        assert "KeyError" in comparison["error"]
    else:
        assert comparison["source_file_sha256"] == TOOL.digest(source)
    assert result["frozen_code_executed"] is False
    assert result["gui_started"] is False
    assert result["operator_state_accessed"] is False
    assert package["exe"].read_bytes() == before


def test_wrong_optimization_is_rejected(package):
    package["archive"].modules["quantum_autopilot.sample"] = compile(
        package["source"].read_text(encoding="utf-8"), "frozen", "exec", dont_inherit=True, optimize=0)
    result = verify(package)
    assert not result["passed"] and not result["checks"]["all_required_modules_match"]


@pytest.mark.parametrize("frozen_state", ["matching", "stale", "missing"])
def test_real_service_provider_is_mandatory_and_stale_bootstrap_is_rejected(package, monkeypatch, frozen_state):
    module = "quantum_autopilot.console.service_provider"
    assert module in REQUIRED_MODULES
    source = package["source_root"] / "src/quantum_autopilot/console/service_provider.py"
    source.parent.mkdir()
    body = (ROOT / "src/quantum_autopilot/console/service_provider.py").read_text(encoding="utf-8")
    source.write_text(body, encoding="utf-8")
    monkeypatch.setattr(TOOL, "MODULES", ("quantum_autopilot.sample", module))
    if frozen_state != "missing":
        frozen_body = body if frozen_state == "matching" else body + "\nSTALE_BOOTSTRAP_CANDIDATE = True\n"
        package["archive"].modules[module] = compile(
            frozen_body, "frozen-service-provider", "exec", dont_inherit=True, optimize=1)
    before = package["exe"].read_bytes()

    result = verify(package)

    comparison = next(row for row in result["modules"] if row["module"] == module)
    assert comparison["passed"] is (frozen_state == "matching")
    assert result["passed"] is (frozen_state == "matching")
    assert result["checks"]["all_required_modules_match"] is (frozen_state == "matching")
    if frozen_state == "missing":
        assert "KeyError" in comparison["error"]
    else:
        assert comparison["source_file_sha256"] == TOOL.digest(source)
    assert result["frozen_code_executed"] is False
    assert result["gui_started"] is False
    assert result["operator_state_accessed"] is False
    assert package["exe"].read_bytes() == before


def test_tampered_parameter_resource_is_rejected(package):
    package["bundled"].write_text('{"origin":"unidentified"}', encoding="utf-8")
    result = verify(package)
    assert not result["passed"] and not result["checks"]["all_required_resources_match"]


@pytest.mark.parametrize("duplicate", [False, True])
def test_stale_or_duplicate_distribution_metadata_is_rejected(package, duplicate):
    path = package["metadata"]
    if duplicate:
        path = path.parent.parent / "quantum_twin_duplicate.dist-info/METADATA"
        path.parent.mkdir()
    path.write_text("Name: quantum-twin\nVersion: stale-version\n", encoding="utf-8")
    result = verify(package)
    assert not result["passed"] and not result["checks"]["distribution_metadata_matches"]


@pytest.mark.parametrize("distribution_name", ["quantum-twin", "quantum_twin"])
def test_editable_egg_info_is_accepted_with_canonical_name(package, distribution_name):
    package["metadata"].unlink()
    metadata = package["metadata"].parent.parent / "quantum_twin.egg-info/PKG-INFO"
    metadata.parent.mkdir()
    metadata.write_text(f"Name: {distribution_name}\nVersion: {package['version']}\n", encoding="utf-8")
    result = verify(package)
    assert result["passed"] and result["checks"]["distribution_metadata_matches"]
    assert len(result["distribution_metadata"]) == 1
    assert result["distribution_metadata"][0]["normalized_name"] == "quantum-twin"


def test_simultaneous_egg_and_dist_metadata_are_rejected_even_when_versions_match(package):
    metadata = package["metadata"].parent.parent / "quantum_twin.egg-info/PKG-INFO"
    metadata.parent.mkdir()
    metadata.write_text(f"Name: quantum_twin\nVersion: {package['version']}\n", encoding="utf-8")
    result = verify(package)
    assert not result["passed"] and not result["checks"]["distribution_metadata_matches"]
    assert len(result["distribution_metadata"]) == 2


def test_stale_egg_info_is_rejected(package):
    package["metadata"].unlink()
    metadata = package["metadata"].parent.parent / "quantum_twin.egg-info/PKG-INFO"
    metadata.parent.mkdir()
    metadata.write_text("Name: quantum_twin\nVersion: stale-version\n", encoding="utf-8")
    result = verify(package)
    assert not result["passed"] and not result["checks"]["distribution_metadata_matches"]


def test_incompatible_python_magic_is_rejected_before_opening_pyz(package):
    package["exe"].write_bytes(b"PYZ\0" + b"bad!" + b"frozen-fixture")
    result = verify(package)
    assert not result["passed"] and not package["archive"].opened
    assert any("bytecode version" in error for error in result["errors"])


def test_stale_pe_version_is_rejected(package):
    package["pe"]["FileVersion"] = "stale-version"
    result = verify(package)
    assert not result["passed"] and not result["checks"]["windows_version_matches"]


def test_concurrent_executable_change_is_detected(package, monkeypatch):
    def changed(path):
        path.write_bytes(path.read_bytes() + b"external replacement")
        return package["pe"]

    monkeypatch.setattr(TOOL, "_source_helpers", lambda: (
        code_fingerprint, changed, "quantum-twin", "OrionQ.exe", "OrionQ", package["version"]))
    result = verify(package)
    assert not result["passed"] and not result["checks"]["executable_unchanged"]


def test_cli_never_overwrites_existing_evidence(package, tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("preserve", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        TOOL.main(["--exe", str(package["exe"]), "--output", str(output)])
    assert error.value.code == 2 and output.read_text(encoding="utf-8") == "preserve"
