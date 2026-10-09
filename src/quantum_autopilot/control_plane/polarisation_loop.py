"""Supervised closed-loop polarisation compensation, simulation mode only.

This is the control target the Q05 repeater policy never had: every 2 ms a
controller turns the monitored fibre rotation into a correction, an independent
supervisor accepts, rate-limits or refuses that correction, and the approved
command takes effect on the simulated plant one control latency (40 ms) later.
The score is the BB84 QBER the residual rotation implies, not a forecast error.

Loop mechanics are the author's ``run_closed_loop`` (qnet_autopilot
``compensation/closed_loop.py``): one batch of monitoring noise per run, commands
in force between updates, residual ``C(t) R(t)``, and either the ideal actuator
or the author's bounded retarder cascade (``qnet_network``, vendored). With
no supervisor intervention the result is identical to the author's function;
the tests check that. What ORION-Q adds is the contract check before a learned
checkpoint may run, the supervisor between controller and plant, a hot-standby
fallback, and a record that always states ``physical_action_performed=False``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math

import numpy as np

from . import polarisation_plant as plant

TARGET_ID = "polarisation.compensation.v1"
LOOP_SCHEMA = "orion.polarisation-loop-run.v1"
BASELINE_IDS = ("baseline.none", "baseline.persistence", "baseline.alpha_beta")
IDENTITY = np.eye(3)


# --------------------------------------------------------------------------
# SO(3) helpers, transcribed from qnet_autopilot so the baselines are bit-exact
# --------------------------------------------------------------------------

def _rotation_vector(matrix):
    matrix = np.asarray(matrix, dtype=float)
    angle = np.arccos(np.clip((np.trace(matrix) - 1.0) / 2.0, -1.0, 1.0))
    skew = np.array([matrix[2, 1] - matrix[1, 2], matrix[0, 2] - matrix[2, 0], matrix[1, 0] - matrix[0, 1]])
    if angle < 1e-8:
        return 0.5 * skew
    if angle <= np.pi - 1e-5:
        return angle / (2.0 * np.sin(angle)) * skew
    symmetric = (matrix + IDENTITY) / 2.0
    axis = symmetric[:, int(np.argmax(np.diag(symmetric)))]
    norm = np.linalg.norm(axis)
    axis = axis / norm if norm > 0 else np.zeros(3)
    sign = np.sign(np.sum(axis * skew))
    return axis * (1.0 if sign == 0 else sign) * angle


def exp_map(vector):
    angle = float(np.linalg.norm(vector))
    if angle < 1e-12:
        return IDENTITY.copy()
    axis = vector / angle
    cos, sin = np.cos(angle), np.sin(angle)
    x, y, z = axis
    cross = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    return cos * IDENTITY + sin * cross + (1.0 - cos) * np.outer(axis, axis)


def log_map(matrix):
    cosine = (matrix[0, 0] + matrix[1, 1] + matrix[2, 2] - 1.0) / 2.0
    skew = np.array([matrix[2, 1] - matrix[1, 2], matrix[0, 2] - matrix[2, 0], matrix[1, 0] - matrix[0, 1]])
    if cosine >= 1.0 - 1e-12:
        return 0.5 * skew
    if cosine <= -1.0 + 1e-9:
        return _rotation_vector(matrix)
    angle = np.arccos(cosine)
    return angle / (2.0 * np.sin(angle)) * skew


def is_rotation(matrix, tolerance=1e-7):
    matrix = np.asarray(matrix)
    return (matrix.shape == (3, 3) and matrix.dtype.kind == "f" and bool(np.all(np.isfinite(matrix)))
            and np.allclose(matrix @ matrix.T, IDENTITY, atol=tolerance, rtol=0)
            and abs(float(np.linalg.det(matrix)) - 1.0) <= tolerance)


# --------------------------------------------------------------------------
# controllers: each proposes the rotation the fibre will have one latency ahead
# --------------------------------------------------------------------------

class NoCompensation:
    controller_id = "baseline.none"

    def reset(self):
        pass

    def propose(self, time_s, measured, covariates):
        return IDENTITY.copy()


class Persistence:
    controller_id = "baseline.persistence"

    def reset(self):
        pass

    def propose(self, time_s, measured, covariates):
        return np.asarray(measured, dtype=float)


class AlphaBeta:
    """The author's alpha-beta tracker in the tangent space; the baseline that matters."""

    controller_id = "baseline.alpha_beta"

    def __init__(self, alpha, beta, horizon_s=plant.LATENCY_S):
        for name, value in (("alpha", alpha), ("beta", beta)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be in [0, 1]")
        self.alpha, self.beta, self.horizon_s = float(alpha), float(beta), horizon_s
        self.reset()

    def reset(self):
        self._state, self._velocity, self._previous_time = None, np.zeros(3), None

    def propose(self, time_s, measured, covariates):
        measured = np.asarray(measured, dtype=float)
        if self._state is None or self._previous_time is None:
            self._state, self._previous_time = measured, time_s
            return measured
        interval = time_s - self._previous_time
        if interval <= 0:
            return self._state
        predicted = exp_map(self._velocity * interval) @ self._state
        innovation = log_map(measured @ predicted.T)
        self._state = exp_map(self.alpha * innovation) @ predicted
        self._velocity = self._velocity + (self.beta / interval) * innovation
        self._previous_time = time_s
        return exp_map(self._velocity * self.horizon_s) @ self._state


class LearnedCheckpoint:
    """A reviewed qnet compensation checkpoint driven through ORION-Q's replay path."""

    def __init__(self, model_id, archive):
        from quantum_autopilot.model_intake.compensation import CompensationReplayModel

        _, _, self.operating_point, architecture = model_id.split(".")
        self.controller_id = model_id
        self._model = CompensationReplayModel(archive, self.operating_point, architecture)
        self._runs = 0

    def reset(self):
        self._model.reset()
        self._runs += 1

    def propose(self, time_s, measured, covariates):
        result = self._model.predict(time_s, measured, covariates, domain="simulated_offline",
                                     operating_point=self.operating_point, stream_id=f"closed-loop/{self._runs}",
                                     horizon_s=plant.LATENCY_S, monitoring_photons_per_basis=plant.PHOTONS_PER_BASIS)
        return np.asarray(result.predicted, dtype=float)


# --------------------------------------------------------------------------
# contract: a learned checkpoint runs only on the plant it declares
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ContractDecision:
    model_id: str
    operating_point: str
    status: str
    reasons: tuple[str, ...]
    archive_sha256: str | None
    checkpoint_sha256: dict | None = None


def check_checkpoint(model_id, archive, operating_point):
    """ADMITTED only when the checkpoint's own metadata declares exactly this plant and loop."""
    from quantum_autopilot.model_intake.artifacts import COMPENSATION_ROOT
    from quantum_autopilot.model_intake.catalog import OPTIONS

    if operating_point not in plant.OPERATING_POINTS:
        raise ValueError("unknown plant operating point")
    option = next((item for item in OPTIONS if item.model_id == model_id), None)
    if option is None or not model_id.startswith("qnet.compensation.") or option.status != "offline_only":
        return ContractDecision(model_id, operating_point, "REFUSED", ("not_a_reviewed_compensation_checkpoint",), None)
    _, _, point, architecture = model_id.split(".")
    prefix = f"{COMPENSATION_ROOT}{point}/model/{architecture}"
    meta = archive.json(prefix + ".json")
    # Identity of the weights themselves, independent of the container ZIP's bytes.
    identity = {suffix: hashlib.sha256(archive.read(prefix + "." + suffix)).hexdigest() for suffix in ("json", "npz")}
    declared = meta.get("operating_point", {})
    reasons = []
    if point != operating_point:
        reasons.append("checkpoint_regime_differs_from_plant")
    # Exports made before harmonics existed omit the field; their model is the single band (1.0,).
    fibre = declared.get("fibre")
    fibre = {"vibration_harmonics": [1.0], **fibre} if isinstance(fibre, dict) else fibre
    if fibre != plant.OPERATING_POINTS[operating_point].declaration():
        reasons.append("declared_training_fibre_differs_from_plant")
    for key, value in (("control_period_s", plant.CONTROL_PERIOD_S), ("horizon_s", plant.LATENCY_S),
                       ("sample_interval_s", plant.SAMPLE_INTERVAL_S),
                       ("monitoring_photons_per_basis", plant.PHOTONS_PER_BASIS)):
        if declared.get(key) != value:
            reasons.append(f"declared_{key}_differs")
    return ContractDecision(model_id, operating_point, "REFUSED" if reasons else "ADMITTED", tuple(reasons),
                            archive.sha256, identity)


# --------------------------------------------------------------------------
# supervisor: independent of every controller, fail-closed
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PolarisationSupervisorConfig:
    """Declared command limits of the simulated actuator interface, not a measured device.

    The default step limit (pi/2 per 2 ms) sits above the calibrated plant's own
    largest true change per control period (1.24 rad on seed 500, outside the
    campaign's evaluation seeds), so it almost never stops the loop from
    following the fibre; it stops commands no fibre motion could justify.
    """

    max_command_step_rad: float = math.pi / 2
    max_consecutive_faults: int = 8
    rotation_tolerance: float = 1e-7

    def __post_init__(self):
        if (isinstance(self.max_command_step_rad, bool) or not isinstance(self.max_command_step_rad, (int, float))
                or not 0 < self.max_command_step_rad <= math.pi):
            raise ValueError("max_command_step_rad must be in (0, pi]")
        if type(self.max_consecutive_faults) is not int or self.max_consecutive_faults < 1:
            raise ValueError("max_consecutive_faults must be a positive integer")
        if not 0 < self.rotation_tolerance < 1e-3:
            raise ValueError("rotation_tolerance must be small and positive")


@dataclass
class SupervisorLedger:
    commands: int = 0
    accepted: int = 0
    rate_limited: int = 0
    faults: int = 0
    fault_reasons: dict = field(default_factory=dict)
    degraded_at_s: float | None = None
    fallback_commands: int = 0
    initial_alignment: bool = False


class PolarisationSupervisor:
    """Accept, rate-limit or refuse each proposed correction before it reaches the plant.

    * a proposal that raises, is not finite or is not in SO(3) is refused and the
      previous command is held;
    * the first command is the initial alignment and is not rate-limited;
    * afterwards a command may move at most ``max_command_step_rad`` (geodesic)
      from the previous one; larger requests are moved that far along the geodesic;
    * after ``max_consecutive_faults`` refusals the loop degrades, for the rest of
      the run, to the hot-standby fallback. Re-enabling the learned controller is
      a new activation, never automatic.
    """

    def __init__(self, config=None):
        self.config = config or PolarisationSupervisorConfig()

    def run(self, controller, fallback, updates, measured, covariates, fault_steps=frozenset()):
        ledger, previous, consecutive, degraded = SupervisorLedger(), IDENTITY, 0, False
        commands = np.empty((len(updates), 3, 3))
        controller.reset()
        if fallback is not None:
            fallback.reset()
        for step, (time_s, reading, sensors) in enumerate(zip(updates, measured, covariates)):
            ledger.commands += 1
            reason, standby = None, None
            if fallback is not None:
                # The standby tracks every reading so a switch-over needs no warm-up.
                try:
                    standby = fallback.propose(time_s, reading, sensors)
                except (ValueError, ArithmeticError):
                    standby = None
            if degraded:
                proposal = standby
                ledger.fallback_commands += 1
                if standby is None:
                    reason = "fallback_unavailable"
            else:
                try:
                    proposal = controller.propose(time_s, reading, sensors)
                    if step in fault_steps:
                        proposal = np.full((3, 3), np.nan)
                except (ValueError, ArithmeticError) as exc:
                    proposal, reason = None, f"controller_error:{type(exc).__name__}"
            if reason is None and not is_rotation(np.asarray(proposal, dtype=float), self.config.rotation_tolerance):
                reason = "proposal_not_in_so3"
            if reason is not None:
                ledger.faults += 1
                ledger.fault_reasons[reason] = ledger.fault_reasons.get(reason, 0) + 1
                consecutive += 1
                if fallback is not None and consecutive >= self.config.max_consecutive_faults:
                    degraded, ledger.degraded_at_s = True, float(time_s)
                commands[step] = previous
                continue
            consecutive = 0
            command = np.asarray(proposal, dtype=float).T
            if step == 0:
                ledger.initial_alignment = True
            else:
                change = log_map(command @ previous.T)
                size = float(np.linalg.norm(change))
                if size > self.config.max_command_step_rad:
                    command = exp_map(change * (self.config.max_command_step_rad / size)) @ previous
                    ledger.rate_limited += 1
            if not degraded:
                ledger.accepted += 1
            commands[step] = previous = command
        return commands, ledger


# --------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class LoopRecord:
    controller_id: str
    fallback_id: str | None
    operating_point: str
    plant_seed: int
    monitoring_seed: int
    duration_s: float
    summary: dict
    supervisor: dict
    archive_sha256: str | None = None
    fault_steps: tuple[int, ...] = ()
    qber_trace_100ms: tuple[float, ...] = ()
    actuator: dict | None = None
    schema: str = field(default=LOOP_SCHEMA, init=False)
    target_id: str = field(default=TARGET_ID, init=False)
    mode: str = field(default="simulation", init=False)
    evidence: str = field(default="simulated", init=False)
    simulator_action_performed: bool = field(default=True, init=False)
    physical_action_performed: bool = field(default=False, init=False)

    def serialized(self):
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, allow_nan=False, sort_keys=True)


