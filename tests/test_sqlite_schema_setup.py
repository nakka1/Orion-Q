"""Interrupted database setup must leave no partly committed schema."""
import sqlite3

import pytest

from quantum_autopilot.control_plane.journal import ControlJournal
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore


@pytest.mark.unit
@pytest.mark.parametrize("store_type,denied_table", [(ControlJournal,"safety_events"),(SimulatedTopologyStore,"topology_events")])
def test_schema_setup_rolls_back_if_a_later_table_cannot_be_created(tmp_path, monkeypatch, store_type, denied_table):
    path = tmp_path/"interrupted.sqlite3"
    connect = sqlite3.connect
    def failing_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(lambda action,name,*_rest: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == denied_table else sqlite3.SQLITE_OK)
        return connection
    with monkeypatch.context() as scoped:
        scoped.setattr(sqlite3,"connect",failing_connect)
        with pytest.raises(sqlite3.DatabaseError):
            store_type(path)
    connection = connect(path)
    try:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    finally:
        connection.close()
    store_type(path)
    connection = connect(path)
    try:
        assert denied_table in {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()
