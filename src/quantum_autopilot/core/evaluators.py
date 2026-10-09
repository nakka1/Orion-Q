"""Fail-closed evaluation of SLO and resource-budget constraints."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from quantum_autopilot.core.contracts import Budget, Prediction, ReasonCode, SLO


@dataclass(frozen=True)
class ConstraintViolation:
    constraint: str
    reason_code: ReasonCode | str
    required: Any = None
    actual: Any = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint": self.constraint,
            "reason_code": (
                self.reason_code.value
                if isinstance(self.reason_code, ReasonCode)
                else str(self.reason_code)
            ),
            "required": self.required,
            "actual": self.actual,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SLOEvaluationResult:
    satisfied: bool
    reason_codes: tuple[ReasonCode, ...] = field(default_factory=tuple)
    violations: tuple[ConstraintViolation, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BudgetEvaluationResult:
    exceeded: bool
    reason_codes: tuple[ReasonCode, ...] = field(default_factory=tuple)
    violations: tuple[ConstraintViolation, ...] = field(default_factory=tuple)


def _missing(constraint: str, required: Any) -> ConstraintViolation:
    return ConstraintViolation(
        constraint=constraint,
        reason_code=ReasonCode.REQUIRED_METRIC_MISSING,
        required=required,
        detail="required value was not supplied; fail-closed evaluation rejected the candidate",
    )


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _usage_value(metric: str, value: Any) -> float | None:
    if metric in {"attempts", "shots"} and (
        isinstance(value, bool) or not isinstance(value, int)
    ):
        return None
    return _numeric(value)


def evaluate_slo(
    slo: SLO | None,
    prediction: Prediction | None,
    metrics: Mapping[str, Any] | None = None,
) -> SLOEvaluationResult:
    """Evaluate every configured SLO constraint.

    Missing values are violations.  A constraint is never considered
    satisfied merely because an adapter did not report the corresponding
    metric.
    """

    if slo is None or not slo.has_constraints:
        return SLOEvaluationResult(True, (ReasonCode.SLO_SATISFIED,))

    supplied = dict(metrics or {})
    violations: list[ConstraintViolation] = []

    if slo.min_fidelity is not None:
        if prediction is None:
            violations.append(
                ConstraintViolation(
                    "min_fidelity",
                    ReasonCode.PREDICTION_MISSING,
                    required=slo.min_fidelity,
                    detail="fidelity constraint requires a prediction",
                )
            )
        elif prediction.mean < slo.min_fidelity:
            violations.append(
                ConstraintViolation(
                    "min_fidelity",
                    ReasonCode.SLO_VIOLATED_MIN_FIDELITY,
                    required=slo.min_fidelity,
                    actual=prediction.mean,
                )
            )

    if slo.max_latency_s is not None:
        latency = _numeric(supplied.get("latency_s"))
        if latency is None or latency < 0.0:
            violations.append(_missing("max_latency_s", slo.max_latency_s))
        elif latency > slo.max_latency_s:
            violations.append(
                ConstraintViolation(
                    "max_latency_s",
                    ReasonCode.SLO_VIOLATED_MAX_LATENCY,
                    required=slo.max_latency_s,
                    actual=latency,
                )
            )

    if slo.min_success_probability is not None:
        probability = _numeric(supplied.get("success_probability"))
        if probability is None or not 0.0 <= probability <= 1.0:
            violations.append(_missing("min_success_probability", slo.min_success_probability))
        elif probability < slo.min_success_probability:
            violations.append(
                ConstraintViolation(
                    "min_success_probability",
                    ReasonCode.SLO_VIOLATED_MIN_SUCCESS_PROBABILITY,
                    required=slo.min_success_probability,
                    actual=probability,
                )
            )

    if not violations:
        return SLOEvaluationResult(True, (ReasonCode.SLO_SATISFIED,))
    return SLOEvaluationResult(False, tuple(dict.fromkeys(item.reason_code for item in violations)), tuple(violations))


def evaluate_budget(
    budget: Budget | None,
    usage: Mapping[str, Any] | None,
) -> BudgetEvaluationResult:
    """Evaluate expected usage against every configured budget limit.

    A configured resource without a non-negative usage estimate is rejected,
    rather than silently treated as zero.
    """

    if budget is None or not budget.has_limits:
        return BudgetEvaluationResult(False, (ReasonCode.BUDGET_OK,))

    supplied = dict(usage or {})
    specs = (
        ("attempts", budget.max_attempts, ReasonCode.BUDGET_EXCEEDED_ATTEMPTS),
        ("time_s", budget.max_time_s, ReasonCode.BUDGET_EXCEEDED_TIME),
        ("shots", budget.max_shots, ReasonCode.BUDGET_EXCEEDED_SHOTS),
    )
    violations: list[ConstraintViolation] = []
    for metric, limit, exceeded_code in specs:
        if limit is None:
            continue
        actual = _usage_value(metric, supplied.get(metric))
        if actual is None or actual < 0.0:
            violations.append(
                ConstraintViolation(
                    metric,
                    ReasonCode.BUDGET_USAGE_MISSING_OR_INVALID,
                    required=limit,
                    actual=supplied.get(metric),
                    detail="configured budget requires a non-negative usage estimate",
                )
            )
        elif actual > limit:
            violations.append(
                ConstraintViolation(metric, exceeded_code, required=limit, actual=actual)
            )

    if not violations:
        return BudgetEvaluationResult(False, (ReasonCode.BUDGET_OK,))
    return BudgetEvaluationResult(True, tuple(dict.fromkeys(item.reason_code for item in violations)), tuple(violations))
