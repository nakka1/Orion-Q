"""Regressions from the 2026-10-05 review, including persisted operator state."""
from dataclasses import replace

import math
import pytest

from quantum_autopilot.control_plane.inventory import SetNodeIsolation, SetNodeLocation, SimulatedNodeAction
from quantum_autopilot.control_plane.topology import SimulatedLink
from quantum_autopilot.network_engine import LinkBank, ModelRegistry, NetworkEngine, SimulatedInputSource
from quantum_autopilot.network_engine.routing import Demand, Router, effective_graph
from tests.test_network_engine_engine import demand, make, triangle
from tests.test_network_engine_links import run
from tests.test_network_engine_models import SyntheticQkdArchive, compensation_helpers
from tests.test_network_engine_sources import topology

pytestmark = pytest.mark.unit


@pytest.fixture()
def reviewed_models(tmp_path, monkeypatch):
    compensation_helpers._archive(tmp_path, monkeypatch)
    registry = ModelRegistry(environ={"ORION_QNET_ARCHIVE": str(tmp_path / "qnet.zip")}, allow_unverified=True)
    assert registry.archive_state("qnet").state == "verified"
    registry.register_test_archive("qkd", SyntheticQkdArchive())
    return registry


@pytest.mark.parametrize("kind,role", [("qnet", "compensation"), ("qkd", "sync"), ("qkd", "keyrate")])
@pytest.mark.parametrize("change", ["remove", "reject"])
def test_archive_change_reports_the_controller_still_running(reviewed_models, tmp_path, kind, role, change):
    learned = reviewed_models
    source, network = SimulatedInputSource(3, "quiet"), topology([50])
    bank, _, _ = run(source, network, range(1), bank=LinkBank(learned, seed=5))
    before = {item.role: item for item in learned.status()}[role]
    assert before.state == "active" and before.archive_sha256
    learned.set_archive(kind, None if change == "remove" else tmp_path / "missing.zip")
    retained = {item.role: item for item in learned.status()}[role]
    assert retained.state == "active"
    assert retained.archive_sha256 == before.archive_sha256
    assert "recommission" in retained.reason
    _, steps, _ = run(source, network, range(1, 2), bank=bank)
    live = steps[-1].links["F50"]
    after = {item.role: item for item in learned.status()}[role]
    assert live.controllers[role + ".model_state"] == after.state == "active"
    assert after.inferences > before.inferences
    assert after.archive_sha256 == before.archive_sha256
    bank.reset_link("F50")
    reset = {item.role: item for item in learned.status()}[role]
    assert reset.state == "unavailable" and reset.archive_sha256 is None


def direct():
    network = triangle()
    return replace(network, links=(network.links[0],))


@pytest.mark.parametrize("node_id", ["A", "B"])
@pytest.mark.parametrize("reset", ["isolate", "calibrate", "legacy-isolate", "legacy-recalibrate"])
def test_memory_reset_invalidates_the_consumable_pair(tmp_path, node_id, reset):
    engine, store, path = make(tmp_path, direct(), demands=(("P1", "A", "B"),))
    pair = engine.command("request_pair", {"demand_id": "P1"})["pair"]
    assert pair["status"] == "delivered"
    if reset == "isolate":
        engine.command("set_node_isolation", {"node_id": node_id, "isolated": True})
        engine.command("set_node_isolation", {"node_id": node_id, "isolated": False})
    elif reset == "calibrate":
        engine.run_calibration({"node_id": node_id})
    else:
        current = store.get(engine.topology_id)
        store.apply_inventory_node_action(SimulatedNodeAction(
            node_id=node_id, action=reset.removeprefix("legacy-"),
            expected_version=current.topology_version, topology_id=engine.topology_id))
    for verb in ("consume_pair", "purify_pair"):
        with pytest.raises(ValueError, match="no delivered pair"):
            engine.command(verb, {"demand_id": "P1"})
    invalid = demand(engine.live_state()).pair
    assert invalid["status"] == "invalidated"
    assert invalid["fidelity"] == pair["fidelity"], "keep the historical result"
    assert node_id in invalid["invalidated_nodes"]
    again = NetworkEngine(store, path, registry=ModelRegistry(environ={}), seed=7)
    assert demand(again.live_state()).pair == invalid
    with pytest.raises(ValueError, match="no delivered pair"):
        again.command("consume_pair", {"demand_id": "P1"})


def test_reset_and_restore_between_ticks_cannot_revive_a_pair(tmp_path):
    engine, store, _ = make(tmp_path, direct(), demands=(("P1", "A", "B"),))
    engine.command("request_pair", {"demand_id": "P1"})
    for isolated in (True, False):
        current = store.get(engine.topology_id)
        store.set_node_isolation(SetNodeIsolation("A", isolated, current.topology_version, engine.topology_id))
    with pytest.raises(ValueError, match="no delivered pair"):
        engine.command("consume_pair", {"demand_id": "P1"})
    assert demand(engine.live_state()).pair["status"] == "invalidated"
    fresh = engine.command("request_pair", {"demand_id": "P1"})["pair"]
    assert fresh["status"] == "delivered"
    assert engine.command("consume_pair", {"demand_id": "P1"})["status"] == "consumed"


