"""Release readers reject incomplete/stale evidence without starting a process."""
import importlib.util
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from quantum_autopilot.product import PRODUCT_NAME
from quantum_autopilot.windows_acceptance import verify_package

SPEC = importlib.util.spec_from_file_location(
    "orion_native_release_reader", Path(__file__).resolve().parents[1] / "tools/consolidate_native_release.py")
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)


def write_json(path, record):
    path.write_text(json.dumps(record), encoding="utf-8")


def junit(path, cases):
    text = '<testsuites><testsuite name="fixtures">'
    for name, status in cases:
        text += f'<testcase classname="test_fixture" name="{name}">'
        text += {"passed": "", "failures": "<failure/>", "errors": "<error/>",
                 "skipped": "<skipped/>"}[status] + "</testcase>"
    path.write_text(text + "</testsuite></testsuites>", encoding="utf-8")


def test_junit_keeps_historical_failures_and_counts_unique_latest_cases(tmp_path):
    earlier, later = tmp_path / "first.xml", tmp_path / "latest.xml"
    junit(earlier, [("a", "failures"), ("b", "passed"), ("c", "skipped")])
    junit(later, [("a", "passed"), ("b", "passed"), ("d", "errors")])
    os.utime(earlier, ns=(1000000000, 1000000000))
    os.utime(later, ns=(2000000000, 2000000000))
    result = reader.junit_summary([later, earlier])
    assert result["executions"] == 6 and result["unique_cases"] == 4
    assert result["execution_counts"] == {"passed": 3, "failures": 1, "errors": 1, "skipped": 1}
    assert result["latest_case_counts"] == {"passed": 2, "failures": 0, "errors": 1, "skipped": 1}
    assert {row["name"] for row in result["latest_nonpassed_cases"]} == {"c", "d"}
    assert "not a full-repository" in result["scope"]


@pytest.mark.parametrize("status", ["failures", "errors"])
def test_mandatory_junit_never_uses_later_success_to_hide_failed_build_gate(tmp_path, status):
    first, latest = tmp_path / "required.xml", tmp_path / "later.xml"
    junit(first, [("a", status)])
    junit(latest, [("a", "passed")])
    with pytest.raises(ValueError, match="Mandatory JUnit"):
        reader.junit_summary([first, latest], required_names=[first.name])


def test_missing_or_empty_junit_gate_rejects_partial_build(tmp_path):
    with pytest.raises(ValueError, match="incomplete"):
        reader.junit_summary([], required_names=["missing.xml"])
    path = tmp_path / "empty.xml"
    junit(path, [])
    with pytest.raises(ValueError, match="empty or failed"):
        reader.junit_summary([path], required_names=[path.name])


def test_suite_collection_errors_are_not_reported_as_success(tmp_path):
    path = tmp_path / "collection.xml"
    path.write_text('<testsuites><testsuite tests="0" errors="1"/></testsuites>')
    with pytest.raises(ValueError, match="no recorded case"):
        reader.junit_summary([path])


def test_mandatory_junit_paths_follow_actual_build_script_and_are_unique():
    names = reader.mandatory_junits()
    assert len(names) > 20 and len(names) == len(set(names))
    assert "pytest-laboratory-results.xml" in names
    assert "pytest-channel-intake-results.xml" in names


@pytest.mark.parametrize("field,value", [
    ("automatic_passed", "true"), ("acceptance_status", "approved"),
    ("manual_review", "approved"), ("independent_machine_verified", True),
    ("integrity_only", True), ("scope", "hardware_qualified"),
    ("package", {"executable_sha256": "wrong"}), ("error", "failed"),
])
def test_acceptance_requires_exact_package_and_keeps_manual_review_separate(field, value):
    package = {"executable_sha256": "a" * 64, "manifest_sha256": "b" * 64,
               "file_count": 2, "executable": "OrionQ.exe"}
    record = {"schema": "orion.windows_acceptance.v1", "automatic_passed": True,
              "acceptance_status": "manual_review_required", "scope": "local_software_checks_only",
              "manual_review": "pending", "independent_machine_verified": False,
              "integrity_only": False, "error": None, "package": package,
              "checks": {"integrity": True, "self_test": True, "synthetic_stress_20_rounds": True}}
    reader.verify_acceptance(record, package)
    record[field] = value
    with pytest.raises(ValueError, match="Native acceptance"):
        reader.verify_acceptance(record, package)


