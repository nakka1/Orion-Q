"""Routing and reallocation of the network engine: effective graph, demands, plans, no-route reasons."""
from dataclasses import replace

import networkx as nx
import numpy as np
import pytest

from qnet_network.agents.actor_critic import ActorCriticPolicy
from quantum_autopilot.control_plane import routing_loop as rl
from quantum_autopilot.control_plane.topology import (NodeRole, SimulatedLink, SimulatedNodeState, SimulatedTopology,
                                                      SimulatedTopologyStore)
from quantum_autopilot.network_engine import (Demand, DemandStore, LinkLive, ModelRegistry, Router, RouterConfig,
                                              block_reasons, effective_graph, explain_no_route, imposed_flags,
                                              salvador_metro)
from quantum_autopilot.network_engine import routing as rt
from quantum_autopilot.network_engine.allocation import PLANT_REASON, Allocator
from quantum_autopilot.network_engine.state import DetectorLive, QkdLive
from tests.test_routing_loop import PolicyArchive

pytestmark = pytest.mark.unit
AT = "2026-10-04T12:00:00+00:00"


def square(topology_id="sandbox-line-3", *, lengths=(5.0, 6.0, 5.5, 4.0, 12.0)):
    """Four nodes: ring A-B-C-D-A plus the long diagonal A-C. Demand A<->C has three candidate paths."""
    nodes = tuple(SimulatedNodeState(name, NodeRole.REPEATER if name in "BD" else NodeRole.ENDPOINT, 0, "IDLE")
                  for name in "ABCD")
    ab, bc, cd, da, ac = lengths
    links = (SimulatedLink("A-B", "A", "B", ab, 0.9), SimulatedLink("B-C", "B", "C", bc, 0.9),
             SimulatedLink("C-D", "C", "D", cd, 0.9), SimulatedLink("D-A", "D", "A", da, 0.9),
             SimulatedLink("A-C", "A", "C", ac, 0.9))
    return SimulatedTopology(topology_id, 0, nodes, links)


def with_link(topology, link_id, **changes):
    return replace(topology, links=tuple(replace(link, **changes) if link.link_id == link_id else link
                                         for link in topology.links))


def isolate(topology, node_id):
    """What ``set_node_isolation`` leaves behind: the flag and the incident fibres switched off."""
    nodes = tuple(replace(node, telemetry={**node.telemetry, "console_control": {"isolated": True}})
                  if node.node_id == node_id else node for node in topology.nodes)
    links = tuple(replace(link, active=False) if node_id in (link.left_node_id, link.right_node_id) else link
                  for link in topology.links)
    return replace(topology, nodes=nodes, links=links)


def live(link, status, fidelity=None):
    return LinkLive(link.link_id, link.left_node_id, link.right_node_id, link.distance_km, status,
                    qber=None if fidelity is None else (1 - fidelity) / 1.5, fidelity=fidelity,
                    detector=DetectorLive(), qkd=QkdLive())


def registry():
    return ModelRegistry(environ={})


# ---------------------------------------------------------------- effective graph

def test_effective_graph_applies_the_availability_rule():
    topology = square()
    graph = effective_graph(topology)
    assert graph.number_of_edges() == 5 and graph.graph["blocked"] == {}
    assert graph["A"]["B"] == {"length": 5.0, "distance_km": 5.0, "link_id": "A-B", "fidelity": 0.9,
                               "fidelity_source": "nominal", "status": None}

    disabled = effective_graph(with_link(topology, "A-B", active=False))
    assert not disabled.has_edge("A", "B") and disabled.graph["blocked"] == {"A-B": ("fibre A-B disabled",)}

    isolated = effective_graph(isolate(topology, "B"))
    assert set(isolated.graph["blocked"]) == {"A-B", "B-C"}
    assert isolated.graph["blocked"]["A-B"] == ("node B isolated",)
    assert "B" in isolated and isolated.degree("B") == 0

    calibrating = effective_graph(topology, None, {"D"})
    assert calibrating.graph["blocked"] == {"C-D": ("node D calibrating",), "D-A": ("node D calibrating",)}

    states = {link.link_id: live(link, "up", 0.96) for link in topology.links}
    states["A-C"] = live(topology.links[4], "compromised", 0.80)
    states["B-C"] = live(topology.links[1], "down", 0.70)
    states["A-B"] = live(topology.links[0], "no_input")
    measured = effective_graph(topology, states)
    assert measured.graph["blocked"] == {
        "A-C": ("fibre A-C compromised (attack alarm held)",),
        "B-C": ("fibre B-C down (no usable key in the measured period)",)}
    assert not measured.has_edge("B", "C"), "received metadata does not make a measured down quantum hop usable"
    assert measured["C"]["D"]["fidelity"] == 0.96 and measured["C"]["D"]["fidelity_source"] == "estimated"
    assert measured["A"]["B"]["fidelity"] == 0.9 and measured["A"]["B"]["fidelity_source"] == "nominal"


