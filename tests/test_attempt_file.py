"""Untrusted operational files stay outside scientific state and the journal."""
import json
import os
from pathlib import Path
from threading import Event

import pytest

from quantum_autopilot.control_plane.attempt_file import MAX_ATTEMPT_FILE_BYTES, verify_attempt_file
from quantum_autopilot.control_plane.campaign_attempts import begin_attempt, finish_attempt, read_attempt, export_attempt_snapshot, TERMINAL
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore, _sha256
from tests import test_console_temporal_campaign as shared
from tests.test_temporal_campaign import request

app, setup, settle = shared.app, shared.setup, shared.settle


@pytest.fixture
def exported(tmp_path):
    store = SimulatedTopologyStore(tmp_path / 'external.sqlite')
    attempt = begin_attempt(store, request())
    snapshot = read_attempt(store, attempt)
    path = tmp_path / 'external.json'
    export_attempt_snapshot(store, attempt, snapshot['record_sha256'], path)
    return path, snapshot, store, attempt


def write_snapshot(path, snapshot):
    snapshot['record_sha256'] = _sha256(snapshot['record'])
    path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')


@pytest.mark.parametrize('status', ['OPEN', *sorted(TERMINAL)])
def test_roundtrip_states_without_store_access(exported, status, monkeypatch):
    path, _, store, attempt = exported
    if status != 'OPEN':
        finish_attempt(store, attempt, status=status, exit_code=-1,
                       receipt_sha256='a'*64 if status in {'CONFIRMED', 'REPLAYED', 'RECOVERED'} else None)
        snapshot = read_attempt(store, attempt)
        write_snapshot(path, snapshot)
    before = path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError('verification must not open a journal')
    monkeypatch.setattr(SimulatedTopologyStore, '_connect', forbidden)
    result = verify_attempt_file(path)
    assert result['record']['status'] == status
    assert path.read_bytes() == before


@pytest.mark.parametrize('field,value', [
    ('schema', 'orion.temporal_campaign.v1'), ('scope', 'physical'), ('physical_action_performed', True),
    ('attempt_id', 'x'*32), ('request_sha256', 'no'), ('expected_version', True), ('expected_version', -1),
    ('cancel_requested', 0), ('campaign_id', ''), ('campaign_id', 'a\nb'), ('topology_id', []),
    ('detail', 'x'*2001), ('detail', {}), ('status', []), ('status', 'SUCCESS'),
    ('requested_at_utc', 'yesterday'), ('requested_at_utc', '2026-02-30T00:00:00Z'),
    ('requested_at_utc', '2026-09-14T00:00:00-03:00'), ('requested_at_utc', '2026-09-14T00:00:00'),
    ('finished_at_utc', '2026-09-14T00:00:00Z'), ('process_exit_code', 0),
    ('cancel_requested', True), ('receipt_sha256', 'a'*64), ('extra_field', 1),
])
def test_rehashed_invalid_records_still_refused(exported, field, value):
    path, snapshot, _, _ = exported
    snapshot['record'][field] = value
    write_snapshot(path, snapshot)
    with pytest.raises(ValueError):
        verify_attempt_file(path)


@pytest.mark.parametrize('field,value', [('process_exit_code', True), ('process_exit_code', None),
                                       ('finished_at_utc', None), ('receipt_sha256', 'invalid')])
def test_terminal_metadata_refused(exported, field, value):
    path, _, store, attempt = exported
    finish_attempt(store, attempt, status='CONFIRMED', exit_code=0, receipt_sha256='a'*64)
    snapshot = read_attempt(store, attempt)
    snapshot['record'][field] = value
    write_snapshot(path, snapshot)
    with pytest.raises(ValueError):
        verify_attempt_file(path)


@pytest.mark.parametrize('raw', [b'', b'{', b'\xff', b'[]', b'null', b'{"record":{},"record":{}}',
    b'{"record":{"status":"OPEN","status":"OPEN"}}', b'{"value":NaN}', b'{"value":Infinity}',
    b'['*1200+b']'*1200, b' '* (MAX_ATTEMPT_FILE_BYTES+1)],
    ids=['empty', 'truncated', 'encoding', 'array', 'null', 'duplicate-wrapper', 'duplicate-record',
         'nan', 'infinity', 'deep-nesting', 'oversize'])
def test_malformed_inputs_bounded_and_refused(tmp_path, raw):
    path = tmp_path / 'bad.json'
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        verify_attempt_file(path)
    assert path.read_bytes() == raw


def test_hash_wrapper_and_unicode_rules(exported):
    path, snapshot, _, _ = exported
    snapshot['record']['detail'] = '<b>não autenticado</b> / a path is only text'
    write_snapshot(path, snapshot)
    path.write_bytes(b'\xef\xbb\xbf' + path.read_bytes())
    assert verify_attempt_file(path) == snapshot
    snapshot['record']['detail'] = 'altered'
    path.write_text(json.dumps(snapshot), encoding='utf-8')
    with pytest.raises(ValueError, match='SHA256 mismatch'):
        verify_attempt_file(path)
    snapshot['record_sha256'] = True
    path.write_text(json.dumps(snapshot), encoding='utf-8')
    with pytest.raises(ValueError):
        verify_attempt_file(path)
    snapshot['extra'] = True
    path.write_text(json.dumps(snapshot), encoding='utf-8')
    with pytest.raises(ValueError, match='wrapper'):
        verify_attempt_file(path)


