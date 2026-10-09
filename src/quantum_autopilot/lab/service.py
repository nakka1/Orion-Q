"""Persistent laboratory receipts shared by the native console, HTTP and CLI.

These workflows explicitly use emulated input. Their receipts cannot authorize
hardware or turn the network engine's conditional key budgets into key material.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def unique_fields(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate field: {key}")
        value[key] = item
    return value


from quantum_autopilot.security.service_boundary import secure_service


@secure_service
class LaboratoryService:
    schema = "orion.lab.run.v1"

    def __init__(self, root: str | Path, *, security=None):
        self.root = Path(root).resolve()
        self._security = security
        self.database = self.root / "laboratory.sqlite"
        self._execution_lock = Lock()
        self._process_instance_id = str(uuid4())
        # Construction remains read-only for an unused laboratory. Existing
        # journals are recovered only after obtaining the OS-owned lock.
        if self.database.is_file() and self._may_restore():
            self.recover()

    @property
    def security(self):
        if self._security is None:
            from quantum_autopilot.security import SecurityRuntime
            name = hashlib.sha256(str(self.root).encode()).hexdigest()[:24]
            self._security = SecurityRuntime(self.root.parent / ".security" / name)
        return self._security

    @property
    def configuration_store(self):
        from .configuration import LaboratoryConfigurationStore
        return LaboratoryConfigurationStore(self.root)

    def _may_restore(self):
        """Read-only callers can inspect receipts without implicitly recovering data."""
        from quantum_autopilot.security.policy import require_permission
        from quantum_autopilot.security import AuthorizationError
        from quantum_autopilot.security.service_boundary import service_resource
        identity = self.security.current_identity()
        try:
            require_permission(identity.roles, identity.scopes, "state.restore", service_resource(self))
        except AuthorizationError:
            return False
        return True

    def capabilities(self):
        availability = self.configuration_store.availability()
        from quantum_autopilot.security.key_requirements import check_kme_security, required_kme_identities
        key_security = check_kme_security(self.security)
        for name in ("qkd", "all"):
            availability["experiments"][name]["security"] = key_security
            availability["experiments"][name]["available"] &= key_security["available"]
        availability["required_test_identities"] = required_kme_identities()
        result = {"schema": "orion.lab.capabilities.v1", "available": True,
                "experiments": ["photon", "qkd", "all"],
                "execution_mode": "emulation", "physical_action_performed": False,
                "replay_origin_preserved": True,
                "dependencies": availability["dependencies"],
                "scope": "Local test bench; hardware and certified secret-key security unvalidated"}
        result["availability"] = availability
        result["available"] = availability["available"]
        return result

    @contextmanager
    def exclusive_operation(self):
        """Single executor across service instances and local processes.

        The lease belongs to an open OS file handle, not a timestamp or PID.
        A slow healthy worker cannot lose its lease, and process death releases
        it even if Python never reaches its finally block.
        """
        if not self._execution_lock.acquire(blocking=False):
            raise ValueError("laboratory_busy")
        handle = None
        locked = False
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            handle = (self.root / ".execution.lock").open("a+b")
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as error:
                raise ValueError("laboratory_busy") from error
            yield
        finally:
            try:
                if handle is not None:
                    try:
                        if locked:
                            if os.name == "nt":
                                import msvcrt
                                handle.seek(0)
                                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                            else:
                                import fcntl
                                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    finally:
                        handle.close()
            finally:
                self._execution_lock.release()

    def configure(self, request):
        with self.exclusive_operation():
            return self.configuration_store.configure(request)

    @contextmanager
    def _connection(self):
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database, timeout=10, isolation_level=None)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, receipt_json TEXT NOT NULL)")
            connection.execute("""CREATE TABLE IF NOT EXISTS receipt_outbox (
                run_id TEXT PRIMARY KEY, receipt_json TEXT NOT NULL, receipt_sha256 TEXT NOT NULL,
                published_sha256 TEXT, last_error_type TEXT)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS run_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                status TEXT NOT NULL, receipt_sha256 TEXT NOT NULL,
                previous_sha256 TEXT NOT NULL, event_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL)""")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _stamp(record):
        record.pop("receipt_sha256", None)
        record["receipt_sha256"] = hashlib.sha256(canonical(record).encode()).hexdigest()
        return record

    def _verify(self, run_id, serialized, *, allow_legacy_running=False):
        record = json.loads(serialized, object_pairs_hook=unique_fields)
        if not isinstance(record, dict):
            raise ValueError("laboratory_receipt_binding_invalid")
        try:
            valid_id = str(UUID(run_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("laboratory_receipt_binding_invalid") from error
        if valid_id != run_id or record.get("run_id") != run_id or record.get("schema") != self.schema:
            raise ValueError("laboratory_receipt_binding_invalid")
        check = dict(record)
        sha = check.pop("receipt_sha256", None)
        legacy = allow_legacy_running and record.get("status") == "running" and sha is None
        if not legacy and sha != hashlib.sha256(canonical(check).encode()).hexdigest():
            raise ValueError("laboratory_receipt_hash_invalid")
        if record.get("mode") != "emulation" or record.get("physical_action_performed") is not False:
            raise ValueError("laboratory_receipt_binding_invalid")
        return record

    def _durability_checkpoint(self, stage, record):
        """Fault-injection seam; the production implementation performs no action."""

    def _save(self, record, *, expected_sha=None):
        self._stamp(record)
        serialized = canonical(record)
        with self._connection() as connection:
            existing = connection.execute("SELECT receipt_json FROM runs WHERE run_id=?", (record["run_id"],)).fetchone()
            if existing is None:
                if expected_sha is not None or record["status"] != "running":
                    raise ValueError("laboratory_run_transition_invalid")
                connection.execute("INSERT INTO runs VALUES (?, ?)", (record["run_id"], serialized))
            else:
                old = self._verify(record["run_id"], existing[0], allow_legacy_running=True)
                if old["status"] != "running" or old.get("receipt_sha256") != expected_sha:
                    raise ValueError("laboratory_run_transition_conflict")
                if record["status"] not in {"completed", "failed", "interrupted"}:
                    raise ValueError("laboratory_run_transition_invalid")
                connection.execute("UPDATE runs SET receipt_json=? WHERE run_id=?", (serialized, record["run_id"]))
            connection.execute("""INSERT INTO receipt_outbox
                (run_id, receipt_json, receipt_sha256) VALUES (?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET receipt_json=excluded.receipt_json,
                    receipt_sha256=excluded.receipt_sha256, last_error_type=NULL""",
                (record["run_id"], serialized, record["receipt_sha256"]))
            previous = connection.execute("SELECT event_sha256 FROM run_events ORDER BY sequence DESC LIMIT 1").fetchone()
            previous_sha = previous[0] if previous else "0" * 64
            payload = {"run_id": record["run_id"], "status": record["status"],
                       "receipt_sha256": record["receipt_sha256"], "previous_sha256": previous_sha,
                       "recorded_at_utc": datetime.now(timezone.utc).isoformat()}
            event = canonical(payload)
            connection.execute("""INSERT INTO run_events
                (run_id, status, receipt_sha256, previous_sha256, event_sha256, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)""", (record["run_id"], record["status"], record["receipt_sha256"],
                    previous_sha, hashlib.sha256(event.encode()).hexdigest(), event))
            self._durability_checkpoint("record_commit_pending", record)

    def _publish_locked(self):
        """Reconcile filesystem projections from committed SQLite receipts.

        The file is a projection, never the authority. Loss of a file or a crash
        between replace and acknowledgement cannot restart an experiment.
        """
        with self._connection() as connection:
            rows = connection.execute("""SELECT run_id, receipt_json, receipt_sha256,
                published_sha256, last_error_type FROM receipt_outbox""").fetchall()
        published, pending = [], []
        for run_id, serialized, sha, published_sha, last_error in rows:
            record = self._verify(run_id, serialized)
            if sha != record["receipt_sha256"]:
                raise ValueError("laboratory_outbox_hash_invalid")
            folder = self.root / run_id
            target = folder / "receipt.json"
            try:
                if folder.resolve().parent != self.root or target.is_symlink():
                    raise OSError("laboratory_receipt_path_invalid")
                previous_content = target.read_bytes() if target.is_file() else None
                content_matches = previous_content == serialized.encode("utf-8")
                if not content_matches:
                    folder.mkdir(parents=True, exist_ok=True)
                    if previous_content is not None:
                        # Preserve a mismatched projection for reconciliation
                        # instead of destroying the evidence of divergence.
                        previous_hash = hashlib.sha256(previous_content).hexdigest()
                        conflict = folder / f"receipt.previous-{previous_hash}.json"
                        if not conflict.exists() or conflict.read_bytes() != previous_content:
                            conflict_pending = folder / ".receipt.previous.pending"
                            with conflict_pending.open("wb") as backup:
                                backup.write(previous_content)
                                backup.flush()
                                os.fsync(backup.fileno())
                            os.replace(conflict_pending, conflict)
                    pending_file = folder / ".receipt.pending"
                    with pending_file.open("wb") as output:
                        output.write(serialized.encode("utf-8"))
                        output.flush()
                        os.fsync(output.fileno())
                    self._durability_checkpoint("receipt_fsynced", record)
                    os.replace(pending_file, target)
                    self._durability_checkpoint("receipt_replaced", record)
                if published_sha != sha or last_error is not None:
                    with self._connection() as connection:
                        connection.execute("""UPDATE receipt_outbox SET published_sha256=?, last_error_type=NULL
                            WHERE run_id=? AND receipt_sha256=?""", (sha, run_id, sha))
                published.append(run_id)
            except OSError as error:
                with self._connection() as connection:
                    connection.execute("UPDATE receipt_outbox SET last_error_type=? WHERE run_id=?",
                                       (type(error).__name__, run_id))
                pending.append(run_id)
        return {"published_receipts": published, "pending_receipts": pending}

    def _recover_locked(self):
        recovered = []
        with self._connection() as connection:
            rows = connection.execute("SELECT run_id, receipt_json FROM runs").fetchall()
            outbox = {run_id: (serialized, sha) for run_id, serialized, sha in connection.execute(
                "SELECT run_id, receipt_json, receipt_sha256 FROM receipt_outbox").fetchall()}
            events = connection.execute("""SELECT run_id, status, receipt_sha256, previous_sha256,
                event_sha256, payload_json FROM run_events ORDER BY sequence""").fetchall()
        previous = "0" * 64
        latest = {}
        for run_id, status, sha, previous_sha, event_sha, payload_json in events:
            payload = json.loads(payload_json, object_pairs_hook=unique_fields)
            if (previous_sha != previous or payload.get("previous_sha256") != previous
                    or payload.get("run_id") != run_id or payload.get("status") != status
                    or payload.get("receipt_sha256") != sha
                    or hashlib.sha256(canonical(payload).encode()).hexdigest() != event_sha):
                raise ValueError("laboratory_event_chain_invalid")
            previous = event_sha
            latest[run_id] = (status, sha)
        known_runs = {run_id for run_id, _ in rows}
        if set(latest) - known_runs or set(outbox) - known_runs:
            raise ValueError("laboratory_orphan_journal_record")
        for run_id, serialized in rows:
            record = self._verify(run_id, serialized, allow_legacy_running=True)
            if record.get("receipt_policy") == "sqlite_authority_atomic_filesystem_projection" and run_id not in latest:
                raise ValueError("laboratory_event_receipt_binding_invalid")
            if run_id in latest and latest[run_id] != (record["status"], record.get("receipt_sha256")):
                raise ValueError("laboratory_event_receipt_binding_invalid")
            if run_id in outbox and outbox[run_id] != (serialized, record.get("receipt_sha256")):
                raise ValueError("laboratory_outbox_receipt_binding_invalid")
            if record.get("status") == "running":
                expected = record.get("receipt_sha256")
                record.update(status="interrupted", ended_at_utc=datetime.now(timezone.utc).isoformat(),
                              recovery_reason="executor_lease_released_before_terminal_commit",
                              recovery_policy="never_resume_or_repeat_automatically")
                if expected is None:
                    record["legacy_running_receipt_unverified"] = True
                self._save(record, expected_sha=expected)
                recovered.append(run_id)
            elif run_id not in outbox:
                # Migrate old completed journals into the outbox without
                # changing their signed receipt or appending duplicate events.
                with self._connection() as connection:
                    connection.execute("""INSERT OR IGNORE INTO receipt_outbox
                        (run_id, receipt_json, receipt_sha256) VALUES (?, ?, ?)""",
                        (run_id, serialized, record["receipt_sha256"]))
        return {"recovered_runs": recovered, **self._publish_locked(), "busy": False}

    def recover(self):
        if not self.database.is_file():
            return {"recovered_runs": [], "published_receipts": [], "pending_receipts": [], "busy": False}
        try:
            with self.exclusive_operation():
                return self._recover_locked()
        except ValueError as error:
            if str(error) != "laboratory_busy":
                raise
            return {"recovered_runs": [], "published_receipts": [], "pending_receipts": [], "busy": True}

    def history(self):
        if not self.database.is_file():
            return {"schema": self.schema, "runs": [], "physical_action_performed": False}
        reconciliation = (self.recover() if self._may_restore() else
                          {"recovery": "requires_authorized_recovery", "read_only": True})
        # Reading an existing journal must never initialize a missing database.
        connection = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=10)
        try:
            rows = connection.execute("SELECT run_id, receipt_json FROM runs ORDER BY rowid DESC LIMIT 20").fetchall()
        finally:
            connection.close()
        records = []
        for run_id, serialized in rows:
            record = self._verify(run_id, serialized)
            records.append(record)
        return {"schema": self.schema, "runs": records,
                "physical_action_performed": False,
                "persistence": {"authority": "sqlite", "reconciliation": reconciliation}}

    def run(self, request):
        if not isinstance(request, dict) or set(request) != {"experiment"} or request["experiment"] not in {
                "photon", "qkd", "all"}:
            raise ValueError("request requires only experiment: photon, qkd or all")
        # Refuse missing dependencies/invalid configuration before the lock
        # file or any experiment directory can be created. Recheck under the
        # lock as configuration can change while this call waits to execute.
        self.configuration_store.snapshot_for_run(request["experiment"])
        if request["experiment"] in {"qkd", "all"}:
            from quantum_autopilot.security.key_requirements import check_kme_security
            if not check_kme_security(self.security)["available"]:
                raise ValueError("laboratory_test_identities_not_provisioned")
        with self.exclusive_operation():
            return self._run_locked(request)

    def _run_locked(self, request):
        snapshot = self.configuration_store.snapshot_for_run(request["experiment"])
        if self.database.is_file():
            self._recover_locked()
        record = {"schema": self.schema, "run_id": str(uuid4()), "request": dict(request),
                  "mode": "emulation", "physical_action_performed": False, "status": "running",
                  "started_at_utc": datetime.now(timezone.utc).isoformat(), "results": {},
                  "ownership": {"process_id": os.getpid(), "process_instance_id": self._process_instance_id,
                                "coordination": "os_exclusive_lock", "lease": "process_handle_lifetime"},
                  "configuration_revision": snapshot["revision"], "configuration_sha256": snapshot["sha256"],
                  "configuration": snapshot["configuration"],
                  "receipt_policy": "sqlite_authority_atomic_filesystem_projection"}
        folder = self.root / record["run_id"]
        folder.mkdir(parents=True, exist_ok=False)
        self._save(record)
        running_sha = record["receipt_sha256"]
        self._durability_checkpoint("running_committed", record)
        self._publish_locked()
        try:
            if request["experiment"] in {"photon", "all"}:
                from .photon import run_photon_demo
                kwargs = ({"config": self.configuration_store.photon_config(snapshot),
                           "adapter_id": snapshot["configuration"]["photon"]["adapter_id"]}
                          if snapshot["customized"] else {})
                if snapshot["configuration"]["photon"]["adapter_id"] == "orion-replay":
                    from .photon import run_registered_acquisition
                    kwargs["manifest_path"] = self.configuration_store.replay_manifest(snapshot)
                    record["results"]["photon"] = run_registered_acquisition(folder / "photon", security=self.security, **kwargs)
                else:
                    record["results"]["photon"] = run_photon_demo(folder / "photon", security=self.security, **kwargs)
            if request["experiment"] in {"qkd", "all"}:
                from .qkd import run_qkd_demo
                kwargs = {"config": self.configuration_store.qkd_config(snapshot)} if snapshot["customized"] else {}
                record["results"]["qkd"] = run_qkd_demo(folder / "qkd", security=self.security, **kwargs)
            if any(value.get("all_passed") is False or value.get("passed") is False
                   for value in record["results"].values()):
                raise ValueError("laboratory_acceptance_checks_failed")
            record["status"] = "completed"
        except BaseException as error:
            record["status"] = "failed" if isinstance(error, Exception) else "interrupted"
            if record["status"] == "interrupted":
                record["recovery_reason"] = "execution_cancelled_before_terminal_commit"
                record["recovery_policy"] = "never_resume_or_repeat_automatically"
            # No exception payload may leak plaintext, keys, tokens or certificate private keys.
            record["error_type"] = type(error).__name__
            raise
        finally:
            record["ended_at_utc"] = datetime.now(timezone.utc).isoformat()
            self._save(record, expected_sha=running_sha)
            self._durability_checkpoint("terminal_committed", record)
            self._publish_locked()
        return json.loads(canonical(record))
