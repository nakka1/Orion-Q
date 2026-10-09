"""Console campaign queries must not initialize/optimize or repair a journal."""
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from quantum_autopilot.control_plane.campaign_read_store import ReadOnlyCampaignStore
from quantum_autopilot.control_plane.attempt_batch import export_attempt_page
from quantum_autopilot.control_plane.attempt_receipt import read_attempt_receipt
from quantum_autopilot.control_plane.campaign_attempts import begin_attempt, list_attempts, read_attempt, export_attempt_snapshot
from quantum_autopilot.control_plane.temporal_campaign import list_temporal_campaigns, read_temporal_campaign
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from tests import test_console_temporal_campaign as shared
from tests.test_attempt_receipt import populate
from tests.test_temporal_campaign import request

app, setup, settle = shared.app, shared.setup, shared.settle


@pytest.fixture
def populated(tmp_path):
    writer = SimulatedTopologyStore(tmp_path / 'journal # 100% ação.sqlite')
    attempt, receipt = populate(writer)
    return writer, attempt, receipt


def test_constructor_never_initializes_or_connects(populated, monkeypatch):
    writer, _, _ = populated
    before = writer.path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError('read store initialization performed I/O beyond path checks')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(SimulatedTopologyStore, '__init__', forbidden)
    reader = ReadOnlyCampaignStore(writer.path)
    assert reader.path == writer.path and writer.path.read_bytes() == before


@pytest.mark.parametrize('kind', ['missing', 'missing-parent', 'directory'])
def test_missing_or_nonfile_path_not_created(tmp_path, kind):
    path = tmp_path if kind == 'directory' else tmp_path / 'missing.sqlite' if kind == 'missing' else tmp_path / 'absent' / 'journal.sqlite'
    before = list(tmp_path.iterdir())
    with pytest.raises((OSError, ValueError)):
        ReadOnlyCampaignStore(path)
    assert list(tmp_path.iterdir()) == before


@pytest.mark.parametrize('raw', [b'', b'not a SQLite database'])
def test_invalid_schema_is_not_repaired(tmp_path, raw):
    path = tmp_path / 'bad.sqlite'
    path.write_bytes(raw)
    reader = ReadOnlyCampaignStore(path)
    with pytest.raises(sqlite3.DatabaseError):
        list_attempts(reader)
    assert path.read_bytes() == raw
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('sql', ['DELETE FROM temporal_campaign_attempts', 'CREATE TABLE should_not_exist (a)',
    'PRAGMA user_version=123', "UPDATE temporal_path_campaigns SET record_json='{}'"])
def test_sqlite_enforces_read_only_even_if_query_only_is_disabled(populated, sql):
    writer, _, _ = populated
    reader = ReadOnlyCampaignStore(writer.path)
    before = writer.path.read_bytes()
    with closing(reader._connect()) as db:
        assert db.row_factory is sqlite3.Row
        assert db.execute('PRAGMA query_only').fetchone()[0] == 1
        db.execute('PRAGMA query_only=OFF')  # mode=ro still enforces protection.
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            db.execute(sql)
    assert writer.path.read_bytes() == before


def test_all_queries_and_exports_preserve_database(populated, tmp_path, monkeypatch):
    writer, attempt, receipt = populated
    before = writer.path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError('read flow used the writable store constructor')
    monkeypatch.setattr(SimulatedTopologyStore, '__init__', forbidden)
    reader = ReadOnlyCampaignStore(writer.path)
    assert list_temporal_campaigns(reader).entries[0].record_sha256 == receipt['record_sha256']
    assert read_temporal_campaign(reader, 'campaign')['record_sha256'] == receipt['record_sha256']
    assert list_attempts(reader)[0] == (attempt,)
    assert read_attempt(reader, attempt['record']['attempt_id']) == attempt
    export_attempt_snapshot(reader, attempt['record']['attempt_id'], attempt['record_sha256'], tmp_path / 'one.json')
    selection = ({'attempt_id': attempt['record']['attempt_id'], 'record_sha256': attempt['record_sha256']},)
    export_attempt_page(reader, selection, tmp_path / 'page.zip')
    assert read_attempt_receipt(reader.path, selection[0]['attempt_id'], selection[0]['record_sha256'])['record_sha256'] == receipt['record_sha256']
    assert writer.path.read_bytes() == before


def test_later_queries_observe_committed_writes(populated):
    writer, _, _ = populated
    with closing(sqlite3.connect(writer.path)) as db:
        db.execute('PRAGMA journal_mode=WAL')
    reader = ReadOnlyCampaignStore(writer.path)
    first = list_attempts(reader)[0]
    new_id = begin_attempt(writer, request())
    second = list_attempts(reader)[0]
    assert len(first) == 1 and len(second) == 2
    assert second[0]['record']['attempt_id'] == new_id