def test_invalid_surrogate_is_refused(exported):
    path, snapshot, _, _ = exported
    snapshot['record']['detail'] = '\ud800'
    path.write_text(json.dumps(snapshot), encoding='utf-8')
    with pytest.raises(ValueError, match='canonically encoded'):
        verify_attempt_file(path)


def test_missing_file(tmp_path):
    with pytest.raises(OSError):
        verify_attempt_file(tmp_path / 'missing.json')


def test_limit_boundary_and_rehashed_edit_are_not_authentication(exported):
    path, snapshot, _, _ = exported
    snapshot['record']['detail'] = 'Edited and rehashed by someone else'
    write_snapshot(path, snapshot)
    raw = path.read_bytes()
    path.write_bytes(raw + b' '*(MAX_ATTEMPT_FILE_BYTES-len(raw)))
    assert verify_attempt_file(path) == snapshot  # Consistency is not authorship.
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='32 KiB'):
        verify_attempt_file(path)


def test_scientific_receipt_is_unchanged(setup, app, exported):
    from quantum_autopilot.control_plane.temporal_campaign import run_temporal_campaign
    service, _, job, _, _ = setup
    run_temporal_campaign(service.topology_store, request())
    job.browse_history()
    settle(app, lambda: not job.active)
    job.open_history(0)
    settle(app, lambda: not job.active)
    receipt, result_rows, provenance = job.receipt, job.result_rows, job.provenance_text
    db_before = service.topology_store.path.read_bytes()
    job.verify_attempt_file(exported[0])
    settle(app, lambda: not job.active)
    assert job.receipt is receipt and job.result_rows == result_rows and job.provenance_text == provenance
    assert service.topology_store.path.read_bytes() == db_before


def test_read_only_ui_keeps_scientific_state_and_clears_failed_preview(setup, app, exported, monkeypatch):
    service, _, job, dialog, manifest = setup
    path, snapshot, _, _ = exported
    job.load(manifest)
    request_before, text_before = job.request, job.review_text
    journal_before = service.topology_store.path.read_bytes()
    # Verifying an external file must never even ask for the current DB path.
    def forbidden(*args):
        raise AssertionError('no source journal access')
    monkeypatch.setattr(type(job.provider.active), 'temporal_campaign_history_store_path', forbidden)
    assert dialog.attempt_file_button.isEnabled() and not job.attempts
    job.verify_attempt_file(path)
    assert job.active and not dialog.attempt_file_button.isEnabled()
    settle(app, lambda: not job.active)
    assert 'SCHEMA + RECORD HASH VERIFIED' in dialog.attempt_file_view.toPlainText()
    assert snapshot['record_sha256'] in job.attempt_file_text
    assert job.request is request_before and job.review_text == text_before
    assert job.receipt is None and not job.attempts and job.process is None
    assert service.topology_store.path.read_bytes() == journal_before
    job.verify_attempt_file(path.parent / 'missing.json')
    assert snapshot['record_sha256'] not in job.attempt_file_text
    settle(app, lambda: not job.active)
    assert job.attempt_file_text.startswith('FILE REFUSED')
    assert snapshot['record_sha256'] not in dialog.attempt_file_view.toPlainText()


def test_chooser_cancel_and_source_change(setup, app, exported, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    _, provider, job, dialog, _ = setup
    path = exported[0]
    original_text = job.attempt_file_text
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: ('', ''))
    dialog.choose_attempt_file()
    assert not job.active and job.attempt_file_text == original_text
    def choose(*args):
        provider.switch(lambda: MeasuredFileProvider(tmp_path / 'absent', tmp_path / 'measured'))
        return str(path), 'JSON'
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', choose)
    dialog.choose_attempt_file()
    assert not job.active and 'source changed while choosing' in dialog.note.text()
    assert not dialog.attempt_file_button.isEnabled()
    with pytest.raises(ValueError, match='unavailable'):
        job.verify_attempt_file(path)


@pytest.mark.parametrize('interruption', ['source', 'cancel', 'capability'])
def test_async_read_discards_late_results(setup, app, exported, tmp_path, monkeypatch, interruption):
    from dataclasses import replace
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    import quantum_autopilot.console.campaign_job as adapter
    _, provider, job, dialog, _ = setup
    entered, release = Event(), Event()
    original = adapter.verify_attempt_file
    def slow(path):
        entered.set()
        assert release.wait(10)
        return original(path)
    monkeypatch.setattr(adapter, 'verify_attempt_file', slow)
    job.verify_attempt_file(exported[0])
    settle(app, entered.is_set)
    try:
        with pytest.raises(ValueError):
            job.verify_attempt_file(exported[0])
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
    assert 'HASH VERIFIED' not in job.attempt_file_text
    assert exported[1]['record_sha256'] not in dialog.attempt_file_view.toPlainText()


def test_verification_layout(setup, app, exported):
    from quantum_autopilot.console.theme import install
    install(app)
    _, _, job, dialog, _ = setup
    job.verify_attempt_file(exported[0])
    settle(app, lambda: not job.active)
    dialog.tabs.setCurrentIndex(4)
    dialog.show()
    for width, height in ((640, 500), (1080, 740)):
        dialog.resize(width, height)
        app.processEvents()
        assert dialog.attempt_file_view.isReadOnly()
        assert dialog.attempt_file_view.viewport().height() > 60
        assert dialog.rect().contains(dialog.attempt_file_button.mapTo(dialog, dialog.attempt_file_button.rect().bottomRight()))
        if folder := os.environ.get('ORION_ATTEMPT_FILE_EVIDENCE'):
            Path(folder).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(folder) / f'verify-{width}.png'))
