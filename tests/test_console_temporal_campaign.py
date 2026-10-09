"""Real child-process campaign UX, receipt verification and cancellation."""
import json
import os
import time
from dataclasses import asdict, replace
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console.campaign_job import CampaignJob
from quantum_autopilot.console.service_provider import ServiceProvider
from quantum_autopilot.console.source_switch import SwitchableProvider
from quantum_autopilot.console.widgets.campaign_runner import CampaignRunner
from quantum_autopilot.control_plane.temporal_campaign import CampaignCancelled, evaluate_temporal_campaign, run_temporal_campaign, read_temporal_campaign
from quantum_autopilot.control_plane.topology import default_simulated_topology, SimulatedTopologyStore
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
from tests.test_temporal_campaign import request

ROOT = Path(__file__).resolve().parents[1]


def test_live_ticks_do_not_repaint_an_unchanged_campaign_form(setup):
    _, provider, job, dialog, _ = setup
    notifications = []
    job.changed.connect(lambda: notifications.append(True))
    job.refresh_source()
    notifications.clear()
    for _ in range(20):
        job.refresh_source()
    assert notifications == []
    provider.stop()
    job.refresh_source()
    assert notifications == [True], "lost execution authority must still clear the source presentation"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def settle(app, condition, timeout=45):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert condition()


@pytest.fixture
def setup(tmp_path, app):
    service = AssuranceDashboardService(repository_root=ROOT, control_journal_path=tmp_path / "state.sqlite")
    service.topology_store.initialize(default_simulated_topology())
    provider = SwitchableProvider(ServiceProvider(service, poll_ms=60000, state_root=tmp_path))
    provider.start()
    settle(app, lambda: provider._snap is not None and not provider.busy)
    job = CampaignJob(provider)
    provider.source_changed.connect(lambda _: job.refresh_source())
    provider.snapshot_changed.connect(lambda _: job.refresh_source())
    dialog = CampaignRunner()
    dialog.attach(job)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(asdict(request())), encoding="utf-8")
    yield service, provider, job, dialog, manifest
    if job.active:
        job.cancel()
        settle(app, lambda: not job.active)
    job.shutdown()
    provider.stop()
    dialog.close()


def test_progress_hooks_and_cancel_at_final_trial_prevents_commit(tmp_path):
    calls = []
    store = SimulatedTopologyStore(tmp_path / "state.sqlite")
    def progress(done, total, regime, seed):
        calls.append((done, total, regime, seed))
    with pytest.raises(CampaignCancelled):
        run_temporal_campaign(store, request(), progress=progress, cancelled=lambda: len(calls) == 4)
    assert [c[0] for c in calls] == [1, 2, 3, 4]
    assert read_temporal_campaign(store) is None
    assert store.get(request().topology_id) is None
    with pytest.raises(CampaignCancelled):
        evaluate_temporal_campaign(default_simulated_topology(), request(), cancelled=lambda: True)


def test_native_worker_progress_responsiveness_and_verified_export(setup, app, tmp_path):
    service, provider, job, dialog, manifest = setup
    job.load(manifest)
    dialog.submit()
    assert not job.active and "review" in dialog.note.text()
    dialog.reviewed.setChecked(True)
    assert dialog.run_button.isEnabled()
    ticks = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    dialog.submit()
    assert job.active and not dialog.load_button.isEnabled()
    assert dialog.cancel_button.isEnabled()
    settle(app, lambda: not job.active)
    timer.stop()
    assert len(ticks) >= 3  # Event loop runs while Qiskit starts in the child.
    assert job.process is None and job.completed == job.total == 4
    assert job.receipt is not None and "CONFIRMED" in job.note
    assert dialog.results.records.rowCount() == 2
    assert dialog.comparisons.records.rowCount() == 1
    assert job.receipt["record_sha256"] == read_temporal_campaign(service.topology_store)["record_sha256"]
    assert service.topology_store.get(request().topology_id).topology_version == 0
    output = tmp_path / "receipt.json"
    job.export(output)
    settle(app, lambda: not job.active)
    saved = output.read_bytes()
    assert json.loads(saved)["record_sha256"] == job.receipt["record_sha256"]
    job.export(output)
    settle(app, lambda: not job.active)
    assert "EXPORT FAILED" in job.note and output.read_bytes() == saved


