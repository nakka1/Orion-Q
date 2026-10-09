"""A brief, local EVA display sequence drawn entirely with native Qt vectors.

This widget has no connection to authentication, services or device commands.
Only its owner decides when to start the cosmetic sequence and what to show
after ``finished``. The optional WAV is an original, locally synthesized tone.
"""
from __future__ import annotations

from functools import lru_cache
from math import sin, tau
from pathlib import Path
import weakref

from PySide6.QtCore import QElapsedTimer, QEvent, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget


EMERGENCY_TITLE = "EMERGENCY"
EMERGENCY_MESSAGE = "YOU SHOULD NOT BE ABLE TO CONNECT TO EVA"
EMERGENCY_SOUND = Path(__file__).resolve().parents[1] / "assets/emergency-interface.wav"
SEQUENCE_DURATION_MS = 4700

_BLACK = QColor("#090807")
_RED = QColor("#f12c16")
_DIM_RED = QColor("#74170d")
_ORANGE = QColor("#ffac1f")
_PALE = QColor("#ffd78c")


def _ease(value: float) -> float:
    value = max(0., min(1., value))
    return 1. - (1. - value) ** 3


def _alpha(colour: QColor, opacity: float) -> QColor:
    result = QColor(colour)
    result.setAlphaF(max(0., min(1., opacity)))
    return result


@lru_cache(maxsize=2)
def _message_path(text: str) -> QPainterPath:
    title_family, label_family = _sequence_fonts()
    font = QFont(title_family if text == EMERGENCY_TITLE else label_family)
    font.setPixelSize(120)
    font.setWeight(QFont.Weight.Black if text == EMERGENCY_TITLE else QFont.Weight.DemiBold)
    if text == EMERGENCY_TITLE:
        font.setStretch(140)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3. if text == EMERGENCY_TITLE else 1.6)
    path = QPainterPath()
    path.addText(0., 0., font, text)
    return path


@lru_cache(maxsize=1)
def _sequence_fonts() -> tuple[str, str]:
    """Use packaged outlines even when Qt has no system font database."""
    families = []
    directory = Path(__file__).resolve().parents[1] / "fonts"
    for filename in ("Oswald.ttf", "JetBrainsMono.ttf"):
        ident = QFontDatabase.addApplicationFont(str(directory / filename))
        loaded = QFontDatabase.applicationFontFamilies(ident) if ident >= 0 else []
        families.append(loaded[0] if loaded else QFont().family())
    return families[0], families[1]


def _path_text(painter: QPainter, text: str, box: QRectF, colour: QColor) -> None:
    path = _message_path(text)
    bounds = path.boundingRect()
    scale = min(box.width() / bounds.width(), box.height() / bounds.height())
    painter.save()
    painter.translate(box.center())
    painter.scale(scale, scale)
    painter.translate(-bounds.center())
    painter.fillPath(path, colour)
    painter.restore()


def _label(painter: QPainter, x: float, y: float, text: str, *, size=18, colour=_RED) -> None:
    font = QFont(_sequence_fonts()[1])
    font.setPixelSize(size)
    font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(font)
    painter.setPen(colour)
    painter.drawText(QPointF(x, y), text)


def _panel(painter: QPainter, rect: QRectF, cut=28.) -> None:
    polygon = QPolygonF([
        rect.topLeft(), QPointF(rect.right() - cut, rect.top()),
        QPointF(rect.right(), rect.top() + cut), rect.bottomRight(),
        QPointF(rect.left() + cut, rect.bottom()), QPointF(rect.left(), rect.bottom() - cut),
    ])
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0., QColor("#2b0c08"))
    gradient.setColorAt(1., QColor("#0e0908"))
    painter.setBrush(gradient)
    painter.setPen(QPen(_RED, 2.))
    painter.drawPolygon(polygon)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    inset = rect.adjusted(9., 9., -9., -9.)
    painter.setPen(QPen(_DIM_RED, 1.))
    painter.drawLine(inset.topLeft(), QPointF(inset.right() - cut, inset.top()))
    painter.drawLine(QPointF(inset.left() + cut, inset.bottom()), inset.bottomRight())


