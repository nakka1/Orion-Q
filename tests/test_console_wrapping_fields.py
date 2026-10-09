"""Full synthetic observations remain readable without layout feedback loops."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from quantum_autopilot.console import theme
from quantum_autopilot.console.widgets.inspector import KVList
from quantum_autopilot.console.widgets.prims import KVRow, WrappingLabel


@pytest.fixture(params=["dark", "light"])
def app(request):
    app = QApplication.instance() or QApplication([])
    previous = theme.current_theme()
    theme.set_theme(request.param); theme.install(app)
    yield app
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    theme.set_theme(previous); theme.install(app)


def settle(app, count=4):
    for _ in range(count):
        app.processEvents()


def geometry(widget):
    rect = widget.geometry()
    return (rect.x(), rect.y(), rect.width(), rect.height())


def assert_full_field(label, raw):
    assert label.text() == label.full_text == label.toolTip() == label.accessibleName() == raw
    assert label.textFormat() == Qt.TextFormat.PlainText
    assert not label.hasHeightForWidth() and not label.sizePolicy().hasHeightForWidth()
    rect = label.contentsRect()
    layout, required = label._text_layout(rect.width())
    assert rect.width() > 0 and rect.height() >= required
    assert layout.text() == raw.replace("\n", "\u2028")
    if raw:
        last = layout.lineAt(layout.lineCount()-1)
        assert last.textStart()+last.textLength() == len(layout.text())
    assert len(label._text_layouts) <= 8


def scale_font(label, factor):
    font = QFont(label.font())
    if font.pixelSize() > 0:
        font.setPixelSize(round(font.pixelSize()*factor))
    else:
        font.setPointSizeF(font.pointSizeF()*factor)
    label.setFont(font)


@pytest.mark.parametrize("font_scale", [1.25, 2.0])
def test_wrapping_label_200_resizes_keep_full_text_height_and_stable_geometry(app, font_scale):
    raw = "SAT_ID_WITHOUT_SPACES_"*7 + "\n<b>Literal provenance & received fields</b>\nDoppler: 12.375 GHz"
    host = QWidget(); outer = QVBoxLayout(host)
    scroll = QScrollArea(); scroll.setWidgetResizable(True); outer.addWidget(scroll)
    content = QWidget(); body = QVBoxLayout(content)
    label = WrappingLabel(raw); scale_font(label, font_scale); body.addWidget(label); body.addStretch()
    scroll.setWidget(content); host.resize(350, 220); host.show(); settle(app)
    try:
        widths = (180, 350, 518, *range(185, 195))
        for index in range(200):
            host.resize(widths[index % len(widths)], 220); settle(app)
            assert_full_field(label, raw)
            assert scroll.horizontalScrollBar().maximum() == 0
            before = (geometry(host), geometry(content), geometry(label),
                      scroll.verticalScrollBar().maximum())
            for _ in range(6):
                app.processEvents()
                assert (geometry(host), geometry(content), geometry(label),
                        scroll.verticalScrollBar().maximum()) == before
        assert scroll.verticalScrollBar().maximum() > 0
        label.setFocus(); QTest.keyClick(label, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert QApplication.clipboard().text() == raw
    finally:
        host.close(); host.deleteLater(); settle(app)


@pytest.mark.parametrize("font_scale", [1.25, 2.0])
def test_wrapping_kv_fields_50_updates_preserve_records_and_all_observations(app, font_scale):
    host = QWidget(); outer = QVBoxLayout(host)
    scroll = QScrollArea(); scroll.setWidgetResizable(True); outer.addWidget(scroll)
    fields = KVList(wrap=True); scroll.setWidget(fields)
    keys = ("Origem e proveniência recebida", "IdentificadorSemEspacos"*3,
            "Valores em linhas distintas", "Tecnologia <HTML literal>")
    initial = [(key, "initial synthetic observation", "ink") for key in keys]
    fields.set_rows(initial)
    records = dict(fields.rows)
    for record in records.values():
        scale_font(record.key_label, font_scale); scale_font(record.value_label, font_scale)
    host.resize(350, 220); host.show(); settle(app)
    try:
        for tick in range(50):
            values = (f"synthetic fixture · received at {tick}.125 s · OGS-A / propagation model",
                      f"source_{tick}_" + "dca390f179"*12,
                      f"Altitude: {550+tick:.2f} km\nDoppler: 12.375 GHz\nJitter: 22.5 ps",
                      f"<b>BBM92 {tick}</b> & <source>fixture</source>")
            rows = [(key, value, "amber" if tick % 2 else "ink") for key, value in zip(keys, values)]
            fields.set_rows(rows); host.resize((180, 350, 518)[tick % 3], 220); settle(app)
            assert fields.texts() == dict(zip(keys, values))
            for key, value in zip(keys, values):
                record = fields.rows[key]
                assert record is records[key]
                assert_full_field(record.key_label, key)
                assert_full_field(record.value_label, value)
                assert record.height() >= max(record.key_label.height(), record.value_label.height()) + 12
                assert record.rect().contains(record.key_label.geometry())
                assert record.rect().contains(record.value_label.geometry())
            snapshot = tuple((geometry(record), geometry(record.key_label), geometry(record.value_label))
                             for record in fields.rows.values())
            for _ in range(6):
                app.processEvents()
                assert tuple((geometry(record), geometry(record.key_label), geometry(record.value_label))
                             for record in fields.rows.values()) == snapshot
            assert scroll.horizontalScrollBar().maximum() == 0
        assert scroll.verticalScrollBar().maximum() > 0
        for key, value in zip(keys, values):
            fields.rows[key].value_label.setFocus()
            QTest.keyClick(fields.rows[key].value_label, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
            assert QApplication.clipboard().text() == value
    finally:
        host.close(); host.deleteLater(); settle(app)


def test_wrapping_is_opt_in_and_unchanged_publications_reuse_text_layout(app):
    compact = KVRow("Altitude", "550.00 km")
    row = KVRow("Proveniência", "<b>synthetic fixture</b>\n" + "Identifier"*20, wrap=True)
    row.resize(350, row.height()); row.show(); settle(app)
    try:
        assert not isinstance(compact.value_label, WrappingLabel)
        assert isinstance(row.key_label, WrappingLabel) and isinstance(row.value_label, WrappingLabel)
        raw = row.value_label.full_text
        assert_full_field(row.value_label, raw)
        width = row.value_label.contentsRect().width()
        cached = row.value_label._text_layout(width)
        for _ in range(50):
            row.set_value(raw, "amber")
        settle(app)
        assert row.value_label._text_layout(width) is cached
        assert_full_field(row.value_label, raw)
        scale_font(row.value_label, 2.0); settle(app)
        width = row.value_label.contentsRect().width()
        assert row.value_label._text_layout(width)[0] is not cached[0]
        assert_full_field(row.value_label, raw)
        row.value_label.setAlignment(Qt.AlignmentFlag.AlignRight); settle(app)
        assert_full_field(row.value_label, raw)
    finally:
        compact.close(); compact.deleteLater(); row.close(); row.deleteLater(); settle(app)
