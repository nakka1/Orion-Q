"""UTC request-day boundaries, explicit application and isolated navigation."""
import os
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

from quantum_autopilot.control_plane.campaign_attempts import begin_attempt, finish_attempt, list_attempts, validate_attempt_period
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from tests import test_console_temporal_campaign as shared
from tests.test_temporal_campaign import request

app, setup, settle = shared.app, shared.setup, shared.settle


def add(store, monkeypatch, stamp, ident='period', state='OPEN'):
    import quantum_autopilot.control_plane.campaign_attempts as journal
    with monkeypatch.context() as patch:
        patch.setattr(journal, '_utc_now', lambda: stamp)
        result = begin_attempt(store, replace(request(), campaign_id=ident))
    if state != 'OPEN':
        finish_attempt(store, result, status=state, exit_code=2)
    return result


@pytest.mark.parametrize('value', [None, True, 1, [], '2026-9-01', '2026-09-1', '20260901', '2026-02-29',
    '2024-02-30', '0000-01-01', '2026-09-19T00:00:00Z', ' 2026-09-19', '2026-09-19 ', '2026-09-19\n', "' OR 1=1"])
def test_invalid_dates_rejected(value):
    with pytest.raises(ValueError):
        validate_attempt_period(value, '')
    with pytest.raises(ValueError):
        validate_attempt_period('', value)


@pytest.mark.parametrize('since,until', [('2026-09-20', '2026-09-19'), ('2026-09-19', '2026-09-19')])
def test_empty_or_reversed_period_rejected(since, until):
    with pytest.raises(ValueError, match='precede'):
        validate_attempt_period(since, until)


def test_exact_utc_boundaries_leap_day_and_open_bounds(tmp_path, monkeypatch):
    store = SimulatedTopologyStore(tmp_path / 'state')
    stamps = ['2024-02-28T23:59:59.999999+00:00', '2024-02-29T00:00:00Z',
              '2024-02-29T23:59:59.999999+00:00', '2024-03-01T00:00:00+00:00']
    ids = [add(store, monkeypatch, stamp) for stamp in stamps]
    def found(**kwargs):
        return [v['record']['attempt_id'] for v in list_attempts(store, **kwargs)[0]]
    assert found(requested_from='2024-02-29', requested_before='2024-03-01') == [ids[2], ids[1]]
    assert found(requested_before='2024-02-29') == [ids[0]]
    assert found(requested_from='2024-03-01') == [ids[3]]
    assert found() == list(reversed(ids))
    assert found(requested_from='0001-01-01') == list(reversed(ids))
    assert found(requested_before='9999-12-31') == list(reversed(ids))


def test_combined_filters_and_request_not_finish_date(tmp_path, monkeypatch):
    store = SimulatedTopologyStore(tmp_path / 'state')
    wanted = add(store, monkeypatch, '2024-02-29T12:00:00+00:00', ident='wanted-1', state='FAILED')
    add(store, monkeypatch, '2024-02-29T12:00:00+00:00', ident='other', state='FAILED')
    add(store, monkeypatch, '2024-02-29T12:00:00+00:00', ident='wanted-2')
    add(store, monkeypatch, '2024-03-01T00:00:00+00:00', ident='wanted-3', state='FAILED')
    before = store.path.read_bytes()
    rows, cursor = list_attempts(store, campaign_prefix='wanted-', status='FAILED',
                                requested_from='2024-02-29', requested_before='2024-03-01')
    assert len(rows) == 1 and rows[0]['record']['attempt_id'] == wanted and cursor is None
    assert not rows[0]['record']['finished_at_utc'].startswith('2024-02-29')
    assert store.path.read_bytes() == before


@pytest.mark.parametrize('stamp', ['2026-09-19T00:00:00-03:00', '2026-02-30T00:00:00Z', 'yesterday'])
def test_returned_noncanonical_timestamp_is_refused(tmp_path, monkeypatch, stamp):
    store = SimulatedTopologyStore(tmp_path / 'state')
    add(store, monkeypatch, stamp)
    with pytest.raises(ValueError):
        list_attempts(store)


def test_pagination_remains_in_period(tmp_path, monkeypatch):
    store = SimulatedTopologyStore(tmp_path / 'state')
    ids = [add(store, monkeypatch, '2024-02-29T12:00:00Z', ident=f'day-{i}') for i in range(23)]
    add(store, monkeypatch, '2024-03-01T00:00:00Z')
    filters = dict(requested_from='2024-02-29', requested_before='2024-03-01')
    first, cursor = list_attempts(store, **filters)
    assert len(first) == 20 and cursor is not None
    add(store, monkeypatch, '2024-02-29T14:00:00Z')
    second, after = list_attempts(store, before_sequence=cursor, **filters)
    assert [v['record']['attempt_id'] for v in second] == list(reversed(ids[:3])) and after is None


