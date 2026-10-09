"""Share one durable SQLite transaction across a simulated scenario's stores."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
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


def connect(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    active = _connections.get()
    if active and resolved in active:
        return active[resolved]
    connection = sqlite3.connect(str(resolved), timeout=10.0)
    connection.row_factory = sqlite3.Row
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
