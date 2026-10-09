"""Resolve a recorded operational association, never infer or reconcile one."""
from contextlib import closing

from .attempt_file import _hex, verify_attempt_snapshot
from .campaign_read_store import ReadOnlyCampaignStore
from .campaign_attempts import RECEIPT_STATES, _verified as verify_attempt_row
from .temporal_campaign import _verified as verify_receipt_row
from .topology import _sha256


def read_attempt_receipt(database_path, attempt_id, expected_sha256):
    """Read both records in one SQLite snapshot and require the selected version.

    A matching receipt is a recorded association, not proof this dispatch ran
    the trials. No worker, write, inferred status or external file import.
    """
    if not _hex(attempt_id, 32) or not _hex(expected_sha256, 64):
        raise ValueError('invalid selected attempt identity/hash')
    store = ReadOnlyCampaignStore(database_path)
    with store._lock, closing(store._connect()) as connection:
        connection.execute('BEGIN')  # Consistent snapshot across both SELECTs.
        row = connection.execute('SELECT * FROM temporal_campaign_attempts WHERE attempt_id = ?',
                                 (attempt_id,)).fetchone()
        if row is None:
            raise ValueError('attempt disappeared; refresh the page')
        attempt = verify_attempt_snapshot(verify_attempt_row(row))
        if attempt['record_sha256'] != expected_sha256:
            raise ValueError('attempt changed since selection; refresh the page')
        record = attempt['record']
        if record['status'] not in RECEIPT_STATES:
            raise ValueError('attempt has no recorded receipt association; no inference permitted')
        row = connection.execute('SELECT * FROM temporal_path_campaigns WHERE campaign_id = ?',
                                 (record['campaign_id'],)).fetchone()
        if row is None:
            raise ValueError('linked receipt missing; no result inferred')
        receipt = verify_receipt_row(row)
    request = receipt['record']['request']
    if (receipt['record'].get('evidence_kind') != 'simulated'
            or receipt['record'].get('physical_action_performed') is not False
            or receipt['record'].get('workspace_mutated') is not False):
        raise ValueError('unsupported linked receipt evidence scope')
    if (receipt['record_sha256'] != record['receipt_sha256']
            or _sha256(request) != record['request_sha256']
            or receipt['record'].get('request_sha256') != record['request_sha256']
            or request.get('topology_id') != record['topology_id']
            or type(request.get('expected_version')) is not int
            or request['expected_version'] != record['expected_version']):
        raise ValueError('attempt/receipt association mismatch')
    return receipt
