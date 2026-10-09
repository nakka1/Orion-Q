"""The polarisation loop through the author's bounded actuator, and the frozen actuator campaign."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from qnet_network.compensation import AlphaBetaFilter, run_closed_loop
from qnet_network.compensation.actuator import ActuatorConfig, PolarisationActuator
from quantum_autopilot.control_plane import polarisation_actuator_campaign as campaign
from quantum_autopilot.control_plane import polarisation_loop as loop
from quantum_autopilot.control_plane import polarisation_plant as plant
from tests import author_scripts

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "configs/optional_models/polarisation_actuator_v1.json"


@pytest.fixture(scope="module")
def short_run():
    return plant.simulate(plant.OPERATING_POINTS["desenvolvimento"], 1.5, 500)


@pytest.mark.parametrize("slew", [30.0, 250.0])
def test_bounded_actuator_matches_authors_closed_loop(short_run, slew):
    task3 = author_scripts.load("qnet_network", "task3")
    author_run = task3.make_trajectories([500], duration_s=1.5)[0]
    assert np.array_equal(author_run.rotation, short_run.rotation)
    config = ActuatorConfig(range_rad=2 * math.pi, slew_rate_rad_per_s=slew)
    reference = run_closed_loop(author_run, AlphaBetaFilter(alpha=0.05, beta=0.005), task3.make_control(), rng=7000,
                                actuator=PolarisationActuator(config)).summary()
    record = loop.run_loop(short_run, loop.AlphaBeta(0.05, 0.005), monitoring_seed=7000,
                           operating_point="desenvolvimento", plant_seed=500, actuator=PolarisationActuator(config))
    assert record.summary["mean_qber"] == reference["mean_qber"]
    assert record.actuator["travel_rad"] == reference["actuator_travel_rad"]
    assert record.actuator["resets"] == reference["actuator_resets"]
    assert record.actuator["slew_rate_rad_per_s"] == slew and record.physical_action_performed is False


def test_ideal_path_unchanged_and_recorded_as_ideal(short_run):
    record = loop.run_loop(short_run, loop.AlphaBeta(0.05, 0.005), monitoring_seed=7000,
                           operating_point="desenvolvimento", plant_seed=500)
    assert record.actuator is None
    fast = loop.run_loop(short_run, loop.AlphaBeta(0.05, 0.005), monitoring_seed=7000,
                         operating_point="desenvolvimento", plant_seed=500,
                         actuator=PolarisationActuator(ActuatorConfig(slew_rate_rad_per_s=1e7)))
    # A device far faster than the fibre reaches every command: same QBER as the ideal actuator.
    assert fast.summary["mean_qber"] == pytest.approx(record.summary["mean_qber"], rel=1e-9)


def test_protocol_file_is_frozen(tmp_path):
    assert campaign.load_protocol(PROTOCOL) == (PROTOCOL.read_bytes(), campaign.PROTOCOL)
    changed = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    changed["slew_rates_rad_per_s"] = changed["slew_rates_rad_per_s"][1:]
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from frozen v1"):
        campaign.run_campaign("never-read.zip", path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_reference_is_the_vendored_author_record():
    reference = campaign.read_reference(ROOT)
    assert len(reference) == 4 * 9
    assert reference[(None, "baseline.alpha_beta")]["mean_qber"] == pytest.approx(0.0373, abs=1e-4)
    assert reference[(30.0, "baseline.alpha_beta")]["mean_qber"] < reference[(30.0, campaign.REFERENCE_NAMES["MLP"])]["mean_qber"]
    vendored = json.loads((ROOT / "external/VENDORED.json").read_text(encoding="utf-8"))
    entry = next(item for item in vendored["vendored"] if item["package"] == "src/qnet_network")
    assert entry["files"][campaign.REFERENCE]["sha256"] == campaign.PROTOCOL["reproduction"]["reference_sha256"]


def test_tampered_reference_refused(tmp_path):
    target = tmp_path / campaign.REFERENCE
    target.parent.mkdir(parents=True)
    target.write_bytes((ROOT / campaign.REFERENCE).read_bytes().replace(b"0.03730", b"0.03731", 1))
    with pytest.raises(ValueError, match="differs from the vendored reference"):
        campaign.read_reference(tmp_path)


def test_two_sided_statistics_name_the_winner():
    protocol = json.loads(json.dumps(campaign.PROTOCOL))
    protocol["operating_points"] = {"desenvolvimento": ["m"]}
    protocol["slew_rates_rad_per_s"] = [30.0, 3000.0]
    rng = np.random.default_rng(0)
    base = 0.04 + 0.002 * rng.random(10)
    per_seed = {("desenvolvimento", 30.0, "baseline.alpha_beta"): list(base),
                ("desenvolvimento", 30.0, "m"): list(base * 1.3),
                ("desenvolvimento", 3000.0, "baseline.alpha_beta"): list(base),
                ("desenvolvimento", 3000.0, "m"): list(base / 4)}
    rows = campaign.statistics(per_seed, protocol)
    assert [row["verdict"] for row in rows] == ["alpha_beta_better", "learned_better"]
    assert all(row["p_value"] == pytest.approx(2 / 1024) for row in rows)
