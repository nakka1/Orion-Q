"""Independent fail-closed supervisor for sandbox control intents."""

from __future__ import annotations

import math
from dataclasses import dataclass

from quantum_autopilot.core import EvidenceKind
from quantum_autopilot.control_plane.contracts import (
    ControlAction,
    ControlIntent,
    ControlMode,
    NetworkStateSnapshot,
    SafetyAssessment,
    SafetyViolation,
)


@dataclass(frozen=True)
class SafetyPolicyConfig:
    max_telemetry_age_s: float = 5.0
    max_prediction_uncertainty: float = 0.20

    def __post_init__(self) -> None:
        for name, value in (
            ("max_telemetry_age_s", self.max_telemetry_age_s),
            ("max_prediction_uncertainty", self.max_prediction_uncertainty),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{name} must be numeric")
            if not math.isfinite(float(value)) or float(value) < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")


class SafetySupervisor:
    """Authorize simulated actions only when every independent gate passes."""

    def __init__(self, config: SafetyPolicyConfig | None = None) -> None:
        self.config = config or SafetyPolicyConfig()

    @staticmethod
    def _violation(
        reason_code: str,
        constraint: str,
        required: object,
        actual: object,
        detail: str,
    ) -> SafetyViolation:
        return SafetyViolation(reason_code, constraint, required, actual, detail)

    def assess(
        self,
        mode: ControlMode,
        intent: ControlIntent,
        snapshot: NetworkStateSnapshot,
    ) -> SafetyAssessment:
        violations: list[SafetyViolation] = []

        # PHYSICAL is gated before any other check: no combination of a
        # simulated snapshot's other fields can ever authorize it. Only
        # PhysicalExecutor's own certified/interlock/dual-auth chain could,
        # and that chain is independent of this supervisor by design (defence
        # in depth — a bug in one must not open the other).
        if mode is ControlMode.PHYSICAL:
            violations.append(
                self._violation(
                    "physical_mode_not_authorized_by_sandbox_supervisor",
                    "mode",
                    "assurance | shadow | simulation",
                    mode.value,
                    "this supervisor authorizes non-physical modes only; physical actuation "
                    "is gated separately by PhysicalExecutor's adapter certification, hardware "
                    "interlock and two-person authorization",
                )
            )
            return SafetyAssessment(
                authorized=False,
                intended_action=intent.action,
                effective_action=ControlAction.HALT,
                violations=tuple(violations),
            )

        if intent.request_id != snapshot.request_id or intent.node_id != snapshot.node_id:
            violations.append(
                self._violation(
                    "intent_snapshot_identity_mismatch",
                    "intent_identity",
                    {"request_id": snapshot.request_id, "node_id": snapshot.node_id},
                    {"request_id": intent.request_id, "node_id": intent.node_id},
                    "intent and observed state must refer to the same request and node",
                )
            )
        if intent.expected_state_version != snapshot.state_version:
            violations.append(
                self._violation(
                    "stale_state_version",
                    "state_version",
                    snapshot.state_version,
                    intent.expected_state_version,
                    "the intent was not generated from the current state version",
                )
            )
        if not snapshot.integrity_verified:
            violations.append(
                self._violation(
                    "state_integrity_unverified",
                    "integrity_verified",
                    True,
                    False,
                    "unverified controller state cannot authorize an operation",
                )
            )
        if not snapshot.operator_interlock:
            violations.append(
                self._violation(
                    "operator_interlock_open",
                    "operator_interlock",
                    True,
                    False,
                    "the explicit sandbox operator interlock is open",
                )
            )
        if snapshot.out_of_distribution:
            violations.append(
                self._violation(
                    "input_out_of_distribution",
                    "out_of_distribution",
                    False,
                    True,
                    "OOD states must use the conservative HALT action",
                )
            )
        if snapshot.telemetry_age_s > self.config.max_telemetry_age_s:
            violations.append(
                self._violation(
                    "stale_telemetry",
                    "telemetry_age_s",
                    f"<= {self.config.max_telemetry_age_s:g}",
                    snapshot.telemetry_age_s,
                    "control input is older than the configured safety limit",
                )
            )
        if snapshot.prediction_uncertainty > self.config.max_prediction_uncertainty:
            violations.append(
                self._violation(
                    "prediction_uncertainty_too_high",
                    "prediction_uncertainty",
                    f"<= {self.config.max_prediction_uncertainty:g}",
                    snapshot.prediction_uncertainty,
                    "uncertainty exceeds the independent control-plane limit",
                )
            )
        if mode is ControlMode.SIMULATION and (
            snapshot.provenance.evidence_kind is not EvidenceKind.SIMULATED
        ):
            violations.append(
                self._violation(
                    "simulation_mode_requires_simulated_state",
                    "provenance.evidence_kind",
                    EvidenceKind.SIMULATED.value,
                    snapshot.provenance.evidence_kind.value,
                    "simulation execution cannot masquerade as measured operation",
                )
            )

        evidence_lane = snapshot.provenance.metadata.get("evidence_lane")
        if intent.action in {ControlAction.PURIFY, ControlAction.SWAP} and (
            evidence_lane != "bell_fidelity_control"
            or snapshot.provenance.evidence_kind is EvidenceKind.MEASURED
        ):
            violations.append(
                self._violation(
                    "evidence_lane_not_authorized_for_bell_control",
                    "provenance.evidence_lane",
                    "bell_fidelity_control with an implemented control adapter",
                    evidence_lane or "undeclared_evidence_lane",
                    (
                        "measured optical/QKD evidence cannot authorize PURIFY or SWAP; "
                        "QBER, SKR and optical KPIs are not Bell fidelity"
                    ),
                )
            )

        pair_count = len(snapshot.available_pair_fidelities)
        if intent.action in {ControlAction.PURIFY, ControlAction.SWAP}:
            if snapshot.memory_state != "STORING":
                violations.append(
                    self._violation(
                        "quantum_memory_not_ready",
                        "memory_state",
                        "STORING",
                        snapshot.memory_state,
                        f"{intent.action.value} requires occupied simulated memory",
                    )
                )
            if pair_count < 2:
                violations.append(
                    self._violation(
                        "insufficient_entangled_pairs",
                        "available_pair_count",
                        ">= 2",
                        pair_count,
                        f"{intent.action.value} requires two available pairs",
                    )
                )
        elif intent.action is ControlAction.WAIT and pair_count < 1:
            violations.append(
                self._violation(
                    "no_pair_available_to_wait",
                    "available_pair_count",
                    ">= 1",
                    pair_count,
                    "WAIT requires a stored simulated pair",
                )
            )

        authorized = intent.action is ControlAction.HALT or not violations
        effective = intent.action if authorized else ControlAction.HALT
        return SafetyAssessment(
            authorized=authorized,
            intended_action=intent.action,
            effective_action=effective,
            violations=tuple(violations),
        )


__all__ = ["SafetyPolicyConfig", "SafetySupervisor"]
