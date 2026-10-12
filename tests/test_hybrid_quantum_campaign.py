"""Acceptance evidence must reject drift, suppressed failure and false admission."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import run_hybrid_quantum_campaign as campaign

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(schema="other"),
    lambda p: p.update(families=["sns"]),
    lambda p: p.update(seeds=[101, 101, 303]),
    lambda p: p.update(seeds=[True, 202, 303]),
    lambda p: p.update(worker_timeout_s=0),
    lambda p: p.update(source_paths=["../../outside.py"]),
    lambda p: p.update(previous_freezes=["same", "same"]),
    lambda p: p.update(forbidden_claims=[]),
])
def test_invalid_campaign_rejected_before_execution(mutation):
    plan = copy.deepcopy(campaign.load(campaign.PLAN))
    mutation(plan)
    with pytest.raises(ValueError):
        campaign.validate_plan(plan)


def test_json_duplicate_and_nonfinite_rejected(tmp_path):
    path = tmp_path / "invalid.json"
    for text in ('{"checks":{},"checks":{"ok":true}}', '{"error":NaN}'):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            campaign.load(path)


@pytest.mark.parametrize("report", [
    {"checks": {}},
    {"checks": {"valid": True, "physics": False}},
    {"checks": {"valid": 1}},
    {"checks": {"valid": True}, "error": "missing reference"},
    {"checks": {"valid": True}, "physical_actuation": True},
    {"checks": {"valid": True}, "security_certified": True},
    {"checks": {"valid": True}, "security_certified": "false"},
    {"checks": {"valid": True}, "certified_secret_bits": 5},
    {"checks": {"valid": True}, "passed": False},
    {"checks": {"valid": True}, "cases": [{"passed": False}]},
    {"checks": {"valid": True}, "cases": [{"checks": {"hash_matches": False}}]},
    {"checks": {"valid": True}, "status": "running"},
    {"checks": {"valid": True}, "cases": [{"passed": True, "result": {"security_certified": True}}]},
    {"checks": {"valid": True}, "cases": [{"result": {"certified_secret_bits": 1}}]},
    {"checks": [{"passed": True}, {"passed": False}]},
])
def test_failure_and_unearned_claim_never_admitted(report):
    plan = campaign.load(campaign.PLAN)
    assert not campaign.evaluate_family(report, plan["forbidden_claims"])["passed"]


def test_frozen_output_cannot_be_overwritten(tmp_path):
    path = tmp_path / "report.json"
    campaign.write(path, {"status": "failed"})
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        campaign.write(path, {"status": "partial_verified"})
    assert path.read_bytes() == original


def test_manifest_detects_modified_and_missing_source(tmp_path, monkeypatch):
    monkeypatch.setattr(campaign, "ROOT", tmp_path)
    path = tmp_path / "source.py"
    path.write_text("initial", encoding="utf-8")
    frozen = {"source.py": campaign.sha(path)}
    assert campaign.changes(frozen) == []
    path.write_text("modified", encoding="utf-8")
    assert campaign.changes(frozen) == ["source.py"]
    path.unlink()
    assert campaign.changes(frozen) == ["source.py"]


def test_output_outside_repository_rejected():
    with pytest.raises(ValueError):
        campaign.within(campaign.ROOT.parent / "outside-campaign")


def test_new_evidence_write_disallows_nan(tmp_path):
    with pytest.raises(ValueError):
        campaign.write(tmp_path / "report.json", {"value": float("nan")})


def test_valid_prospective_plan():
    plan = campaign.load(campaign.PLAN)
    campaign.validate_plan(plan)
    assert json.loads(json.dumps(plan))["acceptance"]["no_failure_or_gap_suppression"] is True


def test_actual_previous_manifest_schema_compatibility():
    for path in campaign.load(campaign.PLAN)["previous_freezes"]:
        manifest = campaign.previous_sources(campaign.load(campaign.ROOT / path))
        assert "src/quantum_autopilot/quantum_protocols/protocols.py" in manifest
        assert len(manifest) >= 583


@pytest.mark.parametrize("frozen", [{"schema": "unknown", "sources": {"source": "digest"}},
                                    {"schema": "orionq.sequence-network-freeze.v1", "sources": {}}])
def test_unknown_or_empty_previous_freeze_rejected(frozen):
    with pytest.raises(ValueError):
        campaign.previous_sources(frozen)
