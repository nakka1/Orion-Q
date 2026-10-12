"""Native calls preserve identity and authorize cached/passive UI operations."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console.app import build_parser, build_service, build_demo_service  # noqa: E402
from quantum_autopilot.console.screens.boot import BootScreen  # noqa: E402
from quantum_autopilot.console.service_provider import ServiceProvider  # noqa: E402
from quantum_autopilot.security import AuthenticationError, AuthorizationError, SecurityRuntime  # noqa: E402
from quantum_autopilot.security.service_boundary import secure_service, service_resource  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@secure_service
class ControlledService:
    def __init__(self, root):
        self.control_journal_path = root / "sandbox.sqlite"
        self.security = SecurityRuntime(root / "security")
        self.steps = 0

    def health(self):
        return {"status": "ok"}

    def wdm_model_status(self):
        return {"configured": False}

    def network_live_state(self):
        return {"source": {"kind": "none"}}

    def network_step(self):
        self.steps += 1
        return {"step": self.steps}

    def network_command(self, verb, payload):
        return {"verb": verb}


def test_worker_does_not_elevate_auditor_to_os_admin(app, tmp_path):
    service = ControlledService(tmp_path)
    auditor = service.security.grant_identity("service:auditor", ["auditor"], ["workspace:*"])
    with service.security.identity_scope(auditor):
        provider = ServiceProvider(service)
    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(provider._run_worker, service.network_step)
        with pytest.raises(AuthorizationError):
            future.result(timeout=5)
    assert service.steps == 0
    assert service.security.local_identity().roles == frozenset({"administrator"})


def test_operator_worker_revalidates_revocation_and_records_identity(app, tmp_path):
    service = ControlledService(tmp_path)
    operator = service.security.grant_identity("service:operator", ["operator"], ["workspace:*"])
    with service.security.identity_scope(operator):
        provider = ServiceProvider(service)
    assert provider._run_worker(service.network_step)["step"] == 1
    service.security.revoke_identity(operator.subject)
    with pytest.raises(AuthenticationError):
        provider._run_worker(service.network_step)
    assert service.steps == 1
    assert service.security.audit.verify()["valid"]


def test_cached_ui_export_requires_permission_before_creating_file(app, tmp_path):
    service = ControlledService(tmp_path)
    auditor = service.security.grant_identity("service:auditor", ["auditor"], ["workspace:*"])
    with service.security.identity_scope(auditor):
        provider = ServiceProvider(service, state_root=tmp_path / "exports")
    with pytest.raises(AuthorizationError):
        provider.export_record("cached")
    assert not (tmp_path / "exports").exists()


def test_anonymous_native_context_is_denied_and_not_replaced(app, tmp_path):
    service = ControlledService(tmp_path)
    provider = ServiceProvider(service)
    with service.security.identity_scope(None), pytest.raises(AuthenticationError):
        provider.dispatch("select_node", "cached")
    assert service.security.authorize("network.read", service_resource(service)).authenticated


def test_source_switches_share_identity_policy_and_skip_auth_does_not_bypass(app, tmp_path):
    args = build_parser().parse_args(["--skip-auth", "--state-root", str(tmp_path)])
    args.repository_root = Path(__file__).resolve().parents[1]
    args.control_journal = tmp_path / "sandbox.sqlite"
    normal = build_service(args)
    demonstration = build_demo_service(args)
    assert normal.security is demonstration.security is args._security
    assert normal.security.local_identity().subject.startswith("os:")
    assert args.skip_auth is True
    with normal.security.identity_scope(None), pytest.raises(AuthenticationError):
        normal.health()


def test_boot_reports_backend_identity_and_keeps_physical_locked(app, tmp_path):
    from quantum_autopilot.console.providers import MockProvider
    service = ControlledService(tmp_path)
    snapshot = MockProvider().snapshot()
    snapshot.security = service.security.snapshot()
    snapshot.operator = snapshot.security["display_name"]
    boot = BootScreen()
    boot.refresh(snapshot)
    assert snapshot.security["subject"] in boot.identity_note.text()
    assert "administrator" in boot.identity_note.text()
    assert ("Physical", "LOCKED") in boot.boundary_values()
    boot.deleteLater()


def _deliver_until(app, condition):
    deadline = time.monotonic() + 8
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert condition()


def test_streaming_frame_commits_all_authorizations_on_worker_before_delivery(app, tmp_path, monkeypatch):
    from quantum_autopilot.console.providers import MockProvider
    from quantum_autopilot.console.source_switch import SwitchableProvider

    service = ControlledService(tmp_path)
    provider = ServiceProvider(service)
    provider._snap = MockProvider().snapshot()
    switchable = SwitchableProvider(provider)
    switchable.running = True
    ui_thread = threading.get_ident()
    commits, received = [], []
    original = service.security.audit._append

    def append(rows):
        commits.append(threading.get_ident())
        return original(rows)

    monkeypatch.setattr(service.security.audit, "_append", append)

    def delivered(snapshot):
        verified = service.security.audit.verify()
        received.append((snapshot, verified))

    switchable.snapshot_changed.connect(delivered)
    with ThreadPoolExecutor(1) as executor:
        provider._executor = executor
        assert provider._schedule(lambda: None, background=True)
        _deliver_until(app, lambda: bool(received) and provider._future is None)
        provider._executor = None
    assert commits and all(thread != ui_thread for thread in commits)
    assert received[0][1]["valid"] and received[0][1]["uncheckpointed_records"] == 0
    assert received[0][1]["count"] == 4  # publication, capabilities, health and model status
    # The public SDK read remains authenticated even though Qt forwards a frame.
    with service.security.identity_scope(None), pytest.raises(AuthenticationError):
        provider.snapshot()


def test_revocation_before_worker_publication_prevents_frame_delivery(app, tmp_path):
    from quantum_autopilot.console.providers import MockProvider

    service = ControlledService(tmp_path)
    operator = service.security.grant_identity("service:frame-operator", ["operator"], ["workspace:*"])
    with service.security.identity_scope(operator):
        provider = ServiceProvider(service)
    provider._snap = MockProvider().snapshot()
    started, release = threading.Event(), threading.Event()
    frames, errors = [], []
    provider.snapshot_changed.connect(frames.append)
    provider.error.connect(errors.append)

    def blocked_read():
        started.set()
        assert release.wait(5)

    with ThreadPoolExecutor(1) as executor:
        provider._executor = executor
        assert provider._schedule(blocked_read, background=True)
        assert started.wait(5)
        service.security.revoke_identity(operator.subject)
        release.set()
        _deliver_until(app, lambda: provider._future is None and bool(errors))
        provider._executor = None
    assert not frames


def test_campaign_frame_presentation_does_not_requery_backend(app):
    from quantum_autopilot.console.campaign_job import CampaignJob
    from quantum_autopilot.console.providers import MockProvider

    class PresentationProvider:
        active = object()
        _generation = 0

        @property
        def capabilities(self):
            pytest.fail("rendering an authorized frame must not query a backend")

    job = CampaignJob(PresentationProvider())
    try:
        job.refresh_source(MockProvider().snapshot())
    finally:
        job.shutdown()


def test_live_read_during_pending_command_keeps_qt_heartbeat_and_commits_on_worker(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer, Qt
    from quantum_autopilot.console.providers import MockProvider
    from quantum_autopilot.console.responsive_validation import run_event_loop

    service = ControlledService(tmp_path)
    provider = ServiceProvider(service)
    provider._snap = MockProvider().snapshot()
    provider._last_inputs = ({"status": "ok"}, {"topology": {}}, [])
    # This fixture represents an already delivered initial full read; live-only
    # publication must not serve as the application's startup completion.
    provider._delivered_publication_sequence = 1
    ui_thread = threading.get_ident()
    commits, frames, gaps = [], [], []
    original_append = service.security.audit._append

    def slow_disk(rows):
        commits.append(threading.get_ident())
        time.sleep(.3)  # Local fault injection: disk commit slower than the UI smoke bound.
        return original_append(rows)

    monkeypatch.setattr(service.security.audit, "_append", slow_disk)
    started, release = threading.Event(), threading.Event()

    def command():
        started.set()
        assert release.wait(5)
        return service.network_command("observe", {})

    provider.snapshot_changed.connect(frames.append)
    heartbeat = QTimer()
    heartbeat.setTimerType(Qt.TimerType.PreciseTimer)
    heartbeat.setInterval(10)
    last = [None]

    def beat():
        now = time.perf_counter()
        if last[0] is not None:
            gaps.append(1000 * (now - last[0]))
        last[0] = now

    heartbeat.timeout.connect(beat)
    with ThreadPoolExecutor(1) as serial, ThreadPoolExecutor(1) as reads:
        provider._executor, provider._live_executor = serial, reads
        try:
            assert provider._schedule(command)
            assert started.wait(5)
            provider._publish_live_only()
            pending = provider._live_future
            provider._publish_live_only()
            assert pending is not None and provider._live_future is pending
            heartbeat.start()
            run_event_loop(1100)
            heartbeat.stop()
            assert frames and frames[0].live is not None
            assert len(gaps) >= 10 and max(gaps) < 250
            assert commits and all(thread != ui_thread for thread in commits)
        finally:
            heartbeat.stop()
            release.set()
            _deliver_until(app, lambda: provider._future is None)
            provider.stop()
    assert service.security.audit.verify()["uncheckpointed_records"] == 0


@pytest.mark.parametrize("widget", ["command_history", "policy_context", "policy_review"])
def test_first_frame_source_reset_does_not_reenter_backend_from_cleared_jobs(app, monkeypatch, widget):
    from dataclasses import replace
    from quantum_autopilot.console.capabilities import SourceCapabilities
    from quantum_autopilot.console.providers import MockProvider
    from quantum_autopilot.console.source_switch import SwitchableProvider
    from quantum_autopilot.console.widgets.command_history import CommandHistory
    from quantum_autopilot.console.widgets.policy_context import PolicyContextInspection
    from quantum_autopilot.console.widgets.policy_review import PolicyReview

    classes = {
        "command_history": CommandHistory,
        "policy_context": PolicyContextInspection,
        "policy_review": PolicyReview,
    }
    source = SwitchableProvider(MockProvider())
    source.start()  # A committed capability frame is usable only for a running source.
    view = classes[widget]()
    view.attach(source)
    generations = view.job.generation, view.export_job.generation

    def unexpected_read(_):
        pytest.fail("a source reset already has the committed frame; job.clear must not requery capabilities")

    monkeypatch.setattr(SwitchableProvider, "capabilities", property(unexpected_read))
    capabilities = SourceCapabilities(
        adapter_id="committed-test-frame", mode="simulation", health="ready",
        operations=("read_console_commands", "observe_policy_context", "review_policy_preparation"),
    )
    snapshot = replace(MockProvider().snapshot(), capabilities=capabilities)
    try:
        view._snapshot(snapshot)
        assert view._source[2] is True
        assert (view.job.generation, view.export_job.generation) == tuple(value + 1 for value in generations)
        assert view.job.result is None and view.export_job.result is None
    finally:
        source.stop()
        view.deleteLater()


@pytest.mark.parametrize("widget", ["policy_context", "policy_review"])
def test_ready_frame_cannot_enable_policy_actions_while_source_is_stopped(app, monkeypatch, widget):
    from dataclasses import replace
    from quantum_autopilot.console.capabilities import SourceCapabilities
    from quantum_autopilot.console.providers import MockProvider
    from quantum_autopilot.console.source_switch import SwitchableProvider
    from quantum_autopilot.console.widgets.policy_context import PolicyContextInspection
    from quantum_autopilot.console.widgets.policy_review import PolicyReview

    source = SwitchableProvider(MockProvider())
    view = {"policy_context": PolicyContextInspection, "policy_review": PolicyReview}[widget]()
    view.attach(source)
    view.show()
    app.processEvents()

    def unexpected_read(_):
        pytest.fail("presenting a stopped source must not query backend capabilities")

    monkeypatch.setattr(SwitchableProvider, "capabilities", property(unexpected_read))
    capabilities = SourceCapabilities(
        adapter_id="stopped-test-frame", mode="simulation", health="ready",
        operations=("observe_policy_context", "review_policy_preparation"),
    )
    snapshot = replace(MockProvider().snapshot(), capabilities=capabilities)
    try:
        view._snapshot(snapshot)
        assert not source.running and view._source[2] is False
        assert not view.load_button.isEnabled()
        assert view.job.result is None and view.export_job.result is None
        assert view.binding is None and view.context is None
    finally:
        view.close()
        source.stop()
        view.deleteLater()


def test_first_native_frame_and_busy_callbacks_only_render_committed_capabilities(app, tmp_path, monkeypatch):
    from quantum_autopilot.console.providers import MockProvider
    from quantum_autopilot.console.shell import ConsoleWindow

    service = ControlledService(tmp_path)
    provider = ServiceProvider(service)
    provider._snap = MockProvider().snapshot()
    window = ConsoleWindow(provider, state_root=tmp_path)
    window.provider.running = True
    ui_thread = threading.get_ident()
    reads, commits = [], []
    original_get = ServiceProvider.capabilities.fget
    original_append = service.security.audit._append

    def capabilities(current):
        reads.append(threading.get_ident())
        return original_get(current)

    def append(rows):
        commits.append(threading.get_ident())
        return original_append(rows)

    monkeypatch.setattr(ServiceProvider, "capabilities", property(capabilities))
    monkeypatch.setattr(service.security.audit, "_append", append)
    try:
        with ThreadPoolExecutor(1) as executor:
            provider._executor = executor
            assert provider._schedule(lambda: None)
            _deliver_until(app, lambda: window.snapshot is not None and provider._future is None)
            provider._executor = None
        assert reads and all(thread != ui_thread for thread in reads)
        assert commits and all(thread != ui_thread for thread in commits)
        assert window.campaign_job.presentation_capabilities == window.snapshot.capabilities
        verified = service.security.audit.verify()
        assert verified["valid"] and verified["uncheckpointed_records"] == 0
        assert verified["count"] == 4
        # A presentation declaration never grants a new SDK invocation access.
        with service.security.identity_scope(None), pytest.raises(AuthenticationError):
            window.campaign_job.history_allowed
    finally:
        window.close()


@pytest.mark.parametrize("close_before_start", [False, True])
def test_native_source_starts_once_in_the_event_after_first_paint(app, tmp_path, monkeypatch, close_before_start):
    from PySide6.QtCore import QTimer
    from quantum_autopilot.console import app as entry
    from quantum_autopilot.console.providers import MockProvider

    args = entry.build_parser().parse_args(["--provider", "mock", "--state-root", str(tmp_path)])
    events = []
    monitor = type("Monitor", (), {})()
    original_start = MockProvider.start

    def attach(application, window):
        monitor.window = window
        def painted():
            events.append("painted")
            if close_before_start:
                window.close()
                QTimer.singleShot(50, application, application.quit)
        window.centralWidget().first_frame_painted.connect(painted)
        monitor.deadline = QTimer(application)
        monitor.deadline.setSingleShot(True)
        monitor.deadline.timeout.connect(application.quit)
        monitor.deadline.start(5000)

    monitor.attach = attach

    def start(provider):
        events.append("started")
        assert monitor.window.centralWidget()._first_frame_painted
        assert events == ["painted", "started"]
        original_start(provider)
        monitor.window.centralWidget().viewport().update()  # A second paint cannot start another source.
        QTimer.singleShot(100, monitor.window, lambda: (monitor.window.close(), app.quit()))

    monkeypatch.setattr(MockProvider, "start", start)
    try:
        assert entry.run(args, startup_monitor=monitor) == 0
        assert events == (["painted"] if close_before_start else ["painted", "started"])
        assert args._security.local_identity().authenticated
        assert args._security.audit.verify()["valid"]
    finally:
        monitor.deadline.stop()
        monitor.window.close()
