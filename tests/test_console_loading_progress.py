"""Loading feedback follows completed work and has a bounded visible lifetime."""
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console import theme
from quantum_autopilot.console.contract import Link, Node
from quantum_autopilot.console.screens.splash import SplashScreen
from quantum_autopilot.console.widgets.inspector import CommandStatus
from quantum_autopilot.console.widgets.loading_state import LoadingStatus, SegmentedProgress
from quantum_autopilot.console.widgets.network_views import build_net_model
from quantum_autopilot.console.widgets.node_window import NodeWindow
from quantum_autopilot.product import PRODUCT_NAME, product_version


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    theme.set_theme("dark")
    theme.install(instance)
    yield instance
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def close_widget(widget):
    widget.close()
    widget.deleteLater()


def test_waiting_pulse_never_advances_progress_or_finishes(app):
    status = LoadingStatus()
    status.resize(560, 72)
    done = []
    status.finished.connect(lambda: done.append(True))
    status.start("Waiting for backend", total=5)
    status.show()
    try:
        QTest.qWait(280)
        assert status.progress._pulse_timer.isActive()
        assert status.progress.fraction == status.progress.displayed_fraction == 0
        assert status.progress.completed == 0 and status.count_label.text() == "0 / 5"
        assert status.percent.text() == "WAIT" and not done
    finally:
        close_widget(status)


def test_only_reported_steps_are_interpolated_and_owner_confirms_ready(app):
    status = LoadingStatus()
    status.resize(560, 72)
    status.show()
    done = []
    status.finished.connect(lambda: done.append(True))
    try:
        status.start(total=5)
        status.set_progress(2, 5, "Loading simulator")
        assert status.progress.fraction == 0.4
        assert 0 <= status.progress.displayed_fraction <= 0.4
        assert status.percent.text() == "40%" and status.count_label.text() == "2 / 5"
        QTest.qWait(260)
        assert status.progress.displayed_fraction == 0.4
        assert status.progress._animation.state() == QAbstractAnimation.State.Stopped
        QTest.qWait(180)
        assert status.progress.displayed_fraction == 0.4 and not done
        status.set_progress(5, 5, "Publishing initial snapshot")
        assert status.percent.text() == "100%" and not done
        status.complete()
        status.complete()
        assert status.percent.text() == "READY" and done == [True]
        assert not status.progress._pulse_timer.isActive()
        assert status.progress.displayed_fraction == 1
    finally:
        close_widget(status)


def test_cancel_failure_restart_and_hide_stop_old_animation(app):
    status = LoadingStatus()
    status.resize(560, 72)
    status.show()
    try:
        status.start(total=5)
        status.set_progress(3, 5)
        status.abort("Provider disconnected; retry available")
        assert status.state == "error" and status.percent.text() == "ERROR"
        assert status.progress.completed == 3 and status.progress.displayed_fraction == 0.6
        assert not status.progress._pulse_timer.isActive()
        assert status.progress._animation.state() == QAbstractAnimation.State.Stopped
        status.start("Retrying provider", total=5)
        assert status.progress.displayed_fraction == status.progress.fraction == 0
        status.set_progress(1, 5)
        status.hide()
        assert not status.progress._pulse_timer.isActive()
        assert status.progress._animation.state() == QAbstractAnimation.State.Stopped
        assert status.progress.displayed_fraction == 0.2
        status.show()
        assert status.progress._pulse_timer.isActive()
        status.dispose()
        assert not status.progress._pulse_timer.isActive()
        status.hide()
        status.show()
        assert not status.progress._pulse_timer.isActive()
    finally:
        close_widget(status)


@pytest.mark.parametrize("completed,total", [(0, 0), (0, -1), (-1, 5), (6, 5),
                                             (float("nan"), 5), (1, float("inf")), (0, 2.5)])
def test_invalid_reports_cannot_create_completion(app, completed, total):
    status = LoadingStatus()
    try:
        with pytest.raises(ValueError):
            status.set_progress(completed, total)
        assert status.progress.fraction == 0 and status.state == "idle"
    finally:
        close_widget(status)


