"""Closed loop of the reviewed key-rate actor on the author's decoy-state link.

The plant is not a stand-in. It is ``qnet_qkd.qkd.keyrate.KeyRateEnv`` — the
code of the QKD source tree that produced the pinned ``modelo_qkd_ml.zip`` —
configured at the author's benchmark operating point (``scripts/task5.py``,
transcribed below and checked against the vendored script by the tests).

Every period ORION-Q reads only what a receiver publishes — the previous
period's gains and error rates with their decoy bounds — plus the commissioning
calibration; it never reads the hidden loss or misalignment walk. The reviewed
replay (``KeyRateReplayModel``, T=0.25, explicit generator) proposes one of nine
bounded adjustments; the transmitter executes that index and the loop checks,
bit for bit, that the settings the plant adopted are the settings ORION-Q
proposed. A proposal the contract refuses is replaced by *manter*; after
``max_faults`` refusals the actor is dropped and the transmitter holds its last
verified settings for the rest of the episode.

Two reproduction gates tie the loop to the manuscript: the exported 40-period
example (actions sampled with ``default_rng(seed)``, settings and key bits
identical) and the published comparison table (Tabela 7 of the QKD manuscript,
tab:taxa of the TCC), which uses the author's own evaluation convention for the
sampling generator (a fresh ``KeyRatePolicy(rng=0)`` per span).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from functools import lru_cache

import numpy as np

from qnet_qkd.physics.decoy import DecoySettings, decoy_key_rate_hz, single_photon_bounds
from qnet_qkd.qkd.keyrate import ACTIONS, KeyRateEnv, KeyRateEnvConfig, OraclePolicy, commissioned_settings
from qnet_qkd.qkd.session import LinkConditions
from qnet_qkd.qkd.timing import TimingBudget

from ..model_intake.keyrate import (KeyRateReplayModel, LinkCalibration, PeriodObservables,
                                    TransmitterSettings)

TARGET_ID = "qkd.keyrate.v1"
RECORD_SCHEMA = "orion.keyrate-loop-run.v1"
ACTOR_ID = "qkd.keyrate.actor"
STATIC_ID = "baseline.static"
ORACLE_ID = "reference.oracle"
SPAN_RANGE_KM = (25.0, 80.0)

# Benchmark operating point of the author's phase 5 (scripts/task5.py).
LINK = LinkConditions(length_km=50.0, excess_loss_db=1.0, coupling_efficiency=0.85,
                      detector_efficiency=0.7, dark_count_rate_hz=10.0, misalignment_error=0.015)
SETTINGS = DecoySettings(signal_intensity=0.5, decoy_intensity=0.1, signal_probability=0.7,
                         vacuum_probability=0.1, clock_rate_hz=1.0e9)
BUDGET = TimingBudget()
DEAD_TIME_S = 50.0e-9
AFTERPULSE_PROBABILITY = 0.01
PERIOD_S = 1.0
STEPS = 240


# A global scenario has more than 16 distinct spans. Retain immutable settings
# for the same bounded 512-span working set as the acquisition commissioning
# cache; eviction must not force the static grid search on every source change.
@lru_cache(maxsize=512)
def commissioned(length_km: float) -> DecoySettings:
    """The static operating point an engineer would leave the span at (task5.make_commissioned)."""
    return commissioned_settings(replace(LINK, length_km=float(length_km)), SETTINGS, BUDGET,
                                 DEAD_TIME_S, AFTERPULSE_PROBABILITY)


def make_link(length_km: float, steps: int = STEPS) -> KeyRateEnv:
    """A fresh author environment at the benchmark point (task5.make_keyrate_env, rng=0)."""
    config = KeyRateEnvConfig(link=replace(LINK, length_km=float(length_km)), initial=commissioned(length_km),
                              budget=BUDGET, period_s=PERIOD_S, steps=int(steps), dead_time_s=DEAD_TIME_S,
                              afterpulse_probability=AFTERPULSE_PROBABILITY)
    return KeyRateEnv(config, rng=0)


# --------------------------------------------------------------------------
# What the receiver publishes, and the transmitter's own state
# --------------------------------------------------------------------------

def published(env: KeyRateEnv) -> PeriodObservables | None:
    """Previous period's observables with their decoy bounds; nothing hidden."""
    previous = env._last_observables
    if previous is None:
        return None
    yield_lower, error_upper = single_photon_bounds(previous)
    return PeriodObservables(signal_gain=previous.signal_gain, decoy_gain=previous.decoy_gain,
                             vacuum_gain=previous.vacuum_gain, signal_error=previous.signal_error,
                             decoy_error=previous.decoy_error, single_photon_yield_lower=yield_lower,
                             single_photon_error_upper=error_upper, key_rate_hz=decoy_key_rate_hz(previous))


def transmitter(env: KeyRateEnv) -> TransmitterSettings:
    settings = env.settings
    return TransmitterSettings(signal_intensity=settings.signal_intensity,
                               decoy_intensity=settings.decoy_intensity,
                               vacuum_probability=settings.vacuum_probability,
                               signal_probability=settings.signal_probability,
                               clock_rate_hz=settings.clock_rate_hz)


