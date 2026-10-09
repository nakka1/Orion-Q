"""Standalone inference for the three phase-5 models.

    from inferencia import AttackDetector, KeyRateAgent, SyncPredictor

This file depends on numpy, pandas and scikit-learn only.  It does not import
``qnet_qkd``: everything the models need at inference time — the feature
builder, the network forward pass, the observation vector, the action box — is
ported here from the package, with the same names and the same arithmetic, and
``scripts/export_models.py`` checks the port against the package on every
example before it writes the bundle.  If the two ever disagree, the export
fails; so the numbers in ``exemplos/`` are the package's numbers.

The three models
----------------

:class:`AttackDetector`
    Gradient boosting over 17 features of the published per-period
    observables, each relative to the link's own commissioning baseline.  The
    deployable configuration is the article's "boosting + limiar + histerese":
    the class the ensemble picks, suppressed to ``normal`` unless the
    probability of *some* attack clears a calibrated threshold, and reported
    only once the same call has persisted for three periods.

:class:`KeyRateAgent`
    The actor of an advantage actor-critic agent: a 16→64→64→9 ReLU network
    that reads the previous period's observables and the transmitter's own
    settings and picks one of nine adjustments to the intensities, the clock
    or the share of signal pulses.  Evaluated in the article at sampling
    temperature 0.25.

:class:`SyncPredictor`
    Ridge regression on the last eight offset estimates and the thermometer,
    predicting where the pulse will arrive two update periods ahead, so the
    detector gate can be moved there before it gets there.

Units: rates in hertz, times in seconds, gains and error rates as fractions
(0.027, not 2.7), temperatures in degrees Celsius.
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

__all__ = [
    "FEATURE_NAMES", "detection_features", "AttackDetector",
    "ACTIONS", "KEYRATE_STATE_FEATURES", "TransmitterSettings", "PeriodObservables",
    "keyrate_observation", "KeyRateAgent",
    "sync_features", "SyncPredictor",
]


# ===========================================================================
# 1. attack detector
# ===========================================================================

#: The feature vector, in the order the model was fitted on.
FEATURE_NAMES: tuple[str, ...] = (
    "log_signal_gain_ratio",
    "log_decoy_gain_ratio",
    "decoy_to_signal_shift",
    "signal_error_shift",
    "decoy_error_shift",
    "log_vacuum_gain_ratio",
    "vacuum_error_shift",
    "log_single_photon_yield_ratio",
    "single_photon_error_shift",
    "log_key_rate_ratio",
    "log_gain_versus_calibration",
    "temperature_shift",
    "temperature_rate",
    "error_temperature_correlation",
    "error_trend",
    "gain_trend",
    "error_volatility",
)

#: The per-period observables a session log must carry.  All of them are
#: published by one end of the link or the other; none is ground truth.
REQUIRED_COLUMNS: tuple[str, ...] = (
    "period", "time_s", "signal_gain", "decoy_gain", "vacuum_gain",
    "signal_error", "decoy_error", "vacuum_error",
    "single_photon_yield_lower", "single_photon_error_upper",
    "key_rate_hz", "temperature_c", "expected_gain",
)

_FLOOR = 1e-12


def _log_ratio(values, reference: float):
    return np.log((np.asarray(values, dtype=float) + _FLOOR) / (reference + _FLOOR))


def _rolling_slope(values, window: int):
    values = np.asarray(values, dtype=float)
    lags = np.arange(window, dtype=float)
    centred = lags - lags.mean()
    denominator = float(np.sum(centred ** 2))
    out = np.zeros_like(values)
    for index in range(window - 1, len(values)):
        segment = values[index - window + 1: index + 1]
        out[index] = float(np.sum(centred * (segment - segment.mean())) / denominator)
    return out


def _rolling_std(values, window: int):
    values = np.asarray(values, dtype=float)
    out = np.zeros_like(values)
    for index in range(window - 1, len(values)):
        out[index] = float(np.std(values[index - window + 1: index + 1]))
    return out


def _rolling_correlation(first, second, window: int):
    first = np.asarray(first, dtype=float)
    second = np.asarray(second, dtype=float)
    out = np.zeros_like(first)
    for index in range(window - 1, len(first)):
        left = first[index - window + 1: index + 1]
        right = second[index - window + 1: index + 1]
        left_spread, right_spread = float(np.std(left)), float(np.std(right))
        if left_spread < 1e-15 or right_spread < 1e-15:
            continue
        out[index] = float(np.mean((left - left.mean()) * (right - right.mean()))
                           / (left_spread * right_spread))
    return out


def detection_features(frame: pd.DataFrame, window: int = 30,
                       baseline_periods: int = 60) -> tuple[np.ndarray, pd.DataFrame]:
    """Feature vectors for one link's session log, against its own baseline.

    The first ``baseline_periods`` rows are the commissioning calibration: every
    feature is a ratio or a shift against their median, which is what lets a
    model fitted on 20–70 km spans transfer between them.  Those rows produce
    no output — scoring the calibration against itself would be a tautology.

    Args:
        frame: one link, one row per settling period, with
            :data:`REQUIRED_COLUMNS`.  Rows are sorted by ``period`` here.
        window: trailing periods for the trend, volatility and
            error–temperature correlation.
        baseline_periods: commissioning rows.

    Returns:
        ``(features, index)`` — ``features`` is ``(n, 17)`` in
        :data:`FEATURE_NAMES` order, ``index`` has ``period`` and ``time_s``
        for each row.

    Raises:
        ValueError: on a missing column or a log shorter than the baseline.
    """
    missing = set(REQUIRED_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"session log is missing columns: {sorted(missing)}")
    frame = frame.sort_values("period").reset_index(drop=True)
    if len(frame) <= baseline_periods:
        raise ValueError(f"need more than {baseline_periods} periods; the first "
                         f"{baseline_periods} are the commissioning baseline")

    baseline = frame.iloc[:baseline_periods]

    def level(column: str) -> float:
        return float(np.median(baseline[column].to_numpy()))

    signal_gain = frame["signal_gain"].to_numpy()
    decoy_gain = frame["decoy_gain"].to_numpy()
    vacuum_gain = frame["vacuum_gain"].to_numpy()
    signal_error = frame["signal_error"].to_numpy()
    decoy_error = frame["decoy_error"].to_numpy()
    vacuum_error = frame["vacuum_error"].to_numpy()
    single_yield = frame["single_photon_yield_lower"].to_numpy()
    single_error = frame["single_photon_error_upper"].to_numpy()
    key_rate = frame["key_rate_hz"].to_numpy()
    temperature = frame["temperature_c"].to_numpy()
    expected_gain = frame["expected_gain"].to_numpy()
    period_s = float(np.median(np.diff(frame["time_s"].to_numpy()))) if len(frame) > 1 else 1.0

    baseline_ratio = level("decoy_gain") / max(level("signal_gain"), _FLOOR)
    ratio = decoy_gain / np.maximum(signal_gain, _FLOOR)
    temperature_rate = np.gradient(temperature, period_s)

    columns = {
        "log_signal_gain_ratio": _log_ratio(signal_gain, level("signal_gain")),
        "log_decoy_gain_ratio": _log_ratio(decoy_gain, level("decoy_gain")),
        "decoy_to_signal_shift": ratio / max(baseline_ratio, _FLOOR) - 1.0,
        "signal_error_shift": signal_error - level("signal_error"),
        "decoy_error_shift": decoy_error - level("decoy_error"),
        "log_vacuum_gain_ratio": _log_ratio(vacuum_gain, level("vacuum_gain")),
        "vacuum_error_shift": vacuum_error - level("vacuum_error"),
        "log_single_photon_yield_ratio": _log_ratio(single_yield,
                                                    level("single_photon_yield_lower")),
        "single_photon_error_shift": single_error - level("single_photon_error_upper"),
        "log_key_rate_ratio": _log_ratio(key_rate, level("key_rate_hz")),
        "log_gain_versus_calibration": _log_ratio(signal_gain, 1.0) - _log_ratio(expected_gain, 1.0),
        "temperature_shift": temperature - level("temperature_c"),
        "temperature_rate": temperature_rate,
        "error_temperature_correlation": _rolling_correlation(
            signal_error, np.abs(temperature_rate), window),
        "error_trend": _rolling_slope(signal_error, window) * period_s,
        "gain_trend": _rolling_slope(_log_ratio(signal_gain, level("signal_gain")),
                                     window) * period_s,
        "error_volatility": _rolling_std(signal_error, window),
    }
    features = np.stack([columns[name] for name in FEATURE_NAMES], axis=-1)

    keep = frame["period"].to_numpy() >= baseline_periods
    index = pd.DataFrame({"period": frame["period"].to_numpy()[keep],
                          "time_s": frame["time_s"].to_numpy()[keep]})
    return features[keep], index


@dataclass
class AttackDetector:
    """The deployable detector: boosting, calibrated threshold, hysteresis.

    Attributes:
        model: the fitted ``HistGradientBoostingClassifier``.
        mean, scale: the standardiser fitted on the training rows.
        classes: label order of ``model.predict_proba``.
        attack_threshold: minimum probability of *some* attack before a
            non-normal call is let through.  It only ever suppresses an alarm the
            model already raised; at zero this is the plain argmax.
        sustain: consecutive agreeing periods before an alarm is reported.
        window, baseline_periods: the feature builder's settings.
    """

    model: object
    mean: np.ndarray
    scale: np.ndarray
    classes: tuple[str, ...]
    attack_threshold: float
    sustain: int = 3
    window: int = 30
    baseline_periods: int = 60

    @classmethod
    def load(cls, directory: str | Path) -> "AttackDetector":
        directory = Path(directory)
        config = json.loads((directory / "detector_config.json").read_text())
        if tuple(config["feature_names"]) != FEATURE_NAMES:
            raise ValueError("the saved model was fitted on a different feature order")
        with open(directory / "detector_boosting.pkl", "rb") as handle:
            model = pickle.load(handle)
        return cls(model=model, mean=np.asarray(config["standardiser_mean"]),
                   scale=np.asarray(config["standardiser_scale"]),
                   classes=tuple(config["classes"]),
                   attack_threshold=float(config["attack_threshold"]),
                   sustain=int(config["sustain_periods"]),
                   window=int(config["window"]),
                   baseline_periods=int(config["baseline_periods"]))

    # -- per period ---------------------------------------------------------

    def probabilities(self, features: np.ndarray) -> np.ndarray:
        """Class probabilities, ``(n, len(classes))``."""
        return np.asarray(self.model.predict_proba((features - self.mean) / self.scale))

    def predict(self, features: np.ndarray) -> np.ndarray:
        """Per-period class after the calibrated threshold, before hysteresis."""
        scaled = (features - self.mean) / self.scale
        best = np.asarray(self.model.predict(scaled)).astype(object)
        if self.attack_threshold <= 0.0 or "normal" not in self.classes:
            return best.astype(str)
        proba = np.asarray(self.model.predict_proba(scaled))
        attack_mass = 1.0 - proba[:, self.classes.index("normal")]
        quiet = (best != "normal") & (attack_mass < self.attack_threshold)
        return np.where(quiet, "normal", best).astype(str)

    def sustained(self, calls: Sequence[str]) -> np.ndarray:
        """Report a call only once it has repeated ``sustain`` periods in a row."""
        calls = np.asarray(calls, dtype=object)
        out = np.full(len(calls), "normal", dtype=object)
        run_label, run_length = None, 0
        for position, call in enumerate(calls):
            if call == run_label:
                run_length += 1
            else:
                run_label, run_length = call, 1
            if call != "normal" and run_length >= self.sustain:
                out[position] = call
        return out.astype(str)

    # -- a whole log --------------------------------------------------------

    def classify_session(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Run the full pipeline over one link's session log.

        Returns:
            One row per scored period with ``period``, ``time_s``, the
            ``argmax`` class, ``attack_probability``, the thresholded
            ``per_period`` call, and ``alarm`` — the call after hysteresis,
            which is what an operator would be shown.  Also one probability
            column per class, prefixed ``p_``.
        """
        features, index = detection_features(frame, self.window, self.baseline_periods)
        proba = self.probabilities(features)
        per_period = self.predict(features)
        out = index.copy()
        out["argmax"] = np.asarray(self.classes, dtype=object)[np.argmax(proba, axis=1)]
        out["attack_probability"] = 1.0 - proba[:, self.classes.index("normal")]
        out["per_period"] = per_period
        out["alarm"] = self.sustained(per_period)
        for position, label in enumerate(self.classes):
            out[f"p_{label}"] = proba[:, position]
        return out