def test_reset_while_engine_is_stopped_invalidates_a_pair_on_restart(tmp_path):
    engine, store, path = make(tmp_path, direct(), demands=(("P1", "A", "B"),))
    engine.command("request_pair", {"demand_id": "P1"})
    for isolated in (True, False):
        current = store.get(engine.topology_id)
        store.set_node_isolation(SetNodeIsolation("B", isolated, current.topology_version, engine.topology_id))
    again = NetworkEngine(store, path, registry=ModelRegistry(environ={}), seed=7)
    assert demand(again.live_state()).pair["status"] == "invalidated"
    with pytest.raises(ValueError, match="no delivered pair"):
        again.command("consume_pair", {"demand_id": "P1"})


@pytest.mark.parametrize("verb", ["request_pair", "consume_pair", "purify_pair"])
def test_reset_between_pair_check_and_commit_is_refused(tmp_path, monkeypatch, verb):
    engine, store, _ = make(tmp_path, direct(), demands=(("P1", "A", "B"),))
    engine.command("request_pair", {"demand_id": "P1"})
    real = engine.demands.set_pair

    def reset_then_commit(demand_id, pair, **kwargs):
        if kwargs.get("memory_generations") is not None:
            current = store.get(engine.topology_id)
            store.set_node_isolation(SetNodeIsolation("A", True, current.topology_version, engine.topology_id))
        return real(demand_id, pair, **kwargs)

    monkeypatch.setattr(engine.demands, "set_pair", reset_then_commit)
    with pytest.raises(ValueError, match="node memory changed"):
        engine.command(verb, {"demand_id": "P1"})
    engine.step()
    assert demand(engine.live_state()).pair["status"] == "invalidated"


def test_memory_reset_of_a_non_endpoint_keeps_the_delivered_pair(tmp_path):
    engine, _, _ = make(tmp_path, triangle(), demands=(("P1", "A", "B"),))
    pair = engine.command("request_pair", {"demand_id": "P1"})["pair"]
    assert pair["status"] == "delivered"
    engine.command("set_node_isolation", {"node_id": "C", "isolated": True})
    assert demand(engine.live_state()).pair == pair
    assert engine.command("consume_pair", {"demand_id": "P1"})["status"] == "consumed"


def test_legacy_pair_without_memory_provenance_is_kept_as_history(tmp_path):
    engine, _, _ = make(tmp_path, direct(), demands=(("P1", "A", "B"),))
    original = engine.command("request_pair", {"demand_id": "P1"})["pair"]
    legacy = {key: value for key, value in original.items() if key != "memory_generations"}
    engine.demands.set_pair("P1", legacy)
    with pytest.raises(ValueError, match="no delivered pair"):
        engine.command("consume_pair", {"demand_id": "P1"})
    pair = demand(engine.live_state()).pair
    assert pair["status"] == "invalidated" and pair["fidelity"] == original["fidelity"]


@pytest.mark.parametrize("external", [False, True])
def test_antipodal_coordinates_block_the_demand_without_breaking_restart(tmp_path, external):
    engine, store, path = make(tmp_path, direct(), demands=(("P1", "A", "B"),))
    assert demand(engine.live_state()).status == "routed"
    for node_id, longitude in (("A", 0.0), ("B", 180.0)):
        if external:
            current = store.get(engine.topology_id)
            store.set_node_location(SetNodeLocation(node_id, 0.0, longitude, "", current.topology_version,
                                                     engine.topology_id))
            engine.step()
        else:
            result = engine.command("set_node_location", {"node_id": node_id, "latitude": 0.0,
                                                          "longitude": longitude})
            assert result["status"] == "persisted"
    live = engine.live_state()
    assert live.topology_version == store.get(engine.topology_id).topology_version
    assert demand(live).status == "blocked" and "probability" in demand(live).reason
    live.to_json()  # no infinity or NaN in the published record
    assert engine.command("request_pair", {"demand_id": "P1"})["status"] == "blocked"
    again = NetworkEngine(store, path, registry=ModelRegistry(environ={}), seed=7)
    assert demand(again.live_state()).status == "blocked"
    assert again.step().topology_version == live.topology_version


def test_zero_probability_on_an_unrelated_branch_does_not_block_a_viable_route():
    network = triangle()
    network = replace(network, links=network.links + (SimulatedLink("B-C-far", "B", "C", 20015.1, 0.9),))
    # Replace the C-B branch rather than the parallel shortest representative.
    network = replace(network, links=tuple(link for link in network.links if link.link_id != "C-B"))
    router = Router(ModelRegistry(environ={}))
    config, request, _ = router.plant(effective_graph(network), Demand("P1", "A", "B"))
    assert not config.graph.has_edge("B", "C")
    assert math.isfinite(request.deadline_s)
    plan = router.plan(Demand("P1", "A", "B"), effective_graph(network), seed=7)
    assert plan.status == "routed" and plan.route == ("A", "B")


def test_frozen_memory_and_distance_diagnostic(tmp_path):
    from quantum_autopilot.network_engine.validation import validate_memory_and_distance
    report = validate_memory_and_distance(tmp_path / "diagnostic")
    assert report["passed"] and all(report["checks"].values())
    assert report["physical_action_performed"] is False
    repeated = validate_memory_and_distance(tmp_path / "diagnostic")
    assert repeated["passed"] and repeated["workspace"] != report["workspace"]