def _hazard_band(painter: QPainter, rect: QRectF, reveal: float, offset: float) -> None:
    painter.save()
    center = rect.center().x()
    painter.setClipRect(QRectF(center - rect.width() * reveal / 2, rect.top(),
                              rect.width() * reveal, rect.height()))
    painter.fillRect(rect, _ORANGE)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_BLACK)
    x = rect.left() - rect.height() - 80 + offset
    while x < rect.right() + 80:
        painter.drawPolygon(QPolygonF([
            QPointF(x, rect.bottom()), QPointF(x + 30, rect.bottom()),
            QPointF(x + 30 + rect.height(), rect.top()), QPointF(x + rect.height(), rect.top()),
        ]))
        x += 74
    painter.restore()


_SEGMENTS = {
    "0": "abcedf", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd", "6": "afgecd", "7": "abc", "8": "abcdefg", "9": "abfgcd",
}


def _readout(painter: QPainter, x: float, y: float, text: str) -> None:
    """Angular seven-segment geometry remains sharp at any device ratio."""
    segments = {
        "a": [(9, 0), (47, 0), (53, 6), (47, 12), (9, 12), (3, 6)],
        "g": [(9, 49), (47, 49), (53, 55), (47, 61), (9, 61), (3, 55)],
        "d": [(9, 98), (47, 98), (53, 104), (47, 110), (9, 110), (3, 104)],
        "b": [(50, 11), (56, 5), (56, 48), (50, 54), (44, 48), (44, 17)],
        "c": [(50, 60), (56, 66), (56, 105), (50, 99), (44, 93), (44, 66)],
        "e": [(6, 60), (12, 66), (12, 93), (6, 99), (0, 105), (0, 66)],
        "f": [(6, 11), (12, 17), (12, 48), (6, 54), (0, 48), (0, 5)],
    }
    painter.save()
    painter.translate(x, y)
    painter.setPen(Qt.PenStyle.NoPen)
    for char in text:
        if char in ":.":
            painter.setBrush(_RED)
            for dot_y in ((35, 76) if char == ":" else (103,)):
                painter.drawRect(QRectF(2, dot_y, 9, 9))
            painter.translate(25, 0)
            continue
        for segment, points in segments.items():
            painter.setBrush(_RED if segment in _SEGMENTS.get(char, "") else QColor("#29110b"))
            painter.drawPolygon(QPolygonF([QPointF(px, py) for px, py in points]))
        painter.translate(72, 0)
    painter.restore()


