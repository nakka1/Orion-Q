"""Frozen protocol, baseline tuning, reproduction gate and statistics of the closed-loop campaign."""
import json
from pathlib import Path

import numpy as np
import pytest

from quantum_autopilot.control_plane import polarisation_campaign as campaign
from tests import test_polarisation_loop as loop_tests

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "configs/optional_models/polarisation_closed_loop_v1.json"


def test_protocol_file_is_the_frozen_protocol(tmp_path):
    assert campaign.load_protocol(PROTOCOL) == (PROTOCOL.read_bytes(), campaign.PROTOCOL)
    development = ROOT / "configs/optional_models/polarisation_closed_loop_dev_v1.json"
    assert campaign.load_protocol(development) == (development.read_bytes(), campaign.PROTOCOL_DEV)
    changed = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    changed["evaluation_plant_seeds"] = changed["evaluation_plant_seeds"][:-1]
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from frozen v1"):
        campaign.load_protocol(path)


def test_protocol_seed_sets_are_disjoint():
    evaluation = set(campaign.PROTOCOL["evaluation_plant_seeds"])
    # Author's checkpoints: training 0-11, validation 100-103, held-out test 500-511.
    author = set(range(12)) | set(range(100, 104)) | set(range(500, 512))
    assert len(evaluation) == 20 and not evaluation & author
    assert set(campaign.PROTOCOL["tuning_plant_seeds"]) <= set(range(100, 104))
    assert set(campaign.PROTOCOL["reproduction"]["plant_seeds"]) <= set(range(500, 512))


def test_tuning_picks_the_lowest_qber_and_flags_grid_edges(monkeypatch):
    monkeypatch.setitem(campaign.PROTOCOL, "alpha_beta_grid", {"alpha": [0.05, 0.5], "beta": [0.0, 0.1]})
    monkeypatch.setitem(campaign.PROTOCOL, "tuning_duration_s", 0.3)
    result = campaign.tune_alpha_beta()
    assert len(result["grid"]) == 4
    assert result["selected"] == min(result["grid"], key=lambda row: row["mean_qber"])
    assert result["alpha_on_grid_edge"] and result["beta_on_grid_edge"]


def test_statistics_holm_and_bootstrap():
    rng = np.random.default_rng(0)
    base = 0.04 + 0.002 * rng.random(8)
    per_seed = {"qnet.compensation.calibrado.ridge": list(base / 3.5),
                "qnet.compensation.calibrado.mlp": list(base / 3.4 + 0.0001),
                "baseline.none": list(base * 1.4), "baseline.persistence": list(base * 2.7),
                "baseline.alpha_beta": list(base)}
    controllers, comparisons = campaign.statistics(per_seed)
    assert len(comparisons) == 6
    for row in comparisons:
        assert row["wins"] == 8 and row["p_value"] == pytest.approx(1 / 256)
        assert row["holm_p_value"] >= row["p_value"] and row["significant"]
        assert row["ratio_ci95"][0] <= row["baseline_to_model_qber_ratio"] <= row["ratio_ci95"][1]
    ridge = next(row for row in comparisons if row["model_id"].endswith("ridge") and row["baseline"] == "baseline.alpha_beta")
    assert ridge["baseline_to_model_qber_ratio"] == pytest.approx(3.5)
    for stats in controllers.values():
        assert stats["ci95"][0] <= stats["mean_qber"] <= stats["ci95"][1]
    assert campaign.statistics(per_seed) == (controllers, comparisons)  # seeded bootstrap


def test_statistics_do_not_invent_significance():
    per_seed = {key: [0.02, 0.03, 0.02, 0.03] for key in
                campaign.PROTOCOL["learned_model_ids"] + campaign.PROTOCOL["baselines"]}
    _, comparisons = campaign.statistics(per_seed)
    assert all(row["p_value"] == 1.0 and not row["significant"] and row["wins"] == 0 for row in comparisons)


def test_reproduction_gate_stops_a_drifted_replay(tmp_path, monkeypatch):
    archive = loop_tests._archive(tmp_path, monkeypatch)
    # Synthetic weights cannot reproduce a declared QBER: the gate must refuse to continue.
    monkeypatch.setitem(campaign.PROTOCOL, "duration_s", 0.2)
    original = archive.json

    def with_declaration(name):
        meta = original(name)
        meta["held_out_mean_qber"] = {"estimate": 0.0132653151857407, "n_seeds": 6}
        return meta

    monkeypatch.setattr(archive, "json", with_declaration)
    with pytest.raises(ValueError, match="does not reproduce"):
        campaign.reproduce(archive)


def _changed(tmp_path):
    path = tmp_path / "protocol.json"
    payload = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    payload["alpha"] = 0.1
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_changed_protocol_refused_before_any_archive_or_output(tmp_path):
    with pytest.raises(ValueError, match="differs from frozen v1"):
        campaign.run_campaign("never-read.zip", _changed(tmp_path), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_campaign_never_overwrites_an_output_directory(tmp_path, monkeypatch):
    loop_tests._archive(tmp_path, monkeypatch)
    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(FileExistsError):
        campaign.run_campaign(tmp_path / "qnet.zip", PROTOCOL, output)
