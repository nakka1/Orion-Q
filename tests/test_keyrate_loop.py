"""Key-rate closed loop on the author's decoy-state link: mapping, parity with the author's
episode runner, proposal verification, supervisor, contract and (with the pinned export)
the regenerated 40-period example."""
from dataclasses import asdict, replace
import os
from pathlib import Path

import numpy as np
import pytest

from qnet_qkd.qkd.keyrate import OraclePolicy, StaticPolicy, run_episode as author_run_episode
from quantum_autopilot.control_plane import keyrate_loop as kr
from quantum_autopilot.model_intake.artifacts import ReviewedArchive
from quantum_autopilot.model_intake.keyrate import KeyRateReplayModel
from tests import author_scripts
from tests.test_optional_keyrate import SyntheticQKD

pytestmark = pytest.mark.unit
QKD_ZIP = os.environ.get("ORION_QKD_ARCHIVE", "")


def real_archive():
    if not QKD_ZIP or not Path(QKD_ZIP).exists():
        pytest.skip("pinned modelo_qkd_ml.zip not supplied (set ORION_QKD_ARCHIVE)")
    return ReviewedArchive(QKD_ZIP, "qkd")


def test_benchmark_point_is_the_authors():
    task5 = author_scripts.load("qnet_qkd", "task5")
    assert kr.LINK == task5.LINK and kr.SETTINGS == task5.SETTINGS and kr.BUDGET == task5.BUDGET
    assert (kr.DEAD_TIME_S, kr.AFTERPULSE_PROBABILITY, kr.PERIOD_S, kr.STEPS) == (
        task5.DEAD_TIME_S, task5.AFTERPULSE_PROBABILITY, task5.PERIOD_S, task5.KEYRATE_STEPS)
    assert kr.commissioned(50.0) == task5.make_commissioned(50.0)
    assert kr.make_link(35.0).config == task5.make_keyrate_config(35.0)


@pytest.mark.parametrize("controller, policy", [(kr.StaticController(), StaticPolicy()), (kr.OracleController(), None)])
def test_loop_equals_authors_episode_runner(controller, policy):
    env = kr.make_link(65.0, steps=30)
    reference = author_run_episode(env, policy or OraclePolicy(env=env), seed=950001)
    ours = kr.run_episode(65.0, 950001, controller, env=kr.make_link(65.0, steps=30))
    assert ours.key_bits == reference.key_bits and ours.zero_key_periods == reference.zero_key_periods
    assert ours.final_settings == asdict(reference.final_settings)
    assert ours.physical_action_performed is False and ours.mode == "simulation"


def test_publication_uses_only_receiver_observables():
    env = kr.make_link(50.0, steps=5)
    env.reset(rng=3)
    assert kr.published(env) is None
    env.step(0)
    published = kr.published(env)
    assert published.signal_gain == env._last_observables.signal_gain
    calibration = kr.calibration(env)
    # Commissioning values of the span, never the drifting hidden conditions.
    assert calibration.misalignment_error == env.link.misalignment_error != env._misalignment


def test_actor_proposals_are_verified_against_the_plant():
    model = KeyRateReplayModel(SyntheticQKD())
    record = kr.run_episode(50.0, 7, kr.ActorController(model, np.random.default_rng(7)), steps=12)
    assert record.proposals_verified == record.periods == 12 and record.faults == 0


def test_plant_adopting_other_settings_stops_the_loop(monkeypatch):
    model = KeyRateReplayModel(SyntheticQKD())
    original = model.recommend

    def drifted(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, proposed_settings=replace(result.proposed_settings, signal_intensity=0.123))

    monkeypatch.setattr(model, "recommend", drifted)
    with pytest.raises(RuntimeError, match="different from the ORION-Q proposal"):
        kr.run_episode(50.0, 7, kr.ActorController(model, np.random.default_rng(7)), steps=3)


def test_supervisor_holds_then_drops_a_refusing_actor():
    class Refusing:
        controller_id = kr.ACTOR_ID

        def decide(self, env, span):
            raise ValueError("contract refusal")

    record = kr.run_episode(50.0, 11, Refusing(), steps=20, supervisor=kr.KeyRateSupervisorConfig(max_faults=3))
    static = kr.run_episode(50.0, 11, kr.StaticController(), steps=20)
    assert record.faults == 3 and record.degraded_at_period == 2
    assert record.key_bits == static.key_bits and record.action_counts["manter"] == 20


def test_contract_refuses_spans_outside_training():
    model, archive = KeyRateReplayModel(SyntheticQKD()), SyntheticQKD()
    archive.sha256 = "synthetic"
    env = kr.make_link(50.0, steps=2)
    assert kr.check_contract(model, archive, env, 50.0)["status"] == "admitted"
    refused = kr.check_contract(model, archive, env, 90.0)
    assert refused["status"] == "refused" and refused["reasons"] == ["span outside the 25-80 km training range"]
    archive.meta["dead_time_s"] = 40e-9
    assert "detector dead time differs from the export" in kr.check_contract(model, archive, env, 50.0)["reasons"]


def test_exported_episode_regenerated_bit_for_bit():
    gate = kr.reproduce_export_episode(real_archive())
    assert gate["passed"] and gate["periods"] == 40 and not any(gate["mismatches"].values())
    # identical on Linux, where the export was written; another libm may move the last bit
    assert gate["calibration_max_relative_error"] <= kr.CALIBRATION_RTOL