def test_bank_flags_come_from_the_same_rule():
    topology = isolate(with_link(square(), "C-D", active=False), "B")
    assert imposed_flags(topology, {"D"}) == {"A-B": "isolated", "B-C": "isolated", "C-D": "disabled",
                                              "D-A": "calibrating"}
    reasons = block_reasons(topology, None, {"D"})
    assert reasons["A-C"] == () and reasons["C-D"] == ("fibre C-D disabled", "node D calibrating")


def test_parallel_fibres_are_one_hop_the_shortest():
    topology = square()
    extra = replace(topology, links=topology.links + (SimulatedLink("A-B2", "A", "B", 2.0, 0.9),))
    assert effective_graph(extra)["A"]["B"]["link_id"] == "A-B2"
    assert effective_graph(with_link(extra, "A-B2", active=False))["A"]["B"]["link_id"] == "A-B"


# ---------------------------------------------------------------- demands

def test_demand_store_crud_and_validation(tmp_path):
    path = tmp_path / "journal.sqlite"
    SimulatedTopologyStore(path)
    topology = square()
    store = DemandStore(path, topology.topology_id)
    assert store.list() == ()
    store.define(Demand("P1", "A", "C"), topology)
    store.define(Demand("P2", "B", "D", 0.74), topology)
    assert [(item.demand_id, item.a, item.b, item.min_fidelity) for item in store.list()] == [
        ("P1", "A", "C", 0.70), ("P2", "B", "D", 0.74)]
    store.set_pair("P1", {"status": "delivered", "fidelity": 0.8})
    assert store.pair("P1") == {"status": "delivered", "fidelity": 0.8} and store.pairs().keys() == {"P1"}
    store.define(Demand("P1", "A", "D"), topology)                     # redefinition drops the pair
    assert store.pair("P1") is None and store.list()[0].b == "D"
    assert DemandStore(path, "another-topology").list() == ()
    with pytest.raises(ValueError, match="not a node"):
        store.define(Demand("P3", "A", "Z"), topology)
    with pytest.raises(ValueError, match="different endpoints"):
        Demand("P3", "A", "A")
    with pytest.raises(ValueError, match="min_fidelity"):
        Demand("P3", "A", "B", 1.0)
    with pytest.raises(ValueError, match="ASCII"):
        Demand("P 3", "A", "B")
    for index in range(rt.MAX_DEMANDS - 2):
        store.define(Demand(f"X{index}", "A", "B"), topology)
    with pytest.raises(ValueError, match="at most 16"):
        store.define(Demand("overflow", "A", "B"), topology)
    store.remove("X0")
    with pytest.raises(ValueError, match="unknown demand"):
        store.remove("X0")
    assert len(DemandStore(path, topology.topology_id).list()) == rt.MAX_DEMANDS - 1


# ---------------------------------------------------------------- plans

