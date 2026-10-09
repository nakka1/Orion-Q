"""Per-fibre processing of the network engine (design §2.2, steps 1-5).

Nothing in this module is simulated. A ``LinkProcessor`` receives the raw observables
of one fibre for one tick (``sources.LinkInput``) and runs the reviewed inference code
on them — the same code whether the frame was simulated or measured; the hidden truth
a simulated frame carries (``LinkInput.reference``) is never read here.

1. **Polarisation compensation.** Every 2 ms the active controller (a reviewed
   ``qnet.compensation.*`` checkpoint, or ``baseline.alpha_beta``) turns the monitored
   rotation into a proposal; the supervisor of ``polarisation_loop`` accepts, rate-limits
   or refuses it; the approved command takes effect one latency (40 ms) later. The
   supervisor loop is unrolled across ticks with its state carried over; a test checks
   that the commands equal ``PolarisationSupervisor.run`` on the same stream. What is
   reported is the *monitored* residual ``C(t) · R_measured(t)`` — the only residual a
   receiver can know — so it includes the monitor's own shot noise (about 0.15 % QBER
   at 400 photons per basis).
2. **Synchronisation.** One offset estimate per second; the predictor (reviewed ridge or
   the author's ``AlphaBetaTracker``) forecasts the gate position two seconds ahead; the
   residual is the next measured offset minus the gate position in force, and the gate
   efficiency follows from ``qnet_qkd.qkd.timing.gate_efficiency``.
3. **Key rate.** The decoy bounds and the secret key rate of the period are recomputed
   from the published gains and errors (``single_photon_bounds``, ``decoy_key_rate_hz``);
   the transmitter controller (reviewed actor or hold-commissioned) decides the next
   period's settings inside the reviewed action box; the key of a period goes to the
   pool, or is discarded while the attack alarm is held.
4. **Attack detector.** The author's 17 features, causal (each period uses the log up
   to that period only), computed incrementally from a bounded window; the reviewed
   boosting detector with its gate and three-period hysteresis; the QBER-threshold
   alarm at the author's calibration as the baseline.
5. **Estimates.** Total QBER (the session's signal error rate), Werner fidelity
   ``F = 1 − 3·QBER/2`` clipped to [0.25, 1], ``stability`` and ``status``.

``stability`` ∈ [0, 1] is the product of three terms over the last
``LinkPolicy.stability_window`` (30) periods of the running session::

    s_qber = clip(1 − (mean(QBER) + 2·std(QBER)) / 0.11, 0, 1)      0.11 = BB84 threshold
    s_sync = clip(mean(gate efficiency) / gate efficiency at zero offset, 0, 1)
    s_pol  = clip(mean(1 − 2·QBER_polarisation), 0, 1)                monitored visibility

It falls when the QBER rises or fluctuates, when the gate drifts off the pulses, and
when the compensator leaves a larger residual. It is a display indicator, not a
security quantity.

``status``: ``compromised`` while the sustained attack alarm is held (raised by three equal
consecutive attack calls, released after ``LinkPolicy.alarm_clear_periods`` = 30 periods
without one — the detector's own feature window); else ``down`` when
the period yields no secret key or the QBER reaches 11 %; else ``degraded`` (a sustained
non-attack event class, the QBER-threshold alarm, a controller in supervisor fallback or
faulting, gate efficiency below 90 % of its ideal, key rate below half the commissioning
calibration, or monitored polarisation QBER at 11 %); else ``up``. ``disabled``,
``isolated`` and ``calibrating`` are imposed by the caller; under them no observable is
published (metrics ``None``, empty wave). Without input the status is ``no_input``.

The three control loops read three independent author plants (see ``sources``): the
polarisation residual and the gate efficiency are *not* fed into the session's error
rate. They are reported side by side and combined only in ``stability`` and ``status``.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping

import numpy as np

from qnet_qkd.physics.decoy import DecoyObservables, decoy_key_rate_hz, single_photon_bounds
from qnet_qkd.qkd.detection import (FEATURE_NAMES, QberThreshold, _log_ratio, _rolling_correlation, _rolling_slope,
                                    _rolling_std)
from qnet_qkd.qkd.keyrate import ACTIONS
from qnet_qkd.qkd.timing import gate_efficiency

from ..control_plane import keyrate_loop as kr
from ..control_plane import polarisation_loop as pol
from ..control_plane import polarisation_plant as plant
from ..model_intake import detector_session
from ..model_intake.keyrate import LinkCalibration, PeriodObservables, TransmitterSettings
from . import models as mdl
from .sources import ATTACK_KINDS, InputFrame, LinkCommand, LinkInput
from .state import IMPOSED_LINK_STATUSES, Decision, DetectorLive, LinkHistory, LinkLive, QkdLive, canonical_sha256

_FLOOR = 1e-12                       # qnet_qkd.qkd.detection._FLOOR
_IDENTITY = np.eye(3)


# --------------------------------------------------------------------------
# configuration and small records
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class LinkPolicy:
    """Declared thresholds of the per-fibre estimates (none of them is a physical model)."""

    wave_hz: int = 20
    wave_seconds: int = 12
    history_seconds: int = 300
    stability_window: int = 30
    qber_down: float = plant.BB84_QBER_THRESHOLD          # 0.11
    degraded_key_fraction: float = 0.5
    degraded_gate_fraction: float = 0.9
    #: Periods without a sustained attack call before a held alarm is released. One full feature
    #: window of the detector (30 periods): its trend and volatility features still contain the
    #: attack for that long, and the detector is observed to call the attack class again some
    #: 13 periods after an intercept-resend attack has ended.
    alarm_clear_periods: int = 30
    keyrate_max_faults: int = kr.KeyRateSupervisorConfig().max_faults
    sync_max_faults: int = 8
    polarisation: pol.PolarisationSupervisorConfig = field(default_factory=pol.PolarisationSupervisorConfig)
    operating_point: str = "calibrado"

    def __post_init__(self):
        for name in ("wave_hz", "wave_seconds", "history_seconds", "stability_window", "alarm_clear_periods",
                     "keyrate_max_faults", "sync_max_faults"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.operating_point not in plant.OPERATING_POINTS:
            raise ValueError("unknown plant operating point")


@dataclass(frozen=True)
class StepControls:
    """What the caller states about one tick of one fibre.

    ``flag``: ``None`` or one of ``disabled | isolated | calibrating`` (imposed status).
    ``domain``: the evidence domain handed to the reviewed replay models; they admit
    ``simulated_offline`` only and refuse anything else by their own contract.
    """

    tick: int
    at_utc: str
    network_time_s: float
    flag: str | None = None
    domain: str = mdl.OFFLINE_DOMAIN

    def __post_init__(self):
        if self.flag is not None and self.flag not in IMPOSED_LINK_STATUSES:
            raise ValueError(f"flag must be None or one of {IMPOSED_LINK_STATUSES}")


@dataclass(frozen=True)
class LinkMeasurement:
    """What the processing last measured on a fibre (also while it is ``calibrating``).

    For the before/after report of a recalibration. ``period`` counts settling periods
    since the session was (re)commissioned; a field is ``None`` until it was measured.
    """

    link_id: str
    tick: int
    epoch_tick: int
    period: int
    distance_km: float
    qber: float | None
    key_rate_hz: float | None
    sync_residual_ps: float | None
    gate_efficiency: float | None
    polarisation_residual_mrad: float | None
    polarisation_qber: float | None
    signal_intensity: float | None
    decoy_intensity: float | None
    clock_rate_hz: float | None
    status: str

    def to_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class BankStep:
    """Result of one tick over all fibres."""

    links: dict                    # link_id -> LinkLive
    decisions: tuple               # Decision records of this tick, in link order
    commands: dict                 # link_id -> LinkCommand, for ``InputSource.actuate``


def domain_of(kind: str) -> str:
    """Evidence domain of a frame kind, as the reviewed replay models name it."""
    return mdl.OFFLINE_DOMAIN if kind == "simulated" else str(kind)


def werner_fidelity(qber: float) -> float:
    """``F = 1 − 3·QBER/2`` (Werner state with that error rate), clipped to [0.25, 1]."""
    return float(min(max(1.0 - 1.5 * float(qber), 0.25), 1.0))


# --------------------------------------------------------------------------
# 1. polarisation: the supervisor loop of polarisation_loop, unrolled across ticks
# --------------------------------------------------------------------------

def _rotations_ok(matrices: np.ndarray, tolerance: float) -> np.ndarray:
    """``polarisation_loop.is_rotation`` for a stack of proposals."""
    finite = np.isfinite(matrices).all(axis=(1, 2))
    safe = np.where(finite[:, None, None], matrices, _IDENTITY)
    gram = safe @ np.transpose(safe, (0, 2, 1))
    orthogonal = (np.abs(gram - _IDENTITY) <= tolerance).all(axis=(1, 2))
    proper = np.abs(np.linalg.det(safe) - 1.0) <= tolerance
    return finite & orthogonal & proper


class PolarisationChannel:
    """Controller + supervisor + latency for one fibre; state survives between ticks."""

    def __init__(self, controller, fallback, config: pol.PolarisationSupervisorConfig | None = None):
        self.controller, self.fallback = controller, fallback
        self.config = config or pol.PolarisationSupervisorConfig()
        self.reset()

    def reset(self):
        self.controller.reset()
        if self.fallback is not None:
            self.fallback.reset()
        self.ledger = pol.SupervisorLedger()
        self.previous, self.consecutive, self.degraded = _IDENTITY.copy(), 0, False
        self.steps = 0
        self.next_time_s = None
        self._in_flight = None           # commands issued but not yet in force (one latency)

    @property
    def acting_id(self) -> str:
        return self.fallback.controller_id if self.degraded and self.fallback is not None \
            else self.controller.controller_id

    def _propose(self, times, measured, covariates):
        """Ask the controller (and the standby) for every control step of the tick."""
        count = len(times)
        proposals, reasons = np.full((count, 3, 3), np.nan), [None] * count
        standby = None if self.fallback is None else np.full((count, 3, 3), np.nan)
        controller, fallback, degraded = self.controller, self.fallback, self.degraded
        from .baseline_worker import batch_proposals
        candidates = {}
        if fallback is not None:
            candidates["fallback"] = fallback
        if not degraded:
            candidates["controller"] = controller
        batched = batch_proposals(candidates, times, measured, covariates) or {}
        for step in range(count):
            time_s, reading, sensors = float(times[step]), measured[step], covariates[step]
            if fallback is not None:
                # The standby tracks every reading so a switch-over needs no warm-up.
                try:
                    standby[step] = (batched["fallback"][0][step] if "fallback" in batched
                                     else fallback.propose(time_s, reading, sensors))
                except (ValueError, ArithmeticError):
                    pass
            if not degraded:
                try:
                    if "controller" in batched:
                        proposals[step], reasons[step] = batched["controller"][0][step], batched["controller"][1][step]
                    else:
                        proposals[step] = controller.propose(time_s, reading, sensors)
                except (ValueError, ArithmeticError) as exc:
                    reasons[step] = f"controller_error:{type(exc).__name__}"
        return proposals, standby, reasons

    def _supervise(self, times, proposals, standby, reasons):
        """``PolarisationSupervisor.run``, one step at a time, with the state carried over."""
        config, ledger = self.config, self.ledger
        commands = np.empty((len(times), 3, 3))
        for step, time_s in enumerate(times):
            ledger.commands += 1
            reason = None
            if self.degraded:
                proposal = standby[step]
                ledger.fallback_commands += 1
                if not np.isfinite(proposal).all():
                    reason = "fallback_unavailable"
            else:
                proposal, reason = proposals[step], reasons[step]
            if reason is None and not pol.is_rotation(np.asarray(proposal, dtype=float), config.rotation_tolerance):
                reason = "proposal_not_in_so3"
            if reason is not None:
                ledger.faults += 1
                ledger.fault_reasons[reason] = ledger.fault_reasons.get(reason, 0) + 1
                self.consecutive += 1
                if self.fallback is not None and self.consecutive >= config.max_consecutive_faults \
                        and not self.degraded:
                    self.degraded, ledger.degraded_at_s = True, float(time_s)
                commands[step] = self.previous
                self.steps += 1
                continue
            self.consecutive = 0
            command = np.asarray(proposal, dtype=float).T
            if self.steps == 0:
                ledger.initial_alignment = True
            else:
                change = pol.log_map(command @ self.previous.T)
                size = float(np.linalg.norm(change))
                if size > config.max_command_step_rad:
                    command = pol.exp_map(change * (config.max_command_step_rad / size)) @ self.previous
                    ledger.rate_limited += 1
            if not self.degraded:
                ledger.accepted += 1
            commands[step] = self.previous = command
            self.steps += 1
        return commands

    def step(self, polarisation) -> dict:
        times, measured = np.asarray(polarisation.time_s, dtype=float), np.asarray(polarisation.measured, dtype=float)
        covariates = np.asarray(polarisation.covariates, dtype=float)
        count = len(times)
        if count < 1 or measured.shape != (count, 3, 3) or covariates.shape != (count, 3):
            raise ValueError("polarisation input must hold n rotations and n covariate rows")
        if self.next_time_s is not None and abs(float(times[0]) - self.next_time_s) > 1e-6:
            raise ValueError("gap in the polarisation monitor stream")
        period = float(polarisation.control_period_s)
        latency = int(round(float(polarisation.latency_s) / period))
        before = (self.ledger.faults, self.ledger.rate_limited, self.degraded, self.ledger.initial_alignment)
        proposals, standby, reasons = self._propose(times, measured, covariates)

        fast = not self.degraded and all(reason is None for reason in reasons) \
            and bool(_rotations_ok(proposals, self.config.rotation_tolerance).all())
        if fast:
            commands = np.transpose(proposals, (0, 2, 1))
            chain = np.concatenate([self.previous[None], commands])
            steps = plant.rotation_angle(chain[1:] @ np.transpose(chain[:-1], (0, 2, 1)))
            if self.steps == 0:
                steps[0] = 0.0            # the first command is the initial alignment
            fast = bool(steps.max() <= self.config.max_command_step_rad * (1.0 - 1e-9))
        if fast:
            ledger = self.ledger
            ledger.commands += count
            ledger.accepted += count
            if self.steps == 0:
                ledger.initial_alignment = True
            self.consecutive, self.previous = 0, commands[-1].copy()
            self.steps += count
        else:
            commands = self._supervise(times, proposals, standby, reasons)

        # Commands take effect one latency later; before the first one lands the fibre is uncorrected.
        if self._in_flight is None:
            self._in_flight = np.broadcast_to(_IDENTITY, (latency, 3, 3)).copy()
        issued = np.concatenate([self._in_flight, commands])
        in_force, self._in_flight = issued[:count], issued[count:]
        residual = in_force @ measured
        qber = plant.bb84_qber(residual)
        angle = plant.rotation_angle(residual)
        self.next_time_s = float(times[-1]) + period
        ledger = self.ledger
        return {"qber": qber, "residual_mrad": float(np.mean(angle) * 1e3), "mean_qber": float(np.mean(qber)),
                "faults": ledger.faults - before[0], "rate_limited": ledger.rate_limited - before[1],
                "fallback_entered": self.degraded and not before[2],
                "initial_alignment": ledger.initial_alignment and not before[3],
                "degraded": self.degraded, "fault_reasons": dict(ledger.fault_reasons), "commands": commands}


# --------------------------------------------------------------------------
# 2. synchronisation
# --------------------------------------------------------------------------

class SyncChannel:
    """Gate predictor with a hot standby; the forecast takes effect one latency later."""

    def __init__(self, predictor, standby, controller_id: str, standby_id: str, max_faults: int = 8):
        self.predictor, self.standby = predictor, standby
        self.controller_id, self.standby_id, self.max_faults = controller_id, standby_id, int(max_faults)
        self.reset()

    def reset(self):
        self.predictor.reset()
        if self.standby is not None:
            self.standby.reset()
        self.faults = self.consecutive = self.updates = 0
        self.degraded = False
        self._pending = deque()          # (effective_time_s, gate position)
        self.gate_position = None
        self.last_prediction = None
        self.fault_reasons: dict = {}

    @property
    def acting_id(self) -> str:
        return self.standby_id if self.degraded and self.standby is not None else self.controller_id

    def step(self, sync, gate_width_s: float, spread_s: float) -> dict:
        time_s, measured, temperature = float(sync.time_s), float(sync.measured_offset_s), float(sync.temperature_c)
        horizon = float(sync.latency_s)
        while self._pending and self._pending[0][0] <= time_s + 1e-9:
            self.gate_position = self._pending.popleft()[1]
        acquired = self.gate_position is not None
        residual = measured - (self.gate_position if acquired else 0.0)
        efficiency = float(gate_efficiency(residual, gate_width_s, spread_s))
        before, entered = self.faults, False
        backup = None
        if self.standby is not None:
            try:
                backup = float(self.standby.predict(time_s, measured, temperature, horizon))
            except (ValueError, ArithmeticError):
                backup = None
        prediction, reason = None, None
        if self.degraded:
            prediction = backup
        else:
            try:
                prediction = float(self.predictor.predict(time_s, measured, temperature, horizon))
            except (ValueError, ArithmeticError) as exc:
                reason = f"predictor_error:{type(exc).__name__}"
            if reason is None and not math.isfinite(prediction):
                reason = "prediction_not_finite"
            if reason is not None:
                self.faults += 1
                self.consecutive += 1
                self.fault_reasons[reason] = self.fault_reasons.get(reason, 0) + 1
                prediction = backup
                if self.standby is not None and self.consecutive >= self.max_faults:
                    self.degraded = entered = True
            else:
                self.consecutive = 0
        if prediction is None or not math.isfinite(prediction):
            prediction = self.last_prediction          # hold the previous forecast
        first = self.last_prediction is None and prediction is not None
        if prediction is not None:
            self._pending.append((time_s + horizon, prediction))
            self.last_prediction = prediction
        self.updates += 1
        return {"residual_s": residual, "efficiency": efficiency, "acquired": acquired, "first_update": first,
                "faults": self.faults - before, "fallback_entered": entered, "degraded": self.degraded,
                "prediction_s": prediction}


# --------------------------------------------------------------------------
# 4. detector: the author's causal features from a bounded window
# --------------------------------------------------------------------------

_LEVELS = ("signal_gain", "decoy_gain", "vacuum_gain", "signal_error", "decoy_error", "vacuum_error",
           "single_photon_yield_lower", "single_photon_error_upper", "key_rate_hz", "temperature_c")


class CausalFeatures:
    """The detector's 17 features of the newest period, computed from the log up to it.

    Equal — bit for bit, a test checks it — to the last row of
    ``qnet_qkd.qkd.detection._episode_features(frame[:t + 1], window)``, which is what
    ``control_plane.detector_causal.causal_features`` computes for every period. That
    reference recomputes the whole growing session each period; here the baseline
    medians are fixed once the first ``baseline_periods`` rows exist and only the last
    ``window + 1`` rows are kept, so the cost per period is constant. The arithmetic
    is the author's own (``_log_ratio``, ``_rolling_slope``, ``_rolling_std``,
    ``_rolling_correlation`` and ``numpy.gradient``).
    """

    def __init__(self, window: int = 30, baseline_periods: int = 60):
        if window < 3 or baseline_periods < window:
            raise ValueError("window of three or more inside the baseline required")
        self.window, self.baseline_periods = int(window), int(baseline_periods)
        self.reset()

    def reset(self):
        self.count = 0
        self.levels = None
        self._baseline = {name: [] for name in _LEVELS}
        self._tail = deque(maxlen=self.window + 1)       # (signal_gain, signal_error, temperature_c)
        self._last_time = self._period_s = None

    @property
    def ready(self) -> bool:
        return self.count > self.baseline_periods

    def push(self, row: Mapping[str, float]):
        """Add one period; returns its feature vector, or ``None`` inside the baseline."""
        time_s = float(row["time_s"])
        if self._last_time is not None:
            interval = time_s - self._last_time
            if self._period_s is None:
                self._period_s = interval
            if interval <= 0 or abs(interval - self._period_s) > 1e-9:
                raise ValueError("session log is not contiguous")
        self._last_time = time_s
        if self.count < self.baseline_periods:
            for name in _LEVELS:
                self._baseline[name].append(float(row[name]))
            if self.count + 1 == self.baseline_periods:
                self.levels = {name: float(np.median(np.asarray(values))) for name, values in self._baseline.items()}
                self._baseline = None
        self._tail.append((float(row["signal_gain"]), float(row["signal_error"]), float(row["temperature_c"])))
        self.count += 1
        if self.count <= self.baseline_periods:
            return None
        level, window, period_s = self.levels, self.window, float(self._period_s)
        tail = np.asarray(self._tail, dtype=float)
        signal_gains, signal_errors = tail[-window:, 0], tail[-window:, 1]
        temperature_rate = np.gradient(tail[:, 2], period_s)[-window:]
        signal_gain, decoy_gain = float(row["signal_gain"]), float(row["decoy_gain"])
        baseline_ratio = level["decoy_gain"] / max(level["signal_gain"], _FLOOR)
        ratio = decoy_gain / max(signal_gain, _FLOOR)
        columns = {
            "log_signal_gain_ratio": _log_ratio(signal_gain, level["signal_gain"]),
            "log_decoy_gain_ratio": _log_ratio(decoy_gain, level["decoy_gain"]),
            "decoy_to_signal_shift": ratio / max(baseline_ratio, _FLOOR) - 1.0,
            "signal_error_shift": float(row["signal_error"]) - level["signal_error"],
            "decoy_error_shift": float(row["decoy_error"]) - level["decoy_error"],
            "log_vacuum_gain_ratio": _log_ratio(float(row["vacuum_gain"]), level["vacuum_gain"]),
            "vacuum_error_shift": float(row["vacuum_error"]) - level["vacuum_error"],
            "log_single_photon_yield_ratio": _log_ratio(float(row["single_photon_yield_lower"]),
                                                        level["single_photon_yield_lower"]),
            "single_photon_error_shift": float(row["single_photon_error_upper"])
            - level["single_photon_error_upper"],
            "log_key_rate_ratio": _log_ratio(float(row["key_rate_hz"]), level["key_rate_hz"]),
            "log_gain_versus_calibration": _log_ratio(signal_gain, 1.0) - _log_ratio(float(row["expected_gain"]), 1.0),
            "temperature_shift": float(row["temperature_c"]) - level["temperature_c"],
            "temperature_rate": temperature_rate[-1],
            "error_temperature_correlation": _rolling_correlation(signal_errors, np.abs(temperature_rate),
                                                                  window)[-1],
            "error_trend": _rolling_slope(signal_errors, window)[-1] * period_s,
            "gain_trend": _rolling_slope(_log_ratio(signal_gains, level["signal_gain"]), window)[-1] * period_s,
            "error_volatility": _rolling_std(signal_errors, window)[-1],
        }
        return np.array([float(columns[name]) for name in FEATURE_NAMES], dtype=np.float64)


# --------------------------------------------------------------------------
# the processor of one fibre
# --------------------------------------------------------------------------

class LinkProcessor:
    """All the processing of one fibre. Stateful; one instance per fibre.

    ``step(link_input, controls=...)`` processes one tick and returns
    ``(LinkLive, [Decision, ...])``. ``link_input=None`` means the source delivered
    nothing for this fibre. ``begin`` / ``finish`` are the two halves of ``step`` so a
    ``LinkBank`` can evaluate the detector once for all fibres.
    """

    def __init__(self, link_id: str, a: str, b: str, distance_km: float, registry: mdl.ModelRegistry | None = None,
                 *, policy: LinkPolicy | None = None, seed: int = 0):
        self.link_id, self.a, self.b, self.distance_km = str(link_id), str(a), str(b), float(distance_km)
        self.registry = registry if registry is not None else mdl.ModelRegistry()
        self.policy = policy or LinkPolicy()
        self.seed = int(seed)
        self.key_pool_bits = 0.0
        self.discarded_bits = 0.0
        self._wave = deque(maxlen=self.policy.wave_hz * self.policy.wave_seconds)
        self._history = deque(maxlen=self.policy.history_seconds)
        self._had_data = False
        self._measurement = None
        self.last: LinkLive | None = None
        self._channels_domain = None
        self._control_input = None
        self._reactivated = False
        self._pending = None
        #: Transmitter command decided in the last tick (``LinkCommand`` or ``None``); the
        #: caller hands it to ``InputSource.actuate``.
        self.last_command = None
        self._clear_session()

    # -- lifecycle ------------------------------------------------------------------

    def _clear_session(self):
        self.registry.release(self.link_id)
        self.epoch_tick = None
        self.polarisation = self.sync = self.keyrate = None
        self.features = None
        self._calls = None
        self._qber_alarm = QberThreshold(sigma=detector_session.AUTHOR_QBER_SIGMA)
        self.alarm_held, self._quiet = False, 0
        self._recent_bits = deque(maxlen=8)
        self._window = deque(maxlen=self.policy.stability_window)
        self._proposal = None
        self._previous_action = 0
        self._keyrate_faults, self._keyrate_degraded = 0, False
        self._baseline_settings = None
        self._last_period = None
        self._stale = False

    def reset(self, distance_km: float | None = None) -> None:
        """Recommission the processing of this fibre: controllers, gate and detector
        baseline start again and a held alarm is released (the key pool, a stock, is
        kept). ``distance_km`` adopts a new length. A controller the supervisor had
        dropped is activated anew."""
        dropped = bool(self.polarisation is not None and self.polarisation.degraded) \
            or bool(self.sync is not None and self.sync.degraded) or self._keyrate_degraded
        if distance_km is not None:
            self.distance_km = float(distance_km)
        self._clear_session()
        self._wave.clear()
        self._reactivated = dropped

    def _build_controls(self, link_input: LinkInput, domain: str):
        """Controllers for this session. A domain the reviewed models do not admit
        (anything but simulated_offline) leaves the named baselines in charge."""
        registry, policy, span = self.registry, self.policy, self.distance_km
        offline = domain == mdl.OFFLINE_DOMAIN
        point = link_input.polarisation.operating_point

        controller, status = registry.compensator(point, subject=self.link_id)
        fallback = registry.baseline_compensator() if status.state == "active" else None
        if status.state == "active" and not offline:
            controller, fallback = registry.baseline_compensator(), None
            status = replace(status, state="fallback", in_contract=False,
                             reason=f"evidence domain {domain!r} not admitted by the checkpoint; "
                                    f"{mdl.COMPENSATION_BASELINE_ID} acting")
            registry.report("compensation", self.link_id, "fallback", status.reason, False)
        self.polarisation = PolarisationChannel(controller, fallback, policy.polarisation)
        self._status["compensation"] = status

        predictor, status = registry.synchroniser(span, subject=self.link_id)
        standby = registry.baseline_synchroniser() if status.state == "active" else None
        if status.state == "active" and not offline:
            predictor, standby = registry.baseline_synchroniser(), None
            status = replace(status, state="fallback", in_contract=False,
                             reason=f"evidence domain {domain!r} not admitted by the synchroniser; "
                                    f"{mdl.SYNC_BASELINE_ID} acting")
            registry.report("sync", self.link_id, "fallback", status.reason, False)
        self.sync = SyncChannel(predictor, standby, mdl.SYNC_MODEL_ID if status.state == "active"
                                else mdl.SYNC_BASELINE_ID, mdl.SYNC_BASELINE_ID, policy.sync_max_faults)
        self._status["sync"] = status

        seed = int.from_bytes(bytes.fromhex(canonical_sha256(["orion.keyrate-policy.v1", self.seed, self.link_id,
                                                              int(link_input.epoch_tick)])[:16]), "big") >> 1
        actor, status = registry.keyrate_actor(span, subject=self.link_id, seed=seed)
        if status.state == "active" and not offline:
            actor = mdl.StaticKeyRate()
            status = replace(status, state="fallback", in_contract=False,
                             reason=f"evidence domain {domain!r} not admitted by the actor; "
                                    f"{mdl.KEYRATE_BASELINE_ID} acting")
            registry.report("keyrate", self.link_id, "fallback", status.reason, False)
        self.keyrate = actor
        self._status["keyrate"] = status

    def rebind_controls(self):
        """Change control policy without releasing alarms, keys, history or detector baseline."""
        if self._control_input is not None and self.polarisation is not None:
            self._build_controls(self._control_input, self._channels_domain)
            self._keyrate_faults, self._keyrate_degraded = 0, False
            self._proposal = None
            self.last_command = None

    def _build(self, link_input: LinkInput, domain: str):
        self._status = {}
        self._build_controls(link_input, domain)
        registry, span = self.registry, self.distance_km

        self.detector, status = registry.detector(span, subject=self.link_id)
        note = next((item for item in link_input.domain_notes if item.component == "detector"), None)
        if note is not None and not note.in_domain:
            status = replace(status, in_contract=False, reason=status.reason.split("; span")[0] + "; " + note.detail)
            registry.report("detector", self.link_id, "active", status.reason, False)
        self._status["detector"] = status
        self.features = CausalFeatures(self.detector.window, self.detector.baseline_periods)
        self._calls = deque(maxlen=self.detector.sustain)
        self._attack_classes = tuple(label for label in self.detector.classes if label in ATTACK_KINDS)
        self._channels_domain = domain
        self.epoch_tick = int(link_input.epoch_tick)

    # -- one tick ------------------------------------------------------------------

    def step(self, link_input: LinkInput | None, *, controls: StepControls):
        """Process one tick of this fibre: ``(LinkLive, [Decision, ...])``."""
        features = self.begin(link_input, controls)
        outcome = None
        if features is not None:
            started = mdl.clock_ms()
            block = features[None, :]
            outcome = (self.detector.probabilities(block)[0], str(self.detector.predict(block)[0]))
            self.registry.note("detector", inferences=1, latency_ms=mdl.clock_ms() - started)
        return self.finish(outcome)

    def begin(self, link_input: LinkInput | None, controls: StepControls):
        """First half of a tick: control loops and key accounting inputs.

        Returns the detector feature row of the period, or ``None`` when there is none
        (no input, imposed flag other than ``calibrating``, or session still in its baseline).
        """
        if not isinstance(controls, StepControls):
            raise ValueError("StepControls required")
        self._pending = pending = {"controls": controls, "input": link_input, "decisions": [], "features": None,
                                   "processed": False}
        flag = controls.flag
        if link_input is None or flag in ("disabled", "isolated"):
            if link_input is not None:
                self._stale = True        # input ignored: the control streams now have a gap
            return None
        if link_input.link_id != self.link_id:
            raise ValueError("input of another fibre")
        self._control_input = link_input
        if (self.epoch_tick != int(link_input.epoch_tick) or self._stale
                or float(link_input.distance_km) != self.distance_km or self._channels_domain != controls.domain
                or (self._last_period is not None and int(link_input.period) != self._last_period + 1)):
            # A new session that nobody asked this processor for (a scheduled recommissioning of the
            # link, a reactivation): a held attack alarm is not released by it. Only an explicit
            # ``reset`` (recalibration) or the detector itself releases an alarm.
            reactivated, held = self._reactivated, self.alarm_held and \
                float(link_input.distance_km) == self.distance_km
            self.reset(float(link_input.distance_km))
            self._reactivated = self._reactivated or reactivated
            self.alarm_held = held
            self._build(link_input, controls.domain)
        self._last_period = int(link_input.period)
        pending["processed"] = True
        pending["domain"] = {note.component: note for note in link_input.domain_notes}
        self._polarisation_step(link_input, pending)
        self._sync_step(link_input, pending)
        self._qkd_step(link_input, pending)
        try:
            pending["features"] = self.features.push(link_input.qkd.row)
        except (ValueError, KeyError) as exc:
            # A gap in the session log: the detector's baseline is no longer this session's.
            self.features.reset()
            self._calls.clear()
            self.registry.note("detector", faults=1)
            pending["detector_fault"] = f"{type(exc).__name__}: {exc}"
        return pending["features"]

    # -- step 1 ----------------------------------------------------------------------

    def _decision(self, pending, kind, controller_id, role, summary, inputs_sha256, outcome, *, fallback=None,
                  in_contract=None):
        controls, status = pending["controls"], self._status[role]
        state = status.state if fallback is None else ("fallback" if fallback else status.state)
        note = pending["domain"].get(role)
        if in_contract is None and state != "active":
            in_contract = True            # a named baseline is acting: there is no model contract to be outside of
        elif in_contract is None:
            in_contract = status.in_contract is not False and (note is None or bool(note.in_domain))
        pending["decisions"].append(Decision.create(
            tick=controls.tick, at_utc=controls.at_utc, kind=kind, controller_id=controller_id, model_state=state,
            subject=self.link_id, summary=summary, inputs_sha256=inputs_sha256, outcome=outcome,
            in_contract=bool(in_contract), fallback_used=state != "active"))

    def _polarisation_step(self, link_input, pending):
        channel, started = self.polarisation, mdl.clock_ms()
        learned = self._status["compensation"].state == "active" and not channel.degraded
        try:
            result = channel.step(link_input.polarisation)
        except ValueError as exc:
            pending["polarisation_error"] = str(exc)
            pending["polarisation"] = None
            self._stale = True
            return
        count = len(link_input.polarisation.time_s)
        if learned:
            self.registry.note("compensation", inferences=count, faults=result["faults"],
                               latency_ms=(mdl.clock_ms() - started) / max(count, 1))
        pending["polarisation"] = result
        if not (result["initial_alignment"] or result["fallback_entered"] or result["faults"]
                or result["rate_limited"] or self._reactivated):
            return
        digest = mdl.digest(link_input.polarisation.measured, link_input.polarisation.covariates)
        numbers = (f"monitored residual {result['residual_mrad']:.0f} mrad, polarisation QBER "
                   f"{100 * result['mean_qber']:.2f} %")
        if self._reactivated and result["initial_alignment"]:
            self._reactivated = False
            self._decision(pending, "compensation", channel.controller.controller_id, "compensation",
                           f"controller activated anew after recommissioning; {numbers}", digest,
                           "fallback_left")
        elif result["initial_alignment"]:
            self._decision(pending, "compensation", channel.controller.controller_id, "compensation",
                           f"initial alignment after commissioning; {numbers}", digest, "initial_alignment")
        if result["fallback_entered"]:
            reason = (f"supervisor dropped {channel.controller.controller_id} after "
                      f"{channel.config.max_consecutive_faults} consecutive refused proposals "
                      f"({result['fault_reasons']}); standby {channel.fallback.controller_id} acting")
            self.registry.report("compensation", self.link_id, "fallback", reason)
            self._decision(pending, "compensation", channel.fallback.controller_id, "compensation",
                           f"{reason}; {numbers}", digest, "fallback_entered", fallback=True)
        elif result["faults"] or result["rate_limited"]:
            self._decision(pending, "compensation", channel.acting_id, "compensation",
                           f"supervisor refused {result['faults']} and rate-limited {result['rate_limited']} of "
                           f"{count} proposals (previous command held); {numbers}", digest, "supervised",
                           fallback=True if channel.degraded else None)

    # -- step 2 ----------------------------------------------------------------------

    def _sync_step(self, link_input, pending):
        channel, commissioning = self.sync, link_input.qkd.commissioning
        learned = self._status["sync"].state == "active" and not channel.degraded
        started = mdl.clock_ms()
        result = channel.step(link_input.sync, commissioning.gate_width_s, commissioning.timing_spread_s)
        if learned:
            self.registry.note("sync", inferences=1, faults=result["faults"], latency_ms=mdl.clock_ms() - started)
        result["perfect"] = float(gate_efficiency(0.0, commissioning.gate_width_s, commissioning.timing_spread_s))
        pending["sync"] = result
        if not (result["first_update"] or result["fallback_entered"] or result["faults"]):
            return
        sync = link_input.sync
        digest = canonical_sha256([sync.time_s, sync.measured_offset_s, sync.temperature_c])
        numbers = (f"measured offset {sync.measured_offset_s * 1e12:.1f} ps, residual "
                   f"{abs(result['residual_s']) * 1e12:.1f} ps, gate efficiency {result['efficiency']:.4f}")
        if result["first_update"]:
            self._decision(pending, "sync", channel.controller_id, "sync",
                           f"gate acquisition: first forecast issued, in force in {sync.latency_s:g} s; {numbers}",
                           digest, "acquisition")
        if result["fallback_entered"]:
            reason = (f"{channel.controller_id} dropped after {channel.max_faults} consecutive faults "
                      f"({channel.fault_reasons}); standby {channel.standby_id} acting")
            self.registry.report("sync", self.link_id, "fallback", reason)
            self._decision(pending, "sync", channel.standby_id, "sync", f"{reason}; {numbers}", digest,
                           "fallback_entered", fallback=True)
        elif result["faults"]:
            self._decision(pending, "sync", channel.standby_id if channel.standby is not None
                           else channel.controller_id, "sync",
                           f"forecast refused ({channel.fault_reasons}); standby forecast used; {numbers}", digest,
                           "supervised", fallback=True)

    # -- step 3 ----------------------------------------------------------------------

    def _qkd_step(self, link_input, pending):
        qkd, row = link_input.qkd, link_input.qkd.row
        commissioning = qkd.commissioning
        settings = kr.SETTINGS.replace(signal_intensity=float(row["signal_intensity"]),
                                       decoy_intensity=float(row["decoy_intensity"]),
                                       signal_probability=float(qkd.signal_probability),
                                       vacuum_probability=float(qkd.vacuum_probability),
                                       clock_rate_hz=float(qkd.clock_rate_hz),
                                       sifting_factor=float(commissioning.sifting_factor))
        observables = DecoyObservables(signal_gain=float(row["signal_gain"]), decoy_gain=float(row["decoy_gain"]),
                                       vacuum_gain=float(row["vacuum_gain"]), signal_error=float(row["signal_error"]),
                                       decoy_error=float(row["decoy_error"]), vacuum_error=float(row["vacuum_error"]),
                                       settings=settings, pulses=float(row["pulses"]))
        yield_lower, error_upper = single_photon_bounds(observables)
        rate_hz = float(decoy_key_rate_hz(observables))
        # A finite-sample bound can produce a positive estimate even on a dark-count
        # dominated long span. The existing BB84 QBER admission rule must also gate
        # key accounting, rather than merely colouring the fibre as down afterwards.
        qber_rejected_rate_hz = rate_hz if observables.signal_error >= self.policy.qber_down else 0.0
        # A non-positive commissioned reference cannot admit key from a lucky
        # finite-count period with zero sampled errors on a dark-count-only span.
        # This receiver rule applies equally to simulation and acquisition files;
        # the author's observables, estimator and calibration remain unchanged.
        commissioning_rejected_rate_hz = rate_hz if commissioning.calibration_rate_hz <= 0.0 else 0.0
        rejected_rate_hz = max(qber_rejected_rate_hz, commissioning_rejected_rate_hz)
        if rejected_rate_hz:
            rate_hz = 0.0
        current = TransmitterSettings(signal_intensity=settings.signal_intensity,
                                      decoy_intensity=settings.decoy_intensity,
                                      vacuum_probability=settings.vacuum_probability,
                                      signal_probability=settings.signal_probability,
                                      clock_rate_hz=settings.clock_rate_hz)
        if self._baseline_settings is None:
            self._baseline_settings = current
        result = {"rate_hz": rate_hz, "rejected_rate_hz": rejected_rate_hz,
                  "qber_rejected_rate_hz": qber_rejected_rate_hz,
                  "commissioning_rejected_rate_hz": commissioning_rejected_rate_hz,
                  "bits": rate_hz * float(qkd.period_s), "settings": current,
                  "sifted_hz": settings.clock_rate_hz * settings.signal_probability * observables.signal_gain
                  * settings.sifting_factor, "observables": observables, "action": 0, "fault": None}
        pending["qkd"] = result
        learned = self._status["keyrate"].state == "active" and not self._keyrate_degraded
        digest = None
        if self._proposal is not None and self._proposal != current:
            # keyrate_loop raises here; a live engine records it and drops the actor.
            digest = canonical_sha256(row)
            self._keyrate_faults += 1
            self._keyrate_degraded = True
            reason = "transmitter did not adopt the proposed settings; commissioned-hold from now on"
            self.registry.report("keyrate", self.link_id, "fallback", reason)
            self.registry.note("keyrate", faults=1)
            self._decision(pending, "keyrate", mdl.KEYRATE_BASELINE_ID, "keyrate", reason, digest,
                           "fallback_entered", fallback=True)
            learned = False
        self._proposal = None
        if not learned:
            return
        calibration = LinkCalibration(calibration_gain=commissioning.calibration_gain,
                                      calibration_rate_hz=commissioning.calibration_rate_hz,
                                      dark_count_probability=commissioning.dark_count_probability,
                                      misalignment_error=commissioning.misalignment_error,
                                      dead_time_s=commissioning.dead_time_s)
        started = mdl.clock_ms()
        try:
            previous = PeriodObservables(signal_gain=observables.signal_gain, decoy_gain=observables.decoy_gain,
                                         vacuum_gain=observables.vacuum_gain, signal_error=observables.signal_error,
                                         decoy_error=observables.decoy_error,
                                         single_photon_yield_lower=float(yield_lower),
                                         single_photon_error_upper=float(error_upper), key_rate_hz=rate_hz)
            index, recommendation = self.keyrate.decide(previous, current, calibration, span_km=self.distance_km,
                                                        domain=self._channels_domain)
            self.registry.note("keyrate", inferences=1, latency_ms=mdl.clock_ms() - started)
        except (ValueError, ArithmeticError) as exc:
            self._keyrate_faults += 1
            self.registry.note("keyrate", inferences=1, faults=1)
            result["fault"] = f"{type(exc).__name__}: {exc}"
            digest = digest or canonical_sha256(row)
            if self._keyrate_faults >= self.policy.keyrate_max_faults:
                self._keyrate_degraded = True
                reason = (f"{mdl.KEYRATE_MODEL_ID} dropped after {self._keyrate_faults} refused recommendations "
                          f"(last: {result['fault']}); commissioned-hold from now on")
                self.registry.report("keyrate", self.link_id, "fallback", reason)
                self._decision(pending, "keyrate", mdl.KEYRATE_BASELINE_ID, "keyrate", reason, digest,
                               "fallback_entered", fallback=True)
            return
        index = int(index)
        result["action"] = index
        if index == 0 or recommendation is None:
            return
        proposal = recommendation.proposed_settings
        self._proposal = proposal
        result["command"] = LinkCommand(self.link_id, keyrate_action=index)
        self._decision(
            pending, "keyrate", self.keyrate.controller_id, "keyrate",
            f"{ACTIONS[index].name}: mu {current.signal_intensity:.4g} -> {proposal.signal_intensity:.4g}, nu "
            f"{current.decoy_intensity:.4g} -> {proposal.decoy_intensity:.4g}, clock "
            f"{current.clock_rate_hz:.4g} -> {proposal.clock_rate_hz:.4g} Hz, p_signal "
            f"{current.signal_probability:.3g} -> {proposal.signal_probability:.3g}; period key rate "
            f"{rate_hz:.4g} bit/s, QBER {100 * observables.signal_error:.2f} %",
            digest or canonical_sha256(row), "settings_proposed")

    # -- steps 4 and 5 ---------------------------------------------------------------

    def finish(self, outcome=None):
        """Second half of a tick. ``outcome`` is ``(class probabilities, gated call)`` of the
        row ``begin`` returned, or ``None``."""
        pending, self._pending = self._pending, None
        if pending is None:
            raise ValueError("begin() must be called before finish()")
        self.last_command = (pending.get("qkd") or {}).get("command")
        controls, link_input = pending["controls"], pending["input"]
        flag = controls.flag
        if not pending["processed"]:
            status = flag or "no_input"
            return self._publish_empty(controls, status, link_input), []
        policy = self.policy
        row, qkd = link_input.qkd.row, pending["qkd"]
        qber = float(row["signal_error"])
        polarisation, sync = pending.get("polarisation"), pending["sync"]

        # detector
        label = probability = alarm = baseline_alarm = None
        sustained, raised, cleared = None, False, False
        detector_note = pending["domain"].get("detector")
        detector_contract = self._status["detector"].in_contract is not False and qkd["settings"] == \
            self._baseline_settings and (detector_note is None or detector_note.in_domain)
        if outcome is None or pending["features"] is None:
            alarm = True if self.alarm_held else None        # held from the previous session; no call yet
        else:
            probabilities, label = outcome
            label = str(label)
            probability = float(min(max(1.0 - float(probabilities[self.detector.classes.index("normal")]), 0.0), 1.0))
            self._calls.append(label)
            sustained = str(self.detector.sustained(list(self._calls))[-1])
            baseline_alarm = bool(self._qber_alarm.predict(pending["features"][None, :])[0] != "normal")
            if sustained in self._attack_classes:
                raised, self._quiet = not self.alarm_held, 0
                self.alarm_held = True
            elif self.alarm_held:
                self._quiet += 1
                if self._quiet >= policy.alarm_clear_periods:
                    self.alarm_held, cleared = False, True
            alarm = self.alarm_held
            digest = canonical_sha256([float(value) for value in pending["features"]])
            if raised:
                # The calls that built the alarm were periods of the same event: their key goes too.
                recalled = 0.0
                for _ in range(min(self.detector.sustain - 1, len(self._recent_bits))):
                    recalled += self._recent_bits.pop()
                recalled = min(recalled, self.key_pool_bits)
                self.key_pool_bits -= recalled
                self.discarded_bits += recalled
                self._decision(pending, "detector", mdl.DETECTOR_ID, "detector",
                               f"sustained {sustained} for {self.detector.sustain} periods: P(event) "
                               f"{probability:.4f}, QBER {100 * qber:.2f} %, key rate {qkd['rate_hz']:.4g} bit/s; "
                               f"fibre compromised, key of this and the {self.detector.sustain - 1} previous "
                               f"periods discarded ({recalled + qkd['bits']:.4g} bit)", digest, "alarm_raised",
                               in_contract=detector_contract)
            elif cleared:
                self._decision(pending, "detector", mdl.DETECTOR_ID, "detector",
                               f"no sustained attack call for {policy.alarm_clear_periods} periods: alarm released; "
                               f"P(event) {probability:.4f}, QBER {100 * qber:.2f} %", digest, "alarm_cleared",
                               in_contract=detector_contract)

        # key accounting (a fibre under recalibration is out of service: its periods are not counted)
        if flag == "calibrating":
            self._recent_bits.clear()
        elif self.alarm_held:
            self.discarded_bits += qkd["bits"]
            self._recent_bits.clear()
        else:
            self.key_pool_bits += qkd["bits"]
            self._recent_bits.append(qkd["bits"])

        # estimates
        pol_qber = None if polarisation is None else polarisation["mean_qber"]
        ratio = sync["efficiency"] / max(sync["perfect"], 1e-12)
        self._window.append((qber, ratio, None if pol_qber is None else 1.0 - 2.0 * pol_qber))
        window = np.array([(q, r, np.nan if v is None else v) for q, r, v in self._window], dtype=float)
        terms = {"qber": float(np.clip(1.0 - (window[:, 0].mean() + 2.0 * window[:, 0].std()) / policy.qber_down,
                                       0.0, 1.0)),
                 "sync": float(np.clip(window[:, 1].mean(), 0.0, 1.0)),
                 "polarisation": None if np.isnan(window[:, 2]).all()
                 else float(np.clip(np.nanmean(window[:, 2]), 0.0, 1.0))}
        stability = terms["qber"] * terms["sync"] * (1.0 if terms["polarisation"] is None else terms["polarisation"])

        reasons = []
        commissioning = link_input.qkd.commissioning
        if self.alarm_held:
            internal = "compromised"
            reasons.append("sustained attack alarm held")
        elif qkd["rate_hz"] <= 0.0 or qber >= policy.qber_down:
            internal = "down"
            reasons.append("no secret key in the period" if qkd["rate_hz"] <= 0.0 else "QBER at the BB84 threshold")
            if commissioning.calibration_rate_hz <= 0.0:
                reasons.append("commissioned reference yields no secret key; finite-count estimates cannot admit key")
        else:
            if sustained is not None and sustained != "normal":
                reasons.append(f"detector: sustained {sustained}")
            if baseline_alarm:
                reasons.append("QBER-threshold alarm")
            if polarisation is None:
                reasons.append("polarisation stream fault: " + pending.get("polarisation_error", ""))
            else:
                if polarisation["degraded"] or polarisation["faults"]:
                    reasons.append("compensation under supervisor fallback or faults")
                if pol_qber >= policy.qber_down:
                    reasons.append("monitored polarisation QBER at the BB84 threshold")
            if sync["degraded"] or sync["faults"]:
                reasons.append("synchroniser under fallback or faults")
            if ratio < policy.degraded_gate_fraction:
                reasons.append("gate efficiency below 90 % of its ideal")
            if self._keyrate_degraded or qkd["fault"]:
                reasons.append("key-rate actor refused or dropped")
            if qkd["rate_hz"] < policy.degraded_key_fraction * commissioning.calibration_rate_hz:
                reasons.append("key rate below half the commissioning calibration")
            internal = "degraded" if reasons else "up"

        if polarisation is not None:
            blocks = np.array_split(polarisation["qber"], policy.wave_hz)
            self._wave.extend(float(1.0 - 2.0 * block.mean()) for block in blocks if len(block))
        residual_ps = abs(sync["residual_s"]) * 1e12
        self._measurement = LinkMeasurement(
            self.link_id, controls.tick, int(link_input.epoch_tick), int(link_input.period), self.distance_km, qber,
            qkd["rate_hz"], residual_ps, sync["efficiency"],
            None if polarisation is None else polarisation["residual_mrad"], pol_qber,
            qkd["settings"].signal_intensity, qkd["settings"].decoy_intensity, qkd["settings"].clock_rate_hz, internal)
        if flag == "calibrating":
            # Recalibration runs the loops but publishes nothing until the fibre is returned.
            return self._publish_empty(controls, "calibrating", link_input), pending["decisions"]

        fidelity = werner_fidelity(qber)
        self._had_data = True
        self._history.append((float(controls.network_time_s), qber, fidelity, qkd["rate_hz"]))
        detector_state = "monitoring" if label is not None else (
            "fault: " + pending["detector_fault"] if "detector_fault" in pending else
            f"session baseline ({min(self.features.count, self.features.baseline_periods)} of "
            f"{self.features.baseline_periods} periods)")
        provenance = {
            "input": link_input_kind(self._channels_domain), "epoch_tick": int(link_input.epoch_tick),
            "period": int(link_input.period), "status_reasons": reasons,
            "qber_definition": "signal error rate of the decoy-BB84 period",
            "polarisation_qber": pol_qber,
            "wave_definition": "1 - 2*QBER of the monitored polarisation residual, 20 Hz",
            "stability_terms": terms, "detector_state": detector_state, "detector_sustained": sustained,
            "detector_in_contract": bool(detector_contract),
            "key_rate_calibration_hz": float(commissioning.calibration_rate_hz),
            "qber_rejected_key_estimate_hz": qkd["qber_rejected_rate_hz"],
            "commissioning_rejected_key_estimate_hz": qkd["commissioning_rejected_rate_hz"],
            "key_admission": "positive commissioned reference and signal QBER below the declared BB84 threshold",
            "gate_efficiency_ideal": sync["perfect"], "gate_acquired": bool(sync["acquired"]),
            "domain": {name: {"in_domain": bool(note.in_domain), "detail": note.detail}
                       for name, note in pending["domain"].items()},
            "physical_action_performed": False,
        }
        live = LinkLive(
            link_id=self.link_id, a=self.a, b=self.b, distance_km=self.distance_km, status=internal, qber=qber,
            fidelity=fidelity, skr_bps=qkd["rate_hz"], sifted_rate_hz=float(qkd["sifted_hz"]),
            sync_residual_ps=residual_ps, gate_efficiency=sync["efficiency"],
            polarisation_residual_mrad=None if polarisation is None else polarisation["residual_mrad"],
            stability=float(stability), wave=tuple(self._wave), history=self._history_record(),
            detector=DetectorLive(label, probability, alarm, baseline_alarm),
            qkd=QkdLive(signal_intensity=qkd["settings"].signal_intensity,
                        decoy_intensity=qkd["settings"].decoy_intensity,
                        clock_rate_hz=qkd["settings"].clock_rate_hz,
                        signal_gain=float(row["signal_gain"]), decoy_gain=float(row["decoy_gain"]),
                        key_pool_bits=self.key_pool_bits, discarded_bits=self.discarded_bits,
                        last_action=ACTIONS[qkd["action"]].name),
            controllers=self._controllers(), classical_admission=None, provenance=provenance)
        self.last = live
        return live, pending["decisions"]

    # -- publishing --------------------------------------------------------------------

    def _controllers(self) -> dict:
        out = {}
        if self.polarisation is not None:
            keyrate_id = mdl.KEYRATE_BASELINE_ID if self._keyrate_degraded else self.keyrate.controller_id
            out = {"compensation": self.polarisation.acting_id, "sync": self.sync.acting_id, "keyrate": keyrate_id,
                   "detector": mdl.DETECTOR_ID, "qber_alarm": mdl.QBER_ALARM_ID}
            for role in ("compensation", "sync", "keyrate", "detector"):
                out[role + ".model_state"] = self._status[role].state
            if self.polarisation.degraded:
                out["compensation.model_state"] = "fallback"
            if self.sync.degraded:
                out["sync.model_state"] = "fallback"
            if self._keyrate_degraded:
                out["keyrate.model_state"] = "fallback"
        return out

    def _history_record(self) -> LinkHistory:
        if not self._history:
            return LinkHistory()
        t_s, qber, fidelity, skr = zip(*self._history)
        return LinkHistory(t_s, qber, fidelity, skr)

    def _publish_empty(self, controls: StepControls, status: str, link_input) -> LinkLive:
        """A fibre with nothing to publish: metrics None, no wave; the history shows the gap."""
        self._wave.clear()
        if self._had_data:
            self._history.append((float(controls.network_time_s), None, None, None))
        stock = self._had_data or self.key_pool_bits > 0 or self.discarded_bits > 0
        live = LinkLive(
            link_id=self.link_id, a=self.a, b=self.b, distance_km=self.distance_km, status=status,
            history=self._history_record(),
            qkd=QkdLive(key_pool_bits=self.key_pool_bits if stock else None,
                        discarded_bits=self.discarded_bits if stock else None),
            controllers={}, provenance={"input": "none" if link_input is None else "withheld",
                                        "status_reasons": [f"imposed: {status}"] if status != "no_input"
                                        else ["the input source delivers nothing for this fibre"],
                                        "physical_action_performed": False})
        self.last = live
        return live

    def measurement(self) -> LinkMeasurement | None:
        """The last internal measurement (``None`` before the first processed period)."""
        return self._measurement


def link_input_kind(domain: str) -> str:
    return "simulated" if domain == mdl.OFFLINE_DOMAIN else str(domain)


# --------------------------------------------------------------------------
# all fibres
# --------------------------------------------------------------------------

class LinkBank:
    """The processors of a changing set of fibres.

    ``sync(topology.links)`` follows the topology (new fibres get a processor, removed
    ones are forgotten, a changed length recommissions). ``step(frame, effective_flags)``
    processes one tick of every fibre and evaluates the detector once for all of them.
    """

    def __init__(self, registry: mdl.ModelRegistry | None = None, *, policy: LinkPolicy | None = None,
                 seed: int = 0):
        self.registry = registry if registry is not None else mdl.ModelRegistry()
        self.policy = policy or LinkPolicy()
        self.seed = int(seed)
        self._processors: dict = {}
        self._active: dict = {}

    @property
    def link_ids(self) -> tuple:
        return tuple(sorted(self._processors))

    def processor(self, link_id: str) -> LinkProcessor:
        return self._processors[link_id]

    def sync(self, topology_links: Iterable[Any]) -> None:
        """Adopt the current fibres (objects with ``link_id``, ``left_node_id``,
        ``right_node_id``, ``distance_km`` and ``active``)."""
        seen = set()
        for link in topology_links:
            link_id = str(link.link_id)
            if link_id in seen:
                raise ValueError(f"duplicate fibre {link_id!r}")
            seen.add(link_id)
            distance = float(link.distance_km)
            processor = self._processors.get(link_id)
            if processor is None:
                self._processors[link_id] = LinkProcessor(link_id, link.left_node_id, link.right_node_id, distance,
                                                          self.registry, policy=self.policy, seed=self.seed)
            else:
                processor.a, processor.b = str(link.left_node_id), str(link.right_node_id)
                if distance != processor.distance_km:
                    processor.reset(distance)
            self._active[link_id] = bool(getattr(link, "active", True))
        for link_id in set(self._processors) - seen:
            del self._processors[link_id]
            self._active.pop(link_id, None)
            self.registry.release(link_id)

    def step(self, frame: InputFrame, effective_flags: Mapping[str, str] | None = None, *,
             at_utc: str | None = None) -> BankStep:
        """One tick. ``effective_flags`` maps a fibre to ``disabled | isolated | calibrating``;
        an inactive fibre without a flag is reported ``disabled``."""
        flags = dict(effective_flags or {})
        unknown = set(flags) - set(self._processors)
        if unknown:
            raise ValueError(f"flags for unknown fibres {sorted(unknown)}")
        at_utc = at_utc or datetime.now(timezone.utc).isoformat()
        domain = domain_of(frame.kind)
        order = sorted(self._processors)
        rows, owners = [], []
        for link_id in order:
            flag = flags.get(link_id)
            if flag is None and not self._active.get(link_id, True):
                flag = "disabled"
            controls = StepControls(int(frame.tick), at_utc, float(frame.network_time_s), flag, domain)
            features = self._processors[link_id].begin(frame.links.get(link_id), controls)
            if features is not None:
                rows.append(features)
                owners.append(link_id)
        outcomes = {}
        if rows:
            detector, _ = self.registry.detector()
            started = mdl.clock_ms()
            block = np.vstack(rows)
            probabilities, calls = detector.probabilities(block), detector.predict(block)
            self.registry.note("detector", inferences=len(rows), latency_ms=(mdl.clock_ms() - started) / len(rows))
            outcomes = {link_id: (probabilities[index], str(calls[index])) for index, link_id in enumerate(owners)}
        links, decisions, commands = {}, [], {}
        for link_id in order:
            processor = self._processors[link_id]
            live, made = processor.finish(outcomes.get(link_id))
            links[link_id] = live
            decisions.extend(made)
            if processor.last_command is not None:
                commands[link_id] = processor.last_command
        return BankStep(links, tuple(decisions), commands)

    def reset_link(self, link_id: str) -> LinkCommand:
        """Recalibration: restart the controllers, the gate and the detector baseline of a
        fibre now. Returns the command that makes the input source recommission the
        session (``source.actuate({link_id: command})``); with a source that cannot,
        the processing still restarts from the next period."""
        self._processors[link_id].reset()
        return LinkCommand(link_id, recommission=True)

    def link_state(self, link_id: str) -> LinkLive | None:
        """The last published ``LinkLive`` of a fibre (``None`` before its first tick)."""
        return self._processors[link_id].last

    def measurement(self, link_id: str) -> LinkMeasurement | None:
        """The last internal measurement of a fibre — available while it is ``calibrating``."""
        return self._processors[link_id].measurement()

    def model_status(self) -> tuple:
        return self.registry.status()


__all__ = ["BankStep", "CausalFeatures", "LinkBank", "LinkMeasurement", "LinkPolicy", "LinkProcessor",
           "PolarisationChannel", "StepControls", "SyncChannel", "domain_of", "werner_fidelity"]
