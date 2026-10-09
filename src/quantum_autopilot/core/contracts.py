"""Validated, domain-neutral contracts for evidence-aware decisions.

The contracts deliberately do not import :mod:`quantum_twin`.  Domain
adapters may translate existing WDM/QKD or circuit objects into these types,
while the scientific implementation remains the source of physical truth.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


SCHEMA_VERSION = "1.0.0"


class EvidenceKind(str, Enum):
    """Origin of the target or outcome represented by an artifact."""

    MEASURED = "measured"
    SIMULATED = "simulated"
    SYNTHESIZED = "synthesized"


class ReasonCode(str, Enum):
    """Stable, machine-readable reasons emitted by evaluators and policies."""

    SLO_SATISFIED = "slo_satisfied"
    SLO_VIOLATED_MIN_FIDELITY = "slo_violated_min_fidelity"
    SLO_VIOLATED_MAX_LATENCY = "slo_violated_max_latency"
    SLO_VIOLATED_MIN_SUCCESS_PROBABILITY = "slo_violated_min_success_probability"
    REQUIRED_METRIC_MISSING = "required_metric_missing"
    PREDICTION_MISSING = "prediction_missing"
    BUDGET_OK = "budget_ok"
    BUDGET_EXCEEDED_ATTEMPTS = "budget_exceeded_attempts"
    BUDGET_EXCEEDED_TIME = "budget_exceeded_time"
    BUDGET_EXCEEDED_SHOTS = "budget_exceeded_shots"
    BUDGET_USAGE_MISSING_OR_INVALID = "budget_usage_missing_or_invalid"
    NO_CANDIDATE_PLANS_AVAILABLE = "no_candidate_plans_available"
    NO_VIABLE_CANDIDATE = "no_viable_candidate"
    BEST_AVAILABLE_PLAN_SELECTED = "best_available_plan_selected"
    CONSERVATIVE_FALLBACK_HIGH_UNCERTAINTY = "conservative_fallback_high_uncertainty"
    CONSERVATIVE_FALLBACK_OOD = "conservative_fallback_ood"
    POLICY_RETURNED_UNKNOWN_PLAN = "policy_returned_unknown_plan"


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {item.name: _json_value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class JSONSerializableMixin:
    """Small deterministic JSON boundary used by decision artifacts."""

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True)


def _finite_number(name: str, value: Any, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and numeric < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return numeric


def _probability(name: str, value: Any) -> float:
    numeric = _finite_number(name, value)
    if not 0.0 <= numeric <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return numeric


def _nonnegative_integer(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


def _aware_iso_timestamp(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SystemSnapshot(JSONSerializableMixin):
    domain: str
    observed: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    timestamp: str = field(default_factory=_utc_now)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.domain, str) or not self.domain.strip():
            raise ValueError("SystemSnapshot.domain cannot be empty")
        _aware_iso_timestamp("SystemSnapshot.timestamp", self.timestamp)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SystemSnapshot":
        return cls(**dict(payload))


@dataclass
class Workload(JSONSerializableMixin):
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    workload_id: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("Workload.kind cannot be empty")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Workload":
        return cls(**dict(payload))


@dataclass
class Prediction(JSONSerializableMixin):
    mean: float
    uncertainty: float
    method: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.mean = _finite_number("Prediction.mean", self.mean)
        self.uncertainty = _finite_number("Prediction.uncertainty", self.uncertainty, minimum=0.0)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Prediction":
        return cls(**dict(payload))


@dataclass
class CandidatePlan(JSONSerializableMixin):
    action: str
    estimated_cost: float | None = None
    prediction: Prediction | None = None
    expected_metrics: dict[str, Any] = field(default_factory=dict)
    expected_usage: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.action, str) or not self.action.strip():
            raise ValueError("CandidatePlan.action cannot be empty")
        if self.estimated_cost is not None:
            self.estimated_cost = _finite_number(
                "CandidatePlan.estimated_cost", self.estimated_cost, minimum=0.0
            )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CandidatePlan":
        values = dict(payload)
        if isinstance(values.get("prediction"), Mapping):
            values["prediction"] = Prediction.from_dict(values["prediction"])
        return cls(**values)


@dataclass
class SLO(JSONSerializableMixin):
    min_fidelity: float | None = None
    max_latency_s: float | None = None
    min_success_probability: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.min_fidelity is not None:
            self.min_fidelity = _probability("SLO.min_fidelity", self.min_fidelity)
        if self.max_latency_s is not None:
            self.max_latency_s = _finite_number("SLO.max_latency_s", self.max_latency_s, minimum=0.0)
        if self.min_success_probability is not None:
            self.min_success_probability = _probability(
                "SLO.min_success_probability", self.min_success_probability
            )

    @property
    def has_constraints(self) -> bool:
        return any(
            value is not None
            for value in (self.min_fidelity, self.max_latency_s, self.min_success_probability)
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SLO":
        return cls(**dict(payload))


@dataclass
class Budget(JSONSerializableMixin):
    max_attempts: int | None = None
    max_time_s: float | None = None
    max_shots: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.max_attempts is not None:
            self.max_attempts = _nonnegative_integer("Budget.max_attempts", self.max_attempts)
        if self.max_time_s is not None:
            self.max_time_s = _finite_number("Budget.max_time_s", self.max_time_s, minimum=0.0)
        if self.max_shots is not None:
            self.max_shots = _nonnegative_integer("Budget.max_shots", self.max_shots)

    @property
    def has_limits(self) -> bool:
        return any(value is not None for value in (self.max_attempts, self.max_time_s, self.max_shots))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Budget":
        return cls(**dict(payload))


@dataclass
class Decision(JSONSerializableMixin):
    action: str
    rationale: dict[str, Any] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)
    slo: SLO | None = None
    budget: Budget | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.action, str) or not self.action.strip():
            raise ValueError("Decision.action cannot be empty")
        self.reason_codes = [item.value if isinstance(item, ReasonCode) else str(item) for item in self.reason_codes]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Decision":
        values = dict(payload)
        if isinstance(values.get("slo"), Mapping):
            values["slo"] = SLO.from_dict(values["slo"])
        if isinstance(values.get("budget"), Mapping):
            values["budget"] = Budget.from_dict(values["budget"])
        return cls(**values)


@dataclass
class Outcome(JSONSerializableMixin):
    success: bool
    observed: dict[str, Any] = field(default_factory=dict)
    actual_cost: float | None = None
    workload_id: str = ""
    timestamp: str = field(default_factory=_utc_now)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.success, bool):
            raise ValueError("Outcome.success must be boolean")
        if self.actual_cost is not None:
            self.actual_cost = _finite_number("Outcome.actual_cost", self.actual_cost, minimum=0.0)
        _aware_iso_timestamp("Outcome.timestamp", self.timestamp)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Outcome":
        return cls(**dict(payload))


@dataclass
class Provenance(JSONSerializableMixin):
    evidence_kind: EvidenceKind | str | None
    source: str = ""
    timestamp: str = field(default_factory=_utc_now)
    git_commit: str = ""
    config_hash: str = ""
    dataset_hash: str = ""
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.evidence_kind is None:
            raise ValueError("Provenance.evidence_kind is required")
        if not isinstance(self.evidence_kind, EvidenceKind):
            try:
                self.evidence_kind = EvidenceKind(self.evidence_kind)
            except ValueError as exc:
                raise ValueError(f"unsupported evidence kind: {self.evidence_kind!r}") from exc
        _aware_iso_timestamp("Provenance.timestamp", self.timestamp)
        for name, digest in self.artifact_hashes.items():
            if not isinstance(name, str) or not name:
                raise ValueError("artifact hash names cannot be empty")
            if not isinstance(digest, str) or not digest:
                raise ValueError(f"artifact hash for {name!r} cannot be empty")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Provenance":
        return cls(**dict(payload))
