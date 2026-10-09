"""Draft editing is explicit, bounded, atomic and never dispatches trials."""
import json
import os
from dataclasses import asdict, replace
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from quantum_autopilot.control_plane.temporal_campaign_cli import parse_manifest, load_manifest
from quantum_autopilot.console.widgets.campaign_manifest_editor import CampaignManifestEditor
from tests import test_console_temporal_campaign as shared
from tests.test_temporal_campaign import request

app, setup, settle = shared.app, shared.setup, shared.settle


@pytest.mark.parametrize('raw', [b'', b'null', b'\xff', b'{"a":1,"a":2}', b'{"a":NaN}',
    b'{"a":Infinity}', b'['*1200+b']'*1200, b' '*65537, 'text'],
    ids=['empty', 'null', 'encoding', 'duplicate', 'nan', 'infinity', 'deep', 'oversize', 'not-bytes'])
def test_shared_parser_refuses_invalid_input(raw):
    with pytest.raises(ValueError):
        parse_manifest(raw)


def test_parser_file_parity_and_byte_boundary(tmp_path):
    raw = json.dumps(asdict(request())).encode()
    raw += b' '*(65536-len(raw))
    path = tmp_path / 'manifest.json'
    path.write_bytes(raw)
    assert load_manifest(path) == parse_manifest(raw) == request()
    with pytest.raises(ValueError, match='64 KiB'):
        parse_manifest(raw+b' ')


@pytest.fixture
def editor(setup, app):
    _, _, job, dialog, manifest = setup
    job.load(manifest)
    dialog.reviewed.setChecked(True)
    widget = CampaignManifestEditor(job, dialog)
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_apply_updates_review_only_and_leaves_source_file_unchanged(setup, editor):
    service, _, job, dialog, manifest = setup
    before_db, before_file = service.topology_store.path.read_bytes(), manifest.read_bytes()
    draft = json.loads(editor.text.toPlainText())
    draft['campaign_id'] = 'edited-campaign'
    draft['target_fidelity'] = .82
    editor.text.setPlainText(json.dumps(draft))
    editor.apply()
    assert editor.result() == editor.DialogCode.Accepted
    assert job.request.campaign_id == 'edited-campaign' and job.request.target_fidelity == .82
    assert not dialog.reviewed.isChecked() and not dialog.run_button.isEnabled()
    assert job.process is None and not job.active and job.receipt is None
    assert not job.result_rows and 'edited-campaign' in job.review_text
    assert service.topology_store.path.read_bytes() == before_db and manifest.read_bytes() == before_file


@pytest.mark.parametrize('change', ['json', 'duplicates', 'version', 'budget', 'seeds', 'oversize', 'unicode'])
def test_invalid_draft_is_atomic(setup, editor, change):
    _, _, job, dialog, _ = setup
    original, review = job.request, job.review_text
    draft = json.loads(editor.text.toPlainText())
    if change == 'version':
        draft['expected_version'] += 1
    elif change == 'budget':
        draft['seeds'] = list(range(64))
        draft['max_attempts'] = 1000
    elif change == 'seeds':
        draft['seeds'] = [0, 0]
    text = json.dumps(draft)
    if change == 'json':
        text = '{'
    elif change == 'duplicates':
        text = text[:-1] + ', "campaign_id":"duplicate"}'
    elif change == 'oversize':
        text = ' '*65537
    elif change == 'unicode':
        # QString normalizes isolated surrogates; test the adapter directly.
        with pytest.raises(ValueError):
            job.apply_manifest_draft('\ud800', expected_request=original, expected_context=job._context())
        assert job.request is original
        return
    editor.text.setPlainText(text)
    editor.apply()
    assert 'DRAFT REFUSED' in editor.note.text()
    assert job.request is original and job.review_text == review
    assert dialog.reviewed.isChecked() and job.process is None and not job.active


def test_cancel_discards_only_draft(setup, editor):
    _, _, job, dialog, _ = setup
    original = job.request
    editor.text.setPlainText('{}')
    editor.reject()
    assert job.request is original and dialog.reviewed.isChecked()


