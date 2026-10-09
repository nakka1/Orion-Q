"""Live-network inventory verbs: geodesy, strict contracts, atomic store, journal, reconciliation."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import math
from pathlib import Path
import sqlite3

import pytest

from quantum_autopilot.console.capabilities import OPERATIONS, SourceCapabilities, synthetic_capabilities
from quantum_autopilot.control_plane import geodesy
from quantum_autopilot.control_plane.command_reconciliation import reconcile_command
from quantum_autopilot.control_plane.console_commands import (
    CONTRACTS, ConsoleCommandJournal, read_commands, validated_request,
)
from quantum_autopilot.control_plane.inventory import (
    AddNetworkNode, AddSimulatedNode, ConfigureSimulatedLink, RecalibrateNode, SetLinkActive,
    SetNodeIsolation, SetNodeLocation, SimulatedNodeAction,
)
from quantum_autopilot.control_plane.topology import (
    DEFAULT_TOPOLOGY_ID, MAX_CALIBRATION_REPORT_BYTES, SimulatedTopology, SimulatedTopologyStore,
    _canonical_json, _sha256, default_simulated_topology,
)

ROOT = Path(__file__).resolve().parents[1]
FAROL = (-13.0104, -38.5325)        # Farol da Barra
AIRPORT = (-12.9086, -38.3225)      # Aeroporto de Salvador
PELOURINHO = (-12.9714, -38.5108)
NEW_VERBS = ('add_network_node', 'set_node_location', 'set_node_isolation', 'set_link_active', 'recalibrate_node')


# --------------------------------------------------------------------------- helpers

@pytest.fixture
def path(tmp_path):
    return tmp_path / 'network.sqlite'


@pytest.fixture
def store(path):
    return SimulatedTopologyStore(path)


def version(store):
    topology = store.get(DEFAULT_TOPOLOGY_ID)
    return 0 if topology is None else topology.topology_version


def add(store, node_id, where=None, place='', role='endpoint'):
    latitude, longitude = where or (None, None)
    return store.add_network_node(AddNetworkNode(node_id, role, '127.0.0.1', 7411, latitude, longitude,
                                                 place, version(store)))


def locate(store, node_id, where, place=''):
    latitude, longitude = where or (None, None)
    return store.set_node_location(SetNodeLocation(node_id, latitude, longitude, place, version(store)))


def fibre(store, link_id, left, right, km=10.0, active=True, mode='create', fidelity=.9):
    return store.configure_inventory_link(ConfigureSimulatedLink(mode, link_id, left, right, km, fidelity,
                                                                 active, version(store)))


def isolate(store, node_id, isolated=True):
    return store.set_node_isolation(SetNodeIsolation(node_id, isolated, version(store)))


def toggle(store, link_id, active):
    return store.set_link_active(SetLinkActive(link_id, active, version(store)))


def events(path):
    with sqlite3.connect(path) as connection:
        return [json.loads(row[0]) for row in
                connection.execute('SELECT event_json FROM topology_events ORDER BY sequence')]


def control(topology, node_id):
    return topology.node(node_id).telemetry.get('console_control', {})


def active(topology):
    return {link.link_id for link in topology.links if link.active}


def link_of(topology, link_id):
    return next(link for link in topology.links if link.link_id == link_id)


def triangle(store):
    """Three unlocated nodes a, b, c fully meshed (ab, bc, ac) beside the default line."""
    for node_id in 'abc':
        add(store, node_id)
    fibre(store, 'ab', 'a', 'b')
    fibre(store, 'bc', 'b', 'c')
    return fibre(store, 'ac', 'a', 'c')


def assert_refused(store, path, call, match):
    """A refused command changes neither the topology nor the audit trail."""
    before, audit = store.get(DEFAULT_TOPOLOGY_ID), events(path)
    with pytest.raises(ValueError, match=match):
        call()
    assert store.get(DEFAULT_TOPOLOGY_ID) == before and events(path) == audit


REPORT = {
    'calibration_id': 'cal-0001', 'completed_at_utc': '2026-10-04T12:00:00+00:00', 'duration_s': 4.0,
    'links': {'alice-r1': {'qber_before': .031, 'qber_after': .012, 'sync_residual_ps_before': 41.0,
                           'sync_residual_ps_after': 6.5, 'signal_intensity': .48, 'decoy_intensity': .1,
                           'key_rate_hz_after': 1250.0, 'status': 'calibrated'}},
    'memory_reset': True,
}


# --------------------------------------------------------------------------- geodesy

def _meridian_arc_km(lat1, lat2, steps=2000):
    """Independent reference: Simpson integration of the WGS-84 meridional radius."""
    a, f = 6378137.0, 1 / 298.257223563
    e2 = f * (2 - f)

    def radius(phi):
        return a * (1 - e2) / (1 - e2 * math.sin(phi) ** 2) ** 1.5

    lower, upper = math.radians(lat1), math.radians(lat2)
    h = (upper - lower) / steps
    total = radius(lower) + radius(upper)
    total += sum((4 if index % 2 else 2) * radius(lower + index * h) for index in range(1, steps))
    return abs(total * h / 3) / 1000


def _andoyer_lambert_km(lat1, lon1, lat2, lon2):
    """Independent reference: Lambert's long-line formula on the WGS-84 ellipsoid."""
    a, f = 6378137.0, 1 / 298.257223563
    b1 = math.atan((1 - f) * math.tan(math.radians(lat1)))
    b2 = math.atan((1 - f) * math.tan(math.radians(lat2)))
    dl = math.radians(lon2 - lon1)
    sigma = math.acos(math.sin(b1) * math.sin(b2) + math.cos(b1) * math.cos(b2) * math.cos(dl))
    p, q = (b1 + b2) / 2, (b2 - b1) / 2
    x = (sigma - math.sin(sigma)) * math.sin(p) ** 2 * math.cos(q) ** 2 / math.cos(sigma / 2) ** 2
    y = (sigma + math.sin(sigma)) * math.cos(p) ** 2 * math.sin(q) ** 2 / math.sin(sigma / 2) ** 2
    return a * (sigma - f / 2 * (x + y)) / 1000


def test_meridian_tenth_of_a_degree_at_salvador_latitude():
    distance = geodesy.geodesic_km(-13.05, -38.5, -12.95, -38.5)
    assert distance == pytest.approx(11.06, abs=.01)
    assert distance == pytest.approx(_meridian_arc_km(-13.05, -12.95), rel=1e-7)


def test_salvador_landmarks_agree_with_an_independent_formula():
    distance = geodesy.geodesic_km(*FAROL, *AIRPORT)
    reference = _andoyer_lambert_km(*FAROL, *AIRPORT)
    assert abs(distance - reference) / reference < 1e-3
    assert 25.0 < distance < 26.0
    # The spherical fallback is a different model and still lands within half a percent.
    assert abs(distance - geodesy.haversine_km(*FAROL, *AIRPORT)) / distance < 5e-3


