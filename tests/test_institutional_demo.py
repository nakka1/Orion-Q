"""National geography, physical limits, controller handover and persistent receipts."""
import json
from pathlib import Path

import pytest

from quantum_autopilot.control_plane.geodesy import link_geodesic_km
from quantum_autopilot.network_engine.control_mode import CONTROL_ROLES
from quantum_autopilot.network_engine.institutional import PRESET_ID, SITES, brazil_institutions
from quantum_autopilot.network_engine.state import Decision, LiveState
from quantum_autopilot.qkd.dashboard_api import demo_journal_path, demo_service

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("site", SITES, ids=[s[0] for s in SITES])
def test_institution_has_named_geography_and_explicit_provenance(site):
    node = next(n for n in brazil_institutions().nodes if n.node_id == site[0])
    assert (node.telemetry["location"]["latitude"], node.telemetry["location"]["longitude"]) == site[2:4]
    assert node.telemetry["demonstration_site"]["equipment"] == "hypothetical"
    assert node.telemetry["demonstration_site"]["coordinate_source"].startswith("https://")


def test_link_lengths_are_geodesic_and_not_clipped_to_training_spans():
    topology = brazil_institutions()
    assert len(topology.nodes) == 31 and len(topology.links) == 36
    for link in topology.links:
        assert link.distance_km == pytest.approx(link_geodesic_km(topology, link))
    spans = {link.link_id: link.distance_km for link in topology.links}
    assert spans["CIMATEC-CBPF"] > 1200 and spans["USP-IME"] < 1
    assert spans["USP-UNICAMP"] > 80


@pytest.fixture
def service(tmp_path):
    return demo_service(state_root=tmp_path, preset=PRESET_ID, scenario="quiet")


def test_default_off_and_long_spans_cannot_create_accepted_keys(service):
    assert service.network_live_state()["policy"]["ml_control_enabled"] is False
    for _ in range(8):
        live = service.network_step()
        for link in live["links"]:
            if link["qber"] is not None and link["qber"] >= live["policy"]["qber_down"]:
                assert link["skr_bps"] == 0
            if "technology" not in link["provenance"] and link["distance_km"] > 300:
                assert link["status"] == "down" and link["qkd"]["key_pool_bits"] == 0
    assert all(m["state"] == "disabled" for m in live["models"] if m["role"] in CONTROL_ROLES)
    assert next(m for m in live["models"] if m["role"] == "detector")["state"] == "active"
    assert LiveState.from_dict(json.loads(json.dumps(live))).to_dict() == live


def test_handover_receipt_readback_and_no_silent_model_activation(service, tmp_path):
    service.network_step()
    result = service.network_command("set_ml_control", {"enabled": True, "expected_revision": 0, "confirmed": True})
    assert result["enabled"] and result["revision"] == 1 and result["physical_action_performed"] is False
    assert Decision.from_dict(result["receipt"]).verify()
    assert result["receipt"]["model_state"] == "fallback"
    assert result["receipt"]["fallback_used"] is True
    live = service.network_live_state()
    assert live["policy"]["ml_control_enabled"] is True
    assert all(m["state"] == "unavailable" for m in live["models"] if m["role"] in CONTROL_ROLES)
    reopened = demo_service(state_root=tmp_path, preset=PRESET_ID, scenario="quiet")
    assert reopened.network_live_state()["policy"]["ml_control_enabled"] is True
    assert reopened.network_live_state()["policy"]["ml_control_revision"] == 1
    assert any(row["record"]["kind"] == "ml_control" for row in reopened.network_decisions(100))


@pytest.mark.parametrize("payload", [
    {"enabled": True, "expected_revision": 0, "confirmed": False},
    {"enabled": 1, "expected_revision": 0, "confirmed": True},
    {"enabled": True, "expected_revision": True, "confirmed": True},
    {"enabled": True, "expected_revision": 19, "confirmed": True},
    {"enabled": True, "confirmed": True},
])
def test_invalid_or_stale_confirmation_does_not_change_state(service, payload):
    before = service.network_live_state()
    with pytest.raises(ValueError):
        service.network_command("set_ml_control", payload)
    assert service.network_live_state() == before


