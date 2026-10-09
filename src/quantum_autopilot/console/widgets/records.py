"""Scrollable records with real headers and virtualised rows."""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView

from ..theme import C, label_font, mix, mono, qc, text_of
from .scrollable_table import ScrollableTableView


class RecordModel(QAbstractTableModel):
    def __init__(self, headers, parent=None, *, identity_columns=(0,)):
        super().__init__(parent)
        self.headers, self.rows = list(headers), []
        self.identity_columns = tuple(identity_columns)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        value, colour = self.rows[index.row()][index.column()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return value
        if role == Qt.ItemDataRole.ForegroundRole:
            return qc(text_of(colour))
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return None

    def replace(self, rows):
        rows = [list(row) for row in rows]
        if any(len(row) != len(self.headers) for row in rows):
            raise ValueError("record row does not match its headers")
        if rows == self.rows:
            return
        if len(rows) == len(self.rows) and all(
            all(row[column][0] == old[column][0] for column in self.identity_columns)
            for row, old in zip(rows, self.rows)
        ):
            self.rows = rows
            self.dataChanged.emit(self.index(0, 0), self.index(len(rows) - 1, len(self.headers) - 1),
                                  [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.ForegroundRole])
            return
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()


class RecordTable(ScrollableTableView):
    def __init__(self, headers, parent=None, *, identity_columns=(0,)):
        super().__init__(parent, background=C.raised)
        self.records = RecordModel(headers, self, identity_columns=identity_columns)
        self.setModel(self.records)
        self.setFont(mono(12.5))
        self.horizontalHeader().setFont(label_font(11))
        self.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.horizontalHeader().setHighlightSections(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setMinimumSectionSize(44)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(34)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMinimumHeight(100)
        # `.tbl`: mono label headers on a line rule, soft row rules, selected ground.
        self.setStyleSheet(f"""
            QTableView {{ background:transparent; border:0; gridline-color:{C.soft}; }}
            QTableView::item {{ border-bottom:1px solid {C.soft}; padding:3px 4px; }}
            QTableView::item:hover {{ background:{C.hover}; }}
            QTableView::item:selected {{ background:{mix(C.sel)}; color:{C.ink}; }}
            QHeaderView::section {{ background:{C.bg}; color:{C.ink3};
                border:0; border-bottom:1px solid {C.line}; padding:6px 4px; }}
        """)

    def set_rows(self, rows):
        position = self.verticalScrollBar().value()
        self.records.replace(rows)
        self.verticalScrollBar().setValue(position)
