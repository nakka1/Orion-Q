"""Actual telemetry widgets must repaint after scrolling and keep headings readable."""
from dataclasses import replace
import json
import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from quantum_autopilot.console import theme
from quantum_autopilot.console.contract import LiveHistory, LiveLink, LiveView
from quantum_autopilot.console.capabilities import synthetic_capabilities
from quantum_autopilot.console.providers import MockProvider
from quantum_autopilot.console.screens.telemetry import TelemetryScreen
from quantum_autopilot.console.screens.keys import KeysScreen
from quantum_autopilot.console.screens.audit import AuditScreen
from quantum_autopilot.console.stage import ResponsiveStage
from quantum_autopilot.console.widgets import evidence_export
from quantum_autopilot.console.widgets.prims import vbox
from quantum_autopilot.console.widgets.records import RecordTable
from quantum_autopilot.console.widgets.satellite_telemetry import SatelliteTelemetryDialog


@pytest.fixture(scope='module')
def app():
    instance = QApplication.instance() or QApplication([])
    theme.install(instance)
    return instance


class PaintProbe(QObject):
    def __init__(self):
        super().__init__()
        self.rects = []

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Paint:
            self.rects.append(event.rect())
        return False


@pytest.mark.parametrize('mode', ['dark', 'light'])
@pytest.mark.parametrize('tab', ['housekeeping', 'science', 'mission', 'records'])
def test_table_scroll_repaints_full_viewport_without_hover(app, tab, mode):
    theme.set_theme(mode)
    theme.install(app)
    dialog = SatelliteTelemetryDialog()
    table = getattr(dialog, tab) if tab != 'records' else RecordTable(['FIELD', 'VALUE', 'UNIT', 'SOURCE'])
    table.setParent(None)
    host = QWidget()
    vbox(host, (0, 0, 0, 0), 0).addWidget(table)
    rows = [(f'Observation {index}', index + 0.123, 'RAW', 'isolated UI fixture') for index in range(120)]
    if tab == 'records':
        table.set_rows([[(str(value), 'ink') for value in row] for row in rows])
        texts = lambda: table.records.rows
    else:
        SatelliteTelemetryDialog._rows(table, rows, 'isolated UI fixture')
        texts = lambda: [[table.item(r, c).text() for c in range(4)] for r in range(table.rowCount())]
    stage = ResponsiveStage(host)
    stage.resize(1050, 700)
    table.show()
    stage.show()
    QTest.qWait(40)
    probe = PaintProbe()
    table.viewport().installEventFilter(probe)
    original = texts()
    try:
        assert table.viewport().testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        scrollbar = table.verticalScrollBar()
        assert scrollbar.maximum() > 10
        assert not table.viewport().testAttribute(Qt.WidgetAttribute.WA_UnderMouse)
        for method in ('bar', 'wheel', 'key'):
            scrollbar.setValue(0)
            table.setCurrentIndex(table.model().index(0, 0))
            QTest.qWait(20)
            probe.rects.clear()
            if method == 'bar':
                scrollbar.setValue(1)
            elif method == 'wheel':
                pos = table.viewport().rect().center()
                app.sendEvent(table.viewport(), QWheelEvent(QPointF(pos), QPointF(table.viewport().mapToGlobal(pos)),
                    QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                    Qt.ScrollPhase.NoScrollPhase, False))
            else:
                QTest.keyClick(table, Qt.Key.Key_PageDown)
            QTest.qWait(40)
            assert scrollbar.value() > 0, method
            assert any(rect.height() >= table.viewport().height() and rect.width() >= table.viewport().width()
                       for rect in probe.rects), (method, probe.rects)
            assert texts() == original
    finally:
        stage.close()
        stage.deleteLater()
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        theme.set_theme('dark')
        theme.install(app)


def live_chart_fixture():
    snap = MockProvider().snapshot()
    history = LiveHistory((1., 2., 3.), (.01, .02, .015), (.985, .97, .9775), (120., 95., 110.))
    links = tuple(LiveLink(f'FIBRE-{index}', 'A', 'B', 8., 'up', configured_active=True,
        fidelity=.9775, qber=.015, skr_bps=110., history=history, input='simulated',
        display_provenance='simulated') for index in range(3))
    live = LiveView(3, 3., 'simulated', 'isolated chart fixture', links=links)
    return replace(snap, live=live, capabilities=synthetic_capabilities())


