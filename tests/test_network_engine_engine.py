"""``NetworkEngine``: one code path for every source, reallocation on commands and on data, journal."""
from contextlib import closing
import inspect
import json
import sqlite3
import statistics
import threading
import time

import pytest

from quantum_autopilot.control_plane.geodesy import link_geodesic_km, node_location
from quantum_autopilot.control_plane.topology import (NodeRole, SimulatedLink, SimulatedNodeState, SimulatedTopology,
                                                      SimulatedTopologyStore)
from quantum_autopilot.network_engine import (VERBS, Decision, IdleInputSource, LiveState, ModelRegistry,
                                              NetworkEngine, SimulatedInputSource, ensure_demo_workspace,
                                              salvador_metro)
from quantum_autopilot.network_engine import allocation, calibration, engine as engine_module, links, routing
from quantum_autopilot.network_engine.state import canonical_sha256
from tests.test_network_engine_routing import square

pytestmark = pytest.mark.unit


def triangle():
    """A-B direct (5 km) and the detour A-C-B (4 + 4.5 km): demand A<->B has exactly two paths."""
    nodes = tuple(SimulatedNodeState(name, NodeRole.REPEATER if name == "C" else NodeRole.ENDPOINT, 0, "IDLE")
                  for name in "ABC")
    return SimulatedTopology("sandbox-line-3", 0, nodes, (
        SimulatedLink("A-B", "A", "B", 5.0, 0.9), SimulatedLink("A-C", "A", "C", 4.0, 0.9),
        SimulatedLink("C-B", "C", "B", 4.5, 0.9)))


def make(tmp_path, topology=None, *, source=None, seed=7, demands=(("P1", "A", "C"),), **kwargs):
    path = tmp_path / "journal.sqlite"
    store = SimulatedTopologyStore(path)
    if topology is None:
        ensure_demo_workspace(store)
    else:
        store.initialize(topology)
    engine = NetworkEngine(store, path, source=source or IdleInputSource(), registry=ModelRegistry(environ={}),
                           seed=seed, **kwargs)
    if topology is not None:
        for demand_id, a, b in demands:
            engine.command("define_pair", {"demand_id": demand_id, "a": a, "b": b})
    return engine, store, path


def demand(state, demand_id="P1"):
    return next(item for item in state.demands if item.demand_id == demand_id)


def node(state, node_id):
    return next(item for item in state.nodes if item.node_id == node_id)


# ---------------------------------------------------------------- preset

def test_salvador_preset_is_valid_and_on_land():
    from quantum_autopilot.console.geodata import load_salvador
    basemap = load_salvador()
    topology = salvador_metro()
    located = {item.node_id: node_location(item) for item in topology.nodes if node_location(item)}
    assert set(located) == {"N-01", "N-02", "N-03", "R-07", "CORE", "N-05", "N-09", "N-11"}
    for node_id, place in located.items():
        assert basemap.contains_land(place["longitude"], place["latitude"]), node_id
        assert place["source"] == "operator_declared" and 0 < len(place["place"]) <= 80
    unlocated = [item.node_id for item in topology.nodes if node_location(item) is None]
    assert unlocated == ["N-12", "N-13"]
    assert {item.node_id for item in topology.nodes if item.role is NodeRole.REPEATER} == {"R-07", "CORE"}
    assert topology.node("CORE").telemetry["inventory"]["kind"] == "core"
    assert len(topology.links) == 11 and all(link.active for link in topology.links)
    for link in topology.links:
        geodesic = link_geodesic_km(topology, link)
        if link.link_id == "N05-N13":
            assert geodesic is None and link.distance_km == 6.5
        else:
            assert link.distance_km == geodesic and 1.0 < geodesic < 20.0
    assert not any("N-12" in (link.left_node_id, link.right_node_id) for link in topology.links)
    assert {link.link_id for link in topology.links} >= {"N01-N02", "N02-N03", "N01-R07", "R07-CORE", "CORE-N03",
                                                         "CORE-N05", "R07-N09", "N09-N11", "N11-N05", "N02-CORE"}


