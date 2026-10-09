"""Live values must update without rebuilding stable lists or losing scroll state."""
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.screens.telemetry import TelemetryScreen  # noqa: E402
from quantum_autopilot.console.screens.topology import TopologyScreen  # noqa: E402
from quantum_autopilot.console.widgets.inspector import ChipRow  # noqa: E402


@pytest.fixture(params=['light', 'dark'])
def app(request):
    instance = QApplication.instance() or QApplication([])
    previous = theme.current_theme()
    theme.set_theme(request.param)
    theme.install(instance)
    yield instance
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    theme.set_theme(previous)
    theme.install(instance)


def close(widget):
    if hasattr(widget, 'shutdown'):
        widget.shutdown()
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def condition_values(screen):
    return [screen.condition_lay.itemAt(index).widget().findChildren(QLabel)[-1]
            for index in range(screen.condition_lay.count())]


def test_live_conditions_keep_widgets_and_scroll_while_values_change(app):
    screen = TelemetryScreen()
    try:
        rows = [(f'Condition {index}', f'Reading {index}') for index in range(35)]
        screen._set_conditions(rows)
        screen.resize(1400, 1300)
        screen.show()
        app.processEvents()
        values = condition_values(screen)
        widgets = [screen.condition_lay.itemAt(i).widget() for i in range(len(rows))]
        scrollbar = screen.condition_scroll.verticalScrollBar()
        assert scrollbar.maximum() > 0
        scrollbar.setValue(scrollbar.maximum() // 2)
        position = scrollbar.value()
        for tick in range(20):
            readings = [(key, f't {tick:02d} / row {index:02d}') for index, (key, _value) in enumerate(rows)]
            screen._set_conditions(readings)
            app.processEvents()
            assert condition_values(screen) == values
            assert [screen.condition_lay.itemAt(i).widget() for i in range(len(rows))] == widgets
            assert [(label.text(), label.toolTip()) for label in values] == [(value, value) for _, value in readings]
            assert scrollbar.value() == position
    finally:
        close(screen)


@pytest.mark.parametrize('keys', [[], ['A'], ['B', 'A'], ['A', 'A']])
def test_condition_structure_changes_are_shown_in_order(app, keys):
    screen = TelemetryScreen()
    try:
        screen._set_conditions([('A', 'old'), ('B', 'old')])
        rows = [(key, f'value {index}') for index, key in enumerate(keys)]
        screen._set_conditions(rows)
        assert screen.condition_lay.count() == len(rows)
        assert [value.text() for value in condition_values(screen)] == [value for _, value in rows]
        screen._set_conditions([(key, 'fresh') for key in keys])
        assert [value.text() for value in condition_values(screen)] == ['fresh'] * len(keys)
    finally:
        close(screen)


def test_status_changes_keep_chips_and_update_text_colour_outline(app):
    row = ChipRow()
    try:
        row.set_chips([('UP', 'ok', False), ('SIMULATED', 'sat', True)])
        widgets = [row.lay.itemAt(i).widget() for i in range(2)]
        for status in ('DEGRADED', 'CALIBRATING', 'ISOLATED', 'UP'):
            row.set_chips([(status, 'warn', True), ('NO INPUT', 'mute', False)])
            assert [row.lay.itemAt(i).widget() for i in range(2)] == widgets
            assert row.texts() == [status, 'NO INPUT']
            assert [(chip._tone, chip._outline) for chip in widgets] == [('warn', True), ('mute', False)]
        row.set_chips([('DOWN', 'bad', True)])
        assert row.texts() == ['DOWN']
        row.set_chips([])
        assert row.texts() == []
        assert row.lay.count() == 1  # Only the alignment spacer remains.
    finally:
        close(row)


def test_health_counts_and_compaction_reuse_chips(app):
    screen = TopologyScreen()
    try:
        screen._strip_counts = {'up': 8, 'degraded': 0, 'down': 0}
        screen._apply_strip_level(0)
        widgets = [screen.health_lay.itemAt(i).widget() for i in range(screen.health_lay.count())]
        for level in (1, 3, 0):
            screen._strip_counts = {'up': 6, 'degraded': 1, 'down': 1}
            screen._apply_strip_level(level)
            assert [screen.health_lay.itemAt(i).widget() for i in range(screen.health_lay.count())] == widgets
            expected = screen._health_chips(level)
            assert [(chip.text(), chip._tone, chip.toolTip()) for chip in widgets] == list(expected)
        screen._strip_counts['isolated'] = 1
        screen._apply_strip_level(0)
        assert screen.health_lay.count() == 4
    finally:
        close(screen)
