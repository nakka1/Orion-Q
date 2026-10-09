"""Session signal branding adds a red tapered cross without changing the default logo."""
from __future__ import annotations

from math import floor
from pathlib import Path
import runpy

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QRegion, QTransform  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from quantum_autopilot.console import branding, theme  # noqa: E402
from quantum_autopilot.console.widgets import brand_mark  # noqa: E402
from quantum_autopilot.console.widgets.brand_mark import (  # noqa: E402
    BlochQMark, DESKTOP_ICON_COLOR, OrionQWordmark, SIGNAL_ICON_SIZES, SIGNAL_Q_COLOR,
    paint_bloch_q, signal_app_icon,
)


ROOT = Path(__file__).resolve().parents[1]
DISPLAY_SIZES = (16, 30, 72, 256)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def appearance(app):
    original = theme.current_theme()
    theme.install(app)
    yield
    theme.set_theme(original)
    theme.install(app)


def _glyph(size, *, signal=False, color=None):
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_bloch_q(painter, QRectF(0, 0, size, size), color, signal=signal)
    painter.end()
    return image


def _widget_frame(widget):
    image = QImage(widget.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    widget.render(painter, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    painter.end()
    return image


def test_default_icon_still_rebuilds_to_approved_packaged_bytes(app):
    generator = runpy.run_path(str(ROOT / "tools/generate_console_icon.py"))
    approved = branding.ICON_PATH.read_bytes()
    assert generator["icon_bytes"]() == approved
    assert approved == (ROOT / "packaging/console.ico").read_bytes()
    for size in SIGNAL_ICON_SIZES:
        implicit = QImage(size, size, QImage.Format.Format_ARGB32)
        implicit.fill(Qt.GlobalColor.transparent)
        painter = QPainter(implicit)
        paint_bloch_q(painter, QRectF(0, 0, size, size), DESKTOP_ICON_COLOR)
        painter.end()
        assert implicit == _glyph(size, color=DESKTOP_ICON_COLOR, signal=False)


@pytest.mark.parametrize("mode", ("dark", "light"))
@pytest.mark.parametrize("size", DISPLAY_SIZES)
def test_signal_glyph_is_red_sharp_and_contained_on_both_themes(app, mode, size):
    theme.set_theme(mode)
    theme.install(app)
    widget = BlochQMark(size, color="#8B5CF6")
    try:
        assert not widget.signal_mode
        widget.set_signal_mode(True)
        assert widget.signal_mode and "cruz" in widget.accessibleName()
        image = _widget_frame(widget)
        assert image == _glyph(size, signal=True)
        red = QColor(SIGNAL_Q_COLOR)
        pixels = [image.pixelColor(x, y) for y in range(size) for x in range(size)]
        assert sum(pixel.alpha() > 100 for pixel in pixels) > size ** 2 * .08
        assert any(pixel.alpha() > 150 and pixel.name() == red.name() for pixel in pixels)
        assert all(pixel.alpha() < 20 or pixel.red() > pixel.green() * 2 for pixel in pixels)
        assert all(image.pixelColor(x, y).alpha() == 0
                   for x, y in ((0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1)))
        # The original sphere has slight antialiasing at a 16px border; no
        # fully painted contour is cut off at any edge, including the cross.
        edge_pixels = [image.pixelColor(x, y).alpha() for x in range(size)
                       for y in (0, size - 1)]
        edge_pixels += [image.pixelColor(x, y).alpha() for y in range(size)
                        for x in (0, size - 1)]
        assert max(edge_pixels) < 200
        # The diagonal crossbar and outer stem retain visible red coverage even
        # in the compact 16px silhouette.
        assert any(image.pixelColor(x, y).alpha() > 100
                   for x in range(round(size * .70), round(size * .90))
                   for y in range(round(size * .76), round(size * .94)))
    finally:
        widget.close()


@pytest.mark.parametrize("mode", ("dark", "light"))
@pytest.mark.parametrize("height", DISPLAY_SIZES)
def test_native_wordmark_preserves_orion_and_uses_exact_signal_q_painter(app, mode, height):
    theme.set_theme(mode)
    theme.install(app)
    wordmark = OrionQWordmark(height)
    try:
        normal = _widget_frame(wordmark)
        normal_size = wordmark.size()
        wordmark.set_signal_mode(True)
        signal = _widget_frame(wordmark)
        assert wordmark.size() == normal_size and wordmark.accessibleName() == "OrionQ"
        assert wordmark.signal_mode
        q_left = wordmark._q_left * height / 100
        left = floor(q_left)
        assert normal.copy(QRect(0, 0, left, height)) == signal.copy(QRect(0, 0, left, height))
        expected = QImage(wordmark.size(), QImage.Format.Format_ARGB32)
        expected.fill(Qt.GlobalColor.transparent)
        painter = QPainter(expected)
        paint_bloch_q(painter, QRectF(q_left, 0, height, height), signal=True)
        painter.end()
        q_region = QRect(left, 0, wordmark.width() - left, height)
        assert signal.copy(q_region) == expected.copy(q_region)
        assert all(signal.pixelColor(wordmark.width() - 1, y).alpha() == 0 for y in range(height))
        wordmark.set_signal_mode(False)
        assert not wordmark.signal_mode and _widget_frame(wordmark) == normal
    finally:
        wordmark.close()


@pytest.mark.parametrize("compact", (False, True))
def test_cross_base_joins_sphere_then_tapers_outward_with_pointed_arms(app, compact):
    image = QImage(512, 512, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(8, 8)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(SIGNAL_Q_COLOR))
    polygon = brand_mark._signal_cross(compact=compact)
    # Inspect the actual rendered silhouette in a frame aligned to the drawn
    # reference: 37 degrees from vertical, rooted at the sphere's lower rim.
    aligned = QTransform(.8, .6, -.6, .8, 7.2, -79.6).map(polygon)
    painter.drawPolygon(aligned)
    painter.end()
    assert QRectF(0, 0, 100, 100).contains(polygon.boundingRect())

    def stem_width(outward):
        y = round((10 + outward) * 8)
        return sum(image.pixelColor(x, y).alpha() > 200 for x in range(12 * 8, 28 * 8))

    def arm_width(across):
        x = round((20 + across) * 8)
        return sum(image.pixelColor(x, y).alpha() > 200 for y in range(31 * 8, 39 * 8))

    assert stem_width(1) > stem_width(12) > stem_width(21) > stem_width(32) > 0
    assert arm_width(3) > arm_width(8) > arm_width(10) > 0
    assert arm_width(-3) > arm_width(-8) > arm_width(-10) > 0
    assert stem_width(35) == arm_width(14) == arm_width(-14) == 0


def test_signal_tail_has_no_gap_between_sphere_and_crossbar(app):
    image = _glyph(512, signal=True)
    for outward in range(2, 31):
        x = round((64 + .6 * outward) * 5.12)
        y = round((64 + .8 * outward) * 5.12)
        assert image.pixelColor(x, y).alpha() > 200


def test_signal_keeps_the_existing_bloch_sphere_geometry(app):
    original = _glyph(256)
    signal = _glyph(256, signal=True)
    # Sphere, equator, meridian and state arrow retain the exact alpha shape.
    for y in range(round(256 * .6)):
        for x in range(round(256 * .62)):
            assert original.pixelColor(x, y).alpha() == signal.pixelColor(x, y).alpha()


def test_signal_mode_belongs_only_to_the_widget_and_can_restore_its_accent(app):
    marked = BlochQMark(72, color="#1234ab")
    untouched = BlochQMark(72, color="#1234ab")
    try:
        normal = _widget_frame(marked)
        marked.set_signal_mode(True)
        assert marked.signal_mode and not untouched.signal_mode
        assert _widget_frame(untouched) == normal
        marked.set_signal_mode(False)
        assert _widget_frame(marked) == normal
        assert "satélite" in marked.accessibleName()
    finally:
        marked.close()
        untouched.close()


def test_runtime_icon_reuses_signal_vectors_and_never_rewrites_packaged_icons(app):
    before = branding.ICON_PATH.read_bytes()
    icon = signal_app_icon()
    assert not icon.isNull()
    assert {size.width() for size in icon.availableSizes()} == set(SIGNAL_ICON_SIZES)
    for size in SIGNAL_ICON_SIZES:
        assert icon.pixmap(size, size).toImage().convertToFormat(QImage.Format.Format_ARGB32) == _glyph(size, signal=True)
    assert branding.ICON_PATH.read_bytes() == before


@pytest.mark.parametrize("widget_type", (BlochQMark, OrionQWordmark))
def test_newly_shown_brand_uses_nearest_boolean_owner_property(app, widget_type):
    owner = QWidget()
    owner.setProperty("orionSignalBrand", True)
    child = QWidget(owner)
    brand = widget_type(72, parent=child)
    standalone = widget_type(72)
    standalone.set_signal_mode(True)
    try:
        owner.show()
        child.show()
        brand.show()
        standalone.show()
        app.processEvents()
        assert brand.signal_mode and standalone.signal_mode
        # A nearer explicit false owner also wins, without any global state.
        brand.hide()
        child.setProperty("orionSignalBrand", False)
        brand.show()
        app.processEvents()
        assert not brand.signal_mode and standalone.signal_mode
        # Parentless widgets retain either their explicit or normal mode.
        standalone.set_signal_mode(False)
        standalone.hide()
        standalone.show()
        app.processEvents()
        assert not standalone.signal_mode
    finally:
        owner.close()
        standalone.close()
