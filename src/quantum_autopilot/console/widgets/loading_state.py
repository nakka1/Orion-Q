"""Native loading feedback driven only by completed work.

Five vector segments interpolate toward reported progress for at most 180 ms.
An optional pulse changes the brightness of the next unfilled segment; it never
changes the completed count or fills additional progress. Animations stop when
the widget is hidden, stopped, or destroyed.
"""
from __future__ import annotations

from math import isfinite, sin

from PySide6.QtCore import QEasingCurve, QElapsedTimer, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..theme import C, mono, qc
from .prims import L, Prose, hbox, vbox


def _counts(completed: float, total: int) -> tuple[float, int]:
    """Reject invalid measurements instead of silently inventing progress."""
    completed = float(completed)
    total_number = float(total)
    if not isfinite(completed) or not isfinite(total_number) or not total_number.is_integer() or total_number < 1:
        raise ValueError("Loading progress needs a finite completed count and positive integer total")
    total = int(total_number)
    if not 0 <= completed <= total:
        raise ValueError("Completed loading steps must be between zero and total")
    return completed, total


class SegmentedProgress(QWidget):
    """Resolution-independent five-segment progress, with no clock progress."""

    SEGMENTS = 5
    ANIMATION_MS = 180

    def __init__(self, parent=None):
        super().__init__(parent)
        self.completed, self.total = 0.0, 5
        self.fraction = 0.0
        self.displayed_fraction = 0.0
        self.color = C.acc
        self._running = False
        self._indeterminate = False
        self._pulse = 0.0
        self.setMinimumWidth(80)
        self.setFixedHeight(10)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Loading progress")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(self.ANIMATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._animate_value)
        self._pulse_clock = QElapsedTimer()
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(80)
        self._pulse_timer.timeout.connect(self._animate_pulse)
        self._update_accessibility()

    def sizeHint(self) -> QSize:
        return QSize(480, 10)

    def set_progress(self, completed: float, total: int, *, indeterminate: bool = True,
                     animate: bool = True) -> None:
        completed, total = _counts(completed, total)
        previous = self.displayed_fraction
        self._animation.stop()
        self.completed, self.total = completed, total
        self.fraction = completed / total
        self._indeterminate = bool(indeterminate)
        # A shorter/restarted run cannot retain the old operation's fill while
        # an animation descends toward the new measured target.
        self.displayed_fraction = min(previous, self.fraction)
        if animate and self.isVisible() and self.displayed_fraction < self.fraction:
            self._animation.setStartValue(self.displayed_fraction)
            self._animation.setEndValue(self.fraction)
            self._animation.start()
        else:
            self.displayed_fraction = self.fraction
        self._update_accessibility()
        self._sync_pulse()
        self.update()

    def set_fraction(self, fraction: float, color=None) -> None:
        """Compatibility with the earlier Track widget; still uses real input."""
        if color is not None:
            self.color = color
        self.set_progress(float(fraction) * self.total, self.total)

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        if not self._running:
            self._animation.stop()
            self.displayed_fraction = self.fraction
        self._sync_pulse()
        self.update()

    def stop(self) -> None:
        self.set_running(False)

    def _animate_value(self, value) -> None:
        self.displayed_fraction = max(0.0, min(self.fraction, float(value)))
        self.update()

    def _animate_pulse(self) -> None:
        self._pulse = (sin(self._pulse_clock.elapsed() / 420.0) + 1.0) / 2.0
        self.update()

    def _sync_pulse(self) -> None:
        active = self._running and self._indeterminate and self.fraction < 1 and self.isVisible()
        if active and not self._pulse_timer.isActive():
            self._pulse_clock.start()
            self._pulse_timer.start()
        elif not active:
            self._pulse_timer.stop()
            self._pulse = 0.0

    def _update_accessibility(self) -> None:
        self.setAccessibleDescription(f"{self.completed:g} of {self.total} steps completed")

    def hideEvent(self, event) -> None:
        self._animation.stop()
        self._pulse_timer.stop()
        self.displayed_fraction = self.fraction
        self._pulse = 0.0
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_pulse()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        gap = min(6.0, self.width() / 20.0)
        width = max(0.0, (self.width() - gap * (self.SEGMENTS - 1)) / self.SEGMENTS)
        top, height = (self.height() - 4.0) / 2.0, 4.0
        filled = self.displayed_fraction * self.SEGMENTS
        active = min(self.SEGMENTS - 1, int(self.fraction * self.SEGMENTS))
        for index in range(self.SEGMENTS):
            rect = QRectF(index * (width + gap), top, width, height)
            painter.fillRect(rect, qc(C.soft))
            amount = max(0.0, min(1.0, filled - index))
            if amount > 0:
                painter.fillRect(QRectF(rect.x(), top, width * amount, height), qc(self.color))
            # The pulse is a dim outline, visually distinct from solid completed
            # segments. Its width and the measured fraction do not advance.
            if index == active and self._pulse_timer.isActive() and amount == 0:
                pulse = QColor(qc(self.color))
                pulse.setAlphaF(0.18 + self._pulse * 0.26)
                painter.setPen(pulse)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        painter.end()


