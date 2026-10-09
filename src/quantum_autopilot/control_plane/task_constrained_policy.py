"""Task-constrained repeater policy and calibration-only abstention fitting.

The legacy policy lets PURIFY and SWAP compete whenever two pairs are present.
This module makes the network operation explicit: link preparation may choose
HALT/WAIT/PURIFY, while path extension may choose HALT/WAIT/SWAP. The two
quantum operations therefore never compete in the same decision.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np

from quantum_autopilot.control_plane.contracts import (
    ControlAction,
    ControlIntent,
    ControlMode,
    NetworkStateSnapshot,
)
from quantum_autopilot.control_plane.safety import SafetyPolicyConfig, SafetySupervisor
from quantum_autopilot.core import EvidenceKind, Provenance
from quantum_autopilot.core.contracts import JSONSerializableMixin
from quantum_twin.quantum.purification import bbpssw_analytical
from quantum_twin.quantum.swapping import WernerStateSwapping


TASK_POLICY_SCHEMA_VERSION = "1.0.0"


class ControlTask(str, Enum):
    """Mutually exclusive quantum-network operations."""

    LINK_PURIFICATION = "link_purification"
    PATH_EXTENSION = "path_extension"


def _finite(
    name: str,
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return result


def _nonempty(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value.strip()


def _vector(name: str, values: Sequence[Any], *, probability: bool = False) -> np.ndarray:
    try:
        result = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if result.ndim != 1 or len(result) == 0:
        raise ValueError(f"{name} must be a non-empty one-dimensional vector")
    if not np.isfinite(result).all():
        raise ValueError(f"{name} must contain only finite values")
    if probability and ((result < 0.0) | (result > 1.0)).any():
        raise ValueError(f"{name} must be in [0, 1]")
    return result


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _probability_good(mean: np.ndarray, sigma: np.ndarray, threshold: float) -> np.ndarray:
    sigma_safe = np.maximum(sigma, 1e-6)
    z = (threshold - mean) / sigma_safe
    erf = np.vectorize(math.erf, otypes=[float])
    return 1.0 - 0.5 * (1.0 + erf(z / math.sqrt(2.0)))


def _purification_potential(
    truth: np.ndarray,
    threshold: float,
) -> tuple[np.ndarray, np.ndarray]:
    useful = np.zeros(len(truth), dtype=bool)
    expected_yield = np.zeros(len(truth), dtype=float)
    for index, fidelity in enumerate(truth):
        outcome = bbpssw_analytical(float(fidelity))
        useful[index] = float(outcome["F_after"]) >= threshold
        if useful[index]:
            expected_yield[index] = float(outcome["success_probability"])
    return useful, expected_yield


def _minimum_input_fidelity_for_output_threshold(output_threshold: float) -> float:
    """Invert monotonic BBPSSW fidelity gain on the useful F >= 0.5 branch."""

    lower = 0.5
    upper = 1.0
    if float(bbpssw_analytical(lower)["F_after"]) >= output_threshold:
        return lower
    if float(bbpssw_analytical(upper)["F_after"]) < output_threshold:
        raise ValueError("fidelity threshold cannot be reached by analytical BBPSSW")
    for _ in range(64):
        midpoint = (lower + upper) / 2.0
        if float(bbpssw_analytical(midpoint)["F_after"]) >= output_threshold:
            upper = midpoint
        else:
            lower = midpoint
    return upper


@dataclass(frozen=True)
class AbstentionCalibrationConfig(JSONSerializableMixin):
    """Predeclared search space and normalized calibration-only utility."""

    fidelity_threshold: float = 0.65
    max_safety_abstention_rate_pct: float = 20.0
    max_policy_wait_rate_pct: float = 35.0
    probability_margin_grid: tuple[float, ...] = (
        0.0,
        0.025,
        0.05,
        0.075,
        0.10,
        0.15,
        0.20,
    )
    useful_pair_reward: float = 1.0
    false_purification_penalty: float = 0.25
    missed_useful_pair_penalty: float = 0.50
    wait_penalty: float = 0.01
    purification_attempt_penalty: float = 0.01
    schema_version: str = TASK_POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _finite("fidelity_threshold", self.fidelity_threshold, minimum=0.0, maximum=1.0)
        _finite(
            "max_safety_abstention_rate_pct",
            self.max_safety_abstention_rate_pct,
            minimum=0.0,
            maximum=100.0,
        )
        _finite(
            "max_policy_wait_rate_pct",
            self.max_policy_wait_rate_pct,
            minimum=0.0,
            maximum=100.0,
        )
        if not self.probability_margin_grid:
            raise ValueError("probability_margin_grid cannot be empty")
        object.__setattr__(
            self,
            "probability_margin_grid",
            tuple(self.probability_margin_grid),
        )
        previous = -1.0
        for margin in self.probability_margin_grid:
            checked = _finite(
                "probability_margin_grid item", margin, minimum=0.0, maximum=0.5
            )
            if checked <= previous:
                raise ValueError("probability_margin_grid must be strictly increasing")
            previous = checked
        for name in (
            "useful_pair_reward",
            "false_purification_penalty",
            "missed_useful_pair_penalty",
            "wait_penalty",
            "purification_attempt_penalty",
        ):
            _finite(name, getattr(self, name), minimum=0.0)
        if self.schema_version != TASK_POLICY_SCHEMA_VERSION:
            raise ValueError("unsupported task-policy schema_version")


@dataclass(frozen=True)
class FrozenTaskPolicySpec(JSONSerializableMixin):
    """One controller's immutable policy state fitted without test samples."""

    controller_name: str
    calibration_id: str
    calibration_sha256: str
    calibration_samples: int
    fidelity_threshold: float
    purification_input_threshold: float
    probability_margin: float
    max_prediction_uncertainty: float
    max_safety_abstention_rate_pct: float
    max_policy_wait_rate_pct: float
    useful_pair_reward: float
    false_purification_penalty: float
    missed_useful_pair_penalty: float
    wait_penalty: float
    purification_attempt_penalty: float
    calibration_diagnostics: Mapping[str, Any] = field(default_factory=dict)
    evidence_kind: str = EvidenceKind.SIMULATED.value
    physical_calibration: bool = False
    schema_version: str = TASK_POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "controller_name", _nonempty("controller_name", self.controller_name))
        object.__setattr__(self, "calibration_id", _nonempty("calibration_id", self.calibration_id))
        if not isinstance(self.calibration_sha256, str) or len(self.calibration_sha256) != 64:
            raise ValueError("calibration_sha256 must be a SHA-256 hex digest")
        try:
            int(self.calibration_sha256, 16)
        except ValueError as exc:
            raise ValueError("calibration_sha256 must be a SHA-256 hex digest") from exc
        if (
            isinstance(self.calibration_samples, bool)
            or not isinstance(self.calibration_samples, int)
            or self.calibration_samples < 1
        ):
            raise ValueError("calibration_samples must be a positive integer")
        for name, minimum, maximum in (
            ("fidelity_threshold", 0.0, 1.0),
            ("purification_input_threshold", 0.0, 1.0),
            ("probability_margin", 0.0, 0.5),
            ("max_prediction_uncertainty", 0.0, 1.0),
            ("max_safety_abstention_rate_pct", 0.0, 100.0),
            ("max_policy_wait_rate_pct", 0.0, 100.0),
            ("useful_pair_reward", 0.0, None),
            ("false_purification_penalty", 0.0, None),
            ("missed_useful_pair_penalty", 0.0, None),
            ("wait_penalty", 0.0, None),
            ("purification_attempt_penalty", 0.0, None),
        ):
            _finite(name, getattr(self, name), minimum=minimum, maximum=maximum)
        if self.evidence_kind != EvidenceKind.SIMULATED.value:
            raise ValueError("task-policy calibration currently supports simulated evidence only")
        if self.physical_calibration:
            raise ValueError("task-policy calibration is not physical calibration")
        object.__setattr__(
            self,
            "calibration_diagnostics",
            _deep_freeze(self.calibration_diagnostics),
        )
        if self.schema_version != TASK_POLICY_SCHEMA_VERSION:
            raise ValueError("unsupported task-policy schema_version")

    @property
    def sha256(self) -> str:
        return _canonical_sha256(self.to_dict())


