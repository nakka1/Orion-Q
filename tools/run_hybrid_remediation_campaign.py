"""Qualify the additive finite-SNS/orbital-memory remediation on the real engine.

No historical oracle or report is changed. Results are conditional simulation
evidence, never physical qualification or certified QKD material.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
import csv
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
import platform
import sqlite3
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

# These imports follow the explicit repository-source path setup above.
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore  # noqa: E402
from quantum_autopilot.network_engine.engine import NetworkEngine  # noqa: E402
from quantum_autopilot.network_engine.institutional import ensure_institutional_workspace  # noqa: E402
from quantum_autopilot.network_engine.models import ModelRegistry  # noqa: E402
from quantum_autopilot.network_engine.sources import SimulatedInputSource  # noqa: E402
from quantum_autopilot.quantum_protocols.contracts import digest  # noqa: E402
from quantum_autopilot.quantum_protocols.orbital_relay_topology import SESSION_ID, PATH, LINK_IDS  # noqa: E402

TF = 'RIO-SNS-FINITE'
TARGETS = (TF, SESSION_ID)
PHASES = (('nominal', 60), ('degraded', 8), ('unavailable', 6), ('recovery', 60))


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')


def source_manifest():
    paths = sorted(p for p in (ROOT / 'src').rglob('*') if p.is_file()
                   and '__pycache__' not in p.parts and p.suffix != '.pyc')
    paths += [Path(__file__), ROOT / 'pyproject.toml']
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def hardware():
    packages = {}
    for name in ('numpy', 'scipy', 'PySide6', 'pytest', 'sgp4', 'psutil'):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    result = {'platform': platform.platform(), 'python': sys.version, 'processor': platform.processor(),
              'packages': packages, 'executable': sys.executable}
    try:
        import psutil
        result.update(logical_cpus=psutil.cpu_count(), physical_cpus=psutil.cpu_count(logical=False),
                      total_ram_bytes=psutil.virtual_memory().total)
    except ImportError:
        pass
    return result


def acquisition_rows(database):
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute('SELECT sha256,observation_zlib FROM satellite_acquisitions').fetchall()
    for sha, compressed in rows:
        blob = zlib.decompress(compressed)
        yield sha, hashlib.sha256(blob).hexdigest() == sha, json.loads(blob)


def validate_receipts(rows, checks):
    previous = ''
    grouped = defaultdict(list)
    for row in rows:
        body = {key: value for key, value in row.items() if key != 'receipt_sha256'}
        if digest(body) != row['receipt_sha256'] or row['previous_sha256'] != previous:
            checks['receipt_hash_chain'] = False
        previous = row['receipt_sha256']
        if row.get('physical_actuation'):
            checks['physical_actuation_blocked'] = False
        if row.get('command_id'):
            grouped[row['command_id']].append(row)
    for command_rows in grouped.values():
        if command_rows[0]['session_id'] not in TARGETS:
            continue
        metrics = [r for r in command_rows if r['event'] == 'metrics_computed']
        classical = [r for r in command_rows if r['event'] == 'classical_received']
        if metrics and (not classical or max(r['at_s'] for r in classical) > metrics[0]['at_s']):
            checks['metrics_after_classical_receipt'] = False
        if command_rows[0]['session_id'] == TF:
            sent = [r for r in command_rows if r['event'] == 'postprocessing_classical_transmitted']
            received = [r for r in command_rows if r['event'] == 'postprocessing_classical_received']
            if metrics and (not sent or len(sent) != len(received)
                            or received[-1]['at_s'] > metrics[0]['at_s']):
                checks['finite_sns_all_public_receipts'] = False
            if metrics:
                for sequence,(send,receive) in enumerate(zip(sent,received),1):
                    public=send['detail']
                    if (public!=receive['detail'] or public.get('sequence')!=sequence
                        or public.get('command_id')!=send['command_id'] or public.get('session_id')!=TF
                        or digest({k:v for k,v in public.items() if k!='receipt_sha256'})!=public.get('receipt_sha256')
                        or not math.isclose(send['at_s'],public.get('transmitted_at_s',-1.),abs_tol=1e-9)
                        or not math.isclose(receive['at_s'],public.get('received_at_s',-1.),abs_tol=1e-9)
                        or send['at_s']>receive['at_s']
                        or sequence>1 and sent[sequence-1]['at_s']<received[sequence-2]['at_s']):
                        checks['finite_sns_all_public_receipts']=False
            for row in sent:
                # The recorded public payload must not contain private Z inputs.
                text = json.dumps(row['detail']).lower()
                if any('"' + key + '"' in text for key in ('alice_preparation', 'bob_preparation',
                        'signal_records', 'source_preparation', 'final_key', 'key_bits', 'private_preparations')):
                    checks['private_preparations_not_transmitted'] = False
    consumed = [parent for r in rows if r['session_id'] == SESSION_ID
                and r['event'] == 'resources_reserved_and_consumed' for parent in r['detail']['parent_ids']]
    checks['parents_consumed_once'] = len(consumed) == len(set(consumed))
    checks['no_processing_errors'] = not any(r['event'] == 'processing_error' for r in rows)


def run_seed(output, seed, phases=PHASES):
    folder = output / f'seed-{seed}'
    folder.mkdir()
    store = SimulatedTopologyStore(folder / 'network.sqlite')
    topology, _ = ensure_institutional_workspace(store)
    engine = NetworkEngine(store, store.path, seed=seed, source=SimulatedInputSource(seed),
                           registry=ModelRegistry(environ={}))
    topology=store.get(topology.topology_id)
    original=topology.to_dict()
    checks = {name: True for name in ('receipt_hash_chain', 'physical_actuation_blocked',
              'metrics_after_classical_receipt', 'finite_sns_all_public_receipts',
              'private_preparations_not_transmitted', 'all_memories_within_capacity',
              'satellite_transmissions_only_senders', 'new_sessions_compatible',
              'certified_bits_zero', 'raw_content_hashes', 'unavailable_no_admission')}
    checks['all_38_sessions_compatible'] = all(s.status != 'incompatible' for s in engine.technologies.sessions.values())
    for ident in TARGETS:
        response = engine.command('configure_technology', {'session_id': ident, 'seed': seed})
        if response.get('status') != 'configured':
            raise RuntimeError(f'validated configuration failed: {response}')
    initial = {ident: engine.technologies.sessions[ident].config.to_dict() for ident in TARGETS}
    write_json(folder / 'topology.json', original)
    write_json(folder / 'initial-configurations.json', initial)
    tick_samples, phase_records = [], []
    observed_transmission_links=set()
    for phase, steps in phases:
        # Changing scenario invalidates old operations/resources. Recovery uses
        # nominal source conditions at the continuing clock, not a clock reset.
        for ident in TARGETS:
            engine.command('configure_technology', {'session_id': ident,
                           'scenario': 'nominal' if phase == 'recovery' else phase})
        before = {ident: dict(engine.technologies.sessions[ident].counts) for ident in TARGETS}
        start = engine.technologies.clock_s
        for _ in range(steps):
            began = time.perf_counter()
            engine.step()
            elapsed = time.perf_counter() - began
            live = engine.live_state()
            runtime = engine.technologies
            for ident in TARGETS:
                session = runtime.sessions[ident]
                checks['new_sessions_compatible'] &= session.status != 'incompatible'
                checks['certified_bits_zero'] &= session.metrics.get('certified_secret_bits', 0) == 0
            session = runtime.sessions[SESSION_ID]
            occupancy = Counter(node for pair in session.pairs for node in (pair.a, pair.b))
            occupancy.update(node for endpoints in session.held_memories.values() for node in endpoints)
            for endpoints, modes in session.capture_reservations.values():
                occupancy.update({node: modes for node in endpoints})
            for ident, used in occupancy.items():
                p = {**session.config.parameters,
                     **session.config.parameters.get('device_parameters', {}).get(ident, {})}
                checks['all_memories_within_capacity'] &= 0 <= used <= p['memory_slots']
            for ident in PATH[1:3]:
                satellite = live.satellites.get(ident, {})
                for transmission in satellite.get('transmitting_links', ()):
                    link_id=transmission['link_id']
                    observed_transmission_links.add(link_id)
                    link=next(link for link in topology.links if link.link_id==link_id)
                    segment=session.metrics.get('segments',{}).get(f'{link.left_node_id}|{link.right_node_id}')
                    if segment is None:
                        segment=session.metrics.get('segments',{}).get(f'{link.right_node_id}|{link.left_node_id}',{})
                    geometry=segment.get('geometry',())
                    checks['satellite_transmissions_only_senders'] &= any(
                        g.get('photon_sender')==ident and g.get('photon_receiver')==link.channels['quantum']['receiver_id']
                        and {g['photon_sender'],g['photon_receiver']}=={link.left_node_id,link.right_node_id}
                        and g.get('visible') for g in geometry)
            tick_samples.append({'phase': phase, 'clock_s': runtime.clock_s,
                                 'step_ms': elapsed * 1000, 'pending_events': len(runtime._queue),
                                 'satellites': len(live.satellites), 'occupied_modes': dict(occupancy),
                                 'sessions': {ident: {'status': runtime.sessions[ident].status,
                                                     'counters': dict(runtime.sessions[ident].counts)}
                                              for ident in TARGETS}})
        after = {ident: dict(engine.technologies.sessions[ident].counts) for ident in TARGETS}
        delta = {ident: {key: value - before[ident].get(key, 0) for key, value in after[ident].items()}
                 for ident in TARGETS}
        if phase == 'unavailable':
            checks['unavailable_no_admission'] &= all(delta[ident].get('observations', 0) == 0 for ident in TARGETS)
        phase_records.append({'phase': phase, 'start_s': start, 'end_s': engine.technologies.clock_s,
                              'steps': steps, 'counter_delta': delta,
                              'end_metrics': {ident: dict(engine.technologies.sessions[ident].metrics) for ident in TARGETS}})
        print(f'seed={seed} phase={phase} clock={engine.technologies.clock_s:g} '
              f'PA={delta[TF].get("conditional_key_estimate_bits", 0)} '
              f'pairs={delta[SESSION_ID].get("delivered_pairs", 0)}', flush=True)
    event_path = folder / 'events.jsonl'
    engine.technologies.export_events(event_path)
    rows = [json.loads(line) for line in event_path.read_text(encoding='utf-8').splitlines()]
    validate_receipts(rows, checks)
    raw_counts, eta = Counter(), defaultdict(list)
    for _, valid, observation in acquisition_rows(store.path):
        checks['raw_content_hashes'] &= valid
        ident = observation['session_id']
        raw_counts[ident] += 1
        raw = observation['raw']
        if ident == SESSION_ID and observation['operation'] == 'generate':
            condition = next((p['phase'] for p in phase_records
                              if p['start_s'] <= raw['start_s'] < p['end_s']), 'boundary')
            eta[condition].append(raw['optical_transmission'])
    checks['private_raw_durable'] = raw_counts[TF] > 0 and raw_counts[SESSION_ID] > 0
    for phase in ('nominal', 'recovery'):
        record = next(p for p in phase_records if p['phase'] == phase)
        metrics = [r for r in rows if r['session_id'] == TF and r['event'] == 'metrics_computed'
                   and record['start_s'] < r['at_s'] <= record['end_s']]
        checks[f'{phase}_finite_sns_positive'] = any(r['detail'].get('metrics', {}).get('conditional_key_estimate_bits', 0) > 0 for r in metrics)
        checks[f'{phase}_orbital_delivery'] = record['counter_delta'][SESSION_ID].get('delivered_pairs', 0) > 0
    checks['optical_degradation_observed'] = bool(eta['nominal'] and eta['degraded']) and max(eta['degraded']) < max(eta['nominal'])
    final_topology = store.get(topology.topology_id)
    checks['topology_preserved_during_execution'] = final_topology.to_dict() == original
    checks['eleven_satellites_published'] = all(r['satellites'] == 11 for r in tick_samples)
    checks['all_orbital_transmissions_published']=set(LINK_IDS)<=observed_transmission_links
    with closing(sqlite3.connect(store.path)) as connection:
        global_rows = [json.loads(row[0]) for row in connection.execute('SELECT record_json FROM global_operation_events')]
    write_json(folder / 'global-events.json', global_rows)
    for ident in TARGETS:
        checks[f'{ident}_global_receipts'] = any(row['event'] == 'protocol_demand_result'
            and row['detail'].get('technology_receipt') for row in global_rows
            if row['demand_id'] == f'SERVICE-{ident}')
    result = {'seed': seed, 'checks': checks, 'passed': all(checks.values()), 'phases': phase_records,
              'topology': {'nodes': len(topology.nodes), 'links': len(topology.links),
                           'sessions': len(engine.technologies.sessions), 'satellites': len(engine.live_state().satellites)},
              'raw_acquisitions': dict(raw_counts), 'event_count': len(rows),
              'observed_orbital_transmission_links':sorted(observed_transmission_links),
              'optical_transmission_by_phase': dict(eta),
              'step_ms': {'mean': sum(r['step_ms'] for r in tick_samples) / len(tick_samples),
                          'max': max(r['step_ms'] for r in tick_samples)},
              'physical_actuation': False, 'certified_secret_bits': 0}
    write_json(folder / 'tick-samples.json', tick_samples)
    write_json(folder / 'summary.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seeds', type=int, nargs='+', default=[101, 202, 303])
    parser.add_argument('--workers', type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args(argv)
    if any(seed < 0 for seed in args.seeds) or len(set(args.seeds)) != len(args.seeds):
        parser.error('seeds must be distinct non-negative integers')
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('output must be new/empty; never overwrite campaign evidence')
    output.mkdir(parents=True, exist_ok=True)
    began = time.perf_counter()
    frozen = source_manifest()
    write_json(output / 'source-freeze.json', {'schema': 'orion.hybrid-remediation-freeze.v1', 'files': frozen})
    write_json(output / 'environment.json', hardware())
    if args.workers==1:
        results = [run_seed(output, seed) for seed in args.seeds]
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures=[pool.submit(run_seed,output,seed) for seed in args.seeds]
            results=[future.result() for future in futures]
    unchanged = source_manifest() == frozen
    report = {'schema': 'orion.hybrid-remediation-campaign.v1',
              'status': 'conditional_models_verified' if unchanged and all(r['passed'] for r in results) else 'failed',
              'source_unchanged_during_run': unchanged, 'seeds': args.seeds, 'workers':args.workers,'runs': results,
              'wall_seconds': time.perf_counter() - began, 'physical_actuation': False,
              'certified_secret_bits': 0,
              'limitations': ['Conditional symmetric finite SNS assumptions; authenticated physical transport/security review pending.',
                              'Dedicated hypothetical GEO memory payload; Werner ensemble/logical-frame model.',
                              'OGS services associated with institutions; no quantum fibre to campus core inferred.',
                              'Common-epoch first-order optics, constant batch geometry, conservative whole-batch windows.',
                              'Backend campaign; native graphical rendering and physical mission not qualified.']}
    write_json(output / 'report.json', report)
    with (output / 'case-matrix.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['seed', 'check', 'status'])
        for run in results:
            writer.writerows((run['seed'], key, 'PASS' if value else 'FAIL') for key, value in run['checks'].items())
    print(json.dumps({'status': report['status'], 'wall_seconds': report['wall_seconds'],
                      'failed': {r['seed']: [key for key, value in r['checks'].items() if not value] for r in results}}, indent=2), flush=True)
    return 0 if report['status'] == 'conditional_models_verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
