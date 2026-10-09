"""Allocator closed loop on the author's metro plant: benchmark constants, plant contract,
parity of the reviewed replay with the author's control law, and the safe fallback."""
from dataclasses import fields, replace
import json

import numpy as np
import pytest

from qnet_network.allocation import (HybridAllocationEnv, PersistenceForecaster,
                                     PredictiveAllocator, QuantumFirst, build_demands, run_episode)
from qnet_network.network import scenario
from quantum_autopilot.control_plane import allocation_campaign as campaign
from quantum_autopilot.control_plane import allocation_loop as al
from tests import author_scripts

pytestmark = pytest.mark.unit


class PlantArchive:
    """The exported allocator's files, written from the author's environment itself."""
    kind = "qnet"
    sha256 = "synthetic-from-author-plant"

    def __init__(self, env):
        config = env.config
        self.plant = {"config": {**{key: (list(getattr(config, key)) if key == "admission_levels" else getattr(config, key))
                                    for key in al.PLANT_KEYS},
                                 "sifted_rate_hz_at_zero_loss": config.sifted_rate_hz, "coexistence_explicit": False},
                      "coexistence": {item.name: getattr(env.coexistence, item.name) for item in fields(env.coexistence)},
                      "links": [{"index": link.index, "name": link.name, "source": link.source, "target": link.target,
                                 "length_km": link.length_km, "sifted_rate_hz": link.sifted_rate_hz,
                                 "signal_counts_per_gate": link.signal_counts_per_gate,
                                 "classical_power_full_w": link.classical_power_full_w} for link in env.links]}
        self.meta = {"coverage_target": 1.0, "idle_level": 1.0, "horizon_cap_s": 150.0,
                     "forecaster": {"kind": "persistence", "horizon": 30}}

    def json(self, name):
        return json.loads(json.dumps(self.plant if name.endswith("plant.json") else self.meta))

    def read(self, name):
        return json.dumps(self.json(name)).encode()


def short_env(seed=0, steps=24):
    graph = scenario(al.SCENARIO)
    demands = build_demands(graph, duration_s=al.DURATION_S, seed=seed)
    config = replace(al.make_config(), duration_s=steps * al.CONTROL_PERIOD_S)
    return HybridAllocationEnv(graph, demands, config)


def test_benchmark_is_the_authors():
    task4 = author_scripts.load("qnet_network", "task4")
    assert al.make_config() == task4.make_config(finite_key=True)
    env, reference = al.make_env(3), task4.make_env(seed=3, finite_key=True)
    assert [link.name for link in env.links] == [link.name for link in reference.links]
    assert len(env.demands) == len(reference.demands)


def test_contract_admits_only_the_exported_plant():
    env = al.make_env(0)
    archive = PlantArchive(env)
    assert al.check_contract(archive, env)["status"] == "admitted"
    assert al.check_contract(archive, al.make_env(0, finite_key=False))["reasons"] == ["plant finite_key differs from the export"]
    archive.plant["links"][4]["length_km"] += 1.0
    assert "span profiles differ from the export" in al.check_contract(archive, env)["reasons"]


def test_replay_in_the_loop_equals_the_authors_control_law():
    env = short_env()
    archive = PlantArchive(env)
    ours = al.run(env, al.ReplayAllocator(archive, "persistence"), 1001, demand_seed=0)
    author = run_episode(env, PredictiveAllocator(forecaster=PersistenceForecaster(horizon=30), coverage_target=1.0),
                         seed=1001).as_row()
    assert (ours.served_qkd, ours.served_classical, ours.mean_admission) == (
        author["served_qkd"], author["served_classical"], author["mean_admission"])
    assert ours.replay_status_counts == {"persistence": 24} and ours.faults == 0
    assert ours.physical_action_performed is False


def test_refusals_fall_back_to_quantum_first_then_degrade(monkeypatch):
    env = short_env(steps=10)
    policy = al.ReplayAllocator(PlantArchive(env), "persistence", max_faults=2)

    def refuse(*args, **kwargs):
        raise ValueError("contract refusal")

    monkeypatch.setattr(policy.model, "recommend", refuse)
    record = al.run(env, policy, 7, demand_seed=0)
    quantum = run_episode(env, QuantumFirst(), seed=7).as_row()
    assert record.faults == 2 and record.degraded_at_step == 1
    assert record.served_qkd == quantum["served_qkd"] and record.served_classical == quantum["served_classical"]


def test_link_order_must_match_the_reviewed_plant():
    env = short_env(steps=2)
    archive = PlantArchive(env)
    archive.plant["links"] = archive.plant["links"][1:] + archive.plant["links"][:1]
    for index, link in enumerate(archive.plant["links"]):
        link["index"] = index
    with pytest.raises(ValueError, match="link order"):
        al.run(env, al.ReplayAllocator(archive, "persistence"), 1, demand_seed=0)


def test_matched_static_interpolation():
    keys, classical = [0.47, 0.35, 0.26, 0.17, 0.10], [0.0, 0.51, 0.71, 0.88, 1.0]
    assert campaign.matched_static_classical(0.47, keys, classical) == 0.0
    assert campaign.matched_static_classical(0.41, keys, classical) == pytest.approx(0.255)
    assert campaign.matched_static_classical(0.48, keys, classical) is None


def test_primary_tests_and_margin():
    rng = np.random.default_rng(0)
    quantum = {"served_qkd": list(0.47 + 0.01 * rng.random(8)), "served_classical": list(0.10 + 0.01 * rng.random(8))}
    better = {"served_qkd": list(np.asarray(quantum["served_qkd"]) - 0.01),
              "served_classical": list(np.asarray(quantum["served_classical"]) * 2.3)}
    values = {convention: {"baseline.quantum_first": quantum, **{model: better for model in campaign.PROTOCOL["model_ids"]}}
              for convention in campaign.PROTOCOL["conventions"]}
    rows = campaign.primary_tests(values)
    assert len(rows) == 8 and all(row["significant"] for row in rows)
    worse = {"served_qkd": list(np.asarray(quantum["served_qkd"]) - 0.03), "served_classical": better["served_classical"]}
    for block in values.values():
        block["qnet.allocator.ridge"] = worse
    rows = campaign.primary_tests(values)
    failing = [row for row in rows if not row["significant"]]
    assert {(row["model_id"], row["hypothesis"]) for row in failing} == {("qnet.allocator.ridge", "key_non_inferiority")}


def test_protocol_file_is_frozen(tmp_path):
    path = campaign.Path(__file__).resolve().parents[1] / "configs/optional_models/allocation_closed_loop_v1.json"
    assert campaign.load_protocol(path) == (path.read_bytes(), campaign.PROTOCOL)
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["primary"]["margin"] = 0.05
    other = tmp_path / "changed.json"
    other.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from frozen v1"):
        campaign.run_campaign("never-read.zip", other, tmp_path / "out")
    assert not (tmp_path / "out").exists()
