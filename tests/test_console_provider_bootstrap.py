"""A real local service must finish startup before its first network tick.

Faults delay or fail actual reads; no snapshot, topology or engine result is
substituted. Each service owns a fresh temporary journal and idle input source.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import threading
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console.service_provider import ServiceProvider  # noqa: E402
from quantum_autopilot.control_plane.topology import default_simulated_topology  # noqa: E402
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, condition, timeout=30.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert condition()


def drain(app):
    """Deliver queued completions, including any obsolete bootstrap result."""
    deadline = time.monotonic() + .2
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)


def make_provider(tmp_path):
    service = AssuranceDashboardService(ROOT, control_journal_path=tmp_path / "state.sqlite")
    service.topology_store.initialize(default_simulated_topology())
    provider = ServiceProvider(service, state_root=tmp_path, poll_ms=60_000, step_period_s=.05)
    return service, provider


def assert_no_ticks(service, provider):
    assert not provider.stepping
    assert provider.steps_taken == 0
    if service._network_engine is not None:
        assert service._network_engine.live_state().tick == 0


def test_transient_topology_failure_retries_without_publishing_partial_ready_frame(app, tmp_path, monkeypatch):
    service, provider = make_provider(tmp_path)
    original = service.control_plane_topology
    retry_entered, release = threading.Event(), threading.Event()
    calls, errors, frames = [], [], []

    def topology():
        calls.append(threading.get_ident())
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        if len(calls) == 2:
            retry_entered.set()
            assert release.wait(15)
        return original()

    monkeypatch.setattr(service, "control_plane_topology", topology)
    provider.error.connect(errors.append)
    provider.snapshot_changed.connect(frames.append)
    try:
        provider.start()
        pump(app, retry_entered.is_set)
        assert errors == ["database is locked"]
        assert frames == []
        assert provider._snap is None
        assert provider._last_inputs is None
        assert_no_ticks(service, provider)
        release.set()
        pump(app, lambda: bool(frames) and provider.steps_taken >= 1)
        assert frames[0].topology.id == "sandbox-line-3"
        assert frames[0].topology.version == 0
        assert all(frame.topology.id == "sandbox-line-3" and frame.topology.version == 0 for frame in frames)
        assert len(calls) >= 2
    finally:
        release.set()
        provider.stop()


def test_network_waits_for_complete_authenticated_publication(app, tmp_path, monkeypatch):
    service, provider = make_provider(tmp_path)
    original = provider._prepare_publication
    prepared, release = threading.Event(), threading.Event()
    frames = []

    def prepare(*args, **kwargs):
        publication = original(*args, **kwargs)
        prepared.set()
        assert release.wait(15)
        return publication

    monkeypatch.setattr(provider, "_prepare_publication", prepare)
    provider.snapshot_changed.connect(frames.append)
    try:
        provider.start()
        pump(app, prepared.is_set)
        assert provider._snap.topology.id == "sandbox-line-3"
        assert provider._snap.topology.version == 0
        assert provider._snap.live.tick == 0
        assert provider._delivered_publication_sequence == 0
        assert frames == []
        assert_no_ticks(service, provider)
        release.set()
        pump(app, lambda: bool(frames) and provider.steps_taken >= 1)
        assert frames[0].live.tick == 0
    finally:
        release.set()
        provider.stop()


def test_stop_discards_completed_bootstrap_before_queued_delivery(app, tmp_path, monkeypatch):
    service, provider = make_provider(tmp_path)
    original = service.control_plane_topology
    entered, release = threading.Event(), threading.Event()
    frames = []

    def topology():
        entered.set()
        assert release.wait(15)
        return original()

    monkeypatch.setattr(service, "control_plane_topology", topology)
    provider.snapshot_changed.connect(frames.append)
    try:
        provider.start()
        assert entered.wait(15)
        future = provider._future
        assert_no_ticks(service, provider)
        release.set()
        # Do not pump Qt: the worker completion is queued but not admitted.
        future.result(timeout=15)
        assert frames == []
        provider.stop()
        drain(app)
        assert frames == []
        assert_no_ticks(service, provider)
        assert provider._executor is None
    finally:
        release.set()
        provider.stop()


def test_snapshot_listener_stop_cannot_restart_stepper(app, tmp_path):
    service, provider = make_provider(tmp_path)
    frames = []

    def close_after_bootstrap(frame):
        frames.append(frame)
        provider.stop()

    provider.snapshot_changed.connect(close_after_bootstrap)
    try:
        provider.start()
        pump(app, lambda: bool(frames))
        drain(app)
        assert len(frames) == 1
        assert frames[0].topology.id == "sandbox-line-3"
        assert_no_ticks(service, provider)
        assert provider._executor is None
    finally:
        provider.stop()


def test_revocation_during_bootstrap_prevents_publication_retry_and_ticks(app, tmp_path, monkeypatch):
    service, _ = make_provider(tmp_path)
    operator = service.security.grant_identity("service:bootstrap-operator", ["operator"], ["workspace:*"])
    with service.security.identity_scope(operator):
        provider = ServiceProvider(service, state_root=tmp_path, poll_ms=60_000, step_period_s=.05)
    original = service.control_plane_topology
    entered, release = threading.Event(), threading.Event()
    frames, errors = [], []

    def topology():
        entered.set()
        assert release.wait(15)
        return original()

    monkeypatch.setattr(service, "control_plane_topology", topology)
    provider.snapshot_changed.connect(frames.append)
    provider.error.connect(errors.append)
    try:
        provider.start()
        pump(app, entered.is_set)
        assert_no_ticks(service, provider)
        service.security.revoke_identity(operator.subject)
        release.set()
        pump(app, lambda: bool(errors) and provider._future is None)
        drain(app)
        assert frames == []
        assert provider._snap is None
        assert not provider._timer.isActive()
        assert not provider._live_timer.isActive()
        assert_no_ticks(service, provider)
        assert service.security.audit.verify()["valid"]
    finally:
        release.set()
        provider.stop()
