"""Private protocol proposal with a bounded public transcript.

Calculations may prepare a proposal locally. The runtime admits it only after
replaying every public exchange through its causal, command-bound channel queue.
These receipts describe simulated/file-replay transport, never authenticated RF.
"""
from .contracts import ProtocolError, canonical, digest


class ClassicalTranscript:
    def __init__(self, command, start_s, roundtrip_s, max_message_bytes, max_exchanges=50000):
        self.command = command
        self.cursor_s = start_s
        self.roundtrip_s = roundtrip_s
        self.max_message_bytes = max_message_bytes
        self.max_exchanges = max_exchanges
        self.exchanges = []
        self.total_bytes = 0

    def exchange(self, stage, payload):
        if len(self.exchanges) >= self.max_exchanges:
            raise ProtocolError('backpressure', 'classical postprocessing exchange quota reached')
        if not isinstance(stage, str) or not isinstance(payload, dict):
            raise ProtocolError('invalid_message', 'named public protocol message required')
        # Inputs are local private acquisition evidence. No preparations, raw
        # bits or derived keys are permitted in a public protocol disclosure.
        forbidden = {'alice_preparation', 'bob_preparation', 'alice_bits', 'bob_bits',
                     'alice_send','bob_send','signal_records','source_preparation',
                     'final_key', 'secure_key', 'key_bits', 'private_preparations'}
        def check(value):
            if isinstance(value, dict):
                if forbidden & set(value):
                    raise ProtocolError('private_disclosure', 'private material in public transcript')
                for item in value.values():
                    check(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    check(item)
        check(payload)
        body = {'message_id': f'{self.command.command_id}:public:{len(self.exchanges)+1}',
                'command_id': self.command.command_id, 'session_id': self.command.session_id,
                'stage': stage, 'sequence': len(self.exchanges)+1, 'payload': payload,
                'classical_channel_ids': list(self.command.classical_channel_ids),
                'associated_quantum_ids': list(self.command.quantum_channel_ids),
                'transmitted_at_s': self.cursor_s, 'received_at_s': self.cursor_s+self.roundtrip_s,
                'authenticated': False, 'transport_scope': 'local causal channel emulation'}
        size = len(canonical(body).encode('utf-8'))
        if size > 8*1024*1024:
            raise ProtocolError('message_limit', 'public transcript assembly exceeds 8 MiB')
        if self.total_bytes+size>64*1024*1024:
            raise ProtocolError('backpressure', 'public transcript total exceeds 64 MiB')
        self.total_bytes+=size
        body['payload_bytes'] = size
        body['fragments'] = max(1, (size+self.max_message_bytes-1)//self.max_message_bytes)
        body['receipt_sha256'] = digest(body)
        self.exchanges.append(body)
        self.cursor_s = body['received_at_s']
        return {'accepted': True, **body}
