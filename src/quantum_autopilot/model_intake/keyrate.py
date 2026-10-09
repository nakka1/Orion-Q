"""Optional decoy-state QKD actor, limited to offline recommendations.

Preserves the pinned export's arithmetic, including the historical rate-feature
quirk. No instrument driver, key-security proof, or predicted Bell fidelity.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

import numpy as np

from .artifacts import QKD_ROOT, ReviewedArchive
from .inference import _finite, _offline

ACTIONS = ("manter", "mu +25%", "mu -20%", "nu +25%", "nu -20%", "clock +50%",
           "clock -33%", "p_sinal +0.05", "p_sinal -0.05")
FEATURE_NAMES = (
    "log ganho de sinal / calibração", "log ganho de decoy / calibração",
    "log ganho de vácuo / prob. de escuro", "erro de sinal (%)", "erro de decoy (%)",
    "log Y1 inferior / calibração", "e1 superior (%)", "log taxa provável / calibração",
    "mu", "nu/mu", "log clock / 1 GHz", "prob. de sinal", "log (cliques/s x tempo morto)",
    "log taxa do período anterior / calibração", "log ganho de sinal absoluto",
    "log taxa de calibração / 1 Mbit/s",
)
BOX = {"signal_intensity_range": [0.05, 1.0], "decoy_intensity_range": [0.01, 0.4],
       "clock_range": [1e8, 5e9], "signal_probability_range": [0.4, 0.85],
       "minimum_decoy_probability": 0.05, "decoy_ratio_max": 0.6}
TEMPERATURE = 0.25


def _number(value, name):
    return float(_finite(value, (), name))


@dataclass(frozen=True)
class TransmitterSettings:
    signal_intensity: float
    decoy_intensity: float
    vacuum_probability: float
    signal_probability: float
    clock_rate_hz: float


@dataclass(frozen=True)
class PeriodObservables:
    signal_gain: float
    decoy_gain: float
    vacuum_gain: float
    signal_error: float
    decoy_error: float
    single_photon_yield_lower: float
    single_photon_error_upper: float
    key_rate_hz: float


@dataclass(frozen=True)
class LinkCalibration:
    calibration_gain: float
    calibration_rate_hz: float
    dark_count_probability: float
    misalignment_error: float
    dead_time_s: float


def _settings(settings: TransmitterSettings) -> TransmitterSettings:
    if not isinstance(settings, TransmitterSettings):
        raise ValueError("typed transmitter settings required")
    values = {key: _number(value, key) for key, value in asdict(settings).items()}
    for name, bounds in (("signal_intensity", "signal_intensity_range"),
                         ("decoy_intensity", "decoy_intensity_range"),
                         ("clock_rate_hz", "clock_range"),
                         ("signal_probability", "signal_probability_range")):
        if not BOX[bounds][0] <= values[name] <= BOX[bounds][1]:
            raise ValueError(f"{name} outside reviewed model box")
    if (not 0 <= values["vacuum_probability"] <= 1
            or values["decoy_intensity"] > values["signal_intensity"] * BOX["decoy_ratio_max"] + 1e-12
            or 1 - values["vacuum_probability"] - values["signal_probability"]
            < BOX["minimum_decoy_probability"] - 1e-12):
        raise ValueError("incompatible decoy intensity/probability constraints")
    return TransmitterSettings(**values)


def _calibration(calibration: LinkCalibration) -> LinkCalibration:
    if not isinstance(calibration, LinkCalibration):
        raise ValueError("typed commissioning calibration required")
    values = {key: _number(value, key) for key, value in asdict(calibration).items()}
    if (not 0 < values["calibration_gain"] <= 1 or values["calibration_rate_hz"] <= 0
            or not 0 < values["dark_count_probability"] <= 1
            or not 0 <= values["misalignment_error"] <= 1 or values["dead_time_s"] != 50e-9):
        raise ValueError("invalid calibration or detector dead time outside reviewed export")
    return LinkCalibration(**values)


def _observables(previous: PeriodObservables | None):
    if previous is None:
        return None
    if not isinstance(previous, PeriodObservables):
        raise ValueError("typed previous-period observables required")
    values = {key: _number(value, key) for key, value in asdict(previous).items()}
    if values["key_rate_hz"] < 0 or any(not 0 <= value <= 1 for key, value in values.items()
                                       if key != "key_rate_hz"):
        raise ValueError("gains/errors/yields must be fractions and key rate nonnegative")
    return PeriodObservables(**values)


def build_observation(previous: PeriodObservables | None, settings: TransmitterSettings,
                      calibration: LinkCalibration) -> np.ndarray:
    """16 causal features. No target, future key bits, or free 'previous_rate' argument.

    Features 7 and 13 normally coincide, but NOT when their different original
    log floors apply (notably zero key rate). Do not replace feature 13 with 7.
    """
    settings, calibration, previous = _settings(settings), _calibration(calibration), _observables(previous)
    floor = 1e-12
    gain, rate = calibration.calibration_gain, calibration.calibration_rate_hz
    if previous is None:
        gains = (1., 1., 1.)
        errors = (calibration.misalignment_error,) * 2
        single = (1., calibration.misalignment_error)
        rate_ratio, previous_rate = 1., rate
        absolute_gain = gain
    else:
        gains = (previous.signal_gain / max(gain, floor), previous.decoy_gain / max(gain, floor),
                 previous.vacuum_gain / max(calibration.dark_count_probability, floor))
        errors = (previous.signal_error, previous.decoy_error)
        single = (previous.single_photon_yield_lower / max(gain, floor), previous.single_photon_error_upper)
        rate_ratio, previous_rate = previous.key_rate_hz / max(rate, floor), previous.key_rate_hz
        absolute_gain = previous.signal_gain
    with np.errstate(over="raise", invalid="raise", divide="raise"):
        saturation = settings.clock_rate_hz * absolute_gain * calibration.dead_time_s
        result = np.array([
            np.log(max(gains[0], floor)), np.log(max(gains[1], floor)), np.log(max(gains[2], floor)),
            100. * errors[0], 100. * errors[1], np.log(max(single[0], floor)), 100. * single[1],
            np.log(max(rate_ratio, floor)), settings.signal_intensity,
            settings.decoy_intensity / max(settings.signal_intensity, floor),
            np.log(settings.clock_rate_hz / 1e9), settings.signal_probability,
            np.log(max(saturation, floor)), np.log(max(previous_rate, floor) / max(rate, floor)),
            np.log(max(absolute_gain, floor)), np.log(max(rate, floor) / 1e6),
        ])
    return _finite(result, (16,), "observation")


def _preview(settings: TransmitterSettings, index: int) -> TransmitterSettings:
    settings = _settings(settings)
    if isinstance(index, (bool, np.bool_)) or not isinstance(index, (int, np.integer)) or not 0 <= index < 9:
        raise ValueError("action index must be an integer from 0 to 8")
    signal_factor = 1.25 if index == 1 else 0.8 if index == 2 else 1.
    decoy_factor = 1.25 if index == 3 else 0.8 if index == 4 else 1.
    clock_factor = 1.5 if index == 5 else 0.67 if index == 6 else 1.
    step = .05 if index == 7 else -.05 if index == 8 else 0.
    signal = float(np.clip(settings.signal_intensity * signal_factor, *BOX["signal_intensity_range"]))
    decoy = float(np.clip(settings.decoy_intensity * decoy_factor, *BOX["decoy_intensity_range"]))
    decoy = min(decoy, signal * BOX["decoy_ratio_max"])
    clock = float(np.clip(settings.clock_rate_hz * clock_factor, *BOX["clock_range"]))
    upper = min(BOX["signal_probability_range"][1],
                1 - settings.vacuum_probability - BOX["minimum_decoy_probability"])
    probability = float(np.clip(settings.signal_probability + step, BOX["signal_probability_range"][0], upper))
    return _settings(replace(settings, signal_intensity=signal, decoy_intensity=decoy,
                             clock_rate_hz=clock, signal_probability=probability))


@dataclass(frozen=True)
class KeyRateRecommendation:
    archive_sha256: str
    observation: tuple[float, ...]
    logits: tuple[float, ...]
    probabilities: tuple[float, ...]
    greedy_index: int
    sampled_index: int
    action_name: str
    proposed_settings: TransmitterSettings
    temperature: float = field(default=TEMPERATURE, init=False)
    model_id: str = field(default="qkd.keyrate.actor", init=False)
    evidence: str = field(default="simulated_offline", init=False)
    physical_action_performed: bool = field(default=False, init=False)


class KeyRateReplayModel:
    """Reviewed actor 16-64-64-9 at T=0.25, with explicit replay RNG."""

    def __init__(self, archive: ReviewedArchive):
        if archive.kind != "qkd":
            raise ValueError("QKD archive required")
        meta = archive.json(QKD_ROOT + "modelos/agente_config.json")
        if (meta["temperature"] != TEMPERATURE or tuple(meta["actions"]) != ACTIONS
                or tuple(meta["observation"]) != FEATURE_NAMES or meta["box"] != BOX
                or meta["dead_time_s"] != 50e-9):
            raise ValueError("incompatible key-rate metadata, feature order or action box")
        arrays = archive.arrays(QKD_ROOT + "modelos/agente_taxa_chave.npz")
        keys = {f"{role}_{i}" for role in ("actor", "critic") for i in range(6)}
        keys |= {"normaliser_mean", "normaliser_m2", "normaliser_count"}
        if set(arrays) != keys:
            raise ValueError("unexpected key-rate arrays")
        for role, sizes in (("actor", (16, 64, 64, 9)), ("critic", (16, 64, 64, 1))):
            layers = tuple((_finite(arrays[f"{role}_{2*i}"], (a, b), "weight"),
                            _finite(arrays[f"{role}_{2*i+1}"], (b,), "bias"))
                           for i, (a, b) in enumerate(zip(sizes[:-1], sizes[1:])))
            if role == "actor":
                self._layers = layers  # Critic is validated, not used for recommendation.
        count = _number(arrays["normaliser_count"], "count")
        m2 = _finite(arrays["normaliser_m2"], (16,), "normaliser_m2")
        if count < 2 or np.any(m2 < 0):
            raise ValueError("invalid trained normaliser")
        self._mean = _finite(arrays["normaliser_mean"], (16,), "normaliser_mean")
        self._scale = _finite(np.sqrt(np.maximum(m2 / count, 1e-8)), (16,), "scale")
        self._sha256 = archive.sha256

    def preview_action(self, settings: TransmitterSettings, action_index: int, *, domain: str):
        """Calculate bounded settings only. This method never applies them."""
        _offline(domain)
        return _preview(settings, action_index)

    def recommend(self, previous: PeriodObservables | None, settings: TransmitterSettings,
                  calibration: LinkCalibration, *, domain: str, link_span_km: float,
                  protocol: str, rng: np.random.Generator) -> KeyRateRecommendation:
        _offline(domain)
        if protocol != "decoy_state_bb84" or not 25 <= _number(link_span_km, "link_span_km") <= 80:
            raise ValueError("reviewed model requires decoy_state_bb84 and a declared 25-80 km span")
        if not isinstance(rng, np.random.Generator):
            raise ValueError("explicit random generator required; no implicit greedy deployment")
        observation = build_observation(previous, settings, calibration)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            activation = (observation[None, :] - self._mean) / self._scale
            for i, (weight, bias) in enumerate(self._layers):
                activation = activation @ weight + bias
                if i < 2:
                    activation = np.maximum(activation, 0.)
            logits = _finite(activation[0], (9,), "logits")
            weights = np.exp((logits - np.max(logits)) / TEMPERATURE)
            probabilities = _finite(weights / weights.sum(), (9,), "probabilities")
        # Validate every proposal before consuming caller RNG.
        proposals = tuple(_preview(settings, i) for i in range(9))
        selected = (int(np.argmax(probabilities)) if np.count_nonzero(probabilities) == 1
                    else int(rng.choice(9, p=probabilities)))
        return KeyRateRecommendation(self._sha256, tuple(observation.tolist()), tuple(logits.tolist()),
                                     tuple(probabilities.tolist()), int(np.argmax(probabilities)), selected,
                                     ACTIONS[selected], proposals[selected])