def test_demo_workspace_is_created_once_with_the_default_demands(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "journal.sqlite")
    topology, created = ensure_demo_workspace(store)
    assert created and topology.topology_version == 0 and store.get(topology.topology_id) == topology
    demands = routing.DemandStore(store.path, topology.topology_id)
    assert [(item.demand_id, item.a, item.b) for item in demands.list()] == [
        ("P1", "N-01", "N-05"), ("P2", "N-03", "N-05"), ("P1"[:1] + "3", "N-01", "N-03"), ("P4", "N-09", "N-03")]
    demands.remove("P4")
    again, created = ensure_demo_workspace(store)
    assert not created and again == topology and len(demands.list()) == 3, "an existing workspace is never reset"


# ---------------------------------------------------------------- idle source: configuration and routing only

def test_idle_engine_routes_on_nominal_values_and_fabricates_nothing(tmp_path):
    engine, store, _ = make(tmp_path)
    first = engine.live_state()
    assert first.tick == 0 and first.source.kind == "none" and first.schema == "orion.network-live.v1"
    assert [item.status for item in first.demands] == ["routed"] * 4, "planned at construction, before any tick"
    state = engine.step()
    assert state.tick == 1 and engine.live_state() is state and state.network_time_s == 1.0
    assert LiveState.from_json(state.to_json()) == state
    for link in state.links:
        assert link.status == "no_input" and link.wave == () and link.classical_admission is None
        assert (link.qber, link.fidelity, link.skr_bps, link.stability, link.sync_residual_ps) == (None,) * 5
        assert link.detector.label is None and link.qkd.key_pool_bits is None
    p1 = demand(state)
    assert p1.route == ("N-01", "R-07", "CORE", "N-05") and p1.route_links == ("N01-R07", "R07-CORE", "CORE-N05")
    assert p1.in_contract is False and p1.fallback_used is True and p1.policy_id == "baseline.greedy_fidelity"
    assert p1.min_fidelity == 0.70 and p1.detail["link_estimate_fidelity"]["source"] == "nominal"
    assert node(state, "CORE").routes_through == ("P1", "P2", "P3", "P4")
    core = node(state, "CORE")
    assert (core.role, core.kind, core.status, core.located, core.place) == ("repeater", "core", "up", True, "Iguatemi")
    assert core.temperature_c is None and core.address == "10.71.0.100" and core.port == 4433
    assert core.memory_state == "IDLE" and core.pairs == () and core.calibration is None
    assert set(core.links) == {"R07-CORE", "CORE-N03", "CORE-N05", "N02-CORE"}
    queue = node(state, "N-12")
    assert (queue.located, queue.latitude, queue.links, queue.status) == (False, None, (), "up")
    assert node(state, "N-13").links == ("N05-N13",) and node(state, "R-07").kind == "repeater"
    roles = {item.role: item for item in state.models}
    assert roles["routing"].state == "unavailable" and roles["allocation"].state == "unavailable"
    assert not [item for item in state.decisions if item.kind not in ("reroute", "no_route")]
    with pytest.raises(ValueError, match="idle source has no input"):
        engine.command("inject_input_event", {"kind": "loss_event", "link_id": "R07-CORE"})


