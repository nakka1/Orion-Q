"""Authenticated consumption journal outside a recoverable key database.

The journal and its vault checkpoint form a separate recovery domain. Restoring
the SQLite database cannot undo a reservation. Rolling back *both* recovery
domains requires an external monotonic authority; a local file cannot prove that
the entire machine was rolled back. Operational deployments must preserve the
vault/checkpoint independently of key-database backups.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import time


class RecoveryIntegrityError(RuntimeError):
    """Acquisition/delivery must remain blocked until recovery is authorized."""


@contextmanager
def exclusive_file(path, timeout=15):
    """OS lock shared by threads/processes; released on process termination."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0)
    handle.write(b"0")
    handle.flush()
    deadline = time.monotonic() + timeout
    while True:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                raise RecoveryIntegrityError("protected journal lock unavailable") from None
            time.sleep(.01)
    try:
        yield
    finally:
        handle.close()


def _canonical(record):
    return json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _atomic_bytes(path, content):
    temporary = Path(str(path) + ".pending-" + os.urandom(6).hex())
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ConsumptionJournal:
    """Append-only MAC chain, with independently protected head checkpoint.

    A record contains identifiers and transitions, never key bytes. A missing,
    truncated or altered journal/checkpoint fails closed. Atomic SQLite and
    journal writes are intentionally not claimed: journal writes happen first,
    so a crash between stores conservatively burns the affected material.
    """
    states = {"reserved", "issued", "authorized", "consumed", "uncertain", "invalidated", "expired", "revoked"}

    def __init__(self, path, secret, *, vault=None, namespace="key-consumption", operational=False):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("journal authentication requires at least 256 bits")
        if operational and vault is None:
            raise ValueError("operational journal requires an independent protected vault checkpoint")
        self._secret, self.vault = secret, vault
        self.name = namespace + "-" + hashlib.sha256(str(self.path).encode()).hexdigest()[:24]
        self.anchor_path = Path(str(self.path) + ".checkpoint")
        with exclusive_file(str(self.path) + ".lock"):
            anchor = self._anchor()
            if not self.path.exists():
                if anchor is not None:
                    raise RecoveryIntegrityError("protected consumption journal missing")
                _atomic_bytes(self.path, b"")
                self._store_anchor({"count": 0, "head": "0" * 64})
            elif anchor is None:
                raise RecoveryIntegrityError("protected journal checkpoint missing")
            self._read()

    def _anchor(self):
        if self.vault is not None:
            if hasattr(self.vault, "contains") and not self.vault.contains(self.name):
                return None
            try:
                encoded = self.vault.get_bytes(self.name)
            except (KeyError, FileNotFoundError):
                return None
        elif self.anchor_path.exists():
            encoded = self.anchor_path.read_bytes()
        else:
            return None
        try:
            record = json.loads(encoded)
            expected = hmac.digest(self._secret, _canonical(record["checkpoint"]), "sha256").hex()
            if not hmac.compare_digest(expected, record["mac"]):
                raise ValueError()
            return record["checkpoint"]
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            raise RecoveryIntegrityError("protected journal checkpoint rejected") from None

    def _store_anchor(self, checkpoint):
        encoded = _canonical({"checkpoint": checkpoint,
            "mac": hmac.digest(self._secret, _canonical(checkpoint), "sha256").hex()})
        if self.vault is not None:
            self.vault.put_bytes(self.name, encoded)
        else:
            _atomic_bytes(self.anchor_path, encoded)
            if os.name != "nt":
                self.anchor_path.chmod(0o600)

    def _read(self):
        previous, records = "0" * 64, []
        try:
            for line in self.path.read_bytes().splitlines():
                row = json.loads(line)
                record = row["record"]
                expected = hmac.digest(self._secret, _canonical(record), "sha256").hex()
                if (record["seq"] != len(records) + 1 or record["previous"] != previous or
                        not hmac.compare_digest(expected, row["mac"])):
                    raise ValueError()
                previous = row["mac"]
                records.append(record)
            if self._anchor() != {"count": len(records), "head": previous}:
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeDecodeError, OSError):
            raise RecoveryIntegrityError("consumption journal integrity or rollback check failed") from None
        return records, previous

    def snapshot(self):
        with exclusive_file(str(self.path) + ".lock"):
            records, _ = self._read()
        return records

    def append(self, identifiers, state, token):
        identifiers = list(identifiers)
        if state not in self.states or not isinstance(token, str) or not token or not identifiers:
            raise ValueError("identified journal transition required")
        if len(set(identifiers)) != len(identifiers) or any(not isinstance(value, str) for value in identifiers):
            raise ValueError("unique journal identifiers required")
        with exclusive_file(str(self.path) + ".lock"):
            records, previous = self._read()
            record = {"seq": len(records) + 1, "previous": previous, "ids": identifiers,
                      "state": state, "token": token, "at": time.time()}
            mac = hmac.digest(self._secret, _canonical(record), "sha256").hex()
            with self.path.open("ab") as handle:
                handle.write(_canonical({"record": record, "mac": mac}) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._store_anchor({"count": len(records) + 1, "head": mac})
        return record["seq"]

    def latest(self):
        return {identifier: record for record in self.snapshot() for identifier in record["ids"]}

    def evidence(self):
        records = self.snapshot()
        return {"authenticated": True, "records": len(records), "key_material_recorded": False,
                "checkpoint_storage": "os_secret_vault" if self.vault is not None else "test_local_file",
                "recovery_domain": "outside_key_database"}
