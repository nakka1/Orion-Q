"""Frozen SO(3) stress protocol, causal baselines, coverage and complete reporting."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from quantum_autopilot.control_plane import compensation_campaign as campaign
from quantum_autopilot.control_plane.optional_compensation import CompensationShadowWindow, TELEMETRY_KEY
from quantum_autopilot.model_intake import artifacts
from tests import test_optional_compensation_session as fixtures
from tests.test_compensation_temporal import rotation

compensation_archive = fixtures.compensation_archive
pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "configs/optional_models/compensation_campaign_v1.json"


@pytest.mark.parametrize("scenario", campaign.SCENARIOS)
def test_stress_batches_reproducible_and_valid(scenario):
    def rows(seed):
        return [state.to_json() if state else None for _, state in campaign.batches(scenario, seed, campaign.MODEL_IDS[0])]
    assert rows(100) == rows(100) and rows(100) != rows(101)
    assert sum(row is None for row in rows(100)) == (4 if scenario == "packet_loss" else 0)
    for _, state in campaign.batches(scenario, 100, campaign.MODEL_IDS[0]):
        if state is not None:
            window = CompensationShadowWindow.from_dict(state.telemetry[TELEMETRY_KEY])
            assert window.validate_context(state) == 0.
            assert state.metadata["stress_scenario"] == scenario
            assert "OOD is not estimated" in state.metadata["scope"]


def test_constant_velocity_uses_only_past_samples():
    text = json.dumps({"samples": [{"rotation": rotation(index * .001)} for index in range(16)]})
    actual = campaign.constant_velocity(text)
    assert np.allclose(actual, rotation(.035), atol=1e-12)
    assert np.linalg.det(actual) == pytest.approx(1.)


@pytest.mark.parametrize("scenario", campaign.SCENARIOS)
def test_pairing_coverage_and_past_only_comparator(compensation_archive, scenario):
    report = campaign.run_case(compensation_archive, scenario, 100, campaign.MODEL_IDS[0])
    assert report["pending"] == 2
    assert report["paired"] + report["pending"] + report["missed_target"] == report["registered_predictions"]
    if scenario == "packet_loss":
        assert report["acquired_batches"] == 28 and report["missed_target"] == 4
        assert report["paired"] == 22 and report["target_coverage"] == pytest.approx(22/30)
    elif scenario == "late_result":
        assert report["late_results_refused"] == 8 and report["late_results_unresolved"] == 0
        assert report["registered_predictions"] == 24 and report["paired"] == 22
        assert report["target_coverage"] == pytest.approx(22/30)
        assert report["coverage_of_resolved_targets"] == 1.  # Conditional coverage must not hide missing predictions.
    else:
        assert report["paired"] == 30 and report["target_coverage"] == 1.
    for row in report["records"]:
        inputs = json.loads(row["forecast"]["evaluation_json"])["input_json"]
        assert json.loads(inputs)["reference"] is None
        assert row["constant_velocity_rotation"] == campaign.constant_velocity(inputs)
        if row["status"] == "paired":
            assert row["reference_observation_index"] > row["committed_observation_index"]


def fake_runs():
    return [{"scenario": scenario, "seed": seed, "model_id": model,
             "model_mean_angle_rad": 1., "persistence_mean_angle_rad": 2.,
             "constant_velocity_mean_angle_rad": 2., "target_coverage": 1.}
            for scenario in campaign.SCENARIOS for model in campaign.MODEL_IDS for seed in campaign.PROTOCOL["seeds"]]


def test_aggregation_requires_both_baselines_and_coverage():
    rows = fake_runs()
    assert len(campaign.aggregate(rows)) == 24
    assert all(row["descriptive_filter_met"] for row in campaign.aggregate(rows))
    rows[0]["target_coverage"] = .8
    assert not campaign.aggregate(rows)[0]["descriptive_filter_met"]
    for row in rows[:10]:
        row["constant_velocity_mean_angle_rad"] = 0.
    result = campaign.aggregate(rows)[0]["comparisons"]["constant_velocity"]
    assert result["model_losses"] == 10 and result["relative_mean_angle_reduction"] is None
    assert not result["descriptive_filter_met"]


@pytest.mark.parametrize("fault", ["missing", "duplicate", "seed", "nan", "none", "coverage", "angle"])
def test_incomplete_or_invalid_reporting_refused(fault):
    rows = fake_runs()
    if fault == "missing":
        rows.pop()
    elif fault == "duplicate":
        rows.append(rows[0])
    elif fault == "seed":
        rows[0]["seed"] = 1337
    elif fault == "coverage":
        rows[0]["target_coverage"] = 1.1
    elif fault == "angle":
        rows[0]["model_mean_angle_rad"] = 4.
    else:
        rows[0]["model_mean_angle_rad"] = float("nan") if fault == "nan" else None
    with pytest.raises(ValueError):
        campaign.aggregate(rows)


@pytest.mark.parametrize("fault", ["batches", "float", "seeds", "threshold", "duplicate", "oversize"])
def test_protocol_change_refused_before_output(fault, tmp_path, monkeypatch):
    value = json.loads(json.dumps(campaign.PROTOCOL))
    if fault == "batches":
        value["batches"] = 64
    elif fault == "float":
        value["batches"] = 32.
    elif fault == "seeds":
        value["seeds"] = [1337]
    elif fault == "threshold":
        value["min_seed_wins"] = 1
    text = json.dumps(value)
    if fault == "duplicate":
        text = text[:-1] + ', "batches":32}'
    elif fault == "oversize":
        text = " " * 16385
    path = tmp_path / "protocol.json"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(campaign, "run_case", lambda *a: pytest.fail("inference called"))
    with pytest.raises(ValueError):
        campaign.run_campaign("unused", path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_manifest_precedes_inference_and_outputs_are_exclusive(tmp_path, monkeypatch):
    output = tmp_path / "out"
    def fake_case(archive, scenario, seed, model):
        manifest = json.loads((output / "frozen-protocol.json").read_text())
        assert manifest["protocol"] == campaign.PROTOCOL
        return next(row for row in fake_runs() if (row["scenario"], row["seed"], row["model_id"]) == (scenario, seed, model)) | {
            "records": [], "archive_sha256": artifacts.PINS["qnet"]}
    monkeypatch.setattr(campaign, "run_case", fake_case)
    result = campaign.run_campaign("unused", PROTOCOL, output)
    assert result["status"] == "complete" and len(result["runs"]) == 240
    for item in result["artifacts"]:
        assert hashlib.sha256((output / item["file"]).read_bytes()).hexdigest() == item["sha256"]
    with pytest.raises(FileExistsError):
        campaign.run_campaign("unused", PROTOCOL, output)


def test_failure_leaves_no_completed_summary(tmp_path, monkeypatch):
    def fail(*args):
        raise ValueError("model failed")
    monkeypatch.setattr(campaign, "run_case", fail)
    with pytest.raises(ValueError, match="model failed"):
        campaign.run_campaign("unused", PROTOCOL, tmp_path / "out")
    assert (tmp_path / "out/frozen-protocol.json").exists()
    assert not (tmp_path / "out/summary.json").exists()


@pytest.mark.parametrize("model_id", campaign.MODEL_IDS)
def test_architectures_share_same_observed_series(model_id):
    point = model_id.split(".")[2]
    ridge = f"qnet.compensation.{point}.ridge"
    actual = [state.to_json() if state else None for _, state in campaign.batches("rotation_noise", 100, model_id)]
    expected = [state.to_json() if state else None for _, state in campaign.batches("rotation_noise", 100, ridge)]
    assert actual == expected


def test_changed_artifact_never_gets_completed_summary(tmp_path, monkeypatch):
    def fake_case(archive, scenario, seed, model):
        return next(row for row in fake_runs() if (row["scenario"], row["seed"], row["model_id"]) == (scenario, seed, model)) | {
            "records": [], "archive_sha256": "f" * 64}
    monkeypatch.setattr(campaign, "run_case", fake_case)
    with pytest.raises(ValueError, match="mixed artifacts"):
        campaign.run_campaign("unused", PROTOCOL, tmp_path / "out")
    assert not (tmp_path / "out/summary.json").exists()
