"""Routing closed loop on the author's repeater environment: benchmark constants, contract,
parity of the reviewed replay with the author's actor-critic in the loop, and the standby."""
import json

import numpy as np
import pytest

from qnet_network.agents import GreedyFidelityPolicy
from qnet_network.agents.actor_critic import ActorCriticPolicy
from quantum_autopilot.control_plane import routing_campaign as campaign
from quantum_autopilot.control_plane import routing_loop as rl
from tests import author_scripts

pytestmark = pytest.mark.unit


class PolicyArchive:
    """A routing export written from an author ActorCriticPolicy, so both can be run side by side."""
    kind = "qnet"
    sha256 = "synthetic-from-author-policy"

    def __init__(self, policy, variant="nominal"):
        self.arrays_ = {f"{role}_{index}": np.array(array, copy=True)
                        for role, network in (("actor", policy.actor), ("critic", policy.critic))
                        for index, array in enumerate(network.parameters)}
        environment = {"topology": {"kind": "chain", "nodes": ["0", "1", "2", "3"],
                                    "links_km": {"0-1": 20.0, "1-2": 20.0, "2-3": 20.0}},
                       "memory_slots_per_node": 6, "memory": {"coherence_time_s": 1.0, "raw_fidelity": 0.9, "cutoff_ratio": 0.1},
                       "gate_fid": 0.999, "meas_fid": 0.999, "coupling_efficiency": 0.7, "detector_efficiency": 0.85,
                       "generate_budget_s": 0.004, "wait_slot_s": 0.002, "attempt_cost": rl.VARIANTS[variant].attempt_cost,
                       "heralding_round_trips": 4.1, "max_steps": 120, "max_pairs_per_link": 3,
                       "request": {"source": "0", "destination": "3", "min_fidelity_choices": [0.7, 0.74, 0.78],
                                   "deadline_s_uniform": [0.04 * rl.VARIANTS[variant].deadline_scale,
                                                          0.09 * rl.VARIANTS[variant].deadline_scale]}}
        self.meta = {"layers": {"actor": 3, "critic": 3}, "features": {"state_count": 11, "action_count": 23},
                     "sizes": {"actor": [34, 64, 64, 1], "critic": [11, 64, 64, 1]},
                     "temperature": rl.VARIANTS[variant].temperature, "environment": environment}

    def json(self, name):
        return json.loads(json.dumps(self.meta))

    def arrays(self, name):
        return {key: value.copy() for key, value in self.arrays_.items()}


def test_benchmark_is_the_authors():
    task = author_scripts.load("qnet_network", "task")
    for variant, settings in rl.VARIANTS.items():
        reference = task.make_config(heralding_round_trips=4.1, attempt_cost=settings.attempt_cost)
        ours = rl.make_config(variant)
        assert {key: getattr(ours, key) for key in vars(ours) if key != "graph"} == \
               {key: getattr(reference, key) for key in vars(reference) if key != "graph"}
        assert sorted(ours.graph.edges(data="length")) == sorted(reference.graph.edges(data="length"))
        for seed in (1, 2, 3):
            assert rl.make_request(np.random.default_rng(seed), variant) == task.make_request(
                np.random.default_rng(seed), settings.deadline_scale)


def test_contract_admits_only_the_declared_environment():
    archive = PolicyArchive(ActorCriticPolicy(rng=4))
    assert rl.check_contract(archive, "nominal", rl.make_env("nominal"))["status"] == "admitted"
    refused = rl.check_contract(archive, "nominal", rl.make_env("prazo_longo"))
    assert refused["status"] == "refused" and "attempt_cost differs from the export" in refused["reasons"]


def test_replay_in_the_loop_equals_the_authors_actor_critic():
    author = ActorCriticPolicy(rng=4)
    archive = PolicyArchive(author)
    author.rng = np.random.default_rng(11)
    ours = rl.ReplayRouter(archive, "nominal", np.random.default_rng(11))
    frame = rl.run("nominal", [ours, author], 700000, 30)
    mine, theirs = (frame[frame["policy"] == name].reset_index(drop=True) for name in (ours.name, author.name))
    assert (mine[["succeeded", "raw_return", "steps", "attempts"]] == theirs[["succeeded", "raw_return", "steps", "attempts"]]).all().all()
    assert ours.faults == 0


def test_refusals_hand_the_episode_to_the_standby(monkeypatch):
    router = rl.ReplayRouter(PolicyArchive(ActorCriticPolicy(rng=4)), "nominal", np.random.default_rng(0), max_faults=2)

    def refuse(*args, **kwargs):
        raise ValueError("contract refusal")

    monkeypatch.setattr(router.model, "evaluate", refuse)
    frame = rl.run("nominal", [router, GreedyFidelityPolicy()], 700100, 5)
    mine, theirs = (frame[frame["policy"] == name].reset_index(drop=True) for name in (router.name, "greedy_fidelity"))
    assert (mine["raw_return"] == theirs["raw_return"]).all()
    assert router.degraded_episodes == 5 and router.faults == 10


def test_block_statistics_are_two_sided():
    blocks = campaign.block_means(np.arange(1000), 20)
    assert blocks.shape == (20,) and blocks[0] == pytest.approx(24.5)


def test_protocol_file_is_frozen(tmp_path):
    path = campaign.Path(__file__).resolve().parents[1] / "configs/optional_models/routing_closed_loop_v1.json"
    assert campaign.load_protocol(path) == (path.read_bytes(), campaign.PROTOCOL)
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["episodes"] = 500
    other = tmp_path / "changed.json"
    other.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from frozen v1"):
        campaign.run_campaign("never-read.zip", other, tmp_path / "out")
    assert not (tmp_path / "out").exists()
