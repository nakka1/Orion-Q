"""Demand scheduling and traceable multi-hop classical control probes.

Quantum deliveries come exclusively from ProtocolRuntime receipts. Separate
BBM92 detections or QKD estimates are never concatenated into an end-to-end pair
or a usable secret key. The classical forwarder transports serialized control
probes through registered channels, with acquired per-hop receipts and latency.
"""
from __future__ import annotations

from collections import Counter, deque
from contextlib import closing
from dataclasses import dataclass
import heapq
import json
import math
from typing import Protocol, runtime_checkable

import networkx as nx

from ..control_plane.sqlite_transaction import connect
from ..quantum_protocols.contracts import ProtocolError, canonical, digest
from .global_operations_topology import institutional_ids, operation_capabilities

SCHEMA = 'orion.global-operations.v1'
CONTROL_SCHEMA = 'orion.classical-control-probe.v1'
STATES = ('not_configured', 'available_no_traffic', 'waiting_resources', 'establishing',
          'transmitting', 'failed_attempt', 'unavailable')
C_KM_S = 299792.458


@dataclass(frozen=True)
class ClassicalHopCommand:
    command_id: str
    demand_id: str
    attempt: int
    hop: int
    link_id: str
    association_id: str
    classical_channel_id: str
    sender: str
    receiver: str
    emitted_at_s: float
    deadline_s: float
    serialized_envelope: str


@dataclass(frozen=True)
class ClassicalHopReceipt:
    command_id: str
    source_id: str
    origin: str
    sequence: int
    observed_at_s: float
    classical_channel_id: str
    receiver: str
    accepted: bool
    envelope_sha256: str
    reason: str = ''


@runtime_checkable
class ClassicalForwardingSource(Protocol):
    source_id: str
    origin: str
    physical_validated: bool

    def acquire(self, command: ClassicalHopCommand, environment: dict) -> ClassicalHopReceipt | None: ...


class SimulatedClassicalForwardingSource:
    """The only artificial input generator; downstream uses the same contract."""
    source_id = 'local_classical_forwarder_v1'
    origin = 'simulated'
    physical_validated = False

    def __init__(self, seed=42):
        self.seed = int(seed)
        self.sequence = 0

    def acquire(self, command, environment):
        self.sequence += 1
        sample = int(digest({'seed': self.seed, 'command': command.command_id})[:16], 16) / 2**64
        loss = float(environment.get('packet_loss_probability', 0.))
        jitter = float(environment.get('jitter_s', 1e-5)) * sample
        propagation = float(environment['propagation_s'])
        serialization = len(command.serialized_envelope.encode('utf-8')) * 8 / float(environment.get('bitrate_bps', 1e6))
        accepted = sample >= loss
        return ClassicalHopReceipt(command.command_id, self.source_id, self.origin, self.sequence,
            command.emitted_at_s + propagation + serialization + jitter,
            command.classical_channel_id, command.receiver, accepted,
            digest(json.loads(command.serialized_envelope)), '' if accepted else 'simulated classical packet loss')


def validate_classical_receipt(receipt, command, *, source, now_s, last_sequence):
    if not isinstance(receipt, ClassicalHopReceipt):
        raise ProtocolError('invalid_control_receipt', 'ClassicalHopReceipt required')
    if receipt.command_id != command.command_id or receipt.classical_channel_id != command.classical_channel_id or receipt.receiver != command.receiver:
        raise ProtocolError('wrong_control_binding', 'control receipt does not match command/channel/receiver')
    if (receipt.source_id, receipt.origin) != (source.source_id, source.origin):
        raise ProtocolError('wrong_control_source', 'receipt origin differs from installed forwarding adapter')
    if type(receipt.sequence) is not int or receipt.sequence <= last_sequence:
        raise ProtocolError('control_replay', 'control receipt sequence stale')
    if not command.emitted_at_s <= receipt.observed_at_s <= now_s <= command.deadline_s:
        raise ProtocolError('control_causality', 'control receipt outside acquisition/deadline window')
    if receipt.envelope_sha256 != digest(json.loads(command.serialized_envelope)):
        raise ProtocolError('control_integrity', 'received serialized envelope differs')
    if type(receipt.accepted) is not bool:
        raise ProtocolError('invalid_control_receipt', 'explicit acceptance boolean required')


def _raw(value):
    return value.to_dict() if hasattr(value, 'to_dict') else value


