"""Local disconnect display: cached native hexagons, then an opaque black frame.

The overlay only draws and temporarily owns input. Its owner clears the local
display mode and returns to login after ``finished``; no service is contacted.
"""
from __future__ import annotations

from functools import lru_cache
from math import ceil, cos, sin, sqrt, tau
from pathlib import Path
import weakref

from PySide6.QtCore import QElapsedTimer, QEvent, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget

from .. import theme


DISCONNECT_MESSAGE = "Disconnected from E.V.A."
DISCONNECT_SOUND = Path(__file__).resolve().parents[1] / "assets/disconnect-interface.wav"
DISCONNECT_DURATION_MS = 3600
BLACK_START = .92
BLACK_HOLD_MS = 180


def _ease(value: float) -> float:
    value = max(0., min(1., value))
    return value * value * (3. - 2. * value)


@lru_cache(maxsize=1)
def _message_path() -> QPainterPath:
    theme.load_bundled_fonts()
    path = QPainterPath()
    # Inter's variable outlines contain overlapping contours; preserve them
    # instead of punching square gaps where component contours overlap.
    path.setFillRule(Qt.FillRule.WindingFill)
    path.addText(0., 0., theme.text(120, 700), DISCONNECT_MESSAGE)
    return path


@lru_cache(maxsize=12)
def _hexagons(width: float, height: float):
    """Geometry is constructed once per aspect ratio, never per animation tick."""
    radius = 25.
    row_step = sqrt(3.) * radius
    column_step = radius * 1.5
    result = []
    for column in range(-1, ceil(width / column_step) + 2):
        x = column * column_step
        for row in range(-1, ceil(height / row_step) + 2):
            y = row * row_step + (column % 2) * row_step / 2
            # Stable integer mixing gives varied activation without random state.
            value = ((column + 103) * 73856093 ^ (row + 107) * 19349663) & 0xffff
            rank = value / 65535.
            distance = min(1., abs(x - width / 2.) / max(1., width / 2.))
            outer = QPolygonF([
                QPointF(x + (radius - .8) * cos(tau * index / 6),
                        y + (radius - .8) * sin(tau * index / 6))
                for index in range(6)
            ])
            inner = QPolygonF([
                QPointF(x + (radius - 4.) * cos(tau * index / 6),
                        y + (radius - 4.) * sin(tau * index / 6))
                for index in range(6)
            ])
            glyph = QPainterPath()
            for offset in (-5., 0., 5.):
                glyph.moveTo(x + offset - 1.4, y - 2.4)
                glyph.lineTo(x + offset, y)
                glyph.lineTo(x + offset + 1.4, y - 2.4)
            glyph.moveTo(x - 7., y + 5.)
            glyph.lineTo(x + 7., y + 5.)
            result.append((outer, inner, glyph, rank, distance))
    return tuple(result)


def paint_disconnect_signal(painter: QPainter, rect: QRectF, progress: float) -> None:
    """Draw one reproducible frame; the last eight percent is strictly black."""
    if rect.width() <= 0 or rect.height() <= 0:
        return
    progress = max(0., min(1., progress))
    painter.save()
    painter.setClipRect(rect)
    painter.setOpacity(1.)
    painter.fillRect(rect, Qt.GlobalColor.black)
    if progress <= 0 or progress >= BLACK_START:
        painter.restore()
        return
    scale = min(rect.width() / 1280., rect.height() / 720.)
    width = round(rect.width() / scale, 4)
    height = round(rect.height() / scale, 4)
    painter.translate(rect.topLeft())
    painter.scale(scale, scale)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for outer, inner, glyph, rank, distance in _hexagons(width, height):
        on = _ease((progress - rank * .30 - distance * .08) / .10)
        off = 1. - _ease((progress - .60 - rank * .21) / .11)
        opacity = on * off
        if opacity <= .001:
            continue
        painter.setOpacity(opacity)
        painter.setPen(QPen(QColor("#ff4030"), .8))
        painter.setBrush(QColor(round(137 + rank * 118), 3, 0))
        painter.drawPolygon(outer)
        painter.setPen(QPen(QColor("#210000"), .7))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(inner)
        painter.setPen(QPen(QColor("#350000"), 1.1))
        painter.drawPath(glyph)

    band = _ease((progress - .30) / .12) * (1. - _ease((progress - .69) / .15))
    if band > 0:
        painter.setOpacity(band)
        band_rect = QRectF(0., height * .40, width, height * .20)
        painter.fillRect(band_rect, QColor("#090000"))
        painter.setPen(QPen(QColor("#ff3a25"), 1.5))
        painter.drawLine(band_rect.topLeft(), band_rect.topRight())
        painter.drawLine(band_rect.bottomLeft(), band_rect.bottomRight())
        path = _message_path()
        bounds = path.boundingRect()
        box = QRectF(width * .05, height * .45, width * .9, height * .10)
        text_scale = min(box.width() / bounds.width(), box.height() / bounds.height())
        painter.save()
        painter.translate(box.center())
        painter.scale(text_scale, text_scale)
        painter.translate(-bounds.center())
        painter.fillPath(path, QColor("#fff5ed"))
        painter.restore()
    painter.restore()


