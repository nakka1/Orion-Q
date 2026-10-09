"""Native scientific tables preserve complete received records and parent scroll.

These records are synthetic UI fixtures; they attest no physical transmission.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, QItemSelection, QRect, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from quantum_autopilot.console import theme
from quantum_autopilot.console.theme import qc, text_tone
from quantum_autopilot.console.widgets.metric_table import MetricTable
from quantum_autopilot.console.widgets.satellite_telemetry import SatelliteLivePanel


@pytest.fixture(params=["dark", "light"])
def app(request):
    app = QApplication.instance() or QApplication([])
    previous = theme.current_theme()
    theme.set_theme(request.param); theme.install(app)
    yield app
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    theme.set_theme(previous); theme.install(app)


def settle(app):
    app.processEvents(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_metric_table_latest_units_origin_identity_and_full_readback(app):
    table = MetricTable()
    table.set_rows([("Origem", "simulated fixture · OGS-A", "dim"),
                    ("Doppler", "12.375 GHz", "amber"), ("Fidelidade", None)])
    try:
        first = dict(table.rows)
        assert table.texts() == {"Origem": "simulated fixture · OGS-A", "Doppler": "12.375 GHz", "Fidelidade": "—"}
        table.set_rows([("Doppler", "13.500 GHz", "red"), ("Origem", "new received fixture", "dim"),
                        ("Jitter", "22.5 ps", "green")])
        assert table.rows["Doppler"] is first["Doppler"]
        assert table.rows["Origem"] is first["Origem"] and "Fidelidade" not in table.rows
        assert table.value("Doppler") == table.rows["Doppler"].value_label.full_text == "13.500 GHz"
        model = table.model()
        assert model.rowCount() == 3 and model.columnCount() == 2
        for index, (key, value) in enumerate(table.texts().items()):
            cell = model.index(index, 1)
            assert cell.data(Qt.ItemDataRole.DisplayRole) == value
            assert cell.data(Qt.ItemDataRole.ToolTipRole) == value
            assert cell.data(Qt.ItemDataRole.AccessibleTextRole) == value
            assert cell.data(Qt.ItemDataRole.AccessibleDescriptionRole) == f"{key}: {value}"
            assert model.index(index, 0).data(Qt.ItemDataRole.ToolTipRole) == key
        table.set_rows([])
        assert not table.rows and table.height() == 0
    finally:
        table.close(); table.deleteLater(); settle(app)


def test_metric_table_resizes_keys_and_uses_parent_scroll_for_all_211_rows(app):
    host = QWidget(); layout = QVBoxLayout(host)
    scroll = QScrollArea(); scroll.setWidgetResizable(True); layout.addWidget(scroll)
    table = MetricTable(); scroll.setWidget(table)
    rows = [(f"ISL {index} · Visibilidade / obstrução terrestre", f"fixture {index} · 9000 km", "ink")
            for index in range(211)]
    table.set_rows(rows); host.resize(900, 610); host.show(); settle(app)
    try:
        original = dict(table.rows)
        maximum_key = max(QFontMetrics(table.model().key_font).horizontalAdvance(key) + 12 for key in table.rows)
        assert table.columnWidth(0) >= maximum_key
        assert table.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert table.verticalScrollBar().maximum() == 0
        assert scroll.verticalScrollBar().maximum() > 0
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum()); settle(app)
        assert not table.visualRect(table.model().index(210, 1)).isEmpty()
        for width in (340, 548, 1100, 420, 900):
            host.resize(width, 610); settle(app)
            assert all(table.rows[key] is record for key, record in original.items())
            assert len(table.texts()) == table.model().rowCount() == 211
            assert table.verticalScrollBar().maximum() == 0
            assert table.columnWidth(0) <= round(table.viewport().width()*.65)
            last = table.model().index(210, 0)
            assert last.data(Qt.ItemDataRole.ToolTipRole) == rows[-1][0]
            assert last.data(Qt.ItemDataRole.AccessibleTextRole) == rows[-1][0]
        assert len(table.findChildren(QWidget)) < 40
    finally:
        host.close(); host.deleteLater(); settle(app)


def test_metric_table_keyboard_copies_unabridged_keys_values_and_units(app):
    table = MetricTable(); table.resize(240, 120)
    rows = [("Protocolo / tecnologia sem abreviação", "BBM92 · simulated source · 850 nm", "ink"),
            ("Aborto pós-processamento", "insufficient detections · 0 certified bits", "amber")]
    table.set_rows(rows); table.show(); settle(app)
    try:
        selection = QItemSelection(table.model().index(0, 0), table.model().index(1, 1))
        table.selectionModel().select(selection, table.selectionModel().SelectionFlag.ClearAndSelect)
        expected = "\n".join(f"{key}\t{value}" for key, value, _tone in rows)
        assert table.copy_text() == expected
        QTest.keyClick(table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert QApplication.clipboard().text() == expected
    finally:
        table.close(); table.deleteLater(); settle(app)


def test_metric_table_theme_updates_tones_without_resetting_records(app):
    table = MetricTable(); table.set_rows([("QBER", "3.00 %", "amber")])
    record = table.rows["QBER"]
    first = table.model().index(0, 1).data(Qt.ItemDataRole.ForegroundRole)
    try:
        theme.set_theme("light" if theme.current_theme() == "dark" else "dark")
        table.set_rows([("QBER", "3.00 %", "amber")])
        colour = table.model().index(0, 1).data(Qt.ItemDataRole.ForegroundRole)
        assert colour != first and colour == qc(text_tone("amber"))
        assert table.rows["QBER"] is record and table.value("QBER") == "3.00 %"
    finally:
        table.close(); table.deleteLater(); settle(app)


@pytest.mark.parametrize("width", [350, 520, 780])
def test_metric_table_wraps_full_values_provenance_and_unbroken_tokens(app, width):
    host = QWidget(); layout = QVBoxLayout(host)
    scroll = QScrollArea(); scroll.setWidgetResizable(True); layout.addWidget(scroll)
    table = MetricTable(); scroll.setWidget(table)
    rows = [
        ("Altitude / distância e posição recebida", "550.00 km · 6900.000 / -1150.000 / 3240.000 km", "ink"),
        ("Proveniência da observação recebida",
         "synthetic fixture · OGS-A / propagation model v3 · received at network time 12345.678 s", "dim"),
        ("IdentificadorSemEspacos"*4, "a3f07812c5d9"*12, "green"),
        ("Valores recebidos em linhas distintas", "Altitude: 550.00 km\nDoppler: 12.375 GHz\nJitter: 22.5 ps", "amber"),
    ]
    table.set_rows(rows); host.resize(width, 200); host.show(); settle(app)
    try:
        assert table.wordWrap() and table.textElideMode() == Qt.TextElideMode.ElideNone
        assert table.columnWidth(1) >= table.viewport().width()*.55 - 1
        for row, (key, value, _tone) in enumerate(rows):
            for column, text in enumerate((key, value)):
                index = table.model().index(row, column)
                font = QFontMetrics(index.data(Qt.ItemDataRole.FontRole))
                bounds = font.boundingRect(
                    QRect(0, 0, max(1, table.columnWidth(column)-12), 10000),
                    int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere), text)
                assert table.rowHeight(row) >= bounds.height() + 12
                assert index.data(Qt.ItemDataRole.DisplayRole) == text
            if row in (2, 3):
                assert table.rowHeight(row) > table.verticalHeader().defaultSectionSize()
        assert table.height() == sum(table.rowHeight(row) for row in range(len(rows)))
        assert table.verticalScrollBar().maximum() == 0
        assert scroll.verticalScrollBar().maximum() > 0
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum()); settle(app)
        assert not table.visualRect(table.model().index(len(rows)-1, 1)).isEmpty()
        selection = QItemSelection(table.model().index(0, 0), table.model().index(len(rows)-1, 1))
        table.selectionModel().select(selection, table.selectionModel().SelectionFlag.ClearAndSelect)
        assert table.copy_text() == "\n".join(f"{key}\t{value}" for key, value, _tone in rows)
    finally:
        host.close(); host.deleteLater(); settle(app)


def test_metric_table_reflows_only_changed_text_and_caches_width_font_layouts(app, monkeypatch):
    table = MetricTable(); table.resize(520, 120)
    rows = [(f"Observação {index}", f"fixture {index} · 550 km", "ink") for index in range(12)]
    table.set_rows(rows); table.show(); settle(app)
    calls = []
    original = table._delegate.text_height

    def measured(*args):
        calls.append(args[0])
        return original(*args)

    monkeypatch.setattr(table._delegate, "text_height", measured)
    try:
        records = dict(table.rows)
        table.set_rows(rows); settle(app)
        assert not calls
        updated = list(rows)
        updated[5] = (rows[5][0], "receivedtoken"*25, "amber")
        table.set_rows(updated); settle(app)
        assert calls == [updated[5][0], updated[5][1]]
        assert table.rowHeight(5) > table.rowHeight(4)
        assert all(table.rows[key] is record for key, record in records.items())
        calls.clear()
        updated[5] = (*updated[5][:2], "green")
        table.set_rows(updated); settle(app)
        assert not calls
        table.resize(350, table.height()); settle(app)
        assert calls
        calls.clear()
        table.set_rows(updated); settle(app)
        assert not calls
        font = QFont(table.model().value_font)
        font.setPointSizeF(font.pointSizeF()+3)
        table.model().value_font = font
        table.set_rows(updated); settle(app)
        assert calls and len(table._delegate._layouts) <= 1024
        calls.clear()
        table.set_rows(updated); settle(app)
        assert not calls
    finally:
        table.close(); table.deleteLater(); settle(app)


def test_hidden_satellite_panel_has_no_tables_until_observation_or_explicit_readback(app):
    panel = SatelliteLivePanel()
    try:
        assert panel._tables is None and not panel.findChildren(MetricTable)
        assert panel.orbital.model().rowCount() == 0
        initial = panel.orbital
        assert len(panel.findChildren(MetricTable)) == 4
        panel.set_state({'device_id':'SAT', 'origin':'synthetic fixture', 'at_network_s':1.,
                         'altitude_km':550., 'ecef_km':[6900.,0.,0.]}, {})
        assert panel.orbital is initial and panel.orbital.value('Altitude') == '550.00 km'
    finally:
        panel.close(); panel.deleteLater(); settle(app)
