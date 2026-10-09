"""Product-gate tests for the read-only WDM/QKD claim adapter."""

import json
from pathlib import Path

import pytest

from quantum_autopilot.core import DecisionRecord, EvidenceKind, Provenance, Workload
from quantum_autopilot.qkd import (
    ClaimRegistryAdapter,
    QKDOperatingPoint,
    build_assurance_workload,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CLAIM_IDS = {
    "H-ORIGINAL",
    "C-MEASURED",
    "L-SESSION",
    "L-DOSE",
    "L-PARETO",
    "L-TRANSPORT",
    "B-FIDELITY",
}
CAMPAIGN_ID = "roadm_3dbm_injected_signal_no_added_fibre_full_sweep"
CAMPAIGN_SHA256 = "0c145f7d70356a9449301f96fa28328b55b34ff6b27750d2d58f1bf19bbe19b8"
GRID_SOURCE_SHA256 = "afe5d110e8e32694591a6a454b6e5a84410e88b24992a193d31822133a0afe4b"


def measured_point(**overrides):
    values = {
        "dataset_id": "irelandqci-toshiba-qkd-coexistence-21132088",
        "campaign_id": CAMPAIGN_ID,
        "device_id": "not_published",
        "channel_id": "not_published",
        "external_signal_present": True,
        "external_signal_power_dbm": 3.0,
        "fiber_length_km": 0.0,
        "voa_attenuation_db": 10.0,
        "qber_reported": 0.033308,
        "qber_unit_status": "publisher_scale_ambiguous",
        "secure_key_rate_bps": 302860.36,
        "observation_level": "published_cell_mean",
        "observation_time_utc": None,
        "time_basis": "not_published",
        "provenance": Provenance(
            evidence_kind=EvidenceKind.MEASURED,
            source="doi:10.5281/zenodo.21132088",
            timestamp="2026-08-27T00:00:00+00:00",
            dataset_hash=GRID_SOURCE_SHA256,
            artifact_hashes={"campaign_source": CAMPAIGN_SHA256},
        ),
    }
    values.update(overrides)
    return QKDOperatingPoint(**values)


def assess(claim_id, requested_use="display", operating_point=None):
    adapter = ClaimRegistryAdapter(repository_root=REPOSITORY_ROOT)
    workload = build_assurance_workload(
        claim_id,
        requested_use=requested_use,
        operating_point=operating_point,
    )
    return adapter.assess(workload)


@pytest.mark.unit
def test_measured_claim_can_be_displayed_with_registered_badge():
    record = assess("C-MEASURED")
    assert record.decision.action == "DISPLAY_EVIDENCE"
    assert record.snapshot.observed["registry_valid"]
    assert record.snapshot.observed["claim"]["product_behavior"]["badge"] == (
        "SUPORTADO_EXPLORATORIO"
    )
    assert record.snapshot.observed["claim_evidence_kind"] == "measured"


@pytest.mark.unit
@pytest.mark.parametrize("claim_id", sorted(CLAIM_IDS))
def test_every_registered_claim_remains_visible(claim_id):
    record = assess(claim_id)
    assert record.decision.action == "DISPLAY_EVIDENCE"
    assert record.snapshot.observed["claim_exists"]


@pytest.mark.unit
def test_measured_claim_allows_support_only_inside_validated_grid():
    inside = assess("C-MEASURED", "decision_support", measured_point())
    outside = assess(
        "C-MEASURED",
        "decision_support",
        measured_point(external_signal_power_dbm=5.0),
    )
    unknown = assess("C-MEASURED", "decision_support")

    assert inside.decision.action == "PROVIDE_DECISION_SUPPORT"
    assert inside.snapshot.observed["condition_scope"] == "inside_validated_grid"
    assert inside.snapshot.observed["grid_classification"]["matched_cell"]
    assert outside.decision.action == "HALT"
    assert outside.snapshot.observed["condition_scope"] == "outside_validated_grid"
    assert unknown.decision.action == "HALT"
    assert "condition_outside_validated_grid" in outside.evaluated_candidates[0].rejection_reasons
    assert "condition_scope_not_verified" in unknown.evaluated_candidates[0].rejection_reasons


@pytest.mark.unit
def test_automation_is_blocked_by_formal_product_behavior():
    record = assess("C-MEASURED", "automation", measured_point())
    assert record.decision.action == "HALT"
    assert "automation_not_authorized_by_claim_registry" in (
        record.evaluated_candidates[0].rejection_reasons
    )


@pytest.mark.unit
@pytest.mark.parametrize("claim_id", sorted(CLAIM_IDS))
def test_automation_is_blocked_for_every_registered_claim(claim_id):
    record = assess(claim_id, "automation", measured_point())
    assert record.decision.action == "HALT"


@pytest.mark.unit
def test_open_temporal_hypothesis_cannot_drive_decision_support():
    record = assess("H-ORIGINAL", "decision_support", measured_point())
    assert record.decision.action == "HALT"
    assert "measured_evidence_absent" in record.evaluated_candidates[0].rejection_reasons
    assert "claim_status_not_supported_for_decision" in (
        record.evaluated_candidates[0].rejection_reasons
    )


@pytest.mark.unit
def test_simulated_fidelity_can_be_displayed_but_not_used_for_support():
    displayed = assess("B-FIDELITY")
    support = assess("B-FIDELITY", "decision_support", measured_point())
    assert displayed.decision.action == "DISPLAY_EVIDENCE"
    assert displayed.snapshot.observed["claim_evidence_kind"] == "simulated"
    assert support.decision.action == "HALT"
    assert "simulation_only_evidence" in support.evaluated_candidates[0].rejection_reasons


@pytest.mark.unit
def test_negative_claims_remain_visible_but_cannot_recommend():
    displayed = assess("L-TRANSPORT")
    support = assess("L-TRANSPORT", "decision_support", measured_point())
    assert displayed.decision.action == "DISPLAY_EVIDENCE"
    assert displayed.snapshot.observed["claim"]["status"] == "refuted_for_evaluated_models"
    assert support.decision.action == "HALT"
    assert "claim_status_not_supported_for_decision" in (
        support.evaluated_candidates[0].rejection_reasons
    )


@pytest.mark.unit
def test_unknown_claim_is_blocked_without_crashing_dashboard_consumer():
    record = assess("UNKNOWN")
    assert record.decision.action == "HALT"
    assert "claim_not_registered" in record.evaluated_candidates[0].rejection_reasons


@pytest.mark.unit
def test_invalid_registry_is_represented_as_blocked_snapshot(tmp_path):
    registry_dir = tmp_path / "docs" / "thesis"
    registry_dir.mkdir(parents=True)
    registry_path = registry_dir / "tcc_claim_registry.json"
    registry_path.write_text('{"schema_version": "broken"}', encoding="utf-8")
    adapter = ClaimRegistryAdapter(repository_root=tmp_path)
    workload = build_assurance_workload("C-MEASURED")

    record = adapter.assess(workload)

    assert record.decision.action == "HALT"
    assert not record.snapshot.observed["registry_valid"]
    assert record.snapshot.observed["validation_error"]["type"] == "ValueError"
    assert "claim_registry_integrity_failed" in record.evaluated_candidates[0].rejection_reasons


@pytest.mark.unit
def test_assessment_is_self_contained_and_round_trips():
    record = assess("C-MEASURED")
    restored = DecisionRecord.from_dict(json.loads(record.to_json()))
    assert restored.to_dict() == record.to_dict()
    assert restored.provenance.evidence_kind is EvidenceKind.SYNTHESIZED
    assert restored.provenance.metadata["upstream_evidence_kind"] == "measured"
    assert restored.provenance.config_hash != restored.provenance.metadata["registry_sha256"]
    assert restored.provenance.metadata["condition_scope_source"] == (
        "computed_exact_cell_classifier"
    )
    assert restored.provenance.dataset_hash == ""
    assert restored.provenance.artifact_hashes


@pytest.mark.unit
def test_workload_rejects_unknown_modes_and_invalid_points():
    with pytest.raises(ValueError, match="requested_use"):
        build_assurance_workload("C-MEASURED", requested_use="execute")
    with pytest.raises(ValueError, match="operating_point"):
        build_assurance_workload("C-MEASURED", operating_point="probably_inside")


@pytest.mark.unit
def test_manual_condition_scope_cannot_bypass_computed_classifier():
    workload = Workload(
        kind="qkd_claim_assurance",
        payload={
            "claim_id": "C-MEASURED",
            "requested_use": "decision_support",
            "condition_scope": "inside_validated_grid",
        },
    )
    record = ClaimRegistryAdapter(repository_root=REPOSITORY_ROOT).assess(workload)
    assert record.decision.action == "HALT"
    assert record.snapshot.observed["condition_scope"] == "unknown"
    assert "operating_point_missing" in record.evaluated_candidates[0].rejection_reasons


@pytest.mark.unit
def test_point_provenance_is_preserved_in_self_contained_record():
    record = assess("C-MEASURED", "decision_support", measured_point())
    assert record.provenance.dataset_hash == GRID_SOURCE_SHA256
    assert record.provenance.metadata["operating_point_provenance"]["evidence_kind"] == (
        "measured"
    )
    assert record.provenance.metadata["grid_source_artifact_verified"] is True


@pytest.mark.unit
def test_adapter_refuses_registry_outside_repository(tmp_path):
    with pytest.raises(ValueError, match="inside repository_root"):
        ClaimRegistryAdapter(repository_root=tmp_path, registry_path=REPOSITORY_ROOT / "README.md")


@pytest.mark.unit
def test_adapter_exposes_no_execution_method():
    adapter = ClaimRegistryAdapter(repository_root=REPOSITORY_ROOT)
    assert not hasattr(adapter, "execute")
