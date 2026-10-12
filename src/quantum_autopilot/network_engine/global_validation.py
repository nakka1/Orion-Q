"""Reproducible backend acceptance of the institutional global demonstration.

No Qt dependency, external service, physical command, final-key injection or
direct live-state modification. Scenarios use the same engine commands and raw
acquisition/receipt processing as the console. Matrices include every device
and every registered link, including unavailable comparison infrastructure.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import csv
import hashlib
import json
import math
import sys
import time

from ..quantum_protocols.contracts import digest
from ..control_plane.sqlite_transaction import connect

SEED = 20261007

FULL_OPERATIONAL_SCENARIOS = (
    'degraded_isl', 'recover_isl', 'isolate_geostationary_hub',
    'restore_geostationary_hub',
)


def _executable_identity():
    """Bind frozen acceptance to the bytes of the executable being validated."""
    frozen = bool(getattr(sys, 'frozen', False))
    fingerprint = None
    if frozen:
        hasher = hashlib.sha256()
        with Path(sys.executable).open('rb') as executable:
            for chunk in iter(lambda: executable.read(1024 * 1024), b''):
                hasher.update(chunk)
        fingerprint = hasher.hexdigest()
    return {'frozen': frozen, 'executable_sha256': fingerprint}


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def _receipt_chain(rows):
    previous = ''
    for row in rows:
        supplied = row['receipt_sha256']
        body = {key: value for key, value in row.items() if key != 'receipt_sha256'}
        if body['previous_sha256'] != previous or digest(body) != supplied:
            return False
        previous = supplied
    return True


def _resource_summary(technology_sessions, components):
    """Count declared simulation equipment; preserve distinct source-rate units."""
    allocations = [row.get('simulation_resource_allocation', {}) for row in components]
    local = [bank for allocation in allocations for bank in allocation.get('banks', ())]
    keys = ('memory_modes', 'pair_sources', 'pulse_sources', 'optical_terminals')
    return {
        'scope': 'hypothetical dedicated equipment per protocol session',
        'physical_capacity_validated': False, 'shared_hardware_arbitration_implemented': False,
        'protocol_session_banks': len({bank['resource_bank_id'] for bank in local}),
        'local_device_allocations': len(local),
        'totals': {key: sum(bank.get(key, 0) for bank in local) for key in keys},
        'declared_source_rates_hz_by_session': {
            ident: {key: state['config']['parameters'][key] for key in
                    ('attempt_rate_hz', 'pair_source_hz', 'pulse_rate_hz')
                    if key in state['config'].get('parameters', {})}
            for ident, state in technology_sessions.items()
        },
        'rate_scope': 'configured generation attempts, pair emissions and prepared pulses are distinct quantities; rates are not measured throughput or installed hardware capacity',
        'components': [{
            'component': row['component'],
            'public_identifier': row.get('public_identifier', row['component']),
            'totals': row.get('simulation_resource_allocation', {}).get('totals', {}),
        } for row in components if row.get('simulation_resource_allocation', {}).get('banks')],
    }


def _rows(path, table, field, topology_id):
    # Table/field names are internal constants below, never user input.
    with closing(connect(path)) as conn:
        return [json.loads(row[0]) for row in conn.execute(f'SELECT {field} FROM {table} WHERE topology_id=? ORDER BY sequence', (topology_id,))]


def _component_matrix(topology, live, operation_snapshot):
    nodes = {row['component']: row for row in operation_snapshot.get('components', ())}
    lives = {link.link_id: link for link in live.links}
    matrix = []
    for node in topology.nodes:
        row = nodes.get(node.node_id, {})
        record = node.telemetry.get('communicator', {})
        matrix.append({**row, 'component': node.node_id, 'component_kind': 'node/communicator',
            'public_identifier': record.get('device_id', node.node_id),
            'source_location': record.get('orbit') if record.get('physical_type') == 'satellite' else record.get('location'),
            'configuration_record': record,
            'operation_state': row.get('operation_state', 'not_configured'),
            'impediments': row.get('impediments', ['no global operation audit row'])})
    demands = operation_snapshot.get('demands', ())
    for link in topology.links:
        measured = lives.get(link.link_id)
        channels = link.channels or {}
        technology = link.technology or {}
        provenance = measured.provenance if measured else {}
        metrics = provenance.get('protocol_metrics', {})
        impediments = list(provenance.get('status_reasons', ()))
        if not channels:
            impediments.append('both channels and association missing')
        elif not channels['classical']['enabled'] or not channels['quantum']['enabled']:
            impediments.append('required paired channel disabled')
        if not link.active:
            impediments.append('registered link disabled')
        receipt = provenance.get('channels', {})
        if receipt.get('status') != 'received':
            impediments.append('no accepted acquisition receipt yet')
        ids = [demand['demand_id'] for demand in demands if link.link_id in demand.get('route_links', ())]
        matrix.append({'component': link.link_id, 'component_kind': 'link', 'public_identifier': link.link_id,
            'name': f'{link.left_node_id} → {link.right_node_id}',
            'physical_type': 'orbital_optical_link' if set(technology.get('technologies', ())) & {'satellite_doppler', 'satellite_isl'} else 'terrestrial_span',
            'role': 'paired_classical_quantum_channels', 'capabilities': {
                'classical_control': bool(channels and channels['classical']['enabled']),
                'quantum_observation': bool(channels and channels['quantum']['enabled']),
                'technologies': technology.get('technologies', ['BB84 statistical fibre plant']),
                'end_to_end_quantum_relay': 'entanglement_swapping' in technology.get('technologies', ())},
            'configuration': 'complete_paired_channels' if channels else 'incomplete',
            'configuration_record': link.to_dict(), 'source': receipt.get('source', 'none'),
            'links': [link.link_id], 'sessions': [technology['session_id']] if technology else [],
            'routes': ids, 'operation_state': measured.status if measured else 'no_input',
            'geographic_reference_km': link.distance_km, 'received_or_effective_range_km': measured.distance_km if measured else None,
            'source_location': {'endpoints': [link.left_node_id, link.right_node_id]},
            'impediments': list(dict.fromkeys(impediments)),
            'evidence_receipts': [receipt_id for demand in demands if demand['demand_id'] in ids for receipt_id in demand.get('receipt_ids', ())][-20:],
            'metrics': metrics, 'certified_secret_bits': 0, 'physical_actuation': False})
    return matrix


def _export_matrix(destination, matrix):
    _write(destination / 'component-matrix.json', matrix)
    fields = ('component', 'component_kind', 'public_identifier', 'name', 'physical_type', 'role',
              'capabilities', 'configuration', 'simulation_resource_allocation', 'links', 'sessions', 'routes', 'operation_state', 'impediments', 'evidence_receipts')
    with (destination / 'component-matrix.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in matrix:
            writer.writerow({key: json.dumps(row.get(key, ''), ensure_ascii=False) if isinstance(row.get(key), (list, tuple, dict)) else row.get(key, '') for key in fields})
    header = '| Componente | Tipo / papel | Capacidades | Configuração | Enlaces | Rotas | Estado | Impedimentos |\n|---|---|---|---|---|---|---|---|\n'
    def text(value):
        if isinstance(value, dict):
            value = ', '.join(key for key, item in value.items() if item is True) or json.dumps(value, ensure_ascii=False)
        elif isinstance(value, (list, tuple)):
            value = ', '.join(str(item) for item in value)
        return str(value).replace('|', '\\|').replace('\n', ' ')
    rows = []
    for row in matrix:
        rows.append('| ' + ' | '.join(text(value) for value in (
            row['component'], f'{row.get("physical_type", "")} / {row.get("role", "")}', row.get('capabilities', {}),
            row.get('configuration', ''), row.get('links', ()), row.get('routes', ()), row.get('operation_state', ''), row.get('impediments', ()))) + ' |')
    (destination / 'component-matrix.md').write_text('# Matriz completa de componentes e enlaces\n\n' + header + '\n'.join(rows) + '\n', encoding='utf-8')


def _isolated_isl_scenarios(destination, topology, runtime, session_id):
    """Reuse registered devices and protocol, exercising raw Earth-blocked data."""
    from ..quantum_protocols.runtime import ProtocolRuntime
    from ..quantum_protocols.sources import SimulatedAcquisitionSource
    config = runtime.sessions[session_id].config
    selected = set(config.path)
    nodes = []
    for node in topology.nodes:
        if node.node_id not in selected:
            continue
        telemetry = {**node.telemetry, 'technology_sessions': [config.to_dict()] if node.node_id == config.path[0] else []}
        nodes.append(replace(node, telemetry=telemetry))
    mini = replace(topology, topology_id='isolated-isl-validation', nodes=tuple(nodes),
                   links=tuple(link for link in topology.links if link.link_id in config.link_ids))
    driver = ProtocolRuntime(destination / 'isolated-isl.sqlite', mini.topology_id, source=SimulatedAcquisitionSource(SEED))
    nominal = driver.advance(mini, 3.)
    peer = mini.node(config.path[-1])
    record = peer.telemetry['communicator']
    first_longitude = mini.node(config.path[0]).telemetry['communicator']['orbit']['reference_longitude']
    opposed = ((first_longitude + 180. + 180.) % 360.) - 180.
    orbit = {**record['orbit'], 'reference_longitude': opposed,
             'source': 'hypothetical opposed GEO slot injected solely for obstruction validation'}
    blocked_peer = replace(peer, telemetry={**peer.telemetry, 'communicator': {**record, 'orbit': orbit}})
    blocked_topology = replace(mini, nodes=tuple(blocked_peer if node.node_id == peer.node_id else node for node in mini.nodes), topology_version=mini.topology_version + 1)
    blocked = driver.advance(blocked_topology, 6.)
    recovered = driver.advance(mini, 9.)
    driver.export_events(destination / 'isolated-isl-events.jsonl')
    report = {'session_id': session_id, 'nominal': nominal['sessions'][session_id],
              'earth_blocked': blocked['sessions'][session_id], 'recovered': recovered['sessions'][session_id]}
    _write(destination / 'isl-obstruction-scenarios.json', report)
    return report


def _migration_preservation(destination):
    from ..control_plane.topology import SimulatedNodeState, SimulatedTopologyStore
    from .institutional import ensure_institutional_workspace
    from .global_operations_topology import GLOBAL_OPERATIONS_EVENT
    store = SimulatedTopologyStore(destination / 'migration-preservation.sqlite')
    topology, _ = ensure_institutional_workspace(store)
    node = SimulatedNodeState('OPERATOR-PRESERVED', 'endpoint', 0, 'IDLE', telemetry={'operator_note': 'preserved'})
    kept = replace(topology, nodes=(*topology.nodes, node), links=topology.links[:-1], topology_version=topology.topology_version + 1)
    store._replace_existing(kept, expected_version=topology.topology_version, event_kind='validation_operator_edit', payload={})
    reopened, created = ensure_institutional_workspace(store)
    with closing(store._connect()) as conn:
        events = conn.execute('SELECT event_json FROM topology_events WHERE event_kind=?', (GLOBAL_OPERATIONS_EVENT,)).fetchall()
    bodies = [json.loads(row[0]) for row in events]
    before = bodies[0]['payload'].get('before', {}) if bodies else {}
    return {'reopen_preserves_exact_operator_state': reopened == kept and not created,
            'global_migration_once': len(events) == 1,
            'atomic_migration_before_state_retained': bool(before.get('nodes') and before.get('links')),
            'operator_node_present': reopened.node(node.node_id).telemetry == node.telemetry,
            'operator_removed_link_not_restored': len(reopened.links) == len(kept.links)}


def validate_global_network(destination, repository_root=None, steps=10, *, full=True):
    """Write backend evidence, explicitly distinguishing quick and full scope.

    ``full=False`` exercises nominal acquisition, routing, a separate registered
    ISL obstruction/recovery, migration and receipt integrity. Only ``full=True``
    additionally runs degradation and isolation/recovery of the global engine.
    """
    if type(steps) is not int or not 6 <= steps <= 120:
        raise ValueError('steps must be an integer in [6,120]')
    if type(full) is not bool:
        raise ValueError('full must be a boolean')
    target = Path(destination).resolve()
    target.mkdir(parents=True, exist_ok=True)
    # A run-specific journal permits consecutive invocations without overwriting
    # evidence or reopening a previous altered validation scenario.
    run = target / ('run-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    run.mkdir()
    started = time.perf_counter()
    checks = []
    report = {'schema': 'orion.global-network-validation.v1', 'seed': SEED, 'steps_nominal': steps,
              'mode': 'full' if full else 'quick', 'full': full, 'full_campaign': full,
              'frozen': bool(getattr(sys, 'frozen', False)), 'executable_sha256': None,
              'full_operational_scenarios_executed': False,
              'omitted_scenarios': [] if full else list(FULL_OPERATIONAL_SCENARIOS),
              'skipped_scenarios': [] if full else list(FULL_OPERATIONAL_SCENARIOS),
              'output': str(target), 'run_directory': str(run), 'checks': checks, 'passed': False,
              'physical_actuation': False, 'certified_secret_bits': 0,
              'limitations': ['GEO/ISL optical terminals are hypothetical and are not hardware-qualified.',
                  'Measured BBM92 photons are consumed and cannot relay entanglement. The separate orbital-memory-cbpf-bsc-v1 session uses simulated heralded memory captures and BSM; it does not qualify arbitrary global routes or physical relay hardware.',
                  'Simulated protocol sessions have declared dedicated hypothetical resource banks; installed capacity and shared-hardware arbitration are not validated.',
                  'Classical diagnostic probes do not carry application payloads or deliver usable QKD keys.',
                  'Classical local forwarding assumes 1 Mbps, declared latency/jitter/loss; RF/optical classical analog budgets are not mission-qualified.']}
    def check(name, passed, detail=None):
        checks.append({'name': name, 'passed': bool(passed), 'detail': detail})
    try:
        report.update(_executable_identity())
        from ..qkd.dashboard_api import demo_service
        from .global_operations_topology import institutional_ids, operation_capabilities
        from ..quantum_protocols.orbits import position
        service = demo_service(repository_root=repository_root, state_root=run / 'state',
            preset='brazil-institutions-v1', seed=SEED, scenario='quiet')
        engine = service.network_engine
        timeline = []
        def step(number, phase):
            live = None
            for _ in range(number):
                live = engine.step()
                operation = live.technologies.get('global_operations', {})
                timeline.append({'phase': phase, 'tick': live.tick, 'clock_s': live.technologies['clock_s'],
                    'state_counts': operation.get('state_counts', {}),
                    'successful_classical': operation.get('successful_classical_probes', 0),
                    'successful_protocol': operation.get('successful_protocol_services', 0),
                    'last_step_ms': engine.timings.get('last_step_ms')})
            return live
        nominal = step(steps, 'nominal')
        topology = engine.topology()
        snapshot = nominal.technologies.get('global_operations', {})
        identifiers = institutional_ids(topology)
        devices = {node.node_id: node.telemetry.get('communicator', {}) for node in topology.nodes}
        control = [demand for demand in snapshot.get('demands', ()) if demand['operation'] == 'classical_control_probe']
        protocol = [demand for demand in snapshot.get('demands', ()) if demand['operation'] in ('entanglement_pair', 'qkd_estimate', 'optical_pair_measurement')]
        check('all_components_and_paired_channels_registered', all(devices.values()) and all(link.channels for link in topology.links),
              {'nodes': len(topology.nodes), 'communicators': len(devices), 'links': len(topology.links)})
        check('all_institutions_endpoint_send_receive', all(topology.node(ident).role.value == 'endpoint' and operation_capabilities(topology.node(ident))['originate_receive'] for ident in identifiers), list(identifiers))
        check('every_institution_originates_and_receives_classical_demand', all(any(demand['a'] == ident for demand in control) and any(demand['b'] == ident for demand in control) for ident in identifiers))
        check('every_institution_has_received_end_to_end_classical_probe', all(any(demand['successful_attempts'] > 0 and ident in (demand['a'], demand['b']) for demand in control) for ident in identifiers),
              {demand['demand_id']: demand['successful_attempts'] for demand in control})
        check('actual_registered_multihop_and_inter_region_routes', any(demand['successful_attempts'] and len(demand.get('route_links', ())) > 2 for demand in control) and any(demand['successful_attempts'] and len([node_id for node_id in demand.get('route', ()) if devices[node_id].get('physical_type') == 'satellite']) > 1 for demand in control))
        check('local_quantum_services_have_fresh_protocol_receipts', any(demand['successful_attempts'] and demand['operation'] == 'entanglement_pair' for demand in protocol) and any(demand['successful_attempts'] and demand['operation'] == 'optical_pair_measurement' for demand in protocol))
        check('no_incompatible_protocol_configuration', not any(session.status == 'incompatible' for session in engine.technologies.sessions.values()),
              {key: session.reason for key, session in engine.technologies.sessions.items() if session.status == 'incompatible'})
        check('unimplemented_quantum_relay_is_reported_without_success', all(not demand['successful_attempts'] for demand in snapshot['demands'] if demand['operation'] == 'global_entanglement' and demand.get('requires_infrastructure')) and any('orbital coherent' in ' '.join(demand.get('requires_infrastructure', ())) for demand in snapshot['demands']))
        check('old_leo_and_meo_comparison_satellites_preserved', all(ident in devices for ident in ('SAT-RIO-SATELLITE', 'SAT-EUROPE-SATELLITE', 'SAT-US-SATELLITE', 'SAT-ATLANTIC-SATELLITE', 'SAT-ATLANTIC-MEO')))
        geos = {ident: record for ident, record in devices.items() if record.get('physical_type') == 'satellite' and record.get('orbit', {}).get('model') == 'geostationary_reference'}
        drifts = {}
        for ident, record in geos.items():
            first, last = position(record['orbit'], 0.), position(record['orbit'], 86164.0905)
            drifts[ident] = math.sqrt(sum((a - b)**2 for a, b in zip(first['ecef_km'], last['ecef_km'])))
        check('explicit_simulated_geo_positions_fixed_in_ecef', len(geos) >= 4 and all(value < 1e-6 for value in drifts.values()), drifts)
        isl_ids = [key for key, session in engine.technologies.sessions.items() if 'satellite_isl' in session.config.technologies]
        isl_evidence = {key: engine.technologies.sessions[key].metrics for key in isl_ids}
        check('registered_isl_uses_received_housekeeping_and_tdc', len(isl_ids) >= 1 and all(value.get('satellite_housekeeping') and value.get('isl_geometry') for value in isl_evidence.values()),
              {key: {'coincidences': value.get('coincidences'), 'range_km': value.get('isl_geometry', {}).get('range_km'), 'available': value.get('available')} for key, value in isl_evidence.items()})
        nominal_matrix = _component_matrix(topology, nominal, snapshot)
        banks = {session.config.session_id: session.config.parameters.get('resource_bank_id') for session in engine.technologies.sessions.values()}
        check('simulated_resources_explicitly_dedicated_not_shared_hardware',
              all(bank == f'sim-resource:{ident}' for ident, bank in banks.items())
              and all(session.config.parameters.get('resource_bank_scope') == 'dedicated_per_session' for session in engine.technologies.sessions.values())
              and all(row.get('simulation_resource_allocation', {}).get('physical_capacity_validated') is False
                      and row.get('simulation_resource_allocation', {}).get('shared_hardware_arbitration_implemented') is False
                      for row in nominal_matrix if row['component_kind'] == 'node/communicator'),
              {'banks': banks, 'scope': 'dedicated hypothetical equipment per protocol session; runtime memory quotas remain per session'})
        _export_matrix(target, nominal_matrix)
        engine.global_operations.export(run / 'nominal-operations')
        _write(run / 'nominal-live.json', nominal.to_dict())
        report['nominal'] = {'nodes': len(topology.nodes), 'links': len(topology.links), 'institutions': list(identifiers),
            'sessions': len(engine.technologies.sessions), 'demands': len(snapshot['demands']),
            'successful_classical_probes': snapshot['successful_classical_probes'],
            'successful_protocol_services': snapshot['successful_protocol_services'],
            'component_matrix_rows': len(nominal_matrix),
            'simulation_resource_allocation': _resource_summary(nominal.technologies['sessions'], snapshot['components'])}
        selected_isl = isl_ids[0]
        physical_cases = _isolated_isl_scenarios(run, topology, engine.technologies, selected_isl)
        original = physical_cases['nominal']['metrics']; blocked = physical_cases['earth_blocked']['metrics']; recovered = physical_cases['recovered']['metrics']
        check('isl_earth_obstruction_blocks_received_science', original.get('available') and original.get('coincidences', 0) > 0 and blocked.get('isl_geometry', {}).get('earth_obstructed') and blocked.get('coincidences', 0) == 0 and blocked.get('available') is False)
        check('isl_recovery_requires_new_pipeline_receipts', recovered.get('available') and recovered.get('coincidences', 0) > 0 and recovered.get('last_observed_at_s', -1.) > blocked.get('last_observed_at_s', 0.))
        recovered_live = nominal
        final_snapshot = snapshot
        degraded_metrics = restored_metrics = satellite = None
        crossing = {}
        if full:
            # Explicit degraded protocol configuration: acquisition environment
            # changes raw counts/budgets instead of patching final UI metrics.
            engine.command('configure_technology', {'session_id': selected_isl, 'scenario': 'degraded'})
            degraded = step(4, 'degraded_isl')
            degraded_metrics = degraded.technologies['sessions'][selected_isl]['metrics']
            baseline_metrics = isl_evidence[selected_isl]
            transmission_before = baseline_metrics.get('isl_geometry', {}).get('channel_transmission')
            transmission_after = degraded_metrics.get('isl_geometry', {}).get('channel_transmission')
            check('degradation_changes_received_optical_budget', transmission_before is not None and transmission_after is not None and transmission_after < transmission_before,
                  {'before': transmission_before, 'after': transmission_after, 'coincidences': degraded_metrics.get('coincidences')})
            engine.command('configure_technology', {'session_id': selected_isl, 'scenario': 'nominal'})
            step(4, 'recover_isl')
            restored_metrics = engine.technologies.sessions[selected_isl].metrics
            check('degraded_isl_returns_after_reacquisition', restored_metrics.get('available') and restored_metrics.get('last_observed_at_s', 0.) > degraded_metrics.get('last_observed_at_s', 0.) and restored_metrics.get('isl_geometry', {}).get('channel_transmission', 0.) > (transmission_after or 0.))
            satellite = next(ident for ident in geos if 'AMERICAS' in ident)
            before_cut = engine.global_operations.snapshot()
            crossing = {demand['demand_id']: demand['successful_attempts'] for demand in before_cut['demands'] if demand['operation'] == 'classical_control_probe' and satellite in demand.get('route', ()) and len([ident for ident in demand.get('route', ()) if ident in geos]) > 1}
            last_observed = {key: session.metrics.get('last_observed_at_s') for key, session in engine.technologies.sessions.items() if satellite in session.config.path}
            engine.command('set_node_isolation', {'node_id': satellite, 'isolated': True})
            cut = step(8, 'isolate_geostationary_hub')
            cut_ops = cut.technologies['global_operations']
            cut_demands = {demand['demand_id']: demand for demand in cut_ops['demands']}
            check('isolation_prevents_fictitious_inter_region_delivery', bool(crossing) and all(cut_demands[key]['successful_attempts'] == count for key, count in crossing.items()), crossing)
            check('isolated_satellite_sessions_do_not_publish_new_observations', all(engine.technologies.sessions[key].metrics.get('last_observed_at_s') == observed for key, observed in last_observed.items()))
            check('unavailable_causes_and_retry_attempts_visible', any(cut_demands[key]['state'] == 'unavailable' and cut_demands[key]['failed_attempts'] > 0 for key in crossing))
            isolated_row = next(row for row in cut_ops['components'] if row['component'] == satellite)
            check('isolated_component_not_reported_available_from_old_observations', isolated_row['operation_state'] == 'unavailable' and not isolated_row['measured_acquisition_present'], isolated_row['operation_state'])
            engine.command('set_node_isolation', {'node_id': satellite, 'isolated': False})
            recovered_live = step(8, 'restore_geostationary_hub')
            final_snapshot = recovered_live.technologies['global_operations']
            final_demands = {demand['demand_id']: demand for demand in final_snapshot['demands']}
            check('restored_global_control_routes_use_new_receipts', all(final_demands[key]['successful_attempts'] > count for key, count in crossing.items()))
            check('restored_satellite_science_has_fresh_observations', all(engine.technologies.sessions[key].metrics.get('last_observed_at_s', -1.) > (observed or 0.) for key, observed in last_observed.items()))
            restored_row = next(row for row in final_snapshot['components'] if row['component'] == satellite)
            check('restored_component_operational_state_uses_fresh_available_acquisition', restored_row['measured_acquisition_present'] and restored_row['operation_state'] not in ('unavailable', 'not_configured'), restored_row['operation_state'])
            _write(run / 'degraded-live.json', degraded.to_dict())
            _write(run / 'isolated-live.json', cut.to_dict())
            _write(run / 'recovered-live.json', recovered_live.to_dict())
            report['full_operational_scenarios_executed'] = True
        engine.global_operations.export(run / 'final-operations')
        _export_matrix(run / 'final-operations', _component_matrix(engine.topology(), recovered_live, final_snapshot))
        events = _rows(service.control_journal_path, 'global_operation_events', 'record_json', engine.topology_id)
        technology_events = _rows(service.control_journal_path, 'technology_events', 'payload_json', engine.topology_id)
        results = [event['detail'] for event in events if event['event'] == 'protocol_demand_result']
        quantum_receipts = [item['technology_receipt'] for item in results]
        check('global_and_protocol_receipt_hash_chains_valid', _receipt_chain(events) and _receipt_chain(technology_events), {'global_events': len(events), 'protocol_events': len(technology_events)})
        check('quantum_resource_receipt_not_duplicated_between_demands', len(quantum_receipts) == len(set(quantum_receipts)))
        sent = {event['detail']['command_id']: event for event in events if event['event'] == 'classical_probe_sent'}
        received = [event for event in events if event['event'] == 'classical_probe_received']
        check('every_classical_received_hop_has_serialized_sent_envelope', all(event['detail']['command_id'] in sent and sent[event['detail']['command_id']]['at_s'] <= event['at_s'] and event['detail']['envelope_sha256'] == sent[event['detail']['command_id']]['detail']['envelope_sha256'] for event in received), {'received_hops': len(received)})
        check('no_physical_actuation_or_certified_secret_keys', not recovered_live.policy['ml_control_enabled'] and all(not event['physical_actuation'] and event['certified_secret_bits'] == 0 for event in events) and all(item['certified_secret_bits'] == 0 and not item['application_payload_forwarded'] for item in results))
        check('all_nodes_and_links_in_delivered_matrix', len(nominal_matrix) == len(topology.nodes) + len(topology.links) and {row['component'] for row in nominal_matrix} == {node.node_id for node in topology.nodes} | {link.link_id for link in topology.links})
        preservation = _migration_preservation(run)
        for key, value in preservation.items():
            check(key, value)
        report['evidence'] = {'journal': str(service.control_journal_path), 'global_events': len(events),
            'protocol_events': len(technology_events), 'classical_received_hops': len(received),
            'protocol_results': len(results), 'unique_quantum_resource_receipts': len(set(quantum_receipts)),
            'matrix_json': str(target / 'component-matrix.json'), 'matrix_csv': str(target / 'component-matrix.csv'),
            'matrix_markdown': str(target / 'component-matrix.md'),
            'final_successful_classical_probes': final_snapshot['successful_classical_probes'],
            'final_successful_protocol_services': final_snapshot['successful_protocol_services']}
        report['scenarios'] = {'nominal': True, 'registered_isl_obstruction_recovery': True,
                              'degraded_isl': degraded_metrics, 'recovered_isl': restored_metrics,
                              'isolated_satellite': satellite, 'isolated_cross_region_demands': crossing,
                              'atomic_migration': preservation, 'isl_obstruction_file': str(run / 'isl-obstruction-scenarios.json')}
        resource_summary = {'nominal': report['nominal']['simulation_resource_allocation'],
            'recovered' if full else 'final_nominal': _resource_summary(recovered_live.technologies['sessions'], final_snapshot['components'])}
        _write(target / 'resource-summary.json', resource_summary)
        report['evidence']['resource_summary_json'] = str(target / 'resource-summary.json')
        _write(run / 'timeline.json', timeline)
        service.network_engine.close_ogs()
    except Exception as error:
        import traceback
        check('validation_completed_without_exception', False, {'exception': f'{type(error).__name__}: {error}', 'traceback': traceback.format_exc()})
    report['elapsed_s'] = time.perf_counter() - started
    report['passed'] = bool(checks) and all(item['passed'] for item in checks)
    report['failed_checks'] = [item for item in checks if not item['passed']]
    _write(target / 'report.json', report)
    lines = ['# Validação da rede global de demonstração', '',
             f"Resultado: {'APROVADO' if report['passed'] else 'FALHAS ENCONTRADAS'}; modo {'completo' if full else 'rápido (escopo parcial)'}; semente {SEED}; {report['elapsed_s']:.2f} s.", '',
             'Cenários omitidos: ' + (', '.join(report['skipped_scenarios']) or 'nenhum') + '.', '',
             '| Caso | Resultado | Evidência / causa |', '|---|---|---|']
    for item in checks:
        detail = '' if item.get('detail') is None else json.dumps(item['detail'], ensure_ascii=False)
        lines.append(f"| {item['name']} | {'APROVADO' if item['passed'] else 'FALHOU'} | {detail.replace('|', ' / ').replace(chr(10), ' ')} |")
    lines += ['', 'O inventário completo, com canais, rotas e impedimentos, está em component-matrix.csv, component-matrix.json e component-matrix.md.', '', '## Limitações', '', *['- ' + text for text in report['limitations']], '']
    (target / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    return report
