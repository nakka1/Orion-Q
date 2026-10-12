"""Admission contracts and independent native callbacks, never hardware claims."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from quantum_autopilot.control_plane.mixed_contracts import validate_record
from quantum_autopilot.lab.adapters import EmulatedPhotonAdapter
from quantum_autopilot.lab.mixed_sources import normalize_acquisition, normalize_sequence_callbacks
from quantum_autopilot.lab.observations import ClockReference
from quantum_autopilot.lab.photon import Acquisition, AcquisitionError, PhotonConfig, TimeTag, digest


ROOT = Path(__file__).resolve().parents[1]


def acquisition(count=6):
    configuration = PhotonConfig(duration_ps=10000)
    events = [TimeTag(index * 10, index % 2 + 1, 'mixed-fixture', 'fixture-tagger',
        configuration.configuration_id, configuration.clock_domain, 'simulated', 'sdk-fixture')
        for index in range(count)]
    return Acquisition('mixed-fixture', 'fixture-tagger', 'fixture-tagger-adapter', 'emulation', 'simulated',
        asdict(configuration), events, configuration.duration_ps, configuration.clock_domain, 'sdk-fixture')


def normalized(record, **options):
    return normalize_acquisition(record, node_id='N01', session_id='session-N01', **options)


def sequence_records(events, **options):
    return normalize_sequence_callbacks(events, node_id='N02', session_id='session-N02',
        configuration_id='a' * 64, clock_domain='aligned-fixture-clock',
        source_id='sequence-callback-fixture', adapter_id='fixture-sequence-adapter', **options)


def native_proof():
    return {'photon_id': 'N02.pulse-0', 'sender': 'N02', 'receiver': 'N03', 'link_id': 'L02-03',
            'quantum_channel_id': 'quantum-L02-03', 'transmitted_at_ps': 10,
            'received_at_ps': 20, 'detected_at_ps': 20}


def proof_events():
    proof = native_proof()
    return [
        {'event': 'quantum_sent', 'node_id': 'N02', 'time_ps': 10, 'photon_id': proof['photon_id'],
         'target': 'N03', 'link_id': 'L02-03', 'quantum_channel_id': proof['quantum_channel_id']},
        {'event': 'quantum_received', 'node_id': 'N03', 'time_ps': 20,
         **{key: value for key, value in proof.items() if key != 'detected_at_ps'}},
        {'event': 'detector_click', 'node_id': 'N03', 'time_ps': 20, 'callback_at_ps': 20,
         'timestamp_ps': 20, 'channel': 1, 'quantum_delivery': proof},
        {'event': 'classical_received', 'node_id': 'N02', 'time_ps': 30, 'link_id': 'L02-03',
         'sender': 'N03', 'quantum_delivery': proof},
    ]


def test_acquisition_conserves_raw_integer_ps_events_in_bounded_blocks():
    record = acquisition(13)
    envelopes = normalized(record, max_events=5, sequence=17)
    assert [value['observation']['sequence'] for value in envelopes] == [17, 18, 19, 20]
    assert envelopes[0]['observation']['data']['verified'] is True
    tags = [tag for value in envelopes if value['observation']['kind'] == 'timetags'
            for tag in value['observation']['data']['events']]
    assert tags == [{'timestamp_ps': value.timestamp_ps, 'channel': value.channel} for value in record.events]
    for value in envelopes:
        observation, provenance = validate_record(value)
        assert provenance['origin'] == 'simulated'
        assert provenance['record_sha256'] == digest(asdict(record))
        assert observation.kind not in {'resources', 'link_state'}
        assert all(field not in observation.data for field in ('basis', 'bit', 'fidelity', 'final_key', 'pair_id'))


def test_legacy_source_identity_is_retained_in_raw_hash_without_invalid_envelope():
    adapter = EmulatedPhotonAdapter()
    adapter.connect()
    adapter.configure(PhotonConfig(duration_ps=10**8))
    adapter.calibrate()
    record = adapter.acquire('existing-emulator')
    values = normalized(record)
    assert values[0]['provenance']['source_id'].startswith('source-')
    assert values[0]['provenance']['record_sha256'] == digest(asdict(record))
    assert record.source_id.endswith(';seed=810')


def test_overflow_counts_once_and_cannot_leave_a_verified_session():
    record = acquisition(9)
    marker = replace(record.events[-1], channel=0, event_type='MissedEvents', missed_events=13)
    record.events.extend([replace(marker, event_type='OverflowBegin', missed_events=0), marker])
    values = normalized(record, max_events=2)
    assert values[0]['observation']['data']['verified'] is False
    assert sum(value['observation']['data']['lost_events'] for value in values
               if value['observation']['kind'] == 'timetags') == 13
    assert any(value['observation']['data'] == {'reason': 'overflow'} for value in values)
    # Counts are known at the drain horizon, not attached before the loss occurred.
    assert all(value['observation']['timestamp_ps'] == record.duration_ps - 1
               for value in values if value['observation']['kind'] == 'timetags')


@pytest.mark.parametrize('flag', ['stream_buffer_saturated', 'disconnected', 'clock_drift',
                                 'detector_unavailable', 'native_error'])
def test_quality_faults_are_explicit_and_block_configuration_admission(flag):
    record = acquisition()
    record.diagnostics['quality_flags'] = [flag]
    values = normalized(record)
    assert values[0]['observation']['data']['verified'] is False
    assert values[-1]['observation']['data'] == {'reason': flag}


def test_empty_click_stream_does_not_invent_samples_or_resources():
    values = normalized(acquisition(0))
    assert values[-1]['observation']['data'] == {'events': [], 'lost_events': 0}
    assert len(values) == 2


def test_unlocked_clock_and_unaligned_reference_do_not_claim_ready():
    record = acquisition()
    record.clock_locked = False
    assert normalized(record)[0]['observation']['data']['verified'] is False
    with pytest.raises(AcquisitionError, match='does not identify'):
        normalized(record, clock_reference=ClockReference('other-clock'))
    record.clock_locked = True
    reference = ClockReference(record.clock_domain, locked=False)
    assert normalized(record, clock_reference=reference)[0]['observation']['data']['verified'] is False


def test_replay_preserves_original_raw_record_and_never_claims_live_hardware():
    record = acquisition()
    record.mode, record.origin, record.source_record = 'replay', 'physical', 'fixture-original-filewriter-record'
    record.events = [replace(event, origin='physical') for event in record.events]
    values = normalized(record)
    assert all(value['provenance']['origin'] == 'replay' for value in values)
    assert values[0]['provenance']['record_sha256'] == digest(asdict(record))


def test_physical_record_requires_existing_authenticated_native_authority():
    record = acquisition()
    record.mode, record.origin = 'hardware', 'physical'
    record.events = [replace(event, origin='physical') for event in record.events]
    with pytest.raises(AcquisitionError, match='authenticated instrument authority'):
        normalized(record, adapter={'state': 'ready', 'verified': True},
                   clock_reference=ClockReference(record.clock_domain))


@pytest.mark.parametrize('max_events', [True, 0, 4097, 1.5])
def test_invalid_block_budget_rejected_before_normalization(max_events):
    with pytest.raises(ValueError):
        normalized(acquisition(), max_events=max_events)


def test_raw_timestamp_regression_or_outside_interval_is_not_sorted_or_repaired():
    record = acquisition()
    record.events[0], record.events[1] = record.events[1], record.events[0]
    with pytest.raises(AcquisitionError, match='regression'):
        normalized(record)
    record = acquisition()
    record.events[-1] = replace(record.events[-1], timestamp_ps=record.duration_ps)
    with pytest.raises(AcquisitionError):
        normalized(record)


def test_native_click_alone_supplies_neither_operational_link_nor_entanglement():
    raw = {'event': 'detector_click', 'node_id': 'N02', 'time_ps': 20, 'timestamp_ps': 20, 'channel': 1}
    values = sequence_records([raw])
    assert values[-1]['observation']['kind'] == 'timetags'
    assert values[-1]['observation']['data'] == {'events': [{'timestamp_ps': 20, 'channel': 1}], 'lost_events': 0}
    assert values[-1]['provenance']['record_sha256'] == digest(raw)


def test_classical_receipt_without_quantum_receipt_keeps_quantum_unknown():
    raw = {'event': 'classical_received', 'node_id': 'N02', 'time_ps': 30, 'link_id': 'L02-03'}
    values = sequence_records([raw])
    assert values[-1]['observation']['data'] == {
        'link_id': 'L02-03', 'classical_state': 'up', 'quantum_state': 'unknown'}


def test_claimed_quantum_delivery_without_independent_callbacks_keeps_unknown():
    raw = proof_events()[-1]
    assert sequence_records([raw])[-1]['observation']['data']['quantum_state'] == 'unknown'


def test_quantum_availability_requires_tx_receive_detector_and_classical_receipt():
    raw = proof_events()
    values = sequence_records([raw[0], raw[-1]], delivery_evidence=raw)
    assert values[-1]['observation']['data']['quantum_state'] == 'up'
    assert values[-1]['observation']['kind'] == 'link_state'
    assert all(value['provenance']['origin'] == 'simulated' for value in values)
    assert all('fidelity' not in value['observation']['data'] for value in values)


@pytest.mark.parametrize('missing', [0, 1, 2])
def test_incomplete_native_delivery_evidence_rejects_quantum_claim(missing):
    raw = proof_events()
    with pytest.raises(AcquisitionError, match='matching native'):
        sequence_records([raw[-1]], delivery_evidence=raw[:missing] + raw[missing + 1:])


@pytest.mark.parametrize('field,value', [('transmitted_at_ps', 21), ('link_id', 'other-link'),
                                       ('receiver', 'N02'), ('detected_at_ps', 31)])
def test_invalid_delivery_identity_or_causality_is_rejected(field, value):
    raw = proof_events()[-1]
    raw['quantum_delivery'][field] = value
    with pytest.raises((AcquisitionError, ValueError)):
        sequence_records([raw])


def test_node_identity_regression_unknown_callbacks_and_guessed_keys_are_rejected():
    with pytest.raises(AcquisitionError, match='another node'):
        sequence_records([{'event': 'heartbeat', 'node_id': 'N03', 'time_ps': 1}])
    with pytest.raises(AcquisitionError, match='clock regressed'):
        sequence_records([{'event': 'heartbeat', 'node_id': 'N02', 'time_ps': time} for time in (2, 1)])
    with pytest.raises(AcquisitionError, match='unsupported native'):
        sequence_records([{'event': 'final_key', 'node_id': 'N02', 'time_ps': 1, 'key': 'not-admitted'}])


def test_brands_change_audit_provenance_without_changing_controller_observation():
    raw = [{'event': 'heartbeat', 'node_id': 'N02', 'time_ps': 2}]
    first = sequence_records(raw)
    second = deepcopy(first)
    for envelope in second:
        envelope['provenance']['source_id'] = 'alternate-native-fixture'
        envelope['provenance']['adapter_id'] = 'another-adapter'
        validate_record(envelope)
    assert [record['observation'] for record in first] == [record['observation'] for record in second]
    assert first[1]['provenance'] != second[1]['provenance']


def test_native_resources_are_explicit_slots_not_derived_from_click_counts():
    values = sequence_records([{'event': 'resources_available', 'node_id': 'N02', 'time_ps': 0, 'available': 4}])
    assert values[-1]['observation']['data'] == {'available': 4}
    assert all(value['observation']['kind'] != 'timetags' for value in values)


@pytest.mark.integration
def test_independent_pinned_sequence_executes_callbacks_and_physical_loss_changes_results(tmp_path):
    interpreter = Path(os.environ.get('SEQUENCE_PYTHON', ROOT / 'outputs/cross_simulator_preparation_20261010/sequence-venv/Scripts/python.exe'))
    source = Path(os.environ.get('SEQUENCE_SRC', 'C:/SeQUeNCe'))
    if not interpreter.is_file() or not (source / 'sequence/__init__.py').is_file():
        pytest.skip('independent pinned SeQUeNCe environment unavailable; no fixture fallback')
    results = []
    raw_runs = []
    for name, attenuation in (('nominal', '.2'), ('reproduce', '.2'), ('blocked', '100')):
        directory = tmp_path / name
        command = [str(interpreter), str(ROOT / 'tools/run_mixed_sequence_nodes.py'), '--sequence-src', str(source),
                   '--output', str(directory), '--seed', '2026101101', '--pulses', '16',
                   '--attenuation-db-km', attenuation, '--dark-count-hz', '0', '--length-m', '1000.011']
        environment = dict(os.environ)
        environment.pop('PYTHONPATH', None)
        completed = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=90)
        assert completed.returncode == 0, completed.stderr
        summary = json.loads((directory / 'summary.json').read_text())
        raw = [json.loads(line) for line in (directory / 'native-events.jsonl').read_text().splitlines()]
        assert summary['metadata']['commit'] == '3133c66d80a898c6c3cc9af9eec3fa94fdbe2233'
        assert summary['metadata']['product_imports'] == [] and summary['metadata']['shims'] == []
        assert summary['physical_action_performed'] is False
        assert summary['entanglement_established'] is False and summary['key_material_generated'] is False
        assert summary['node_ids'] == [f'N{number:02d}' for number in range(2, 12)]
        assert summary['raw_events_sha256'] == hashlib.sha256((directory / 'native-events.jsonl').read_bytes()).hexdigest()
        for node_id in summary['node_ids']:
            callbacks = [value for value in raw if value['node_id'] == node_id]
            assert [value['time_ps'] for value in callbacks] == sorted(value['time_ps'] for value in callbacks)
            assert [value['callback_at_ps'] for value in callbacks] == sorted(value['callback_at_ps'] for value in callbacks)
            assert all(value['callback_at_ps'] <= value['time_ps'] for value in callbacks)
            envelopes = normalize_sequence_callbacks(callbacks, node_id=node_id, session_id='session-' + node_id,
                configuration_id=summary['configuration_id'], clock_domain='aligned-fixture-clock',
                source_id=summary['source_id'], adapter_id=summary['adapter_id'], delivery_evidence=raw)
            assert any(value['observation']['kind'] == 'resources' for value in envelopes)
            assert any(value['observation']['data'].get('classical_state') == 'up' for value in envelopes)
            for value in envelopes:
                validate_record(value)
        results.append(summary)
        raw_runs.append(raw)
    assert raw_runs[0] == raw_runs[1]
    assert any(value['time_ps'] > value['callback_at_ps'] for value in raw_runs[0]
               if value['event'] == 'detector_click')
    assert results[0]['counters']['quantum_sent'] == results[2]['counters']['quantum_sent'] == 160
    assert results[0]['counters']['detector_click'] > 0
    assert results[2]['counters'].get('detector_click', 0) == 0
    assert results[2]['counters']['classical_received'] == 20
