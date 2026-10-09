"""Migration, optical reach, paired acquisition and honest repeater availability."""
from contextlib import closing
from dataclasses import replace
import json

import networkx as nx
import pytest

from quantum_autopilot.control_plane.topology import SimulatedNodeState, SimulatedTopologyStore
from quantum_autopilot.network_engine.institutional import (
    CONNECTIONS, PRESET_ID, REPEATER_MAX_SPAN_KM, UPGRADE_EVENT,
    brazil_institutions, ensure_institutional_workspace,
)
from quantum_autopilot.network_engine.routing import effective_graph
from quantum_autopilot.qkd.dashboard_api import demo_service

pytestmark = pytest.mark.unit


def test_direct_connections_are_preserved_and_all_campuses_have_segmented_paths():
    original = brazil_institutions(repeaters=False)
    expanded = brazil_institutions()
    assert len(expanded.nodes) == 31 and len(expanded.links) == 36
    assert [l.to_dict() for l in expanded.links[:8]] == [l.to_dict() for l in original.links]
    segments = [l for l in expanded.links if l.link_id.startswith('REP-')]
    from quantum_autopilot.console.geodata import load_brazil
    land = load_brazil()
    for node in expanded.nodes:
        if 'demonstration_repeater' in node.telemetry:
            location = node.telemetry['location']
            assert land.contains_land(location['longitude'], location['latitude']), node.node_id
    graph = nx.Graph()
    graph.add_edges_from((l.left_node_id, l.right_node_id) for l in expanded.links
                         if l.distance_km <= REPEATER_MAX_SPAN_KM)
    assert nx.is_connected(graph) and len(graph) == len(expanded.nodes)
    assert all(0 < l.distance_km <= REPEATER_MAX_SPAN_KM for l in segments)
    identifiers = [l.channels[k]['channel_id'] for l in expanded.links for k in ('classical','quantum')]
    assert len(set(identifiers)) == 2 * len(expanded.links)
    for ident, a, b in CONNECTIONS:
        if next(l for l in original.links if l.link_id == ident).distance_km > REPEATER_MAX_SPAN_KM:
            path = nx.shortest_path(graph, a, b)
            assert len(path) > 2
            assert all(expanded.node(n).telemetry['demonstration_repeater']['equipment'] == 'hypothetical'
                       for n in path[1:-1])


def test_legacy_demo_upgrade_preserves_edits_records_before_and_runs_once(tmp_path):
    from quantum_autopilot.quantum_protocols.topology import GLOBAL_EVENT
    from quantum_autopilot.quantum_protocols.satellite_bridges import BRIDGE_EVENT
    from quantum_autopilot.quantum_protocols.global_geo import GEO_EVENT
    from quantum_autopilot.network_engine.global_operations_topology import GLOBAL_OPERATIONS_EVENT, institutional_ids
    store = SimulatedTopologyStore(tmp_path / 'legacy.sqlite')
    old = brazil_institutions(repeaters=False)
    links = [replace(l, channels=None) for l in old.links]
    links[1] = replace(links[1], active=False)
    custom = old.links[0].channels
    custom = {**custom, 'classical': {**custom['classical'], 'max_age_s': 3.0}}
    links[0] = replace(links[0], channels=custom)
    extra = SimulatedNodeState('OPERATOR', 'endpoint', 0, 'IDLE', telemetry={'operator_note': 'keep'})
    old = replace(old, links=tuple(links), nodes=(*old.nodes, extra), topology_version=7)
    store.initialize(old)
    upgraded, created = ensure_institutional_workspace(store)
    # Repeater, global technology, Atlantic bridge, GEO and global-operation
    # migrations are separate recorded revisions preserving operator edits.
    migration_kinds=(UPGRADE_EVENT,GLOBAL_EVENT,BRIDGE_EVENT,GEO_EVENT,GLOBAL_OPERATIONS_EVENT)
    assert not created and upgraded.topology_version == old.topology_version+len(migration_kinds)
    migrations={event['event_kind']:event for event in store.list_events(old.topology_id,100)
        if event['event_kind'] in migration_kinds}
    assert set(migrations)==set(migration_kinds)
    for index,kind in enumerate(migration_kinds):
        assert (migrations[kind]['version_before'],migrations[kind]['version_after'])==(7+index,8+index)
    assert 'UNIJORGE' not in {n.node_id for n in upgraded.nodes}
    for node in old.nodes:
        if node.node_id == 'UNIJORGE': continue
        after=upgraded.node(node.node_id)
        if node.node_id in institutional_ids(upgraded):
            assert after.role.value=='endpoint'
            assert after.telemetry['operational_capabilities']['originate_receive']
        else:assert after.role==node.role
        assert after.memory_state==node.memory_state
        assert all(after.telemetry[key]==value for key,value in node.telemetry.items())
    for before in old.links:
        if 'UNIJORGE' in (before.left_node_id,before.right_node_id): continue
        after=next(l for l in upgraded.links if l.link_id==before.link_id)
        assert replace(after,channels=before.channels)==before
    assert not next(l for l in upgraded.links if l.link_id=='CIMATEC-CIQUANTA').active
    with closing(store._connect()) as connection:
        event = json.loads(connection.execute('SELECT event_json FROM topology_events WHERE event_kind=?',
                                             (UPGRADE_EVENT,)).fetchone()[0])
    assert event['payload']['before'] == old.to_dict()
    assert event['payload']['physical_action_performed'] is False
    # A later operator removal or deliberately cleared channel is not silently undone.
    changed = replace(upgraded, links=(replace(upgraded.links[0], channels=None), *upgraded.links[1:-1]),
                      topology_version=upgraded.topology_version+1)
    store._replace_existing(changed, expected_version=upgraded.topology_version, event_kind='operator_edit', payload={})
    again, created = ensure_institutional_workspace(store)
    assert not created and again == changed
    events=store.list_events(old.topology_id,100)
    assert all(sum(e['event_kind']==kind for e in events)==1 for kind in migration_kinds)


