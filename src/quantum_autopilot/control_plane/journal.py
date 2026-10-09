"""Small append-only SQLite journal for sandbox control cycles and safety events."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from quantum_autopilot.control_plane.contracts import ControlCycleRecord
from quantum_autopilot.control_plane.sqlite_transaction import connect


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ControlJournal:
    """Persist immutable cycle records and safety events; enforce cycle
    idempotency. Safety events (interlock changes, authorization lifecycle)
    are a separate append-only table — they have no request_id to de-dupe
    against, and are audit trail rather than control decisions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with closing(self._connect()) as connection, connection:
            # DDL otherwise commits each table separately on a new database.
            if not connection.in_transaction:
                connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS control_cycles (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_id TEXT NOT NULL UNIQUE,
                    request_id TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    completed_at_utc TEXT NOT NULL,
                    record_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS safety_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_sha256 TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    at_utc TEXT NOT NULL,
                    detail_json TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return connect(self.path)

    # ------------------------------------------------------------ cycles
    def get_by_request_id(self, request_id: str) -> dict[str, Any] | None:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT request_sha256, record_sha256, record_json
                FROM control_cycles WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "request_sha256": row["request_sha256"],
            "record_sha256": row["record_sha256"],
            "record": json.loads(row["record_json"]),
        }

    def append(self, record: ControlCycleRecord) -> str:
        payload = record.to_dict()
        serialized = _canonical_json(payload)
        record_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO control_cycles (
                    cycle_id, request_id, request_sha256, record_sha256,
                    mode, action, status, completed_at_utc, record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.cycle_id,
                    record.snapshot.request_id,
                    record.request_sha256,
                    record_hash,
                    record.mode.value,
                    record.safety.effective_action.value,
                    record.execution.status,
                    record.completed_at_utc,
                    serialized,
                ),
            )
        return record_hash

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer in [1, 100]")
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT sequence, request_id, record_sha256, mode, action,
                       status, completed_at_utc, json_extract(record_json, '$.snapshot.node_id') AS node_id
                FROM control_cycles ORDER BY sequence DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def is_authorization_revoked(self, authorization_id: str) -> bool:
        with self._lock, closing(self._connect()) as connection:
            return connection.execute(
                "SELECT 1 FROM safety_events WHERE kind = ? "
                "AND json_extract(detail_json, '$.authorization_id') = ? LIMIT 1",
                ("physical_authorization_revoked", authorization_id),
            ).fetchone() is not None

    # ------------------------------------------------------------- safety
    def append_safety_event(self, kind: str, actor: str, at_utc: str, detail: Mapping[str, Any]) -> str:
        """Record one interlock or authorization lifecycle fact. Hashed over
        its own canonical JSON, same discipline as a control cycle — an
        auditor should be able to verify this table was not edited either."""

        if not kind or not kind.strip():
            raise ValueError("kind cannot be empty")
        if not actor or not actor.strip():
            raise ValueError("actor cannot be empty")
        payload = {"kind": kind, "actor": actor, "at_utc": at_utc, "detail": dict(detail)}
        serialized = _canonical_json(payload)
        event_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO safety_events (event_sha256, kind, actor, at_utc, detail_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_hash, kind, actor, at_utc, _canonical_json(dict(detail))),
            )
        return event_hash

    def list_recent_safety_events(self, limit: int = 20) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer in [1, 200]")
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT sequence, event_sha256, kind, actor, at_utc, detail_json
                FROM safety_events ORDER BY sequence DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "event_sha256": row["event_sha256"],
                "kind": row["kind"],
                "actor": row["actor"],
                "at_utc": row["at_utc"],
                "detail": json.loads(row["detail_json"]),
            }
            for row in rows
        ]


__all__ = ["ControlJournal"]
