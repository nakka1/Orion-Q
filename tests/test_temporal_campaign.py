"""Paired campaign boundaries, statistics, isolation and complete receipts."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from threading import Barrier

import pytest

from quantum_autopilot.control_plane import temporal_campaign as campaign
from quantum_autopilot.control_plane.temporal_campaign import (
    TemporalRegime, TemporalCampaignRequest, evaluate_temporal_campaign,
    run_temporal_campaign, read_temporal_campaign, _wilson, _comparisons, _summary,
)
from quantum_autopilot.control_plane.temporal_campaign_cli import main, load_manifest
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore, default_simulated_topology


def request(**changes):
    baseline = TemporalRegime("baseline", 0, 0, .00005, .00003, 2)
    other = replace(baseline, regime_id="candidate", success_probability_left=1, success_probability_right=1)
    return TemporalCampaignRequest(**dict(dict(campaign_id="campaign", source="alice", repeater="repeater-a",
        destination="bob", expected_version=0, target_fidelity=.8, attempt_interval_s=1e-6, max_attempts=4,
        seeds=(0, 1), regimes=(baseline, other), baseline_id="baseline"), **changes))


@pytest.mark.parametrize("changes", [
    {"seeds": ()}, {"seeds": (1,)}, {"seeds": (1, 1)}, {"seeds": (0, True)},
    {"seeds": (0, -1)}, {"seeds": (0, 2**32)}, {"seeds": [1, 2]}, {"seeds": tuple(range(65))},
    {"baseline_id": "missing"}, {"protocol": "physical"}, {"max_attempts": 0}, {"max_attempts": True},
    {"attempt_interval_s": float("nan")}, {"target_fidelity": float("inf")},
    {"expected_version": True}, {"source": "bob"}, {"topology_id": "physical-network"},
    {"regimes": ()}, {"regimes": (None, None)},
    {"seeds": tuple(range(64)), "max_attempts": 1000},
])
def test_invalid_campaign(changes):
    with pytest.raises(ValueError):
        request(**changes)


@pytest.mark.parametrize("changes", [
    {"regime_id": "bad id"}, {"regime_id": "x"*33}, {"success_probability_left": True},
    {"success_probability_right": 2}, {"memory_t1_s": 0}, {"memory_t2_s": 1},
    {"memory_ttl_steps": 0}, {"memory_ttl_steps": 1.1},
])
def test_invalid_regime(changes):
    with pytest.raises(ValueError):
        replace(request().regimes[0], **changes)


def test_strict_json_and_immutable_request():
    req = request()
    payload = json.loads(json.dumps(asdict(req)))
    assert TemporalCampaignRequest.from_dict(payload) == req
    for key in payload:
        with pytest.raises(ValueError):
            TemporalCampaignRequest.from_dict({k: v for k, v in payload.items() if k != key})
    with pytest.raises(ValueError):
        TemporalCampaignRequest.from_dict(dict(payload, measured=True))
    for key in payload["regimes"][0]:
        with pytest.raises(ValueError):
            TemporalRegime.from_dict({k: v for k, v in payload["regimes"][0].items() if k != key})
    with pytest.raises(ValueError):
        replace(req, regimes=(req.regimes[0], req.regimes[0]))


def test_wilson_known_boundaries():
    assert _wilson(0, 10) == pytest.approx([0, .2775327999], abs=1e-9)
    assert _wilson(10, 10) == pytest.approx([.7224672001, 1], abs=1e-9)
    assert _wilson(5, 10) == pytest.approx([.23659309, .76340691], abs=1e-8)


def outcomes(values):
    return [{"seed": seed, "result": {"outcome": "target_met" if value else "acquisition_timeout"}}
            for seed, value in enumerate(values)]


def test_exact_paired_test_and_holm_including_no_discordance():
    rows = _comparisons({"b": outcomes([False]*8), "win": outcomes([True]*8),
                         "partial": outcomes([True]*6 + [False]*2), "tie": outcomes([False]*8)}, "b")
    assert [r["p_value_exact_two_sided"] for r in rows] == [.0078125, .03125, 1]
    assert [r["p_value_holm"] for r in rows] == [.0234375, .0625, 1]
    assert [r["reject_equal_target_probability_at_005"] for r in rows] == [True, False, False]
    assert rows[0]["target_probability_difference"] == 1
    negative = _comparisons({"b": outcomes([True]*8), "loss": outcomes([False]*8)}, "b")[0]
    assert negative["target_probability_difference"] == -1
    assert negative["baseline_only_target_met"] == 8
    assert negative["p_value_exact_two_sided"] == .0078125


def test_pairing_is_by_seed_not_row_position():
    base = outcomes([True, False, True, False])
    candidate = outcomes([False, False, True, True])
    a = _comparisons({"base": base, "candidate": candidate}, "base")
    b = _comparisons({"base": base, "candidate": list(reversed(candidate))}, "base")
    assert a == b
    assert a[0]["candidate_only_target_met"] == a[0]["baseline_only_target_met"] == 1


def test_exact_tick_budget_and_path_validation_before_trials(monkeypatch):
    regimes = tuple(replace(request().regimes[0], regime_id=f"r{i}") for i in range(4))
    req = request(regimes=regimes, baseline_id="r0", seeds=tuple(range(64)), max_attempts=250)
    assert len(req.seeds)*len(req.regimes)*req.max_attempts == 64000
    with pytest.raises(ValueError, match="64000"):
        replace(req, max_attempts=251)
    def never(*args):
        pytest.fail("kernel must not run on an invalid path")
    monkeypatch.setattr(campaign, "evaluate_temporal_path", never)
    topo = default_simulated_topology()
    disabled = replace(topo, links=(replace(topo.links[0], active=False), *topo.links[1:]))
    with pytest.raises(ValueError, match="enabled link"):
        evaluate_temporal_campaign(disabled, req)
    with pytest.raises(ValueError, match="conflict"):
        evaluate_temporal_campaign(topo, replace(req, expected_version=1))


def test_timeouts_never_disappear_from_denominator_or_become_fidelity_zero():
    result = evaluate_temporal_campaign(default_simulated_topology(), request())
    b, c = result["summaries"].values()
    assert b["target_probability"] == 0 and b["timeouts"] == b["trials"] == 2
    assert b["conditional_fidelity_mean"] is None and b["conditional_target_probability"] is None
    assert b["swap_time_mean_s_conditional"] is None
    assert b["termination_time_mean_s_all_trials"] == 4e-6
    assert c["swaps"] == c["target_met"] == 2 and c["target_probability"] == 1
    assert c["conditional_fidelity_mean"] == pytest.approx(.8133333333, abs=1e-7)
    trials = result["trials_by_regime"]
    mixed = _summary([trials["baseline"][0], trials["candidate"][0]])
    assert mixed["target_probability"] == .5 and mixed["conditional_target_probability"] == 1
    assert mixed["conditional_fidelity_n"] == 1
    miss = evaluate_temporal_campaign(default_simulated_topology(), request(target_fidelity=.99))
    assert miss["summaries"]["candidate"]["target_not_met"] == 2
    assert miss["summaries"]["candidate"]["timeouts"] == 0


def test_matched_draws_regime_order_invariance_and_preserved_quantum_states():
    req = request()
    baseline = replace(req.regimes[0], success_probability_left=1, success_probability_right=.25)
    other = replace(baseline, regime_id="longer", memory_t1_s=.005, memory_t2_s=.003)
    req = replace(req, seeds=(7, 8), regimes=(baseline, other))
    topo = default_simulated_topology()
    a = evaluate_temporal_campaign(topo, req)
    b = evaluate_temporal_campaign(topo, replace(req, regimes=(other, baseline)))
    for ident in ("baseline", "longer"):
        assert a["summaries"][ident] == b["summaries"][ident]
    for left, right in zip(a["trials_by_regime"]["baseline"], a["trials_by_regime"]["longer"]):
        assert left["seed"] == right["seed"]
        assert left["result"]["timeline"] == right["result"]["timeline"]
        if left["result"]["pairs_available"]:
            assert left["result"]["quantum_execution"]["density_matrices_preserved"]
        else:
            assert left["result"]["quantum_execution"] is None
            assert right["result"]["quantum_execution"] is None
    first = a["trials_by_regime"]["baseline"][0]["result"]
    second = a["trials_by_regime"]["longer"][0]["result"]
    assert first["memory_ages_s"] == [1e-6, 0]
    assert second["conditional_fidelity"] > first["conditional_fidelity"]


def test_receipt_persistence_replay_and_no_workspace_mutation(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "state.sqlite")
    topo = default_simulated_topology()
    store.initialize(topo)
    receipt = run_temporal_campaign(store, request())
    assert store.get(topo.topology_id) == topo
    read = read_temporal_campaign(SimulatedTopologyStore(store.path), "campaign")
    assert json.loads(json.dumps(receipt["record"])) == read["record"]
    assert receipt["record_sha256"] == read["record_sha256"]
    assert receipt["record"]["source_topology"] == topo.to_dict()
    assert receipt["record"]["evidence_kind"] == "simulated"
    assert not receipt["record"]["physical_action_performed"]
    store.reset()  # Replay reads the original frozen receipt, not this new version.
    assert run_temporal_campaign(store, request())["idempotent_replay"]
    with pytest.raises(ValueError, match="different request"):
        run_temporal_campaign(store, replace(request(), seeds=(2, 3)))
    with pytest.raises(ValueError, match="conflict"):
        run_temporal_campaign(store, replace(request(), campaign_id="stale"))


def test_blueprint_is_not_initialized_and_tampering_is_refused(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "state.sqlite")
    receipt = run_temporal_campaign(store, request())
    assert not receipt["record"]["source_was_persisted"]
    assert store.get(request().topology_id) is None
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE temporal_path_campaigns SET record_json = '{}' WHERE campaign_id = 'campaign'")
    with pytest.raises(ValueError, match="integrity"):
        read_temporal_campaign(store)
    with pytest.raises(ValueError, match="integrity"):
        run_temporal_campaign(store, request())


def test_failure_aborts_whole_campaign_without_receipt(tmp_path, monkeypatch):
    store = SimulatedTopologyStore(tmp_path / "state.sqlite")
    original = campaign.evaluate_temporal_path
    def fail_second(topology, trial):
        if trial.seed == 1:
            raise RuntimeError("test kernel failure")
        return original(topology, trial)
    monkeypatch.setattr(campaign, "evaluate_temporal_path", fail_second)
    with pytest.raises(RuntimeError, match="kernel failure"):
        run_temporal_campaign(store, request())
    assert read_temporal_campaign(store) is None


def test_evaluation_holds_no_writer_lock_and_keeps_frozen_source(tmp_path, monkeypatch):
    store = SimulatedTopologyStore(tmp_path / "state.sqlite")
    original = campaign.evaluate_temporal_campaign
    def edit_then_evaluate(topology, req):
        # A different store/connection must be able to write during evaluation.
        SimulatedTopologyStore(store.path).reset()
        return original(topology, req)
    monkeypatch.setattr(campaign, "evaluate_temporal_campaign", edit_then_evaluate)
    receipt = run_temporal_campaign(store, request())
    assert receipt["record"]["source_was_persisted"] is False
    assert store.get(request().topology_id) is not None
    assert receipt["record"]["workspace_mutated"] is False  # The campaign itself did not edit it.


def test_concurrent_duplicate_campaigns_commit_only_once(tmp_path, monkeypatch):
    store = SimulatedTopologyStore(tmp_path / "state.sqlite")
    store.initialize(default_simulated_topology())
    barrier = Barrier(2)
    original = campaign.evaluate_temporal_campaign
    def synchronized(topology, req):
        barrier.wait(timeout=30)
        return original(topology, req)
    monkeypatch.setattr(campaign, "evaluate_temporal_campaign", synchronized)
    def run():
        return run_temporal_campaign(SimulatedTopologyStore(store.path), request())
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: run(), range(2)))
    assert a["record_sha256"] == b["record_sha256"]
    assert sorted([a["idempotent_replay"], b["idempotent_replay"]]) == [False, True]
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM temporal_path_campaigns").fetchone()[0] == 1


def test_cli_export_and_refuse_overwrite(tmp_path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(asdict(request())), encoding="utf-8")
    output, store = tmp_path / "result.json", tmp_path / "state.sqlite"
    args = ["--manifest", str(manifest), "--store", str(store), "--output", str(output)]
    assert main(args) == 0
    saved = output.read_bytes()
    assert "SIMULATION ONLY" in capsys.readouterr().out
    assert json.loads(saved)["record"]["result"]["total_trials"] == 4
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2 and output.read_bytes() == saved
    assert main(args[:-1] + [str(tmp_path / "replay.json")]) == 0
    assert json.loads((tmp_path / "replay.json").read_bytes())["idempotent_replay"]


def test_manifest_duplicate_oversize_and_invalid_input_never_create_store(tmp_path):
    manifest = tmp_path / "invalid.json"
    manifest.write_text('{"campaign_id":"a","campaign_id":"b"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_manifest(manifest)
    manifest.write_text(" "*65537, encoding="utf-8")
    with pytest.raises(ValueError, match="64 KiB"):
        load_manifest(manifest)
    with pytest.raises(SystemExit):
        main(["--manifest", str(manifest), "--store", str(tmp_path / "absent.sqlite"), "--output", str(tmp_path / "absent.json")])
    assert not (tmp_path / "absent.sqlite").exists()
    assert not (tmp_path / "absent.json").exists()