def test_demo_upgrade_does_not_repair_non_institution_operator_links(tmp_path):
    from quantum_autopilot.control_plane.topology import SimulatedLink
    old = brazil_institutions(repeaters=False)
    extra = SimulatedLink('CUSTOM', 'USP', 'IME-USP', 1, .95, channels=None)
    store = SimulatedTopologyStore(tmp_path / 'custom.sqlite')
    store.initialize(replace(old, links=(*old.links, extra)))
    upgraded, _ = ensure_institutional_workspace(store)
    assert next(l for l in upgraded.links if l.link_id == 'CUSTOM') == extra


def test_reserved_identifier_collision_leaves_existing_topology_untouched(tmp_path):
    old = brazil_institutions(repeaters=False)
    operator = SimulatedNodeState('R-CIMATEC-CBPF-01', 'endpoint', 0, 'IDLE')
    store = SimulatedTopologyStore(tmp_path / 'collision.sqlite')
    store.initialize(replace(old, nodes=(*old.nodes, operator)))
    before = store.get(old.topology_id)
    with pytest.raises(ValueError, match='identifier collision'):
        ensure_institutional_workspace(store)
    assert store.get(old.topology_id) == before


@pytest.mark.parametrize('seed', [20261004, 19, 71])
def test_all_nodes_receive_usable_hops_but_no_direct_long_span_is_promoted(tmp_path, seed):
    service = demo_service(state_root=tmp_path, preset=PRESET_ID, scenario='quiet', seed=seed)
    for _ in range(3):
        live = service.network_engine.step()
        legacy_ids={n.node_id for n in brazil_institutions().nodes if n.node_id!='UNIJORGE'}
        assert all(n.status in ('up','degraded') for n in live.nodes if n.node_id in legacy_ids)
        measured = {l.link_id: l for l in live.links}
        topology = service.topology_store.get(service.network_engine.topology_id)
        graph = effective_graph(topology, measured)
        assert nx.is_connected(graph.subgraph(legacy_ids))
        for link in live.links:
            if 'technology' in link.provenance:
                assert link.link_id in graph.graph['blocked']
                continue
            receipt = link.provenance['channels']
            assert receipt['status'] == 'received' and len(receipt['stages']) == 6
            assert receipt['physical_transmission'] is False
            if link.link_id.startswith('REP-'):
                assert link.skr_bps > 0 and link.qkd.key_pool_bits > 0 and link.qber < live.policy['qber_down']
                assert link.link_id not in graph.graph['blocked']
            elif link.distance_km > 300:
                assert link.status == 'down' and link.skr_bps == link.qkd.key_pool_bits == 0
                assert link.link_id in graph.graph['blocked']
        assert not live.policy['ml_control_enabled']
        for demand in live.demands:
            if demand.status in ('routed', 'rerouted'):
                assert demand.fidelity >= demand.min_fidelity
                assert all(link not in graph.graph['blocked'] for link in demand.route_links)