_INPUT_EVENTS = frozenset((
    QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.Shortcut,
    QEvent.Type.ShortcutOverride, QEvent.Type.MouseButtonPress,
    QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick,
    QEvent.Type.Wheel, QEvent.Type.ContextMenu, QEvent.Type.TouchBegin,
    QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd, QEvent.Type.InputMethod,
    QEvent.Type.TabletPress, QEvent.Type.TabletRelease, QEvent.Type.DragEnter,
    QEvent.Type.DragMove, QEvent.Type.Drop,
))


def _inside_owner(widget: QWidget, owner: QWidget) -> bool:
    """Include fields on a logical canvas embedded through QGraphicsProxyWidget."""
    if widget.window() is owner.window():
        return True
    ancestor = widget
    while ancestor is not None:
        proxy = ancestor.graphicsProxyWidget()
        if proxy is not None and proxy.scene() is not None:
            if any(view.window() is owner.window() for view in proxy.scene().views()):
                return True
        ancestor = ancestor.parentWidget()
    return False


class DisconnectSignalOverlay(QWidget):
    """Bounded full-window display with a silent explicit cancellation path.

    Escape and other inputs are consumed while playing. ``stop()`` cancels
    silently; external hiding cancels and emits ``finished`` with ``completed``
    false. Natural completion emits once after showing black for at least 180 ms.
    Timers and both event filters are released on stop, hide or disposal. The
    optional original shutdown tone loads asynchronously and never delays login.
    """

    finished = Signal()

    def __init__(self, parent=None, *, duration_ms=DISCONNECT_DURATION_MS, sound_enabled=True):
        super().__init__(parent)
        self.setObjectName("evaDisconnectSignal")
        self.setAccessibleName(DISCONNECT_MESSAGE)
        self.setAccessibleDescription("Local display disconnect animation; no remote connection.")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: black;")
        self._duration_ms = max(1, int(duration_ms))
        self._sound_enabled = bool(sound_enabled)
        self._sound = None
        self._sound_checked = False
        self._sound_requested = False
        self._sound_played = False
        self._sound_ready_status = None
        self._sound_error_status = None
        self._running = False
        self.completed = False
        self._disposed = False
        self._input_filter_installed = False
        self._parent_filter_installed = False
        self._previous_focus = None
        self._progress = 0.
        self._black_since = None
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        if parent is not None:
            self.setGeometry(parent.rect())
        self.hide()

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._disposed:
            raise RuntimeError("A disposed disconnect display cannot be restarted.")
        self.stop()
        self._running = True
        self._progress = 0.
        self._black_since = None
        focused = QApplication.focusWidget()
        self._previous_focus = weakref.ref(focused) if focused is not None else None
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
            parent.installEventFilter(self)
            self._parent_filter_installed = True
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()

    def stop(self) -> None:
        self._running = False
        self.completed = False
        self._timer.stop()
        self._sound_requested = False
        self._sound_played = False
        if self._sound is not None:
            try:
                self._sound.stop()
            except RuntimeError:
                pass
        app = QApplication.instance()
        if self._input_filter_installed and app is not None:
            app.removeEventFilter(self)
        self._input_filter_installed = False
        parent = self.parentWidget()
        if self._parent_filter_installed and parent is not None:
            parent.removeEventFilter(self)
        self._parent_filter_installed = False
        self.hide()
        previous = self._previous_focus() if self._previous_focus is not None else None
        self._previous_focus = None
        if previous is not None:
            try:
                if previous.isVisible() and previous.isEnabled():
                    previous.setFocus(Qt.FocusReason.OtherFocusReason)
            except RuntimeError:
                pass

    def dispose(self) -> None:
        if self._disposed:
            return
        self.stop()
        self._disposed = True
        self._release_sound()

    def _release_sound(self) -> None:
        sound, self._sound = self._sound, None
        self._sound_requested = False
        if sound is not None:
            try:
                sound.statusChanged.disconnect(self._sound_status_changed)
            except (RuntimeError, TypeError):
                pass
            try:
                sound.stop()
            except RuntimeError:
                pass
            try:
                sound.deleteLater()
            except RuntimeError:
                pass

    def _finish(self, *, completed=False) -> None:
        if not self._running:
            return
        self.stop()
        self.completed = bool(completed)
        self.finished.emit()

    def _tick(self) -> None:
        if not self._running or not self.isVisible():
            self._finish()
            return
        elapsed = self._clock.elapsed()
        self._progress = min(1., elapsed / self._duration_ms)
        self.update()
        if (self._progress >= 1. and self._black_since is not None
                and elapsed - self._black_since >= BLACK_HOLD_MS):
            self._finish(completed=True)

    def _play_sound(self) -> None:
        if not self._sound_enabled or not self._running or not self.isVisible():
            return
        self._sound_requested = True
        if not self._sound_checked:
            self._sound_checked = True
            try:
                from PySide6.QtMultimedia import QMediaDevices, QSoundEffect
                if DISCONNECT_SOUND.is_file() and QMediaDevices.audioOutputs():
                    self._sound = QSoundEffect(self)
                    self._sound_ready_status = QSoundEffect.Status.Ready
                    self._sound_error_status = QSoundEffect.Status.Error
                    self._sound.setVolume(.24)
                    self._sound.setLoopCount(1)
                    self._sound.statusChanged.connect(self._sound_status_changed)
                    self._sound.setSource(QUrl.fromLocalFile(str(DISCONNECT_SOUND)))
            except (ImportError, OSError, RuntimeError):
                self._release_sound()
        self._sound_status_changed()

    def _sound_status_changed(self) -> None:
        # Readiness may arrive after the visual sequence finishes. Never queue
        # a late sound, replay it on repaint, or hold the black/login transition.
        if (self._sound is None or not self._sound_requested or self._sound_played
                or not self._running or not self.isVisible() or self._disposed):
            return
        try:
            status = self._sound.status()
            if status == self._sound_error_status:
                self._sound_requested = False
            elif status == self._sound_ready_status:
                self._sound_played = True
                self._sound.play()
        except RuntimeError:
            self._sound_requested = False

    def showEvent(self, event):
        super().showEvent(event)
        if self._running:
            self._clock.start()
            app = QApplication.instance()
            if app is not None and not self._input_filter_installed:
                app.installEventFilter(self)
                self._input_filter_installed = True
            self._timer.start()
            self._play_sound()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()
        if self._running:
            self._finish()

    def closeEvent(self, event):
        self.dispose()
        super().closeEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.setGeometry(watched.rect())
            if self._running:
                self.raise_()
        if self._running and self.isVisible() and event.type() in _INPUT_EVENTS:
            ancestor = watched
            while ancestor is not None and not isinstance(ancestor, QWidget):
                ancestor = ancestor.parent()
            if ancestor is not None and _inside_owner(ancestor, self):
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        paint_disconnect_signal(painter, QRectF(self.rect()), self._progress)
        painter.end()
        # Start the hold only once black was actually painted. A delayed or
        # suspended paint queue must never let timer ticks skip the black frame.
        if self._running and self._progress >= BLACK_START and self._black_since is None:
            self._black_since = self._clock.elapsed()


__all__ = ["BLACK_HOLD_MS", "BLACK_START", "DISCONNECT_DURATION_MS", "DISCONNECT_MESSAGE", "DISCONNECT_SOUND",
           "DisconnectSignalOverlay", "paint_disconnect_signal"]
