"""Live presentation cannot depend on a blocked full evidence read."""
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from quantum_autopilot.console.service_provider import ServiceProvider
from tests.test_console_service_provider import FakeService
from tests.test_console_publication_order import committed_fixture, place_cache
from tests.test_security_console import ControlledService
from quantum_autopilot.security import AuthenticationError


def settle(app, condition):
    limit = time.monotonic() + 5
    while not condition() and time.monotonic() < limit:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert condition()


def test_live_tick_delivered_while_full_evidence_worker_is_blocked(monkeypatch):
    app = QApplication.instance() or QApplication([])
    provider = ServiceProvider(FakeService())
    snap, inputs = committed_fixture(provider, 10, 1)
    place_cache(provider, snap, inputs)
    provider.has_network_engine = True
    provider._future = Future()  # A full read has started, but has not finished.
    provider._future_background = True
    provider._delivered_publication_sequence = 1  # The initial full frame is already shown.
    provider._live_executor = ThreadPoolExecutor(1)
    state = {"tick": 11, "at_utc": "2026-10-07T12:00:02+00:00"}
    monkeypatch.setattr(provider.service, "network_live_state", lambda: state, raising=False)
    monkeypatch.setattr("quantum_autopilot.console.service_provider.live_view_from_state",
                        lambda *_: replace(snap.live, tick=11, at=state["at_utc"]))
    monkeypatch.setattr(provider, "_topology", lambda *_: snap.topology)
    monkeypatch.setattr(provider, "_counts", lambda *_: snap.counts)
    received = []
    provider.snapshot_changed.connect(received.append)
    try:
        provider._publish_live_only()
        settle(app, lambda: bool(received))
        assert received[-1].live.tick == 11
        assert not provider._future.done()
        assert not provider._foreground_busy
    finally:
        provider._future = None
        provider.stop()


def test_unchanged_tick_uses_only_metadata_and_does_not_copy_scientific_arrays(monkeypatch):
    app = QApplication.instance() or QApplication([])
    provider = ServiceProvider(FakeService())
    snap, inputs = committed_fixture(provider, 10, 1)
    place_cache(provider, snap, inputs)
    provider.service.network_metadata_supported = True
    calls = []
    def read(*, metadata_only=False):
        calls.append(metadata_only)
        assert metadata_only, "unchanged state should not serialize all observations"
        return {"tick": snap.live.tick, "at_utc": snap.live.at}
    monkeypatch.setattr(provider.service, "network_live_state", read, raising=False)
    assert provider._collect_live_publication(None) is None
    assert calls == [True]
    provider.deleteLater()
    app.processEvents()


def test_initial_full_frame_finishes_before_live_delivery_is_admitted(monkeypatch):
    app = QApplication.instance() or QApplication([])
    provider = ServiceProvider(FakeService())
    snap, inputs = committed_fixture(provider, 10, 1)
    place_cache(provider, snap, inputs)
    provider.has_network_engine = True
    provider._foreground_busy = True
    provider._future = Future()
    class UnusedExecutor:
        def submit(self, *args):
            pytest.fail("partial delivery started before the first full frame completed")
    provider._live_executor = UnusedExecutor()
    provider._publish_live_only()
    assert provider._live_future is None
    provider._live_executor, provider._future = None, None
    provider.deleteLater()
    app.processEvents()


def test_periodic_full_read_and_live_read_have_independent_cadence(monkeypatch):
    app = QApplication.instance() or QApplication([])
    provider = ServiceProvider(FakeService())
    snap, inputs = committed_fixture(provider, 10, 1)
    place_cache(provider, snap, inputs)
    provider.has_network_engine = True
    provider._last_full_poll_at = time.monotonic()
    full, live = [], []
    monkeypatch.setattr(provider, "_schedule", lambda *a, **k: full.append(k) or True)
    monkeypatch.setattr(provider, "_publish_live_only", lambda: live.append(True))
    provider._poll_presentation()
    assert not full and live == [True]
    provider._last_full_poll_at = 0
    provider._poll_presentation()
    assert full == [{"background": True}] and len(live) == 2
    provider.deleteLater()
    app.processEvents()


