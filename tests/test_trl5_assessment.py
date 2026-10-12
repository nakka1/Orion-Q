"""Evidence-integrity regressions; these do not certify the product."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("trl5_assessor", ROOT / "tools/assess_trl5_software.py")
assessor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assessor)


@pytest.fixture
def frozen(tmp_path):
    root = tmp_path / "source"
    (root / "configs/qualification").mkdir(parents=True)
    (root / assessor.PROTOCOL).write_bytes((ROOT / assessor.PROTOCOL).read_bytes())
    (root / "src").mkdir()
    (root / "src/kernel.py").write_text("value = 1\n")
    (root / "tools").mkdir()
    for kind in ("reference", "pipeline"):
        (root / "tools" / f"run_trl5_{kind}_campaign.py").write_text("# fixture\n")
    (root / "tools/run_trl5_reference_campaign.py").write_text(
        'def run_campaign(seed):\n'
        '    return {"checks": [{"name": "fixture", "expected": 1., "absolute_tolerance": 1e-12, "relative_tolerance": 1e-10}]}\n'
    )
    directory = tmp_path / "campaign"
    assessor.prepare(directory, root)
    return root, directory


def test_missing_evidence_never_qualifies(frozen):
    result = assessor.assess(frozen[1], frozen[0])
    assert result["assessment_status"] == "not_demonstrated"
    assert not result["trl5_demonstrated"] and not result["local_trial_passed"]
    assert all(gate["status"] == "not_demonstrated" for gate in result["gate_results"])


def test_protocol_changed_after_freeze_fails(frozen):
    root, directory = frozen
    protocol = json.loads((root / assessor.PROTOCOL).read_text())
    protocol["performance_requirements"]["runtime"]["budget_ms"] = 500
    (root / assessor.PROTOCOL).write_text(json.dumps(protocol))
    assert "Frozen protocol identity changed" in assessor.assess(directory, root)["observed_failures"]


def test_source_changed_after_freeze_fails(frozen):
    root, directory = frozen
    (root / "src/kernel.py").write_text("value = 2\n")
    assert any("Frozen source changed" in message for message in assessor.assess(directory, root)["observed_failures"])


@pytest.mark.parametrize("name", [
    "src/new_parameters.json", "tests/test_recovery.py", "tools/profile_trl5_persistence.py",
])
def test_source_addition_after_freeze_fails(frozen, name):
    root, directory = frozen
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"coefficient": 1}')
    assert any("Source added" in message for message in assessor.assess(directory, root)["observed_failures"])


def test_qualification_test_oracles_are_in_inventory(frozen):
    root, _ = frozen
    (root / "tests").mkdir()
    path = root / "tests/test_trl5_reference_campaign.py"
    path.write_text("# independent oracle regression\n")
    assert assessor.source_inventory(root)["tests/test_trl5_reference_campaign.py"] == assessor.sha(path)


def test_reference_cannot_relax_its_own_tolerance(frozen):
    root, directory = frozen
    path = directory / "reference-1"
    path.mkdir()
    manifest = json.loads((directory / "freeze.json").read_text())
    (path / "report.json").write_text(json.dumps({
        "schema": "orion.trl5.reference-campaign.v1", "seed": manifest["initial_trial_seed"],
        "source_hashes": {"src/kernel.py": assessor.sha(root / "src/kernel.py")},
        "passed": True, "physical_action_performed": False,
        "checks": [{"name": "fixture", "passed": True, "actual": 1.5, "expected": 1.,
                    "absolute_tolerance": 1., "relative_tolerance": 1e-10}],
    }))
    result = assessor.assess(directory, root)
    assert any("differs from frozen oracle" in failure for failure in result["observed_failures"])
    assert next(gate for gate in result["gate_results"] if gate["id"] == "G02")["status"] == "fail"


def test_failed_pipeline_check_is_assigned_to_pipeline_gate(frozen):
    root, directory = frozen
    path = directory / "pipeline-1"
    path.mkdir()
    manifest = json.loads((directory / "freeze.json").read_text())
    (path / "report.json").write_text(json.dumps({
        "schema": "orion.trl5.pipeline-campaign.v1", "seed": manifest["initial_trial_seed"],
        "source_hashes": {"src/kernel.py": assessor.sha(root / "src/kernel.py")},
        "passed": True, "physical_action_performed": False,
        "checks": {"causal_order": False},
    }))
    result = assessor.assess(directory, root)
    assert next(gate for gate in result["gate_results"] if gate["id"] == "G03")["status"] == "fail"
    assert next(gate for gate in result["gate_results"] if gate["id"] == "G01")["status"] == "not_demonstrated"


@pytest.mark.parametrize("measurement", [-1, True, "fast"])
def test_invalid_runtime_values_are_explicit_failures(frozen, measurement):
    root, directory = frozen
    path = directory / "runtime-baseline"
    path.mkdir()
    (path / "diagnostic.json").write_text(json.dumps({
        "schema": "orion.runtime-diagnostic.v1", "samples": 1, "budget_ms": 10,
        "failed_attempts": 0, "budget_exceeded": 0, "attempts": [{"total_ms": measurement}],
    }))
    result = assessor.assess(directory, root)
    assert any("finite numeric" in failure for failure in result["observed_failures"])
    assert next(gate for gate in result["gate_results"] if gate["id"] == "G01")["status"] == "not_demonstrated"
    assert next(gate for gate in result["gate_results"] if gate["id"] == "G05")["status"] == "fail"


def test_forged_runtime_pass_counter_is_rejected(frozen):
    root, directory = frozen
    path = directory / "runtime-baseline"
    path.mkdir()
    (path / "diagnostic.json").write_text(json.dumps({
        "schema": "orion.runtime-diagnostic.v1", "samples": 1, "budget_ms": 10,
        "failed_attempts": 0, "budget_exceeded": 0, "attempts": [{"total_ms": 50}],
    }))
    result = assessor.assess(directory, root)
    assert any("counter contradicts" in failure for failure in result["observed_failures"])
    assert next(gate for gate in result["gate_results"] if gate["id"] == "G05")["status"] == "fail"


def test_non_finite_evidence_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"latency": NaN}')
    with pytest.raises(ValueError, match="non-finite"):
        assessor.load_json(path)


@pytest.mark.parametrize("heartbeat, expected", [
    ({"p95": 20, "p99": 30, "maximum": 1200}, "fail"),
    ({"p95": None, "p99": None, "maximum": None}, "not_demonstrated"),
])
def test_ui_tail_and_empty_samples_cannot_be_pass(frozen, heartbeat, expected):
    root, directory = frozen
    path = directory / "performance-baseline"
    path.mkdir()
    (path / "report.json").write_text(json.dumps({
        "schema": "orion.console-performance.v1", "measurement_completed": True,
        "source_unchanged": True, "errors": [], "phases": {"globe": {"heartbeat_ms": heartbeat}},
    }))
    result = assessor.assess(directory, root)
    assert next(gate for gate in result["gate_results"] if gate["id"] == "G05")["status"] == expected
    assert not result["trl5_demonstrated"]


@pytest.mark.parametrize("path", ["../outside.json", str(ROOT / "README.md")])
def test_path_escape_rejected(tmp_path, path):
    with pytest.raises(ValueError):
        assessor.contained(tmp_path, path)


def test_campaign_and_report_cannot_be_overwritten(frozen):
    root, directory = frozen
    with pytest.raises(FileExistsError):
        assessor.prepare(directory, root)
    path = directory / "result.json"
    assessor.write_new(path, {"first": True})
    with pytest.raises(FileExistsError):
        assessor.write_new(path, {"first": False})
    assert json.loads(path.read_text()) == {"first": True}
