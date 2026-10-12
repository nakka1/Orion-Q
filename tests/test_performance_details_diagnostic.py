"""Focused details diagnosis preserves the ordinary entry and readback contract."""
import json
from types import SimpleNamespace

import pytest

from quantum_autopilot.console import performance_validation as harness


pytestmark = pytest.mark.unit


def test_details_plan_preserves_canonical_order_and_has_only_two_public_clicks():
    plan = harness._details_action_plan(('CIMATEC', 'CBPF'), ('CBPF', 'CIMATEC', 'USP'))
    assert list(plan['phases']) == ['map']
    assert [row['target'] for row in plan['phases']['map']] == ['CIMATEC', 'CBPF']
    assert all(row['action'] == 'map-node-hover-click' and row['expected_mode'] == 'map'
               for row in plan['phases']['map'])
    assert not plan['production_zoom_thresholds_modified']


@pytest.mark.parametrize('targets', [(), ('CBPF',), ('CBPF', 'CBPF'), ('', 'CBPF'),
                                     ('CIMATEC', 'BR-CBPF')])
def test_details_plan_rejects_reduced_or_unavailable_work(targets):
    with pytest.raises(ValueError):
        harness._details_action_plan(targets, ('CIMATEC', 'CBPF'))


def test_details_entry_creates_fresh_state_without_authentication_or_scientific_overrides(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(harness, '_run_ui', lambda args: seen.append(args) or 0)
    output = tmp_path / 'details'
    assert harness.run_details_diagnostic(output, repository_root=tmp_path) == 0
    args = seen[0]
    assert args.details_diagnostic == ('CIMATEC', 'CBPF')
    assert args.presentation_interval_ms == 8
    assert args.soak == 0. and args.duration == 1.
    assert not args.profile and not args.local_profile
    assert not any(key in vars(args) for key in ('password', 'principal', 'fixture', 'bypass'))
    config = json.loads((output / 'configuration.json').read_text(encoding='utf-8'))
    assert config['readback_deadline_s'] == 5. and config['workspace_entry_deadline_s'] == 90.
    assert config['authentication'] == 'current_windows_session'
    assert not config['physical_action_performed']
    with pytest.raises(ValueError, match='new isolated directory'):
        harness.run_details_diagnostic(output, repository_root=tmp_path)
    assert len(seen) == 1


def test_invalid_details_targets_create_no_diagnostic_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, '_run_ui', lambda args: pytest.fail('must not construct Qt'))
    output = tmp_path / 'details'
    with pytest.raises(ValueError):
        harness.run_details_diagnostic(output, targets=('CBPF', 'CBPF'), repository_root=tmp_path)
    assert not output.exists()


@pytest.mark.parametrize('interval', (8, 20))
def test_details_cadence_is_explicit_without_changing_acceptance_or_work_budget(tmp_path, monkeypatch, interval):
    seen = []
    monkeypatch.setattr(harness, '_run_ui', lambda args: seen.append(args) or 0)
    output = tmp_path / 'details'
    assert harness.run_details_diagnostic(output, repository_root=tmp_path, presentation_interval_ms=interval) == 0
    assert seen[0].presentation_interval_ms == interval
    config = json.loads((output / 'configuration.json').read_text(encoding='utf-8'))
    assert config['presentation_interval_ms'] == interval
    assert config['presentation_work_budget_ms'] == 6. and config['new_cards_per_turn'] == 1
    assert config['readback_deadline_s'] == 5.


@pytest.mark.parametrize('interval', (True, 0, 1, 8., 100))
def test_invalid_cadence_creates_no_state(tmp_path, interval):
    output = tmp_path / 'details'
    with pytest.raises(ValueError, match='8 or 20'):
        harness.run_details_diagnostic(output, repository_root=tmp_path, presentation_interval_ms=interval)
    assert not output.exists()


def test_node_close_diagnostic_retains_sender_identity_without_querying_backend():
    from quantum_autopilot.console.widgets.node_window import NodeWindows
    source = SimpleNamespace(_close_reason='close_button')
    recorded = []
    registry = SimpleNamespace(sender=lambda: source, windows={'CBPF': source},
                               close=lambda ident, **details: recorded.append((ident, details)))
    NodeWindows._window_closed(registry, 'CBPF')
    assert recorded == [('CBPF', {'reason': 'close_button', 'signaling_window': source})]


def test_diagnostic_metadata_omits_operator_and_arbitrary_snapshot_fields():
    from quantum_autopilot.console.widgets.node_window import NodeWindow
    records = []
    snap = SimpleNamespace(input_kind='demo', campaign='grid', capabilities=SimpleNamespace(adapter_id='demo'),
                           topology=SimpleNamespace(id='topology'), live=SimpleNamespace(tick=6),
                           operator='SECRET', password='SECRET')
    window = SimpleNamespace(_diagnostic_observer=records.append, _latest_context=(snap, None, None),
                             node_id='CBPF', _generation=2, _presentation_state='presenting',
                             _pending_widgets=[1], isVisible=lambda: True)
    NodeWindow._emit_diagnostic(window, 'close_request', reason='close_button')
    assert records[0]['context'] == {'input_kind': 'demo', 'campaign': 'grid',
                                     'adapter_id': 'demo', 'topology_id': 'topology'}
    assert records[0]['received_tick'] == 6
    assert 'SECRET' not in json.dumps(records)
    window._diagnostic_observer = lambda record: (_ for _ in ()).throw(RuntimeError('observer failed'))
    NodeWindow._emit_diagnostic(window, 'close_request')  # Observation cannot change semantics.


def test_failed_materialization_keeps_error_and_diagnostic_without_sensitive_exception_text():
    from collections import deque
    from quantum_autopilot.console.widgets.node_window import NodeWindow
    records, stopped = [], []
    window = SimpleNamespace(_pending_widgets=deque([('card', 'CBPF-USP', 0)]),
        _diagnostic_observer=records.append, _materialize_one=lambda *args: (_ for _ in ()).throw(ValueError('SECRET')),
        _legend={}, _cards={}, _link_generations={}, _section_generations={},
        _presentation_timer=SimpleNamespace(stop=lambda: stopped.append(True)),
        _rendered_generation=1, _presentation_snapshot=object(), _presentation_state='presenting',
        _update_presentation_line=lambda: None,
        _emit_diagnostic=lambda event, **details: records.append({'event': event, **details}))
    assert NodeWindow._materialize_pending(window) is False
    assert window._presentation_state == 'error' and window._presentation_snapshot is None
    assert 'ValueError' in window._presentation_error and 'SECRET' not in window._presentation_error
    assert records[0]['event'] == 'presentation_item' and records[0]['kind'] == 'card'
    assert records[0]['duration_ms'] >= 0 and stopped == [True]
