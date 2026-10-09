"""Additive, once-only institutional operational configuration.

Institutions are endpoints; optical functions are capabilities, not mutually
exclusive geographic roles. Existing links, operator revisions and removals are
preserved. New optical records use independently identified paired channels.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import json

import networkx as nx

from ..control_plane.channels import local_pair
from ..control_plane.topology import NodeRole, SimulatedLink
from ..quantum_protocols.contracts import ProtocolConfig

GLOBAL_OPERATIONS_EVENT = 'institutional_global_operations_v1'
BASE_INSTITUTIONS = ('CIMATEC', 'CBPF', 'USP', 'IME-USP', 'UNICAMP', 'CIQUANTA')
OPTICAL_CAPABILITIES = ('quantum_memory', 'bell_measurement', 'bilateral_cnot')


def institutional_ids(topology):
    from ..quantum_protocols.topology import site_catalogue
    known = set(BASE_INSTITUTIONS) | {row['id'] for row in site_catalogue()['sites']}
    return tuple(node.node_id for node in topology.nodes if node.node_id in known and
                 node.telemetry.get('inventory', {}).get('origin') == 'brazil-institutions-v1')


def operation_capabilities(node):
    """Read configurations; a location or institution name is not a capability."""
    record = node.telemetry.get('communicator', {})
    capabilities = set(record.get('capabilities', node.telemetry.get('technology_capabilities', ())))
    technologies = set(record.get('technologies', ()))
    declared = node.telemetry.get('operational_capabilities', {})
    return {
        'originate_receive': {'endpoint_send', 'endpoint_receive'} <= capabilities or bool(declared.get('originate_receive', record.get('network_role') in ('endpoint', 'core'))),
        'classical_forwarding': 'classical_forward' in capabilities or bool(declared.get('classical_forwarding', False)),
        'quantum_repetition': 'entanglement_swapping' in technologies and {'quantum_memory', 'bell_measurement'} <= capabilities,
        'entanglement_establishment': bool(technologies & {'entanglement_swapping', 'satellite_doppler', 'satellite_isl'}),
        'key_distribution_estimate': bool(technologies & {'tf_qkd', 'satellite_doppler', 'satellite_isl'}) or 'BB84' in record.get('protocols', ()),
        'usable_certified_keys': False,
        'quantum_memory': 'quantum_memory' in technologies and 'quantum_memory' in capabilities,
        'purification': 'purification' in technologies and 'bilateral_cnot' in capabilities,
        'physical_adapter_validated': False,
    }


def _unique(base, occupied):
    candidate, suffix = base, 1
    while candidate in occupied:
        suffix += 1
        candidate = f'{base}-{suffix}'
    occupied.add(candidate)
    return candidate


def _preserve_record(record):
    if not record:
        return False
    orbit = record.get('orbit', {})
    source = str(orbit.get('source', '')).upper()
    return (record.get('revision', 0) > 0 or record.get('integration', {}).get('mode') != 'simulated'
            or orbit.get('model') in ('sgp4_tle', 'state_vectors') or 'NORAD' in source or 'TLE' in source)


def _simulation_resource_banks(nodes, links, sessions):
    """Declare the independent hardware banks the simulator already assumes.

    Runtime pools and reservations belong to a session, not to a shared device.
    These counts therefore describe hypothetical *additional* equipment at
    each path point; they neither validate installed hardware nor arbitrate it.
    Source placement on the left end of each terrestrial span is an explicit
    bookkeeping convention for the current symmetric pair-generation model.
    Operator revisions, external acquisitions and mission ephemerides are left
    untouched rather than assigned hypothetical hardware by this migration.
    """
    by_node = {node.node_id: node for node in nodes}
    by_link = {link.link_id: link for link in links}
    banks, annotated, skipped = {}, [], []
    for raw in sessions:
        parameters = raw.get('parameters', {})
        path = tuple(raw['path'])
        orbits = [parameters.get('orbit_record', {})]
        orbits.extend(parameters.get('satellite_orbits', {}).values())
        protected = (any(ident not in by_node for ident in path)
            or parameters.get('acquisition_source', {}).get('mode', 'simulated') != 'simulated'
            or any(_preserve_record(by_node[ident].telemetry.get('communicator', {})) for ident in path)
            or any(_preserve_record({'integration': {'mode': 'simulated'}, 'orbit': orbit}) for orbit in orbits if orbit))
        if protected:
            annotated.append(raw)
            skipped.append({'session_id': raw['session_id'],
                'reason': 'operator, external acquisition or mission orbit retained; no simulated hardware allocation inferred'})
            continue
        bank_id = f"sim-resource:{raw['session_id']}"
        annotated.append({**raw, 'parameters': {**parameters,
            'resource_bank_id': bank_id, 'resource_bank_scope': 'dedicated_per_session'}})
        technologies = set(raw['technologies'])
        pair_sources = {ident: 0 for ident in path}
        pulse_sources = {ident: 0 for ident in path}
        if 'tf_qkd' in technologies:
            for ident in raw['endpoints']:
                pulse_sources[ident] += 1
            placement = 'dedicated pulse source at each TF-QKD endpoint'
        elif 'satellite_isl' in technologies:
            pair_sources[raw['endpoints'][0]] += 1
            placement = 'dedicated pair source at the configured emitting ISL endpoint'
        elif 'satellite_doppler' in technologies:
            satellite = next((ident for ident in path if ident not in raw['endpoints']), path[0])
            pair_sources[satellite] += 1
            placement = 'dedicated pair source aboard the configured dual-downlink satellite'
        else:
            for ident in path[:-1]:
                pair_sources[ident] += 1
            placement = 'hypothetical source at left endpoint of each path span; symmetric generation-model bookkeeping'
        for ident in dict.fromkeys(path):
            incident = [link_id for link_id in raw['link_ids'] if link_id in by_link
                and ident in (by_link[link_id].left_node_id, by_link[link_id].right_node_id)]
            device = parameters.get('device_parameters', {}).get(ident, {})
            memory = device.get('memory_slots', parameters.get('memory_slots', 4)) if technologies & {'quantum_memory', 'entanglement_swapping'} else 0
            banks.setdefault(ident, []).append({
                'resource_bank_id': bank_id, 'session_id': raw['session_id'], 'node_id': ident,
                'resource_bank_scope': 'dedicated_per_session', 'technologies': list(raw['technologies']),
                'memory_modes': memory, 'pair_sources': pair_sources[ident], 'pulse_sources': pulse_sources[ident],
                'optical_terminals': len(incident), 'quantum_link_ids': incident,
                'source_placement_assumption': placement,
                'capacity_scope': 'hypothetical simulated equipment; additional bank per session',
                'physical_capacity_validated': False, 'shared_hardware_arbitration_implemented': False,
            })
    totals = {ident: {
        'resource_bank_scope': 'dedicated_per_session', 'resource_bank_count': len(local),
        'session_ids': [bank['session_id'] for bank in local],
        **{key: sum(bank[key] for bank in local) for key in ('memory_modes', 'pair_sources', 'pulse_sources', 'optical_terminals')},
        'sources': sum(bank['pair_sources'] + bank['pulse_sources'] for bank in local),
        'capacity_scope': 'sum of dedicated hypothetical banks, not installed or shared hardware',
        'physical_capacity_validated': False, 'shared_hardware_arbitration_implemented': False,
    } for ident, local in banks.items()}
    return annotated, banks, totals, skipped


def expand_global_operations(topology):
    """Configure endpoints and measured-resource sessions on existing land spans."""
    ids = set(institutional_ids(topology))
    if not ids:
        return topology, {'scope': 'non-institutional workspace; unchanged'}
    nodes, links = list(topology.nodes), list(topology.links)
    sessions = [raw for node in nodes for raw in node.telemetry.get('technology_sessions', ())]
    occupied_sessions = {raw['session_id'] for raw in sessions}
    occupied_links = {link.link_id for link in links}
    # Reuse the already verified inland repeater placements as geometric spans.
    # New protocol links are separate resources and never overwrite old BB84
    # channels or promote the original long, attenuated direct fibres.
    fibre_graph = nx.Graph()
    for link in links:
        if link.technology is None and link.distance_km <= 150.0 and link.channels:
            if not fibre_graph.has_edge(link.left_node_id, link.right_node_id) or link.distance_km < fibre_graph[link.left_node_id][link.right_node_id]['length']:
                fibre_graph.add_edge(link.left_node_id, link.right_node_id, length=link.distance_km, link=link)
    added, impediments = [], []
    pairs = (
        ('BR-NORDESTE-ENT', 'CIMATEC', 'CIQUANTA'),
        ('BR-SALVADOR-RIO-ENT', 'CIMATEC', 'CBPF'),
        ('BR-SALVADOR-USP-ENT', 'CIMATEC', 'USP'),
        ('BR-RIO-USP-ENT', 'CBPF', 'USP'),
        ('BR-BUTANTA-ENT', 'USP', 'IME-USP'),
        ('BR-CAMPINAS-ENT', 'USP', 'UNICAMP'),
        ('BR-IME-CAMPINAS-ENT', 'IME-USP', 'UNICAMP'),
    )
    paths = []
    for ident, a, b in pairs:
        if a not in ids or b not in ids or not nx.has_path(fibre_graph, a, b):
            impediments.append({'session': ident, 'endpoints': [a, b], 'reason': 'no existing configured terrestrial span path'})
            continue
        path = nx.shortest_path(fibre_graph, a, b, weight='length')
        paths.append((ident, path, [fibre_graph[u][v]['link'] for u, v in zip(path, path[1:])]))
    # IME-RIO was an intermediary in the metropolitan chain; make its own
    # endpoint delivery observable without bypassing the represented equipment.
    for link in links:
        if {link.left_node_id, link.right_node_id} == {'IME-RIO', 'CBPF'} and link.technology:
            paths.append(('RIO-IME-ENDPOINT', ['IME-RIO', 'CBPF'], [link]))
            break
    enriched_ids = set()
    for ident, path, spans in paths:
        if any(_preserve_record(topology.node(node_id).telemetry.get('communicator', {})) for node_id in path):
            impediments.append({'session': ident, 'endpoints': [path[0], path[-1]], 'reason': 'operator-edited device preserved; supplementary resource adapter needs explicit enrollment'})
            continue
        session_id = _unique(ident, occupied_sessions)
        link_ids = []
        for index, span in enumerate(spans, 1):
            link_id = _unique(f'TECH-{session_id}-{index:02d}', occupied_links)
            pair = local_pair(link_id, path[index])
            pair['quantum']['protocol'] = 'quantum-operation-record-v1'
            # Availability is inherited. A disabled source span is not healed.
            pair['classical']['enabled'] = span.channels['classical']['enabled']
            pair['quantum']['enabled'] = span.channels['quantum']['enabled']
            links.append(SimulatedLink(link_id, path[index - 1], path[index], span.distance_km, .9,
                active=span.active, channels=pair, technology={
                    'session_id': session_id, 'technologies': ['entanglement_swapping', 'quantum_memory', 'purification'],
                    'infrastructure': 'proposed_simulation_only', 'source_span_id': span.link_id,
                    'physical_connection_to_orion': False,
                    'distance_scope': 'existing WGS84 land reference; effective fibre includes route_factor'},))
            link_ids.append(link_id)
        config = ProtocolConfig(session_id, ('entanglement_swapping', 'quantum_memory', 'purification'),
            (path[0], path[-1]), tuple(path), tuple(link_ids), parameters={
                'block_s': .5, 'timeout_s': 3., 'attempt_rate_hz': 250000.,
                'source_depolarization': .04, 'memory_slots': 8, 'coherence_s': 1.,
                'memory_write_efficiency': .7, 'memory_read_efficiency': .7,
                'detector_efficiency': .8, 'attenuation_db_km': .2, 'route_factor': 1.2,
                'timing_jitter_ps': 50., 'phase_sigma_rad': .05, 'classical_jitter_s': 1e-5,
                'bsm_success': .5, 'operation_error': .005, 'purification_operation_error': .01,
                'min_fidelity': .7, 'global_demand_service': True,
            }, parameter_sources=('briegel1998', 'bennett1996', 'engineering_assumptions_v1'))
        sessions.append(config.to_dict())
        enriched_ids.update(path)
        added.append({'session_id': session_id, 'endpoints': list(config.endpoints), 'path': path,
                      'link_ids': link_ids, 'technology': list(config.technologies)})
    sessions, resource_banks, resource_totals, resource_skipped = _simulation_resource_banks(nodes, links, sessions)
    by_origin = {}
    for raw in sessions:
        by_origin.setdefault(raw['path'][0], []).append(raw)
    changed_roles = []
    for index, node in enumerate(nodes):
        telemetry = dict(node.telemetry)
        record = telemetry.get('communicator', {})
        institution = node.node_id in ids
        operator_edited = _preserve_record(record)
        if institution and not operator_edited:
            changed_roles.append({'node_id': node.node_id, 'before': node.role.value, 'after': 'endpoint'})
            node = replace(node, role=NodeRole.ENDPOINT)
        telemetry['operational_capabilities'] = {
            'originate_receive': (institution and not operator_edited) or record.get('network_role') in ('endpoint', 'core'),
            'classical_forwarding': bool(not operator_edited and record.get('integration', {}).get('mode', 'simulated') == 'simulated'),
            'source': 'configured simulator capabilities; no validated institutional hardware',
        }
        logical_caps = (['endpoint_send', 'endpoint_receive'] if institution and not operator_edited else []) + (['classical_forward'] if telemetry['operational_capabilities']['classical_forwarding'] else [])
        telemetry['technology_capabilities'] = list(dict.fromkeys([*telemetry.get('technology_capabilities', ()),
            *(OPTICAL_CAPABILITIES if node.node_id in enriched_ids else ()), *logical_caps]))
        telemetry['technology_sessions'] = by_origin.get(node.node_id, [])
        if node.node_id in resource_banks:
            telemetry['simulation_resource_banks'] = resource_banks[node.node_id]
            telemetry['simulation_resource_totals'] = resource_totals[node.node_id]
        nodes[index] = replace(node, telemetry=telemetry)
    expanded = replace(topology, nodes=tuple(nodes), links=tuple(links))
    # Existing seed-only communicator records must gain the newly configured
    # resources. Confirmed operator revisions remain entirely unchanged.
    from ..control_plane.communicators import seed_record
    refreshed = []
    nodes = list(expanded.nodes)
    for index, node in enumerate(nodes):
        old = node.telemetry.get('communicator')
        if old and not _preserve_record(old):
            seed = seed_record(node, expanded, old['integration']['mode']).to_dict()
            for key in ('device_id', 'name', 'institution', 'manufacturer_model', 'category', 'description', 'integration', 'location', 'orbit', 'operational_state'):
                seed[key] = old[key]
            seed['optical'] = old['optical']
            seed['configuration'] = {**seed['configuration'], **old['configuration']}
            seed['capabilities'] = list(dict.fromkeys([*old['capabilities'], *seed['capabilities']]))
            seed['protocols'] = list(dict.fromkeys([*old['protocols'], *seed['protocols']]))
            seed['technologies'] = list(dict.fromkeys([*old['technologies'], *seed['technologies']]))
            if node.node_id in ids:
                seed['network_role'] = 'core' if old.get('network_role') == 'core' else 'endpoint'
            nodes[index] = replace(node, telemetry={**node.telemetry, 'communicator': seed,
                                  'technology_capabilities': list(seed['capabilities'])})
            refreshed.append(seed)
    return replace(expanded, nodes=tuple(nodes), topology_version=topology.topology_version + 1,
                   updated_at_utc=datetime.now(timezone.utc).isoformat()), {
        'before': topology.to_dict(), 'institutional_endpoints': sorted(ids), 'changed_roles': changed_roles,
        'added_sessions': added, 'impediments': impediments, 'refreshed_seed_communicators': refreshed,
        'simulation_resource_totals': resource_totals, 'resource_allocation_not_inferred': resource_skipped,
        'scope': 'hypothetical operational resource configuration; no deployed global quantum network',
        'physical_action_performed': False,
    }


def upgrade_global_operations_workspace(store, topology):
    if not institutional_ids(topology):
        return topology
    with closing(store._connect()) as conn:
        marker = conn.execute('SELECT 1 FROM topology_events WHERE topology_id=? AND event_kind=?',
                              (topology.topology_id, GLOBAL_OPERATIONS_EVENT)).fetchone()
    if marker:
        return topology
    expanded, payload = expand_global_operations(topology)
    # Topology, seed communicator rows, before-state and marker commit together.
    with store._lock, closing(store._connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        cursor = conn.execute('UPDATE simulated_topologies SET topology_version=?,updated_at_utc=?,state_json=? WHERE topology_id=? AND topology_version=?',
            (expanded.topology_version, expanded.updated_at_utc, json.dumps(expanded.to_dict()), topology.topology_id, topology.topology_version))
        if cursor.rowcount != 1:
            raise ValueError('global operation migration version conflict; operator changes preserved')
        has_records = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='communicators'").fetchone()
        if has_records:
            for record in payload['refreshed_seed_communicators']:
                conn.execute('UPDATE communicators SET revision=?,record_json=? WHERE topology_id=? AND node_id=?',
                             (record['revision'], json.dumps(record), topology.topology_id, record['node_id']))
        conn.execute('INSERT INTO topology_events(event_id,topology_id,version_before,version_after,event_kind,event_sha256,created_at_utc,event_json) VALUES(?,?,?,?,?,?,?,?)',
            store._event(expanded, version_before=topology.topology_version, event_kind=GLOBAL_OPERATIONS_EVENT, payload=payload))
    return expanded
