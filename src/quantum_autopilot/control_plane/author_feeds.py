"""Shadow producers drawn from the plants the models were trained on.

The console's shadow panels used to be fed by ORION-Q's own artificial
generators (a 0.003 rad vibration with no photon noise, sine-wave decoy
observables, a fixed allocation queue). Those are replaced here by the author's
plants, the same ones the closed-loop campaigns run:

* polarisation: ``polarisation_plant`` (bit-identical transcription of
  qnet_autopilot's fibre and monitoring) at the calibrated or development point,
  published as the monitored rotation estimates the compensator actually sees;
* timing: ``qnet_qkd`` ``ClockModel`` at 50 km with the 2000-photon offset
  estimate of ``run_sync_loop``;
* key rate: ``qnet_qkd`` ``KeyRateEnv`` at 50 km, held at its commissioned
  settings (the incumbent), publishing each period's observables;
* allocation: ``qnet_network`` ``HybridAllocationEnv`` on the exported
  finite-key plant, driven by quantum-first (the incumbent);
* routing: ``qnet_network`` ``QuantumNetworkEnv`` in the exported nominal
  configuration, driven by the greedy-fidelity heuristic (the incumbent).

In shadow mode no recommendation is ever applied: the incumbent drives the
plant and the model is scored on what it would have done. Each feed is a pure
function of ``(seed, segment, index)``; a segment is one bounded simulation
(30 s of fibre, one hour of clock, one 240-period key-rate episode, one 30-min
allocation episode), after which the stream starts a new segment.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from . import polarisation_plant as plant

POLARISATION_SEGMENT_S = 30.0
POLARISATION_STRIDE = int(round(plant.CONTROL_PERIOD_S / plant.SAMPLE_INTERVAL_S))
CLOCK_SEGMENT_S = 3600
KEYRATE_SPAN_KM = 50.0


def _segment_seed(label: str, seed: int, segment: int) -> int:
    """Stable 63-bit seed per feed, console seed and segment (no Python hash randomisation)."""
    import hashlib

    digest = hashlib.sha256(f"{label}:{int(seed)}:{int(segment)}".encode()).digest()
    return int.from_bytes(digest[:8], "big") >> 1


# --------------------------------------------------------------------------
# Polarisation
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PolarisationSegment:
    point: str
    plant_seed: int
    monitoring_seed: int
    measured: np.ndarray          # (updates, 3, 3) reprojected monitoring estimates, one per 2 ms
    covariates: np.ndarray        # (updates, 3) temperature, dT/dt, vibration RMS

    @property
    def updates(self) -> int:
        return len(self.measured)


@lru_cache(maxsize=4)
def polarisation_segment(point: str, seed: int, segment: int) -> PolarisationSegment:
    plant_seed = _segment_seed(f"polarisation-plant:{point}", seed, segment)
    monitoring_seed = _segment_seed(f"polarisation-monitoring:{point}", seed, segment)
    trajectory = plant.simulate(plant.OPERATING_POINTS[point], POLARISATION_SEGMENT_S, plant_seed)
    indices = np.arange(0, len(trajectory.time_s), POLARISATION_STRIDE)
    measured = plant.measure_rotation(trajectory.rotation[indices], np.random.default_rng(monitoring_seed))
    return PolarisationSegment(point, plant_seed, monitoring_seed, measured, trajectory.covariates()[indices])


# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ClockSegment:
    trajectory_seed: int
    measurement_seed: int
    measured_offset_s: np.ndarray
    temperature_c: np.ndarray


@lru_cache(maxsize=4)
def clock_segment(seed: int, segment: int) -> ClockSegment:
    from qnet_qkd.qkd.timing import MeasurementConfig, measure_offset, timing_spread

    from .sync_loop import PHOTONS, SYNC_PERIOD_S, TRAINED_SPAN_KM, BUDGET, ClockModel

    trajectory_seed = _segment_seed("clock-trajectory", seed, segment)
    measurement_seed = _segment_seed("clock-measurement", seed, segment)
    path = ClockModel(length_km=TRAINED_SPAN_KM).simulate(float(CLOCK_SEGMENT_S), SYNC_PERIOD_S, rng=trajectory_seed)
    measured = measure_offset(path.offset_s, MeasurementConfig(photons=PHOTONS,
                                                               spread_s=timing_spread(BUDGET, TRAINED_SPAN_KM)),
                              rng=measurement_seed)
    return ClockSegment(trajectory_seed, measurement_seed, measured, path.temperature_c)


# --------------------------------------------------------------------------
# Key rate
# --------------------------------------------------------------------------

class KeyRateEpisode:
    """One author episode at 50 km, held at the commissioned settings, advanced one period per call."""

    def __init__(self, seed: int, segment: int):
        from . import keyrate_loop as kr

        self.kr = kr
        self.plant_seed = _segment_seed("keyrate-plant", seed, segment)
        self.env = kr.make_link(KEYRATE_SPAN_KM)
        self.env.reset(rng=self.plant_seed)
        self.periods = 0

    @property
    def finished(self) -> bool:
        return self.periods >= self.env.config.steps

    def publish(self):
        """What the receiver publishes now: previous-period observables (None at bootstrap)."""
        kr = self.kr
        return kr.published(self.env), kr.transmitter(self.env), kr.calibration(self.env)

    def advance(self):
        self.env.step(0)  # the incumbent keeps its commissioned settings; recommendations are not applied
        self.periods += 1


# --------------------------------------------------------------------------
# Allocation
# --------------------------------------------------------------------------

class AllocationEpisode:
    """One author episode on the exported finite-key plant, driven by quantum-first."""

    def __init__(self, seed: int, segment: int):
        from qnet_network.allocation import QuantumFirst

        from . import allocation_loop as al

        self.episode_seed = _segment_seed("allocation-plant", seed, segment) % (2 ** 31)
        self.env = al.make_env(self.episode_seed)
        self.observation = self.env.reset(seed=self.episode_seed)
        self.incumbent = QuantumFirst()
        self.incumbent.reset(self.env)
        self.steps, self.done = 0, False

    def advance(self):
        result = self.env.step(self.incumbent.act(self.observation, self.steps))
        self.observation, self.done = result.observation, result.done
        self.steps += 1


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------

class RoutingEpisodes:
    """Consecutive author episodes (nominal variant), one step per call, driven by greedy fidelity."""

    def __init__(self, seed: int, segment: int):
        from qnet_network.agents import GreedyFidelityPolicy

        from . import routing_loop as rl

        self.rl = rl
        self.base_seed = _segment_seed("routing-plant", seed, segment) % (2 ** 31)
        self.env = rl.make_env("nominal")
        self.incumbent = GreedyFidelityPolicy()
        self.episode, self.decisions = -1, 0
        self._start()

    def _start(self):
        self.episode += 1
        episode_seed = self.base_seed + self.episode
        request = self.rl.make_request(np.random.default_rng(episode_seed), "nominal")
        self.observation, _ = self.env.reset(request=request, memory_size=self.rl.MEMORY_SIZE, rng=episode_seed)
        self.incumbent.reset()
        self.steps = 0

    def decide(self) -> int:
        """What the incumbent does now: asked once per observation, applied by ``advance``."""
        self.pending = int(self.incumbent.act(self.observation))
        return self.pending

    def advance(self):
        actions = self.observation[2]
        self.observation, result = self.env.step(actions[self.pending])
        self.steps += 1
        self.decisions += 1
        if result.done:
            self._start()


__all__ = ["AllocationEpisode", "ClockSegment", "KeyRateEpisode", "PolarisationSegment", "clock_segment",
           "polarisation_segment", "RoutingEpisodes"]
