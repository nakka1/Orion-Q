"""A deterministic, domain-neutral policy with fail-closed constraints."""

from __future__ import annotations

from collections.abc import Sequence

from quantum_autopilot.core.contracts import Budget, CandidatePlan, Decision, ReasonCode, SLO
from quantum_autopilot.core.evaluators import evaluate_budget, evaluate_slo


class GenericThresholdPolicy:
    """Choose the least-cost candidate that satisfies SLO and budget.

    The policy never recommends a known-invalid "least bad" candidate.  If no
    candidate is demonstrably viable it returns ``HALT``.
    """

    def decide(
        self,
        plans: Sequence[CandidatePlan],
        slo: SLO | None = None,
        budget: Budget | None = None,
    ) -> Decision:
        if not plans:
            return Decision(
                action="HALT",
                rationale={"candidates": {}},
                reason_codes=[ReasonCode.NO_CANDIDATE_PLANS_AVAILABLE],
                slo=slo,
                budget=budget,
            )

        rationale: dict[str, dict] = {}
        viable: list[CandidatePlan] = []
        for plan in plans:
            slo_result = evaluate_slo(slo, plan.prediction, plan.expected_metrics)
            budget_result = evaluate_budget(budget, plan.expected_usage)
            eligible = slo_result.satisfied and not budget_result.exceeded
            rationale[plan.action] = {
                "eligible": eligible,
                "estimated_cost": plan.estimated_cost,
                "predicted_mean": plan.prediction.mean if plan.prediction else None,
                "prediction_uncertainty": plan.prediction.uncertainty if plan.prediction else None,
                "slo_violations": [item.to_dict() for item in slo_result.violations],
                "budget_violations": [item.to_dict() for item in budget_result.violations],
            }
            if eligible:
                viable.append(plan)

        if not viable:
            return Decision(
                action="HALT",
                rationale={"candidates": rationale},
                reason_codes=[ReasonCode.NO_VIABLE_CANDIDATE],
                slo=slo,
                budget=budget,
            )

        def ranking_key(plan: CandidatePlan) -> tuple[float, float, str]:
            cost = plan.estimated_cost if plan.estimated_cost is not None else float("inf")
            mean = plan.prediction.mean if plan.prediction is not None else float("-inf")
            return cost, -mean, plan.action

        chosen = min(viable, key=ranking_key)
        return Decision(
            action=chosen.action,
            rationale={"chosen_plan": chosen.action, "candidates": rationale},
            reason_codes=[
                ReasonCode.SLO_SATISFIED,
                ReasonCode.BUDGET_OK,
                ReasonCode.BEST_AVAILABLE_PLAN_SELECTED,
            ],
            slo=slo,
            budget=budget,
        )
