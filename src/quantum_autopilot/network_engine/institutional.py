"""Geographic demonstration. Sites are real; equipment and fibres are hypothetical.

WGS84 geodesics are optimistic lower bounds for fibre lengths, never surveyed routes.
The ordinary input plant processes those lengths without clipping them to ML domains.
"""
from dataclasses import replace
from datetime import datetime, timezone
from contextlib import closing
import math
from functools import lru_cache

from ..control_plane.geodesy import LOCATION_SOURCE, link_geodesic_km
from ..control_plane.topology import DEFAULT_TOPOLOGY_ID, NodeRole, SimulatedLink, SimulatedNodeState, SimulatedTopology
from .presets import INVENTORY_PORT, NOMINAL_PAIR_FIDELITY
from .routing import DEFAULT_MIN_FIDELITY, Demand, DemandStore

PRESET_ID = "brazil-institutions-v1"
REPEATER_MAX_SPAN_KM = 150.0
UPGRADE_EVENT = "institutional_channels_repeaters_v1"
# Coordinates identify a campus/building reference, not a surveyed quantum terminal.
SITES = (
    ("UNIJORGE", "endpoint", -12.9367532, -38.4106091, "UNIJORGE · Paralela · Salvador/BA",
     "Av. Luís Viana Filho, 6775, Salvador/BA", "https://www.unijorge.edu.br/sedes/",
     "https://www.openstreetmap.org/way/1096965108"),
    ("CIMATEC", "repeater", -12.9391787, -38.3866934, "SENAI CIMATEC · Piatã · Salvador/BA",
     "Av. Orlando Gomes, 1845, Salvador/BA", "https://senaicimatec.com.br/sobre-o-senai-cimatec/nossos-campi/cimatec-sede/",
     "https://www.openstreetmap.org/node/9864252064"),
    ("CBPF", "repeater", -22.953888889, -43.173611111, "CBPF · Urca · Rio de Janeiro/RJ",
     "Rua Dr. Xavier Sigaud, 150, Rio de Janeiro/RJ", "https://www.gov.br/cbpf/pt-br/o-cbpf/como-chegar",
     "https://www.gov.br/cbpf/pt-br/o-cbpf/como-chegar"),
    ("IME-USP", "endpoint", -23.5600895, -46.7315809, "IME-USP · Butantã · São Paulo/SP",
     "Rua do Matão, 1010, São Paulo/SP", "https://www.ime.usp.br/localizacao/",
     "https://www.ime.usp.br/localizacao/"),
    ("USP", "repeater", -23.5587573, -46.7257853, "USP · campus Butantã · São Paulo/SP",
     "Cidade Universitária, Butantã, São Paulo/SP", "https://puspc.usp.br/insitucional/",
     "https://www.openstreetmap.org/way/152638233"),
    ("UNICAMP", "endpoint", -22.8243075, -47.067048, "UNICAMP · Barão Geraldo · Campinas/SP",
     "Cidade Universitária Zeferino Vaz, Campinas/SP", "https://www.fecfau.unicamp.br/localizacao-e-mapas/a-unicamp",
     "https://www.openstreetmap.org/way/562047779"),
    ("CIQUANTA", "endpoint", -7.1512091, -34.797882, "CIQUANTA · Estação das Artes · João Pessoa/PB",
     "Estação das Artes Luciano Agra, Altiplano, João Pessoa/PB",
     "https://qa.site-padrao.portais.rke.codatahml.pb.gov.br/noticias/governo-da-paraiba-recebe-visita-do-mcti-para-acompanhar-implantacao-do-ciquanta/",
     "https://www.openstreetmap.org/way/191634953"),
)
CONNECTIONS = (
    ("UNIJORGE-CIMATEC", "UNIJORGE", "CIMATEC"),
    ("CIMATEC-CIQUANTA", "CIMATEC", "CIQUANTA"),
    ("CIMATEC-CBPF", "CIMATEC", "CBPF"),
    ("CIMATEC-USP", "CIMATEC", "USP"),
    ("CBPF-USP", "CBPF", "USP"),
    ("USP-IME", "USP", "IME-USP"),
    ("USP-UNICAMP", "USP", "UNICAMP"),
    ("IME-UNICAMP", "IME-USP", "UNICAMP"),
)
PAIRS = (("SALVADOR", "UNIJORGE", "CIMATEC"), ("BUTANTA", "IME-USP", "USP"),
         ("CAMPINAS", "USP", "UNICAMP"), ("NORDESTE", "CIMATEC", "CIQUANTA"),
         ("NACIONAL", "UNIJORGE", "CBPF"))


