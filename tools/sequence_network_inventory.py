"""Export the complete production demo without opening an operator workspace.

The native repeater request is an explicitly different experiment: SeQUeNCe's
Barrett--Kok reservation stack runs on the registered terrestrial session spans.
It is not equivalent to OrionQ's batched generation or noisy BBPSSW model. All
physical components and unsupported services remain in the separate census.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path

INVENTORY_SCHEMA = "orionq.sequence-network-inventory.v1"
NATIVE_SCHEMA = "orionq.sequence-network-input.v1"
C_KM_S = 299792.458
FIBRE_SPEED_KM_S = C_KM_S / 1.468
VOLATILE_KEYS = frozenset(("updated_at_utc", "set_at_utc", "created_at_utc"))


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _structural(value):
    if isinstance(value, dict):
        return {key: _structural(item) for key, item in value.items() if key not in VOLATILE_KEYS}
    if isinstance(value, (tuple, list)):
        return [_structural(item) for item in value]
    return value


def _number(value, name, minimum=0., maximum=None):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name}: finite number required")
    if value < minimum or maximum is not None and value > maximum:
        raise ValueError(f"{name}: outside valid range")
    return float(value)


def _index(rows, key):
    result = {}
    for row in rows:
        ident = row.get(key)
        if not isinstance(ident, str) or not ident or ident in result:
            raise ValueError(f"missing or duplicate {key}")
        result[ident] = row
    return result


def _validated(census):
    if not isinstance(census, dict) or census.get("schema") != INVENTORY_SCHEMA:
        raise ValueError("invalid network census schema")
    nodes = _index(census["nodes"], "node_id")
    links = _index(census["links"], "link_id")
    sessions = _index(census["sessions"], "session_id")
    for link in links.values():
        if link["left_node_id"] not in nodes or link["right_node_id"] not in nodes:
            raise ValueError("link references absent physical node")
    for session in sessions.values():
        path, spans = session["path"], session["link_ids"]
        if len(path) < 2 or len(set(path)) != len(path) or len(spans) != len(path) - 1:
            raise ValueError("invalid session path")
        if session["endpoints"] != [path[0], path[-1]]:
            raise ValueError("session endpoint/path mismatch")
        for left, right, link_id in zip(path, path[1:], spans):
            if left not in nodes or right not in nodes or link_id not in links:
                raise ValueError("session references absent physical resource")
            link = links[link_id]
            if {left, right} != {link["left_node_id"], link["right_node_id"]}:
                raise ValueError("session link/path mismatch")
    return nodes, links, sessions


def _native_sessions(census):
    nodes, links, sessions = _validated(census)
    eligible = []
    for session in sessions.values():
        technologies = set(session["technologies"])
        if not {"entanglement_swapping", "quantum_memory"} <= technologies:
            continue
        if technologies & {"tf_qkd", "satellite_doppler", "satellite_isl"}:
            continue
        if any(nodes[ident]["telemetry"].get("communicator", {}).get("physical_type") == "satellite"
               for ident in session["path"]):
            continue
        eligible.append(session)
    return nodes, links, sorted(eligible, key=lambda row: row["session_id"])


def native_mapping(census):
    """Describe substitutions and omitted physics independently of native input."""
    nodes, _, sessions = _native_sessions(census)
    supported = {row["session_id"] for row in sessions}
    return {
        "native_protocol": "SeQUeNCe Barrett-Kok generation, routing, reservation, purification and swapping",
        "equivalence_claimed": False,
        "resource_scope": "one independent registered simulation bank per session; no shared physical arbitration",
        "session_mappings": [{
            "session_id": row["session_id"], "physical_path": row["path"],
            "native_path": [f"{ident}::{row['session_id']}" for ident in row["path"]],
            "registered_bank_id": row["parameters"].get("resource_bank_id"),
            "links": row["link_ids"], "source_parameters": deepcopy(row["parameters"]),
            "devices": [{"physical_id": ident, "native_id": f"{ident}::{row['session_id']}",
                         "registered_banks": deepcopy(nodes[ident]["telemetry"].get("simulation_resource_banks", []))}
                        for ident in row["path"]],
        } for row in sessions],
        "other_sessions": [{"session_id": row["session_id"], "technologies": row["technologies"],
            "native_quantum_validation": "not_demonstrated",
            "reason": "No independently qualified native TF-QKD or moving free-space/downlink/ISL implementation in this campaign"}
            for row in census["sessions"] if row["session_id"] not in supported],
        "conversions": {
            "length": "effective fibre km = registered geographic lower bound km * route_factor",
            "insertion": "configured insertion + extra loss; distributed scalar loss in native nonzero fibre",
            "frequency": "native memory emission frequency uses declared attempt_rate_hz without artificial acceleration",
            "source_fidelity": "prepared Werner raw_fidelity = 1 - 3*source_depolarization/4; not measured output fidelity",
            "coherence": "memory_coherence_s retains declared storage interval; no equivalence with Orion pair visibility exponential or noisy protocol claimed",
            "efficiency": "native memory emission efficiency uses configured memory_read_efficiency; separate write inefficiency is not duplicated or declared equivalent",
            "detector": "explicit native efficiency, zero dark-count control, 20 MHz count ceiling and 50 ps resolution; independent reference assumption, not Orion dark_probability equivalence",
            "classical": "per-span native deterministic propagation + declared fixed latency; runtime classical RPC separately validates serialized control, jitter and receipts",
            "reservation": "request at t=0; reservation start must exceed registered path round-trip classical delay before the native RSVP experiment",
        },
        "unmatched_physics": ["Orion batched pair generation and characterization consumption",
            "independent memory write/read errors", "source phase/polarization and dark counts",
            "noisy gate/postselection BBPSSW semantics", "shared hardware arbitration",
            "TF-QKD interference/security estimator", "satellite geometry, Doppler, atmosphere and TDC acquisition"],
    }


def build_inventory(state_root, *, repository_root=None, seed=2026101001):
    """Bootstrap exactly the current console demo in a fresh isolated directory.

    Existing contents are refused before importing the production service. No
    network tick, optional-model activation or physical action is performed.
    """
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("non-negative 63-bit integer seed required")
    state = Path(state_root).resolve()
    if state.exists() and (not state.is_dir() or any(state.iterdir())):
        raise ValueError("fresh empty isolated state directory required")
    root = Path(repository_root).resolve() if repository_root else Path(__file__).resolve().parents[1]
    from quantum_autopilot.qkd.dashboard_api import demo_service
    from quantum_autopilot.network_engine.global_operations_topology import institutional_ids, operation_capabilities
    service = demo_service(root, state_root=state, preset="brazil-institutions-v1", seed=seed, scenario="quiet")
    engine = service.network_engine
    try:
        topology = engine.topology()
        raw = topology.to_dict()
        nodes = sorted(raw["nodes"], key=lambda row: row["node_id"])
        links = sorted(raw["links"], key=lambda row: row["link_id"])
        sessions = sorted((session.config.to_dict() for session in engine.technologies.sessions.values()),
                          key=lambda row: row["session_id"])
        endpoints = sorted(institutional_ids(topology))
        demands = sorted(engine.global_operations._templates(topology, engine.technologies),
                         key=lambda row: row["demand_id"])
        fpga = engine.fpga.snapshot()
        result = {
            "schema": INVENTORY_SCHEMA, "seed": seed, "preset": "brazil-institutions-v1",
            "bootstrap": "demo_service -> ensure_institutional_workspace -> complete migrations -> communicator/FPGA seed",
            "topology_id": topology.topology_id, "topology_version": topology.topology_version,
            "nodes": nodes, "links": links, "sessions": sessions,
            "institutional_endpoints": endpoints, "global_demand_templates": demands,
            "legacy_demands": [demand.to_dict() for demand in engine.demands.list()],
            "communicators": [deepcopy(node["telemetry"]["communicator"]) for node in nodes],
            "operational_capabilities": {node.node_id: operation_capabilities(node) for node in topology.nodes},
            "fpga": fpga,
            "counts": {
                "nodes": len(nodes), "links": len(links), "communicators": len(nodes),
                "institutional_endpoints": len(endpoints), "sessions": len(sessions),
                "global_demand_templates": len(demands), "legacy_demands": len(engine.demands.list()),
                "node_physical_types": dict(Counter(node["telemetry"]["communicator"]["physical_type"] for node in nodes)),
                "legacy_links": sum("technology" not in link for link in links),
                "technology_links": sum("technology" in link for link in links),
                "session_technologies": dict(Counter(technology for session in sessions for technology in session["technologies"])),
                "fpga_devices": len(fpga["devices"]),
            },
            "observed_network_ticks": 0, "dynamic_route_availability_evaluated": False,
            "physical_actuation": False, "certified_secret_bits": 0,
            "optional_models_required": False,
        }
        _validated(result)
        result["native_mapping"] = native_mapping(result)
        result["endpoint_route_census"] = endpoint_route_census(result)
        # Keep raw bootstrap timestamps while giving a reproducible structural
        # fingerprint; this is inventory identity, not authenticated evidence.
        result["structural_sha256"] = hashlib.sha256(canonical(_structural(result)).encode()).hexdigest()
        return result
    finally:
        engine.close_ogs()


def endpoint_route_census(census):
    """Enumerate every unordered institution pair, without claiming availability."""
    import networkx as nx
    nodes, links, sessions = _validated(census)
    endpoints = sorted(census["institutional_endpoints"])
    if len(set(endpoints)) != len(endpoints) or any(ident not in nodes for ident in endpoints):
        raise ValueError("invalid institutional endpoint list")
    graph = nx.Graph()
    graph.add_nodes_from(nodes)
    for link in links.values():
        pair = link.get("channels", {})
        if not link.get("active", True) or not pair.get("classical", {}).get("enabled", False):
            continue
        # Preserve the production rejection of unregenerated long direct fibres.
        if not link.get("technology") and link["distance_km"] > 150.:
            continue
        a, b = link["left_node_id"], link["right_node_id"]
        if any(nodes[ident]["telemetry"].get("communicator", {}).get("operational_state") != "enabled" for ident in (a, b)):
            continue
        weight = max(.001, _number(link["distance_km"], "distance_km"))
        if not graph.has_edge(a, b) or weight < graph[a][b]["weight"]:
            graph.add_edge(a, b, weight=weight, link_id=link["link_id"])
    rows = []
    for a, b in combinations(endpoints, 2):
        path = nx.shortest_path(graph, a, b, weight="weight") if nx.has_path(graph, a, b) else []
        span_ids = [graph[u][v]["link_id"] for u, v in zip(path, path[1:])]
        dedicated = [row["session_id"] for row in sessions.values()
                     if set(row["endpoints"]) == {a, b} and "entanglement_swapping" in row["technologies"]]
        local = [row["session_id"] for row in sessions.values() if set(row["endpoints"]) == {a, b}]
        rows.append({"a": a, "b": b, "registered_classical_path": path, "registered_links": span_ids,
            "configured_path_present": bool(path), "dynamic_availability": "not_evaluated",
            "routing_scope": "static registration and channel eligibility; orbital zero lengths are references, not measured routing costs",
            "dedicated_entanglement_sessions": dedicated, "local_protocol_sessions": local,
            "global_quantum_driver_available": bool(dedicated),
            "quantum_scope": "dedicated session only" if dedicated else "no compatible global coherent relay driver; separate QKD/BBM92 services cannot be concatenated",
            "physical_actuation": False, "certified_secret_bits": 0})
    return rows


def build_native_request(census, *, seed=2026101001, scenario_id="nominal-terrestrial-reference",
                         duration_s=.1, demand_start_s=.05, demand_duration_s=.03,
                         demand_memory_count=1, detector_efficiency=.8):
    """Generate independent native banks for every registered terrestrial session."""
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("non-negative 63-bit integer seed required")
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ValueError("nonempty native scenario identifier required")
    duration = _number(duration_s, "duration_s", 1e-6, 3600.)
    start = _number(demand_start_s, "demand_start_s", 1e-9)
    period = _number(demand_duration_s, "demand_duration_s", 1e-9)
    if start + period >= duration:
        raise ValueError("demand exceeds native experiment duration")
    if type(demand_memory_count) is not int or demand_memory_count < 1:
        raise ValueError("positive demand memory count required")
    detector = _number(detector_efficiency, "detector_efficiency", 0., 1.)
    physical_nodes, physical_links, sessions = _native_sessions(census)
    native_nodes, native_links, demands = [], [], []
    for session in sessions:
        ident, p = session["session_id"], session["parameters"]
        bank_id = p.get("resource_bank_id")
        if bank_id != f"sim-resource:{ident}" or p.get("resource_bank_scope") != "dedicated_per_session":
            raise ValueError("native experiment requires explicitly dedicated session bank")
        native_id = {node: f"{node}::{ident}" for node in session["path"]}
        profiles = p.get("device_parameters", {})
        for node_id in session["path"]:
            profile = {**p, **profiles.get(node_id, {})}
            banks = physical_nodes[node_id]["telemetry"].get("simulation_resource_banks", [])
            bank = next((row for row in banks if row.get("resource_bank_id") == bank_id), None)
            if bank is None or bank.get("memory_modes") != profile.get("memory_slots", 4):
                raise ValueError("missing or inconsistent registered memory bank")
            count = bank["memory_modes"]
            if type(count) is not int or count < 2 * demand_memory_count:
                raise ValueError("memory bank cannot reserve requested native resources")
            native_nodes.append({"id": native_id[node_id], "memory_count": count,
                "memory_frequency_hz": _number(profile.get("attempt_rate_hz", 100000.), "attempt_rate_hz", 1.),
                "memory_coherence_s": _number(profile.get("coherence_s", 1.), "coherence_s", 1e-9),
                "memory_efficiency": _number(profile.get("memory_read_efficiency", .7), "memory_read_efficiency", 0., 1.),
                "raw_fidelity": 1. - .75 * _number(profile.get("source_depolarization", .08), "source_depolarization", 0., 1.),
                "native_eligible": True})
        for a, b, link_id in zip(session["path"], session["path"][1:], session["link_ids"]):
            link = physical_links[link_id]
            length = _number(link["distance_km"], "distance_km") * _number(p.get("route_factor", 1.2), "route_factor", 1.)
            pair = link.get("channels", {})
            native_links.append({"id": link_id, "a": native_id[a], "b": native_id[b], "length_km": length,
                "attenuation_db_km": _number(p.get("attenuation_db_km", .2), "attenuation_db_km"),
                "insertion_loss_db": _number(p.get("insertion_loss_db", 2.), "insertion_loss_db") + _number(p.get("extra_loss_db", 0.), "extra_loss_db"),
                "classical_delay_s": length / FIBRE_SPEED_KM_S + _number(p.get("classical_latency_s", .001), "classical_latency_s"),
                "enabled": bool(link.get("active", True) and session.get("enabled", True) and pair.get("quantum", {}).get("enabled", False)),
                "classical_enabled": bool(pair.get("classical", {}).get("enabled", False)),
                "protocol": "entanglement_swapping"})
        round_trip = 2. * sum(link["classical_delay_s"] for link in native_links if link["id"] in session["link_ids"])
        if start <= round_trip:
            raise ValueError(f"{ident}: reservation start must follow classical round trip ({round_trip:.12g} s)")
        demands.append({"id": ident, "source": native_id[session["endpoints"][0]],
            "target": native_id[session["endpoints"][1]], "start_s": start, "duration_s": period,
            "memory_count": demand_memory_count, "target_fidelity": _number(p.get("min_fidelity", .7), "min_fidelity", 0., 1.),
            "request_s": 0.})
    topology_hash = census.get("structural_sha256") or hashlib.sha256(canonical(_structural(census)).encode()).hexdigest()
    return {"schema": NATIVE_SCHEMA, "scenario_id": scenario_id, "topology_sha256": topology_hash,
            "seed": seed, "duration_s": duration,
            "detector": {"efficiency": detector, "dark_count_hz": 0.,
                         "count_rate_hz": 20000000., "time_resolution_ps": 50},
            "nodes": native_nodes, "links": native_links, "demands": demands}
