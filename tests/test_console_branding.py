"""OrionQ's interface and shell icons share one vector glyph and stable identity."""
from __future__ import annotations

import ctypes
from pathlib import Path
import struct
from types import SimpleNamespace
import runpy

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QRegion  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from quantum_autopilot.console import branding  # noqa: E402
from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.theme import TOKENS  # noqa: E402
from quantum_autopilot.console.widgets.brand_mark import (  # noqa: E402
    BlochQMark, DESKTOP_ICON_COLOR, OrionQWordmark,
)
from quantum_autopilot.console.widgets.chrome import TopBar  # noqa: E402
from quantum_autopilot.product import PRODUCT_NAME  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def icon_frames():
    encoded = branding.ICON_PATH.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", encoded)
    assert (reserved, kind, count) == (0, 1, len(ICON_SIZES))
    frames = {}
    for index in range(count):
        width, height, _, _, planes, bits, length, offset = struct.unpack_from("<BBBBHHII", encoded, 6 + 16 * index)
        size = width or 256
        assert (height or 256) == size and (planes, bits) == (1, 32)
        frame = QImage.fromData(encoded[offset:offset + length])
        assert frame.size() == QSize(size, size)
        frames[size] = frame
    return frames


def test_executable_and_runtime_icons_share_every_resolution(app):
    assert branding.ICON_PATH.read_bytes() == (ROOT / "packaging/console.ico").read_bytes()
    assert set(icon_frames()) == set(ICON_SIZES)
    icon = branding.app_icon()
    assert not icon.isNull()
    assert {size.width() for size in icon.availableSizes()} == set(ICON_SIZES)
    for size in ICON_SIZES:
        assert not icon.pixmap(QSize(size, size)).isNull()


@pytest.mark.parametrize("size", ICON_SIZES)
def test_every_ico_frame_is_pixel_identical_to_the_real_interface_widget(app, size):
    expected = QImage(size, size, QImage.Format.Format_ARGB32)
    expected.fill(Qt.GlobalColor.transparent)
    widget = BlochQMark(size, color=DESKTOP_ICON_COLOR)
    painter = QPainter(expected)
    # Only the logo paint; the transparent icon has no inherited QWidget ground.
    widget.render(painter, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    painter.end()
    assert icon_frames()[size].convertToFormat(expected.format()) == expected
    widget.close()


def test_icons_rebuild_reproducibly_from_the_shared_vector_painter(app):
    generator = runpy.run_path(str(ROOT / "tools/generate_console_icon.py"))
    assert generator["icon_bytes"]() == branding.ICON_PATH.read_bytes()


def luminance(colour):
    channels = QColor(colour).getRgbF()[:3]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
    return sum(weight * value for weight, value in zip((.2126, .7152, .0722), linear))


def test_every_icon_keeps_an_open_sphere_and_visible_satellite_on_both_themes(app):
    foreground = luminance(DESKTOP_ICON_COLOR)
    for mode in ("dark", "light"):
        ground = luminance(TOKENS[mode]["bg"])
        contrast = (max(foreground, ground) + .05) / (min(foreground, ground) + .05)
        assert contrast >= 3.
    for size, image in icon_frames().items():
        assert image.pixelColor(0, 0).alpha() == 0
        pixels = [image.pixelColor(x, y) for y in range(size) for x in range(size)]
        assert sum(pixel.alpha() > 150 for pixel in pixels) >= size * size * .075
        assert sum(pixel.alpha() == 0 for pixel in pixels) > size * size * .4
        # At 16px antialiased edge coverage touches additional pixels, while
        # more than half the image still has negligible opacity.
        assert sum(pixel.alpha() < 16 for pixel in pixels) > size * size * .5
        # The satellite sits diagonally beyond the sphere's lower right arc.
        assert any(image.pixelColor(x, y).alpha() > 200
                   for x in range(int(.7 * size), size) for y in range(int(.7 * size), size))


@pytest.mark.parametrize("mode", ("dark", "light"))
@pytest.mark.parametrize("height", (30, 72, 90))
def test_wordmark_is_named_orionq_and_fits_header_and_entry_surfaces(app, mode, height):
    original = theme.current_theme()
    theme.set_theme(mode)
    theme.install(app)
    widget = OrionQWordmark(height)
    try:
        assert widget.accessibleName() == PRODUCT_NAME == "OrionQ"
        assert widget.height() == height and 3. < widget.width() / height < 3.6
        image = QImage(widget.size(), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        widget.render(painter, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
        painter.end()
        # Both the readable 'Orion' outlines and the accented Q are painted.
        colours = {image.pixelColor(x, y).name() for y in range(height) for x in range(widget.width())
                   if image.pixelColor(x, y).alpha() > 245}
        assert TOKENS[mode]["ink"] in colours and TOKENS[mode]["acc"] in colours
        assert all(image.pixelColor(widget.width() - 1, y).alpha() == 0 for y in range(height))
    finally:
        widget.close()
        theme.set_theme(original)
        theme.install(app)


def test_main_header_reuses_the_vector_wordmark(app):
    header = TopBar()
    try:
        assert isinstance(header.brand, OrionQWordmark)
        assert header.brand.accessibleName() == PRODUCT_NAME
    finally:
        header.close()


def test_windows_identity_uses_a_stable_typed_shell_call(monkeypatch):
    calls = []

    class Setter:
        def __call__(self, app_id):
            calls.append(app_id)
            return 0

    setter = Setter()
    monkeypatch.setattr(branding, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: SimpleNamespace(
        SetCurrentProcessExplicitAppUserModelID=setter), raising=False)
    assert branding.configure_windows_app_identity() is True
    assert calls == ["ORION-Q.Desktop"]
    assert setter.argtypes == [ctypes.c_wchar_p]
    assert setter.restype is ctypes.c_long


def test_non_windows_identity_never_loads_a_windows_library(monkeypatch):
    monkeypatch.setattr(branding, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: pytest.fail("Windows library called"),
                        raising=False)
    assert branding.configure_windows_app_identity() is False


def test_failed_shell_identity_is_reported_without_blocking_the_interface(monkeypatch, caplog):
    def failed_load(*args, **kwargs):
        raise OSError("shell unavailable")

    monkeypatch.setattr(branding, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ctypes, "WinDLL", failed_load, raising=False)
    assert branding.configure_windows_app_identity() is False
    assert "shell unavailable" in caplog.text


def test_missing_runtime_icon_is_reported_clearly(app, monkeypatch, tmp_path):
    monkeypatch.setattr(branding, "ICON_PATH", tmp_path / "missing.ico")
    with pytest.raises(FileNotFoundError, match="Required OrionQ desktop icon missing"):
        branding.app_icon()
