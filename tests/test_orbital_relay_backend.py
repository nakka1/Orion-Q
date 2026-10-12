"""Exact-state oracles, raw-source substitution and genuine backend relay flows.

All fixtures and device profiles are synthetic. A physical-origin envelope in
one test is contract validation only; it is not a connected/validated device.
"""
from __future__ import annotations

import copy
from collections import Counter
import json
import sqlite3
from dataclasses import replace

import numpy as np
import pytest

from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from quantum_autopilot.network_engine.engine import NetworkEngine
from quantum_autopilot.network_engine.institutional import brazil_institutions
from quantum_autopilot.network_engine.sources import SimulatedInputSource
from quantum_autopilot.quantum_protocols.contracts import Acquisition, DeviceCommand, ProtocolConfig, ProtocolError
from quantum_autopilot.quantum_protocols.orbital_relay import OrbitalRelayAdapter
from quantum_autopilot.quantum_protocols.orbital_relay_simulation import ORBITAL_SCHEMA, generate_observation, observe_bsm
from quantum_autopilot.quantum_protocols.orbital_relay_topology import LINK_IDS, PATH, SESSION_ID, expand_orbital_relay
from quantum_autopilot.quantum_protocols.registry import default_registry
from quantum_autopilot.quantum_protocols.runtime import ProtocolRuntime
from quantum_autopilot.quantum_protocols.sources import MeasurementFileSource, ReadOnlyQueueSource, SimulatedAcquisitionSource
from quantum_autopilot.quantum_protocols.topology import expand_global

pytestmark = pytest.mark.unit


def relay_topology(**parameters):
    base, _ = expand_global(brazil_institutions())
    expanded, payload = expand_orbital_relay(base)
    cfg = ProtocolConfig.from_dict(payload['sessions'][0])
    cfg = replace(cfg, parameters={**cfg.parameters, **parameters})
    # Isolate test load; production migration preserves every old record.
    nodes = tuple(replace(n, telemetry={**n.telemetry,
                  'technology_sessions': [cfg.to_dict()] if n.node_id == PATH[0] else []})
                  for n in expanded.nodes if n.node_id in PATH or n.node_id in ('CBPF', 'BSC'))
    return replace(expanded, nodes=nodes, links=tuple(link for link in expanded.links if link.link_id in LINK_IDS)), cfg


def environment(topology, condition='nominal'):
    return {'condition': condition, 'node_positions': {n.node_id: n.telemetry.get('location') for n in topology.nodes}}


def command(cfg, *, operation='generate', endpoints=None, at_s=0., ident='raw-command'):
    args = {'duration_s': .5, 'device_endpoints': list(endpoints or cfg.path[:2]), 'length_km': 1.}
    if operation == 'bsm':
        args.update(bsm_node=PATH[1], parent_ids=['parent-a', 'parent-b'])
    return DeviceCommand(ident, cfg.session_id, operation, cfg.path,
                         tuple(f'{n}.q' for n in LINK_IDS), tuple(f'{n}.c' for n in LINK_IDS),
                         at_s, at_s + 4., 1, args)


def context(cfg, cmd, raw):
    value = {'operation': cmd.operation, 'parameters': cfg.parameters,
             'observed_endpoints': cmd.arguments['device_endpoints'], 'duration_s': .5,
             'command_emitted_at_s': cmd.emitted_at_s,
             'observation_at_s': raw.get('end_s', cmd.emitted_at_s) + raw.get('quantum_flight_s', 0.)}
    if cmd.operation == 'bsm':
        value.update(parent_ids=cmd.arguments['parent_ids'], first_frame=(0, 0), second_frame=(0, 0),
                     first_fidelity=.9, second_fidelity=.8, operation_error=.005)
    return value


def raw_generation(topology=None, cfg=None, endpoints=None, seed=1001):
    if topology is None:
        topology, cfg = relay_topology()
    cmd = command(cfg, endpoints=endpoints)
    raw = generate_observation(np.random.default_rng(seed), cmd, cfg, environment(topology), cfg.parameters)
    return topology, cfg, cmd, raw