# ===========================================================================
# 2. key-rate agent
# ===========================================================================

@dataclass(frozen=True)
class ActionSpec:
    """One adjustment: multiplicative on intensities and clock, additive on a share."""

    name: str
    signal_factor: float = 1.0
    decoy_factor: float = 1.0
    clock_factor: float = 1.0
    signal_probability_step: float = 0.0


ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec("manter"),
    ActionSpec("mu +25%", signal_factor=1.25),
    ActionSpec("mu -20%", signal_factor=0.80),
    ActionSpec("nu +25%", decoy_factor=1.25),
    ActionSpec("nu -20%", decoy_factor=0.80),
    ActionSpec("clock +50%", clock_factor=1.50),
    ActionSpec("clock -33%", clock_factor=0.67),
    ActionSpec("p_sinal +0.05", signal_probability_step=0.05),
    ActionSpec("p_sinal -0.05", signal_probability_step=-0.05),
)

KEYRATE_STATE_FEATURES = 16

#: The reference clock the observation's clock feature is relative to.
DEFAULT_CLOCK_RATE_HZ = 1.0e9


@dataclass(frozen=True)
class TransmitterSettings:
    """Alice's operating point."""

    signal_intensity: float
    decoy_intensity: float
    vacuum_probability: float
    signal_probability: float
    clock_rate_hz: float


