"""Synchroniser closed loop on the author's thermal-drift model: the reviewed replay behind
the author's predictor protocol, the key-rate score, the contract and the warm-up rule."""
from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest

from qnet_qkd.qkd.timing import LearnedSync, run_sync_loop
from quantum_autopilot.control_plane import sync_loop as sy
from quantum_autopilot.model_intake.inference import SyncReplayModel
from tests import author_scripts

pytestmark = pytest.mark.unit
COEFFICIENTS = [0.0141, -0.0008, -0.0181, -0.0056, 0.0148, 0.0005, -0.0852, -0.1534, -0.0219]


class SyntheticSync:
    kind = "qkd"
    sha256 = "synthetic-for-tests-only"

    def __init__(self):
        self.meta = {"coefficients": list(COEFFICIENTS), "intercept": 0.0023, "window": 8, "scale": 1e-9,
                     "update_period_s": 1.0, "horizon_s": 2.0,
                     "trained_on": "28728 janelas de 8 trajetórias de 1 h a 50 km"}

    def json(self, _name):
        return deepcopy(self.meta)


class Linear:
    """Stands in for the author's fitted sklearn Ridge: X @ coef + intercept."""

    def predict(self, rows):
        return rows @ np.asarray(COEFFICIENTS) + 0.0023


def test_gate_and_constants_are_the_authors():
    task5 = author_scripts.load("qnet_qkd", "task5")
    assert sy.make_gate(350e-12) == task5.make_gate(width_s=350e-12)
    assert (sy.SYNC_DURATION_S, sy.SYNC_PERIOD_S, sy.SYNC_LATENCY_S) == (
        task5.SYNC_DURATION_S, task5.SYNC_PERIOD_S, task5.SYNC_LATENCY_S)


def test_key_rate_score_is_the_authors():
    evaluation = author_scripts.load("qnet_qkd", "evaluate_phase5")
    for width, efficiency, dark in ((200e-12, 0.61, 10.0), (1000e-12, 0.97, 1e6)):
        assert sy.key_rate_with(50.0, width, efficiency, dark) == evaluation._key_rate_with(50.0, width, efficiency, dark)


def test_replay_in_the_loop_matches_the_authors_learned_predictor():
    full = sy.trajectory(600)
    path = replace(full, time_s=full.time_s[:400], offset_s=full.offset_s[:400],
                   temperature_c=full.temperature_c[:400], components={})
    replay = sy.ReplaySync(SyncReplayModel(SyntheticSync()))
    ours = run_sync_loop(path, replay, sy.make_gate(200e-12), rng=600)
    author = run_sync_loop(path, LearnedSync(model=Linear()), sy.make_gate(200e-12), rng=600)
    np.testing.assert_allclose(ours.residual_s, author.residual_s, rtol=0, atol=1e-18)
    assert replay.warmup_updates == 7 and replay.model_updates == len(path.time_s) - 7


def test_record_states_simulation_and_warmup():
    record = sy.run(601, sy.MODEL_ID, sy.ReplaySync(SyncReplayModel(SyntheticSync())), 500e-12, (10.0, 1e6))
    assert record.warmup_updates == 7 and set(record.key_rate_hz) == {"10", "1e+06"}
    assert record.physical_action_performed is False and record.mode == "simulation"
    assert record.key_rate_hz["10"] > record.key_rate_hz["1e+06"] >= 0


def test_contract_admits_only_the_trained_span_and_loop():
    archive = SyntheticSync()
    assert sy.check_contract(archive, sy.make_gate(), 50.0)["status"] == "admitted"
    assert sy.check_contract(archive, sy.make_gate(), 80.0)["reasons"] == [
        "span differs from the 50 km the synchroniser was trained on"]
    from qnet_qkd.qkd.timing import GateConfig
    slow = GateConfig(width_s=200e-12, update_period_s=1.0, latency_s=3.0)
    assert "gate cadence/latency differs from the exported update period and horizon" in sy.check_contract(
        archive, slow, 50.0)["reasons"]
