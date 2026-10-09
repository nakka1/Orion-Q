"""Typed contracts for the non-physical quantum-network control sandbox."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from quantum_autopilot.core import Provenance
from quantum_autopilot.core.contracts import JSONSerializableMixin


CONTROL_PLANE_SCHEMA_VERSION = "1.1.0"


class ControlMode(str, Enum):
    """PHYSICAL exists as a mode value so the safety supervisor can name and
    refuse it explicitly. No executor may report a physical action performed
    until a certified adapter and a two-person authorization exist — see
    control_plane/physical.py."""

    ASSURANCE = "assurance"
    SHADOW = "shadow"
    SIMULATION = "simulation"
    PHYSICAL = "physical"


class ControlAction(str, Enum):
    HALT = "HALT"
    WAIT = "WAIT"
    PURIFY = "PURIFY"
    SWAP = "SWAP"


def _nonempty(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value.strip()


def _finite(
    name: str,
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and numeric < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and numeric > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return numeric


def _aware_timestamp(name: str, value: Any) -> str:
    text = _nonempty(name, value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return text


@dataclass(frozen=True)
class NetworkStateSnapshot(JSONSerializableMixin):
    """One complete controller input for a simulated repeater node."""

    request_id: str
    node_id: str
    timestamp_utc: str
    state_version: int
    predicted_fidelity: float
    prediction_uncertainty: float
    telemetry_age_s: float
    memory_state: str
    available_pair_fidelities: tuple[float, ...] = field(default_factory=tuple)
    memory_ages_s: tuple[float, ...] = field(default_factory=tuple)
    telemetry: dict[str, Any] = field(default_factory=dict)
    out_of_distribution: bool = False
    integrity_verified: bool = False
    operator_interlock: bool = False
    provenance: Provenance | Mapping[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = CONTROL_PLANE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _nonempty("request_id", self.request_id))
        object.__setattr__(self, "node_id", _nonempty("node_id", self.node_id))
        object.__setattr__(
            self,
            "timestamp_utc",
            _aware_timestamp("timestamp_utc", self.timestamp_utc),
        )
        if isinstance(self.state_version, bool) or not isinstance(self.state_version, int):
            raise ValueError("state_version must be an integer")
        if self.state_version < 0:
            raise ValueError("state_version must be >= 0")
        object.__setattr__(
            self,
            "predicted_fidelity",
            _finite("predicted_fidelity", self.predicted_fidelity, minimum=0.0, maximum=1.0),
        )
        object.__setattr__(
            self,
            "prediction_uncertainty",
            _finite("prediction_uncertainty", self.prediction_uncertainty, minimum=0.0),
        )
        object.__setattr__(
            self,
            "telemetry_age_s",
            _finite("telemetry_age_s", self.telemetry_age_s, minimum=0.0),
        )
        if self.memory_state not in {"IDLE", "STORING"}:
            raise ValueError("memory_state must be IDLE or STORING")
        pairs = tuple(
            _finite(
                f"available_pair_fidelities[{index}]",
                value,
                minimum=0.0,
                maximum=1.0,
            )
            for index, value in enumerate(self.available_pair_fidelities)
        )
        ages = tuple(
            _finite(f"memory_ages_s[{index}]", value, minimum=0.0)
            for index, value in enumerate(self.memory_ages_s)
        )
        if len(ages) != len(pairs):
            raise ValueError("memory_ages_s must align with available_pair_fidelities")
        object.__setattr__(self, "available_pair_fidelities", pairs)
        object.__setattr__(self, "memory_ages_s", ages)
        if not isinstance(self.out_of_distribution, bool):
            raise ValueError("out_of_distribution must be boolean")
        if not isinstance(self.integrity_verified, bool):
            raise ValueError("integrity_verified must be boolean")
        if not isinstance(self.operator_interlock, bool):
            raise ValueError("operator_interlock must be boolean")
        provenance = self.provenance
        if isinstance(provenance, Mapping):
            # A retry of the same JSON must not acquire a new request hash.
            # Default missing provenance time to this snapshot's stable time,
            # not Provenance's wall-clock construction default.
            provenance_values = dict(provenance)
            provenance_values.setdefault("timestamp", self.timestamp_utc)
            provenance = Provenance.from_dict(provenance_values)
        if not isinstance(provenance, Provenance):
            raise ValueError("provenance must be provided")
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "telemetry", dict(self.telemetry))
        object.__setattr__(self, "metadata", dict(self.metadata))
        if self.schema_version != CONTROL_PLANE_SCHEMA_VERSION:
            raise ValueError("unsupported control-plane schema_version")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "NetworkStateSnapshot":
        values = dict(payload)
        for name in ("available_pair_fidelities", "memory_ages_s"):
            if name in values:
                values[name] = tuple(values[name])
        return cls(**values)


@dataclass(frozen=True)
class ControlIntent(JSONSerializableMixin):
    intent_id: str
    request_id: str
    node_id: str
    action: ControlAction | str
    expected_state_version: int
    issued_at_utc: str
    policy_name: str
    policy_details: dict[str, Any] = field(default_factory=dict)
    schema_version: str = CONTROL_PLANE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_id", _nonempty("intent_id", self.intent_id))
        object.__setattr__(self, "request_id", _nonempty("request_id", self.request_id))
        object.__setattr__(self, "node_id", _nonempty("node_id", self.node_id))
        try:
            action = self.action if isinstance(self.action, ControlAction) else ControlAction(self.action)
        except ValueError as exc:
            raise ValueError(f"unsupported control action: {self.action!r}") from exc
        object.__setattr__(self, "action", action)
        if isinstance(self.expected_state_version, bool) or not isinstance(
            self.expected_state_version, int
        ):
            raise ValueError("expected_state_version must be an integer")
        if self.expected_state_version < 0:
            raise ValueError("expected_state_version must be >= 0")
        object.__setattr__(
            self,
            "issued_at_utc",
            _aware_timestamp("issued_at_utc", self.issued_at_utc),
        )
        object.__setattr__(self, "policy_name", _nonempty("policy_name", self.policy_name))
        object.__setattr__(self, "policy_details", dict(self.policy_details))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ControlIntent":
        return cls(**dict(payload))


@dataclass(frozen=True)
class SafetyViolation(JSONSerializableMixin):
    reason_code: str
    constraint: str
    required: Any
    actual: Any
    detail: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_code", _nonempty("reason_code", self.reason_code))
        object.__setattr__(self, "constraint", _nonempty("constraint", self.constraint))
        object.__setattr__(self, "detail", _nonempty("detail", self.detail))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SafetyViolation":
        return cls(**dict(payload))


@dataclass(frozen=True)
class SafetyAssessment(JSONSerializableMixin):
    authorized: bool
    intended_action: ControlAction | str
    effective_action: ControlAction | str
    violations: tuple[SafetyViolation, ...] = field(default_factory=tuple)
    schema_version: str = CONTROL_PLANE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.authorized, bool):
            raise ValueError("authorized must be boolean")
        object.__setattr__(self, "intended_action", ControlAction(self.intended_action))
        object.__setattr__(self, "effective_action", ControlAction(self.effective_action))
        violations = tuple(
            item if isinstance(item, SafetyViolation) else SafetyViolation.from_dict(item)
            for item in self.violations
        )
        object.__setattr__(self, "violations", violations)
        if not self.authorized and self.effective_action is not ControlAction.HALT:
            raise ValueError("unauthorized actions must fail closed to HALT")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SafetyAssessment":
        values = dict(payload)
        values["violations"] = tuple(values.get("violations", ()))
        return cls(**values)


@dataclass(frozen=True)
class ControlExecutionResult(JSONSerializableMixin):
    """`physical_action_performed` stays False for every executor this repo
    ships. The invariant below is deliberate: no code path in this codebase
    is allowed to claim it touched hardware."""

    status: str
    action: ControlAction | str
    performed_in_simulator: bool
    physical_action_performed: bool
    details: dict[str, Any] = field(default_factory=dict)
    schema_version: str = CONTROL_PLANE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _nonempty("status", self.status))
        object.__setattr__(self, "action", ControlAction(self.action))
        if not isinstance(self.performed_in_simulator, bool):
            raise ValueError("performed_in_simulator must be boolean")
        if not isinstance(self.physical_action_performed, bool):
            raise ValueError("physical_action_performed must be boolean")
        if self.physical_action_performed:
            raise ValueError(
                "no executor in this codebase is certified to perform a physical action"
            )
        object.__setattr__(self, "details", dict(self.details))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ControlExecutionResult":
        return cls(**dict(payload))


@dataclass(frozen=True)
class ControlCycleRecord(JSONSerializableMixin):
    cycle_id: str
    mode: ControlMode | str
    started_at_utc: str
    completed_at_utc: str
    request_sha256: str
    snapshot: NetworkStateSnapshot | Mapping[str, Any]
    intent: ControlIntent | Mapping[str, Any]
    safety: SafetyAssessment | Mapping[str, Any]
    execution: ControlExecutionResult | Mapping[str, Any]
    schema_version: str = CONTROL_PLANE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "cycle_id", _nonempty("cycle_id", self.cycle_id))
        object.__setattr__(self, "mode", ControlMode(self.mode))
        object.__setattr__(
            self,
            "started_at_utc",
            _aware_timestamp("started_at_utc", self.started_at_utc),
        )
        object.__setattr__(
            self,
            "completed_at_utc",
            _aware_timestamp("completed_at_utc", self.completed_at_utc),
        )
        if not isinstance(self.request_sha256, str) or len(self.request_sha256) != 64:
            raise ValueError("request_sha256 must be a SHA-256 hex digest")
        try:
            int(self.request_sha256, 16)
        except ValueError as exc:
            raise ValueError("request_sha256 must be a SHA-256 hex digest") from exc
        if isinstance(self.snapshot, Mapping):
            object.__setattr__(self, "snapshot", NetworkStateSnapshot.from_dict(self.snapshot))
        if isinstance(self.intent, Mapping):
            object.__setattr__(self, "intent", ControlIntent.from_dict(self.intent))
        if isinstance(self.safety, Mapping):
            object.__setattr__(self, "safety", SafetyAssessment.from_dict(self.safety))
        if isinstance(self.execution, Mapping):
            object.__setattr__(
                self,
                "execution",
                ControlExecutionResult.from_dict(self.execution),
            )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ControlCycleRecord":
        return cls(**dict(payload))


__all__ = [
    "CONTROL_PLANE_SCHEMA_VERSION",
    "ControlAction",
    "ControlCycleRecord",
    "ControlExecutionResult",
    "ControlIntent",
    "ControlMode",
    "NetworkStateSnapshot",
    "SafetyAssessment",
    "SafetyViolation",
]
