"""A cold native worker must initialize scientific DLLs before reading pipes."""
import builtins
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from queue import Queue

import pytest

from quantum_autopilot.control_plane.temporal_campaign import (
    TemporalCampaignRequest, TemporalRegime, read_temporal_campaign, run_temporal_campaign,
)
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore


def request():
    baseline = TemporalRegime("baseline", 1, .25, .00005, .00003, 8)
    return TemporalCampaignRequest("cold-native-worker", "alice", "repeater-a", "bob", 0, .8, 1e-6, 20,
        (7, 8), (baseline, replace(baseline, regime_id="longer-memory", memory_t1_s=.005, memory_t2_s=.003)), "baseline")


def scientific_result(receipt):
    result = json.loads(json.dumps(receipt["record"]["result"]))
    # Only wall-clock receipt labels differ; all simulation times and matrices remain.
    for rows in result["trials_by_regime"].values():
        for row in rows:
            quantum = row["result"]["quantum_execution"]
            if quantum:
                quantum["snapshot"].pop("timestamp_utc")
                quantum["snapshot"]["provenance"].pop("timestamp")
                quantum["intent"].pop("issued_at_utc")
    return result


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native DLL and inherited pipe regression")
@pytest.mark.parametrize("cancel", (False, True), ids=("complete-parity", "cancel-before-commit"))
def test_cold_windowless_worker_finishes_with_control_pipe_open(tmp_path, cancel):
    interpreter = Path(sys.executable).with_name("pythonw.exe")
    if not interpreter.is_file():
        pytest.skip("Pythonw is required to qualify Windows native startup")
    launcher = Path(__file__).resolve().parents[1]/"packaging/console_launcher.py"
    child = tmp_path/"windowless-child.py"
    child.write_text(
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('orion_startup_test', {str(launcher)!r})\n"
        "launcher = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(launcher)\n"
        "raise SystemExit(launcher.launch(['--temporal-campaign-worker', '--store', sys.argv[1]]))\n",
        encoding="utf-8")
    environment = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH"):
        environment.pop(name, None)
    windir = Path(environment.get("WINDIR", "C:/Windows"))
    environment["PATH"] = str(windir/"System32")+os.pathsep+str(windir)
    store_path = tmp_path/"worker state.sqlite"
    process = subprocess.Popen([str(interpreter), str(child), str(store_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
        cwd=tmp_path, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        process.stdin.write((json.dumps(asdict(request()))+"\n"+("CANCEL\n" if cancel else "")).encode())
        process.stdin.flush()
        # Keep stdin open; communicate(input=...) would close it and hide the deadlock.
        process.wait(timeout=20)
        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream and not stream.closed:
                stream.close()
    events = [json.loads(line) for line in stdout.splitlines()]
    if cancel:
        assert process.returncode == 3, stderr.decode(errors="replace")
        assert events[-1]["kind"] == "cancelled"
        assert not any(event["kind"] == "committed" for event in events)
        assert read_temporal_campaign(SimulatedTopologyStore(store_path), request().campaign_id) is None
        return
    assert process.returncode == 0, stderr.decode(errors="replace")
    assert [event["kind"] for event in events] == ["started", *("progress" for _ in range(4)), "committed"]
    assert [event["completed"] for event in events if event["kind"] == "progress"] == [1, 2, 3, 4]
    assert events[-1]["replay"] is False
    receipt = read_temporal_campaign(SimulatedTopologyStore(store_path), request().campaign_id)
    reference = run_temporal_campaign(SimulatedTopologyStore(tmp_path/"reference.sqlite"), request())
    assert events[-1]["record_sha256"] == receipt["record_sha256"]
    assert scientific_result(receipt) == scientific_result(reference)
    assert any(
        row["result"]["quantum_execution"] and any(age > 0 for age in row["result"]["memory_ages_s"])
        for rows in receipt["record"]["result"]["trials_by_regime"].values() for row in rows)


def test_native_initialization_failure_never_starts_reader(monkeypatch, capsys, tmp_path):
    from quantum_autopilot.control_plane import temporal_campaign_worker as worker
    starts = []
    class Reader:
        def __init__(self, **kwargs):
            pass
        def start(self):
            starts.append(True)
    inbox = Queue()
    inbox.put(json.dumps(asdict(request())).encode())
    monkeypatch.setattr(worker, "Thread", Reader)
    monkeypatch.setattr(worker, "Queue", lambda: inbox)
    original_import = builtins.__import__
    def fail_native_import(name, *args, **kwargs):
        if name == "numpy":
            raise OSError("synthetic native backend initialization failure")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", fail_native_import)
    assert worker.main(["--store", str(tmp_path/"unused.sqlite")]) == 2
    assert starts == []
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(events) == 1 and events[0]["kind"] == "failed"
    assert not (tmp_path/"unused.sqlite").exists()