def calibrate_link_policy_spec(
    controller_name: str,
    calibration_id: str,
    mean: Sequence[float],
    uncertainty: Sequence[float],
    actual_fidelity: Sequence[float],
    availability: Sequence[bool | int | float],
    out_of_distribution: Sequence[bool | int | float],
    *,
    config: AbstentionCalibrationConfig | None = None,
) -> FrozenTaskPolicySpec:
    """Fit safety and WAIT thresholds using calibration observations only."""

    cfg = config or AbstentionCalibrationConfig()
    mu = _vector("mean", mean, probability=True)
    sigma = _vector("uncertainty", uncertainty)
    truth = _vector("actual_fidelity", actual_fidelity, probability=True)
    available = _vector("availability", availability).astype(bool)
    ood = _vector("out_of_distribution", out_of_distribution).astype(bool)
    lengths = {len(mu), len(sigma), len(truth), len(available), len(ood)}
    if len(lengths) != 1:
        raise ValueError("calibration vectors must align")
    if (sigma < 0.0).any():
        raise ValueError("uncertainty must be non-negative")
    eligible = available & ~ood
    if not eligible.any():
        raise ValueError("calibration requires at least one available non-OOD pair")

    quantile = 1.0 - cfg.max_safety_abstention_rate_pct / 100.0
    uncertainty_limit = float(
        np.clip(np.quantile(sigma[eligible], quantile, method="higher"), 1e-6, 1.0)
    )
    safe = eligible & (sigma <= uncertainty_limit)
    if not safe.any():  # pragma: no cover - quantile construction guarantees this
        raise ValueError("uncertainty calibration removed every eligible sample")

    input_threshold = _minimum_input_fidelity_for_output_threshold(
        cfg.fidelity_threshold
    )
    p_good = _probability_good(mu, sigma, input_threshold)
    useful_potential, expected_yield = _purification_potential(truth, cfg.fidelity_threshold)
    candidate_rows: list[dict[str, Any]] = []
    safe_count = int(safe.sum())
    for margin in cfg.probability_margin_grid:
        purify = safe & (p_good >= 0.5 + margin)
        halt = safe & (p_good <= 0.5 - margin) & ~purify
        wait = safe & ~(purify | halt)
        false_purification = purify & ~useful_potential
        missed_useful = safe & useful_potential & ~purify
        expected_useful = float(expected_yield[purify].sum())
        net_utility = (
            cfg.useful_pair_reward * expected_useful
            - cfg.false_purification_penalty * int(false_purification.sum())
            - cfg.missed_useful_pair_penalty * int(missed_useful.sum())
            - cfg.wait_penalty * int(wait.sum())
            - cfg.purification_attempt_penalty * int(purify.sum())
        ) / safe_count
        wait_rate = float(wait.sum() / safe_count * 100.0)
        candidate_rows.append(
            {
                "probability_margin": float(margin),
                "net_utility_per_safe_arrival": float(net_utility),
                "policy_wait_rate_pct": wait_rate,
                "purification_attempts": int(purify.sum()),
                "false_purifications": int(false_purification.sum()),
                "missed_useful_pairs": int(missed_useful.sum()),
                "expected_useful_pairs": expected_useful,
                "within_wait_budget": wait_rate <= cfg.max_policy_wait_rate_pct + 1e-12,
            }
        )
    feasible = [row for row in candidate_rows if row["within_wait_budget"]]
    if not feasible:  # A zero margin normally guarantees zero WAIT, but fail closed.
        raise ValueError("no probability margin satisfies the policy WAIT budget")
    selected = max(
        feasible,
        key=lambda row: (
            row["net_utility_per_safe_arrival"],
            -row["policy_wait_rate_pct"],
            -row["probability_margin"],
        ),
    )
    calibration_payload = {
        "controller_name": controller_name,
        "calibration_id": calibration_id,
        "mean": mu.tolist(),
        "uncertainty": sigma.tolist(),
        "actual_fidelity": truth.tolist(),
        "availability": available.astype(int).tolist(),
        "out_of_distribution": ood.astype(int).tolist(),
        "config": cfg.to_dict(),
    }
    safety_rate = float((eligible & ~safe).sum() / int(eligible.sum()) * 100.0)
    diagnostics = {
        "selection_scope": "calibration_only",
        "eligible_available_non_ood": int(eligible.sum()),
        "safe_after_uncertainty": safe_count,
        "calibration_ood_count": int((available & ood).sum()),
        "output_fidelity_threshold": cfg.fidelity_threshold,
        "purification_input_threshold": input_threshold,
        "observed_high_uncertainty_abstention_rate_pct": safety_rate,
        "selected": selected,
        "candidates": candidate_rows,
    }
    return FrozenTaskPolicySpec(
        controller_name=controller_name,
        calibration_id=calibration_id,
        calibration_sha256=_canonical_sha256(calibration_payload),
        calibration_samples=len(mu),
        fidelity_threshold=cfg.fidelity_threshold,
        purification_input_threshold=input_threshold,
        probability_margin=float(selected["probability_margin"]),
        max_prediction_uncertainty=uncertainty_limit,
        max_safety_abstention_rate_pct=cfg.max_safety_abstention_rate_pct,
        max_policy_wait_rate_pct=cfg.max_policy_wait_rate_pct,
        useful_pair_reward=cfg.useful_pair_reward,
        false_purification_penalty=cfg.false_purification_penalty,
        missed_useful_pair_penalty=cfg.missed_useful_pair_penalty,
        wait_penalty=cfg.wait_penalty,
        purification_attempt_penalty=cfg.purification_attempt_penalty,
        calibration_diagnostics=diagnostics,
    )


