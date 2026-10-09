"""The vector disconnect sequence fills, clears to black and releases input."""
from __future__ import annotations

from array import array
from enum import IntEnum
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import wave

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QInputMethodEvent, QKeySequence, QPainter, QShortcut  # noqa: E402
from PySide6.QtTest import QSignalSpy, QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QGraphicsScene, QGraphicsView, QLineEdit, QMainWindow, QPushButton, QWidget,
)

from quantum_autopilot.console.widgets import disconnect_signal  # noqa: E402
from quantum_autopilot.console.widgets.disconnect_signal import (  # noqa: E402
    BLACK_HOLD_MS, BLACK_START, DISCONNECT_DURATION_MS, DISCONNECT_MESSAGE,
    DisconnectSignalOverlay, paint_disconnect_signal,
)


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
    image.fill(Qt.GlobalColor.magenta)
    painter = QPainter(image)
    paint_disconnect_signal(painter, QRectF(0, 0, width, height), progress)
    painter.end()
    return image


def _red_count(image):
    return sum(image.pixelColor(x, y).red() > 90 and image.pixelColor(x, y).green() < 60
               for x in range(0, image.width(), 7) for y in range(0, image.height(), 7))


@pytest.mark.parametrize("width,height", ((320, 240), (960, 480), (1920, 1080), (3440, 1440)))
def test_native_hexagons_fill_viewport_and_text_stays_inside_it(app, width, height):
    image = _frame(width, height, .55)
    for left, right in ((0, width // 4), (width * 3 // 4, width)):
        red = sum(image.pixelColor(x, y).red() > 90 and image.pixelColor(x, y).green() < 60
                  for x in range(left, right, 5) for y in range(0, height // 3, 5))
        assert red > ((right - left) / 5) * (height / 15) * .6
    white_text = sum(image.pixelColor(x, y).green() > 180
                     for x in range(width) for y in range(round(height * .45), round(height * .55), 3))
    assert white_text > width * .9
    assert image.pixelColor(0, 0).alpha() == 255


def test_fill_and_extinction_are_deterministic_then_every_pixel_is_black(app):
    frames = [_frame(320, 240, progress) for progress in (.0, .15, .55, .83, BLACK_START, 1.)]
    counts = list(map(_red_count, frames))
    assert counts[0] == 0 < counts[1] < counts[2]
    assert 0 < counts[3] < counts[2]
    assert counts[4] == counts[5] == 0
    assert _frame(320, 240, .55) == frames[2]
    for image in (frames[0], frames[4], frames[5]):
        assert all(image.pixelColor(x, y).name() == "#000000"
                   and image.pixelColor(x, y).alpha() == 255
                   for x in range(image.width()) for y in range(image.height()))


def test_message_uses_existing_bundled_letter_outlines_and_no_error_label(app):
    assert DISCONNECT_MESSAGE == "Disconnected from E.V.A."
    path = disconnect_signal._message_path()
    assert path.fillRule() == Qt.FillRule.WindingFill
    assert path.elementCount() > 500
    assert any(path.elementAt(index).isCurveTo() for index in range(path.elementCount()))
    assert 3000 <= DISCONNECT_DURATION_MS <= 4000


def test_resize_geometry_is_cached_with_a_bounded_capacity(app):
    first = disconnect_signal._hexagons(1280., 720.)
    assert first is disconnect_signal._hexagons(1280., 720.)
    assert 300 < len(first) < 1000
    assert disconnect_signal._hexagons.cache_info().maxsize == 12


def test_sequence_holds_an_opaque_black_frame_before_natural_completion(viewport, app):
    overlay = DisconnectSignalOverlay(viewport, duration_ms=80, sound_enabled=False)
    finished = QSignalSpy(overlay.finished)
    overlay.start()
    QTest.qWait(120)
    app.processEvents()
    assert finished.count() == 0 and overlay.isVisible() and overlay.is_running
    assert overlay._progress >= BLACK_START
    assert overlay.grab().toImage().pixelColor(480, 240).name() == "#000000"
    QTest.qWait(BLACK_HOLD_MS + 100)
    app.processEvents()
    assert finished.count() == 1 and overlay.completed is True
    assert not overlay.is_running and not overlay.isVisible()
    assert not overlay._timer.isActive()
    assert not overlay._input_filter_installed and not overlay._parent_filter_installed
    QTest.qWait(60)
    assert finished.count() == 1


def test_active_overlay_covers_entire_parent_and_follows_resizes(viewport, app):
    overlay = DisconnectSignalOverlay(viewport, sound_enabled=False)
    overlay.start()
    for width, height in ((1280, 720), (3440, 1440), (960, 480)):
        viewport.resize(width, height)
        app.processEvents()
        assert overlay.geometry() == viewport.rect()
    overlay.stop()


def test_suspended_paints_cannot_complete_or_start_the_black_hold(viewport, app):
    overlay = DisconnectSignalOverlay(viewport, duration_ms=80, sound_enabled=False)
    finished = QSignalSpy(overlay.finished)
    overlay.setUpdatesEnabled(False)
    overlay.start()
    QTest.qWait(80 + BLACK_HOLD_MS + 100)
    app.processEvents()
    overlay._tick()
    assert overlay._progress == 1. and overlay._black_since is None
    assert overlay.is_running and finished.count() == 0
    overlay.setUpdatesEnabled(True)
    app.processEvents()
    assert overlay._black_since is not None
    painted_at = overlay._black_since
    overlay._tick()
    assert overlay.is_running and finished.count() == 0
    assert overlay._black_since == painted_at
    QTest.qWait(BLACK_HOLD_MS - 60)
    app.processEvents()
    assert finished.count() == 0
    QTest.qWait(120)
    app.processEvents()
    assert finished.count() == 1 and overlay.completed
    assert not overlay._timer.isActive() and not overlay._input_filter_installed


def test_keys_mouse_composition_and_escape_are_blocked_until_stop(viewport, app):
    field = QLineEdit(viewport)
    field.setGeometry(20, 20, 240, 36)
    field.show()
    field.setFocus()
    button = QPushButton("Behind", viewport)
    button.setGeometry(20, 70, 100, 40)
    button.show()
    shortcut = QShortcut(QKeySequence("Esc"), viewport)
    shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
    activated = QSignalSpy(shortcut.activated)
    clicks = QSignalSpy(button.clicked)
    overlay = DisconnectSignalOverlay(viewport, sound_enabled=False)
    finished = QSignalSpy(overlay.finished)
    overlay.start()
    QTest.keyClicks(field, "covered")
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    composition = QInputMethodEvent()
    composition.setCommitString("covered")
    QApplication.sendEvent(field, composition)
    QTest.keyClick(overlay, Qt.Key.Key_Escape)
    assert field.text() == "" and clicks.count() == activated.count() == 0
    assert overlay.is_running and finished.count() == 0
    overlay.stop()
    assert QApplication.focusWidget() is field
    QTest.keyClicks(field, "restored")
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert field.text() == "restored" and clicks.count() == 1


def test_proxy_embedded_fields_are_blocked_but_unrelated_window_keeps_input(app):
    owner = QMainWindow()
    owner.resize(640, 480)
    scene = QGraphicsScene(owner)
    canvas = QWidget()
    canvas.resize(600, 400)
    field = QLineEdit(canvas)
    field.setGeometry(20, 20, 240, 36)
    scene.addWidget(canvas)
    view = QGraphicsView(scene, owner)
    owner.setCentralWidget(view)
    owner.show()
    unrelated = QLineEdit()
    unrelated.show()
    app.processEvents()
    overlay = DisconnectSignalOverlay(owner, sound_enabled=False)
    overlay.start()
    QTest.keyClicks(field, "blocked")
    QTest.keyClicks(unrelated, "allowed")
    assert field.text() == "" and unrelated.text() == "allowed"
    assert overlay.geometry() == owner.rect()
    overlay.dispose()
    unrelated.close()
    owner.close()
    unrelated.deleteLater()
    owner.deleteLater()
    app.processEvents()


def test_restart_reuses_one_timer_and_stop_is_silent(viewport):
    overlay = DisconnectSignalOverlay(viewport, sound_enabled=False)
    finished = QSignalSpy(overlay.finished)
    timer = overlay._timer
    overlay.start()
    overlay._progress = .7
    overlay.start()
    assert overlay._timer is timer and overlay._progress == 0
    overlay.stop()
    overlay.stop()
    assert finished.count() == 0 and overlay.completed is False
    assert not timer.isActive() and not overlay.isVisible()
    assert not overlay._input_filter_installed and not overlay._parent_filter_installed


def test_parent_hide_cancels_and_removes_all_active_listeners(viewport, app):
    overlay = DisconnectSignalOverlay(viewport, sound_enabled=False)
    finished = QSignalSpy(overlay.finished)
    overlay.start()
    viewport.hide()
    app.processEvents()
    assert finished.count() == 1 and overlay.completed is False
    assert not overlay.is_running and not overlay._timer.isActive()
    assert not overlay._input_filter_installed and not overlay._parent_filter_installed


def test_hidden_parent_has_no_timer_until_shown(viewport, app):
    viewport.hide()
    overlay = DisconnectSignalOverlay(viewport, sound_enabled=False)
    overlay.start()
    assert not overlay._timer.isActive() and not overlay._input_filter_installed
    viewport.show()
    app.processEvents()
    assert overlay._timer.isActive() and overlay.isVisible()
    overlay.stop()


def test_dispose_and_close_are_silent_irreversible_teardown(viewport):
    overlay = DisconnectSignalOverlay(viewport, sound_enabled=False)
    finished = QSignalSpy(overlay.finished)
    overlay.start()
    overlay.close()
    assert finished.count() == 0 and overlay._disposed
    assert not overlay._timer.isActive()
    assert not overlay._input_filter_installed and not overlay._parent_filter_installed
    with pytest.raises(RuntimeError, match="disposed"):
        overlay.start()
    overlay.dispose()


def test_path_and_geometry_support_nonzero_rect_origins(app):
    image = QImage(500, 400, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.blue)
    painter = QPainter(image)
    paint_disconnect_signal(painter, QRectF(50, 40, 400, 320), .55)
    painter.end()
    assert image.pixelColor(0, 0).name() == "#0000ff"
    assert image.pixelColor(499, 399).name() == "#0000ff"
    assert image.pixelColor(50, 40).name() != "#0000ff"
    assert image.pixelColor(449, 359).name() != "#0000ff"


class _FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        if callback not in self.callbacks:
            raise TypeError("Callback is not connected")
        self.callbacks.remove(callback)

    def emit(self):
        for callback in tuple(self.callbacks):
            callback()


@pytest.fixture
def audio_backend(monkeypatch):
    class FakeSound:
        class Status(IntEnum):
            Loading = 1
            Ready = 2
            Error = 3

        instances = []
        failure = None

        def __init__(self, parent):
            if self.failure == "create":
                raise RuntimeError("Synthetic output failure")
            self.parent = parent
            self.statusChanged = _FakeSignal()
            self.current_status = self.Status.Loading
            self.plays = 0
            self.stops = 0
            self.deleted = False
            self.instances.append(self)

        def setVolume(self, volume):
            self.volume = volume

        def setLoopCount(self, count):
            self.loops = count

        def setSource(self, url):
            if self.failure == "source":
                raise RuntimeError("Synthetic load failure")
            self.url = url

        def status(self):
            return self.current_status

        def play(self):
            if self.failure == "play":
                raise RuntimeError("Synthetic playback failure")
            self.plays += 1

        def stop(self):
            self.stops += 1
            if self.failure == "stop":
                raise RuntimeError("Synthetic stop failure")

        def ready(self):
            self.current_status = self.Status.Ready
            self.statusChanged.emit()

        def deleteLater(self):
            self.deleted = True

    media = SimpleNamespace(QSoundEffect=FakeSound,
                            QMediaDevices=SimpleNamespace(audioOutputs=lambda: [object()]))
    monkeypatch.setitem(sys.modules, "PySide6.QtMultimedia", media)
    return media


def test_shutdown_wave_is_original_reproducible_pcm_with_silent_boundaries():
    generator_path = Path(__file__).resolve().parents[1] / "tools/generate_disconnect_sound.py"
    spec = importlib.util.spec_from_file_location("shutdown_sound_generator", generator_path)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    with wave.open(str(disconnect_signal.DISCONNECT_SOUND), "rb") as audio:
        assert audio.getnchannels() == 1 and audio.getsampwidth() == 2
        assert audio.getframerate() == generator.SAMPLE_RATE == 44100
        assert audio.getnframes() == round(generator.DURATION_SECONDS * generator.SAMPLE_RATE)
        assert generator.DURATION_SECONDS * 1000 < DISCONNECT_DURATION_MS * BLACK_START
        recorded = audio.readframes(audio.getnframes())
    assert recorded == generator.tone_pcm()
    samples = array("h", recorded)
    if sys.byteorder != "little":
        samples.byteswap()
    assert samples[0] == samples[-1] == 0
    assert 10000 < max(abs(sample) for sample in samples) < 24000

    def crossings(start, end):
        section = samples[round(start * generator.SAMPLE_RATE):round(end * generator.SAMPLE_RATE)]
        return sum((left < 0) != (right < 0) for left, right in zip(section, section[1:]))

    assert crossings(.10, .30) > 3 * crossings(2.6, 2.8)


def test_sound_is_lazy_optional_and_ready_plays_once_per_start(viewport, audio_backend):
    disabled = DisconnectSignalOverlay(viewport, sound_enabled=False)
    disabled.start()
    assert disabled._sound is None and not disabled._sound_checked
    disabled.dispose()
    overlay = DisconnectSignalOverlay(viewport, sound_enabled=True)
    assert overlay._sound is None and not audio_backend.QSoundEffect.instances
    overlay.start()
    sound = overlay._sound
    assert sound is not None and sound.plays == 0
    assert sound.parent is overlay and sound.loops == 1 and sound.volume == .24
    assert Path(sound.url.toLocalFile()) == disconnect_signal.DISCONNECT_SOUND
    sound.ready()
    overlay._play_sound()
    sound.statusChanged.emit()
    assert sound.plays == 1
    overlay.start()
    assert overlay._sound is sound and len(sound.instances) == 1 and sound.plays == 2
    overlay.dispose()
    assert sound.deleted and overlay._sound is None
    assert not sound.statusChanged.callbacks and not overlay._sound_requested


@pytest.mark.parametrize("action", ("stop", "hide", "dispose", "finish"))
def test_late_sound_readiness_never_plays_after_teardown(viewport, audio_backend, action):
    overlay = DisconnectSignalOverlay(viewport, sound_enabled=True)
    overlay.start()
    sound = overlay._sound
    assert sound.plays == 0
    if action == "finish":
        overlay._finish(completed=True)
    else:
        getattr(overlay, action)()
    sound.ready()
    assert sound.plays == 0 and sound.stops > 0
    assert not overlay._sound_requested and not overlay.is_running
    overlay.dispose()


@pytest.mark.parametrize("failure", ("missing", "outputs", "create", "source", "play", "stop", "error"))
def test_audio_failure_never_delays_the_black_frame_or_completion(viewport, app, monkeypatch,
                                                                 audio_backend, failure, tmp_path):
    if failure == "missing":
        monkeypatch.setattr(disconnect_signal, "DISCONNECT_SOUND", tmp_path / "missing.wav")
    elif failure == "outputs":
        audio_backend.QMediaDevices.audioOutputs = lambda: []
    else:
        audio_backend.QSoundEffect.failure = failure
    overlay = DisconnectSignalOverlay(viewport, duration_ms=1, sound_enabled=True)
    finished = QSignalSpy(overlay.finished)
    overlay.start()
    if overlay._sound is not None:
        if failure == "error":
            overlay._sound.current_status = overlay._sound.Status.Error
            overlay._sound.statusChanged.emit()
        else:
            overlay._sound.ready()
    QTest.qWait(BLACK_HOLD_MS + 140)
    app.processEvents()
    assert finished.count() == 1 and overlay.completed
    assert not overlay.is_running and not overlay._timer.isActive()
    assert not overlay._sound_requested
    overlay.dispose()


def test_missing_multimedia_module_keeps_animation_operational(viewport, monkeypatch):
    import builtins

    original_import = builtins.__import__

    def without_multimedia(name, *args, **kwargs):
        if name == "PySide6.QtMultimedia":
            raise ImportError("No optional audio module")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_multimedia)
    overlay = DisconnectSignalOverlay(viewport, sound_enabled=True)
    overlay.start()
    assert overlay.is_running and overlay._timer.isActive() and overlay._sound is None
    overlay.dispose()