def _link_control_reason(topology, link, live, runtime, now_s):
    pair = link.channels
    if not link.active:
        return 'registered link disabled'
    if pair is None:
        return 'both channels and their association are not configured'
    if not pair['classical']['enabled']:
        return 'associated classical channel disabled'
    for ident in (link.left_node_id, link.right_node_id):
        node = topology.node(ident)
        record = node.telemetry.get('communicator', {})
        if not record:
            return f'{ident}: communicator not configured'
        if node.telemetry.get('console_control', {}).get('isolated'):
            return f'{ident}: node isolated'
        if record.get('operational_state') != 'enabled':
            return f'{ident}: communicator unavailable'
        if not operation_capabilities(node)['classical_forwarding']:
            return f'{ident}: no configured classical forwarding capability'
        if record.get('integration', {}).get('mode') != 'simulated':
            return f'{ident}: external classical forwarding adapter not validated; no simulated receipt substituted'
    if live is None:
        return 'awaiting paired acquisition evidence'
    provenance = live.provenance
    receipt = provenance.get('channels', {})
    if receipt.get('status') != 'received' or receipt.get('source') not in ('simulated',):
        return 'awaiting fresh received simulated acquisition; physical/absent data cannot be fabricated'
    technology = link.technology or {}
    parent_id = technology.get('source_span_id')
    if parent_id:
        parent = next((candidate for candidate in topology.links if candidate.link_id == parent_id), None)
        if parent is None or not parent.active:
            return f'underlying physical span {parent_id} removed or disabled'
        if parent.channels is None or not parent.channels['classical']['enabled']:
            return f'underlying physical span {parent_id} classical channel unavailable'
    if set(technology.get('technologies', ())) & {'satellite_doppler', 'satellite_isl'}:
        session = runtime.sessions.get(technology.get('session_id'))
        if session is None:
            return 'satellite session absent'
        metrics = provenance.get('protocol_metrics', session.metrics)
        hk = metrics.get('housekeeping', {})
        if 'satellite_isl' in session.config.technologies:
            # ISL terminal LOS, Earth obstruction, APT and clock conditions are
            # supplied by its received geometry; a drawing is not connectivity.
            engineering=metrics.get('satellite_housekeeping',{})
            if set(engineering)!=set(session.config.endpoints) or any(now_s>value.get('valid_until_s',-1) for value in engineering.values()):
                return 'engineering observations from both satellites expired or absent'
            geometry = metrics.get('isl_geometry', metrics.get('geometry', {}))
            if isinstance(geometry, (tuple, list)):
                geometry = geometry[0] if geometry else {}
            if metrics.get('available') is False or geometry.get('earth_obstructed') or geometry.get('visible') is False:
                return metrics.get('reason', 'satellite mutual visibility/pointing unavailable')
        else:
            if not hk or now_s > hk.get('valid_until_s', -1):
                return 'satellite housekeeping expired or absent'
            ends = session.config.endpoints
            ground = next((ident for ident in ends if ident in (link.left_node_id, link.right_node_id)), None)
            geometry = metrics.get('geometry', ())
            station = geometry[ends.index(ground)] if ground is not None and len(geometry) > ends.index(ground) else {}
            if not station.get('visible'):
                return 'station outside satellite visibility/elevation window'
        last = metrics.get('last_observed_at_s')
        max_age = session.config.parameters.get('block_s', 2.) + session.config.parameters.get('timeout_s', 4.)
        if last is None or not 0 <= now_s - last <= max_age:
            return 'satellite acquisition expired or absent'
    elif link.technology is None and link.distance_km > 150.:
        return 'long direct terrestrial span has no declared classical regeneration; use configured segmented path'
    elif link.technology is None:
        received_tick = receipt.get('tick')
        acquired_clock = None if received_tick is None else received_tick * runtime.clock_rate
        if acquired_clock is None or not 0 <= now_s - acquired_clock <= pair['classical']['max_age_s']:
            return 'classical acquisition expired; fresh paired measurement needed'
    return None


def classical_graph(topology, live_links, runtime, now_s, *, configured=False):
    graph = nx.Graph()
    graph.add_nodes_from(node.node_id for node in topology.nodes)
    blockers = {}
    for link in topology.links:
        live = live_links.get(link.link_id)
        reason = _link_control_reason(topology, link, live, runtime, now_s)
        if reason:
            blockers[link.link_id] = reason
        if reason and not configured:
            continue
        distance = getattr(live, 'distance_km', link.distance_km) if live else link.distance_km
        length = max(.001, float(distance))
        if graph.has_edge(link.left_node_id, link.right_node_id) and graph[link.left_node_id][link.right_node_id]['length'] <= length:
            continue
        graph.add_edge(link.left_node_id, link.right_node_id, length=length, link_id=link.link_id)
    graph.graph['blockers'] = blockers
    return graph