class TaskConstrainedRepeaterPolicy:
    """Choose only actions that belong to one declared network task."""

    name = "quantum_autopilot.TaskConstrainedRepeaterPolicy"

    def __init__(self, task: ControlTask | str, spec: FrozenTaskPolicySpec) -> None:
        self.task = ControlTask(task)
        if not isinstance(spec, FrozenTaskPolicySpec):
            raise ValueError("spec must be a FrozenTaskPolicySpec")
        self.spec = spec
        self._swapper = WernerStateSwapping()

    @property
    def allowed_actions(self) -> tuple[ControlAction, ...]:
        if self.task is ControlTask.LINK_PURIFICATION:
            return (ControlAction.HALT, ControlAction.WAIT, ControlAction.PURIFY)
        return (ControlAction.HALT, ControlAction.WAIT, ControlAction.SWAP)

    def _base_details(self) -> dict[str, Any]:
        return {
            "control_task": self.task.value,
            "allowed_actions": [action.value for action in self.allowed_actions],
            "policy_spec_sha256": self.spec.sha256,
            "controller_name": self.spec.controller_name,
            "fidelity_threshold": self.spec.fidelity_threshold,
            "purification_input_threshold": self.spec.purification_input_threshold,
            "physical_calibration": False,
        }

    def decide(self, snapshot: NetworkStateSnapshot) -> tuple[ControlAction, dict[str, Any]]:
        if not isinstance(snapshot, NetworkStateSnapshot):
            raise ValueError("snapshot must be a NetworkStateSnapshot")
        pair_count = len(snapshot.available_pair_fidelities)
        details = self._base_details()
        if pair_count == 0:
            return ControlAction.HALT, {
                **details,
                "reason_code": "no_pair_available",
                "prediction_used": self.task is ControlTask.LINK_PURIFICATION,
            }
        if pair_count == 1:
            return ControlAction.WAIT, {
                **details,
                "reason_code": "awaiting_second_task_input_pair",
                "prediction_used": self.task is ControlTask.LINK_PURIFICATION,
            }
        if pair_count != 2:
            return ControlAction.HALT, {
                **details,
                "reason_code": "ambiguous_pair_selection",
                "required_pair_count": 2,
                "actual_pair_count": pair_count,
                "prediction_used": self.task is ControlTask.LINK_PURIFICATION,
            }

        if self.task is ControlTask.LINK_PURIFICATION:
            sigma = max(snapshot.prediction_uncertainty, 1e-6)
            z = (
                self.spec.purification_input_threshold
                - snapshot.predicted_fidelity
            ) / sigma
            p_good = 1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
            upper = 0.5 + self.spec.probability_margin
            lower = 0.5 - self.spec.probability_margin
            if p_good >= upper:
                action = ControlAction.PURIFY
                reason = "predicted_link_pair_eligible"
            elif p_good <= lower:
                action = ControlAction.HALT
                reason = "ordinary_low_predicted_link_fidelity"
            else:
                action = ControlAction.WAIT
                reason = "policy_probability_abstention"
            return action, {
                **details,
                "reason_code": reason,
                "prediction_used": True,
                "prediction_semantics": "future_fidelity_of_each_raw_same_link_pair",
                "output_fidelity_threshold": self.spec.fidelity_threshold,
                "purification_input_threshold": self.spec.purification_input_threshold,
                "predicted_fidelity": snapshot.predicted_fidelity,
                "prediction_uncertainty": snapshot.prediction_uncertainty,
                "probability_good": p_good,
                "probability_halt_boundary": lower,
                "probability_purify_boundary": upper,
                "pair_values_used_for_action": False,
            }

        left, right = snapshot.available_pair_fidelities
        swap = self._swapper.swap(
            {"F_t": left, "success": True},
            {"F_t": right, "success": True},
        )
        expected_fidelity = float(swap["F_t"])
        eligible = bool(swap["success"] and expected_fidelity >= self.spec.fidelity_threshold)
        return (
            ControlAction.SWAP if eligible else ControlAction.HALT,
            {
                **details,
                "reason_code": (
                    "stored_link_pairs_eligible_for_swap"
                    if eligible
                    else "stored_link_pairs_below_swap_threshold"
                ),
                "prediction_used": False,
                "prediction_semantics": None,
                "pair_values_used_for_action": True,
                "input_link_pair_fidelities": [left, right],
                "expected_swapped_fidelity": expected_fidelity,
            },
        )


