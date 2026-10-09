"""Read-only, hash-checked explanations of recorded simulation cycles; never re-decide."""
from contextlib import closing
from datetime import datetime, timezone
import re

from .campaign_read_store import ReadOnlyCampaignStore
from .command_reconciliation import _strict_json
from .contracts import ControlCycleRecord, CONTROL_PLANE_SCHEMA_VERSION
from .policy_activation import _load
from .policy_preparation import _receipt
from .runtime import _request_hash
from .topology import _sha256

MAX_CYCLE_BYTES = 256 * 1024


def _digest(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('lowercase SHA-256 required')
    return value


def _cycle(connection, request_id, expected_sha256=None):
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200 or not request_id.isprintable():
        raise ValueError('printable request_id of 1..200 characters required')
    if expected_sha256 is not None:
        _digest(expected_sha256)
    row = connection.execute('''SELECT sequence, cycle_id, request_id, request_sha256, record_sha256,
        mode, action, status, completed_at_utc, substr(record_json,1,?) AS record_json,
        length(CAST(record_json AS BLOB)) AS size FROM control_cycles WHERE request_id=?''',
        (MAX_CYCLE_BYTES+1, request_id)).fetchone()
    if row is None or row['size'] > MAX_CYCLE_BYTES:
        raise ValueError('simulation cycle missing or exceeds 256 KiB')
    raw = _strict_json(row['record_json'])
    if not isinstance(raw, dict) or _sha256(raw) != row['record_sha256']:
        raise ValueError('cycle receipt hash mismatch')
    if expected_sha256 is not None and row['record_sha256'] != expected_sha256:
        raise ValueError('selected cycle hash changed')
    cycle = ControlCycleRecord.from_dict(raw)
    if (cycle.mode.value != 'simulation' or cycle.schema_version != CONTROL_PLANE_SCHEMA_VERSION
            or any(raw[part].get('schema_version') != CONTROL_PLANE_SCHEMA_VERSION
                   for part in ('snapshot', 'intent', 'safety', 'execution'))):
        raise ValueError('only supported simulation cycle receipts may be explained')
    if (row['cycle_id'] != cycle.cycle_id or row['request_id'] != cycle.snapshot.request_id
            or row['request_sha256'] != cycle.request_sha256 or row['mode'] != cycle.mode.value
            or row['action'] != cycle.safety.effective_action.value or row['status'] != cycle.execution.status
            or row['completed_at_utc'] != cycle.completed_at_utc
            or _request_hash(cycle.mode, cycle.snapshot) != cycle.request_sha256):
        raise ValueError('cycle index or request hash mismatch')
    if (cycle.intent.request_id != cycle.snapshot.request_id or cycle.intent.node_id != cycle.snapshot.node_id
            or cycle.intent.action != cycle.safety.intended_action
            or cycle.safety.effective_action != cycle.execution.action
            or (cycle.safety.authorized and (cycle.safety.violations or cycle.intent.action != cycle.safety.effective_action))):
        raise ValueError('cycle intent/safety/execution mismatch')
    return {'record': raw, 'record_sha256': row['record_sha256']}, row['sequence']


def _policy(connection, cycle):
    intent = cycle['intent']
    ref = intent['policy_details'].get('policy_activation')
    if ref is None:
        return dict(status='NOT_RECORDED', reference=None, activation=None,
                    reason='No activation reference was recorded in this cycle; current configuration is not substituted.')
    try:
        required = {'activation_id', 'revision', 'active_policy_sha256', 'runtime_instance_id'}
        if not isinstance(ref, dict) or set(ref) not in (required, required | {'activation_record_sha256'}):
            raise ValueError('invalid cycle policy reference')
        _digest(ref['active_policy_sha256'])
        if type(ref['revision']) is not int or ref['revision'] < 1:
            raise ValueError('invalid recorded revision')
        row = connection.execute('SELECT record_sha256 FROM policy_activations WHERE activation_id=?',
                                 (ref['activation_id'],)).fetchone()
        if row is None:
            return dict(status='UNAVAILABLE', reference=ref, activation=None, reason='Referenced policy receipt is absent.')
        anchored = 'activation_record_sha256' in ref
        expected = _digest(ref['activation_record_sha256']) if anchored else row['record_sha256']
        receipt = _load(connection, ref['activation_id'], expected)
        record = receipt['record']
        if (record['revision'] != ref['revision'] or record['runtime_instance_id'] != ref['runtime_instance_id']
                or record['active_policy_sha256'] != ref['active_policy_sha256']
                or record['active_policy']['policy_name'] != intent['policy_name']):
            raise ValueError('recorded activation reference mismatch')
        return dict(status='VERIFIED_RECORDED_HASH' if anchored else 'LEGACY_REFERENCE_MATCH',
                    reference=ref, activation=receipt,
                    reason='Historical correspondence only; no current activity claim.' if anchored else
                    'Legacy cycle matches ID/revision/runtime/configuration; the activation receipt hash was not recorded in that cycle.')
    except Exception as exc:
        return dict(status='UNVERIFIED', reference=ref, activation=None, reason=type(exc).__name__)


def decision_history(path, limit=20):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('limit must be an integer in [1, 50]')
    with closing(ReadOnlyCampaignStore(path)._connect()) as connection:
        connection.execute('BEGIN')
        rows = connection.execute("SELECT request_id FROM control_cycles WHERE mode='simulation' ORDER BY sequence DESC LIMIT ?", (limit,)).fetchall()
        items = []
        for row in rows:
            receipt, sequence = _cycle(connection, row['request_id'])
            record = receipt['record']
            items.append(dict(request_id=row['request_id'], cycle_id=record['cycle_id'], sequence=sequence,
                record_sha256=receipt['record_sha256'], completed_at_utc=record['completed_at_utc'],
                intended_action=record['intent']['action'], effective_action=record['safety']['effective_action']))
    return _receipt(dict(schema='orion.policy_decision_history.v1', observed_at_utc=datetime.now(timezone.utc).isoformat(),
        items=items, limit=limit, scope='recorded_simulation_cycles_only', physical_action_performed=False, commands_emitted=0))


def explain_decision(path, request_id, expected_sha256=None):
    with closing(ReadOnlyCampaignStore(path)._connect()) as connection:
        connection.execute('BEGIN')
        receipt, sequence = _cycle(connection, request_id, expected_sha256)
        cycle = receipt['record']
        policy = _policy(connection, cycle)
    return _receipt(dict(schema='orion.policy_decision_explanation.v1', observed_at_utc=datetime.now(timezone.utc).isoformat(),
        cycle=receipt, sequence=sequence, request_id=request_id, cycle_id=cycle['cycle_id'], mode=cycle['mode'],
        policy_name=cycle['intent']['policy_name'], policy_evidence=policy,
        intended_action=cycle['intent']['action'], authorized=cycle['safety']['authorized'],
        safety_violations=cycle['safety']['violations'], effective_action=cycle['safety']['effective_action'],
        execution=cycle['execution'], recorded_policy_details=cycle['intent']['policy_details'],
        decision_recomputed=False, current_activity_observed=False, physical_action_performed=False, commands_emitted=0,
        interpretation='Recorded simulation cycle, independent of current runtime policy. Safety effective action and executor outcome are separate recorded facts.'))