def test_cancel_starting_child_and_no_partial_receipt(setup, app):
    service, _, job, dialog, manifest = setup
    job.load(manifest)
    job.start()
    job.cancel()
    assert job.cancel_requested and not dialog.cancel_button.isEnabled()
    settle(app, lambda: not job.active)
    assert job.note.startswith("CANCELLED")
    assert read_temporal_campaign(service.topology_store) is None
    assert not dialog.export_button.isEnabled()


def test_cancel_does_not_erase_existing_completed_receipt(setup, app):
    service, _, job, _, manifest = setup
    original = run_temporal_campaign(service.topology_store, request())
    job.load(manifest)
    job.start()
    job.cancel()
    settle(app, lambda: not job.active)
    assert job.receipt["record_sha256"] == original["record_sha256"]
    assert "CONFIRMED" in job.note and "no rollback" in job.note


def test_wrong_request_receipt_never_confirms(setup, app, tmp_path):
    service, _, job, dialog, _ = setup
    run_temporal_campaign(service.topology_store, request())
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(asdict(replace(request(), target_fidelity=.99))), encoding="utf-8")
    job.load(changed)
    job.start()
    settle(app, lambda: not job.active)
    assert job.receipt is None and job.note.startswith("UNCONFIRMED")
    assert "different request" in job.note
    assert dialog.results.records.rowCount() == 0


def test_tampered_receipt_is_not_displayed(setup, app):
    import sqlite3
    service, _, job, dialog, manifest = setup
    run_temporal_campaign(service.topology_store, request())
    with sqlite3.connect(service.topology_store.path) as db:
        db.execute("UPDATE temporal_path_campaigns SET record_json = '{}' WHERE campaign_id = 'campaign'")
    job.load(manifest)
    job.start()
    settle(app, lambda: not job.active)
    assert job.receipt is None and "integrity" in job.note
    assert dialog.results.records.rowCount() == 0


def test_forced_process_stop_is_unconfirmed_not_a_quantum_timeout(setup, app):
    from PySide6.QtCore import QProcess
    service, _, job, dialog, manifest = setup
    job.load(manifest)
    job.start()
    settle(app, lambda: job.process.state() == QProcess.ProcessState.Running)
    job.process.kill()
    settle(app, lambda: not job.active)
    assert job.process is None and job.receipt is None
    assert job.note.startswith("UNCONFIRMED")
    assert read_temporal_campaign(service.topology_store) is None
    assert dialog.results.records.rowCount() == 0


def test_version_change_after_review_is_refused_by_worker(setup, app):
    service, _, job, _, manifest = setup
    job.load(manifest)
    service.topology_store.reset()  # UI snapshot remains stale; DB gate must catch it.
    job.start()
    settle(app, lambda: not job.active)
    assert "conflict" in job.note and job.receipt is None
    assert read_temporal_campaign(service.topology_store) is None


def test_source_switch_cancels_and_discards_late_result(setup, app, tmp_path):
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    _, provider, job, dialog, manifest = setup
    job.load(manifest)
    job.start()
    provider.switch(lambda: MeasuredFileProvider(tmp_path / "absent.json", tmp_path / "measured"))
    assert job.request is None and job.receipt is None
    assert job.review_text == "" and dialog.results.records.rowCount() == 0
    assert not dialog.run_button.isEnabled()
    settle(app, lambda: not job.active)
    assert job.receipt is None and "not attached" in job.note
    with pytest.raises(ValueError):
        job.load(manifest)
    with pytest.raises(ValueError):
        job.start()


def test_invalid_replacement_clears_prior_review_and_result(setup, tmp_path):
    _, _, job, dialog, manifest = setup
    job.load(manifest)
    dialog.reviewed.setChecked(True)
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"x":1,"x":2}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        job.load(invalid)
    assert job.request is None and not dialog.reviewed.isChecked()
    assert dialog.manifest.toPlainText() == "" and not dialog.run_button.isEnabled()


def test_failed_process_launch_never_confirms(setup, app, monkeypatch):
    import quantum_autopilot.worker_runtime as module
    _, _, job, _, manifest = setup
    job.load(manifest)
    monkeypatch.setattr(module.sys, "executable", "orion-missing-python-executable")
    job.start()
    settle(app, lambda: not job.active)
    assert job.note.startswith("UNCONFIRMED") and job.receipt is None