def calibration(env: KeyRateEnv) -> LinkCalibration:
    """Commissioning values of the span as drawn, not the drifting conditions."""
    return LinkCalibration(calibration_gain=env._calibration_gain, calibration_rate_hz=env._calibration_rate,
                           dark_count_probability=env.link.dark_count_probability,
                           misalignment_error=env.link.misalignment_error, dead_time_s=env.config.dead_time_s)


def check_contract(model: KeyRateReplayModel, archive, env: KeyRateEnv, length_km: float) -> dict:
    """The plant must be the domain the actor was exported for, before any period runs."""
    from ..model_intake.artifacts import QKD_ROOT
    from ..model_intake.keyrate import ACTIONS as REVIEWED_ACTIONS, BOX

    meta = archive.json(QKD_ROOT + "modelos/agente_config.json")
    config = env.config
    plant_box = {"signal_intensity_range": list(config.signal_intensity_range),
                 "decoy_intensity_range": list(config.decoy_intensity_range),
                 "clock_range": list(config.clock_range),
                 "signal_probability_range": list(config.signal_probability_range),
                 "minimum_decoy_probability": config.minimum_decoy_probability,
                 "decoy_ratio_max": config.decoy_ratio_max}
    reasons = []
    if tuple(action.name for action in ACTIONS) != REVIEWED_ACTIONS:
        reasons.append("plant action set differs from the reviewed actions")
    if plant_box != BOX or meta["box"] != plant_box:
        reasons.append("plant action box differs from the exported box")
    if config.dead_time_s != meta["dead_time_s"]:
        reasons.append("detector dead time differs from the export")
    if not SPAN_RANGE_KM[0] <= float(length_km) <= SPAN_RANGE_KM[1]:
        reasons.append("span outside the 25-80 km training range")
    return {"model_id": ACTOR_ID, "status": "admitted" if not reasons else "refused",
            "reasons": reasons, "archive_sha256": archive.sha256, "length_km": float(length_km)}


# --------------------------------------------------------------------------
# Controllers
# --------------------------------------------------------------------------

class StaticController:
    controller_id = STATIC_ID

    def decide(self, env, span_km):
        return 0, None


class OracleController:
    """Author's one-step oracle: reads the true channel. A reference, not deployable."""
    controller_id = ORACLE_ID

    def decide(self, env, span_km):
        return int(OraclePolicy(env=env).act(None)), None


class ActorController:
    """The reviewed replay; the caller owns the generator (no implicit greedy)."""
    controller_id = ACTOR_ID

    def __init__(self, model: KeyRateReplayModel, rng: np.random.Generator):
        self.model, self.rng = model, rng

    def decide(self, env, span_km):
        result = self.model.recommend(published(env), transmitter(env), calibration(env),
                                      domain="simulated_offline", link_span_km=span_km,
                                      protocol="decoy_state_bb84", rng=self.rng)
        return result.sampled_index, result


@dataclass(frozen=True)
class KeyRateSupervisorConfig:
    max_faults: int = 8


@dataclass(frozen=True)
class KeyRateRecord:
    controller_id: str
    length_km: float
    plant_seed: int
    policy_seed: int | None
    periods: int
    key_bits: float
    mean_key_rate_hz: float
    zero_key_periods: int
    action_counts: dict
    final_settings: dict
    key_rate_trace_hz: tuple
    proposals_verified: int
    faults: int
    degraded_at_period: int | None
    archive_sha256: str | None
    schema: str = field(default=RECORD_SCHEMA, init=False)
    target_id: str = field(default=TARGET_ID, init=False)
    mode: str = field(default="simulation", init=False)
    physical_action_performed: bool = field(default=False, init=False)
    simulator_action_performed: bool = field(default=True, init=False)

    def to_dict(self):
        return asdict(self)


def _same(settings: DecoySettings, proposal: TransmitterSettings) -> bool:
    return all(getattr(settings, name) == getattr(proposal, name) for name in TransmitterSettings.__dataclass_fields__)