def test_removed_database_after_construction_is_not_recreated(populated):
    writer, _, _ = populated
    reader = ReadOnlyCampaignStore(writer.path)
    saved = writer.path.with_suffix('.saved')
    writer.path.rename(saved)  # Isolated fixture only; preserve the original bytes.
    with pytest.raises(sqlite3.OperationalError):
        list_attempts(reader)
    assert not writer.path.exists() and saved.is_file()


def test_connection_closed_when_configuration_fails(populated, monkeypatch):
    class FailingConnection:
        closed = False
        def execute(self, *args):
            raise sqlite3.OperationalError('injected configuration failure')
        def close(self):
            self.closed = True
    db = FailingConnection()
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: db)
    with pytest.raises(sqlite3.OperationalError, match='injected'):
        ReadOnlyCampaignStore(populated[0].path)._connect()
    assert db.closed


def test_native_browse_open_export_without_writable_constructor(setup, app, tmp_path, monkeypatch):
    import quantum_autopilot.console.campaign_job as adapter
    service, _, job, _, _ = setup
    attempt, receipt = populate(service.topology_store)
    before = service.topology_store.path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError('console read/export instantiated a writable store')
    monkeypatch.setattr(adapter, 'SimulatedTopologyStore', forbidden)
    job.browse_history()
    settle(app, lambda: not job.active)
    assert len(job.history_entries) == 1
    job.open_history(0)
    settle(app, lambda: not job.active)
    assert job.receipt['record_sha256'] == receipt['record_sha256']
    job.browse_attempts()
    settle(app, lambda: not job.active)
    assert job.attempts == (attempt,)
    job.export_attempt(0, tmp_path / 'one.json')
    settle(app, lambda: not job.active)
    assert 'ATTEMPT EXPORTED' in job.attempt_note
    job.export_attempt_page(tmp_path / 'page.zip')
    settle(app, lambda: not job.active)
    assert 'PAGE EXPORTED' in job.attempt_note
    job.open_attempt_receipt(0)
    settle(app, lambda: not job.active)
    assert 'ASSOCIATION VERIFIED' in job.note
    job.export(tmp_path / 'scientific.json')
    settle(app, lambda: not job.active)
    assert 'EXPORTED' in job.note
    assert job.process is None and service.topology_store.path.read_bytes() == before


@pytest.mark.parametrize('operation', ['history', 'receipt', 'attempts', 'single-export', 'page-export', 'linked-receipt'])
def test_missing_source_refused_without_creating_database_or_export(setup, app, tmp_path, monkeypatch, operation):
    service, provider, job, _, _ = setup
    populate(service.topology_store)
    job.browse_history()
    settle(app, lambda: not job.active)
    job.browse_attempts()
    settle(app, lambda: not job.active)
    missing = tmp_path / 'absent' / 'journal.sqlite'
    destination = tmp_path / 'refused-export'
    monkeypatch.setattr(type(provider.active), 'temporal_campaign_history_store_path', lambda _: missing)
    actions = {
        'history': job.browse_history, 'receipt': lambda: job.open_history(0),
        'attempts': job.browse_attempts, 'single-export': lambda: job.export_attempt(0, destination),
        'page-export': lambda: job.export_attempt_page(destination), 'linked-receipt': lambda: job.open_attempt_receipt(0),
    }
    actions[operation]()
    settle(app, lambda: not job.active)
    assert not missing.parent.exists() and not destination.exists() and job.process is None
    text = job.note + job.history_note + job.attempt_note
    assert any(word in text for word in ('UNAVAILABLE', 'REFUSED', 'FAILED'))


def test_real_worker_keeps_writes_but_uses_read_only_readback(setup, app, monkeypatch):
    import quantum_autopilot.console.campaign_job as adapter
    service, _, job, _, manifest = setup
    original, calls = adapter.ReadOnlyCampaignStore, []
    def capture(path):
        calls.append(Path(path))
        return original(path)
    monkeypatch.setattr(adapter, 'ReadOnlyCampaignStore', capture)
    job.load(manifest)
    job.start()
    settle(app, lambda: not job.active)
    assert job.receipt is not None and 'CONFIRMED' in job.note
    assert calls == [service.topology_store.path]
    records = list_attempts(ReadOnlyCampaignStore(service.topology_store.path))[0]
    assert len(records) == 1 and records[0]['record']['status'] == 'CONFIRMED'
