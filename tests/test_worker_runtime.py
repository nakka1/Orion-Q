"""Frozen dispatch must not invoke the GUI or pretend python -m is available."""
import importlib.util
import sys
from pathlib import Path

import pytest

from quantum_autopilot import worker_runtime as runtime


def test_source_command_and_existing_stdio():
    assert runtime.worker_command("a b.sqlite") == (sys.executable,
        ["-m", "quantum_autopilot.control_plane.temporal_campaign_worker", "--store", "a b.sqlite"])
    assert runtime.restore_worker_stdio()


def test_unknown_frozen_launcher_refused(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_orion_temporal_worker_v1", raising=False)
    assert not runtime.worker_supported()
    with pytest.raises(ValueError):
        runtime.worker_command("test.sqlite")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows frozen launcher")
def test_declared_frozen_command_uses_internal_dispatch(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_orion_temporal_worker_v1", True, raising=False)
    monkeypatch.setattr(sys, "executable", "C:/app with spaces/ORION-Q.exe")
    program, args = runtime.worker_command("C:/my state/db.sqlite")
    assert runtime.worker_supported()
    assert program == sys.executable and args == [runtime.WORKER_FLAG, "--store", "C:/my state/db.sqlite"]
    assert "-m" not in args


def test_launcher_routes_worker_before_desktop(monkeypatch):
    import quantum_autopilot.desktop as desktop
    import quantum_autopilot.control_plane.temporal_campaign_worker as worker
    spec = importlib.util.spec_from_file_location("console_launcher_test", Path(__file__).resolve().parents[1] / "packaging/console_launcher.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    monkeypatch.setattr(sys, "_orion_temporal_worker_v1", False, raising=False)
    monkeypatch.setattr(desktop, "main", lambda argv: pytest.fail("worker opened the desktop"))
    calls = []
    monkeypatch.setattr(worker, "main", lambda argv: calls.append(argv) or 17)
    monkeypatch.setattr(runtime, "restore_worker_stdio", lambda: True)
    assert launcher.launch([runtime.WORKER_FLAG, "--store", "state.sqlite"]) == 17
    assert calls == [["--store", "state.sqlite"]]
    assert sys._orion_temporal_worker_v1 is True
    monkeypatch.setattr(runtime, "restore_worker_stdio", lambda: False)
    assert launcher.launch([runtime.WORKER_FLAG, "--store", "state.sqlite"]) == 2
    assert len(calls) == 1