@pytest.mark.parametrize('change', ['source', 'manifest', 'capability', 'busy'])
def test_stale_editor_cannot_overwrite_current_state(setup, editor, app, tmp_path, monkeypatch, change):
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    _, provider, job, _, manifest = setup
    if change == 'source':
        provider.switch(lambda: MeasuredFileProvider(tmp_path / 'missing', tmp_path / 'measured'))
    elif change == 'manifest':
        job.load(manifest)  # Same content, different explicitly loaded request.
    elif change == 'capability':
        caps = provider.active.capabilities
        read_only = replace(caps, operations=tuple(op for op in caps.operations if op != 'run_temporal_campaign'))
        monkeypatch.setattr(type(provider.active), 'capabilities', property(lambda _: read_only))
    else:
        job.active = True
    current = job.request
    try:
        editor.refresh()
        assert not editor.apply_button.isEnabled()
        editor.apply()  # Backend rechecks even if invoked outside the enabled UI.
        assert 'DRAFT REFUSED' in editor.note.text() and job.request is current
    finally:
        if change == 'busy':
            job.active = False


def test_edit_after_completed_run_clears_results_without_changing_receipt(setup, app):
    service, _, job, dialog, manifest = setup
    job.load(manifest)
    job.start()
    settle(app, lambda: not job.active)
    assert job.receipt is not None
    before = service.topology_store.path.read_bytes()
    widget = CampaignManifestEditor(job, dialog)
    try:
        draft = json.loads(widget.text.toPlainText())
        draft['campaign_id'] = 'next-explicit-campaign'
        widget.text.setPlainText(json.dumps(draft))
        widget.apply()
        assert job.receipt is None and not job.provenance_text and not job.result_rows
        assert job.process is None and not dialog.reviewed.isChecked()
        assert service.topology_store.path.read_bytes() == before
    finally:
        widget.deleteLater()


def test_edit_button_requires_loaded_runnable_manifest(setup, app):
    _, _, job, dialog, manifest = setup
    assert not dialog.edit_manifest_button.isEnabled()
    job.load(manifest)
    assert dialog.edit_manifest_button.isEnabled()
    job.active = True
    dialog.refresh()
    assert not dialog.edit_manifest_button.isEnabled()
    job.active = False


def test_runner_modal_editor_integration(setup, app, monkeypatch):
    _, _, job, dialog, manifest = setup
    job.load(manifest)
    def apply_modal(widget):
        draft = json.loads(widget.text.toPlainText())
        draft['campaign_id'] = 'modal-edited'
        widget.text.setPlainText(json.dumps(draft))
        widget.apply()
        return widget.result()
    monkeypatch.setattr(CampaignManifestEditor, 'exec', apply_modal)
    dialog.edit_manifest()
    assert job.request.campaign_id == 'modal-edited' and dialog.tabs.currentIndex() == 0


def test_editor_layout(setup, editor, app):
    from quantum_autopilot.console.theme import install
    install(app)
    editor.show()
    for width, height in ((640, 500), (1080, 740)):
        editor.resize(width, height)
        app.processEvents()
        assert editor.text.viewport().height() > 100
        assert editor.rect().contains(editor.apply_button.mapTo(editor, editor.apply_button.rect().bottomRight()))
        assert editor.note.geometry().bottom() < editor.apply_button.mapTo(editor, editor.apply_button.rect().topLeft()).y()
        if folder := os.environ.get('ORION_MANIFEST_EDITOR_EVIDENCE'):
            Path(folder).mkdir(parents=True, exist_ok=True)
            assert editor.grab().save(str(Path(folder) / f'editor-{width}.png'))


def save_applied(job, path):
    job.save_manifest(path, expected_request=job.request, expected_context=job._context())