def test_plan_runs_real_episodes_and_reports_the_delivered_path():
    router = Router(registry())
    graph = effective_graph(square())
    plan = router.plan(Demand("P1", "A", "C"), graph, seed=11)
    # A-D-C is 9.5 km, A-B-C 11 km, the diagonal 12 km: the heuristic covers the fewest-kilometre path.
    assert plan.status == "routed" and plan.route == ("A", "D", "C") and plan.route_links == ("D-A", "C-D")
    assert plan.episodes >= 3 and 0.5 < plan.success_rate <= 1.0 and 0.70 <= plan.mean_fidelity < 0.90
    assert plan.controller_id == "baseline.greedy_fidelity" and plan.model_state == "unavailable"
    assert plan.fallback_used is True and plan.in_contract is False
    assert any("4-node 20 km chain" in reason for reason in plan.contract_reasons)
    assert plan.deadline_s == pytest.approx(2 * 2 * 0.004), "two generation budgets per hop bind on metro spans"
    assert plan.mean_steps > 0 and plan.mean_latency_ms > 0 and plan.route_share == 1.0
    assert router.plan(Demand("P1", "A", "C"), graph, seed=11).numbers() | {"planning_ms": 0} == \
        plan.numbers() | {"planning_ms": 0}, "a plan is reproducible from its seed"
    status = {item.role: item for item in router.registry.status()}["routing"]
    assert status.state == "unavailable" and status.in_contract is False and "greedy_fidelity acting" in status.reason


def test_episode_lineage_is_the_path_the_pair_was_built_on():
    router = Router(registry())
    config, request, _ = router.plant(effective_graph(square()), Demand("P1", "B", "D"))
    from qnet_network.agents import GreedyFidelityPolicy
    from qnet_network.env import QuantumNetworkEnv
    result = rt.run_episode(QuantumNetworkEnv(config, rng=3), request, GreedyFidelityPolicy(), 3)
    assert result.delivered and result.path[0] == "B" and result.path[-1] == "D"
    assert result.swaps_at == result.path[1:-1] and result.hops == len(result.path) - 1
    assert all(config.graph.has_edge(a, b) for a, b in zip(result.path, result.path[1:]))
    assert len(result.state) == 4 and result.state[0] == pytest.approx(result.fidelity)
    assert result.memory_wait_s <= result.latency_s <= request.deadline_s


def test_plan_stays_inside_its_time_budget():
    router = Router(registry(), RouterConfig(time_budget_s=0.05))
    graph = effective_graph(salvador_metro())
    for a, b in (("N-01", "N-05"), ("N-09", "N-03"), ("N-11", "N-02")):
        plan = router.plan(Demand("P", a, b, 0.9), graph, seed=5)       # unreachable threshold: the slow case
        assert plan.status == "blocked" and plan.success_rate == 0.0 and 3 <= plan.episodes <= 24
        assert plan.planning_ms < 400.0
        assert "connected but no episode delivered" in plan.reason
        assert "episodes ended by: " in plan.reason and "{" not in plan.reason and "}" not in plan.reason


def test_no_route_names_what_blocks_it():
    topology = with_link(with_link(square(), "A-C", active=False), "A-B", active=False)
    router = Router(registry())
    cut = isolate(topology, "D")
    plan = router.plan(Demand("P1", "A", "C"), effective_graph(cut), seed=1, topology=cut)
    assert plan.status == "no_route" and plan.route == () and plan.success_rate is None and plan.episodes == 0
    assert plan.explanation["blockers"] == ["fibre A-B disabled", "fibre A-C disabled", "node D isolated"]
    assert [item["link_id"] for item in plan.explanation["cut"]] == ["A-B", "A-C", "D-A"]
    assert plan.explanation["shortest_path"] == ["A", "D", "C"]
    assert "fibre A-B disabled" in plan.reason and "node D isolated" in plan.reason

    calibrating = effective_graph(topology, None, {"D"})
    explanation = explain_no_route(topology, calibrating, "A", "C")
    assert explanation["blockers"] == ["fibre A-B disabled", "fibre A-C disabled", "node D calibrating"]

    lonely = replace(topology, links=tuple(link for link in topology.links if "A" not in link.link_id.split("-")))
    assert "no fibre path between A and C is configured" in explain_no_route(
        lonely, effective_graph(lonely), "A", "C")["reason"]

    states = {link.link_id: live(link, "compromised", 0.8) for link in square().links
              if link.link_id in ("A-B", "D-A", "A-C")}
    alarm = router.plan(Demand("P1", "A", "C"), effective_graph(square(), states), seed=1, topology=square(),
                        link_live=states)
    assert alarm.status == "no_route" and "compromised (attack alarm held)" in alarm.reason