def paint_emergency_signal(painter: QPainter, rect: QRectF, progress: float) -> None:
    """Paint a deterministic vector frame; progress runs from zero to one.

    The center messages scale uniformly, while the frame and warning stripes
    extend to the available viewport. No raster assets or theme colors are used.
    """
    if rect.width() <= 0 or rect.height() <= 0:
        return
    progress = max(0., min(1., progress))
    fade = _ease(progress / .07) * (1. - _ease((progress - .85) / .15))
    scale = min(rect.width() / 1280., rect.height() / 900.)
    width, height = rect.width() / scale, rect.height() / scale
    middle, top = width / 2., (height - 900.) / 2.
    assembly = _ease(progress / .19)
    bands = _ease((progress - .09) / .15)
    message = _ease((progress - .18) / .15)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setOpacity(fade)
    painter.translate(rect.topLeft())
    painter.scale(scale, scale)
    painter.fillRect(QRectF(0, 0, width, height), _BLACK)
    painter.translate(0, top)

    glow = QLinearGradient(QPointF(0, 0), QPointF(width, 900))
    glow.setColorAt(0., QColor("#3a0c06"))
    glow.setColorAt(.42, QColor("#100908"))
    glow.setColorAt(1., QColor("#200b07"))
    painter.fillRect(QRectF(32, 28, width - 64, 844), glow)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(_DIM_RED, 1.))
    painter.drawRect(QRectF(32, 28, width - 64, 844))
    for corner_x, direction in ((32, 1), (width - 32, -1)):
        painter.setPen(QPen(_RED, 4.))
        for corner_y, vertical in ((28, 1), (872, -1)):
            painter.drawLine(QPointF(corner_x, corner_y), QPointF(corner_x + direction * 76, corner_y))
            painter.drawLine(QPointF(corner_x, corner_y), QPointF(corner_x, corner_y + vertical * 48))

    # Upper panels glide into place independently of the center warning.
    panel_width = min(middle - 102., 840.)
    painter.save()
    painter.translate(-(1. - assembly) * middle, -(1. - assembly) * 80)
    left = QRectF(58., 58., panel_width, 224.)
    _panel(painter, left)
    _label(painter, 84, 88, "LOCAL SIGNAL // EVA", size=21)
    remaining = max(0, round((1. - progress) * SEQUENCE_DURATION_MS / 10))
    _readout(painter, 84, 112, f"00:{remaining // 100:02d}.{remaining % 100:02d}")
    _label(painter, 84, 259, "MONITOR / 00     RETURN PATH / 04", size=17)
    painter.restore()

    painter.save()
    painter.translate((1. - assembly) * middle, -(1. - assembly) * 60)
    right_x = width - 58. - panel_width
    _panel(painter, QRectF(right_x, 58, panel_width, 224))
    _label(painter, right_x + 25, 88, "SIGNAL GEOMETRY // 04", size=21)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for index in range(7):
        y = 113 + index * 19
        line = QPainterPath(QPointF(right_x + 26, y))
        line.lineTo(right_x + panel_width * .56 - index * 7, y)
        line.lineTo(right_x + panel_width * .68 - index * 5, y + 38)
        line.lineTo(right_x + panel_width - 32, y + 38)
        painter.setPen(QPen(_alpha(_RED, .4 + index * .075), 2.))
        painter.drawPath(line)
        painter.fillRect(QRectF(right_x + 20, y - 3, 4, 6), _ORANGE)
    painter.restore()

    drift = sin(progress * tau * 1.25) * 7
    _hazard_band(painter, QRectF(0, 309, width, 58), bands, drift)
    _hazard_band(painter, QRectF(0, 538, width, 58), bands, -drift)
    painter.fillRect(QRectF(0, 369, width, 167), _BLACK)
    painter.save()
    painter.setClipRect(QRectF(middle - width * message / 2, 369, width * message, 167))
    title_width = min(width - 140., 1320.)
    _path_text(painter, EMERGENCY_TITLE, QRectF(middle - title_width / 2, 382, title_width, 103),
               _ORANGE)
    _path_text(painter, EMERGENCY_MESSAGE,
               QRectF(middle - min(width - 160., 1360.) / 2, 499, min(width - 160., 1360.), 29), _PALE)
    painter.restore()
    painter.setPen(QPen(_alpha(_ORANGE, message * .7), 1.))
    painter.drawLine(QPointF(middle - title_width / 2, 490), QPointF(middle + title_width / 2, 490))

    # Lower panels carry only decorative display telemetry and angular motifs.
    painter.save()
    painter.translate(0, (1. - assembly) * 280)
    lower_width = min(middle - 112., 690.)
    _panel(painter, QRectF(58, 642, lower_width, 185))
    _label(painter, 84, 675, "DISPLAY ARRAY // EVA", size=21)
    motif_x = 58 + lower_width / 2
    for center_x, center_y in ((motif_x - 103, 748), (motif_x + 103, 748), (motif_x, 708)):
        painter.setBrush(QColor("#5f160c"))
        painter.setPen(QPen(_RED, 2.))
        painter.drawPolygon(QPolygonF([
            QPointF(center_x - 68, center_y - 22), QPointF(center_x + 45, center_y - 22),
            QPointF(center_x + 68, center_y + 1), QPointF(center_x + 68, center_y + 28),
            QPointF(center_x - 45, center_y + 28), QPointF(center_x - 68, center_y + 5),
        ]))
    _label(painter, 84, 804, "VECTOR / 07     PHASE / 04", size=17)
    lower_right = width - 58 - lower_width
    _panel(painter, QRectF(lower_right, 642, lower_width, 185))
    _label(painter, lower_right + 25, 675, "CHANNEL // MONITOR", size=21)
    for row in range(2):
        y = 710 + row * 64
        _label(painter, lower_right + 25, y + 17, f"0{row + 1}", size=35)
        for index in range(18):
            brightness = .18 + .63 * (.5 + .5 * sin(index * .65 + progress * tau + row))
            painter.fillRect(QRectF(lower_right + 105 + index * 15, y - 12, 9, 36),
                             _alpha(_RED, brightness))
        painter.setPen(QPen(_DIM_RED, 1.))
        painter.drawLine(QPointF(lower_right + 25, y + 33), QPointF(width - 87, y + 33))
    painter.restore()

    _label(painter, 60, 858, "EVA // LOCAL DISPLAY", size=15, colour=_alpha(_RED, .72))
    painter.setPen(QPen(_DIM_RED, 2.))
    painter.drawLine(QPointF(middle - 125, 851), QPointF(middle + 125, 851))
    painter.setPen(QPen(_ORANGE, 2.))
    painter.drawLine(QPointF(middle - 125, 851), QPointF(middle - 125 + 250 * progress, 851))
    painter.restore()