def test_published_vincenty_example_symmetry_and_degenerate_cases():
    # Vincenty (1975) line: Flinders Peak -> Buninyong = 54 972.271 m.
    flinders, buninyong = (-37.95103341666667, 144.42486788888888), (-37.65282113888889, 143.92649552777777)
    assert geodesy.geodesic_km(*flinders, *buninyong) == pytest.approx(54.972271, abs=1e-6)
    assert geodesy.geodesic_km(*buninyong, *flinders) == pytest.approx(geodesy.geodesic_km(*flinders, *buninyong), abs=1e-9)
    assert geodesy.geodesic_km(*FAROL, *FAROL) == 0.0
    assert geodesy.geodesic_km(0, 0, 0, 1) == pytest.approx(111.319491, abs=1e-5)  # one equatorial degree
    assert geodesy.geodesic_km(10, 179.9, 10, -179.9) == pytest.approx(geodesy.geodesic_km(10, -.1, 10, .1), rel=1e-9)
    assert geodesy.geodesic_km(90, 0, -90, 0) == pytest.approx(20003.93, abs=.01)  # pole to pole


@pytest.mark.parametrize('points', [(0, 0, 0, 180), (0, 0, .5, 179.7), (0, 0, 0, 179.9)])
def test_antipodal_points_fall_back_to_a_finite_spherical_distance(points):
    assert geodesy._vincenty_km(*map(float, points)) is None
    distance = geodesy.geodesic_km(*points)
    assert distance == geodesy.haversine_km(*points)
    assert 19900 < distance < 20100


@pytest.mark.parametrize('latitude,longitude', [
    (90.01, 0), (-90.01, 0), (0, 180.01), (0, -180.01), (float('nan'), 0), (0, float('inf')),
    (True, 0), (0, False), ('-13.0', 0), (None, 0), (0, None)])
def test_coordinate_validation(latitude, longitude):
    with pytest.raises(ValueError):
        geodesy.validate_coordinates(latitude, longitude)
    with pytest.raises(ValueError):
        geodesy.geodesic_km(latitude, longitude, 0, 0)


def test_valid_coordinates_are_returned_as_floats():
    result = geodesy.validate_coordinates(-13, 180)
    assert result == (-13.0, 180.0) and all(type(value) is float for value in result)


def test_node_location_and_link_length_need_valid_locations(store):
    add(store, 'farol', FAROL, 'Farol da Barra')
    add(store, 'aero', AIRPORT)
    add(store, 'queue')
    fibre(store, 'located', 'farol', 'aero')
    topology = fibre(store, 'half', 'farol', 'queue', km=7.5)
    location = geodesy.node_location(topology.node('farol'))
    assert (location['latitude'], location['longitude'], location['place']) == (*FAROL, 'Farol da Barra')
    assert location['source'] == 'operator_declared'
    assert geodesy.node_location(topology.node('queue')) is None
    assert geodesy.node_location(topology.node('farol').to_dict()) == location  # dict form too
    expected = round(geodesy.geodesic_km(*FAROL, *AIRPORT), 6)
    for form in (topology, topology.to_dict()):
        links = form['links'] if isinstance(form, dict) else form.links
        assert geodesy.link_geodesic_km(form, links[-2]) == expected
        assert geodesy.link_geodesic_km(form, links[-1]) is None
    for bad in ({}, {'location': None}, {'location': 'Salvador'}, {'location': {'latitude': 1}},
                {'location': {'latitude': 91, 'longitude': 0}}, {'location': {'latitude': True, 'longitude': 0}},
                {'location': {'latitude': 0, 'longitude': 0, 'place': 'x' * 81}}):
        assert geodesy.node_location({'node_id': 'n', 'telemetry': bad}) is None
    assert geodesy.node_location({'node_id': 'n'}) is None


# --------------------------------------------------------------------------- contracts

VALID = {
    AddNetworkNode: dict(node_id='farol', role='endpoint', address='127.0.0.1', port=7411, latitude=FAROL[0],
                         longitude=FAROL[1], place='Farol da Barra', expected_version=0,
                         topology_id=DEFAULT_TOPOLOGY_ID),
    SetNodeLocation: dict(node_id='alice', latitude=FAROL[0], longitude=FAROL[1], place='Farol da Barra',
                          expected_version=0, topology_id=DEFAULT_TOPOLOGY_ID),
    SetNodeIsolation: dict(node_id='repeater-a', isolated=True, expected_version=0, topology_id=DEFAULT_TOPOLOGY_ID),
    SetLinkActive: dict(link_id='alice-r1', active=False, expected_version=0, topology_id=DEFAULT_TOPOLOGY_ID),
    RecalibrateNode: dict(node_id='repeater-a', expected_version=0, topology_id=DEFAULT_TOPOLOGY_ID),
}
COMMON_BAD = [dict(expected_version=True), dict(expected_version=-1), dict(expected_version=None),
              dict(expected_version=1.0), dict(topology_id='other')]
BAD_ID = ['', '<node>', 'x' * 65, '-lead', 'espaço', 7, None, 'a b']
BAD_LOCATION = [
    dict(latitude=None), dict(longitude=None), dict(latitude=90.5), dict(longitude=-180.5),
    dict(latitude=float('nan')), dict(longitude=float('inf')), dict(latitude='-13.0'), dict(latitude=True),
    dict(place='x' * 81), dict(place=None), dict(place='two\nlines'), dict(place=7),
    dict(latitude=None, longitude=None),  # a place label cannot be stored without coordinates
]
BAD = {
    AddNetworkNode: COMMON_BAD + BAD_LOCATION + [dict(node_id=value) for value in BAD_ID] + [
        dict(role='core'), dict(role=[]), dict(port=0), dict(port=65536), dict(port=True), dict(port='7411'),
        dict(address='example.com'), dict(address='http://localhost'), dict(address=None)],
    SetNodeLocation: COMMON_BAD + BAD_LOCATION + [dict(node_id=value) for value in BAD_ID],
    SetNodeIsolation: COMMON_BAD + [dict(node_id=value) for value in BAD_ID] + [
        dict(isolated=1), dict(isolated='true'), dict(isolated=None)],
    SetLinkActive: COMMON_BAD + [dict(link_id=value) for value in BAD_ID] + [
        dict(active=0), dict(active='false'), dict(active=None)],
    RecalibrateNode: COMMON_BAD + [dict(node_id=value) for value in BAD_ID],
}


@pytest.mark.parametrize('contract,overrides', [(contract, overrides) for contract, cases in BAD.items()
                                                for overrides in cases],
                         ids=lambda value: value.__name__ if isinstance(value, type) else None)
def test_bad_values_rejected(contract, overrides):
    with pytest.raises(ValueError):
        contract(**{**VALID[contract], **overrides})


@pytest.mark.parametrize('contract', list(VALID), ids=lambda contract: contract.__name__)
def test_exact_field_sets(contract):
    payload = VALID[contract]
    assert asdict(contract.from_dict(payload)) == payload
    for extra in ('auth', 'token', 'physical_authorization', 'kind', 'report', 'distance_km'):
        with pytest.raises(ValueError):
            contract.from_dict({**payload, extra: 'unexpected'})
    for missing in payload:  # topology_id and null coordinates must be spelled out too
        with pytest.raises(ValueError):
            contract.from_dict({key: value for key, value in payload.items() if key != missing})
    for not_a_mapping in (None, [], 'x', tuple(payload.items())):
        with pytest.raises(ValueError):
            contract.from_dict(not_a_mapping)


