"""Freeze and execute a complete-census, native SeQUeNCe network campaign.

The complete hybrid quantum network is never inferred from compatible subnetworks.
Native classical receipts traverse OrionQ's unchanged operational pipeline.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import gzip
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
DEFAULT_PROTOCOL = ROOT / 'configs/qualification/sequence_network_v1.json'
SOURCE_FILES = (
    'tools/run_sequence_network_campaign.py', 'tools/sequence_network_inventory.py',
    'tools/sequence_network_backend.py', 'tools/sequence_network_source.py', 'tools/sequence_network_worker.py',
    'tests/test_sequence_network_campaign.py', 'tests/test_sequence_network_inventory.py',
    'tests/test_sequence_network_backend.py', 'tests/test_sequence_network_source.py',
    'configs/qualification/sequence_network_v1.json', 'docs/product/orionq_sequence_network.md', 'pyproject.toml',
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def write(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False)
        stream.write('\n')


def inventory():
    names = set(SOURCE_FILES)
    names.update(p.relative_to(ROOT).as_posix() for p in (ROOT / 'src').rglob('*')
                 if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo'))
    return {name: sha(ROOT / name) for name in sorted(names)}


def check_freeze(directory):
    frozen = load(directory / 'freeze.json')
    if frozen['sources'] != inventory() or frozen['protocol_sha256'] != sha(directory / 'protocol.json'):
        raise ValueError('frozen sources or criteria changed; select a new campaign directory')
    for name, expected in frozen['inputs'].items():
        if sha(directory / name) != expected:
            raise ValueError(f'frozen input changed: {name}')
    return frozen


def reference_requests(base):
    """Explicit bounded alternative experiment, preserving a real campus chain.

    These reference settings qualify protocol behavior, not original throughput.
    Actual mapped full-topology requests remain untouched and run separately.
    """
    row = next(d for d in base['demands'] if 'RIO-ENTANGLEMENT' in d['id'])
    source, target = row['source'], row['target']
    links = [link for link in base['links'] if link.get('resource_bank_id') == 'sim-resource:RIO-ENTANGLEMENT']
    if not links:
        # Bank IDs are part of the exported node identity even with older link metadata.
        links = [link for link in base['links'] if '::RIO-ENTANGLEMENT' in link['a'] and '::RIO-ENTANGLEMENT' in link['b']]
    ids = {link[key] for link in links for key in ('a', 'b')}
    if source not in ids or target not in ids:
        raise ValueError('registered Rio bank mapping missing')
    nodes = [{**node, 'memory_count': 8, 'raw_fidelity': .95, 'memory_frequency_hz': 10000.,
              'memory_coherence_s': 1., 'memory_efficiency': .8, 'gate_fidelity': 1., 'measurement_fidelity': 1.}
             for node in base['nodes'] if node['id'] in ids]
    selected_links = [{**link, 'attenuation_db_km': .2, 'insertion_loss_db': 0., 'enabled': True,
                       'classical_enabled': True} for link in links]
    ref = {**base, 'scenario_id': 'reference_nominal', 'nodes': nodes, 'links': selected_links,
           'duration_s': 1.5, 'detector': {'efficiency': .8, 'dark_count_hz': 0., 'time_resolution_ps': 100,
                                         'count_rate_hz': 5e7},
           'demands': [{'id': 'reference-pair', 'source': source, 'target': target, 'request_s': 0.,
                        'start_s': .01, 'duration_s': 1.2, 'memory_count': 2, 'target_fidelity': .7}],
           'disruptions': [], 'reference_scope': 'altered bounded Rio experiment, not nominal product parity'}
    yield ref
    first_span = selected_links[0]
    yield {**ref, 'scenario_id': 'reference_purification', 'links': [first_span],
           'nodes': [{**node, 'raw_fidelity': .8} for node in nodes if node['id'] in (first_span['a'], first_span['b'])],
           'demands': [{**ref['demands'][0], 'id': 'purified-pair', 'source': first_span['a'],
                        'target': first_span['b'], 'target_fidelity': .83, 'memory_count': 4}]}
    yield {**ref, 'scenario_id': 'reference_zero_efficiency', 'detector': {**ref['detector'], 'efficiency': 0.}}
    yield {**ref, 'scenario_id': 'reference_classical_blocked',
           'links': [{**link, 'classical_enabled': False} for link in selected_links]}
    yield {**ref, 'scenario_id': 'reference_contention_release', 'demands': [
        {**ref['demands'][0], 'id': 'reserve-first', 'memory_count': 4, 'start_s': .01, 'duration_s': .4},
        {**ref['demands'][0], 'id': 'reserve-overlap', 'memory_count': 4, 'request_s': .001, 'start_s': .02, 'duration_s': .2},
        {**ref['demands'][0], 'id': 'reserve-after-release', 'memory_count': 2, 'request_s': .45, 'start_s': .47, 'duration_s': .8},
    ]}
    yield {**ref, 'scenario_id': 'reference_outage_recovery', 'disruptions': [
        {'node_id': source, 'time_s': .4, 'down': True}, {'node_id': source, 'time_s': .7, 'down': False}],
        'demands': [{**ref['demands'][0], 'id': 'pre-outage', 'duration_s': .35},
                    {**ref['demands'][0], 'id': 'during-outage', 'request_s': .45, 'start_s': .48, 'duration_s': .12},
                    {**ref['demands'][0], 'id': 'post-recovery', 'request_s': .72, 'start_s': .75, 'duration_s': .6}]}


def prepare(directory, protocol=DEFAULT_PROTOCOL):
    from sequence_network_backend import validate_payload
    from sequence_network_inventory import build_inventory, build_native_request, endpoint_route_census
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT):
        raise ValueError('campaign evidence must stay in the workspace')
    plan = load(protocol)
    if plan.get('schema') != 'orionq.sequence-network-protocol.v1':
        raise ValueError('unsupported protocol')
    seeds = plan['seeds']
    if len(seeds) < 2 or len(seeds) != len(set(seeds)) or any(type(s) is not int or s < 0 for s in seeds):
        raise ValueError('distinct non-negative seeds required')
    before = inventory()
    directory.mkdir(parents=True, exist_ok=False)
    census = build_inventory(directory / 'inventory-state', repository_root=ROOT, seed=seeds[0])
    write(directory / 'census.json', census)
    expected_counts = plan['expected_inventory']
    if any(census['counts'].get(key) != count for key, count in expected_counts.items()):
        raise ValueError('production inventory differs from the prospective declared scope')
    write(directory / 'endpoint-routes.json', endpoint_route_census(census))
    write(directory / 'protocol.json', plan)
    inputs = {name: sha(directory / name) for name in ('census.json', 'endpoint-routes.json')}
    for seed in seeds:
        base = build_native_request(census, seed=seed, duration_s=plan['native_duration_s'],
                                    demand_start_s=plan['native_demand_start_s'],
                                    demand_duration_s=plan['native_active_duration_s'], demand_memory_count=plan['native_request_memories'])
        base['scenario_id'] = 'full_terrestrial_banks'
        base['formalism'] = plan['native_formalism']
        for request in (base, *reference_requests(base)):
            validate_payload(request)
            name = f"input-{seed}-{request['scenario_id']}.json"
            write(directory / name, request)
            inputs[name] = sha(directory / name)
    if before != inventory():
        raise ValueError('sources changed during preparation')
    frozen = {'schema': 'orionq.sequence-network-freeze.v1', 'prepared_utc': datetime.now(timezone.utc).isoformat(),
              'sources': before, 'source_manifest_sha256': digest(before), 'inputs': inputs,
              'protocol_sha256': sha(directory / 'protocol.json'), 'criteria_before_execution': True,
              'physical_action_performed': False}
    write(directory / 'freeze.json', frozen)
    return frozen


def native_checks(result, request, directory):
    from sequence_network_source import PIN
    if (result.get('schema') != 'orionq.sequence-network-output.v1' or result.get('backend') != 'SeQUeNCe'
            or result.get('seed') != request['seed'] or result.get('scenario_id') != request['scenario_id']
            or result.get('topology_sha256') != request['topology_sha256']):
        raise ValueError('native result not bound to frozen request')
    if result.get('request_sha256') != digest(request):
        raise ValueError('native result request hash mismatch')
    if result.get('state_formalism') != request.get('formalism', 'density_matrix'):
        raise ValueError('native quantum formalism mismatch')
    provenance = result.get('provenance', {})
    if provenance.get('commit') != PIN or provenance.get('product_imports') != [] or provenance.get('shims') != [] or not provenance.get('source_sha256'):
        raise ValueError('native framework provenance absent')
    if provenance.get('physical_action_performed') is not False:
        raise ValueError('unexpected physical action claim')
    checks = []
    def check(name, accepted, detail=None):
        checks.append({'name': name, 'passed': bool(accepted), 'detail': detail})
    check('every_native_node_in_census', len(result['node_census']) == len(request['nodes'])
          and {n['id'] for n in result['node_census']} == {n['id'] for n in request['nodes']})
    check('every_native_link_in_census', len(result['link_census']) == len(request['links'])
          and {n['id'] for n in result['link_census']} == {n['id'] for n in request['links']})
    check('every_native_demand_has_result', len(result['demands']) == len(request['demands'])
          and {n['demand_id'] for n in result['demands']} == {n['id'] for n in request['demands']})
    event_path = directory / result['event_log']['path']
    if sha(event_path) != result['event_log']['sha256']:
        raise ValueError('native event log hash mismatch')
    with gzip.open(event_path, 'rt', encoding='utf-8') as stream:
        events = [json.loads(line) for line in stream]
    check('full_native_event_log_preserved', len(events) == result['event_log']['count'])
    check('native_clock_monotonic', all(a['time_ps'] <= b['time_ps'] for a, b in zip(events, events[1:])))
    check('classical_native_delay_causal', result['classical_receipts']['max_delay_error_ps'] <= 2)
    paths = {(link['a'], link['b']) for link in request['links'] if link.get('enabled', True) and link.get('classical_enabled', True)}
    paths |= {(b, a) for a, b in paths}
    by_id = {d['id']: d for d in request['demands']}
    for demand in result['demands']:
        route = demand.get('path', [])
        check(f"{demand['demand_id']}:native_path_only_configured_links",
              all((a, b) in paths for a, b in zip(route, route[1:])))
        config = by_id[demand['demand_id']]
        pairs = demand['delivery_observations']
        check(f"{demand['demand_id']}:approved_route_matches_endpoints", demand['status'] != 'approved'
              or bool(route) and route[0] == config['source'] and route[-1] == config['target'])
        check(f"{demand['demand_id']}:delivery_identity_time_fidelity",
              len(pairs) == demand['delivered_pairs'] and (not pairs or demand['status'] == 'approved') and all(
                  p['remote_node'] == config['target'] and p['path'][0] == config['source']
                  and p['reservation_identity'] == demand['identity']
                  and config['start_s'] <= p['time_ps'] / 1e12 <= config['start_s'] + config['duration_s']
                  and math.isfinite(p['fidelity']) and config['target_fidelity'] <= p['fidelity'] <= 1 + 1e-12 for p in pairs))
        check(f"{demand['demand_id']}:native_metadata_and_state_explicit", all(
              p.get('native_metadata_fidelity') == p['fidelity']
              and p.get('retained_density', {}).get('status') in ('observed', 'not_demonstrated') for p in pairs))
    rows = {d['demand_id']: d for d in result['demands']}
    scenario = request['scenario_id']
    delivered = sum(d['delivered_pairs'] for d in rows.values())
    if scenario == 'full_terrestrial_banks':
        check('all_compatible_banks_instantiated', all(n['status'] == 'native_instantiated' for n in result['node_census'])
              and all(n['status'] == 'native_instantiated' for n in result['link_census']))
        check('all_compatible_services_reservation_executed', len(rows) == 13 and all(d['status'] == 'approved'
              and d['path'][0] == d['source'] and d['path'][-1] == d['target'] for d in rows.values()))
        check('actual_photon_and_generation_activity', result['event_counts'].get('photon_sent', 0) > 0
              and result['event_counts'].get('photon_received', 0) > 0 and result['native_metrics'].get('EG_SUCCESS', 0) > 0)
    elif scenario == 'reference_nominal':
        check('native_multihop_pair_observed', delivered > 0 and any(len(d['path']) >= 3 for d in rows.values()))
        check('native_generation_and_swapping_observed', bool(result['native_metrics'].get('EG_SUCCESS', 0)) and bool(result['native_metrics'].get('ES_SUCCESS', 0)))
    elif scenario == 'reference_purification':
        check('native_purification_retained_pair_observed', delivered > 0 and result['native_metrics'].get('EP_SUCCESS', 0) > 0)
        check('native_purification_generated_input_resources', result['native_metrics'].get('EG_SUCCESS', 0) >= 2)
        consumed = result.get('purification_consumption_records', [])
        check('native_purification_two_distinct_input_resources', bool(consumed) and all(
              r['input_memory_count'] == 2 and r['kept_memory'] != r['measured_memory']
              and r['requested_measured_state'] == 'RAW' for r in consumed))
    elif scenario == 'reference_zero_efficiency':
        check('zero_detectors_do_not_deliver_pairs', delivered == 0)
        check('zero_detectors_still_process_reservation', any(d['status'] == 'approved' for d in rows.values()))
    elif scenario == 'reference_classical_blocked':
        check('blocked_classical_channel_no_pair', delivered == 0 and all(d['status'] == 'blocked_no_native_route' for d in rows.values()))
    elif scenario == 'reference_contention_release':
        check('overlap_rejected_resource_recovered', rows['reserve-first']['status'] == 'approved'
              and rows['reserve-overlap']['status'] == 'rejected' and rows['reserve-after-release']['status'] == 'approved')
        check('released_memories_deliver_fresh_pair', rows['reserve-after-release']['delivered_pairs'] > 0)
    elif scenario == 'reference_outage_recovery':
        check('down_node_does_not_deliver', rows['during-outage']['delivered_pairs'] == 0)
        check('new_request_after_recovery_delivers', rows['post-recovery']['delivered_pairs'] > 0)
        check('native_down_up_events_preserved', sum(e['event'] == 'node_status_changed' for e in events) == 2)
    check('no_hardware_or_secret_key_claim', result.get('complete_network_validation') is False
          and result.get('hardware_qualified') is False and result.get('certified_secret_bits') == 0)
    return checks


def execute_orion(directory, python, source_path, seed, plan):
    from quantum_autopilot.control_plane.sqlite_transaction import connect
    from quantum_autopilot.network_engine.global_validation import _receipt_chain
    from quantum_autopilot.qkd.dashboard_api import demo_service
    from sequence_network_source import SequenceClassicalSource, SequenceWorker
    target = directory / f'orion-network-{seed}'
    target.mkdir()
    service = demo_service(repository_root=ROOT, state_root=target / 'state', preset='brazil-institutions-v1', seed=seed, scenario='quiet')
    engine = service.network_engine
    worker = SequenceWorker(python, source_path, target / 'sequence-worker', timeout_s=plan['worker_deadline_s'])
    source = SequenceClassicalSource(worker, target / 'native-classical.jsonl', seed=seed)
    engine.global_operations.source = source
    timeline, phases, checks = [], {}, []
    def check(name, accepted, detail=None):
        checks.append({'name': name, 'passed': bool(accepted), 'detail': detail})
    def step(phase, n):
        source.phase = phase
        for _ in range(n):
            live = engine.step()
            snap = live.technologies['global_operations']
            timeline.append({'phase': phase, 'tick': live.tick, 'clock_s': live.technologies['clock_s'],
                             'successful_classical_probes': snap['successful_classical_probes'],
                             'successful_protocol_services': snap['successful_protocol_services'],
                             'state_counts': snap['state_counts']})
        phases[phase] = snap
        destination = target / phase
        engine.global_operations.export(destination)
        write(destination / 'live.json', live.to_dict())
        return snap
    def controls(snap):
        return {d['demand_id']: d for d in snap['demands'] if d['operation'] == 'classical_control_probe'}
    try:
        nominal = step('nominal', plan['nominal_ticks'])
        baseline = controls(nominal)
        check('every_institution_receives_native_control_probe', len(baseline) == 20 and all(d['successful_attempts'] > 0 for d in baseline.values()),
              {key: d['successful_attempts'] for key, d in baseline.items()})
        check('native_source_configured_without_core_changes', nominal['source']['source_id'] == source.source_id)
        check('both_terrestrial_and_satellite_multihop_routes', any(len(d['route_links']) > 2 for d in baseline.values())
              and any(sum('GEO-' in ident for ident in d['route']) > 1 for d in baseline.values()))
        source.external_loss_probability = 1.
        loss = step('external_delivery_loss', plan['loss_ticks'])
        loss_rows = controls(loss)
        check('delivery_loss_recorded_as_failure', all(loss_rows[k]['failed_attempts'] > d['failed_attempts'] for k, d in baseline.items()))
        source.external_loss_probability = 0.
        source.external_timeout = True
        timeout = step('external_delivery_timeout', plan['timeout_ticks'])
        timeout_rows = controls(timeout)
        check('timeout_does_not_fabricate_success', all(timeout_rows[k]['successful_attempts'] == d['successful_attempts'] for k, d in loss_rows.items()))
        check('timeout_failures_visible', any('timeout' in d['reason'] for d in timeout_rows.values()))
        source.external_timeout = False
        recovered = step('source_recovered', plan['recovery_ticks'])
        recovery_rows = controls(recovered)
        check('source_recovery_requires_new_native_receipts', all(recovery_rows[k]['successful_attempts'] > d['successful_attempts'] for k, d in timeout_rows.items()))
        satellite = 'GEO-AMERICAS-SIM'
        crossing = {k: d['successful_attempts'] for k, d in recovery_rows.items() if satellite in d.get('route', ())}
        engine.command('set_node_isolation', {'node_id': satellite, 'isolated': True})
        isolated = step('satellite_isolated', plan['isolation_ticks'])
        isolated_rows = controls(isolated)
        check('isolated_satellite_routes_blocked_or_rerouted', bool(crossing) and all(
            isolated_rows[k]['successful_attempts'] == n or satellite not in isolated_rows[k].get('route', ())
            for k, n in crossing.items()), crossing)
        engine.command('set_node_isolation', {'node_id': satellite, 'isolated': False})
        restored = step('satellite_restored', plan['restore_ticks'])
        restored_rows = controls(restored)
        check('satellite_restoration_reacquires_route', all(restored_rows[k]['successful_attempts'] > n for k, n in crossing.items()))
        with closing(connect(service.control_journal_path)) as conn:
            global_events = [json.loads(r[0]) for r in conn.execute('SELECT record_json FROM global_operation_events WHERE topology_id=? ORDER BY sequence', (engine.topology_id,))]
            quantum_events = [json.loads(r[0]) for r in conn.execute('SELECT payload_json FROM technology_events WHERE topology_id=? ORDER BY sequence', (engine.topology_id,))]
        sent = {e['detail']['command_id']: e for e in global_events if e['event'] == 'classical_probe_sent'}
        received = [e for e in global_events if e['event'] == 'classical_probe_received']
        native = [json.loads(line) for line in (target / 'native-classical.jsonl').read_text(encoding='utf-8').splitlines()]
        check('isolated_node_never_used_by_new_native_hop', not any(
            r['phase'] == 'satellite_isolated' and satellite in (r['request']['sender'], r['request']['receiver']) for r in native))
        restore_command_ids = {r['request']['command_id'] for r in native if r['phase'] == 'satellite_restored' and r['admitted_to_orion']}
        check('satellite_restoration_has_fresh_native_callback', bool(restore_command_ids) and any(
              e['detail']['command_id'] in restore_command_ids for e in received))
        by_command = {r['request']['command_id']: r for r in native}
        check('every_orion_receipt_bound_to_native_callback', bool(received) and all(
            e['detail']['command_id'] in by_command and e['detail']['source_id'] == source.source_id
            and by_command[e['detail']['command_id']]['admitted_to_orion']
            and abs(e['at_s'] - by_command[e['detail']['command_id']]['native']['received_at_s']) <= 2e-12
            and sent[e['detail']['command_id']]['at_s'] <= e['at_s'] for e in received))
        check('global_and_quantum_hash_chains_intact', _receipt_chain(global_events) and _receipt_chain(quantum_events))
        check('native_classical_delay_error_bounded', source.maximum_delay_error_s <= 2e-12, source.maximum_delay_error_s)
        check('all_protocol_sessions_compatible', not any(s.status == 'incompatible' for s in engine.technologies.sessions.values()))
        check('no_ml_physical_or_certified_keys', not engine.live_state().policy['ml_control_enabled']
              and all(e['physical_actuation'] is False and e['certified_secret_bits'] == 0 for e in global_events))
        engine.technologies.export_events(target / 'quantum-events.jsonl')
        write(target / 'timeline.json', timeline)
        write(target / 'topology.json', engine.topology().to_dict())
        result = {'schema': 'orionq.sequence-orion-network.v1', 'seed': seed, 'checks': checks,
                  'passed': all(c['passed'] for c in checks), 'phases': phases,
                  'counts': {'nodes': len(engine.topology().nodes), 'links': len(engine.topology().links),
                             'sessions': len(engine.technologies.sessions), 'demands': len(restored['demands']),
                             'global_events': len(global_events), 'quantum_events': len(quantum_events),
                             'native_hops': source.native_received, 'admitted_hops': len(received),
                             'external_rejections': source.rejected},
                  'native_classical_sha256': sha(target / 'native-classical.jsonl'), 'quantum_events_sha256': sha(target / 'quantum-events.jsonl'),
                  'source_identity': worker.metadata, 'complete_external_quantum_flow': False,
                  'limitations': ['Optical acquisitions retain the existing identified OrionQ simulation source.',
                                  'Native classical hop durations receive OrionQ geometry/latency as input; no independent RF or orbital physics qualification.',
                                  'Loss and timeout are explicit external bridge injections after native lossless channel reception.'],
                  'physical_action_performed': False, 'certified_secret_bits': 0}
        write(target / 'report.json', result)
        return result
    finally:
        source.close()
        worker.close()
        engine.close_satellite_receivers()


def export_coverage(directory, census, native_results, product_results):
    """Preserve every physical registration, including externally uncovered cases."""
    mapping = {r['session_id']: r for r in census['native_mapping']['session_mappings']}
    full_native = [load(directory / r['file']) for r in native_results
                   if r['scenario_id'] == 'full_terrestrial_banks' and r.get('status') != 'error']
    mapped_nodes = {node for m in mapping.values() for node in m['physical_path']}
    mapped_links = {link for m in mapping.values() for link in m['links']}
    native_link_counts, native_node_counts = Counter(), Counter()
    for product in product_results:
        path = directory / f"orion-network-{product['seed']}" / 'native-classical.jsonl'
        for line in path.read_text(encoding='utf-8').splitlines():
            r = json.loads(line)
            if r['admitted_to_orion']:
                native_link_counts[r['channel_id']] += 1
                native_node_counts.update((r['request']['sender'], r['request']['receiver']))
    rows = []
    def row(ident, kind, quantum, control=0, detail=None):
        rows.append({'component': ident, 'kind': kind, 'native_quantum_scope': quantum,
                     'native_classical_received_at_source': control, 'detail': detail or {},
                     'physical_connection_validated': False, 'certified_secret_bits': 0})
    for node in census['nodes']:
        ident = node['node_id']
        row(ident, 'node/communicator', 'mapped_dedicated_terrestrial_bank_reference' if ident in mapped_nodes else 'not_demonstrated',
            native_node_counts[ident], {'physical_type': node['telemetry']['communicator']['physical_type'],
                                       'banks': node['telemetry'].get('simulation_resource_banks', [])})
    for link in census['links']:
        row(link['link_id'], 'link', 'native_terrestrial_channel_reference' if link['link_id'] in mapped_links else 'not_demonstrated',
            native_link_counts[link['channels']['classical']['channel_id']],
            {'a': link['left_node_id'], 'b': link['right_node_id'], 'technologies': (link.get('technology') or {}).get('technologies', ['legacy_fibre_plant']),
             'geographic_distance_km': link['distance_km']})
    for session in census['sessions']:
        ident = session['session_id']
        outcomes = [next(d for d in n['demands'] if d['demand_id'] == ident) for n in full_native] if ident in mapping else []
        row(ident, 'protocol_session', 'native_alternative_model_executed' if outcomes else 'not_demonstrated',
            detail={'technologies': session['technologies'], 'native_outcomes': outcomes,
                    'source_parameters': session['parameters'],
                    'limitation': 'no pair-rate/fidelity-model equivalence claimed' if outcomes else 'no independent native model for this optical protocol'})
    final_demands = [p['phases']['satellite_restored']['demands'] for p in product_results]
    for demand in census['global_demand_templates']:
        outcomes = [next(d for d in ds if d['demand_id'] == demand['demand_id']) for ds in final_demands]
        row(demand['demand_id'], 'operational_demand', 'OrionQ_local_source_only' if demand['operation'] != 'classical_control_probe' else 'classical_control_only',
            detail={'operation': demand['operation'], 'outcomes': outcomes, 'native_quantum_driver': False})
    devices = census['fpga']['devices']
    if isinstance(devices, dict):
        devices = list(devices.values())
    for device in devices:
        row(device.get('device_id', device.get('id', 'unknown')), 'fpga', 'not_demonstrated',
            detail={'scope': 'registration and unchanged OrionQ simulator only; no native SeQUeNCe FPGA implementation'})
    write(directory / 'coverage-matrix.json', rows)
    fields = ['component', 'kind', 'native_quantum_scope', 'native_classical_received_at_source',
              'physical_connection_validated', 'certified_secret_bits', 'detail']
    with (directory / 'coverage-matrix.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows({**r, 'detail': json.dumps(r['detail'], ensure_ascii=False)} for r in rows)
    text = ['# Matriz completa de cobertura externa', '',
            'Recepção clássica conta callbacks na fonte; consulte os recibos OrionQ para confirmar a admissão final.', '',
            '| Componente | Tipo | Escopo quântico externo | Callbacks clássicos na fonte |', '|---|---|---|---:|']
    text.extend(f"| {r['component']} | {r['kind']} | {r['native_quantum_scope']} | {r['native_classical_received_at_source']} |" for r in rows)
    (directory / 'coverage-matrix.md').write_text('\n'.join(text) + '\n', encoding='utf-8')
    return {'rows': len(rows), 'physical_nodes': len(census['nodes']), 'physical_links': len(census['links']),
            'sessions': len(census['sessions']), 'global_demands': len(census['global_demand_templates']),
            'native_reference_sessions': len(mapping), 'unsupported_native_quantum_sessions': len(census['sessions']) - len(mapping),
            'matrix_sha256': sha(directory / 'coverage-matrix.json')}


def run(directory, python, source_path):
    directory = Path(directory).resolve()
    frozen = check_freeze(directory)
    plan = load(directory / 'protocol.json')
    started = time.perf_counter()
    native_results, product_results, provenance_hashes = [], [], set()
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    try:
        for name in frozen['inputs']:
            if not name.startswith('input-'):
                continue
            request = load(directory / name)
            output = directory / name.replace('input-', 'native-')
            if output.exists():
                raise ValueError('campaign cannot overwrite existing results')
            command = [str(Path(python).resolve()), str(ROOT / 'tools/sequence_network_backend.py'),
                       '--input', str(directory / name), '--output', str(output), '--sequence-src', str(Path(source_path).resolve())]
            try:
                proc = subprocess.run(command, text=True, encoding='utf-8', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      timeout=plan['network_deadline_s'], env=env)
            except subprocess.TimeoutExpired as exc:
                write(output.with_suffix('.execution.json'), {'command': command, 'status': 'timeout',
                      'request_sha256': sha(directory / name), 'deadline_s': plan['network_deadline_s']})
                native_results.append({'scenario_id': request['scenario_id'], 'seed': request['seed'], 'passed': False,
                                       'status': 'error', 'file': output.name, 'reason': str(exc), 'delivered_pairs': None})
                print(json.dumps(native_results[-1]), flush=True)
                continue
            (output.with_suffix('.stdout.log')).write_text(proc.stdout, encoding='utf-8')
            (output.with_suffix('.stderr.log')).write_text(proc.stderr, encoding='utf-8')
            write(output.with_suffix('.execution.json'), {'command': command, 'exit_code': proc.returncode,
                                                       'request_sha256': sha(directory / name)})
            if proc.returncode != 0 or not output.exists():
                native_results.append({'scenario_id': request['scenario_id'], 'seed': request['seed'], 'passed': False,
                                       'status': 'error', 'file': output.name, 'delivered_pairs': None,
                                       'reason': f'native execution exit {proc.returncode}; inspect preserved stderr'})
                print(json.dumps(native_results[-1]), flush=True)
                continue
            result = load(output)
            checks = native_checks(result, request, directory)
            provenance_hashes.add(digest(result['provenance']['source_sha256']))
            native_results.append({'scenario_id': request['scenario_id'], 'seed': request['seed'],
                                   'passed': all(c['passed'] for c in checks), 'checks': checks,
                                   'delivered_pairs': sum(d['delivered_pairs'] for d in result['demands']),
                                   'native_metrics': result['native_metrics'], 'file': output.name, 'sha256': sha(output)})
            print(json.dumps({k: native_results[-1][k] for k in ('scenario_id', 'seed', 'passed', 'delivered_pairs') }), flush=True)
        for seed in plan['seeds']:
            product = execute_orion(directory, python, source_path, seed, plan)
            product_results.append(product)
            provenance_hashes.add(digest(product['source_identity']['source_sha256']))
            print(json.dumps({'orion_seed': seed, 'passed': product['passed'], 'counts': product['counts']}), flush=True)
        coverage = export_coverage(directory, load(directory / 'census.json'), native_results, product_results)
        source_unchanged = frozen['sources'] == inventory()
        checks_passed = source_unchanged and len(provenance_hashes) == 1 and all(r['passed'] for r in native_results + product_results)
        report = {'schema': 'orionq.sequence-network-campaign.v1', 'protocol_id': plan['protocol_id'],
                  'status': 'partial_verified' if checks_passed else 'fail', 'covered_scope_passed': checks_passed,
                  'full_hybrid_quantum_network_validated': False, 'source_unchanged': source_unchanged,
                  'native_source_consistent': len(provenance_hashes) == 1, 'native_results': native_results,
                  'coverage': coverage,
                  'orion_results': [{'seed': r['seed'], 'passed': r['passed'], 'checks': r['checks'], 'counts': r['counts']} for r in product_results],
                  'census_sha256': sha(directory / 'census.json'), 'freeze_sha256': sha(directory / 'freeze.json'),
                  'elapsed_s': time.perf_counter() - started, 'physical_action_performed': False,
                  'hardware_qualified': False, 'trl_assigned': False, 'certified_secret_bits': 0,
                  'limitations': ['Native quantum network covers compatible terrestrial session banks using a different entanglement-generation architecture.',
                                  'TF-QKD, satellite optical payload and inter-satellite physics lack independent native SeQUeNCe model qualification.',
                                  'Global coherent quantum relay across satellites remains unavailable in OrionQ; classical end-to-end control is tested.',
                                  'No equality of native pair rates, fidelities under incompatible models, or shared physical device capacity is claimed.']}
        write(directory / 'report.json', report)
        return report
    except Exception as exc:
        import traceback
        write(directory / 'incomplete.json', {'status': 'incomplete', 'exception': f'{type(exc).__name__}: {exc}',
              'traceback': traceback.format_exc(), 'native_completed': native_results, 'orion_completed': len(product_results),
              'source_unchanged': frozen['sources'] == inventory(), 'full_hybrid_quantum_network_validated': False})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    prepared = sub.add_parser('prepare')
    prepared.add_argument('--output', type=Path, required=True)
    prepared.add_argument('--protocol', type=Path, default=DEFAULT_PROTOCOL)
    execution = sub.add_parser('run')
    execution.add_argument('--output', type=Path, required=True)
    execution.add_argument('--sequence-python', type=Path, required=True)
    execution.add_argument('--sequence-src', type=Path, default=Path(os.environ.get('SEQUENCE_SRC', str(Path(__file__).resolve().parents[2] / 'dependencies' / 'SeQUeNCe'))))
    args = parser.parse_args()
    if args.operation == 'prepare':
        print(json.dumps(prepare(args.output, args.protocol)))
        return 0
    report = run(args.output, args.sequence_python, args.sequence_src)
    print(json.dumps({'status': report['status'], 'full_hybrid_quantum_network_validated': False, 'output': str(args.output)}))
    return 0 if report['covered_scope_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
