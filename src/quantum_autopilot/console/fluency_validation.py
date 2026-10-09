"""Rendered rolling-window checks shared by source and packaged self-tests."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication

from . import theme
from .widgets.wave_chart import WaveChart, WaveSeries


def validate_wave_continuity(destination) -> dict:
    """Verify a real raster and retained source samples across rolling deliveries.

    The values are explicitly artificial test input. The deterministic display
    clock observes the delivery boundary without depending on a wall-clock race.
    It never submits a command or acquires the operator's desktop input.
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance()
    previous = theme.current_theme()
    checks, observations = {}, {}
    try:
        for mode in ("dark", "light"):
            theme.set_theme(mode)
            chart = WaveChart()
            theme.install(app, root=chart)
            chart.resize(580, 180)
            chart.show()
            try:
                flat = WaveSeries("SELFTEST", "Artificial received samples", (.65,) * 240)
                chart.set_series([flat], 30.)
                chart.freeze(1.)
                first = chart.visible_samples("SELFTEST")
                before = chart.grab().toImage()
                chart.set_series([flat], 31.)
                chart.freeze(0.)
                after = chart.grab().toImage()
                checks[f"{mode}_delivery_retains_visible_samples"] = chart.visible_samples("SELFTEST") == first
                plot = chart.plot_rect()
                density = before.devicePixelRatio()
                y = round((plot.top() + plot.height() / 2) * density)
                xs = range(round((plot.left() + 10) * density), round((plot.left() + 30) * density))
                checks[f"{mode}_left_pixels_survive_delivery"] = all(
                    before.pixelColor(x, y) == after.pixelColor(x, y) for x in xs)
                grid, traces = chart._grid_image, tuple(chart._trace_images)
                chart.freeze(.5)
                chart.grab()
                checks[f"{mode}_clock_moves_over_received_data"] = (
                    chart.display_time() == 30.5 and chart.visible_samples("SELFTEST")[0][0] == 18.5)
                checks[f"{mode}_frame_reuses_grid_and_traces"] = chart._grid_image is grid and all(
                    left is right for left, right in zip(traces, chart._trace_images))
                checks[f"{mode}_sampling_contract_unchanged"] = chart.series == (flat,) and (
                    chart._timer.interval() == 33 and chart._timer.timerType() == Qt.TimerType.PreciseTimer)
                chart.freeze(1.)
                checks[f"{mode}_stops_at_last_received_sample"] = (
                    chart.display_time() == 31. and len(chart.visible_samples("SELFTEST")) == 240)
                chart.set_series([WaveSeries("SELFTEST", "No input", ())], 32.)
                checks[f"{mode}_missing_input_has_no_trace"] = (
                    not chart.visible_samples("SELFTEST") and not chart._timer.isActive())
                checks[f"{mode}_evidence_saved"] = before.save(str(destination / f"{mode}-before.png")) and (
                    after.save(str(destination / f"{mode}-after.png")))
                observations[mode] = {"qt_dpr": chart.devicePixelRatioF(), "raster_dpr": density,
                                      "first_received_timestamp": first[0][0], "plot_width": plot.width()}
            finally:
                chart.close()
                chart.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    finally:
        theme.set_theme(previous)
    report = {"passed": all(checks.values()), "checks": checks, "observations": observations,
              "check_count": len(checks), "qt_platform": app.platformName(),
              "scope": "Artificial chart input and deterministic presentation clock; rendered Qt raster",
              "desktop_input_verified": False, "physical_action_performed": False}
    (destination / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
