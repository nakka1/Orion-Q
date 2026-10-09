"""Q05 preparation stays observational, bounded, source-bound and optional-model OFF."""
from dataclasses import asdict
import json
import sqlite3
from threading import Event
from concurrent.futures import ThreadPoolExecutor

import pytest

from quantum_autopilot.control_plane import policy_preparation as prep
from quantum_autopilot.control_plane.policy import RepeaterSandboxPolicy
from quantum_autopilot.control_plane.runtime import ControllerRuntime
from quantum_autopilot.control_plane.safety import SafetySupervisor, SafetyPolicyConfig
from quantum_autopilot.control_plane.topology import default_simulated_topology, _sha256
from quantum_autopilot.control_plane.inventory import AddSimulatedNode
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService


@pytest.fixture
def local(tmp_path):
    service = AssuranceDashboardService(control_journal_path=tmp_path/'control.sqlite')
    runtime = service.controller_runtime
    service.topology_store.initialize(default_simulated_topology())
    return service, runtime


def payload(context, **changes):
    return dict(dict(mode='simulation', policy_id=prep.POLICY_ID, threshold=.70,
                     optional_model_id=None, expected_context_sha256=context['record']['context_sha256']), **changes)


def prepare(service):
    context = service.policy_preparation_context()
    return service.prepare_policy_change(payload(context))


def selection(proposal):
    return proposal['record']['proposal_id'], proposal['record_sha256']


def review(service, proposal):
    return service.review_policy_change(*selection(proposal))


def test_actual_baseline_and_no_policy_cycle_or_database_mutation(local, monkeypatch, tmp_path):
    service, runtime = local
    active = runtime.policy
    def forbidden(*args, **kwargs):
        pytest.fail('preparation must not decide, execute, or load a model')
    monkeypatch.setattr(runtime, 'cycle', forbidden)
    monkeypatch.setattr(runtime.policy, 'decide', forbidden)
    for executor in (runtime.simulated_executor, runtime.dry_run_executor, runtime.physical_executor):
        monkeypatch.setattr(executor, 'execute', forbidden)
    from quantum_autopilot.model_intake import catalog
    monkeypatch.setattr(catalog, 'open_optional_model', forbidden)
    before = runtime.journal.path.read_bytes()
    proposal = prepare(service)
    result = review(service, proposal)
    assert result['record']['status'] == 'CURRENT'
    baseline = proposal['record']['context']['baseline']
    assert baseline['threshold'] == active.engine.threshold == .65
    assert baseline['risk_costs'] == asdict(active.engine.risk_cfg)
    assert baseline['energy_costs'] == asdict(active.engine.energy_cfg)
    assert proposal['record']['proposed_policy']['threshold'] == .7
    assert proposal['record']['proposed_policy']['optional_model_id'] is None
    assert runtime.policy is active and active.engine.threshold == .65
    assert active.engine._risk_controller.threshold == .65
    for item in (proposal, result):
        assert item['record_sha256'] == _sha256(item['record'])
        assert item['record']['commands_emitted'] == 0
        assert not any(item['record'][k] for k in ('activation_available','operator_approval_recorded','policy_activated','physical_action_performed'))
    target = tmp_path/'review.json'
    exported = service.export_policy_review(*selection(proposal), target)
    assert json.loads(target.read_text(encoding='utf-8')) == exported
    with pytest.raises(FileExistsError):
        service.export_policy_review(*selection(proposal), target)
    with pytest.raises(FileExistsError):
        service.export_policy_review(*selection(proposal), runtime.journal.path)
    assert runtime.journal.path.read_bytes() == before


@pytest.mark.parametrize('changes', [
    {'mode':'physical'}, {'mode':'shadow'}, {'policy_id':'ML'}, {'policy_id':None},
    {'threshold':True}, {'threshold':-.01}, {'threshold':1.01}, {'threshold':float('nan')},
    {'threshold':float('inf')}, {'threshold':'0.7'}, {'threshold':None},
    {'optional_model_id':'qkd.sync.ridge.1s.h2s'}, {'optional_model_id':False},
    {'expected_context_sha256':'bad'}, {'expected_context_sha256':'A'*64},
    {'threshold':.65}, {'unknown':True},
], ids=['physical','shadow','ml-policy','null-policy','bool','negative','above-one','nan','infinity',
        'string','null-threshold','ml-model','false-model','bad-hash','uppercase-hash','noop','extra-field'])
