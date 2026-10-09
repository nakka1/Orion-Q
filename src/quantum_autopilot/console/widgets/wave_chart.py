"""Scrolling oscilloscope trace of the stability wave of several fibres.

Data contract (`contract.LiveLink`): `wave` holds visibility samples (1 - 2·QBER of the
monitored polarisation residual) at `wave_rate_hz` (20 Hz), oldest first, for the last
12 network seconds; the backend delivers it once per network second and the last sample
belongs to the network time of that state. A fibre that is not producing data (disabled,
isolated, calibrating, no input) has an EMPTY wave.

Presentation. The chart retains a bounded prefix from preceding deliveries until those
received samples leave the visible window. Between two deliveries it moves a
presentation clock over them: at a delivery the newest second of samples is held back and
then revealed left to right during the following second, so the trace scrolls smoothly
at the sample rate. When no new delivery comes the clock stops at the last received
sample — nothing is synthesised, extrapolated or looped. A fibre with no samples has no
trace; its legend entry says why.

Series are told apart by colour AND dash pattern (and the colours differ in lightness),
so they stay distinguishable without colour vision; the legend names each one.
"""
from __future__ import annotations

import time
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from math import ceil, floor, isfinite, log10

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ..theme import C, current_theme, mono, qc
from ..display import raster_scale_for

#: (colour token, dash pattern in px or None). Order chosen so neighbours differ in hue and lightness.
SERIES_STYLES = (
    ("ink", None),
    ("acc", (7.0, 3.0)),
    ("violet", (2.0, 3.0)),
    ("green", (9.0, 3.0, 2.0, 3.0)),
    ("ink3", (4.0, 2.0)),
    ("red", (12.0, 3.0)),
    ("acc-t", (1.5, 2.5)),
    ("green-t", (6.0, 2.0, 2.0, 2.0, 2.0, 2.0)),
)
WINDOW_S = 12.0
FRAME_MS = 33            # Presentation only: the source's sampling and delivery cadence is unchanged.


def series_style(index: int) -> tuple:
    return SERIES_STYLES[index % len(SERIES_STYLES)]


def series_pen(index: int, width: float = 1.8) -> QPen:
    name, dash = series_style(index)
    pen = QPen(qc(getattr(C, name.replace("-", "_"))), width)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    if dash:
        pen.setDashPattern([value / width for value in dash])
    return pen


@dataclass(frozen=True)
class WaveSeries:
    key: str                         # link id
    label: str                       # "N-05 · CORE-N05"
    samples: tuple = ()              # the backend's `LiveLink.wave`, unmodified
    rate_hz: float = 20.0
    note: str = ""                   # why there is no trace (shown by the legend)
    times_s: tuple = ()              # explicit backend times; no invented sample cadence


class LegendSwatch(QWidget):
    """Line sample of one series: its colour, dash pattern and width."""

    def __init__(self, index: int, parent=None) -> None:
        super().__init__(parent)
        self.index = index
        self.active = True
        self.setFixedSize(34, 14)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_active(self, active: bool) -> None:
        if active != self.active:
            self.active = active
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(1.0 if self.active else 0.35)
        p.setPen(series_pen(self.index, 2.2))
        p.drawLine(QPointF(1, self.height() / 2), QPointF(self.width() - 1, self.height() / 2))
        p.end()


