"""Bounded export of a selected operational page, never a scientific dataset."""
import json
import re
from contextlib import closing
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .attempt_file import MAX_ATTEMPT_FILE_BYTES, verify_attempt_snapshot
from .campaign_attempts import _verified, validate_attempt_period, validate_attempt_status
from .temporal_campaign import validate_history_prefix
from .topology import _sha256, _utc_now


MAX_BATCH_ATTEMPTS = 50


def export_attempt_page(store, selection, destination, *, campaign_prefix='', status='', requested_from='', requested_before=''):
    """Recheck all selected hashes in one SELECT before exclusively creating a ZIP.

    selection is a sequence of {attempt_id, record_sha256}, in page order.
    All members are read by a single SQLite statement (one read snapshot).
    A later state transition does not change the exported snapshot.
    """
    if not isinstance(selection, (tuple, list)) or not 1 <= len(selection) <= MAX_BATCH_ATTEMPTS:
        raise ValueError('select a nonempty page of at most 50 attempts')
    prefix = validate_history_prefix(campaign_prefix)
    validate_attempt_status(status)
    validate_attempt_period(requested_from, requested_before)
    expected, ids = {}, []
    for item in selection:
        if (not isinstance(item, dict) or set(item) != {'attempt_id', 'record_sha256'}
                or not isinstance(item['attempt_id'], str) or not re.fullmatch('[0-9a-f]{32}', item['attempt_id'])
                or not isinstance(item['record_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', item['record_sha256'])):
            raise ValueError('invalid selected attempt identity/hash')
        ident = item['attempt_id']
        if ident in expected:
            raise ValueError('duplicate selected attempt')
        ids.append(ident)
        expected[ident] = item['record_sha256']
    placeholders = ','.join('?' for _ in ids)
    with store._lock, closing(store._connect()) as db:
        rows = db.execute(f'SELECT * FROM temporal_campaign_attempts WHERE attempt_id IN ({placeholders})', ids).fetchall()
    current = {}
    for row in rows:
        snapshot = verify_attempt_snapshot(_verified(row))
        ident = snapshot['record']['attempt_id']
        if snapshot['record_sha256'] != expected[ident]:
            raise ValueError('attempt changed since listing; refresh the page')
        current[ident] = snapshot
    if set(current) != set(expected):
        raise ValueError('attempt disappeared since listing; refresh the page')
    members, payloads = [], []
    for ident in ids:
        snapshot = current[ident]
        record, day = snapshot['record'], snapshot['record']['requested_at_utc'][:10]
        if (not record['campaign_id'].startswith(prefix) or (status and record['status'] != status)
                or (requested_from and day < requested_from) or (requested_before and day >= requested_before)):
            raise ValueError('selected page does not match the declared filters')
        filename = f'attempt-{ident}.json'
        payload = (json.dumps(snapshot, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
        if len(payload) > MAX_ATTEMPT_FILE_BYTES:
            raise ValueError('attempt exceeds the individual JSON verification limit')
        payloads.append((filename, payload))
        members.append({'file': filename, 'attempt_id': ident, 'record_sha256': snapshot['record_sha256']})
    manifest = {'schema': 'orion.campaign_attempt_page.v1', 'scope': 'native_console_software_execution',
                'physical_action_performed': False, 'page_only': True, 'count': len(members),
                'exported_at_utc': _utc_now(), 'filters': {'campaign_prefix': prefix, 'status': status,
                    'requested_from': requested_from, 'requested_before': requested_before}, 'members': members}
    manifest_wrapper = {'manifest': manifest, 'manifest_sha256': _sha256(manifest)}
    manifest_bytes = (json.dumps(manifest_wrapper, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    # All validation and serialization precede file creation. An I/O failure can
    # still leave a partial ZIP; exclusive creation deliberately refuses retries
    # over an existing path rather than silently deleting or overwriting it.
    with Path(destination).open('xb') as stream, ZipFile(stream, 'w', compression=ZIP_DEFLATED) as bundle:
        bundle.writestr('manifest.json', manifest_bytes)
        for filename, payload in payloads:
            bundle.writestr(filename, payload)
    return manifest_wrapper
