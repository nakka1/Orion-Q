"""Operational demands: acquired forwarding, resources, refusal and preservation."""
from contextlib import closing
from dataclasses import replace
import json

import pytest

from quantum_autopilot.control_plane.communicators import CommunicatorStore
from quantum_autopilot.control_plane.topology import NodeRole, SimulatedTopologyStore
from quantum_autopilot.network_engine.global_operations import (
    ClassicalHopCommand, ClassicalHopReceipt, GlobalOperationManager,
    SimulatedClassicalForwardingSource, validate_classical_receipt,
)
from quantum_autopilot.network_engine.global_operations_topology import (
    GLOBAL_OPERATIONS_EVENT, expand_global_operations, institutional_ids,
    operation_capabilities, upgrade_global_operations_workspace,
)
from quantum_autopilot.network_engine.institutional import brazil_institutions
from quantum_autopilot.network_engine.state import LinkLive
from quantum_autopilot.quantum_protocols.contracts import ProtocolError, canonical, digest
from quantum_autopilot.quantum_protocols.runtime import ProtocolRuntime
from quantum_autopilot.quantum_protocols.sources import SimulatedAcquisitionSource
from quantum_autopilot.quantum_protocols.topology import expand_global
from quantum_autopilot.quantum_protocols.satellite_bridges import add_atlantic_bridge

pytestmark = pytest.mark.unit


def _global():
    topology, _ = expand_global(brazil_institutions())
    topology, _ = add_atlantic_bridge(topology)
    return topology


def test_institutions_are_endpoints_and_optical_functions_remain_combinable():
    before = _global()
    after, report = expand_global_operations(before)
    assert len(institutional_ids(after)) == 20
    assert after.links[:len(before.links)] == before.links
    assert len(report['added_sessions']) == 8
    assert all(after.node(ident).role == NodeRole.ENDPOINT for ident in institutional_ids(after))
    for node in after.nodes:
        if node.node_id not in institutional_ids(after):
            assert node.role == before.node(node.node_id).role
    path_ids = {ident for raw in report['added_sessions'] for ident in raw['path']}
    assert 'CIMATEC' in path_ids and 'IME-RIO' in path_ids
    assert all(set(after.node(ident).telemetry['technology_capabilities']) >= {'quantum_memory', 'bell_measurement', 'bilateral_cnot'} for ident in path_ids)
    assert all(0 < link.distance_km <= 150 for link in after.links[len(before.links):])


def test_one_time_upgrade_keeps_operator_removals_and_full_before_state(tmp_path):
    store = SimulatedTopologyStore(tmp_path / 'migration.sqlite')
    before = _global()
    store.initialize(before)
    migrated = upgrade_global_operations_workspace(store, before)
    changed = replace(migrated, links=migrated.links[:-1], topology_version=migrated.topology_version + 1)
    store._replace_existing(changed, expected_version=migrated.topology_version, event_kind='operator_removal', payload={})
    assert upgrade_global_operations_workspace(store, changed) == changed
    with closing(store._connect()) as conn:
        raw = conn.execute('SELECT event_json FROM topology_events WHERE event_kind=?', (GLOBAL_OPERATIONS_EVENT,)).fetchone()[0]
    assert json.loads(raw)['payload']['before'] == before.to_dict()


def test_confirmed_communicator_revision_and_role_are_preserved(tmp_path):
    store = SimulatedTopologyStore(tmp_path / 'operator.sqlite')
    before = _global()
    store.initialize(before)
    enrolled = CommunicatorStore(store, before.topology_id).seed(before, 'simulated')
    old = enrolled.node('CBPF')
    record = {**old.telemetry['communicator'], 'revision': 3, 'description': 'operator configuration retained'}
    edited_node = replace(old, telemetry={**old.telemetry, 'communicator': record})
    edited = replace(enrolled, nodes=tuple(edited_node if node.node_id == 'CBPF' else node for node in enrolled.nodes))
    after, report = expand_global_operations(edited)
    assert after.node('CBPF').telemetry['communicator'] == record
    assert after.node('CBPF').role == edited_node.role
    assert any('operator-edited' in item['reason'] for item in report['impediments'])


