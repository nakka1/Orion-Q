"""Closed loop of the reviewed synchroniser on the author's thermal delay drift.

Plant and loop are the author's (``qnet_qkd.qkd.timing``): ``ClockModel`` drifts
the arrival time with fibre temperature (about 40 ps per km per kelvin), the
gate estimates the offset every second from a finite photon histogram, and the
predicted position takes effect after a two-second latency. ORION-Q enters as
the predictor: the reviewed ridge replay (``SyncReplayModel``, eight samples, one
second cadence, two-second horizon) forecasts where the gate must be.

The replay refuses fewer than eight samples, so the first seven updates of a run
use the author's own warm-up rule — the alpha-beta tracker that ``LearnedSync``
falls back to — which is recorded, not hidden. The score is the key rate the
residual leaves, computed exactly as the author's evaluation does.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field, replace


from qnet_qkd.physics.decoy import decoy_key_rate_hz, observe
from qnet_qkd.qkd.keyrate import receiver_response
from qnet_qkd.qkd.timing import (AlphaBetaTracker, ClockModel, GateConfig, MeasurementConfig, NoCorrection,
                                 PeriodicCalibration, gate_efficiency, run_sync_loop, timing_spread)

from ..model_intake.inference import SyncReplayModel
from .keyrate_loop import BUDGET, DEAD_TIME_S, LINK, commissioned

TARGET_ID = "qkd.sync.v1"
RECORD_SCHEMA = "orion.sync-loop-run.v1"
MODEL_ID = "qkd.sync.ridge.1s.h2s"
BASELINE_IDS = ("baseline.none", "baseline.periodic_60s", "baseline.alpha_beta")
# Author's timing study (scripts/task5.py).
SYNC_DURATION_S = 3600.0
SYNC_PERIOD_S = 1.0
SYNC_LATENCY_S = 2.0
PHOTONS = 2000
TRAINED_SPAN_KM = 50.0
TRAJECTORY_SEED_OFFSET = 10_000


def make_gate(width_s: float = LINK.gate_width_s) -> GateConfig:
    return GateConfig(width_s=float(width_s), update_period_s=SYNC_PERIOD_S, latency_s=SYNC_LATENCY_S,
                      measurement=MeasurementConfig(photons=PHOTONS), budget=BUDGET)


def trajectory(seed: int, length_km: float = TRAINED_SPAN_KM):
    """One hour of drift, seeded as in the author's evaluation (seed + 10000)."""
    return ClockModel(length_km=float(length_km)).simulate(SYNC_DURATION_S, SYNC_PERIOD_S,
                                                           rng=int(seed) + TRAJECTORY_SEED_OFFSET)


def key_rate_with(length_km: float, gate_width_s: float, efficiency: float, dark_count_rate_hz: float) -> float:
    """Key rate left by a synchroniser's mean gate efficiency (evaluate_phase5._key_rate_with)."""
    link = replace(LINK, length_km=float(length_km), gate_width_s=float(gate_width_s),
                   dark_count_rate_hz=float(dark_count_rate_hz))
    settings = commissioned(float(length_km))
    perfect = float(gate_efficiency(0.0, gate_width_s, timing_spread(BUDGET, length_km)))
    achieved = efficiency / max(perfect, 1e-12)
    degraded = replace(link, coupling_efficiency=link.coupling_efficiency * achieved)
    response = receiver_response(degraded, settings, BUDGET, DEAD_TIME_S)
    return decoy_key_rate_hz(observe(response, settings))


class ReplaySync:
    """The reviewed replay behind the author's ``SyncPredictor`` protocol."""
    controller_id = MODEL_ID
    name = MODEL_ID

    def __init__(self, model: SyncReplayModel):
        self.model = model
        self.reset()

    def reset(self):
        self._times, self._offsets, self._temperatures = (deque(maxlen=8) for _ in range(3))
        self._fallback = AlphaBetaTracker()
        self._fallback.reset()
        self.warmup_updates = self.model_updates = 0

    def predict(self, time_s, measured_s, temperature_c, horizon_s):
        self._times.append(time_s)
        self._offsets.append(measured_s)
        self._temperatures.append(temperature_c)
        fallback = self._fallback.predict(time_s, measured_s, temperature_c, horizon_s)
        if len(self._offsets) < 8:
            self.warmup_updates += 1
            return fallback
        self.model_updates += 1
        return self.model.predict(list(self._times), list(self._offsets), list(self._temperatures),
                                  domain="simulated_offline", horizon_s=horizon_s).predicted_offset_s


def baselines():
    """Author's comparison predictors, with the author's defaults."""
    return {"baseline.none": NoCorrection(), "baseline.periodic_60s": PeriodicCalibration(interval_s=60.0),
            "baseline.alpha_beta": AlphaBetaTracker()}


def check_contract(archive, gate: GateConfig, length_km: float) -> dict:
    from ..model_intake.artifacts import QKD_ROOT

    config = archive.json(QKD_ROOT + "modelos/sincronizador_ridge.json")
    reasons = []
    if gate.update_period_s != config["update_period_s"] or gate.latency_s != config["horizon_s"]:
        reasons.append("gate cadence/latency differs from the exported update period and horizon")
    if gate.measurement.photons != PHOTONS:
        reasons.append("offset estimate uses a different photon budget than training")
    if float(length_km) != TRAINED_SPAN_KM or f"{TRAINED_SPAN_KM:.0f} km" not in config["trained_on"]:
        reasons.append("span differs from the 50 km the synchroniser was trained on")
    return {"model_id": MODEL_ID, "status": "admitted" if not reasons else "refused", "reasons": reasons,
            "archive_sha256": archive.sha256, "length_km": float(length_km)}


@dataclass(frozen=True)
class SyncRecord:
    controller_id: str
    length_km: float
    seed: int
    gate_width_ps: float
    mean_efficiency: float
    p05_efficiency: float
    mean_residual_ps: float
    p95_residual_ps: float
    uptime: float
    key_rate_hz: dict
    warmup_updates: int | None
    archive_sha256: str | None
    schema: str = field(default=RECORD_SCHEMA, init=False)
    target_id: str = field(default=TARGET_ID, init=False)
    mode: str = field(default="simulation", init=False)
    physical_action_performed: bool = field(default=False, init=False)
    simulator_action_performed: bool = field(default=True, init=False)

    def to_dict(self):
        return asdict(self)


def run(seed: int, controller_id: str, predictor, width_s: float, dark_rates_hz, *,
        length_km: float = TRAINED_SPAN_KM, path=None, archive_sha256=None) -> SyncRecord:
    """One hour of the author's loop (measurement noise seeded with ``seed``)."""
    path = path if path is not None else trajectory(seed, length_km)
    result = run_sync_loop(path, predictor, make_gate(width_s), rng=int(seed))
    summary = result.summary()
    rates = {f"{dark:g}": key_rate_with(length_km, width_s, summary["mean_efficiency"], dark) for dark in dark_rates_hz}
    return SyncRecord(controller_id, float(length_km), int(seed), round(width_s * 1e12, 6),
                      summary["mean_efficiency"], summary["p05_efficiency"], summary["mean_residual_ps"],
                      summary["p95_residual_ps"], summary["uptime"], rates,
                      getattr(predictor, "warmup_updates", None), archive_sha256)