def test_same_processing_objects_for_simulated_and_idle_sources(tmp_path, monkeypatch):
    calls = {"none": [], "simulated": []}
    current = {"kind": None}

    def spy(owner, name):
        original = getattr(owner, name)

        def wrapper(*args, **kwargs):
            calls[current["kind"]].append(f"{owner.__name__}.{name}")
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, wrapper)

    for owner, name in ((links.LinkBank, "sync"), (links.LinkBank, "step"),
                        (calibration.CalibrationManager, "before_frame"),
                        (calibration.CalibrationManager, "after_bank"), (allocation.Allocator, "step"),
                        (routing.Router, "plan"), (engine_module.DecisionJournal, "append")):
        spy(owner, name)
    engines = {}
    for kind, source in (("none", IdleInputSource()), ("simulated", SimulatedInputSource(5, ()))):
        current["kind"] = kind
        (tmp_path / kind).mkdir()
        engines[kind], _, _ = make(tmp_path / kind, triangle(), source=source, demands=(("P1", "A", "B"),))
        for _ in range(3):
            engines[kind].step()
        engines[kind].command("set_link_active", {"link_id": "A-B", "active": False})
        engines[kind].step()
    assert calls["none"] == calls["simulated"] and "Router.plan" in calls["none"]
    idle, simulated = engines["none"], engines["simulated"]
    for name in ("bank", "router", "allocator", "calibration", "journal", "demands"):
        assert type(getattr(idle, name)) is type(getattr(simulated, name))
    assert type(idle).step is type(simulated).step is NetworkEngine.step
    for function in (NetworkEngine.step, NetworkEngine._reallocate, NetworkEngine._publish, NetworkEngine._refresh):
        text = inspect.getsource(function)
        assert "source.kind" not in text and "isinstance(self.source" not in text and "Simulated" not in text, \
            "the processing path must not look at what kind of source feeds it"
    assert demand(idle.live_state()).route == demand(simulated.live_state()).route == ("A", "C", "B")
    assert all(link.status == "no_input" or link.status == "disabled" for link in idle.live_state().links)
    live = {link.link_id: link for link in simulated.live_state().links}
    assert live["A-C"].status in ("up", "degraded") and live["A-C"].qber is not None and len(live["A-C"].wave) > 0
    assert live["A-C"].classical_admission["controller_id"] == "baseline.quantum_first"
    assert node(simulated.live_state(), "C").temperature_c is not None


# ---------------------------------------------------------------- operator commands -> reallocation

def test_disabling_a_fibre_reroutes_at_once_and_reenabling_returns(tmp_path):
    engine, store, _ = make(tmp_path, square())
    engine.step()
    assert demand(engine.live_state()).route == ("A", "D", "C")
    version = store.get("sandbox-line-3").topology_version
    result = engine.command("set_link_active", {"link_id": "C-D", "active": False})
    assert result["status"] == "persisted" and result["physical_action_performed"] is False
    assert result["topology_version"] == version + 1 == store.get("sandbox-line-3").topology_version
    assert result["topology"] == store.get("sandbox-line-3").to_dict() and result["trigger"] == "fibre C-D disabled"
    (affected,) = result["affected_demands"]
    assert affected["status"] == "rerouted" and affected["route"] == ["A", "B", "C"]
    assert "C-D" not in affected["route_links"] and affected["detail"]["trigger"] == "fibre C-D disabled"
    assert affected["detail"]["previous_route"] == ["A", "D", "C"]
    (decision,) = result["decisions"]
    assert decision["kind"] == "reroute" and decision["subject"] == "P1" and decision["outcome"] == "rerouted"
    assert "trigger: fibre C-D disabled" in decision["summary"] and "A -> D -> C => A -> B -> C" in decision["summary"]
    assert decision["physical_action_performed"] is False and decision["in_contract"] is False
    assert decision["controller_id"] == "baseline.greedy_fidelity" and decision["model_state"] == "unavailable"
    state = engine.live_state()                      # visible before the next network second
    assert state.tick == 1 and demand(state).status == "rerouted" and demand(state).changed_at_tick == 1
    assert {link.link_id: link.status for link in state.links}["C-D"] == "disabled"
    assert [event.kind for event in state.events][-2:] == ["fibre_disabled", "reroute"]
    row = engine.recent_decisions(1)[0]
    assert row["record"] == decision and row["detail"]["old_route"] == ["A", "D", "C"]
    assert row["detail"]["new_route"] == ["A", "B", "C"] and row["detail"]["trigger"] == "fibre C-D disabled"
    assert row["detail"]["plan"]["success_rate"] > 0.5 and row["detail"]["plan"]["episodes"] >= 3

    back = engine.command("set_link_active", {"link_id": "C-D", "active": True})
    assert back["affected_demands"][0]["route"] == ["A", "D", "C"]
    assert "trigger: fibre C-D enabled" in back["decisions"][0]["summary"]
    state = engine.step()
    assert demand(state).route == ("A", "D", "C") and demand(state).status == "rerouted"
    with pytest.raises(ValueError, match="already active"):
        engine.command("set_link_active", {"link_id": "C-D", "active": True})
    with pytest.raises(ValueError, match="version conflict"):
        engine.command("set_link_active", {"link_id": "C-D", "active": False, "expected_version": 0})