@dataclass(frozen=True)
class PeriodObservables:
    """What one settling period published — the agent's input.

    ``single_photon_yield_lower`` and ``single_photon_error_upper`` are the
    vacuum+weak-decoy bounds, and ``key_rate_hz`` the provable rate computed
    from them; every decoy-state receiver computes these to extract key at all.
    """

    signal_gain: float
    decoy_gain: float
    vacuum_gain: float
    signal_error: float
    decoy_error: float
    single_photon_yield_lower: float
    single_photon_error_upper: float
    key_rate_hz: float


def keyrate_observation(previous: PeriodObservables | None,
                        settings: TransmitterSettings, *,
                        calibration_gain: float, calibration_rate_hz: float,
                        dark_count_probability: float, misalignment_error: float,
                        previous_rate_hz: float, dead_time_s: float = 50e-9
                        ) -> np.ndarray:
    """The 16-number observation the policy was trained on.

    Args:
        previous: last period's observables; ``None`` for the first period of a
            session, when nothing has been measured under these settings yet.
        settings: the transmitter's current settings.
        calibration_gain: signal gain at commissioning.
        calibration_rate_hz: provable key rate at commissioning.
        dark_count_probability: the receiver's per-gate dark-count probability,
            which is what the vacuum gain is relative to.
        misalignment_error: commissioned optical error, used only for the first
            period.
        previous_rate_hz: pass ``previous.key_rate_hz`` (or
            ``calibration_rate_hz`` for the first period).  The package's
            docstring describes feature 13 as the rate change since the period
            before, but its environment stores the rate of the period just
            observed, so feature 13 always equals feature 7; the policy was
            trained that way and is reproduced here that way.
        dead_time_s: detector dead time.

    Returns:
        ``(16,)``, in the order the network expects.
    """
    floor = 1e-12
    if previous is None:
        gains = (1.0, 1.0, 1.0)
        errors = (misalignment_error, misalignment_error)
        single = (1.0, misalignment_error)
        rate_ratio = 1.0
        click_rate = settings.clock_rate_hz * calibration_gain
        absolute_gain = calibration_gain
    else:
        gains = (previous.signal_gain / max(calibration_gain, floor),
                 previous.decoy_gain / max(calibration_gain, floor),
                 previous.vacuum_gain / max(dark_count_probability, floor))
        errors = (previous.signal_error, previous.decoy_error)
        single = (previous.single_photon_yield_lower / max(calibration_gain, floor),
                  previous.single_photon_error_upper)
        rate_ratio = previous.key_rate_hz / max(calibration_rate_hz, floor)
        click_rate = settings.clock_rate_hz * previous.signal_gain
        absolute_gain = previous.signal_gain

    saturation = click_rate * dead_time_s
    return np.array([
        np.log(max(gains[0], floor)),
        np.log(max(gains[1], floor)),
        np.log(max(gains[2], floor)),
        100.0 * errors[0],
        100.0 * errors[1],
        np.log(max(single[0], floor)),
        100.0 * single[1],
        np.log(max(rate_ratio, floor)),
        settings.signal_intensity,
        settings.decoy_intensity / max(settings.signal_intensity, floor),
        np.log(settings.clock_rate_hz / DEFAULT_CLOCK_RATE_HZ),
        settings.signal_probability,
        np.log(max(saturation, floor)),
        np.log(max(previous_rate_hz, floor) / max(calibration_rate_hz, floor)),
        np.log(max(absolute_gain, floor)),
        np.log(max(calibration_rate_hz, floor) / 1.0e6),
    ])


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits, axis=-1, keepdims=True)
    exponentiated = np.exp(shifted)
    return exponentiated / np.sum(exponentiated, axis=-1, keepdims=True)


