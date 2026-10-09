"""Vector scenery for EVA mode; independent of observations and hit geometry."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QTransform

from ..theme import current_theme

SIGNAL_CROSS_RED = "#F02D40"
SIGNAL_CROSS_OPACITY = 0.25


def signal_map_ocean() -> str:
    return "#651F2A" if current_theme() == "dark" else "#C96E77"


def signal_globe_ocean() -> tuple[str, str, str]:
    """Ocean, shadow and limb; terrestrial paint keeps its normal palette."""
    if current_theme() == "dark":
        return "#963744", "#4B1720", "#B87C83"
    return "#CD737D", "#923A46", "#85444E"


@lru_cache(maxsize=1)
def _unit_cross() -> QPainterPath:
    """Three tapered tips and a broad, flared vertical base in unit bounds."""
    path = QPainterPath()
    path.moveTo(0.5, 0.0)
    path.cubicTo(0.518, 0.03, 0.514, 0.18, 0.535, 0.265)
    path.cubicTo(0.70, 0.268, 0.90, 0.25, 1.0, 0.29)
    path.cubicTo(0.89, 0.316, 0.71, 0.31, 0.54, 0.325)
    path.cubicTo(0.55, 0.55, 0.58, 0.80, 0.64, 1.0)
    path.lineTo(0.36, 1.0)
    path.cubicTo(0.42, 0.80, 0.45, 0.55, 0.46, 0.325)
    path.cubicTo(0.29, 0.31, 0.11, 0.316, 0.0, 0.29)
    path.cubicTo(0.10, 0.25, 0.30, 0.268, 0.465, 0.265)
    path.cubicTo(0.486, 0.18, 0.482, 0.03, 0.5, 0.0)
    path.closeSubpath()
    return path


def signal_cross_path(rect: QRectF) -> QPainterPath:
    transform = QTransform()
    transform.translate(rect.x(), rect.y())
    transform.scale(rect.width(), rect.height())
    return transform.map(_unit_cross())


def map_cross_rect(width: float, height: float) -> QRectF:
    cross_height = max(0.0, min(height * 0.28, width * 0.35))
    cross_width = cross_height * 0.88
    return QRectF((width - cross_width) / 2.0, max(40.0, height * 0.08), cross_width, cross_height)


def globe_cross_rect(centre: QPointF, radius: float) -> QRectF:
    cross_height = max(54.0, radius * 0.40)
    cross_width = cross_height * 0.88
    # The broad base touches the northern screen-space limb, independently of
    # camera rotation. Its arms and three tips sit outside the Earth's surface.
    base = centre.y() - radius + min(3.0, radius * 0.018)
    return QRectF(centre.x() - cross_width / 2.0, base - cross_height, cross_width, cross_height)


def paint_signal_cross(painter: QPainter, rect: QRectF) -> None:
    if rect.isEmpty():
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    colour = QColor(SIGNAL_CROSS_RED)
    colour.setAlphaF(SIGNAL_CROSS_OPACITY)
    painter.setBrush(colour)
    painter.drawPath(signal_cross_path(rect))
    painter.restore()


__all__ = ["SIGNAL_CROSS_OPACITY", "SIGNAL_CROSS_RED", "globe_cross_rect", "map_cross_rect",
           "paint_signal_cross", "signal_cross_path", "signal_globe_ocean", "signal_map_ocean"]