def test_invalid_proposal_preserves_previous_selection(local, changes):
    service, _ = local
    original = prepare(service)
    context = service.policy_preparation_context()
    with pytest.raises(ValueError):
        service.prepare_policy_change(payload(context, **changes))
    assert review(service, original)['record']['status'] == 'CURRENT'


def test_no_runtime_or_topology_is_initialized_by_preparation(tmp_path):
    service = AssuranceDashboardService(control_journal_path=tmp_path/'missing.sqlite')
    with pytest.raises(ValueError, match='initialize the simulation independently'):
        service.policy_preparation_context()
    assert not service.control_journal_path.exists() and service._controller_runtime is None
    runtime = service.controller_runtime
    before = runtime.journal.path.read_bytes()
    with pytest.raises(sqlite3.OperationalError):
        service.policy_preparation_context()
    assert runtime.journal.path.read_bytes() == before
    store = service.topology_store
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match='persisted simulator topology'):
        service.policy_preparation_context()
    assert store.path.read_bytes() == before and store.get('sandbox-line-3') is None


@pytest.mark.parametrize('change', ['topology','same_version_topology','policy','same_config_policy',
                                  'energy','risk','latency','wait','supervisor','same_config_supervisor'])
def test_context_changes_make_review_stale_and_do_not_rewrite_proposal(local, change):
    service, runtime = local
    proposal = prepare(service)
    captured = json.dumps(proposal, sort_keys=True)
    if change == 'topology':
        service.topology_store.add_inventory_node(AddSimulatedNode('extra','endpoint',0,'127.0.0.1',7411))
    elif change == 'same_version_topology':
        with sqlite3.connect(runtime.journal.path) as con:
            row = json.loads(con.execute('SELECT state_json FROM simulated_topologies').fetchone()[0])
            row['links'][0]['distance_km'] += 1
            con.execute('UPDATE simulated_topologies SET state_json=?',(json.dumps(row),))
    elif change in {'policy','same_config_policy'}:
        runtime.policy = RepeaterSandboxPolicy(.75 if change == 'policy' else .65)
    elif change == 'energy':
        runtime.policy.engine.energy_cfg.P_MEMORY_HOLD_W *= 2
    elif change == 'risk':
        runtime.policy.engine.risk_cfg.FAILURE_COST_J *= 2
    elif change == 'latency':
        runtime.policy.engine._risk_controller.inference_latency_s *= 2
    elif change == 'wait':
        runtime.policy.engine._risk_controller.wait_time_s *= 2
    else:
        runtime.supervisor = SafetySupervisor(SafetyPolicyConfig(max_prediction_uncertainty=.1)) if change == 'supervisor' else SafetySupervisor()
    result = review(service, proposal)['record']
    assert result['status'] == 'STALE' and result['reasons']
    assert result['proposal'] == proposal and json.dumps(proposal, sort_keys=True) == captured
    assert not result['activation_available']


def test_inspection_binding_refuses_stale_prepare(local):
    service, runtime = local
    context = service.policy_preparation_context()
    runtime.policy = RepeaterSandboxPolicy()  # Same parameters, new observed object.
    with pytest.raises(ValueError, match='context changed'):
        service.prepare_policy_change(payload(context))
    assert prepare(service)['record']['context']['policy_observation_revision'] == 1


def test_observed_staleness_is_sticky_even_if_state_is_restored(local):
    service, runtime = local
    proposal = prepare(service)
    initial = runtime.policy
    runtime.policy = RepeaterSandboxPolicy(.8)
    assert review(service, proposal)['record']['status'] == 'STALE'
    runtime.policy = initial
    restored = review(service, proposal)['record']
    assert restored['status'] == 'STALE' and 'previously_invalidated' in restored['reasons']
    assert review(service, prepare(service))['record']['status'] == 'CURRENT'


def test_expiry_uses_monotonic_time_and_boundary_is_excluded(local, monkeypatch):
    service, _ = local
    clock = [1000.]
    monkeypatch.setattr(prep.time, 'monotonic', lambda: clock[0])
    proposal = prepare(service)
    clock[0] += prep.MAX_PROPOSAL_AGE_S - .01
    assert review(service, proposal)['record']['status'] == 'CURRENT'
    clock[0] += .01
    assert 'proposal_expired' in review(service, proposal)['record']['reasons']
    clock[0] = 1001.
    assert 'previously_invalidated' in review(service, proposal)['record']['reasons']


