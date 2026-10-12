"""Scroll every reader/editor without selecting text to force its first repaint."""
import json
import os
from types import SimpleNamespace

import pytest

pytest.importorskip('PySide6')
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.responsive_validation import run_event_loop  # noqa: E402
from quantum_autopilot.console.text_scroll_validation import (  # noqa: E402
    prepare_display_fixture, probe_captured_receipt, probe_text_scroll, reveal_text, text_dialog_cases,
)
from tests import test_console_policy_context as context_tests  # noqa: E402

provider, panel = context_tests.provider, context_tests.panel


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('mode', ['dark', 'light'])
@pytest.mark.parametrize('name,factory', list(text_dialog_cases()), ids=[name for name, _ in text_dialog_cases()])
def test_every_text_field_paints_after_wheel_keys_and_scrollbar(app, mode, name, factory):
    theme.set_theme(mode)
    theme.install(app)
    view = factory()
    view.resize(960, 680)
    view.show()
    prepare_display_fixture(view)
    try:
        fields = view.findChildren(QPlainTextEdit)
        assert fields, name
        for field in fields:
            reveal_text(view, field)
            assert field.isVisible() and field.viewport().height() > 20
            checks = probe_text_scroll(field)
            assert all(checks.values()), (name, field.accessibleName(), [key for key, ok in checks.items() if not ok])
    finally:
        view.close()
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        theme.set_theme('dark')
        theme.install(app)


def test_real_captured_observation_scrolls_and_watchdog_preserves_reading(panel, app, tmp_path):
    view, _, service, _ = panel
    result = context_tests.observe(panel, app)
    view.tabs.setCurrentWidget(view.receipt)
    run_event_loop(30)
    captured = json.dumps(result, indent=2, ensure_ascii=False)
    before = service.controller_runtime.journal.path.read_bytes()
    paints = probe_captured_receipt(view.receipt)
    assert all(paints.values()), paints
    # The real receipt has sufficient JSON lines to exercise the reader.
    assert view.receipt.document().blockCount() > 40
    view.receipt.verticalScrollBar().setValue(15)
    run_event_loop(30)
    cursor = view.receipt.textCursor()
    cursor.setPosition(80)
    cursor.setPosition(110, QTextCursor.MoveMode.KeepAnchor)
    view.receipt.setTextCursor(cursor)
    view.receipt.verticalScrollBar().setValue(15)
    scroll = view.receipt.verticalScrollBar().value()
    revision = view.receipt.document().revision()
    selected = view.receipt.textCursor().selectedText()
    for _ in range(8):
        view._tick()
    run_event_loop(300)  # Includes a real 250 ms watchdog timeout.
    assert view.receipt.toPlainText() == captured
    assert view.receipt.document().revision() == revision
    assert view.receipt.verticalScrollBar().value() == scroll
    assert view.receipt.textCursor().selectedText() == selected
    from quantum_autopilot.console.widgets.policy_context import _export_captured
    output = tmp_path / 'captured.json'
    view.export_job.submit(_export_captured, captured + '\n', str(output),
                           view.provider.active.export_captured_evidence)
    context_tests.wait(app, lambda: not view.export_job.active)
    assert not view.export_job.error
    assert json.loads(output.read_text(encoding='utf-8')) == result
    assert service.controller_runtime.journal.path.read_bytes() == before
    assert not result['record']['selection_available']