def test_location_is_both_or_neither_and_normalised_to_float():
    unlocated = AddNetworkNode('queue', 'repeater', '::1', 1, None, None, '', 3)
    assert not unlocated.located and unlocated.location('2026-10-04T00:00:00+00:00') is None
    assert 'location' not in unlocated.node().telemetry
    located = AddNetworkNode('edge', 'endpoint', '10.0.0.1', 65535, -13, 180, '', 0)
    assert (located.latitude, located.longitude) == (-13.0, 180.0)
    assert type(located.latitude) is float and type(located.longitude) is float
    with pytest.raises(ValueError, match='timestamp'):
        located.node()
    node = located.node('2026-10-04T00:00:00+00:00')
    assert node.telemetry['location'] == {'latitude': -13.0, 'longitude': 180.0, 'place': '',
                                          'source': 'operator_declared', 'set_at_utc': '2026-10-04T00:00:00+00:00'}
    assert node.memory_state == 'IDLE' and node.state_version == 0
    assert node.telemetry['inventory'] == AddSimulatedNode('edge', 'endpoint', 0, '10.0.0.1', 65535).node().telemetry['inventory']
    assert SetNodeLocation('alice', None, None, '', 0).location('2026-10-04T00:00:00+00:00') is None
    assert SetNodeLocation('alice', 0, 0, 'Itapuã · orla', 0).place == 'Itapuã · orla'  # labels are not ASCII-only


def test_new_contracts_are_tracked_and_old_ones_unchanged():
    assert set(CONTRACTS) >= {'add_simulated_node', 'configure_simulated_link', 'apply_simulated_node_action',
                              *NEW_VERBS}
    assert [CONTRACTS[verb] for verb in NEW_VERBS] == list(VALID)
    assert (CONTRACTS['add_simulated_node'], CONTRACTS['configure_simulated_link'],
            CONTRACTS['apply_simulated_node_action']) == (AddSimulatedNode, ConfigureSimulatedLink, SimulatedNodeAction)


# --------------------------------------------------------------------------- store: add / locate

def test_add_network_node_with_and_without_location(store, path):
    first = add(store, 'farol', FAROL, 'Farol da Barra', role='repeater')
    second = add(store, 'queue')
    assert (first.topology_version, second.topology_version) == (1, 2)
    assert SimulatedTopologyStore(path).get(DEFAULT_TOPOLOGY_ID) == second
    assert [node.node_id for node in second.nodes] == ['alice', 'repeater-a', 'bob', 'farol', 'queue']
    assert second.links == default_simulated_topology().links  # no fabricated fibre
    located = second.node('farol')
    assert located.role.value == 'repeater' and located.memory_state == 'IDLE'
    assert located.telemetry['location']['place'] == 'Farol da Barra'
    assert located.telemetry['location']['source'] == 'operator_declared'
    assert located.telemetry['location']['set_at_utc'] == first.updated_at_utc == located.updated_at_utc
    assert located.telemetry['inventory']['scope'] == 'metadata_only_no_connection'
    assert 'location' not in second.node('queue').telemetry
    log = events(path)
    assert [event['event_kind'] for event in log] == ['network_node_added'] * 2
    assert (log[0]['version_before'], log[0]['version_after']) == (0, 1)
    payload = log[0]['payload']
    assert payload['physical_action_performed'] is False and payload['scope'] == 'simulator_only'
    assert payload['before'] is None and payload['after'] == located.to_dict() and payload['located'] is True
    assert log[1]['payload']['located'] is False
    assert_refused(store, path, lambda: add(store, 'farol'), 'duplicate')
    assert_refused(store, path, lambda: store.add_network_node(
        AddNetworkNode('late', 'endpoint', '127.0.0.1', 1, None, None, '', 1)), 'version conflict')
    with pytest.raises(ValueError, match='AddNetworkNode'):
        store.add_network_node(AddSimulatedNode('n', 'endpoint', 2, '127.0.0.1', 1))


def test_node_limit_is_shared_with_the_legacy_verb(store, path):
    for index in range(29):
        add(store, f'n{index}')
    # The historical isolated editor retains its boundary, while live network
    # inventory supports the expanded 66-node demonstration and up to 256 nodes.
    assert_refused(store, path, lambda: store.add_inventory_node(
        AddSimulatedNode('legacy-overflow', 'endpoint', version(store), '127.0.0.1', 7411)), 'at most 32')
    for index in range(29, 253):
        add(store, f'n{index}')
    assert len(store.get(DEFAULT_TOPOLOGY_ID).nodes) == 256
    assert_refused(store, path, lambda: add(store, 'overflow'), 'at most 256')


def test_location_set_later_preserves_node_data_and_connections(store, path):
    add(store, 'aero', AIRPORT, 'Aeroporto')
    fibre(store, 'to-aero', 'repeater-a', 'aero', km=12.0, fidelity=.95)
    before = store.get(DEFAULT_TOPOLOGY_ID)
    repeater = before.node('repeater-a')
    assert repeater.memory_state == 'STORING' and len(repeater.available_pair_fidelities) == 2

    after = locate(store, 'repeater-a', FAROL, 'Farol da Barra')
    moved = after.node('repeater-a')
    assert moved.telemetry['location']['latitude'] == FAROL[0]
    assert {key: value for key, value in moved.telemetry.items() if key != 'location'} == repeater.telemetry
    assert (moved.role, moved.memory_state, moved.available_pair_fidelities, moved.memory_ages_s) == (
        repeater.role, 'STORING', repeater.available_pair_fidelities, repeater.memory_ages_s)
    assert moved.state_version == repeater.state_version + 1
    assert [node for node in after.nodes if node.node_id != 'repeater-a'] == [
        node for node in before.nodes if node.node_id != 'repeater-a']
    # Same fibres, same endpoints, same state; only the fully located one gets the geodesic length.
    geodesic = round(geodesy.geodesic_km(*FAROL, *AIRPORT), 6)
    def wiring(topology):
        return [(link.link_id, link.left_node_id, link.right_node_id, link.active, link.nominal_pair_fidelity)
                for link in topology.links]

    assert wiring(after) == wiring(before)
    assert link_of(after, 'to-aero').distance_km == geodesic
    assert link_of(after, 'alice-r1').distance_km == 25.0 and link_of(after, 'r1-bob').distance_km == 25.0
    event = events(path)[-1]
    assert event['event_kind'] == 'node_location_set'
    payload = event['payload']
    assert payload['before'] is None and payload['after'] == moved.telemetry['location']
    assert payload['recomputed_links'] == [
        {'link_id': 'to-aero', 'distance_km_before': 12.0, 'distance_km_after': geodesic}]
    assert payload['physical_action_performed'] is False and payload['scope'] == 'simulator_only'
    assert SimulatedTopologyStore(path).get(DEFAULT_TOPOLOGY_ID) == after


