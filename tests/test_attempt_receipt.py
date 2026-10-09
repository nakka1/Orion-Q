"""A dispatch may refer to a receipt; it is never itself a scientific sample."""
import json
import os
import sqlite3
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

from quantum_autopilot.control_plane.attempt_receipt import read_attempt_receipt
from quantum_autopilot.control_plane.campaign_attempts import begin_attempt, finish_attempt, read_attempt, TERMINAL
from quantum_autopilot.control_plane.temporal_campaign import run_temporal_campaign
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore, _sha256
from tests import test_console_temporal_campaign as shared
from tests.test_temporal_campaign import request

app, setup, settle = shared.app, shared.setup, shared.settle


def populate(store, status='CONFIRMED'):
    req = request(max_attempts=1)
    receipt = run_temporal_campaign(store, req)
    ident = begin_attempt(store, req)
    if status != 'OPEN':
        finish_attempt(store, ident, status=status, exit_code=0,
                       receipt_sha256=receipt['record_sha256'] if status in {'CONFIRMED', 'REPLAYED', 'RECOVERED'} else None)
    return read_attempt(store, ident), receipt


@pytest.fixture
def linked(tmp_path):
    store = SimulatedTopologyStore(tmp_path / 'state.sqlite')
    attempt, receipt = populate(store)
    return store, attempt, receipt


def open_link(linked):
    store, attempt, _ = linked
    return read_attempt_receipt(store.path, attempt['record']['attempt_id'], attempt['record_sha256'])


def rewrite_attempt(linked):
    store, attempt, _ = linked
    attempt['record_sha256'] = _sha256(attempt['record'])
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE temporal_campaign_attempts SET record_json=?,record_sha256=? WHERE attempt_id=?',
                   (json.dumps(attempt['record']), attempt['record_sha256'], attempt['record']['attempt_id']))


@pytest.mark.parametrize('status', ['CONFIRMED', 'REPLAYED', 'RECOVERED'])
def test_association_is_read_only_and_survives_reopening(tmp_path, monkeypatch, status):
    import quantum_autopilot.control_plane.temporal_campaign as engine
    store = SimulatedTopologyStore(tmp_path / 'state.sqlite')
    attempt, receipt = populate(store, status)
    before = store.path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError('opening a receipt must never run experiments')
    monkeypatch.setattr(engine, 'evaluate_temporal_campaign', forbidden)
    monkeypatch.setattr(SimulatedTopologyStore, '__init__', forbidden)
    result = read_attempt_receipt(store.path, attempt['record']['attempt_id'], attempt['record_sha256'])
    assert result == json.loads(json.dumps({'record': receipt['record'], 'record_sha256': receipt['record_sha256']}))
    assert store.path.read_bytes() == before
    assert read_attempt(store, attempt['record']['attempt_id']) == attempt


@pytest.mark.parametrize('status', ['OPEN', *sorted(TERMINAL - {'CONFIRMED', 'REPLAYED', 'RECOVERED'})])
def test_matching_campaign_does_not_infer_missing_association(tmp_path, status):
    store = SimulatedTopologyStore(tmp_path / 'state.sqlite')
    attempt, receipt = populate(store, status)
    assert receipt  # A campaign with exactly this request really exists.
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match='no recorded receipt'):
        read_attempt_receipt(store.path, attempt['record']['attempt_id'], attempt['record_sha256'])
    assert store.path.read_bytes() == before


@pytest.mark.parametrize('ident,digest', [('', 'a'*64), ('../file', 'a'*64), ('a'*32, True),
                                        ('a'*32, 'bad'), (None, 'a'*64)])
def test_identity_guard_before_database(linked, monkeypatch, ident, digest):
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid input accessed database')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    with pytest.raises(ValueError, match='identity/hash'):
        read_attempt_receipt(linked[0].path, ident, digest)


@pytest.mark.parametrize('change', ['attempt-missing', 'attempt-corrupt', 'attempt-changed',
    'receipt-missing', 'receipt-corrupt', 'receipt-rehashed', 'receipt-request-column'])
def test_changed_missing_or_corrupt_records_refused(linked, change):
    store, attempt, receipt = linked
    with sqlite3.connect(store.path) as db:
        if change == 'attempt-missing':
            db.execute('DELETE FROM temporal_campaign_attempts')
        elif change == 'attempt-corrupt':
            db.execute("UPDATE temporal_campaign_attempts SET record_json='{}'")
        elif change == 'attempt-changed':
            changed = dict(attempt['record'], detail='changed after selection')
            db.execute('UPDATE temporal_campaign_attempts SET record_json=?,record_sha256=?',
                       (json.dumps(changed), _sha256(changed)))
        elif change == 'receipt-missing':
            db.execute('DELETE FROM temporal_path_campaigns')
        elif change == 'receipt-corrupt':
            db.execute("UPDATE temporal_path_campaigns SET record_json='{}'")
        elif change == 'receipt-rehashed':
            changed = dict(receipt['record'], completed_at_utc='2026-09-20T00:00:00Z')
            db.execute('UPDATE temporal_path_campaigns SET record_json=?,record_sha256=?',
                       (json.dumps(changed), _sha256(changed)))
        else:
            db.execute('UPDATE temporal_path_campaigns SET request_sha256=?', ('0'*64,))
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        open_link(linked)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize('field,value', [('receipt_sha256', '0'*64), ('request_sha256', '0'*64),
    ('topology_id', 'different'), ('expected_version', 1), ('expected_version', True)])