def evaluate_link_policy_predictions(
    controller_name: str,
    spec: FrozenTaskPolicySpec,
    mean: Sequence[float],
    uncertainty: Sequence[float],
    actual_fidelity: Sequence[float],
    availability: Sequence[bool | int | float],
    out_of_distribution: Sequence[bool | int | float],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Evaluate a frozen link-purification policy with separated abstentions."""

    mu = _vector("mean", mean, probability=True)
    sigma = _vector("uncertainty", uncertainty)
    truth = _vector("actual_fidelity", actual_fidelity, probability=True)
    available = _vector("availability", availability).astype(bool)
    ood = _vector("out_of_distribution", out_of_distribution).astype(bool)
    if len({len(mu), len(sigma), len(truth), len(available), len(ood)}) != 1:
        raise ValueError("evaluation vectors must align")
    if (sigma < 0.0).any():
        raise ValueError("uncertainty must be non-negative")

    policy = TaskConstrainedRepeaterPolicy(ControlTask.LINK_PURIFICATION, spec)
    supervisor = SafetySupervisor(
        SafetyPolicyConfig(max_prediction_uncertainty=spec.max_prediction_uncertainty)
    )
    useful_potential, expected_yield = _purification_potential(truth, spec.fidelity_threshold)
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for index, (mean_value, sigma_value, true_value, pair_available, is_ood) in enumerate(
        zip(mu, sigma, truth, available, ood)
    ):
        pairs = (float(true_value), float(true_value)) if pair_available else ()
        ages = (0.0, 0.0) if pair_available else ()
        snapshot = NetworkStateSnapshot(
            request_id=f"task-policy-evaluation-{controller_name}-{index}",
            node_id="repeater-policy-evaluation",
            timestamp_utc=timestamp,
            state_version=index,
            predicted_fidelity=float(mean_value),
            prediction_uncertainty=float(sigma_value),
            telemetry_age_s=0.0,
            memory_state="STORING" if pair_available else "IDLE",
            available_pair_fidelities=pairs,
            memory_ages_s=ages,
            out_of_distribution=bool(is_ood),
            integrity_verified=True,
            operator_interlock=True,
            provenance=Provenance(
                evidence_kind=EvidenceKind.SIMULATED,
                source="frozen-task-policy-evaluation",
                timestamp=timestamp,
                metadata={
                    "evidence_lane": "bell_fidelity_control",
                    "target_semantics": "simulated_future_bell_fidelity",
                },
            ),
        )
        intended, details = policy.decide(snapshot)
        intent = ControlIntent(
            intent_id=f"task-policy-intent-{controller_name}-{index}",
            request_id=snapshot.request_id,
            node_id=snapshot.node_id,
            action=intended,
            expected_state_version=index,
            issued_at_utc=timestamp,
            policy_name=policy.name,
            policy_details=details,
        )
        safety = supervisor.assess(ControlMode.SHADOW, intent, snapshot)
        effective = safety.effective_action
        if not pair_available:
            outcome_category = "no_pair"
        elif is_ood:
            outcome_category = "safety_ood_abstention"
        elif sigma_value > spec.max_prediction_uncertainty:
            outcome_category = "safety_high_uncertainty_abstention"
        elif effective is ControlAction.WAIT:
            outcome_category = "policy_probability_abstention"
        elif effective is ControlAction.HALT:
            outcome_category = "ordinary_low_fidelity_halt"
        elif effective is ControlAction.PURIFY:
            outcome_category = "purification_attempt"
        else:  # pragma: no cover - task policy action set excludes SWAP
            raise RuntimeError("unexpected link-policy action")
        attempted = effective is ControlAction.PURIFY
        expected_useful = float(expected_yield[index]) if attempted else 0.0
        false_purification = bool(attempted and not useful_potential[index])
        missed_useful = bool(pair_available and useful_potential[index] and not attempted)
        rows.append(
            {
                "index": index,
                "controller": controller_name,
                "control_task": ControlTask.LINK_PURIFICATION.value,
                "predicted_fidelity": float(mean_value),
                "prediction_uncertainty": float(sigma_value),
                "actual_fidelity": float(true_value),
                "pair_available": bool(pair_available),
                "out_of_distribution": bool(is_ood),
                "intended_action": intended.value,
                "effective_action": effective.value,
                "outcome_category": outcome_category,
                "probability_good": details.get("probability_good"),
                "purification_attempted": attempted,
                "false_purification": false_purification,
                "missed_useful_pair": missed_useful,
                "expected_useful_pair": expected_useful,
                "policy_spec_sha256": spec.sha256,
                "physical_action_performed": False,
            }
        )

    arrivals = int(available.sum())
    attempts = sum(int(row["purification_attempted"]) for row in rows)
    false_count = sum(int(row["false_purification"]) for row in rows)
    missed_count = sum(int(row["missed_useful_pair"]) for row in rows)
    waits = sum(row["outcome_category"] == "policy_probability_abstention" for row in rows)
    safety_ood = sum(row["outcome_category"] == "safety_ood_abstention" for row in rows)
    safety_uncertainty = sum(
        row["outcome_category"] == "safety_high_uncertainty_abstention" for row in rows
    )
    ordinary_halts = sum(
        row["outcome_category"] == "ordinary_low_fidelity_halt" for row in rows
    )
    expected_useful = float(sum(row["expected_useful_pair"] for row in rows))
    net_utility = (
        spec.useful_pair_reward * expected_useful
        - spec.false_purification_penalty * false_count
        - spec.missed_useful_pair_penalty * missed_count
        - spec.wait_penalty * waits
        - spec.purification_attempt_penalty * attempts
    ) / max(arrivals, 1)
    action_values = {row["intended_action"] for row in rows if row["pair_available"]}
    return rows, {
        "controller": controller_name,
        "control_task": ControlTask.LINK_PURIFICATION.value,
        "samples": len(rows),
        "pair_arrivals": arrivals,
        "prediction_mae": float(np.mean(np.abs(mu - truth))),
        "prediction_rmse": float(np.sqrt(np.mean((mu - truth) ** 2))),
        "max_prediction_uncertainty": spec.max_prediction_uncertainty,
        "probability_margin": spec.probability_margin,
        "purification_attempts": attempts,
        "false_purification_count": false_count,
        "missed_useful_pair_count": missed_count,
        "expected_useful_pairs": expected_useful,
        "expected_useful_pair_rate_per_arrival_pct": expected_useful
        / max(arrivals, 1)
        * 100.0,
        "net_utility_per_arrival": float(net_utility),
        "policy_wait_count": waits,
        "policy_wait_rate_pct": waits / max(arrivals, 1) * 100.0,
        "safety_ood_abstention_count": safety_ood,
        "safety_ood_abstention_rate_pct": safety_ood / max(arrivals, 1) * 100.0,
        "safety_high_uncertainty_abstention_count": safety_uncertainty,
        "safety_high_uncertainty_abstention_rate_pct": safety_uncertainty
        / max(arrivals, 1)
        * 100.0,
        "ordinary_low_fidelity_halt_count": ordinary_halts,
        "ordinary_low_fidelity_halt_rate_pct": ordinary_halts
        / max(arrivals, 1)
        * 100.0,
        "total_abstention_rate_pct": (waits + safety_ood + safety_uncertainty)
        / max(arrivals, 1)
        * 100.0,
        "intended_action_count": len(action_values),
        "intended_policy_saturated": len(action_values) <= 1,
        "policy_spec_sha256": spec.sha256,
        "physical_action_performed": False,
        "schema_version": TASK_POLICY_SCHEMA_VERSION,
    }


def audit_task_policy_sensitivity(spec: FrozenTaskPolicySpec) -> dict[str, Any]:
    """Prove task separation and response to the inputs each task owns."""

    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()

    def state(
        request_id: str,
        predicted: float,
        uncertainty: float,
        pairs: tuple[float, ...],
    ) -> NetworkStateSnapshot:
        return NetworkStateSnapshot(
            request_id=request_id,
            node_id="sensitivity-repeater",
            timestamp_utc=timestamp,
            state_version=0,
            predicted_fidelity=predicted,
            prediction_uncertainty=uncertainty,
            telemetry_age_s=0.0,
            memory_state="STORING" if pairs else "IDLE",
            available_pair_fidelities=pairs,
            memory_ages_s=tuple(0.0 for _ in pairs),
            integrity_verified=True,
            operator_interlock=True,
            provenance=Provenance(
                evidence_kind=EvidenceKind.SIMULATED,
                source="task-policy-sensitivity-audit",
                timestamp=timestamp,
                metadata={"evidence_lane": "bell_fidelity_control"},
            ),
        )

    link = TaskConstrainedRepeaterPolicy(ControlTask.LINK_PURIFICATION, spec)
    link_actions = [
        link.decide(state(f"link-{index}", mean, sigma, (0.8, 0.8)))[0].value
        for index, (mean, sigma) in enumerate(
            ((0.05, 0.01), (spec.purification_input_threshold, 0.20), (0.99, 0.01))
        )
    ]
    path = TaskConstrainedRepeaterPolicy(ControlTask.PATH_EXTENSION, spec)
    path_cases = ((0.5, 0.5), (0.7, 0.7), (0.9, 0.9))
    path_actions = [
        path.decide(state(f"path-{index}", 0.1, 0.9, pairs))[0].value
        for index, pairs in enumerate(path_cases)
    ]
    invariant_low = path.decide(state("path-invariant-low", 0.01, 0.99, (0.9, 0.9)))[0]
    invariant_high = path.decide(state("path-invariant-high", 0.99, 0.001, (0.9, 0.9)))[0]
    return {
        "policy_spec_sha256": spec.sha256,
        "link_allowed_actions": [action.value for action in link.allowed_actions],
        "path_allowed_actions": [action.value for action in path.allowed_actions],
        "purify_and_swap_mutually_exclusive": (
            ControlAction.SWAP not in link.allowed_actions
            and ControlAction.PURIFY not in path.allowed_actions
        ),
        "link_probe_actions": link_actions,
        "link_prediction_sensitive": len(set(link_actions)) >= 2,
        "path_probe_pair_fidelities": [list(item) for item in path_cases],
        "path_probe_actions": path_actions,
        "path_pair_sensitive": len(set(path_actions)) >= 2,
        "path_prediction_invariant": invariant_low is invariant_high,
        "physical_action_performed": False,
        "schema_version": TASK_POLICY_SCHEMA_VERSION,
    }


__all__ = [
    "TASK_POLICY_SCHEMA_VERSION",
    "AbstentionCalibrationConfig",
    "ControlTask",
    "FrozenTaskPolicySpec",
    "TaskConstrainedRepeaterPolicy",
    "audit_task_policy_sensitivity",
    "calibrate_link_policy_spec",
    "evaluate_link_policy_predictions",
]
