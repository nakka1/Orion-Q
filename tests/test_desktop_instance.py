"""Real Windows kernel-object and desktop entry-point exclusion tests."""

import multiprocessing
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call
from uuid import uuid4

import pytest

from quantum_autopilot import desktop_instance


windows = pytest.mark.skipif(os.name != "nt", reason="Windows global named objects")


@pytest.fixture
def namespace():
    # Never interfere with an operator's normal application or an old release.
    return "ORION-Q.Desktop.Test." + uuid4().hex


def _guard_worker(pipe, namespace, directory, state_root, barrier=None):
    os.chdir(directory)
    if barrier is not None:
        barrier.wait(timeout=20)
    with desktop_instance.acquire_desktop_instance(_namespace=namespace) as guard:
        signaled = False if guard.is_primary else guard.request_activation()
        pipe.send((guard.is_primary, signaled, str(Path.cwd()), str(state_root)))
        if guard.is_primary:
            while True:
                instruction = pipe.recv()
                if instruction == "stop":
                    break
                if instruction == "consume":
                    pipe.send(guard.consume_activation())
    pipe.close()


def _start_worker(namespace, directory, state_root, barrier=None):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_guard_worker, args=(child, namespace, directory, state_root, barrier))
    process.start()
    child.close()
    return process, parent


def _receive(pipe):
    assert pipe.poll(25), "the child must acquire or decline the mutex without hanging"
    return pipe.recv()


def _cleanup(workers):
    for process, pipe in workers:
        if process.is_alive():
            try:
                pipe.send("stop")
            except (BrokenPipeError, EOFError, OSError):
                pass
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        pipe.close()


@windows
def test_same_thread_cannot_recursively_create_a_second_desktop(namespace):
    with desktop_instance.acquire_desktop_instance(_namespace=namespace) as primary:
        assert primary.is_primary
        with desktop_instance.acquire_desktop_instance(_namespace=namespace) as secondary:
            assert not secondary.is_primary
            assert secondary.request_activation()
        assert primary.consume_activation()
        assert not primary.consume_activation()
    with desktop_instance.acquire_desktop_instance(_namespace=namespace) as reopened:
        assert reopened.is_primary
        assert not reopened.consume_activation()


@windows
def test_simultaneous_processes_have_one_owner_across_cwd_and_state_roots(namespace, tmp_path):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(6)
    workers = []
    try:
        for index in range(6):
            directory = tmp_path / f"copy-{index}"
            directory.mkdir()
            workers.append(_start_worker(namespace, directory, directory / f"state-{index}", barrier))
        results = [_receive(pipe) for _, pipe in workers]
        owners = [index for index, result in enumerate(results) if result[0]]
        assert len(owners) == 1
        assert all(result[0] or result[1] for result in results)
        assert len({result[2] for result in results}) == 6
        assert len({result[3] for result in results}) == 6
        owner_pipe = workers[owners[0]][1]
        owner_pipe.send("consume")
        assert _receive(owner_pipe) is True
        owner_pipe.send("consume")
        assert _receive(owner_pipe) is False
    finally:
        _cleanup(workers)
    assert all(process.exitcode == 0 for process, _ in workers)


@windows
def test_crash_releases_machine_ownership_without_removing_a_file(namespace, tmp_path):
    process, pipe = _start_worker(namespace, tmp_path, tmp_path / "state")
    try:
        assert _receive(pipe)[0]
        process.kill()
        process.join(timeout=10)
        assert not process.is_alive()
        with desktop_instance.acquire_desktop_instance(_namespace=namespace) as recovered:
            assert recovered.is_primary
            assert not recovered.consume_activation()
    finally:
        _cleanup([(process, pipe)])


@windows
def test_abandoned_mutex_recovers_even_while_another_handle_is_open(namespace, tmp_path):
    process, pipe = _start_worker(namespace, tmp_path, tmp_path / "state")
    api = desktop_instance._WindowsApi()
    held = None
    try:
        assert _receive(pipe)[0]
        held = api.kernel.CreateMutexW(None, False, "Global\\" + namespace + ".Owner")
        assert held
        process.kill()
        process.join(timeout=10)
        # ERROR_ALREADY_EXISTS alone would wrongly decline this abandoned owner.
        with desktop_instance.acquire_desktop_instance(_namespace=namespace) as recovered:
            assert recovered.is_primary
    finally:
        if held:
            api.kernel.CloseHandle(held)
        _cleanup([(process, pipe)])