def _control_network(tmp_path):
    """Only records are supplied; forwarding is acquired, serialized and timed."""
    original = brazil_institutions(repeaters=False)
    chosen = {'USP', 'IME-USP', 'UNICAMP'}
    topology = replace(original, nodes=tuple(node for node in original.nodes if node.node_id in chosen),
        links=tuple(link for link in original.links if link.link_id in ('USP-IME', 'USP-UNICAMP')))
    # Preserve real campus locations and declared span distances.
    topology, _ = expand_global_operations(topology)
    store = SimulatedTopologyStore(tmp_path / 'control.sqlite')
    store.initialize(topology)
    topology = CommunicatorStore(store, topology.topology_id).seed(topology, 'simulated')
    runtime = ProtocolRuntime(store.path, topology.topology_id, source=SimulatedAcquisitionSource(19))
    runtime.sync(topology)
    manager = GlobalOperationManager(store.path, topology.topology_id, seed=19)
    lives = {link.link_id: LinkLive(link.link_id, link.left_node_id, link.right_node_id,
             link.distance_km, 'up', provenance={'channels': {'source': 'simulated', 'status': 'received', 'tick': 0}})
             for link in topology.links}
    return store, topology, runtime, manager, lives


def test_serialized_multihop_probe_requires_every_received_classical_hop(tmp_path):
    store, topology, runtime, manager, live = _control_network(tmp_path)
    manager.prepare(topology, runtime, live, 1)
    demand = manager.demands['CONTROL-UNICAMP-IME-USP']
    assert demand['active'] and demand['successful_attempts'] == 0
    assert demand['route'] == ['UNICAMP', 'USP', 'IME-USP']
    assert demand['last_result'] is None
    runtime.advance(topology, 1.)
    snapshot = manager.observe(topology, runtime, live, 1)
    demand = manager.demands[demand['demand_id']]
    assert demand['successful_attempts'] == 1
    assert demand['last_result']['hops'] == 2
    assert 0 < demand['last_result']['latency_s'] < 1
    assert len(demand['receipt_ids']) == 2
    with closing(store._connect()) as conn:
        rows = [json.loads(row[0]) for row in conn.execute('SELECT record_json FROM global_operation_events WHERE demand_id=? ORDER BY sequence', (demand['demand_id'],))]
    sent = [row for row in rows if row['event'] == 'classical_probe_sent']
    received = [row for row in rows if row['event'] == 'classical_probe_received']
    first_envelope = json.loads(sent[0]['detail']['serialized_envelope'])
    second_envelope = json.loads(sent[1]['detail']['serialized_envelope'])
    assert first_envelope['visited_receipts'] == []
    assert second_envelope['visited_receipts'] == [received[0]['receipt_sha256']]
    assert sent[1]['at_s'] >= received[0]['at_s']
    assert snapshot['certified_secret_bits'] == 0


def test_classical_loss_retries_then_recovers_without_inventing_receipts(tmp_path):
    _, topology, runtime, manager, lives = _control_network(tmp_path)
    bad = replace(topology.node('UNICAMP'), telemetry={**topology.node('UNICAMP').telemetry,
        'classical_forwarding_parameters': {'packet_loss_probability': 1.}})
    topology = replace(topology, nodes=tuple(bad if node.node_id == bad.node_id else node for node in topology.nodes))
    manager.prepare(topology, runtime, lives, 1)
    runtime.advance(topology, 1.)
    manager.observe(topology, runtime, lives, 1)
    demand = manager.demands['CONTROL-UNICAMP-IME-USP']
    assert demand['failed_attempts'] == 1 and demand['successful_attempts'] == 0
    assert demand['receipt_ids'] == [] and 'packet loss' in demand['reason']
    assert demand['next_attempt_s'] > 1.
    good = replace(bad, telemetry={**bad.telemetry, 'classical_forwarding_parameters': {'packet_loss_probability': 0.}})
    topology = replace(topology, nodes=tuple(good if node.node_id == good.node_id else node for node in topology.nodes))
    runtime.advance(topology, 2.)
    lives = {ident: replace(live, provenance={'channels': {'source': 'simulated', 'status': 'received', 'tick': 2}})
             for ident, live in lives.items()}
    manager.prepare(topology, runtime, lives, 3)
    runtime.advance(topology, 3.)
    manager.observe(topology, runtime, lives, 3)
    assert demand['attempts'] == 2 and demand['successful_attempts'] == 1
    assert len(demand['receipt_ids']) == 2


def test_classical_channel_disabled_blocks_route_and_is_not_quantum_success(tmp_path):
    _, topology, runtime, manager, lives = _control_network(tmp_path)
    disabled = []
    for link in topology.links:
        if 'UNICAMP' in (link.left_node_id, link.right_node_id):
            channels = {**link.channels, 'classical': {**link.channels['classical'], 'enabled': False}}
            link = replace(link, channels=channels)
        disabled.append(link)
    topology = replace(topology, links=tuple(disabled))
    manager.prepare(topology, runtime, lives, 1)
    demand = manager.demands['CONTROL-UNICAMP-IME-USP']
    assert not demand['active'] and demand['successful_attempts'] == 0
    assert 'classical channel disabled' in demand['reason']
    assert demand['state'] == 'unavailable'


