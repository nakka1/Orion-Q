"""Operational attempts, separate from completed scientific campaign receipts.

OPEN means no terminal observation was journalled, not that a process is alive.
No startup reconciliation guesses failure or rewrites attempts from another window.
"""
import json
import re
from contextlib import closing
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from .topology import _sha256, _canonical_json, _utc_now
from .temporal_campaign import TemporalCampaignRequest, validate_history_prefix


TERMINAL = frozenset({'CONFIRMED', 'REPLAYED', 'RECOVERED', 'CANCELLED', 'FAILED_TO_START', 'FAILED', 'UNCONFIRMED'})
RECEIPT_STATES = frozenset({'CONFIRMED', 'REPLAYED', 'RECOVERED'})


def _verified(row):
    value = json.loads(row['record_json'])
    keys = {'schema', 'attempt_id', 'campaign_id', 'request_sha256', 'topology_id', 'expected_version',
            'requested_at_utc', 'finished_at_utc', 'status', 'process_exit_code', 'cancel_requested',
            'receipt_sha256', 'detail', 'scope', 'physical_action_performed'}
    if (not isinstance(value, dict) or _sha256(value) != row['record_sha256']
            or set(value) != keys
            or value.get('attempt_id') != row['attempt_id']
            or value.get('schema') != 'orion.campaign_attempt.v1'
            or value.get('status') not in TERMINAL | {'OPEN'}):
        raise ValueError('attempt integrity check failed')
    if (any(not isinstance(value[key], str) for key in ('attempt_id', 'campaign_id', 'topology_id', 'requested_at_utc', 'detail'))
            or not re.fullmatch('[0-9a-f]{32}', value['attempt_id'])
            or not isinstance(value['request_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['request_sha256'])
            or type(value['cancel_requested']) is not bool or type(value['expected_version']) is not int
            or len(value['detail']) > 2000 or value['scope'] != 'native_console_software_execution'
            or value['physical_action_performed'] is not False
            or (value['status'] == 'OPEN') != (value['finished_at_utc'] is None)
            or (value['status'] in RECEIPT_STATES) != (value['receipt_sha256'] is not None)):
        raise ValueError('invalid operational attempt metadata')
    # Filtering uses the UTC date prefix produced by this journal's writer.
    # A returned row with a non-UTC/malformed timestamp must not be presented.
    stamp = value['requested_at_utc']
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)', stamp):
        raise ValueError('invalid requested UTC timestamp')
    datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    return {'record': value, 'record_sha256': row['record_sha256']}


def begin_attempt(store, request):
    if type(request) is not TemporalCampaignRequest:
        raise ValueError('validated temporal campaign request required')
    record = {'schema': 'orion.campaign_attempt.v1', 'attempt_id': uuid4().hex,
              'campaign_id': request.campaign_id, 'request_sha256': _sha256(asdict(request)),
              'topology_id': request.topology_id, 'expected_version': request.expected_version,
              'requested_at_utc': _utc_now(), 'finished_at_utc': None,
              'status': 'OPEN', 'process_exit_code': None, 'cancel_requested': False,
              'receipt_sha256': None, 'detail': 'Dispatch registered; no terminal observation yet.',
              'scope': 'native_console_software_execution', 'physical_action_performed': False}
    with store._lock, closing(store._connect()) as db, db:
        db.execute('INSERT INTO temporal_campaign_attempts (attempt_id,record_sha256,record_json) VALUES (?,?,?)',
                   (record['attempt_id'], _sha256(record), _canonical_json(record)))
    return record['attempt_id']


def finish_attempt(store, attempt_id, *, status, exit_code, cancelled=False, receipt_sha256=None, detail=''):
    if status not in TERMINAL or type(exit_code) is not int or type(cancelled) is not bool:
        raise ValueError('invalid terminal observation')
    if not isinstance(detail, str) or len(detail) > 2000:
        raise ValueError('attempt detail must be text of at most 2000 characters')
    if (status in RECEIPT_STATES) != (receipt_sha256 is not None):
        raise ValueError('receipt states require a receipt hash; failures must not fabricate one')
    if receipt_sha256 is not None and (not isinstance(receipt_sha256, str) or not re.fullmatch('[0-9a-f]{64}', receipt_sha256)):
        raise ValueError('invalid receipt hash')
    changes = {'status': status, 'process_exit_code': exit_code, 'cancel_requested': cancelled,
               'receipt_sha256': receipt_sha256, 'detail': detail}
    with store._lock, closing(store._connect()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM temporal_campaign_attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
        if row is None:
            raise ValueError('unknown attempt')
        record = _verified(row)['record']
        if record['status'] != 'OPEN':
            if all(record[key] == value for key, value in changes.items()):
                return
            raise ValueError('terminal attempt cannot be rewritten')
        record.update(changes, finished_at_utc=_utc_now())
        db.execute('UPDATE temporal_campaign_attempts SET record_sha256=?,record_json=? WHERE attempt_id=?',
                   (_sha256(record), _canonical_json(record), attempt_id))


def validate_attempt_status(status):
    if not isinstance(status, str) or status not in TERMINAL | {'OPEN', ''}:
        raise ValueError('invalid operational status filter')
    return status


def validate_attempt_period(requested_from, requested_before):
    for value in (requested_from, requested_before):
        if not isinstance(value, str) or (value and not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value)):
            raise ValueError('requested UTC dates must be YYYY-MM-DD or empty')
        if value:
            date.fromisoformat(value)
    if requested_from and requested_before and requested_from >= requested_before:
        raise ValueError('requested UTC start must precede the exclusive end date')
    return requested_from, requested_before


def list_attempts(store, *, before_sequence=None, limit=20, campaign_prefix='', status='', requested_from='', requested_before=''):
    """Filter operational metadata; verify returned rows, not the whole database."""
    prefix = validate_history_prefix(campaign_prefix)
    validate_attempt_status(status)
    validate_attempt_period(requested_from, requested_before)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('invalid attempt page size')
    if before_sequence is not None and (type(before_sequence) is not int or not 1 <= before_sequence <= 2**63-1):
        raise ValueError('invalid attempt cursor')
    query, args, clauses = 'SELECT * FROM temporal_campaign_attempts', [], []
    if before_sequence is not None:
        clauses.append('sequence < ?')
        args.append(before_sequence)
    filters = []
    if prefix:
        filters.append("instr(json_extract(record_json, '$.campaign_id'), ?) = 1")
        args.append(prefix)
    if status:
        filters.append("json_extract(record_json, '$.status') = ?")
        args.append(status)
    # UTC writer timestamps have a fixed ISO date prefix. Do not use floating
    # Julian dates: rounding can move a microsecond before midnight to the next day.
    if requested_from:
        filters.append("substr(json_extract(record_json, '$.requested_at_utc'), 1, 10) >= ?")
        args.append(requested_from)
    if requested_before:
        filters.append("substr(json_extract(record_json, '$.requested_at_utc'), 1, 10) < ?")
        args.append(requested_before)
    if filters:
        # Malformed JSON must reach verification rather than vanish under a filter.
        # CASE guards json_extract. Well-formed rows outside the filter are not audited.
        clauses.append('CASE WHEN json_valid(record_json) THEN (' + ' AND '.join(filters) + ') ELSE 1 END')
    if clauses:
        query += ' WHERE ' + ' AND '.join(clauses)
    with store._lock, closing(store._connect()) as db:
        rows = db.execute(query + ' ORDER BY sequence DESC LIMIT ?', (*args, limit+1)).fetchall()
    # Metadata only (no shot arrays/matrices), at most one page verified per read.
    return tuple(_verified(row) for row in rows[:limit]), rows[limit-1]['sequence'] if len(rows) > limit else None


def read_attempt(store, attempt_id):
    if not isinstance(attempt_id, str) or not re.fullmatch('[0-9a-f]{32}', attempt_id):
        raise ValueError('invalid attempt ID')
    with store._lock, closing(store._connect()) as db:
        row = db.execute('SELECT * FROM temporal_campaign_attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
    return _verified(row) if row is not None else None


def export_attempt_snapshot(store, attempt_id, expected_sha256, destination):
    """Export the selected version, not a newer terminal state substituted silently.

The record can advance AFTER this read. The file is the verified read snapshot,
not a statement about current process liveness. OPEN is valid operational data.
"""
    if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise ValueError('invalid selected attempt hash')
    snapshot = read_attempt(store, attempt_id)
    if snapshot is None or snapshot['record_sha256'] != expected_sha256:
        raise ValueError('attempt changed or disappeared since selection; refresh and select again')
    payload = json.dumps(snapshot, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
    # Serialize before touching the destination; exclusive create also rejects symlinks.
    with Path(destination).open('x', encoding='utf-8') as stream:
        stream.write(payload)
    return snapshot