def test_frozen_build_does_not_claim_worker_support(setup, monkeypatch):
    import sys
    _, provider, job, _, _ = setup
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_orion_temporal_worker_v1", raising=False)
    assert not provider.capabilities.allows("run_temporal_campaign")
    assert not job.allowed
    with pytest.raises(ValueError):
        provider.active.temporal_campaign_store_path()


def test_main_window_navigation_hide_and_close_manage_child(app, tmp_path):
    from quantum_autopilot.console.shell import ConsoleWindow
    service = AssuranceDashboardService(repository_root=ROOT, control_journal_path=tmp_path / "window.sqlite")
    window = ConsoleWindow(ServiceProvider(service, poll_ms=60000), skip_auth=True)
    window.provider.start()
    settle(app, lambda: window._snap is not None and not window._busy)
    window.show()
    window.show_screen("topology")
    dialog = window.screens["topology"].campaign_runner
    job = window.campaign_job
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(asdict(request())), encoding="utf-8")
    try:
        job.load(manifest)
        dialog.begin()
        dialog.reviewed.setChecked(True)
        dialog.submit()
        dialog.reject()  # Esc/hide does not cancel.
        assert job.active and not job.cancel_requested
        window.show_screen("node")
        settle(app, lambda: not job.active)
        assert job.receipt is not None
        job.start()  # Same request: existing receipt survives closing/cancel.
        window.close()
        assert window._close_after_campaign
        settle(app, lambda: not window.provider.running)
        assert not job.active and job.process is None
    finally:
        if job.active:
            job.cancel()
            settle(app, lambda: not job.active)
        window.close()


def test_native_comparison_render_at_small_and_large_sizes(setup, app):
    from quantum_autopilot.console.theme import install
    service, _, job, dialog, manifest = setup
    install(app)
    req = request()
    req = replace(req, regimes=(*req.regimes, replace(req.regimes[1], regime_id="longer-memory"),
                               replace(req.regimes[0], regime_id="short-retention")))
    manifest.write_text(json.dumps(asdict(req)), encoding="utf-8")
    job.load(manifest)
    job.receipt = run_temporal_campaign(service.topology_store, req)
    job._present(job.receipt)
    job.completed = job.total
    job.note = "CONFIRMED · software rendering fixture; simulated receipt."
    job.changed.emit()
    dialog.tabs.setCurrentIndex(1)
    dialog.show()
    for width, height in ((640, 500), (1080, 740)):
        dialog.resize(width, height)
        app.processEvents()
        assert dialog.results.viewport().width() > 0
        assert dialog.comparisons.viewport().height() > 0
        assert dialog.results.geometry().bottom() < dialog.comparisons.geometry().top()
        if height == 500:
            assert dialog.results_scroll.verticalScrollBar().maximum() > 0
        assert dialog.rect().contains(dialog.export_button.mapTo(dialog, dialog.export_button.rect().bottomRight()))
        evidence = os.environ.get("ORION_CAMPAIGN_UI_EVIDENCE")
        if evidence:
            directory = Path(evidence)
            directory.mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(directory / f"campaign-{width}.png"))


def test_topology_campaign_entry_render_preserves_links(setup, app):
    from quantum_autopilot.console.screens.topology import TopologyScreen
    from quantum_autopilot.console.theme import install
    _, provider, job, _, _ = setup
    install(app)
    screen = TopologyScreen()
    screen.campaign_runner.attach(job)
    screen.apply(provider.snapshot())
    screen.resize(960, 640)
    screen.show()
    app.processEvents()
    try:
        from PySide6.QtCore import QPoint
        screen.inspector.set_tab("links")          # the campaign entry lives with the links, in the inspector
        app.processEvents()
        assert screen.run_campaign.isVisible() and screen.run_campaign.isEnabled()
        assert screen.link_table.isVisible() and screen.link_table.viewport().height() >= 50
        button_bottom = screen.run_campaign.mapTo(screen, QPoint(0, screen.run_campaign.height())).y()
        assert button_bottom <= screen.link_table.mapTo(screen, QPoint(0, 0)).y()
        assert screen.link_table.model().rowCount() == len(provider.snapshot().topology.links)
        evidence = os.environ.get("ORION_CAMPAIGN_UI_EVIDENCE")
        if evidence:
            assert screen.grab().save(str(Path(evidence) / "topology-entry-960.png"))
    finally:
        screen.close()
