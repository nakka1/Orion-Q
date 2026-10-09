"""Responsive canvas: the scene follows the window, never a fixed aspect ratio.

Small windows scroll instead of shrinking the reference typography. Windows
larger than the 1440 x 900 reference frame zoom uniformly. Qt applies monitor DPI.
"""
from __future__ import annotations

from math import ceil

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QTransform
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QLayout, QWidget

from .theme import C, DESIGN_H, DESIGN_W, MIN_CONTENT_H, MIN_CONTENT_W, qc


class ResponsiveStage(QGraphicsView):
    first_frame_painted = Signal()

    def __init__(self, content: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.content = content
        self.user_zoom = 1.0
        self._laying_out = False
        self._first_frame_painted = False
        content.setMinimumSize(MIN_CONTENT_W, MIN_CONTENT_H)
        if content.layout():
            content.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        scene = QGraphicsScene(self)
        scene.setBackgroundBrush(qc(C.bg))
        self.proxy = scene.addWidget(content)
        self.setScene(scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(qc(C.bg))
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)

    def _arrange(self):
        if self._laying_out:
            return
        self._laying_out = True
        try:
            # Scrollbar visibility changes the viewport and triggers another
            # resizeEvent. Using that changing size for automatic zoom makes
            # fractional scales alternate forever (e.g. 3096 x 1219 at 96 DPI).
            # Decide zoom and overflow from the whole available client area.
            area = self.contentsRect()
            width, height = max(1, area.width()), max(1, area.height())
            # Zoom is relative to the 1440 x 900 reference frame, so that frame renders
            # at the prototype's own type sizes; smaller windows keep scale 1 and reflow.
            automatic = max(1.0, min(3.0, width / DESIGN_W, height / DESIGN_H))
            scale = max(.75, min(4.0, automatic * self.user_zoom))
            hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
            need_h = need_v = False
            for _ in range(3):
                available_w = width - (vbar.sizeHint().width() if need_v else 0)
                available_h = height - (hbar.sizeHint().height() if need_h else 0)
                need_h = MIN_CONTENT_W * scale > available_w + 1e-7
                need_v = MIN_CONTENT_H * scale > available_h + 1e-7
            for setter, needed in ((self.setHorizontalScrollBarPolicy, need_h),
                                   (self.setVerticalScrollBarPolicy, need_v)):
                # An exact-fit floating-point scene must not introduce a bar
                # merely because Qt rounds its transformed bounds by one pixel.
                setter(Qt.ScrollBarPolicy.ScrollBarAlwaysOn if needed else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            available_w = max(1, width - (vbar.sizeHint().width() if need_v else 0))
            available_h = max(1, height - (hbar.sizeHint().height() if need_h else 0))
            logical_w = max(MIN_CONTENT_W, available_w / scale)
            logical_h = max(MIN_CONTENT_H, available_h / scale)
            # 900.0000000000001 is an exact 900-pixel logical height, not 901.
            size = QSize(ceil(logical_w - 1e-7), ceil(logical_h - 1e-7))
            if self.content.size() != size:
                self.content.resize(size)
            if self.proxy.size().toSize() != size:
                self.proxy.resize(size.width(), size.height())
            rect = QRectF(0, 0, logical_w, logical_h)
            if self.sceneRect() != rect:
                self.scene().setSceneRect(rect)
            transform = QTransform.fromScale(scale, scale)
            if self.transform() != transform:
                self.setTransform(transform)
        finally:
            self._laying_out = False

    def change_zoom(self, step: float):
        self.user_zoom = max(0.75, min(2.0, self.user_zoom + step))
        self._arrange()

    def reset_zoom(self):
        self.user_zoom = 1.0
        self._arrange()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._first_frame_painted:
            self._first_frame_painted = True
            self.first_frame_painted.emit()


ScaledStage = ResponsiveStage  # backwards-compatible import
__all__ = ["ResponsiveStage", "ScaledStage"]
