"""End-to-end finite SNS admission: causal public transport and durable evidence."""
from dataclasses import replace
import hashlib
import json
import sqlite3
import zlib

import pytest

from quantum_autopilot.network_engine.institutional import brazil_institutions
from quantum_autopilot.quantum_protocols.contracts import Acquisition, digest
from quantum_autopilot.quantum_protocols.runtime import ProtocolRuntime
from quantum_autopilot.quantum_protocols.sources import MeasurementFileSource, ReadOnlyQueueSource, SimulatedAcquisitionSource
from quantum_autopilot.quantum_protocols.topology import expand_global


def topology(seed=101, **overrides):
    value = expand_global(brazil_institutions())[0]
    parameters = {'tf_estimation_mode': 'finite_sns_v2', 'signal_mu': .15, 'decoy_mu': .03,
                  'decoy_strong_mu': .15, 'send_probability': .005, 'pulse_rate_hz': 20000000.,
                  'phase_sigma_rad': .01, 'polarization_error_rad': .01, 'tf_misalignment_error': .005,
                  'dark_probability': 1e-10, 'block_s': 1., 'timeout_s': 60., 'max_attempts': 1,
                  'source_override': True, **overrides}
    nodes = []
    for node in value.nodes:
        telemetry = {**node.telemetry, 'technology_sessions': []}
        # This discardable test fixture supplies explicit source parameters;
        # it does not mutate saved or user communicator configuration.
        telemetry.pop('communicator', None)
        for config in node.telemetry.get('technology_sessions', []):
            if config['session_id'] == 'RIO-TF':
                telemetry['technology_sessions'].append({**config, 'seed': seed,
                    'parameters': {**config['parameters'], **parameters}})
        nodes.append(replace(node, telemetry=telemetry))
    return replace(value, nodes=tuple(nodes))


def advance(runtime, value, seconds):
    for _ in range(20):
        runtime.advance(value, seconds)
        if runtime.clock_s >= seconds:
            return
    raise AssertionError('bounded event processing did not drain within 20 advances')


def events(runtime, path):
    runtime.export_events(path)
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def raw_observations(path):
    with sqlite3.connect(path) as connection:
        values = connection.execute('SELECT sha256,observation_zlib FROM satellite_acquisitions').fetchall()
    results = []
    for sha, payload in values:
        text = zlib.decompress(payload)
        assert hashlib.sha256(text).hexdigest() == sha
        results.append((sha, json.loads(text)))
    return results


@pytest.mark.parametrize('seed', [101, 202, 303])
def test_runtime_requires_all_classical_receipts_before_metrics_and_persists_private_raw(tmp_path, seed):
    value = topology(seed)
    runtime = ProtocolRuntime(tmp_path / 'runtime.sqlite', value.topology_id,
                              source=SimulatedAcquisitionSource(seed))
    advance(runtime, value, 1.1)
    assert runtime.snapshot()['sessions']['RIO-TF']['counters'].get('observations', 0) == 0
    advance(runtime, value, 5.)
    snap = runtime.snapshot()['sessions']['RIO-TF']
    assert snap['counters']['observations'] > 0 and snap['metrics']['conditional_key_estimate_bits'] > 0
    rows = events(runtime, tmp_path / 'events.jsonl')
    assert not [row for row in rows if row['event'] in ('processing_error', 'operation_error')]
    by_command = {}
    previous = ''
    for row in rows:
        body = {key: val for key, val in row.items() if key != 'receipt_sha256'}
        assert digest(body) == row['receipt_sha256'] and row['previous_sha256'] == previous
        previous = row['receipt_sha256']
        if row['command_id']:
            by_command.setdefault(row['command_id'], []).append(row)
        assert not row['physical_actuation']
    completed = 0
    for command_rows in by_command.values():
        names = [row['event'] for row in command_rows]
        if 'metrics_computed' not in names:
            continue
        completed += 1
        assert names.index('acquired') < names.index('validated') < names.index('synchronized')
        assert names.index('protocol_proposal_prepared') < names.index('postprocessing_classical_transmitted')
        assert names.index('protocol_processed') > max(i for i, name in enumerate(names) if name == 'postprocessing_classical_received')
        assert names.index('metrics_computed') > names.index('protocol_processed')
        sends = {row['detail']['message_id']: row for row in command_rows if row['event'] == 'postprocessing_classical_transmitted'}
        receives = {row['detail']['message_id']: row for row in command_rows if row['event'] == 'postprocessing_classical_received'}
        assert sends.keys() == receives.keys()
        assert {row['detail']['stage'] for row in receives.values()} >= {
            'sifting', 'parameter_estimation', 'reconciliation', 'verification', 'privacy_amplification'}
        for ident, transmitted in sends.items():
            received = receives[ident]
            assert received['at_s'] >= transmitted['at_s']
            assert received['detail']['received_at_s'] == received['at_s']
            assert transmitted['detail']['classical_channel_ids'] and transmitted['detail']['associated_quantum_ids']
            public = transmitted['detail']['payload']
            assert 'alice_send' not in public and 'bob_send' not in public and 'signal_records' not in public
    assert completed == snap['counters']['observations']
    acquired = [row for row in rows if row['event'] == 'acquired']
    assert all('observation' not in row['detail'] and row['detail']['observation_reference'] for row in acquired)
    private = raw_observations(runtime.journal_path)
    assert private and all(record['raw']['tf_schema'] == 'orion.tf-sns.v2' for _, record in private)
    assert snap['metrics']['certified_secret_bits'] == 0