class EmergencySignalOverlay(QWidget):
    """A viewport overlay with a bounded timer, input barrier and optional sound.

    ``start()`` restarts the sequence. Natural completion or Escape emits
    ``finished`` once, after teardown. ``stop()`` silently cancels it so an
    owner can shut down or switch surfaces without triggering its finish flow.
    Hiding an active overlay also ends the sequence and emits ``finished``.
    ``completed`` is true only when the full sequence reaches its natural end.
    """

    finished = Signal()

    def __init__(self, parent=None, *, duration_ms=SEQUENCE_DURATION_MS, sound_enabled=True):
        super().__init__(parent)
        self.setObjectName("evaEmergencySignal")
        self.setAccessibleName(EMERGENCY_TITLE)
        self.setAccessibleDescription(EMERGENCY_MESSAGE)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self._duration_ms = max(1, int(duration_ms))
        self._sound_enabled = bool(sound_enabled)
        self._sound = None
        self._sound_checked = False
        self._running = False
        self.completed = False
        self._input_filter_installed = False
        self._previous_focus = None
        self._progress = 0.
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        if parent is not None:
            parent.installEventFilter(self)
            self.setGeometry(parent.rect())
        self.hide()

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        self.stop()
        self._progress = 0.
        self._running = True
        focused = QApplication.focusWidget()
        self._previous_focus = weakref.ref(focused) if focused is not None else None
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()

    def stop(self) -> None:
        self._running = False
        self.completed = False
        self._timer.stop()
        if self._sound is not None:
            self._sound.stop()
        app = QApplication.instance()
        if self._input_filter_installed and app is not None:
            app.removeEventFilter(self)
        self._input_filter_installed = False
        self.hide()
        previous = self._previous_focus() if self._previous_focus is not None else None
        self._previous_focus = None
        if previous is not None:
            try:
                if previous.isVisible() and previous.isEnabled():
                    previous.setFocus(Qt.FocusReason.OtherFocusReason)
            except RuntimeError:
                pass  # The owner may have replaced the entry surface meanwhile.

    def _finish(self, *, completed=False) -> None:
        if not self._running:
            return
        self.stop()
        self.completed = bool(completed)
        self.finished.emit()

    def _play_sound(self) -> None:
        if not self._sound_enabled:
            return
        if not self._sound_checked:
            self._sound_checked = True
            try:
                from PySide6.QtMultimedia import QMediaDevices, QSoundEffect
                if EMERGENCY_SOUND.is_file() and QMediaDevices.audioOutputs():
                    self._sound = QSoundEffect(self)
                    self._sound.setVolume(.18)
                    self._sound.setLoopCount(1)
                    self._sound.setSource(QUrl.fromLocalFile(str(EMERGENCY_SOUND)))
            except (ImportError, OSError, RuntimeError):
                self._sound = None
        if self._sound is not None:
            try:
                self._sound.play()
            except RuntimeError:
                pass

    def _tick(self) -> None:
        if not self._running or not self.isVisible():
            self._finish()
            return
        self._progress = min(1., self._clock.elapsed() / self._duration_ms)
        if self._progress >= 1.:
            self._finish(completed=True)
        else:
            self.update()

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

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.setGeometry(watched.rect())
            if self._running:
                self.raise_()
        if self._running and self.isVisible() and event.type() in (
            QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.Shortcut,
            QEvent.Type.ShortcutOverride, QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick,
            QEvent.Type.Wheel, QEvent.Type.ContextMenu, QEvent.Type.TouchBegin,
            QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd, QEvent.Type.InputMethod,
            QEvent.Type.TabletPress, QEvent.Type.TabletRelease, QEvent.Type.DragEnter,
            QEvent.Type.DragMove, QEvent.Type.Drop,
        ):
            ancestor = watched
            while ancestor is not None and not isinstance(ancestor, QWidget):
                ancestor = ancestor.parent()
            if ancestor is not None and ancestor.window() is self.window():
                if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                    self._finish()
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        paint_emergency_signal(painter, QRectF(self.rect()), self._progress)
        painter.end()


__all__ = ["EMERGENCY_MESSAGE", "EMERGENCY_SOUND", "EMERGENCY_TITLE",
           "EmergencySignalOverlay", "SEQUENCE_DURATION_MS", "paint_emergency_signal"]
