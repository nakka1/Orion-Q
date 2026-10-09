"""Bounded history discovery, verified opening, no reruns or cross-source results."""
import json
import os
import sqlite3
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

from quantum_autopilot.control_plane.temporal_campaign import list_temporal_campaigns, read_temporal_campaign, run_temporal_campaign
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from tests.test_temporal_campaign import request
from tests import test_console_temporal_campaign as shared

app, setup, settle = shared.app, shared.setup, shared.settle


def save(store, count=1):
    req = request(max_attempts=1)
    req = replace(req, regimes=(req.regimes[0], replace(req.regimes[0], regime_id="candidate")))
    return [run_temporal_campaign(store, replace(req, campaign_id=f"history-{i:03}")) for i in range(count)]


def test_bounded_keyset_pages_survive_concurrent_insert(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "history.sqlite")
    save(store, 23)
    page = list_temporal_campaigns(store)
    assert len(page.entries) == 20
    assert page.entries[0].campaign_id == "history-022"
    assert page.next_before == 4
    run_temporal_campaign(store, replace(request(max_attempts=1), campaign_id="new"))
    older = list_temporal_campaigns(store, before_sequence=page.next_before)
    assert [e.campaign_id for e in older.entries] == ["history-002", "history-001", "history-000"]
    assert older.next_before is None
    assert list_temporal_campaigns(store).entries[0].campaign_id == "new"


@pytest.mark.parametrize("kwargs", [{"limit": True}, {"limit": 0}, {"limit": 51}, {"limit": 1.5},
    {"before_sequence": True}, {"before_sequence": 0}, {"before_sequence": -2}, {"before_sequence": 2**63},
    {"before_sequence": "3 OR 1=1"}])
def test_history_bounds(tmp_path, kwargs):
    with pytest.raises(ValueError):
        list_temporal_campaigns(SimulatedTopologyStore(tmp_path / "db.sqlite"), **kwargs)


def test_index_does_not_deserialize_or_pretend_to_verify_receipts(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "db.sqlite")
    assert list_temporal_campaigns(store).entries == ()
    save(store)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE temporal_path_campaigns SET record_json='invalid json'")
    assert list_temporal_campaigns(store).entries[0].campaign_id == "history-000"
    with pytest.raises(ValueError, match="integrity"):
        read_temporal_campaign(store, "history-000")


def test_old_version_open_after_restart_does_not_execute_or_modify(setup, app, tmp_path, monkeypatch):
    import quantum_autopilot.control_plane.temporal_campaign as engine
    service, provider, job, dialog, manifest = setup
    original = save(SimulatedTopologyStore(service.topology_store.path))[0]
    service.topology_store.reset()
    before = service.topology_store.get(request().topology_id)
    monkeypatch.setattr(engine, "evaluate_temporal_campaign", lambda *a, **k: pytest.fail("history reran an experiment"))
    job.load(manifest)  # UI snapshot still v0; history must clear this runnable request.
    dialog.reviewed.setChecked(True)
    job.browse_history()
    settle(app, lambda: not job.active)
    assert job.history_entries[0].campaign_id == "history-000"
    assert "UNVERIFIED" in job.history_rows[0][-1][0]
    job.open_history(0)
    settle(app, lambda: not job.active)
    assert job.request is None and not dialog.reviewed.isChecked()
    assert not dialog.run_button.isEnabled() and job.process is None
    assert job.receipt["record_sha256"] == original["record_sha256"]
    assert "HISTORICAL" in job.note and "version 0" in job.note
    assert service.topology_store.get(request().topology_id) == before
    assert dialog.tabs.currentIndex() == 1
    exported = tmp_path / "historical-export.json"
    job.export(exported)
    settle(app, lambda: not job.active)
    assert json.loads(exported.read_text())["record_sha256"] == original["record_sha256"]


