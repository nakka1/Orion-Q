"""The local EVA animation covers its viewport and always releases its timer/input."""
from __future__ import annotations

from array import array
import builtins
from pathlib import Path
import runpy
import wave

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QInputMethodEvent, QKeySequence, QPainter, QShortcut  # noqa: E402
from PySide6.QtTest import QSignalSpy, QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QWidget  # noqa: E402

from quantum_autopilot.console.widgets.emergency_signal import (  # noqa: E402
    EMERGENCY_MESSAGE, EMERGENCY_SOUND, EMERGENCY_TITLE, EmergencySignalOverlay,
    SEQUENCE_DURATION_MS, paint_emergency_signal,
)
from quantum_autopilot.console.widgets import emergency_signal  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def viewport(app):
    parent = QWidget()
    parent.resize(960, 480)
    parent.show()
    app.processEvents()
    yield parent
    parent.close()
    parent.deleteLater()
    app.processEvents()


def _frame(width, height, progress):
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_emergency_signal(painter, QRectF(0, 0, width, height), progress)
    painter.end()
    return image


@pytest.mark.parametrize("width,height", ((960, 480), (1920, 1080), (3440, 1440)))
def test_native_vectors_keep_messages_and_full_width_hazard_bands_visible(app, width, height):
    image = _frame(width, height, .55)
    assert image.size().width() == width and image.size().height() == height
    assert image.pixelColor(0, 0).alpha() == 255
    # Uniform canvas scaling keeps the central message within every viewport.
    scale = min(width / 1280, height / 900)
    canvas_top = (height - 900 * scale) / 2
    title_y = round(canvas_top + 431 * scale)
    title_left, title_right = round(width / 2 - 660 * scale), round(width / 2 + 660 * scale)
    bright_title = sum(image.pixelColor(x, title_y).red() > 230
                      and image.pixelColor(x, title_y).green() > 110
                      for x in range(max(0, title_left), min(width, title_right)))
    assert bright_title > min(width, 1320 * scale) * .18
    subtitle_y = round(canvas_top + 514 * scale)
    assert any(image.pixelColor(x, subtitle_y).green() > 140 for x in range(width))
    band_y = round(canvas_top + 336 * scale)
    # Both edges of an ultrawide display get warning geometry as well as its center.
    for left, right in ((0, width // 4), (width * 3 // 4, width)):
        stripe_pixels = sum(image.pixelColor(x, band_y).green() > 120 for x in range(left, right))
        assert stripe_pixels > (right - left) * .15


def test_frame_fades_from_transparent_back_to_transparent(app):
    assert _frame(960, 480, 0).pixelColor(480, 240).alpha() == 0
    full = _frame(960, 480, .6).pixelColor(480, 240).alpha()
    fading = _frame(960, 480, .94).pixelColor(480, 240).alpha()
    assert 0 < fading < full == 255
    assert _frame(960, 480, 1).pixelColor(480, 240).alpha() == 0


def test_packaged_fonts_provide_real_letter_outlines_without_system_fonts(app):
    assert emergency_signal._sequence_fonts() == ("Oswald", "JetBrains Mono")
    title = emergency_signal._message_path(EMERGENCY_TITLE)
    subtitle = emergency_signal._message_path(EMERGENCY_MESSAGE)
    # Missing-glyph boxes would have only a handful of straight edges per character.
    assert title.elementCount() > 150
    assert subtitle.elementCount() > 600
    assert any(title.elementAt(index).isCurveTo() for index in range(title.elementCount()))


def test_sequence_finishes_once_and_leaves_no_background_timer(viewport, app):
    overlay = EmergencySignalOverlay(viewport, duration_ms=90, sound_enabled=False)
    finished = QSignalSpy(overlay.finished)
    assert not overlay.isVisible() and not overlay._timer.isActive()
    overlay.start()
    assert overlay.is_running and overlay.isVisible() and overlay._timer.isActive()
    assert overlay.geometry() == viewport.rect()
    QTest.qWait(150)
    app.processEvents()
    assert finished.count() == 1
    assert overlay.completed is True
    assert not overlay.is_running and not overlay.isVisible()
    assert not overlay._timer.isActive() and not overlay._input_filter_installed
    QTest.qWait(70)
    assert finished.count() == 1


def test_active_overlay_resizes_with_its_viewport(viewport, app):
    overlay = EmergencySignalOverlay(viewport, sound_enabled=False)
    overlay.start()
    for width, height in ((1280, 720), (3440, 1440), (960, 480)):
        viewport.resize(width, height)
        app.processEvents()
        assert overlay.geometry() == viewport.rect()
    overlay.stop()


def test_escape_releases_input_and_restores_the_previous_field(viewport, app):
    field = QLineEdit(viewport)
    field.setGeometry(20, 20, 240, 36)
    field.show()
    field.setFocus()
    app.processEvents()
    overlay = EmergencySignalOverlay(viewport, sound_enabled=False)
    finished = QSignalSpy(overlay.finished)
    overlay.start()
    # Even a programmatically targeted event cannot leak to the covered input.
    QTest.keyClicks(field, "accidental")
    assert field.text() == ""
    composition = QInputMethodEvent()
    composition.setCommitString("accidental")
    QApplication.sendEvent(field, composition)
    assert field.text() == ""
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False
    QTest.keyClick(overlay, Qt.Key.Key_Escape)
    assert finished.count() == 1 and not overlay.is_running
    assert overlay.completed is False
    assert not overlay._timer.isActive() and not overlay._input_filter_installed
    QTest.keyClicks(field, "restored")
    assert field.text() == "restored"
    assert QApplication.focusWidget() is field


def test_mouse_input_is_blocked_until_stop(viewport):
    button = QPushButton("Entry", viewport)
    button.setGeometry(20, 70, 100, 40)
    button.show()
    clicks = QSignalSpy(button.clicked)
    overlay = EmergencySignalOverlay(viewport, sound_enabled=False)
    overlay.start()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert clicks.count() == 0
    overlay.stop()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert clicks.count() == 1


def test_window_escape_shortcut_cannot_fire_through_the_overlay(viewport):
    shortcut = QShortcut(QKeySequence("Esc"), viewport)
    shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
    activated = QSignalSpy(shortcut.activated)
    overlay = EmergencySignalOverlay(viewport, sound_enabled=False)
    finished = QSignalSpy(overlay.finished)
    overlay.start()
    QTest.keyClick(overlay, Qt.Key.Key_Escape)
    assert finished.count() == 1 and not overlay.completed
    assert activated.count() == 0


def test_restart_reuses_one_timer_and_stop_is_silent(viewport):
    overlay = EmergencySignalOverlay(viewport, sound_enabled=False)
    finished = QSignalSpy(overlay.finished)
    timer = overlay._timer
    overlay.start()
    overlay._progress = .8
    overlay.start()
    assert overlay._timer is timer and overlay._progress == 0
    assert overlay._timer.isActive()
    overlay.stop()
    overlay.stop()
    assert finished.count() == 0 and not timer.isActive()
    assert not overlay.isVisible() and not overlay._input_filter_installed


def test_parent_hide_ends_sequence_and_releases_sound(viewport, app):
    class Sound:
        stops = 0

        def stop(self):
            self.stops += 1

    overlay = EmergencySignalOverlay(viewport, sound_enabled=False)
    finished = QSignalSpy(overlay.finished)
    overlay.start()
    sound = overlay._sound = Sound()
    viewport.hide()
    app.processEvents()
    assert finished.count() == 1 and sound.stops == 1
    assert not overlay.is_running and not overlay._timer.isActive()
    assert not overlay._input_filter_installed


def test_hidden_parent_never_starts_the_animation_timer(viewport, app):
    viewport.hide()
    overlay = EmergencySignalOverlay(viewport, sound_enabled=False)
    overlay.start()
    assert not overlay.isVisible() and not overlay._timer.isActive()
    viewport.show()
    app.processEvents()
    assert overlay.isVisible() and overlay._timer.isActive()
    overlay.stop()


def test_no_audio_output_or_multimedia_support_is_required(viewport, monkeypatch):
    multimedia = pytest.importorskip("PySide6.QtMultimedia")
    monkeypatch.setattr(multimedia.QMediaDevices, "audioOutputs", lambda: [])
    overlay = EmergencySignalOverlay(viewport)
    overlay.start()
    assert overlay.is_running and overlay._sound is None
    assert overlay._timer.isActive()
    overlay.stop()


def test_missing_multimedia_import_uses_the_same_silent_sequence(viewport, monkeypatch):
    original_import = builtins.__import__

    def without_multimedia(name, *args, **kwargs):
        if name == "PySide6.QtMultimedia":
            raise ImportError("optional multimedia unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_multimedia)
    overlay = EmergencySignalOverlay(viewport)
    overlay.start()
    assert overlay.is_running and overlay._sound is None
    assert overlay._timer.isActive()
    overlay.stop()


def test_messages_are_exact_and_duration_is_short(viewport):
    overlay = EmergencySignalOverlay(viewport, sound_enabled=False)
    assert EMERGENCY_TITLE == overlay.accessibleName() == "EMERGENCY"
    assert EMERGENCY_MESSAGE == overlay.accessibleDescription() == "YOU SHOULD NOT BE ABLE TO CONNECT TO EVA"
    assert 4000 <= SEQUENCE_DURATION_MS <= 5000


def test_original_local_sound_is_reproducible_short_and_soft():
    generator = runpy.run_path(str(Path(__file__).resolve().parents[1] / "tools/generate_emergency_sound.py"))
    with wave.open(str(EMERGENCY_SOUND), "rb") as audio:
        assert audio.getnchannels() == 1 and audio.getsampwidth() == 2
        assert audio.getframerate() == generator["SAMPLE_RATE"] == 44100
        duration = audio.getnframes() / audio.getframerate()
        assert 1.5 < duration < 3.
        pcm = audio.readframes(audio.getnframes())
    assert pcm == generator["tone_pcm"]()
    samples = array("h", pcm)
    assert 500 < max(abs(value) for value in samples) < 12000
    assert abs(samples[0]) < 10 and abs(samples[-1]) < 10