def test_the_reviewed_chain_is_the_only_plant_in_contract():
    router = Router(registry())
    chain = nx.relabel_nodes(rl.make_config("nominal").graph, str)
    for u, v, data in chain.edges(data=True):
        data.update(link_id=f"{u}-{v}", fidelity=0.9, fidelity_source="nominal")
    config, request, reasons = router.plant(chain, Demand("X", "0", "3", 0.74))
    assert reasons == () and request.deadline_s == pytest.approx(0.065)
    assert {key: getattr(config, key) for key in vars(rl.make_config("nominal")) if key not in ("graph", "_relabel")} \
        == {key: getattr(rl.make_config("nominal"), key) for key in vars(rl.make_config("nominal"))
            if key not in ("graph", "_relabel")}
    assert router.plan(Demand("X", "0", "3", 0.74), chain, seed=2).in_contract is True
    assert router.plant(chain, Demand("X", "0", "3", 0.72))[2] == (
        "fidelity threshold outside the reviewed choices 0.70 / 0.74 / 0.78",)
    assert "request endpoints differ" in router.plant(chain, Demand("X", "0", "2", 0.74))[2][0]


def learned_registry(seed=4):
    models = ModelRegistry(allow_unverified=True, environ={})
    models.register_test_archive("qnet", PolicyArchive(ActorCriticPolicy(rng=seed)))
    return models


def test_learned_router_is_active_but_out_of_contract_on_the_metro_topology():
    router = Router(learned_registry())
    graph = effective_graph(salvador_metro())
    plan = router.plan(Demand("P2", "N-03", "N-05"), graph, seed=9)
    assert plan.model_state == "active" and plan.in_contract is False
    assert plan.status in ("routed", "blocked")
    status = {item.role: item for item in router.registry.status()}["routing"]
    assert status.state == "active" and status.in_contract is False and status.inferences > 0
    assert status.archive_sha256 == "synthetic-from-author-policy"
    if plan.controller_id == "baseline.greedy_fidelity":
        # An untrained actor delivers nothing: the plan-level supervisor hands the request to the standby.
        assert plan.fallback_used is True and "standby planned the route" in plan.reason
        assert plan.route == ("N-03", "CORE", "N-05")
    else:
        assert plan.controller_id == "qnet.routing.nominal"


def test_refused_inferences_fall_back_to_the_greedy_standby(monkeypatch):
    router = Router(learned_registry())
    graph = effective_graph(salvador_metro())
    router.plan(Demand("P2", "N-03", "N-05"), graph, seed=9)             # builds the replay

    def refuse(*args, **kwargs):
        raise ValueError("contract refusal")

    monkeypatch.setattr(router._replay.model, "evaluate", refuse)
    plan = router.plan(Demand("P2", "N-03", "N-05"), graph, seed=9)
    reference = Router(registry()).plan(Demand("P2", "N-03", "N-05"), graph, seed=9)
    assert plan.model_state == "active" and plan.fallback_used is True and plan.faults >= 3 * plan.episodes
    assert plan.status == "routed" and plan.route == reference.route == ("N-03", "CORE", "N-05")
    assert plan.success_rate == reference.success_rate and plan.mean_fidelity == reference.mean_fidelity
    assert {item.role: item for item in router.registry.status()}["routing"].faults >= plan.faults


def test_unreadable_routing_export_leaves_the_baseline_in_charge():
    class Broken:
        kind, sha256 = "qnet", "broken"

        def json(self, name):
            return {"layers": {"actor": 1, "critic": 1}}

        def arrays(self, name):
            return {}

    models = ModelRegistry(allow_unverified=True, environ={})
    models.register_test_archive("qnet", Broken())
    plan = Router(models).plan(Demand("P1", "A", "C"), effective_graph(square()), seed=1)
    assert plan.model_state == "fallback" and plan.controller_id == "baseline.greedy_fidelity"
    assert plan.status == "routed" and plan.fallback_used is True


# ---------------------------------------------------------------- pair operations