def test_repeater_isolation_breaks_its_hops_and_restore_uses_real_acquisition(tmp_path):
    service = demo_service(state_root=tmp_path, preset=PRESET_ID, scenario='quiet')
    service.network_engine.step()
    ident = 'R-CIMATEC-CIQUANTA-03'
    topology = service.topology_store.get(service.network_engine.topology_id)
    service.set_node_isolation({'node_id': ident, 'isolated': True,
        'expected_version': topology.topology_version, 'topology_id': topology.topology_id})
    isolated = service.network_engine.step()
    graph = effective_graph(service.topology_store.get(topology.topology_id), {l.link_id:l for l in isolated.links})
    assert not nx.has_path(graph, 'CIMATEC', 'CIQUANTA')
    assert next(n for n in isolated.nodes if n.node_id == ident).status == 'isolated'
    topology = service.topology_store.get(topology.topology_id)
    service.set_node_isolation({'node_id': ident, 'isolated': False,
        'expected_version': topology.topology_version, 'topology_id': topology.topology_id})
    restored = service.network_engine.step()
    # Aggregate health includes the new measured-resource sessions, which may
    # remain degraded without preventing actual BB84 hop reacquisition.
    assert next(n for n in restored.nodes if n.node_id == ident).status in ('up','degraded')
    original_hops=[l for l in restored.links if ident in (l.a,l.b) and 'technology' not in l.provenance]
    assert len(original_hops)==2
    for link in original_hops:
        receipt=link.provenance['channels']
        assert receipt['status']=='received' and receipt['tick']==restored.tick and len(receipt['stages'])==6
        assert receipt['acquisition_id'] and receipt['physical_transmission'] is False
        assert link.status in ('up','degraded') and link.qkd.key_pool_bits>0
    modular=[l for l in restored.links if ident in (l.a,l.b) and 'technology' in l.provenance]
    assert modular
    assert all(link.qkd.key_pool_bits is None for link in modular)


def test_repeater_acquisition_file_replay_uses_identical_processing(tmp_path):
    from quantum_autopilot.network_engine.channel_io import AcquisitionFileSource, observation_record
    from quantum_autopilot.network_engine.control_mode import read_mode
    from quantum_autopilot.network_engine.engine import NetworkEngine
    from quantum_autopilot.network_engine.models import ModelRegistry
    from quantum_autopilot.network_engine.sources import SimulatedInputSource
    topology = brazil_institutions()
    generator = SimulatedInputSource(19, 'quiet')
    frames = [generator.frame(tick, topology) for tick in (1, 2)]
    capture = tmp_path / 'raw-fixture.json'
    capture.write_text(json.dumps({'schema':'orion.channel-capture.v1', 'source_id':'simulated-test-fixture',
        'kind':'simulated', 'frames':[{'tick':f.tick, 'links':[
            {'observation':observation_record(item), 'quantum_record':item.quantum_record}
            for item in f.links.values()]} for f in frames]}, allow_nan=False), encoding='utf-8')
    engines = []
    for name, source in [('simulation', SimulatedInputSource(19, 'quiet')),
                         ('replay', AcquisitionFileSource(capture, expected_kind='simulated'))]:
        store = SimulatedTopologyStore(tmp_path / f'{name}.sqlite')
        store.initialize(topology)
        read_mode(store.path, topology.topology_id, default=False)
        engines.append(NetworkEngine(store, store.path, source=source, registry=ModelRegistry(environ={})))
    for _ in range(2):
        left, right = (engine.step() for engine in engines)
        for a, b in zip(left.links, right.links):
            for field in ('status','qber','fidelity','skr_bps','history','wave','qkd'):
                assert getattr(a, field) == getattr(b, field), (a.link_id, field)
            assert a.provenance['channels']['status'] == b.provenance['channels']['status'] == 'received'
        assert [(n.node_id,n.status) for n in left.nodes] == [(n.node_id,n.status) for n in right.nodes]
    ended = engines[1].step()
    assert all(l.qber is None and l.skr_bps is None for l in ended.links)
    assert all(l.provenance['channels']['status'] == 'no_input' for l in ended.links)
