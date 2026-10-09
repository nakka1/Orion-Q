"""Native icon edges use quantized premultiplied colour, with alpha enforced."""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QImage

from quantum_autopilot.console.desktop_validation import compare_icon_pixels


def _image(colour):
    image = QImage(2, 2, QImage.Format.Format_RGBA8888)
    image.fill(QColor(0, 0, 0, 0))
    # Include an opaque pixel so low-alpha edge checks cannot pass solely by
    # losing the icon's visible coverage.
    image.setPixelColor(0, 0, QColor(193, 95, 60, 255))
    image.setPixelColor(1, 1, QColor(*colour))
    return image


@pytest.mark.parametrize("alpha", [1, 3, 7, 16, 64, 128])
def test_low_alpha_native_quantization_matches_without_relaxing_tolerance(alpha):
    expected = _image((193, 95, 60, alpha))
    native = expected.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    # Simulate the old native capture's lossy unpremultiply. At alpha=3 the
    # straight RGB differs by 25 even though its visual contribution is exact.
    roundtrip = native.convertToFormat(QImage.Format.Format_RGBA8888)
    if alpha <= 16:
        before = expected.pixelColor(1, 1)
        after = roundtrip.pixelColor(1, 1)
        assert max(abs(x - y) for x, y in zip(before.getRgb()[:3], after.getRgb()[:3])) > 2
    for actual in (native, roundtrip):
        result = compare_icon_pixels(actual, expected)
        assert result["passed"]
        assert result["comparison_space"] == "RGBA8888_Premultiplied"
        assert result["channel_tolerance"] == 2
        assert result["mismatched_alpha_pixels"] == 0
        assert result["max_alpha_difference"] == 0
        assert result["max_colour_difference"] == 0


@pytest.mark.parametrize("alpha", [0, 3, 16, 128, 255])
def test_alpha_mismatch_is_checked_at_every_opacity(alpha):
    expected = _image((193, 95, 60, alpha))
    actual = _image((193, 95, 60, alpha + 3 if alpha <= 252 else alpha - 3))
    result = compare_icon_pixels(actual, expected)
    assert not result["passed"]
    assert result["mismatched_alpha_pixels"] == 1
    assert result["max_alpha_difference"] == 3
    assert result["channel_tolerance"] == 2


@pytest.mark.parametrize("alpha", [3, 16, 128, 255])
def test_visible_colour_mismatch_is_rejected_including_low_alpha(alpha):
    expected = _image((255, 0, 0, alpha))
    actual = _image((0, 0, 255, alpha))
    result = compare_icon_pixels(actual, expected)
    assert not result["passed"]
    assert result["mismatched_alpha_pixels"] == 0
    assert result["mismatched_colour_pixels"] == 1
    assert result["max_colour_difference"] == alpha


def test_opaque_colour_tolerance_boundary_is_unchanged():
    expected = _image((193, 95, 60, 255))
    assert compare_icon_pixels(_image((195, 93, 62, 255)), expected)["passed"]
    result = compare_icon_pixels(_image((196, 95, 60, 255)), expected)
    assert not result["passed"]
    assert result["max_colour_difference"] == 3


def test_coverage_and_dimensions_cannot_pass_by_colour_conversion():
    transparent = QImage(2, 2, QImage.Format.Format_RGBA8888)
    transparent.fill(QColor(255, 0, 255, 0))
    expected = _image((193, 95, 60, 255))
    lost_coverage = compare_icon_pixels(transparent, expected)
    assert not lost_coverage["passed"]
    assert lost_coverage["mismatched_alpha_pixels"] == 2
    assert not compare_icon_pixels(transparent, transparent)["passed"]
    wrong_size = QImage(3, 2, QImage.Format.Format_ARGB32_Premultiplied)
    assert compare_icon_pixels(wrong_size, expected) == {
        "passed": False, "reason": "image dimensions differ"}
