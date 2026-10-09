"""Operator workflows for the terminal and the space shared by all screens."""
import os
from dataclasses import replace
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console import theme
from quantum_autopilot.console.contract import LedgerEntry
from quantum_autopilot.console.shell import ConsoleWindow
from quantum_autopilot.console.synthetic import SyntheticProvider
from quantum_autopilot.console.terminal_validation import drag_terminal, validate_terminal

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.set_theme("dark")
    theme.install(instance)
    return instance


def settle(app):
    for _ in range(5):
        app.processEvents()


@pytest.fixture
def window(app, tmp_path):
    provider = SyntheticProvider(ROOT, tmp_path / "fixture")
    win = ConsoleWindow(provider, skip_auth=True, state_root=tmp_path)
    win._on_snapshot(provider.snapshot())
    win.resize(1280, 820)
    win.show()
    settle(app)
    yield win
    win._busy = False
    win.close()
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    theme.set_theme("dark")
    theme.install(app)
    settle(app)


@pytest.mark.parametrize("page", ["topology", "telemetry", "keys", "node", "fpga", "audit"])
def test_close_and_reopen_reflow_each_screen_without_recreating_it(window, app, page):
    window.show_screen(page)
    settle(app)
    screen, panel = window.current_widget(), window.journal
    before = window.screen_stack.height()
    QTest.mouseClick(panel.close_button, Qt.MouseButton.LeftButton)
    settle(app)
    assert panel.isHidden() and not window.terminal_workspace.handle(1).isVisible()
    assert window.screen_stack.height() == window.terminal_workspace.height() > before
    assert window.status.terminal_button.isVisible() and not window.status.terminal_button.isChecked()
    QTest.mouseClick(window.status.terminal_button, Qt.MouseButton.LeftButton)
    settle(app)
    assert panel.isVisible() and window.screen_stack.height() == before
    assert window.current_widget() is screen and window.journal is panel
    assert window.status.terminal_button.isChecked()


@pytest.mark.parametrize("size,zoom", [((1024, 600), 1), ((1280, 820), 1), ((1920, 1080), 1),
                                       ((3440, 1440), 1), ((1280, 820), 1.4)])
def test_drag_through_scaled_viewport_moves_divider_and_reflows_screen(window, app, size, zoom):
    window.resize(*size)
    stage = window.centralWidget()
    stage.user_zoom = zoom
    stage._arrange()
    window.terminal_workspace.set_terminal_height(160)
    settle(app)
    before = window.journal.height()
    screen_height = window.screen_stack.height()
    drag_terminal(window, -60)
    assert window.journal.height() > before + 15
    assert window.screen_stack.height() < screen_height
    expanded = window.journal.height()
    drag_terminal(window, 45)
    assert window.journal.height() < expanded - 10
    assert window.journal.scroll.height() >= 32
    assert window.journal.rect().contains(window.journal.prompt_row.geometry().translated(window.journal.body.pos()))
    assert window.screen_stack.width() == window.terminal_workspace.width() - window.rail_host.width()
    assert window.workspace.rect().contains(window.status.geometry())
    assert stage.scene().mouseGrabberItem() is None


