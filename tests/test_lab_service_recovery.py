"""Actual worker termination, cross-process exclusion and receipt reconciliation.

Runners are deliberately small identified fixtures. SQLite commits, OS file
locks, file replacement, termination and restart use real local processes.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from tests.security_fixtures import provision_test_kme_identities

from quantum_autopilot.lab.service import LaboratoryService, canonical

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.integration

CHILD = textwrap.dedent("""
    import json, os, sys, time
    from pathlib import Path
    from quantum_autopilot.lab.service import LaboratoryService
    from quantum_autopilot.lab import qkd
    root, stage = Path(sys.argv[1]), sys.argv[2]
    def stop_here(name):
        print(json.dumps({'stage': name, 'process_id': os.getpid()}), flush=True)
        time.sleep(90)
    def fixture(folder, **kwargs):
        with (root / 'executions.jsonl').open('a') as output:
            output.write(json.dumps({'run_id': folder.parent.name}) + '\\n')
            output.flush()
            os.fsync(output.fileno())
        if stage == 'runner_active':
            stop_here(stage)
        return {'mode': 'emulation', 'all_passed': True, 'fixture_id': 'recovery-worker-v1'}
    qkd.run_qkd_demo = fixture
    class Worker(LaboratoryService):
        def _durability_checkpoint(self, name, record):
            if name == stage and (name == 'running_committed' or record['status'] == 'completed'):
                stop_here(name)
    service=Worker(root)
    from tests.security_fixtures import provision_test_kme_identities
    provision_test_kme_identities(service.security)
    service.run({'experiment': 'qkd'})