def summarise(qber, residual_angle, key_rate):
    return {"mean_qber": float(np.mean(qber)), "median_qber": float(np.median(qber)),
            "p95_qber": float(np.percentile(qber, 95)),
            "uptime": float(np.mean(qber < plant.BB84_QBER_THRESHOLD)),
            "mean_key_rate_hz": float(np.mean(key_rate)),
            "mean_residual_mrad": float(np.mean(residual_angle) * 1e3),
            "p95_residual_mrad": float(np.percentile(residual_angle, 95) * 1e3)}


def actuator_description(actuator, samples):
    """The bounded device and what it did, as the record states it."""
    config = actuator.config
    return {"model": "qnet_network.compensation.actuator.PolarisationActuator",
            "range_rad": float(config.range_rad), "slew_rate_rad_per_s": float(config.slew_rate_rad_per_s),
            "reset_slew_multiplier": float(config.reset_slew_multiplier), "resets": int(actuator.resets),
            "resetting_fraction": actuator.resetting_steps / max(samples, 1), "travel_rad": float(actuator.travel_rad)}


def run_loop(trajectory, controller, *, monitoring_seed, operating_point, plant_seed, fallback=None,
             supervisor=None, fault_steps=(), archive_sha256=None, actuator=None, return_qber=False):
    """Drive ``controller`` against ``trajectory`` through the supervisor; return a LoopRecord.

    ``actuator`` is the author's bounded device (``qnet_network.compensation.actuator.
    PolarisationActuator``): three X-Y-X retarders with finite slew and travel and an
    endless reset. The approved command in force at each sample is walked through it in
    order, exactly as the author's ``run_closed_loop`` does; only what reaches the fibre
    changes. ``None`` keeps the ideal actuator.

    ``return_qber=True`` also returns the per-sample QBER the record summarises, for key
    accounting on the same run (the author's ``ClosedLoopResult.qber``).
    """
    if type(monitoring_seed) is not int or monitoring_seed < 0:
        raise ValueError("explicit monitoring seed required")
    supervisor = supervisor or PolarisationSupervisor()
    time_s, rotations = trajectory.time_s, trajectory.rotation
    count = len(time_s)
    dt_s = float(time_s[1] - time_s[0])
    stride = max(int(round(plant.CONTROL_PERIOD_S / dt_s)), 1)
    latency = int(round(plant.LATENCY_S / dt_s))
    covariates = trajectory.covariates()
    update_indices = np.arange(0, count, stride)
    measured = plant.measure_rotation(rotations[update_indices], np.random.default_rng(monitoring_seed))

    commands, ledger = supervisor.run(controller, fallback, [float(time_s[i]) for i in update_indices], measured,
                                      covariates[update_indices], frozenset(fault_steps))

    effective = np.minimum(update_indices + latency, count - 1)
    in_force = np.searchsorted(effective, np.arange(count), side="right") - 1
    corrections = np.empty((count, 3, 3))
    uncorrected = in_force < 0
    corrections[uncorrected] = IDENTITY
    corrections[~uncorrected] = commands[in_force[~uncorrected]]
    device = None
    if actuator is not None:
        actuator.reset()
        achieved = np.empty_like(corrections)
        for index in range(count):
            achieved[index] = actuator.apply(corrections[index], dt_s)
        corrections = achieved
        device = actuator_description(actuator, count)
    residual = np.einsum("nij,njk->nik", corrections, rotations)
    qber = plant.bb84_qber(residual)
    summary = summarise(qber, plant.rotation_angle(residual), plant.bb84_secret_key_rate(plant.SIFTED_RATE_HZ, qber))
    block = max(int(round(0.1 / dt_s)), 1)
    usable = count // block * block
    trace = tuple(float(value) for value in qber[:usable].reshape(-1, block).mean(axis=1))
    record = LoopRecord(controller.controller_id, None if fallback is None else fallback.controller_id,
                        operating_point, plant_seed, monitoring_seed, float(count * dt_s), summary, asdict(ledger),
                        archive_sha256, tuple(sorted(fault_steps)), trace, device)
    return (record, qber) if return_qber else record


__all__ = ["AlphaBeta", "BASELINE_IDS", "ContractDecision", "LearnedCheckpoint", "LoopRecord", "NoCompensation",
           "Persistence", "PolarisationSupervisor", "PolarisationSupervisorConfig", "TARGET_ID", "check_checkpoint",
           "run_loop"]
