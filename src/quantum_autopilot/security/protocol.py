"""Authenticated, session-bound classical QKD messages with durable replay state.

HMAC-SHA256 provides computational authentication, NOT information theoretic
authentication or a composable QKD proof. Authentication material comes from a
separate vault reference, never from the unqualified observation/key fixture.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3


class ProtocolAuthenticationError(ValueError):
    """Malformed, altered, replayed, cross-session or unexpected message."""


def _canonical(value):
    try:
        data = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError) as error:
        raise ProtocolAuthenticationError("invalid classical payload schema") from error
    if len(data) > 8 * 1024 * 1024:
        raise ProtocolAuthenticationError("classical message exceeds 8 MiB")
    return data


def _hex_digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


@dataclass(frozen=True)
class ClassicalEnvelope:
    session_id: str
    sender: str
    receiver: str
    sequence: int
    operation: str
    transcript_binding: str
    previous_sha256: str
    payload: dict = field(repr=False)
    key_reference: str
    authentication_tag: str = field(repr=False)
    schema: str = "orion.security.qkd-classical.v1"

    def signed_fields(self):
        value = asdict(self)
        value.pop("authentication_tag")
        return value

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ProtocolAuthenticationError("classical envelope fields invalid")
        try:
            return cls(**value)
        except TypeError as error:
            raise ProtocolAuthenticationError("classical envelope schema invalid") from error


class AuthenticatedClassicalChannel:
    operations = frozenset({"observation", "basis_sifting", "parameter_estimation",
                            "reconciliation", "verification", "privacy_amplification", "abort"})

    def __init__(self, path: Path | str, *, session_id: str, sender: str, receiver: str,
                 transcript_binding: str, key_reference: str, vault,
                 origin: str, mode: str, runtime=None, principal=None):
        if any(not isinstance(v, str) or not v or len(v) > 256 or any(ord(c) < 32 for c in v)
               for v in (session_id, sender, receiver, key_reference)) or sender == receiver:
            raise ProtocolAuthenticationError("distinct authenticated endpoints and session required")
        if not _hex_digest(transcript_binding):
            raise ProtocolAuthenticationError("preparation/analysis/clock transcript binding required")
        if origin not in {"simulated", "physical"} or mode not in {"emulation", "hardware", "replay"} or (
                mode == "hardware" and origin != "physical") or (mode == "emulation" and origin != "simulated"):
            raise ProtocolAuthenticationError("classical provenance inconsistent")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as initialization:
            initialization.execute("PRAGMA journal_mode=WAL")
            initialization.execute("PRAGMA synchronous=FULL")
        self.session_id, self.sender, self.receiver = session_id, sender, receiver
        self.transcript_binding, self.key_reference, self.vault = transcript_binding, key_reference, vault
        self.origin, self.mode, self.runtime, self.principal = origin, mode, runtime, principal
        self._processing_transaction = ContextVar("classical_processing_" + str(id(self)), default=None)
        self._checkpoint_ready = False
        self._checkpoint_supported = all(callable(getattr(vault, name, None)) for name in ("put_bytes", "contains"))
        self._checkpoint_reference = "qkd-transcript:" + hashlib.sha256(
            _canonical([session_id, sender, receiver])).hexdigest()
        self._authorize()
        # get_bytes never provisions a key implicitly: peers must be provisioned
        # independently by an authorized administrator/service.
        self._key()
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS classical_sessions (
                session TEXT NOT NULL, sender TEXT NOT NULL, receiver TEXT NOT NULL,
                binding TEXT NOT NULL, key_reference TEXT NOT NULL, origin TEXT NOT NULL,
                mode TEXT NOT NULL, direction TEXT NOT NULL, next_sequence INTEGER NOT NULL,
                previous_sha256 TEXT NOT NULL, PRIMARY KEY(session,sender,receiver,direction))""")
            for direction in ("outbound", "inbound"):
                existing = db.execute("SELECT binding,key_reference,origin,mode FROM classical_sessions WHERE session=? AND sender=? AND receiver=? AND direction=?",
                            (session_id, sender, receiver, direction)).fetchone()
                expected = (transcript_binding, key_reference, origin, mode)
                if existing is not None and existing != expected:
                    raise ProtocolAuthenticationError("existing session binding cannot be changed")
                db.execute("INSERT OR IGNORE INTO classical_sessions VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (session_id, sender, receiver, *expected, direction, 0, "0" * 64))
        self._checkpoint_ready = True
        if self._checkpoint_supported:
            # Provision only a genuinely empty session. Existing nonempty
            # journals without protected evidence are conservatively refused.
            with self._db():
                pass

    def _authorize(self):
        if self.runtime is not None:
            self.runtime.authorize("protocol.process", "protocol:" + self.session_id, self.principal)

    def _key(self):
        value = self.vault.get_bytes(self.key_reference)
        if not isinstance(value, bytes) or len(value) < 32:
            raise ProtocolAuthenticationError("classical authentication key must contain at least 256 bits")
        return value

    @contextmanager
    def _db(self):
        active = self._processing_transaction.get()
        if active is not None:
            yield active
            return
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            before = self._verify_checkpoint(db) if self._checkpoint_ready and self._checkpoint_supported else None
            yield db
            if self._checkpoint_ready and self._checkpoint_supported:
                after = self._snapshot(db)
                if before != after:
                    # Checkpoint first; a crash before SQL commit leaves an
                    # uncertainty mismatch, never permits replay after restore.
                    self.vault.put_bytes(self._checkpoint_reference, _canonical(after))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _snapshot(self, db):
        rows = db.execute("SELECT direction,next_sequence,previous_sha256,binding,key_reference,origin,mode FROM classical_sessions WHERE session=? AND sender=? AND receiver=? ORDER BY direction",
                          (self.session_id, self.sender, self.receiver)).fetchall()
        return {"schema": "orion.security.classical-checkpoint.v1", "rows": [list(row) for row in rows]}

    def _verify_checkpoint(self, db):
        current = self._snapshot(db)
        if not self.vault.contains(self._checkpoint_reference):
            if len(current["rows"]) != 2 or any(row[1] != 0 or row[2] != "0" * 64 for row in current["rows"]):
                raise ProtocolAuthenticationError("classical protected checkpoint missing; session quarantined")
            return None
        try:
            anchored = json.loads(self.vault.get_bytes(self._checkpoint_reference))
        except (ValueError, TypeError) as error:
            raise ProtocolAuthenticationError("classical protected checkpoint invalid; session quarantined") from error
        if _canonical(anchored) != _canonical(current):
            raise ProtocolAuthenticationError("classical journal restored/uncertain; session quarantined, create a new session")
        return current

    @contextmanager
    def atomic_processing(self):
        """Commit a LOCAL post-processing block before any result is released.

        No transport, equipment action, material admission or key delivery may
        occur in this context. External send/receive retains a commit per message.
        Physical observations and fixtures use the same local block boundary.
        """
        self._authorize()
        if self._processing_transaction.get() is not None:
            raise ProtocolAuthenticationError("nested classical processing transaction prohibited")
        with self._db() as db:
            token = self._processing_transaction.set(db)
            try:
                yield
            finally:
                self._processing_transaction.reset(token)

    def _state(self, db, direction):
        return db.execute("SELECT next_sequence,previous_sha256 FROM classical_sessions WHERE session=? AND sender=? AND receiver=? AND direction=?",
                          (self.session_id, self.sender, self.receiver, direction)).fetchone()

    def send(self, operation: str, payload: dict):
        self._authorize()
        if operation not in self.operations or not isinstance(payload, dict):
            raise ProtocolAuthenticationError("unsupported classical operation/payload")
        # Validate size/types before advancing durable state.
        _canonical(payload)
        with self._db() as db:
            sequence, previous = self._state(db, "outbound")
            unsigned = ClassicalEnvelope(self.session_id, self.sender, self.receiver, sequence, operation,
                         self.transcript_binding, previous, payload, self.key_reference, "")
            tag = hmac.new(self._key(), _canonical(unsigned.signed_fields()), hashlib.sha256).hexdigest()
            envelope = ClassicalEnvelope(**{**unsigned.to_dict(), "authentication_tag": tag})
            sha = hashlib.sha256(_canonical(envelope.to_dict())).hexdigest()
            db.execute("UPDATE classical_sessions SET next_sequence=?,previous_sha256=? WHERE session=? AND sender=? AND receiver=? AND direction='outbound'",
                       (sequence + 1, sha, self.session_id, self.sender, self.receiver))
        return envelope

    def receive(self, envelope: ClassicalEnvelope | dict, *, expected_operation: str, expected_payload: dict | None = None):
        try:
            result = self._receive(envelope, expected_operation=expected_operation, expected_payload=expected_payload)
        except ProtocolAuthenticationError:
            self._audit(expected_operation, "rejected")
            raise
        self._audit(expected_operation, "accepted")
        return result

    def _audit(self, operation, outcome):
        if self.runtime is not None:
            identity = self.principal if self.principal is not None else self.runtime.current_identity()
            self.runtime.audit.record(identity, "protocol.process", "protocol:" + self.session_id,
                                      "allowed" if outcome == "accepted" else "denied",
                                      {"stage": operation if operation in self.operations else "unknown",
                                       "classical_message_accepted": outcome == "accepted"})

    def _receive(self, envelope: ClassicalEnvelope | dict, *, expected_operation: str, expected_payload: dict | None = None):
        self._authorize()
        if isinstance(envelope, dict):
            envelope = ClassicalEnvelope.from_dict(envelope)
        if not isinstance(envelope, ClassicalEnvelope):
            raise ProtocolAuthenticationError("classical envelope required")
        fields = envelope.signed_fields()
        if (envelope.schema != "orion.security.qkd-classical.v1" or type(envelope.sequence) is not int
            or envelope.sequence < 0 or not isinstance(envelope.payload, dict)
            or not _hex_digest(envelope.authentication_tag) or not _hex_digest(envelope.previous_sha256)
            or (envelope.session_id, envelope.sender, envelope.receiver, envelope.transcript_binding, envelope.key_reference)
               != (self.session_id, self.sender, self.receiver, self.transcript_binding, self.key_reference)
            or envelope.operation != expected_operation or envelope.operation not in self.operations):
            raise ProtocolAuthenticationError("classical identity/session/operation binding invalid")
        expected = hmac.new(self._key(), _canonical(fields), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(envelope.authentication_tag, expected):
            raise ProtocolAuthenticationError("classical authentication failed")
        if expected_payload is not None and _canonical(envelope.payload) != _canonical(expected_payload):
            raise ProtocolAuthenticationError("classical payload does not match actual protocol observations")
        sha = hashlib.sha256(_canonical(envelope.to_dict())).hexdigest()
        with self._db() as db:
            sequence, previous = self._state(db, "inbound")
            if envelope.sequence != sequence or envelope.previous_sha256 != previous:
                raise ProtocolAuthenticationError("classical replay, gap or out-of-order transcript")
            db.execute("UPDATE classical_sessions SET next_sequence=?,previous_sha256=? WHERE session=? AND sender=? AND receiver=? AND direction='inbound'",
                       (sequence + 1, sha, self.session_id, self.sender, self.receiver))
        return envelope.payload

    def exchange(self, operation: str, payload: dict):
        """Local transport harness, identical sender/receiver validation contracts."""
        return self.receive(self.send(operation, payload), expected_operation=operation, expected_payload=payload)

    def summary(self):
        self._authorize()
        with self._db() as db:
            count, last_sha = self._state(db, "inbound")
        return {"schema": "orion.security.classical-authentication.v1", "session_id": self.session_id,
                "sender": self.sender, "receiver": self.receiver, "origin": self.origin, "mode": self.mode,
                "transcript_binding": self.transcript_binding, "accepted_messages": count,
                "authentication_tag_bits": 256, "authentication_transmitted_bits": 256 * count,
                "qkd_authentication_key_consumed_bits": 0,
                "authentication_model": "computational HMAC-SHA256 with separately provisioned key",
                "information_theoretic_authentication": False, "composable_failure_bound": None,
                "last_authenticated_sha256": last_sha, "hardware_qualification": False,
                "backup_rollback_protected": self._checkpoint_supported}


def require_operational_qkd_material(block, *, purpose="test"):
    """No fixture, central estimator or unqualified source can authorize real keys."""
    if purpose not in {"test", "operational"}:
        raise ProtocolAuthenticationError("unknown QKD material purpose")
    if purpose == "operational":
        # Even authenticated physical records need independent experimental
        # source/detector/security qualification not available in this build.
        raise ProtocolAuthenticationError("QKD material is unqualified for operational use")