def test_draft_history_records_and_scroll_position_survive_hiding(window, app):
    snap = window.snapshot
    entries = [LedgerEntry(f"00:00:{i:02}", "fixture", f"Record {i}", "test", "ok") for i in range(60)]
    window._on_snapshot(replace(snap, ledger=replace(snap.ledger, entries=entries, commands=[])))
    window.run_terminal("help")
    window.journal.prompt.remember("help")
    window.journal.prompt.setText("select_node ")
    window.terminal_workspace.set_terminal_height(310)
    settle(app)
    panel = window.journal
    bar = panel.scroll.verticalScrollBar()
    bar.setValue(bar.maximum() // 3)
    expected = (panel.rows(), panel.feedback(), panel.prompt.text(), panel.prompt.history[:], bar.value())
    for _ in range(3):
        panel.close_button.click()
        settle(app)
        window.status.terminal_button.click()
        settle(app)
        assert expected == (panel.rows(), panel.feedback(), panel.prompt.text(), panel.prompt.history[:], bar.value())
        assert panel.height() == 310


def test_ctrl_j_closes_from_prompt_and_reopens_from_status(window, app):
    window.activateWindow()
    window.journal.prompt.setFocus()
    settle(app)
    QTest.keyClick(window.journal.prompt, Qt.Key.Key_J, Qt.KeyboardModifier.ControlModifier)
    settle(app)
    assert window.journal.isHidden()
    QTest.keyClick(window.status.terminal_button, Qt.Key.Key_J, Qt.KeyboardModifier.ControlModifier)
    settle(app)
    assert window.journal.isVisible()
    window.journal.prompt.setText("go audit")
    QTest.keyClick(window.journal.prompt, Qt.Key.Key_Return)
    assert window.current_screen == "audit" and window.journal.prompt.history[-1] == "go audit"


def test_resize_keyboard_and_reset_keep_controls_accessible(window, app):
    handle = window.terminal_workspace.handle(1)
    before = window.journal.height()
    QTest.keyClick(handle, Qt.Key.Key_Up)
    settle(app)
    assert window.journal.height() == before + 12
    QTest.keyClick(handle, Qt.Key.Key_Down)
    settle(app)
    assert window.journal.height() == before
    QTest.keyClick(handle, Qt.Key.Key_Up, Qt.KeyboardModifier.ShiftModifier)
    settle(app)
    assert window.journal.height() == before + 40
    QTest.keyClick(handle, Qt.Key.Key_Home)
    settle(app)
    assert window.journal.height() == before
    drag_terminal(window, -80)
    QTest.mouseDClick(handle, Qt.MouseButton.LeftButton)
    settle(app)
    assert window.journal.height() == before
    assert all(widget.toolTip() and widget.accessibleName() for widget in
               (handle, window.journal.close_button, window.status.terminal_button))


def test_collapsing_and_window_resize_retain_expanded_height(window, app):
    workspace = window.terminal_workspace
    workspace.set_terminal_height(360)
    settle(app)
    window.journal.toggle.click()
    settle(app)
    assert window.journal.height() == window.journal.head.height()
    assert not workspace.handle(1).isEnabled()
    QTest.keyClick(workspace.handle(1), Qt.Key.Key_Up)
    assert workspace.preferred_height == 360
    window.journal.toggle.click()
    settle(app)
    assert window.journal.height() == 360 and workspace.handle(1).isEnabled()
    window.resize(1024, 540)
    settle(app)
    assert window.journal.height() < 360 and window.screen_stack.height() >= 160
    assert workspace.preferred_height == 360
    window.resize(1280, 820)
    settle(app)
    assert window.journal.height() == 360


@pytest.mark.parametrize("mode", ["open", "collapsed", "closed"])
def test_terminal_state_survives_theme_rebuild(window, app, mode):
    window.terminal_workspace.set_terminal_height(290)
    window.journal.prompt.setText("select_node ")
    window.journal.prompt.remember("help")
    if mode == "collapsed":
        window.journal.set_open(False)
    elif mode == "closed":
        window.set_terminal_visible(False)
    settle(app)
    for name in ("light", "dark"):
        assert window.set_theme(name)
        settle(app)
        assert window.terminal_workspace.terminal_visible == (mode != "closed")
        assert window.journal.is_open == (mode != "collapsed")
        assert window.terminal_workspace.preferred_height == 290
        assert window.journal.prompt.text() == "select_node " and window.journal.prompt.history == ["help"]
        if mode == "open":
            assert window.journal.height() == 290
    window.set_terminal_visible(True)
    settle(app)
    assert window.journal.height() == 290


def test_navigation_stays_accessible_when_terminal_uses_most_of_window(window, app):
    window.terminal_workspace.set_terminal_height(2000)
    settle(app)
    assert window.screen_stack.height() >= 160
    rail_scroll = window.rail_host.verticalScrollBar()
    assert rail_scroll.maximum() > 0
    window.rail_host.ensureWidgetVisible(window.rail.sign_out)
    settle(app)
    assert window.rail_host.viewport().rect().contains(
        window.rail.sign_out.mapTo(window.rail_host.viewport(), window.rail.sign_out.rect().center()))
    QTest.mouseClick(window.rail.sign_out, Qt.MouseButton.LeftButton)
    assert window.current_screen == "boot"
    window.show_screen("topology")
    assert window.journal.isVisible()


def test_theme_rebuild_keeps_the_record_being_read(window, app):
    snap = window.snapshot
    entries = [LedgerEntry(f"00:00:{i:02}", "fixture", f"Record {i}", "test", "ok") for i in range(60)]
    window._on_snapshot(replace(snap, ledger=replace(snap.ledger, entries=entries, commands=[])))
    settle(app)
    bar = window.journal.scroll.verticalScrollBar()
    bar.setValue(bar.maximum() // 3)
    value = bar.value()
    for name in ("light", "dark"):
        assert window.set_theme(name)
        settle(app)
        assert window.journal.scroll.verticalScrollBar().value() == value
        assert not window.journal._stick


def test_hiding_while_busy_does_not_cancel_or_stop_source(window, app, monkeypatch):
    stopped = []
    monkeypatch.setattr(window.provider.active, "stop", lambda: stopped.append(True))
    window._on_busy(True)
    window.journal.close_button.click()
    settle(app)
    assert window.journal.isHidden() and window._busy and stopped == []
    window.status.terminal_button.click()
    assert window.journal.isVisible() and window._busy and stopped == []
    window._on_busy(False)


def test_real_backend_keeps_publishing_while_terminal_is_hidden(app, tmp_path):
    from quantum_autopilot.console.service_provider import ServiceProvider
    from quantum_autopilot.qkd.dashboard_api import demo_service
    service = demo_service(ROOT, tmp_path / "backend", preset="brazil-institutions-v1", scenario="quiet")
    provider = ServiceProvider(service, state_root=tmp_path / "provider", poll_ms=600000)
    provider.refresh()
    win = ConsoleWindow(provider, skip_auth=True, state_root=tmp_path)
    win._on_snapshot(provider.snapshot())
    win.resize(1280, 820)
    win.show()
    settle(app)
    try:
        win.set_terminal_visible(False)
        previous = win.snapshot.live.at
        for _ in range(4):
            service.network_step()
        provider.refresh()
        settle(app)
        assert win.journal.isHidden() and win.snapshot.live.at != previous
        assert win.journal.rows()
        win.set_terminal_visible(True)
        settle(app)
        net=win.screens['topology']._net
        expected={l.id for l in net.links if all(net.node(n).physical_type!='satellite' for n in (l.a,l.b))}
        expected.update(l['link_id'] for s in net.satellites.values() for l in s['transmitting_links'])
        assert len(net.nodes)==66 and win.screens['topology'].geo.drawn_link_keys()==expected
        assert not win.screens["fpga"].flash.isEnabled()
        assert service.network_live_state()["policy"]["ml_control_enabled"] is False
        report = validate_terminal(win, tmp_path / "live-terminal")
        assert report["passed"], report
    finally:
        win.close()
        win.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        settle(app)


def test_executable_terminal_validator(window, tmp_path):
    report = validate_terminal(window, tmp_path / "terminal")
    assert report["passed"], report
    assert report["check_count"] >= 26
    assert report["desktop_input_verified"] is False and report["physical_action_performed"] is False


def test_narrow_labels_keep_complete_text_and_stable_layout_hints(app):
    from quantum_autopilot.console.widgets.prims import L
    from quantum_autopilot.console.theme import mono
    label = L("Logical geometry", mono(12), elide=True)
    label.show()
    settle(app)
    hint = label.sizeHint()
    try:
        for width in [72, 73, 72, 1, 180, 73] * 12:
            label.resize(width, 24)
            settle(app)
            assert label.sizeHint() == hint
            assert label.text() == label.full_text == label.toolTip() == "Logical geometry"
            assert not label.grab().isNull()
    finally:
        label.close()
        label.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