@pytest.mark.parametrize('change', ['session','runtime','other_service','replacement'])
def test_proposals_cannot_cross_source_or_current_selection(local, change):
    service, runtime = local
    proposal = prepare(service)
    if change == 'session':
        service.invalidate_policy_preparation()
    elif change == 'runtime':
        service._controller_runtime = ControllerRuntime(runtime.journal.path)
    elif change == 'other_service':
        service = AssuranceDashboardService(control_journal_path=runtime.journal.path)
        service.controller_runtime
    else:
        prepare(service)
    with pytest.raises(ValueError, match='current local selection'):
        review(service, proposal)


def test_returned_objects_are_detached_and_modified_hashes_are_refused(local):
    service, _ = local
    proposal = prepare(service)
    key = selection(proposal)
    proposal['record']['proposed_policy']['threshold'] = .99
    result = service.review_policy_change(*key)
    assert result['record']['proposal']['record']['proposed_policy']['threshold'] == .7
    with pytest.raises(ValueError):
        service.review_policy_change(key[0], _sha256(proposal['record']))
    result['record']['proposal']['record']['proposed_policy']['threshold'] = .2
    assert service.review_policy_change(*key)['record']['proposal']['record']['proposed_policy']['threshold'] == .7


@pytest.mark.parametrize('change', ['unsupported_policy','unsupported_supervisor','inconsistent_engine','nonfinite_config','missing_topology','corrupt_topology','journal_changed'])
def test_unavailable_context_is_stale_not_permission(local, tmp_path, change):
    service, runtime = local
    proposal = prepare(service)
    if change == 'unsupported_policy':
        runtime.policy = object()
    elif change == 'unsupported_supervisor':
        runtime.supervisor = object()
    elif change == 'inconsistent_engine':
        runtime.policy.engine._risk_controller.threshold = .9
    elif change == 'nonfinite_config':
        runtime.policy.engine.energy_cfg.P_MEMORY_HOLD_W = float('nan')
    elif change == 'journal_changed':
        runtime.journal.path = tmp_path/'absent.sqlite'
    else:
        with sqlite3.connect(runtime.journal.path) as con:
            con.execute('DELETE FROM simulated_topologies' if change == 'missing_topology' else "UPDATE simulated_topologies SET state_json='{}'")
    result = review(service, proposal)['record']
    assert result['status'] == 'STALE' and result['current_context'] is None
    assert any(reason.startswith('context_unavailable:') for reason in result['reasons'])
    assert not result['activation_available']


def test_capture_detects_policy_change_during_read(local, monkeypatch):
    service, runtime = local
    original = prep._read_topology
    def changing(*args):
        result = original(*args)
        runtime.policy = RepeaterSandboxPolicy(.8)
        return result
    monkeypatch.setattr(prep, '_read_topology', changing)
    with pytest.raises(ValueError, match='policy changed during observation'):
        service.policy_preparation_context()


def test_review_waits_for_runtime_cycle_lock(local):
    service, runtime = local
    proposal = prepare(service)
    entered = Event()
    def read():
        entered.set()
        return review(service, proposal)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with runtime._cycle_lock:
            pending = pool.submit(read)
            assert entered.wait(2)
            assert not pending.done()
            runtime.policy = RepeaterSandboxPolicy(.8)
        assert pending.result(timeout=5)['record']['status'] == 'STALE'


def test_provider_stop_invalidates_preparation(local):
    pytest.importorskip('PySide6')
    from quantum_autopilot.console.service_provider import ServiceProvider
    service, _ = local
    proposal = prepare(service)
    provider = ServiceProvider(service)
    provider.stop()
    with pytest.raises(ValueError, match='current local selection'):
        review(service, proposal)


def test_unknown_request_fields_and_missing_fields_are_refused(local):
    service, _ = local
    request = payload(service.policy_preparation_context())
    for name in request:
        incomplete = dict(request)
        del incomplete[name]
        with pytest.raises(ValueError, match='fields'):
            service.prepare_policy_change(incomplete)
    for value in (None, [], 'configuration'):
        with pytest.raises(ValueError):
            service.prepare_policy_change(value)

def test_boolean_inner_cost_is_not_equivalent_to_numeric_outer_cost(local):
    service, runtime = local
    engine = runtime.policy.engine
    engine.energy_cfg.P_MEMORY_HOLD_W = 1
    proposal = prepare(service)
    engine._risk_controller.energy_cfg = type(engine.energy_cfg)(**dict(asdict(engine.energy_cfg), P_MEMORY_HOLD_W=True))
    result = review(service, proposal)['record']
    assert result['status'] == 'STALE'
    assert 'context_unavailable:ValueError' in result['reasons']
