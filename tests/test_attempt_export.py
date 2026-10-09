"""Selected operational snapshots, not scientific receipts or a moving target."""
import json
import os
import sqlite3
from pathlib import Path
from threading import Event
from dataclasses import replace

import pytest

from quantum_autopilot.control_plane.campaign_attempts import begin_attempt, finish_attempt, read_attempt, export_attempt_snapshot
from quantum_autopilot.control_plane.temporal_campaign import read_temporal_campaign
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from tests import test_console_temporal_campaign as shared
from tests.test_temporal_campaign import request

app, setup, settle = shared.app, shared.setup, shared.settle


def test_open_snapshot_roundtrip_and_no_overwrite(tmp_path):
    store = SimulatedTopologyStore(tmp_path / 'state')
    attempt = begin_attempt(store, request())
    selected = read_attempt(store, attempt)
    path = tmp_path / 'tentativa com espaços.json'
    result = export_attempt_snapshot(store, attempt, selected['record_sha256'], path)
    assert json.loads(path.read_text(encoding='utf-8')) == selected == result
    assert result['record']['schema'] == 'orion.campaign_attempt.v1'
    assert result['record']['status'] == 'OPEN'
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        export_attempt_snapshot(store, attempt, selected['record_sha256'], path)
    assert path.read_bytes() == original and read_attempt(store, attempt) == selected
    assert read_temporal_campaign(store) is None


@pytest.mark.parametrize('change', ['completed', 'deleted', 'tampered'])
def test_changed_selection_refused_before_file_creation(tmp_path, change):
    store = SimulatedTopologyStore(tmp_path / 'state')
    attempt = begin_attempt(store, request())
    selected = read_attempt(store, attempt)
    if change == 'completed':
        finish_attempt(store, attempt, status='CANCELLED', exit_code=3, cancelled=True)
    else:
        with sqlite3.connect(store.path) as db:
            db.execute('DELETE FROM temporal_campaign_attempts' if change == 'deleted' else
                       "UPDATE temporal_campaign_attempts SET record_json='{}'")
    path = tmp_path / 'refused.json'
    with pytest.raises(ValueError):
        export_attempt_snapshot(store, attempt, selected['record_sha256'], path)
    assert not path.exists()


@pytest.mark.parametrize('attempt_id,sha', [('invalid', 'a'*64), ('a'*32, 'invalid'), (True, 'a'*64)])
def test_invalid_identity(tmp_path, attempt_id, sha):
    store = SimulatedTopologyStore(tmp_path / 'state')
    with pytest.raises(ValueError):
        export_attempt_snapshot(store, attempt_id, sha, tmp_path / 'invalid.json')
    assert not (tmp_path / 'invalid.json').exists()


def test_operational_export_requires_selection_not_scientific_receipt(setup, app, tmp_path):
    service, _, job, dialog, _ = setup
    attempt = begin_attempt(service.topology_store, request())
    finish_attempt(service.topology_store, attempt, status='FAILED_TO_START', exit_code=-1)
    assert not dialog.attempt_export.isEnabled()
    job.browse_attempts()
    settle(app, lambda: not job.active)
    assert not dialog.attempt_export.isEnabled()
    dialog.attempt_table.selectRow(0)
    assert dialog.attempt_export.isEnabled() and not dialog.export_button.isEnabled()
    path = tmp_path / 'operational.json'
    job.export_attempt(0, path)
    assert job.active and not dialog.attempt_export.isEnabled()
    settle(app, lambda: not job.active)
    assert json.loads(path.read_text())['record']['status'] == 'FAILED_TO_START'
    assert 'ATTEMPT EXPORTED' in job.attempt_note
    assert job.receipt is None and job.request is None and not job.result_rows
    job.export_attempt(0, path)
    settle(app, lambda: not job.active)
    assert 'ATTEMPT EXPORT FAILED' in job.attempt_note


