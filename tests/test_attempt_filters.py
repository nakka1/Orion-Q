"""Literal operational filtering, keyset navigation and explicit UI application."""
import json
import os
import sqlite3
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

from quantum_autopilot.control_plane.campaign_attempts import begin_attempt, finish_attempt, list_attempts
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from tests import test_console_temporal_campaign as shared
from tests.test_temporal_campaign import request

app, setup, settle = shared.app, shared.setup, shared.settle


def populate(store, count=23):
    ids = []
    for i in range(count):
        attempt = begin_attempt(store, replace(request(), campaign_id=f'filter-{i:03}'))
        finish_attempt(store, attempt, status='CANCELLED', exit_code=3, cancelled=True)
        ids.append(attempt)
        begin_attempt(store, replace(request(), campaign_id=f'filter-open-{i:03}'))
    begin_attempt(store, replace(request(), campaign_id='unrelated'))
    return ids


@pytest.mark.parametrize('kwargs', [dict(status=None), dict(status=True), dict(status=[]), dict(status='cancelled'),
    dict(status="' OR 1=1 --"), dict(campaign_prefix=None), dict(campaign_prefix=True), dict(campaign_prefix=[]),
    dict(campaign_prefix='x'*129), dict(campaign_prefix='x\n'), dict(campaign_prefix='\x00')])
def test_invalid_filters(tmp_path, kwargs):
    with pytest.raises(ValueError):
        list_attempts(SimulatedTopologyStore(tmp_path / 'state'), **kwargs)


def test_combined_literal_filters_and_pagination(tmp_path):
    store = SimulatedTopologyStore(tmp_path / 'state')
    ids = populate(store)
    rows, cursor = list_attempts(store, campaign_prefix='filter-', status='CANCELLED')
    assert len(rows) == 20 and cursor is not None
    assert [v['record']['attempt_id'] for v in rows] == list(reversed(ids[3:]))
    new = begin_attempt(store, replace(request(), campaign_id='filter-new'))
    finish_attempt(store, new, status='CANCELLED', exit_code=3)
    tail, after = list_attempts(store, before_sequence=cursor, campaign_prefix='filter-', status='CANCELLED')
    assert [v['record']['attempt_id'] for v in tail] == list(reversed(ids[:3])) and after is None
    for prefix in ('Filter-', '%', '_', "' OR 1=1 --", ' filter-'):
        assert list_attempts(store, campaign_prefix=prefix) == ((), None)
    assert list_attempts(store, campaign_prefix='filter-022', status='OPEN') == ((), None)
    assert len(list_attempts(store, campaign_prefix='filter-022', status='CANCELLED')[0]) == 1


@pytest.mark.parametrize('corruption', ['hash', 'malformed'])
def test_matching_corruption_refuses_page(tmp_path, corruption):
    store = SimulatedTopologyStore(tmp_path / 'state')
    attempt = populate(store, 1)[0]
    with sqlite3.connect(store.path) as db:
        if corruption == 'hash':
            db.execute("UPDATE temporal_campaign_attempts SET record_sha256=? WHERE attempt_id=?", ('a'*64, attempt))
        else:
            db.execute("UPDATE temporal_campaign_attempts SET record_json='{' WHERE attempt_id=?", (attempt,))
    with pytest.raises(ValueError):
        list_attempts(store, campaign_prefix='filter-', status='CANCELLED')


def test_filtered_query_is_not_a_whole_database_integrity_audit(tmp_path):
    store = SimulatedTopologyStore(tmp_path / 'state')
    populate(store, 1)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE temporal_campaign_attempts SET record_sha256=? WHERE json_extract(record_json, '$.campaign_id')='unrelated'", ('a'*64,))
    assert len(list_attempts(store, status='CANCELLED')[0]) == 1
    with pytest.raises(ValueError):
        list_attempts(store)