def brazil_institutions(topology_id=DEFAULT_TOPOLOGY_ID, *, repeaters=True):
    now = datetime.now(timezone.utc).isoformat()
    nodes = []
    for index, (ident, role, lat, lon, place, address, site_source, coordinate_source) in enumerate(SITES, 1):
        telemetry = {
            "inventory": {"origin": PRESET_ID, "address": f"10.72.0.{index}", "port": INVENTORY_PORT,
                          "scope": "metadata_only_no_connection", "kind": "core" if ident == "USP" else role},
            "location": {"latitude": lat, "longitude": lon, "place": place, "source": LOCATION_SOURCE,
                         "set_at_utc": now},
            "demonstration_site": {"preset": PRESET_ID, "address": address, "institution_source": site_source,
                                   "coordinate_source": coordinate_source, "coordinate_accuracy": "campus_reference_not_surveyed",
                                   "equipment": "hypothetical", "fibre_length": "wgs84_geodesic_lower_bound",
                                   "physical_action_performed": False},
        }
        nodes.append(SimulatedNodeState(ident, NodeRole(role), 0, "IDLE", telemetry=telemetry, updated_at_utc=now))
    links = tuple(SimulatedLink(ident, a, b, 0.0, NOMINAL_PAIR_FIDELITY, True) for ident, a, b in CONNECTIONS)
    topology = SimulatedTopology(topology_id, 0, tuple(nodes), links, updated_at_utc=now)
    topology = replace(topology, links=tuple(replace(link, distance_km=link_geodesic_km(topology, link)) for link in links))
    return _with_repeaters(topology)[0] if repeaters else topology


@lru_cache(maxsize=512)
def _repeater_location(latitude, longitude):
    """Approximate hypothetical inland placement using the bundled IBGE polygon.

    A local angular grid avoids assigning terrestrial equipment to open water.
    This is a demonstration placement, not a surveyed or optimized fibre route.
    """
    from ..console.geodata import load_brazil
    from ..control_plane.geodesy import geodesic_km
    land = load_brazil()
    if land.contains_land(longitude, latitude):
        return latitude, longitude
    for radius in range(1, 41):
        distance = radius * .025
        candidates = []
        for direction in range(16):
            angle = direction * math.tau / 16
            lat = latitude + distance * math.sin(angle)
            lon = longitude + distance * math.cos(angle)
            if land.contains_land(lon, lat):
                candidates.append((geodesic_km(latitude, longitude, lat, lon), lat, lon))
        if candidates:
            _, lat, lon = min(candidates)
            return lat, lon
    raise ValueError('no hypothetical terrestrial repeater placement near this demo span; topology preserved')