def exported(runtime, path):
    runtime.export_events(path)
    return [json.loads(row) for row in path.read_text(encoding='utf-8').splitlines()]


def test_additive_migration_preserves_records_and_declares_exclusive_resources():
    before, _ = expand_global(brazil_institutions())
    after, report = expand_orbital_relay(before)
    assert after.nodes[:len(before.nodes)] == before.nodes
    assert after.links[:len(before.links)] == before.links
    assert len(after.nodes) == len(before.nodes) + 4 and len(after.links) == len(before.links) + 3
    assert report['preserved_original_records'] and not report['physical_adapter_validated']
    assert not report['certified_secret_bits']
    assert all(row['reference_visible'] and 0 < row['reference_transmission'] < 1 for row in report['connection_matrix'])
    for node_id in PATH:
        banks = after.node(node_id).telemetry['simulation_resource_banks']
        assert len(banks) == 1 and banks[0]['memory_modes'] == 16 and banks[0]['exclusive_device']
    for ogs, site in ((PATH[0], 'CBPF'), (PATH[-1], 'BSC')):
        assert after.node(ogs).telemetry['serves_institution'] == site
        assert after.node(ogs).telemetry['location'] == before.node(site).telemetry['location']
    with pytest.raises(ValueError, match='conflict'):
        expand_orbital_relay(after)


def test_registered_relay_admission_and_both_channels():
    topology, cfg = relay_topology()
    default_registry().validate(cfg, topology)
    assert 'orbital_relay' in default_registry().adapters
    for link in topology.links:
        assert link.channels['quantum']['protocol'] == 'quantum-operation-record-v1'
        assert link.channels['quantum']['channel_id'] != link.channels['classical']['channel_id']


def test_dedicated_inventory_counts_sources_by_emission_and_terminals_by_incident_span():
    topology, cfg = relay_topology()
    expected = {PATH[0]: (0, 1), PATH[1]: (2, 2), PATH[2]: (1, 2), PATH[3]: (0, 1)}
    for node_id, (sources, terminals) in expected.items():
        bank = topology.node(node_id).telemetry['simulation_resource_banks'][0]
        emitted = [link.link_id for link in topology.links if link.left_node_id == node_id]
        incident = [link.link_id for link in topology.links
                    if node_id in (link.left_node_id, link.right_node_id)]
        assert bank['pair_sources'] == sources == len(emitted)
        assert bank['pulse_sources'] == 0
        assert bank['optical_terminals'] == terminals == len(incident)
        assert bank['emission_link_ids'] == emitted
        assert bank['quantum_link_ids'] == incident
        assert bank['memory_modes'] == cfg.parameters['memory_slots']


@pytest.mark.parametrize('index', range(3))
def test_physical_link_direction_matches_raw_photon_sender_and_receiver(index):
    topology, cfg = relay_topology()
    _, _, _, raw = raw_generation(topology, cfg, endpoints=PATH[index:index + 2])
    link = next(link for link in topology.links if link.link_id == LINK_IDS[index])
    geometry = raw['geometry']
    assert cfg.path == PATH  # Stored-pair routing order is independent of downlink direction.
    assert link.left_node_id == geometry['photon_sender']
    assert link.right_node_id == geometry['photon_receiver']
    assert link.channels['quantum']['receiver_id'] == geometry['photon_receiver']
    assert link.technology['endpoints'] == [geometry['photon_sender'], geometry['photon_receiver']]
    assert link.technology['logical_path_endpoints'] == list(PATH[index:index + 2])


