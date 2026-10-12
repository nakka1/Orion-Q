"""Process-crash recovery at both sides of the scenario commit boundary.

These tests terminate only their own child process. They do not simulate a
power failure or certify the host's storage hardware.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from quantum_autopilot.control_plane.runtime import ControllerRuntime
from quantum_autopilot.control_plane.topology import (
    SimulatedTopologyStore,
    TopologyScenarioRunner,
    resilience_demo_scenario,
)


_CHILD = """
import os
import sys
from quantum_autopilot.control_plane.runtime import ControllerRuntime
from quantum_autopilot.control_plane.topology import (
    SimulatedTopologyStore, TopologyScenarioRunner, resilience_demo_scenario,
)

path, boundary = sys.argv[1:]
runtime = ControllerRuntime(path)
store = SimulatedTopologyStore(path)
runner = TopologyScenarioRunner(runtime, store)
if boundary == 'before_commit':
    append = store.append_scenario_run
    def terminate_after_receipt(*args, **kwargs):
        append(*args, **kwargs)
        os._exit(23)
    store.append_scenario_run = terminate_after_receipt
runner.run(resilience_demo_scenario('process-crash-witness'))
os._exit(24)
"""


def _hash(payload):
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
def test_process_exit_preserves_complete_scenario_or_nothing(tmp_path, boundary):
    path = tmp_path / "crash.sqlite"
    runtime = ControllerRuntime(path)
    store = SimulatedTopologyStore(path)
    scenario = resilience_demo_scenario("process-crash-witness")
    # Commit schemas before the child so interruption targets the scenario,
    # rather than the initialization of an empty database.
    assert store.get(scenario.topology_id) is None
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    child = subprocess.run(
        [sys.executable, "-c", _CHILD, str(path), boundary],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert child.returncode == (23 if boundary == "before_commit" else 24), child.stderr

    reopened_runtime = ControllerRuntime(path)
    reopened_store = SimulatedTopologyStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        cycles = connection.execute(
            "SELECT record_json, record_sha256 FROM control_cycles ORDER BY sequence"
        ).fetchall()
    receipt = reopened_store.scenario_run(scenario.scenario_id)
    if boundary == "before_commit":
        assert cycles == []
        assert receipt is None
        assert reopened_store.get(scenario.topology_id) is None
        assert reopened_store.list_events(scenario.topology_id) == []
        assert runtime.journal.list_recent() == []
        return

    assert cycles and receipt is not None
    assert all(_hash(json.loads(serialized)) == digest for serialized, digest in cycles)
    assert _hash(receipt["record"]) == receipt["record_sha256"]
    assert reopened_store.get(scenario.topology_id).to_dict() == receipt["record"]["final_topology"]
    assert reopened_store.list_events(scenario.topology_id)

    class RefuseRepeatedExecution:
        def execute(self, *args, **kwargs):
            pytest.fail("a committed scenario must replay without another execution")

    reopened_runtime.simulated_executor = RefuseRepeatedExecution()
    replay = TopologyScenarioRunner(reopened_runtime, reopened_store).run(scenario)
    assert replay["idempotent_replay"] is True
    assert replay["record"] == receipt["record"]
    assert replay["record_sha256"] == receipt["record_sha256"]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM control_cycles").fetchone()[0] == len(cycles)