def test_save_applied_roundtrip_without_dispatch_or_journal_change(setup, editor, app, tmp_path):
    service, _, job, dialog, manifest = setup
    original_file, original_db = manifest.read_bytes(), service.topology_store.path.read_bytes()
    draft = json.loads(editor.text.toPlainText())
    draft['campaign_id'] = 'saved-explicit-config'
    editor.text.setPlainText(json.dumps(draft))
    editor.apply()
    applied = job.request
    output = tmp_path / 'configuration with spaces.json'
    save_applied(job, output)
    assert job.active and not dialog.save_manifest_button.isEnabled()
    assert job.process is None and not dialog.cancel_button.isEnabled()
    settle(app, lambda: not job.active)
    assert load_manifest(output) == applied
    assert json.loads(output.read_bytes()) == json.loads(json.dumps(asdict(applied)))
    assert job.request is applied and not dialog.reviewed.isChecked()
    assert 'MANIFEST SAVED' in job.note and job.receipt is None and job.attempt_id is None
    assert manifest.read_bytes() == original_file and service.topology_store.path.read_bytes() == original_db
    job.load(output)
    assert job.request == applied and job.process is None and not dialog.reviewed.isChecked()


@pytest.mark.parametrize('destination', ['original', 'existing', 'directory', 'missing-parent'])
def test_save_never_overwrites_and_failure_preserves_review(setup, app, tmp_path, destination):
    _, _, job, dialog, manifest = setup
    job.load(manifest)
    dialog.reviewed.setChecked(True)
    before, original = manifest.read_bytes(), job.request
    output = {'original': manifest, 'existing': tmp_path / 'existing.json',
              'directory': tmp_path, 'missing-parent': tmp_path / 'missing' / 'file.json'}[destination]
    if destination == 'existing':
        output.write_bytes(b'preserve me')
    save_applied(job, output)
    settle(app, lambda: not job.active)
    assert 'MANIFEST SAVE FAILED' in job.note and job.request is original
    assert dialog.reviewed.isChecked() and manifest.read_bytes() == before
    if destination == 'existing':
        assert output.read_bytes() == b'preserve me'
    if destination == 'missing-parent':
        assert not output.parent.exists()


@pytest.mark.parametrize('change', ['source', 'manifest', 'capability', 'busy', 'empty'])
def test_save_guard_after_file_chooser(setup, app, tmp_path, monkeypatch, change):
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    from PySide6.QtWidgets import QFileDialog
    _, provider, job, dialog, manifest = setup
    job.load(manifest)
    output = tmp_path / 'refused.json'
    def choose(*args):
        if change == 'source':
            provider.switch(lambda: MeasuredFileProvider(tmp_path / 'missing', tmp_path / 'measured'))
        elif change == 'manifest':
            job.load(manifest)
        elif change == 'capability':
            caps = provider.active.capabilities
            caps = replace(caps, operations=tuple(op for op in caps.operations if op != 'run_temporal_campaign'))
            monkeypatch.setattr(type(provider.active), 'capabilities', property(lambda _: caps))
        elif change == 'busy':
            job.active = True
        else:
            job.request = None
        return str(output), ''
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose)
    try:
        dialog.choose_manifest_save()
        assert 'REFUSED' in dialog.note.text() and not output.exists() and job.process is None
    finally:
        if change == 'busy':
            job.active = False


def test_save_chooser_cancel_and_concurrent_destination(setup, app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    _, _, job, dialog, manifest = setup
    job.load(manifest)
    original, note = job.request, job.note
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: ('', ''))
    dialog.choose_manifest_save()
    assert job.request is original and job.note == note and not job.active
    output = tmp_path / 'concurrent.json'
    def choose(*args):
        output.write_bytes(b'other writer')
        return str(output), ''
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose)
    dialog.choose_manifest_save()
    settle(app, lambda: not job.active)
    assert output.read_bytes() == b'other writer' and 'MANIFEST SAVE FAILED' in job.note