class LoadingStatus(QWidget):
    """Step caption and bar for startup, source changes, and pending work.

    ``set_progress(completed, total, message)`` reports actual finished work.
    It never emits ``finished``: the owner calls ``complete`` after its result
    is ready. ``start`` resets a run; ``abort`` retains the last measured count.
    """

    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "idle"
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = vbox(self, (0, 0, 0, 0), 8)
        self.progress = SegmentedProgress(self)
        layout.addWidget(self.progress)
        line = QWidget(self)
        row = hbox(line, (0, 0, 0, 0), 12)
        self.marker = L("›", mono(14), C.acc_t)
        row.addWidget(self.marker, 0, Qt.AlignmentFlag.AlignTop)
        self.message = Prose("Waiting for application service…", 13, C.ink2)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.message, 1, Qt.AlignmentFlag.AlignTop)
        self.count_label = L("0 / 5", mono(11), C.ink3)
        self.count_label.setAccessibleName("Completed loading steps")
        row.addWidget(self.count_label, 0, Qt.AlignmentFlag.AlignTop)
        self.percent = L("WAIT", mono(11), C.acc_t)
        self.percent.setAccessibleName("Loading status")
        row.addWidget(self.percent, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(line)

    def start(self, message: str = "Loading local service, evidence and model metadata…", *,
              total: int = 5, completed: float = 0) -> None:
        _counts(completed, total)
        self.state = "loading"
        self.progress.color = C.acc
        self.progress.set_running(False)
        self.progress.set_progress(completed, total, animate=False)
        self.progress.set_running(True)
        self.note(message)
        self._show_counts()

    def set_progress(self, completed: float, total: int, message: str = "", *,
                     indeterminate: bool = True) -> None:
        _counts(completed, total)
        if self.state != "loading":
            self.state = "loading"
            self.progress.color = C.acc
        self.progress.set_progress(completed, total, indeterminate=indeterminate)
        self.progress.set_running(True)
        if message:
            self.note(message)
        self._show_counts()

    def note(self, message: str) -> None:
        self.message.setText(str(message))
        self.setAccessibleDescription(str(message))

    def complete(self, message: str = "Selected data source is ready.") -> None:
        already_complete = self.state == "complete"
        self.state = "complete"
        self.progress.color = C.green
        self.progress.set_running(False)
        self.progress.set_progress(self.progress.total, self.progress.total, indeterminate=False, animate=False)
        self.note(message)
        self._show_counts()
        if not already_complete:
            self.finished.emit()

    def abort(self, message: str = "Unable to load selected data source.") -> None:
        self.state = "error"
        self.progress.stop()
        self.progress.color = C.red
        self.progress.update()
        self.note(message)
        self.percent.setText("ERROR")

    def dispose(self) -> None:
        """Explicit cleanup for owners that remove a surface before deletion."""
        self.progress.stop()

    def _show_counts(self) -> None:
        completed, total = self.progress.completed, self.progress.total
        self.count_label.setText(f"{completed:g} / {total}")
        self.percent.setText("READY" if self.state == "complete" else
                             ("WAIT" if completed == 0 else f"{self.progress.fraction:.0%}"))


__all__ = ["LoadingStatus", "SegmentedProgress"]
