"""Reflow existing widgets without recreating controls or discarding their state."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSize
from PySide6.QtWidgets import QBoxLayout, QGridLayout, QLayout, QSizePolicy, QWidget


def _height(widget, width):
    return max(widget.minimumSizeHint().height(),
               widget.heightForWidth(width) if widget.hasHeightForWidth() else widget.sizeHint().height())


class AdaptiveGrid(QWidget):
    """Keep readable card widths; use fewer columns when the page is narrower."""

    def __init__(self, items, columns=3, gap=16, minimum_column=260, parent=None):
        super().__init__(parent)
        self.items = [item if isinstance(item, tuple) else (item, 1) for item in items]
        self.columns, self.gap, self.minimum_column = columns, gap, minimum_column
        self._columns = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(gap)
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        for widget, _span in self.items:
            widget.setMinimumWidth(0)
            child_policy = widget.sizePolicy()
            child_policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            widget.setSizePolicy(child_policy)
        self._reflow()

    def column_count(self, width):
        return max(1, min(self.columns, (width + self.gap) // (self.minimum_column + self.gap)))

    def positions(self, columns):
        column = line = 0
        for index, (widget, desired_span) in enumerate(self.items):
            span = min(desired_span, columns)
            if column + span > columns:
                column, line = 0, line + 1
            if column == 0 and index == len(self.items) - 1:
                span = columns  # A last card fills its row instead of leaving an empty column.
            yield widget, line, column, span
            column += span

    def _reflow(self):
        columns = self.column_count(self.width())
        if columns == self._columns:
            return
        self._columns = columns
        while self.grid.count():
            self.grid.takeAt(0)
        for column in range(self.columns):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
            self.grid.setColumnMinimumWidth(column, 0)
        for widget, line, column, span in self.positions(columns):
            self.grid.addWidget(widget, line, column, 1, span)
        self.updateGeometry()

    def resizeEvent(self, event):
        self._reflow()
        super().resizeEvent(event)

    def heightForWidth(self, width):  # noqa: N802 - Qt naming
        columns = self.column_count(width)
        unit = max(1, (width - (columns - 1) * self.gap) // columns)
        heights = {}
        for widget, line, _column, span in self.positions(columns):
            heights[line] = max(heights.get(line, 0), _height(widget, unit * span + (span - 1) * self.gap))
        return sum(heights.values()) + max(0, len(heights) - 1) * self.gap

    def minimumSizeHint(self):  # noqa: N802
        return QSize(0, self.heightForWidth(max(1, self.width())))

    def sizeHint(self):  # noqa: N802
        width = self.columns * self.minimum_column + (self.columns - 1) * self.gap
        return QSize(width, self.heightForWidth(width))


class AdaptiveRow(QWidget):
    """Keep a heading/action row together, stacking its groups when necessary."""

    def __init__(self, widgets, gap=14, parent=None):
        super().__init__(parent)
        self.box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(gap)
        self.box.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        for index, widget in enumerate(widgets):
            self.box.addWidget(widget, 1 if index == 0 else 0)

    def _visible_items(self):
        return [self.box.itemAt(i) for i in range(self.box.count()) if not self.box.itemAt(i).isEmpty()]

    def _stacked(self, width):
        items = self._visible_items()
        needed = sum(item.minimumSize().width() for item in items)
        return needed + max(0, len(items) - 1) * self.box.spacing() > width

    def _reflow(self):
        wanted = QBoxLayout.Direction.TopToBottom if self._stacked(self.width()) else QBoxLayout.Direction.LeftToRight
        if wanted != self.box.direction():
            self.box.setDirection(wanted)
            self.updateGeometry()

    def resizeEvent(self, event):
        self._reflow()
        super().resizeEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.LayoutRequest and hasattr(self, "box"):
            self._reflow()
        return super().event(event)

    def heightForWidth(self, width):  # noqa: N802
        heights = [_height(item.widget(), width) for item in self._visible_items()]
        if self._stacked(width):
            return sum(heights) + max(0, len(heights) - 1) * self.box.spacing()
        return max(heights, default=0)

    def minimumSizeHint(self):  # noqa: N802
        return QSize(0, self.heightForWidth(max(1, self.width())))

    def sizeHint(self):  # noqa: N802
        return self.box.sizeHint()
