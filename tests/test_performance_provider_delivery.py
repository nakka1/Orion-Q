"""Selection never runs SDK/audit I/O in Qt or loses deferred authorization."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console.service_provider import ServiceProvider  # noqa: E402
from quantum_autopilot.security import AuthenticationError, AuthorizationError, SecurityRuntime  # noqa: E402
from tests.test_console_service_provider import FakeService  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def provider_for(tmp_path, identity=None):
    service = FakeService()
    service.control_journal_path = tmp_path / "sandbox.sqlite"
    service.security = SecurityRuntime(tmp_path / "security")
    if identity:
        identity = service.security.grant_identity(identity, ["operator"], ["workspace:*"])
        with service.security.identity_scope(identity):
            provider = ServiceProvider(service)
    else:
        provider = ServiceProvider(service)
    provider.refresh()
    return provider, identity


def deliver_until(app, condition):
    deadline = time.monotonic() + 10
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)
    app.processEvents()
    assert condition()


@contextmanager
def blocked_selection_worker(provider):
    release, entered = threading.Event(), threading.Event()

    def gate():
        entered.set()
        assert release.wait(10)

    with ThreadPoolExecutor(1, thread_name_prefix="test-service") as executor, \
            ThreadPoolExecutor(1, thread_name_prefix="test-selection") as selection:
        provider._executor, provider._selection_executor = executor, selection
        selection.submit(gate)
        assert entered.wait(5)
        try:
            yield release
        finally:
            release.set()
            provider._executor = provider._selection_executor = None


def test_async_selection_keeps_latest_node_and_all_durable_audits_off_qt(app, tmp_path, monkeypatch):
    provider, _ = provider_for(tmp_path)
    frames, commits = [], []
    provider.snapshot_changed.connect(frames.append)
    original = provider.security.audit._append

    def append(rows):
        commits.append(threading.get_ident())
        return original(rows)

    monkeypatch.setattr(provider.security.audit, "_append", append)
    before = provider.security.audit.verify()["count"]
    qt_thread = threading.get_ident()
    with blocked_selection_worker(provider) as release:
        for target in ("alice", "repeater-a", "bob"):
            assert provider.dispatch("select_node", target)
        # Dispatch remains responsive even though no worker can progress yet.
        assert not commits and not frames
        assert provider._presentation_stats["selection_jobs_pending"] == 3
        release.set()
        deliver_until(app, lambda: not provider._selection_futures)
    assert frames and all(frame.node.id == "bob" for frame in frames)
    assert provider._selected_node == "bob"
    assert provider._presentation_stats["coalesced_selection_frames"] == 2
    assert provider._presentation_stats["selection_jobs_pending"] == 0
    verified = provider.security.audit.verify()
    assert verified["valid"] and verified["uncheckpointed_records"] == 0
    assert verified["count"] >= before + 15
    assert commits and all(thread != qt_thread for thread in commits)


@pytest.mark.parametrize("change", ["revoke", "scope"])
def test_selection_rechecks_captured_principal_after_queueing(app, tmp_path, change):
    provider, operator = provider_for(tmp_path, "service:queued-operator")
    frames, errors = [], []
    provider.snapshot_changed.connect(frames.append)
    provider.error.connect(errors.append)
    original_selection = provider._selected_node
    with blocked_selection_worker(provider) as release:
        assert provider.dispatch("select_node", "bob")
        if change == "revoke":
            provider.security.revoke_identity(operator.subject)
        else:
            provider.security.grant_identity(operator.subject, ["auditor"], ["workspace:somewhere-else"])
        release.set()
        deliver_until(app, lambda: not provider._selection_futures)
    assert errors and not frames
    assert provider._selected_node == original_selection
    verified = provider.security.audit.verify()
    assert verified["valid"] and verified["uncheckpointed_records"] == 0
    rows = [json.loads(line) for line in provider.security.audit.path.read_text().splitlines()]
    assert any(row["result"] == "denied" for row in rows)


def test_selection_queue_backpressure_is_explicit_without_dropping_accepted_jobs(app, tmp_path):
    provider, _ = provider_for(tmp_path)
    errors = []
    provider.error.connect(errors.append)
    before = provider.security.audit.verify()["count"]
    with blocked_selection_worker(provider) as release:
        for _ in range(16):
            assert provider.dispatch("select_node", "bob")
        assert not provider.dispatch("select_node", "alice")
        assert errors == ["Selection queue is full; retry after pending inspections complete."]
        assert provider._presentation_stats["selection_queue_rejections"] == 1
        assert provider._selection_requested_node == "bob"
        release.set()
        deliver_until(app, lambda: not provider._selection_futures)
    assert provider._selected_node == "bob"
    assert provider.security.audit.verify()["count"] >= before + 16 * 5
    assert provider.security.audit.verify()["uncheckpointed_records"] == 0


def test_anonymous_selection_never_enters_queue_and_direct_calls_remain_guarded(app, tmp_path):
    provider, _ = provider_for(tmp_path)
    with blocked_selection_worker(provider):
        with provider.security.identity_scope(None), pytest.raises(AuthenticationError):
            provider.dispatch("select_node", "bob")
        assert not provider._selection_futures
    auditor = provider.security.grant_identity("service:direct-auditor", ["auditor"], ["workspace:*"])
    with provider.security.identity_scope(auditor):
        assert provider.dispatch("select_node", "bob")  # permitted cached read
        with pytest.raises(AuthorizationError):
            provider.dispatch("isolate", "bob")
    assert provider._selected_node == "bob"
    assert provider.security.audit.verify()["valid"]


@pytest.mark.parametrize("verb", ("select_node", "reload", "isolate"))
def test_explicit_anonymous_async_admission_never_reads_vault_or_enqueues(app, tmp_path, monkeypatch, verb):
    provider, _ = provider_for(tmp_path)
    with blocked_selection_worker(provider):
        with monkeypatch.context() as patches:
            patches.setattr(provider.security.vault, "_read", lambda:
                            pytest.fail("anonymous presentation admission read the vault on Qt"))
            with provider.security.identity_scope(None), pytest.raises(AuthenticationError):
                provider.dispatch(verb, "bob")
        assert not provider._selection_futures
        assert provider._future is None
        assert provider._presentation_stats["selection_jobs_pending"] == 0
        assert provider._selection_generation == 0


def test_async_callback_without_scope_keeps_captured_operator_in_every_audit(app, tmp_path):
    provider, operator = provider_for(tmp_path, "service:callback-operator")
    assert not provider.security.has_identity_scope()
    before = provider.security.audit.verify()["count"]
    with blocked_selection_worker(provider) as release:
        assert provider.dispatch("select_node", "bob")
        release.set()
        deliver_until(app, lambda: not provider._selection_futures)
    assert provider._selected_node == "bob"
    rows = [json.loads(line) for line in provider.security.audit.path.read_text().splitlines()][before:]
    assert len(rows) >= 5
    assert all(row["identity"] == operator.subject for row in rows)
    assert provider.security.audit.verify()["valid"]


def test_legacy_service_without_metadata_keyword_remains_supported(app, tmp_path):
    provider, _ = provider_for(tmp_path)

    class Legacy:
        def network_live_state(self):
            return {"source": {"kind": "none"}, "tick": 8}

        def network_step(self):
            return {"source": {"kind": "none"}, "tick": 9}

    provider.service = Legacy()
    assert provider._network_metadata()["tick"] == 8
    assert provider._network_step()["tick"] == 9


def test_stop_finishes_accepted_selections_and_their_audits(app, tmp_path):
    provider, _ = provider_for(tmp_path)
    before = provider.security.audit.verify()["count"]
    with blocked_selection_worker(provider) as release:
        for target in ("alice", "repeater-a", "bob"):
            assert provider.dispatch("select_node", target)
        accepted = tuple(provider._selection_futures)
        release.set()
        provider.stop()
    app.processEvents()
    assert all(future.done() and not future.cancelled() for future in accepted)
    assert provider._selected_node == "bob"
    assert not provider._selection_futures
    verified = provider.security.audit.verify()
    assert verified["valid"] and verified["uncheckpointed_records"] == 0
    assert verified["count"] >= before + 15
