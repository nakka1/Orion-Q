"""Selected-page export: all hashes checked before writing any ZIP bytes."""
import json
import os
import sqlite3
from dataclasses import replace
from pathlib import Path
from threading import Event
from zipfile import ZipFile

import pytest

from quantum_autopilot.control_plane.attempt_batch import export_attempt_page
from quantum_autopilot.control_plane.attempt_file import verify_attempt_snapshot, verify_attempt_file
from quantum_autopilot.control_plane.campaign_attempts import begin_attempt, finish_attempt, list_attempts
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore, _sha256
from tests import test_console_temporal_campaign as shared
from tests.test_temporal_campaign import request

app, setup, settle = shared.app, shared.setup, shared.settle


def populate(store, count=3):
    for i in range(count):
        ident = begin_attempt(store, replace(request(), campaign_id=f'batch-{i:03}'))
        if i % 2:
            finish_attempt(store, ident, status='FAILED_TO_START', exit_code=-1, detail='Falha <b>local</b>')
    rows, _ = list_attempts(store, limit=min(50, count))
    return rows, tuple({'attempt_id': v['record']['attempt_id'], 'record_sha256': v['record_sha256']} for v in rows)


def read_bundle(path):
    with ZipFile(path) as bundle:
        assert bundle.testzip() is None
        wrapper = json.loads(bundle.read('manifest.json'))
        manifest = wrapper['manifest']
        assert _sha256(manifest) == wrapper['manifest_sha256']
        expected = ['manifest.json'] + [member['file'] for member in manifest['members']]
        assert bundle.namelist() == expected
        records = [verify_attempt_snapshot(json.loads(bundle.read(member['file']))) for member in manifest['members']]
        for member, snapshot in zip(manifest['members'], records):
            assert member['record_sha256'] == snapshot['record_sha256']
            assert member['attempt_id'] == snapshot['record']['attempt_id']
        return wrapper, records


def test_page_roundtrip_no_overwrite_no_journal_change(tmp_path):
    store = SimulatedTopologyStore(tmp_path / 'state')
    rows, selection = populate(store)
    before = store.path.read_bytes()
    destination = tmp_path / 'página operacional.zip'
    result = export_attempt_page(store, selection, destination, campaign_prefix='batch-')
    manifest, records = read_bundle(destination)
    assert records == list(rows) and result == manifest
    assert manifest['manifest']['count'] == 3 and manifest['manifest']['page_only'] is True
    assert manifest['manifest']['schema'] == 'orion.campaign_attempt_page.v1'
    assert manifest['manifest']['physical_action_performed'] is False
    assert store.path.read_bytes() == before
    original = destination.read_bytes()
    with pytest.raises(FileExistsError):
        export_attempt_page(store, selection, destination)
    assert destination.read_bytes() == original
    # Existing JSON verifier consumes members, not the ZIP manifest.
    with ZipFile(destination) as bundle:
        single = tmp_path / 'one.json'
        single.write_bytes(bundle.read(manifest['manifest']['members'][0]['file']))
        assert verify_attempt_file(single) == records[0]
        single.write_bytes(bundle.read('manifest.json'))
        with pytest.raises(ValueError):
            verify_attempt_file(single)


@pytest.mark.parametrize('change', ['changed', 'deleted', 'corrupt'])
def test_one_bad_member_refuses_whole_page_before_creation(tmp_path, change):
    store = SimulatedTopologyStore(tmp_path / 'state')
    rows, selection = populate(store)
    ident = rows[0]['record']['attempt_id']
    if change == 'changed':
        finish_attempt(store, ident, status='CANCELLED', exit_code=3, cancelled=True)
    else:
        with sqlite3.connect(store.path) as db:
            db.execute('DELETE FROM temporal_campaign_attempts WHERE attempt_id=?' if change == 'deleted' else
                       "UPDATE temporal_campaign_attempts SET record_json='{}' WHERE attempt_id=?", (ident,))
    destination = tmp_path / 'refused.zip'
    with pytest.raises(ValueError):
        export_attempt_page(store, selection, destination)
    assert not destination.exists()


@pytest.mark.parametrize('filters', [dict(campaign_prefix='different'), dict(status='CONFIRMED'),
    dict(requested_from='9999-12-31'), dict(requested_before='0001-01-01'), dict(status='unknown'),
    dict(requested_from='bad'), dict(requested_from='2026-01-01', requested_before='2026-01-01')])
