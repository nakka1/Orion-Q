"""Causal, fixed-plant allocation shadow integration; synthetic package only."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import zipfile

import pytest

from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.optional_allocation import (
    MODEL_IDS, TELEMETRY_KEY, AllocationDriftSample, AllocationSample, AllocationShadowWindow,
    evaluate_optional_allocation, export_allocation_shadow, main,
)
from quantum_autopilot.control_plane.runtime import ControllerRuntime
from quantum_autopilot.core import Provenance
from quantum_autopilot.model_intake import catalog
from quantum_autopilot.model_intake.allocation_session import demo_allocation_input, evaluate_allocation_session
from quantum_autopilot.model_intake.artifacts import ALLOCATION_ROOT
from tests import test_optional_allocation_session as fixtures

make_archive = fixtures.make_archive
allocation_package = fixtures.allocation_package
pytestmark = pytest.mark.unit
ORIGIN = datetime(2026, 9, 24, tzinfo=timezone.utc)
MODEL = MODEL_IDS[1]


def window(plant='a'*64, count=5):
    data = json.loads(demo_allocation_input(MODEL))
    obs = data['observation']
    stamp = (ORIGIN + timedelta(seconds=obs.pop('time_s'))).isoformat()
    history = tuple(AllocationDriftSample((ORIGIN + timedelta(seconds=t)).isoformat(), row)
                    for t, row in zip(data['history_times_s'], data['drift_history']))
    return AllocationShadowWindow('fixture', 'campaign', 'stream', 'node', 'clock', plant, ORIGIN.isoformat(),
                                  AllocationSample(stamp, **obs), history[-count:] if count else ())


def snapshot(plant='a'*64, count=5):
    w = window(plant, count)
    return NetworkStateSnapshot(
        request_id='req', node_id=w.node_id, timestamp_utc=w.observation.timestamp_utc, state_version=1,
        predicted_fidelity=.5, prediction_uncertainty=1., telemetry_age_s=0., memory_state='IDLE',
        integrity_verified=True, telemetry={TELEMETRY_KEY: w.to_dict()},
        provenance=Provenance('simulated', w.source_id, timestamp=w.observation.timestamp_utc),
        metadata={'campaign_id': w.campaign_id, 'clock_id': w.clock_id, 'allocation_stream_id': w.stream_id,
                  'allocation_plant_sha256': w.plant_sha256, 'allocation_origin_timestamp_utc': w.origin_timestamp_utc})


def plant_hash(path):
    with zipfile.ZipFile(path) as archive:
        return hashlib.sha256(archive.read(ALLOCATION_ROOT + 'model/plant.json')).hexdigest()


def run(state, archive=None, model=MODEL):
    return evaluate_optional_allocation(state, model_id=model, archive_path=archive)


def forbid_loading(monkeypatch):
    monkeypatch.setattr(catalog, 'open_optional_model', lambda *a, **k: pytest.fail('weights opened'))


def test_off_and_unavailable_never_load(monkeypatch):
    forbid_loading(monkeypatch)
    assert evaluate_optional_allocation(object(), archive_path='missing.zip').status == 'off'
    state = replace(snapshot(), telemetry={'qber': .01, 'ber': 1e-6, 'optional_compensation_window': {},
                                           'optional_sync_window': {}})
    assert run(state).reason == 'missing_allocation_window'
    assert run(snapshot()).reason == 'missing_archive'
    with pytest.raises(ValueError, match='checkpoint'):
        run(snapshot(), model='qnet.compensation.calibrado.ridge')
    with pytest.raises(ValueError, match='NetworkStateSnapshot'):
        run({})


@pytest.mark.parametrize('model', MODEL_IDS)
@pytest.mark.parametrize('count', [0, 1, 4, 5])
def test_offline_parity_warmup_and_queue_preserved(allocation_package, model, count):
    state = snapshot(plant_hash(allocation_package), count)
    before = state.to_json()
    result = run(state, allocation_package, model)
    assert result.status == 'evaluated', result.detail
    evaluation = json.loads(result.evaluation_json)
    offline = evaluate_allocation_session(model, allocation_package, evaluation['input_json'])
    assert evaluation['recommendation'] == json.loads(offline.serialized())['recommendation']
    assert evaluation['persistence'] == json.loads(offline.serialized())['persistence']
    assert result.final_output_status == ('persistence' if model == MODEL_IDS[0]
                                         else 'ridge' if count == 5 else 'warmup_persistence')
    assert result.effective_timestamp_utc == (ORIGIN + timedelta(seconds=55)).isoformat()
    assert evaluation['recommendation']['effective_at_s'] == 55.
    assert evaluation['recommendation']['key_accounting'] == 'asymptotic_forecast_not_certified'
    assert state.to_json() == before
    assert result.snapshot_sha256 == hashlib.sha256(result.snapshot_json.encode()).hexdigest()
    assert not any((result.physical_action_performed, result.simulator_action_performed,
                    result.policy_activated, result.queue_mutated))


BAD_FIELDS = [
    ('source_id', 'other'), ('node_id', 'other'), ('campaign_id', 'other'), ('clock_id', 'other'),
    ('stream_id', 'other'), ('plant_sha256', 'b'*64), ('plant_sha256', 'invalid'),
    ('origin_timestamp_utc', '2026-09-24T00:00:01+00:00'), ('evidence', 'measured'),
    ('protocol', 'decoy_state_bb84'), ('period_s', True), ('period_s', 1.), ('latency_s', 5.),
    ('horizon_s', 30.), ('qber_unit', 'percent'), ('volume_unit', 'byte'), ('time_unit', 'ms'),
    ('queue_order', 'newest_first'), ('drift_definition', 'oracle'), ('link_names', []),
    ('schema', 'unknown'), ('reference', {}), ('observation', []), ('history', {}),
]


@pytest.mark.parametrize('name,value', BAD_FIELDS, ids=[f'field-{i}' for i in range(len(BAD_FIELDS))])
def test_bad_window_refused_before_weights(name, value, monkeypatch):
    forbid_loading(monkeypatch)
    state = snapshot()
    state.telemetry[TELEMETRY_KEY][name] = value
    result = run(state, 'unused.zip')
    assert result.status == 'refused' and result.reason == 'incompatible_allocation_window'
    assert result.evaluation_json is None


@pytest.mark.parametrize('case', ['future', 'stale', 'measured', 'integrity', 'ood', 'metadata',
                                 'origin', 'stream', 'plant', 'links', 'history_future', 'history_gap',
                                 'history_reversed', 'history_duplicate', 'history_extra', 'history_large', 'history_shape',
                                 'history_before_origin', 'queue', 'queue_bool', 'qber', 'qber_bool',
                                 'urgency', 'urgency_bool', 'pool', 'backlog', 'naive', 'extra'])
def test_bad_context_or_sample(case, monkeypatch):
    forbid_loading(monkeypatch)
    state = snapshot()
    w = state.telemetry[TELEMETRY_KEY]
    obs = w['observation']
    if case in {'future', 'stale'}:
        state = replace(state, timestamp_utc=(ORIGIN + timedelta(seconds=24 if case == 'future' else 30.000001)).isoformat())
    elif case == 'measured':
        state.provenance.evidence_kind = 'measured'
    elif case == 'integrity':
        state = replace(state, integrity_verified=False)
    elif case == 'ood':
        state = replace(state, out_of_distribution=True)
    elif case == 'metadata':
        state.metadata.clear()
    elif case in {'origin', 'stream', 'plant'}:
        state.metadata[{'origin': 'allocation_origin_timestamp_utc', 'stream': 'allocation_stream_id',
                        'plant': 'allocation_plant_sha256'}[case]] = 'other'
    elif case == 'links':
        w['link_names'].reverse()
    elif case in {'history_future', 'history_gap', 'history_before_origin'}:
        t = {'history_future': 30, 'history_gap': 19, 'history_before_origin': -5}[case]
        w['history'][-1 if case != 'history_before_origin' else 0]['timestamp_utc'] = (ORIGIN + timedelta(seconds=t)).isoformat()
    elif case == 'history_reversed':
        w['history'].reverse()
    elif case == 'history_duplicate':
        w['history'][-1]['timestamp_utc'] = w['history'][-2]['timestamp_utc']
    elif case == 'history_extra':
        w['history'].append(w['history'][-1])
    elif case == 'history_large':
        w['history'][0]['drift'][0] = 1.1
    elif case == 'history_shape':
        w['history'][0]['drift'].pop()
    elif case in {'queue', 'queue_bool'}:
        obs['pending_admission'][0][0] = .1 if case == 'queue' else True
    elif case in {'qber', 'qber_bool'}:
        obs['measured_qber'][0] = 1.1 if case == 'qber' else True
    elif case in {'urgency', 'urgency_bool'}:
        obs['urgency_s'][0] = 0 if case == 'urgency' else True
    elif case in {'pool', 'backlog'}:
        obs['key_pool_bits' if case == 'pool' else 'backlog_bits'][0] = -1
    elif case == 'naive':
        obs['timestamp_utc'] = '2026-09-24T00:00:25'
    else:
        obs['future_key_bits'] = 100
    assert run(state, 'unused.zip').status == 'refused'


def test_immutable_types_and_timezones(allocation_package):
    state = snapshot(plant_hash(allocation_package))
    w = AllocationShadowWindow.from_dict(state.telemetry[TELEMETRY_KEY])
    assert AllocationShadowWindow.from_dict(w.to_dict()) == w
    result = run(state, allocation_package)
    data = state.telemetry[TELEMETRY_KEY]
    data['observation']['timestamp_utc'] = '2026-09-23T21:00:25-03:00'
    data['origin_timestamp_utc'] = '2026-09-23T21:00:00-03:00'
    data['observation']['backlog_bits'][0] = 2.
    assert w.observation.backlog_bits[0] == 100000.
    assert json.loads(result.snapshot_json)['telemetry'][TELEMETRY_KEY]['observation']['backlog_bits'][0] == 100000.
    data['observation']['backlog_bits'][0] = 100000.
    assert json.loads(run(state, allocation_package).evaluation_json)['input_json'] == json.loads(result.evaluation_json)['input_json']


def test_plant_mismatch_and_replaced_archive(allocation_package):
    state = snapshot()
    assert run(state, allocation_package).reason == 'plant_identity_mismatch'
    state = snapshot(plant_hash(allocation_package))
    allocation_package.write_bytes(b'corrupted')
    assert run(state, allocation_package).reason == 'model_evaluation_failed'


def test_fidelity_ignored_and_age_boundary(allocation_package):
    state = snapshot(plant_hash(allocation_package))
    a = run(state, allocation_package)
    b = run(replace(state, timestamp_utc=(ORIGIN + timedelta(seconds=30)).isoformat(),
                    predicted_fidelity=.99, telemetry_age_s=99.), allocation_package)
    assert b.status == 'evaluated' and b.window_age_s == 5.
    assert json.loads(a.evaluation_json)['recommendation'] == json.loads(b.evaluation_json)['recommendation']


def test_no_runtime_queue_or_journal_mutation(allocation_package, tmp_path, monkeypatch):
    runtime = ControllerRuntime(tmp_path / 'commands.sqlite')
    before = (tmp_path / 'commands.sqlite').read_bytes()
    for obj, method in [(runtime, 'cycle'), (runtime.policy, 'decide'), (runtime.journal, 'append'),
                        (runtime.simulated_executor, 'execute'), (runtime.physical_executor, 'execute'),
                        (runtime.dry_run_executor, 'execute')]:
        monkeypatch.setattr(obj, method, lambda *a, **k: pytest.fail('control invoked'))
    assert runtime.observe_optional_allocation(object()).status == 'off'
    assert runtime.observe_optional_allocation(snapshot(plant_hash(allocation_package)), model_id=MODEL,
                                               archive_path=allocation_package).status == 'evaluated'
    assert (tmp_path / 'commands.sqlite').read_bytes() == before


def test_cli_and_exclusive_export(allocation_package, tmp_path):
    state = snapshot(plant_hash(allocation_package))
    source, output = tmp_path / 'input.json', tmp_path / 'output.json'
    source.write_text(state.to_json(), encoding='utf-8')
    assert main(['--input', str(source), '--output', str(output), '--model', MODEL,
                 '--qnet-zip', str(allocation_package)]) == 0
    result = json.loads(output.read_text(encoding='utf-8'))
    assert result['status'] == 'evaluated' and result['queue_mutated'] is False
    with pytest.raises(SystemExit) as exc:
        main(['--input', 'missing', '--output', str(output)])
    assert exc.value.code == 2
    with pytest.raises(FileExistsError):
        export_allocation_shadow(run(state, allocation_package), output)
    assert main(['--input', 'missing', '--output', str(tmp_path / 'off.json')]) == 0
    state.metadata.clear()
    source.write_text(state.to_json(), encoding='utf-8')
    assert main(['--input', str(source), '--output', str(tmp_path / 'refused.json'), '--model', MODEL]) == 2


@pytest.mark.parametrize('raw', ['{"x":1,"x":2}', '[]', '{"x":NaN}', '{"x":1e999}', ' '*131073],
                         ids=['duplicate', 'array', 'nan', 'overflow', 'size'])
def test_cli_strict_loader(raw, tmp_path, monkeypatch):
    forbid_loading(monkeypatch)
    path = tmp_path / 'bad.json'
    path.write_text(raw, encoding='utf-8')
    output = tmp_path / 'result.json'
    with pytest.raises(SystemExit) as exc:
        main(['--input', str(path), '--output', str(output), '--model', MODEL])
    assert exc.value.code == 2 and not output.exists()


@pytest.mark.parametrize('case', ['nan', 'oversize'])
def test_api_serialization_bounds(case, monkeypatch):
    forbid_loading(monkeypatch)
    state = snapshot()
    if case == 'nan':
        state.telemetry[TELEMETRY_KEY]['observation']['measured_qber'][0] = float('nan')
    else:
        state.metadata['oversize'] = 'x'*131073
    with pytest.raises(ValueError):
        run(state)


def test_diagnostic_exports_all_variants_without_overwrite(allocation_package, tmp_path):
    from tools.validate_allocation_shadow import main as diagnose

    output = tmp_path / 'diagnostic'
    assert diagnose(['--qnet-zip', str(allocation_package), '--output', str(output)]) == 0
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert report['passed'] and len(report['checks']) == 4
    assert {row['status'] for row in report['checks']} == {'persistence', 'ridge', 'warmup_persistence'}
    assert len(list(output.glob('*-input.json'))) == 4
    for row in report['checks']:
        path = output / f"{row['model_id']}-{row['history_samples']}.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['report_sha256']
    with pytest.raises(FileExistsError):
        diagnose(['--qnet-zip', str(allocation_package), '--output', str(output)])
