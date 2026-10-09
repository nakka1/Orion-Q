"""Work areas, monitor transitions, readable reflow and native pixel density."""
from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
import weakref

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QObject, QRect, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QAbstractButton, QDialog, QLineEdit, QMainWindow, QPushButton, QWidget  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.display import (  # noqa: E402
    DisplayObserver, fit_to_screen, prepare_main_window, raster_scale_for, show_main_window,
)
from quantum_autopilot.console.stage import ResponsiveStage  # noqa: E402
from quantum_autopilot.console.widgets import Card, L, Prose, vbox  # noqa: E402
from quantum_autopilot.console.widgets.adaptive_layout import AdaptiveGrid, AdaptiveRow  # noqa: E402


def settle(app):
    for _ in range(8):
        app.processEvents()


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    theme.set_theme("dark")
    theme.install(instance)
    return instance


class Monitor(QObject):
    availableGeometryChanged = Signal(QRect)
    geometryChanged = Signal(QRect)
    logicalDotsPerInchChanged = Signal(float)

    def __init__(self, area):
        super().__init__()
        self.area = area

    def availableGeometry(self):  # noqa: N802
        return self.area


def test_launch_geometry_uses_the_selected_real_qt_screen(app):
    window = QMainWindow()
    selected = app.primaryScreen()
    prepare_main_window(window, selected)
    window.show()
    settle(app)
    try:
        assert window.screen() is selected
        assert selected.availableGeometry().adjusted(16, 40, -16, -16).contains(window.frameGeometry())
    finally:
        window.close()


@pytest.mark.parametrize("fullscreen", [False, True])
def test_main_window_launches_restored_or_explicit_fullscreen(app, fullscreen):
    window = QMainWindow()
    show_main_window(window, fullscreen=fullscreen)
    settle(app)
    try:
        assert window.isFullScreen() if fullscreen else not (window.isFullScreen() or window.isMaximized())
    finally:
        window.close()


@pytest.mark.parametrize("area", [QRect(0, 0, 1366, 728), QRect(-1920, -120, 1920, 1040), QRect(3840, 0, 1280, 680)])
def test_fitting_includes_native_frame_and_monitor_origin(app, area):
    window = QMainWindow()
    window.resize(2500, 1800)
    window.move(10000, 10000)
    window.show()
    settle(app)
    try:
        fit_to_screen(window, Monitor(area))
        settle(app)
        assert area.adjusted(16, 40, -16, -16).contains(window.frameGeometry())
    finally:
        window.close()


def test_monitor_and_dpi_changes_preserve_controls_and_input(app):
    window = QMainWindow()
    content = QWidget()
    field = QLineEdit("unsent operator input")
    vbox(content).addWidget(field)
    stage = ResponsiveStage(content)
    window.setCentralWidget(stage)
    observer = DisplayObserver(window, fit=True)
    window.resize(1400, 900)
    window.show()
    settle(app)
    first = Monitor(QRect(0, 0, 1920, 1040))
    second = Monitor(QRect(-1280, 0, 1280, 680))
    try:
        observer.bind_screen(first)
        settle(app)
        observer.bind_screen(second)
        second.logicalDotsPerInchChanged.emit(144.0)
        settle(app)
        assert window.centralWidget() is stage and stage.content is content
        assert field.text() == "unsent operator input"
        assert second.area.adjusted(16, 40, -16, -16).contains(window.frameGeometry())
        second.area = QRect(-1280, 0, 1280, 620)  # Work area changes, e.g. taskbar moves.
        second.availableGeometryChanged.emit(second.area)
        settle(app)
        assert second.area.adjusted(16, 40, -16, -16).contains(window.frameGeometry())
    finally:
        window.close()


def test_display_observer_does_not_retain_closed_windows(app):
    window = QMainWindow()
    observer = DisplayObserver(window)
    reference = weakref.ref(window)
    window.show()
    settle(app)
    window.close()
    del window
    gc.collect()
    assert reference() is None
    assert observer.window is None
    observer.schedule()
    observer.synchronize()


