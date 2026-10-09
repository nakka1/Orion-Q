"""Authenticated, serialized audit records with an OS-protected head checkpoint."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar

from .identity import Principal
from .vault import SecretVault, VaultError, file_lock


class AuditIntegrityError(RuntimeError):
    pass


_GENESIS = "0" * 64
_NUMERIC = {"count", "key_count", "pair_count", "sequence", "configuration_version", "duration_ms",
            "status_code", "lost_events", "accepted_events", "rejected_events"}
_HASHES = {"configuration_sha256", "artifact_sha256", "schema_sha256"}
_ENUMS = {"state": {"ready", "running", "blocked", "stopped", "failed", "revoked", "active", "expired"},
          "error_type": {"AuthenticationError", "AuthorizationError", "PermissionError", "ValueError", "TypeError",
                         "TimeoutError", "OSError", "RuntimeError", "ConnectionError", "VaultError",
                         "AuditIntegrityError", "KeyDeliveryError", "LeaseError"},
          "stage": {"observation", "basis_sifting", "parameter_estimation", "reconciliation", "verification",
                    "privacy_amplification", "abort", "unknown"},
          "mode": {"physical", "simulated", "local", "operational"},
          "reason": {"permission_denied", "authentication_failed", "configuration_invalid", "revoked",
                     "expired", "uncertain_delivery", "explicit_recovery", "policy_changed"}}


def redact_details(details) -> dict:
    """Allow data deliberately; arbitrary request/error text is never serialized."""
    if not isinstance(details, dict):
        return {}
    clean, removed = {}, 0
    for name, value in details.items():
        if name == "classical_message_accepted" and isinstance(value, bool):
            clean[name] = value
            continue
        if name in _NUMERIC and isinstance(value, (int, float)) and not isinstance(value, bool):
            if float("-inf") < value < float("inf"):
                clean[name] = value
                continue
        if name in _HASHES and isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value):
            clean[name] = value
            continue
        if name in _ENUMS and isinstance(value, str) and value in _ENUMS[name]:
            clean[name] = value
            continue
        removed += 1
    if removed:
        clean["redacted_fields"] = removed
    return clean


def _identifier(value, *, maximum=256):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        return "redacted"
    if any(character in value for character in "\r\n\x00?=") or "bearer " in value.lower():
        return "redacted"
    return value


def _canonical(row):
    return json.dumps(row, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()


class AuthenticatedAudit:
    def __init__(self, path, vault: SecretVault, *, name="audit-v1"):
        self.path, self.vault, self.name = Path(path), vault, name
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._key = vault.get_or_create_bytes(name + ":hmac", 32)
        self._checkpoint_name = name + ":checkpoint"
        self._verified_stat = None
        self._verified_status = None
        self._verified_checkpoint = None
        self._batch_rows = ContextVar(f"orion_audit_batch_{id(self)}", default=None)
        with vault.transaction() as rows:
            import base64
            if self._checkpoint_name not in rows:
                # Never silently reset a checkpoint for an existing evidence log.
                if self.path.exists() and self.path.stat().st_size:
                    raise AuditIntegrityError("audit checkpoint is missing for existing records")
                rows[self._checkpoint_name] = base64.b64encode(
                    _canonical({"sequence": 0, "head": _GENESIS})).decode("ascii")

    def _checkpoint(self):
        try:
            value = json.loads(self.vault.get_bytes(self._checkpoint_name))
            if not isinstance(value.get("sequence"), int) or value["sequence"] < 0:
                raise ValueError()
            if not re.fullmatch(r"[a-f0-9]{64}", value.get("head", "")):
                raise ValueError()
            return value
        except (VaultError, TypeError, ValueError) as exc:
            raise AuditIntegrityError("audit checkpoint is invalid") from exc

    def _file_stat(self):
        if not self.path.exists():
            return None
        stat = self.path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def _scan(self, *, use_cache=False):
        checkpoint = self._checkpoint()
        current_stat = self._file_stat()
        if (use_cache and self._verified_status is not None and current_stat == self._verified_stat
                and checkpoint == self._verified_checkpoint):
            return self._verified_status.copy()
        sequence, head, anchored = 0, _GENESIS, checkpoint["sequence"] == 0 and checkpoint["head"] == _GENESIS
        if self.path.exists():
            with self.path.open("rb") as log:
                for raw in log:
                    if len(raw) > 16384 or not raw.endswith(b"\n"):
                        raise AuditIntegrityError("audit record is incomplete or oversized")
                    try:
                        row = json.loads(raw)
                        signature = row.pop("mac")
                        expected = hmac.new(self._key, _canonical(row), hashlib.sha256).hexdigest()
                        if not isinstance(signature, str) or not hmac.compare_digest(expected, signature):
                            raise ValueError()
                        if row["sequence"] != sequence + 1 or row["previous"] != head:
                            raise ValueError()
                        sequence, head = row["sequence"], signature
                        if sequence == checkpoint["sequence"]:
                            anchored = hmac.compare_digest(head, checkpoint["head"])
                    except (KeyError, ValueError, TypeError) as exc:
                        raise AuditIntegrityError("audit authentication or chain verification failed") from exc
        if sequence < checkpoint["sequence"] or not anchored:
            raise AuditIntegrityError("audit was truncated or restored behind its protected checkpoint")
        status = {"valid": True, "count": sequence, "head": head,
                  "checkpoint_count": checkpoint["sequence"], "uncheckpointed_records": sequence - checkpoint["sequence"]}
        self._verified_stat, self._verified_status, self._verified_checkpoint = current_stat, status, checkpoint
        return status.copy()

    def verify(self):
        with file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            return self._scan()

    @contextmanager
    def batch(self):
        """Group UI reads, retaining every event before the context returns.

        Do not place hardware/key-delivery side effects in this context: they
        require their own durable write-ahead records before the side effect.
        """
        if self._batch_rows.get() is not None:
            yield
            return
        with file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            self._scan(use_cache=True)
        rows = []
        token = self._batch_rows.set(rows)
        try:
            yield
        finally:
            self._batch_rows.reset(token)
            if rows:
                self._append(rows)

    def record(self, identity: Principal | str, operation: str, resource: str, result: str, details=None):
        subject = identity.subject if isinstance(identity, Principal) else identity
        row = {"schema": "orion.security.audit.v1", "time_unix": time.time(),
               "identity": _identifier(subject), "operation": _identifier(operation, maximum=128),
               "resource": _identifier(resource, maximum=512),
               "result": result if result in {"allowed", "denied", "success", "failure", "revoked", "created",
                                              "completed", "failed", "accepted", "rejected"}
               else "redacted", "details": redact_details(details)}
        batch = self._batch_rows.get()
        if batch is not None:
            if len(batch) >= 4096:
                raise AuditIntegrityError("audit batch limit exceeded")
            batch.append(row)
            return {"pending": True, "sequence": None, "mac": None}
        return self._append([row])

    def _append(self, rows):
        with file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            status = self._scan(use_cache=True)
            sequence, head, payloads = status["count"], status["head"], []
            for record in rows:
                row = dict(record)
                row.update(sequence=sequence + 1, previous=head)
                row["mac"] = hmac.new(self._key, _canonical(row), hashlib.sha256).hexdigest()
                sequence, head = row["sequence"], row["mac"]
                payloads.append(_canonical(row) + b"\n")
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(descriptor, "ab") as log:
                log.write(b"".join(payloads))
                log.flush()
                os.fsync(log.fileno())
            checkpoint = {"sequence": sequence, "head": head}
            self.vault.put_bytes(self._checkpoint_name, _canonical(checkpoint))
            self._verified_stat = self._file_stat()
            self._verified_checkpoint = checkpoint
            self._verified_status = {"valid": True, "count": sequence, "head": head,
                                     "checkpoint_count": sequence, "uncheckpointed_records": 0}
            return {"sequence": sequence, "mac": head}
