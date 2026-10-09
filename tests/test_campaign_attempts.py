"""Operational attempts must never manufacture scientific samples or outcomes."""
import json
import os
import sqlite3
from pathlib import Path
from threading import Event

import pytest

from quantum_autopilot.control_plane.campaign_attempts import begin_attempt, finish_attempt, list_attempts
from quantum_autopilot.control_plane.temporal_campaign import read_temporal_campaign
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from tests import test_console_temporal_campaign as shared
from tests.test_temporal_campaign import request

app, setup, settle = shared.app, shared.setup, shared.settle


def test_begin_survives_reopen_no_guessing_liveness_or_scientific_receipt(tmp_path):
    path = tmp_path / 'state.sqlite'
    store = SimulatedTopologyStore(path)
    first = begin_attempt(store, request())
    second = begin_attempt(store, request())
    assert first != second
    reopened = SimulatedTopologyStore(path)
    records, _ = list_attempts(reopened)
    assert [v['record']['attempt_id'] for v in records] == [second, first]
    assert all(v['record']['status'] == 'OPEN' for v in records)
    assert read_temporal_campaign(reopened) is None
    assert not {'fidelity', 'timeouts', 'qber', 'skr'} & records[0]['record'].keys()


def test_terminal_observation_is_idempotent_but_not_rewritable(tmp_path):
    store = SimulatedTopologyStore(tmp_path / 'state')
    attempt = begin_attempt(store, request())
    kwargs = dict(status='CANCELLED', exit_code=3, cancelled=True, detail='cancelled before dispatch')
    finish_attempt(store, attempt, **kwargs)
    first = list_attempts(store)[0][0]
    finish_attempt(store, attempt, **kwargs)
    assert list_attempts(store)[0][0] == first
    with pytest.raises(ValueError, match='rewritten'):
        finish_attempt(store, attempt, status='FAILED', exit_code=2)
    assert read_temporal_campaign(store) is None


@pytest.mark.parametrize('kwargs', [dict(status='OPEN', exit_code=0), dict(status='FAILED', exit_code=True),
    dict(status='FAILED', exit_code=2, receipt_sha256='a'*64), dict(status='CONFIRMED', exit_code=0),
    dict(status='RECOVERED', exit_code=3, receipt_sha256='invalid'), dict(status='FAILED', exit_code=2, detail='x'*2001)])
def test_terminal_contract(tmp_path, kwargs):
    store = SimulatedTopologyStore(tmp_path / 'state')
    attempt = begin_attempt(store, request())
    with pytest.raises(ValueError):
        finish_attempt(store, attempt, **kwargs)
    assert list_attempts(store)[0][0]['record']['status'] == 'OPEN'


def test_attempt_pagination_and_integrity(tmp_path):
    store = SimulatedTopologyStore(tmp_path / 'state')
    ids = [begin_attempt(store, request()) for _ in range(23)]
    first, cursor = list_attempts(store)
    assert len(first) == 20 and cursor is not None
    begin_attempt(store, request())
    second, cursor = list_attempts(store, before_sequence=cursor)
    assert [v['record']['attempt_id'] for v in second] == list(reversed(ids[:3]))
    assert cursor is None
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE temporal_campaign_attempts SET record_json='{}' WHERE attempt_id=?", (ids[0],))
    with pytest.raises(ValueError, match='integrity'):
        list_attempts(store, before_sequence=4)


def test_native_success_replay_cancel_and_launch_failure_are_distinct(setup, app, monkeypatch):
    import quantum_autopilot.worker_runtime as runtime
    service, _, job, dialog, manifest = setup
    job.load(manifest)
    job.start()
    settle(app, lambda: not job.active)
    assert list_attempts(service.topology_store)[0][0]['record']['status'] == 'CONFIRMED'
    receipt_hash = job.receipt['record_sha256']
    job.start()
    settle(app, lambda: not job.active)
    assert list_attempts(service.topology_store)[0][0]['record']['status'] == 'REPLAYED'
    monkeypatch.setattr(runtime.sys, 'executable', 'orion-no-such-executable')
    job.start()
    settle(app, lambda: not job.active)
    records, _ = list_attempts(service.topology_store)
    assert len(records) == 3 and records[0]['record']['status'] == 'RECOVERED'
    assert records[0]['record']['receipt_sha256'] == receipt_hash
    assert records[0]['record']['process_exit_code'] == -1
    job.browse_attempts()
    settle(app, lambda: not job.active)
    dialog.attempt_table.selectRow(0)
    assert json.loads(dialog.attempt_detail.toPlainText())['record']['status'] == 'RECOVERED'


