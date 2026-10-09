"""Rebuild both desktop ICO files from OrionQ's native Bloch-sphere satellite Q.

Run with the console extra installed: python tools/generate_console_icon.py
The interface and icon call the same vector painter. Each resolution is painted
independently by Qt, with legible simplified geometry for the smallest icons.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import struct

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QRegion

from quantum_autopilot.console.widgets.brand_mark import DESKTOP_ICON_COLOR, paint_bloch_q


ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render_mark(size: int) -> QImage:
    """Use the same square vector glyph as BlochQMark on transparent ground."""

    image = QImage(size, size, QImage.Format.Format_ARGB32)
    # PNG density metadata otherwise inherits the current screen/application
    # DPI, changing ICO bytes even when the vector pixels are identical.
    image.setDotsPerMeterX(3780)
    image.setDotsPerMeterY(3780)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_bloch_q(painter, QRectF(0, 0, size, size), DESKTOP_ICON_COLOR)
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    encoded = QByteArray()
    stream = QBuffer(encoded)
    stream.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(stream, "PNG"):
        raise RuntimeError("Qt could not encode the OrionQ icon")
    stream.close()
    return bytes(encoded)


def icon_bytes() -> bytes:
    """PNG-backed 32-bit ICO entries supported by Qt and Windows Vista onward."""

    frames = [png_bytes(render_mark(size)) for size in SIZES]
    offset = 6 + 16 * len(frames)
    directory = []
    for size, frame in zip(SIZES, frames):
        directory.append(struct.pack("<BBBBHHII", size % 256, size % 256,
                                     0, 0, 1, 32, len(frame), offset))
        offset += len(frame)
    return struct.pack("<HHH", 0, 1, len(frames)) + b"".join(directory) + b"".join(frames)


def save_previews(directory: Path) -> None:
    """Render actual interface widgets and actual icon frames for visual review."""
    from PySide6.QtWidgets import QApplication, QWidget

    from quantum_autopilot.console import theme
    from quantum_autopilot.console.widgets.brand_mark import BlochQMark, OrionQWordmark

    app = QApplication.instance() or QApplication([])
    directory.mkdir(parents=True, exist_ok=True)
    for mode in ("dark", "light"):
        theme.set_theme(mode)
        theme.install(app)
        for name, widget in (("wordmark", OrionQWordmark(90)), ("q", BlochQMark(256))):
            preview = QImage(widget.width() + 32, widget.height() + 32, QImage.Format.Format_ARGB32)
            preview.fill(QColor(theme.resolve(theme.C.bg)))
            painter = QPainter(preview)
            widget.render(painter, QPoint(16, 16), QRegion(), QWidget.RenderFlag.DrawChildren)
            painter.end()
            (directory / (name + "-" + mode + ".png")).write_bytes(png_bytes(preview))
            widget.close()
        # These are the ICO's actual independently rendered resolutions, placed
        # at native pixel size on both Windows-like light and dark surfaces.
        preview = QImage(sum(SIZES) + 24 * (len(SIZES) + 1), 288, QImage.Format.Format_ARGB32)
        preview.fill(QColor(theme.resolve(theme.C.bg)))
        painter = QPainter(preview)
        left = 24
        for size in SIZES:
            painter.drawImage(QPoint(left, (288 - size) // 2), render_mark(size))
            left += size + 24
        painter.end()
        (directory / ("icon-sizes-" + mode + ".png")).write_bytes(png_bytes(preview))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", type=Path, help="Also save a 256px transparent PNG for inspection")
    parser.add_argument("--preview-dir", type=Path, help="Save themed native wordmark/Q and icon-size previews")
    args = parser.parse_args()
    encoded = icon_bytes()
    for path in (ROOT / "src/quantum_autopilot/console/assets/orion-q.ico",
                 ROOT / "packaging/console.ico"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
        print(f"{path}: {len(encoded)} bytes, sizes {SIZES}")
    if args.preview:
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        args.preview.write_bytes(png_bytes(render_mark(256)))
    if args.preview_dir:
        save_previews(args.preview_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