@pytest.mark.parametrize("record", [
    {"passed": "true"}, {"passed": True, "checks": {"gate": False}},
    {"passed": True, "checks": [{"passed": False}]},
    {"passed": True, "executable_sha256": "different"},
    {"passed": True, "executable_sha256_after": "different"},
])
def test_native_reports_cannot_override_failed_checks_or_binary_identity(record):
    with pytest.raises(ValueError):
        reader._passed(record, "native fixture", "a" * 64)


@pytest.fixture
def portable(tmp_path):
    release = tmp_path / "release"
    product = release / PRODUCT_NAME
    (product / "_internal").mkdir(parents=True)
    exe = product / (PRODUCT_NAME + ".exe")
    exe.write_bytes(b"fixture-not-an-executable")
    (product / "_internal/data.bin").write_bytes(b"payload")
    files = sorted(path for path in product.rglob("*") if path.is_file())
    (product / "SHA256SUMS.txt").write_text("".join(
        f"{reader.digest(path)}  {path.relative_to(product).as_posix()}\n" for path in files))
    archive = release / f"{PRODUCT_NAME}-fixture-native-windows-x64.zip"
    with ZipFile(archive, "w") as bundle:
        for path in product.rglob("*"):
            if path.is_file():
                bundle.write(path, path.relative_to(release).as_posix())

    def update_record():
        record = {"archive": str(archive), "sha256": reader.digest(archive),
                  "bytes": archive.stat().st_size, "executable_sha256": reader.digest(exe),
                  "files": 3, "zip_crc_verified": True}
        write_json(release / "portable-package.json", record)
        Path(str(archive) + ".sha256").write_text(f"{record['sha256']}  {archive.name}\n")
    update_record()
    return release, product, exe, archive, update_record


def test_portable_rechecks_crc_all_bytes_and_metadata_without_extracting(portable):
    release, product, exe, _, _ = portable
    before = {path.relative_to(release): reader.digest(path) for path in release.rglob("*") if path.is_file()}
    result = reader.verify_portable(release, product, exe, verify_package(product), version="fixture")
    assert result["zip_crc_rechecked"] is True and result["all_archived_bytes_match_package"] is True
    assert before == {path.relative_to(release): reader.digest(path) for path in release.rglob("*") if path.is_file()}


@pytest.mark.parametrize("damage", ["altered_payload", "extra_entry", "duplicate_entry", "bad_sidecar"])
def test_portable_rejects_repacked_different_bytes_even_with_updated_outer_hash(portable, damage):
    release, product, exe, archive, update_record = portable
    integrity = verify_package(product)
    if damage == "bad_sidecar":
        Path(str(archive) + ".sha256").write_text("0" * 64 + "  " + archive.name)
    else:
        with ZipFile(archive) as bundle:
            entries = [(entry.filename, bundle.read(entry)) for entry in bundle.infolist()]
        if damage == "altered_payload":
            entries = [(name, b"altered" if name.endswith("data.bin") else data) for name, data in entries]
        else:
            entries.append(("unexpected.txt" if damage == "extra_entry" else entries[0][0], b"extra"))
        with ZipFile(archive, "w") as bundle:
            for name, data in entries:
                bundle.writestr(name, data)
        update_record()
    with pytest.raises(ValueError, match="differ"):
        reader.verify_portable(release, product, exe, integrity, version="fixture")


