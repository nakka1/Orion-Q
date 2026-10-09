"""The compensator in the author's fixed point inside ORION-Q's supervised loop (campaign v1)."""
import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from qnet_network.compensation.predictors import FeatureSpec, LearnedCompensator, Standardiser
from qnet_network.deploy import build_quantised
from quantum_autopilot.control_plane import polarisation_loop as loop
from quantum_autopilot.control_plane import polarisation_plant as plant
from quantum_autopilot.control_plane import polarisation_quantisation as campaign

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "configs/optional_models/polarisation_quantisation_v1.json"
SUMMARY = ROOT / "docs/results/orion_q_polarisation_quantisation_20260930_summary.json"


def synthetic(seed=3):
    rng = np.random.default_rng(seed)
    arrays = {"coef": rng.normal(scale=1e-3, size=(3, 48)), "intercept": np.zeros(3)}
    features = rng.normal(size=(200, 48))
    return LearnedCompensator(model=campaign._Ridge(arrays),
                              spec=FeatureSpec(window=16, use_covariates=True, horizon_scale=0.002),
                              standardiser=Standardiser(mean=features.mean(axis=0), scale=features.std(axis=0)),
                              name="ridge")


def short_protocol():
    protocol = copy.deepcopy(campaign.PROTOCOL)
    protocol["calibration"].update(plant_seeds=[100], monitoring_seeds=[7], duration_s=0.4)
    return protocol


def test_protocol_file_is_the_frozen_protocol(tmp_path):
    assert campaign.load_protocol(PROTOCOL) == (PROTOCOL.read_bytes(), campaign.PROTOCOL)
    changed = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    changed["primary"]["margin"] = 0.2
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from frozen v1"):
        campaign.run_campaign("never-read.zip", path, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert not set(campaign.PROTOCOL["evaluation_plant_seeds"]) & set(campaign.PROTOCOL["calibration"]["plant_seeds"])


def test_mlp_forward_is_relu_on_hidden_layers_only():
    rng = np.random.default_rng(0)
    arrays = {f"weight_{i}": rng.normal(size=shape) for i, shape in enumerate([(4, 5), (5, 5), (5, 3)])}
    arrays |= {f"bias_{i}": rng.normal(size=size) for i, size in enumerate([5, 5, 3])}
    x = rng.normal(size=(7, 4))
    hidden = np.maximum(np.maximum(x @ arrays["weight_0"] + arrays["bias_0"], 0) @ arrays["weight_1"]
                        + arrays["bias_1"], 0)
    assert np.allclose(campaign._MLP(arrays).predict(x), hidden @ arrays["weight_2"] + arrays["bias_2"])


def test_calibration_through_orion_loop_feeds_the_authors_quantiser():
    compensator = synthetic()
    calibration = campaign.calibrate(compensator, "calibrado", short_protocol())
    assert calibration["raw"].shape[1] == 48 and len(calibration["raw"]) > 100
    assert [pre.shape[1] for pre in calibration["pre_activations"]] == [3]
    quantised = build_quantised(compensator, calibration, campaign.quantisation_config(16, 8))
    assert quantised.network.layers[0].weight.dtype.kind == "i"


def test_fixed_point_proposals_are_projected_into_so3_and_accepted():
    compensator = synthetic()
    calibration = campaign.calibrate(compensator, "calibrado", short_protocol())
    quantised = build_quantised(compensator, calibration, campaign.quantisation_config(12, 8))
    controller = campaign.AuthorController(quantised, "fixed", project=True)
    trajectory = plant.simulate(plant.OPERATING_POINTS["calibrado"], 0.3, 1000)
    record = loop.run_loop(trajectory, controller, monitoring_seed=7000, operating_point="calibrado", plant_seed=1000,
                           fallback=loop.AlphaBeta(0.01, 0.0))
    assert record.supervisor["faults"] == 0 and 0 < controller.max_projection < 1e-2
    unprojected = campaign.AuthorController(quantised, "raw")
    record = loop.run_loop(trajectory, unprojected, monitoring_seed=7000, operating_point="calibrado",
                           plant_seed=1000, fallback=loop.AlphaBeta(0.01, 0.0))
    assert record.supervisor["fault_reasons"].get("proposal_not_in_so3", 0) > 0


def test_results_summary_is_the_frozen_run():
    if not SUMMARY.exists():
        pytest.skip("campaign summary not generated yet")
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert summary["manifest"]["protocol_file_sha256"] == hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    assert all(row["passed"] for row in summary["reproduction"]) and all(row["passed"] for row in summary["consistency"])
    assert summary["policy_activated"] is False and summary["physical_action_performed"] is False