@pytest.mark.parametrize('width', [600, 900, 1280, 1300, 1360, 1440, 1920])
def test_fidelity_heading_gets_its_full_available_width(app, width):
    screen = TelemetryScreen()
    snapshot = live_chart_fixture()
    screen.apply(snapshot)
    stage = ResponsiveStage(screen)
    stage.resize(width, 1000)
    stage.show()
    QTest.qWait(80)
    try:
        title = screen.window_note
        assert title.text() == 'Fidelity by link · Werner estimate'
        assert title.width() == screen.chart.width(), (title.geometry(), screen.chart.geometry())
        required = title.heightForWidth(title.width())
        assert title.height() >= required
        assert screen.chart.plotted() == [(link.id, list(zip(link.history.t_s, link.history.fidelity)))
                                           for link in snapshot.live.links]
    finally:
        stage.close()
        stage.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize('metric,heading,attribute', [
    ('fidelity', 'Fidelity by link · Werner estimate', 'fidelity'),
    ('qber', 'QBER by link', 'qber'), ('skr', 'Secret key rate by link', 'skr_bps')])
def test_chart_controls_preserve_received_points_and_series_selection(app, metric, heading, attribute):
    screen = TelemetryScreen()
    snap = live_chart_fixture()
    screen.apply(snap)
    screen.show()
    try:
        QTest.mouseClick(screen.metric_bar.buttons[metric], Qt.MouseButton.LeftButton)
        assert screen.window_note.text() == heading
        expected = [(link.id, list(zip(link.history.t_s, getattr(link.history, attribute)))) for link in snap.live.links]
        assert screen.chart.plotted() == expected
        QTest.mouseClick(screen.series_chips['b'], Qt.MouseButton.LeftButton)
        assert screen.chart.plotted() == [expected[0], expected[2]]
        QTest.mouseClick(screen.series_chips['b'], Qt.MouseButton.LeftButton)
        assert screen.chart.plotted() == expected
        QTest.mouseClick(screen.mode_bar.buttons['replay'], Qt.MouseButton.LeftButton)
        assert screen._chart_mode == 'replay'
        QTest.mouseClick(screen.mode_bar.buttons['live'], Qt.MouseButton.LeftButton)
        assert screen.chart.plotted() == expected
    finally:
        screen.close()
        screen.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize('factory,scope', [(KeysScreen, 'qkd'), (TelemetryScreen, 'telemetry'), (AuditScreen, 'campaign')])
@pytest.mark.parametrize('change', ['success', 'cancel', 'existing', 'extension', 'permission', 'source', 'busy', 'close'])
def test_export_buttons_choose_destination_and_respect_changed_context(app, tmp_path, monkeypatch, factory, scope, change):
    screen = factory()
    snap = live_chart_fixture()
    screen.apply(snap)
    screen.show()
    target = tmp_path / ('record.txt' if change == 'extension' else 'evidence.json')
    if change == 'existing':
        target.write_text('existing evidence', encoding='utf-8')
    commands, notices = [], []
    screen.command.connect(lambda *args: commands.append(args))
    screen.notify.connect(notices.append)
    def choose(*args, **kwargs):
        if change == 'permission':
            screen.apply(replace(snap, capabilities=replace(snap.capabilities, operations=())))
        elif change == 'source':
            screen.apply(replace(snap, campaign='changed-source'))
        elif change == 'busy':
            screen.commands_busy = True
        elif change == 'close':
            screen.close()
        return ('' if change == 'cancel' else str(target)), 'JSON'
    monkeypatch.setattr(evidence_export.QFileDialog, 'getSaveFileName', choose)
    try:
        QTest.mouseClick(screen.export_button, Qt.MouseButton.LeftButton)
        if change == 'success':
            assert commands == [('export_record', json.dumps({'scope': scope, 'destination': str(target)}, ensure_ascii=False))]
        else:
            assert not commands
        assert target.exists() == (change == 'existing')
        if change == 'existing':
            assert target.read_text(encoding='utf-8') == 'existing evidence'
            assert any('new filename' in notice for notice in notices)
    finally:
        screen.close()
        screen.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_key_export_click_writes_real_service_evidence_in_worker(provider, app, tmp_path, monkeypatch):
    screen = KeysScreen()
    screen.command.connect(provider.dispatch)
    target = tmp_path / 'selected-key-evidence.json'
    monkeypatch.setattr(evidence_export.QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(target), 'JSON'))
    try:
        provider.start()
        # Keep this explicit export test separate from periodic refresh jobs.
        provider._timer.stop()
        provider._live_timer.stop()
        deadline = time.monotonic() + 30
        while provider._future is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        assert provider._future is None and provider._snap is not None
        screen.apply(provider._snap)
        screen.show()
        QTest.mouseClick(screen.export_button, Qt.MouseButton.LeftButton)
        deadline = time.monotonic() + 30
        while provider._future is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        assert provider._future is None
        data = json.loads(target.read_text(encoding='utf-8'))
        assert data['scope'] == 'qkd'
        assert data['format'] == 'unsigned_console_evidence_export_v1'
        assert {'health', 'topology', 'events', 'qkd_example'} <= data.keys()
    finally:
        provider.stop()
        screen.close()
        screen.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


from tests.test_console_inventory import provider