def test_moving_relabelling_and_clearing_a_location(store, path):
    add(store, 'farol', FAROL, 'Farol')
    add(store, 'aero', AIRPORT)
    add(store, 'queue')
    fibre(store, 'metro', 'farol', 'aero', km=99.0)
    fibre(store, 'spur', 'farol', 'queue', km=3.5)
    geodesic = round(geodesy.geodesic_km(*FAROL, *AIRPORT), 6)
    assert link_of(store.get(DEFAULT_TOPOLOGY_ID), 'metro').distance_km == geodesic  # set at creation

    assert_refused(store, path, lambda: locate(store, 'farol', FAROL, 'Farol'), 'unchanged')
    relabelled = locate(store, 'farol', FAROL, 'Farol da Barra')  # a new label alone is a real change
    assert relabelled.node('farol').telemetry['location']['place'] == 'Farol da Barra'
    assert link_of(relabelled, 'metro').distance_km == geodesic

    moved = locate(store, 'farol', PELOURINHO, 'Pelourinho')
    shorter = round(geodesy.geodesic_km(*PELOURINHO, *AIRPORT), 6)
    assert link_of(moved, 'metro').distance_km == shorter != geodesic
    assert link_of(moved, 'spur').distance_km == 3.5  # the other end is still in the queue
    payload = events(path)[-1]['payload']
    assert payload['before']['place'] == 'Farol da Barra' and payload['after']['place'] == 'Pelourinho'
    assert payload['recomputed_links'] == [
        {'link_id': 'metro', 'distance_km_before': geodesic, 'distance_km_after': shorter}]

    queued = locate(store, 'farol', None)
    assert 'location' not in queued.node('farol').telemetry
    assert queued.node('farol').telemetry['inventory'] == moved.node('farol').telemetry['inventory']
    assert queued.links == moved.links  # nothing recomputed; the fibres keep their last length
    assert events(path)[-1]['payload'] == {
        'node_id': 'farol', 'before': moved.node('farol').telemetry['location'], 'after': None,
        'recomputed_links': [], 'scope': 'simulator_only', 'physical_action_performed': False}
    assert_refused(store, path, lambda: locate(store, 'farol', None), 'no location to clear')
    assert_refused(store, path, lambda: locate(store, 'absent', FAROL), 'outside topology')
    assert_refused(store, path, lambda: store.set_node_location(
        SetNodeLocation('farol', *FAROL, '', version(store) - 1)), 'version conflict')

    # With an unlocated endpoint the operator's figure rules again.
    edited = fibre(store, 'metro', 'farol', 'aero', km=42.0, mode='update')
    assert link_of(edited, 'metro').distance_km == 42.0
    assert events(path)[-1]['payload']['distance_source'] == 'operator_configured'


def test_link_editor_between_located_nodes_stores_the_geodesic(store, path):
    add(store, 'farol', FAROL)
    add(store, 'aero', AIRPORT)
    geodesic = round(geodesy.geodesic_km(*FAROL, *AIRPORT), 6)
    created = fibre(store, 'metro', 'farol', 'aero', km=1.0)
    assert link_of(created, 'metro').distance_km == geodesic
    payload = events(path)[-1]['payload']
    assert payload['requested_distance_km'] == 1.0 and payload['after']['distance_km'] == geodesic
    assert payload['distance_source'] == 'geodesic_from_node_locations'
    # A new figure for a located fibre is not a change; a real change still goes through.
    assert_refused(store, path, lambda: fibre(store, 'metro', 'farol', 'aero', km=500.0, mode='update'), 'unchanged')
    updated = fibre(store, 'metro', 'farol', 'aero', km=500.0, mode='update', fidelity=.8)
    assert (link_of(updated, 'metro').distance_km, link_of(updated, 'metro').nominal_pair_fidelity) == (geodesic, .8)


# --------------------------------------------------------------------------- store: isolation / links

def test_isolate_then_restore_brings_back_exactly_what_was_switched_off(store, path):
    before = triangle(store)
    isolated = isolate(store, 'repeater-a')
    node = isolated.node('repeater-a')
    assert control(isolated, 'repeater-a')['isolated'] is True
    assert control(isolated, 'repeater-a')['isolation_disabled_links'] == ['alice-r1', 'r1-bob']
    assert control(isolated, 'repeater-a')['physical_action_performed'] is False
    assert node.memory_state == 'IDLE' and node.available_pair_fidelities == () and node.memory_ages_s == ()
    assert active(isolated) == {'ab', 'bc', 'ac'}
    assert isolated.topology_version == before.topology_version + 1
    event = events(path)[-1]
    assert event['event_kind'] == 'node_isolated'
    assert event['payload']['disabled_links'] == ['alice-r1', 'r1-bob'] and event['payload']['isolated'] is True
    assert event['payload']['before'] == before.node('repeater-a').to_dict()
    assert event['payload']['after'] == node.to_dict()
    assert event['payload']['physical_action_performed'] is False and event['payload']['scope'] == 'simulator_only'
    assert SimulatedTopologyStore(path).get(DEFAULT_TOPOLOGY_ID) == isolated
    assert_refused(store, path, lambda: isolate(store, 'repeater-a'), 'already isolated')

    restored = isolate(store, 'repeater-a', False)
    assert restored.links == before.links
    assert control(restored, 'repeater-a')['isolated'] is False
    assert control(restored, 'repeater-a')['isolation_disabled_links'] == []
    assert restored.node('repeater-a').memory_state == 'IDLE'  # discarded pairs do not come back
    event = events(path)[-1]
    assert event['event_kind'] == 'node_restored'
    assert event['payload']['restored_links'] == ['alice-r1', 'r1-bob']
    assert event['payload']['deferred_links'] == [] and event['payload']['skipped_links'] == []
    assert event['payload']['before'] == node.to_dict()
    assert event['payload']['physical_action_performed'] is False
    assert_refused(store, path, lambda: isolate(store, 'repeater-a', False), 'not isolated')
    assert_refused(store, path, lambda: isolate(store, 'absent'), 'outside topology')
    assert_refused(store, path, lambda: store.set_node_isolation(SetNodeIsolation('a', True, 0)), 'version conflict')
    with pytest.raises(ValueError, match='SetNodeIsolation'):
        store.set_node_isolation(SimulatedNodeAction('isolate', 'a', version(store)))


def test_a_node_without_fibres_can_be_isolated_and_restored(store, path):
    add(store, 'lonely')
    isolated = isolate(store, 'lonely')
    assert control(isolated, 'lonely')['isolated'] is True
    assert events(path)[-1]['payload']['disabled_links'] == []
    assert active(isolate(store, 'lonely', False)) == {'alice-r1', 'r1-bob'}
    assert events(path)[-1]['payload']['restored_links'] == []


def test_restore_keeps_an_individually_disabled_link_disabled(store, path):
    triangle(store)
    toggle(store, 'ac', False)          # the operator's own decision, before the isolation
    isolated = isolate(store, 'a')
    assert control(isolated, 'a')['isolation_disabled_links'] == ['ab']
    assert events(path)[-1]['payload']['disabled_links'] == ['ab']
    restored = isolate(store, 'a', False)
    assert link_of(restored, 'ab').active and not link_of(restored, 'ac').active
    assert events(path)[-1]['payload']['restored_links'] == ['ab']


@pytest.mark.parametrize('order', ['ab', 'ba'])
def test_two_isolated_neighbours_restore_their_shared_link_exactly_once(store, path, order):
    before = triangle(store)
    isolate(store, 'a')                 # switches off ab and ac
    both = isolate(store, 'b')          # ab is already off; only bc is new
    assert control(both, 'a')['isolation_disabled_links'] == ['ab', 'ac']
    assert control(both, 'b')['isolation_disabled_links'] == ['bc']
    assert active(both) == {'alice-r1', 'r1-bob'}

    first, second = order
    half = isolate(store, first, False)
    assert not link_of(half, 'ab').active   # the neighbour is still isolated
    assert control(half, first)['isolated'] is False and control(half, first)['isolation_disabled_links'] == []
    assert control(half, second)['isolated'] is True
    payload = events(path)[-1]['payload']
    if first == 'a':
        assert payload['restored_links'] == ['ac']
        assert payload['deferred_links'] == [{'link_id': 'ab', 'held_by': 'b'}]
        assert control(half, 'b')['isolation_disabled_links'] == ['bc', 'ab']  # handed over
        assert half.node('b').state_version == both.node('b').state_version + 1
    else:
        assert payload['restored_links'] == ['bc'] and payload['deferred_links'] == []
        assert control(half, 'a')['isolation_disabled_links'] == ['ab', 'ac']
        assert half.node('a') == both.node('a')

    full = isolate(store, second, False)
    assert full.links == before.links
    assert all(control(full, node_id)['isolation_disabled_links'] == [] for node_id in 'ab')
    restores = [event['payload']['restored_links'] for event in events(path) if event['event_kind'] == 'node_restored']
    assert sum(links.count('ab') for links in restores) == 1
    assert sorted(link_id for links in restores for link_id in links) == ['ab', 'ac', 'bc']


