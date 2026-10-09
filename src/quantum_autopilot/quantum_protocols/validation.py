"""Self-test shared by source and the Windows executable, with isolated state."""
from dataclasses import replace
import json
from pathlib import Path


def validate_modular_runtime(destination):
    from ..network_engine.institutional import brazil_institutions
    from .contracts import digest
    from .runtime import ProtocolRuntime
    from .sources import MeasurementFileSource, SimulatedAcquisitionSource
    from .topology import expand_global

    out = Path(destination)
    out.mkdir(parents=True, exist_ok=True)
    topology, _ = expand_global(brazil_institutions())
    selected = {'RIO-ENTANGLEMENT', 'RIO-TF', 'RIO-SATELLITE'}
    topology = replace(topology, nodes=tuple(replace(n, telemetry={
        **n.telemetry, 'technology_sessions': [c for c in
        n.telemetry.get('technology_sessions', ()) if c['session_id'] in selected]
    }) for n in topology.nodes))
    runtime = ProtocolRuntime(out / 'operations.sqlite', topology.topology_id,
                              source=SimulatedAcquisitionSource(42))
    for tick in range(1, 101):
        runtime.advance(topology, tick)
    runtime.export_events(out / 'receipts.jsonl')
    rows = [json.loads(line) for line in (out / 'receipts.jsonl').read_text().splitlines()]
    families = {e['detail']['technology'] for e in rows if e['event'] == 'protocol_processed'}
    snapshot = runtime.snapshot()
    sessions = snapshot['sessions']
    checks = {
        'five_adapters_process_observations': families == {
            'entanglement_swapping', 'quantum_memory', 'purification',
            'tf_qkd', 'satellite_doppler'},
        'end_to_end_pair_delivered': sessions['RIO-ENTANGLEMENT']['counters'].get('delivered_pairs', 0) > 0,
        'failed_bsm_consumes_inputs': any(e['event'] == 'bsm_failed' and len(e['detail']['consumed']) == 2 for e in rows),
        'memory_expiry_observed': any(e['event'] == 'memory_expired' for e in rows),
        'bell_frame_received_classically': any(e['event'] == 'pauli_frame_recorded' for e in rows),
        'purification_cost_and_yield': sessions['RIO-ENTANGLEMENT']['metrics']['purification_yield'] <= .5,
        'tf_budget_is_not_certified_key': sessions['RIO-TF']['metrics']['conditional_key_estimate_bits'] > 0
            and sessions['RIO-TF']['metrics']['certified_secret_bits'] == 0,
        'satellite_coincidences_and_doppler': sessions['RIO-SATELLITE']['metrics']['coincidences'] > 0
            and any(abs(g['doppler_shift_hz']) > 0 for g in sessions['RIO-SATELLITE']['metrics']['geometry']),
        'no_physical_actuation': not snapshot['physical_actuation'] and all(not e['physical_actuation'] for e in rows),
    }
    previous = ''
    chain_valid = True
    messages = {}
    for row in rows:
        body = {k: v for k, v in row.items() if k != 'receipt_sha256'}
        chain_valid &= digest(body) == row['receipt_sha256'] and row['previous_sha256'] == previous
        previous = row['receipt_sha256']
        if row['command_id']:
            messages.setdefault(row['command_id'], []).append(row)
    checks['receipt_hash_chain'] = chain_valid
    causal = True
    for records in messages.values():
        names = [e['event'] for e in records]
        if 'metrics_computed' not in names:
            continue
        stages = ['acquired', 'validated', 'synchronized', 'protocol_processed',
                  'classical_transmitted', 'classical_received', 'metrics_computed']
        causal &= [names.index(k) for k in stages] == sorted(names.index(k) for k in stages)
        sent = next(e for e in records if e['event'] == 'classical_transmitted')
        received = next(e for e in records if e['event'] == 'classical_received')
        causal &= bool(sent['detail']['associated_quantum_ids'] and sent['detail']['classical_channel_ids'])
        causal &= received['at_s'] >= sent['detail']['arrival_at_s'] > sent['at_s']
        if 'pauli_frame_recorded' in names:
            causal &= names.index('classical_received') < names.index('pauli_frame_recorded')
        if 'quantum_transmission_observed' in names:
            observed = next(e for e in records if e['event'] == 'quantum_transmission_observed')
            processed = next(e for e in records if e['event'] == 'protocol_processed')
            expected = 1 if processed['detail']['operation'] == 'generate' else 2
            causal &= len(observed['detail']['quantum_channel_ids']) == expected
    checks['paired_channels_and_causality'] = causal
    # Replaying synthetic raw observations proves interface equivalence only;
    # it is explicitly not a claim that the replay is a physical measurement.
    tf = replace(topology, nodes=tuple(replace(n, telemetry={**n.telemetry,
        'technology_sessions': [c for c in n.telemetry.get('technology_sessions', ())
                               if c['session_id'] == 'RIO-TF']}) for n in topology.nodes))
    source = SimulatedAcquisitionSource(42)
    first = ProtocolRuntime(out / 'replay-simulated.sqlite', tf.topology_id, source=source)
    for tick in range(1, 5):
        first.advance(tf, tick)
    records = [{**r, 'origin': 'measurement_file', 'source_id': 'measurement_file'} for r in source.records]
    path = out / 'synthetic-replay.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in records) + '\n', encoding='utf-8')
    second = ProtocolRuntime(out / 'replay-file.sqlite', tf.topology_id, source=MeasurementFileSource(path))
    for tick in range(1, 5):
        second.advance(tf, tick)
    a = first.snapshot()['sessions']['RIO-TF']; b = second.snapshot()['sessions']['RIO-TF']
    checks['file_and_simulator_process_identically'] = a['metrics'] == b['metrics'] and a['counters'] == b['counters']
    report = {'schema': 'orion.modular-runtime-validation.v1', 'passed': all(checks.values()),
              'checks': checks, 'check_count': len(checks), 'seed': 42,
              'duration_simulated_s': 100, 'sessions': sessions,
              'scope': 'raw simulated operations and explicitly synthetic file replay; no physical integration'}
    (out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