def test_rehashed_attempt_still_requires_exact_association(linked, field, value):
    linked[1]['record'][field] = value
    rewrite_attempt(linked)
    with pytest.raises(ValueError):
        open_link(linked)


def test_internal_request_digest_must_match(linked):
    store, attempt, receipt = linked
    receipt['record']['request_sha256'] = '0'*64
    receipt['record_sha256'] = _sha256(receipt['record'])
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE temporal_path_campaigns SET record_json=?,record_sha256=?',
                   (json.dumps(receipt['record']), receipt['record_sha256']))
    attempt['record']['receipt_sha256'] = receipt['record_sha256']
    rewrite_attempt(linked)
    with pytest.raises(ValueError, match='association mismatch'):
        open_link(linked)


@pytest.mark.parametrize('field,value', [('evidence_kind', 'measured'),
    ('physical_action_performed', True), ('workspace_mutated', True)])
def test_rehashed_wrong_evidence_scope_refused(linked, field, value):
    store, attempt, receipt = linked
    receipt['record'][field] = value
    receipt['record_sha256'] = _sha256(receipt['record'])
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE temporal_path_campaigns SET record_json=?,record_sha256=?',
                   (json.dumps(receipt['record']), receipt['record_sha256']))
    attempt['record']['receipt_sha256'] = receipt['record_sha256']
    rewrite_attempt(linked)
    with pytest.raises(ValueError, match='evidence scope'):
        open_link(linked)


def test_uri_special_characters_are_literal_paths(tmp_path):
    store = SimulatedTopologyStore(tmp_path / 'açāo # 100%.sqlite')
    attempt, receipt = populate(store)
    before = store.path.read_bytes()
    assert read_attempt_receipt(store.path, attempt['record']['attempt_id'], attempt['record_sha256'])['record_sha256'] == receipt['record_sha256']
    assert store.path.read_bytes() == before


