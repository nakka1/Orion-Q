"""Closed loop of the reviewed hybrid allocator on the author's metro plant.

The plant is ``qnet_network.allocation.HybridAllocationEnv`` — the author's
eleven-span São Paulo–Rio corridor, where classical traffic on a span costs key
through Raman scattering — configured exactly as the exported allocator's
``model/plant.json`` declares (5 s control, 30 s reconfiguration latency, five
admission levels, −5 dBm at full admission, finite-block key accounting). The
benchmark constants are the author's ``scripts/task4.py``; the tests compare them.

Every 5 s ORION-Q hands the reviewed replay (``AllocationReplayModel``) what a
controller can know — measured QBER, its own Raman estimate and queued
admissions, key pools, backlog and deadlines — and the plant applies the
admission vector the replay returns, after ``latency_steps`` periods, exactly as
the author's rollout does. A recommendation the replay refuses is replaced by the
safe quantum-first action (silence classical traffic where key is owed); after
``max_faults`` refusals the episode stays quantum-first.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

import numpy as np

from qnet_network.allocation import AllocationConfig, HybridAllocationEnv, QuantumFirst, build_demands
from qnet_network.allocation.rollout import run_episode as author_run_episode
from qnet_network.network import scenario

from ..model_intake.allocation import AllocationObservation, AllocationReplayModel
from ..model_intake.artifacts import ALLOCATION_ROOT

TARGET_ID = "allocation.admission.v1"
RECORD_SCHEMA = "orion.allocation-loop-run.v1"
MODEL_IDS = ("qnet.allocator.persistence", "qnet.allocator.ridge")
# Author's phase-4 benchmark (scripts/task4.py).
SCENARIO = "metro"
CONTROL_PERIOD_S = 5.0
DURATION_S = 1800.0
CLASSICAL_POWER_W = 3.0e-4
PLANT_KEYS = ("control_period_s", "reconfiguration_latency_s", "latency_steps", "duration_s", "admission_levels",
              "estimation_fraction", "qber_threshold", "mean_photon_number", "classical_capacity_bps",
              "classical_power_full_w", "finite_key")


def make_config(finite_key: bool = True) -> AllocationConfig:
    return AllocationConfig(control_period_s=CONTROL_PERIOD_S, duration_s=DURATION_S,
                            classical_power_full_w=CLASSICAL_POWER_W, finite_key=bool(finite_key))


def make_env(demand_seed: int = 0, *, finite_key: bool = True, load_scale: float = 1.0) -> HybridAllocationEnv:
    """task4.make_env: the metro graph and a demand stream drawn from ``demand_seed``."""
    graph = scenario(SCENARIO)
    demands = build_demands(graph, duration_s=DURATION_S, seed=int(demand_seed), load_scale=load_scale)
    return HybridAllocationEnv(graph, demands, make_config(finite_key))


def check_contract(archive, env: HybridAllocationEnv) -> dict:
    """The environment must be the plant the export declares, span by span, before any step."""
    plant = archive.json(ALLOCATION_ROOT + "model/plant.json")
    reasons = []
    config = env.config
    declared = plant["config"]
    for key in PLANT_KEYS:
        value = getattr(config, key)
        value = list(value) if isinstance(value, tuple) else value
        if declared.get(key) != value:
            reasons.append(f"plant {key} differs from the export")
    if declared.get("sifted_rate_hz_at_zero_loss") != config.sifted_rate_hz:
        reasons.append("plant sifted rate differs from the export")
    if declared.get("coexistence_explicit") is not (config.coexistence is not None):
        reasons.append("coexistence parameters were overridden")
    if plant["coexistence"] != {item.name: getattr(env.coexistence, item.name) for item in fields(env.coexistence)}:
        reasons.append("Raman/detector parameters differ from the export")
    links = [{"index": link.index, "name": link.name, "source": link.source, "target": link.target,
              "length_km": link.length_km, "sifted_rate_hz": link.sifted_rate_hz,
              "signal_counts_per_gate": link.signal_counts_per_gate,
              "classical_power_full_w": link.classical_power_full_w} for link in env.links]
    if links != plant["links"]:
        reasons.append("span profiles differ from the export")
    return {"status": "admitted" if not reasons else "refused", "reasons": reasons,
            "archive_sha256": archive.sha256, "finite_key": config.finite_key}


class ReplayAllocator:
    """The reviewed replay behind the author's ``AllocationPolicy`` protocol, with a safe fallback."""

    def __init__(self, archive, variant: str, *, max_faults: int = 3, stream_id: str = "orion-closed-loop"):
        self.model = AllocationReplayModel(archive, variant)
        self.controller_id = self.name = f"qnet.allocator.{variant}"
        self.max_faults, self.stream_id = int(max_faults), stream_id
        self._fallback = QuantumFirst()

    def reset(self, env):
        names = tuple(link.name for link in env.links)
        if names != self.model.link_names:
            raise ValueError("plant link order differs from the reviewed allocator")
        self.model.reset()
        self._fallback.reset(env)
        self._names = names
        self.faults, self.degraded_at_step, self.statuses = 0, None, {}

    @staticmethod
    def observation(observation) -> AllocationObservation:
        return AllocationObservation(
            time_s=float(observation.time_s), measured_qber=tuple(map(float, observation.measured_qber)),
            pending_admission=tuple(tuple(map(float, row)) for row in observation.pending_admission),
            raman_qber_estimate=tuple(map(float, observation.raman_qber_estimate)),
            key_pool_bits=tuple(map(float, observation.key_pool_bits)),
            backlog_bits=tuple(map(float, observation.backlog_bits)),
            urgency_s=tuple(None if not np.isfinite(value) else float(value) for value in observation.urgency_s))

    def act(self, observation, step):
        if self.degraded_at_step is None:
            try:
                result = self.model.recommend(self.observation(observation), domain="simulated_offline",
                                              protocol="single_photon_bb84", link_names=self._names,
                                              stream_id=self.stream_id)
                self.statuses[result.status] = self.statuses.get(result.status, 0) + 1
                return np.asarray(result.admission, dtype=float)
            except ValueError:
                self.faults += 1
                if self.faults >= self.max_faults:
                    self.degraded_at_step = int(step)
                # The replay's window has a gap now; it restarts from warm-up.
                self.model.reset()
        return self._fallback.act(observation, step)


