"""FULL-owned connections and guarded WAL keepers on isolated SQLite files."""
from contextlib import closing
import sqlite3
from threading import Thread

import pytest

from quantum_autopilot.control_plane import sqlite_transaction as storage

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("version,expected", (
    ("3.53.1", True), ("3.51.3", True), ("3.51.2", False), ("3.50.7", True),
    ("3.50.6", False), ("3.44.6", True), ("3.44.5", False), ("3.49.999", False),
    ("3.51.3-custom", False), ("3.51", False), (None, False), ("-3.51.3", False),
))
def test_only_known_fixed_wal_releases_eligible(version, expected):
    assert storage._wal_version_eligible(version) is expected


def _eligible(monkeypatch):
    monkeypatch.setattr(storage.sqlite3, "sqlite_version", "3.53.1")
    monkeypatch.setattr(storage, "_wal_storage_status", lambda _: {
        "local_fixed_storage": True, "storage_reason": "explicit test fixture"})


def test_unpatched_version_does_not_open_or_mutate_database(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.sqlite3, "sqlite_version", "3.51.2")
    monkeypatch.setattr(storage.sqlite3, "connect", lambda *args, **kwargs:
                        pytest.fail("ineligible keeper attempted SQLite open"))
    path = tmp_path / "unopened.sqlite"
    keeper, status = storage.open_wal_keeper(path)
    assert keeper is None
    assert status["reason"] == "sqlite_version_not_qualified"
    assert status["deferred_to_scenario"] is False
    assert not path.exists()


def test_unqualified_storage_does_not_open_or_mutate_database(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.sqlite3, "sqlite_version", "3.53.1")
    monkeypatch.setattr(storage, "_wal_storage_status", lambda _: {
        "local_fixed_storage": False, "storage_reason": "network test double"})
    monkeypatch.setattr(storage.sqlite3, "connect", lambda *args, **kwargs:
                        pytest.fail("unqualified storage attempted SQLite open"))
    path = tmp_path / "unopened.sqlite"
    keeper, status = storage.open_wal_keeper(path)
    assert keeper is None
    assert status["reason"] == "storage_not_qualified"
    assert status["effective_journal_mode"] is None
    assert status["deferred_to_scenario"] is False
    assert not path.exists()


def test_unc_path_never_qualifies_as_fixed_storage():
    from pathlib import Path
    status = storage._wal_storage_status(Path("//server/share/journal.sqlite"))
    assert status["local_fixed_storage"] is False


def test_full_is_set_even_if_new_connection_arrives_with_lower_sync(tmp_path, monkeypatch):
    original = sqlite3.connect

    def lowered_connection(*args, **kwargs):
        connection = original(*args, **kwargs)
        # Deliberately injected only on this fresh diagnostic file, before any
        # write. Production ownership must override it before operations start.
        connection.execute("PRAGMA synchronous=OFF")
        return connection

    monkeypatch.setattr(storage.sqlite3, "connect", lowered_connection)
    path = tmp_path / "full.sqlite"
    with closing(storage.connect(path)) as connection, connection:
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        connection.execute("CREATE TABLE record (value INTEGER)")
    with storage.transaction(path) as connection:
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        connection.execute("INSERT INTO record VALUES (7)")
    with closing(storage.connect(path)) as connection:
        assert connection.execute("SELECT value FROM record").fetchall()[0][0] == 7