def test_filter_mismatch_or_invalid_refused(tmp_path, filters):
    store = SimulatedTopologyStore(tmp_path / 'state')
    _, selection = populate(store)
    path = tmp_path / 'refused.zip'
    with pytest.raises(ValueError):
        export_attempt_page(store, selection, path, **filters)
    assert not path.exists()


@pytest.mark.parametrize('selection', [None, [], True, [{'attempt_id': '../escape', 'record_sha256': 'a'*64}],
    [{'attempt_id': 'a'*32, 'record_sha256': 'bad'}], [{'attempt_id': 'a'*32, 'record_sha256': 'a'*64, 'extra': 1}],
    [{'attempt_id': 'a'*32, 'record_sha256': 'a'*64}]*2, [{'attempt_id': 'a'*32, 'record_sha256': 'a'*64}]*51],
    ids=['null', 'empty', 'bool', 'path', 'hash', 'unknown-field', 'duplicate', 'too-many'])
def test_selection_contract(tmp_path, selection):
    store = SimulatedTopologyStore(tmp_path / 'state')
    with pytest.raises(ValueError):
        export_attempt_page(store, selection, tmp_path / 'bad.zip')
    assert not (tmp_path / 'bad.zip').exists()


def test_upper_bound_and_single_read_statement(tmp_path, monkeypatch):
    store = SimulatedTopologyStore(tmp_path / 'state')
    rows, selection = populate(store, 50)
    statements = []
    connect = store._connect
    def traced():
        db = connect()
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(store, '_connect', traced)
    path = tmp_path / 'fifty.zip'
    export_attempt_page(store, selection, path)
    assert len([sql for sql in statements if sql.startswith('SELECT')]) == 1
    assert not any(sql.startswith(('UPDATE', 'INSERT', 'DELETE')) for sql in statements)
    assert read_bundle(path)[1] == list(rows)


def test_later_state_change_does_not_replace_read_snapshot(tmp_path, monkeypatch):
    import quantum_autopilot.control_plane.attempt_batch as adapter
    store = SimulatedTopologyStore(tmp_path / 'state')
    rows, selection = populate(store, 1)
    original = adapter.verify_attempt_snapshot
    def advance(snapshot):
        finish_attempt(store, snapshot['record']['attempt_id'], status='FAILED', exit_code=2)
        return original(snapshot)
    monkeypatch.setattr(adapter, 'verify_attempt_snapshot', advance)
    path = tmp_path / 'snapshot.zip'
    export_attempt_page(store, selection, path)
    assert read_bundle(path)[1] == list(rows)
    assert list_attempts(store)[0][0]['record']['status'] == 'FAILED'


def test_ui_exports_current_page_only_without_row_selection(setup, app, tmp_path):
    service, _, job, dialog, manifest = setup
    populate(service.topology_store, 23)
    job.load(manifest)
    original_request = job.request
    job.browse_attempts(campaign_prefix='batch-')
    settle(app, lambda: not job.active)
    assert not dialog.attempt_table.currentIndex().isValid()
    assert dialog.attempt_page_export.isEnabled() and not dialog.attempt_export.isEnabled()
    path = tmp_path / 'page.zip'
    job.export_attempt_page(path)
    assert job.active and not dialog.attempt_page_export.isEnabled()
    settle(app, lambda: not job.active)
    manifest, records = read_bundle(path)
    assert manifest['manifest']['count'] == 20 and len(records) == 20
    assert job.request is original_request and job.receipt is None and job.process is None
    assert 'PAGE EXPORTED' in job.attempt_note and job.attempt_next is not None
    dialog.attempt_from.setText('draft')
    assert not dialog.attempt_page_export.isEnabled()


def test_page_export_failure_and_capability_guard(setup, app, tmp_path, monkeypatch):
    service, provider, job, dialog, _ = setup
    with pytest.raises(ValueError, match='nonempty'):
        job.export_attempt_page(tmp_path / 'empty.zip')
    populate(service.topology_store, 1)
    job.browse_attempts()
    settle(app, lambda: not job.active)
    caps = provider.active.capabilities
    with monkeypatch.context() as patch:
        no_export = replace(caps, operations=tuple(op for op in caps.operations if op != 'export_record'))
        patch.setattr(type(provider.active), 'capabilities', property(lambda _: no_export))
        dialog.refresh()
        assert not dialog.attempt_page_export.isEnabled()
        with pytest.raises(ValueError, match='unavailable'):
            job.export_attempt_page(tmp_path / 'forbidden.zip')
    finish_attempt(service.topology_store, job.attempts[0]['record']['attempt_id'], status='FAILED', exit_code=2)
    job.export_attempt_page(tmp_path / 'changed.zip')
    settle(app, lambda: not job.active)
    assert 'PAGE EXPORT FAILED' in job.attempt_note and not (tmp_path / 'changed.zip').exists()


