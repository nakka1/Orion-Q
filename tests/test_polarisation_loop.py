"""Closed loop, supervisor and checkpoint contract of the polarisation control target."""
import hashlib
import io
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest

from quantum_autopilot.control_plane import polarisation_loop as loop
from quantum_autopilot.control_plane import polarisation_plant as plant
from quantum_autopilot.model_intake import artifacts
from quantum_autopilot.model_intake.artifacts import ReviewedArchive
from tests import test_optional_compensation as helpers

pytestmark = pytest.mark.unit
FIXTURE = Path(__file__).resolve().parent / "fixtures/polarisation_plant_reference_v1.npz"


def z_rotation(angle):
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


class Scripted:
    """A controller whose proposals are a fixed list, for supervisor tests."""

    controller_id = "test.scripted"

    def __init__(self, proposals):
        self.proposals = proposals

    def reset(self):
        self.step = 0

    def propose(self, time_s, measured, covariates):
        value = self.proposals[min(self.step, len(self.proposals) - 1)]
        self.step += 1
        if isinstance(value, Exception):
            raise value
        return value


def supervise(controller, steps, fallback=None, faults=(), **config):
    supervisor = loop.PolarisationSupervisor(loop.PolarisationSupervisorConfig(**config))
    times = [i * 0.002 for i in range(steps)]
    return supervisor.run(controller, fallback, times, [np.eye(3)] * steps, [np.zeros(3)] * steps, frozenset(faults))


@pytest.fixture(scope="module")
def short_run():
    return plant.simulate(plant.OPERATING_POINTS["calibrado"], 2.0, 1000)


@pytest.mark.parametrize("name, controller", [
    ("none", loop.NoCompensation()), ("persistence", loop.Persistence()), ("alpha_beta", loop.AlphaBeta(0.05, 0.005))])
def test_supervised_loop_reproduces_author_closed_loop(short_run, name, controller):
    with np.load(FIXTURE, allow_pickle=False) as data:
        keys = json.loads(bytes(data["meta_json"]))["loop_keys"]
        expected = dict(zip(keys, data["loop_" + name]))
    record = loop.run_loop(short_run, controller, monitoring_seed=7000, operating_point="calibrado", plant_seed=1000)
    for key, value in expected.items():
        assert record.summary[key] == pytest.approx(value, rel=1e-9, abs=1e-12), key
    assert record.supervisor["rate_limited"] == record.supervisor["faults"] == 0


def test_record_invariants(short_run):
    record = loop.run_loop(short_run, loop.Persistence(), monitoring_seed=1, operating_point="calibrado", plant_seed=1000)
    payload = json.loads(record.serialized())
    assert payload["mode"] == "simulation" and payload["evidence"] == "simulated"
    assert payload["physical_action_performed"] is False and payload["simulator_action_performed"] is True
    assert payload["target_id"] == loop.TARGET_ID and payload["supervisor"]["commands"] == 1000
    with pytest.raises(ValueError):
        loop.run_loop(short_run, loop.Persistence(), monitoring_seed=-1, operating_point="calibrado", plant_seed=1000)


def test_first_command_is_alignment_then_rate_limited():
    far = z_rotation(3.0)
    commands, ledger = supervise(Scripted([far, np.eye(3), np.eye(3), np.eye(3)]), 4)
    np.testing.assert_allclose(commands[0], far.T)
    assert ledger.initial_alignment and ledger.rate_limited == 1
    steps = [plant.rotation_angle(commands[i] @ commands[i - 1].T) for i in range(1, 4)]
    assert steps[0] == pytest.approx(np.pi / 2) and steps[1] == pytest.approx(3.0 - np.pi / 2) and steps[2] < 1e-12
    np.testing.assert_allclose(commands[3], np.eye(3), atol=1e-12)


@pytest.mark.parametrize("bad, reason", [
    (np.full((3, 3), np.nan), "proposal_not_in_so3"), (2 * np.eye(3), "proposal_not_in_so3"),
    (np.diag([1.0, 1.0, -1.0]), "proposal_not_in_so3"), (ValueError("x"), "controller_error:ValueError"),
    (FloatingPointError("x"), "controller_error:FloatingPointError"), (np.eye(2), "proposal_not_in_so3"),
])
def test_invalid_proposal_holds_previous_command(bad, reason):
    good = z_rotation(0.2)
    commands, ledger = supervise(Scripted([good, bad, good]), 3)
    np.testing.assert_allclose(commands[1], commands[0])
    assert ledger.faults == 1 and ledger.fault_reasons == {reason: 1} and ledger.degraded_at_s is None


def test_consecutive_faults_degrade_to_standby_and_stay_degraded():
    standby = loop.AlphaBeta(0.05, 0.0)
    commands, ledger = supervise(Scripted([z_rotation(0.1)] * 3), 30, fallback=standby, faults=range(5, 13))
    assert ledger.faults == 8 and ledger.degraded_at_s == pytest.approx(12 * 0.002)
    assert ledger.fallback_commands == 30 - 13 and ledger.accepted == 5
    np.testing.assert_allclose(commands[-1], np.eye(3), atol=1e-12)  # standby tracks the identity readings