def test_failed_launch_without_receipt_has_no_quantum_outcome(setup, app, monkeypatch):
    import quantum_autopilot.worker_runtime as runtime
    service, _, job, _, manifest = setup
    monkeypatch.setattr(runtime.sys, 'executable', 'orion-no-such-executable')
    job.load(manifest)
    job.start()
    settle(app, lambda: not job.active)
    record = list_attempts(service.topology_store)[0][0]['record']
    assert record['status'] == 'FAILED_TO_START' and record['receipt_sha256'] is None
    assert job.receipt is None and read_temporal_campaign(service.topology_store) is None


def test_cancel_before_dispatch_is_durable(setup, app):
    service, _, job, _, manifest = setup
    job.load(manifest)
    job.start()
    job.cancel()
    settle(app, lambda: not job.active)
    record = list_attempts(service.topology_store)[0][0]['record']
    assert record['status'] == 'CANCELLED' and record['cancel_requested'] is True
    assert read_temporal_campaign(service.topology_store) is None


def test_journal_begin_failure_never_launches_worker(setup, app, monkeypatch):
    import quantum_autopilot.console.campaign_job as adapter
    service, _, job, _, manifest = setup
    def fail(*args):
        raise OSError('injected disk failure')
    monkeypatch.setattr(adapter, 'begin_attempt', fail)
    job.load(manifest)
    job.start()
    settle(app, lambda: not job.active)
    assert job.process is None and 'no worker launched' in job.note
    assert not list_attempts(service.topology_store)[0]
    assert read_temporal_campaign(service.topology_store) is None


def test_journal_finish_failure_preserves_receipt_but_warns(setup, app, monkeypatch):
    import quantum_autopilot.console.campaign_job as adapter
    service, _, job, _, manifest = setup
    def fail(*args, **kwargs):
        raise OSError('injected terminal write failure')
    monkeypatch.setattr(adapter, 'finish_attempt', fail)
    job.load(manifest)
    job.start()
    settle(app, lambda: not job.active)
    assert job.receipt is not None and 'NOT finalized' in job.note
    assert list_attempts(service.topology_store)[0][0]['record']['status'] == 'OPEN'


def test_source_switch_during_registration_finishes_on_original_store(setup, app, tmp_path, monkeypatch):
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    import quantum_autopilot.console.campaign_job as adapter
    service, provider, job, dialog, manifest = setup
    entered, release = Event(), Event()
    original = adapter.begin_attempt
    def slow(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(adapter, 'begin_attempt', slow)
    job.load(manifest)
    job.start()
    settle(app, entered.is_set)
    try:
        provider.switch(lambda: MeasuredFileProvider(tmp_path / 'absent.json', tmp_path / 'measured'))
        assert not dialog.attempt_refresh.isEnabled()
    finally:
        release.set()
    settle(app, lambda: not job.active)
    assert list_attempts(service.topology_store)[0][0]['record']['status'] == 'CANCELLED'
    assert not job.attempts and job.receipt is None


def test_attempt_ui_pagination_and_layout(setup, app):
    from quantum_autopilot.console.theme import install
    install(app)
    service, _, job, dialog, _ = setup
    for _ in range(23):
        begin_attempt(service.topology_store, request())
    job.browse_attempts()
    settle(app, lambda: not job.active)
    assert len(job.attempts) == 20
    job.browse_attempts('older')
    settle(app, lambda: not job.active)
    assert len(job.attempts) == 3 and job.attempt_back
    job.browse_attempts('newer')
    settle(app, lambda: not job.active)
    dialog.tabs.setCurrentIndex(3)
    dialog.show()
    dialog.attempt_table.selectRow(0)
    for width, height in ((640, 500), (1080, 740)):
        dialog.resize(width, height)
        app.processEvents()
        assert dialog.attempt_table.viewport().height() > 40
        dialog.attempts_scroll.ensureWidgetVisible(dialog.attempt_older)
        app.processEvents()
        assert dialog.rect().contains(dialog.attempt_older.mapTo(dialog, dialog.attempt_older.rect().bottomRight()))
        if folder := os.environ.get('ORION_ATTEMPT_EVIDENCE'):
            Path(folder).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(folder) / f'attempts-{width}.png'))