@pytest.mark.parametrize('mutation', ['no_capture', 'no_memory', 'wavelength', 'BBM92', 'destructive', 'bsm_limit'])
def test_invalid_resources_rejected_before_acquisition(mutation):
    topology, cfg = relay_topology()
    if mutation in ('no_capture', 'no_memory'):
        missing = 'heralded_photon_capture' if mutation == 'no_capture' else 'quantum_memory'
        n = topology.node(PATH[1])
        topology = replace(topology, nodes=tuple(replace(x, telemetry={**x.telemetry,
            'technology_capabilities': [c for c in x.telemetry['technology_capabilities'] if c != missing]})
            if x.node_id == n.node_id else x for x in topology.nodes))
    else:
        p = copy.deepcopy(cfg.parameters)
        if mutation == 'wavelength':
            p['device_parameters'][PATH[0]]['wavelength_nm'] = 1550.
        elif mutation == 'BBM92':
            p['quantum_encoding'] = 'polarization_BBM92'
        elif mutation == 'bsm_limit':
            p['bsm_success'] = .51
        elif mutation == 'destructive':
            cfg = replace(cfg, technologies=(*cfg.technologies, 'satellite_isl'))
        cfg = replace(cfg, parameters=p)
    with pytest.raises(ProtocolError):
        OrbitalRelayAdapter().validate(cfg, topology)


@pytest.mark.parametrize('endpoints', [PATH[:2], PATH[1:3], PATH[2:]])
def test_raw_capture_events_probe_consumption_and_optical_ranges(endpoints):
    _, cfg, cmd, raw = raw_generation(endpoints=endpoints)
    result = OrbitalRelayAdapter().process(raw, context(cfg, cmd, raw))
    probes = sum(sum(counts.values()) for counts in raw['correlations'].values())
    assert raw['heralds'] > 12 and probes > 0
    assert result['tomography_consumed'] == probes
    assert result['available_pairs'] + result['tomography_consumed'] == raw['heralds']
    assert raw['memory_writes'] == result['available_pairs']
    assert raw['heralds'] + raw['capture_capacity_rejections'] == raw['optical_capture_candidates']
    assert raw['memory_writes'] <= raw['capture_mode_budget'] <= cfg.parameters['memory_slots']
    assert raw['quantum_flight_s'] > .1 and result['geometry'][0]['visible']
    assert result['geometry'][0]['range_km'] == pytest.approx(raw['effective_length_km'])
    assert 'final_fidelity' not in raw and 'tdc' not in raw and 'final_key' not in raw
    assert result['satellite_housekeeping'] and .5 < result['fidelity'] <= 1.


@pytest.mark.parametrize('mutation', ['tdc', 'fake_eta', 'capture_on_blocked', 'wrong_endpoint', 'past_epoch', 'flight', 'no_probe', 'overflow', 'missing_lock', 'early'])
def test_admission_rejects_destructive_fabricated_or_noncausal_records(mutation):
    _, cfg, cmd, original = raw_generation()
    raw = copy.deepcopy(original)
    ctx = context(cfg, cmd, raw)
    if mutation == 'tdc':
        raw['tdc'] = []
    elif mutation == 'fake_eta':
        raw['optical_transmission'] = 1.
    elif mutation == 'capture_on_blocked':
        raw['tracking'][PATH[0]]['locked'] = False
    elif mutation == 'wrong_endpoint':
        raw['segment_endpoints'] = list(PATH[1:3])
    elif mutation == 'past_epoch':
        raw['housekeeping'][PATH[1]]['at_s'] = 1.
    elif mutation == 'flight':
        raw['quantum_flight_s'] = 0.
    elif mutation == 'no_probe':
        raw['correlations'] = {}
    elif mutation == 'overflow':
        raw['memory_writes'] = raw['heralds']
    elif mutation == 'missing_lock':
        raw['tracking'] = {}
    elif mutation == 'early':
        ctx['observation_at_s'] = .5
    with pytest.raises(ProtocolError):
        OrbitalRelayAdapter().process(raw, ctx)


def test_no_characterization_does_not_invent_a_stored_pair():
    topology, cfg = relay_topology(attempt_rate_hz=1.)
    _, _, cmd, raw = raw_generation(topology, cfg)
    result = OrbitalRelayAdapter().process(raw, context(cfg, cmd, raw))
    assert not result['calibration_available'] and result['fidelity'] is None
    assert result['available_pairs'] == raw['memory_writes'] == 0


@pytest.mark.parametrize('field,value', [('ground_stations', []), ('housekeeping', []),
    ('tracking', 1), ('tracking', {PATH[0]: 'invalid'}), ('correlations', ['xx']),
    ('correlations', {'xx': 1, 'yy': {}, 'zz': {}})])
