"""Capture native entry/connect/disconnect screens with an isolated mock input.

This diagnostic opens no service or hardware, never reads operator state and
does not submit Windows credentials. Authentication is covered separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time


def run(output: Path) -> int:
    from PySide6.QtCore import QEvent, QCoreApplication, Qt
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from quantum_autopilot.console import shell, theme
    from quantum_autopilot.console.branding import ICON_PATH
    from quantum_autopilot.console.providers import MockProvider
    from quantum_autopilot.console.widgets.disconnect_signal import BLACK_START
    from quantum_autopilot.desktop_instance import mark_isolated_diagnostic_window
    from quantum_autopilot.product import product_version

    app = QApplication.instance() or QApplication([])
    if sys.platform != "win32" or app.platformName() != "windows":
        raise ValueError("Use the native Windows Qt platform for this diagnostic")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_file = output / "entry-visuals.json"
    if report_file.exists():
        raise ValueError("Use a new evidence directory")
    original_screens, original_theme = shell.SCREENS, theme.current_theme()
    original_quit = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    icon_before = hashlib.sha256(ICON_PATH.read_bytes()).hexdigest()
    report = {"schema": "orion.entry-visuals.v2", "passed": False,
              "version": product_version(), "platform": app.platformName(),
              "frozen": bool(getattr(sys, "frozen", False)),
              "operator_state_accessed": False, "service_started": False,
              "physical_action_performed": False, "authentication_exercised": False,
              "source": "MockProvider: artificial UI fixture",
              "cosmetic_durations_ms": {"connect": 1200, "disconnect": 1800},
              "cases": [], "errors": []}
    window = None

    def wait(milliseconds):
        until = time.monotonic() + milliseconds / 1000
        while time.monotonic() < until:
            app.processEvents()
            time.sleep(.005)

    def capture(key, *, inspect_black=False):
        pixmap = window.screen().grabWindow(int(window.winId()))
        path = output / (key + ".png")
        if pixmap.isNull() or not pixmap.save(str(path)):
            raise ValueError("Native window capture failed")
        result = {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "method": "QScreen.grabWindow(HWND)"}
        if inspect_black:
            rgb = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)
            pixels = rgb.constBits()
            stride, row_size = rgb.bytesPerLine(), rgb.width() * 3
            # Exclude alignment padding and inspect every RGB pixel captured
            # from the Windows compositor, rather than repainting the widget.
            result["all_rgb_pixels_black"] = all(
                not any(pixels[y * stride:y * stride + row_size]) for y in range(rgb.height()))
        return result

    def until(predicate, timeout_ms=3000):
        deadline = time.monotonic() + timeout_ms / 1000
        while not predicate():
            if time.monotonic() >= deadline:
                raise TimeoutError("Native visual milestone did not complete")
            app.processEvents()
            time.sleep(.005)

    try:
        shell.SCREENS = ()  # Explicit entry-only UI fixture; no operation pages.
        for mode in ("dark", "light"):
            theme.set_theme(mode)
            theme.install(app)
            for width, height in ((1280, 720), (3440, 1440)):
                key = f"{mode}-{width}x{height}"
                provider = MockProvider()
                window = shell.ConsoleWindow(provider)
                window.interface_sequence._sound_enabled = False
                window.disconnect_sequence._sound_enabled = False
                # Compress only the cosmetic animation for repeatable capture.
                window.interface_sequence._duration_ms = 1200
                window.disconnect_sequence._duration_ms = 1800
                window.setWindowTitle("OrionQ · isolated entry visual diagnostic")
                window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
                window.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                                      | Qt.WindowType.WindowTransparentForInput
                                      | Qt.WindowType.WindowDoesNotAcceptFocus)
                mark_isolated_diagnostic_window(window)
                window.resize(width, height)
                window.move(app.primaryScreen().availableGeometry().topLeft())
                window.show()
                window.splash.start("Preparing an isolated UI fixture…", total=5, completed=3)
                wait(200)
                images = [capture(key + "-loading")]
                window._on_snapshot(provider.snapshot())
                wait(80)
                normal_icon = window.windowIcon().pixmap(64, 64).toImage()
                checks = {"normal_logo_on_new_window": not window.boot.brand.signal_mode,
                          "login_is_ready": window.current_screen == "boot"}
                images.append(capture(key + "-login"))
                window.boot.select_source("demo")
                window.boot.operator_id.setText("S. Ikari")
                window.boot.password.setText("thirdpilot")
                window.boot.open_button.click()
                wait(800)
                checks.update({"signal_started": window._signal_brand,
                               "full_window_overlay": window.interface_sequence.geometry() == window.rect(),
                               "password_cleared": window.boot.password.text() == "",
                               "red_logo": window.boot.brand.signal_mode,
                               "runtime_icon_changed": window.windowIcon().pixmap(64, 64).toImage() != normal_icon})
                images.append(capture(key + "-emergency"))
                wait(500)
                checks.update({"returned_to_login": window.current_screen == "boot",
                               "marker": window.boot.effect_status.text() == "eva operator connected",
                               "source_preserved": window.boot.selected_source == "demo",
                               "no_authentication": not window._login_verified,
                               "animation_released": not window.interface_sequence.is_running
                                  and not window.interface_sequence._input_filter_installed,
                               "physical_locked": ("Physical", "LOCKED") in window.boot.boundary_values()})
                images.append(capture(key + "-signal-login"))
                checks["unplug_control"] = window.boot.unplug_button.isVisible() \
                    and window.boot.unplug_button.text() == "Unplug from E.V.A."
                window.boot.unplug_button.click()
                until(lambda: window.disconnect_sequence._progress >= .48)
                checks.update({"disconnect_started": window.disconnect_sequence.is_running,
                               "disconnect_full_window": window.disconnect_sequence.geometry() == window.rect()})
                images.append(capture(key + "-disconnect"))
                until(lambda: window.disconnect_sequence._progress >= BLACK_START
                      and window.disconnect_sequence._black_since is not None)
                # A Qt paint and presentation by the Windows compositor are
                # distinct milestones. Allow several display refreshes while
                # still inside the mandatory black hold, then inspect capture.
                wait(60)
                checks["black_hold_visible"] = window.disconnect_sequence.isVisible() \
                    and window.disconnect_sequence.is_running
                black_capture = capture(key + "-black", inspect_black=True)
                checks["black_frame"] = black_capture["all_rgb_pixels_black"]
                images.append(black_capture)
                until(lambda: not window.disconnect_sequence.is_running)
                wait(80)
                checks.update({"standard_login_restored": window.current_screen == "boot" \
                                   and not window._signal_brand and not window._effect_connected,
                               "normal_logo_restored": not window.boot.brand.signal_mode,
                               "normal_icon_restored": window.windowIcon().pixmap(64, 64).toImage() == normal_icon,
                               "marker_removed": window.boot.effect_status.isHidden(),
                               "unplug_hidden": window.boot.effect_footer.isHidden(),
                               "source_preserved_after_disconnect": window.boot.selected_source == "demo",
                               "disconnect_released": window.disconnect_sequence.completed \
                                   and not window.disconnect_sequence._timer.isActive() \
                                   and not window.disconnect_sequence._input_filter_installed \
                                   and not window.disconnect_sequence._parent_filter_installed,
                               "requires_normal_sign_in": not window._login_verified})
                images.append(capture(key + "-standard-login"))
                report["cases"].append({"case": key, "passed": all(checks.values()), "checks": checks,
                                        "size": [window.width(), window.height()],
                                        "dpr": window.devicePixelRatioF(), "screenshots": images})
                window.close()
                window.deleteLater()
                window = None
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
        report["packaged_icon_unchanged"] = icon_before == hashlib.sha256(ICON_PATH.read_bytes()).hexdigest()
        report["passed"] = len(report["cases"]) == 4 and all(case["passed"] for case in report["cases"]) \
            and report["packaged_icon_unchanged"]
    except Exception as error:
        report["errors"].append(type(error).__name__ + ": " + str(error))
    finally:
        if window is not None:
            window.close()
            window.deleteLater()
        shell.SCREENS = original_screens
        theme.set_theme(original_theme)
        theme.install(app)
        app.setQuitOnLastWindowClosed(original_quit)
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    raise SystemExit(run(parser.parse_args().output))