def test_restored_console_fits_a_smaller_monitor_without_losing_drafts(app):
    from quantum_autopilot.console.providers import MockProvider
    from quantum_autopilot.console.shell import ConsoleWindow

    provider = MockProvider()
    window = ConsoleWindow(provider, skip_auth=True)
    window._on_snapshot(provider.snapshot())
    window.show_screen("node")
    field = window.screens["node"].fields["id"]
    field.setText("N-DRAFT")
    screens = dict(window.screens)
    window.resize(2400, 1500)
    window.show()
    settle(app)
    try:
        for area in (QRect(-1280, 0, 1280, 680), QRect(0, 0, 640, 360)):
            monitor = Monitor(area)
            window._display_observer.bind_screen(monitor)
            settle(app)
            assert area.adjusted(16, 40, -16, -16).contains(window.frameGeometry())
            assert field.text() == "N-DRAFT" and window.screens == screens
        assert window.centralWidget().horizontalScrollBar().maximum() > 0
        assert window.centralWidget().verticalScrollBar().maximum() > 0
    finally:
        window.close()


@pytest.mark.parametrize("size", [(960, 540), (1280, 720), (1920, 1080), (2560, 1440), (3440, 1440), (3840, 2160), (5120, 2160)])
def test_stage_fills_monitor_without_distortion_and_map_density_tracks_zoom(app, size):
    content = QWidget()
    stage = ResponsiveStage(content)
    stage.resize(*size)
    stage.show()
    settle(app)
    try:
        scale = stage.transform().m11()
        assert scale == stage.transform().m22() and scale >= 1
        assert stage.sceneRect().width() * scale >= stage.viewport().width()
        assert stage.sceneRect().height() * scale >= stage.viewport().height()
        assert stage.verticalScrollBar().maximum() == 0
        assert stage.horizontalScrollBar().maximum() == 0
        assert raster_scale_for(content) == pytest.approx(stage.viewport().devicePixelRatioF() * scale)
        stage.change_zoom(.5)
        settle(app)
        assert raster_scale_for(content) > stage.viewport().devicePixelRatioF() * scale
        stage.reset_zoom()
        settle(app)
        assert stage.transform().m11() == pytest.approx(scale)
    finally:
        stage.close()


def test_card_reflow_preserves_widgets_and_their_actions(app):
    cards, fields, buttons = [], [], []
    clicks = []
    for index in range(3):
        card = Card()
        field = QLineEdit(f"draft-{index}")
        button = QPushButton(f"Action {index}")
        button.clicked.connect(lambda checked=False, i=index: clicks.append(i))
        card.add(Prose("Readable contents that wrap in a narrow card.", 14))
        card.add(field)
        card.add(button)
        cards.append(card)
        fields.append(field)
        buttons.append(button)
    grid = AdaptiveGrid(cards)
    grid.show()
    try:
        for width, columns in ((1100, 3), (600, 2), (300, 1), (1100, 3)):
            grid.resize(width, grid.heightForWidth(width))
            settle(app)
            assert grid._columns == columns
            for card, field, button in zip(cards, fields, buttons):
                assert card.parentWidget() is grid
                assert 0 <= card.x() <= width - card.width()
                assert button.isVisible() and field.text().startswith("draft-")
            for button in buttons:
                button.click()
        assert clicks == [0, 1, 2] * 4
    finally:
        grid.close()


def test_action_row_stacks_instead_of_clipping_controls(app):
    buttons = [QPushButton("A long operator action") for _ in range(3)]
    row = AdaptiveRow([L("Controls"), *buttons])
    row.show()
    try:
        for width in (1100, 260, 1100):
            row.resize(width, row.heightForWidth(width))
            settle(app)
            for button in buttons:
                assert 0 <= button.x() <= width - button.width()
                assert button.y() + button.height() <= row.height()
    finally:
        row.close()