def test_malformed_containers_fail_with_protocol_error_instead_of_worker_crash(field, value):
    _, cfg, cmd, raw = raw_generation()
    raw[field] = value
    with pytest.raises(ProtocolError):
        OrbitalRelayAdapter().process(raw, context(cfg, cmd, raw))


def test_off_segment_satellite_vector_is_validated_before_ui_publication():
    _, cfg, cmd, raw = raw_generation(endpoints=PATH[:2])
    raw['housekeeping'][PATH[2]]['ecef_km'] = ['bad', 1., 2.]
    with pytest.raises(ProtocolError, match='coordinates'):
        OrbitalRelayAdapter().process(raw, context(cfg, cmd, raw))


@pytest.mark.parametrize('window,expected', [([0., .25], False), ([0., .5], True), ([.5, 1.], False)])
def test_capture_batch_respects_complete_communication_window(window, expected):
    topology, cfg = relay_topology()
    p = copy.deepcopy(cfg.parameters)
    p['satellite_orbits'][PATH[1]]['communication_windows'] = [window]
    cfg = replace(cfg, parameters=p)
    _, _, cmd, raw = raw_generation(topology, cfg)
    result = OrbitalRelayAdapter().process(raw, context(cfg, cmd, raw))
    assert result['available'] is expected
    if not expected:
        assert raw['heralds'] == raw['memory_writes'] == 0
        assert 'communication_window' in result['geometry'][0]['block_reasons']


def bell(x, z):
    """Independent computational-basis state: (I tensor X^x Z^z)|Phi+>."""
    state = np.zeros((2, 2), complex)
    for bit in (0, 1):
        state[bit, bit ^ x] = (-1) ** (bit * z) / np.sqrt(2.)
    return state.reshape(4)