def run_episode(length_km: float, plant_seed: int, controller, *, policy_seed: int | None = None,
                supervisor: KeyRateSupervisorConfig | None = None, env: KeyRateEnv | None = None,
                steps: int = STEPS, archive_sha256: str | None = None, collect=None) -> KeyRateRecord:
    """One episode; the author's ``run_episode`` semantics plus the proposal check and supervisor."""
    supervisor = supervisor or KeyRateSupervisorConfig()
    env = env or make_link(length_km, steps)
    env.reset(rng=plant_seed)
    total = 0.0
    zero = faults = verified = 0
    degraded_at = None
    counts = {action.name: 0 for action in ACTIONS}
    trace = []
    for period in range(env.config.steps):
        proposal = None
        if degraded_at is None:
            try:
                index, recommendation = controller.decide(env, float(length_km))
                proposal = None if recommendation is None else recommendation.proposed_settings
            except ValueError:
                index, faults = 0, faults + 1
                if faults >= supervisor.max_faults:
                    degraded_at = period
        else:
            index = 0
        if collect is not None:
            collect(period, env, index, proposal)
        _, _, done, info = env.step(int(index))
        if proposal is not None:
            if not _same(env.settings, proposal):
                raise RuntimeError(f"plant adopted settings different from the ORION-Q proposal at period {period}")
            verified += 1
        counts[ACTIONS[int(index)].name] += 1
        bits = float(info["key_bits"])
        total += bits
        zero += int(bits <= 0.0)
        trace.append(float(info["key_rate_hz"]))
        if done:
            break
    duration = env.config.steps * env.config.period_s
    return KeyRateRecord(controller.controller_id, float(length_km), int(plant_seed), policy_seed,
                         len(trace), total, total / duration, zero, counts, asdict(env.settings),
                         tuple(trace), verified, faults, degraded_at, archive_sha256)


# --------------------------------------------------------------------------
# Reproduction gates
# --------------------------------------------------------------------------

EXAMPLE = "exemplos/agente_episodio_50km.json"
FEATURE_ATOL = 1e-8     # the export rounds observations to nine decimals
PROBABILITY_ATOL = 1e-6  # and probabilities to six
#: The export's link calibration is written at full precision; another libm can move its
#: last bit (Windows: 3.5e-16 relative), so it is compared to this relative tolerance.
CALIBRATION_RTOL = 1e-12


def reproduce_export_episode(archive) -> dict:
    """Regenerate the author's exported 40-period episode in closed loop.

    The export ran ``env.reset(rng=seed)`` and sampled with a separate
    ``np.random.default_rng(seed)`` (``scripts/export_models.py``). With both, the
    sampled actions, the adopted settings and the key bits of every period must
    be identical, not merely close. The link calibration must agree to
    ``CALIBRATION_RTOL`` (identical on Linux, where the export was written).
    """
    from ..model_intake.artifacts import QKD_ROOT

    model = KeyRateReplayModel(archive)
    episode = archive.json(QKD_ROOT + EXAMPLE)
    span, seed, rows = float(episode["vão_km"]), int(episode["semente"]), episode["periodos"]
    env = make_link(span)
    env.reset(rng=seed)
    declared = episode["calibração_do_enlace"]
    actual = asdict(calibration(env))
    calibration_equal = actual == declared
    calibration_error = (max(abs(float(actual[key]) - float(value)) / max(abs(float(value)), 1e-300)
                             if float(actual[key]) != float(value) else 0.0 for key, value in declared.items())
                         if set(actual) == set(declared) else float("inf"))
    generator = np.random.default_rng(seed)
    mismatches = {"sampled_action": 0, "settings": 0, "key_bits": 0, "greedy_action": 0}
    feature_error = probability_error = 0.0
    for row in rows:
        result = model.recommend(published(env), transmitter(env), calibration(env), domain="simulated_offline",
                                 link_span_km=span, protocol="decoy_state_bb84", rng=generator)
        expected = row["saida"]
        feature_error = max(feature_error, float(np.max(np.abs(np.asarray(result.observation)
                                                                - np.asarray(row["observacao_16"])))))
        reference = np.asarray([expected["probabilidades"][name] for name in (a.name for a in ACTIONS)])
        probability_error = max(probability_error, float(np.max(np.abs(np.asarray(result.probabilities) - reference))))
        mismatches["greedy_action"] += ACTIONS[result.greedy_index].name != expected["acao_gulosa"]
        mismatches["sampled_action"] += result.action_name != expected["acao_amostrada_T=0.25"]
        _, _, _, info = env.step(result.sampled_index)
        mismatches["settings"] += not (_same(env.settings, result.proposed_settings)
                                       and asdict(result.proposed_settings) == expected["novos_parametros"])
        mismatches["key_bits"] += float(info["key_bits"]) != row["resultado_no_simulador"]["bits_de_chave"]
    passed = (calibration_error <= CALIBRATION_RTOL and not any(mismatches.values()) and len(rows) == 40
              and feature_error <= FEATURE_ATOL and probability_error <= PROBABILITY_ATOL)
    return {"gate": "export_episode", "model_id": ACTOR_ID, "archive_sha256": archive.sha256,
            "length_km": span, "plant_seed": seed, "policy_generator": f"numpy.random.default_rng({seed})",
            "periods": len(rows), "calibration_identical": calibration_equal,
            "calibration_max_relative_error": calibration_error, "mismatches": mismatches,
            "max_feature_absolute_error": feature_error, "max_probability_absolute_error": probability_error,
            "passed": bool(passed)}


def author_policy_generator() -> np.random.Generator:
    """The sampling generator of the author's evaluation (``_load_policy``: KeyRatePolicy(rng=0)).

    Only its random stream is used; the weights of that freshly initialised
    policy are discarded — actions come from the reviewed replay.
    """
    from qnet_qkd.qkd.keyrate import KeyRatePolicy

    return KeyRatePolicy(rng=0).rng
