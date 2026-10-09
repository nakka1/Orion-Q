"""OrionQ's native vector wordmark and shared Bloch-sphere satellite Q.

The icon and interface call the same painter. Small icons retain the sphere,
equator and satellite; larger surfaces add a meridian and state-vector arrow.
"""
from __future__ import annotations

from math import ceil

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget

from quantum_autopilot.product import PRODUCT_NAME

from ..theme import C, TOKENS, qc, text


# A native shell icon cannot follow the application theme. This accent remains
# visible against both light and dark Windows surfaces.
DESKTOP_ICON_COLOR = TOKENS["light"]["acc"]
SIGNAL_Q_COLOR = "#E53935"
SIGNAL_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _owner_signal_mode(widget: QWidget) -> bool | None:
    """Read the nearest explicit owner flag; standalone widgets stay independent."""
    owner = widget.parentWidget()
    while owner is not None:
        enabled = owner.property("orionSignalBrand")
        if isinstance(enabled, bool):
            return enabled
        owner = owner.parentWidget()
    return None


def _signal_cross(compact: bool = False) -> QPolygonF:
    """A diagonal Q tail tapering from the sphere into a pointed cross.

    Coordinates below use the tail's local frame: transverse width, then
    distance outward from its broad attachment to the sphere. The crossbar
    sits near the outer end, with both arms narrowing to their tips.
    """
    if compact:
        points = ((-5.2, 0), (5.2, 0), (2.6, 22.1), (13, 25),
                  (2.3, 27.9), (0, 34), (-2.3, 27.9), (-13, 25), (-2.6, 22.1))
    else:
        points = ((-4.6, 0), (4.6, 0), (1.8, 22), (1.8, 22.8),
                  (7, 24), (11, 25), (7, 26), (1.6, 27.2), (1.05, 30),
                  (0, 34), (-1.05, 30), (-1.6, 27.2), (-7, 26), (-11, 25),
                  (-7, 24), (-1.8, 22.8), (-1.8, 22))
    # Unit direction (3, 4)/5 gives a 37-degree diagonal from the vertical.
    return QPolygonF([QPointF(64 + .8 * across + .6 * outward,
                             64 - .6 * across + .8 * outward)
                      for across, outward in points])