def project_two_pairs(first, second, outcome):
    """Exact four-qubit amplitudes projected on the two middle qubits."""
    psi = np.kron(bell(*first), bell(*second)).reshape(2, 2, 2, 2)
    measure = bell(outcome // 2, outcome % 2).reshape(2, 2)
    output = np.einsum('abcd,bc->ad', psi, measure.conj()).reshape(4)
    probability = float(np.vdot(output, output).real)
    return probability, output / np.sqrt(probability)


@pytest.mark.parametrize('first', [(0, 0), (0, 1), (1, 0), (1, 1)])
@pytest.mark.parametrize('second', [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_pauli_frame_matches_exact_four_qubit_projection(first, second):
    ctx = {'operation': 'bsm', 'parameters': {'bsm_kind': 'abstract_heralded'},
           'first_frame': first, 'second_frame': second, 'first_fidelity': 1.,
           'second_fidelity': 1., 'operation_error': 0., 'parent_ids': ['a', 'b']}
    for outcome in range(4):
        raw = {'orbital_relay_schema': ORBITAL_SCHEMA, 'start_s': 0., 'parent_ids': ['a', 'b'],
               'memory_readouts': [1, 1], 'detector_clicks': [1, 1],
               'bsm_success': 1, 'bell_outcome': outcome}
        result = OrbitalRelayAdapter().process(raw, ctx)
        probability, exact = project_two_pairs(first, second, outcome)
        assert probability == pytest.approx(.25)
        assert abs(np.vdot(exact, bell(*result['pauli_frame_bits']))) ** 2 == pytest.approx(1.)
        assert result['fidelity'] == pytest.approx(1.)


@pytest.mark.parametrize('fidelities', [(.6, .7), (.8, .9), (.97, .95)])
def test_werner_swap_estimate_matches_independent_density_matrix(fidelities):
    f, h = fidelities
    def density(v):
        return v * np.outer(bell(0, 0), bell(0, 0).conj()) + (1. - v) * np.eye(4) / 4.
    # Convert F to visibility only in the independently constructed density.
    pair = np.kron(density((4. * f - 1.) / 3.), density((4. * h - 1.) / 3.)).reshape((2,) * 8)
    for outcome in (2, 3):
        b = bell(outcome // 2, outcome % 2).reshape(2, 2)
        output = np.einsum('abcdefgh,bc,fg->adeh', pair, b.conj(), b).reshape(4, 4)
        probability = np.trace(output).real
        output /= probability
        target = bell(outcome // 2, outcome % 2)
        expected = float(np.vdot(target, output @ target).real)
        raw = {'orbital_relay_schema': ORBITAL_SCHEMA, 'start_s': 0., 'parent_ids': ['a', 'b'],
               'memory_readouts': [1, 1], 'detector_clicks': [1, 1], 'bsm_success': 1, 'bell_outcome': outcome}
        result = OrbitalRelayAdapter().process(raw, {'operation': 'bsm', 'parameters': {},
            'first_fidelity': f, 'second_fidelity': h, 'operation_error': 0., 'parent_ids': ['a', 'b']})
        assert probability == pytest.approx(.25)
        assert result['fidelity'] == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize('mutation', ['same_parent', 'other_parent', 'fake_click', 'fake_success', 'failed_outcome'])
def test_bsm_resource_bindings_and_outcomes_fail_closed(mutation):
    topology, cfg = relay_topology(memory_read_efficiency=1., detector_efficiency=1.)
    cmd = command(cfg, operation='bsm')
    rng = np.random.default_rng(13)
    raw = observe_bsm(rng, cmd, cfg, environment(topology), cfg.parameters)
    raw.update(bsm_success=1, bell_outcome=2, memory_readouts=[1, 1], detector_clicks=[1, 1])
    if mutation == 'same_parent':
        raw['parent_ids'] = ['parent-a', 'parent-a']
    elif mutation == 'other_parent':
        raw['parent_ids'] = ['other', 'parent-b']
    elif mutation == 'fake_click':
        raw['memory_readouts'] = [0, 1]
    elif mutation == 'fake_success':
        raw['detector_clicks'] = [0, 1]
    elif mutation == 'failed_outcome':
        raw['bsm_success'] = 0
    with pytest.raises(ProtocolError):
        OrbitalRelayAdapter().process(raw, context(cfg, cmd, raw))


def test_bsm_success_probability_has_independent_branch_read_and_detection_factors():
    topology, cfg = relay_topology(memory_read_efficiency=.7, detector_efficiency=.8)
    cmd = command(cfg, operation='bsm')
    rng = np.random.default_rng(20261010)
    size = 3000
    successes = sum(observe_bsm(rng, cmd, cfg, environment(topology), cfg.parameters)['bsm_success'] for _ in range(size))
    probability = .5 * .7 ** 2 * .8 ** 2
    sigma = np.sqrt(size * probability * (1. - probability))
    assert abs(successes - size * probability) < 5. * sigma


def test_identical_raw_records_process_through_simulated_file_and_physical_envelopes(tmp_path):
    topology, cfg = relay_topology()
    source = SimulatedAcquisitionSource(41)
    cmd = command(cfg)
    measured = source.acquire(cmd, cfg, environment(topology))
    expected = OrbitalRelayAdapter().process(measured.raw, context(cfg, cmd, measured.raw))
    fixture = replace(measured, source_id='measurement_file', origin='measurement_file')
    path = tmp_path / 'captured.jsonl'
    path.write_text(json.dumps(fixture.to_dict()) + '\n', encoding='utf-8')
    replay = MeasurementFileSource(path)
    raw = replay.acquire(cmd, cfg, environment(topology))
    assert raw.raw == measured.raw
    assert OrbitalRelayAdapter().process(raw.raw, context(cfg, cmd, raw.raw)) == expected
    assert replay.acquire(cmd, cfg, environment(topology)) is None
    queue = ReadOnlyQueueSource(source_id='fixture_physical_contract')
    fixture = replace(measured, source_id=queue.source_id, origin=queue.origin)
    queue.submit(fixture)
    observed = queue.acquire(cmd, cfg, environment(topology))
    assert isinstance(observed, Acquisition) and observed.origin == 'physical'
    assert OrbitalRelayAdapter().process(observed.raw, context(cfg, cmd, observed.raw)) == expected
    assert not queue.physical_validated  # Synthetic fixture is not hardware qualification.


def run_runtime(tmp_path, topology, seconds=35, source=None, name='runtime'):
    runtime = ProtocolRuntime(tmp_path / f'{name}.sqlite', topology.topology_id,
                              source=source or SimulatedAcquisitionSource(1001))
    for tick in range(1, seconds + 1):
        runtime.advance(topology, tick)
        session = runtime.sessions[SESSION_ID]
        occupancy = Counter(n for pair in session.pairs for n in (pair.a, pair.b))
        occupancy.update(n for endpoints in session.held_memories.values() for n in endpoints)
        for endpoints, modes in session.capture_reservations.values():
            occupancy.update({n: modes for n in endpoints})
        for ident, used in occupancy.items():
            installed = {**session.config.parameters,
                         **session.config.parameters.get('device_parameters', {}).get(ident, {})}['memory_slots']
            assert 0 <= used <= installed
    return runtime


def test_runtime_delivers_through_ground_space_space_ground_with_exact_once_consumption(tmp_path):
    topology, cfg = relay_topology()
    runtime = run_runtime(tmp_path, topology)
    rows = exported(runtime, tmp_path / 'orbital-events.jsonl')
    kinds = {r['event'] for r in rows}
    assert not ({'processing_error', 'configuration_rejected'} & kinds), [r for r in rows if r['event'] == 'processing_error']
    assert {'quantum_transmission_observed', 'classical_received', 'resource_stored',
            'resources_reserved_and_consumed', 'pauli_frame_recorded', 'pauli_frame_acknowledged', 'end_to_end_delivered'} <= kinds
    session = runtime.sessions[SESSION_ID]
    assert session.counts['delivered_pairs'] > 0 and session.counts['bsm_successes'] > 0
    consumed = [parent for row in rows if row['event'] == 'resources_reserved_and_consumed' for parent in row['detail']['parent_ids']]
    assert len(consumed) == len(set(consumed))
    stored = {r['detail']['pair_id']: r for r in rows if r['event'] == 'resource_stored'}
    for row in rows:
        if row['event'] != 'end_to_end_delivered':
            continue
        assert row['detail']['pair_id'] in stored
        pair = stored[row['detail']['pair_id']]
        assert pair['at_s'] <= row['at_s']
        assert pair['detail']['parent_ids']
        output_command_id = pair['detail']['pair_id'].removesuffix(':output')
        ack = [r for r in rows if r['command_id'] == output_command_id and r['event'] == 'pauli_frame_acknowledged']
        assert len(ack) == 1 and ack[0]['at_s'] <= pair['at_s']
    occupancy = session.metrics['memory_qubit_occupancy']
    assert all(value <= cfg.parameters['memory_slots'] for value in occupancy.values())
    assert session.counts['tomography_consumed'] > 0 and session.counts['capture_capacity_rejections'] > 0
    assert session.metrics['certified_secret_bits'] == 0


def test_physical_obstructions_memory_decay_and_sync_change_actual_delivery(tmp_path):
    nominal, _ = relay_topology()
    good = run_runtime(tmp_path, nominal, name='nominal')
    short, _ = relay_topology(coherence_s=.01, memory_cutoff_s=.02)
    bad = run_runtime(tmp_path, short, name='short-memory')
    assert good.sessions[SESSION_ID].counts['delivered_pairs'] > 0
    assert bad.sessions[SESSION_ID].counts['delivered_pairs'] == 0
    assert bad.sessions[SESSION_ID].counts['memory_expired'] > 0
    topology, cfg = relay_topology()
    orbits = copy.deepcopy(cfg.parameters['satellite_orbits'])
    orbits[PATH[2]]['reference_longitude'] = 120.
    blocked, _ = relay_topology(satellite_orbits=orbits)
    failed = run_runtime(tmp_path, blocked, name='blocked')
    assert failed.sessions[SESSION_ID].counts['delivered_pairs'] == 0
    assert any(not segment['visible'] for segment in failed.sessions[SESSION_ID].metrics['geometry'])


def test_full_captured_runtime_replay_keeps_protocol_results_and_causal_times(tmp_path):
    topology, _ = relay_topology()
    class RecordingSource(SimulatedAcquisitionSource):
        def __init__(self, seed):
            super().__init__(seed)
            self.captured = []

        def acquire(self, cmd, cfg, env):
            observation = super().acquire(cmd, cfg, env)
            if observation is not None:
                self.captured.append(observation)
            return observation

    source = RecordingSource(1001)
    generated = run_runtime(tmp_path, topology, seconds=22, source=source, name='capture-original')
    path = tmp_path / 'runtime-acquisitions.jsonl'
    path.write_text('\n'.join(json.dumps(replace(record, source_id='measurement_file',
                    origin='measurement_file').to_dict()) for record in source.captured) + '\n', encoding='utf-8')
    replay = run_runtime(tmp_path, topology, seconds=22, source=MeasurementFileSource(path), name='capture-replay')
    original_session = generated.sessions[SESSION_ID]
    replayed_session = replay.sessions[SESSION_ID]
    assert original_session.counts['delivered_pairs'] > 0
    assert original_session.counts == replayed_session.counts
    assert original_session.delivered_fidelities == replayed_session.delivered_fidelities
    first = exported(generated, tmp_path / 'capture-events.jsonl')
    second = exported(replay, tmp_path / 'replay-events.jsonl')
    assert [(r['event'], r['at_s'], r['command_id']) for r in first] == [
        (r['event'], r['at_s'], r['command_id']) for r in second]


def test_lost_bsm_observations_timeout_without_restoring_consumed_parents_and_recover(tmp_path):
    class InterruptedSource(SimulatedAcquisitionSource):
        blocked = True

        def acquire(self, cmd, cfg, env):
            if self.blocked and cmd.operation == 'bsm':
                return None
            return super().acquire(cmd, cfg, env)

    topology, _ = relay_topology()
    source = InterruptedSource(1001)
    runtime = ProtocolRuntime(tmp_path / 'timeouts.sqlite', topology.topology_id, source=source)
    for tick in range(1, 11):
        runtime.advance(topology, tick)
    session = runtime.sessions[SESSION_ID]
    assert session.counts['delivered_pairs'] == 0 and session.counts['errors'] > 0
    source.blocked = False
    for tick in range(11, 36):
        runtime.advance(topology, tick)
    rows = exported(runtime, tmp_path / 'recovery-events.jsonl')
    failed_parents = {parent for row in rows if row['event'] == 'operation_error'
                      for parent in row['detail'].get('input_resources', [])}
    assert failed_parents
    outputs = [row for row in rows if row['event'] == 'resource_stored' and row['detail']['parent_ids']]
    assert all(not (set(row['detail']['parent_ids']) & failed_parents) for row in outputs)
    consumed = [p for row in rows if row['event'] == 'resources_reserved_and_consumed' for p in row['detail']['parent_ids']]
    assert len(consumed) == len(set(consumed))
    assert session.counts['delivered_pairs'] > 0


def test_network_engine_observes_service_and_global_demand_receipts(tmp_path):
    topology, _ = relay_topology()
    store = SimulatedTopologyStore(tmp_path / 'engine.sqlite')
    store.initialize(topology)
    engine = NetworkEngine(store, store.path, seed=1001, topology_id=topology.topology_id,
                           source=SimulatedInputSource(1001, 'quiet'))
    for _ in range(40):
        engine.step()
    runtime = engine.technologies
    assert runtime.sessions[SESSION_ID].counts['delivered_pairs'] > 0
    snapshot = engine.global_operations.snapshot()
    services = [d for d in snapshot['demands'] if d.get('session_id') == SESSION_ID]
    assert services and any(d['successful_attempts'] > 0 for d in services)
    with sqlite3.connect(store.path) as connection:
        results = [json.loads(row[0]) for row in connection.execute(
            "SELECT record_json FROM global_operation_events WHERE event_kind='protocol_demand_result'")]
    assert any(row['detail']['kind'] == 'entanglement_pair_delivered' and
               row['detail'].get('technology_receipt') for row in results)
    assert snapshot['physical_actuation'] is False