@dataclass(frozen=True)
class AllocationRecord:
    controller_id: str
    episode_seed: int
    demand_seed: int
    finite_key: bool
    served_qkd: float
    served_classical: float
    served_entanglement: float
    mean_admission: float
    mean_qber: float
    dead_link_fraction: float
    key_bits: float
    classical_bits: float
    faults: int | None
    degraded_at_step: int | None
    replay_status_counts: dict | None
    archive_sha256: str | None
    schema: str = field(default=RECORD_SCHEMA, init=False)
    target_id: str = field(default=TARGET_ID, init=False)
    mode: str = field(default="simulation", init=False)
    physical_action_performed: bool = field(default=False, init=False)
    simulator_action_performed: bool = field(default=True, init=False)

    def to_dict(self):
        return asdict(self)


def run(env, policy, episode_seed: int, *, demand_seed: int, controller_id: str | None = None,
        archive_sha256: str | None = None) -> AllocationRecord:
    """One author episode (``allocation.rollout.run_episode``) with the policy in the loop."""
    result = author_run_episode(env, policy, seed=int(episode_seed), keep_traces=False)
    row = result.as_row()
    return AllocationRecord(controller_id or getattr(policy, "controller_id", policy.name), int(episode_seed),
                            int(demand_seed), bool(env.config.finite_key), row["served_qkd"], row["served_classical"],
                            row["served_entanglement"], row["mean_admission"], row["mean_qber"],
                            row["dead_link_fraction"], row["key_bits"], row["classical_bits"],
                            getattr(policy, "faults", None), getattr(policy, "degraded_at_step", None),
                            dict(getattr(policy, "statuses", {})) or None, archive_sha256)