def test_two_selects_in_one_read_transaction(linked, monkeypatch):
    original, statements = sqlite3.connect, []
    def traced(database_uri, **kwargs):
        assert database_uri.endswith('?mode=ro') and kwargs['uri'] is True
        db = original(database_uri, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            db.execute('DELETE FROM temporal_campaign_attempts')
        db.rollback()
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(sqlite3, 'connect', traced)
    assert open_link(linked)['record_sha256'] == linked[2]['record_sha256']
    assert statements[:2] == ['PRAGMA query_only=ON', 'BEGIN']
    assert len([s for s in statements if s.startswith('SELECT')]) == 2
    assert not any(s.startswith(('INSERT', 'UPDATE', 'DELETE')) for s in statements)


def test_missing_database_is_never_created(tmp_path):
    path = tmp_path / 'missing' / 'state.sqlite'
    with pytest.raises(OSError):
        read_attempt_receipt(path, 'a'*32, 'a'*64)
    assert not path.parent.exists()


def test_concurrent_receipt_change_cannot_mix_snapshots(linked, monkeypatch):
    import quantum_autopilot.control_plane.attempt_receipt as adapter
    store, _, receipt = linked
    with sqlite3.connect(store.path) as db:
        db.execute('PRAGMA journal_mode=WAL')
    original = adapter.verify_attempt_snapshot
    def advance(snapshot):
        # Runs after the first SELECT, before the receipt SELECT.
        with sqlite3.connect(store.path) as db:
            db.execute('DELETE FROM temporal_path_campaigns')
        return original(snapshot)
    monkeypatch.setattr(adapter, 'verify_attempt_snapshot', advance)
    assert open_link(linked)['record_sha256'] == receipt['record_sha256']
    # The first read used a coherent snapshot, not a forever-current view.
    with pytest.raises(ValueError, match='missing'):
        open_link(linked)


def test_ui_opens_verified_comparison_without_new_request(setup, app, monkeypatch):
    import quantum_autopilot.control_plane.temporal_campaign as engine
    service, _, job, dialog, manifest = setup
    attempt, receipt = populate(service.topology_store, 'RECOVERED')
    job.load(manifest)
    dialog.reviewed.setChecked(True)
    job.browse_attempts()
    settle(app, lambda: not job.active)
    assert not dialog.attempt_receipt_open.isEnabled()
    dialog.attempt_table.selectRow(0)
    assert dialog.attempt_receipt_open.isEnabled()
    dialog.attempt_filter.setText('draft')
    assert not dialog.attempt_receipt_open.isEnabled()
    dialog.attempt_filter.clear()
    before = service.topology_store.path.read_bytes()
    monkeypatch.setattr(engine, 'evaluate_temporal_campaign', lambda *a, **k: pytest.fail('reran trials'))
    dialog.attempt_receipt_open.clicked.emit()
    assert job.active and not dialog.attempt_receipt_open.isEnabled()
    settle(app, lambda: not job.active)
    assert job.receipt['record_sha256'] == receipt['record_sha256']
    assert job.request is None and job.process is None and not dialog.run_button.isEnabled()
    assert not dialog.reviewed.isChecked() and dialog.tabs.currentIndex() == 1
    assert 'ASSOCIATION VERIFIED' in job.note
    assert attempt['record']['attempt_id'] in job.provenance_text
    assert 'not proof this dispatch' in job.provenance_text
    assert service.topology_store.path.read_bytes() == before


def test_no_link_button_for_open_or_external_preview(setup, app):
    service, _, job, dialog, _ = setup
    populate(service.topology_store, 'OPEN')
    job.browse_attempts()
    settle(app, lambda: not job.active)
    dialog.attempt_table.selectRow(0)
    assert not dialog.attempt_receipt_open.isEnabled()
    with pytest.raises(ValueError, match='no recorded receipt'):
        job.open_attempt_receipt(0)
    assert not job.active and job.receipt is None


@pytest.mark.parametrize('index', [-1, 1, True, '0', None])
def test_ui_invalid_selection(setup, app, index):
    service, _, job, _, _ = setup
    populate(service.topology_store)
    job.browse_attempts()
    settle(app, lambda: not job.active)
    with pytest.raises(ValueError, match='select an attempt'):
        job.open_attempt_receipt(index)
    assert not job.active


def test_failed_open_clears_old_result_and_request(setup, app):
    service, _, job, dialog, _ = setup
    populate(service.topology_store)
    job.browse_attempts()
    settle(app, lambda: not job.active)
    job.open_attempt_receipt(0)
    settle(app, lambda: not job.active)
    assert job.receipt is not None
    with sqlite3.connect(service.topology_store.path) as db:
        db.execute('DELETE FROM temporal_path_campaigns')
    job.open_attempt_receipt(0)
    assert job.receipt is None
    settle(app, lambda: not job.active)
    assert 'LINKED RECEIPT REFUSED' in job.note
    assert not job.result_rows and not job.comparison_rows and not job.provenance_text
    assert not dialog.export_button.isEnabled() and not dialog.run_button.isEnabled()


@pytest.mark.parametrize('interruption', ['source', 'cancel', 'capability'])
def test_pending_read_discards_late_receipt(setup, app, tmp_path, monkeypatch, interruption):
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    import quantum_autopilot.console.campaign_job as adapter
    service, provider, job, dialog, _ = setup
    populate(service.topology_store)
    job.browse_attempts()
    settle(app, lambda: not job.active)
    entered, release = Event(), Event()
    original = adapter.read_attempt_receipt
    def slow(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(adapter, 'read_attempt_receipt', slow)
    job.open_attempt_receipt(0)
    settle(app, entered.is_set)
    try:
        with pytest.raises(ValueError, match='unavailable'):
            job.open_attempt_receipt(0)
        with pytest.raises(RuntimeError):
            job.shutdown()
        if interruption == 'source':
            provider.switch(lambda: MeasuredFileProvider(tmp_path / 'absent', tmp_path / 'measured'))
        elif interruption == 'cancel':
            job.cancel()
        else:
            caps = provider.active.capabilities
            no_history = replace(caps, operations=tuple(op for op in caps.operations if op != 'read_temporal_campaign_history'))
            monkeypatch.setattr(type(provider.active), 'capabilities', property(lambda _: no_history))
    finally:
        release.set()
    settle(app, lambda: not job.active)
    assert job.receipt is None and not job.result_rows and not job.provenance_text
    assert not dialog.export_button.isEnabled()
    if interruption != 'cancel':
        with pytest.raises(ValueError, match='unavailable'):
            job.open_attempt_receipt(0)


def test_link_layout(setup, app):
    from quantum_autopilot.console.theme import install
    install(app)
    service, _, job, dialog, _ = setup
    populate(service.topology_store)
    job.browse_attempts()
    settle(app, lambda: not job.active)
    dialog.attempt_table.selectRow(0)
    dialog.tabs.setCurrentIndex(3)
    dialog.show()
    for width, height in ((640, 500), (1080, 740)):
        dialog.resize(width, height)
        app.processEvents()
        dialog.attempts_scroll.ensureWidgetVisible(dialog.attempt_receipt_open)
        app.processEvents()
        viewport = dialog.attempts_scroll.viewport()
        assert viewport.rect().contains(dialog.attempt_receipt_open.mapTo(viewport, dialog.attempt_receipt_open.rect().bottomRight()))
        assert dialog.attempt_receipt_open.isEnabled()
        if folder := os.environ.get('ORION_ATTEMPT_RECEIPT_EVIDENCE'):
            Path(folder).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(folder) / f'link-{width}.png'))
    job.open_attempt_receipt(0)
    settle(app, lambda: not job.active)
    app.processEvents()
    if folder := os.environ.get('ORION_ATTEMPT_RECEIPT_EVIDENCE'):
        assert dialog.grab().save(str(Path(folder) / 'linked-comparison.png'))