def test_native_five_segments_only_fill_reported_completed_area(app):
    progress = SegmentedProgress()
    progress.resize(504, 10)
    progress.show()
    try:
        progress.set_progress(2, 5, animate=False, indeterminate=False)
        app.processEvents()
        pixels = progress.grab().toImage()
        fill = theme.qc(theme.C.acc)
        width = (progress.width() - 24) / 5
        for index in range(5):
            color = pixels.pixelColor(int(index * (width + 6) + width / 2), 5)
            assert (color == fill) == (index < 2)
        assert pixels.pixelColor(int(width + 3), 5) != fill
    finally:
        close_widget(progress)


def test_splash_error_offers_retry_and_back_without_claiming_ready(app):
    screen = SplashScreen()
    screen.show()
    events = []
    screen.retry_requested.connect(lambda: events.append("retry"))
    screen.back_requested.connect(lambda: events.append("back"))
    screen.finished.connect(lambda: events.append("ready"))
    try:
        screen.start()
        assert screen.recovery_actions.isHidden()
        screen.set_progress(2, 5)
        screen.abort("Unable to load: local service unavailable")
        app.processEvents()
        assert screen.percent.text() == "ERROR" and screen.progress.fraction == 0.4
        assert screen.retry_button.isVisible() and screen.back_button.isVisible()
        screen.retry_button.click()
        screen.back_button.click()
        assert events == ["retry", "back"]
        screen.start()
        assert screen.recovery_actions.isHidden() and screen.percent.text() == "WAIT"
    finally:
        close_widget(screen)


def test_node_footer_tracks_current_generation_sections_and_connections(app):
    nodes = [Node("HUB", "REPEATER", "up", .5, .5), Node("PEER", "ENDPOINT", "up", .5, .5)]
    snap = SimpleNamespace(topology=SimpleNamespace(nodes=nodes,
                           links=[Link("HUB", "PEER", "FIBRE", id="FIBRE")]), live=None)
    window = NodeWindow("HUB", incremental=True)
    window.show()
    try:
        window.refresh(snap, build_net_model(snap), CommandStatus())
        window._presentation_timer.stop()
        report = window.presentation_status()
        assert not report["ready"]
        expected = len(report["expected_sections"]) + len(report["expected_link_keys"])
        completed = len(report["completed_sections"]) + len(report["completed_link_keys"])
        assert window.presentation_line is window.loading.message
        assert window.loading.progress.total == expected
        assert window.loading.progress.completed == completed
        assert window.loading_brand.accessibleName() == PRODUCT_NAME
        QTest.qWait(260)
        assert window.loading.progress.fraction == completed / expected
        assert window.loading.percent.text() != "READY"
        for _ in range(80):
            if window.presentation_status()["ready"]:
                break
            window._materialize_step()
        assert window.presentation_status()["ready"]
        assert window.loading.percent.text() == "READY"
        assert not window.loading.progress._pulse_timer.isActive()
        window.close_window()
        assert not window.loading.progress._pulse_timer.isActive()
    finally:
        close_widget(window)


@pytest.mark.parametrize("width,height", [(960, 480), (1440, 900), (3440, 1440)])
def test_sparse_splash_keeps_brand_build_and_step_message_inside(app, width, height):
    screen = SplashScreen()
    screen.resize(width, height)
    screen.show()
    try:
        screen.start()
        screen.set_progress(2, 5, "Loading control plane and simulator")
        app.processEvents()
        assert screen.brand.accessibleName() == PRODUCT_NAME
        assert screen.version_label.text() == "Version " + product_version()
        assert screen.build_label.text() == "BUILD " + product_version()
        assert "PHYSICAL ACTUATION LOCKED" in screen.lock_label.text()
        assert screen.message.text() == "Loading control plane and simulator"
        assert screen.percent.text() == "40%"
        for widget in (screen.brand, screen.version_label, screen.loading,
                       screen.build_label, screen.lock_label):
            assert screen.rect().contains(widget.mapTo(screen, QPoint()))
            assert screen.rect().contains(widget.mapTo(screen, widget.rect().bottomRight()))
        assert not screen.grab().isNull()
    finally:
        close_widget(screen)
