"""Persistent, versioned operator permission for learned network control."""
from contextlib import closing

from ..control_plane.sqlite_transaction import connect

CONTROL_ROLES = ("compensation", "sync", "keyrate", "routing", "allocation")


def read_mode(path, topology_id, default=True):
    with closing(connect(path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS network_ml_control (topology_id TEXT PRIMARY KEY, "
                   "enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), revision INTEGER NOT NULL)")
        db.execute("INSERT OR IGNORE INTO network_ml_control VALUES (?, ?, 0)", (topology_id, int(default)))
        row = db.execute("SELECT enabled, revision FROM network_ml_control WHERE topology_id=?", (topology_id,)).fetchone()
    return bool(row["enabled"]), int(row["revision"])


def write_mode(path, topology_id, enabled, expected_revision):
    with closing(connect(path)) as db, db:
        cursor = db.execute("UPDATE network_ml_control SET enabled=?, revision=revision+1 "
                            "WHERE topology_id=? AND revision=?", (int(enabled), topology_id, expected_revision))
        if cursor.rowcount != 1:
            raise ValueError("ML control changed since confirmation; refresh and confirm again")
    return expected_revision + 1
