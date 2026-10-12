"""Real viewport geometry and complete telemetry text at narrow desktop sizes."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QTextLayout, QTextOption
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QScrollArea, QWidget

from quantum_autopilot.console import theme
from quantum_autopilot.console.widgets.node_window import NodeWindows
from quantum_autopilot.console.widgets.satellite_telemetry import SatelliteTelemetryDialog


pytestmark = pytest.mark.unit


@pytest.fixture(scope='module')
def app():
    instance = QApplication.instance() or QApplication([])
    theme.install(instance)
    return instance


def settle(app):
    for _ in range(6):
        app.processEvents()


def cleanup(app, widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settle(app)


@pytest.mark.parametrize('size', [(640, 480), (1280, 720), (3440, 1440)])
@pytest.mark.parametrize('scrolled', [False, True])
def test_floating_node_fits_visible_scroll_host_and_escape_still_closes(app, size, scrolled):
    # A large screen inside a short scroll host reproduces the shell with its
    # terminal open. It used to centre the complete frame on focus, hiding the
    # title even though that title was inside the outer Stage's bounds.
    viewport = QScrollArea()
    viewport.setWidgetResizable(True)
    viewport.resize(size[0], max(280, size[1] - 250))
    host = QWidget()
    host.setMinimumSize(size[0] - 100, size[1] + 250)
    viewport.setWidget(host)
    registry = NodeWindows(host)
    viewport.show()
    settle(app)
    if scrolled:
        viewport.verticalScrollBar().setValue(130)
        settle(app)
    try:
        window = registry.open('CIMATEC')
        settle(app)
        window.fit_host()
        settle(app)
        title = QRect(window.bar.mapTo(viewport.viewport(), QPoint()), window.bar.size())
        assert viewport.viewport().rect().contains(title), (viewport.viewport().rect(), title)
        assert window.height() <= viewport.viewport().height() - 16
        before = viewport.verticalScrollBar().value()
        window.close_button.setFocus()
        settle(app)
        assert viewport.verticalScrollBar().value() == before
        title = QRect(window.bar.mapTo(viewport.viewport(), QPoint()), window.bar.size())
        assert viewport.viewport().rect().contains(title)
        viewport.verticalScrollBar().setValue(min(viewport.verticalScrollBar().maximum(), before + 40))
        settle(app)
        title = QRect(window.bar.mapTo(viewport.viewport(), QPoint()), window.bar.size())
        assert viewport.viewport().rect().contains(title)
        # Moving to a position outside the host cannot lose the drag handle.
        window.move_within(QPoint(-500, -500))
        title = QRect(window.bar.mapTo(viewport.viewport(), QPoint()), window.bar.size())
        assert viewport.viewport().rect().contains(title)
        QTest.keyClick(window.close_button, Qt.Key.Key_Escape)
        settle(app)
        assert not registry.windows
    finally:
        registry.shutdown()
        cleanup(app, viewport)


@pytest.mark.parametrize('saved_point', [QPoint(422, 35), QPoint(9000, 5000)])
def test_hidden_restore_waits_for_displayed_viewport_before_clamping(app, saved_point):
    # Shell theme rebuilds recreate the screen while its ancestors still have
    # Qt's default 640x480 geometry. The saved point belongs to the displayed
    # viewport, so a provisional fit must not erase it before Show/layout.
    viewport = QScrollArea()
    viewport.setWidgetResizable(True)
    viewport.resize(640, 480)
    host = QWidget()
    host.setMinimumSize(900, 550)
    viewport.setWidget(host)
    registry = NodeWindows(host)
    saved = [{'id': 'CIMATEC', 'x': saved_point.x(), 'y': saved_point.y()}]
    try:
        registry.restore(saved)
        settle(app)
        assert not host.isVisible()
        assert registry.state() == saved
        assert registry._restore_positions == {'CIMATEC': saved_point}
        viewport.resize(1440, 900)
        viewport.show()
        settle(app)
        window = registry.windows['CIMATEC']
        title = QRect(window.bar.mapTo(viewport.viewport(), QPoint()), window.bar.size())
        assert viewport.viewport().rect().contains(title)
        if saved_point.x() == 422:
            assert window.pos() == saved_point
        else:
            assert window.pos() != saved_point
        assert not registry._restore_positions
        assert not registry._viewport_fit.isActive()
        window.move_within(QPoint(45, 40))
        viewport.resize(1450, 910)
        settle(app)
        assert window.pos() == QPoint(45, 40)
    finally:
        registry.shutdown()
        assert not registry._restore_positions
        assert not registry._viewport_fit.isActive()
        cleanup(app, viewport)


def test_close_discards_hidden_restore_position_before_reopening(app):
    host = QWidget()
    registry = NodeWindows(host)
    try:
        registry.restore([{'id': 'CIMATEC', 'x': 422, 'y': 35}])
        registry.close('CIMATEC')
        assert not registry._restore_positions
        host.resize(1440, 900)
        host.show()
        settle(app)
        window = registry.open('CIMATEC', position=QPoint(70, 65))
        settle(app)
        assert window.pos() == QPoint(70, 65)
        assert not registry._restore_positions
    finally:
        registry.shutdown()
        cleanup(app, host)


def telemetry():
    return {'observation_origin': 'simulated', 'observation_source_id': 'OGS-regression-fixture',
            'housekeeping': {'at_s': 12., 'valid_until_s': 20.,
                'at_utc': '2026-10-11T12:34:56.123456789+00:00',
                'ecef_km': [-42164.123456789, 12345.987654321, -0.123456789],
                'velocity_ecef_km_s': [0.000123456789, -7.123456789, 1.987654321],
                'source': 'Parameterized orbital observation received through the classical interface'},
            'finite_key': {'abort_reason': 'insufficient_parameter_sample'}, 'qber': .023,
            'coincidences': 123, 'sifted_bits': 12}


def wrapped_height(table, row, column):
    # Independent QTextLayout line construction checks that *every* character
    # fits its actual cell; model readback alone never established legibility.
    text = table.item(row, column).text()
    layout = QTextLayout(text, table.font())
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    characters = 0
    height = 0.
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max(1, table.columnWidth(column) - 12))
        characters += line.textLength()
        height += line.height()
    layout.endLayout()
    assert characters == len(text)
    return height


@pytest.mark.parametrize('mode', ['dark', 'light'])
@pytest.mark.parametrize('size', [(640, 480), (1100, 750), (3440, 1440)])
def test_housekeeping_utc_vectors_and_origins_wrap_without_elision(app, mode, size, tmp_path):
    theme.set_theme(mode)
    theme.install(app)
    dialog = SatelliteTelemetryDialog()
    dialog.resize(*size)
    metrics = telemetry()
    dialog.set_metrics(metrics, session_id='REGRESSION-SIMULATED', clock_s=13.)
    dialog.show()
    settle(app)
    table = dialog.housekeeping
    try:
        assert table.wordWrap()
        assert table.textElideMode() == Qt.TextElideMode.ElideNone
        assert table.columnWidth(1) >= 170
        viewport_bounds = QRect(table.viewport().mapTo(dialog, QPoint()), table.viewport().size())
        assert dialog.rect().contains(viewport_bounds)
        assert viewport_bounds.bottom() < dialog.fixed_footer[0].y()
        assert table.viewport().visibleRegion().boundingRect() == table.viewport().rect()
        indices = [row for row in range(table.rowCount()) if table.item(row, 0).text() in
                   ('Época observada UTC', 'Posição ECEF (X,Y,Z)', 'Velocidade ECEF (Vx,Vy,Vz)', 'Efemérides')]
        assert len(indices) == 4
        for row in indices:
            for column in (0, 1, 3):
                required = wrapped_height(table, row, column)
                assert table.rowHeight(row) >= required + 10, (row, column, required, table.rowHeight(row))
        dialog.grab().save(str(tmp_path / f'housekeeping-{mode}-{size[0]}.png'))
        # Received updates retain values and scroll position without mouse
        # hover, selecting text or requesting a manual repaint.
        table.verticalScrollBar().setValue(table.verticalScrollBar().maximum())
        settle(app)
        old_scroll = table.verticalScrollBar().value()
        changed = copy.deepcopy(metrics)
        changed['housekeeping']['at_utc'] = '2026-10-11T12:35:57.987654321+00:00'
        changed['housekeeping']['ecef_km'][0] = -42163.987654321
        dialog.set_metrics(changed, session_id='REGRESSION-SIMULATED', clock_s=14.)
        settle(app)
        assert table.verticalScrollBar().value() == old_scroll
        assert table.item(0, 1).text() == changed['housekeeping']['at_utc']
        table.scrollToItem(table.item(4, 1))
        settle(app)
        assert table.rowHeight(4) >= wrapped_height(table, 4, 1) + 10
        assert table.viewport().rect().contains(table.visualItemRect(table.item(4, 1)))
        dialog.grab().save(str(tmp_path / f'housekeeping-{mode}-{size[0]}-updated.png'))
    finally:
        cleanup(app, dialog)
        theme.set_theme('dark')
        theme.install(app)


def test_live_wrapping_updates_reuse_items_and_bound_layout_cache(app):
    dialog = SatelliteTelemetryDialog()
    dialog.resize(640, 480)
    metrics = telemetry()
    dialog.set_metrics(metrics, session_id='REGRESSION-SIMULATED', clock_s=13.)
    dialog.show()
    settle(app)
    table = dialog.housekeeping
    original = table.item(0, 1)
    try:
        for tick in range(200):
            changed = copy.deepcopy(metrics)
            changed['housekeeping']['at_utc'] = f'2026-10-11T12:34:56.{tick:09d}+00:00'
            changed['housekeeping']['ecef_km'][0] -= tick * .001
            dialog.set_metrics(changed, session_id='REGRESSION-SIMULATED', clock_s=13.)
            settle(app)
            assert table.item(0, 1) is original
            assert table.rowHeight(0) >= wrapped_height(table, 0, 1) + 10
        assert len(table.itemDelegate()._layouts) <= 512
        assert not table._layout_pending.isActive()
        assert table.columnWidth(2) >= table.horizontalHeader().fontMetrics().horizontalAdvance('Unidade') + 8
    finally:
        cleanup(app, dialog)


def test_geodata_attribution_and_title_have_no_mojibake():
    root = Path(__file__).resolve().parents[1]
    paths = [root / f'src/quantum_autopilot/{part}/geo/world_v1.json' for part in ('console', 'geography')]
    maps = [json.loads(path.read_text(encoding='utf-8')) for path in paths]
    assert maps[0] == maps[1]
    for data in maps:
        assert '\u00c2' not in data['attribution'] + data['meta']['title']
        assert ' · ' in data['attribution'] and ' © OpenStreetMap' in data['attribution']
        assert data['land'] and len(data['bbox']) == 4


def test_satellite_forms_scroll_independently_without_clipping_science(app):
    dialog = SatelliteTelemetryDialog()
    dialog.resize(640, 480)
    dialog.set_metrics(telemetry(), session_id='REGRESSION-SIMULATED', clock_s=13., allowed=True)
    dialog.show()
    settle(app)
    try:
        assert dialog.content_scroll is None
        dialog.tabs.setCurrentWidget(dialog.ogs_scroll)
        settle(app)
        assert dialog.ogs_scroll.verticalScrollBar().maximum() > 0
        dialog.ogs_scroll.ensureWidgetVisible(dialog.stop, 0, 0)
        settle(app)
        button = QRect(dialog.stop.mapTo(dialog.ogs_scroll.viewport(), QPoint()), dialog.stop.size())
        assert dialog.ogs_scroll.viewport().rect().contains(button)
        assert dialog.fixed_footer[0].isVisible()
        dialog.tabs.setCurrentWidget(dialog.housekeeping)
        settle(app)
        viewport_bounds = QRect(dialog.housekeeping.viewport().mapTo(dialog, QPoint()),
                                dialog.housekeeping.viewport().size())
        assert dialog.rect().contains(viewport_bounds)
        assert viewport_bounds.bottom() < dialog.fixed_footer[0].y()
        dialog.housekeeping.scrollToItem(dialog.housekeeping.item(5, 1), QAbstractItemView.ScrollHint.PositionAtCenter)
        settle(app)
        item = dialog.housekeeping.visualItemRect(dialog.housekeeping.item(5, 1))
        assert dialog.housekeeping.viewport().rect().contains(item)
    finally:
        cleanup(app, dialog)
