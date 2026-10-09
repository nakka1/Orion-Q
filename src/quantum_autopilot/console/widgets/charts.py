"""Line chart of backend series for the workspace cards.

`SeriesChart` plots exactly the points it is given: one vertex per (x, y) pair
whose y is a number. A missing value (None) breaks the line; nothing is
interpolated, smoothed or extrapolated, and no point is added. `plotted()`
returns what was drawn, so a test can compare it with the snapshot.

Colours are read from `theme` at paint time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..theme import C, mono, qc, tone


@dataclass
class ChartSeries:
    """One polyline. `xs` and `ys` are parallel; a None in `ys` is a gap."""

    label: str
    xs: Sequence[float]
    ys: Sequence[float | None]
    tone: str = "amber"
    width: float = 2.0
    dashed: bool = False
    points: list = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.xs) != len(self.ys):
            raise ValueError("series x and y must have the same length")
        self.points = [(float(x), float(y)) for x, y in zip(self.xs, self.ys) if y is not None]


class SeriesChart(QWidget):
    """Soft gridlines with value labels, an optional dashed threshold, N polylines."""

    def __init__(self, parent=None, *, height: int = 230, grow: bool = False) -> None:
        super().__init__(parent)
        self._height = height
        self.setMinimumHeight(height)
        if not grow:
            self.setMaximumHeight(height)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding if grow else QSizePolicy.Policy.Fixed)
        self.series: list[ChartSeries] = []
        self.threshold: float | None = None
        self.threshold_label = ""
        self.x_labels: list[str] = []
        self.empty_text = "No series supplied by this source"
        self.y_format: Callable[[float], str] = lambda value: f"{value:g}"
        self.y_min, self.y_max = 0.0, 1.0
        self.x_min, self.x_max = 0.0, 1.0
        self.setAccessibleName("Series chart")

    # ------------------------------------------------------------------ data
    def set_series(self, series: Sequence[ChartSeries], *, threshold: float | None = None,
                   threshold_label: str = "", x_labels: Sequence[str] = (),
                   y_format: Callable[[float], str] | None = None,
                   y_range: tuple[float, float] | None = None, empty_text: str | None = None) -> None:
        self.series = list(series)
        self.threshold, self.threshold_label = threshold, threshold_label
        self.x_labels = list(x_labels)
        if y_format is not None:
            self.y_format = y_format
        if empty_text is not None:
            self.empty_text = empty_text
        values = [y for item in self.series for _, y in item.points]
        xs = [x for item in self.series for x, _ in item.points]
        if y_range is not None:
            self.y_min, self.y_max = y_range
        elif values:
            low, high = min(values), max(values)
            pad = (high - low) * 0.12 or abs(high) * 0.02 or 0.01
            self.y_min, self.y_max = low - pad, high + pad
        else:
            self.y_min, self.y_max = 0.0, 1.0
        if self.y_max <= self.y_min:
            self.y_max = self.y_min + 1e-9
        self.x_min, self.x_max = (min(xs), max(xs)) if xs else (0.0, 1.0)
        self.update()

    def plotted(self) -> list[tuple[str, list[tuple[float, float]]]]:
        """(label, [(x, y), …]) of every polyline as drawn."""

        return [(item.label, list(item.points)) for item in self.series]

    def threshold_in_range(self) -> bool:
        """True when the threshold line is drawn: a threshold exists, data exists and it is on the scale."""

        return (self.threshold is not None and any(item.points for item in self.series)
                and self.y_min <= self.threshold <= self.y_max)

    # ----------------------------------------------------------------- paint
    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(360, self._height)

    def _y_ticks(self) -> list[tuple[float, str]]:
        """(fraction of the plot height, label) of the three gridlines."""

        if not any(item.points for item in self.series):
            return []
        return [(fraction, self.y_format(self.y_min + fraction * (self.y_max - self.y_min)))
                for fraction in (0.25, 0.5, 0.75)]

    def _plot(self) -> QRectF:
        bottom = 20 if self.x_labels else 4
        metrics = QFontMetrics(mono(11))
        gutter = max((metrics.horizontalAdvance(label) for _, label in self._y_ticks()), default=0)
        left = gutter + 10.5 if gutter else 0.5
        return QRectF(left, 6.5, max(1.0, self.width() - left - 0.5), max(1.0, self.height() - 6.0 - bottom))

    def _point(self, plot: QRectF, x: float, y: float) -> QPointF:
        span_x = self.x_max - self.x_min
        fx = 0.5 if span_x <= 0 else (x - self.x_min) / span_x
        fy = (y - self.y_min) / (self.y_max - self.y_min)
        return QPointF(plot.left() + fx * plot.width(), plot.bottom() - fy * plot.height())

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot()
        font = mono(11)
        p.setFont(font)
        metrics = p.fontMetrics()
        has_data = any(item.points for item in self.series)
        for fraction, label in self._y_ticks():
            y = plot.bottom() - fraction * plot.height()
            p.setPen(QPen(qc(C.soft), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(qc(C.ink3))
            p.drawText(QRectF(0, y - 8, plot.left() - 8, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
        if self.threshold_in_range():
            y = self._point(plot, self.x_min, self.threshold).y()
            pen = QPen(qc(C.ink3), 1)
            pen.setDashPattern([5, 5])
            p.setPen(pen)
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        for item in self.series:
            if not item.points:
                continue
            pen = QPen(qc(tone(item.tone)), item.width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            if item.dashed:
                pen.setDashPattern([6, 4])
            p.setPen(pen)
            path, open_run, run_length, last = QPainterPath(), False, 0, None
            for x, y in zip(item.xs, item.ys):
                if y is None:
                    if open_run and run_length == 1 and last is not None:
                        p.drawPoint(last)
                    open_run, run_length = False, 0
                    continue
                last = self._point(plot, float(x), float(y))
                if open_run:
                    path.lineTo(last)
                else:
                    path.moveTo(last)
                    open_run = True
                run_length += 1
            if len(item.points) == 1 and last is not None:
                p.setBrush(qc(tone(item.tone)))
                p.drawEllipse(last, 2.5, 2.5)
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        if self.threshold_label:
            text = self.threshold_label
            width = metrics.horizontalAdvance(text) + 8
            if self.threshold_in_range():
                y = self._point(plot, self.x_min, self.threshold).y()
                top = min(max(plot.top(), y - 18), plot.bottom() - 16)
            else:
                top = plot.top()
            rect = QRectF(plot.right() - width - 2, top, width, 16)
            p.fillRect(rect, qc(C.raised))
            p.setPen(qc(C.ink3))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        if not has_data:
            p.setPen(qc(C.ink3))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.empty_text)
        if self.x_labels:
            p.setPen(qc(C.ink3))
            count = len(self.x_labels)
            baseline = self.height() - 5
            for index, label in enumerate(self.x_labels):
                width = metrics.horizontalAdvance(label)
                if count == 1:
                    x = plot.left()
                else:
                    x = plot.left() + index * (plot.width() - width) / (count - 1)
                p.drawText(QPointF(x, baseline), label)
        p.end()


def axis_labels(low: float, high: float, count: int = 5, fmt: Callable[[float], str] | None = None) -> list[str]:
    """`count` evenly spaced labels between two real axis ends."""

    fmt = fmt or (lambda value: f"{value:g}")
    if count < 2 or high <= low:
        return [fmt(low)]
    return [fmt(low + index * (high - low) / (count - 1)) for index in range(count)]


__all__ = ["ChartSeries", "SeriesChart", "axis_labels"]