def test_reallocation_respects_isolated_nodes(tmp_path):
    engine, store, _ = make(tmp_path, square())
    result = engine.command("set_node_isolation", {"node_id": "D", "isolated": True})
    affected = result["affected_demands"][0]
    assert affected["route"] == ["A", "B", "C"] and "D" not in affected["route"]
    assert affected["detail"]["trigger"] == "node D isolated"
    state = engine.step()
    assert node(state, "D").status == "isolated" and node(state, "D").routes_through == ()
    statuses = {link.link_id: link.status for link in state.links}
    assert statuses["C-D"] == statuses["D-A"] == "isolated"
    kinds = [event.kind for event in state.events]
    assert "node_isolated" in kinds and kinds.count("fibre_disabled") == 2
    # Isolating B as well leaves only the direct fibre.
    engine.command("set_node_isolation", {"node_id": "B", "isolated": True})
    assert demand(engine.live_state()).route == ("A", "C")
    restored = engine.command("set_node_isolation", {"node_id": "D", "isolated": False})
    assert restored["affected_demands"][0]["route"] == ["A", "D", "C"]
    assert node(engine.live_state(), "D").status in ("up", "down")   # fibres are processed from the next tick
    assert node(engine.step(), "D").status == "up"


def test_no_route_is_reported_with_its_reason_and_recovers(tmp_path):
    engine, store, _ = make(tmp_path, square())
    for link_id in ("A-B", "A-C"):
        engine.command("set_link_active", {"link_id": link_id, "active": False})
    result = engine.command("set_link_active", {"link_id": "D-A", "active": False})
    (affected,) = result["affected_demands"]
    assert affected["status"] == "no_route" and affected["route"] == [] and affected["fidelity"] is None
    assert affected["success_rate"] is None
    for blocker in ("fibre A-B disabled", "fibre A-C disabled", "fibre D-A disabled"):
        assert blocker in affected["reason"]
    assert [item["link_id"] for item in affected["detail"]["explanation"]["cut"]] == ["A-B", "A-C", "D-A"]
    (decision,) = result["decisions"]
    assert decision["kind"] == "no_route" and decision["outcome"] == "no_route" and "NO_ROUTE" in decision["summary"]
    state = engine.step()
    assert demand(state).status == "no_route" and state.events[-1].kind == "no_route"
    assert state.events[-1].severity == "critical"
    refused = engine.command("request_pair", {"demand_id": "P1"})
    assert refused["status"] == "no_route" and refused["pair"] is None and "fibre A-B disabled" in refused["reason"]
    # Isolating a node that is already cut off changes the explanation, not the status: no new decision.
    count = len(engine.recent_decisions(100))
    engine.command("set_node_isolation", {"node_id": "A", "isolated": True})
    assert len(engine.recent_decisions(100)) == count and "node A isolated" in demand(engine.live_state()).reason
    engine.command("set_node_isolation", {"node_id": "A", "isolated": False})
    recovered = engine.command("set_link_active", {"link_id": "A-C", "active": True})
    assert recovered["affected_demands"][0]["status"] == "routed"
    assert recovered["affected_demands"][0]["route"] == ["A", "C"]
    assert recovered["decisions"][0]["kind"] == "reroute"


def test_external_store_changes_are_adopted_at_the_next_tick(tmp_path):
    from quantum_autopilot.control_plane.inventory import SetLinkActive
    engine, store, _ = make(tmp_path, square())
    engine.step()
    store.set_link_active(SetLinkActive("C-D", False, store.get("sandbox-line-3").topology_version))
    assert demand(engine.live_state()).route == ("A", "D", "C")
    state = engine.step()
    assert demand(state).route == ("A", "B", "C") and demand(state).detail["trigger"] == "fibre C-D disabled"
    assert "fibre_disabled" in [event.kind for event in state.events]