def _with_repeaters(topology):
    """Add independently acquired spans; never synthesize a delivered end-to-end pair.

    150 km is a demo engineering ceiling, not a universal fibre reach. Each actual
    WGS84 segment is checked against it, then the unchanged author plant determines
    QBER, finite-key rate and availability. Coordinates are hypothetical straight
    latitude/longitude interpolation adjusted to the bundled land polygon, not
    installed equipment or surveyed routes.
    """
    from ..control_plane.channels import local_pair
    nodes, links = list(topology.nodes), list(topology.links)
    repaired, added = [], []
    known = {ident: (a, b) for ident, a, b in CONNECTIONS}
    for index, direct in enumerate(tuple(links)):
        if known.get(direct.link_id) != (direct.left_node_id, direct.right_node_id):
            continue
        ends = [topology.node(ident) for ident in (direct.left_node_id, direct.right_node_id)]
        if not all(n.telemetry.get('inventory', {}).get('origin') == PRESET_ID for n in ends):
            continue
        if direct.channels is None:
            links[index] = replace(direct, channels=local_pair(direct.link_id, direct.right_node_id))
            repaired.append(direct.link_id)
        if direct.distance_km <= REPEATER_MAX_SPAN_KM:
            continue
        locations = [n.telemetry.get('location') for n in ends]
        if not all(locations):
            continue
        hops = math.ceil(direct.distance_km / REPEATER_MAX_SPAN_KM)
        while True:
            intermediate = []
            for step in range(1, hops):
                fraction = step / hops
                lat, lon = (locations[0][key] + fraction * (locations[1][key] - locations[0][key])
                            for key in ('latitude', 'longitude'))
                lat, lon = _repeater_location(lat, lon)
                ident = f'R-{direct.link_id}-{step:02d}'
                intermediate.append(SimulatedNodeState(ident, NodeRole.REPEATER, 0, 'IDLE', telemetry={
                    'inventory': {'origin': PRESET_ID, 'address': f'10.73.{index}.{step}',
                                  'port': INVENTORY_PORT, 'scope': 'metadata_only_no_connection', 'kind': 'repeater'},
                    'location': {'latitude': lat, 'longitude': lon, 'place': f'Simulated repeater {direct.link_id} {step}/{hops-1}',
                                 'source': LOCATION_SOURCE, 'set_at_utc': topology.updated_at_utc},
                    'demonstration_repeater': {'direct_link_id': direct.link_id, 'equipment': 'hypothetical',
                        'coordinate_source': 'hypothetical_interpolation_adjusted_to_offline_ibge_land',
                        'span_ceiling_km': REPEATER_MAX_SPAN_KM, 'physical_action_performed': False}},
                    updated_at_utc=topology.updated_at_utc))
            path = [direct.left_node_id, *(n.node_id for n in intermediate), direct.right_node_id]
            spans = [SimulatedLink(f'REP-{direct.link_id}-{step:02d}', a, b, 0.0,
                                   direct.nominal_pair_fidelity, True)
                     for step, (a, b) in enumerate(zip(path, path[1:]), 1)]
            # Check before constructing the candidate so the failure names the
            # conflicting upgrade without overwriting an operator object.
            if {n.node_id for n in intermediate} & {n.node_id for n in nodes} or \
                    {l.link_id for l in spans} & {l.link_id for l in links}:
                raise ValueError(f'repeater identifier collision on {direct.link_id}; existing configuration preserved')
            candidate = replace(topology, nodes=(*nodes, *intermediate), links=())
            spans = [replace(link, distance_km=link_geodesic_km(candidate, link)) for link in spans]
            if max(link.distance_km for link in spans) <= REPEATER_MAX_SPAN_KM:
                break
            hops += 1
        nodes.extend(intermediate)
        links.extend(spans)
        added.extend(n.node_id for n in intermediate)
    return replace(topology, nodes=tuple(nodes), links=tuple(links)), {'configured_channels': repaired, 'added_repeaters': added}


def ensure_institutional_workspace(store, topology_id=DEFAULT_TOPOLOGY_ID):
    topology, created = store.initialize(brazil_institutions(topology_id, repeaters=False))
    from ..quantum_protocols.topology import GLOBAL_EVENT, upgrade_global_workspace
    from ..quantum_protocols.satellite_bridges import upgrade_satellite_bridge_workspace
    from ..quantum_protocols.global_geo import upgrade_geo_constellation_workspace
    from .global_operations_topology import upgrade_global_operations_workspace
    with closing(store._connect()) as connection:
        migrated = connection.execute('SELECT 1 FROM topology_events WHERE topology_id=? AND event_kind=? LIMIT 1',
                                      (topology_id, GLOBAL_EVENT)).fetchone()
    if migrated:
        topology = upgrade_satellite_bridge_workspace(store,topology)
        topology = upgrade_geo_constellation_workspace(store,topology)
        return upgrade_global_operations_workspace(store,topology), created
    # The topology replacement and its event are one version-checked transaction.
    # The persistent event prevents reopening from restoring operator removals or
    # reconfiguring channels after the initial, explicitly scoped demo upgrade.
    with closing(store._connect()) as connection:
        upgraded = connection.execute('SELECT 1 FROM topology_events WHERE topology_id=? AND event_kind=? LIMIT 1',
                                      (topology_id, UPGRADE_EVENT)).fetchone()
    if not upgraded:
        expanded, payload = _with_repeaters(topology)
        expanded = replace(expanded, topology_version=topology.topology_version + 1,
                           updated_at_utc=datetime.now(timezone.utc).isoformat())
        topology = store._replace_existing(expanded, expected_version=topology.topology_version,
            event_kind=UPGRADE_EVENT, payload={**payload, 'before': topology.to_dict(),
                'span_ceiling_km': REPEATER_MAX_SPAN_KM, 'simulated': True, 'physical_action_performed': False})
    if created:
        demands = DemandStore(store.path, topology_id)
        for ident, a, b in PAIRS:
            demands.define(Demand(ident, a, b, DEFAULT_MIN_FIDELITY), topology)
    topology = upgrade_global_workspace(store, topology)
    topology = upgrade_satellite_bridge_workspace(store,topology)
    topology = upgrade_geo_constellation_workspace(store,topology)
    topology = upgrade_global_operations_workspace(store,topology)
    return topology, created
