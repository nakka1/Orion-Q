"""Adversarial bridge tests; emulated worker is not scientific evidence."""
from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sequence_network_source import PIN, SequenceClassicalSource, validate_probe
from quantum_autopilot.network_engine.global_operations import ClassicalHopCommand, validate_classical_receipt
from quantum_autopilot.quantum_protocols.contracts import ProtocolError


def request():
    return {'command_id': 'd:1:0', 'sender': 'a', 'receiver': 'b', 'emitted_at_s': 2.,
            'propagation_s': .03, 'serialization_s': .02, 'jitter_s': .001, 'payload': {'a': 1}}


def response(req):
    arrival = req['emitted_at_s'] + sum(req[k] for k in ('propagation_s', 'serialization_s', 'jitter_s'))
    return {'schema': 'orionq.sequence-classical-probe.v1', 'command_id': req['command_id'],
            'payload': req['payload'], 'sent_at_s': req['emitted_at_s'],
            'received_at_s': arrival, 'native_commit': PIN,
            'events': [{'event': 'classical_sent', 'time_ps': round(req['emitted_at_s'] * 1e12),
                        'command_id': req['command_id'], 'sender': req['sender'], 'receiver': req['receiver']},
                       {'event': 'classical_received', 'time_ps': round(arrival * 1e12),
                        'command_id': req['command_id'], 'sender': req['sender'], 'receiver': req['receiver'], 'payload': req['payload']}],
            'native_module': 'sequence.components.optical_channel.ClassicalChannel'}


@pytest.mark.parametrize('field,value', [('schema', 'wrong'), ('command_id', 'other'), ('payload', '{}'),
    ('sent_at_s', 1.), ('received_at_s', 1.), ('received_at_s', float('nan')),
    ('received_at_s', float('inf')), ('received_at_s', True), ('events', []), ('native_module', 'local')])
def test_bridge_rejects_unbound_or_fabricated_metadata(field, value):
    row = response(request())
    row[field] = value
    with pytest.raises(ProtocolError):
        validate_probe(row, request())


class Worker:
    def probe(self, req):
        return response(req)


def command():
    return ClassicalHopCommand('d:1:0', 'd', 1, 0, 'l', 'assoc', 'classic', 'a', 'b', 2., 4., '{"a":1}')


def test_bridge_receipt_uses_existing_binding_and_replay_validation(tmp_path):
    source = SequenceClassicalSource(Worker(), tmp_path / 'events.jsonl', seed=4)
    cmd = command()
    row = source.acquire(cmd, {'propagation_s': .03, 'bitrate_bps': 1e6})
    validate_classical_receipt(row, cmd, source=source, now_s=row.observed_at_s, last_sequence=0)
    with pytest.raises(ProtocolError):
        validate_classical_receipt(row, cmd, source=source, now_s=row.observed_at_s, last_sequence=row.sequence)
    with pytest.raises(ProtocolError):
        validate_classical_receipt(replace(row, envelope_sha256='bad'), cmd, source=source,
                                   now_s=row.observed_at_s, last_sequence=0)
    source.close()
    logged = json.loads((tmp_path / 'events.jsonl').read_text())
    assert logged['association_id'] == 'assoc' and logged['admitted_to_orion'] is True


def test_loss_and_timeout_never_substitute_success(tmp_path):
    source = SequenceClassicalSource(Worker(), tmp_path / 'events.jsonl', seed=4)
    source.external_loss_probability = 1.
    row = source.acquire(command(), {'propagation_s': .03, 'bitrate_bps': 1e6})
    assert row.accepted is False
    source.external_timeout = True
    assert source.acquire(command(), {'propagation_s': .03, 'bitrate_bps': 1e6}) is None
    assert source.native_received == source.rejected == 2
    source.close()