def test_topology_verbs_delegate_to_the_store(tmp_path):
    engine, store, _ = make(tmp_path, square())
    initial_version=engine.live_state().topology_version
    added = engine.command("add_network_node", {"node_id": "E", "role": "endpoint", "address": "10.0.0.9",
                                                "port": 4000, "latitude": -12.97, "longitude": -38.5,
                                                "place": "Centro"})
    assert added["status"] == "persisted" and added["affected_demands"] == []
    state = engine.live_state()
    assert node(state, "E").located and node(state, "E").place == "Centro" and node(state, "E").links == ()
    moved = engine.command("set_node_location", {"node_id": "E", "latitude": None, "longitude": None, "place": ""})
    assert moved["status"] == "persisted" and not node(engine.live_state(), "E").located
    kinds = [event.kind for event in engine.live_state().events]
    assert "node_added" in kinds and "node_location_set" in kinds
    assert store.get("sandbox-line-3").topology_version == engine.live_state().topology_version == initial_version+2
    with pytest.raises(ValueError, match="invalid node-isolation fields"):
        engine.command("set_node_isolation", {"node_id": "E", "isolated": True, "extra": 1})
    with pytest.raises(ValueError, match="unknown network command"):
        engine.command("format_disk", {})
    assert set(VERBS) == {"set_node_isolation", "set_link_active", "set_node_location", "add_network_node",
                          "recalibrate_node", "define_pair", "remove_pair", "request_pair", "purify_pair",
                          "consume_pair", "inject_input_event", "set_model_archive", "configure_network_input", "set_ml_control",
                              "configure_technology", "configure_technology_source", "export_technology_events",
                              "save_communicator", "configure_communicator_connection", "provision_satellite_session",
                              "register_fpga_device", "configure_fpga_device", "set_fpga_connection", "inject_fpga_fault"}


# ---------------------------------------------------------------- pairs

def test_pair_lifecycle_runs_real_episodes(tmp_path):
    engine, _, _ = make(tmp_path, square())
    result = engine.command("request_pair", {"demand_id": "P1"})
    pair = result["pair"]
    assert result["status"] == "delivered" and pair["route"] == ["A", "D", "C"] and pair["swaps_at"] == ["D"]
    assert pair["hops"] == 2 and 0.70 <= pair["fidelity"] < 0.90 and pair["memory_wait_ms"] >= 0
    assert pair["latency_ms"] <= pair["deadline_ms"] and pair["controller_id"] == "baseline.greedy_fidelity"
    assert pair["physical_action_performed"] is False and pair["rounds"] == 0 and len(pair["state"]) == 4
    assert demand(engine.live_state()).pair == pair and engine.live_state().events[-1].kind == "pair_delivered"
    other = engine.command("request_pair", {"demand_id": "P1"})["pair"]
    assert other["seed"] != pair["seed"], "each request is a new episode"
    purified = engine.command("purify_pair", {"demand_id": "P1"})
    record = purified["pair"]["last_purification"]
    assert record["protocol"] == "dejmps" and 0 < record["success_probability"] <= 1
    assert purified["pair"]["rounds"] == 1 and purified["pair"]["copies_consumed"] == 2
    if record["succeeded"]:
        assert purified["status"] == "delivered" and purified["pair"]["fidelity"] > record["fidelity_before"]
        consumed = engine.command("consume_pair", {"demand_id": "P1"})
        assert consumed["status"] == "consumed" and demand(engine.live_state()).pair["status"] == "consumed"
    else:
        assert purified["status"] == "purification_failed" and purified["pair"]["fidelity"] is None
    with pytest.raises(ValueError, match="no delivered pair"):
        engine.command("consume_pair", {"demand_id": "P1"})
    with pytest.raises(ValueError, match="no delivered pair to purify"):
        engine.command("purify_pair", {"demand_id": "P1"})
    with pytest.raises(ValueError, match="unknown demand"):
        engine.command("request_pair", {"demand_id": "P9"})
    removed = engine.command("remove_pair", {"demand_id": "P1"})
    assert removed["status"] == "persisted" and engine.live_state().demands == ()
    assert engine.step().demands == ()


