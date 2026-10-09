"""Paired multi-metric evaluation for simulated fidelity controllers.

Prediction quality, admission utility and the four-action sandbox policy are
reported separately.  This prevents a saturated downstream policy from being
mistaken for evidence that two predictors are operationally equivalent.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

import numpy as np

from quantum_autopilot.control_plane.contracts import (
    ControlAction,
    ControlIntent,
    ControlMode,
    NetworkStateSnapshot,
)
from quantum_autopilot.control_plane.policy import RepeaterSandboxPolicy
from quantum_autopilot.control_plane.safety import SafetyPolicyConfig, SafetySupervisor
from quantum_autopilot.core import EvidenceKind, Provenance
from quantum_twin.ml.probabilistic import evaluate_calibration
from quantum_twin.quantum.purification import bbpssw_analytical


COMPARATIVE_EVALUATION_SCHEMA_VERSION = "1.0.0"
CONTROLLER_KINDS = {"distribution", "reactive", "oracle"}


@dataclass(frozen=True)
class ComparativeControlConfig:
    """Frozen decision/resource assumptions for one comparative campaign."""

    fidelity_threshold: float = 0.65
    confidence_k: float = 1.0
    max_wait_cycles: int = 2
    max_prediction_uncertainty: float = 0.20
    n_gates_per_purification: int = 10
    neural_inference_latency_s: float = 267.6e-6
    control_latency_s: float = 3.863e-6
    wait_time_s: float = 1e-6
    qpu_energy_per_gate_j: float = 1e-6
    inference_power_w: float = 0.1
    control_power_w: float = 0.1
    memory_hold_power_w: float = 0.01
    schema_version: str = COMPARATIVE_EVALUATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("fidelity_threshold", "confidence_k", "max_prediction_uncertainty"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
            if not math.isfinite(float(value)) or float(value) < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not 0.0 <= self.fidelity_threshold <= 1.0:
            raise ValueError("fidelity_threshold must be in [0, 1]")
        if not 0.0 <= self.max_prediction_uncertainty <= 1.0:
            raise ValueError("max_prediction_uncertainty must be in [0, 1]")
        for name in ("max_wait_cycles", "n_gates_per_purification"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in (
            "neural_inference_latency_s",
            "control_latency_s",
            "wait_time_s",
            "qpu_energy_per_gate_j",
            "inference_power_w",
            "control_power_w",
            "memory_hold_power_w",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
            if not math.isfinite(float(value)) or float(value) < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.schema_version != COMPARATIVE_EVALUATION_SCHEMA_VERSION:
            raise ValueError("unsupported comparative-evaluation schema_version")


def _vector(name: str, values: Sequence[Any], *, probability: bool = False) -> np.ndarray:
    result = np.asarray(values)
    if result.ndim != 1 or len(result) == 0:
        raise ValueError(f"{name} must be a non-empty one-dimensional vector")
    if result.dtype == np.bool_:
        result = result.astype(bool)
    else:
        try:
            result = result.astype(float)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be numeric") from exc
        if not np.isfinite(result).all():
            raise ValueError(f"{name} must contain only finite values")
        if probability and ((result < 0.0) | (result > 1.0)).any():
            raise ValueError(f"{name} must be in [0, 1]")
    return result


def _validate_aligned(**vectors: np.ndarray) -> None:
    lengths = {name: len(value) for name, value in vectors.items()}
    if len(set(lengths.values())) != 1:
        raise ValueError(f"controller vectors must align: {lengths}")


def _initial_decision(
    mean: float,
    uncertainty: float,
    *,
    controller_kind: str,
    config: ComparativeControlConfig,
) -> tuple[str, str, int]:
    if controller_kind in {"reactive", "oracle"}:
        action = "PURIFY" if mean >= config.fidelity_threshold else "HALT"
        return action, action, 0
    lower = mean - config.confidence_k * uncertainty
    upper = mean + config.confidence_k * uncertainty
    if lower >= config.fidelity_threshold:
        return "PURIFY", "PURIFY", 0
    if upper < config.fidelity_threshold:
        return "HALT", "HALT", 0
    terminal = "PURIFY" if mean >= config.fidelity_threshold else "HALT"
    return "WAIT", terminal, config.max_wait_cycles


def evaluate_controller_predictions(
    controller: str,
    mean: Sequence[float],
    uncertainty: Sequence[float],
    actual_fidelity: Sequence[float],
    availability: Sequence[bool | int | float],
    out_of_distribution: Sequence[bool | int],
    *,
    controller_kind: str = "distribution",
    inference_latency_s: float | None = None,
    config: ComparativeControlConfig | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Evaluate one controller on a shared trace without scalarizing trade-offs."""

    cfg = config or ComparativeControlConfig()
    if not isinstance(controller, str) or not controller.strip():
        raise ValueError("controller cannot be empty")
    if controller_kind not in CONTROLLER_KINDS:
        raise ValueError(f"controller_kind must be one of {sorted(CONTROLLER_KINDS)}")
    mu = _vector("mean", mean, probability=True)
    sigma = _vector("uncertainty", uncertainty)
    truth = _vector("actual_fidelity", actual_fidelity, probability=True)
    available_raw = _vector("availability", availability)
    ood_raw = _vector("out_of_distribution", out_of_distribution)
    _validate_aligned(
        mean=mu,
        uncertainty=sigma,
        actual_fidelity=truth,
        availability=available_raw,
        out_of_distribution=ood_raw,
    )
    if (sigma < 0.0).any():
        raise ValueError("uncertainty must be non-negative")
    available = available_raw.astype(bool)
    ood = ood_raw.astype(bool)
    latency = cfg.neural_inference_latency_s if inference_latency_s is None else inference_latency_s
    if isinstance(latency, bool) or not isinstance(latency, (int, float)):
        raise ValueError("inference_latency_s must be numeric")
    latency = float(latency)
    if not math.isfinite(latency) or latency < 0.0:
        raise ValueError("inference_latency_s must be finite and non-negative")

    rows: list[dict[str, Any]] = []
    for index, (mean_value, sigma_value, true_value, pair_available, is_ood) in enumerate(
        zip(mu, sigma, truth, available, ood)
    ):
        initial, terminal, waits = _initial_decision(
            float(mean_value),
            float(sigma_value),
            controller_kind=controller_kind,
            config=cfg,
        )
        safety_gate = bool(is_ood or sigma_value > cfg.max_prediction_uncertainty)
        no_pair = not bool(pair_available)
        if no_pair:
            effective = "NO_PAIR"
        elif safety_gate and terminal != "HALT":
            effective = "HALT"
        else:
            effective = terminal
        forced_halt = bool(pair_available and safety_gate and terminal != "HALT")
        attempted = effective == "PURIFY"
        expected_useful = 0.0
        output_fidelity: float | None = None
        success_probability: float | None = None
        if attempted:
            purification = bbpssw_analytical(float(true_value))
            output_fidelity = float(purification["F_after"])
            success_probability = float(purification["success_probability"])
            if output_fidelity >= cfg.fidelity_threshold:
                expected_useful = success_probability
        rows.append(
            {
                "index": index,
                "controller": controller,
                "controller_kind": controller_kind,
                "predicted_fidelity": float(mean_value),
                "prediction_uncertainty": float(sigma_value),
                "actual_fidelity": float(true_value),
                "pair_available": bool(pair_available),
                "out_of_distribution": bool(is_ood),
                "initial_action": "NO_PAIR" if no_pair else initial,
                "terminal_action_before_safety": "NO_PAIR" if no_pair else terminal,
                "effective_action": effective,
                "wait_cycles": 0 if no_pair else waits,
                "safety_gate_triggered": bool(pair_available and safety_gate),
                "safety_forced_halt": forced_halt,
                "purification_attempted": attempted,
                "false_purification": bool(
                    attempted and true_value < cfg.fidelity_threshold
                ),
                "missed_good_pair": bool(
                    pair_available
                    and true_value >= cfg.fidelity_threshold
                    and not attempted
                ),
                "bbpssw_output_fidelity": output_fidelity,
                "bbpssw_success_probability": success_probability,
                "expected_useful_pair": expected_useful,
                "physical_action_performed": False,
            }
        )

    arrivals = int(available.sum())
    attempts = sum(int(row["purification_attempted"]) for row in rows)
    waits = sum(int(row["initial_action"] == "WAIT") for row in rows)
    total_wait_cycles = sum(int(row["wait_cycles"]) for row in rows)
    expected_useful_pairs = float(sum(row["expected_useful_pair"] for row in rows))
    inference_evaluations = len(rows) + total_wait_cycles
    qpu_gates = attempts * cfg.n_gates_per_purification
    total_latency_s = (
        inference_evaluations * latency
        + len(rows) * cfg.control_latency_s
        + total_wait_cycles * cfg.wait_time_s
    )
    energy_qpu = qpu_gates * cfg.qpu_energy_per_gate_j
    energy_inference = inference_evaluations * latency * cfg.inference_power_w
    energy_control = len(rows) * cfg.control_latency_s * cfg.control_power_w
    energy_memory = total_wait_cycles * cfg.wait_time_s * cfg.memory_hold_power_w
    calibration = evaluate_calibration(mu, np.maximum(sigma, 1e-6), truth, cfg.fidelity_threshold)
    available_error = mu[available] - truth[available]
    summary = {
        "controller": controller,
        "controller_kind": controller_kind,
        "samples": len(rows),
        "pair_arrivals": arrivals,
        "no_pair": len(rows) - arrivals,
        "prediction_mae": float(np.mean(np.abs(mu - truth))),
        "prediction_rmse": float(np.sqrt(np.mean((mu - truth) ** 2))),
        "available_pair_mae": (
            float(np.mean(np.abs(available_error))) if arrivals else None
        ),
        "brier_score": calibration["brier_score"],
        "ece": calibration["ece"],
        "coverage_1sigma": calibration["coverage_1sigma"],
        "mean_sigma": calibration["mean_sigma"],
        "initial_wait_count": waits,
        "initial_wait_rate_pct": waits / max(arrivals, 1) * 100.0,
        "purification_attempts": attempts,
        "qpu_gates": qpu_gates,
        "qpu_cycle_savings_vs_blind_pct": (1.0 - attempts / max(arrivals, 1)) * 100.0,
        "false_purification_count": sum(int(row["false_purification"]) for row in rows),
        "missed_good_pair_count": sum(int(row["missed_good_pair"]) for row in rows),
        "expected_useful_pairs": expected_useful_pairs,
        "expected_useful_pair_rate_per_arrival_pct": (
            expected_useful_pairs / max(arrivals, 1) * 100.0
        ),
        "expected_yield_per_attempt_pct": (
            expected_useful_pairs / max(attempts, 1) * 100.0
        ),
        "safety_gate_count": sum(int(row["safety_gate_triggered"]) for row in rows),
        "safety_gate_rate_pct": (
            sum(int(row["safety_gate_triggered"]) for row in rows)
            / max(arrivals, 1)
            * 100.0
        ),
        "safety_forced_halt_count": sum(int(row["safety_forced_halt"]) for row in rows),
        "ood_rate_pct": float(ood.mean() * 100.0),
        "inference_evaluations": inference_evaluations,
        "total_latency_ms_model_based": total_latency_s * 1000.0,
        "energy_qpu_j_model_based": energy_qpu,
        "energy_inference_j_model_based": energy_inference,
        "energy_control_j_model_based": energy_control,
        "energy_memory_j_model_based": energy_memory,
        "energy_total_j_model_based": (
            energy_qpu + energy_inference + energy_control + energy_memory
        ),
        "physical_action_performed": False,
        "schema_version": COMPARATIVE_EVALUATION_SCHEMA_VERSION,
    }
    return rows, summary


