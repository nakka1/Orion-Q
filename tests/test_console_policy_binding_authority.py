"""Loaded native policy sessions never inherit authority from displayed frames."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import time

import pytest

from quantum_autopilot.console.policy_binding_authority import PolicyBindingAuthority
from quantum_autopilot.console.policy_context_binding import PolicyContextBinding
from quantum_autopilot.console.policy_review_binding import PolicyReviewBinding
from quantum_autopilot.control_plane import policy_activation as activation
from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.topology import default_simulated_topology
from quantum_autopilot.core import EvidenceKind, Provenance
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
from quantum_autopilot.security.service_boundary import (
    request_deadline, service_resource, DurableResultAuditPending,
)
from tests import test_policy_approval as approvals
from tests import test_policy_activation as activations
from tests import test_policy_recovery as recoveries


REVIEW_OPS = ('inspect', 'prepare', 'review', 'approve', 'activate', 'read_active',
              'prepare_rollback', 'inspect_recovery', 'recover')


@pytest.fixture
def environment(tmp_path):
    service = AssuranceDashboardService(repository_root=Path(__file__).resolve().parents[1],
                                        control_journal_path=tmp_path/'journal.sqlite')
    service.controller_runtime
    topology, _ = service.topology_store.initialize(default_simulated_topology())
    identity = service.security.grant_identity('policy-binding-operator', ['operator'], ['*'])
    source = SimpleNamespace(_identity=identity)
    return service, source, topology


def preparation(context):
    return dict(mode='simulation', policy_id='repeater.utility.v1', threshold=.7,
                optional_model_id=None, expected_context_sha256=context['record']['context_sha256'])


def select(receipt):
    return receipt['record']['proposal_id'], receipt['record_sha256']


def loaded_review(environment, operation, monkeypatch):
    service, source, _ = environment
    binding = PolicyReviewBinding(service, source=source)
    context = binding.run('inspect')['receipt']
    if operation in ('inspect', 'read_active', 'inspect_recovery'):
        # For recovery inspection create an actual ambiguous activation below.
        if operation != 'inspect_recovery':
            return binding, ()
    request = preparation(context)
    if operation == 'prepare':
        return binding, (request,)
    proposal = binding.run('prepare', request)['receipt']
    if operation == 'review':
        return binding, select(proposal)
    reviewed = binding.run('review', *select(proposal))['receipt']
    if operation == 'approve':
        return binding, (approvals.request(reviewed),)
    approved = binding.run('approve', approvals.request(reviewed))['receipt']
    request = activations.request(approved)
    if operation == 'activate':
        return binding, (request,)
    if operation == 'prepare_rollback':
        active = binding.run('activate', request)['receipt']
        return binding, (active['record']['activation_id'], active['record_sha256'])
    with monkeypatch.context() as patch:
        def ambiguous(_connection):
            raise RuntimeError('injected pre-commit ambiguity')
        patch.setattr(activation, '_commit', ambiguous)
        with pytest.raises(RuntimeError, match='ambiguity'):
            binding.run('activate', request)
    assert service._controller_runtime._policy_activation_fault is not None
    if operation == 'inspect_recovery':
        return binding, ()
    selected = binding.run('inspect_recovery')['receipt']
    return binding, (recoveries.recovery_request(selected),)


def loaded_context(environment, operation):
    service, source, topology = environment
    binding = PolicyContextBinding(service, source=source)
    context = binding.run('inspect')
    if operation == 'inspect':
        return binding, ()
    node = next(node for node in topology.nodes if node.role.value == 'repeater')
    stamp = datetime.now(timezone.utc).isoformat()
    state = NetworkStateSnapshot(request_id='binding-authority-fixture', node_id=node.node_id,
        timestamp_utc=stamp, state_version=node.state_version, predicted_fidelity=.9,
        prediction_uncertainty=.01, telemetry_age_s=.1, memory_state=node.memory_state,
        available_pair_fidelities=node.available_pair_fidelities, memory_ages_s=node.memory_ages_s,
        telemetry=node.telemetry, integrity_verified=True, operator_interlock=False,
        provenance=Provenance(evidence_kind=EvidenceKind.SIMULATED, source='binding-authority-fixture',
            timestamp=stamp, metadata=dict(topology_id=topology.topology_id,
                evidence_lane='bell_fidelity_control', target_semantics='simulated_future_bell_fidelity',
                physical_calibration=False)))
    return binding, (dict(mode='simulation', candidate_id='repeater.utility.v1',
                         expected_context_sha256=context['record']['context_sha256'], snapshot=state.to_dict()),)


def deny(environment, condition):
    service, source, _ = environment
    if condition == 'anonymous':
        source._identity = None
    elif condition == 'revoked':
        service.security.revoke_identity(source._identity.subject)
    elif condition == 'roles':
        service.security.grant_identity(source._identity.subject, ['consumer'], ['*'])
    elif condition == 'scope':
        service.security.grant_identity(source._identity.subject, ['operator'], ['workspace:other'])


@pytest.mark.parametrize('operation', REVIEW_OPS)
@pytest.mark.parametrize('condition', ('anonymous', 'revoked', 'roles', 'scope'))
def test_every_loaded_review_action_denied_before_effect(environment, monkeypatch, operation, condition):
    binding, arguments = loaded_review(environment, operation, monkeypatch)
    service, _, _ = environment
    runtime = service._controller_runtime
    before = hashlib.sha256(runtime.journal.path.read_bytes()).hexdigest()
    policy = runtime.policy
    marker = runtime._policy_activation_marker
    deny(environment, condition)
    with pytest.raises(PermissionError):
        binding.dispatch(operation, *arguments)()
    assert hashlib.sha256(runtime.journal.path.read_bytes()).hexdigest() == before
    assert runtime.policy is policy and runtime._policy_activation_marker is marker


@pytest.mark.parametrize('operation', ('inspect', 'observe'))
@pytest.mark.parametrize('condition', ('anonymous', 'revoked', 'roles', 'scope'))
def test_every_loaded_context_action_denied_before_effect(environment, operation, condition):
    binding, arguments = loaded_context(environment, operation)
    before = environment[0].control_journal_path.read_bytes()
    deny(environment, condition)
    with pytest.raises(PermissionError):
        binding.dispatch(operation, *arguments)()
    assert environment[0].control_journal_path.read_bytes() == before


@pytest.mark.parametrize('binding_type', (PolicyReviewBinding, PolicyContextBinding))
def test_dispatch_retains_source_caller_instead_of_engine_account(environment, binding_type):
    service, source, _ = environment
    binding = binding_type(service, source=source)
    pending = binding.dispatch('inspect')
    service.security.revoke_identity(source._identity.subject)
    source._identity = service.security.local_identity()  # An administrator logs in later.
    with pytest.raises(PermissionError):
        pending()
    assert binding.dispatch('inspect')()  # A new dispatch captures the new operator.


@pytest.mark.parametrize('binding_type', (PolicyReviewBinding, PolicyContextBinding))
def test_explicit_anonymous_dispatch_never_falls_back_to_source(environment, binding_type):
    service, source, _ = environment
    binding = binding_type(service, source=source)
    with service.security.identity_scope(None):
        pending = binding.dispatch('inspect')
    with pytest.raises(PermissionError):
        pending()


@pytest.mark.parametrize('binding_type', (PolicyReviewBinding, PolicyContextBinding))
def test_unbound_direct_call_requires_explicit_identity(environment, binding_type):
    service, _, _ = environment
    binding = binding_type(service)
    with pytest.raises(PermissionError, match='explicit policy binding caller'):
        binding.run('inspect')
    with service.security.identity_scope(service.security.local_identity()):
        assert binding.run('inspect')


@pytest.mark.parametrize('operation', ('inspect', 'observe'))
def test_researcher_context_operations_are_permitted_only_in_workspace(environment, operation):
    service, source, _ = environment
    binding, arguments = loaded_context(environment, operation)
    source._identity = service.security.grant_identity('policy-binding-researcher', ['researcher'],
                                                     [service_resource(service)])
    assert binding.dispatch(operation, *arguments)()


@pytest.mark.parametrize('operation', ('prepare', 'approve', 'activate', 'prepare_rollback', 'recover'))
def test_researcher_cannot_use_operator_policy_actions(environment, monkeypatch, operation):
    service, source, _ = environment
    binding, arguments = loaded_review(environment, operation, monkeypatch)
    source._identity = service.security.grant_identity('policy-binding-researcher', ['researcher'], ['*'])
    with pytest.raises(PermissionError):
        binding.dispatch(operation, *arguments)()


@pytest.mark.parametrize('operation', ('inspect', 'review', 'read_active', 'inspect_recovery'))
def test_auditor_read_actions_succeed_with_exact_workspace_scope(environment, monkeypatch, operation):
    service, source, _ = environment
    binding, arguments = loaded_review(environment, operation, monkeypatch)
    source._identity = service.security.grant_identity('policy-binding-auditor', ['auditor'],
                                                     [service_resource(service)])
    assert binding.dispatch(operation, *arguments)()


@pytest.mark.parametrize('kind', ('review', 'context'))
def test_revocation_during_lock_wait_is_revalidated(environment, monkeypatch, kind):
    operation = 'prepare' if kind == 'review' else 'observe'
    binding, arguments = (loaded_review(environment, operation, monkeypatch) if kind == 'review'
                          else loaded_context(environment, operation))
    service, source, _ = environment
    runtime = service._controller_runtime
    entered = Event()
    original = service.security.authorize
    def authorize(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        return result
    monkeypatch.setattr(service.security, 'authorize', authorize)
    pending = binding.dispatch(operation, *arguments)
    before = runtime.journal.path.read_bytes()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with runtime._cycle_lock:
            future = pool.submit(pending)
            assert entered.wait(3)
            service.security.revoke_identity(source._identity.subject)
        with pytest.raises(PermissionError):
            future.result(5)
    assert runtime.journal.path.read_bytes() == before


@pytest.mark.parametrize('operation', ('activate', 'recover'))
@pytest.mark.parametrize('condition', ('revoked', 'deadline'))
def test_authority_rechecked_immediately_before_durable_commit(environment, monkeypatch, operation, condition):
    binding, arguments = loaded_review(environment, operation, monkeypatch)
    service, source, _ = environment
    runtime = service._controller_runtime
    before = runtime.journal.path.read_bytes()
    original = activation._schema
    commit_calls = []
    revoked = []
    administrator = service.security.local_identity()
    def before_sql(connection):
        original(connection)
        if condition == 'revoked':
            with service.security.identity_scope(administrator):
                service.security.revoke_identity(source._identity.subject)
            revoked.append(True)
        else:
            request_deadline.set(time.monotonic()-1)
    monkeypatch.setattr(activation, '_schema', before_sql)
    monkeypatch.setattr(activation, '_commit', lambda connection: commit_calls.append(True))
    with pytest.raises(PermissionError if condition == 'revoked' else TimeoutError):
        binding.dispatch(operation, *arguments)()
    assert not commit_calls and runtime.journal.path.read_bytes() == before
    if condition == 'revoked':
        assert revoked == [True]
        with pytest.raises(PermissionError, match='revoked'):
            service.security.principal(source._identity.subject)


@pytest.mark.parametrize('method', ('policy_decision_history', 'explain_policy_decision'))
@pytest.mark.parametrize('condition', ('anonymous', 'revoked', 'roles', 'scope'))
def test_audit_reads_revalidate_queued_caller(environment, method, condition):
    service, source, _ = environment
    authority = PolicyBindingAuthority(service, source=source)
    called = []
    pending = authority.prepare(method, lambda: called.append(True))
    if condition == 'anonymous':
        with service.security.identity_scope(None):
            pending = authority.prepare(method, lambda: called.append(True))
    else:
        deny(environment, condition)
    with pytest.raises(PermissionError):
        pending()
    assert called == []


@pytest.mark.parametrize('condition', ('revoked', 'roles', 'scope', 'deadline'))
def test_read_captured_during_authority_change_is_not_released(environment, condition):
    service, source, _ = environment
    administrator = service.security.local_identity()
    completed_read = []
    def revoked_read():
        with service.security.identity_scope(administrator):
            if condition == 'deadline':
                request_deadline.set(time.monotonic()-1)
            else:
                deny(environment, condition)
        completed_read.append(True)
        return {'sensitive': 'captured-evidence'}
    pending = PolicyBindingAuthority(service, source=source).prepare('policy_decision_history', revoked_read)
    with pytest.raises(TimeoutError if condition == 'deadline' else PermissionError):
        pending()
    assert completed_read == [True]  # The reader actually returned; refusal is the end guard.
    audit = service.security.audit.path.read_text(encoding='utf-8')
    assert 'captured-evidence' not in audit


def test_proposal_session_invalidated_if_grant_changes_during_capture(environment, monkeypatch):
    binding, arguments = loaded_review(environment, 'prepare', monkeypatch)
    service, source, _ = environment
    administrator = service.security.local_identity()
    original = binding.session.prepare
    completed = []
    def captured(*args):
        result = original(*args)
        with service.security.identity_scope(administrator):
            service.security.revoke_identity(source._identity.subject)
        completed.append(True)
        return result
    monkeypatch.setattr(binding.session, 'prepare', captured)
    with pytest.raises(PermissionError, match='revoked'):
        binding.dispatch('prepare', *arguments)()
    assert completed == [True] and binding.session._closed


@pytest.mark.parametrize('binding_type', (PolicyReviewBinding, PolicyContextBinding))
def test_dispatched_deadline_is_retained_and_worker_context_restored(environment, binding_type):
    service, source, _ = environment
    binding = binding_type(service, source=source)
    token = request_deadline.set(time.monotonic()+.01)
    try:
        pending = binding.dispatch('inspect')
    finally:
        request_deadline.reset(token)
    time.sleep(.02)
    def worker():
        assert request_deadline.get() is None
        with pytest.raises(TimeoutError):
            pending()
        assert request_deadline.get() is None
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(worker).result(5)


def test_dispatch_capture_does_not_read_vault_and_audit_contains_no_payload(environment, monkeypatch):
    service, source, _ = environment
    binding, arguments = loaded_review(environment, 'prepare', monkeypatch)
    with monkeypatch.context() as patch:
        patch.setattr(service.security, 'authorize', lambda *a, **k: pytest.fail('Qt authorization IO'))
        patch.setattr(service.security, '_revalidate', lambda *a, **k: pytest.fail('Qt identity IO'))
        pending = binding.dispatch('prepare', *arguments)
    result = pending()
    assert result['receipt']['record']['schema'] == 'orion.policy_proposal.v1'
    audit = service.security.audit.path.read_text(encoding='utf-8')
    assert 'expected_context_sha256' not in audit and 'threshold' not in audit
    assert service.security.audit.verify()


def public_transition(environment, operation, monkeypatch):
    service, _, _ = environment
    if operation == 'activate':
        request = activations.request(activations.approved(service))
        return lambda: service.activate_policy_change(request)
    recoveries.fail_commit(service, monkeypatch)
    request = recoveries.recovery_request(service.policy_recovery_context())
    return lambda: service.recover_policy_change(request)


@pytest.mark.parametrize('operation', ('activate', 'recover'))
@pytest.mark.parametrize('condition', ('revoked', 'deadline'))
def test_public_transition_rechecks_authority_immediately_before_commit(
        environment, monkeypatch, operation, condition):
    pending = public_transition(environment, operation, monkeypatch)
    service, source, _ = environment
    administrator = service.security.local_identity()
    original = activation._schema
    commits, injections = [], []
    before = service.control_journal_path.read_bytes()
    def before_sql(connection):
        original(connection)
        if condition == 'revoked':
            with service.security.identity_scope(administrator):
                service.security.revoke_identity(source._identity.subject)
        else:
            request_deadline.set(time.monotonic()-1)
        injections.append(True)
    monkeypatch.setattr(activation, '_schema', before_sql)
    monkeypatch.setattr(activation, '_commit', lambda connection: commits.append(True))
    deadline_token = request_deadline.set(None)
    try:
        with service.security.identity_scope(source._identity):
            with pytest.raises(PermissionError if condition == 'revoked' else TimeoutError):
                pending()
    finally:
        request_deadline.reset(deadline_token)
    assert injections == [True] and commits == []
    assert service.control_journal_path.read_bytes() == before
    if condition == 'revoked':
        with pytest.raises(PermissionError, match='revoked'):
            service.security.principal(source._identity.subject)


@pytest.mark.parametrize('entry', ('binding', 'service'))
def test_revocation_after_recovery_commit_keeps_checkpoint_and_invalidates_approvals(
        environment, monkeypatch, entry):
    service, source, _ = environment
    stale, old_arguments = loaded_review(environment, 'activate', monkeypatch)
    if entry == 'binding':
        binding, arguments = loaded_review(environment, 'recover', monkeypatch)
        pending = binding.dispatch('recover', *arguments)
    else:
        pending = public_transition(environment, 'recover', monkeypatch)
    old_service_session = service._policy_preparation
    administrator = service.security.local_identity()
    generation = service._policy_preparation_generation
    original = activation._commit
    committed = []
    def committed_then_revoked(connection):
        original(connection)
        committed.append(True)
        with service.security.identity_scope(administrator):
            service.security.revoke_identity(source._identity.subject)
    monkeypatch.setattr(activation, '_commit', committed_then_revoked)
    if entry == 'binding':
        result = pending()['receipt']
    else:
        with service.security.identity_scope(source._identity):
            result = pending()
    assert committed == [True]
    assert result['record']['transition_kind'] == 'recovery_checkpoint'
    assert service._policy_preparation_generation == generation+1
    assert service._controller_runtime._policy_activation_fault is None
    if old_service_session is not None:
        assert old_service_session._closed
    with service.security.identity_scope(administrator):
        status = service.policy_activation_status()
        assert status['record']['status'] == 'ACTIVE'
        assert status['record']['activation'] == result
        with pytest.raises(ValueError, match='source invalidated'):
            stale.run('activate', *old_arguments)
    with pytest.raises(PermissionError, match='revoked'):
        with service.security.identity_scope(source._identity):
            service.invalidate_policy_preparation()
    assert service._policy_preparation_generation == generation+1


@pytest.mark.parametrize('operation', ('activate', 'recover'))
def test_binding_postcommit_audit_failure_exposes_committed_receipt(environment, monkeypatch, operation):
    binding, arguments = loaded_review(environment, operation, monkeypatch)
    service, _, _ = environment
    original = service.security.audit.record
    def fail_result(identity, action, resource, outcome, details=None):
        if action == 'policy.activate' and outcome == 'completed':
            raise OSError('injected authenticated audit write failure')
        return original(identity, action, resource, outcome, details)
    monkeypatch.setattr(service.security.audit, 'record', fail_result)
    with pytest.raises(DurableResultAuditPending) as error:
        binding.dispatch(operation, *arguments)()
    proof = error.value.recovery()
    receipt = error.value.receipt
    assert proof['committed'] is True and proof['error'] == 'durable_result_audit_pending'
    assert proof['activation_id'] == receipt['record']['activation_id']
    assert proof['record_sha256'] == receipt['record_sha256']
    assert proof['recovery'] == 'inspect_activation_or_recovery_status_without_resubmission'
    assert service.policy_activation_status()['record']['activation'] == receipt
    rows = activations.rows(service._controller_runtime, 'policy_activations')
    assert len(rows) == 1


@pytest.mark.parametrize('operation', ('activate', 'recover'))
@pytest.mark.parametrize('condition', ('revoked', 'deadline'))
def test_public_transition_rechecks_after_controller_lock_wait(environment, monkeypatch, operation, condition):
    pending = public_transition(environment, operation, monkeypatch)
    service, source, _ = environment
    administrator = service.security.local_identity()
    entered = Event()
    authorize = service.security.authorize
    def marked_authorize(*args, **kwargs):
        result = authorize(*args, **kwargs)
        entered.set()
        return result
    monkeypatch.setattr(service.security, 'authorize', marked_authorize)
    before = service.control_journal_path.read_bytes()
    def worker():
        with service.security.identity_scope(source._identity):
            token = request_deadline.set(time.monotonic()+.1 if condition == 'deadline' else None)
            try:
                return pending()
            finally:
                request_deadline.reset(token)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service._controller_lock:
            future = pool.submit(worker)
            assert entered.wait(3)
            if condition == 'revoked':
                with service.security.identity_scope(administrator):
                    service.security.revoke_identity(source._identity.subject)
            else:
                time.sleep(.12)
        with pytest.raises(PermissionError if condition == 'revoked' else TimeoutError):
            future.result(5)
    assert service.control_journal_path.read_bytes() == before


@pytest.mark.parametrize('operation', ('activate', 'recover'))
def test_public_postcommit_audit_failure_exposes_committed_receipt(environment, monkeypatch, operation):
    pending = public_transition(environment, operation, monkeypatch)
    service, source, _ = environment
    original = service.security.audit.record
    def fail_result(identity, action, resource, outcome, details=None):
        if action == 'policy.activate' and outcome == 'completed':
            raise OSError('injected authenticated audit write failure')
        return original(identity, action, resource, outcome, details)
    monkeypatch.setattr(service.security.audit, 'record', fail_result)
    with service.security.identity_scope(source._identity):
        with pytest.raises(DurableResultAuditPending) as error:
            pending()
    receipt = error.value.receipt
    assert error.value.recovery()['activation_id'] == receipt['record']['activation_id']
    assert error.value.recovery()['recovery'] == 'inspect_activation_or_recovery_status_without_resubmission'
    assert service.policy_activation_status()['record']['activation'] == receipt
    before = activations.rows(service._controller_runtime, 'policy_activations')
    assert len(before) == 1
    with monkeypatch.context() as patch:
        patch.setattr(service.security.audit, 'record', original)
        with pytest.raises(ValueError):
            with service.security.identity_scope(source._identity):
                pending()
    assert activations.rows(service._controller_runtime, 'policy_activations') == before


@pytest.mark.parametrize('method', ('prepare_policy_change', 'review_policy_change', 'approve_policy_change'))
def test_non_durable_policy_methods_never_report_committed_audit_pending(environment, monkeypatch, method):
    service, source, _ = environment
    original = service.security.audit.record
    def fail_result(identity, action, resource, outcome, details=None):
        if outcome == 'completed':
            raise OSError('injected outcome failure')
        return original(identity, action, resource, outcome, details)
    monkeypatch.setattr(service.security.audit, 'record', fail_result)
    pending = PolicyBindingAuthority(service, source=source).prepare(method, lambda: {'record': {'status': 'PREPARED'}})
    if method == 'review_policy_change':  # Read actions have no duplicate result audit.
        assert pending() == {'record': {'status': 'PREPARED'}}
    else:
        with pytest.raises(OSError):
            pending()
    assert not service._controller_runtime._policy_activation_marker


@pytest.mark.parametrize('operation', ('activate_policy_change', 'recover_policy_change'))
@pytest.mark.parametrize('invalid', ('missing', 'schema', 'status', 'empty_id', 'bad_id',
                                    'missing_hash', 'bad_hash', 'mismatched_hash'))
def test_durable_policy_audit_error_requires_full_verified_receipt(operation, invalid):
    schema = 'orion.policy_activation.v1' if operation == 'activate_policy_change' else 'orion.policy_recovery.v1'
    record = dict(schema=schema, status='COMMITTED', activation_id='33982f5c-6aa5-473c-97a6-06a159ea108d')
    receipt = {'record': record, 'record_sha256': hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()}
    if invalid == 'missing':
        receipt = {}
    elif invalid in ('schema', 'status'):
        record[invalid] = 'NOT_COMMITTED'
    elif invalid in ('empty_id', 'bad_id'):
        record['activation_id'] = '' if invalid == 'empty_id' else 'arbitrary untrusted payload'
    elif invalid == 'missing_hash':
        del receipt['record_sha256']
    elif invalid == 'bad_hash':
        receipt['record_sha256'] = 'private data'
    else:
        receipt['record_sha256'] = '0'*64
    with pytest.raises(ValueError, match='committed policy receipt required'):
        DurableResultAuditPending(operation, receipt)