def test_handed_over_link_stays_off_when_it_was_never_isolated_by_either(store):
    triangle(store)
    toggle(store, 'ab', False)          # operator decision
    isolate(store, 'a')
    isolate(store, 'b')
    isolate(store, 'a', False)
    final = isolate(store, 'b', False)
    assert active(final) == {'alice-r1', 'r1-bob', 'bc', 'ac'} and not link_of(final, 'ab').active


def test_link_toggle_semantics_and_activation_refused_while_isolated(store, path):
    before = triangle(store)
    off = toggle(store, 'ab', False)
    assert not link_of(off, 'ab').active and off.nodes == before.nodes  # nodes untouched
    assert off.topology_version == before.topology_version + 1
    event = events(path)[-1]
    assert event['event_kind'] == 'link_deactivated'
    assert event['payload'] == {'link_id': 'ab', 'active': False, 'before': link_of(before, 'ab').to_dict(),
                                'after': link_of(off, 'ab').to_dict(), 'scope': 'simulator_only',
                                'physical_action_performed': False}
    assert_refused(store, path, lambda: toggle(store, 'ab', False), 'already inactive')
    on = toggle(store, 'ab', True)
    assert on.links == before.links and events(path)[-1]['event_kind'] == 'link_activated'
    assert_refused(store, path, lambda: toggle(store, 'ab', True), 'already active')
    assert_refused(store, path, lambda: toggle(store, 'absent', True), 'outside topology')
    assert_refused(store, path, lambda: store.set_link_active(SetLinkActive('ab', False, 0)), 'version conflict')

    isolated = isolate(store, 'a')
    for link_id in ('ab', 'ac'):        # either endpoint isolated blocks activation
        assert_refused(store, path, lambda link_id=link_id: toggle(store, link_id, True), 'restore the node first')
    # Deactivating another fibre leaves isolation flags and bookkeeping alone.
    other = toggle(store, 'bc', False)
    assert other.node('a') == isolated.node('a') and other.node('b') == isolated.node('b')
    assert 'console_control' not in other.node('b').telemetry
    restored = isolate(store, 'a', False)
    assert active(restored) == {'alice-r1', 'r1-bob', 'ab', 'ac'}  # bc was the operator's, not the isolation's
    assert toggle(store, 'bc', True).links == before.links
    with pytest.raises(ValueError, match='SetLinkActive'):
        store.set_link_active(SetNodeIsolation('a', True, version(store)))


def test_legacy_isolate_is_undone_by_the_new_restore(store, path):
    before = triangle(store)
    legacy = store.apply_inventory_node_action(SimulatedNodeAction('isolate', 'a', version(store)))
    assert control(legacy, 'a')['isolation_disabled_links'] == ['ab', 'ac']
    assert events(path)[-1]['event_kind'] == 'simulated_node_isolated'
    again = store.apply_inventory_node_action(SimulatedNodeAction('recalibrate', 'a', version(store)))
    assert control(again, 'a')['isolation_disabled_links'] == ['ab', 'ac'] and control(again, 'a')['isolated'] is True
    restored = isolate(store, 'a', False)
    assert restored.links == before.links and control(restored, 'a')['calibration_count'] == 1
    assert_refused(store, path, lambda: store.apply_inventory_node_action(
        SimulatedNodeAction('isolate', 'absent', version(store))), 'outside topology')


def test_legacy_reconnection_ends_isolation_without_restoring_other_links(store, path):
    triangle(store)
    isolate(store, 'a')
    reconnected = fibre(store, 'ab', 'a', 'b', mode='update', active=True)
    assert control(reconnected, 'a')['isolated'] is False
    assert control(reconnected, 'a')['isolation_disabled_links'] == []
    assert link_of(reconnected, 'ab').active and not link_of(reconnected, 'ac').active
    assert_refused(store, path, lambda: isolate(store, 'a', False), 'not isolated')
    assert link_of(toggle(store, 'ac', True), 'ac').active  # the operator re-enables the rest one by one
    # A second isolation starts a fresh record.
    assert control(isolate(store, 'a'), 'a')['isolation_disabled_links'] == ['ab', 'ac']


def test_restore_skips_recorded_links_that_are_gone_or_foreign(store, path):
    triangle(store)
    isolated = isolate(store, 'a')
    payload = isolated.to_dict()
    node = next(item for item in payload['nodes'] if item['node_id'] == 'a')
    node['telemetry']['console_control']['isolation_disabled_links'] = ['ab', 'vanished', 'bc', 'ab', 7]
    with sqlite3.connect(path) as connection:
        connection.execute('UPDATE simulated_topologies SET state_json=?', (_canonical_json(payload),))
    restored = isolate(store, 'a', False)
    assert active(restored) == {'alice-r1', 'r1-bob', 'ab', 'bc'}  # ac was dropped from the record
    assert events(path)[-1]['payload']['restored_links'] == ['ab']
    assert events(path)[-1]['payload']['skipped_links'] == ['vanished', 'bc']


# --------------------------------------------------------------------------- store: calibration

def test_calibration_report_is_stored_hashed_and_counted(store, path):
    isolate(store, 'repeater-a')
    before = store.get(DEFAULT_TOPOLOGY_ID)
    request = RecalibrateNode('repeater-a', version(store))
    report = {**REPORT, 'steps': ('polarisation', 'sync', 'decoy')}  # tuples become JSON arrays
    updated = store.record_node_calibration(request, report)
    saved = control(updated, 'repeater-a')
    body = {**REPORT, 'steps': ['polarisation', 'sync', 'decoy']}
    assert saved['calibration'] == {**body, 'report_sha256': _sha256(body)}
    assert saved['calibration_count'] == 1 and saved['calibrated_at_utc'] == updated.updated_at_utc
    assert saved['last_action'] == 'recalibrate' and saved['last_command_sha256'] == _sha256(asdict(request))
    assert saved['isolated'] is True and saved['isolation_disabled_links'] == ['alice-r1', 'r1-bob']
    assert saved['physical_action_performed'] is False
    assert updated.links == before.links  # recalibration reconnects nothing
    assert updated.node('repeater-a').memory_state == 'IDLE'
    assert updated.topology_version == before.topology_version + 1
    assert SimulatedTopologyStore(path).get(DEFAULT_TOPOLOGY_ID) == updated
    event = events(path)[-1]
    assert event['event_kind'] == 'node_recalibrated'
    assert event['payload']['report_sha256'] == saved['calibration']['report_sha256']
    assert event['payload']['previous_report_sha256'] is None and event['payload']['calibration_count'] == 1
    assert event['payload']['after'] == updated.node('repeater-a').to_dict()
    assert event['payload']['physical_action_performed'] is False and event['payload']['scope'] == 'simulator_only'

    second = store.record_node_calibration(RecalibrateNode('repeater-a', version(store)),
                                           saved['calibration'])  # a matching hash from the engine is accepted
    assert control(second, 'repeater-a')['calibration_count'] == 2
    assert events(path)[-1]['payload']['previous_report_sha256'] == saved['calibration']['report_sha256']


