"""Adversarial tests for the selectively integrated Autopilot core."""

import json

import pytest

from quantum_autopilot.core import (
    Budget,
    CandidatePlan,
    ConservativePolicyWrapper,
    Decision,
    DecisionRecord,
    EvidenceKind,
    GenericThresholdPolicy,
    Prediction,
    Provenance,
    ReasonCode,
    SLO,
    SystemSnapshot,
    ConstraintViolation,
    evaluate_budget,
    evaluate_slo,
    is_out_of_distribution,
    make_audited_decision,
)


def plan(
    action: str,
    *,
    mean: float = 0.9,
    uncertainty: float = 0.02,
    cost: float = 1.0,
    latency_s: float | None = 0.1,
    success_probability: float | None = 0.95,
    shots: int | None = 100,
) -> CandidatePlan:
    metrics = {}
    usage = {}
    if latency_s is not None:
        metrics["latency_s"] = latency_s
    if success_probability is not None:
        metrics["success_probability"] = success_probability
    if shots is not None:
        usage["shots"] = shots
    return CandidatePlan(
        action=action,
        estimated_cost=cost,
        prediction=Prediction(mean=mean, uncertainty=uncertainty, method="test"),
        expected_metrics=metrics,
        expected_usage=usage,
    )


@pytest.mark.unit
def test_slo_missing_latency_and_success_fail_closed():
    result = evaluate_slo(
        SLO(min_fidelity=0.8, max_latency_s=1.0, min_success_probability=0.9),
        Prediction(mean=0.95, uncertainty=0.01),
        metrics={},
    )

    assert not result.satisfied
    assert {item.constraint for item in result.violations} == {
        "max_latency_s",
        "min_success_probability",
    }
    assert set(result.reason_codes) == {ReasonCode.REQUIRED_METRIC_MISSING}


@pytest.mark.unit
def test_slo_rejects_invalid_or_out_of_range_metrics():
    invalid = evaluate_slo(
        SLO(max_latency_s=1.0, min_success_probability=0.9),
        Prediction(mean=0.95, uncertainty=0.01),
        {"latency_s": -1.0, "success_probability": 1.5},
    )
    assert not invalid.satisfied
    assert len(invalid.violations) == 2


@pytest.mark.unit
def test_budget_missing_usage_is_not_interpreted_as_zero():
    result = evaluate_budget(Budget(max_shots=100), {})
    assert result.exceeded
    assert result.reason_codes == (ReasonCode.BUDGET_USAGE_MISSING_OR_INVALID,)


@pytest.mark.unit
def test_budget_rejects_fractional_shot_count():
    result = evaluate_budget(Budget(max_shots=100), {"shots": 1.5})
    assert result.exceeded
    assert result.reason_codes == (ReasonCode.BUDGET_USAGE_MISSING_OR_INVALID,)


@pytest.mark.unit
def test_budget_limit_is_enforced_by_generic_policy():
    decision = GenericThresholdPolicy().decide(
        [plan("OVER", shots=101), plan("WITHIN", cost=2.0, shots=100)],
        slo=SLO(min_fidelity=0.8),
        budget=Budget(max_shots=100),
    )
    assert decision.action == "WITHIN"
    assert not decision.rationale["candidates"]["OVER"]["eligible"]


@pytest.mark.unit
def test_policy_halts_when_every_candidate_violates_constraints():
    decision = GenericThresholdPolicy().decide(
        [plan("LOW", mean=0.3), plan("SLOW", latency_s=5.0)],
        slo=SLO(min_fidelity=0.8, max_latency_s=1.0),
    )
    assert decision.action == "HALT"
    assert decision.reason_codes == [ReasonCode.NO_VIABLE_CANDIDATE.value]


@pytest.mark.unit
def test_policy_chooses_lowest_cost_among_demonstrably_viable_plans():
    decision = GenericThresholdPolicy().decide(
        [plan("EXPENSIVE", cost=3.0), plan("CHEAP", cost=1.0)],
        slo=SLO(min_fidelity=0.8, max_latency_s=1.0),
        budget=Budget(max_shots=100),
    )
    assert decision.action == "CHEAP"


@pytest.mark.unit
def test_conservative_wrapper_checks_the_plan_actually_selected():
    low_uncertainty = plan("LOW_UNCERTAINTY", uncertainty=0.01, cost=10.0)
    high_uncertainty = plan("HIGH_UNCERTAINTY", uncertainty=0.8, cost=1.0)
    wrapped = ConservativePolicyWrapper(GenericThresholdPolicy(), max_uncertainty=0.1)

    decision = wrapped.decide([low_uncertainty, high_uncertainty], slo=SLO(min_fidelity=0.8))

    assert decision.action == "HALT"
    assert decision.rationale["rejected_plan"] == "HIGH_UNCERTAINTY"
    assert decision.reason_codes == [ReasonCode.CONSERVATIVE_FALLBACK_HIGH_UNCERTAINTY.value]


@pytest.mark.unit
def test_ood_gate_fails_closed_for_missing_required_feature():
    assert is_out_of_distribution(
        {"osnr_db": 20.0},
        {"osnr_db": (15.0, 25.0), "power_dbm": (-10.0, 5.0)},
    )


@pytest.mark.unit
def test_ood_wrapper_rejects_invalid_training_ranges():
    with pytest.raises(ValueError, match="invalid training range"):
        ConservativePolicyWrapper(
            GenericThresholdPolicy(),
            max_uncertainty=0.1,
            training_feature_ranges={"osnr_db": (25.0, 15.0)},
        )


