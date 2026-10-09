"""Fail-closed laboratory key ledger. Test material only; no certified key vault.

A reservation is irrevocable: an uncertain delivery burns its keys. HTTP retries
with the same delivery token return status, never material. SQLite transactions
serialize assignment across processes; secrets are encrypted at rest using an
operator-supplied local wrapping key (not a substitute for qualified hardware).
"""
from __future__ import annotations

from contextlib import contextmanager
import base64
import json
import hmac
import hashlib
import os
from pathlib import Path
import sqlite3
import time
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .kms import KeyDeliveryError
from quantum_autopilot.security.key_lifecycle import ConsumptionJournal


def validate_enc_request(request):
    if not isinstance(request, dict) or set(request) - {"number", "size"}:
        raise KeyDeliveryError(400, "unsupported key request fields")
    number, size = request.get("number", 1), request.get("size", 256)
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 64:
        raise KeyDeliveryError(400, "number requires an integer from 1 to 64")
    if isinstance(size, bool) or not isinstance(size, int) or size != 256:
        raise KeyDeliveryError(400, "laboratory supports 256-bit keys only")
    return number


def validate_key_ids(request):
    if not isinstance(request, dict) or set(request) != {"key_IDs"}:
        raise KeyDeliveryError(400, "key_IDs request required")
    rows = request["key_IDs"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 64:
        raise KeyDeliveryError(400, "one to 64 key_ID objects required")
    identifiers = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"key_ID"} or not isinstance(row["key_ID"], str):
            raise KeyDeliveryError(400, "invalid key_ID object")
        try:
            UUID(row["key_ID"])
        except ValueError as exc:
            raise KeyDeliveryError(400, "invalid UUID key_ID") from exc
        identifiers.append(row["key_ID"])
    if len(set(identifiers)) != len(identifiers):
        raise KeyDeliveryError(400, "duplicate key_ID")
    return identifiers


def validate_delivery_token(token):
    if not isinstance(token, str) or not 1 <= len(token) <= 128 or not token.isascii():
        raise KeyDeliveryError(400, "delivery token must be an ASCII identifier of at most 128 characters")
    if not all(character.isalnum() or character in "-_" for character in token):
        raise KeyDeliveryError(400, "invalid delivery token")
    return token


