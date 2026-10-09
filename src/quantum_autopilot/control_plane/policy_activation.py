"""Q05 process-bound simulation activation with durable evidence, never physical control."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from uuid import UUID, uuid4

from .campaign_read_store import ReadOnlyCampaignStore
from .command_reconciliation import _strict_json
from .policy import RepeaterSandboxPolicy
from .policy_preparation import _describe, _receipt, MAX_PROPOSAL_AGE_S
from . import policy_preparation as preparation
from .topology import _sha256

ACTIVATION_CONFIRMATION = 'ACTIVATE_APPROVED_SIMULATION_POLICY'
MAX_RECORD_BYTES = 256 * 1024


@dataclass(frozen=True)
class PolicyActivationRequest:
    mode: str
    approval_id: str
    expected_approval_sha256: str
    confirmation: str

    def __post_init__(self):
        if self.mode != 'simulation' or self.confirmation != ACTIVATION_CONFIRMATION:
            raise ValueError('explicit simulation activation confirmation required')
        try:
            canonical = str(UUID(self.approval_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('canonical approval UUID required') from None
        if canonical != self.approval_id:
            raise ValueError('canonical approval UUID required')
        if not isinstance(self.expected_approval_sha256, str) or not re.fullmatch('[0-9a-f]{64}', self.expected_approval_sha256):
            raise ValueError('exact selected approval SHA-256 required')

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict) or set(payload) != {'mode', 'approval_id', 'expected_approval_sha256', 'confirmation'}:
            raise ValueError('invalid policy activation fields')
        return cls(**payload)


def _connect(path):
    connection = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10.0)
    connection.row_factory = sqlite3.Row
    return connection


def _schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS policy_activations (
        revision INTEGER PRIMARY KEY, activation_id TEXT NOT NULL UNIQUE,
        approval_id TEXT NOT NULL UNIQUE, runtime_instance_id TEXT NOT NULL,
        record_sha256 TEXT NOT NULL, record_json TEXT NOT NULL)''')


def _commit(connection):
    connection.commit()


def _candidate(baseline, proposed, supervisor):
    from quantum_twin.control.policy import PolicyEngine
    from quantum_twin.control.risk_aware import RiskCostConfig
    from quantum_twin.evaluation.energy import EnergyConfig
    candidate = RepeaterSandboxPolicy(proposed['threshold'])
    candidate.engine = PolicyEngine(threshold=proposed['threshold'],
                                   energy_cfg=EnergyConfig(**baseline['energy_costs']),
                                   risk_cfg=RiskCostConfig(**baseline['risk_costs']))
    candidate.engine._risk_controller.inference_latency_s = baseline['inference_latency_s']
    candidate.engine._risk_controller.wait_time_s = baseline['wait_time_s']
    actual, _, _ = _describe(SimpleNamespace(policy=candidate, supervisor=supervisor))
    if _sha256(actual) != _sha256(proposed):
        raise ValueError('candidate does not reproduce the approved configuration')
    return candidate


def _unexpired(session):
    if session._started is None or not 0 <= preparation.time.monotonic() - session._started < MAX_PROPOSAL_AGE_S:
        raise ValueError('proposal expired before activation completed')


def activate(session, payload, *, validate_source=None):
    request = PolicyActivationRequest.from_dict(payload)
    runtime = session.runtime
    with session._lock, runtime._cycle_lock:
        if validate_source is not None:
            validate_source()
        if session._closed or session._stale or runtime._policy_activation_fault is not None:
            raise ValueError('session stale/closed or runtime activation unresolved')
        approval = json.loads(session._approval_json) if session._approval_json else None
        if (approval is None or approval['record']['approval_id'] != request.approval_id
                or approval['record_sha256'] != request.expected_approval_sha256):
            raise ValueError('approval or hash is not the current local selection; imported receipts cannot activate')
        old_policy, old_marker = runtime.policy, runtime._policy_activation_marker
        committed_attempt = False
        # From this point an attempted activation consumes the selected approval.
        try:
            _unexpired(session)
            with closing(_connect(session._journal_path)) as connection:
                try:
                    connection.execute('BEGIN IMMEDIATE')
                    context = session._capture(connection=connection)
                    expected = approval['record']['confirmation_review']['record']['current_context_sha256']
                    if _sha256(context) != expected:
                        raise ValueError('approved context changed; prepare and approve again')
                    if old_marker is not None:
                        _verify_marker(runtime, connection)
                    proposal = approval['record']['approved_review']['record']['proposal']
                    proposed = proposal['record']['proposed_policy']
                    rollback = proposal['record'].get('rollback_of')
                    if rollback is not None:
                        if (old_marker is None or old_marker['activation_id'] != rollback['activation_id']
                                or old_marker['record_sha256'] != rollback['record_sha256']):
                            raise ValueError('rollback activation selection changed')
                        prior = _verify_marker(runtime, connection)['record']
                        target = prior.get('rollback_policy', prior['previous_policy'])
                        if target is None or _sha256(target) != rollback['target_policy_sha256'] or proposed != target:
                            raise ValueError('rollback target configuration mismatch')
                    candidate = _candidate(context['baseline'], proposed, runtime.supervisor)
                    _unexpired(session)
                    _schema(connection)
                    revision = connection.execute('SELECT COALESCE(MAX(revision),0)+1 FROM policy_activations').fetchone()[0]
                    record = dict(schema='orion.policy_activation.v1', activation_id=str(uuid4()), revision=revision,
                                  status='COMMITTED', transition_kind='rollback' if rollback is not None else 'activation',
                                  rollback_of=rollback, runtime_instance_id=runtime.instance_id,
                                  recorded_at_utc=datetime.now(timezone.utc).isoformat(), mode='simulation',
                                  scope='this_runtime_instance_only_no_restart_restore', approval=approval,
                                  validated_context=context, previous_policy=context['baseline'],
                                  active_policy=proposed, active_policy_sha256=_sha256(proposed),
                                  previous_activation_id=None if old_marker is None else old_marker['activation_id'],
                                  supervisor=context['supervisor'], commands_emitted=0,
                                  optional_models_activated=False, physical_action_performed=False,
                                  interpretation='Committed activation in one process; current activity requires live readback.')
                    receipt = _receipt(record)
                    raw = json.dumps(record, ensure_ascii=False, allow_nan=False)
                    if len(raw.encode('utf-8')) > MAX_RECORD_BYTES:
                        raise ValueError('activation record exceeds 256 KiB')
                    connection.execute('INSERT INTO policy_activations VALUES (?,?,?,?,?,?)',
                                       (revision, record['activation_id'], request.approval_id, runtime.instance_id,
                                        receipt['record_sha256'], raw))
                    _unexpired(session)
                    runtime.policy = candidate
                    observed, supervisor, identities = _describe(runtime)
                    if _sha256(observed) != record['active_policy_sha256'] or supervisor != context['supervisor']:
                        raise ValueError('candidate readback mismatch')
                    runtime._policy_activation_marker = dict(activation_id=record['activation_id'],
                        record_sha256=receipt['record_sha256'], revision=revision, identities=identities,
                        journal_path=session._journal_path)
                    # A native caller may cancel while waiting for locks/SQLite.
                    # Once commit begins, cancellation cannot promise to undo it.
                    if validate_source is not None:
                        validate_source()
                    committed_attempt = True
                    _commit(connection)
                    # A slow/ambiguous commit must not leave an expired proposal active.
                    _unexpired(session)
                except BaseException:
                    runtime.policy, runtime._policy_activation_marker = old_policy, old_marker
                    if committed_attempt:
                        from .policy_recovery import mark_uncertain
                        mark_uncertain(runtime, receipt, session._last_identity, session._journal_path)
                    connection.rollback()
                    raise
            return receipt
        finally:
            session._approval_json = session._review_json = None
            session._stale = True


def _load(connection, activation_id, expected_sha256):
    row = connection.execute('SELECT revision, activation_id, approval_id, runtime_instance_id, record_sha256, '
                             'substr(record_json,1,?) AS record_json, length(CAST(record_json AS BLOB)) AS size '
                             'FROM policy_activations WHERE activation_id=?', (MAX_RECORD_BYTES+1, activation_id)).fetchone()
    if row is None or row['size'] > MAX_RECORD_BYTES:
        raise ValueError('activation missing or oversized')
    record = _strict_json(row['record_json'])
    if (not isinstance(record, dict) or _sha256(record) != expected_sha256 or row['record_sha256'] != expected_sha256
            or record.get('schema') not in {'orion.policy_activation.v1','orion.policy_recovery.v1'} or record.get('status') != 'COMMITTED'
            or record.get('activation_id') != row['activation_id'] or record.get('revision') != row['revision']
            or record.get('runtime_instance_id') != row['runtime_instance_id']):
        raise ValueError('activation receipt/index/hash mismatch')
    if record['schema'] == 'orion.policy_activation.v1':
        approval = record['approval']
        if approval['record']['approval_id'] != row['approval_id'] or _sha256(approval['record']) != approval['record_sha256']:
            raise ValueError('activation nested receipt mismatch')
    else:
        from .policy_recovery import PolicyRecoveryRequest
        request = PolicyRecoveryRequest.from_dict(record['recovery_request'])
        reviewed, failed = record['reviewed_context'], record['failed_attempt']
        if (row['approval_id'] != 'recovery:'+request.fault_id
                or _sha256(reviewed['record']) != reviewed['record_sha256']
                or reviewed['record_sha256'] != request.expected_recovery_sha256
                or _sha256(failed['record']) != failed['record_sha256']):
            raise ValueError('recovery nested receipt mismatch')
    if _sha256(record['active_policy']) != record['active_policy_sha256']:
        raise ValueError('activation policy hash mismatch')
    return {'record': record, 'record_sha256': expected_sha256}


def activation_record(path, activation_id, expected_sha256):
    with closing(ReadOnlyCampaignStore(path)._connect()) as connection:
        return _load(connection, activation_id, expected_sha256)


def _verify_marker(runtime, connection):
    marker = runtime._policy_activation_marker
    if runtime.journal.path.resolve(strict=True) != marker['journal_path']:
        raise ValueError('activation journal changed')
    receipt = _load(connection, marker['activation_id'], marker['record_sha256'])
    record = receipt['record']
    observed, supervisor, identities = _describe(runtime)
    if (record['runtime_instance_id'] != runtime.instance_id or record['revision'] != marker['revision']
            or _sha256(observed) != record['active_policy_sha256'] or supervisor != record['supervisor']
            or any(a is not b for a,b in zip(identities, marker['identities']))):
        raise ValueError('live policy does not match this activation')
    return receipt


def activation_status(runtime):
    with runtime._cycle_lock:
        record = dict(schema='orion.policy_activation_readback.v1', observed_at_utc=datetime.now(timezone.utc).isoformat(),
                      runtime_instance_id=runtime.instance_id, mode='simulation', physical_action_performed=False,
                      current_activity_observed=False, commands_emitted=0)
        if runtime._policy_activation_fault is not None:
            fault = runtime._policy_activation_recovery
            return _receipt(dict(record, status='UNRESOLVED', reason=runtime._policy_activation_fault,
                                 fault_id=None if fault is None else fault['fault_id']))
        marker = runtime._policy_activation_marker
        if marker is None:
            return _receipt(dict(record, status='NO_LOCAL_ACTIVATION', reason='No activation in this runtime; persisted historical records are not restored.'))
        try:
            with closing(ReadOnlyCampaignStore(runtime.journal.path)._connect()) as connection:
                receipt = _verify_marker(runtime, connection)
        except Exception as exc:
            return _receipt(dict(record, status='UNVERIFIED', activation_id=marker['activation_id'], reason=type(exc).__name__))
        return _receipt(dict(record, status='ACTIVE', current_activity_observed=True, activation=receipt,
                             interpretation='Live simulation policy matches committed evidence at readback time; no physical authority.'))


def ensure_activation_consistent(runtime):
    result = activation_status(runtime)['record']
    if result['status'] != 'ACTIVE':
        raise ValueError('activated simulation policy cannot run: ' + result['status'])
    return result['activation']['record']


def export_activation(path, activation_id, expected_sha256, destination):
    receipt = activation_record(path, activation_id, expected_sha256)
    with Path(destination).open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(receipt, ensure_ascii=False, allow_nan=False, indent=2)+'\n')
    return receipt
