"""QKD shadow context, time and isolation checks with pinned synthetic weights."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import json

import pytest

from quantum_autopilot.control_plane.optional_keyrate import (
    MODEL_ID, TELEMETRY_KEY, KeyRateShadowWindow, evaluate_optional_keyrate, export_keyrate_shadow, main,
)
from quantum_autopilot.control_plane.optional_sync import _hash, _json
from quantum_autopilot.control_plane.runtime import ControllerRuntime
from quantum_autopilot.model_intake import catalog
from quantum_autopilot.model_intake.keyrate import BOX, _settings, TransmitterSettings
from quantum_autopilot.model_intake.keyrate_session import evaluate_keyrate_session
from tests import test_optional_keyrate_session as fixtures
from tools.validate_keyrate_shadow import CASES, demo_snapshot

actor_archive = fixtures.actor_archive
pytestmark = pytest.mark.unit


def run(state, path=None):
    return evaluate_optional_keyrate(state, model_id=MODEL_ID, archive_path=path)


def bind_context(state):
    state.metadata['keyrate_context_sha256'] = _hash(_json(state.telemetry[TELEMETRY_KEY]['context']))


def forbid_loading(monkeypatch):
    monkeypatch.setattr(catalog, 'open_optional_model', lambda *a, **k: pytest.fail('weights opened'))


def test_off_missing_wrong_model(monkeypatch):
    forbid_loading(monkeypatch)
    assert evaluate_optional_keyrate(object(), archive_path='absent').status == 'off'
    state = replace(demo_snapshot(), telemetry={'qber': .01, 'optional_allocation_window': {}, 'optional_sync_window': {}})
    assert run(state).reason == 'missing_keyrate_window'
    assert run(demo_snapshot()).reason == 'missing_archive'
    with pytest.raises(ValueError):
        evaluate_optional_keyrate(demo_snapshot(), model_id='qnet.routing.nominal')
    with pytest.raises(ValueError):
        run({})


@pytest.mark.parametrize('case', CASES)
@pytest.mark.parametrize('seed', [0, 7, 1234, 2**32 - 1])
def test_offline_parity_bounds_and_baseline(actor_archive, case, seed):
    state = demo_snapshot(case, seed)
    before = state.to_json()
    result = run(state, actor_archive)
    assert result.status == 'evaluated', result.detail
    evaluation = json.loads(result.evaluation_json)
    offline = json.loads(evaluate_keyrate_session(actor_archive, evaluation['input_json']).serialized())
    compare = dict(evaluation)
    del compare['evaluated_at_utc']
    del offline['evaluated_at_utc']
    assert compare == offline
    assert evaluation['baseline_settings'] == state.telemetry[TELEMETRY_KEY]['context']['settings']
    assert evaluation['candidates'][0]['proposed_settings'] == evaluation['baseline_settings']
    assert len(evaluation['candidates']) == 9
    for candidate in evaluation['candidates']:
        _settings(TransmitterSettings(**candidate['proposed_settings']))
    assert evaluation['recommendation']['proposed_settings'] == evaluation['candidates'][evaluation['recommendation']['sampled_index']]['proposed_settings']
    if seed == 1234:
        assert evaluation['recommendation']['sampled_index'] != evaluation['recommendation']['greedy_index']
    if case == 'zero_rate':
        assert evaluation['recommendation']['observation'][7] != evaluation['recommendation']['observation'][13]
    if case == 'upper_clock':
        assert evaluation['candidates'][5]['proposed_settings']['clock_rate_hz'] == BOX['clock_range'][1]
    assert result.observation_source == ('calibration_bootstrap' if case == 'bootstrap' else 'previous_period')
    assert result.previous_age_s == (None if case == 'bootstrap' else 0.)
    assert state.to_json() == before
    assert result.snapshot_sha256 == hashlib.sha256(result.snapshot_json.encode()).hexdigest()
    assert not any((result.settings_mutated, result.policy_activated, result.physical_action_performed, result.simulator_action_performed))
    state.telemetry.clear()
    assert TELEMETRY_KEY in json.loads(result.snapshot_json)['telemetry']
    with pytest.raises(FrozenInstanceError):
        result.status = 'off'


BAD_WINDOW = [('schema', 'bad'), ('evidence', 'measured'), ('units', {}), ('extra', None),
              ('source_id', 'other'), ('campaign_id', 'other'), ('stream_id', 'other'), ('clock_id', 'other'),
              ('node_id', 'other'), ('seed', True), ('seed', -1), ('seed', 2**32), ('context', []),
              ('previous', None), ('observation_mode', 'auto'), ('observation_mode', 'calibration_bootstrap'),
              ('origin_timestamp_utc', 'bad'), ('decision_timestamp_utc', '2026-09-24')]


@pytest.mark.parametrize('key,value', BAD_WINDOW)
def test_bad_window_before_weights(key, value, monkeypatch):
    forbid_loading(monkeypatch)
    state = demo_snapshot()
    state.telemetry[TELEMETRY_KEY][key] = value
    assert run(state, 'unused').reason == 'incompatible_keyrate_window'


BAD_CONTEXT = [('link_id', ''), ('transmitter_id', 'other'), ('receiver_id', 'fixture-tx'),
               ('protocol', 'single_photon_bb84'), ('link_span_km', 24.99), ('link_span_km', 80.01),
               ('link_span_km', True), ('settings_version', True), ('settings_version', -1),
               ('settings_version', 2), ('settings_version', 2**63), ('calibration_id', ''),
               ('settings_timestamp_utc', '2026-09-24T00:00:00.001+00:00'),
               ('calibration_timestamp_utc', '2026-09-24T00:00:00.001+00:00'),
               ('calibration_valid_until_utc', '2026-09-24T00:00:01+00:00'), ('settings', {}),
               ('calibration', {}), ('driver', 'hardware')]


@pytest.mark.parametrize('key,value', BAD_CONTEXT)
def test_bad_context_before_weights(key, value, monkeypatch):
    forbid_loading(monkeypatch)
    state = demo_snapshot()
    state.telemetry[TELEMETRY_KEY]['context'][key] = value
    bind_context(state)
    assert run(state, 'unused').status == 'refused'


BAD_NUMERIC = [('settings', 'signal_intensity', True), ('settings', 'signal_intensity', 1.01),
               ('settings', 'decoy_intensity', .4), ('settings', 'clock_rate_hz', 5e9 + 1),
               ('settings', 'signal_probability', .9), ('settings', 'vacuum_probability', .5),
               ('settings', 'extra', 0), ('calibration', 'dead_time_s', 1e-9),
               ('calibration', 'calibration_gain', 0), ('calibration', 'calibration_rate_hz', 0),
               ('calibration', 'dark_count_probability', 0), ('calibration', 'misalignment_error', 2),
               ('previous', 'signal_gain', 1.1), ('previous', 'decoy_gain', -1),
               ('previous', 'vacuum_gain', True), ('previous', 'signal_error', 2),
               ('previous', 'single_photon_yield_lower', -1), ('previous', 'single_photon_error_upper', 2),
               ('previous', 'key_rate_hz', -1), ('previous', 'extra', 0)]


@pytest.mark.parametrize('section,key,value', BAD_NUMERIC)
def test_numeric_refusal_before_weights(section, key, value, monkeypatch):
    forbid_loading(monkeypatch)
    state = demo_snapshot()
    w = state.telemetry[TELEMETRY_KEY]
    target = w['previous']['observables'] if section == 'previous' else w['context'][section]
    target[key] = value
    w['previous']['settings_sha256'] = _hash(_json(w['context']['settings']))
    bind_context(state)
    assert run(state, 'unused').status == 'refused'


@pytest.mark.parametrize('case', ['future_end', 'empty', 'before_origin', 'stale', 'settings_hash', 'period_extra',
                                 'measured', 'integrity', 'ood', 'metadata_hash', 'metadata_origin', 'metadata_stream',
                                 'snapshot_time', 'snapshot_version', 'bootstrap_late', 'bootstrap_previous'])
def test_causal_and_snapshot_refusal(case, monkeypatch):
    forbid_loading(monkeypatch)
    state = demo_snapshot('bootstrap' if case.startswith('bootstrap') else 'previous')
    w = state.telemetry[TELEMETRY_KEY]
    previous = w['previous']
    if case == 'future_end':
        previous['end_timestamp_utc'] = '2026-09-24T00:00:02+00:00'
    elif case == 'empty':
        previous['start_timestamp_utc'] = previous['end_timestamp_utc']
    elif case == 'before_origin':
        previous['start_timestamp_utc'] = '2026-09-23T23:59:59+00:00'
    elif case == 'stale':
        w['decision_timestamp_utc'] = '2026-09-24T00:00:02.000001+00:00'
        state = replace(state, timestamp_utc=w['decision_timestamp_utc'])
    elif case == 'settings_hash':
        previous['settings_sha256'] = 'a'*64
    elif case == 'period_extra':
        previous['future_rate'] = 100
    elif case == 'measured':
        state.provenance.evidence_kind = 'measured'
    elif case == 'integrity':
        state = replace(state, integrity_verified=False)
    elif case == 'ood':
        state = replace(state, out_of_distribution=True)
    elif case.startswith('metadata'):
        state.metadata[{'metadata_hash': 'keyrate_context_sha256', 'metadata_origin': 'keyrate_origin_timestamp_utc',
                        'metadata_stream': 'keyrate_stream_id'}[case]] = 'other'
    elif case == 'snapshot_time':
        state = replace(state, timestamp_utc='2026-09-24T00:00:02+00:00')
    elif case == 'snapshot_version':
        state = replace(state, state_version=2)
    elif case == 'bootstrap_late':
        w['decision_timestamp_utc'] = '2026-09-24T00:00:01+00:00'
    elif case == 'bootstrap_previous':
        w['previous'] = demo_snapshot().telemetry[TELEMETRY_KEY]['previous']
    assert run(state, 'unused').status == 'refused'


def test_age_boundary_and_detached_window(actor_archive):
    state = demo_snapshot()
    state.telemetry[TELEMETRY_KEY]['decision_timestamp_utc'] = '2026-09-24T00:00:02+00:00'
    state = replace(state, timestamp_utc='2026-09-24T00:00:02+00:00')
    assert run(state, actor_archive).previous_age_s == 1.
    window = KeyRateShadowWindow.from_dict(state.telemetry[TELEMETRY_KEY])
    copy = window.to_dict()
    copy['context']['settings'].clear()
    assert window.to_dict()['context']['settings']


def test_failure_and_size_limits(tmp_path):
    assert run(demo_snapshot(), tmp_path/'missing.zip').reason == 'model_evaluation_failed'
    path = tmp_path/'bad.zip'
    path.write_bytes(b'changed')
    assert run(demo_snapshot(), path).reason == 'model_evaluation_failed'
    state = demo_snapshot()
    state.metadata['huge'] = 'x'*131073
    with pytest.raises(ValueError, match='128 KiB'):
        run(state)


def test_no_control_or_journal_mutation(actor_archive, tmp_path, monkeypatch):
    runtime = ControllerRuntime(tmp_path/'commands.sqlite')
    before = (tmp_path/'commands.sqlite').read_bytes()
    for obj, method in [(runtime, 'cycle'), (runtime.policy, 'decide'), (runtime.journal, 'append'),
                        (runtime.simulated_executor, 'execute'), (runtime.physical_executor, 'execute'),
                        (runtime.dry_run_executor, 'execute')]:
        monkeypatch.setattr(obj, method, lambda *a, **k: pytest.fail('control invoked'))
    assert runtime.observe_optional_keyrate(object()).status == 'off'
    assert runtime.observe_optional_keyrate(demo_snapshot(), model_id=MODEL_ID, archive_path=actor_archive).status == 'evaluated'
    assert (tmp_path/'commands.sqlite').read_bytes() == before


def test_cli_and_exclusive_export(actor_archive, tmp_path):
    source, output = tmp_path/'input.json', tmp_path/'output.json'
    source.write_text(demo_snapshot().to_json(), encoding='utf-8')
    assert main(['--input', str(source), '--output', str(output), '--model', MODEL_ID, '--qkd-zip', str(actor_archive)]) == 0
    before = output.read_bytes()
    with pytest.raises(SystemExit) as exc:
        main(['--input', 'missing', '--output', str(output)])
    assert exc.value.code == 2 and output.read_bytes() == before
    with pytest.raises(FileExistsError):
        export_keyrate_shadow(evaluate_optional_keyrate(None), output)
    assert main(['--input', 'missing', '--output', str(tmp_path/'off.json')]) == 0
    assert main(['--input', str(source), '--output', str(tmp_path/'unavailable.json'), '--model', MODEL_ID]) == 2


@pytest.mark.parametrize('raw', ['{"x":1,"x":2}', '[]', '{"x":NaN}', '{"x":1e999}', ' '*131073],
                         ids=['duplicate', 'array', 'nan', 'overflow', 'size'])
def test_cli_strict_loader(raw, tmp_path, monkeypatch):
    forbid_loading(monkeypatch)
    source = tmp_path/'input.json'
    source.write_text(raw, encoding='utf-8')
    output = tmp_path/'output.json'
    with pytest.raises(SystemExit) as exc:
        main(['--input', str(source), '--output', str(output), '--model', MODEL_ID])
    assert exc.value.code == 2 and not output.exists()