@pytest.mark.parametrize('change', ['draft', 'page', 'source'])
def test_file_chooser_change_refused(setup, app, tmp_path, monkeypatch, change):
    from PySide6.QtWidgets import QFileDialog
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    service, provider, job, dialog, _ = setup
    populate(service.topology_store, 23)
    job.browse_attempts()
    settle(app, lambda: not job.active)
    path = tmp_path / 'refused.zip'
    def choose(*args):
        if change == 'draft':
            dialog.attempt_filter.setText('new')
        elif change == 'page':
            job.browse_attempts('older')
            settle(app, lambda: not job.active)
        else:
            provider.switch(lambda: MeasuredFileProvider(tmp_path / 'absent', tmp_path / 'measured'))
        return str(path), 'ZIP'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose)
    dialog.choose_attempt_page_export()
    assert not path.exists() and 'REFUSED' in dialog.note.text()


def test_accepted_export_finishes_old_source_without_stale_success(setup, app, tmp_path, monkeypatch):
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    import quantum_autopilot.console.campaign_job as adapter
    service, provider, job, dialog, _ = setup
    populate(service.topology_store, 1)
    job.browse_attempts()
    settle(app, lambda: not job.active)
    entered, release = Event(), Event()
    original = adapter.export_attempt_page
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(adapter, 'export_attempt_page', slow)
    path = tmp_path / 'authorized.zip'
    job.export_attempt_page(path)
    settle(app, entered.is_set)
    try:
        with pytest.raises(ValueError):
            job.export_attempt_page(tmp_path / 'busy.zip')
        with pytest.raises(RuntimeError):
            job.shutdown()
        provider.switch(lambda: MeasuredFileProvider(tmp_path / 'absent', tmp_path / 'measured'))
    finally:
        release.set()
    settle(app, lambda: not job.active)
    assert read_bundle(path)[0]['manifest']['count'] == 1
    assert 'PAGE EXPORTED' not in job.attempt_note and not dialog.attempt_page_export.isEnabled()


def test_batch_layout(setup, app):
    from quantum_autopilot.console.theme import install
    install(app)
    service, _, job, dialog, _ = setup
    populate(service.topology_store)
    job.browse_attempts()
    settle(app, lambda: not job.active)
    dialog.tabs.setCurrentIndex(3)
    dialog.show()
    for width, height in ((640, 500), (1080, 740)):
        dialog.resize(width, height)
        app.processEvents()
        dialog.attempts_scroll.ensureWidgetVisible(dialog.attempt_page_export)
        app.processEvents()
        viewport = dialog.attempts_scroll.viewport()
        assert viewport.rect().contains(dialog.attempt_page_export.mapTo(viewport, dialog.attempt_page_export.rect().bottomRight()))
        assert dialog.attempt_table.geometry().bottom() < dialog.attempt_detail.geometry().top()
        if folder := os.environ.get('ORION_ATTEMPT_BATCH_EVIDENCE'):
            Path(folder).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(folder) / f'batch-{width}.png'))


def test_cancelled_chooser_does_not_export(setup, app, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    service, _, job, dialog, _ = setup
    populate(service.topology_store, 1)
    job.browse_attempts()
    settle(app, lambda: not job.active)
    before = job.attempt_note
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: ('', ''))
    dialog.choose_attempt_page_export()
    assert not job.active and job.attempt_note == before


def test_write_failure_is_not_success_and_existing_path_is_preserved(setup, app, tmp_path, monkeypatch):
    service, _, job, _, _ = setup
    populate(service.topology_store, 1)
    job.browse_attempts()
    settle(app, lambda: not job.active)
    path = tmp_path / 'partial.zip'
    def fail(*args, **kwargs):
        raise OSError('injected disk write failure')
    with monkeypatch.context() as patch:
        patch.setattr(ZipFile, 'writestr', fail)
        job.export_attempt_page(path)
        settle(app, lambda: not job.active)
    assert path.exists() and 'PAGE EXPORT FAILED' in job.attempt_note
    assert 'injected disk' in job.attempt_note
    partial = path.read_bytes()
    job.export_attempt_page(path)
    settle(app, lambda: not job.active)
    assert 'PAGE EXPORT FAILED' in job.attempt_note and path.read_bytes() == partial
