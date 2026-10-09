"""Read-only observations using Qt model/view without a widget per field.

The table fills its parent scrolling document; it has no independent vertical
scrolling. Received strings, units, origins and row identities remain current.
``rows`` exposes light readback records, not child QWidgets; geometry belongs
to the table's model indexes. Instruments/protocols never run in this widget.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from math import ceil

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QPointF, Qt
from PySide6.QtGui import QFontMetrics, QKeySequence, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QHeaderView, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTableView,
)

from ..theme import C, mono, qc, text as text_font, text_tone


@dataclass(eq=False)
class MetricCell:
    """Stable full-text readback; the model owns the rendered cell."""

    full_text: str = ""
    _tooltip: str = ""

    def text(self) -> str:
        return self.full_text

    def toolTip(self) -> str:  # noqa: N802 - matches existing inspection helpers
        return self._tooltip or self.full_text

    def setToolTip(self, text: str) -> None:  # noqa: N802
        self._tooltip = text


@dataclass(eq=False)
class MetricRow:
    key: str
    value_label: MetricCell = field(default_factory=MetricCell)
    key_label: MetricCell = field(default_factory=MetricCell)

    def setToolTip(self, text: str) -> None:  # noqa: N802
        self.value_label.setToolTip(text)


class _MetricModel(QAbstractTableModel):
    def __init__(self, parent, size: float):
        super().__init__(parent)
        self.items = ()
        self.rows = {}
        self.key_font = text_font(size)
        self.value_font = mono(size-.5)
        self.key_colour = qc(C.ink2)
        self.colours = {}
        self._colour_signature = ()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else 2

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.items):
            return None
        key, value, tone = self.items[index.row()]
        key_cell = index.column() == 0
        text = key if key_cell else value
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return text
        if role == Qt.ItemDataRole.ToolTipRole:
            record = self.rows[key]
            return (record.key_label if key_cell else record.value_label).toolTip()
        if role == Qt.ItemDataRole.AccessibleDescriptionRole:
            return f"{key}: {value}"
        if role == Qt.ItemDataRole.FontRole:
            return self.key_font if key_cell else self.value_font
        if role == Qt.ItemDataRole.ForegroundRole:
            return self.key_colour if key_cell else self.colours[tone]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignVCenter | (
                Qt.AlignmentFlag.AlignLeft if key_cell else Qt.AlignmentFlag.AlignRight))
        return None

    def set_rows(self, rows):
        items = tuple((str(key), "—" if value in (None, "") else str(value), str(tone))
                      for key, value, tone in ((*row, "ink")[:3] for row in rows))
        if len({item[0] for item in items}) != len(items):
            raise ValueError("Metric row keys must be unique")
        colours = tuple((tone, str(text_tone(tone))) for tone in sorted({item[2] for item in items}))
        changed_theme = colours != self._colour_signature or self.key_colour != qc(C.ink2)
        if items == self.items and not changed_theme:
            return False
        previous = self.items
        previous_keys = tuple(item[0] for item in previous)
        keys = tuple(item[0] for item in items)
        reset = previous_keys != keys
        if reset:
            self.beginResetModel()
        self.items = items
        records = self.rows
        self.rows = {key: records.get(key) or MetricRow(key) for key in keys}
        for key, value, _tone in items:
            record = self.rows[key]
            record.key_label.full_text = key
            record.value_label.full_text = value
            record.value_label._tooltip = value
        self.colours = {tone: qc(colour) for tone, colour in colours}
        self._colour_signature = colours
        self.key_colour = qc(C.ink2)
        if reset:
            self.endResetModel()
        else:
            changed = [index for index, pair in enumerate(zip(previous, items))
                       if pair[0] != pair[1] or changed_theme]
            if changed:
                self.dataChanged.emit(self.index(min(changed), 0), self.index(max(changed), 1), [])
        return reset


class _MetricDelegate(QStyledItemDelegate):
    padding = 6

    def __init__(self, parent):
        super().__init__(parent)
        self._layouts = OrderedDict()

    def _layout(self, text, font, width, alignment):
        width = max(1, width - 2*self.padding)
        key = (text, font.toString(), width, int(alignment))
        cached = self._layouts.get(key)
        if cached is not None:
            self._layouts.move_to_end(key)
            return cached
        layout = QTextLayout(text.replace("\n", "\u2028"), font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        option.setAlignment(alignment)
        layout.setTextOption(option)
        layout.setCacheEnabled(True)
        layout.beginLayout()
        height = 0.
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            line.setPosition(QPointF(0, height))
            height += line.height()
        layout.endLayout()
        cached = (layout, ceil(max(height, QFontMetrics(font).height())))
        self._layouts[key] = cached
        if len(self._layouts) > 1024:
            self._layouts.popitem(last=False)
        return cached

    def text_height(self, text, font, width, alignment=Qt.AlignmentFlag.AlignLeft):
        return self._layout(text, font, width, alignment)[1] + 2*self.padding

    def paint(self, painter, option, index):
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        text = styled.text
        styled.text = ""
        style = styled.widget.style() if styled.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, styled.widget)
        painter.save()
        painter.setClipRect(styled.rect)
        alignment = Qt.AlignmentFlag.AlignLeft if index.column() == 0 else Qt.AlignmentFlag.AlignRight
        layout, height = self._layout(text, styled.font, styled.rect.width(), alignment)
        colour = (styled.palette.highlightedText().color()
                  if styled.state & QStyle.StateFlag.State_Selected else styled.palette.text().color())
        painter.setPen(colour)
        layout.draw(painter, QPointF(styled.rect.left() + self.padding,
                                    styled.rect.top() + max(self.padding, (styled.rect.height()-height)/2)))
        painter.setPen(QPen(qc(C.soft), 1))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.restore()


class MetricTable(QTableView):
    """Key/value table for larger science panels; small ground rows use KVList."""

    def __init__(self, parent=None, *, size: float = 12.5):
        super().__init__(parent)
        self._metrics = _MetricModel(self, size)
        self._key_width = 0
        self._viewport_width = None
        self._layout_signature = None
        self._reflowing = False
        self.setModel(self._metrics)
        self._delegate = _MetricDelegate(self)
        self.setItemDelegate(self._delegate)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setShowGrid(False)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.horizontalHeader().hide()
        self.verticalHeader().hide()
        height = max(QFontMetrics(self._metrics.key_font).height(),
                     QFontMetrics(self._metrics.value_font).height()) + 12
        self.verticalHeader().setDefaultSectionSize(max(24, height))
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.setStyleSheet(f"QTableView{{background:{C.raised};color:{C.ink};border:0;}}")
        self.setAccessibleName("Observações recebidas · campos e valores completos")
        self.setFixedHeight(0)

    @property
    def rows(self):
        return self._metrics.rows

    def set_rows(self, rows) -> None:
        previous = self._metrics.items
        reset = self._metrics.set_rows(rows)
        fonts = (self._metrics.key_font.toString(), self._metrics.value_font.toString())
        if reset or self._layout_signature is None or self._layout_signature[2:] != fonts:
            metrics = QFontMetrics(self._metrics.key_font)
            self._key_width = max((metrics.horizontalAdvance(key) + 12 for key in self.rows), default=0)
            self._fit_columns()
        if reset or self._geometry_signature() != self._layout_signature:
            self._resize_rows()
        else:
            # Tone/theme-only publications need repainting, but no text layout.
            changed = [index for index, (old, new) in enumerate(zip(previous, self._metrics.items))
                       if old[:2] != new[:2]]
            if changed:
                self._resize_rows(changed)

    def _geometry_signature(self):
        return (self.columnWidth(0), self.columnWidth(1),
                self._metrics.key_font.toString(), self._metrics.value_font.toString())

    def _resize_rows(self, rows=None):
        if self._reflowing:
            return
        self._reflowing = True
        try:
            self._layout_signature = self._geometry_signature()
            for index in range(len(self._metrics.items)) if rows is None else rows:
                key, value, _tone = self._metrics.items[index]
                height = max(self._delegate.text_height(key, self._metrics.key_font, self.columnWidth(0)),
                             self._delegate.text_height(value, self._metrics.value_font, self.columnWidth(1),
                                                        Qt.AlignmentFlag.AlignRight))
                if self.rowHeight(index) != height:
                    self.setRowHeight(index, height)
            # The complete document scrolls in the parent; each cell remains
            # readable without hiding long values behind an ellipsis or tooltip.
            height = self.verticalHeader().length()
            if self.height() != height:
                self.setFixedHeight(height)
        finally:
            self._reflowing = False

    def _fit_columns(self):
        available = max(0, self.viewport().width())
        width = min(self._key_width, max(1, round(available*.45)))
        if self.columnWidth(0) != width:
            self.setColumnWidth(0, width)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_metrics") and self.viewport().width() != self._viewport_width:
            self._viewport_width = self.viewport().width()
            self._fit_columns()
            if self._geometry_signature() != self._layout_signature:
                self._resize_rows()

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if (hasattr(self, "_delegate")
                and event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange)):
            self._layout_signature = None
            metrics = QFontMetrics(self._metrics.key_font)
            self._key_width = max((metrics.horizontalAdvance(key) + 12 for key in self.rows), default=0)
            self._fit_columns()
            self._resize_rows()

    def value(self, key: str) -> str:
        return self.rows[key].value_label.full_text

    def texts(self) -> dict:
        return {key: row.value_label.full_text for key, row in self.rows.items()}

    def copy_text(self) -> str:
        grouped = {}
        for index in sorted(self.selectedIndexes(), key=lambda item: (item.row(), item.column())):
            grouped.setdefault(index.row(), []).append(str(index.data(Qt.ItemDataRole.DisplayRole)))
        return "\n".join("\t".join(values) for values in grouped.values())

    def keyPressEvent(self, event):  # noqa: N802
        if event.matches(QKeySequence.StandardKey.Copy):
            QApplication.clipboard().setText(self.copy_text())
            event.accept()
            return
        super().keyPressEvent(event)
