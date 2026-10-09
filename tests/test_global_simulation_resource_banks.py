"""Hardware-bank assumptions are explicit without modifying physical records."""
import csv
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from quantum_autopilot.control_plane.communicators import CommunicatorStore
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from quantum_autopilot.network_engine.global_operations import GlobalOperationManager
from quantum_autopilot.network_engine.global_operations_topology import expand_global_operations
from quantum_autopilot.network_engine.institutional import brazil_institutions
from quantum_autopilot.quantum_protocols.contracts import ProtocolConfig
from quantum_autopilot.quantum_protocols.global_geo import expand_geo_constellation
from quantum_autopilot.quantum_protocols.satellite_bridges import add_atlantic_bridge
from quantum_autopilot.quantum_protocols.topology import expand_global

pytestmark = pytest.mark.unit


def _global():
    topology, _ = expand_global(brazil_institutions())
    topology, _ = add_atlantic_bridge(topology)
    topology, _ = expand_geo_constellation(topology)
    return topology


def _sessions(topology):
    return {raw['session_id']: raw for node in topology.nodes for raw in node.telemetry.get('technology_sessions', ())}


def test_session_banks_are_unique_and_totals_sum_additional_equipment():
    before = _global()
    after, report = expand_global_operations(before)
    sessions = _sessions(after)
    assert len(sessions) == 36
    assert len({raw['parameters']['resource_bank_id'] for raw in sessions.values()}) == len(sessions)
    for session_id, raw in sessions.items():
        ProtocolConfig.from_dict(raw)  # The strict existing contract must remain usable.
        assert raw['parameters']['resource_bank_scope'] == 'dedicated_per_session'
        for ident in raw['path']:
            banks = [bank for bank in after.node(ident).telemetry['simulation_resource_banks'] if bank['session_id'] == session_id]
            assert len(banks) == 1 and banks[0]['resource_bank_id'] == raw['parameters']['resource_bank_id']
            assert set(banks[0]['quantum_link_ids']) <= set(raw['link_ids'])
            if 'tf_qkd' in raw['technologies'] or 'satellite_isl' in raw['technologies']:
                assert banks[0]['memory_modes'] == 0  # Destructive detections are not quantum storage.
    for node in after.nodes:
        banks = node.telemetry.get('simulation_resource_banks', [])
        if not banks:
            continue
        totals = node.telemetry['simulation_resource_totals']
        assert totals['resource_bank_count'] == len(banks)
        assert totals['session_ids'] == [bank['session_id'] for bank in banks]
        for key in ('memory_modes', 'pair_sources', 'pulse_sources', 'optical_terminals'):
            assert totals[key] == sum(bank[key] for bank in banks)
        assert totals['sources'] == totals['pair_sources'] + totals['pulse_sources']
        assert not totals['physical_capacity_validated'] and not totals['shared_hardware_arbitration_implemented']
    assert after.node('CIMATEC').telemetry['simulation_resource_totals']['memory_modes'] >= 24
    assert report['resource_allocation_not_inferred'] == []
    # Metadata must not change the link/channel geometry or optical parameters.
    assert before.links == after.links[:len(before.links)]
    for session_id, raw in _sessions(before).items():
        updated = {key: value for key, value in sessions[session_id]['parameters'].items() if key not in ('resource_bank_id', 'resource_bank_scope')}
        assert updated == raw['parameters']


@pytest.mark.parametrize('protection', ['operator_revision', 'measurement_file', 'mission_tle'])
def test_protected_device_and_session_do_not_receive_hypothetical_banks(tmp_path, protection):
    topology = _global()
    store = SimulatedTopologyStore(tmp_path / 'protected.sqlite')
    store.initialize(topology)
    topology = CommunicatorStore(store, topology.topology_id).seed(topology, 'simulated')
    ident = 'GEO-AMERICAS-SIM' if protection == 'mission_tle' else 'CBPF'
    old = topology.node(ident)
    record = dict(old.telemetry['communicator'])
    if protection == 'operator_revision':
        record.update(revision=3, description='operator bank preserved')
    elif protection == 'measurement_file':
        record['integration'] = {**record['integration'], 'mode': 'measurement_file', 'service_address': 'preserved.jsonl'}
    else:
        record['orbit'] = {**record['orbit'], 'model': 'sgp4_tle', 'source': 'NORAD TLE real mission'}
    edited = replace(old, telemetry={**old.telemetry, 'communicator': record})
    topology = replace(topology, nodes=tuple(edited if node.node_id == ident else node for node in topology.nodes))
    before_sessions = _sessions(topology)
    after, report = expand_global_operations(topology)
    assert after.node(ident).telemetry['communicator'] == record
    assert 'simulation_resource_banks' not in after.node(ident).telemetry
    assert after.node(ident).role == old.role
    protected_sessions = {session_id for session_id, raw in before_sessions.items() if ident in raw['path']}
    assert protected_sessions
    for session_id in protected_sessions:
        assert _sessions(after)[session_id] == before_sessions[session_id]
    assert protected_sessions <= {item['session_id'] for item in report['resource_allocation_not_inferred']}


def test_declared_source_mode_is_preserved_without_inferred_equipment():
    topology = _global()
    owner = next(node for node in topology.nodes if node.telemetry.get('technology_sessions'))
    raw = owner.telemetry['technology_sessions'][0]
    external = {**raw, 'parameters': {**raw['parameters'], 'acquisition_source': {'mode': 'measurement_file', 'path': 'acquisition.jsonl'}}}
    owner = replace(owner, telemetry={**owner.telemetry, 'technology_sessions': [external, *owner.telemetry['technology_sessions'][1:]]})
    topology = replace(topology, nodes=tuple(owner if node.node_id == owner.node_id else node for node in topology.nodes))
    after, report = expand_global_operations(topology)
    assert _sessions(after)[raw['session_id']] == external
    assert not any(bank['session_id'] == raw['session_id'] for node in after.nodes for bank in node.telemetry.get('simulation_resource_banks', []))
    assert raw['session_id'] in {item['session_id'] for item in report['resource_allocation_not_inferred']}


def test_component_matrix_and_csv_expose_banks_without_affecting_availability(tmp_path):
    topology, _ = expand_global_operations(_global())
    manager = GlobalOperationManager(tmp_path / 'matrix.sqlite', topology.topology_id)
    runtime = SimpleNamespace(sessions={}, clock_rate=1.)
    with_banks = manager._matrix(topology, runtime, ())
    stripped = replace(topology, nodes=tuple(replace(node, telemetry={key: value for key, value in node.telemetry.items()
        if key not in ('simulation_resource_banks', 'simulation_resource_totals')}) for node in topology.nodes))
    without_banks = manager._matrix(stripped, runtime, ())
    assert [{key: value for key, value in row.items() if key != 'simulation_resource_allocation'} for row in with_banks] == [
        {key: value for key, value in row.items() if key != 'simulation_resource_allocation'} for row in without_banks]
    manager._components = with_banks
    manager.export(tmp_path / 'export')
    with (tmp_path / 'export' / 'component-matrix.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = {row['component']: row for row in csv.DictReader(stream)}
    allocation = json.loads(rows['CIMATEC']['simulation_resource_allocation'])
    assert allocation['totals']['resource_bank_count'] >= 3
    assert not allocation['shared_hardware_arbitration_implemented']