def test_calibration_resets_stored_pairs(store):
    assert default_simulated_topology().node('repeater-a').memory_state == 'STORING'
    updated = store.record_node_calibration(RecalibrateNode('repeater-a', 0), REPORT)
    node = updated.node('repeater-a')
    assert (node.memory_state, node.available_pair_fidelities, node.memory_ages_s) == ('IDLE', (), ())
    assert node.telemetry['source'] == 'default_simulated_topology'  # other telemetry preserved


@pytest.mark.parametrize('report,match', [
    (None, 'non-empty JSON object'), ([], 'non-empty JSON object'), ({}, 'non-empty JSON object'),
    ({'report_sha256': 'a' * 64}, 'non-empty JSON object'),
    ({'duration_s': float('nan')}, 'finite'), ({'duration_s': float('inf')}, 'finite'),
    ({1: 'numeric key'}, 'keys must be strings'), ({'links': {('a', 'b'): 1}}, 'keys must be strings'),
    ({'when': object()}, 'non-JSON value'), ({'raw': b'bytes'}, 'non-JSON value'), ({'ids': {1, 2}}, 'non-JSON value'),
    ({**REPORT, 'report_sha256': '0' * 64}, 'does not match'),
    ({'blob': 'x' * MAX_CALIBRATION_REPORT_BYTES}, 'exceeds'),
    ({'deep': [[[[[[[[[[1]]]]]]]]]]}, 'nested too deeply'),
])
def test_invalid_calibration_reports_are_refused(store, path, report, match):
    store.initialize(default_simulated_topology())
    assert_refused(store, path, lambda: store.record_node_calibration(RecalibrateNode('repeater-a', 0), report), match)


def test_calibration_size_bound_is_on_canonical_json(store, path):
    overhead = len(_canonical_json({'blob': ''}).encode())
    exact = {'blob': 'x' * (MAX_CALIBRATION_REPORT_BYTES - overhead)}
    assert len(_canonical_json(exact).encode()) == MAX_CALIBRATION_REPORT_BYTES == 16384
    assert control(store.record_node_calibration(RecalibrateNode('alice', 0), exact), 'alice')['calibration_count'] == 1
    multibyte = {'blob': 'ã' * (MAX_CALIBRATION_REPORT_BYTES // 2)}  # characters fit, bytes do not
    assert_refused(store, path, lambda: store.record_node_calibration(RecalibrateNode('alice', 1), multibyte), 'exceeds')
    assert_refused(store, path, lambda: store.record_node_calibration(RecalibrateNode('absent', 1), REPORT), 'outside topology')
    assert_refused(store, path, lambda: store.record_node_calibration(RecalibrateNode('alice', 0), REPORT), 'version conflict')
    with pytest.raises(ValueError, match='RecalibrateNode'):
        store.record_node_calibration(SimulatedNodeAction('recalibrate', 'alice', 1), REPORT)


# --------------------------------------------------------------------------- store: atomicity

MUTATIONS = {
    'add_network_node': lambda store: add(store, 'extra', FAROL),
    'set_node_location': lambda store: locate(store, 'alice', FAROL),
    'set_node_isolation': lambda store: isolate(store, 'repeater-a'),
    'set_link_active': lambda store: toggle(store, 'alice-r1', False),
    'recalibrate_node': lambda store: store.record_node_calibration(RecalibrateNode('repeater-a', version(store)), REPORT),
}


@pytest.mark.parametrize('verb', NEW_VERBS)
def test_event_failure_rolls_back_the_whole_mutation(path, verb):
    store = SimulatedTopologyStore(path)
    before = add(store, 'seed')
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TRIGGER fail_event BEFORE INSERT ON topology_events "
                           "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='injected failure'):
        MUTATIONS[verb](store)
    assert SimulatedTopologyStore(path).get(DEFAULT_TOPOLOGY_ID) == before


@pytest.mark.parametrize('verb', NEW_VERBS)
def test_first_mutation_persists_the_default_blueprint_with_one_event(path, verb):
    store = SimulatedTopologyStore(path)
    assert store.get(DEFAULT_TOPOLOGY_ID) is None
    updated = MUTATIONS[verb](store)
    assert updated.topology_version == 1 and store.get(DEFAULT_TOPOLOGY_ID) == updated
    log = events(path)
    assert len(log) == 1 and (log[0]['version_before'], log[0]['version_after']) == (0, 1)
    assert log[0]['payload']['physical_action_performed'] is False and log[0]['payload']['scope'] == 'simulator_only'
    listed = store.list_events(DEFAULT_TOPOLOGY_ID)
    assert listed[0]['event_sha256'] and listed[0]['event_kind'] == log[0]['event_kind']


def test_competing_writers_cannot_both_win(path):
    stores = [SimulatedTopologyStore(path) for _ in range(2)]

    def run(index):
        try:
            stores[index].set_node_isolation(SetNodeIsolation('repeater-a', True, 0))
            return 'confirmed'
        except ValueError as error:
            assert 'version conflict' in str(error)
            return 'stale'

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, range(2))) == ['confirmed', 'stale']
    assert stores[0].get(DEFAULT_TOPOLOGY_ID).topology_version == 1 and len(events(path)) == 1


# --------------------------------------------------------------------------- journal + reconciliation

def test_command_journal_lifecycle_for_a_new_verb(store, path):
    journal = ConsoleCommandJournal(path)
    request = validated_request('set_node_location', dict(VALID[SetNodeLocation], latitude=-13, longitude=-38))
    assert (request['latitude'], request['longitude']) == (-13.0, -38.0)
    command_id = journal.begin('set_node_location', request)
    journal.transition(command_id, 'RUNNING')
    persisted = store.set_node_location(SetNodeLocation.from_dict(request))
    for detail in ({'topology_version': 2, 'topology_sha256': _sha256(persisted.to_dict())},
                   {'topology_version': 1, 'topology_sha256': 'bad'}):
        with pytest.raises(ValueError):
            journal.transition(command_id, 'CONFIRMED', detail)
    record = journal.transition(command_id, 'CONFIRMED', {
        'topology_version': persisted.topology_version, 'topology_sha256': _sha256(persisted.to_dict())})
    assert [event['state'] for event in record['events']] == ['REQUESTED', 'RUNNING', 'CONFIRMED']
    stored = read_commands(path)[0]  # re-validated against the current contracts on every read
    assert stored['record'] == record and stored['record']['request'] == request
    assert stored['record']['physical_action_performed'] is False
    with pytest.raises(ValueError):
        journal.transition(command_id, 'UNKNOWN', {'reason': 'execution_or_readback_failed', 'error_type': 'OSError'})


@pytest.mark.parametrize('verb', NEW_VERBS)
def test_every_new_verb_is_journalled_strictly(store, path, verb):
    journal = ConsoleCommandJournal(path)
    payload = VALID[CONTRACTS[verb]]
    for bad in ({**payload, 'token': 'secret'}, {key: value for key, value in payload.items() if key != 'topology_id'},
                {**payload, 'expected_version': '0'}):
        with pytest.raises(ValueError):
            journal.begin(verb, bad)
    assert read_commands(path) == []
    command_id = journal.begin(verb, payload)
    journal.transition(command_id, 'REJECTED', {'reason': 'capability_revoked_before_dispatch'})
    assert read_commands(path)[0]['record']['verb'] == verb