class WaveChart(QWidget):
    """One trace per connected fibre over the last 12 network seconds."""

    highlight_changed = Signal(str)

    def __init__(self, parent=None, *, window_s: float = WINDOW_S, animate: bool = True,
                 empty_message: str = 'No samples received', value_bounds=(0.0, 1.0)) -> None:
        super().__init__(parent)
        self.window_s = float(window_s)
        self.empty_message = empty_message
        self.value_bounds = value_bounds
        self.series: tuple = ()
        self.network_time_s: float | None = None     # network time of the newest delivery
        self._delivered_at = 0.0                     # wall clock of that delivery
        self._previous_time: float | None = None
        self._presentation_from: float | None = None
        self._duration_s = 0.0
        self._received: dict[str, tuple] = {}
        self._latest: dict[str, tuple] = {}
        self._times: dict[str, tuple] = {}
        self._latest_times: dict[str, tuple] = {}
        self._rates: dict[str, float] = {}
        self._revision = 0
        self._range = (0.0, 1.0)
        self._highlight = ""
        self._frozen: float | None = None            # fixed reveal position (tests, screenshots)
        self.animate = animate
        self._trace_key = None
        self._trace_images: list[QPixmap] = []
        self._grid_key = None
        self._grid_image: QPixmap | None = None
        self.setMinimumSize(260, 120)
        self.setMouseTracking(True)
        # paintEvent fills the whole rectangle: nothing underneath has to be repainted per frame
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._frame)
        self.setAccessibleName("Stability wave per connection, last 12 network seconds")

    # ------------------------------------------------------------------ data
    def set_series(self, series, network_time_s: float | None) -> None:
        """Replace the traces with what the backend delivered for `network_time_s`."""

        series = tuple(series)
        fresh = network_time_s != self.network_time_s
        clock = self.display_time()
        previous = self.network_time_s
        if fresh:
            self._previous_time = previous
            self.network_time_s = network_time_s
            step = network_time_s - previous if network_time_s is not None and previous is not None else 0.0
            continuous = 0 < step <= 3.0 and clock is not None and network_time_s - clock <= 3.0
            self._presentation_from = clock if continuous else network_time_s
            self._duration_s = step if continuous else 0.0
            self._delivered_at = time.monotonic()
        if series != self.series or fresh:
            keep = (network_time_s is not None and previous is not None
                    and 0 <= network_time_s - previous <= 3.0)
            received, latest, rates = {}, {}, {}
            for item in series:
                if (not item.samples or network_time_s is None or
                        (not item.times_s and item.rate_hz <= 0)):
                    continue
                if item.times_s:
                    if len(item.times_s) != len(item.samples):
                        continue
                    points = tuple(zip(item.times_s, item.samples))
                else:
                    points = tuple((network_time_s - (len(item.samples) - 1 - index) / item.rate_hz, value)
                                   for index, value in enumerate(item.samples))
                prefix = ()
                if keep and not item.times_s and self._rates.get(item.key) == item.rate_hz:
                    # The source rolls its buffer at delivery, before the on-screen
                    # clock reaches that delivery. Keep only previously received
                    # points needed by the still-visible left edge. New points
                    # replace their overlapping timestamps, including corrections.
                    prefix = tuple(point for point in self._received.get(item.key, ())
                                   if network_time_s - self.window_s - 3.0 <= point[0] < points[0][0] - 1e-9)
                received[item.key], latest[item.key] = prefix + points, points
                rates[item.key] = item.rate_hz
            self._received, self._latest, self._rates = received, latest, rates
            self._times = {key: tuple(at for at, _value in points) for key, points in received.items()}
            self._latest_times = {key: tuple(at for at, _value in points) for key, points in latest.items()}
            self.series = series
            self._revision += 1
            self._range = self._value_range()
            self._trace_key = None
            self._trace_images = []
            self.update()
        self._sync_timer()

    def keys(self) -> list:
        return [item.key for item in self.series]

    def labels(self) -> list:
        return [item.label for item in self.series]

    def set_highlight(self, key: str) -> None:
        if key != self._highlight:
            self._highlight = key
            self.highlight_changed.emit(key)
            self.update()

    @property
    def highlight(self) -> str:
        return self._highlight

    def freeze(self, reveal: float | None = 1.0) -> None:
        """Hold the presentation clock: 1.0 = everything received is shown, None = run."""

        self._frozen = reveal
        self.update()
        self._sync_timer()

    # ---------------------------------------------------------- presentation
    def reveal(self) -> float:
        """How much of the newest delivery is on screen, 0..1 (1 = all of it)."""

        if self._frozen is not None:
            return max(0.0, min(1.0, self._frozen))
        if not self.animate or self._duration_s <= 0 or self.network_time_s is None:
            return 1.0                       # first delivery: nothing was held back before it
        return max(0.0, min(1.0, (time.monotonic() - self._delivered_at) / self._duration_s))

    def display_time(self) -> float | None:
        """Network time at the right edge of the plot; never beyond the last received sample."""

        if self.network_time_s is None:
            return None
        if self._presentation_from is None or self._duration_s <= 0:
            return self.network_time_s
        return self._presentation_from + (self.network_time_s - self._presentation_from) * self.reveal()

    def visible_samples(self, key: str) -> list:
        """Received points on screen, including the preceding delivery while it scrolls out."""
        now = self.display_time()
        if now is None:
            return []
        points, times = (self._latest.get(key, ()), self._latest_times.get(key, ())) if now >= self.network_time_s else (
            self._received.get(key, ()), self._times.get(key, ()))
        return list(points[bisect_left(times, now - self.window_s - 1e-9):bisect_right(times, now + 1e-9)])

    def value_range(self) -> tuple:
        return self._range

    def set_value_bounds(self, bounds) -> None:
        """Select the actual observable's bounds; None leaves that side unbounded."""
        bounds = tuple(bounds)
        if bounds == self.value_bounds:
            return
        self.value_bounds = bounds
        self._range = self._value_range()
        self._trace_key = self._grid_key = None
        self.update()

    def _value_range(self) -> tuple:
        values = [value for points in self._received.values() for _at, value in points
                  if value is not None and isfinite(value)]
        if not values:
            return 0.0, 1.0
        low, high = min(values), max(values)
        pad = max(0.01, (high - low) * 0.12)
        lower, upper = self.value_bounds
        low, high = low - pad, high + pad
        if lower is not None:
            low = max(lower, low)
        if upper is not None:
            high = min(upper, high)
        if high - low < 0.04:
            centre = (high + low) / 2
            low, high = centre - 0.02, centre + 0.02
            if lower is not None:
                low = max(lower, low)
            if upper is not None:
                high = min(upper, high)
        return low, high

    # ---------------------------------------------------------------- events
    def _sync_timer(self) -> None:
        running = (self.animate and self._frozen is None and self.isVisible()
                   and bool(self._received) and self.reveal() < 1.0)
        if running and not self._timer.isActive():
            self._timer.start()
        elif not running and self._timer.isActive():
            self._timer.stop()

    def _frame(self) -> None:
        if self.reveal() >= 1.0:
            self._timer.stop()               # nothing new arrived: the trace stands still
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def plot_rect(self) -> QRectF:
        if self.value_bounds == (0., 1.) and not any(item.times_s for item in self.series):
            return QRectF(40, 6, max(10, self.width() - 48), max(10, self.height() - 26))
        low, high = self.value_range()
        span = max(high - low, 1e-9)
        metrics = QFontMetrics(mono(11))
        left = max(40, max(metrics.horizontalAdvance(self.axis_label(high - span * i / 4))
                           for i in range(5)) + 8)
        return QRectF(left, 6, max(10, self.width() - left - 8), max(10, self.height() - 26))

    def axis_label(self, value):
        if self.value_bounds == (0., 1.) and not any(item.times_s for item in self.series):
            return f"{value:.2f}"
        return f"{value:.3g}"

    def _prepare_traces(self, plot, low, high):
        """Rasterize received paths once; presentation only moves and clips them.

        The cache contains no QWidget content. Its key includes the complete
        received series, time, range, highlight and actual pixel density. A
        three-second left margin covers every supported reveal interval.
        """
        scale = raster_scale_for(self)
        key = (self._revision, self.network_time_s, self.window_s, self._highlight,
               plot.width(), plot.height(), low, high, scale, current_theme())
        if key == self._trace_key:
            return
        self._trace_key = key
        self._trace_images = []
        margin = plot.width() * 3.0 / self.window_s
        span = max(high - low, 1e-9)
        for index, item in enumerate(self.series):
            image = QPixmap(ceil((plot.width() + margin + 4) * scale), ceil((plot.height() + 4) * scale))
            image.setDevicePixelRatio(scale)
            image.fill(Qt.GlobalColor.transparent)
            if item.samples and (item.rate_hz > 0 or item.times_s) and self.network_time_s is not None:
                path = QPainterPath()
                first = True
                previous_at = None
                vertices = []
                for at, value in self._received.get(item.key, ()):
                    if value is None or not isfinite(value):
                        first = True
                        previous_at = None
                        continue
                    ago = self.network_time_s - at
                    extra = 1.0 / item.rate_hz if item.rate_hz > 0 else 0.0
                    if ago > self.window_s + 3.0 + extra:
                        continue
                    x = margin + plot.width() * (1.0 - ago / self.window_s) + 2
                    y = (high - value) / span * plot.height() + 2
                    vertices.append(QPointF(x, y))
                    if first or (not item.times_s and at - previous_at > 1.0 / item.rate_hz + 1e-9):
                        path.moveTo(x, y)
                    else:
                        path.lineTo(x, y)
                    first = False
                    previous_at = at
                painter = QPainter(image)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(series_pen(index, 2.8 if self._highlight == item.key else 1.8))
                painter.drawPath(path)
                if item.times_s:
                    # A received event remains visible even if it is the only
                    # event in the window; no extra points are generated.
                    for point in vertices:
                        painter.drawEllipse(point, 1.8, 1.8)
                painter.end()
            self._trace_images.append(image)

    def _grid(self, plot, low, high) -> QPixmap:
        scale = raster_scale_for(self)
        key = (self.width(), self.height(), plot.left(), self.window_s, low, high,
               any(item.times_s for item in self.series), scale, current_theme())
        if key == self._grid_key:
            return self._grid_image
        image = QPixmap(ceil(self.width() * scale), ceil(self.height() * scale))
        image.setDevicePixelRatio(scale)
        image.fill(qc(C.bg))
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        span = max(high - low, 1e-9)
        p.setFont(mono(11))
        # Keep the legacy visibility scale; other observable units need enough
        # significant digits to distinguish neighbouring ticks close to zero.
        for i in range(5):
            y = plot.top() + plot.height() * i / 4
            p.setPen(QPen(qc(C.soft), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(qc(C.ink3))
            p.drawText(QRectF(0, y - 8, plot.left() - 6, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       self.axis_label(high - span * i / 4))
        step = self.time_tick_step(plot.width())
        for index in range(int(self.window_s // step) + 1):
            i = index * step
            x = plot.right() - plot.width() * i / self.window_s
            p.setPen(QPen(qc(C.soft), 1))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            p.setPen(qc(C.ink3))
            text = "now" if i == 0 else f"−{i:g} s"
            align = Qt.AlignmentFlag.AlignRight if i == 0 else Qt.AlignmentFlag.AlignHCenter
            rect = QRectF(x - 40, plot.bottom() + 3, 40 if i == 0 else 80, 16)
            if abs(i - self.window_s) < 1e-9:
                rect, align = QRectF(x, plot.bottom() + 3, 60, 16), Qt.AlignmentFlag.AlignLeft
            p.drawText(rect, align | Qt.AlignmentFlag.AlignVCenter, text)
        p.end()
        self._grid_key, self._grid_image = key, image
        return image

    def time_tick_step(self, width):
        """Readable 1/2/5 intervals for the selected duration and visible width."""
        if self.window_s == WINDOW_S and not any(item.times_s for item in self.series):
            return 2.
        target = self.window_s / max(2, min(6, int(width / 60)))
        scale = 10. ** floor(log10(max(target, 1e-9)))
        return next(factor * scale for factor in (1., 2., 5., 10.) if factor * scale >= target)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        plot = self.plot_rect()
        low, high = self.value_range()
        p.drawPixmap(0, 0, self._grid(plot, low, high))
        now = self.display_time()
        if now is None or not any(value is not None and isfinite(value)
                                  for points in self._received.values() for _at, value in points):
            p.setPen(qc(C.ink3))
            p.setFont(mono(11.5))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter,
                       self.empty_message if self.series else "No connection")
            p.end()
            return
        p.setClipRect(plot.adjusted(0, -2, 1, 2))
        self._prepare_traces(plot, low, high)
        margin = plot.width() * 3.0 / self.window_s
        shift = (self.network_time_s - now) / self.window_s * plot.width()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        order = sorted(range(len(self.series)), key=lambda i: self.series[i].key == self._highlight)
        for index in order:
            item = self.series[index]
            times = self._times.get(item.key, ())
            end = bisect_right(times, now + 1e-9)
            start = bisect_left(times, now - self.window_s - 1e-9)
            if end - start < (1 if item.times_s else 2):
                continue
            emphasised = self._highlight == item.key
            p.setOpacity(1.0 if not self._highlight or emphasised else 0.28)
            # Clip the future at the last received visible vertex. A real point
            # outside the left border still lets its segment scroll out normally.
            right = plot.right() - (now - times[end - 1]) / self.window_s * plot.width()
            p.save()
            p.setClipRect(QRectF(plot.left(), plot.top() - 2, max(0.0, right - plot.left()), plot.height() + 4),
                          Qt.ClipOperation.IntersectClip)
            p.drawPixmap(QPointF(plot.left() - margin - 2 + shift, plot.top() - 2), self._trace_images[index])
            p.restore()
        p.setOpacity(1.0)
        p.end()


__all__ = ["FRAME_MS", "LegendSwatch", "SERIES_STYLES", "WINDOW_S", "WaveChart", "WaveSeries", "series_pen",
           "series_style"]