def test_demands_and_their_pairs_survive_an_engine_restart(tmp_path):
    engine, store, path = make(tmp_path, square())
    engine.command("request_pair", {"demand_id": "P1"})
    pair = demand(engine.live_state()).pair
    again = NetworkEngine(store, path, registry=ModelRegistry(environ={}), seed=7)
    assert demand(again.live_state()).pair == pair and demand(again.live_state()).status == "routed"


# ---------------------------------------------------------------- decisions journal

def test_decisions_are_journalled_with_verifiable_hashes(tmp_path):
    engine, _, path = make(tmp_path, square(), source=SimulatedInputSource(3, ()))
    for _ in range(3):
        engine.step()
    engine.command("set_link_active", {"link_id": "C-D", "active": False})
    rows = engine.recent_decisions(200)
    assert rows and rows == sorted(rows, key=lambda row: -row["sequence"])
    kinds = {row["record"]["kind"] for row in rows}
    assert {"reroute", "allocation", "compensation"} <= kinds
    with closing(sqlite3.connect(path)) as connection:
        stored = connection.execute("SELECT decision_id, tick, kind, subject, record_json, record_sha256 "
                                    "FROM network_decisions ORDER BY sequence").fetchall()
        assert len(stored) == len(rows) == len({row[0] for row in stored})
        for decision_id, tick, kind, subject, record_json, record_sha256 in stored:
            record = json.loads(record_json)
            body = {key: value for key, value in record.items() if key != "record_sha256"}
            assert canonical_sha256(body) == record_sha256 == record["record_sha256"]
            assert record["physical_action_performed"] is False and Decision.from_dict(record).verify()
            assert (record["decision_id"], record["tick"], record["kind"], record["subject"]) == (
                decision_id, tick, kind, subject)
        connection.execute("UPDATE network_decisions SET record_json = replace(record_json, 'P1', 'P2') "
                           "WHERE kind = 'reroute'")
        connection.commit()
    with pytest.raises(ValueError, match="fails its hash|differs|record"):
        engine.recent_decisions(200)
    with pytest.raises(ValueError, match="1..500"):
        engine.recent_decisions(0)
    assert len(engine.live_state().decisions) <= 200


# ---------------------------------------------------------------- demo: data -> detector -> reallocation

def test_attack_on_a_route_fibre_reroutes_and_the_route_returns_when_the_alarm_clears(tmp_path):
    engine, _, _ = make(tmp_path, triangle(), source=SimulatedInputSource(3, ()), demands=(("P1", "A", "B"),))
    state = None
    for _ in range(65):
        state = engine.step()
    assert demand(state).route == ("A", "B") and demand(state).status == "routed"
    assert {link.status for link in state.links} <= {"up", "degraded"}
    injected = engine.command("inject_input_event", {"kind": "intercept_resend", "link_id": "A-B", "strength": 1.0,
                                                     "duration_s": 20.0})
    assert injected["status"] == "accepted" and engine.live_state().events[-1].kind == "input_event_injected"
    seen, rerouted_at, alarmed_at, cleared_at, returned_at = [], None, None, None, None
    for _ in range(110):
        state = engine.step()
        status = {link.link_id: link for link in state.links}["A-B"].status
        seen.append(status)
        if rerouted_at is None and demand(state).route == ("A", "C", "B"):
            rerouted_at = state.tick
            assert status in ("down", "compromised") and demand(state).status == "rerouted"
            assert status in demand(state).detail["trigger"]
        if alarmed_at is None and status == "compromised":
            alarmed_at = state.tick
            assert {link.link_id: link for link in state.links}["A-B"].classical_admission["level"] == 0.0
        if alarmed_at is not None and cleared_at is None and status != "compromised":
            cleared_at = state.tick
        if cleared_at is not None and demand(state).route == ("A", "B"):
            returned_at = state.tick
            break
    assert rerouted_at is not None and rerouted_at - 66 <= 5, "alarm on the third attacked period"
    assert alarmed_at is not None and cleared_at is not None and cleared_at - alarmed_at >= 30, "the alarm is held for the detector window"
    assert returned_at == cleared_at, "the route returns as soon as the fibre is usable again"
    kinds = [event.kind for event in state.events]
    for kind in ("input_event_started", "attack_alarm_raised", "reroute", "attack_alarm_cleared"):
        assert kind in kinds
    raised = next(event for event in state.events if event.kind == "attack_alarm_raised")
    assert raised.severity == "critical" and raised.subject == "A-B"
    decisions = [item for item in engine.recent_decisions(500) if item["record"]["kind"] == "reroute"]
    assert any("usable again" in item["detail"]["trigger"] for item in decisions)
    assert any(item["record"]["kind"] == "detector" for item in engine.recent_decisions(500))