RECONCILE = {
    # verb: (request, apply the same effect, state in which the target exists but differs)
    'add_network_node': (AddNetworkNode('farol', 'endpoint', '127.0.0.1', 7411, *FAROL, 'Farol', 0),
                         lambda store: add(store, 'farol', FAROL, 'Farol'),
                         lambda store: add(store, 'farol', AIRPORT, 'Farol')),
    'set_node_location': (SetNodeLocation('alice', *FAROL, 'Farol', 0),
                          lambda store: locate(store, 'alice', FAROL, 'Farol'),
                          lambda store: locate(store, 'alice', FAROL, 'Outro rótulo')),
    'set_node_isolation': (SetNodeIsolation('repeater-a', True, 0),
                           lambda store: isolate(store, 'repeater-a'),
                           lambda store: add(store, 'unrelated')),
    'set_link_active': (SetLinkActive('alice-r1', False, 0),
                        lambda store: toggle(store, 'alice-r1', False),
                        lambda store: add(store, 'unrelated')),
    'recalibrate_node': (RecalibrateNode('repeater-a', 0),
                         lambda store: store.record_node_calibration(RecalibrateNode('repeater-a', 0), REPORT),
                         lambda store: store.apply_inventory_node_action(
                             SimulatedNodeAction('recalibrate', 'repeater-a', 0))),  # another command's hash
}


@pytest.mark.parametrize('verb', NEW_VERBS)
@pytest.mark.parametrize('effect', ['absent', 'matching', 'different', 'later'])
def test_new_verbs_reconcile_as_observations_only(store, path, verb, effect):
    request, apply, differ = RECONCILE[verb]
    journal = ConsoleCommandJournal(path)
    command_id = journal.begin(verb, asdict(request))
    journal.transition(command_id, 'RUNNING')
    record = journal.transition(command_id, 'UNKNOWN', {'reason': 'execution_or_readback_failed', 'error_type': 'OSError'})
    if effect in {'matching', 'later'}:
        apply(store)
        if effect == 'later':
            add(store, 'afterwards')
    elif effect == 'different':
        differ(store)
    before = path.read_bytes()
    report = reconcile_command(path, command_id, _sha256(record))
    assert path.read_bytes() == before
    comparison = report['record']['comparison']
    assert report['record_sha256'] == _sha256(report['record'])
    assert comparison['target_status'] == {'absent': 'topology_absent', 'matching': 'matches_current_state',
                                           'later': 'matches_current_state', 'different': 'differs_current_state'}[effect]
    assert comparison['version_relation'] == {'absent': 'unavailable', 'matching': 'at_expected_plus_one',
                                              'later': 'later', 'different': 'at_expected_plus_one'}[effect]
    assert comparison['command_outcome'] == 'UNRESOLVED' and comparison['command_effect_proven'] is False
    assert report['record']['original_state'] == 'UNKNOWN'
    assert not any(report['record'][key] for key in ('physical_action_performed', 'policy_activated', 'journal_mutated'))
    assert report['record']['commands_emitted'] == 0
    if effect != 'absent':
        assert comparison['observed_fields'] is not None
        assert (comparison['observed_fields'] == comparison['expected_fields']) == (effect != 'different')


def test_reconciliation_reports_missing_targets_and_expected_fields(store, path):
    journal = ConsoleCommandJournal(path)
    store.initialize(default_simulated_topology())
    expected = {}
    for verb, request in (('add_network_node', AddNetworkNode('queue', 'repeater', '127.0.0.1', 1, None, None, '', 0)),
                          ('set_node_location', SetNodeLocation('ghost', None, None, '', 0)),
                          ('set_node_isolation', SetNodeIsolation('ghost', False, 0)),
                          ('set_link_active', SetLinkActive('ghost', True, 0)),
                          ('recalibrate_node', RecalibrateNode('ghost', 0))):
        command_id = journal.begin(verb, asdict(request))
        comparison = reconcile_command(path, command_id, read_commands(path)[0]['record_sha256'])['record']['comparison']
        assert comparison['target_status'] == 'target_absent' and comparison['observed_fields'] is None
        assert comparison['version_relation'] == 'at_expected'
        expected[verb] = comparison['expected_fields']
    assert expected['add_network_node']['location'] is None
    assert expected['add_network_node']['inventory']['address'] == '127.0.0.1'
    assert expected['set_node_location'] == {'node_id': 'ghost', 'location': None}
    assert expected['set_node_isolation'] == {'node_id': 'ghost', 'isolated': False}
    assert expected['set_link_active'] == {'link_id': 'ghost', 'active': True}
    assert expected['recalibrate_node'] == {'node_id': 'ghost', 'last_command_sha256': _sha256(asdict(RecalibrateNode('ghost', 0)))}


def test_location_reconciliation_ignores_the_timestamp_but_not_the_declaration(store, path):
    journal = ConsoleCommandJournal(path)
    request = SetNodeLocation('alice', *FAROL, 'Farol', 0)
    command_id = journal.begin('set_node_location', asdict(request))
    digest = read_commands(path)[0]['record_sha256']
    store.set_node_location(request)
    comparison = reconcile_command(path, command_id, digest)['record']['comparison']
    assert comparison['target_status'] == 'matches_current_state'
    assert comparison['expected_fields'] == {'node_id': 'alice', 'location': {
        'latitude': FAROL[0], 'longitude': FAROL[1], 'place': 'Farol'}}
    locate(store, 'alice', None)
    comparison = reconcile_command(path, command_id, digest)['record']['comparison']
    assert comparison['target_status'] == 'differs_current_state'
    assert comparison['observed_fields'] == {'node_id': 'alice', 'location': None}


def test_link_reconciliation_expects_the_geodesic_between_located_nodes(store, path):
    add(store, 'farol', FAROL)
    add(store, 'aero', AIRPORT)
    add(store, 'queue')
    journal = ConsoleCommandJournal(path)
    for link_id, right in (('metro', 'aero'), ('spur', 'queue')):
        request = ConfigureSimulatedLink('create', link_id, 'farol', right, 1.0, .9, True, version(store))
        command_id = journal.begin('configure_simulated_link', asdict(request))
        digest = read_commands(path)[0]['record_sha256']
        store.configure_inventory_link(request)
        comparison = reconcile_command(path, command_id, digest)['record']['comparison']
        assert comparison['target_status'] == 'matches_current_state'
        assert comparison['expected_fields']['distance_km'] == (
            round(geodesy.geodesic_km(*FAROL, *AIRPORT), 6) if right == 'aero' else 1.0)


# --------------------------------------------------------------------------- rows written by the old code

def _old_row():
    """A topology exactly as the pre-network code persisted it: no location, no isolation record."""
    payload = default_simulated_topology().to_dict()
    payload['topology_version'] = 7
    payload['nodes'].append(AddSimulatedNode('legacy', 'endpoint', 0, '127.0.0.1', 7411).node().to_dict())
    payload['links'].append({'link_id': 'old-link', 'left_node_id': 'legacy', 'right_node_id': 'alice',
                             'distance_km': 12.5, 'nominal_pair_fidelity': .93, 'active': False,
                             'schema_version': '1.0.0'})
    repeater = payload['nodes'][1]
    repeater.update(memory_state='IDLE', available_pair_fidelities=[], memory_ages_s=[], state_version=3)
    repeater['telemetry']['console_control'] = {
        'isolated': True, 'last_action': 'isolate', 'last_command_sha256': 'c' * 64,
        'scope': 'simulator_only', 'physical_action_performed': False, 'calibration_count': 2,
        'calibrated_at_utc': '2026-09-12T10:00:00+00:00'}
    for link in payload['links'][:2]:
        link['active'] = False
    return payload


