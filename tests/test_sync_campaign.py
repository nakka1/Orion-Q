"""Frozen multi-seed protocol: causal baselines, full coverage and exclusive artifacts."""
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from quantum_autopilot.control_plane import sync_campaign as campaign
from quantum_autopilot.model_intake import artifacts

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def archive(tmp_path, monkeypatch):
    path = tmp_path / "sync.zip"
    config = {"window": 8, "scale": 1e-9, "update_period_s": 1., "horizon_s": 2.,
              "coefficients": [0.] * 9, "intercept": .5}
    with zipfile.ZipFile(path, "w") as target:
        target.writestr(artifacts.QKD_ROOT + "modelos/sincronizador_ridge.json", json.dumps(config))
    monkeypatch.setitem(artifacts.PINS, "qkd", hashlib.sha256(path.read_bytes()).hexdigest())
    return path


@pytest.mark.parametrize("scenario", campaign.SCENARIOS)
def test_samples_reproducible_and_seed_dependent(scenario):
    original = [campaign.sample(scenario, 100, tick) for tick in range(128)]
    assert original == [campaign.sample(scenario, 100, tick) for tick in range(128)]
    assert original != [campaign.sample(scenario, 101, tick) for tick in range(128)]
    assert sum(row is None for row in original) == (5 if scenario == "packet_loss" else 0)


@pytest.mark.parametrize("scenario", campaign.SCENARIOS)
def test_case_pairing_and_past_only_baseline(archive, scenario):
    report = campaign.run_case(archive, scenario, 100)
    assert report["paired"] + report["pending"] + report["missed_target"] == report["registered_predictions"]
    assert report["pending"] == 2
    if scenario == "packet_loss":
        assert report["target_coverage"] < .9 and report["missed_target"] > 0
        assert report["acquired_samples"] == 123
    else:
        assert report["paired"] == 119 and report["target_coverage"] == 1.
    for row in report["records"]:
        prediction = row["forecast"]["evaluation"]
        inputs = json.loads(prediction["input_json"])
        assert inputs["reference"] is None
        assert row["past_window_mean_offset_s"] == pytest.approx(sum(inputs["offsets_s"]) / 8, abs=1e-24)
        if row["status"] == "paired":
            assert row["reference_observation_index"] > row["committed_observation_index"]
            assert row["past_window_mean_absolute_error_s"] == abs(row["past_window_mean_offset_s"] - row["reference_offset_s"])


def fake_runs():
    return [{"scenario": scenario, "seed": seed, "model_mae_s": 1., "persistence_mae_s": 2.,
             "past_window_mean_mae_s": 2., "target_coverage": 1.}
            for scenario in campaign.SCENARIOS for seed in campaign.PROTOCOL["seeds"]]


def test_aggregation_equal_seed_weight_requires_both_baselines_and_coverage():
    rows = fake_runs()
    assert all(row["descriptive_filter_met"] for row in campaign.aggregate(rows))
    rows[0]["past_window_mean_mae_s"] = 0.
    rows[0]["target_coverage"] = .8
    report = campaign.aggregate(rows)[0]
    assert not report["descriptive_filter_met"]
    assert report["comparisons"]["past_window_mean"]["model_losses"] == 1
    for row in rows[:10]:
        row["past_window_mean_mae_s"] = 0.
    result = campaign.aggregate(rows)[0]["comparisons"]["past_window_mean"]
    assert result["relative_mae_reduction"] is None and not result["descriptive_filter_met"]


@pytest.mark.parametrize("fault", ["missing", "duplicate", "wrong_seed"])
def test_no_selective_reporting(fault):
    rows = fake_runs()
    if fault == "missing":
        rows.pop()
    elif fault == "duplicate":
        rows.append(rows[0])
    else:
        rows[0]["seed"] = 1337
    with pytest.raises(ValueError, match="exactly once"):
        campaign.aggregate(rows)


@pytest.mark.parametrize("change", ["frames", "seeds", "threshold", "float_frames", "duplicate", "oversize"])
def test_protocol_changes_refused_before_output_or_inference(change, tmp_path, monkeypatch):
    config = json.loads(json.dumps(campaign.PROTOCOL))
    if change == "frames":
        config["frames"] = 100
    elif change == "seeds":
        config["seeds"] = [1337]
    elif change == "threshold":
        config["min_relative_mae_reduction"] = 0.
    elif change == "float_frames":
        config["frames"] = 128.
    raw = json.dumps(config)
    if change == "duplicate":
        raw = raw[:-1] + ', "frames":128}'
    if change == "oversize":
        raw = " " * 16385
    path = tmp_path / "protocol.json"
    path.write_text(raw, encoding="utf-8")
    monkeypatch.setattr(campaign, "run_case", lambda *a: pytest.fail("inference started"))
    with pytest.raises(ValueError):
        campaign.run_campaign("unused.zip", path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_manifest_precedes_inference_and_outputs_are_exclusive(tmp_path, monkeypatch):
    output = tmp_path / "campaign"
    def fake_case(archive, scenario, seed):
        frozen = json.loads((output / "frozen-protocol.json").read_text())
        assert frozen["protocol"] == campaign.PROTOCOL
        return next(row for row in fake_runs() if row["scenario"] == scenario and row["seed"] == seed) | {
            "records": [], "archive_sha256": artifacts.PINS["qkd"]}
    monkeypatch.setattr(campaign, "run_case", fake_case)
    protocol = ROOT / "configs/optional_models/sync_campaign_v1.json"
    report = campaign.run_campaign("unused.zip", protocol, output)
    assert report["status"] == "complete" and len(report["runs"]) == 60
    for item in report["artifacts"]:
        assert hashlib.sha256((output / item["file"]).read_bytes()).hexdigest() == item["sha256"]
    with pytest.raises(FileExistsError):
        campaign.run_campaign("unused.zip", protocol, output)


def test_failure_leaves_manifest_but_no_completed_summary(tmp_path, monkeypatch):
    output = tmp_path / "campaign"
    def fail(*args):
        raise ValueError("refused archive")
    monkeypatch.setattr(campaign, "run_case", fail)
    with pytest.raises(ValueError, match="refused archive"):
        campaign.run_campaign("unused.zip", ROOT / "configs/optional_models/sync_campaign_v1.json", output)
    assert (output / "frozen-protocol.json").exists()
    assert not (output / "summary.json").exists()
