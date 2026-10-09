"""
tests/test_validation_taxonomy.py

Unit tests for validation_taxonomy.py (master prompt v4, Fases 23 + 25).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quantum_twin.evaluation.validation_taxonomy import (
    BANNED_UNQUALIFIED_TERMS, PROJECT_PROVENANCE_LEDGER, PROJECT_VALIDATION_LEDGER,
    DataProvenance, ExperimentProvenanceRecord, ExperimentValidationRecord,
    LoopIntegration, RealismLevel, TargetProvenance, ValidationLevel,
    audit_text_for_banned_terms,
)


def test_realism_level_has_exactly_five_levels():
    assert len(RealismLevel) == 5
    values = {level.value for level in RealismLevel}
    assert values == {"L0-ideal", "L1-stochastic", "L2-physics-based",
                       "L3-hardware-in-the-loop", "L4-experimental"}


def test_validation_level_has_exactly_seven_levels():
    assert len(ValidationLevel) == 7


def test_audit_detects_a_banned_term_present_in_text():
    text = "This system provides real-time decision making."
    findings = audit_text_for_banned_terms(text)
    assert len(findings) >= 1
    assert any(term == "real-time" for term, _ in findings)


def test_audit_finds_nothing_in_clean_text():
    text = "This controller purifies pairs based on a threshold rule, measured on synthetic data."
    findings = audit_text_for_banned_terms(text)
    assert findings == []


def test_audit_is_case_insensitive():
    text = "This is a REAL-TIME system."
    findings = audit_text_for_banned_terms(text)
    assert any(term == "real-time" for term, _ in findings)


def test_project_validation_ledger_entries_are_all_l1_stochastic():
    """Regression guard for this project's own honest self-assessment:
    every entry in the project's OWN ledger must be L1-stochastic --
    if a future addendum claims a higher realism level without updating
    this test, that claim needs explicit scrutiny."""
    for record in PROJECT_VALIDATION_LEDGER:
        assert record.realism_level == RealismLevel.L1_STOCHASTIC, (
            f"{record.experiment_name} claims a realism level above L1 -- verify this is backed "
            f"by real evidence before updating this test."
        )


def test_project_validation_ledger_never_claims_hardware_validation():
    """Regression guard: no entry in this project's own ledger may claim
    HARDWARE_IN_THE_LOOP or PHYSICAL_EXPERIMENT validation, since this
    project has not reached either."""
    forbidden = {ValidationLevel.HARDWARE_IN_THE_LOOP, ValidationLevel.PHYSICAL_EXPERIMENT,
                 ValidationLevel.VALIDATED_AGAINST_REAL_TELEMETRY}
    for record in PROJECT_VALIDATION_LEDGER:
        assert record.validation_level not in forbidden, (
            f"{record.experiment_name} claims a validation level this project has not reached."
        )


def test_experiment_validation_record_summary_includes_key_fields():
    record = ExperimentValidationRecord(
        "Test Experiment", RealismLevel.L1_STOCHASTIC, ValidationLevel.VALIDATED_AGAINST_QISKIT_AER,
        notes="a test note")
    summary = record.summary()
    assert "Test Experiment" in summary
    assert "L1-stochastic" in summary
    assert "validated_against_qiskit_aer" in summary
    assert "a test note" in summary


def test_banned_terms_dict_has_a_note_for_every_term():
    for term, note in BANNED_UNQUALIFIED_TERMS.items():
        assert isinstance(note, str) and len(note) > 10, f"Term '{term}' lacks a substantive justification note."


def test_recorded_optical_evidence_does_not_claim_measured_quantum_target():
    santanna = next(
        record for record in PROJECT_PROVENANCE_LEDGER
        if "Sant'Anna" in record.experiment_name
    )
    assert santanna.data_provenance == DataProvenance.RECORDED_HARDWARE
    assert santanna.target_provenance == TargetProvenance.RECORDED_OPTICAL
    assert santanna.loop_integration == LoopIntegration.OFFLINE


def test_offline_replay_is_not_hardware_in_the_loop():
    replay = next(
        record for record in PROJECT_PROVENANCE_LEDGER
        if "recorded-WDM-driven" in record.experiment_name
    )
    assert replay.loop_integration == LoopIntegration.OFFLINE_REPLAY
    assert replay.target_provenance == TargetProvenance.SIMULATED_CIRCUIT


def test_measured_qkd_evidence_is_distinct_from_bell_fidelity_replay():
    qkd = next(
        record for record in PROJECT_PROVENANCE_LEDGER
        if "controlled QKD coexistence" in record.experiment_name
    )
    assert qkd.data_provenance == DataProvenance.RECORDED_HARDWARE
    assert qkd.target_provenance == TargetProvenance.MEASURED_QUANTUM
    assert qkd.loop_integration == LoopIntegration.OFFLINE
    assert "no Bell fidelity" in qkd.notes


def test_bristol_hcf_is_measured_quantum_but_still_offline():
    hcf = next(
        record for record in PROJECT_PROVENANCE_LEDGER
        if "HCF ordered QKD" in record.experiment_name
    )
    assert hcf.data_provenance == DataProvenance.RECORDED_HARDWARE
    assert hcf.target_provenance == TargetProvenance.MEASURED_QUANTUM
    assert hcf.loop_integration == LoopIntegration.OFFLINE
    assert "no Bell fidelity" in hcf.notes
