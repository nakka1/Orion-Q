"""Render the actual native console against completed laboratory receipts."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time


class StartupTrace:
    """Timing and stack evidence; never logs arguments, results or secrets."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.started = time.monotonic()
        self.events = []
        self._lock = threading.Lock()
        self._stream = (self.folder / "startup-timeline.jsonl").open("w", encoding="utf-8")
        self._stacks = (self.folder / "startup-stacks.txt").open("w", encoding="utf-8")
        self._stack_stop = threading.Event()
        self._stack_thread = threading.Thread(target=self._sample_stacks, name="startup-stack-sampler", daemon=True)
        self._stack_thread.start()

    def _sample_stacks(self):
        while not self._stack_stop.wait(10):
            self.sample_stacks()

    def sample_stacks(self):
        # Capture Python frames while owning the GIL; avoid an asynchronous
        # native stack dump racing lazy module imports in the diagnostic tool.
        names = {thread.ident: thread.name for thread in threading.enumerate()}
        threads = []
        for ident, frame in sys._current_frames().items():
            frames = []
            while frame is not None and len(frames) < 80:
                frames.append({"file": frame.f_code.co_filename, "function": frame.f_code.co_name,
                               "line": frame.f_lineno})
                frame = frame.f_back
            threads.append({"thread": names.get(ident, "unknown"), "frames": frames})
        with self._lock:
            self._stacks.write(json.dumps({"elapsed_s": time.monotonic() - self.started,
                                          "threads": threads}, sort_keys=True) + "\n")
            self._stacks.flush()

    def mark(self, event, **fields):
        entry = {"event": event, "elapsed_s": time.monotonic() - self.started,
                 "thread": threading.current_thread().name, "process_id": os.getpid(), **fields}
        with self._lock:
            self.events.append(entry)
            self._stream.write(json.dumps(entry, sort_keys=True) + "\n")
            self._stream.flush()

    def wrap(self, target, name):
        original = getattr(target, name)
        def timed(*args, **kwargs):
            self.mark("call_started", call=name)
            started = time.monotonic()
            error_type = None
            try:
                return original(*args, **kwargs)
            except BaseException as error:
                error_type = type(error).__name__
                raise
            finally:
                self.mark("call_finished", call=name, duration_s=time.monotonic() - started,
                          error_type=error_type)
        setattr(target, name, timed)

    def close(self, **fields):
        self._stack_stop.set()
        self._stack_thread.join(timeout=1)
        self.mark("finished", **fields)
        report = {"schema": "orion.lab.native-startup-trace.v1", **fields,
                  "elapsed_s": time.monotonic() - self.started, "events": self.events,
                  "physical_action_performed": False}
        (self.folder / "startup-trace.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        self._stream.close()
        self._stacks.close()


def wait_for_snapshot(app, window, timeout, *, mode="event-loop"):
    """Wait using a Qt event loop that lets the Python service worker run."""
    if window._snap is not None:
        return True
    if mode == "qwait":
        # Retained only for reproducing the former diagnostic harness.
        from PySide6.QtTest import QTest
        deadline = time.monotonic() + timeout
        while window._snap is None and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(50)
        return window._snap is not None
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    timeout_timer = QTimer()
    timeout_timer.setSingleShot(True)
    timeout_timer.timeout.connect(loop.quit)
    def snapshot_received(*_):
        if window._snap is not None:
            loop.quit()
    window.provider.snapshot_changed.connect(snapshot_received)
    timeout_timer.start(max(1, round(timeout * 1000)))
    try:
        loop.exec()
    finally:
        timeout_timer.stop()
        window.provider.snapshot_changed.disconnect(snapshot_received)
    return window._snap is not None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument("--timeout", type=float, default=40.0)
    parser.add_argument("--wait-mode", choices=("qwait", "event-loop"), default="event-loop")
    args = parser.parse_args()
    if not 0 < args.timeout <= 60:
        parser.error("timeout must be greater than zero and at most 60 seconds")
    trace = StartupTrace(args.diagnostics) if args.diagnostics else None
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if trace:
        trace.mark("imports_started")
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.console import theme
    from quantum_autopilot.console.service_provider import ServiceProvider
    from quantum_autopilot.console.shell import ConsoleWindow
    from quantum_autopilot.console.ui_gc import UiThreadCollector
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
    if trace:
        trace.mark("imports_finished")
    root = Path(__file__).resolve().parents[1]
    app = QApplication.instance() or QApplication([])
    collector = UiThreadCollector(app)
    collector.start()
    theme.install(app)
    service = AssuranceDashboardService(root, control_journal_path=args.state / "workspace.sqlite")
    if trace:
        trace.mark("service_constructed")
        for name in ("health", "control_plane_topology", "control_plane_events", "wdm_model_status",
                     "evidence_lanes", "summary", "laboratory_state"):
            trace.wrap(service, name)
    provider = ServiceProvider(service, state_root=args.state)
    if trace:
        trace.wrap(provider, "build")
        provider.snapshot_changed.connect(lambda *_: trace.mark("snapshot_published"))
    errors = []
    provider.error.connect(lambda message: errors.append({"length": len(str(message))}))
    window = ConsoleWindow(provider, skip_auth=True, state_root=args.state)
    if trace:
        trace.mark("window_constructed")
    window.resize(1600, 1100)
    passed = False
    try:
        window.show()
        window.provider.start()
        if trace:
            trace.mark("provider_started")
        wait_for_snapshot(app, window, args.timeout, mode=args.wait_mode)
        if window._snap is None:
            future = provider._future
            diagnostic = {"schema": "orion.lab.native-startup-diagnostic.v1", "status": "timeout",
                          "provider_snapshot_present": provider._snap is not None,
                          "provider_errors": errors, "future_present": future is not None,
                          "future_done": future.done() if future is not None else None,
                          "step_error_present": provider._step_error is not None,
                          "window_busy": window._busy}
            (args.state / "native_startup_timeout.json").write_text(json.dumps(diagnostic, indent=2), encoding="utf8")
            if trace:
                trace.mark("snapshot_timeout", **diagnostic)
                trace.sample_stacks()
            raise AssertionError("native startup did not publish source snapshot; diagnostic saved")
        if trace:
            trace.mark("window_snapshot_received")
        window.splash.finished.emit()
        window.show_screen("keys")
        app.processEvents()
        screen = window.screens["keys"]
        assert "completed" in screen.laboratory_panel.evidence.toPlainText()
        screenshot = args.state / "native_console.png"
        assert window.grab().save(str(screenshot.resolve()))
        passed = True
        if trace:
            trace.mark("screenshot_saved")
        print(str(screenshot.resolve()))
    finally:
        if trace:
            trace.mark("shutdown_started")
        window.provider.stop()
        window.close()
        app.processEvents()
        collector.stop()
        if trace:
            trace.close(passed=passed, provider_errors=errors, wait_mode=args.wait_mode)


if __name__ == "__main__":
    main()