def test_purification_uses_the_authors_closed_form():
    from qnet_network.physics import bell_diagonal as bds
    router = Router(registry())
    config, _, _ = router.plant(effective_graph(square()), Demand("P1", "A", "C"))
    kept, second = bds.werner(0.80), bds.werner(0.78)
    expected_p, expected_state = bds.dejmps(kept, second, gate_fid=config.gate_fid, meas_fid=config.meas_fid)
    outcome = rt.purify(kept, second, config, np.random.default_rng(0))
    assert outcome["protocol"] == "dejmps" and outcome["success_probability"] == pytest.approx(float(expected_p))
    assert outcome["fidelity"] == pytest.approx(float(bds.fidelity(expected_state))) and outcome["fidelity"] > 0.80
    draws = [rt.purify(kept, second, config, np.random.default_rng(seed))["succeeded"] for seed in range(200)]
    assert abs(np.mean(draws) - float(expected_p)) < 0.12


def test_chain_estimate_reads_the_fibre_estimates_not_the_plant():
    topology = square()
    states = {link.link_id: live(link, "up", 0.97) for link in topology.links}
    graph = effective_graph(topology, states)
    reference = Router(registry()).reference
    estimate = rt.chain_estimate(graph, ("A", "D", "C"), reference)
    assert estimate["source"] == "estimated" and estimate["links"] == [0.97, 0.97]
    assert 0.93 < estimate["fidelity"] < 0.97
    nominal = rt.chain_estimate(effective_graph(topology), ("A", "D", "C"), reference)
    assert nominal["source"] == "nominal" and nominal["fidelity"] < estimate["fidelity"]
    assert rt.chain_estimate(graph, ("A", "Z"), reference) is None


# ---------------------------------------------------------------- allocation

def test_allocation_runs_quantum_first_and_reports_the_learned_allocator_unavailable():
    topology = square()
    allocator = Allocator(registry())
    status = {item.role: item for item in allocator.registry.status()}["allocation"]
    assert status.state == "unavailable" and status.in_contract is False and PLANT_REASON in status.reason
    key = {"skr_bps": 4.0e6, "qkd": QkdLive(key_pool_bits=1e7, discarded_bits=0.0)}
    links = {link.link_id: replace(live(link, "up", 0.96), **key) for link in topology.links}
    links["B-C"] = replace(links["B-C"], status="degraded", skr_bps=1.0e6)
    links["C-D"] = replace(links["C-D"], status="compromised")
    links["D-A"] = live(topology.links[3], "no_input")
    links["A-C"] = live(topology.links[4], "disabled")
    out, decisions = allocator.step(links, tick=5, at_utc=AT)
    assert out["A-B"].classical_admission["level"] == 1.0 and out["A-B"].classical_admission["backlog_bits"] == 0.0
    assert out["B-C"].classical_admission["level"] == 0.0 and out["B-C"].classical_admission["backlog_bits"] > 0
    assert out["C-D"].classical_admission["level"] == 0.0
    assert out["D-A"].classical_admission is None and out["A-C"].classical_admission is None, \
        "no observables: no decision is invented"
    record = out["B-C"].classical_admission
    assert record["controller_id"] == "baseline.quantum_first" and record["model_state"] == "unavailable"
    assert record["in_contract"] is False and record["fallback_used"] is True
    assert sorted(item.subject for item in decisions) == ["A-B", "B-C", "C-D"]
    assert all(item.kind == "allocation" and item.verify() and item.physical_action_performed is False
               for item in decisions)
    again, repeated = allocator.step(links, tick=6, at_utc=AT)
    assert repeated == () and again["B-C"].classical_admission["level"] == 0.0
    links["B-C"] = replace(links["B-C"], status="up", skr_bps=4.0e6)
    _, changed = allocator.step(links, tick=7, at_utc=AT)
    assert [(item.subject, item.outcome) for item in changed] == [("B-C", "admission=1")]

    present = ModelRegistry(allow_unverified=True, environ={})
    present.register_test_archive("qnet", PolicyArchive(ActorCriticPolicy(rng=1)))
    status = {item.role: item for item in Allocator(present).registry.status()}["allocation"]
    assert status.state == "fallback" and PLANT_REASON in status.reason


def test_blocked_reason_is_written_for_an_operator_and_keeps_every_count():
    """The reason shown on screen is the backend's sentence; no raw dict fragment reaches the UI."""
    assert rt.outcome_counts({"deadline": 21, "max_steps": 3}) == "deadline × 21, max steps × 3"
    assert rt.outcome_counts({"max_steps": 3, "deadline": 3}) == "deadline × 3, max steps × 3"
    assert rt.outcome_counts({}) == "none"
