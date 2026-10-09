"""Native Q04 reads, source/selection guards and captured evidence export."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
from threading import Event, get_ident
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from quantum_autopilot.console.source_switch import SwitchableProvider  # noqa: E402
from quantum_autopilot.console.widgets import command_history as ui  # noqa: E402
from quantum_autopilot.control_plane.console_commands import ConsoleCommandJournal, read_commands  # noqa: E402
from tests import test_console_inventory as shared  # noqa: E402

app, provider = shared.app, shared.provider


def wait(app, predicate, timeout=8):
    until = time.monotonic() + timeout
    while not predicate() and time.monotonic() < until:
        app.processEvents()
        QTest.qWait(20)
    assert predicate()


def click(button):
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)


@pytest.fixture
def view(provider, app):
    from quantum_autopilot.console.theme import install
    install(app)
    # Set up an isolated local inventory journal; no service executor is needed.
    store = provider.service.topology_store
    journal = ConsoleCommandJournal(store.path)
    command_id = journal.begin('add_simulated_node', asdict(shared.request()))
    journal.transition(command_id, 'RUNNING')
    journal.transition(command_id, 'UNKNOWN', dict(reason='execution_or_readback_failed', error_type='OSError'))
    store.add_inventory_node(shared.request())
    wrapper = SwitchableProvider(provider)
    wrapper.running = True
    dialog = ui.CommandHistory()
    dialog.attach(wrapper)
    wrapper.snapshot_changed.emit(provider.build())
    dialog.show()
    dialog.table.selectRow(0)
    app.processEvents()
    yield dialog, wrapper, journal, command_id
    dialog.close()
    wait(app, lambda: not dialog.job.active and not dialog.export_job.active)
    wrapper.running = False


def query(view, app):
    dialog = view[0]
    assert dialog.check_button.isEnabled()
    click(dialog.check_button)
    wait(app, lambda: not dialog.job.active)
    assert dialog.evidence_json, dialog.status.text()
    return json.loads(dialog.evidence_json)


def test_native_read_is_explicit_background_and_preserves_receipt(view, app, monkeypatch):
    dialog, _, journal, _ = view
    before = journal.path.read_bytes()
    assert not dialog.evidence_json and not dialog.job.active
    main_thread = get_ident()
    threads = []
    original = ui.reconcile_command
    def read(*args):
        threads.append(get_ident())
        return original(*args)
    monkeypatch.setattr(ui, 'reconcile_command', read)
    report = query(view, app)
    assert threads and threads[0] != main_thread
    assert report['record']['comparison']['target_status'] == 'matches_current_state'
    assert report['record']['comparison']['command_outcome'] == 'UNRESOLVED'
    assert report['record']['command'] == read_commands(journal.path)[0]
    assert journal.path.read_bytes() == before
    assert 'UNKNOWN' in dialog.detail.toPlainText()
    assert dialog.export_button.isEnabled()
    for width, height in ((640, 500), (1080, 740)):
        dialog.resize(width, height)
        app.processEvents()
        assert dialog.output.height() > 100
        assert dialog.export_button.geometry().right() < dialog.width()
        if folder := os.environ.get('ORION_RECONCILIATION_UI_EVIDENCE'):
            out = Path(folder)
            out.mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(out / f'commands-reconciliation-{width}.png'))
            (out / 'evidence.json').write_text(dialog.evidence_json, encoding='utf-8')


@pytest.mark.parametrize('change', ['selection', 'clear_selection', 'receipt', 'source', 'capability', 'close'])
def test_pending_read_is_discarded_without_queue(view, app, monkeypatch, change):
    dialog, wrapper, journal, command_id = view
    entered, release = Event(), Event()
    calls = []
    original = ui.reconcile_command
    def slow(*args):
        calls.append(args)
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(ui, 'reconcile_command', slow)
    click(dialog.check_button)
    wait(app, entered.is_set)
    try:
        if change == 'selection':
            journal.begin('add_simulated_node', asdict(shared.request(node_id='other', expected_version=1)))
            wrapper.snapshot_changed.emit(wrapper.active.build())
            dialog.table.selectRow(0)
        elif change == 'clear_selection':
            dialog.table.clearSelection()
        elif change == 'receipt':
            # Same UUID, changed selected receipt bytes are also a new selection.
            records = list(dialog.records)
            records[0] = replace(records[0], serialized=records[0].serialized + ' ')
            dialog.set_records(tuple(records), 'changed')
        elif change == 'source':
            wrapper._generation += 1
            wrapper.source_changed.emit('new source')
        elif change == 'capability':
            wrapper.running = False
            wrapper.snapshot_changed.emit(wrapper.active.build())
        else:
            dialog.close()
        dialog.check_current()
        assert not dialog.evidence_json and not dialog.export_button.isEnabled()
        assert len(calls) == 1
    finally:
        release.set()
    wait(app, lambda: not dialog.job.active)
    assert not dialog.evidence_json and not dialog.output.toPlainText()


def test_unrelated_refresh_preserves_selection_and_evidence(view, app):
    dialog, wrapper, journal, command_id = view
    query(view, app)
    captured = dialog.evidence_json
    journal.begin('add_simulated_node', asdict(shared.request(node_id='other', expected_version=1)))
    wrapper.snapshot_changed.emit(wrapper.active.build())
    assert dialog._selected_record().command_id == command_id
    assert dialog.evidence_json == captured and dialog.export_button.isEnabled()


def test_repeated_updates_preserve_reading_position_and_selection(view, app):
    dialog, wrapper, journal, _ = view
    query(view, app)
    dialog.job.result['record']['reading_fixture'] = [f'Line {i}' for i in range(300)]
    dialog._render()
    dialog.tabs.setCurrentWidget(dialog.output)
    app.processEvents()
    bar = dialog.output.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    cursor = dialog.output.textCursor()
    cursor.setPosition(12)
    cursor.setPosition(24, cursor.MoveMode.KeepAnchor)
    dialog.output.setTextCursor(cursor)
    bar.setValue(bar.maximum() // 2)
    app.processEvents()
    value, selection = bar.value(), dialog.output.textCursor().selectedText()
    revision = dialog.output.document().revision()
    assert value > 0 and selection
    for _ in range(12):
        dialog._render()
    journal.begin('add_simulated_node', asdict(shared.request(node_id='reading-new', expected_version=1)))
    wrapper.snapshot_changed.emit(wrapper.active.build())
    app.processEvents()
    assert bar.value() == value
    assert dialog.output.textCursor().selectedText() == selection
    assert dialog.output.document().revision() == revision


def test_original_receipt_is_not_rewritten_when_selection_is_unchanged(view, app):
    dialog = view[0]
    record = replace(dialog.records[0], serialized='\n'.join(f'Receipt line {i}' for i in range(300)))
    dialog.set_records((record,), 'Captured receipt')
    dialog.table.selectRow(0)
    dialog.tabs.setCurrentWidget(dialog.detail)
    app.processEvents()
    bar = dialog.detail.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    value, revision = bar.value(), dialog.detail.document().revision()
    assert value > 0
    for _ in range(12):
        dialog.selected()
    assert bar.value() == value and dialog.detail.document().revision() == revision


def test_changed_database_receipt_refused_without_snapshot_refresh(view, app):
    dialog, _, journal, command_id = view
    # Reconstruct REQUESTED selection and advance it after publication.
    command_id = journal.begin('add_simulated_node', asdict(shared.request(node_id='next', expected_version=1)))
    dialog.provider.snapshot_changed.emit(dialog.provider.active.build())
    dialog.table.selectRow(0)
    journal.transition(command_id, 'RUNNING')
    click(dialog.check_button)
    wait(app, lambda: not dialog.job.active)
    assert 'changed since selection' in dialog.status.text()
    assert not dialog.evidence_json and not dialog.export_button.isEnabled()


@pytest.mark.parametrize('state', ['CONFIRMED', 'REJECTED'])
def test_terminal_selection_has_no_query(view, app, state):
    dialog, _, _, _ = view
    current = dialog.records[0]
    dialog.set_records((replace(current, state=state),), 'terminal')
    assert not dialog.check_button.isEnabled()
    dialog.check_current()
    assert not dialog.job.active


def test_export_is_captured_async_and_exclusive(view, app, tmp_path, monkeypatch):
    dialog, wrapper, journal, _ = view
    query(view, app)
    captured = dialog.evidence_json
    version_before=wrapper.active.service.topology_store.get(shared.request().topology_id).topology_version
    target = tmp_path / 'evidence.json'
    main_thread = get_ident()
    threads = []
    original = ui._export_captured
    def write(*args):
        threads.append(get_ident())
        return original(*args)
    monkeypatch.setattr(ui, '_export_captured', write)
    def choose(*args):
        store=wrapper.active.service.topology_store
        version=store.get(shared.request().topology_id).topology_version
        store.add_inventory_node(shared.request(node_id='later', expected_version=version))
        wrapper.snapshot_changed.emit(wrapper.active.build())
        return str(target), 'JSON'
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', choose)
    click(dialog.export_button)
    wait(app, lambda: not dialog.export_job.active)
    assert threads and threads[0] != main_thread
    assert target.read_text(encoding='utf-8') == captured
    assert json.loads(captured)['record']['topology']['topology_version'] == version_before
    assert wrapper.active.service.topology_store.get(shared.request().topology_id).topology_version == version_before+1
    before = journal.path.read_bytes()
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', lambda *args: (str(target), 'JSON'))
    click(dialog.export_button)
    wait(app, lambda: not dialog.export_job.active)
    assert 'REFUSED' in dialog.status.text()
    assert target.read_text(encoding='utf-8') == captured and journal.path.read_bytes() == before


def test_read_permission_does_not_grant_captured_export(view, app, monkeypatch):
    dialog, wrapper, _, _ = view
    snap = wrapper.active.build()
    capabilities = replace(snap.capabilities,
                           operations=tuple(op for op in snap.capabilities.operations if op != 'export_record'))
    wrapper.snapshot_changed.emit(replace(snap, capabilities=capabilities))
    dialog.table.selectRow(0)
    query(view, app)
    assert dialog.check_button.isEnabled()
    assert dialog.evidence_json and not dialog.export_button.isEnabled()
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName',
                        lambda *args: pytest.fail('file picker requires export permission'))
    dialog.export_evidence()
    assert not dialog.export_job.active


@pytest.mark.parametrize('change', ['cancel', 'selection', 'source', 'capability', 'close'])
def test_file_picker_changes_cancel_export(view, app, tmp_path, monkeypatch, change):
    dialog, wrapper, _, _ = view
    query(view, app)
    target = tmp_path / 'not-created.json'
    def choose(*args):
        if change == 'selection':
            dialog.table.clearSelection()
        elif change == 'source':
            wrapper._generation += 1  # guard also works without a notification
        elif change == 'capability':
            wrapper.running = False
        elif change == 'close':
            dialog.close()
        return ('' if change == 'cancel' else str(target)), 'JSON'
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', choose)
    click(dialog.export_button)
    assert not target.exists() and not dialog.export_job.active


def test_close_reopen_requires_new_explicit_read(view, app):
    dialog = view[0]
    query(view, app)
    dialog.close()
    dialog.show()
    app.processEvents()
    assert not dialog.evidence_json and not dialog.export_button.isEnabled()
    assert dialog.check_button.isEnabled()


def test_missing_journal_refused_without_creation(view, app, tmp_path, monkeypatch):
    dialog, wrapper, _, _ = view
    missing = tmp_path / 'missing.sqlite'
    monkeypatch.setattr(wrapper.active, 'command_reconciliation_store_path', lambda: missing)
    click(dialog.check_button)
    wait(app, lambda: not dialog.job.active)
    assert 'REFUSED' in dialog.status.text()
    assert not missing.exists() and not dialog.evidence_json


def test_window_wires_command_center(provider, app):
    from quantum_autopilot.console.shell import ConsoleWindow
    window = ConsoleWindow(provider, skip_auth=True)
    assert window.screens['audit'].command_history.provider is window.provider
    window.close()

@pytest.mark.parametrize('change', ['selection', 'source'])
def test_accepted_export_finishes_captured_file_but_discards_late_ui_result(view, app, tmp_path, monkeypatch, change):
    dialog, wrapper, _, _ = view
    query(view, app)
    captured = dialog.evidence_json
    target = tmp_path / 'accepted.json'
    entered, release = Event(), Event()
    original = ui._export_captured
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(ui, '_export_captured', slow)
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', lambda *args: (str(target), 'JSON'))
    click(dialog.export_button)
    wait(app, entered.is_set)
    try:
        assert not dialog.check_button.isEnabled()
        if change == 'selection':
            dialog.table.clearSelection()
        else:
            wrapper._generation += 1
            wrapper.source_changed.emit('changed')
        dialog.check_current()
        assert not dialog.job.active and not dialog.evidence_json
    finally:
        release.set()
    wait(app, lambda: not dialog.export_job.active)
    assert target.read_text(encoding='utf-8') == captured
    assert not dialog.export_job.result and not dialog.evidence_json
    assert 'EXPORTED' not in dialog.status.text()