def test_active_scenario_borrowed_without_new_connection_or_pragma(tmp_path, monkeypatch):
    path = tmp_path / "shared.sqlite"
    with closing(storage.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE record (value INTEGER)")
    with storage.transaction(path) as connection:
        sql = []
        connection.set_trace_callback(sql.append)
        monkeypatch.setattr(storage.sqlite3, "connect", lambda *args, **kwargs:
                            pytest.fail("borrowed scenario tried a second connection"))
        assert storage.connect(path) is connection
        keeper, status = storage.open_wal_keeper(path)
        assert keeper is None
        assert status["deferred_to_scenario"] is True
        assert status["reason"] == "active_scenario_transaction"
        assert sql == []
        with connection:
            connection.execute("INSERT INTO record VALUES (7)")
        connection.close()  # Borrowed stores cannot close the owner.
        assert connection.in_transaction is True
        assert connection.execute("SELECT value FROM record").fetchall()[0][0] == 7
    assert "COMMIT" in sql


def test_scenario_failure_rolls_back_borrowed_store_write(tmp_path):
    path = tmp_path / "rollback.sqlite"
    with closing(storage.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE record (value INTEGER)")
    with pytest.raises(RuntimeError, match="injected failure"):
        with storage.transaction(path) as owner:
            with closing(storage.connect(path)) as borrowed, borrowed:
                assert borrowed is owner
                borrowed.execute("INSERT INTO record VALUES (7)")
            raise RuntimeError("injected failure")
    with closing(storage.connect(path)) as connection:
        assert connection.execute("SELECT value FROM record").fetchall() == []


class _Cursor:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)

    def fetchall(self):
        return [(self.value,)]


class _ConnectionDouble:
    def __init__(self, *, sync=2, mode="wal", error=None):
        self.sync, self.mode, self.error = sync, mode, error
        self.closed = False
        self.in_transaction = False
        self.operations = []

    def execute(self, sql):
        self.operations.append(sql)
        if self.error is not None:
            raise self.error
        return _Cursor(self.mode if sql.startswith("PRAGMA journal_mode=") else self.sync)

    def close(self):
        self.closed = True


@pytest.mark.parametrize("entry", ("connect", "transaction", "open_wal_keeper"))
def test_owned_connection_closed_when_full_cannot_be_verified(tmp_path, monkeypatch, entry):
    _eligible(monkeypatch)
    double = _ConnectionDouble(sync=1)
    monkeypatch.setattr(storage.sqlite3, "connect", lambda *args, **kwargs: double)
    with pytest.raises(RuntimeError, match="FULL"):
        if entry == "transaction":
            with storage.transaction(tmp_path / "failed.sqlite"):
                pytest.fail("unverified FULL reached caller")
        else:
            getattr(storage, entry)(tmp_path / "failed.sqlite")
    assert double.closed is True


def test_rejected_wal_mode_closed_and_explicit_fallback(tmp_path, monkeypatch):
    _eligible(monkeypatch)
    double = _ConnectionDouble(mode="delete")
    monkeypatch.setattr(storage.sqlite3, "connect", lambda *args, **kwargs: double)
    keeper, status = storage.open_wal_keeper(tmp_path / "fallback.sqlite")
    assert keeper is None
    assert double.closed is True
    assert status["effective_journal_mode"] == "DELETE"
    assert status["reason"] == "journal_mode_not_wal"
    assert status["keeper_active"] is False
    assert "SELECT name FROM sqlite_schema" not in double.operations


def test_busy_or_corrupt_database_error_not_silently_downgraded(tmp_path, monkeypatch):
    _eligible(monkeypatch)
    failure = sqlite3.OperationalError("injected database busy")
    double = _ConnectionDouble(error=failure)
    monkeypatch.setattr(storage.sqlite3, "connect", lambda *args, **kwargs: double)
    with pytest.raises(sqlite3.OperationalError) as caught:
        storage.open_wal_keeper(tmp_path / "busy.sqlite")
    assert caught.value is failure
    assert double.closed is True


def test_keeper_closed_when_schema_priming_fails(tmp_path, monkeypatch):
    _eligible(monkeypatch)
    double = _ConnectionDouble()
    original = double.execute

    def fail_schema(sql):
        if sql == "SELECT name FROM sqlite_schema":
            raise sqlite3.DatabaseError("injected corrupt schema")
        return original(sql)

    monkeypatch.setattr(double, "execute", fail_schema)
    monkeypatch.setattr(storage.sqlite3, "connect", lambda *args, **kwargs: double)
    with pytest.raises(sqlite3.DatabaseError, match="corrupt schema"):
        storage.open_wal_keeper(tmp_path / "corrupt-schema.sqlite")
    assert double.closed is True


def test_keeper_primes_pager_retains_commits_backup_and_final_close(tmp_path, monkeypatch):
    _eligible(monkeypatch)
    path = tmp_path / "wal.sqlite"
    wal_path = tmp_path / "wal.sqlite-wal"
    backup_path = tmp_path / "backup.sqlite"
    keeper, status = storage.open_wal_keeper(path)
    assert keeper is not None
    try:
        assert status["keeper_active"] is True
        assert status["reason"] == "wal_keeper_ready"
        assert status["deferred_to_scenario"] is False
        assert keeper.in_transaction is False
        with closing(storage.connect(path)) as writer, writer:
            assert writer.execute("PRAGMA synchronous").fetchone()[0] == 2
            writer.execute("CREATE TABLE record (value INTEGER)")
            writer.execute("INSERT INTO record VALUES (7)")
        assert wal_path.exists() and wal_path.stat().st_size > 32
        assert keeper.in_transaction is False
        with closing(storage.connect(path)) as source:
            with closing(storage.connect(backup_path)) as destination:
                source.backup(destination)
                assert destination.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                assert destination.execute("SELECT value FROM record").fetchall()[0][0] == 7
        assert wal_path.exists()
    finally:
        keeper.close()
    assert not wal_path.exists()
    with closing(storage.connect(path)) as reopened:
        assert reopened.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert reopened.execute("SELECT value FROM record").fetchall()[0][0] == 7


def test_keeper_can_be_explicitly_closed_by_its_lifecycle_thread(tmp_path, monkeypatch):
    _eligible(monkeypatch)
    keeper, _ = storage.open_wal_keeper(tmp_path / "cross-thread.sqlite")
    results = []

    def close_owned_keeper():
        try:
            assert keeper.execute("PRAGMA synchronous").fetchone()[0] == 2
            keeper.close()
            results.append("closed")
        except BaseException as error:
            results.append(type(error).__name__)

    thread = Thread(target=close_owned_keeper)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert results == ["closed"]
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        keeper.execute("SELECT 1")