def test_ui_pages_and_selection_survive_regular_refresh(setup, app):
    service, _, job, dialog, _ = setup
    save(service.topology_store, 22)
    job.browse_history()
    settle(app, lambda: not job.active)
    dialog.history_table.selectRow(2)
    job.refresh_source()
    assert dialog.history_table.currentIndex().row() == 2
    assert dialog.history_open.isEnabled()
    job.browse_history("older")
    settle(app, lambda: not job.active)
    assert len(job.history_entries) == 2 and job.history_back == (None,)
    assert not dialog.history_older.isEnabled() and dialog.history_newer.isEnabled()
    job.browse_history("newer")
    settle(app, lambda: not job.active)
    assert len(job.history_entries) == 20 and not job.history_back


def test_corrupted_receipt_refused_and_previous_result_cleared(setup, app):
    service, _, job, dialog, _ = setup
    save(service.topology_store)
    job.browse_history()
    settle(app, lambda: not job.active)
    job.open_history(0)
    settle(app, lambda: not job.active)
    assert job.receipt is not None
    with sqlite3.connect(service.topology_store.path) as db:
        db.execute("UPDATE temporal_path_campaigns SET record_json='{}'")
    job.open_history(0)
    settle(app, lambda: not job.active)
    assert job.receipt is None and job.request is None
    assert "integrity" in job.note
    assert not dialog.export_button.isEnabled() and dialog.results.records.rowCount() == 0


def test_deleted_record_refused(setup, app):
    service, _, job, _, _ = setup
    save(service.topology_store)
    job.browse_history()
    settle(app, lambda: not job.active)
    with sqlite3.connect(service.topology_store.path) as db:
        db.execute("DELETE FROM temporal_path_campaigns")  # Isolated test fixture only.
    job.open_history(0)
    settle(app, lambda: not job.active)
    assert job.receipt is None and "disappeared" in job.note


def test_slow_history_result_discarded_after_source_switch(setup, app, tmp_path, monkeypatch):
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    import quantum_autopilot.console.campaign_job as adapter
    service, provider, job, dialog, _ = setup
    save(service.topology_store)
    entered, release = Event(), Event()
    original = adapter.list_temporal_campaigns
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(adapter, "list_temporal_campaigns", slow)
    job.browse_history()
    settle(app, entered.is_set)
    try:
        provider.switch(lambda: MeasuredFileProvider(tmp_path / "absent.json", tmp_path / "measured"))
        assert not job.history_entries and not dialog.history_refresh.isEnabled()
    finally:
        release.set()
    settle(app, lambda: not job.active)
    assert not job.history_entries and job.receipt is None
    assert "discarded" in job.history_note
    with pytest.raises(ValueError):
        job.browse_history()


def test_history_available_without_worker_and_no_implicit_execution(setup, app, monkeypatch):
    import sys
    service, provider, job, dialog, _ = setup
    save(service.topology_store)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_orion_temporal_worker_v1", raising=False)
    assert not job.allowed and job.history_allowed
    assert provider.active.temporal_campaign_history_store_path() == service.topology_store.path
    job.browse_history()
    settle(app, lambda: not job.active)
    job.open_history(0)
    settle(app, lambda: not job.active)
    assert job.receipt is not None and not dialog.run_button.isEnabled()
    assert dialog.export_button.isEnabled()


def test_history_render(setup, app):
    from quantum_autopilot.console.theme import install
    service, _, job, dialog, _ = setup
    install(app)
    save(service.topology_store, 22)
    job.browse_history()
    settle(app, lambda: not job.active)
    dialog.tabs.setCurrentIndex(2)
    dialog.show()
    for width, height in ((640, 500), (1080, 740)):
        dialog.resize(width, height)
        app.processEvents()
        assert dialog.history_table.viewport().height() > 60
        assert dialog.rect().contains(dialog.history_open.mapTo(dialog, dialog.history_open.rect().bottomRight()))
        evidence = os.environ.get("ORION_HISTORY_EVIDENCE")
        if evidence:
            root = Path(evidence)
            root.mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(root / f"history-{width}.png"))
