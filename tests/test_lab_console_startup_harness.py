"""Native event delivery while a Python service worker computes a snapshot."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("orion_lab_renderer", ROOT / "tools" / "render_lab_console.py")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class SnapshotSource(QObject):
    snapshot_changed = Signal(object)


class SnapshotWindow(QObject):
    def __init__(self):
        super().__init__()
        self.provider = SnapshotSource(self)
        self._snap = None
        self.received_on_thread = None
        self.provider.snapshot_changed.connect(self.receive)

    @Slot(object)
    def receive(self, value):
        self.received_on_thread = threading.get_ident()
        self._snap = value


def test_wait_delivers_worker_snapshot_on_gui_thread_and_does_not_restart_work():
    app = QApplication.instance() or QApplication([])
    window = SnapshotWindow()
    calls = []
    def worker():
        # Deliberately uses Python bytecode/GIL, not an all-native sleep.
        result = sum(index * index for index in range(250_000))
        calls.append(result)
        window.provider.snapshot_changed.emit({"result": result})
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert renderer.wait_for_snapshot(app, window, 2)
        assert window.received_on_thread == threading.get_ident()
        assert window._snap == {"result": calls[0]}
        assert renderer.wait_for_snapshot(app, window, 0.1)
        assert len(calls) == 1
    finally:
        thread.join(2)
        assert not thread.is_alive()


def test_deadline_expires_without_snapshot_and_late_signal_remains_safe():
    app = QApplication.instance() or QApplication([])
    window = SnapshotWindow()
    started = time.monotonic()
    assert not renderer.wait_for_snapshot(app, window, 0.05)
    assert time.monotonic() - started < 1
    window.provider.snapshot_changed.emit({"late": True})
    app.processEvents()
    assert window._snap == {"late": True}


def test_diagnostics_record_type_timing_and_stack_without_private_payloads(tmp_path):
    private = "never-serialize-this-token-or-key-5ca933"
    def reader(secret):
        raise ValueError(secret)
    target = SimpleNamespace(reader=reader)
    trace = renderer.StartupTrace(tmp_path)
    trace.wrap(target, "reader")
    try:
        with pytest.raises(ValueError):
            target.reader(private)
        trace.sample_stacks()
    finally:
        trace.close(passed=False)
    report = json.loads((tmp_path / "startup-trace.json").read_text())
    finished = next(event for event in report["events"] if event["event"] == "call_finished")
    assert finished["error_type"] == "ValueError" and finished["duration_s"] >= 0
    for file in tmp_path.iterdir():
        assert private not in file.read_text()
