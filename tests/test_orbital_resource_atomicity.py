"""Synthetic orbital bank admission and centralized atomic resource reservations."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from threading import Barrier
import time

import pytest

from quantum_autopilot.control_plane import mixed_controller as mixed_module
from quantum_autopilot.control_plane.mixed_controller import MixedController
from quantum_autopilot.quantum_protocols.contracts import ProtocolError
from quantum_autopilot.quantum_protocols.orbital_relay import OrbitalRelayAdapter
from quantum_autopilot.quantum_protocols.orbital_relay_topology import PATH
from quantum_autopilot.quantum_protocols.runtime import ProtocolRuntime
from quantum_autopilot.quantum_protocols.sources import SimulatedAcquisitionSource
from tests.test_mixed_controller import configuration, demand, ready_records
from tests.test_orbital_relay_backend import relay_topology

pytestmark = pytest.mark.unit


def test_second_session_cannot_claim_the_same_exclusive_orbital_bank(tmp_path):
    topology, cfg = relay_topology()
    second = replace(cfg, session_id="same-bank-competitor")
    with pytest.raises(ProtocolError, match="resource bank"):
        OrbitalRelayAdapter().validate(second, topology)
    topology = replace(topology, nodes=tuple(replace(n, telemetry={**n.telemetry,
        "technology_sessions": [cfg.to_dict(), second.to_dict()] if n.node_id == PATH[0] else []})
        for n in topology.nodes))
    runtime = ProtocolRuntime(tmp_path / "overlap.sqlite", topology.topology_id,
                              source=SimulatedAcquisitionSource(1001))
    runtime.advance(topology, 0.)
    assert runtime.sessions[second.session_id].status == "incompatible"
    assert not runtime.sessions[second.session_id].capture_reservations
    original = runtime.sessions[cfg.session_id]
    assert original.capture_reservations
    used = sum(slots for ends, slots in original.capture_reservations.values() if PATH[1] in ends)
    assert used <= cfg.parameters["memory_slots"]


@pytest.mark.parametrize("mutation", ["missing_bank", "wrong_bank", "wrong_owner", "duplicate_claim", "scope", "capacity", "malformed_inventory"])
def test_orbital_resource_claim_must_match_registered_bank(mutation):
    topology, cfg = relay_topology()
    parameters = deepcopy(cfg.parameters)
    nodes = list(topology.nodes)
    index = next(i for i, n in enumerate(nodes) if n.node_id == PATH[1])
    node = nodes[index]
    banks = deepcopy(node.telemetry["simulation_resource_banks"])
    if mutation == "missing_bank":
        banks = []
    elif mutation == "wrong_bank":
        parameters["resource_bank_id"] = "unregistered-memory"
    elif mutation == "wrong_owner":
        banks[0]["session_id"] = "another-owner"
    elif mutation == "duplicate_claim":
        banks += deepcopy(banks)
    elif mutation == "scope":
        parameters["resource_bank_scope"] = "shared_without_lease"
    elif mutation == "capacity":
        parameters["memory_slots"] += 1
    elif mutation == "malformed_inventory":
        banks = None
    nodes[index] = replace(node, telemetry={**node.telemetry, "simulation_resource_banks": banks})
    with pytest.raises(ProtocolError, match="resource bank"):
        OrbitalRelayAdapter().validate(replace(cfg, parameters=parameters), replace(topology, nodes=tuple(nodes)))


def test_two_simultaneous_quantum_reservations_have_one_winner_and_expire(tmp_path, monkeypatch):
    core = MixedController(tmp_path / "quantum-reservation.sqlite")
    try:
        config = configuration(capacity=1)
        for node in config["nodes"]:
            node["capabilities"] += ["entanglement_memory", "bsm"]
        core.configure(config)
        records = ready_records(config)
        for item in records:
            observation = item["observation"]
            if observation["kind"] == "link_state":
                observation["data"]["quantum_state"] = "up"
                from quantum_autopilot.control_plane.mixed_contracts import digest
                item["provenance"]["record_sha256"] = digest(observation)
        core.ingest({"records": records})
        now = time.time_ns()
        monkeypatch.setattr(mixed_module.time, "time_ns", lambda: now)
        barrier = Barrier(2)
        def reserve(index):
            barrier.wait(timeout=10)
            return core.demand({**demand(f"simultaneous-{index}", operation="entanglement"), "ttl_s": 1})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, range(2)))
        assert sorted(r["status"] for r in results) == ["blocked", "reserved"]
        winner = next(r for r in results if r["status"] == "reserved")
        assert winner["route"] == ["N01", "N02", "N03"]
        assert all(r["created_ns"] == now for r in results)
        assert winner["quantum_result"] is None  # Reservation is not delivered entanglement.
        monkeypatch.setattr(mixed_module.time, "time_ns", lambda: now + 10**9 + 1)
        assert core.demand(winner["request"])["status"] == "expired"
        fresh = core.demand(demand("after-expiration", operation="entanglement"))
        assert fresh["status"] == "reserved"
        assert core.demand(winner["request"])["status"] == "expired"
    finally:
        core.close()


def test_independent_declared_orbital_banks_remain_compatible(tmp_path):
    topology, cfg = relay_topology()
    second = replace(cfg, session_id="independent-relay", parameters={**cfg.parameters,
                     "resource_bank_id": "sim-resource:independent-relay"})
    nodes = []
    for node in topology.nodes:
        banks = deepcopy(node.telemetry.get("simulation_resource_banks", []))
        if node.node_id in cfg.path:
            original = banks[0]
            banks.append({**original, "resource_bank_id": second.parameters["resource_bank_id"],
                          "session_id": second.session_id})
        nodes.append(replace(node, telemetry={**node.telemetry, "simulation_resource_banks": banks,
            "technology_sessions": [cfg.to_dict(), second.to_dict()] if node.node_id == PATH[0] else []}))
    topology = replace(topology, nodes=tuple(nodes))
    runtime = ProtocolRuntime(tmp_path / "independent-banks.sqlite", topology.topology_id,
                              source=SimulatedAcquisitionSource(1001))
    runtime.advance(topology, 0.)
    for ident in (cfg.session_id, second.session_id):
        session = runtime.sessions[ident]
        assert session.status != "incompatible" and session.capture_reservations
        used = sum(slots for ends, slots in session.capture_reservations.values() if PATH[1] in ends)
        assert used <= cfg.parameters["memory_slots"]