def test_export_rechecks_database_after_ui_selection(setup, app, tmp_path):
    service, _, job, _, _ = setup
    attempt = begin_attempt(service.topology_store, request())
    job.browse_attempts()
    settle(app, lambda: not job.active)
    finish_attempt(service.topology_store, attempt, status='CANCELLED', exit_code=3, cancelled=True)
    path = tmp_path / 'changed.json'
    job.export_attempt(0, path)
    settle(app, lambda: not job.active)
    assert 'changed or disappeared' in job.attempt_note and not path.exists()


def test_export_requires_export_capability_in_addition_to_history(setup, app, tmp_path, monkeypatch):
    service, provider, job, dialog, _ = setup
    begin_attempt(service.topology_store, request())
    job.browse_attempts()
    settle(app, lambda: not job.active)
    caps = provider.active.capabilities
    no_export = replace(caps, operations=tuple(op for op in caps.operations if op != 'export_record'))
    monkeypatch.setattr(type(provider.active), 'capabilities', property(lambda _: no_export))
    dialog.attempt_table.selectRow(0)
    dialog.refresh()
    assert job.history_allowed and not dialog.attempt_export.isEnabled()
    with pytest.raises(ValueError, match='unavailable'):
        job.export_attempt(0, tmp_path / 'forbidden.json')
    assert not (tmp_path / 'forbidden.json').exists()


def test_source_change_during_file_dialog_refuses_selection(setup, app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    service, provider, job, dialog, _ = setup
    begin_attempt(service.topology_store, request())
    job.browse_attempts()
    settle(app, lambda: not job.active)
    dialog.attempt_table.selectRow(0)
    path = tmp_path / 'wrong-source.json'
    def choose(*args):
        provider.switch(lambda: MeasuredFileProvider(tmp_path / 'absent', tmp_path / 'measured'))
        return str(path), 'JSON'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose)
    dialog.choose_attempt_export()
    assert 'changed while choosing' in dialog.note.text()
    assert not path.exists() and not job.active


def test_accepted_export_finishes_on_original_source_without_stale_ui(setup, app, tmp_path, monkeypatch):
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    import quantum_autopilot.console.campaign_job as adapter
    service, provider, job, dialog, _ = setup
    attempt = begin_attempt(service.topology_store, request())
    job.browse_attempts()
    settle(app, lambda: not job.active)
    entered, release = Event(), Event()
    original = adapter.export_attempt_snapshot
    def slow(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(adapter, 'export_attempt_snapshot', slow)
    path = tmp_path / 'authorized-snapshot.json'
    job.export_attempt(0, path)
    settle(app, entered.is_set)
    try:
        provider.switch(lambda: MeasuredFileProvider(tmp_path / 'absent', tmp_path / 'measured'))
    finally:
        release.set()
    settle(app, lambda: not job.active)
    assert json.loads(path.read_text())['record']['attempt_id'] == attempt
    assert not job.attempts and not dialog.attempt_export.isEnabled()
    assert 'EXPORTED' not in job.attempt_note
    with pytest.raises(ValueError):
        job.export_attempt(0, tmp_path / 'not-authorized.json')


def test_attempt_export_layout(setup, app, tmp_path):
    from quantum_autopilot.console.theme import install
    install(app)
    service, _, job, dialog, _ = setup
    begin_attempt(service.topology_store, request())
    job.browse_attempts()
    settle(app, lambda: not job.active)
    dialog.attempt_table.selectRow(0)
    job.export_attempt(0, tmp_path / 'snapshot.json')
    settle(app, lambda: not job.active)
    dialog.tabs.setCurrentIndex(3)
    dialog.show()
    for width, height in ((640, 500), (1080, 740)):
        dialog.resize(width, height)
        app.processEvents()
        assert dialog.attempt_table.viewport().height() > 30
        dialog.attempts_scroll.ensureWidgetVisible(dialog.attempt_export)
        app.processEvents()
        assert dialog.rect().contains(dialog.attempt_export.mapTo(dialog, dialog.attempt_export.rect().bottomRight()))
        if folder := os.environ.get('ORION_ATTEMPT_EXPORT_EVIDENCE'):
            Path(folder).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(folder) / f'export-{width}.png'))