def test_save_is_background_and_source_change_does_not_receive_late_status(setup, app, tmp_path, monkeypatch):
    from threading import Event
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    _, provider, job, dialog, manifest = setup
    job.load(manifest)
    output = (tmp_path / 'accepted-before-source-change.json').absolute()
    entered, release = Event(), Event()
    original_open = Path.open
    def delayed_open(path, *args, **kwargs):
        if path == output:
            entered.set()
            if not release.wait(10):
                raise TimeoutError('test did not release export')
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', delayed_open)
    request_before = job.request
    try:
        save_applied(job, output)
        settle(app, entered.is_set)
        assert job.active and job.process is None and not output.exists()
        provider.switch(lambda: MeasuredFileProvider(tmp_path / 'missing', tmp_path / 'measured'))
        changed_note = job.note
        assert job.request is None and not dialog.save_manifest_button.isEnabled()
    finally:
        release.set()
        settle(app, lambda: not job.active)
    assert load_manifest(output) == request_before
    assert job.note == changed_note and 'MANIFEST SAVED' not in job.note and job.request is None


def test_save_after_completed_run_preserves_result_and_review(setup, app, tmp_path):
    service, _, job, dialog, manifest = setup
    job.load(manifest)
    job.start()
    settle(app, lambda: not job.active)
    receipt, rows, attempt = job.receipt, job.result_rows, job.attempt_id
    before_db = service.topology_store.path.read_bytes()
    dialog.reviewed.setChecked(True)
    save_applied(job, tmp_path / 'completed-config.json')
    settle(app, lambda: not job.active)
    assert job.receipt is receipt and job.result_rows is rows and job.attempt_id == attempt
    assert dialog.reviewed.isChecked() and service.topology_store.path.read_bytes() == before_db
    job.browse_history()
    settle(app, lambda: not job.active)
    job.open_history(0)
    settle(app, lambda: not job.active)
    assert job.request is None and not dialog.save_manifest_button.isEnabled()
    with pytest.raises(ValueError):
        save_applied(job, tmp_path / 'historical.json')
    assert not (tmp_path / 'historical.json').exists()


def test_manifest_save_layout(setup, app):
    from quantum_autopilot.console.theme import install
    install(app)
    _, _, job, dialog, manifest = setup
    job.load(manifest)
    dialog.show()
    for width, height in ((640, 500), (1080, 740)):
        dialog.resize(width, height)
        app.processEvents()
        assert dialog.manifest.viewport().height() > 60
        assert dialog.edit_manifest_button.geometry().bottom() < dialog.save_manifest_button.geometry().top()
        assert dialog.save_manifest_button.geometry().bottom() < dialog.manifest.geometry().top()
        assert dialog.rect().contains(dialog.save_manifest_button.mapTo(dialog, dialog.save_manifest_button.rect().bottomRight()))
        if folder := os.environ.get('ORION_MANIFEST_EDITOR_EVIDENCE'):
            Path(folder).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(folder) / f'manifest-save-{width}.png'))


def test_save_button_success_and_unavailable_no_chooser(setup, app, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog
    _, _, job, dialog, manifest = setup
    calls = []
    output = tmp_path / 'via-button.json'
    def choose(*args):
        calls.append(1)
        return str(output), ''
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose)
    assert not dialog.save_manifest_button.isEnabled()
    dialog.choose_manifest_save()
    assert not calls
    job.load(manifest)
    dialog.show()
    app.processEvents()
    QTest.mouseClick(dialog.save_manifest_button, Qt.MouseButton.LeftButton)
    settle(app, lambda: not job.active)
    assert calls == [1] and load_manifest(output) == job.request


def test_write_failure_is_not_success_and_preserves_loaded_request(setup, app, tmp_path, monkeypatch):
    _, _, job, dialog, manifest = setup
    job.load(manifest)
    original, review = job.request, job.review_text
    output = (tmp_path / 'partial.json').absolute()
    original_open = Path.open
    class FailedWrite:
        def __enter__(self):
            self.stream = original_open(output, 'xb')
            return self
        def write(self, raw):
            self.stream.write(raw[:10])
            raise OSError('injected disk failure')
        def __exit__(self, *args):
            self.stream.close()
    def failing_open(path, *args, **kwargs):
        return FailedWrite() if path == output and args == ('xb',) else original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', failing_open)
    save_applied(job, output)
    settle(app, lambda: not job.active)
    assert 'MANIFEST SAVE FAILED' in job.note and 'incomplete file' in job.note
    assert job.request is original and job.review_text == review and job.process is None
    assert len(output.read_bytes()) == 10
