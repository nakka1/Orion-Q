"""Campaign-only source: native channel receipts enter the existing control pipeline.

Only the observation source is replaced. Optical science remains the identified
local source, and externally imposed outages are not native RF/optical models.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading

from quantum_autopilot.network_engine.global_operations import ClassicalHopReceipt
from quantum_autopilot.quantum_protocols.contracts import ProtocolError, digest

PIN = '3133c66d80a898c6c3cc9af9eec3fa94fdbe2233'


class SequenceWorker:
    def __init__(self, python, source, directory, *, timeout_s=30.):
        self.timeout_s = timeout_s
        self.responses = queue.Queue()
        self.lock = threading.Lock()
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.stderr = (self.directory / 'stderr.log').open('x', encoding='utf-8')
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        env['PYTHONUNBUFFERED'] = '1'
        self.command = [str(Path(python).resolve()), str(Path(__file__).with_name('sequence_network_worker.py')),
                        '--sequence-src', str(Path(source).resolve())]
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.stderr, text=True, encoding='utf-8', env=env)

        def read():
            for line in self.process.stdout:
                try:
                    self.responses.put(json.loads(line))
                except (ValueError, TypeError):
                    self.responses.put({'invalid_json': True})
            self.responses.put({'worker_closed': True})

        self.reader = threading.Thread(target=read, name='sequence-campaign-reader', daemon=True)
        self.reader.start()
        try:
            ready = self._receive()
            metadata = ready.get('metadata', {})
            if (ready.get('schema') != 'orionq.sequence-worker-ready.v1' or metadata.get('commit') != PIN
                    or not metadata.get('source_sha256') or metadata.get('shims') != []
                    or metadata.get('product_imports') != []):
                raise ProtocolError('external_provenance', 'independent pinned SeQUeNCe worker required')
            self.metadata = metadata
            (self.directory / 'identity.json').write_text(json.dumps({'command': self.command, **ready}, indent=2), encoding='utf-8')
        except Exception:
            self.close()
            raise

    def _receive(self):
        try:
            value = self.responses.get(timeout=self.timeout_s)
        except queue.Empty as exc:
            raise ProtocolError('external_timeout', 'SeQUeNCe worker did not respond') from exc
        if not isinstance(value, dict) or 'worker_closed' in value or 'invalid_json' in value:
            raise ProtocolError('external_worker', 'SeQUeNCe worker ended or returned invalid JSON')
        return value

    def probe(self, request):
        with self.lock:
            if self.process.poll() is not None:
                raise ProtocolError('external_worker', 'SeQUeNCe worker unavailable; no local substitute')
            self.process.stdin.write(json.dumps(request, allow_nan=False) + '\n')
            self.process.stdin.flush()
            reply = self._receive()
            if reply.get('ok') is not True:
                raise ProtocolError('external_worker', str(reply.get('error', 'invalid worker response')))
            return reply['result']

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write('{"operation":"close"}\n')
                self.process.stdin.flush()
                self.process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()
        self.reader.join(timeout=5)
        self.stderr.close()


def validate_probe(value, request):
    if not isinstance(value, dict) or value.get('schema') != 'orionq.sequence-classical-probe.v1':
        raise ProtocolError('external_contract', 'native classical probe schema required')
    if value.get('command_id') != request['command_id'] or value.get('payload') != request['payload']:
        raise ProtocolError('external_binding', 'native command or received payload mismatch')
    for name in ('sent_at_s', 'received_at_s'):
        if type(value.get(name)) not in (int, float) or not math.isfinite(value[name]):
            raise ProtocolError('external_time', 'finite native timestamp required')
    expected = request['emitted_at_s'] + sum(request[key] for key in ('propagation_s', 'serialization_s', 'jitter_s'))
    if abs(value['sent_at_s'] - request['emitted_at_s']) > 2e-12 or abs(value['received_at_s'] - expected) > 2e-12:
        raise ProtocolError('external_time', 'native arrival does not match the declared channel duration')
    if value['received_at_s'] < value['sent_at_s']:
        raise ProtocolError('external_causality', 'arrival precedes transmission')
    events = value.get('events', [])
    if (len(events) != 2 or value.get('native_module') != 'sequence.components.optical_channel.ClassicalChannel'
            or value.get('native_commit') != PIN):
        raise ProtocolError('external_evidence', 'native transmission and callback evidence required')
    for event, kind, time_key in zip(events, ('classical_sent', 'classical_received'), ('sent_at_s', 'received_at_s')):
        if (event.get('event') != kind or event.get('command_id') != request['command_id']
                or event.get('sender') != request['sender'] or event.get('receiver') != request['receiver']
                or type(event.get('time_ps')) is not int or abs(event['time_ps'] / 1e12 - value[time_key]) > 2e-12):
            raise ProtocolError('external_evidence', 'native event identities or timestamps differ')
    if events[1].get('payload') != request['payload']:
        raise ProtocolError('external_integrity', 'native callback payload differs')
    return value


class SequenceClassicalSource:
    source_id = 'sequence_native_classical_campaign_v1'
    origin = 'simulated'
    physical_validated = False

    def __init__(self, worker, path, *, seed):
        self.worker, self.seed = worker, seed
        self.stream = Path(path).open('x', encoding='utf-8', newline='\n')
        self.sequence = 0
        self.phase = 'unspecified'
        self.external_loss_probability = 0.
        self.external_timeout = False
        self.native_received = 0
        self.rejected = 0
        self.maximum_delay_error_s = 0.

    def acquire(self, command, environment):
        self.sequence += 1
        sample = int(hashlib.sha256(f'{self.seed}:{command.command_id}'.encode()).hexdigest()[:16], 16) / 2**64
        request = {'command_id': command.command_id, 'sender': command.sender, 'receiver': command.receiver,
                   'emitted_at_s': command.emitted_at_s, 'propagation_s': environment['propagation_s'],
                   'serialization_s': len(command.serialized_envelope.encode('utf-8')) * 8 / environment['bitrate_bps'],
                   'jitter_s': environment.get('jitter_s', 0.) * sample, 'payload': json.loads(command.serialized_envelope)}
        value = validate_probe(self.worker.probe(request), request)
        self.native_received += 1
        expected = command.emitted_at_s + sum(request[key] for key in ('propagation_s', 'serialization_s', 'jitter_s'))
        self.maximum_delay_error_s = max(self.maximum_delay_error_s, abs(value['received_at_s'] - expected))
        loss = max(self.external_loss_probability, environment.get('packet_loss_probability', 0.))
        rejected = self.external_timeout or sample < loss
        self.rejected += int(rejected)
        self.stream.write(json.dumps({'sequence': self.sequence, 'phase': self.phase, 'channel_id': command.classical_channel_id,
                                     'association_id': command.association_id, 'request': request, 'native': value,
                                     'external_loss_probability': loss, 'external_timeout': self.external_timeout,
                                     'admitted_to_orion': not rejected}, sort_keys=True, allow_nan=False) + '\n')
        self.stream.flush()
        if self.external_timeout:
            return None
        return ClassicalHopReceipt(command.command_id, self.source_id, self.origin, self.sequence,
                                   value['received_at_s'], command.classical_channel_id, command.receiver,
                                   not rejected, digest(value['payload']),
                                   'external campaign delivery loss after native channel' if rejected else '')

    def close(self):
        self.stream.close()
