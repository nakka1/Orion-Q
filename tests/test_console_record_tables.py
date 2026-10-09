"""Large evidence lists must stay reachable and update without widget churn."""
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from quantum_autopilot.console.screens.audit import AuditScreen
from quantum_autopilot.console.screens.keys import KeysScreen
from quantum_autopilot.console.synthetic import artificial_snapshot
from quantum_autopilot.console.theme import C
from quantum_autopilot.console.widgets.records import RecordTable


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.unit
@pytest.mark.parametrize("screen_type,attribute,source", [(KeysScreen,"session_scroll","keys"),(AuditScreen,"entry_scroll","ledger")])
def test_load_tables_keep_all_rows_and_bounded_widgets(app, screen_type, attribute, source):
    screen = screen_type()
    first = artificial_snapshot("load", seed=7, tick=0)
    screen.apply(first)
    table = getattr(screen, attribute)
    model = table.model()
    assert model.rowCount() == 500
    widget_count = len(screen.findChildren(QWidget))
    assert widget_count < 250
    for tick in range(1, 4):
        snapshot = artificial_snapshot("load", seed=7, tick=tick)
        screen.apply(snapshot)
        assert model.rowCount() == 500
        assert len(table.findChildren(QWidget)) < 30
        expected = snapshot.keys.sessions[-1].id if source == "keys" else snapshot.ledger.entries[-1].digest
        column = 0 if source == "keys" else 3
        assert model.data(model.index(499, column), Qt.ItemDataRole.ToolTipRole) == expected
    screen.resize(1440,800)
    screen.show()
    app.processEvents()
    table.scrollToBottom()
    app.processEvents()
    rect = table.visualRect(model.index(499,0))
    assert 0 <= rect.top() < table.viewport().height()
    screen.apply(artificial_snapshot("load", dropout=True))
    assert model.rowCount() == 0
    screen.apply(first)
    assert model.rowCount() == 500
    screen.close()
    screen.deleteLater()


@pytest.mark.unit
def test_table_updates_preserve_selection_scroll_and_full_values(app):
    table = RecordTable(["ID","VALUE"])
    rows = [[(f"long-id-{i}-" + "x" * 100,C.ink),(str(i),C.ink)] for i in range(500)]
    table.set_rows(rows)
    table.resize(500,200)
    table.show()
    app.processEvents()
    table.selectRow(499)
    table.scrollToBottom()
    app.processEvents()
    before = table.verticalScrollBar().value()
    updated = [list(row) for row in rows]
    updated[-1][1] = ("updated",C.amber)
    table.set_rows(updated)
    app.processEvents()
    assert table.currentIndex().row() == 499
    assert table.verticalScrollBar().value() == before
    assert table.model().data(table.model().index(499,1)) == "updated"
    assert table.model().data(table.model().index(499,0), Qt.ItemDataRole.ToolTipRole) == rows[-1][0][0]
    table.set_rows(list(reversed(updated)))
    assert not table.currentIndex().isValid()
    table.close()
    table.deleteLater()


@pytest.mark.unit
def test_audit_filter_checks_records_and_empty_recovery(app):
    screen = AuditScreen()
    snapshot = artificial_snapshot("load", seed=7)
    screen.apply(snapshot)
    screen.set_filter("KEY")
    expected = [e for e in snapshot.ledger.entries if e.category == "key"]
    assert screen.entry_scroll.model().rowCount() == len(expected)
    assert screen.entry_scroll.model().data(screen.entry_scroll.model().index(0,3)) == expected[0].digest
    screen.set_filter("EXPORT")
    assert screen.entry_scroll.model().rowCount() == 0
    assert not screen.entry_empty.isHidden()
    screen.set_filter("ALL")
    assert screen.entry_scroll.model().rowCount() == 500
    assert screen.entry_empty.isHidden()
    screen.close()
    screen.deleteLater()


@pytest.mark.unit
def test_reusing_the_application_theme_does_not_repaint_existing_controls(app):
    from PySide6.QtCore import QEvent, QObject
    from quantum_autopilot.console import theme
    theme.install(app)
    screen = KeysScreen()
    screen.apply(artificial_snapshot("reference"))
    screen.show()
    app.processEvents()
    class Changes(QObject):
        def __init__(self):
            super().__init__()
            self.events = []
        def eventFilter(self, watched, event):
            if event.type() in (QEvent.Type.StyleChange,QEvent.Type.FontChange):
                self.events.append(event.type())
            return False
    changes = Changes()
    screen.installEventFilter(changes)
    for _ in range(3):
        theme.install(app)
        app.processEvents()
    assert changes.events == []
    screen.close()
    screen.deleteLater()