def audit_repeater_policy_saturation(
    controller: str,
    mean: Sequence[float],
    uncertainty: Sequence[float],
    out_of_distribution: Sequence[bool | int],
    *,
    config: ComparativeControlConfig | None = None,
) -> dict[str, Any]:
    """Audit whether predictions can change the current four-action policy."""

    cfg = config or ComparativeControlConfig()
    mu = _vector("mean", mean, probability=True)
    sigma = _vector("uncertainty", uncertainty)
    ood = _vector("out_of_distribution", out_of_distribution).astype(bool)
    _validate_aligned(mean=mu, uncertainty=sigma, out_of_distribution=ood)
    policy = RepeaterSandboxPolicy(threshold=cfg.fidelity_threshold)
    supervisor = SafetySupervisor(
        SafetyPolicyConfig(max_prediction_uncertainty=cfg.max_prediction_uncertainty)
    )
    intended: list[str] = []
    effective: list[str] = []
    violations: dict[str, int] = {}
    timestamp = datetime.now(timezone.utc).isoformat()
    for index, (mean_value, sigma_value, is_ood) in enumerate(zip(mu, sigma, ood)):
        snapshot = NetworkStateSnapshot(
            request_id=f"policy-audit-{controller}-{index}",
            node_id="repeater-policy-audit",
            timestamp_utc=timestamp,
            state_version=index,
            predicted_fidelity=float(mean_value),
            prediction_uncertainty=float(sigma_value),
            telemetry_age_s=0.1,
            memory_state="STORING",
            available_pair_fidelities=(0.9, 0.9),
            memory_ages_s=(0.0, 0.0),
            out_of_distribution=bool(is_ood),
            integrity_verified=True,
            operator_interlock=True,
            provenance=Provenance(
                evidence_kind=EvidenceKind.SIMULATED,
                source="paired-controller-policy-saturation-audit",
                timestamp=timestamp,
                metadata={
                    "evidence_lane": "bell_fidelity_control",
                    "target_semantics": "simulated_future_bell_fidelity",
                },
            ),
        )
        action, details = policy.decide(snapshot)
        intent = ControlIntent(
            intent_id=f"intent-{controller}-{index}",
            request_id=snapshot.request_id,
            node_id=snapshot.node_id,
            action=action,
            expected_state_version=index,
            issued_at_utc=timestamp,
            policy_name=policy.name,
            policy_details=details,
        )
        safety = supervisor.assess(ControlMode.SHADOW, intent, snapshot)
        intended.append(action.value)
        effective.append(safety.effective_action.value)
        for violation in safety.violations:
            violations[violation.reason_code] = violations.get(violation.reason_code, 0) + 1
    intended_counts = {action: intended.count(action) for action in sorted(set(intended))}
    effective_counts = {action: effective.count(action) for action in sorted(set(effective))}
    return {
        "controller": controller,
        "samples": len(mu),
        "prediction_min": float(mu.min()),
        "prediction_max": float(mu.max()),
        "prediction_span": float(mu.max() - mu.min()),
        "intended_action_counts": intended_counts,
        "effective_action_counts": effective_counts,
        "unique_intended_action_count": len(intended_counts),
        "intended_policy_saturated": len(intended_counts) == 1,
        "safety_violation_counts": dict(sorted(violations.items())),
        "fixed_policy_audit_state": {
            "memory_state": "STORING",
            "available_pair_fidelities": [0.9, 0.9],
        },
        "physical_action_performed": False,
        "schema_version": COMPARATIVE_EVALUATION_SCHEMA_VERSION,
    }


