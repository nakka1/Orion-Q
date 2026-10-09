"""Auditable decisions that cannot select rejected candidates."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from quantum_autopilot.core.contracts import (
    SCHEMA_VERSION,
    Budget,
    CandidatePlan,
    Decision,
    JSONSerializableMixin,
    Provenance,
    ReasonCode,
    SLO,
    SystemSnapshot,
)
from quantum_autopilot.core.evaluators import ConstraintViolation, evaluate_budget, evaluate_slo


@dataclass
class CandidateEvaluation(JSONSerializableMixin):
    action: str
    eligible: bool
    selected: bool = False
    prediction_mean: float | None = None
    prediction_uncertainty: float | None = None
    estimated_cost: float | None = None
    slo_violations: list[dict[str, Any]] = field(default_factory=list)
    budget_violations: list[dict[str, Any]] = field(default_factory=list)
    domain_violations: list[dict[str, Any]] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CandidateEvaluation":
        return cls(**dict(payload))


@dataclass
class DecisionRecord(JSONSerializableMixin):
    decision: Decision
    evaluated_candidates: list[CandidateEvaluation]
    active_slo: SLO | None
    active_budget: Budget | None
    model_version: str
    snapshot_domain: str
    snapshot_timestamp: str
    snapshot: SystemSnapshot
    provenance: Provenance
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DecisionRecord":
        values = dict(payload)
        if isinstance(values.get("decision"), dict):
            values["decision"] = Decision.from_dict(values["decision"])
        values["evaluated_candidates"] = [
            CandidateEvaluation.from_dict(item) if isinstance(item, dict) else item
            for item in values.get("evaluated_candidates", [])
        ]
        if isinstance(values.get("active_slo"), dict):
            values["active_slo"] = SLO.from_dict(values["active_slo"])
        if isinstance(values.get("active_budget"), dict):
            values["active_budget"] = Budget.from_dict(values["active_budget"])
        if isinstance(values.get("provenance"), dict):
            values["provenance"] = Provenance.from_dict(values["provenance"])
        if isinstance(values.get("snapshot"), dict):
            values["snapshot"] = SystemSnapshot.from_dict(values["snapshot"])
        return cls(**values)


def make_audited_decision(
    policy: Any,
    plans: Sequence[CandidatePlan],
    slo: SLO | None,
    budget: Budget | None,
    snapshot: SystemSnapshot,
    model_version: str,
    provenance: Provenance,
    candidate_gate: Callable[[CandidatePlan], Sequence[ConstraintViolation]] | None = None,
) -> DecisionRecord:
    """Evaluate candidates, pass only eligible plans to the policy, and audit."""

    evaluations: list[CandidateEvaluation] = []
    viable: list[CandidatePlan] = []
    for plan in plans:
        slo_result = evaluate_slo(slo, plan.prediction, plan.expected_metrics)
        budget_result = evaluate_budget(budget, plan.expected_usage)
        domain_violations = tuple(candidate_gate(plan)) if candidate_gate is not None else ()
        eligible = slo_result.satisfied and not budget_result.exceeded and not domain_violations
        evaluation = CandidateEvaluation(
            action=plan.action,
            eligible=eligible,
            prediction_mean=plan.prediction.mean if plan.prediction else None,
            prediction_uncertainty=plan.prediction.uncertainty if plan.prediction else None,
            estimated_cost=plan.estimated_cost,
            slo_violations=[item.to_dict() for item in slo_result.violations],
            budget_violations=[item.to_dict() for item in budget_result.violations],
            domain_violations=[item.to_dict() for item in domain_violations],
        )
        if not eligible:
            evaluation.rejection_reasons = [
                item["reason_code"]
                for item in (
                    evaluation.slo_violations
                    + evaluation.budget_violations
                    + evaluation.domain_violations
                )
            ]
        else:
            viable.append(plan)
        evaluations.append(evaluation)

    decision = policy.decide(viable, slo=slo, budget=budget)
    viable_actions = {plan.action for plan in viable}
    identifier = (
        "HALT"
        if decision.action == "HALT"
        else decision.rationale.get("chosen_plan", decision.action)
    )
    if decision.action != "HALT" and identifier not in viable_actions:
        decision = Decision(
            action="HALT",
            rationale={
                "reason": "policy_returned_unknown_or_rejected_plan",
                "returned_action": identifier,
                "eligible_actions": sorted(viable_actions),
            },
            reason_codes=[ReasonCode.POLICY_RETURNED_UNKNOWN_PLAN],
            slo=slo,
            budget=budget,
        )
        identifier = "HALT"

    for evaluation in evaluations:
        evaluation.selected = identifier != "HALT" and evaluation.action == identifier
        if evaluation.eligible and not evaluation.selected:
            evaluation.rejection_reasons = ["eligible_but_not_selected"]

    return DecisionRecord(
        decision=decision,
        evaluated_candidates=evaluations,
        active_slo=slo,
        active_budget=budget,
        model_version=model_version,
        snapshot_domain=snapshot.domain,
        snapshot_timestamp=snapshot.timestamp,
        snapshot=snapshot,
        provenance=provenance,
    )