class KeyLedger:
    schema = "orion.lab.key-ledger.v1"

    def __init__(self, path, wrapping_key: bytes, *, identity, experiment_id, model_id,
                 security_runtime=None, journal_path=None, usage="test"):
        if usage != "test":
            raise ValueError("emulated laboratory material is forbidden for operational key usage")
        if identity not in {"kme-alice", "kme-bob"}:
            raise ValueError("invalid laboratory KME identity")
        if not isinstance(wrapping_key, bytes) or len(wrapping_key) != 32:
            raise ValueError("32-byte local wrapping key required")
        if not experiment_id or not model_id:
            raise ValueError("identified material source required")
        self.path = Path(path)
        self.identity = identity
        self.experiment_id, self.model_id = experiment_id, model_id
        self.security_runtime, self.usage = security_runtime, usage
        self._cipher = AESGCM(wrapping_key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS keys (key_id TEXT PRIMARY KEY, sealed BLOB,
                    state TEXT NOT NULL, delivery_token TEXT, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries (token TEXT PRIMARY KEY, state TEXT NOT NULL,
                    key_ids TEXT NOT NULL, direction TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL, key_count INTEGER NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS invalidations (token TEXT PRIMARY KEY, key_ids TEXT NOT NULL,
                    acknowledged INTEGER NOT NULL DEFAULT 0);
            """)
            db.execute("BEGIN IMMEDIATE")
            expected = {"identity": identity, "experiment_id": experiment_id, "model_id": model_id,
                        "schema": self.schema}
            actual = dict(db.execute("SELECT name,value FROM metadata"))
            check = actual.pop("wrapping_key_check", None)
            if actual and actual != expected:
                raise ValueError("key ledger provenance or endpoint mismatch")
            marker = (self.schema + ":" + identity).encode("ascii")
            if check is not None:
                try:
                    sealed = base64.b64decode(check, validate=True)
                    if self._cipher.decrypt(sealed[:12], sealed[12:], identity.encode("ascii")) != marker:
                        raise ValueError()
                except Exception as error:
                    raise ValueError("key ledger wrapping credential rejected") from error
            else:
                # Older laboratory ledgers can migrate only with a credential
                # capable of decrypting their still-present test material.
                existing = db.execute("SELECT key_id,sealed FROM keys WHERE sealed IS NOT NULL LIMIT 1").fetchone()
                if existing:
                    try:
                        sealed = existing[1]
                        self._cipher.decrypt(sealed[:12], sealed[12:], existing[0].encode("ascii"))
                    except Exception as error:
                        raise ValueError("key ledger wrapping credential rejected") from error
                nonce = os.urandom(12)
                check = base64.b64encode(nonce + self._cipher.encrypt(nonce, marker, identity.encode("ascii"))).decode("ascii")
                db.execute("INSERT INTO metadata VALUES ('wrapping_key_check',?)", (check,))
            for name, value in expected.items():
                db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (name, value))
            db.commit()
        journal_key = (security_runtime.vault.get_or_create_bytes("key-delivery-journal-v1", 32)
                       if security_runtime is not None else
                       hmac.digest(wrapping_key, b"orion-key-consumption-journal-test-v1", "sha256"))
        default_journal = (security_runtime.root / "key-recovery" /
            (hashlib.sha256(str(self.path.resolve()).encode()).hexdigest() + ".jsonl") if security_runtime is not None
            else self.path.parent / ".key-recovery" / (self.path.name + ".jsonl"))
        selected_journal = Path(journal_path or default_journal).resolve()
        if (security_runtime is not None and security_runtime.profile == "operational" and
                (self.path.parent.resolve() == selected_journal.parent or self.path.parent.resolve() in selected_journal.parents)):
            raise ValueError("operational consumption checkpoint must be outside the key-database backup directory")
        self.journal = ConsumptionJournal(selected_journal, journal_key,
            vault=security_runtime.vault if security_runtime is not None else None,
            operational=security_runtime is not None and security_runtime.profile == "operational")
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS expiration (key_id TEXT PRIMARY KEY, expires REAL NOT NULL)")
            if not self.journal.snapshot():
                # Upgrade an existing ledger by importing irreversible history,
                # before any future backup can restore old available rows.
                legacy = db.execute("SELECT key_id,state,delivery_token FROM keys WHERE state!='available'").fetchall()
                if legacy:
                    self._authorize("keys.invalidate")
                    for key_id, state, token in legacy:
                        journal_state = state if state in self.journal.states else "invalidated"
                        self.journal.append([key_id], journal_state, token or "legacy-history-" + key_id)
                    self.event(db, "legacy_consumption_history_authenticated", len(legacy))

    def _authorize(self, action):
        if self.security_runtime is not None:
            self.security_runtime.authorize(action, "keys:" + self.identity)

    def _reconcile(self, db):
        """A restored available row may never override the independent journal."""
        latest = self.journal.latest()
        for key_id, record in latest.items():
            row = db.execute("SELECT state,delivery_token FROM keys WHERE key_id=?", (key_id,)).fetchone()
            terminal = {"issued", "consumed", "uncertain", "invalidated", "expired"}
            rolled_back = row and (row[0] == "available" or
                (record["state"] in terminal and row[0] in {"reserved", "authorized"}) or
                (record["state"] == "authorized" and row[0] == "reserved"))
            if rolled_back:
                token = row[1] or record["token"]
                db.execute("UPDATE keys SET state='invalidated',sealed=NULL,delivery_token=?,updated=? WHERE key_id=?",
                           (token, time.time(), key_id))
                old = db.execute("SELECT key_ids FROM deliveries WHERE token=?", (token,)).fetchone()
                ids = list(dict.fromkeys((json.loads(old[0]) if old else []) + [key_id]))
                db.execute("INSERT OR REPLACE INTO deliveries VALUES (?,'invalidated',?,'recovery',?)",
                           (token, json.dumps(ids), time.time()))
                if record["token"] != token:
                    db.execute("INSERT OR REPLACE INTO deliveries VALUES (?,'invalidated',?,'recovery',?)",
                               (record["token"], json.dumps(ids), time.time()))
                if self.identity == "kme-alice":
                    db.execute("INSERT OR REPLACE INTO invalidations(token,key_ids,acknowledged) VALUES (?,?,0)",
                               (token, json.dumps(ids)))
                self.event(db, "backup_rollback_material_invalidated", 1)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._reconcile(db)
                yield db
            except BaseException:
                db.rollback()
                raise
            else:
                db.commit()

    @staticmethod
    def event(db, kind, count):
        db.execute("INSERT INTO events(kind,key_count,created) VALUES (?,?,?)", (kind, count, time.time()))

    def provision(self, pairs, *, expires_at=None):
        """Import identified paired TEST material; never replay an already-used ID."""
        rows = list(pairs)
        self._authorize("keys.provision")
        if expires_at is not None and (isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)) or
                not time.time() < expires_at < time.time() + 365 * 86400):
            raise ValueError("expiry must be a finite future UTC timestamp within one year")
        if len({key_id for key_id, _ in rows}) != len(rows):
            raise ValueError("duplicate provisioning identifiers")
        for key_id, material in rows:
            UUID(key_id)
            if not isinstance(material, bytes) or len(material) != 32:
                raise ValueError("complete 256-bit test key required")
        with self.transaction() as db:
            used = self.journal.latest()
            if any(key_id in used or db.execute("SELECT 1 FROM keys WHERE key_id=?", (key_id,)).fetchone()
                   for key_id, _ in rows):
                raise ValueError("provisioning identifier already exists; material cannot be reused")
            for key_id, material in rows:
                nonce = os.urandom(12)
                sealed = nonce + self._cipher.encrypt(nonce, material, key_id.encode("ascii"))
                db.execute("INSERT INTO keys VALUES (?,?, 'available',NULL,?)", (key_id, sealed, time.time()))
                if expires_at is not None:
                    db.execute("INSERT INTO expiration VALUES (?,?)", (key_id, expires_at))
            self.event(db, "provisioned_identified_test_material", len(rows))

    def reserve(self, request, token):
        self._authorize("keys.consume")
        self.expire()
        number = validate_enc_request(request)
        token = validate_delivery_token(token)
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM deliveries WHERE token=?", (token,)).fetchone():
                raise KeyDeliveryError(409, "delivery token already recorded; inspect status without retrying material")
            rows = db.execute("SELECT key_id FROM keys WHERE state='available' ORDER BY rowid LIMIT ?",
                              (number,)).fetchall()
            if len(rows) != number:
                raise KeyDeliveryError(503, "insufficient key buffer")
            ids = [row[0] for row in rows]
            self.journal.append(ids, "reserved", token)
            db.executemany("UPDATE keys SET state='reserved',delivery_token=?,updated=? WHERE key_id=?",
                           [(token, time.time(), key_id) for key_id in ids])
            db.execute("INSERT INTO deliveries VALUES (?,'reserved',?,'enc',?)", (token, json.dumps(ids), time.time()))
            self.event(db, "reserved", number)
        return ids

    def reserve_peer(self, ids, token):
        self._authorize("keys.consume")
        self.expire()
        ids = validate_key_ids({"key_IDs": [{"key_ID": key_id} for key_id in ids]})
        token = validate_delivery_token(token)
        with self.transaction() as db:
            recorded = db.execute("SELECT state,key_ids FROM deliveries WHERE token=?", (token,)).fetchone()
            if recorded:
                if recorded[0] in {"reserved", "authorized"} and json.loads(recorded[1]) == ids:
                    return
                raise KeyDeliveryError(409, "peer delivery binding already terminal or mismatched")
            if any(not db.execute("SELECT 1 FROM keys WHERE key_id=? AND state='available'", (key_id,)).fetchone()
                   for key_id in ids):
                raise KeyDeliveryError(409, "peer keys unavailable")
            self.journal.append(ids, "reserved", token)
            db.executemany("UPDATE keys SET state='reserved',delivery_token=?,updated=? WHERE key_id=?",
                           [(token, time.time(), key_id) for key_id in ids])
            db.execute("INSERT INTO deliveries VALUES (?,'reserved',?,'peer',?)", (token, json.dumps(ids), time.time()))
            self.event(db, "peer_reserved", len(ids))

    def authorize_peer(self, ids, token):
        self._authorize("keys.consume")
        with self.transaction() as db:
            recorded = db.execute("SELECT state,key_ids FROM deliveries WHERE token=?", (token,)).fetchone()
            if not recorded or json.loads(recorded[1]) != ids or recorded[0] not in {"reserved", "authorized"}:
                raise KeyDeliveryError(409, "peer reservation not valid")
            db.execute("UPDATE keys SET state='authorized',updated=? WHERE delivery_token=? AND state='reserved'",
                       (time.time(), token))
            db.execute("UPDATE deliveries SET state='authorized' WHERE token=?", (token,))
            self.event(db, "peer_authorized", len(ids))

    def commit_issue(self, token):
        """Persist consumption BEFORE sending any byte to the application."""
        with self.transaction() as db:
            self._authorize("keys.consume")
            recorded = db.execute("SELECT state,key_ids FROM deliveries WHERE token=?", (token,)).fetchone()
            if not recorded or recorded[0] != "reserved":
                raise KeyDeliveryError(409, "reservation cannot be issued")
            ids = json.loads(recorded[1])
            pairs = self._read_material(db, ids, "reserved", token)
            self.journal.append(ids, "issued", token)
            db.execute("UPDATE keys SET state='issued',sealed=NULL,updated=? WHERE delivery_token=?",
                       (time.time(), token))
            db.execute("UPDATE deliveries SET state='issued' WHERE token=?", (token,))
            self.event(db, "committed_before_http_response", len(ids))
        return self.container(pairs)

    def consume(self, request, token):
        self._authorize("keys.consume")
        self.expire()
        ids = validate_key_ids(request)
        token = validate_delivery_token(token)
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM deliveries WHERE token=?", (token,)).fetchone():
                raise KeyDeliveryError(409, "delivery token already used")
            pairs = self._read_material(db, ids, "authorized")
            self.journal.append(ids, "consumed", token)
            db.executemany("UPDATE keys SET state='consumed',sealed=NULL,updated=? WHERE key_id=?",
                           [(time.time(), key_id) for key_id in ids])
            db.execute("INSERT INTO deliveries VALUES (?,'consumed',?,'dec',?)", (token, json.dumps(ids), time.time()))
            self.event(db, "peer_consumed_before_http_response", len(ids))
        return self.container(pairs)

    def response_sent(self, token):
        """A successful socket write is not an application acknowledgement."""
        with self.transaction() as db:
            db.execute("UPDATE deliveries SET state='response_sent_unconfirmed' WHERE token=? AND state='issued'",
                       (token,))
            self.event(db, "http_response_sent_application_receipt_unconfirmed", 0)

    def _read_material(self, db, ids, state, token=None):
        pairs = []
        for key_id in ids:
            row = db.execute("SELECT sealed,state,delivery_token FROM keys WHERE key_id=?", (key_id,)).fetchone()
            if not row or row[1] != state or not row[0] or (token is not None and row[2] != token):
                raise KeyDeliveryError(400, "one or more requested keys are unavailable")
            sealed = row[0]
            pairs.append((key_id, self._cipher.decrypt(sealed[:12], sealed[12:], key_id.encode("ascii"))))
        return pairs

    @staticmethod
    def container(pairs):
        return {"keys": [{"key_ID": key_id, "key": base64.b64encode(material).decode("ascii")}
                         for key_id, material in pairs]}

    def invalidate(self, token, ids=None, *, reconcile_peer=False):
        self._authorize("keys.invalidate")
        token = validate_delivery_token(token)
        with self.transaction() as db:
            recorded = db.execute("SELECT key_ids FROM deliveries WHERE token=?", (token,)).fetchone()
            if ids is None:
                ids = json.loads(recorded[0]) if recorded else []
            else:
                ids = validate_key_ids({"key_IDs": [{"key_ID": key_id} for key_id in ids]})
                if recorded and json.loads(recorded[0]) != ids:
                    raise KeyDeliveryError(409, "invalidation reservation mismatch")
            # Terminal material never returns to available, including late peer messages.
            for key_id in ids:
                row = db.execute("SELECT delivery_token,state FROM keys WHERE key_id=?", (key_id,)).fetchone()
                if row and row[0] not in {None, token}:
                    raise KeyDeliveryError(409, "invalidation belongs to a different delivery")
            if ids:
                self.journal.append(ids, "invalidated", token)
            for key_id in ids:
                row = db.execute("SELECT delivery_token,state FROM keys WHERE key_id=?", (key_id,)).fetchone()
                if row and row[1] != "consumed":
                    db.execute("UPDATE keys SET state='invalidated',sealed=NULL,delivery_token=?,updated=? WHERE key_id=?",
                               (token, time.time(), key_id))
            if recorded:
                db.execute("UPDATE deliveries SET state='invalidated' WHERE token=?", (token,))
            elif ids:
                db.execute("INSERT INTO deliveries VALUES (?,'invalidated',?,'peer',?)", (token, json.dumps(ids), time.time()))
            if reconcile_peer and ids:
                db.execute("INSERT OR IGNORE INTO invalidations(token,key_ids) VALUES (?,?)", (token, json.dumps(ids)))
            self.event(db, "invalidated_uncertain_delivery", len(ids))

    def recover(self):
        """Called once by an exclusive owner on restart, not on each HTTP request."""
        self._authorize("keys.invalidate")
        with self.connection() as db:
            reservations = db.execute("SELECT token,key_ids FROM deliveries WHERE state IN ('reserved','issued')").fetchall()
        for token, serialized in reservations:
            self.invalidate(token, json.loads(serialized), reconcile_peer=self.identity == "kme-alice")
        return len(reservations)

    def expire(self, *, now=None):
        """Expiry is terminal and journalled before discarding encrypted bytes."""
        if now is not None:
            self._authorize("keys.invalidate")
        now = time.time() if now is None else now
        with self.transaction() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='expiration'").fetchone()
            if not exists:
                return 0
            ids = [row[0] for row in db.execute("SELECT k.key_id FROM keys k JOIN expiration e USING(key_id) "
                "WHERE e.expires<=? AND k.state IN ('available','reserved','authorized')", (now,))]
            if ids:
                self.journal.append(ids, "expired", "expired-" + str(int(now)))
                db.executemany("UPDATE keys SET state='expired',sealed=NULL,updated=? WHERE key_id=?",
                               [(now, key_id) for key_id in ids])
                self.event(db, "expired", len(ids))
            return len(ids)

    def pending_invalidations(self):
        self._authorize("keys.invalidate")
        with self.connection() as db:
            return [(token, json.loads(ids)) for token, ids in
                    db.execute("SELECT token,key_ids FROM invalidations WHERE acknowledged=0")]

    def acknowledge_invalidation(self, token):
        self._authorize("keys.invalidate")
        with self.transaction() as db:
            db.execute("UPDATE invalidations SET acknowledged=1 WHERE token=?", (token,))

    def delivery_status(self, token):
        self._authorize("keys.status")
        self.journal.snapshot()
        token = validate_delivery_token(token)
        with self.connection() as db:
            row = db.execute("SELECT state,key_ids FROM deliveries WHERE token=?", (token,)).fetchone()
        if not row:
            raise KeyDeliveryError(404, "delivery token unknown")
        return {"state": row[0], "key_count": len(json.loads(row[1])), "material_recoverable": False,
                "retry_material_allowed": False}

    def summary(self):
        self._authorize("keys.status")
        integrity = self.journal.evidence()
        with self.connection() as db:
            counts = dict(db.execute("SELECT state,COUNT(*) FROM keys GROUP BY state"))
            events = [{"kind": kind, "key_count": count} for kind, count in
                      db.execute("SELECT kind,key_count FROM events ORDER BY seq")]
            pending = db.execute("SELECT COUNT(*) FROM invalidations WHERE acknowledged=0").fetchone()[0]
        return {"schema": self.schema, "identity": self.identity, "experiment_id": self.experiment_id,
                "model_id": self.model_id, "test_material_only": True, "certified_secret_bits": 0,
                "counts": counts, "events": events, "pending_peer_invalidations": pending,
                "capacity": sum(counts.values()), "consumption_journal": integrity}
