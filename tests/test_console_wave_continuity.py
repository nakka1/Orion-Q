"""A delivered rolling window must not erase samples still visible in the node panel."""
import math
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console import theme
from quantum_autopilot.console.widgets import wave_chart
from quantum_autopilot.console.widgets.wave_chart import WaveChart, WaveSeries


@pytest.fixture(params=['dark', 'light'])
def chart(request):
    app = QApplication.instance() or QApplication([])
    previous = theme.current_theme()
    theme.set_theme(request.param); theme.install(app)
    widget = WaveChart()
    widget.resize(580, 180); widget.show()
    try:
        yield widget
    finally:
        widget.close(); widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        theme.set_theme(previous); theme.install(app)


def delivery(end, rate=20):
    return WaveSeries('A', 'fibre A', tuple(.65 + .1 * math.sin((end * rate - 239 + i) * .1)
                                         for i in range(240)), rate)


def test_new_delivery_keeps_left_edge_until_its_samples_scroll_out(chart):
    first, second = delivery(30), delivery(31)
    chart.set_series([first], 30); chart.freeze(1)
    before = chart.visible_samples('A')
    chart.set_series([second], 31); chart.freeze(0)
    assert chart.visible_samples('A') == before
    assert chart.series[0].samples == second.samples
    chart.freeze(.5)
    half = chart.visible_samples('A')
    assert half[0][0] == pytest.approx(18.5)
    assert half[-1][0] == pytest.approx(30.5)
    received = dict(before)
    received.update({31 - (239 - i) / 20: value for i, value in enumerate(second.samples)})
    assert all(value == received[at] for at, value in half)
    chart.freeze(1)
    assert [value for _at, value in chart.visible_samples('A')] == list(second.samples)


@pytest.mark.parametrize('density', [1, 1.5, 2])
def test_pixels_at_left_edge_survive_delivery_and_frames_reuse_artwork(chart, monkeypatch, density):
    monkeypatch.setattr(wave_chart, 'raster_scale_for', lambda _widget: density)
    flat = WaveSeries('A', 'fibre A', (.65,) * 240)
    chart.set_series([flat], 30); chart.freeze(1)
    before = chart.grab().toImage()
    chart.set_series([flat], 31); chart.freeze(0)
    after = chart.grab().toImage()
    plot = chart.plot_rect()
    y = round((plot.top() + plot.height() / 2) * before.devicePixelRatio())
    left = round((plot.left() + 10) * before.devicePixelRatio())
    right = round((plot.left() + 30) * before.devicePixelRatio())
    assert all(before.pixelColor(x, y) == after.pixelColor(x, y) for x in range(left, right))
    grid, traces = chart._grid_image, list(chart._trace_images)
    for fraction in (.25, .5, .75):
        chart.freeze(fraction); chart.grab()
        assert chart._grid_image is grid and all(a is b for a, b in zip(chart._trace_images, traces))


def test_early_next_delivery_does_not_jump_the_presentation_clock(chart, monkeypatch):
    wall = [100.0]
    monkeypatch.setattr(wave_chart.time, 'monotonic', lambda: wall[0])
    chart.set_series([delivery(30)], 30)
    chart.set_series([delivery(31)], 31)
    wall[0] += .4
    prior = chart.display_time()
    chart.set_series([delivery(32)], 32)
    assert chart.display_time() == pytest.approx(prior)
    wall[0] += 1
    assert chart.display_time() == 32
    wall[0] += 60
    assert chart.display_time() == 32
    chart._frame()
    assert not chart._timer.isActive()
    assert chart._timer.timerType() == Qt.TimerType.PreciseTimer
    assert chart._timer.interval() == 33


def test_disabled_empty_removed_and_rate_changed_connections_drop_old_history(chart):
    chart.set_series([delivery(30)], 30)
    chart.set_series([delivery(31)], 31); chart.freeze(0)
    assert len(chart.visible_samples('A')) == 240
    chart.set_series([WaveSeries('A', 'fibre A', (), note='disabled')], 32)
    assert not chart.visible_samples('A') and not chart._received and not chart._timer.isActive()
    chart.set_series([delivery(33)], 33); chart.freeze(1)
    chart.set_series([], 34)
    assert not chart._received
    chart.set_series([delivery(35)], 35)
    chart.set_series([WaveSeries('A', 'fibre A', (.7,) * 120, 10)], 36)
    assert all(value == .7 for _at, value in chart._received['A'])


def test_revised_samples_same_timestamp_replace_overlap_without_restarting_clock(chart):
    chart.set_series([delivery(30)], 30)
    chart.set_series([delivery(31)], 31); chart.freeze(1)
    previous = chart._delivered_at
    revised = WaveSeries('A', 'fibre A', (.72,) * 240)
    chart.set_series([revised], 31)
    assert chart._delivered_at == previous
    assert [value for _at, value in chart.visible_samples('A')] == list(revised.samples)


def test_cold_start_clock_reset_and_missing_delivery_never_create_a_trace(chart):
    chart.set_series([delivery(30)], 30)
    assert len(chart.visible_samples('A')) == 240 and not chart._timer.isActive()
    chart.set_series([delivery(100)], 100)
    assert chart.display_time() == 100 and len(chart._received['A']) == 240
    chart.set_series([delivery(10)], 10)
    assert chart.display_time() == 10 and max(at for at, _v in chart.visible_samples('A')) == 10
    chart.set_series([delivery(11)], None)
    assert not chart.visible_samples('A') and not chart._received


def test_long_session_retains_only_bounded_received_history(chart):
    for tick in range(30, 230):
        chart.set_series([delivery(tick)], tick); chart.freeze(1)
        assert len(chart._received['A']) <= 301
        assert len(chart.visible_samples('A')) == 240
    assert min(at for at, _v in chart._received['A']) >= 229 - 15
