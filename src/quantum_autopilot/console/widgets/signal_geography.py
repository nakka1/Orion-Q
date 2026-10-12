"""Vector scenery for EVA mode; independent of observations and hit geometry."""
from __future__ import annotations

from functools import lru_cache
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QTransform

from ..theme import current_theme

SIGNAL_CROSS_RED = "#F02D40"
SIGNAL_CROSS_OPACITY = 0.25
SIGNAL_CROSS_INNER_RED = "#FF8B78"
# Shared geographic scenery, never a device or a physical structure. The
# reference extent defines only the decoration's visual zoom scale.
SIGNAL_CROSS_LONLAT = (0.0, 55.0)
SIGNAL_CROSS_HEIGHT_M = 2_548_400.0


@lru_cache(maxsize=1)
def signal_cross_vector() -> tuple[float, float, float]:
    longitude, latitude = map(math.radians, SIGNAL_CROSS_LONLAT)
    return (6371.0 * math.cos(latitude) * math.cos(longitude),
            6371.0 * math.cos(latitude) * math.sin(longitude),
            6371.0 * math.sin(latitude))


def signal_map_ocean() -> str:
    return "#651F2A" if current_theme() == "dark" else "#C96E77"


def signal_globe_ocean() -> tuple[str, str, str]:
    """Ocean, shadow and limb; terrestrial paint keeps its normal palette."""
    if current_theme() == "dark":
        return "#963744", "#4B1720", "#B87C83"
    return "#CD737D", "#923A46", "#85444E"


@lru_cache(maxsize=1)
def _unit_cross() -> QPainterPath:
    """Three tapered tips and a slender shaft with a gently flared base."""
    path = QPainterPath()
    path.moveTo(0.5, 0.0)
    path.cubicTo(0.518, 0.03, 0.514, 0.18, 0.535, 0.265)
    path.cubicTo(0.70, 0.268, 0.90, 0.25, 1.0, 0.29)
    path.cubicTo(0.89, 0.316, 0.71, 0.31, 0.54, 0.325)
    path.cubicTo(0.53, 0.55, 0.54, 0.80, 0.55, 1.0)
    path.lineTo(0.45, 1.0)
    path.cubicTo(0.46, 0.80, 0.47, 0.55, 0.46, 0.325)
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


def map_cross_rect(anchor: QPointF, px_per_m: float) -> QRectF:
    """Scale with the map while the base stays on the geographic anchor."""
    cross_height = max(0.0, SIGNAL_CROSS_HEIGHT_M * px_per_m)
    cross_width = cross_height * 0.72
    return QRectF(anchor.x() - cross_width / 2.0, anchor.y() - cross_height, cross_width, cross_height)


def globe_cross_rect(anchor: QPointF, radius: float) -> QRectF:
    """Use the projected geographic base, never the viewport's north limb."""
    cross_height = max(54.0, radius * SIGNAL_CROSS_HEIGHT_M / 6_371_000.0)
    cross_width = cross_height * 0.72
    return QRectF(anchor.x() - cross_width / 2.0, anchor.y() - cross_height, cross_width, cross_height)


def paint_signal_cross(painter: QPainter, rect: QRectF) -> None:
    if rect.isEmpty():
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    colour = QColor(SIGNAL_CROSS_RED)
    colour.setAlphaF(SIGNAL_CROSS_OPACITY)
    inner = QColor(SIGNAL_CROSS_INNER_RED)
    inner.setAlphaF(SIGNAL_CROSS_OPACITY)
    # A single fill keeps the glow entirely inside the silhouette. Constant
    # alpha preserves transparency; no halo, blur, extra layer or timer is used.
    glow = QLinearGradient(QPointF(rect.left(), rect.center().y()),
                           QPointF(rect.right(), rect.center().y()))
    for position, shade in ((0., colour), (.43, colour), (.5, inner),
                            (.57, colour), (1., colour)):
        glow.setColorAt(position, shade)
    painter.setBrush(glow)
    painter.drawPath(signal_cross_path(rect))
    painter.restore()


__all__ = ["SIGNAL_CROSS_INNER_RED", "SIGNAL_CROSS_OPACITY", "SIGNAL_CROSS_RED", "SIGNAL_CROSS_LONLAT",
           "SIGNAL_CROSS_HEIGHT_M", "signal_cross_vector", "globe_cross_rect", "map_cross_rect",
           "paint_signal_cross", "signal_cross_path", "signal_globe_ocean", "signal_map_ocean"]
