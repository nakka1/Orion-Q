"""Routing inside SeQUeNCe (campaign v1): frozen protocol, gate arithmetic, replay parity in the event loop."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qnet_network.agents.actor_critic import ActorCriticPolicy
from quantum_autopilot.control_plane import routing_loop as rl
from quantum_autopilot.control_plane import routing_sequence as campaign
from tests.test_routing_loop import PolicyArchive

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "configs/optional_models/routing_sequence_v1.json"
SUMMARY = ROOT / "docs/results/orion_q_routing_sequence_20260930_summary.json"


def _sequence_available():
    try:
        campaign.simulator()
    except ImportError:
        return False
    return True


def test_protocol_file_is_frozen_and_shares_the_routing_requests(tmp_path):
    assert campaign.load_protocol(PROTOCOL) == (PROTOCOL.read_bytes(), campaign.PROTOCOL)
    changed = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    changed["episodes"] = 100
    other = tmp_path / "changed.json"
    other.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from frozen v1"):
        campaign.run_campaign("never-read.zip", "never-read.zip", other, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    routing = json.loads((ROOT / "configs/optional_models/routing_closed_loop_v1.json").read_text(encoding="utf-8"))
    assert {key: campaign.PROTOCOL[key] for key in ("evaluation_seed", "episodes", "blocks", "variants")} == {
        key: routing[key] for key in ("evaluation_seed", "episodes", "blocks", "variants")}


def test_author_weights_come_from_the_vendored_archive_only(tmp_path):
    assert campaign.author_archive_sha256() == "f7673852f6973aa9a48af78b523cd26bc056511556ee70bea6e07481953f3824"
    other = tmp_path / "other.zip"
    other.write_bytes(b"not the author's archive")
    with pytest.raises(ValueError, match="differs from external/VENDORED.json"):
        campaign.reproduce_author(other)


def test_table_gate_catches_missing_rows_and_differences():
    author = pd.DataFrame({"span_km": [10.0, 20.0], "ratio": [4.0, 0.0], "label": ["a", "b"]})
    same = author.copy()
    same.loc[0, "ratio"] *= 1 + 1e-12
    assert campaign._table_errors(same, author, ["span_km"], 1e-9)["passed"] is True
    changed = author.copy()
    changed.loc[1, "ratio"] = 1e-6
    assert campaign._table_errors(changed, author, ["span_km"], 1e-9)["passed"] is False
    assert campaign._table_errors(author.iloc[:1], author, ["span_km"], 1e-9)["unmatched_rows"] == 1
    relabelled = author.assign(label=["a", "c"])
    assert campaign._table_errors(relabelled, author, ["span_km"], 1e-9)["passed"] is False


@pytest.mark.skipif(not _sequence_available(), reason="SeQUeNCe not importable (pip install sequence or SEQUENCE_SRC)")
def test_replay_inside_sequence_equals_the_authors_actor_critic():
    from qnet_network.env.sequence_runner import run_sequence_episode

    author = ActorCriticPolicy(rng=4)
    archive = PolicyArchive(author, "prazo_longo")
    author.temperature, author.greedy = 0.0, True
    ours = rl.ReplayRouter(archive, "prazo_longo", np.random.default_rng(0))
    config = rl.make_config("prazo_longo")
    for seed in (600000, 600001):
        request = rl.make_request(np.random.default_rng(seed), "prazo_longo")
        mine = run_sequence_episode(config, ours, request, seed=seed, memory_size=rl.MEMORY_SIZE)
        theirs = run_sequence_episode(config, author, request, seed=seed, memory_size=rl.MEMORY_SIZE)
        assert {key: value for key, value in mine.items() if key != "policy"} == pytest.approx(
            {key: value for key, value in theirs.items() if key != "policy"}, nan_ok=True)
        assert mine["legality_disagreements"] == 0
    assert ours.faults == 0


def test_results_summary_is_the_frozen_run():
    if not SUMMARY.exists():
        pytest.skip("campaign summary not generated yet")
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert summary["manifest"]["protocol_file_sha256"] == hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    assert all(row["passed"] for row in summary["reproduction"])
    assert {row["gate"] for row in summary["reproduction"]} == {"comparison.csv", "summary.csv", "round_cost.csv",
                                                                 "analytic_side"}
    assert all(row["legality_disagreements"] == 0 for row in summary["policies"])
    assert summary["policy_activated"] is False and summary["physical_action_performed"] is False
