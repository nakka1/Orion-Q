"""Exact current column widths with fewer repeated native text measurements."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6.QtWidgets')
from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QHeaderView  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.screens.topology import TopologyScreen  # noqa: E402
from quantum_autopilot.console.widgets.records import RecordTable  # noqa: E402

pytestmark = pytest.mark.unit


def previous_set_links(host, rows):
    """Independent reference retaining the previous every-row width behavior."""
    records = []
    for row in rows:
        name, fidelity, qber, skr, name_colour, value_colour = row[:6]
        state = row[6] if len(row) > 6 else ''
        records.append([(name, name_colour), (fidelity, value_colour), (qber, theme.C.ink),
                        (skr, theme.C.ink), (state, name_colour)])
    host.link_table.set_rows(records)
    metrics = host.link_table.fontMetrics()
    for col in (1, 2, 3, 4):
        width = max((metrics.horizontalAdvance(row[col][0]) + 18
                     for row in host.link_table.records.rows), default=40)
        width = min(120, max(56 if col < 4 else 46, width))
        if host.link_table.columnWidth(col) != width:
            host.link_table.setColumnWidth(col, width)


class MeasuredTable(RecordTable):
    """Instrument only the explicit Python fontMetrics() call under test."""

    def __init__(self):
        super().__init__(['LINK', 'F', 'QBER', 'SKR', 'STATE'])
        self.measurements = []
        for col in (1, 2, 3, 4):
            self.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)

    def fontMetrics(self):
        underlying = super().fontMetrics()

        def advance(text):
            self.measurements.append(text)
            return underlying.horizontalAdvance(text)

        return SimpleNamespace(horizontalAdvance=advance)


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(params=['dark', 'light'])
def tables(app, request):
    previous = theme.current_theme()
    theme.set_theme(request.param)
    theme.install(app)
    old, current = MeasuredTable(), MeasuredTable()
    try:
        yield SimpleNamespace(link_table=old), SimpleNamespace(link_table=current)
    finally:
        for table in (old, current):
            table.close()
            table.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        theme.set_theme(previous)
        theme.install(app)


def rows(count=512):
    return [(f'LINK-{i}', f'{.90 + (i % 6) * .01:.3f}', f'{i % 4 + 1}.00%',
             f'{i % 8 + 1}k', theme.C.ink, theme.C.ink, 'UP' if i % 2 else 'DEGR')
            for i in range(count)]


def assert_equivalent(old, current, input_rows):
    previous_set_links(old, input_rows)
    TopologyScreen._set_links(current, input_rows)
    assert old.link_table.records.rows == current.link_table.records.rows
    assert current.link_table.records.rowCount() == len(input_rows)
    assert [old.link_table.columnWidth(col) for col in (1, 2, 3, 4)] == [
        current.link_table.columnWidth(col) for col in (1, 2, 3, 4)]


def test_measures_each_exact_text_once_per_column_without_removing_rows(tables):
    old, current = tables
    assert_equivalent(old, current, rows())
    assert len(old.link_table.measurements) == 2048
    assert len(current.link_table.measurements) == 20


@pytest.mark.parametrize('input_rows', [
    [],
    [('L', '', '', '', 'ink', 'ink')],
    [('L', '—', 'não publicado', '0.0 bit/s', 'ink', 'ink', 'no input')],
    [('L-α', '0.999', '2.00 %', '1000 Mbit/s', 'ink', 'ink', '等待同步 / indisponível')],
    [('L-1', '0.5', '1%', '1k', 'ink', 'ink', 'UP'),
     ('L-2', '123456789012345678901234567890', '1%', '1k', 'ink', 'ink', 'UP')],
])
def test_empty_missing_unicode_and_wide_values_match_previous(tables, input_rows):
    assert_equivalent(*tables, input_rows)


def test_removal_of_widest_value_shrinks_current_width(tables):
    old, current = tables
    broad = [('L-1', '0.9', '1%', '1k', 'ink', 'ink', 'UP'),
             ('L-2', '123456789012345678901234567890', '1%', '1k', 'ink', 'ink', 'UP')]
    assert_equivalent(old, current, broad)
    assert current.link_table.columnWidth(1) == 120
    assert_equivalent(old, current, broad[:1])
    assert current.link_table.columnWidth(1) < 120


def test_font_change_recomputes_exact_current_widths(tables):
    old, current = tables
    input_rows = [('L', '0.999', '2.00%', '1234k', 'ink', 'ink', 'DEGR')]
    assert_equivalent(old, current, input_rows)
    first = [current.link_table.columnWidth(col) for col in (1, 2, 3, 4)]
    for host in (old, current):
        font = host.link_table.font()
        font.setPixelSize(24)
        host.link_table.setFont(font)
    assert_equivalent(old, current, input_rows)
    assert [current.link_table.columnWidth(col) for col in (1, 2, 3, 4)] != first


def test_duplicate_text_is_still_measured_per_column(tables):
    old, current = tables
    input_rows = [('L-1', 'IDENTICAL', 'IDENTICAL', 'IDENTICAL', 'ink', 'ink', 'IDENTICAL')]
    assert_equivalent(old, current, input_rows)
    assert current.link_table.measurements.count('IDENTICAL') == 4


def test_current_values_and_repeated_identity_survive_each_update(tables):
    old, current = tables
    input_rows = rows(10)
    assert_equivalent(old, current, input_rows)
    old.link_table.selectRow(3)
    current.link_table.selectRow(3)
    modified = list(input_rows)
    value = list(modified[3])
    value[2] = '25.75%'
    value[6] = 'DOWN'
    modified[3] = tuple(value)
    assert_equivalent(old, current, modified)
    assert current.link_table.records.rows[3][2][0] == '25.75%'
    assert current.link_table.records.rows[3][4][0] == 'DOWN'
    assert current.link_table.currentIndex().row() == old.link_table.currentIndex().row() == 3