class GlobalOperationManager:
    """Network demand journal; prepare before acquisitions, observe after them."""
    def __init__(self, journal_path, topology_id, *, source=None, seed=42):
        self.journal_path, self.topology_id = journal_path, str(topology_id)
        self.source = source or SimulatedClassicalForwardingSource(seed)
        if not isinstance(self.source, ClassicalForwardingSource):
            raise ProtocolError('invalid_forwarding_source', 'ClassicalForwardingSource required')
        self.clock_s = 0.
        self.demands = {}
        self.events = deque(maxlen=128)
        self._outbox = []
        self._previous_hash = ''
        self._queue = []
        self._queue_sequence = 0
        self._seen_receipt_sequences = set()
        self._claimed_quantum_receipts = set()
        self._last_source_sequence = 0
        self._topology = None
        self._live_links = {}
        self._admitted_links = {}
        self._runtime = None
        self._last_tick = 0
        self._components = []
        with closing(connect(journal_path)) as conn, conn:
            conn.executescript('''CREATE TABLE IF NOT EXISTS global_operation_demands(
                topology_id TEXT NOT NULL,demand_id TEXT NOT NULL,state_json TEXT NOT NULL,
                PRIMARY KEY(topology_id,demand_id));
                CREATE TABLE IF NOT EXISTS global_operation_events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,topology_id TEXT NOT NULL,
                demand_id TEXT NOT NULL,network_s REAL NOT NULL,event_kind TEXT NOT NULL,
                record_json TEXT NOT NULL,receipt_sha256 TEXT NOT NULL,previous_sha256 TEXT NOT NULL);''')
            row = conn.execute('SELECT receipt_sha256 FROM global_operation_events WHERE topology_id=? ORDER BY sequence DESC LIMIT 1', (self.topology_id,)).fetchone()
            if row:
                self._previous_hash = row[0]
            row = conn.execute('SELECT COALESCE(MAX(sequence),0) FROM technology_events WHERE topology_id=?', (self.topology_id,)).fetchone() if conn.execute("SELECT 1 FROM sqlite_master WHERE name='technology_events'").fetchone() else (0,)
            self._technology_sequence = row[0]
            for raw, in conn.execute('SELECT state_json FROM global_operation_demands WHERE topology_id=?', (self.topology_id,)):
                state = json.loads(raw)
                state.update(state='waiting_resources', reason='engine restarted; fresh acquisitions and receipts required',
                             active=False, next_attempt_s=0., last_result=None, attempt_started_s=None)
                self.demands[state['demand_id']] = state

    def _event(self, demand, kind, detail=None, at_s=None):
        body = {'schema': SCHEMA, 'topology_id': self.topology_id,
                'demand_id': demand['demand_id'], 'at_s': self.clock_s if at_s is None else float(at_s),
                'event': kind, 'detail': detail or {}, 'origin': 'simulated' if demand['operation'] == 'classical_control_probe' else 'backend_protocol_receipt',
                'physical_actuation': False, 'certified_secret_bits': 0, 'previous_sha256': self._previous_hash}
        record = {**body, 'receipt_sha256': digest(body)}
        self._previous_hash = record['receipt_sha256']
        self.events.append(record)
        self._outbox.append(record)
        return record

    def _flush(self):
        rows, self._outbox = self._outbox, []
        with closing(connect(self.journal_path)) as conn, conn:
            conn.executemany('INSERT INTO global_operation_events(topology_id,demand_id,network_s,event_kind,record_json,receipt_sha256,previous_sha256) VALUES(?,?,?,?,?,?,?)',
                [(self.topology_id, row['demand_id'], row['at_s'], row['event'], canonical(row), row['receipt_sha256'], row['previous_sha256']) for row in rows])
            conn.executemany('INSERT INTO global_operation_demands VALUES(?,?,?) ON CONFLICT(topology_id,demand_id) DO UPDATE SET state_json=excluded.state_json',
                [(self.topology_id, key, canonical(value)) for key, value in self.demands.items()])

    def _templates(self, topology, runtime):
        ids = institutional_ids(topology)
        templates = []
        if not ids:
            return templates
        for session in runtime.sessions.values():
            config = session.config
            technologies = set(config.technologies)
            operation = 'entanglement_pair' if 'entanglement_swapping' in technologies else 'qkd_estimate' if 'tf_qkd' in technologies else 'optical_pair_measurement'
            templates.append({'demand_id': f'SERVICE-{config.session_id}', 'a': config.endpoints[0],
                'b': config.endpoints[1], 'operation': operation, 'session_id': config.session_id,
                'declared_path': list(config.path), 'declared_links': list(config.link_ids),
                'timeout_s': max(8., 2 * config.parameters.get('timeout_s', 3.)), 'repeat_s': 5., 'max_attempts': 3,
                'scope': 'measured protocol service; no application/key forwarding'})
        # Every institution originates and receives a serialized probe. Ring
        # demands include national and intercontinental cases; missing routes
        # remain explicit failed/unavailable demands rather than invented edges.
        if len(ids) > 1:
            for a, b in zip(ids, (*ids[1:], ids[0])):
                templates.append({'demand_id': f'CONTROL-{a}-{b}', 'a': a, 'b': b,
                    'operation': 'classical_control_probe', 'session_id': None, 'timeout_s': 10.,
                    'repeat_s': 5., 'max_attempts': 3, 'scope': 'classical diagnostic payload only; no quantum state or key'})
                templates.append({'demand_id': f'GLOBAL-ENT-{a}-{b}', 'a': a, 'b': b,
                    'operation': 'global_entanglement', 'session_id': None, 'timeout_s': 10.,
                    'repeat_s': 10., 'max_attempts': 3,
                    'scope': 'route feasibility; separately detected pairs are not a coherent relay'})
        return templates

    def _sync(self, topology, runtime):
        self._topology, self._runtime = topology, runtime
        active = set()
        for template in self._templates(topology, runtime):
            key = template['demand_id']
            active.add(key)
            if key not in self.demands:
                demand = {**template, 'state': 'available_no_traffic', 'reason': 'configured; first demand not started',
                          'attempts': 0, 'successful_attempts': 0, 'failed_attempts': 0, 'cycle_attempts': 0,
                          'active': False, 'next_attempt_s': 0., 'attempt_started_s': None,
                          'route': [], 'route_links': [], 'receipt_ids': [], 'last_result': None,
                          'last_activity_s': None, 'requires_infrastructure': []}
                self.demands[key] = demand
                self._event(demand, 'demand_registered', template)
            else:
                for name, value in template.items():
                    if name == 'session_id' and value is None and self.demands[key]['active']:
                        continue
                    self.demands[key][name] = value
        for key, demand in self.demands.items():
            if key not in active:
                if demand['state'] != 'not_configured':
                    self._event(demand, 'demand_configuration_removed', {'reason': 'endpoint/session removed; not restored from journal'})
                demand.update(state='not_configured', active=False, reason='endpoint/session removed; not automatically restored')
        return active

    def _set(self, demand, state, reason, *, at_s=None, detail=None):
        if state not in STATES:
            raise ValueError(state)
        if (demand['state'], demand['reason']) != (state, reason):
            demand['state'], demand['reason'] = state, reason
            self._event(demand, 'demand_state_changed', {'state': state, 'reason': reason, **(detail or {})}, at_s)

    def _fail(self, demand, reason, *, at_s=None, unavailable=False):
        demand['active'] = False
        demand['failed_attempts'] += 1
        demand['next_attempt_s'] = (self.clock_s if at_s is None else at_s) + min(10., 2 ** max(0, demand['cycle_attempts'] - 1))
        self._set(demand, 'unavailable' if unavailable else 'failed_attempt', reason, at_s=at_s)
        self._event(demand, 'attempt_failed', {'attempt': demand['attempts'], 'reason': reason,
            'next_retry_at_s': demand['next_attempt_s'], 'resource_copies_retained': 0}, at_s)

    def _start(self, demand, route, links):
        demand['attempts'] += 1
        demand['cycle_attempts'] = demand['cycle_attempts'] + 1 if demand['cycle_attempts'] < demand['max_attempts'] else 1
        demand.update(active=True, attempt_started_s=self.clock_s, last_activity_s=self.clock_s,
                      deadline_s=self.clock_s + demand['timeout_s'], route=list(route), route_links=list(links),
                      receipt_ids=[], classical_command_receipts={}, last_result=None)
        self._admitted_links[demand['demand_id']] = {ident: self._live_links[ident] for ident in links if ident in self._live_links}
        self._set(demand, 'establishing', 'validated route; waiting for acquired operations and classical receipts')
        self._event(demand, 'attempt_started', {'attempt': demand['attempts'], 'route': list(route),
            'links': list(links), 'deadline_s': demand['deadline_s'], 'operation': demand['operation']})

    def _session_reason(self, session):
        if not session.config.enabled:
            return 'protocol session disabled'
        if not self._runtime._available(session):
            return session.reason or 'node/link/associated channel unavailable'
        if session.status in ('incompatible', 'error'):
            return f'{session.status}: {session.reason}'
        if session.status == 'unavailable':
            return session.reason
        return None

    def prepare(self, topology, runtime, link_live, tick):
        self.clock_s = float(runtime.clock_s)
        self._last_tick = int(tick)
        self._live_links = dict(link_live)
        active = self._sync(topology, runtime)
        session_owners = {demand['session_id']: key for key, demand in self.demands.items()
                          if demand['active'] and demand.get('session_id')}
        graph = classical_graph(topology, self._live_links, runtime, self.clock_s)
        configured = classical_graph(topology, self._live_links, runtime, self.clock_s, configured=True)
        for key in sorted(active):
            demand = self.demands[key]
            if demand['active'] or self.clock_s < demand['next_attempt_s']:
                continue
            for endpoint in (demand['a'], demand['b']):
                if endpoint not in graph:
                    self._set(demand, 'not_configured', f'endpoint {endpoint} missing')
                    break
            else:
                if demand['operation'] in ('entanglement_pair', 'qkd_estimate', 'optical_pair_measurement'):
                    session = runtime.sessions.get(demand['session_id'])
                    if session is None:
                        self._set(demand, 'not_configured', 'protocol session absent')
                        continue
                    owner = session_owners.get(session.config.session_id)
                    if owner is not None and owner != key:
                        self._set(demand, 'waiting_resources', f'protocol session resources reserved by demand {owner}')
                        continue
                    reason = self._session_reason(session)
                    if reason:
                        self._start(demand, session.config.path, session.config.link_ids)
                        self._fail(demand, reason, unavailable=True)
                    else:
                        self._start(demand, session.config.path, session.config.link_ids)
                        session_owners[session.config.session_id] = key
                        demand['baseline_counters'] = dict(session.counts)
                        demand['last_source_sequence'] = session.last_sequence
                    continue
                a, b = demand['a'], demand['b']
                if demand['operation'] == 'global_entanglement':
                    direct = next((session for session in runtime.sessions.values() if
                                   set(session.config.endpoints) == {a, b} and 'entanglement_swapping' in session.config.technologies), None)
                    if direct:
                        owner = session_owners.get(direct.config.session_id)
                        if owner is not None and owner != key:
                            self._set(demand, 'waiting_resources', f'protocol session resources reserved by demand {owner}')
                            continue
                        demand['session_id'] = direct.config.session_id
                        reason = self._session_reason(direct)
                        self._start(demand, direct.config.path, direct.config.link_ids)
                        if reason:
                            self._fail(demand, reason, unavailable=True)
                        else:
                            session_owners[direct.config.session_id] = key
                        continue
                    path = nx.shortest_path(configured, a, b, weight='length') if nx.has_path(configured, a, b) else []
                    links = [configured[u][v]['link_id'] for u, v in zip(path, path[1:])]
                    self._start(demand, path, links)
                    resources = ['shared end-to-end quantum resource driver', 'compatible optical memories and Bell operations at every quantum relay']
                    if any(set((next(link for link in topology.links if link.link_id == ident).technology or {}).get('technologies', ())) & {'satellite_doppler', 'satellite_isl'} for ident in links):
                        resources.append('orbital coherent storage/swapping interface; present TDC/BBM92 detections are destructive and cannot relay quantum states')
                    if not path:
                        resources.insert(0, 'registered physically admissible inter-region links')
                    demand['requires_infrastructure'] = resources
                    self._fail(demand, 'no compatible end-to-end quantum driver; separate local BBM92/QKD services cannot be concatenated', unavailable=True)
                    continue
                if not nx.has_path(graph, a, b):
                    path = nx.shortest_path(configured, a, b, weight='length') if nx.has_path(configured, a, b) else []
                    links = [configured[u][v]['link_id'] for u, v in zip(path, path[1:])]
                    blockers = [{'link_id': ident, 'reason': graph.graph['blockers'][ident]} for ident in links if ident in graph.graph['blockers']]
                    reason = '; '.join(item['reason'] for item in blockers[:4]) or 'no registered classical route between regions'
                    demand['requires_infrastructure'] = ['restore registered channels and received LOS/acquisition evidence'] if path else ['register compatible terrestrial/OGS/ISL connections']
                    self._start(demand, path, links)
                    self._fail(demand, reason, unavailable=True)
                    self._event(demand, 'route_admission_rejected', {'blockers': blockers, 'configured_path': path})
                    continue
                route = nx.shortest_path(graph, a, b, weight='length')
                links = [graph[u][v]['link_id'] for u, v in zip(route, route[1:])]
                self._start(demand, route, links)
                demand['requires_infrastructure'] = []
                self._send_hop(demand, 0, self.clock_s)
        self._flush()
        return self.snapshot()

    def _send_hop(self, demand, hop, at_s):
        ident = demand['route_links'][hop]
        link = next(link for link in self._topology.links if link.link_id == ident)
        admitted_live = self._admitted_links.get(demand['demand_id'], {}).get(ident, self._live_links.get(ident))
        reason = _link_control_reason(self._topology, link, admitted_live, self._runtime, at_s)
        if reason:
            self._fail(demand, reason, at_s=at_s, unavailable=True)
            return
        pair = link.channels
        envelope = {'schema': CONTROL_SCHEMA, 'demand_id': demand['demand_id'], 'attempt': demand['attempts'],
                    'origin': demand['a'], 'destination': demand['b'], 'hop': hop,
                    'sender': demand['route'][hop], 'receiver': demand['route'][hop + 1],
                    'path': demand['route'], 'visited_receipts': demand['receipt_ids'],
                    'payload': {'kind': 'diagnostic_probe', 'network_tick': self._last_tick,
                                'scope': 'classical control only; no key material or quantum state'}}
        serialized = canonical(envelope)
        if len(serialized.encode('utf-8')) > pair['classical']['max_message_bytes']:
            self._fail(demand, 'serialized control probe exceeds classical channel message limit', at_s=at_s)
            return
        command = ClassicalHopCommand(f'{demand["demand_id"]}:{demand["attempts"]}:{hop}',
            demand['demand_id'], demand['attempts'], hop, ident, pair['association_id'], pair['classical']['channel_id'],
            demand['route'][hop], demand['route'][hop + 1], at_s,
            min(demand['deadline_s'], at_s + pair['classical']['max_age_s']), serialized)
        live = admitted_live
        distance = max(.001, float(live.distance_km if live else link.distance_km))
        orbital = bool(set((link.technology or {}).get('technologies', ())) & {'satellite_doppler', 'satellite_isl'})
        params = self._topology.node(command.sender).telemetry.get('classical_forwarding_parameters', {})
        environment = {'propagation_s': distance / (C_KM_S if orbital else 200000.) * (1. if orbital else 1.2),
                       'bitrate_bps': params.get('bitrate_bps', 1e6), 'jitter_s': params.get('jitter_s', 1e-5),
                       'packet_loss_probability': params.get('packet_loss_probability', 0.)}
        if not all(math.isfinite(float(value)) for value in environment.values()) or not 0 <= environment['packet_loss_probability'] <= 1 or environment['bitrate_bps'] <= 0 or environment['jitter_s'] < 0:
            self._fail(demand, 'invalid classical forwarding operational parameters', at_s=at_s)
            return
        self._set(demand, 'transmitting', f'classical hop {hop + 1}/{len(demand["route_links"])} through {command.classical_channel_id}', at_s=at_s)
        self._event(demand, 'classical_probe_sent', {'command_id': command.command_id, 'sender': command.sender,
            'receiver': command.receiver, 'link_id': ident, 'classical_channel_id': command.classical_channel_id,
            'association_id': command.association_id, 'envelope_sha256': digest(envelope),
            'serialized_envelope': serialized, 'source_id': self.source.source_id,
            'environment': environment}, at_s)
        try:
            receipt = self.source.acquire(command, environment)
        except (ProtocolError, ValueError, TypeError) as exc:
            self._fail(demand, str(exc), at_s=at_s)
            return
        if receipt is None:
            self._queue_sequence += 1
            heapq.heappush(self._queue, (command.deadline_s, self._queue_sequence, command, None))
            return
        if not isinstance(receipt, ClassicalHopReceipt) or not isinstance(receipt.observed_at_s, (int, float)) or not math.isfinite(receipt.observed_at_s):
            self._fail(demand, 'forwarding adapter returned invalid non-finite receipt time', at_s=at_s)
            return
        if receipt.observed_at_s > command.deadline_s:
            self._queue_sequence += 1
            heapq.heappush(self._queue, (command.deadline_s, self._queue_sequence, command, None))
            return
        self._queue_sequence += 1
        heapq.heappush(self._queue, (max(at_s, float(receipt.observed_at_s)), self._queue_sequence, command, receipt))

    def _receive_hops(self, target):
        while self._queue and self._queue[0][0] <= target:
            at_s, _, command, receipt = heapq.heappop(self._queue)
            demand = self.demands.get(command.demand_id)
            if demand is None or not demand['active'] or command.attempt != demand['attempts']:
                continue
            if receipt is None:
                self._fail(demand, 'classical forwarding adapter produced no receipt before timeout', at_s=at_s)
                continue
            ident = command.link_id
            link = next((link for link in self._topology.links if link.link_id == ident), None)
            reason = 'link removed while control packet was in flight' if link is None else _link_control_reason(self._topology, link, self._live_links.get(ident), self._runtime, target)
            if reason:
                self._fail(demand, reason, at_s=at_s, unavailable=True)
                continue
            try:
                # Independent in-flight hops can arrive out of sequence. Replay
                # protection is an acquired-sequence set, not receive ordering.
                validate_classical_receipt(receipt, command, source=self.source, now_s=at_s,
                    last_sequence=receipt.sequence if receipt.sequence in self._seen_receipt_sequences else 0)
            except (ProtocolError, ValueError, TypeError) as exc:
                self._fail(demand, str(exc), at_s=at_s)
                continue
            self._seen_receipt_sequences.add(receipt.sequence)
            if not receipt.accepted:
                self._fail(demand, receipt.reason or 'classical packet rejected', at_s=at_s)
                continue
            record = self._event(demand, 'classical_probe_received', {
                'command_id': command.command_id, 'hop': command.hop, 'link_id': ident,
                'receiver': command.receiver, 'classical_channel_id': command.classical_channel_id,
                'association_id': command.association_id, 'source_id': receipt.source_id,
                'source_origin': receipt.origin, 'acquisition_sequence': receipt.sequence,
                'envelope_sha256': receipt.envelope_sha256, 'latency_s': receipt.observed_at_s - command.emitted_at_s}, at_s)
            demand['receipt_ids'].append(record['receipt_sha256'])
            demand['last_activity_s'] = at_s
            if command.hop + 1 < len(demand['route_links']):
                self._send_hop(demand, command.hop + 1, at_s)
            else:
                demand.update(active=False, successful_attempts=demand['successful_attempts'] + 1,
                    next_attempt_s=at_s + demand['repeat_s'],
                    last_result={'kind': 'classical_probe_delivered', 'at_s': at_s, 'origin': receipt.origin,
                        'hops': len(demand['route_links']), 'latency_s': at_s - demand['attempt_started_s'],
                        'receipt_sha256': record['receipt_sha256'], 'certified_secret_bits': 0})
                self._set(demand, 'available_no_traffic', 'serialized classical probe received at destination; waiting for next demand', at_s=at_s)
                self._event(demand, 'classical_probe_delivered', demand['last_result'], at_s)

    def _technology_events(self):
        with closing(connect(self.journal_path)) as conn:
            rows = conn.execute('SELECT sequence,payload_json FROM technology_events WHERE topology_id=? AND sequence>? ORDER BY sequence',
                (self.topology_id, self._technology_sequence)).fetchall()
        for sequence, raw in rows:
            self._technology_sequence = sequence
            yield json.loads(raw)

    def _observe_quantum(self):
        for record in self._technology_events():
            session_id = record['session_id']
            for demand in self.demands.values():
                if not demand['active'] or demand.get('session_id') != session_id or record['at_s'] < demand['attempt_started_s']:
                    continue
                kind = record['event']
                if kind in ('operation_error', 'processing_error', 'acquisition_rejected'):
                    self._event(demand, 'protocol_attempt_observation_error', {'technology_receipt': record['receipt_sha256'], 'detail': record['detail']}, record['at_s'])
                if kind == 'classical_received':
                    demand['receipt_ids'].append(record['receipt_sha256'])
                    demand.setdefault('classical_command_receipts', {})[record['command_id']] = record['receipt_sha256']
                    demand['last_activity_s'] = record['at_s']
                    self._set(demand, 'transmitting', 'protocol operations received through associated classical channel; quantum result still pending', at_s=record['at_s'])
                    self._event(demand, 'protocol_classical_receipt_linked', {'technology_receipt': record['receipt_sha256'], 'command_id': record['command_id']}, record['at_s'])
                success = kind == 'end_to_end_delivered'
                session = self._runtime.sessions.get(session_id)
                if kind == 'metrics_computed' and session and demand['operation'] in ('qkd_estimate', 'optical_pair_measurement') and record['command_id'] in demand.get('classical_command_receipts', {}):
                    metrics = session.metrics
                    # A tick can contain several processed blocks. Only bind the
                    # snapshot result to its own latest metric receipt, never
                    # reuse a later block's success at an earlier event time.
                    same_block = math.isclose(metrics.get('last_observed_at_s', -1.), record['at_s'], rel_tol=0., abs_tol=1e-9)
                    success = same_block and metrics.get('available', True) and (metrics.get('coincidences', 0) > 0 if demand['operation'] == 'optical_pair_measurement' else metrics.get('sifted_bits', 0) > 0)
                if not success:
                    continue
                if record['receipt_sha256'] in self._claimed_quantum_receipts:
                    continue
                self._claimed_quantum_receipts.add(record['receipt_sha256'])
                metrics = dict(session.metrics) if session else {}
                demand.update(active=False, successful_attempts=demand['successful_attempts'] + 1,
                    last_activity_s=record['at_s'], next_attempt_s=record['at_s'] + demand['repeat_s'],
                    last_result={'kind': 'entanglement_pair_delivered' if kind == 'end_to_end_delivered' else 'observed_protocol_block',
                        'at_s': record['at_s'], 'origin': record['origin'], 'technology_receipt': record['receipt_sha256'],
                        'pair_id': record['detail'].get('pair_id'), 'fidelity_estimate': record['detail'].get('fidelity_estimate', metrics.get('fidelity')),
                        'coincidences': metrics.get('coincidences'), 'sifted_bits': metrics.get('sifted_bits'),
                        'qber': metrics.get('qber'), 'conditional_key_estimate_bits': metrics.get('conditional_key_estimate_bits', 0),
                        'certified_secret_bits': 0, 'application_payload_forwarded': False})
                self._set(demand, 'available_no_traffic', 'fresh protocol receipt satisfies this local service; no application payload or certified key', at_s=record['at_s'])
                self._event(demand, 'protocol_demand_result', demand['last_result'], record['at_s'])

    def observe(self, topology, runtime, link_live, tick, legacy_demands=()):
        self._topology, self._runtime, self._live_links = topology, runtime, dict(link_live)
        self._last_tick = int(tick)
        self.clock_s = float(runtime.clock_s)
        self._sync(topology, runtime)
        self._receive_hops(self.clock_s)
        self._observe_quantum()
        for demand in self.demands.values():
            if not demand['active']:
                continue
            if demand.get('session_id'):
                session = runtime.sessions.get(demand['session_id'])
                reason = 'session removed' if session is None else self._session_reason(session)
                if reason:
                    self._fail(demand, reason, unavailable=True)
                    continue
            if self.clock_s >= demand.get('deadline_s', math.inf):
                self._fail(demand, 'demand timeout: no fresh accepted end-to-end resource/protocol block before deadline')
            elif demand['state'] == 'establishing':
                self._set(demand, 'waiting_resources', 'protocol acquiring observations, synchronizing clocks or waiting for stored pairs/heralding')
        self._components = self._matrix(topology, runtime, legacy_demands)
        self._flush()
        return self.snapshot()

    def step(self, topology, protocol_runtime, link_live, tick, legacy_demands=()):
        """Convenience audit; engines should prepare before advance then observe."""
        self.prepare(topology, protocol_runtime, link_live, tick)
        return self.observe(topology, protocol_runtime, link_live, tick, legacy_demands)

    def _matrix(self, topology, runtime, legacy_demands):
        rows = []
        ids = set(institutional_ids(topology))
        for node in topology.nodes:
            record = node.telemetry.get('communicator', {})
            links = [link for link in topology.links if node.node_id in (link.left_node_id, link.right_node_id)]
            sessions = [session for session in runtime.sessions.values() if node.node_id in session.config.path]
            demands = [demand for demand in self.demands.values() if node.node_id in demand.get('route', ()) or node.node_id in (demand['a'], demand['b'])]
            traffic = [demand for demand in demands if demand.get('last_result') or demand['active'] or demand.get('receipt_ids')]
            impediments = []
            if not record:
                impediments.append('communicator not configured')
            if record.get('physical_type') != 'satellite' and not node.telemetry.get('location'):
                impediments.append('terrestrial location missing')
            for link in links:
                if link.channels is None:
                    impediments.append(f'{link.link_id}: paired channels missing')
            if not links:
                impediments.append('no registered links')
            if node.telemetry.get('console_control', {}).get('isolated'):
                impediments.append('node isolated')
            if record.get('operational_state') not in (None, 'enabled'):
                impediments.append(f'communicator {record["operational_state"]}')
            for session in sessions:
                if session.status in ('incompatible', 'error', 'unavailable', 'disabled'):
                    impediments.append(f'{session.config.session_id}: {session.reason}')
            successes = sum(demand['successful_attempts'] for demand in demands)
            def fresh_session(session):
                last = session.metrics.get('last_observed_at_s')
                max_age = session.config.parameters.get('block_s', .5) + session.config.parameters.get('timeout_s', 3.)
                return (session.status == 'operational' and runtime._available(session) and last is not None
                    and 0 <= self.clock_s - last <= max_age)
            measured = any(fresh_session(session) for session in sessions)
            for link in links:
                live = self._live_links.get(link.link_id)
                if not live or link.technology is not None:
                    continue
                channels = live.provenance.get('channels', {})
                tick = channels.get('tick')
                max_age = (link.channels or {}).get('classical', {}).get('max_age_s', 2.)
                measured = measured or (channels.get('status') == 'received' and tick is not None
                    and 0 <= (self._last_tick - tick) * runtime.clock_rate <= max_age and link.active
                    and live.status in ('up','degraded') and link.channels is not None
                    and all(link.channels[kind]['enabled'] for kind in ('classical','quantum'))
                    and not node.telemetry.get('console_control',{}).get('isolated')
                    and record.get('operational_state')=='enabled')
            status = 'not_configured' if not record or not links else 'transmitting' if any(demand['active'] for demand in traffic) else 'available_no_traffic' if measured else 'waiting_resources'
            if impediments and not measured:
                status = 'unavailable' if record and links else 'not_configured'
            rows.append({'component': node.node_id, 'public_identifier': record.get('device_id', node.node_id),
                'name': record.get('name', node.telemetry.get('location', {}).get('place', node.node_id)),
                'physical_type': record.get('physical_type', 'unconfigured'), 'role': record.get('network_role', node.role.value),
                'institutional_endpoint': node.node_id in ids, 'capabilities': operation_capabilities(node),
                'declared_capabilities': record.get('capabilities', ()), 'technologies': record.get('technologies', ()),
                'configuration': 'complete_declared_simulator' if record and links and all(link.channels for link in links) else 'incomplete',
                'simulation_resource_allocation': {
                    'banks': node.telemetry.get('simulation_resource_banks', []),
                    'totals': node.telemetry.get('simulation_resource_totals', {}),
                    'physical_capacity_validated': False, 'shared_hardware_arbitration_implemented': False},
                'source': record.get('integration', {}).get('data_origin', 'unconfigured'),
                'links': [link.link_id for link in links], 'sessions': [session.config.session_id for session in sessions],
                'routes': [demand['demand_id'] for demand in demands], 'legacy_routes': [getattr(demand, 'demand_id', '') for demand in legacy_demands if node.node_id in getattr(demand, 'route', ())],
                'operation_state': status, 'active_demands': sum(demand['active'] for demand in demands),
                'successful_local_or_classical_operations': successes,
                'measured_acquisition_present': measured, 'impediments': list(dict.fromkeys(impediments)),
                'evidence_receipts': list(dict.fromkeys(receipt for demand in demands for receipt in demand.get('receipt_ids', ())))[-20:],
                'certified_secret_bits': 0, 'physical_actuation': False})
        return rows

    def snapshot(self):
        demands = list(self.demands.values())
        return {'schema': SCHEMA, 'clock_s': self.clock_s, 'network_tick': self._last_tick,
            'source': {'source_id': self.source.source_id, 'origin': self.source.origin,
                       'physical_validated': self.source.physical_validated},
            'states': list(STATES), 'demands': json.loads(canonical(demands)), 'components': self._components,
            'state_counts': dict(Counter(demand['state'] for demand in demands)),
            'pending_classical_hops': len(self._queue), 'events': list(self.events),
            'successful_classical_probes': sum(demand['successful_attempts'] for demand in demands if demand['operation'] == 'classical_control_probe'),
            'successful_protocol_services': sum(demand['successful_attempts'] for demand in demands if demand['operation'] in ('entanglement_pair', 'qkd_estimate', 'optical_pair_measurement')),
            'certified_secret_bits': 0, 'application_quantum_forwarding_implemented': False,
            'limitations': ['Independent satellite/ISL TDC measurements are destructive; no orbital memory/BSM relay driver.',
                'Classical probes are diagnostic control payloads, not application data or delivered secret keys.',
                'Physical classical forwarding adapter requires integration and validation; no synthetic physical receipts.'],
            'physical_actuation': False}

    def export(self, directory):
        from pathlib import Path
        import csv
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        snapshot = self.snapshot()
        (target / 'global-operations.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
        with (target / 'component-matrix.csv').open('w', newline='', encoding='utf-8-sig') as stream:
            fields = ('component', 'name', 'physical_type', 'role', 'capabilities', 'configuration', 'links', 'routes', 'operation_state', 'impediments', 'evidence_receipts', 'simulation_resource_allocation')
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for row in self._components:
                writer.writerow({key: json.dumps(row[key], ensure_ascii=False) if isinstance(row[key], (list, dict, tuple)) else row[key] for key in fields})
        with closing(connect(self.journal_path)) as conn:
            rows = conn.execute('SELECT record_json FROM global_operation_events WHERE topology_id=? ORDER BY sequence', (self.topology_id,)).fetchall()
        (target / 'global-operation-events.jsonl').write_text('\n'.join(row[0] for row in rows) + '\n', encoding='utf-8')
        return {'directory': str(target), 'events': len(rows), 'components': len(self._components), 'demands': len(self.demands),
                'certified_secret_bits': 0, 'physical_actuation': False}