def test_async_click_captures_actor_without_vault_io_and_worker_revalidates(monkeypatch, tmp_path):
    import threading
    app = QApplication.instance() or QApplication([])
    service = ControlledService(tmp_path)
    actor = service.security.grant_identity("service:click-operator", ["operator"], ["workspace:*"])
    with service.security.identity_scope(actor):
        provider = ServiceProvider(service)
        snap, inputs = committed_fixture(provider, 10, 1)
        snap = replace(snap, capabilities=provider.capabilities)
        place_cache(provider, snap, inputs)
    captured = []
    provider._executor = object()  # Explicit fixture captures the accepted job, never executes it on Qt.
    monkeypatch.setattr(provider, "_schedule_selection", lambda callback, *a: captured.append(callback) or True)
    gui_thread = threading.get_ident()
    original = service.security.vault.get_bytes
    def read(key):
        assert threading.get_ident() != gui_thread, "interactive admission accessed the vault"
        return original(key)
    with service.security.identity_scope(actor):
        monkeypatch.setattr(service.security.vault, "get_bytes", read)
        assert provider.dispatch("select_node", "A")
    monkeypatch.setattr(service.security.vault, "get_bytes", original)
    service.security.revoke_identity(actor.subject)
    with ThreadPoolExecutor(1) as worker:
        with pytest.raises(AuthenticationError):
            worker.submit(provider._run_worker, captured[0], actor).result(5)
    provider._executor = None
    provider.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("new_static_read", [False, True])
def test_live_formatting_does_not_lock_qt_and_cannot_mix_static_readbacks(monkeypatch, new_static_read):
    import threading
    app = QApplication.instance() or QApplication([])
    provider = ServiceProvider(FakeService())
    snap, inputs = committed_fixture(provider, 10, 1)
    place_cache(provider, snap, inputs)
    entered, release = threading.Event(), threading.Event()
    state = {"tick": 11, "at_utc": "2026-10-07T12:00:02+00:00"}
    monkeypatch.setattr(provider.service, "network_live_state", lambda: state, raising=False)
    def format_live(*_):
        entered.set()
        assert release.wait(3)
        return replace(snap.live, tick=11, at=state["at_utc"])
    monkeypatch.setattr("quantum_autopilot.console.service_provider.live_view_from_state", format_live)
    monkeypatch.setattr(provider, "_topology", lambda *_: snap.topology)
    monkeypatch.setattr(provider, "_counts", lambda *_: snap.counts)
    with ThreadPoolExecutor(1) as executor:
        pending = executor.submit(provider._collect_live_publication, None)
        try:
            assert entered.wait(3)
            assert provider._snap_lock.acquire(blocking=False), "Qt dispatch would block on history formatting"
            provider._snap_lock.release()
            if new_static_read:
                newer, new_inputs = committed_fixture(provider, 12, 2)
                place_cache(provider, newer, new_inputs)
        finally:
            release.set()
        publication = pending.result(5)
    if new_static_read:
        assert publication is None and provider._snap.live.tick == 12
    else:
        assert publication.snapshot.live.tick == 11
    provider.deleteLater()
    app.processEvents()


def test_anonymous_async_caller_is_never_replaced_by_the_provider_identity(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = ControlledService(tmp_path)
    provider = ServiceProvider(service)
    with ThreadPoolExecutor(1) as executor:
        provider._executor = executor
        with service.security.identity_scope(None):
            assert provider._schedule(service.network_step)
        with pytest.raises(AuthenticationError):
            provider._future.result(5)
        with pytest.raises(AuthenticationError):
            executor.submit(provider._run_worker, service.network_step, None).result(5)
        assert service.steps == 0
        provider._executor, provider._future = None, None
    provider.deleteLater()
    app.processEvents()