@windows
def test_activation_survives_loading_and_restores_on_qt_thread(namespace, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QThread, Qt
    from PySide6.QtWidgets import QApplication, QMainWindow

    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window.resize(640, 480)
    window.showMaximized()
    app.processEvents()
    window.showMinimized()
    app.processEvents()
    assert window.isMinimized()
    received_threads = []
    try:
        with desktop_instance.acquire_desktop_instance(_namespace=namespace) as primary:
            with desktop_instance.acquire_desktop_instance(_namespace=namespace) as secondary:
                # A request comes before the window/timer are attached.
                assert secondary.request_activation()
            original_consume = primary.consume_activation

            def consume():
                result = original_consume()
                if result:
                    received_threads.append((threading.get_ident(), QThread.currentThread()))
                return result

            monkeypatch.setattr(primary, "consume_activation", consume)
            primary.attach_qt_window(window)
            deadline = time.monotonic() + 3
            while window.isMinimized() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            assert window.isVisible()
            assert not window.isMinimized()
            assert window.windowState() & Qt.WindowState.WindowMaximized
            assert received_threads == [(threading.get_ident(), app.thread())]
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


@windows
def test_activation_retries_when_owner_event_has_not_been_created_yet(namespace, monkeypatch):
    api = desktop_instance._WindowsApi()
    secondary = desktop_instance.DesktopInstance(namespace, api)
    actual_open = api.kernel.OpenEventW
    calls = []
    event = None

    def delayed_open(*args):
        nonlocal event
        calls.append(True)
        if len(calls) == 3:
            event = api.kernel.CreateEventW(None, False, False, secondary._event_name)
        return actual_open(*args)

    monkeypatch.setattr(api.kernel, "OpenEventW", delayed_open)
    try:
        assert secondary.request_activation(timeout=1)
        assert len(calls) == 3
        assert api.kernel.WaitForSingleObject(event, 0) == 0
    finally:
        if event:
            api.kernel.CloseHandle(event)
        secondary.close()


@windows
def test_access_denied_never_falls_back_to_local_or_per_user_mutex(namespace, monkeypatch):
    kernel = SimpleNamespace(CreateMutexW=Mock(return_value=None), WaitForSingleObject=Mock())
    api = SimpleNamespace(kernel=kernel, error=lambda _: desktop_instance.DesktopInstanceError(5, "denied"))
    monkeypatch.setattr(desktop_instance, "_WindowsApi", lambda: api)
    with pytest.raises(desktop_instance.DesktopInstanceError, match="denied"):
        desktop_instance.acquire_desktop_instance(_namespace=namespace)
    kernel.CreateMutexW.assert_called_once_with(None, False, "Global\\" + namespace + ".Owner")
    kernel.WaitForSingleObject.assert_not_called()


@windows
def test_event_creation_failure_releases_owned_mutex_and_fails_closed(namespace, monkeypatch):
    kernel = SimpleNamespace(
        CreateMutexW=Mock(return_value=1), WaitForSingleObject=Mock(return_value=0),
        CreateEventW=Mock(return_value=None), ReleaseMutex=Mock(), CloseHandle=Mock())
    api = SimpleNamespace(kernel=kernel, error=lambda _: desktop_instance.DesktopInstanceError(5, "denied"))
    monkeypatch.setattr(desktop_instance, "_WindowsApi", lambda: api)
    with pytest.raises(desktop_instance.DesktopInstanceError, match="denied"):
        desktop_instance.acquire_desktop_instance(_namespace=namespace)
    kernel.ReleaseMutex.assert_called_once_with(1)
    kernel.CloseHandle.assert_called_once_with(1)
    assert namespace not in desktop_instance._active_names


def test_desktop_launch_declines_before_state_directories_or_services(tmp_path, monkeypatch):
    from quantum_autopilot import desktop
    guard = MagicMock(is_primary=False, activation_error=None)
    guard.request_activation.return_value = True
    guard.__enter__.return_value = guard
    acquire = Mock(return_value=guard)
    monkeypatch.setattr(desktop_instance, "acquire_desktop_instance", acquire)
    monkeypatch.setattr(desktop, "_run_native", lambda *a, **kw: pytest.fail("must not start a second console"))
    for options in ([], ["--demo"], ["--synthetic", "faults"], ["--telemetry-file", "unread.json"]):
        state = tmp_path / str(uuid4())
        assert desktop.main([*options, "--state-root", str(state)]) == 0
        assert not state.exists()
    assert acquire.call_args_list == [call()] * 4
    assert guard.request_activation.call_count == 4


def test_browser_launch_is_guarded_while_headless_web_is_exempt(tmp_path, monkeypatch):
    from quantum_autopilot import desktop
    acquire = Mock(side_effect=desktop_instance.DesktopInstanceError(5, "denied"))
    monkeypatch.setattr(desktop_instance, "acquire_desktop_instance", acquire)
    monkeypatch.setattr(desktop_instance, "report_instance_error", Mock())
    launch = Mock(return_value=0)
    monkeypatch.setattr(desktop, "_run_web", launch)
    assert desktop.main(["--ui", "web", "--state-root", str(tmp_path)]) == 2
    launch.assert_not_called()
    assert desktop.main(["--ui", "web", "--no-browser", "--state-root", str(tmp_path)]) == 0
    assert acquire.call_count == 1
    launch.assert_called_once()


def test_direct_console_launch_acquires_before_services_and_releases_on_failure(monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from quantum_autopilot.console import app
    guard = SimpleNamespace(is_primary=True, close=Mock())
    acquire = Mock(return_value=guard)
    monkeypatch.setattr(desktop_instance, "acquire_desktop_instance", acquire)
    workspace = Mock(side_effect=RuntimeError("construction failed"))
    monkeypatch.setattr(app, "_run_workspace", workspace)
    args = app.build_parser().parse_args([])
    with pytest.raises(RuntimeError, match="construction failed"):
        app.run(args)
    acquire.assert_called_once_with()
    workspace.assert_called_once_with(args, startup_monitor=None, desktop_instance=guard)
    guard.close.assert_called_once_with()


def test_desktop_owner_is_transferred_to_console_without_another_lock(monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from quantum_autopilot.console import app
    guard = SimpleNamespace(is_primary=True, close=Mock())
    acquire = Mock(side_effect=AssertionError("must retain the launcher-owned mutex"))
    monkeypatch.setattr(desktop_instance, "acquire_desktop_instance", acquire)
    workspace = Mock(return_value=0)
    monkeypatch.setattr(app, "_run_workspace", workspace)
    assert app.run(app.build_parser().parse_args([]), desktop_instance=guard) == 0
    acquire.assert_not_called()
    guard.close.assert_not_called()


@pytest.mark.parametrize("diagnostic", [{"isolated_diagnostic": True}, {"startup_monitor": object()}])
def test_isolated_console_diagnostics_do_not_claim_operator_ownership(diagnostic, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from quantum_autopilot.console import app
    acquire = Mock(side_effect=AssertionError("isolated diagnostics never use the product mutex"))
    monkeypatch.setattr(desktop_instance, "acquire_desktop_instance", acquire)
    workspace = Mock(return_value=0)
    monkeypatch.setattr(app, "_run_workspace", workspace)
    assert app.run(app.build_parser().parse_args([]), **diagnostic) == 0
    acquire.assert_not_called()
    assert workspace.call_args.kwargs["isolated_diagnostic"] is True


@pytest.mark.parametrize("failure", ["session", "activation"])
def test_web_startup_failure_closes_server_without_scheduling_a_browser(tmp_path, monkeypatch, failure):
    from quantum_autopilot import desktop
    paths = SimpleNamespace(repository_root=tmp_path, dashboard_root=tmp_path,
                            state_root=tmp_path, control_journal=tmp_path / "journal.sqlite",
                            wdm_model_bundle=None)
    server = Mock(server_port=8765)
    server.service.security.issue_local_session.return_value = "isolated-session"
    guard = Mock()
    if failure == "session":
        server.service.security.issue_local_session.side_effect = RuntimeError("session failed")
    else:
        guard.attach_web_dashboard.side_effect = RuntimeError("activation failed")
    monkeypatch.setattr(desktop, "resolve_desktop_paths", Mock(return_value=paths))
    monkeypatch.setattr(desktop, "create_server", Mock(return_value=server))
    timer_factory = Mock()
    monkeypatch.setattr(desktop, "Timer", timer_factory)
    with pytest.raises(RuntimeError, match="failed"):
        desktop._run_web(desktop.build_parser().parse_args(["--ui", "web"]), desktop_instance=guard)
    server.server_close.assert_called_once_with()
    server.serve_forever.assert_not_called()
    timer_factory.assert_not_called()


def test_web_loop_exit_cancels_pending_browser_timer_and_closes_server(tmp_path, monkeypatch):
    from quantum_autopilot import desktop
    paths = SimpleNamespace(repository_root=tmp_path, dashboard_root=tmp_path,
                            state_root=tmp_path, control_journal=tmp_path / "journal.sqlite",
                            wdm_model_bundle=None)
    server = Mock(server_port=8765)
    server.service.security.issue_local_session.return_value = "isolated-session"
    server.serve_forever.side_effect = KeyboardInterrupt()
    guard = Mock()
    timer = Mock()
    monkeypatch.setattr(desktop, "resolve_desktop_paths", Mock(return_value=paths))
    monkeypatch.setattr(desktop, "create_server", Mock(return_value=server))
    monkeypatch.setattr(desktop, "Timer", Mock(return_value=timer))
    assert desktop._run_web(desktop.build_parser().parse_args(["--ui", "web"]), desktop_instance=guard) == 0
    guard.attach_web_dashboard.assert_called_once_with(tmp_path / "index.html")
    timer.start.assert_called_once_with()
    timer.cancel.assert_called_once_with()
    server.server_close.assert_called_once_with()
