"""Acceptance waits verify received content rather than force UI completion."""
from types import SimpleNamespace

import pytest

from quantum_autopilot.console import performance_validation as content
from quantum_autopilot.console import responsive_validation as validation


def _receipt(*, ready=True, complete=True, error=''):
    return {'ready': ready, 'complete': complete,
            'checks': {'all_rendered_values_match': complete},
            'presentation': {'error': error}}


def test_wait_releases_turns_and_requires_full_values(monkeypatch):
    snapshot, model = object(), object()
    window = SimpleNamespace(presentation_snapshot=lambda: (snapshot, model))
    receipts = iter((_receipt(ready=False, complete=False),
                     _receipt(complete=False), _receipt()))
    turns = []
    monkeypatch.setattr(content, '_node_presentation_evidence', lambda *args: next(receipts))
    monkeypatch.setattr(validation, 'run_event_loop', turns.append)
    result = validation.wait_for_node_presentation(window, snapshot, model)
    assert turns == [5, 5]
    assert result['complete'] and all(result['checks'].values())
    assert result['wait_elapsed_ms'] <= result['content_deadline_ms'] == 5000


def test_old_ready_context_is_not_accepted(monkeypatch):
    target = (object(), object())
    presented = [(object(), object())]
    window = SimpleNamespace(presentation_snapshot=lambda: presented[0])
    monkeypatch.setattr(content, '_node_presentation_evidence', lambda *args: _receipt())
    turns = []
    def turn(ms):
        turns.append(ms)
        presented[0] = target
    monkeypatch.setattr(validation, 'run_event_loop', turn)
    result = validation.wait_for_node_presentation(window, *target)
    assert turns == [5]
    assert result['checks']['requested_context_presented'] is True


def test_context_getters_follow_an_asynchronous_received_selection(monkeypatch):
    current = [(object(), object())]
    presented = [current[0]]
    calls = []
    window = SimpleNamespace(presentation_snapshot=lambda: presented[0])
    def check(opened, snapshot, model):
        calls.append((snapshot, model))
        return _receipt(ready=len(calls) > 1, complete=len(calls) > 1)
    monkeypatch.setattr(content, '_node_presentation_evidence', check)
    def turn(ms):
        current[0] = (object(), object())
        presented[0] = current[0]
    monkeypatch.setattr(validation, 'run_event_loop', turn)
    result = validation.wait_for_node_presentation(window, lambda: current[0][0], lambda: current[0][1])
    assert calls[0] != calls[1] == current[0]
    assert result['complete']


def test_failed_materialization_cannot_pass_or_log_its_error_text(monkeypatch):
    monkeypatch.setattr(content, '_node_presentation_evidence',
                        lambda *args: _receipt(complete=False, error='untrusted-secret'))
    with pytest.raises(RuntimeError, match='node presentation reported an error') as caught:
        validation.wait_for_node_presentation(object(), object(), object())
    assert 'untrusted-secret' not in str(caught.value)


def test_content_after_deadline_is_rejected_even_when_ready(monkeypatch):
    target = (object(), object())
    window = SimpleNamespace(presentation_snapshot=lambda: target)
    clock = iter((0., 5.001))
    monkeypatch.setattr(validation, 'perf_counter', lambda: next(clock))
    monkeypatch.setattr(content, '_node_presentation_evidence', lambda *args: _receipt())
    with pytest.raises(TimeoutError, match='content deadline'):
        validation.wait_for_node_presentation(window, *target)


def test_completion_flag_does_not_override_a_failed_field_check(monkeypatch):
    target = (object(), object())
    window = SimpleNamespace(presentation_snapshot=lambda: target)
    clock = iter((0., 0., 5.001))
    monkeypatch.setattr(validation, 'perf_counter', lambda: next(clock))
    monkeypatch.setattr(validation, 'run_event_loop', lambda ms: None)
    monkeypatch.setattr(content, '_node_presentation_evidence', lambda *args: {
        'ready': True, 'complete': True, 'checks': {'missing_protocol_field': False},
        'presentation': {'error': ''}})
    with pytest.raises(TimeoutError, match='content deadline'):
        validation.wait_for_node_presentation(window, *target)


@pytest.mark.parametrize('limit', (True, 0, -1, 5001, 5., float('inf'), float('nan')))
def test_invalid_deadline_is_refused_before_any_window_access(limit):
    with pytest.raises(ValueError, match='within 1..5000'):
        validation.wait_for_node_presentation(object(), object(), object(), timeout_ms=limit)


def test_missing_window_is_failure():
    with pytest.raises(ValueError, match='not opened'):
        validation.wait_for_node_presentation(None, object(), object())


@pytest.mark.parametrize('changes,other_field,accepted', [
    ({}, True, True),
    ({'source_kind': 'measured'}, True, False),
    ({'presented_tick': 7}, True, False),
    ({'core_tick': 7}, True, False),
    ({'latest_tick': 7}, True, False),
    ({'network_time_s': 7.}, True, False),
    ({}, False, False),
])
def test_snapshot_without_live_requires_explicit_source_and_absent_clocks(
        monkeypatch, changes, other_field, accepted):
    snapshot = SimpleNamespace(live=None, input_kind='synthetic')
    model = object()
    window = SimpleNamespace(presentation_snapshot=lambda: (snapshot, model))

    def checked(*args):
        return {'ready': True, 'complete': False, 'mode': 'asynchronous-contract',
                'source_kind': None, 'presented_tick': None, 'network_time_s': None,
                'widget_content_sha256': 'a' * 64,
                'checks': {'identity_widget_values': other_field, 'presentation_timestamp': False},
                'presentation': {'source_kind': 'synthetic', 'core_tick': None,
                                 'latest_tick': None, 'presented_tick': None,
                                 'network_time_s': None, 'error': '', **changes}}

    monkeypatch.setattr(content, '_node_presentation_evidence', checked)
    monkeypatch.setattr(validation, 'run_event_loop', lambda ms: None)
    times = iter((0., 0., 5.001))
    monkeypatch.setattr(validation, 'perf_counter', lambda: next(times))
    if accepted:
        receipt = validation.wait_for_node_presentation(window, snapshot, model)
        assert receipt['source_kind'] == 'synthetic'
        assert receipt['presented_tick'] is None and receipt['network_time_s'] is None
        assert receipt['complete'] and all(receipt['checks'].values())
    else:
        with pytest.raises(TimeoutError, match='content deadline'):
            validation.wait_for_node_presentation(window, snapshot, model)