@pytest.fixture
def static_report(tmp_path, monkeypatch):
    project = tmp_path / "source"
    source = project / "src/quantum_autopilot/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("example = 1\n")
    resource = source.parent / "example.json"
    resource.write_text("{}")
    exe = tmp_path / "OrionQ.exe"
    exe.write_bytes(b"fixture-not-an-executable")
    bundled = tmp_path / "_internal/quantum_autopilot/example.json"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(resource.read_bytes())
    monkeypatch.setattr(reader, "ROOT", project)
    monkeypatch.setattr(reader, "_tool", lambda name: SimpleNamespace(
        MODULES=("quantum_autopilot.example",), RESOURCES=("example.json",)))
    record = {
        "schema": "orion.frozen-source-verification.v1", "passed": True,
        "source_version": "fixture", "source_optimization": 1,
        "errors": [], "executable": str(exe),
        "executable_sha256_before": reader.digest(exe), "executable_sha256_after": reader.digest(exe),
        "frozen_code_executed": False, "gui_started": False, "operator_state_accessed": False,
        "physical_action_performed": False,
        "checks": {key: True for key in (
            "executable_name", "all_required_modules_match", "all_required_resources_match",
            "excluded_resources_absent",
            "distribution_metadata_matches", "windows_version_matches", "executable_unchanged")},
        "modules": [{"module": "quantum_autopilot.example", "passed": True,
                     "source_file_sha256": reader.digest(source), "source_code_sha256": "a" * 64,
                     "frozen_code_sha256": "a" * 64}],
        "resources": [{"resource": "example.json", "passed": True,
                       "source_sha256": reader.digest(resource), "bundled_sha256": reader.digest(bundled)}],
        "distribution_metadata": [{"version": "fixture"}],
        "windows_version_info": {"ProductName": PRODUCT_NAME, "OriginalFilename": exe.name,
                                "FileVersion": "fixture", "ProductVersion": "fixture"}}
    report = tmp_path / "static.json"
    write_json(report, record)
    return report, record, exe, source


def test_static_source_comparison_requires_complete_corpus_and_current_source(static_report):
    report, record, exe, source = static_report
    assert reader.verify_frozen_report(report, exe, version="fixture")["passed"] is True
    source.write_text("example = 2\n")
    with pytest.raises(ValueError, match="stale or mismatched"):
        reader.verify_frozen_report(report, exe, version="fixture")
    source.write_text("example = 1\n")
    record["modules"] = []
    write_json(report, record)
    with pytest.raises(ValueError, match="corpus is incomplete"):
        reader.verify_frozen_report(report, exe, version="fixture")


@pytest.mark.parametrize("optimization", [None, False, True, 0, 2, 1.0, "1"])
def test_static_proof_requires_exact_compiled_optimization_one(static_report, optimization):
    report, record, exe, _ = static_report
    record["source_optimization"] = optimization
    write_json(report, record)
    with pytest.raises(ValueError, match="Static source report"):
        reader.verify_frozen_report(report, exe, version="fixture")


@pytest.mark.parametrize("damage", ["missing_resource", "invalid_module", "false_module", "false_check"])
def test_static_proof_cannot_accept_incomplete_or_failed_corpus(static_report, damage):
    report, record, exe, _ = static_report
    if damage == "missing_resource":
        record["resources"] = []
    elif damage == "invalid_module":
        record["modules"] = [None]
    elif damage == "false_module":
        record["modules"][0]["passed"] = False
    else:
        record["checks"]["all_required_modules_match"] = False
    write_json(report, record)
    with pytest.raises(ValueError):
        reader.verify_frozen_report(report, exe, version="fixture")


@pytest.fixture(scope="module")
def source_scientific_report():
    # Exercise the actual v1 corpus once, independent of the reader's name list.
    # This constructs local simulated channels only; it never starts a backend,
    # GUI, instrument or acquisition service.
    from quantum_twin.core.runtime_validation import validate_scientific_inputs

    report = validate_scientific_inputs()
    assert report["passed"] is True and report["checks_count"] == 113
    assert sum(row["outcome"] == "rejected" for row in report["checks"]) == 112
    report["frozen"] = True
    report["python_optimization"] = 0
    return report


@pytest.fixture
def scientific_report(source_scientific_report):
    return deepcopy(source_scientific_report)


def scientific_check(report, source_optimization=1):
    return reader.verify_scientific_input(
        report, executable_sha256="a" * 64, source_optimization=source_optimization)


@pytest.mark.parametrize("runtime_flag", [0, 1, 2])
def test_real_scientific_corpus_accepts_bootloader_flag_separately_from_static_optimization(
        scientific_report, static_report, runtime_flag):
    report, _, exe, _ = static_report
    proof = reader.verify_frozen_report(report, exe, version="fixture")
    scientific_report["python_optimization"] = runtime_flag
    result = scientific_check(scientific_report, proof["source_optimization"])
    assert result == {"passed": True, "checks": 113, "rejected_inputs": 112, "boundaries": 1,
                      "python_runtime_optimization": runtime_flag, "compiled_source_optimization": 1}


@pytest.mark.parametrize("flag", [None, False, True, -1, 3, 0.0, 1.0, "0"])
def test_scientific_corpus_rejects_invalid_or_boolean_runtime_flag(scientific_report, flag):
    scientific_report["python_optimization"] = flag
    with pytest.raises(ValueError, match="scope/optimization"):
        scientific_check(scientific_report)