def test_worker_refresh_keeps_offline_result_selection_and_scroll(app):
    from quantum_autopilot.console.widgets.sync_session import SyncSession

    view = SyncSession()
    view.show()
    text = '\n'.join(f'OFFLINE RESULT {i}' for i in range(180))
    try:
        view.job.result = SimpleNamespace(describe=lambda: text)
        view._refresh()
        run_event_loop(30)
        cursor = view.output.textCursor()
        cursor.setPosition(90)
        cursor.setPosition(130, QTextCursor.MoveMode.KeepAnchor)
        view.output.setTextCursor(cursor)
        view.output.verticalScrollBar().setValue(25)
        scroll = view.output.verticalScrollBar().value()
        revision = view.output.document().revision()
        selected = view.output.textCursor().selectedText()
        for _ in range(8):
            view.job.changed.emit()
        run_event_loop(40)
        assert view.output.document().revision() == revision
        assert view.output.verticalScrollBar().value() == scroll
        assert view.output.textCursor().selectedText() == selected
        assert view.package is None and not view.job.active
    finally:
        view.close()
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_scroll_support_keeps_both_native_constructor_overloads_and_json_undo(app):
    from quantum_autopilot.console.widgets.evidence_text import EvidenceText, ScrollableTextEdit
    from PySide6.QtWidgets import QWidget

    parent = QWidget()
    reader = EvidenceText('Initial receipt Ω', parent)
    editor = ScrollableTextEdit(parent)
    try:
        assert reader.toPlainText() == 'Initial receipt Ω' and reader.isReadOnly()
        assert reader.parentWidget() is parent and editor.parentWidget() is parent
        assert not editor.isReadOnly()
        editor.setPlainText('{"editable": true}')
        editor.moveCursor(QTextCursor.MoveOperation.End)
        editor.insertPlainText('\n')
        editor.undo()
        assert editor.toPlainText() == '{"editable": true}'
    finally:
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_frozen_context_validation_commits_authenticated_frame_without_clock(app, tmp_path, monkeypatch):
    from pathlib import Path
    from quantum_autopilot.console import text_scroll_validation as validation
    from quantum_autopilot.console.service_provider import ServiceProvider
    from quantum_autopilot.console.widgets.policy_context import PolicyContextInspection

    refreshed, admitted, stopped = [], [], []
    refresh, load, stop = ServiceProvider.refresh, PolicyContextInspection.load, ServiceProvider.stop

    def refresh_checked(self):
        assert self.security.has_identity_scope()
        assert self.security.current_identity().subject == self._identity.subject
        assert self._stepper is None and self.steps_taken == 0
        refresh(self)
        refreshed.append(self._snap)

    def load_checked(self):
        assert self.provider._snap is not None
        assert self._presented_source == (id(self.provider.active), self.provider._generation)
        assert self.load_button.isEnabled()
        admitted.append(self.provider._snap.capabilities.allows('observe_policy_context'))
        load(self)

    def stop_checked(self):
        assert self._stepper is None and self.steps_taken == 0
        stop(self)
        stopped.append(not self.stepping and self._executor is None)

    def forbidden_start(self):
        pytest.fail('The read-only context fixture must not start the simulation clock')

    monkeypatch.setattr(ServiceProvider, 'refresh', refresh_checked)
    monkeypatch.setattr(ServiceProvider, 'start', forbidden_start)
    monkeypatch.setattr(ServiceProvider, 'stop', stop_checked)
    monkeypatch.setattr(PolicyContextInspection, 'load', load_checked)
    report = validation.validate_context_receipt(Path(__file__).resolve().parents[1], tmp_path / 'native-context')
    assert report['passed'] and all(report['checks'].values())
    assert len(refreshed) == 1 and admitted == [True] and stopped == [True]
    assert report['checks']['private_inspection_keeps_journal']
    assert report['checks']['captured_export_equals_observation']


def test_frozen_context_validation_releases_provider_when_authenticated_refresh_fails(app, tmp_path, monkeypatch):
    from pathlib import Path
    from quantum_autopilot.console import text_scroll_validation as validation
    from quantum_autopilot.console.service_provider import ServiceProvider

    stopped = []
    stop = ServiceProvider.stop

    def refused(self):
        assert self.security.has_identity_scope()
        raise PermissionError('fresh authenticated read refused')

    def stop_checked(self):
        stop(self)
        stopped.append(not self.stepping and self._executor is None)

    monkeypatch.setattr(ServiceProvider, 'refresh', refused)
    monkeypatch.setattr(ServiceProvider, 'stop', stop_checked)
    with pytest.raises(PermissionError, match='fresh authenticated read refused'):
        validation.validate_context_receipt(Path(__file__).resolve().parents[1], tmp_path / 'refused-context')
    assert stopped == [True]
