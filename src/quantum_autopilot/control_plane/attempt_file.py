"""Bounded, read-only verification of untrusted operational JSON snapshots.

No store is opened, referenced paths are not followed, and no receipt is imported.
The digest proves local consistency only, not authorship or scientific validity.
"""
import json
import re
from datetime import datetime
from pathlib import Path

from .campaign_attempts import TERMINAL, RECEIPT_STATES
from .topology import _sha256


MAX_ATTEMPT_FILE_BYTES = 32768
_KEYS = {'schema', 'attempt_id', 'campaign_id', 'request_sha256', 'topology_id', 'expected_version',
         'requested_at_utc', 'finished_at_utc', 'status', 'process_exit_code', 'cancel_requested',
         'receipt_sha256', 'detail', 'scope', 'physical_action_performed'}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON field')
        result[key] = value
    return result


def _nonfinite(_):
    raise ValueError('non-finite JSON number')


def _hex(value, length):
    return isinstance(value, str) and re.fullmatch(f'[0-9a-f]{{{length}}}', value) is not None


def _utc(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)', value):
        raise ValueError('UTC timestamp required')
    datetime.fromisoformat(value.replace('Z', '+00:00'))


def verify_attempt_file(path):
    """Return a validated snapshot of at most 32 KiB, without database access."""
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_ATTEMPT_FILE_BYTES + 1)
    if len(raw) > MAX_ATTEMPT_FILE_BYTES:
        raise ValueError('operational JSON exceeds 32 KiB')
    try:
        snapshot = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique, parse_constant=_nonfinite)
    except (UnicodeError, RecursionError, ValueError) as exc:
        raise ValueError('invalid UTF-8 operational JSON: ' + type(exc).__name__) from exc
    return verify_attempt_snapshot(snapshot)


def verify_attempt_snapshot(snapshot):
    """Validate one decoded operational snapshot without file or database access."""
    if not isinstance(snapshot, dict) or set(snapshot) != {'record', 'record_sha256'}:
        raise ValueError('operational snapshot wrapper required; not a scientific receipt')
    value = snapshot['record']
    if (not isinstance(value, dict) or set(value) != _KEYS
            or value['schema'] != 'orion.campaign_attempt.v1'
            or value['scope'] != 'native_console_software_execution'
            or value['physical_action_performed'] is not False):
        raise ValueError('unsupported operational schema or evidence scope')
    if (not _hex(snapshot['record_sha256'], 64) or not _hex(value['attempt_id'], 32)
            or not _hex(value['request_sha256'], 64)
            or type(value['expected_version']) is not int or value['expected_version'] < 0
            or type(value['cancel_requested']) is not bool):
        raise ValueError('invalid operational identity, version or flag')
    for key in ('campaign_id', 'topology_id'):
        if not isinstance(value[key], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', value[key]):
            raise ValueError('invalid operational identifier')
    if not isinstance(value['detail'], str) or len(value['detail']) > 2000:
        raise ValueError('invalid operational detail')
    status = value['status']
    if not isinstance(status, str) or status not in TERMINAL | {'OPEN'}:
        raise ValueError('invalid operational status')
    _utc(value['requested_at_utc'])
    if status == 'OPEN':
        if value['finished_at_utc'] is not None or value['process_exit_code'] is not None or value['cancel_requested']:
            raise ValueError('OPEN must have no terminal observation')
    else:
        _utc(value['finished_at_utc'])
        if type(value['process_exit_code']) is not int:
            raise ValueError('terminal exit code must be an integer')
    if status in RECEIPT_STATES:
        if not _hex(value['receipt_sha256'], 64):
            raise ValueError('receipt state requires a receipt hash')
    elif value['receipt_sha256'] is not None:
        raise ValueError('non-receipt state cannot carry a receipt hash')
    try:
        digest = _sha256(value)
    except (UnicodeError, ValueError) as exc:
        raise ValueError('record cannot be canonically encoded') from exc
    if digest != snapshot['record_sha256']:
        raise ValueError('record SHA256 mismatch')
    return snapshot