def paired_sign_flip_test(
    differences: Sequence[float],
    *,
    alternative: str = "greater",
) -> dict[str, Any]:
    """Exact paired randomization test for a mean seed-level difference."""

    values = _vector("differences", differences)
    if alternative not in {"greater", "two-sided"}:
        raise ValueError("alternative must be greater or two-sided")
    if len(values) > 20:
        raise ValueError("exact sign-flip test is limited to 20 paired seeds")
    observed = float(values.mean())
    distribution = np.asarray(
        [
            np.mean(values * np.asarray(signs, dtype=float))
            for signs in itertools.product((-1.0, 1.0), repeat=len(values))
        ],
        dtype=float,
    )
    tolerance = 1e-15
    if alternative == "greater":
        p_value = float(np.mean(distribution >= observed - tolerance))
    else:
        p_value = float(np.mean(np.abs(distribution) >= abs(observed) - tolerance))
    return {
        "n_pairs": len(values),
        "mean_difference": observed,
        "p_value": p_value,
        "alternative": alternative,
        "method": "exact paired sign-flip randomization test",
    }


def holm_adjust(p_values: Sequence[float]) -> np.ndarray:
    """Return monotone Holm-adjusted p-values in original order."""

    values = _vector("p_values", p_values, probability=True)
    order = np.argsort(values, kind="stable")
    adjusted_sorted = np.empty(len(values), dtype=float)
    running = 0.0
    for rank, index in enumerate(order):
        candidate = min(1.0, (len(values) - rank) * float(values[index]))
        running = max(running, candidate)
        adjusted_sorted[rank] = running
    adjusted = np.empty(len(values), dtype=float)
    for rank, index in enumerate(order):
        adjusted[index] = adjusted_sorted[rank]
    return adjusted


__all__ = [
    "COMPARATIVE_EVALUATION_SCHEMA_VERSION",
    "ComparativeControlConfig",
    "audit_repeater_policy_saturation",
    "evaluate_controller_predictions",
    "holm_adjust",
    "paired_sign_flip_test",
]