@pytest.mark.parametrize("optimization", [None, False, True, 0, 2, 1.0, "1"])
def test_scientific_runtime_success_never_substitutes_for_compiled_proof(scientific_report, optimization):
    with pytest.raises(ValueError, match="scope/optimization"):
        scientific_check(scientific_report, optimization)


@pytest.mark.parametrize("field,value", [
    ("schema", "orion.scientific-input-validation.v2"), ("passed", False),
    ("frozen", False), ("frozen", 1), ("hardware_validated", True),
    ("physical_action_performed", True), ("checks_count", 112), ("checks_count", 114),
    ("checks_count", 113.0), ("checks_count", True), ("python_version", ""),
    ("scope", "experimental qualification"), ("checks", []), ("checks", {}),
    ("executable_sha256", "b" * 64),
])
def test_scientific_corpus_requires_native_scope_complete_counters_and_identity(scientific_report, field, value):
    scientific_report[field] = value
    with pytest.raises(ValueError):
        scientific_check(scientific_report)


@pytest.mark.parametrize("damage", ["duplicate", "empty", "missing", "unknown", "nonstring"])
def test_scientific_corpus_checks_actual_unique_input_names(scientific_report, damage):
    rows = scientific_report["checks"]
    if damage == "duplicate":
        rows[0]["name"] = rows[1]["name"]
    elif damage == "empty":
        rows[0]["name"] = " "
    elif damage == "missing":
        rows.pop(0)
    else:
        rows[0]["name"] = "PhysicsConfig.unknown.nan" if damage == "unknown" else []
    with pytest.raises(ValueError):
        scientific_check(scientific_report)


@pytest.mark.parametrize("field,value", [
    ("passed", False), ("passed", 1), ("outcome", "invalid_input_accepted"),
    ("outcome", "evaluated"), ("error_type", "AssertionError"),
    ("error_type", "TypeError"), ("message", ""), ("message", " "), ("message", None),
])
def test_scientific_rejections_require_explicit_value_error_and_message(scientific_report, field, value):
    scientific_report["checks"][0][field] = value
    with pytest.raises(ValueError):
        scientific_check(scientific_report)


@pytest.mark.parametrize("field,value", [
    ("outcome", "rejected"), ("passed", False),
    ("pure_dephasing", 0.1), ("identity_fidelity", 0.99),
    ("fully_depolarized_fidelity", 0.3), ("fidelity_abs_tolerance", 0.1),
    ("identity_fidelity", float("nan")), ("identity_fidelity", float("inf")),
    ("pure_dephasing", False), ("identity_fidelity", "1.0"),
])
def test_scientific_boundary_rechecks_values_instead_of_trusting_passed(scientific_report, field, value):
    boundary = next(row for row in scientific_report["checks"] if row["name"] == "valid_physical_boundaries")
    boundary[field] = value
    with pytest.raises(ValueError):
        scientific_check(scientific_report)


def test_consolidation_requires_static_proof_before_accepting_native_reports(portable, monkeypatch):
    release, _, _, _, _ = portable
    calls = []

    def missing_proof(*args, **kwargs):
        calls.append("static")
        raise ValueError("Static source comparison corpus is incomplete")

    def forbidden_diagnostic(*args, **kwargs):
        pytest.fail("Diagnostic must not be accepted before complete static comparison")

    monkeypatch.setattr(reader, "verify_frozen_report", missing_proof)
    monkeypatch.setattr(reader, "verify_scientific_input", forbidden_diagnostic)
    with pytest.raises(ValueError, match="corpus is incomplete"):
        reader.consolidate(release, release / "missing-static.json")
    assert calls == ["static"]


def test_main_refuses_overwrite_and_output_inside_checksummed_product(tmp_path):
    existing = tmp_path / "existing.json"
    existing.write_text("sentinel")
    args = ["--release-root", str(tmp_path), "--frozen-source-report", str(tmp_path / "static.json")]
    with pytest.raises(SystemExit):
        reader.main([*args, "--output", str(existing)])
    assert existing.read_text() == "sentinel"
    with pytest.raises(SystemExit):
        reader.main([*args, "--output", str(tmp_path / PRODUCT_NAME / "new.json")])
    assert not (tmp_path / PRODUCT_NAME / "new.json").exists()