""")


def _worker(root, stage):
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    child = subprocess.Popen([sys.executable, "-u", "-c", CHILD, str(root), stage], cwd=ROOT,
                             env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    line = child.stdout.readline().strip()
    try:
        marker = json.loads(line)
    except ValueError:
        marker = {}
    if marker.get("stage") != stage:
        stdout, stderr = child.communicate(timeout=15)
        pytest.fail(f"Worker never reached {stage}: {line}; {stdout}; {stderr}")
    child.worker_pid = marker["process_id"]
    return child


def _kill(child):
    # Windows virtualenv python.exe can be a launcher with a different PID.
    # Terminate the exact interpreter that acquired the lease, not its launcher.
    if os.name == "nt" and child.worker_pid != child.pid:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x0001, False, child.worker_pid)
        assert handle, ctypes.get_last_error()
        try:
            assert kernel.TerminateProcess(handle, 99), ctypes.get_last_error()
        finally:
            kernel.CloseHandle(handle)
    else:
        child.kill()
    child.communicate(timeout=15)


def _events(root):
    with sqlite3.connect(root / "laboratory.sqlite") as connection:
        return connection.execute("SELECT status, event_sha256 FROM run_events ORDER BY sequence").fetchall()


def _assert_projection(root, receipt):
    file = root / receipt["run_id"] / "receipt.json"
    assert file.read_text(encoding="utf-8") == canonical(receipt)
    check = dict(receipt)
    assert check.pop("receipt_sha256") == hashlib.sha256(canonical(check).encode()).hexdigest()
    with sqlite3.connect(root / "laboratory.sqlite") as connection:
        row = connection.execute("SELECT receipt_sha256, published_sha256, last_error_type FROM receipt_outbox").fetchone()
        assert row == (receipt["receipt_sha256"], receipt["receipt_sha256"], None)


@pytest.mark.parametrize("stage,expected", [
    ("running_committed", "interrupted"),
    ("runner_active", "interrupted"),
    ("record_commit_pending", "interrupted"),
    ("terminal_committed", "completed"),
    ("receipt_fsynced", "completed"),
    ("receipt_replaced", "completed"),
])
def test_process_termination_recovers_committed_state_without_rerunning(tmp_path, stage, expected):
    root = tmp_path / "laboratory"
    child = _worker(root, stage)
    _kill(child)
    service = LaboratoryService(root)
    provision_test_kme_identities(service.security)
    receipt = service.history()["runs"][0]
    assert receipt["status"] == expected
    assert receipt["ownership"]["process_id"] == child.worker_pid
    assert receipt["ownership"]["lease"] == "process_handle_lifetime"
    if expected == "interrupted":
        assert receipt["recovery_policy"] == "never_resume_or_repeat_automatically"
        assert receipt["results"] == {}
    else:
        assert receipt["results"]["qkd"]["fixture_id"] == "recovery-worker-v1"
    _assert_projection(root, receipt)
    before = _events(root)
    assert [status for status, _ in before] == ["running", expected]
    for _ in range(3):
        assert LaboratoryService(root).history()["runs"] == [receipt]
    assert _events(root) == before
    executions = root / "executions.jsonl"
    count = len(executions.read_text().splitlines()) if executions.exists() else 0
    assert count == (0 if stage == "running_committed" else 1)


def test_independent_process_cannot_execute_or_recover_live_worker(tmp_path):
    root = tmp_path / "laboratory"
    child = _worker(root, "runner_active")
    try:
        observer = LaboratoryService(root)
        assert observer.recover()["busy"] is True
        current = observer.history()["runs"][0]
        assert current["status"] == "running"
        with pytest.raises(ValueError, match="laboratory_busy"):
            observer.run({"experiment": "qkd"})
        assert len(observer.history()["runs"]) == 1
        assert [status for status, _ in _events(root)] == ["running"]
    finally:
        _kill(child)
    assert LaboratoryService(root).history()["runs"][0]["status"] == "interrupted"


def test_service_instances_share_os_lock_in_same_process(tmp_path):
    first, second = LaboratoryService(tmp_path), LaboratoryService(tmp_path)
    with first.exclusive_operation():
        with pytest.raises(ValueError, match="laboratory_busy"):
            with second.exclusive_operation():
                pytest.fail("Second service acquired the same laboratory")
    with second.exclusive_operation():
        pass


def test_missing_or_changed_file_is_rebuilt_without_reexecuting(tmp_path, monkeypatch):
    from quantum_autopilot.lab import qkd
    calls = []
    monkeypatch.setattr(qkd, "run_qkd_demo", lambda folder, **kwargs: calls.append(folder) or {"all_passed": True})
    service = LaboratoryService(tmp_path)
    provision_test_kme_identities(service.security)
    receipt = service.run({"experiment": "qkd"})
    original_events = _events(tmp_path)
    file = tmp_path / receipt["run_id"] / "receipt.json"
    changed = '{"uncommitted_projection":true}'
    file.write_text(changed)
    assert service.recover()["pending_receipts"] == []
    assert any(previous.read_text() == changed for previous in file.parent.glob("receipt.previous-*.json"))
    _assert_projection(tmp_path, receipt)
    file.unlink()
    _assert_projection(tmp_path, LaboratoryService(tmp_path).history()["runs"][0])
    assert len(calls) == 1
    assert _events(tmp_path) == original_events


def test_publication_failure_is_pending_and_reconcilable(tmp_path, monkeypatch):
    from quantum_autopilot.lab import qkd
    import quantum_autopilot.lab.service as implementation
    monkeypatch.setattr(qkd, "run_qkd_demo", lambda folder, **kwargs: {"all_passed": True})
    replace = implementation.os.replace
    def refuse_receipt(source, target):
        if Path(target).name == "receipt.json":
            raise PermissionError("secret path")
        return replace(source, target)
    monkeypatch.setattr(implementation.os, "replace", refuse_receipt)
    service = LaboratoryService(tmp_path)
    provision_test_kme_identities(service.security)
    receipt = service.run({"experiment": "qkd"})
    assert receipt["status"] == "completed"
    state = service.history()
    assert state["persistence"]["reconciliation"]["pending_receipts"] == [receipt["run_id"]]
    with sqlite3.connect(service.database) as connection:
        assert connection.execute("SELECT last_error_type FROM receipt_outbox").fetchone() == ("PermissionError",)
    assert "secret path" not in canonical(state)
    monkeypatch.setattr(implementation.os, "replace", replace)
    assert service.recover()["pending_receipts"] == []
    _assert_projection(tmp_path, receipt)


@pytest.mark.parametrize("table,column", [("run_events", "payload_json"), ("receipt_outbox", "receipt_sha256")])
def test_cross_artifact_tampering_is_not_silently_reconciled(tmp_path, monkeypatch, table, column):
    from quantum_autopilot.lab import qkd
    monkeypatch.setattr(qkd, "run_qkd_demo", lambda folder, **kwargs: {"all_passed": True})
    service = LaboratoryService(tmp_path)
    provision_test_kme_identities(service.security)
    service.run({"experiment": "qkd"})
    with sqlite3.connect(service.database) as connection:
        connection.execute(f"UPDATE {table} SET {column}=?", ("{}" if column == "payload_json" else "f" * 64,))
    with pytest.raises(ValueError, match="laboratory_(event_chain|outbox_receipt_binding)_invalid"):
        service.history()


def test_legacy_abandoned_receipt_is_marked_unverified_and_interrupted(tmp_path):
    from uuid import uuid4
    run_id = str(uuid4())
    old = {"schema": "orion.lab.run.v1", "run_id": run_id, "mode": "emulation",
           "physical_action_performed": False, "status": "running", "results": {},
           "request": {"experiment": "qkd"}, "started_at_utc": "2026-10-07T00:00:00+00:00"}
    with sqlite3.connect(tmp_path / "laboratory.sqlite") as connection:
        connection.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, receipt_json TEXT NOT NULL)")
        connection.execute("INSERT INTO runs VALUES (?, ?)", (run_id, canonical(old)))
    receipt = LaboratoryService(tmp_path).history()["runs"][0]
    assert receipt["status"] == "interrupted" and receipt["legacy_running_receipt_unverified"] is True
    _assert_projection(tmp_path, receipt)


@pytest.mark.parametrize("table,message", [
    ("runs", "laboratory_orphan_journal_record"),
    ("run_events", "laboratory_event_receipt_binding_invalid"),
])
def test_deleted_journal_records_are_not_misrepresented_as_verified_history(tmp_path, monkeypatch, table, message):
    from quantum_autopilot.lab import qkd
    monkeypatch.setattr(qkd, "run_qkd_demo", lambda folder, **kwargs: {"all_passed": True})
    service = LaboratoryService(tmp_path)
    provision_test_kme_identities(service.security)
    service.run({"experiment": "qkd"})
    with sqlite3.connect(service.database) as connection:
        connection.execute(f"DELETE FROM {table}")
    with pytest.raises(ValueError, match=message):
        service.history()


def test_cancellation_is_explicit_and_releases_both_locks(tmp_path, monkeypatch):
    from quantum_autopilot.lab import qkd
    def cancel(folder, **kwargs):
        raise KeyboardInterrupt("private cancellation context")
    monkeypatch.setattr(qkd, "run_qkd_demo", cancel)
    service = LaboratoryService(tmp_path)
    provision_test_kme_identities(service.security)
    with pytest.raises(KeyboardInterrupt):
        service.run({"experiment": "qkd"})
    receipt = service.history()["runs"][0]
    assert receipt["status"] == "interrupted" and receipt["error_type"] == "KeyboardInterrupt"
    assert "private cancellation context" not in canonical(receipt)
    with LaboratoryService(tmp_path).exclusive_operation():
        pass
    _assert_projection(tmp_path, receipt)
