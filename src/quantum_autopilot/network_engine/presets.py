"""Reference topology of the demonstration: "Salvador metro" (design §2.5).

Eight located nodes and ten fibres of the approved prototype, at the example
coordinates of its ``GEO`` table (operator-declared positions in Salvador, BA — they
are illustration, not a campaign record), plus two nodes WITHOUT location so the
interface shows the unlocated queue: ``N-12`` (no fibre) and ``N-13`` (one fibre to a
located node, with a configured length because no geodesic exists for it).

Every fibre between two located nodes has ``distance_km`` = the geodesic distance
(``geodesy.link_geodesic_km``, the value the store itself would persist). All fibres
are active: the prototype's fixed "down / fault" states were fixtures and are not
reproduced — a fault now comes only from an input event or an operator command.
Roles are the backend's ``endpoint | repeater`` (``R-07`` and ``CORE`` are repeaters);
``CORE`` carries the visual kind ``telemetry["inventory"]["kind"] = "core"``.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from ..control_plane.geodesy import LOCATION_SOURCE, link_geodesic_km
from ..control_plane.topology import (DEFAULT_TOPOLOGY_ID, NodeRole, SimulatedLink, SimulatedNodeState,
                                      SimulatedTopology)
from .routing import DEFAULT_MIN_FIDELITY, Demand

#: node_id -> (role, longitude, latitude, place, address, port); positions of the prototype's GEO table.
SALVADOR_NODES = (
    ("N-01", "endpoint", -38.389, -12.938, "Piatã · SENAI CIMATEC area", "10.71.0.1"),
    ("N-02", "endpoint", -38.505, -13.000, "Ondina · university campus", "10.71.0.2"),
    ("N-03", "endpoint", -38.505, -12.972, "Comércio · downtown", "10.71.0.3"),
    ("R-07", "repeater", -38.430, -12.940, "CAB · Paralela", "10.71.0.7"),
    ("CORE", "repeater", -38.455, -12.977, "Iguatemi", "10.71.0.100"),
    ("N-05", "endpoint", -38.455, -12.993, "Pituba", "10.71.0.5"),
    ("N-09", "endpoint", -38.462, -12.948, "Cabula", "10.71.0.9"),
    ("N-11", "endpoint", -38.335, -12.915, "Airport area", "10.71.0.11"),
)
#: Nodes without location (bottom queue of the map).
UNLOCATED_NODES = (("N-12", "endpoint", "10.71.0.12"), ("N-13", "endpoint", "10.71.0.13"))
SALVADOR_LINKS = (
    ("N01-N02", "N-01", "N-02"), ("N02-N03", "N-02", "N-03"), ("N01-R07", "N-01", "R-07"),
    ("R07-CORE", "R-07", "CORE"), ("CORE-N03", "CORE", "N-03"), ("CORE-N05", "CORE", "N-05"),
    ("R07-N09", "R-07", "N-09"), ("N09-N11", "N-09", "N-11"), ("N11-N05", "N-11", "N-05"),
    ("N02-CORE", "N-02", "CORE"),
)
#: The fibre of the unlocated node: (link_id, located end, unlocated end, configured km).
UNLOCATED_LINK = ("N05-N13", "N-05", "N-13", 6.5)
NOMINAL_PAIR_FIDELITY = 0.90          # the author's raw fidelity of a heralded pair (routing benchmark)
INVENTORY_PORT = 4433
#: Default demands P1-P4 of the prototype.
DEFAULT_DEMANDS = (("P1", "N-01", "N-05"), ("P2", "N-03", "N-05"), ("P3", "N-01", "N-03"), ("P4", "N-09", "N-03"))


def _inventory(address: str, kind: str | None = None) -> dict:
    record = {"origin": "orion-preset", "address": address, "port": INVENTORY_PORT,
              "scope": "metadata_only_no_connection"}
    if kind is not None:
        record["kind"] = kind
    return record


def salvador_metro(topology_id: str = DEFAULT_TOPOLOGY_ID) -> SimulatedTopology:
    """The demonstration topology (version 0, nothing persisted)."""
    now = datetime.now(timezone.utc).isoformat()
    nodes = []
    for node_id, role, longitude, latitude, place, address in SALVADOR_NODES:
        telemetry = {"inventory": _inventory(address, "core" if node_id == "CORE" else None),
                     "location": {"latitude": latitude, "longitude": longitude, "place": place,
                                  "source": LOCATION_SOURCE, "set_at_utc": now}}
        nodes.append(SimulatedNodeState(node_id, NodeRole(role), 0, "IDLE", telemetry=telemetry, updated_at_utc=now))
    for node_id, role, address in UNLOCATED_NODES:
        nodes.append(SimulatedNodeState(node_id, NodeRole(role), 0, "IDLE",
                                        telemetry={"inventory": _inventory(address)}, updated_at_utc=now))
    links = [SimulatedLink(link_id, left, right, 0.0, NOMINAL_PAIR_FIDELITY, True)
             for link_id, left, right in SALVADOR_LINKS]
    link_id, left, right, distance = UNLOCATED_LINK
    links.append(SimulatedLink(link_id, left, right, distance, NOMINAL_PAIR_FIDELITY, True))
    draft = SimulatedTopology(topology_id, 0, tuple(nodes), tuple(links), updated_at_utc=now)
    measured = []
    for link in draft.links:
        geodesic = link_geodesic_km(draft, link)
        measured.append(link if geodesic is None else replace(link, distance_km=geodesic))
    return replace(draft, links=tuple(measured))


def default_demands() -> tuple:
    return tuple(Demand(demand_id, a, b, DEFAULT_MIN_FIDELITY) for demand_id, a, b in DEFAULT_DEMANDS)


def ensure_demo_workspace(store, topology_id: str = DEFAULT_TOPOLOGY_ID) -> tuple:
    """Initialise ``topology_id`` with the preset when the journal has no topology yet.

    Returns ``(topology, created)``. An existing topology is never replaced; the default
    demands P1-P4 are defined only together with a newly created workspace."""
    topology, created = store.initialize(salvador_metro(topology_id))
    if created:
        from .routing import DemandStore
        demands = DemandStore(store.path, topology_id)
        for demand in default_demands():
            demands.define(demand, topology)
    return topology, created


__all__ = ["DEFAULT_DEMANDS", "NOMINAL_PAIR_FIDELITY", "SALVADOR_LINKS", "SALVADOR_NODES", "UNLOCATED_LINK",
           "UNLOCATED_NODES", "default_demands", "ensure_demo_workspace", "salvador_metro"]
