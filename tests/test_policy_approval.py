"""Local explicit Q05 approval binds reviewed evidence, without activation authority."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from threading import Event

import pytest

from quantum_autopilot.control_plane import policy_preparation as prep
from quantum_autopilot.control_plane.policy_approval import APPROVAL_CONFIRMATION
from quantum_autopilot.control_plane.policy import RepeaterSandboxPolicy
from quantum_autopilot.control_plane.runtime import ControllerRuntime
from quantum_autopilot.control_plane.inventory import AddSimulatedNode
from quantum_autopilot.control_plane.topology import _sha256
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
from tests import test_policy_preparation as shared

local = shared.local


def reviewed(service):
    proposal = shared.prepare(service)
    review = shared.review(service, proposal)
    return proposal, review


def request(review, **changes):
    proposal = review['record']['proposal']
    return dict(dict(mode='simulation', proposal_id=proposal['record']['proposal_id'],
                     expected_proposal_sha256=proposal['record_sha256'], expected_review_sha256=review['record_sha256'],
                     operator_label='test operator', confirmation=APPROVAL_CONFIRMATION), **changes)


def export(service, approval, path):
    return service.export_policy_approval(approval['record']['approval_id'], approval['record_sha256'], path)


def test_explicit_approval_binds_original_and_fresh_evidence_without_action(local, monkeypatch, tmp_path):
    service, runtime = local
    proposal, review = reviewed(service)
    baseline = runtime.policy
    before = runtime.journal.path.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail('approval must not run a cycle, decide, execute or open models')
    monkeypatch.setattr(runtime, 'cycle', forbidden)
    monkeypatch.setattr(baseline, 'decide', forbidden)
    for executor in (runtime.simulated_executor, runtime.dry_run_executor, runtime.physical_executor):
        monkeypatch.setattr(executor, 'execute', forbidden)
    from quantum_autopilot.model_intake import catalog
    monkeypatch.setattr(catalog, 'open_optional_model', forbidden)
    approval = service.approve_policy_change(request(review))
    record = approval['record']
    assert record['status'] == 'RECORDED' and record['operator_approval_recorded'] is True
    assert record['approved_review'] == review
    assert record['confirmation_review']['record']['proposal'] == proposal
    assert record['confirmation_review']['record']['review_id'] != review['record']['review_id']
    assert record['confirmation_review']['record']['current_context_sha256'] == review['record']['current_context_sha256']
    assert record['operator_identity'] == 'caller_declared_not_authenticated'
    assert record['proposal_expires_at_utc'] == proposal['record']['expires_at_utc']
    assert record['scope'] == 'simulation_proposal_only'
    assert record['commands_emitted'] == 0
    for key in ('activation_authority','activation_available','policy_activated','physical_action_performed'):
        assert record[key] is False
    assert approval['record_sha256'] == _sha256(record)
    for receipt in (record['approved_review'], record['confirmation_review']):
        assert receipt['record_sha256'] == _sha256(receipt['record'])
    target = tmp_path/'approval.json'
    assert export(service, approval, target) == approval
    assert json.loads(target.read_text(encoding='utf-8')) == approval
    assert runtime.policy is baseline and baseline.engine.threshold == .65
    assert runtime.journal.path.read_bytes() == before


@pytest.mark.parametrize('changes', [
    {'mode':'physical'}, {'mode':'shadow'}, {'confirmation':True}, {'confirmation':False},
    {'confirmation':'APPROVE'}, {'confirmation':None}, {'proposal_id':'wrong'}, {'proposal_id':None},
    {'expected_proposal_sha256':'bad'}, {'expected_review_sha256':'A'*64},
    {'expected_review_sha256':None}, {'operator_label':''}, {'operator_label':'x'*81},
    {'operator_label':' operator'}, {'operator_label':'operator\n'}, {'operator_label':'x\x00y'},
    {'operator_label':True}, {'unknown':True}, {'optional_model_id':'ridge'},
    {'activation_authority':True}, {'expected_review_sha256':'0'*64}, {'expected_proposal_sha256':'0'*64},
])
def test_invalid_approval_never_records_or_consumes_review(local, changes):
    service, _ = local
    _, review = reviewed(service)
    with pytest.raises(ValueError):
        service.approve_policy_change(request(review, **changes))
    assert service._policy_preparation._approval_json is None
    assert service.approve_policy_change(request(review))['record']['operator_approval_recorded']


def test_missing_fields_and_non_objects_refused(local):
    service, _ = local
    _, review = reviewed(service)
    payload = request(review)
    for field in payload:
        with pytest.raises(ValueError):
            service.approve_policy_change({k:v for k,v in payload.items() if k != field})
    for value in (None, [], 'approval'):
        with pytest.raises(ValueError):
            service.approve_policy_change(value)
    assert service._policy_preparation._approval_json is None


@pytest.mark.parametrize('change', ['policy','same_policy','topology','same_version_topology','missing_topology',
                                  'corrupt_topology','supervisor','unsupported','journal','expiry'])
def test_context_changed_after_review_cannot_be_approved(local, monkeypatch, tmp_path, change):
    service, runtime = local
    clock = [1000.]
    monkeypatch.setattr(prep.time, 'monotonic', lambda: clock[0])
    _, review = reviewed(service)
    if change in {'policy','same_policy'}:
        runtime.policy = RepeaterSandboxPolicy(.8 if change == 'policy' else .65)
    elif change == 'topology':
        service.topology_store.add_inventory_node(AddSimulatedNode('new','endpoint',0,'127.0.0.1',7411))
    elif change == 'supervisor':
        runtime.supervisor = type(runtime.supervisor)()
    elif change == 'unsupported':
        runtime.policy = object()
    elif change == 'journal':
        runtime.journal.path = tmp_path/'absent.sqlite'
    elif change == 'expiry':
        clock[0] += prep.MAX_PROPOSAL_AGE_S
    else:
        with sqlite3.connect(runtime.journal.path) as con:
            if change == 'missing_topology':
                con.execute('DELETE FROM simulated_topologies')
            elif change == 'corrupt_topology':
                con.execute("UPDATE simulated_topologies SET state_json='{}'")
            else:
                row=json.loads(con.execute('SELECT state_json FROM simulated_topologies').fetchone()[0])
                row['links'][0]['distance_km'] += 1
                con.execute('UPDATE simulated_topologies SET state_json=?',(json.dumps(row),))
    with pytest.raises(ValueError, match='approval refused'):
        service.approve_policy_change(request(review))
    assert service._policy_preparation._approval_json is None
    assert json.loads(service._policy_preparation._review_json)['record']['status'] == 'STALE'


@pytest.mark.parametrize('change', ['new_review','new_proposal','session','runtime','service'])
def test_approval_cannot_cross_selection_or_session(local, change):
    service, runtime = local
    proposal, review = reviewed(service)
    if change == 'new_review':
        assert shared.review(service, proposal)['record_sha256'] != review['record_sha256']
    elif change == 'new_proposal':
        shared.prepare(service)
    elif change == 'session':
        service.invalidate_policy_preparation()
    elif change == 'runtime':
        service._controller_runtime = ControllerRuntime(runtime.journal.path)
    else:
        service = AssuranceDashboardService(control_journal_path=runtime.journal.path)
        service.controller_runtime
    with pytest.raises(ValueError, match='current local selection'):
        service.approve_policy_change(request(review))
    assert service._policy_preparation._approval_json is None


def test_stale_review_and_reverted_context_do_not_become_approval(local):
    service, runtime = local
    proposal, _ = reviewed(service)
    original = runtime.policy
    runtime.policy = RepeaterSandboxPolicy(.8)
    stale = shared.review(service, proposal)
    runtime.policy = original
    with pytest.raises(ValueError, match='CURRENT'):
        service.approve_policy_change(request(stale))
    still_stale = shared.review(service, proposal)
    with pytest.raises(ValueError, match='CURRENT'):
        service.approve_policy_change(request(still_stale))


def test_deadline_crossed_during_read_is_stale_for_review_and_approval(local, monkeypatch):
    service, _ = local
    clock = [1000.]
    monkeypatch.setattr(prep.time, 'monotonic', lambda: clock[0])
    proposal, review = reviewed(service)
    clock[0] += prep.MAX_PROPOSAL_AGE_S - .1
    original = prep._read_topology
    def slow(*args):
        result = original(*args)
        clock[0] += .1
        return result
    monkeypatch.setattr(prep, '_read_topology', slow)
    with pytest.raises(ValueError, match='proposal_expired'):
        service.approve_policy_change(request(review))
    assert 'proposal_expired' in shared.review(service, proposal)['record']['reasons']
    assert service._policy_preparation._approval_json is None


def test_deadline_crossed_while_building_approval_is_refused(local, monkeypatch):
    service, _ = local
    clock=[1000.]
    monkeypatch.setattr(prep.time, 'monotonic', lambda: clock[0])
    _, review=reviewed(service)
    original=prep._receipt
    def slower(record):
        result=original(record)
        if record['schema']=='orion.policy_approval.v1':
            clock[0] += prep.MAX_PROPOSAL_AGE_S
        return result
    monkeypatch.setattr(prep, '_receipt', slower)
    with pytest.raises(ValueError, match='expired while recording'):
        service.approve_policy_change(request(review))
    assert service._policy_preparation._approval_json is None


def test_duplicate_and_concurrent_requests_cannot_record_twice(local):
    service, _ = local
    _, review = reviewed(service)
    def submit():
        try:
            return service.approve_policy_change(request(review))
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _: submit(), range(2)))
    assert sum(result is not None for result in results) == 1
    approval = next(result for result in results if result is not None)
    with pytest.raises(ValueError):
        service.approve_policy_change(request(review))
    with pytest.raises(ValueError, match='already recorded'):
        service.approve_policy_change(request(approval['record']['confirmation_review']))


def test_approval_waits_for_cycle_lock_then_refuses_changed_policy(local):
    service, runtime = local
    _, review = reviewed(service)
    entered = Event()
    def approve():
        entered.set()
        return service.approve_policy_change(request(review))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with runtime._cycle_lock:
            pending = pool.submit(approve)
            assert entered.wait(2) and not pending.done()
            runtime.policy = RepeaterSandboxPolicy(.8)
        with pytest.raises(ValueError, match='approval refused'):
            pending.result(timeout=5)


@pytest.mark.parametrize('change', ['review','prepare','invalidate'])
def test_new_selection_invalidates_export_of_previous_approval(local, tmp_path, change):
    service, _ = local
    proposal, review = reviewed(service)
    approval = service.approve_policy_change(request(review))
    if change == 'review':
        shared.review(service, proposal)
    elif change == 'prepare':
        shared.prepare(service)
    else:
        service.invalidate_policy_preparation()
    with pytest.raises(ValueError, match='current local selection'):
        export(service, approval, tmp_path/'old.json')
    assert not (tmp_path/'old.json').exists()


def test_export_is_detached_historical_exclusive_and_does_not_renew(local, tmp_path, monkeypatch):
    service, runtime = local
    clock=[1000.]
    monkeypatch.setattr(prep.time, 'monotonic', lambda:clock[0])
    _, review = reviewed(service)
    approved = service.approve_policy_change(request(review))
    captured=json.loads(json.dumps(approved))
    approved['record']['request']['operator_label']='changed by caller'
    review['record']['status']='invented'
    clock[0] += prep.MAX_PROPOSAL_AGE_S
    runtime.policy = RepeaterSandboxPolicy(.8)
    def forbidden(*args):
        pytest.fail('historical export must not revalidate, approve again or renew')
    monkeypatch.setattr(prep, '_read_topology', forbidden)
    target=tmp_path/'historical.json'
    assert export(service, approved, target)==captured
    before=target.read_bytes()
    with pytest.raises(FileExistsError):
        export(service, approved, target)
    assert target.read_bytes()==before
    with pytest.raises(ValueError):
        service.export_policy_approval(approved['record']['approval_id'], _sha256(approved['record']), tmp_path/'forged.json')
    with pytest.raises(FileExistsError):
        export(service, approved, runtime.journal.path)


def test_missing_runtime_and_missing_review_cannot_be_approved(tmp_path, local):
    service=AssuranceDashboardService(control_journal_path=tmp_path/'absent.sqlite')
    local_service, _ = local
    _, review=reviewed(local_service)
    with pytest.raises(ValueError, match='initialize'):
        service.approve_policy_change(request(review))
    assert service._controller_runtime is None and not service.control_journal_path.exists()
    local_service._policy_preparation._review_json=None
    with pytest.raises(ValueError, match='current local selection'):
        local_service.approve_policy_change(request(review))
