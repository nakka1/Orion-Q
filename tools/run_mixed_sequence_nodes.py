"""Independent native SeQUeNCe signals for ten virtual nodes; no product imports.

This is a photonic/control-plane interoperability fixture, not a real instrument
or an entanglement/QKD protocol. Each reported delivery traverses QuantumChannel
and Detector; separate ClassicalChannel callbacks carry the control receipts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stdout
import hashlib
import importlib
import json
import math
import os
import re
import time
from pathlib import Path
import sys


def run(source, output, *, seed=2026101101, pulses=128, length_m=1000.,
        attenuation_db_km=.2, detector_efficiency=.75, dark_count_hz=100.,
        pulse_interval_ps=100_000_000, classical_delay_ps=10_000_000,
        interactive=False):
    if type(seed) is not int or seed < 0 or type(pulses) is not int or not 1 <= pulses <= 4096:
        raise ValueError("bounded integer seed/pulse count required")
    for value in (length_m, attenuation_db_km, detector_efficiency, dark_count_hz):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("finite declared optical parameters required")
    if not 1 <= length_m <= 100_000 or not 0 <= attenuation_db_km <= 100:
        raise ValueError("fiber fixture requires 1..100000 m and 0..100 dB/km")
    if not 0 <= detector_efficiency <= 1 or not 0 <= dark_count_hz <= 1e6:
        raise ValueError("invalid native detector parameters")
    if (type(pulse_interval_ps) is not int or not 1_000_000 <= pulse_interval_ps <= 10**12
            or type(classical_delay_ps) is not int or not 1 <= classical_delay_ps <= 10**12):
        raise ValueError("bounded integer ps cadence and delay required")
    if any(name == 'quantum_autopilot' or name.startswith('quantum_autopilot.') for name in sys.modules):
        raise RuntimeError("independent process required; product implementation is loaded")
    from sequence_network_backend import load_native
    api, metadata = load_native(source)
    photon_module = importlib.import_module('sequence.components.photon')
    detector_module = importlib.import_module('sequence.components.detector')
    memory_module = importlib.import_module('sequence.components.memory')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    node_ids = [f'N{number:02d}' for number in range(2, 12)]
    stop_ps = 10**9 + pulses * pulse_interval_ps + int(length_m / 2e-4) + 3 * classical_delay_ps
    timeline = api['timeline'].Timeline(stop_time=stop_ps, formalism=api['constants'].KET_VECTOR_FORMALISM)
    events = []
    counters = Counter()
    observed_horizons = {}
    callback_clocks = {}

    def record(kind, node_id, **fields):
        callback_time = int(timeline.now())
        if callback_time < callback_clocks.get(node_id, -1):
            raise RuntimeError('native timeline callback clock regressed')
        callback_clocks[node_id] = callback_time
        # Detector quantization may round a tag up by half its resolution.
        # The observed horizon must include that tag and remain nondecreasing
        # for subsequent callbacks; actual causal time is retained separately.
        horizon = max(callback_time, fields.get('timestamp_ps', callback_time),
                      observed_horizons.get(node_id, -1))
        observed_horizons[node_id] = horizon
        value = {'event': kind, 'node_id': node_id, 'time_ps': horizon,
                 'callback_at_ps': callback_time, **fields}
        events.append(value)
        counters[kind] += 1

    class SignalNode(api['node'].Node):
        def __init__(self, name, native_seed):
            super().__init__(name, timeline, seed=native_seed)
            self.detectors = {}
            self.active_delivery = None
            self.operational = True
            for channel in (1, 2):
                detector = detector_module.Detector(f'{name}.detector{channel}', timeline,
                    efficiency=detector_efficiency, dark_count=dark_count_hz,
                    count_rate=25e6, time_resolution=100)
                self.add_component(detector)
                detector.attach(self)
                self.detectors[channel] = detector
            self.memory_bank = memory_module.MemoryArray(f'{name}.memories', timeline,
                num_memories=4, fidelity=.9, efficiency=.8, coherence_time=.1)
            self.add_component(self.memory_bank)

        def report_resources(self):
            # Native empty memory slots are not entangled pairs or final keys.
            available = sum(memory.entangled_memory['node_id'] is None for memory in self.memory_bank.memories)
            record('resources_available', self.name, available=available,
                   resource_kind='unoccupied_native_memory_slots')

        def emit_pulse(self, target, link_id, index):
            if not self.operational:
                return
            photon_id = f'{self.name}.pulse-{index}'
            photon = photon_module.Photon(photon_id, timeline, wavelength=810, location=self)
            photon.acquisition_channel = index % 2 + 1
            photon.transmitted_at_ps = int(timeline.now())
            photon.link_id = link_id
            record('quantum_sent', self.name, photon_id=photon_id, target=target,
                   link_id=link_id, quantum_channel_id=self.qchannels[target].name)
            self.send_qubit(target, photon)

        def receive_qubit(self, source, photon):
            if not self.operational:
                return
            proof = {'photon_id': photon.name, 'sender': source, 'receiver': self.name,
                     'link_id': photon.link_id,
                     'quantum_channel_id': nodes[source].qchannels[self.name].name,
                     'transmitted_at_ps': photon.transmitted_at_ps,
                     'received_at_ps': int(timeline.now())}
            record('quantum_received', self.name, **proof)
            self.active_delivery = proof
            try:
                self.detectors[photon.acquisition_channel].get(photon)
            finally:
                self.active_delivery = None

        def trigger(self, detector, info):
            timestamp = int(info['time'])
            channel = next(channel for channel, component in self.detectors.items() if component is detector)
            proof = ({**self.active_delivery, 'detected_at_ps': int(timeline.now())}
                     if self.active_delivery is not None else None)
            record('detector_click', self.name, timestamp_ps=timestamp, channel=channel,
                   detector_id=detector.name, callback_at_ps=int(timeline.now()), quantum_delivery=proof)
            if proof is not None:
                self.emit_control(proof['sender'], proof['link_id'], proof)

        def emit_control(self, target, link_id, proof=None):
            if not self.operational:
                return
            message = api['message'].Message('mixed-telemetry', 'signals')
            message.payload = {'link_id': link_id, 'quantum_delivery': proof}
            record('classical_sent', self.name, target=target, link_id=link_id,
                   classical_channel_id=self.cchannels[target].name,
                   payload_sha256=hashlib.sha256(json.dumps(message.payload, sort_keys=True).encode()).hexdigest())
            self.send_message(target, message)

        def source_fault(self):
            self.operational = False
            record('fault', self.name, reason='identified_native_source_outage')

        def forward_control(self, request):
            index = request['route'].index(self.name)
            if index == len(request['route']) - 1:
                record('heartbeat', self.name, command_id=request['request_id'],
                       receipt_sha256=request['receipt_sha256'], command_delivered=True)
                return
            target = request['route'][index + 1]
            link = next(link for link in links if {link['a'], link['b']} == {self.name, target})
            message = api['message'].Message('mixed-authorized-control', 'signals')
            message.payload = {'link_id': link['link_id'], 'quantum_delivery': None,
                               'authorized_control': request, 'hop': index + 1}
            record('classical_sent', self.name, target=target, link_id=link['link_id'],
                   classical_channel_id=self.cchannels[target].name,
                   command_id=request['request_id'], hop=index + 1,
                   payload_sha256=hashlib.sha256(json.dumps(message.payload, sort_keys=True).encode()).hexdigest())
            self.send_message(target, message)

        def receive_message(self, source, message):
            if not self.operational:
                record('fault', self.name, reason='identified_native_source_unavailable')
                return
            payload = message.payload
            extra = {}
            control = payload.get('authorized_control')
            if control is not None:
                hop = payload['hop']
                if control['route'][hop] != self.name or control['route'][hop - 1] != source:
                    raise RuntimeError('native control hop does not match admitted route')
                extra = {'command_id': control['request_id'], 'hop': hop,
                         'receipt_sha256': control['receipt_sha256']}
            record('classical_received', self.name, sender=source, link_id=payload['link_id'],
                   classical_channel_id=nodes[source].cchannels[self.name].name,
                   quantum_delivery=payload['quantum_delivery'], **extra)
            if control is not None:
                self.forward_control(control)

    nodes = {node_id: SignalNode(node_id, seed + index) for index, node_id in enumerate(node_ids)}
    links = []
    for index, sender_id in enumerate(node_ids):
        receiver_id = node_ids[(index + 1) % len(node_ids)]
        link_id = f'L{sender_id[1:]}-{receiver_id[1:]}'
        quantum_id = 'quantum-' + link_id
        classical_id = 'classical-' + link_id
        quantum = api['channel'].QuantumChannel(quantum_id, timeline,
            attenuation=attenuation_db_km / 1000, distance=length_m, frequency=1e7)
        quantum.set_ends(nodes[sender_id], receiver_id)
        for source_id, target_id in ((sender_id, receiver_id), (receiver_id, sender_id)):
            classical = api['channel'].ClassicalChannel(f'{classical_id}.{source_id}', timeline,
                distance=length_m, delay=classical_delay_ps)
            classical.set_ends(nodes[source_id], target_id)
        links.append({'link_id': link_id, 'a': sender_id, 'b': receiver_id,
                      'classical_channel_id': classical_id, 'quantum_channel_id': quantum_id})
    timeline.init()
    for node in nodes.values():
        timeline.schedule(api['event'].Event(0, api['process'].Process(node, 'report_resources', [])))
    for link in links:
        sender, receiver = nodes[link['a']], nodes[link['b']]
        timeline.schedule(api['event'].Event(0, api['process'].Process(sender, 'emit_control',
            [receiver.name, link['link_id']])))
        timeline.schedule(api['event'].Event(0, api['process'].Process(receiver, 'emit_control',
            [sender.name, link['link_id']])))
        for index in range(pulses):
            time_ps = 10**9 + index * pulse_interval_ps
            timeline.schedule(api['event'].Event(time_ps, api['process'].Process(sender, 'emit_pulse',
                [receiver.name, link['link_id'], index])))
    timeline.run()
    with (output / 'native-events.jsonl').open('x', encoding='utf-8', newline='\n') as stream:
        for event in events:
            stream.write(json.dumps(event, sort_keys=True, allow_nan=False) + '\n')
    if any(name == 'quantum_autopilot' or name.startswith('quantum_autopilot.') for name in sys.modules):
        raise RuntimeError("product import appeared in independent simulation")
    parameters = {'seed': seed, 'pulses_per_link': pulses, 'length_m': length_m,
                  'attenuation_db_km': attenuation_db_km, 'detector_efficiency': detector_efficiency,
                  'dark_count_hz': dark_count_hz, 'pulse_interval_ps': pulse_interval_ps,
                  'classical_delay_ps': classical_delay_ps, 'duration_ps': stop_ps}
    summary = {'schema': 'orion.mixed-sequence-native.v1', 'status': 'executed',
               'origin': 'simulated', 'source_id': 'sequence-native-mixed-v1',
               'adapter_id': 'sequence-native-callbacks-v1', 'metadata': metadata,
               'parameters': parameters, 'node_ids': node_ids, 'links': links,
               'counters': dict(counters), 'timeline_run_counter': timeline.run_counter,
               'raw_events_sha256': hashlib.sha256((output / 'native-events.jsonl').read_bytes()).hexdigest(),
               'configuration_id': hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest(),
               'physical_action_performed': False, 'entanglement_established': False,
               'key_material_generated': False,
               'limits': ['Photonic/control observations and unoccupied native memory slots only.',
                          'No physical source or Time Tagger is connected.',
                          'Native detector clicks do not establish entanglement or QKD bits.',
                          'No native classical-channel loss model; faults require separate identified injection.']}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    if interactive:
        _serve_controls(timeline, nodes, links, events, api, output,
                        pulses=pulses, pulse_interval_ps=pulse_interval_ps,
                        classical_delay_ps=classical_delay_ps)
    return summary



def _receipt_hash(receipt):
    return hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _validate_control_receipt(receipt, nodes, links, used, *, now_ns):
    """Parent is a trusted mTLS harness; a hash alone is not authentication."""
    if not isinstance(receipt, dict):
        raise ValueError('bounded reservation receipt required')
    unsigned = {key: value for key, value in receipt.items() if key != 'receipt_sha256'}
    if receipt.get('receipt_sha256') != _receipt_hash(unsigned):
        raise ValueError('reservation receipt hash does not match')
    request = receipt.get('request')
    route = receipt.get('route')
    identifier = receipt.get('request_id')
    if (receipt.get('status') != 'reserved' or receipt.get('physical_action_performed') is not False
            or receipt.get('quantum_result') is not None or not isinstance(request, dict)
            or request.get('operation') != 'classical_control' or request.get('request_id') != identifier
            or not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', identifier)
            or identifier in used or type(receipt.get('expires_ns')) is not int
            or receipt['expires_ns'] <= now_ns):
        raise ValueError('fresh unique classical-control reservation required')
    if (not isinstance(route, list) or not 2 <= len(route) <= len(nodes)
            or any(not isinstance(node, str) or node not in nodes for node in route)
            or len(route) != len(set(route)) or route[0] != request.get('source')
            or route[-1] != request.get('target') or any(not nodes[node].operational for node in route)):
        raise ValueError('operational native route endpoints required')
    native_links = []
    for source, target in zip(route, route[1:]):
        matching = [link for link in links if {link['a'], link['b']} == {source, target}]
        if len(matching) != 1 or target not in nodes[source].cchannels:
            raise ValueError('route has no native classical channel')
        native_links.append(matching[0]['link_id'])
    if receipt.get('links') != native_links:
        raise ValueError('admitted link sequence does not match native channels')
    return {'request_id': identifier, 'receipt_sha256': receipt['receipt_sha256'],
            'route': list(route), 'links': native_links}



def _unique_object(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('duplicate native command field')
        value[key] = item
    return value

def _serve_controls(timeline, nodes, links, events, api, output, *, pulses, pulse_interval_ps, classical_delay_ps):
    """Bounded trusted stdin commands; the same Timeline/Nodes stay alive."""
    identities = (id(timeline), tuple(id(nodes[node]) for node in sorted(nodes)))
    used = set()
    output_stream = sys.__stdout__
    def publish(value):
        output_stream.write(json.dumps(value, sort_keys=True) + '\n')
        output_stream.flush()
    publish({'stage': 'ready', 'pid': os.getpid(), 'timeline_ps': int(timeline.now()),
             'native_process_retained': True, 'physical_action_performed': False})
    for stage in range(1, 129):
        line = sys.stdin.readline(65537)
        if not line:
            return
        if len(line) > 65536:
            raise ValueError('bounded native command required')
        command = json.loads(line, object_pairs_hook=_unique_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite command')))
        if command == {'type': 'stop'}:
            publish({'stage': 'stopped', 'pid': os.getpid(), 'timeline_ps': int(timeline.now())})
            return
        before, at = len(events), int(timeline.now()) + 1
        try:
            if not isinstance(command, dict):
                raise ValueError('native command object required')
            if set(command) == {'type', 'receipt'} and command['type'] == 'execute_control':
                receipt = _validate_control_receipt(command['receipt'], nodes, links, used, now_ns=time.time_ns())
                used.add(receipt['request_id'])
                timeline.schedule(api['event'].Event(at, api['process'].Process(nodes[receipt['route'][0]],
                    'forward_control', [receipt])))
                timeline.stop_time = at + (len(receipt['route']) + 2) * classical_delay_ps
            elif set(command) == {'type', 'node_id'} and command['type'] == 'fault_source' and command['node_id'] in nodes:
                timeline.schedule(api['event'].Event(at, api['process'].Process(nodes[command['node_id']], 'source_fault', [])))
                timeline.stop_time = at + 1
            elif command == {'type': 'recovery_snapshot'}:
                # Restart the identified simulated source; observe resources and
                # every channel through fresh native callbacks, never write core state.
                for node in nodes.values():
                    node.operational = True
                    timeline.schedule(api['event'].Event(at, api['process'].Process(node, 'report_resources', [])))
                for link in links:
                    for source, target in ((link['a'], link['b']), (link['b'], link['a'])):
                        timeline.schedule(api['event'].Event(at, api['process'].Process(nodes[source], 'emit_control',
                            [target, link['link_id']])))
                    for index in range(pulses):
                        timeline.schedule(api['event'].Event(at + 10**9 + index * pulse_interval_ps,
                            api['process'].Process(nodes[link['a']], 'emit_pulse', [link['b'], link['link_id'], pulses + index])))
                timeline.stop_time = at + 10**9 + pulses * pulse_interval_ps + 10**9 + 3 * classical_delay_ps
            else:
                raise ValueError('unsupported native control command')
            timeline.run()
            if identities != (id(timeline), tuple(id(nodes[node]) for node in sorted(nodes))):
                raise RuntimeError('native timeline/node identity changed')
            emitted = events[before:]
            path = output / f'control-stage-{stage:04d}.jsonl'
            path.write_text(''.join(json.dumps(row, sort_keys=True, allow_nan=False) + '\n' for row in emitted), encoding='utf-8')
            (output / 'all-events-latest.jsonl').write_text(''.join(json.dumps(row, sort_keys=True, allow_nan=False) + '\n' for row in events), encoding='utf-8')
            publish({'stage': 'executed', 'stage_number': stage, 'events_path': str(path),
                     'events_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'event_count': len(emitted),
                     'pid': os.getpid(), 'timeline_ps': int(timeline.now()), 'same_timeline_and_nodes': True,
                     'timeline_run_counter': timeline.run_counter, 'physical_action_performed': False,
                     'quantum_result': None})
        except (ValueError, KeyError, TypeError) as exc:
            publish({'stage': 'rejected', 'reason_type': type(exc).__name__, 'stage_number': stage,
                     'pid': os.getpid(), 'timeline_ps': int(timeline.now())})
    raise ValueError('native command quota exceeded')

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sequence-src', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--seed', type=int, default=2026101101)
    parser.add_argument('--pulses', type=int, default=128)
    parser.add_argument('--length-m', type=float, default=1000.)
    parser.add_argument('--attenuation-db-km', type=float, default=.2)
    parser.add_argument('--detector-efficiency', type=float, default=.75)
    parser.add_argument('--dark-count-hz', type=float, default=100.)
    parser.add_argument('--interactive', action='store_true', help='Keep native timeline/nodes alive for trusted stdin control commands')
    args = parser.parse_args(argv)
    with redirect_stdout(sys.stderr):
        result = run(args.sequence_src, args.output, seed=args.seed, pulses=args.pulses,
                     length_m=args.length_m, attenuation_db_km=args.attenuation_db_km,
                     detector_efficiency=args.detector_efficiency, dark_count_hz=args.dark_count_hz,
                     interactive=args.interactive)
    print(json.dumps({'status': result['status'], 'nodes': len(result['node_ids']),
                      'counters': result['counters'], 'output': str(args.output)}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