def test_explicit_apply_navigation_reset_and_export_guard(setup, app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    service, _, job, dialog, _ = setup
    populate(service.topology_store)
    job.browse_attempts()
    settle(app, lambda: not job.active)
    dialog.attempt_table.selectRow(0)
    assert dialog.attempt_export.isEnabled()
    dialog.attempt_filter.setText('filter-')
    dialog.attempt_status.setCurrentIndex(dialog.attempt_status.findData('CANCELLED'))
    assert job.attempt_prefix == job.attempt_status == '' and not job.active
    assert 'FILTERS NOT APPLIED' in dialog.attempt_note.text()
    assert not dialog.attempt_older.isEnabled() and not dialog.attempt_export.isEnabled()
    dialog.attempt_filter.returnPressed.emit()
    settle(app, lambda: not job.active)
    assert job.attempt_prefix == 'filter-' and job.attempt_status == 'CANCELLED' and len(job.attempts) == 20
    job.browse_attempts('older')
    settle(app, lambda: not job.active)
    assert len(job.attempts) == 3 and job.attempt_back
    with pytest.raises(ValueError, match='refresh'):
        job.browse_attempts('newer', status='OPEN')
    dialog.attempt_table.selectRow(0)
    target = tmp_path / 'filtered.json'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(target), 'JSON'))
    dialog.choose_attempt_export()
    settle(app, lambda: not job.active)
    assert json.loads(target.read_text())['record']['status'] == 'CANCELLED'
    dialog.attempt_filter.setText('none')
    dialog.attempt_refresh.clicked.emit()
    settle(app, lambda: not job.active)
    assert not job.attempts and not job.attempt_back and job.attempt_next is None
    assert not dialog.attempt_export.isEnabled()
    dialog.attempt_filter.setText('')
    dialog.attempt_status.setCurrentIndex(0)
    dialog.search_attempts()
    settle(app, lambda: not job.active)
    assert len(job.attempts) == 20 and job.attempt_prefix == job.attempt_status == ''


def test_state_changes_are_observations_not_frozen_search_results(setup, app):
    service, _, job, _, _ = setup
    attempt = begin_attempt(service.topology_store, request())
    job.browse_attempts(status='OPEN')
    settle(app, lambda: not job.active)
    assert len(job.attempts) == 1
    finish_attempt(service.topology_store, attempt, status='FAILED', exit_code=2)
    job.browse_attempts()
    settle(app, lambda: not job.active)
    assert not job.attempts and job.attempt_status == 'OPEN'
    assert job.receipt is None and job.process is None


def test_source_switch_clears_draft_applied_and_late_query(setup, app, tmp_path, monkeypatch):
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    import quantum_autopilot.console.campaign_job as adapter
    service, provider, job, dialog, _ = setup
    populate(service.topology_store, 1)
    job.browse_attempts(status='CANCELLED')
    settle(app, lambda: not job.active)
    dialog.attempt_filter.setText('unsent')
    entered, release = Event(), Event()
    original = adapter.list_attempts
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(adapter, 'list_attempts', slow)
    job.browse_attempts()
    settle(app, entered.is_set)
    try:
        provider.switch(lambda: MeasuredFileProvider(tmp_path / 'absent', tmp_path / 'measured'))
    finally:
        release.set()
    settle(app, lambda: not job.active)
    assert job.attempt_prefix == job.attempt_status == dialog.attempt_filter.text() == ''
    assert dialog.attempt_status.currentData() == '' and not job.attempts
    assert not dialog.attempt_filter.isEnabled() and not dialog.attempt_export.isEnabled()


def test_filter_change_in_export_chooser_refused(setup, app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    service, _, job, dialog, _ = setup
    populate(service.topology_store, 1)
    job.browse_attempts()
    settle(app, lambda: not job.active)
    dialog.attempt_table.selectRow(0)
    path = tmp_path / 'refused.json'
    def choose(*args):
        dialog.attempt_filter.setText('changed')
        return str(path), 'JSON'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose)
    dialog.choose_attempt_export()
    assert not path.exists() and 'REFUSED' in dialog.note.text()


def test_filtered_layout(setup, app):
    from quantum_autopilot.console.theme import install
    install(app)
    service, _, job, dialog, _ = setup
    populate(service.topology_store, 3)
    job.browse_attempts(campaign_prefix='filter-', status='CANCELLED')
    settle(app, lambda: not job.active)
    dialog.tabs.setCurrentIndex(3)
    dialog.attempt_table.selectRow(0)
    dialog.show()
    for width, height in ((640, 500), (1080, 740)):
        dialog.resize(width, height)
        app.processEvents()
        assert dialog.attempt_table.viewport().height() > 30
        for button in (dialog.attempt_status, dialog.attempt_export):
            dialog.attempts_scroll.ensureWidgetVisible(button)
            app.processEvents()
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        if folder := os.environ.get('ORION_ATTEMPT_FILTER_EVIDENCE'):
            Path(folder).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(folder) / f'filters-{width}.png'))
