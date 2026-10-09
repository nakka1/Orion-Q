"""Bounded local inventory command lifecycle; not device authority or retries."""
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from .campaign_read_store import ReadOnlyCampaignStore
from .inventory import (AddNetworkNode, AddSimulatedNode, ConfigureSimulatedLink, RecalibrateNode,
                        SetLinkActive, SetNodeIsolation, SetNodeLocation, SimulatedNodeAction)
from .topology import _sha256

# Every tracked verb is exactly one topology version increment, so the CONFIRMED
# detail (topology_version == expected_version + 1) has the same shape for all.
CONTRACTS = {'add_simulated_node': AddSimulatedNode, 'configure_simulated_link': ConfigureSimulatedLink,
             'apply_simulated_node_action': SimulatedNodeAction,
             'add_network_node': AddNetworkNode, 'set_node_location': SetNodeLocation,
             'set_node_isolation': SetNodeIsolation, 'set_link_active': SetLinkActive,
             'recalibrate_node': RecalibrateNode}
NEXT = {'REQUESTED': {'RUNNING', 'REJECTED'}, 'RUNNING': {'CONFIRMED', 'UNKNOWN'}}


def validated_request(verb, payload):
    if verb not in CONTRACTS:
        raise ValueError('command tracking supports local inventory only')
    return asdict(CONTRACTS[verb].from_dict(payload))


def _verify(record, digest):
    if (not isinstance(record, dict) or set(record) != {'schema', 'command_id', 'verb', 'request', 'events', 'physical_action_performed'}
            or record['schema'] != 'orion.console_command.v1' or record['physical_action_performed'] is not False
            or _sha256(record) != digest):
        raise ValueError('command schema/hash mismatch')
    if str(UUID(record['command_id'])) != record['command_id']:
        raise ValueError('invalid command ID')
    validated_request(record['verb'], record['request'])
    events = record['events']
    if not isinstance(events, list) or not 1 <= len(events) <= 3:
        raise ValueError('invalid command lifecycle')
    previous = None
    for event in events:
        if not isinstance(event, dict) or set(event) != {'state', 'at_utc', 'detail'} or not isinstance(event['detail'], dict):
            raise ValueError('invalid command event')
        stamp = datetime.fromisoformat(event['at_utc'])
        if stamp.utcoffset() != timezone.utc.utcoffset(stamp):
            raise ValueError('UTC timestamp required')
        state = event['state']
        if (previous is None and state != 'REQUESTED') or (previous is not None and state not in NEXT.get(previous, set())):
            raise ValueError('invalid command state transition')
        detail = event['detail']
        if state in {'REQUESTED', 'RUNNING'} and detail != {}:
            raise ValueError('unexpected initial command detail')
        if state == 'REJECTED' and detail != {'reason': 'capability_revoked_before_dispatch'}:
            raise ValueError('invalid rejection detail')
        if state == 'UNKNOWN' and (set(detail) != {'reason', 'error_type'}
                or detail['reason'] != 'execution_or_readback_failed'
                or not isinstance(detail['error_type'], str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', detail['error_type'])):
            raise ValueError('invalid unknown-outcome detail')
        if state == 'CONFIRMED' and (set(detail) != {'topology_version', 'topology_sha256'}
                or type(detail['topology_version']) is not int
                or detail['topology_version'] != record['request']['expected_version'] + 1
                or not isinstance(detail['topology_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', detail['topology_sha256'])):
            raise ValueError('invalid confirmation detail')
        previous = state
    return record


def _decode(row):
    if len(row['record_json'].encode('utf-8')) > 32768:
        raise ValueError('command record exceeds 32 KiB')
    record = _verify(json.loads(row['record_json']), row['record_sha256'])
    if record['command_id'] != row['command_id']:
        raise ValueError('command index mismatch')
    return {'record': record, 'record_sha256': row['record_sha256']}


def read_commands(path, limit=50):
    """Read existing journal only. Missing table is a pre-Q04 journal, not an error."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('command limit must be 1..50')
    store = ReadOnlyCampaignStore(path)
    with closing(store._connect()) as connection:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='console_commands'").fetchone() is None:
            return []
        rows = connection.execute('SELECT command_id, record_json, record_sha256 FROM console_commands ORDER BY sequence DESC LIMIT ?', (limit,)).fetchall()
    return [_decode(row) for row in rows]


class ConsoleCommandJournal:
    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)
        if not self.path.is_file():
            raise ValueError('existing local journal required')

    def _connect(self):
        connection = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def begin(self, verb, request):
        request = validated_request(verb, request)
        record = dict(schema='orion.console_command.v1', command_id=str(uuid4()), verb=verb, request=request,
                      events=[dict(state='REQUESTED', at_utc=datetime.now(timezone.utc).isoformat(), detail={})],
                      physical_action_performed=False)
        with closing(self._connect()) as connection, connection:
            connection.execute('CREATE TABLE IF NOT EXISTS console_commands (sequence INTEGER PRIMARY KEY AUTOINCREMENT, command_id TEXT NOT NULL UNIQUE, record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL)')
            connection.execute('INSERT INTO console_commands(command_id, record_json, record_sha256) VALUES (?, ?, ?)',
                               (record['command_id'], json.dumps(record, allow_nan=False), _sha256(record)))
        return record['command_id']

    def transition(self, command_id, state, detail=None):
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT command_id, record_json, record_sha256 FROM console_commands WHERE command_id=?', (command_id,)).fetchone()
            if row is None:
                raise ValueError('unknown command ID')
            record = _decode(row)['record']
            previous = record['events'][-1]['state']
            if state not in NEXT.get(previous, set()):
                raise ValueError('invalid command state transition')
            record['events'].append(dict(state=state, at_utc=datetime.now(timezone.utc).isoformat(), detail=detail or {}))
            raw, digest = json.dumps(record, allow_nan=False), _sha256(record)
            _verify(record, digest)
            if len(raw.encode('utf-8')) > 32768:
                raise ValueError('command record exceeds 32 KiB')
            connection.execute('UPDATE console_commands SET record_json=?, record_sha256=? WHERE command_id=?', (raw, digest, command_id))
        return record