def test_switch_preserves_alarm_keys_detector_history_and_topology(service):
    service.network_step()
    engine = service.network_engine; p = engine.bank.processor("USP-IME")
    p.alarm_held = True
    features, history, stock, epoch, topology = p.features, tuple(p._history), p.key_pool_bits, p.epoch_tick, engine.topology()
    service.network_command("set_ml_control", {"enabled": True, "expected_revision": 0, "confirmed": True})
    service.network_command("set_ml_control", {"enabled": False, "expected_revision": 1, "confirmed": True})
    assert p.alarm_held and p.features is features and p.epoch_tick == epoch
    assert tuple(p._history) == history and p.key_pool_bits == stock and engine.topology() == topology
    assert all(m["state"] == "disabled" for m in service.network_live_state()["models"] if m["role"] in CONTROL_ROLES)
    assert p._controllers()["compensation"] == "baseline.alpha_beta"
    assert p._controllers()["keyrate"] == "baseline.static"


def test_new_demo_does_not_replace_legacy_or_edited_workspaces(tmp_path):
    legacy = demo_service(state_root=tmp_path)
    old = legacy.topology_store.get(legacy.network_engine.topology_id)
    demo = demo_service(state_root=tmp_path, preset=PRESET_ID)
    assert legacy.control_journal_path != demo.control_journal_path
    assert legacy.topology_store.get(old.topology_id) == old
    demo.network_command("add_network_node", {"node_id": "OPERATOR-NODE", "role": "endpoint", "address": "10.72.0.99", "port": 4433})
    again = demo_service(state_root=tmp_path, preset=PRESET_ID)
    assert any(n["node_id"] == "OPERATOR-NODE" for n in again.network_live_state()["nodes"])
    assert demo.control_journal_path == demo_journal_path(tmp_path, preset=PRESET_ID)


def test_learned_controllers_stop_and_can_be_reenabled_without_detector_reset(tmp_path):
    from quantum_autopilot.control_plane.topology import NodeRole, SimulatedLink, SimulatedNodeState, SimulatedTopology, SimulatedTopologyStore
    from quantum_autopilot.network_engine import ModelRegistry, NetworkEngine, SimulatedInputSource
    from tests.test_network_engine_models import SyntheticQkdArchive
    path = tmp_path / "journal.sqlite"; store = SimulatedTopologyStore(path)
    store.initialize(SimulatedTopology("test", 0, (SimulatedNodeState("A", NodeRole.ENDPOINT, 0, "IDLE"),
        SimulatedNodeState("B", NodeRole.ENDPOINT, 0, "IDLE")), (SimulatedLink("AB", "A", "B", 50, 0.9, True),)))
    registry = ModelRegistry(environ={}, allow_unverified=True)
    registry.register_test_archive("qkd", SyntheticQkdArchive())
    engine = NetworkEngine(store, path, topology_id="test", registry=registry, source=SimulatedInputSource(2, ()))
    before = engine.step(); p = engine.bank.processor("AB")
    assert p.keyrate.learned and before.links[0].controllers["keyrate"] == "qkd.keyrate.actor"
    features = p.features
    engine.command("set_ml_control", {"enabled": False, "expected_revision": 0, "confirmed": True})
    after = engine.step()
    assert p.features is features and not p.keyrate.learned
    assert after.links[0].controllers["keyrate"] == "baseline.static"
    engine.command("set_ml_control", {"enabled": True, "expected_revision": 1, "confirmed": True})
    assert p.features is features and p.keyrate.learned


def test_institutional_routing_uses_reference_when_export_contract_does_not_match(service, monkeypatch):
    from quantum_autopilot.network_engine.routing import Demand, effective_graph
    engine = service.network_engine
    assert engine.registry.enforce_control_contracts
    class ForbiddenPolicy:
        def act(self, *args, **kwargs):
            raise AssertionError("out-of-contract learned policy must not run")
    monkeypatch.setattr(engine.router, "_controller", lambda rng: (ForbiddenPolicy(), "qnet.routing.nominal", "active", "test export"))
    engine.registry.control_enabled = True
    topology = engine.topology()
    graph = effective_graph(topology)
    demand = Demand("LOCAL", "USP", "IME-USP", 0.70)
    plan = engine.router.plan(demand, graph, seed=7, topology=topology)
    assert plan.controller_id == "baseline.greedy_fidelity" and plan.model_state == "fallback"
    assert plan.in_contract is False and plan.fallback_used is True
    result, config, request, controller = engine.router.episode(demand, graph, ("USP", "IME-USP"), seed=7)
    assert controller == "baseline.greedy_fidelity"
