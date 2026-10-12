"""Administrative identity correction; fixtures provide no scientific evidence."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from tests.qualification_fixtures import extract_qualification_fixture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sequence_network_coverage_contract", ROOT / "tools/finalize_sequence_network_coverage.py")
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)


def fixture():
    census = {"schema": "orionq.sequence-network-inventory.v1",
        "nodes": [{"node_id": f"N-{i}"} for i in range(71)],
        "links": [{"link_id": f"L-{i}"} for i in range(131)],
        "sessions": [{"session_id": f"S-{i}"} for i in range(36)],
        "global_demand_templates": [{"demand_id": f"D-{i}"} for i in range(76)], "fpga": {"devices": []}}
    for i in range(3):
        config = {"schema": "orion.fpga-devices.v1", "device_id": f"FPGA-{i}", "node_id": f"N-{i}",
                  "adapter_id": "simulated", "clock_hz": 100000000., "label": "Fixture only"}
        census["fpga"]["devices"].append({"configuration": config, "configuration_sha256": coverage.digest(config), "mode": "simulated"})
    expected, _ = coverage.validate_census(census)
    rows = []
    for kind, identifiers in expected.items():
        for ident in sorted(identifiers):
            rows.append({"component": "unknown" if kind == "fpga" else ident, "kind": kind,
                "native_quantum_scope": "not_demonstrated", "native_classical_received_at_source": 0,
                "physical_connection_validated": False, "certified_secret_bits": 0,
                "detail": {"scope": "discardable manufactured contract fixture; not scientific evidence"}})
    report = {"schema": "orionq.sequence-network-campaign.v1", "status": "partial_verified",
        "covered_scope_passed": True, "full_hybrid_quantum_network_validated": False,
        "coverage": {"rows": 317, "physical_nodes": 71, "physical_links": 131, "sessions": 36, "global_demands": 76}}
    return census, rows, report


def write_campaign(directory):
    directory.mkdir()
    census, rows, report = fixture()
    for name, value in (("census.json", census), ("coverage-matrix.json", rows), ("freeze.json", {"fixture": True})):
        (directory / name).write_text(json.dumps(value), encoding="utf-8")
    (directory / "coverage-matrix.csv").write_text("fixture raw CSV preserved\n", encoding="utf-8")
    (directory / "coverage-matrix.md").write_text("fixture raw Markdown preserved\n", encoding="utf-8")
    report["coverage"]["matrix_sha256"] = coverage.sha(directory / "coverage-matrix.json")
    report["census_sha256"] = coverage.sha(directory / "census.json")
    report["freeze_sha256"] = coverage.sha(directory / "freeze.json")
    (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return {name: coverage.sha(directory / name) for name in coverage.RAW_INPUTS}


def test_only_three_fpga_rows_change_and_nested_identity_hash_is_bound():
    census, rows, report = fixture()
    before = deepcopy((census, rows, report))
    identified, changes = coverage.derive_coverage(census, rows, report)
    assert (census, rows, report) == before
    assert identified[:-3] == rows[:-3]
    assert [row["component"] for row in identified[-3:]] == ["FPGA-0", "FPGA-1", "FPGA-2"]
    assert len({row["component"] for row in identified}) == 317
    assert len(changes) == 3
    for old, new, device in zip(rows[-3:], identified[-3:], census["fpga"]["devices"]):
        assert new["detail"]["scope"] == old["detail"]["scope"]
        assert new["detail"]["configuration"] == device["configuration"]
        assert new["detail"]["configuration_sha256"] == device["configuration_sha256"]
        assert new["native_quantum_scope"] == "not_demonstrated"
        assert new["physical_connection_validated"] is False


@pytest.mark.parametrize("damage", ["missing_device", "missing_nested_identity", "duplicate_device",
    "configuration_hash", "associated_node", "missing_node", "duplicate_node", "identity_collision",
    "schema", "configuration_schema"])
def test_census_corruption_refused(damage):
    census, _, _ = fixture()
    devices = census["fpga"]["devices"]
    if damage == "missing_device":
        devices.pop()
    elif damage == "missing_nested_identity":
        devices[0]["configuration"].pop("device_id")
        devices[0]["configuration_sha256"] = coverage.digest(devices[0]["configuration"])
    elif damage == "duplicate_device":
        devices[1] = deepcopy(devices[0])
    elif damage == "configuration_hash":
        devices[0]["configuration_sha256"] = "0" * 64
    elif damage == "associated_node":
        devices[0]["configuration"]["node_id"] = "ABSENT"
        devices[0]["configuration_sha256"] = coverage.digest(devices[0]["configuration"])
    elif damage == "missing_node":
        census["nodes"].pop()
    elif damage == "duplicate_node":
        census["nodes"][1] = deepcopy(census["nodes"][0])
    elif damage == "identity_collision":
        census["links"][0]["link_id"] = "N-0"
    elif damage == "schema":
        census["schema"] = "wrong"
    else:
        devices[0]["configuration"]["schema"] = "wrong"
        devices[0]["configuration_sha256"] = coverage.digest(devices[0]["configuration"])
    with pytest.raises(ValueError):
        coverage.validate_census(census)


@pytest.mark.parametrize("damage", ["missing_row", "duplicate_non_fpga", "wrong_identity", "wrong_kind",
    "wrong_report_count", "missing_report", "fpga_already_identified", "qualified_fpga", "secret_bits", "overwrite_detail"])
def test_raw_coverage_or_report_mismatch_refused(damage):
    census, rows, report = fixture()
    if damage == "missing_row":
        rows.pop()
    elif damage == "duplicate_non_fpga":
        rows[1] = deepcopy(rows[0])
    elif damage == "wrong_identity":
        rows[0]["component"] = "WRONG"
    elif damage == "wrong_kind":
        rows[0]["kind"] = "fpga"
    elif damage == "wrong_report_count":
        report["coverage"]["physical_nodes"] = 70
    elif damage == "missing_report":
        report = {}
    elif damage == "fpga_already_identified":
        rows[-1]["component"] = "FPGA-2"
    elif damage == "qualified_fpga":
        rows[-1]["physical_connection_validated"] = True
    elif damage == "secret_bits":
        rows[-1]["certified_secret_bits"] = 1
    else:
        rows[-1]["detail"]["configuration"] = {"overwrite": "refused"}
    with pytest.raises(ValueError):
        coverage.derive_coverage(census, rows, report)


def test_finalize_writes_new_files_and_preserves_originals_exactly(tmp_path):
    directory = tmp_path / "campaign"
    originals = write_campaign(directory)
    manifest = coverage.finalize(directory)
    assert originals == {name: coverage.sha(directory / name) for name in coverage.RAW_INPUTS}
    assert manifest["originals_unchanged"] and manifest["non_fpga_rows_unchanged"]
    assert manifest["raw_export_defect"]["unknown_fpga_rows"] == 3
    assert manifest["raw_export_defect"]["raw_unique_component_identifiers"] == 315
    assert manifest["unique_component_identifiers"] == 317
    assert not manifest["scientific_results_recomputed"] and not manifest["scientific_acceptance_changed"]
    assert manifest["raw_campaign_status"] == "partial_verified"
    assert set(manifest["postprocessing_source_sha256"]) == set(coverage.SOURCE_FILES)
    for name, fingerprint in manifest["identified_output_sha256"].items():
        assert coverage.sha(directory / name) == fingerprint
    assert coverage.load(directory / coverage.OUTPUTS[3]) == manifest
    assert "FPGA-0" in (directory / "coverage-matrix-identified.md").read_text(encoding="utf-8")


def test_overwrite_refused_before_modifying_any_file(tmp_path):
    directory = tmp_path / "campaign"
    write_campaign(directory)
    coverage.finalize(directory)
    before = {path.name: coverage.sha(path) for path in directory.iterdir()}
    with pytest.raises(ValueError, match="overwrite refused"):
        coverage.finalize(directory)
    assert before == {path.name: coverage.sha(path) for path in directory.iterdir()}


@pytest.mark.parametrize("artifact", ["report.json", "coverage-matrix.json", "census.json"])
def test_required_final_artifacts_or_report_binding_failure_write_nothing(tmp_path, artifact):
    directory = tmp_path / "campaign"
    write_campaign(directory)
    (directory / artifact).write_text("{}", encoding="utf-8")
    with pytest.raises((ValueError, KeyError)):
        coverage.finalize(directory)
    assert not any((directory / name).exists() for name in coverage.OUTPUTS)


def test_missing_final_report_writes_nothing(tmp_path):
    directory = tmp_path / "campaign"
    directory.mkdir()
    with pytest.raises(ValueError, match="final report"):
        coverage.finalize(directory)
    assert not list(directory.iterdir())


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"a":NaN}'])
def test_ambiguous_or_nonfinite_json_refused(tmp_path, text):
    source = tmp_path / "invalid.json"
    source.write_text(text)
    with pytest.raises(ValueError):
        coverage.load(source)


@pytest.mark.integration
def test_real_frozen_census_read_only_has_three_identified_fpga_configurations(tmp_path):
    # Archived public inputs; this regression is not a new SeQUeNCe experiment.
    directory = extract_qualification_fixture(
        "sequence-network-20261010-public-fixture.zip", tmp_path / "campaign")
    census = coverage.load(directory / "census.json")
    before = coverage.sha(directory / "census.json")
    expected, devices = coverage.validate_census(census)
    assert expected["fpga"] == {"FPGA-DEMO-1", "FPGA-DEMO-2", "FPGA-DEMO-3"}
    assert len(set.union(*expected.values())) == 317
    assert all(device["configuration"]["adapter_id"] == "simulated" for device in devices)
    rows = coverage.load(directory / "coverage-matrix.json")
    report = coverage.load(directory / "report.json")
    identified, changes = coverage.derive_coverage(census, rows, report)
    assert len(identified) == 317 and len(changes) == 3
    assert identified[:-3] == rows[:-3]
    assert coverage.sha(directory / "census.json") == before