@dataclass
class KeyRateAgent:
    """The trained actor, and the box its actions are clipped to.

    Attributes:
        weights, biases: the actor network, ReLU between layers, linear out.
        mean, scale: the observation normaliser, frozen at the end of training.
        temperature: sampling temperature selected on validation seeds.
        box: the hardware limits every action is clipped to.
    """

    weights: list[np.ndarray]
    biases: list[np.ndarray]
    mean: np.ndarray
    scale: np.ndarray
    temperature: float
    box: dict

    @classmethod
    def load(cls, directory: str | Path) -> "KeyRateAgent":
        directory = Path(directory)
        data = np.load(directory / "agente_taxa_chave.npz", allow_pickle=False)
        config = json.loads((directory / "agente_config.json").read_text())
        layers = len([key for key in data.files if key.startswith("actor_")]) // 2
        weights = [np.asarray(data[f"actor_{2 * i}"]) for i in range(layers)]
        biases = [np.asarray(data[f"actor_{2 * i + 1}"]) for i in range(layers)]
        count = float(data["normaliser_count"])
        mean = np.asarray(data["normaliser_mean"])
        variance = (np.asarray(data["normaliser_m2"]) / count if count >= 2
                    else np.ones_like(mean))
        scale = np.sqrt(np.maximum(variance, 1e-8))
        if weights[0].shape[0] != KEYRATE_STATE_FEATURES:
            raise ValueError(f"policy expects {weights[0].shape[0]} inputs, "
                             f"this module builds {KEYRATE_STATE_FEATURES}")
        return cls(weights=weights, biases=biases, mean=mean, scale=scale,
                   temperature=float(config["temperature"]), box=config["box"])

    def logits(self, observation: np.ndarray) -> np.ndarray:
        activation = (np.atleast_2d(np.asarray(observation, dtype=float)) - self.mean) / self.scale
        last = len(self.weights) - 1
        for index, (weight, bias) in enumerate(zip(self.weights, self.biases)):
            activation = activation @ weight + bias
            if index != last:
                activation = np.maximum(activation, 0.0)
        return activation[0]

    def action_probabilities(self, observation: np.ndarray,
                             temperature: float | None = None) -> np.ndarray:
        logits = self.logits(observation)
        scale = self.temperature if temperature is None else temperature
        if scale <= 0:
            probabilities = np.zeros_like(logits)
            probabilities[int(np.argmax(logits))] = 1.0
            return probabilities
        return _softmax(logits / scale)

    def act(self, observation: np.ndarray, rng: np.random.Generator | None = None,
            temperature: float | None = None) -> int:
        """Sample an action at the trained temperature; argmax when it is zero."""
        probabilities = self.action_probabilities(observation, temperature)
        if rng is None or np.count_nonzero(probabilities) == 1:
            return int(np.argmax(probabilities))
        return int(rng.choice(len(probabilities), p=probabilities))

    def apply(self, settings: TransmitterSettings, action_index: int) -> TransmitterSettings:
        """The settings after an action, clipped to the hardware box."""
        box = self.box
        action = ACTIONS[action_index]
        signal = float(np.clip(settings.signal_intensity * action.signal_factor,
                               *box["signal_intensity_range"]))
        decoy = float(np.clip(settings.decoy_intensity * action.decoy_factor,
                              *box["decoy_intensity_range"]))
        decoy = float(min(decoy, signal * box["decoy_ratio_max"]))
        clock = float(np.clip(settings.clock_rate_hz * action.clock_factor,
                              *box["clock_range"]))
        highest = min(box["signal_probability_range"][1],
                      1.0 - settings.vacuum_probability - box["minimum_decoy_probability"])
        probability = float(np.clip(settings.signal_probability + action.signal_probability_step,
                                    box["signal_probability_range"][0], highest))
        return replace(settings, signal_intensity=signal, decoy_intensity=decoy,
                       clock_rate_hz=clock, signal_probability=probability)


