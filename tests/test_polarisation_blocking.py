"""Key-blocking campaign v1: frozen protocol, the author's study and summaries, and the loop's QBER hand-off."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantum_autopilot.control_plane import polarisation_blocking as campaign
from quantum_autopilot.control_plane import polarisation_loop as loop
from quantum_autopilot.control_plane import polarisation_plant as plant

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "configs/optional_models/polarisation_blocking_v1.json"
SUMMARY = ROOT / "docs/results/orion_q_polarisation_blocking_20260930_summary.json"
RUNS = ROOT / "external/qnet_network/runs/realism"


def test_protocol_file_is_frozen_and_shares_the_closed_loop_design(tmp_path):
    assert campaign.load_protocol(PROTOCOL) == (PROTOCOL.read_bytes(), campaign.PROTOCOL)
    changed = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    changed["non_inferiority_margin"] = 0.05
    other = tmp_path / "changed.json"
    other.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from frozen v1"):
        campaign.run_campaign("never-read.zip", other, tmp_path / "out")
    closed = json.loads((ROOT / "configs/optional_models/polarisation_closed_loop_v1.json").read_text(encoding="utf-8"))
    assert campaign.PROTOCOL["evaluation_plant_seeds"] == closed["evaluation_plant_seeds"]
    assert campaign.PROTOCOL["supervisor"] == closed["supervisor"]
    for point, name in campaign.CLOSED_LOOP_SUMMARIES.items():
        published = json.loads((ROOT / "docs/results" / name).read_text(encoding="utf-8"))["alpha_beta"]
        assert {key: published[key] for key in ("alpha", "beta")} == campaign.PROTOCOL["alpha_beta"][point]


def test_summaries_are_the_authors_functions():
    frame = pd.read_csv(RUNS / "adaptive_blocks.csv")
    ours = campaign.summarise_adaptive_blocks(frame)
    assert campaign.table_errors(ours, pd.read_csv(RUNS / "adaptive_blocks_summary.csv"),
                                 ["compensator", "strategy"], 1e-12)["passed"]
    stress = pd.read_csv(RUNS / "blocking_stress.csv")
    assert campaign.table_errors(campaign.summarise_blocking_stress(stress),
                                 pd.read_csv(RUNS / "blocking_stress_summary.csv"),
                                 ["vibration_amplitude_rad"], 1e-12)["passed"]


def test_table_gate_catches_differences_and_missing_rows():
    author = pd.read_csv(RUNS / "blocking_stress_summary.csv")
    changed = author.copy()
    changed.loc[1, "pooling_hz"] *= 1 + 1e-6
    row = campaign.table_errors(changed, author, ["vibration_amplitude_rad"], 1e-9)
    assert row["passed"] is False and row["columns"]["pooling_hz"] > 1e-7
    assert campaign.table_errors(author.iloc[:3], author, ["vibration_amplitude_rad"], 1e-9)["unmatched_rows"] == 1


def test_plans_follow_the_authors_order_and_a_bad_stretch_is_cut_out():
    qber = np.full(20000, 0.01)
    qber[5000:6000] = 0.4
    plans = campaign.plans_for(qber, 1e-3, 20.0, rng=1)
    assert [plan.strategy for plan in plans] == campaign.PROTOCOL["strategies"]
    rates = {plan.strategy: plan.mean_key_rate_hz for plan in plans}
    assert rates["fixo 20 s"] < rates["adaptativo agrupado"] <= rates["oráculo agrupado"]


def test_loop_hands_over_the_qber_it_summarises():
    trajectory = plant.simulate(plant.OPERATING_POINTS["calibrado"], 2.0, 1000)
    record, qber = loop.run_loop(trajectory, loop.Persistence(), monitoring_seed=7000, operating_point="calibrado",
                                 plant_seed=1000, return_qber=True)
    assert qber.shape == trajectory.time_s.shape
    assert record.summary["mean_qber"] == float(np.mean(qber))
    assert loop.run_loop(trajectory, loop.Persistence(), monitoring_seed=7000, operating_point="calibrado",
                         plant_seed=1000) == record


def test_results_summary_is_the_frozen_run():
    if not SUMMARY.exists():
        pytest.skip("campaign summary not generated yet")
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert summary["manifest"]["protocol_file_sha256"] == hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    assert all(row["passed"] for row in summary["reproduction"]) and len(summary["reproduction"]) == 6
    assert summary["supervisor_faults"] == 0
    assert summary["policy_activated"] is False and summary["physical_action_performed"] is False


def test_comparisons_stay_within_one_point():
    rows = []
    for point, scale in (("calibrado", 1.0), ("desenvolvimento", 3.0)):
        for seed in range(1000, 1020):
            for strategy in campaign.PROTOCOL["strategies"]:
                rate = scale * (2.0 if strategy == "adaptativo agrupado" else 1.0 + (strategy == "fixo 20 s"))
                rows.append({"point": point, "controller_id": "baseline.persistence", "seed": seed,
                             "strategy": strategy, "key_rate_hz": rate})
    frame = pd.DataFrame(rows)
    row = campaign.compare(frame, "desenvolvimento", "baseline.persistence", campaign.PROTOCOL,
                           np.random.default_rng(0))
    assert row["best_timer"] == "fixo 20 s" and row["best_timer_hz"] == 6.0 and row["controller_hz"] == 6.0
    assert row["seeds_won"] == 0 and row["seeds_lost"] == 0 and row["ratio_to_best_timer"] == 1.0
