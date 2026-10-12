"""Share one durable SQLite transaction across a simulated scenario's stores."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import ctypes
import os
from pathlib import Path
import sqlite3


class _ScenarioConnection(sqlite3.Connection):
    """Nested store contexts borrow this connection; its owner commits/closes."""
    _managed = False

    def __exit__(self, *error):
        if self._managed:
            return False
        return super().__exit__(*error)

    def close(self):
        if not self._managed:
            super().close()


_connections: ContextVar[dict[Path, sqlite3.Connection] | None] = ContextVar(
    "orion_scenario_connections", default=None
)


def _set_full(connection: sqlite3.Connection) -> None:
    """Set durability on each newly owned connection, never a borrowed one."""
    connection.execute("PRAGMA synchronous=FULL")
    if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
        raise RuntimeError("SQLite FULL synchronization could not be verified")


def _wal_version_eligible(version: str) -> bool:
    """SQLite's official WAL-reset fix or a documented fixed backport."""
    try:
        parts = tuple(int(part) for part in version.split("."))
    except (AttributeError, ValueError):
        return False
    if len(parts) != 3 or any(part < 0 for part in parts):
        return False
    return (parts >= (3, 51, 3) or
            parts[:2] == (3, 50) and parts[2] >= 7 or
            parts[:2] == (3, 44) and parts[2] >= 6)


def _wal_storage_status(path: Path) -> dict:
    """Positive local fixed-drive qualification; network/unknown fails closed."""
    if os.name != "nt" or str(path).startswith("\\\\"):
        return {"local_fixed_storage": False, "storage_reason": "non_windows_or_network_path"}
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetDriveTypeW.argtypes = (ctypes.c_wchar_p,)
        kernel.GetDriveTypeW.restype = ctypes.c_uint
        drive_type = int(kernel.GetDriveTypeW(path.anchor))
    except (OSError, AttributeError):
        return {"local_fixed_storage": False, "storage_reason": "storage_probe_unavailable"}
    return {"local_fixed_storage": drive_type == 3, "windows_drive_type": drive_type,
            "storage_reason": "local_fixed_drive" if drive_type == 3 else "non_fixed_drive"}


def open_wal_keeper(path: str | Path) -> tuple[sqlite3.Connection | None, dict]:
    """Keep an initialized WAL pager alive without borrowing a writer.

    Existing modes are preserved when SQLite/local storage cannot be qualified.
    Eligible local Windows databases use FULL and SQLite's normal checkpoints.
    Backup must use SQLite's backup API or preserve a consistent database/WAL
    snapshot. The caller owns the returned handle's explicit/finalized close.
    No transaction or long-running reader is retained by the keeper.
    """
    resolved = Path(path).resolve()
    version_eligible = _wal_version_eligible(sqlite3.sqlite_version)
    metadata = {"requested_journal_mode": "WAL", "effective_journal_mode": None,
                "sqlite_version": sqlite3.sqlite_version, "wal_reset_fix_eligible": version_eligible,
                "local_fixed_storage": False, "keeper_active": False,
                "deferred_to_scenario": False, "synchronous": "FULL"}
    active = _connections.get()
    if active and resolved in active:
        metadata.update(reason="active_scenario_transaction", deferred_to_scenario=True)
        return None, metadata
    if not version_eligible:
        metadata["reason"] = "sqlite_version_not_qualified"
        return None, metadata
    metadata.update(_wal_storage_status(resolved))
    if not metadata["local_fixed_storage"]:
        metadata["reason"] = "storage_not_qualified"
        return None, metadata
    connection = sqlite3.connect(str(resolved), timeout=10.0, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    try:
        _set_full(connection)
        effective = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).upper()
        metadata["effective_journal_mode"] = effective
        if effective != "WAL":
            connection.close()
            metadata["reason"] = "journal_mode_not_wal"
            return None, metadata
        # PRAGMA alone leaves a lazy handle; access/drain the schema so it
        # actually retains the WAL pager when the short-lived writers close.
        connection.execute("SELECT name FROM sqlite_schema").fetchall()
        if connection.in_transaction:
            raise RuntimeError("SQLite keeper unexpectedly retained a transaction")
    except BaseException:
        connection.close()
        raise
    metadata.update(keeper_active=True, reason="wal_keeper_ready")
    return connection, metadata


def connect(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    active = _connections.get()
    if active and resolved in active:
        return active[resolved]
    connection = sqlite3.connect(str(resolved), timeout=10.0)
    connection.row_factory = sqlite3.Row
    try:
        _set_full(connection)
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def transaction(path: str | Path):
    """Commit a complete scenario once; exceptions roll back all its stores.

    SQLite's existing journal mode and FULL synchronous durability stay intact.
    The context is local to the calling thread and resolved database path.
    """
    resolved = Path(path).resolve()
    active = _connections.get() or {}
    if resolved in active:
        raise RuntimeError("a scenario transaction is already active for this database")
    connection = sqlite3.connect(str(resolved), timeout=10.0, factory=_ScenarioConnection)
    connection.row_factory = sqlite3.Row
    try:
        _set_full(connection)
    except BaseException:
        connection.close()
        raise
    connection._managed = True
    token = _connections.set({**active, resolved:connection})
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
    finally:
        _connections.reset(token)
        connection._managed = False
        connection.close()
