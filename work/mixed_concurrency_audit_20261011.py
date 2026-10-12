"""Disposable source audit probes; never opens an existing service state root."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from threading import Barrier, Event, Thread
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

import pytest
from quantum_autopilot.control_plane.mixed_controller import MixedController
from quantum_autopilot.service.client import CoreClient, CoreRequestError
from tests.test_mixed_controller import configuration, demand, ready_records, record
from tests.test_portable_core_service import provisioned, serving


def simultaneous(call, identifiers):
    barrier = Barrier(2)
    def one(identifier):
        barrier.wait(timeout=5)
        started = time.perf_counter_ns()
        result = call(identifier)
        return {"started_ns": started, "result": result}
    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(one, identifiers))


def ready(path):
    core = MixedController(path)
    cfg = configuration()
    core.configure(cfg)
    core.ingest({"records": ready_records(cfg)})
    return core, cfg


def main():
    output = {"source_root": str(ROOT), "sqlite_version": sqlite3.sqlite_version,
              "scope": "synthetic control slots; no physical memory or hardware"}
    with tempfile.TemporaryDirectory(prefix="mixed-audit-", dir=ROOT / "work") as directory:
        area = Path(directory)
        core, cfg = ready(area / "mixed.sqlite")
        starts, outcomes = [], []
        for n in range(16):
            rows = simultaneous(lambda identifier: core.demand(demand(identifier)), [f"a-{n}", f"b-{n}"])
            starts.append(abs(rows[0]["started_ns"] - rows[1]["started_ns"]) / 1e6)
            statuses = sorted(row["result"]["status"] for row in rows)
            assert statuses == ["blocked", "reserved"], statuses
            winner = next(row["result"] for row in rows if row["result"]["status"] == "reserved")
            core.release({"request_id": winner["request_id"]})
            outcomes.append(statuses)
        rows = simultaneous(lambda identifier: core.demand(demand(identifier)), ["same", "same"])
        assert rows[0]["result"] == rows[1]["result"]
        assert rows[0]["result"]["status"] == "reserved"
        with closing(sqlite3.connect(core.path)) as db:
            assert db.execute("SELECT count(*) FROM mixed_reservations WHERE request_id='same'").fetchone()[0] == 1
            mode = db.execute("PRAGMA journal_mode").fetchone()[0]
            before = db.execute("SELECT count(*) FROM mixed_events").fetchone()[0]
        bad = record(cfg, "N02", 3, "heartbeat", {})
        bad["observation"]["session_id"] = "wrong-session"
        from quantum_autopilot.control_plane.mixed_contracts import digest
        bad["provenance"]["record_sha256"] = digest(bad["observation"])
        try:
            core.ingest({"records": [record(cfg, "N01", 3, "heartbeat", {}), bad]})
        except ValueError:
            pass
        else:
            raise AssertionError("invalid second record unexpectedly accepted")
        with closing(sqlite3.connect(core.path)) as db:
            assert db.execute("SELECT count(*) FROM mixed_events").fetchone()[0] == before
            assert json.loads(db.execute("SELECT body FROM mixed_nodes WHERE node_id='N01'").fetchone()[0])["sequence"] == 2
        environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        command = [sys.executable, "-c", "from quantum_autopilot.control_plane.mixed_controller import MixedController; import sys; MixedController(sys.argv[1])", str(core.path)]
        child = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=15)
        assert child.returncode != 0 and "already has a live process authority" in child.stderr, child.stderr
        core.release({"request_id": "same"})
        deadline = time.monotonic() + .04
        admitted, started = Event(), Event()
        errors = []
        def waiting_operation():
            started.set()
            def admit():
                admitted.set()
                if time.monotonic() >= deadline:
                    raise TimeoutError("fixture expired after SQLite wait")
            try:
                core.demand(demand("deadline"), admit=admit)
            except Exception as error:
                errors.append(type(error).__name__)
        with closing(sqlite3.connect(core.path)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            worker = Thread(target=waiting_operation)
            worker.start()
            assert started.wait(1)
            time.sleep(.10)
            assert not admitted.is_set()
            blocker.rollback()
        worker.join(5)
        assert not worker.is_alive() and errors == ["TimeoutError"]
        with closing(sqlite3.connect(core.path)) as db:
            assert db.execute("SELECT count(*) FROM mixed_reservations WHERE request_id='deadline'").fetchone()[0] == 0
        output["direct"] = {"rounds": len(outcomes), "outcomes_each": ["blocked", "reserved"],
                            "start_gap_ms_min": min(starts), "start_gap_ms_max": max(starts),
                            "same_request_equal_receipts_and_one_row": True,
                            "batch_mid_failure_rollback": True, "second_process_owner_rejected": True,
                            "admit_after_wait_no_commit_when_expired": True, "journal_mode": mode}
        core.close()

        independent = []
        try:
            for name in ("workspace-one", "workspace-two"):
                other, _ = ready(area / (name + ".sqlite"))
                independent.append(other)
            statuses = [other.demand(demand())["status"] for other in independent]
            assert statuses == ["reserved", "reserved"]
            output["different_databases_same_node_ids"] = {
                "statuses": statuses, "global_hardware_coordination": False}
        finally:
            for other in independent:
                other.close()

        monkeypatch = pytest.MonkeyPatch()
        native_fixture = tempfile.TemporaryDirectory(prefix="mixed-api-audit-", dir="/var/tmp")
        config, runtime, *rest = provisioned.__wrapped__(Path(native_fixture.name), monkeypatch)
        operator_context = rest[-1]
        try:
            with serving(config, runtime) as server:
                url = f"https://localhost:{server.server_port}"
                operator = CoreClient(url, operator_context)
                operator.mixed_configure(cfg)
                operator.mixed_ingest({"records": ready_records(cfg)})
                gaps = []
                for n in range(6):
                    rows = simultaneous(lambda identifier: CoreClient(url, operator_context).mixed_demand(demand(identifier)),
                                        [f"http-a-{n}", f"http-b-{n}"])
                    gaps.append(abs(rows[0]["started_ns"] - rows[1]["started_ns"]) / 1e6)
                    assert sorted(row["result"]["status"] for row in rows) == ["blocked", "reserved"]
                    winner = next(row["result"] for row in rows if row["result"]["status"] == "reserved")
                    operator.mixed_release({"request_id": winner["request_id"]})
                rows = simultaneous(lambda identifier: CoreClient(url, operator_context).mixed_demand(demand(identifier)),
                                    ["http-same", "http-same"])
                assert rows[0]["result"] == rows[1]["result"] and rows[0]["result"]["status"] == "reserved"
                operator.mixed_release({"request_id": "http-same"})
                original_record = runtime.audit.record
                def fail_completed(identity, action, resource, outcome, details=None):
                    if action == "network.operate" and outcome == "completed":
                        raise RuntimeError("fixture post-commit audit failure")
                    return original_record(identity, action, resource, outcome, details)
                runtime.audit.record = fail_completed
                try:
                    operator.mixed_demand(demand("postcommit"))
                except CoreRequestError as error:
                    postcommit_http = error.status
                else:
                    raise AssertionError("fixture completion audit unexpectedly succeeded")
                finally:
                    runtime.audit.record = original_record
                stored = next(row for row in operator.mixed_status()["reservations"] if row["request_id"] == "postcommit")
                assert stored["status"] == "reserved" and postcommit_http == 500
                assert operator.mixed_demand(demand("postcommit")) == stored
                assert runtime.audit.verify()["valid"]
                output["api"] = {"rounds": 6, "outcomes_each": ["blocked", "reserved"],
                                 "start_gap_ms_min": min(gaps), "start_gap_ms_max": max(gaps),
                                 "same_request_equal_receipts": True, "audit_valid": True,
                                 "postcommit_audit_failure_http": postcommit_http,
                                 "receipt_persisted_despite_failure": stored["status"], "retry_same_id_equal": True}
                server.service._release_controller_io_resources()
        finally:
            monkeypatch.undo()
            native_fixture.cleanup()
    destination = ROOT / "work" / "mixed_concurrency_audit_20261011.json"
    destination.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
