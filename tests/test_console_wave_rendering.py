"""Rendered waves stay current and reveal only real samples at every density."""
import math
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.widgets import wave_chart  # noqa: E402
from quantum_autopilot.console.widgets.wave_chart import WaveChart, WaveSeries  # noqa: E402


@pytest.fixture(params=['dark', 'light'])
def chart(request):
    app = QApplication.instance() or QApplication([])
    previous = theme.current_theme()
    theme.set_theme(request.param)
    theme.install(app)
    widget = WaveChart(animate=False)
    widget.resize(580, 180)
    widget.show()
    widget.freeze(1.0)
    QTest.qWait(20)
    try:
        yield widget
    finally:
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        theme.set_theme(previous)
        theme.install(app)


def samples(count=240):
    return tuple(.55 + .15 * math.sin(index * .37) for index in range(count))


def test_new_samples_same_timestamp_redraw_and_empty_input_clears_trace(chart):
    values = samples()
    chart.set_series([WaveSeries('A', 'fibre', values)], 13.0)
    before = chart.grab().toImage()
    changed = tuple(.55 - .15 * math.sin(index * .37) for index in range(240))
    chart.set_series([WaveSeries('A', 'fibre', changed)], 13.0)
    assert before != chart.grab().toImage()
    assert [value for _at, value in chart.visible_samples('A')] == list(changed)
    chart.set_series([WaveSeries('A', 'fibre', (), note='isolated')], 14.0)
    assert chart.visible_samples('A') == []
    assert not chart._trace_images
    empty = chart.grab().toImage()
    chart.set_series([WaveSeries('A', 'fibre', values)], 15.0)
    assert empty != chart.grab().toImage()


def test_reveal_clips_future_received_points_and_keeps_real_sample_times(chart):
    values = tuple(.3 + .4 * (index % 2) for index in range(13))
    series = [WaveSeries('empty', 'empty'), WaveSeries('A', 'fibre', values, rate_hz=1)]
    chart.set_series(series, 12.0)
    chart.set_series(series, 13.0)
    images = []
    for reveal, end in [(0.0, 12.0), (.5, 12.0), (1.0, 13.0)]:
        chart.freeze(reveal)
        points = chart.visible_samples('A')
        assert points[-1][0] == end
        assert all(at <= chart.display_time() for at, _value in points)
        assert all(value in values for _at, value in points)
        images.append(chart.grab().toImage())
    assert images[0] != images[1] != images[2]
    # At t=12.5, the last real point is t=12.0. No orange trace may fill that gap.
    plot = chart.plot_rect()
    right = plot.right() - .5 / chart.window_s * plot.width()
    image = images[1]
    accent = theme.qc(theme.C.acc)
    for x in range(math.ceil(right) + 2, math.floor(plot.right())):
        for y in range(math.ceil(plot.top()) + 2, math.floor(plot.bottom()) - 2):
            assert image.pixelColor(x, y) != accent


@pytest.mark.parametrize('density', [1.0, 1.5, 2.0])
def test_trace_density_resize_and_highlight_invalidate_rendering(chart, monkeypatch, density):
    monkeypatch.setattr(wave_chart, 'raster_scale_for', lambda _widget: density)
    chart.set_series([WaveSeries('A', 'fibre', samples()), WaveSeries('B', 'peer', samples()[::-1])], 13.0)
    normal = chart.grab().toImage()
    assert all(image.devicePixelRatioF() == density for image in chart._trace_images)
    chart.set_highlight('A')
    assert normal != chart.grab().toImage()
    dimensions = [image.size() for image in chart._trace_images]
    chart.resize(760, 240)
    chart.grab()
    assert all(image.width() > old.width() and image.height() > old.height()
               for image, old in zip(chart._trace_images, dimensions))
    assert [value for _at, value in chart.visible_samples('A')] == list(samples())