def test_without_standby_faults_only_hold():
    _, ledger = supervise(Scripted([z_rotation(0.1)]), 20, faults=range(2, 20))
    assert ledger.faults == 18 and ledger.degraded_at_s is None and ledger.fallback_commands == 0


@pytest.mark.parametrize("change", [dict(max_command_step_rad=0.0), dict(max_command_step_rad=4.0),
                                    dict(max_command_step_rad=True), dict(max_consecutive_faults=0),
                                    dict(max_consecutive_faults=1.5), dict(rotation_tolerance=0.1)])
def test_supervisor_config_validation(change):
    with pytest.raises(ValueError):
        loop.PolarisationSupervisorConfig(**change)


@pytest.mark.parametrize("alpha, beta", [(-0.1, 0.0), (0.5, 1.5), (True, 0.0)])
def test_alpha_beta_gains_bounded(alpha, beta):
    with pytest.raises(ValueError):
        loop.AlphaBeta(alpha, beta)


def _archive(tmp_path, monkeypatch, fibre=None, **operating):
    """Synthetic qnet archive with complete metadata for all four checkpoints."""
    path = tmp_path / "qnet.zip"
    with zipfile.ZipFile(path, "w") as target:
        for point in ("calibrado", "desenvolvimento"):
            for kind in ("ridge", "mlp"):
                fixture = helpers.SyntheticArchive(kind, point)
                fixture.meta["operating_point"] = {
                    "control_period_s": 0.002, "horizon_s": 0.04, "sample_interval_s": 0.001,
                    "monitoring_photons_per_basis": 400,
                    "fibre": fibre if fibre is not None else plant.OPERATING_POINTS[point].declaration(), **operating}
                prefix = artifacts.COMPENSATION_ROOT + point + "/model/" + kind
                buffer = io.BytesIO()
                np.savez(buffer, **fixture.data)
                target.writestr(prefix + ".npz", buffer.getvalue())
                target.writestr(prefix + ".json", json.dumps(fixture.meta))
    monkeypatch.setitem(artifacts.PINS, "qnet", hashlib.sha256(path.read_bytes()).hexdigest())
    return ReviewedArchive(path, "qnet")


def test_contract_admits_only_the_declared_plant(tmp_path, monkeypatch):
    archive = _archive(tmp_path, monkeypatch)
    assert loop.check_checkpoint("qnet.compensation.calibrado.ridge", archive, "calibrado").status == "ADMITTED"
    refused = loop.check_checkpoint("qnet.compensation.desenvolvimento.mlp", archive, "calibrado")
    assert refused.status == "REFUSED"
    assert refused.reasons == ("checkpoint_regime_differs_from_plant", "declared_training_fibre_differs_from_plant")
    other = loop.check_checkpoint("qnet.routing.nominal", archive, "calibrado")
    assert other.status == "REFUSED" and other.reasons == ("not_a_reviewed_compensation_checkpoint",)
    with pytest.raises(ValueError):
        loop.check_checkpoint("qnet.compensation.calibrado.ridge", archive, "unknown")


@pytest.mark.parametrize("fibre, operating, reason", [
    ({**plant.OPERATING_POINTS["calibrado"].declaration(), "vibration_harmonics": [1.0]}, {},
     "declared_training_fibre_differs_from_plant"),
    (None, {"horizon_s": 0.08}, "declared_horizon_s_differs"),
    (None, {"monitoring_photons_per_basis": 200}, "declared_monitoring_photons_per_basis_differs"),
])
def test_contract_refuses_any_declared_difference(tmp_path, monkeypatch, fibre, operating, reason):
    archive = _archive(tmp_path, monkeypatch, fibre, **operating)
    decision = loop.check_checkpoint("qnet.compensation.calibrado.ridge", archive, "calibrado")
    assert decision.status == "REFUSED" and reason in decision.reasons


def test_learned_checkpoint_runs_through_replay_path(tmp_path, monkeypatch, short_run):
    archive = _archive(tmp_path, monkeypatch)
    controller = loop.LearnedCheckpoint("qnet.compensation.calibrado.ridge", archive)
    record = loop.run_loop(short_run, controller, monitoring_seed=3, operating_point="calibrado", plant_seed=1000,
                           fallback=loop.AlphaBeta(0.05, 0.0), archive_sha256=archive.sha256)
    # The synthetic weights predict a constant 10 rad/s increment: in SO(3), accepted, never a fault.
    assert record.supervisor["faults"] == 0 and record.supervisor["fallback_commands"] == 0
    assert record.archive_sha256 == archive.sha256 and record.fallback_id == "baseline.alpha_beta"
    again = loop.run_loop(short_run, controller, monitoring_seed=3, operating_point="calibrado", plant_seed=1000,
                          fallback=loop.AlphaBeta(0.05, 0.0), archive_sha256=archive.sha256)
    assert again.summary == record.summary  # reset between runs: no state leaks across streams