def test_ui_apply_invalid_range_reset_and_navigation(setup, app, monkeypatch):
    service, _, job, dialog, _ = setup
    for i in range(23):
        add(service.topology_store, monkeypatch, '2024-02-29T12:00:00Z', ident=f'day-{i}')
    add(service.topology_store, monkeypatch, '2024-03-01T00:00:00Z')
    job.browse_attempts()
    settle(app, lambda: not job.active)
    dialog.attempt_table.selectRow(0)
    dialog.attempt_from.setText('2024-02-29')
    dialog.attempt_before_date.setText('2024-02-29')
    assert not dialog.attempt_export.isEnabled() and not dialog.attempt_older.isEnabled()
    dialog.attempt_before_date.returnPressed.emit()
    assert not job.active and 'precede' in dialog.note.text()
    assert job.attempt_from == job.attempt_before_date == ''
    dialog.attempt_before_date.setText('2024-03-01')
    dialog.attempt_from.returnPressed.emit()
    settle(app, lambda: not job.active)
    assert len(job.attempts) == 20 and job.attempt_from == '2024-02-29'
    job.browse_attempts('older')
    settle(app, lambda: not job.active)
    assert len(job.attempts) == 3 and job.attempt_before_date == '2024-03-01'
    with pytest.raises(ValueError, match='refresh'):
        job.browse_attempts('newer', requested_from='2024-02-28')
    dialog.attempt_from.setText('2025-01-01')
    dialog.attempt_before_date.setText('')
    dialog.search_attempts()
    settle(app, lambda: not job.active)
    assert not job.attempts and not job.attempt_back and job.attempt_next is None
    dialog.attempt_from.setText('')
    dialog.search_attempts()
    settle(app, lambda: not job.active)
    assert len(job.attempts) == 20 and job.attempt_from == job.attempt_before_date == ''


def test_period_source_switch_discards_read_and_drafts(setup, app, monkeypatch, tmp_path):
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    import quantum_autopilot.console.campaign_job as adapter
    service, provider, job, dialog, _ = setup
    add(service.topology_store, monkeypatch, '2024-02-29T12:00:00Z')
    job.browse_attempts(requested_from='2024-02-29')
    settle(app, lambda: not job.active)
    entered, release = Event(), Event()
    original = adapter.list_attempts
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(adapter, 'list_attempts', slow)
    dialog.attempt_before_date.setText('draft')
    job.browse_attempts()
    settle(app, entered.is_set)
    try:
        provider.switch(lambda: MeasuredFileProvider(tmp_path / 'absent', tmp_path / 'measured'))
    finally:
        release.set()
    settle(app, lambda: not job.active)
    assert job.attempt_from == job.attempt_before_date == ''
    assert dialog.attempt_from.text() == dialog.attempt_before_date.text() == ''
    assert not job.attempts and not dialog.attempt_from.isEnabled()


def test_date_draft_during_export_chooser_is_refused(setup, app, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog
    service, _, job, dialog, _ = setup
    add(service.topology_store, monkeypatch, '2024-02-29T12:00:00Z')
    job.browse_attempts()
    settle(app, lambda: not job.active)
    dialog.attempt_table.selectRow(0)
    path = tmp_path / 'refused.json'
    def choose(*args):
        dialog.attempt_from.setText('2025-01-01')
        return str(path), 'JSON'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose)
    dialog.choose_attempt_export()
    assert not path.exists() and not job.active and 'REFUSED' in dialog.note.text()


def test_period_layout(setup, app, monkeypatch):
    from quantum_autopilot.console.theme import install
    install(app)
    service, _, job, dialog, _ = setup
    for i in range(3):
        add(service.topology_store, monkeypatch, '2024-02-29T12:00:00Z', ident=f'day-{i}')
    job.browse_attempts(requested_from='2024-02-29', requested_before='2024-03-01')
    settle(app, lambda: not job.active)
    dialog.tabs.setCurrentIndex(3)
    dialog.attempt_table.selectRow(0)
    dialog.show()
    for width, height in ((640, 500), (1080, 740)):
        dialog.resize(width, height)
        app.processEvents()
        assert dialog.attempt_table.viewport().height() > 30
        assert dialog.attempt_table.geometry().bottom() < dialog.attempt_detail.geometry().top()
        assert dialog.attempt_detail.geometry().bottom() < dialog.attempt_note.geometry().top()
        for widget in (dialog.attempt_from, dialog.attempt_before_date, dialog.attempt_export):
            dialog.attempts_scroll.ensureWidgetVisible(widget)
            app.processEvents()
            viewport = dialog.attempts_scroll.viewport()
            assert viewport.rect().contains(widget.mapTo(viewport, widget.rect().bottomRight()))
        if folder := os.environ.get('ORION_ATTEMPT_PERIOD_EVIDENCE'):
            Path(folder).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(folder) / f'period-{width}-bottom.png'))
            dialog.attempts_scroll.verticalScrollBar().setValue(0)
            app.processEvents()
            assert dialog.grab().save(str(Path(folder) / f'period-{width}-top.png'))