def test_physical_devices_never_receive_synthetic_forwarding_results(tmp_path):
    _, topology, runtime, manager, lives = _control_network(tmp_path)
    old = topology.node('UNICAMP')
    record = old.telemetry['communicator']
    physical = replace(old, telemetry={**old.telemetry, 'communicator': {**record,
        'integration': {**record['integration'], 'mode': 'physical', 'data_origin': 'physical'}}})
    topology = replace(topology, nodes=tuple(physical if node.node_id == old.node_id else node for node in topology.nodes))
    manager.prepare(topology, runtime, lives, 1)
    demand = manager.demands['CONTROL-UNICAMP-IME-USP']
    assert not demand['active'] and 'external classical forwarding adapter not validated' in demand['reason']
    assert demand['receipt_ids'] == []


def test_real_orbital_source_at_seed_revision_zero_is_preserved(tmp_path):
    store = SimulatedTopologyStore(tmp_path / 'real-orbit.sqlite')
    before = _global()
    store.initialize(before)
    enrolled = CommunicatorStore(store, before.topology_id).seed(before, 'simulated')
    old = enrolled.node('SAT-RIO-SATELLITE')
    record = old.telemetry['communicator']
    record = {**record, 'revision': 0, 'orbit': {**record['orbit'], 'model': 'sgp4_tle', 'source': 'NORAD TLE supplied by operator'},
              'description': 'real ephemeris provenance preserved'}
    edited = replace(enrolled, nodes=tuple(replace(node, telemetry={**node.telemetry, 'communicator': record}) if node.node_id == old.node_id else node for node in enrolled.nodes))
    after, _ = expand_global_operations(edited)
    assert after.node(old.node_id).telemetry['communicator'] == record


def test_expired_acquisition_does_not_make_component_available_forever(tmp_path):
    _, topology, runtime, manager, lives = _control_network(tmp_path)
    runtime.sessions.clear()
    runtime.clock_s = 20.
    manager.observe(topology, runtime, lives, 20)
    assert all(not row['measured_acquisition_present'] for row in manager.snapshot()['components'])
    assert all(row['operation_state'] != 'available_no_traffic' for row in manager.snapshot()['components'])


def test_quantum_resource_receipt_is_assigned_to_only_one_demand(tmp_path):
    _, topology, runtime, manager, lives = _control_network(tmp_path)
    for tick in range(1, 5):
        manager.prepare(topology, runtime, lives, tick)
        runtime.advance(topology, tick)
        manager.observe(topology, runtime, lives, tick)
    receipts = [event['detail']['technology_receipt'] for event in manager.events if event['event'] == 'protocol_demand_result']
    assert receipts and len(receipts) == len(set(receipts))
    delivered = sum(session.counts['delivered_pairs'] for session in runtime.sessions.values())
    assigned = sum(demand['successful_attempts'] for demand in manager.demands.values() if demand['operation'] in ('entanglement_pair', 'global_entanglement'))
    assert 0 < assigned <= delivered


@pytest.mark.parametrize('mutation,error', [
    ({'receiver': 'OTHER'}, 'wrong_control_binding'),
    ({'observed_at_s': -1.}, 'control_causality'),
    ({'envelope_sha256': 'wrong'}, 'control_integrity'),
    ({'source_id': 'unknown'}, 'wrong_control_source'),
])
def test_control_receipt_binding_causality_and_integrity(mutation, error):
    source = SimulatedClassicalForwardingSource(4)
    envelope = canonical({'payload': 'probe'})
    command = ClassicalHopCommand('command', 'demand', 1, 0, 'link', 'link', 'link.classical', 'A', 'B', 0., 2., envelope)
    receipt = source.acquire(command, {'propagation_s': .01})
    receipt = replace(receipt, **mutation)
    with pytest.raises(ProtocolError) as failure:
        validate_classical_receipt(receipt, command, source=source, now_s=.1, last_sequence=0)
    assert failure.value.code == error


def test_control_receipt_replay_is_refused():
    source = SimulatedClassicalForwardingSource(4)
    command = ClassicalHopCommand('command', 'demand', 1, 0, 'link', 'link', 'link.classical', 'A', 'B', 0., 2., canonical({'payload': 'probe'}))
    receipt = source.acquire(command, {'propagation_s': .01})
    with pytest.raises(ProtocolError, match='sequence stale'):
        validate_classical_receipt(receipt, command, source=source, now_s=.1, last_sequence=receipt.sequence)