def paint_bloch_q(painter: QPainter, rect: QRectF, color=None, *, signal=False) -> None:
    """Paint a square Q at any resolution without fonts or raster dependencies."""
    size = min(rect.width(), rect.height())
    if size <= 0:
        return
    colour = qc(SIGNAL_Q_COLOR if signal else C.acc if color is None else color)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.center().x() - size / 2, rect.center().y() - size / 2)
    painter.scale(size / 100., size / 100.)
    pen = QPen(colour, 6.5 if size < 24 else 4.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    # The outer circle and diagonal tail read as the letter Q. Signal mode
    # supplies its own taper from the sphere, instead of a constant-width line.
    painter.drawEllipse(QPointF(42, 42), 34, 34)
    if not signal:
        painter.drawLine(QPointF(64, 64), QPointF(80, 80))

    interior = QColor(colour)
    interior.setAlphaF(.72)
    pen.setColor(interior)
    pen.setWidthF(3.7 if size < 24 else 2.)
    painter.setPen(pen)
    painter.save()
    painter.translate(42, 42)
    painter.rotate(-14)
    painter.drawEllipse(QRectF(-32, -10, 64, 20))
    if size >= 24:
        painter.rotate(28)
        painter.drawEllipse(QRectF(-12, -32, 24, 64))
    painter.restore()
    if size >= 24:
        pen.setColor(colour)
        pen.setWidthF(2.5)
        painter.setPen(pen)
        painter.drawLine(QPointF(42, 42), QPointF(56, 24))
        painter.setBrush(colour)
        painter.drawPolygon(QPolygonF([QPointF(56, 24), QPointF(55.4, 29.2), QPointF(51.4, 26.1)]))
        painter.setBrush(Qt.BrushStyle.NoBrush)

    # Signal mode changes only the Q's accent and tail.
    painter.save()
    if signal:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawPolygon(_signal_cross(compact=size < 24))
    else:
        # Solar wings run across the Q tail; the solid bus is its orbital endpoint.
        painter.translate(80, 80)
        painter.rotate(-45)
        pen.setColor(colour)
        pen.setWidthF(3.8 if size < 24 else 2.5)
        painter.setPen(pen)
        painter.drawLine(QPointF(-10, 0), QPointF(10, 0))
        for left in (-18., 8.):
            painter.drawRect(QRectF(left, -5, 10, 10))
            if size >= 48:
                painter.drawLine(QPointF(left + 5, -5), QPointF(left + 5, 5))
        painter.setBrush(colour)
        painter.drawRoundedRect(QRectF(-3.5, -6, 7, 12), 1.2, 1.2)
    painter.restore()
    painter.restore()


class BlochQMark(QWidget):
    """Square reusable product glyph; identical to the packaged icon frames."""

    def __init__(self, size: int = 48, *, color=None, parent=None):
        super().__init__(parent)
        self.color = color
        self._signal_mode = False
        self.setFixedSize(size, size)
        self.setAccessibleName(PRODUCT_NAME + " · Q, esfera de Bloch e satélite")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    @property
    def signal_mode(self) -> bool:
        return self._signal_mode

    def set_signal_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._signal_mode:
            return
        self._signal_mode = enabled
        self.setAccessibleName(PRODUCT_NAME + " · Q, esfera de Bloch e " + ("cruz" if enabled else "satélite"))
        self.update()

    def showEvent(self, event):
        inherited = _owner_signal_mode(self)
        if inherited is not None:
            self.set_signal_mode(inherited)
        super().showEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        paint_bloch_q(painter, QRectF(self.rect()), self.color, signal=self._signal_mode)
        painter.end()


class OrionQWordmark(QWidget):
    """Native 'Orion' letter outlines followed by the shared custom Q glyph.

    Height includes the satellite tail. Natural width is approximately 3.3 times
    the height; callers can inspect sizeHint() instead of guessing text metrics.
    """

    def __init__(self, height: int = 48, *, color=None, accent=None, parent=None):
        super().__init__(parent)
        self.color, self.accent = color, accent
        self._signal_mode = False
        self._letters = QPainterPath()
        self._letters.addText(0, 0, text(100, 500), "Orion")
        bounds = self._letters.boundingRect()
        self._letter_scale = 68. / bounds.height()
        self._letter_bounds = bounds
        self._q_left = bounds.width() * self._letter_scale + 7
        self._units_width = self._q_left + 100
        self.setFixedSize(ceil(height * self._units_width / 100.), height)
        self.setAccessibleName(PRODUCT_NAME)
        self.setToolTip(PRODUCT_NAME + " · esfera de Bloch e comunicação por satélite")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    @property
    def signal_mode(self) -> bool:
        return self._signal_mode

    def set_signal_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._signal_mode:
            return
        self._signal_mode = enabled
        self.setToolTip(PRODUCT_NAME + (" · esfera de Bloch e cruz" if enabled
                                       else " · esfera de Bloch e comunicação por satélite"))
        self.update()

    def showEvent(self, event):
        inherited = _owner_signal_mode(self)
        if inherited is not None:
            self.set_signal_mode(inherited)
        super().showEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = self.height() / 100.
        painter.save()
        painter.scale(scale, scale)
        painter.save()
        painter.translate(-self._letter_bounds.left() * self._letter_scale,
                          8 - self._letter_bounds.top() * self._letter_scale)
        painter.scale(self._letter_scale, self._letter_scale)
        painter.fillPath(self._letters, qc(C.ink if self.color is None else self.color))
        painter.restore()
        # Actual display size selects the same legibility details as the icon.
        painter.restore()
        paint_bloch_q(painter, QRectF(self._q_left * scale, 0, self.height(), self.height()), self.accent,
                      signal=self._signal_mode)
        painter.end()


def signal_app_icon() -> QIcon:
    """Render the session's red Q/cross icon in memory; packaged icons stay intact."""
    icon = QIcon()
    for size in SIGNAL_ICON_SIZES:
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        paint_bloch_q(painter, QRectF(0, 0, size, size), signal=True)
        painter.end()
        icon.addPixmap(QPixmap.fromImage(image))
    return icon


__all__ = ["BlochQMark", "DESKTOP_ICON_COLOR", "OrionQWordmark", "SIGNAL_ICON_SIZES", "SIGNAL_Q_COLOR",
           "paint_bloch_q", "signal_app_icon"]