# ---------------------------------------------------------------- threads and performance

def test_live_state_is_readable_from_another_thread_while_stepping(tmp_path):
    engine, _, _ = make(tmp_path, triangle(), source=SimulatedInputSource(2, ()), demands=(("P1", "A", "B"),))
    seen, stop, errors = [], threading.Event(), []

    def reader():
        while not stop.is_set():
            try:
                state = engine.live_state()
                seen.append(state.tick)
                state.to_dict()
                time.sleep(0.01)                           # a reader that polls, not one that spins
            except Exception as exc:                       # pragma: no cover - the assertion below reports it
                errors.append(exc)

    thread = threading.Thread(target=reader)
    thread.start()
    for _ in range(6):
        engine.step()
    engine.command("set_link_active", {"link_id": "A-B", "active": False})
    stop.set()
    thread.join()
    assert not errors and seen == sorted(seen) and seen[-1] == 6 and len(set(seen)) >= 2


def test_demo_preset_tick_meets_the_budget(tmp_path):
    engine, _, _ = make(tmp_path, source=SimulatedInputSource(11, "demo"), seed=11)
    engine.step()                                          # first tick commissions every fibre's plant
    quiet = []
    for _ in range(6):
        started = time.perf_counter()
        engine.step()
        quiet.append(time.perf_counter() - started)
    assert len(engine.live_state().links) == 11 and len(engine.live_state().demands) == 4
    assert statistics.median(quiet) < 0.6, f"median demo tick {statistics.median(quiet) * 1e3:.0f} ms"
    with engine._lock:
        engine._meta = {key: {**value, "planned": -10_000} for key, value in engine._meta.items()}
    started = time.perf_counter()
    engine.step()                                          # every demand is planned again in this tick
    assert time.perf_counter() - started < 1.5
    assert all(item.detail["planned_at_tick"] == engine.tick for item in engine.live_state().demands)


def test_live_state_publishes_the_thresholds_the_running_engine_applies(tmp_path):
    """Rule: the interface reads thresholds from the backend state, never from a module default."""
    engine, _, _ = make(tmp_path, triangle())
    policy = engine.live_state().policy
    assert policy["qber_down"] == engine.bank.policy.qber_down == links.LinkPolicy().qber_down
    assert policy["fidelity_at_qber_down"] == links.werner_fidelity(engine.bank.policy.qber_down)
    assert policy["wave_rate_hz"] == engine.bank.policy.wave_hz
    assert policy["history_window_s"] == engine.bank.policy.history_seconds
    assert policy["replan_period_ticks"] == engine.router.config.replan_period_ticks
    assert LiveState.from_json(engine.live_state().to_json()).policy == policy

    (tmp_path / "custom").mkdir()
    custom, _, _ = make(tmp_path / "custom", triangle(), link_policy=links.LinkPolicy(qber_down=0.07))
    published = custom.step().policy
    assert published["qber_down"] == 0.07 != policy["qber_down"]
    assert published["fidelity_at_qber_down"] == links.werner_fidelity(0.07)
    # the same numbers after the source is swapped: the new bank keeps the engine's policy
    custom.command("configure_network_input", {"kind": "simulated", "seed": 3})
    assert custom.step().policy["qber_down"] == 0.07