def test_rows_written_by_the_old_code_still_load_hash_and_reconcile(store, path):
    payload = _old_row()
    raw = _canonical_json(payload)
    with sqlite3.connect(path) as connection:
        connection.execute('INSERT INTO simulated_topologies VALUES (?, ?, ?, ?)',
                           (payload['topology_id'], payload['topology_version'], payload['updated_at_utc'], raw))
    loaded = store.get(DEFAULT_TOPOLOGY_ID)
    assert loaded.to_dict() == payload and _canonical_json(loaded.to_dict()) == raw  # same bytes, same hash
    assert _sha256(SimulatedTopology.from_dict(json.loads(raw)).to_dict()) == _sha256(payload)
    assert all(geodesy.node_location(node) is None for node in loaded.nodes)
    assert all(geodesy.link_geodesic_km(loaded, link) is None for link in loaded.links)

    journal = ConsoleCommandJournal(path)
    request = SetNodeLocation('legacy', *FAROL, 'Farol da Barra', 7)
    command_id = journal.begin('set_node_location', asdict(request))
    digest = read_commands(path)[0]['record_sha256']
    before = reconcile_command(path, command_id, digest)['record']
    assert before['topology'] == payload and before['topology_sha256'] == _sha256(payload)
    assert before['comparison']['target_status'] == 'differs_current_state'
    assert before['comparison']['observed_fields'] == {'node_id': 'legacy', 'location': None}

    located = store.set_node_location(request)
    assert located.topology_version == 8
    assert located.node('legacy').telemetry['inventory'] == payload['nodes'][3]['telemetry']['inventory']
    assert located.links == loaded.links  # alice is unlocated: the configured 12.5 km stands
    after = reconcile_command(path, command_id, digest)['record']
    assert after['comparison']['target_status'] == 'matches_current_state'
    assert after['comparison']['version_relation'] == 'at_expected_plus_one'

    # Old isolation has no record of what it switched off: restoring clears the flag and enables nothing.
    restored = isolate(store, 'repeater-a', False)
    saved = control(restored, 'repeater-a')
    assert saved['isolated'] is False and saved['isolation_disabled_links'] == [] and saved['calibration_count'] == 2
    assert active(restored) == set()
    assert events(path)[-1]['payload']['restored_links'] == []
    assert active(toggle(store, 'alice-r1', True)) == {'alice-r1'}
    final = store.record_node_calibration(RecalibrateNode('repeater-a', version(store)), REPORT)
    assert control(final, 'repeater-a')['calibration_count'] == 3
    assert SimulatedTopologyStore(path).get(DEFAULT_TOPOLOGY_ID) == final


# --------------------------------------------------------------------------- service + capabilities

def test_service_pass_throughs_persist_and_read_back(tmp_path):
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
    service = AssuranceDashboardService(repository_root=ROOT, control_journal_path=tmp_path / 'journal.sqlite')
    calls = [
        ('add_network_node', dict(VALID[AddNetworkNode], expected_version=0)),
        ('add_network_node', dict(VALID[AddNetworkNode], node_id='aero', latitude=AIRPORT[0], longitude=AIRPORT[1],
                                  place='Aeroporto', expected_version=1)),
        ('set_node_location', dict(VALID[SetNodeLocation], expected_version=2)),
        ('set_node_isolation', dict(VALID[SetNodeIsolation], expected_version=3)),
        ('set_node_isolation', dict(VALID[SetNodeIsolation], isolated=False, expected_version=4)),
        ('set_link_active', dict(VALID[SetLinkActive], expected_version=5)),
    ]
    for verb, payload in calls:
        result = getattr(service, verb)(payload)
        persisted = service.topology_store.get(DEFAULT_TOPOLOGY_ID)
        # Exactly the readback that the console's tracked dispatch requires before CONFIRMED.
        assert set(result) == {'status', 'physical_action_performed', 'topology'}
        assert result['status'] == 'persisted' and result['physical_action_performed'] is False
        assert persisted.topology_version == payload['expected_version'] + 1
        assert persisted.to_dict() == result['topology']
        json.dumps(result, allow_nan=False)
    result = service.record_node_calibration(dict(VALID[RecalibrateNode], expected_version=6), REPORT)
    assert result['status'] == 'persisted' and result['physical_action_performed'] is False
    assert result['topology'] == service.topology_store.get(DEFAULT_TOPOLOGY_ID).to_dict()
    assert result['topology']['topology_version'] == 7
    kinds = [event['event_kind'] for event in service.topology_store.list_events(DEFAULT_TOPOLOGY_ID)][::-1]
    assert kinds == ['network_node_added', 'network_node_added', 'node_location_set', 'node_isolated',
                     'node_restored', 'link_deactivated', 'node_recalibrated']
    for verb, payload in (('add_network_node', {**VALID[AddNetworkNode], 'kind': 'core'}),
                          ('set_node_location', {'node_id': 'alice'}),
                          ('set_node_isolation', {**VALID[SetNodeIsolation], 'isolated': 'yes'}),
                          ('set_link_active', {**VALID[SetLinkActive], 'force': True})):
        with pytest.raises(ValueError):
            getattr(service, verb)(payload)
    with pytest.raises(ValueError):
        service.record_node_calibration({**VALID[RecalibrateNode], 'report': REPORT}, REPORT)
    assert service.topology_store.get(DEFAULT_TOPOLOGY_ID).topology_version == 7


def test_operation_catalogue_declares_the_network_verbs():
    expected = {
        'add_network_node': 'simulation_inventory', 'set_node_location': 'simulation_inventory',
        'set_node_isolation': 'simulation_inventory', 'set_link_active': 'simulation_inventory',
        'recalibrate_node': 'simulation', 'request_pair': 'simulation', 'purify_pair': 'simulation',
        'consume_pair': 'simulation', 'define_pair': 'simulation', 'remove_pair': 'simulation',
        'inject_input_event': 'simulated_input', 'configure_network_input': 'simulation',
        'set_model_archive': 'local_staging', 'read_network_live': 'view',
    }
    assert {verb: OPERATIONS[verb] for verb in expected} == expected
    for legacy in ('add_simulated_node', 'configure_simulated_link', 'apply_simulated_node_action'):
        assert OPERATIONS[legacy] == 'simulation_inventory'
    assert set(NEW_VERBS) <= set(OPERATIONS)
    # Declaring a verb in the catalogue grants it to no source; the legacy synthetic verbs are intact.
    assert {'apply_simulated_node_action', 'configure_simulated_link'} <= set(synthetic_capabilities().operations)
    assert SourceCapabilities(mode='simulation', health='ready', operations=tuple(expected)).allows('set_link_active')
    SourceCapabilities(mode='read_only', operations=('read_network_live',))
    for verb in set(expected) - {'read_network_live'}:
        with pytest.raises(ValueError, match='read-only'):
            SourceCapabilities(mode='read_only', operations=(verb,))