# ===========================================================================
# 3. synchroniser
# ===========================================================================

def sync_features(history_s: Sequence[float], temperatures_c: Sequence[float],
                  scale: float = 1.0e-9) -> np.ndarray:
    """Lagged differences from the current estimate, plus temperature change and rate."""
    now = history_s[-1]
    lags = [(history_s[-1 - lag] - now) / scale for lag in range(1, len(history_s))]
    temperature_rate = (temperatures_c[-1] - temperatures_c[0]) / max(len(temperatures_c) - 1, 1)
    return np.asarray(lags + [temperatures_c[-1] - temperatures_c[0], temperature_rate],
                      dtype=float)


@dataclass
class SyncPredictor:
    """Where the pulse will be ``horizon_s`` from now, from the last ``window`` estimates.

    Attributes:
        coefficients, intercept: the ridge fit, on increments in units of ``scale``.
        window: estimates per prediction.
        scale: units of the fitted increments (1 ns).
        update_period_s, horizon_s: one estimate per second, two seconds ahead —
            the loop latency the model was trained for.
    """

    coefficients: np.ndarray
    intercept: float
    window: int
    scale: float
    update_period_s: float
    horizon_s: float

    @classmethod
    def load(cls, directory: str | Path) -> "SyncPredictor":
        config = json.loads((Path(directory) / "sincronizador_ridge.json").read_text())
        return cls(coefficients=np.asarray(config["coefficients"]),
                   intercept=float(config["intercept"]), window=int(config["window"]),
                   scale=float(config["scale"]),
                   update_period_s=float(config["update_period_s"]),
                   horizon_s=float(config["horizon_s"]))

    def predict(self, offsets_s: Sequence[float], temperatures_c: Sequence[float]) -> float:
        """Predicted offset, in seconds, ``horizon_s`` after the last estimate.

        Args:
            offsets_s: the last ``window`` offset estimates, oldest first.
            temperatures_c: the cable temperature at the same instants.

        Raises:
            ValueError: if fewer than ``window`` estimates are given.  In the
                package the first seconds of a session are covered by an
                alpha-beta tracker; that warm-up is not part of this model.
        """
        if len(offsets_s) < self.window or len(temperatures_c) < self.window:
            raise ValueError(f"need the last {self.window} estimates")
        history = list(offsets_s)[-self.window:]
        temperatures = list(temperatures_c)[-self.window:]
        increment = float(sync_features(history, temperatures, self.scale) @ self.coefficients
                          + self.intercept)
        return history[-1] + increment * self.scale
