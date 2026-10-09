"""Terminal use cases in the rendered workspace, also available in the exe."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def settle() -> None:
    for _ in range(5):
        QApplication.processEvents()


def drag_terminal(window, delta: int) -> None:
    """Send pointer events through the scaled viewport to its splitter handle.

    These are application Qt events, not input on the operator's desktop.
    Mapping through the scene exercises proxy coordinates and user zoom.
    """
    stage = window.centralWidget()
    handle = window.terminal_workspace.handle(1)
    scene = stage.proxy.mapToScene(QPointF(handle.mapTo(window.canvas, handle.rect().center())))
    start = stage.mapFromScene(scene)
    for kind, local, button, buttons in [
        (QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
        *((QEvent.Type.MouseMove, start + QPoint(0, round(delta * step / 8)),
           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton) for step in range(1, 9)),
        (QEvent.Type.MouseButtonRelease, start + QPoint(0, delta), Qt.MouseButton.LeftButton,
         Qt.MouseButton.NoButton),
    ]:
        global_point = stage.viewport().mapToGlobal(local)
        QApplication.sendEvent(stage.viewport(), QMouseEvent(kind, QPointF(local), QPointF(global_point),
            button, buttons, Qt.KeyboardModifier.NoModifier))
        settle()


def validate_terminal(window, destination) -> dict:
    """Close, reopen, resize and navigate without submitting backend operations."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    state = window._capture_state()
    geometry = window.geometry()
    checks = {}
    try:
        window.show_screen("topology")
        window.resize(1280, 820)
        window.set_terminal_visible(True)
        settle()
        workspace, panel = window.terminal_workspace, window.journal
        panel.prompt.setText("help")
        rows, history = panel.rows(), list(panel.prompt.history)
        original = panel.height()
        screen_height = window.screen_stack.height()
        drag_terminal(window, -80)
        checks["viewport_drag_expands_terminal"] = panel.height() > original + 30
        checks["screen_shrinks_after_drag"] = window.screen_stack.height() < screen_height
        expanded = panel.height()
        drag_terminal(window, 60)
        checks["viewport_drag_reduces_terminal"] = panel.height() < expanded - 20
        wanted = workspace.preferred_height
        window.grab().save(str(destination / "terminal-resized.png"))
        occupied = window.screen_stack.height()
        QTest.mouseClick(panel.close_button, Qt.MouseButton.LeftButton)
        settle()
        checks["close_hides_panel_and_divider"] = panel.isHidden() and not workspace.handle(1).isVisible()
        checks["close_frees_entire_space"] = window.screen_stack.height() == workspace.height() > occupied
        checks["reopen_control_stays_reachable"] = window.status.terminal_button.isVisible()
        window.grab().save(str(destination / "terminal-closed.png"))
        QTest.mouseClick(window.status.terminal_button, Qt.MouseButton.LeftButton)
        settle()
        checks["button_reopens_same_panel"] = window.journal is panel and panel.isVisible()
        checks["reopening_retains_size"] = abs(panel.height() - wanted) <= 2
        checks["draft_and_history_retained"] = panel.prompt.text() == "help" and panel.prompt.history == history
        checks["records_retained"] = panel.rows() == rows
        for page in window.screens:
            window.show_screen(page)
            settle()
            before = window.screen_stack.height()
            window.set_terminal_visible(False)
            settle()
            checks[f"close_reflows_{page}"] = window.screen_stack.height() > before
            window.set_terminal_visible(True)
            settle()
            checks[f"reopen_reflows_{page}"] = window.screen_stack.height() == before
        handle = workspace.handle(1)
        before = panel.height()
        QTest.keyClick(handle, Qt.Key.Key_Up)
        settle()
        checks["keyboard_expands_terminal"] = panel.height() > before
        QTest.keyClick(handle, Qt.Key.Key_Down)
        settle()
        checks["keyboard_reduces_terminal"] = panel.height() == before
        checks["controls_have_help"] = all(widget.toolTip() and widget.accessibleName()
            for widget in (handle, panel.close_button, window.status.terminal_button))
        checks["no_commands_or_records_created"] = panel.rows() == rows and panel.prompt.history == history
        checks["physical_controls_remain_locked"] = not window.screens["fpga"].flash.isEnabled()
        window.grab().save(str(destination / "terminal-reopened.png"))
    finally:
        window.setGeometry(geometry)
        window._restore_state(state)
        settle()
    report = {"passed": all(checks.values()), "checks": checks, "check_count": len(checks),
              "qt_platform": QApplication.platformName(), "physical_action_performed": False,
              "desktop_input_verified": False,
              "scope": "Scaled viewport Qt pointer events; no backend operations submitted"}
    (destination / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


__all__ = ["drag_terminal", "validate_terminal"]