@pytest.mark.parametrize("factor", [1.0, 1.25, 1.5, 2.0])
def test_fractional_dpi_uses_native_qt_pixels_in_separate_processes(factor):
    code = '''
import json
from PySide6.QtWidgets import QApplication, QWidget
from quantum_autopilot.console.display import configure_high_dpi, raster_scale_for
from quantum_autopilot.console.stage import ResponsiveStage
configure_high_dpi()
app = QApplication([])
content = QWidget()
stage = ResponsiveStage(content)
stage.resize(1280, 720)
stage.show()
for _ in range(8): app.processEvents()
print(json.dumps({"dpr": stage.viewport().devicePixelRatioF(), "raster": raster_scale_for(content),
                  "zoom": stage.transform().m11(), "pixels": stage.grab().width()}))
stage.close()
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=str(factor))
    process = subprocess.run([sys.executable, "-B", "-c", code], env=env, text=True, capture_output=True, timeout=30)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["dpr"] == pytest.approx(factor)
    assert result["zoom"] == 1.0  # DPI is not applied as a second presentation transform.
    assert result["raster"] == pytest.approx(factor)
    assert result["pixels"] == round(1280 * factor)


@pytest.mark.parametrize("mode", ["dark", "light"])
@pytest.mark.parametrize("size", [(960, 540), (1280, 720), (3840, 2160)])
def test_loading_view_fits_compact_and_large_windows(app, mode, size, tmp_path):
    from quantum_autopilot.console.screens import SplashScreen

    theme.set_theme(mode)
    theme.install(app)
    stage = ResponsiveStage(SplashScreen())
    stage.resize(*size)
    stage.show()
    settle(app)
    try:
        assert stage.verticalScrollBar().maximum() == 0
        assert stage.horizontalScrollBar().maximum() == 0
        assert stage.content.isVisible()
        stage.grab().save(str(tmp_path / f'{mode}-{size[0]}x{size[1]}-loading.png'))
    finally:
        stage.close()
        theme.set_theme("dark")
        theme.install(app)


@pytest.mark.parametrize("mode", ["dark", "light"])
@pytest.mark.parametrize("size", [(960, 540), (1280, 720), (3840, 2160)])
def test_all_pages_and_palette_keep_controls_inside_their_containers(app, mode, size, tmp_path):
    from quantum_autopilot.console.providers import MockProvider
    from quantum_autopilot.console.shell import ConsoleWindow

    theme.set_theme(mode)
    theme.install(app)
    provider = MockProvider()
    window = ConsoleWindow(provider, skip_auth=True)
    window._on_snapshot(provider.snapshot())
    window.resize(*size)
    window.show()
    try:
        for page in ("boot", *window.screens):
            window.show_screen(page)
            settle(app)
            screen = window.boot if page == "boot" else window.screens[page]
            for button in screen.findChildren(QAbstractButton):
                if not button.isVisible():
                    continue
                ancestor = button.parentWidget()
                in_dialog = False
                while ancestor is not None:
                    in_dialog |= isinstance(ancestor, QDialog)
                    ancestor = ancestor.parentWidget()
                if in_dialog:
                    continue
                parent = button.parentWidget()
                assert parent.rect().adjusted(-1, -1, 1, 1).contains(button.geometry()), (
                    page, button.text(), button.geometry(), parent.rect(), type(parent).__name__)
            window.grab().save(str(tmp_path / f'{mode}-{size[0]}x{size[1]}-{page}.png'))
            if page != "boot":
                stage = window.centralWidget()
                assert stage.verticalScrollBar().maximum() == 0
                assert window.status.isVisible() and window.topbar.isVisible()
        window.open_palette()
        settle(app)
        assert window.palette.rect().contains(window.palette.panel.geometry())
        window.close_palette()
    finally:
        window.close()
        theme.set_theme("dark")
        theme.install(app)