def test_synthetic_capture_replays_through_measurement_file_pipeline_without_metric_changes(tmp_path):
    value = topology()
    source = SimulatedAcquisitionSource(101)
    first = ProtocolRuntime(tmp_path / 'simulation.sqlite', value.topology_id, source=source)
    advance(first, value, 5.)
    capture = tmp_path / 'synthetic-acquisition.jsonl'
    records = [replace(Acquisition.from_dict(row), origin='measurement_file', source_id='measurement_file').to_dict()
               for row in source.records]
    capture.write_text('\n'.join(json.dumps(row) for row in records) + '\n', encoding='utf-8')
    second = ProtocolRuntime(tmp_path / 'replay.sqlite', value.topology_id, source=MeasurementFileSource(capture))
    advance(second, value, 5.)
    a, b = [runtime.snapshot()['sessions']['RIO-TF'] for runtime in (first, second)]
    assert a['metrics'] == b['metrics'] and a['counters'] == b['counters']
    left = events(first, tmp_path / 'left.jsonl')
    right = events(second, tmp_path / 'right.jsonl')
    assert [(r['event'], r['at_s']) for r in left] == [(r['event'], r['at_s']) for r in right]
    assert all(row['origin'] == 'measurement_file' for row in right)
    queue = ReadOnlyQueueSource('synthetic_sns_replay_queue')
    queue.origin = 'simulated'
    for row in source.records:
        queue.submit(replace(Acquisition.from_dict(row), source_id=queue.source_id, origin='simulated'))
    third = ProtocolRuntime(tmp_path / 'queue.sqlite', value.topology_id, source=queue)
    advance(third, value, 5.)
    c = third.snapshot()['sessions']['RIO-TF']
    assert a['metrics'] == c['metrics'] and a['counters'] == c['counters']
    queued = events(third, tmp_path / 'queue.jsonl')
    assert [(r['event'], r['at_s']) for r in left] == [(r['event'], r['at_s']) for r in queued]


def test_channel_loss_during_postprocessing_invalidates_private_proposal(tmp_path):
    value = topology()
    runtime = ProtocolRuntime(tmp_path / 'interrupted.sqlite', value.topology_id, source=SimulatedAcquisitionSource(101))
    advance(runtime, value, 1.1)
    before = events(runtime, tmp_path / 'before.jsonl')
    assert any(row['event'] == 'protocol_proposal_prepared' for row in before)
    assert not any(row['event'] == 'metrics_computed' for row in before)
    links = []
    for link in value.links:
        if link.link_id == 'TECH-RIO-TF-01':
            channels = json.loads(json.dumps(link.channels))
            channels['classical']['enabled'] = False
            link = replace(link, channels=channels)
        links.append(link)
    interrupted = replace(value, links=tuple(links))
    advance(runtime, interrupted, 5.)
    after = events(runtime, tmp_path / 'after.jsonl')
    assert not any(row['event'] == 'metrics_computed' for row in after)
    assert runtime.snapshot()['sessions']['RIO-TF']['counters'].get('conditional_key_estimate_bits', 0) == 0
    assert any(row['event'] == 'operation_error' for row in after)


@pytest.mark.parametrize('fault', ['deadline', 'classical_disabled', 'quantum_disabled', 'low_sample', 'bad_phase'])
def test_runtime_failure_states_do_not_fabricate_conditional_key_results(tmp_path, fault):
    overrides = {'timeout_s': 1.01} if fault == 'deadline' else {'pulse_rate_hz': 10000.} if fault == 'low_sample' else {
        'tf_misalignment_error': .49} if fault == 'bad_phase' else {}
    value = topology(**overrides)
    if fault in ('classical_disabled', 'quantum_disabled'):
        layer = 'classical' if fault == 'classical_disabled' else 'quantum'
        links = []
        for link in value.links:
            if link.link_id == 'TECH-RIO-TF-01':
                channels = json.loads(json.dumps(link.channels))
                channels[layer]['enabled'] = False
                link = replace(link, channels=channels)
            links.append(link)
        value = replace(value, links=tuple(links))
    runtime = ProtocolRuntime(tmp_path / 'failure.sqlite', value.topology_id, source=SimulatedAcquisitionSource(101))
    advance(runtime, value, 5.)
    snap = runtime.snapshot()['sessions']['RIO-TF']
    assert snap['counters'].get('conditional_key_estimate_bits', 0) == 0
    assert snap['metrics'].get('conditional_key_estimate_bits', 0) == 0
    rows = events(runtime, tmp_path / 'events.jsonl')
    if fault in ('deadline', 'classical_disabled', 'quantum_disabled'):
        assert not [row for row in rows if row['event'] == 'metrics_computed']
    else:
        assert snap['metrics']['finite_key']['abort_reason']
