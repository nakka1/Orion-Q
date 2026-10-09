"""Conservative policy wrapper for uncertainty and simple range OOD gates."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from quantum_autopilot.core.contracts import Budget, CandidatePlan, Decision, ReasonCode, SLO


def is_out_of_distribution(
    features: Mapping[str, Any],
    training_feature_ranges: Mapping[str, tuple[float, float]],
) -> bool:
    """Return true for missing, invalid, or out-of-range required features."""

    for feature, bounds in training_feature_ranges.items():
        if feature not in features:
            return True
        value = features[feature]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return True
        numeric = float(value)
        lower, upper = bounds
        if not math.isfinite(numeric) or lower > upper or numeric < lower or numeric > upper:
            return True
    return False


@dataclass
class ConservativePolicyWrapper:
    wrapped_policy: Any
    max_uncertainty: float
    conservative_action: str = "HALT"
    training_feature_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_uncertainty) or self.max_uncertainty < 0.0:
            raise ValueError("max_uncertainty must be finite and non-negative")
        for feature, bounds in self.training_feature_ranges.items():
            if (
                not isinstance(feature, str)
                or not feature
                or not isinstance(bounds, (tuple, list))
                or len(bounds) != 2
                or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in bounds)
                or any(not math.isfinite(float(value)) for value in bounds)
                or float(bounds[0]) > float(bounds[1])
            ):
                raise ValueError(f"invalid training range for feature {feature!r}: {bounds!r}")

    def decide(
        self,
        plans: Sequence[CandidatePlan],
        slo: SLO | None = None,
        budget: Budget | None = None,
        ood_features: Mapping[str, Any] | None = None,
    ) -> Decision:
        if self.training_feature_ranges and (
            ood_features is None
            or is_out_of_distribution(ood_features, self.training_feature_ranges)
        ):
            return Decision(
                action=self.conservative_action,
                rationale={"reason": "input_out_of_distribution"},
                reason_codes=[ReasonCode.CONSERVATIVE_FALLBACK_OOD],
                slo=slo,
                budget=budget,
            )

        decision = self.wrapped_policy.decide(plans, slo=slo, budget=budget)
        if decision.action == self.conservative_action:
            return decision

        identifier = decision.rationale.get("chosen_plan", decision.action)
        selected = next((plan for plan in plans if plan.action == identifier), None)
        uncertainty = selected.prediction.uncertainty if selected and selected.prediction else None
        if uncertainty is None or uncertainty > self.max_uncertainty:
            return Decision(
                action=self.conservative_action,
                rationale={
                    "reason": "selected_plan_uncertainty_exceeds_threshold",
                    "rejected_plan": identifier,
                    "selected_uncertainty": uncertainty,
                    "max_uncertainty_allowed": self.max_uncertainty,
                },
                reason_codes=[ReasonCode.CONSERVATIVE_FALLBACK_HIGH_UNCERTAINTY],
                slo=slo,
                budget=budget,
            )
        return decision
