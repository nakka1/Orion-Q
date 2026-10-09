"""Closed loop of the reviewed routing agent on the author's repeater environment.

The plant is ``qnet_network.env.QuantumNetworkEnv`` — the author's analytic
repeater chain (four nodes, 20 km spans, six memory slots, heralding charged at
the 4.1 span traversals measured against SeQUeNCe) — configured exactly as the
exported policy's ``model/policy.json`` declares, for its two variants:

* ``nominal``: deadlines 40-90 ms, 0.01 per attempt, sampled at T = 1;
* ``prazo_longo``: deadlines ×4 (160-360 ms), 0.0025 per attempt, greedy.

Each step ORION-Q hands the reviewed replay (``RoutingReplayModel``) the eleven
state features and the 23 features of every legal action; the environment
executes the action at the index the replay returns. A step the replay refuses
is taken by the author's best heuristic (greedy fidelity) as hot standby; after
``max_faults`` refusals the heuristic finishes the episode. The benchmark
constants are the author's ``scripts/task.py``; the tests compare them.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from qnet_network.agents import GreedyFidelityPolicy, evaluate
from qnet_network.env import EnvironmentConfig, QuantumNetworkEnv, Request
from qnet_network.network import scenario
from qnet_network.physics.memory import MemoryParameters

from ..model_intake.artifacts import ROUTING_ROOT
from ..model_intake.inference import RoutingReplayModel

TARGET_ID = "routing.repeater.v1"
MEMORY_SIZE = 6
HERALDING_ROUND_TRIPS = 4.1


@dataclass(frozen=True)
class Variant:
    attempt_cost: float
    deadline_scale: float
    temperature: float


VARIANTS = {"nominal": Variant(0.01, 1.0, 1.0), "prazo_longo": Variant(0.0025, 4.0, 0.0)}


def make_config(variant: str) -> EnvironmentConfig:
    """task.make_config(heralding_round_trips=4.1, attempt_cost=...) of the author."""
    return EnvironmentConfig(
        graph=scenario("chain", num_nodes=4, span_km=20), heralding_round_trips=HERALDING_ROUND_TRIPS,
        memory=MemoryParameters(coherence_time_s=1.0, raw_fidelity=0.90, cutoff_ratio=0.1),
        gate_fid=0.999, meas_fid=0.999, coupling_efficiency=0.7, detector_efficiency=0.85,
        generate_budget_s=0.004, wait_slot_s=0.002, attempt_cost=VARIANTS[variant].attempt_cost,
        max_steps=120, max_pairs_per_link=3, shaping_weight=0.0, gamma=0.995)


def make_env(variant: str) -> QuantumNetworkEnv:
    return QuantumNetworkEnv(make_config(variant), rng=0)


def make_request(rng: np.random.Generator, variant: str) -> Request:
    """task.make_request: threshold and deadline drawn per episode."""
    return Request(source="0", destination="3", min_fidelity=float(rng.choice([0.70, 0.74, 0.78])),
                   deadline_s=float(rng.uniform(0.040, 0.090)) * VARIANTS[variant].deadline_scale)


def check_contract(archive, variant: str, env: QuantumNetworkEnv) -> dict:
    declared = archive.json(ROUTING_ROOT + variant + "/model/policy.json")["environment"]
    config = env.config
    graph = config.graph
    reasons = []
    topology = {"kind": "chain", "nodes": sorted(str(node) for node in graph.nodes()),
                "links_km": {f"{min(u, v)}-{max(u, v)}": float(data["length"]) for u, v, data in graph.edges(data=True)}}
    if topology != {**declared["topology"], "nodes": sorted(declared["topology"]["nodes"])}:
        reasons.append("repeater topology differs from the export")
    memory = {"coherence_time_s": config.memory.coherence_time_s, "raw_fidelity": config.memory.raw_fidelity,
              "cutoff_ratio": config.memory.cutoff_ratio}
    if memory != declared["memory"]:
        reasons.append("memory parameters differ from the export")
    for key in ("gate_fid", "meas_fid", "coupling_efficiency", "detector_efficiency", "generate_budget_s",
                "wait_slot_s", "attempt_cost", "heralding_round_trips", "max_steps", "max_pairs_per_link"):
        if getattr(config, key) != declared[key]:
            reasons.append(f"{key} differs from the export")
    if declared["memory_slots_per_node"] != MEMORY_SIZE:
        reasons.append("memory slots differ from the export")
    request = declared["request"]
    scale = VARIANTS[variant].deadline_scale
    if (request["min_fidelity_choices"] != [0.7, 0.74, 0.78]
            or request["deadline_s_uniform"] != [round(0.040 * scale, 12), round(0.090 * scale, 12)]):
        reasons.append("request distribution differs from the export")
    return {"model_id": f"qnet.routing.{variant}", "status": "admitted" if not reasons else "refused",
            "reasons": reasons, "archive_sha256": archive.sha256}


class ReplayRouter:
    """The reviewed replay behind the author's ``Policy`` protocol, with a heuristic hot standby."""

    def __init__(self, archive, variant: str, rng: np.random.Generator, *, max_faults: int = 3):
        self.model = RoutingReplayModel(archive, variant)
        self.controller_id = self.name = f"qnet.routing.{variant}"
        self.rng, self.max_faults = rng, int(max_faults)
        self._standby = GreedyFidelityPolicy()
        self.faults = 0
        self.degraded_episodes = 0

    def reset(self):
        self._standby.reset()
        self._episode_faults, self._degraded = 0, False

    def act(self, observation):
        if not self._degraded:
            state, features, actions = observation
            try:
                return self.model.evaluate(state, features, [{"kind": action.kind.name} for action in actions],
                                           domain="simulated_offline", rng=self.rng).sampled_index
            except ValueError:
                self.faults += 1
                self._episode_faults += 1
                if self._episode_faults >= self.max_faults:
                    self._degraded = True
                    self.degraded_episodes += 1
        return self._standby.act(observation)


def run(variant: str, policies, seed: int, episodes: int):
    """The author's ``agents.evaluate`` harness: episode i uses seed + i for request and plant."""
    result = evaluate(lambda: make_env(variant), policies, episodes=episodes,
                      request_factory=lambda rng: make_request(rng, variant), memory_size=MEMORY_SIZE, seed=seed)
    return result.frame