@pytest.mark.unit
def test_audited_decision_passes_only_eligible_candidates_to_policy():
    class RecordingPolicy:
        received = None

        def decide(self, plans, slo=None, budget=None):
            self.received = [item.action for item in plans]
            return Decision(action=plans[0].action if plans else "HALT")

    policy = RecordingPolicy()
    record = make_audited_decision(
        policy,
        [plan("REJECTED", mean=0.2), plan("ELIGIBLE", mean=0.95)],
        SLO(min_fidelity=0.8),
        None,
        SystemSnapshot(domain="photonic"),
        "test-model",
        Provenance(evidence_kind=EvidenceKind.SIMULATED),
    )

    assert policy.received == ["ELIGIBLE"]
    assert record.decision.action == "ELIGIBLE"
    rejected, eligible = record.evaluated_candidates
    assert not rejected.eligible and not rejected.selected
    assert eligible.eligible and eligible.selected


@pytest.mark.unit
def test_audited_decision_applies_domain_gate_before_policy():
    class RecordingPolicy:
        received = None

        def decide(self, plans, slo=None, budget=None):
            self.received = [item.action for item in plans]
            return Decision(action=plans[0].action if plans else "HALT")

    policy = RecordingPolicy()

    def domain_gate(candidate):
        if candidate.action == "BLOCKED":
            return [
                ConstraintViolation(
                    constraint="claim_status",
                    reason_code="claim_not_supported",
                    required="supported_exploratory",
                    actual="open_not_tested",
                )
            ]
        return []

    record = make_audited_decision(
        policy,
        [plan("BLOCKED"), plan("ALLOWED")],
        None,
        None,
        SystemSnapshot(domain="qkd_assurance"),
        "claim-guard-v1",
        Provenance(evidence_kind=EvidenceKind.SYNTHESIZED),
        candidate_gate=domain_gate,
    )

    assert policy.received == ["ALLOWED"]
    assert record.decision.action == "ALLOWED"
    assert record.evaluated_candidates[0].domain_violations[0]["reason_code"] == (
        "claim_not_supported"
    )


@pytest.mark.unit
def test_audit_replaces_unknown_policy_action_with_halt():
    class InvalidPolicy:
        def decide(self, plans, slo=None, budget=None):
            return Decision(action="NOT_A_CANDIDATE")

    record = make_audited_decision(
        InvalidPolicy(),
        [plan("VALID")],
        SLO(min_fidelity=0.8),
        None,
        SystemSnapshot(domain="photonic"),
        "test-model",
        Provenance(evidence_kind=EvidenceKind.SIMULATED),
    )

    assert record.decision.action == "HALT"
    assert record.decision.reason_codes == [ReasonCode.POLICY_RETURNED_UNKNOWN_PLAN.value]


@pytest.mark.unit
def test_halt_decision_never_marks_a_candidate_as_selected():
    class HaltPolicy:
        def decide(self, plans, slo=None, budget=None):
            return Decision(action="HALT", rationale={"chosen_plan": "VALID"})

    record = make_audited_decision(
        HaltPolicy(),
        [plan("VALID")],
        SLO(min_fidelity=0.8),
        None,
        SystemSnapshot(domain="photonic"),
        "test-model",
        Provenance(evidence_kind=EvidenceKind.SIMULATED),
    )
    assert not any(item.selected for item in record.evaluated_candidates)


@pytest.mark.unit
@pytest.mark.parametrize(
    "factory",
    [
        lambda: Budget(max_attempts=-1),
        lambda: Budget(max_shots=-1),
        lambda: Budget(max_time_s=-1.0),
        lambda: SLO(min_fidelity=1.1),
        lambda: SLO(min_success_probability=-0.1),
        lambda: Prediction(mean=float("nan"), uncertainty=0.1),
        lambda: Prediction(mean=0.9, uncertainty=float("inf")),
        lambda: CandidatePlan(action="A", estimated_cost=-1.0),
    ],
)
def test_contracts_reject_invalid_numeric_values(factory):
    with pytest.raises(ValueError):
        factory()


@pytest.mark.unit
def test_provenance_requires_evidence_kind_and_timezone():
    with pytest.raises(ValueError, match="evidence_kind"):
        Provenance(evidence_kind=None)
    with pytest.raises(ValueError, match="timezone"):
        Provenance(evidence_kind=EvidenceKind.MEASURED, timestamp="2026-09-03T12:00:00")


@pytest.mark.unit
def test_contracts_serialize_without_enum_or_dataclass_leakage():
    provenance = Provenance(
        evidence_kind=EvidenceKind.MEASURED,
        artifact_hashes={"aggregate.csv": "abc123"},
    )
    encoded = provenance.to_json()
    decoded = json.loads(encoded)

    assert decoded["evidence_kind"] == "measured"
    assert Provenance.from_dict(decoded).evidence_kind is EvidenceKind.MEASURED


@pytest.mark.unit
def test_candidate_plan_round_trip_restores_prediction_type():
    original = plan("ROUND_TRIP")
    restored = CandidatePlan.from_dict(original.to_dict())
    assert isinstance(restored.prediction, Prediction)
    assert restored.to_dict() == original.to_dict()


@pytest.mark.unit
def test_decision_record_round_trip_restores_nested_contracts():
    record = make_audited_decision(
        GenericThresholdPolicy(),
        [plan("VALID")],
        SLO(min_fidelity=0.8),
        Budget(max_shots=100),
        SystemSnapshot(domain="photonic"),
        "test-model",
        Provenance(evidence_kind=EvidenceKind.MEASURED),
    )
    restored = DecisionRecord.from_dict(json.loads(record.to_json()))
    assert isinstance(restored.decision, Decision)
    assert isinstance(restored.provenance, Provenance)
    assert isinstance(restored.snapshot, SystemSnapshot)
    assert restored.provenance.evidence_kind is EvidenceKind.MEASURED
    assert restored.to_dict() == record.to_dict()
