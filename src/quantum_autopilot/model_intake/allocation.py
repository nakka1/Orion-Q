"""Optional offline hybrid allocator, ported from the reviewed author export.

Admits classical traffic using a model-based law. Persistence is NOT ML; Ridge
changes only the drift forecast. Internal key budgets are asymptotic estimates,
not delivered bits, finite-key certificates or hardware commands.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib

import numpy as np

from .artifacts import ALLOCATION_ROOT, ReviewedArchive
from .inference import _finite, _offline

LINKS, LATENCY, HORIZON, WINDOW = 11, 6, 30, 6
PERIOD_S = 5.0
LEVELS = (0., .25, .5, .75, 1.)
COEXISTENCE = {
    "raman_coefficient_per_km_nm": 4e-9, "filter_bandwidth_nm": .1,
    "detection_window_s": 1e-9, "detector_efficiency": .7, "dark_count_rate_hz": 10.,
    "optical_error_rate": .005, "attenuation_db_per_km": .2, "wavelength_nm": 1550.,
}


@dataclass(frozen=True)
class AllocationObservation:
    time_s: float
    measured_qber: tuple[float, ...]
    pending_admission: tuple[tuple[float, ...], ...]  # Six rows, oldest first.
    raman_qber_estimate: tuple[float, ...]
    key_pool_bits: tuple[float, ...]
    backlog_bits: tuple[float, ...]
    urgency_s: tuple[float | None, ...]  # None/+inf = no pending deadline.


@dataclass(frozen=True)
class AllocationRecommendation:
    model_id: str
    archive_sha256: str
    plant_sha256: str
    time_s: float
    effective_at_s: float
    link_names: tuple[str, ...]
    status: str
    drift: tuple[float, ...]
    forecast: tuple[tuple[float, ...], ...]
    admission: tuple[float, ...]
    forecast_key_bits: tuple[float | None, ...]
    required_key_bits: tuple[float, ...]
    model_coverage_met: tuple[bool, ...]
    evidence: str = field(default="simulated_offline", init=False)
    key_accounting: str = field(default="asymptotic_forecast_not_certified", init=False)
    physical_action_performed: bool = field(default=False, init=False)


def _urgency(values):
    if isinstance(values, np.ndarray) and values.ndim != 1:
        raise ValueError("urgency must be a vector")
    if not isinstance(values, (list, tuple, np.ndarray)) or len(values) != LINKS:
        raise ValueError("eleven urgency entries required")
    result = []
    for value in values:
        if value is None:
            result.append(np.inf)
            continue
        raw = np.asarray(value)
        if raw.shape != () or raw.dtype.kind not in "fi" or np.isnan(raw) or float(raw) <= 0:
            raise ValueError("urgency must be positive seconds or None/+inf; expired deadlines refused")
        result.append(float(raw))
    return np.array(result)


def _vector(value, name, *, maximum=None):
    result = _finite(value, (LINKS,), name)
    if np.any(result < 0) or (maximum is not None and np.any(result > maximum)):
        raise ValueError(f"{name} outside permitted range")
    return result


class AllocationReplayModel:
    """Fixed reviewed eleven-link plant; no remapping to the ORION topology."""

    def __init__(self, archive: ReviewedArchive, variant: str):
        if archive.kind != "qnet" or variant not in {"persistence", "ridge"}:
            raise ValueError("unsupported allocation export")
        plant_name = ALLOCATION_ROOT + "model/plant.json"
        plant = archive.json(plant_name)
        config = plant["config"]
        expected = {"control_period_s": 5., "reconfiguration_latency_s": 30., "latency_steps": 6,
                    "admission_levels": list(LEVELS), "estimation_fraction": .05, "coexistence_explicit": False}
        if any(config.get(k) != v for k, v in expected.items()) or plant["coexistence"] != COEXISTENCE:
            raise ValueError("incompatible allocation plant timing/physics")
        profiles = plant["links"]
        if len(profiles) != LINKS or [p["index"] for p in profiles] != list(range(LINKS)):
            raise ValueError("eleven ordered plant links required")
        names = tuple(p["name"] for p in profiles)
        if len(set(names)) != LINKS or any(not isinstance(n, str) or not n.strip() for n in names):
            raise ValueError("unique named plant links required")
        self._profiles = tuple(
            _finite([p["length_km"], p["sifted_rate_hz"], p["signal_counts_per_gate"],
                     p["classical_power_full_w"]], (4,), "link profile") for p in profiles)
        if any(np.any(p <= 0) or p[2] > 1 for p in self._profiles):
            raise ValueError("invalid allocation link profile")
        meta = archive.json(ALLOCATION_ROOT + f"model/{variant}.json")
        if (meta["coverage_target"] != 1. or meta["idle_level"] != 1. or meta["horizon_cap_s"] != 150.
                or meta["forecaster"]["kind"] != variant or meta["forecaster"]["horizon"] != HORIZON):
            raise ValueError("unsupported allocator configuration")
        if variant == "ridge":
            if meta["forecaster"]["window"] != WINDOW or meta["forecaster"]["coefficients_shape"] != [7, 30]:
                raise ValueError("unsupported allocation Ridge layout")
            arrays = archive.arrays(ALLOCATION_ROOT + "model/ridge.npz")
            if set(arrays) != {"coefficients"}:
                raise ValueError("unexpected allocation arrays")
            self._coefficients = _finite(arrays["coefficients"], (7, 30), "coefficients")
        self._variant, self._link_names = variant, names
        self._archive_sha256 = archive.sha256
        self._plant_sha256 = hashlib.sha256(archive.read(plant_name)).hexdigest()
        self.reset()

    @property
    def link_names(self) -> tuple[str, ...]:
        return self._link_names

    def reset(self):
        self._history = ()
        self._last_time = None
        self._stream_id = None

    def _validate(self, observation, domain, protocol, link_names):
        _offline(domain)
        if protocol != "single_photon_bb84":
            raise ValueError("allocator plant requires single_photon_bb84, not decoy-state")
        if not isinstance(link_names, (tuple, list)) or tuple(link_names) != self.link_names:
            raise ValueError("link identity/order differs from reviewed plant")
        if not isinstance(observation, AllocationObservation):
            raise ValueError("typed allocation observation required")
        time = float(_finite(observation.time_s, (), "time_s"))
        if time < 0:
            raise ValueError("relative time must be nonnegative")
        measured = _vector(observation.measured_qber, "measured_qber", maximum=1.)
        raman = _vector(observation.raman_qber_estimate, "raman_qber_estimate", maximum=1.)
        pending = _finite(observation.pending_admission, (LATENCY, LINKS), "pending_admission")
        if not np.isin(pending, LEVELS).all():
            raise ValueError("pending admissions must use the five reviewed levels")
        pool = _vector(observation.key_pool_bits, "key_pool_bits")
        backlog = _vector(observation.backlog_bits, "backlog_bits")
        urgency = _urgency(observation.urgency_s)
        return time, np.maximum(measured - raman - .005, 0.), pending, pool, backlog, urgency

    def _key_over(self, link, drift, level):
        if len(drift) == 0:
            return 0.
        length, sifted, signal, full_power = self._profiles[link]
        power = np.asarray(level, dtype=float) * full_power
        alpha = .2 * np.log(10.) / 10.
        noise_power = power * np.exp(-alpha * length) * (4e-9 * .1) * length
        energy = 6.62607015e-34 * (2.99792458e8 / (1550. * 1e-9))
        background = (noise_power / energy) * 1e-9 * .7 + 10. * 1e-9
        alignment = np.clip(drift + .005, 0., 1.)
        total = signal + background
        qber = np.clip((alignment * signal + .5 * background) / np.maximum(total, 1e-300), 0., .5)
        with np.errstate(divide="ignore", invalid="ignore"):
            entropy = -qber * np.log2(qber) - (1 - qber) * np.log2(1 - qber)
        entropy = np.where((qber <= 0) | (qber >= 1), 0., entropy)
        fraction = 1. - 1.16 * entropy - entropy
        return float(np.sum(np.maximum(sifted * .95 * fraction, 0.)))

    def _calculate(self, validated, history):
        time, drift, pending, pool, backlog, urgency = validated
        candidate = (tuple(history) + (drift,))[-WINDOW:]
        status = "persistence"
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            if self._variant == "ridge" and len(candidate) == WINDOW:
                features = np.stack(candidate, axis=1)
                design = np.hstack([features, np.ones((LINKS, 1))])
                forecast = np.maximum(design @ self._coefficients, 0.)
                status = "ridge"
            else:
                forecast = np.repeat(drift[:, None], HORIZON, axis=1)
                if self._variant == "ridge":
                    status = "warmup_persistence"
            forecast = _finite(forecast, (LINKS, HORIZON), "drift forecast")
            window = np.minimum(urgency, 150.)
            periods = np.clip(np.ceil(window / PERIOD_S).astype(int), 1, HORIZON)
            debt = backlog - pool
            pace = np.where(np.isfinite(urgency), np.minimum(window / np.maximum(urgency, PERIOD_S), 1.), 1.)
            required = np.maximum(debt, 0.) * pace
            admission = np.ones(LINKS)
            budgets, covered = [None] * LINKS, [True] * LINKS
            for link in range(LINKS):
                if debt[link] <= 0:
                    continue
                horizon = forecast[link, :periods[link]]
                committed = min(LATENCY, len(horizon))
                locked = self._key_over(link, horizon[:committed], pending[:committed, link]) * PERIOD_S
                free = horizon[committed:]
                for level in reversed(LEVELS):
                    promised = locked + self._key_over(link, free, level) * PERIOD_S
                    if promised >= required[link]:
                        admission[link] = level
                        break
                else:
                    admission[link] = 0.
                    covered[link] = False  # promised is the level-zero budget, still insufficient.
                budgets[link] = promised
        _finite(required, (LINKS,), "required_key_bits")
        if any(v is not None and not np.isfinite(v) for v in budgets):
            raise ValueError("non-finite forecast key budget")
        result = AllocationRecommendation(
            f"qnet.allocator.{self._variant}", self._archive_sha256, self._plant_sha256,
            time, time + LATENCY * PERIOD_S, self.link_names, status, tuple(drift.tolist()),
            tuple(tuple(row) for row in forecast.tolist()), tuple(admission.tolist()),
            tuple(budgets), tuple(required.tolist()), tuple(covered))
        return result, candidate[-(WINDOW-1):]

    def recommend(self, observation: AllocationObservation, *, domain: str, protocol: str,
                  link_names: tuple[str, ...], stream_id: str) -> AllocationRecommendation:
        if not isinstance(stream_id, str) or not stream_id.strip() or len(stream_id) > 256:
            raise ValueError("bounded nonempty stream identity required")
        if self._stream_id is not None and stream_id != self._stream_id:
            raise ValueError("stream changed; reset before changing campaign/plant")
        validated = self._validate(observation, domain, protocol, link_names)
        time = validated[0]
        if self._last_time is not None and abs(time - self._last_time - PERIOD_S) > 1e-9:
            raise ValueError("consecutive five-second observations required; reset after gap")
        result, candidate = self._calculate(validated, self._history)
        self._history, self._last_time, self._stream_id = candidate, time, stream_id
        return result

    def recommend_snapshot(self, observation: AllocationObservation, *, drift_history, history_times_s,
                           domain: str, protocol: str, link_names: tuple[str, ...]) -> AllocationRecommendation:
        """Stateless exported snapshot with up to five previous drift estimates."""
        validated = self._validate(observation, domain, protocol, link_names)
        times = np.asarray(history_times_s)
        if times.ndim != 1 or len(times) > WINDOW-1:
            raise ValueError("zero to five history timestamps required")
        times = _finite(times, (len(times),), "history_times_s")
        history = _finite(drift_history, (len(times), LINKS), "drift_history")
        if (np.any(history < 0) or np.any(history > 1) or np.any(times < 0)
                or not np.allclose(np.diff(np.append(times, validated[0])), PERIOD_S, rtol=0, atol=1e-9)):
            raise ValueError("incompatible drift history or timestamps")
        return self._calculate(validated, tuple(history))[0]
