"""Persistence profile rejects unsafe configurations and verifies real receipts."""
import json
import sqlite3

import pytest

from tools.profile_trl5_persistence import MODES, ProfiledConnection, Trace, run_profile, wal_version_eligible

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("version,expected", (
    ("3.53.1", True), ("3.51.3", True), ("3.51.2", False), ("3.50.7", True),
    ("3.50.6", False), ("3.44.6", True), ("3.44.5", False), ("3.49.99", False),
    ("3.51.3-custom", False), ("3.51", False), (None, False), ("-3.51.3", False),
))
def test_wal_requires_known_official_fixed_version(version, expected):
    assert wal_version_eligible(version) is expected


@pytest.mark.parametrize("kwargs", (
    {"samples": True}, {"samples": 0}, {"samples": 1001}, {"warmup": -1},
    {"warmup": False}, {"audit_samples": -1}, {"audit_samples": 101},
    {"modes": ()}, {"modes": ("DELETE", "DELETE")}, {"modes": ("OFF",)},
))
def test_invalid_arguments_refused_before_creating_output(tmp_path, kwargs):
    output = tmp_path / "not-created"
    with pytest.raises(ValueError):
        run_profile(output, **kwargs)
    assert not output.exists()


def test_existing_output_and_sentinel_preserved(tmp_path):
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_profile(tmp_path, modes=("DELETE",), samples=1)
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["sentinel.txt"]


def test_wal_network_storage_refused_before_creation(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.profile_trl5_persistence.disk_eligibility", lambda _: {
        "wal_local_disk_eligible": False, "reason": "network test double"})
    output = tmp_path / "not-created"
    with pytest.raises(ValueError, match="qualified local"):
        run_profile(output, modes=("WAL",), samples=1)
    assert not output.exists()


def test_unpatched_wal_refused_before_creation(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.profile_trl5_persistence.disk_eligibility", lambda _: {
        "wal_local_disk_eligible": True})
    monkeypatch.setattr("tools.profile_trl5_persistence.sqlite3.sqlite_version", "3.51.2")
    output = tmp_path / "not-created"
    with pytest.raises(ValueError, match="patched SQLite"):
        run_profile(output, modes=("WAL_KEEPER",), samples=1)
    assert not output.exists()


def test_context_commit_and_rollback_are_measured_and_durable(tmp_path):
    trace = Trace()
    path = tmp_path / "context.sqlite"
    connection = sqlite3.connect(path, factory=lambda *args, **kwargs:
                                 ProfiledConnection(*args, trace=trace, **kwargs))
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("CREATE TABLE receipt (value INTEGER)")
    with connection:
        connection.execute("INSERT INTO receipt VALUES (7)")
    with pytest.raises(RuntimeError):
        with connection:
            connection.execute("INSERT INTO receipt VALUES (8)")
            raise RuntimeError("diagnostic rollback")
    connection.close()
    summary = trace.summary()
    assert summary["context_commit"]["n"] == 1
    assert summary["context_rollback"]["n"] == 1
    assert summary["connection_close"]["n"] == 1
    with sqlite3.connect(path) as reopened:
        assert reopened.execute("SELECT value FROM receipt").fetchall() == [(7,)]


def test_bounded_profile_real_cycles_backup_full_and_hashes(tmp_path):
    # Portable rollback-mode witness. WAL locality is separately fail-closed;
    # the coordinated Windows profile, not this unit, covers all six modes.
    directory = tmp_path / "profile"
    report = run_profile(directory, samples=2, warmup=1, modes=("PERSIST",))
    assert report["passed"], report["checks"]
    assert report["operator_state_accessed"] is False
    assert report["physical_action_performed"] is False
    assert report["environment"]["gui_started"] is False
    assert report["adequate_p99_sampling"] is False
    assert report["separate_authenticated_audit"]["performed"] is False
    result = report["modes"]["PERSIST"]
    assert result["record_count"] == 3
    assert set(result["synchronous_counts"]) == {2}
    assert set(result["effective_mode_counts"]) == {"PERSIST"}
    assert result["cycle_ms"]["n"] == 2
    assert result["boundary_ms"]["context_commit"]["n"] == 2
    assert result["boundary_ms"]["connection_close"]["n"] == 4
    assert result["checks"]["backup_identical_receipts"] is True
    assert result["checks"]["idempotent_no_second_record"] is True
    decoded = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert decoded["schema"] == report["schema"]
    assert decoded["passed"] is True
    receipts = (directory / "persist" / "receipts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(receipts) == 3
    assert all(json.loads(line)["mode"] == "simulation" for line in receipts)
    assert MODES == ("DEFAULT_DELETE", "DELETE", "PERSIST", "TRUNCATE", "WAL", "WAL_KEEPER")
