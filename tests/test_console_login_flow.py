"""Native entry verifies the backend; the S. Ikari display sequence never does.

The provider and destination page are explicitly UI fixtures. Security uses a
real temporary runtime with a synthetic OS binding; no native credentials or
device/service operations are exercised by these tests.
"""
from __future__ import annotations

import json
import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt  # noqa: E402
from PySide6.QtTest import QSignalSpy, QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from quantum_autopilot.console import shell, theme  # noqa: E402
from quantum_autopilot.console.capabilities import SourceCapabilities  # noqa: E402
from quantum_autopilot.console.login_session import NativeLoginController  # noqa: E402
from quantum_autopilot.console.providers import MockProvider  # noqa: E402
from quantum_autopilot.console.screens.base import Screen  # noqa: E402
from quantum_autopilot.console.screens.boot import BootScreen  # noqa: E402
from quantum_autopilot.console.widgets.emergency_signal import EmergencySignalOverlay  # noqa: E402
from quantum_autopilot.security import AuthenticationError, Principal, SecurityRuntime  # noqa: E402
from quantum_autopilot.security import native_login, runtime as runtime_module  # noqa: E402


SUBJECT = "os:sid:S-1-5-21-987654321-123456789-111111111-1001"
ACCOUNT = "entry-ui-unit-account"
PASSWORD = "entry-ui-unit-only-password"
RESOURCE = "workspace:entry-ui-unit"
MARKER = "eva operator connected"


class _FixtureWorkspace(Screen):
    """A navigation destination without science panels or operational workers."""

    key = "topology"
    title = "UI fixture workspace"


class _ReadyInputFixture(MockProvider):
    """One ready, artificial display frame; no operational capabilities."""

    @property
    def capabilities(self):
        return SourceCapabilities(adapter_id="entry-ui-fixture", mode="synthetic", health="ready",
                                  operations=(), note="Artificial entry-flow display fixture only.")

    def start(self):
        self.snapshot_changed.emit(self.snapshot())


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _drain(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def _until(app, predicate, timeout_ms=2000):
    deadline = time.monotonic() + timeout_ms / 1000
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        # Native QTest waits can hold Python's GIL on Windows. Yield it so the
        # actual vault/audit worker progresses alongside the Qt event loop.
        time.sleep(.005)
    _drain(app)
    assert predicate(), "The bounded native-entry wait did not reach its expected state"


@pytest.fixture(autouse=True)
def appearance(app):
    previous = theme.current_theme()
    theme.set_theme("dark")
    theme.install(app)
    yield
    theme.set_theme(previous)
    theme.install(app)
    _drain(app)


@pytest.fixture
def security(tmp_path, monkeypatch):
    def identity():
        return SUBJECT, ACCOUNT
    monkeypatch.setattr(runtime_module, "operating_system_identity", identity)
    monkeypatch.setattr(native_login, "operating_system_identity", identity)
    runtime = SecurityRuntime(tmp_path / "security", profile="local")
    runtime.grant_identity(SUBJECT, ["researcher"], ["workspace:*"], display_name=ACCOUNT, source="os")
    return runtime


@pytest.fixture
def make_window(app, security, monkeypatch, tmp_path):
    monkeypatch.setattr(shell, "SCREENS", (_FixtureWorkspace,))
    windows = []

    def create(*, principal=None):
        provider = MockProvider()
        if principal is not None:
            provider._identity = principal
        window = shell.ConsoleWindow(provider, state_root=tmp_path, security=security,
                                     service_factory=MockProvider, demo_factory=MockProvider)
        window.interface_sequence._sound_enabled = False
        window.disconnect_sequence._sound_enabled = False
        window.resize(960, 480)
        window.show()
        window._on_snapshot(provider.snapshot())
        _drain(app)
        assert window.current_screen == "boot"
        windows.append(window)
        return window

    yield create
    for window in windows:
        window.close()
        window.deleteLater()
    _drain(app)


@pytest.fixture
def boot(app):
    screen = BootScreen()
    screen.configure_login(available=True, display_name=ACCOUNT)
    screen.select_source("demo")
    screen.resize(960, 480)
    screen.show()
    _drain(app)
    yield screen
    screen.close()
    screen.deleteLater()
    _drain(app)


def _trigger(window, *, account="  S. iKArI  ", password="thirdpilot"):
    window.boot.operator_id.setText(account)
    window.boot.password.setText(password)
    assert window.boot.open_button.isEnabled()
    window.boot.open_button.click()


@pytest.mark.parametrize("account,password", [
    ("S. Ikari", "thirdpilot"), (" S. IKARI ", "thirdpilot"), ("s. ikari", "thirdpilot"),
])
def test_reserved_pair_is_casefolded_and_emits_only_effect(boot, account, password):
    effect = QSignalSpy(boot.effect_requested)
    login = QSignalSpy(boot.login_requested)
    opened = QSignalSpy(boot.open_requested)
    authenticated = QSignalSpy(boot.authenticated)
    commands = QSignalSpy(boot.command)
    boot.select_source(None)
    boot.operator_id.setText(account)
    boot.password.setText(password)
    assert boot.open_button.isEnabled()
    boot.open_button.click()
    assert effect.count() == 1
    assert login.count() == opened.count() == authenticated.count() == commands.count() == 0
    assert boot.password.text() == ""
    assert not boot._login_verified


@pytest.mark.parametrize("account", ["eva operator", "Shinji Ikari"])
def test_former_eva_name_is_an_ordinary_native_login_request(boot, account):
    effect = QSignalSpy(boot.effect_requested)
    login = QSignalSpy(boot.login_requested)
    boot.operator_id.setText(account)
    boot.password.setText(PASSWORD)
    boot.open_button.click()
    assert effect.count() == 0 and login.count() == 1
    assert login.at(0) == [account, PASSWORD]
    assert boot.password.text() == ""


@pytest.mark.parametrize("password", ["", "wrong", "THIRDPILOT", " thirdpilot "])
def test_reserved_name_rejects_wrong_phrase_without_native_authentication(boot, password):
    effect = QSignalSpy(boot.effect_requested)
    login = QSignalSpy(boot.login_requested)
    opened = QSignalSpy(boot.open_requested)
    authenticated = QSignalSpy(boot.authenticated)
    boot.operator_id.setText("S. Ikari")
    boot.password.setText(password)
    boot._open()
    assert effect.count() == login.count() == opened.count() == authenticated.count() == 0
    assert boot.password.text() == ""
    assert not boot._login_verified
    assert "not accepted" in boot.login_feedback.text()


def test_reserved_pair_without_data_source_requires_exact_phrase(boot):
    boot.select_source(None)
    boot.operator_id.setText("S. Ikari")
    boot.password.setText("wrong")
    assert not boot.open_button.isEnabled()
    boot.password.setText("thirdpilot")
    assert boot.open_button.isEnabled()


def test_show_password_never_puts_username_or_password_in_saved_state(boot):
    boot.operator_id.setText("entered-private-account")
    boot.password.setText(PASSWORD)
    boot.show_password.click()
    assert boot.password.echoMode() == QLineEdit.EchoMode.Normal
    state = boot.save_state()
    assert state == {"selected_source": "demo"}
    encoded = json.dumps(state)
    assert PASSWORD not in encoded and "entered-private-account" not in encoded
    boot.restore_state({"selected_source": "measured", "password": "must-not-restore"})
    assert boot.selected_source == "measured"
    assert boot.password.text() == PASSWORD


def test_cosmetic_sequence_covers_entire_window_without_authentication_or_dispatch(make_window, monkeypatch):
    window = make_window()
    backend, dispatch, source_change = [], [], []
    monkeypatch.setattr(native_login, "authenticate_native_login", lambda *args: backend.append(args))
    monkeypatch.setattr(window.provider, "dispatch", lambda *args: dispatch.append(args))
    monkeypatch.setattr(window, "source_factory", lambda key: source_change.append(key))
    opened = QSignalSpy(window.boot.open_requested)
    authenticated = QSignalSpy(window.boot.authenticated)
    principal = window._login_controller.principal
    policy_before = window._security._policy()
    _trigger(window)
    overlay = window.interface_sequence
    assert isinstance(overlay, EmergencySignalOverlay)
    assert overlay.parentWidget() is window
    assert overlay.geometry() == window.rect() and overlay.isVisible()
    assert overlay._timer.isActive() and overlay._input_filter_installed
    assert not window._login_verified and not window.boot._login_verified
    assert not window._login_controller.active
    assert backend == dispatch == source_change == []
    assert opened.count() == authenticated.count() == 0
    assert window._security._policy() == policy_before
    assert window._login_controller.principal == principal
    assert window._security._sessions == {}
    assert ("Physical", "LOCKED") in window.boot.boundary_values()
    overlay.stop()


def test_natural_completion_returns_to_boot_and_sets_only_cosmetic_markers(make_window, app, monkeypatch):
    window = make_window()
    window.boot.select_source("measured")
    backend = []
    monkeypatch.setattr(native_login, "authenticate_native_login", lambda *args: backend.append(args))
    opened = QSignalSpy(window.boot.open_requested)
    authenticated = QSignalSpy(window.boot.authenticated)
    overlay = window.interface_sequence
    overlay._duration_ms = 70
    finished = QSignalSpy(overlay.finished)
    before = window._login_controller.principal
    _trigger(window)
    _until(app, lambda: finished.count() == 1)
    assert overlay.completed and not overlay.is_running and not overlay.isVisible()
    assert not overlay._timer.isActive() and not overlay._input_filter_installed
    assert window.current_screen == "boot" and window.current_widget() is window.boot
    assert window.boot.effect_status.text() == window.status.effect_status.text() == MARKER
    assert not window.boot.effect_status.isHidden() and not window.status.effect_status.isHidden()
    assert window.boot.selected_source == "measured"
    assert window.boot.password.text() == ""
    assert window.boot.password.echoMode() == QLineEdit.EchoMode.Password
    assert not window._login_verified and not window.boot._login_verified
    assert window._login_controller.principal == before and backend == []
    assert opened.count() == authenticated.count() == 0


def test_escape_cancels_without_connected_marker_or_root_shortcut_leak(make_window, app):
    window = make_window()
    window.boot.select_source("demo")
    finished = QSignalSpy(window.interface_sequence.finished)
    _trigger(window)
    QTest.keyClick(window.interface_sequence, Qt.Key.Key_Escape)
    _drain(app)
    assert finished.count() == 1 and not window.interface_sequence.completed
    assert window.current_screen == "boot" and window.boot.selected_source == "demo"
    assert window.boot.effect_status.text() == window.status.effect_status.text() == ""
    assert not window._effect_connected and not window._login_verified
    assert not window.interface_sequence._timer.isActive()
    assert not window.interface_sequence._input_filter_installed
    assert window.boot.password.text() == ""


def test_repeated_effect_reuses_one_timer_and_keeps_source_choice(make_window, app):
    window = make_window()
    overlay = window.interface_sequence
    timer = overlay._timer
    overlay._duration_ms = 60
    for selected in ("demo", "measured", "demo"):
        window.boot.select_source(selected)
        finished = QSignalSpy(overlay.finished)
        _trigger(window)
        _until(app, lambda: finished.count() == 1)
        assert overlay._timer is timer
        assert overlay.findChildren(QTimer, options=Qt.FindChildOption.FindDirectChildrenOnly) == [timer]
        assert window.boot.selected_source == selected
        assert window.boot.password.text() == ""
        assert window.boot.effect_status.text() == MARKER
        assert not timer.isActive()


def test_connected_marker_survives_theme_and_resize_without_old_overlay(make_window, app):
    window = make_window()
    overlay = window.interface_sequence
    overlay._duration_ms = 60
    _trigger(window)
    _until(app, lambda: window._effect_connected)
    window.resize(3440, 1440)
    _drain(app)
    assert overlay.geometry() == window.rect()
    assert window.set_theme("light")
    _drain(app)
    assert window.interface_sequence is not overlay
    assert window.findChildren(EmergencySignalOverlay) == [window.interface_sequence]
    assert not window.interface_sequence.isVisible() and not window.interface_sequence._timer.isActive()
    assert not window.interface_sequence._input_filter_installed
    assert window.boot.effect_status.text() == window.status.effect_status.text() == MARKER
    assert not window._login_verified and not window.boot._login_verified
    window.resize(960, 480)
    _drain(app)
    assert window.interface_sequence.geometry() == window.rect()


def test_signal_brand_starts_at_trigger_survives_rebuild_and_resets_in_new_window(make_window, app):
    from quantum_autopilot.console.widgets.brand_mark import BlochQMark, OrionQWordmark
    from PySide6.QtWidgets import QDialog

    window = make_window()
    assert not window._signal_brand
    assert not window.boot.brand.signal_mode and not window.splash.brand.signal_mode
    normal_icon = window.windowIcon().pixmap(64, 64).toImage()
    _trigger(window)
    assert window._signal_brand
    for widget_type in (BlochQMark, OrionQWordmark):
        assert all(mark.signal_mode for mark in window.canvas.findChildren(widget_type))
    assert window.windowIcon().pixmap(64, 64).toImage() != normal_icon
    window.interface_sequence.stop()
    window._interface_sequence_finished()
    # Cancellation affects the completion marker, not the requested triggered logo.
    assert not window._effect_connected and window.boot.brand.signal_mode
    assert window.set_theme("light")
    _drain(app)
    assert window.boot.brand.signal_mode and window.splash.brand.signal_mode
    added = OrionQWordmark(48, parent=window.boot)
    added.show()
    _drain(app)
    assert added.signal_mode
    dialog = QDialog(window)
    separate = BlochQMark(48, parent=dialog)
    dialog.show()
    separate.show()
    _drain(app)
    assert separate.signal_mode
    dialog.close()
    fresh = make_window()
    assert not fresh._signal_brand and not fresh.boot.brand.signal_mode
    assert fresh.windowIcon().pixmap(64, 64).toImage() == normal_icon


def _connect_effect(window, app):
    window.interface_sequence._duration_ms = 60
    _trigger(window)
    _until(app, lambda: window._effect_connected)
    window.disconnect_sequence._duration_ms = 90


def test_geo_display_mode_follows_trigger_theme_rebuild_and_unplug(make_window, app, monkeypatch):
    from quantum_autopilot.console.widgets.geo_view import GeoMap

    original_init = _FixtureWorkspace.__init__

    def with_geo(self):
        original_init(self)
        self.geo = GeoMap(self)
        self.root.addWidget(self.geo)

    monkeypatch.setattr(_FixtureWorkspace, "__init__", with_geo)
    window = make_window()
    geo = window.screens["topology"].geo
    assert not geo.scene.signal_mode
    _connect_effect(window, app)
    assert geo.scene.signal_mode
    assert window.set_theme("light")
    _drain(app)
    geo = window.screens["topology"].geo
    assert geo.scene.signal_mode and geo.renderer.signal_mode
    window.disconnect_sequence._sound_enabled = False
    window.disconnect_sequence._duration_ms = 90
    window.boot.unplug_button.click()
    _until(app, lambda: not window._signal_brand)
    assert not geo.scene.signal_mode and not geo.renderer.signal_mode
    fresh = make_window()
    assert not fresh.screens["topology"].geo.scene.signal_mode


def test_unplug_animation_restores_standard_login_without_backend_operations(make_window, app, monkeypatch):
    window = make_window()
    normal_icon = window.windowIcon().pixmap(64, 64).toImage()
    principal, policy = window._login_controller.principal, window._security._policy()
    calls = []
    monkeypatch.setattr(native_login, "authenticate_native_login", lambda *args: calls.append(args))
    monkeypatch.setattr(window.provider, "dispatch", lambda *args: calls.append(args))
    window.boot.select_source("measured")
    _connect_effect(window, app)
    assert window.boot.unplug_button.text() == "Unplug from E.V.A."
    assert window.boot.effect_footer.isVisible()
    assert window.topbar.source_button.text() == "Unplug from E.V.A."
    window.boot.password.setText("must be cleared on unplug")
    finished = QSignalSpy(window.disconnect_sequence.finished)
    window.boot.unplug_button.click()
    assert window.disconnect_sequence.isVisible()
    assert window.disconnect_sequence.geometry() == window.rect()
    assert window.boot.password.text() == "" and not window._login_verified
    _until(app, lambda: finished.count() == 1)
    assert window.disconnect_sequence.completed and not window.disconnect_sequence.is_running
    assert window.current_screen == "boot" and not window._signal_brand and not window._effect_connected
    assert not window.boot.brand.signal_mode and not window.splash.brand.signal_mode
    assert window.windowIcon().pixmap(64, 64).toImage() == normal_icon
    assert window.boot.effect_status.text() == window.status.effect_status.text() == ""
    assert window.boot.effect_footer.isHidden() and window.boot.unplug_button.isVisible() is False
    assert window.boot.operator_id.text() == ACCOUNT and window.boot.password.text() == ""
    assert window.boot.selected_source == "measured" and calls == []
    assert window._security._policy() == policy and window._login_controller.principal == principal
    assert not window.boot._login_verified and not window._login_verified
    assert ("Physical", "LOCKED") in window.boot.boundary_values()


@pytest.mark.parametrize("via", ["source", "rail", "loading"])
def test_workspace_and_loading_return_controls_use_disconnect_flow(make_window, app, via):
    window = make_window()
    _connect_effect(window, app)
    if via == "loading":
        window.splash.abort("Injected fixture read error")
        window.canvas.setCurrentWidget(window.splash)
        window._current = "splash"
        assert window.splash.back_button.text() == "Unplug from E.V.A."
        window.splash.back_button.click()
    else:
        window.show_screen("topology")  # Explicitly the zero-operation UI fixture.
        window._update_chrome()
        assert window.topbar.source_button.text() == "Unplug from E.V.A."
        assert window.rail.sign_out.accessibleName() == "Unplug from E.V.A."
        if via == "source":
            window.topbar.source_button.click()
        else:
            window.rail.sign_out.click()
    assert window.disconnect_sequence.is_running
    _until(app, lambda: not window._signal_brand)
    assert window.current_screen == "boot" and window.boot.effect_footer.isHidden()
    assert window.topbar.source_button.text().endswith(" ▾")
    assert window.splash.back_button.text() == "Back to sign in"
    assert window.rail.sign_out.accessibleName() == "Sign out"


def test_unplug_blocks_proxy_inputs_and_theme_until_black_hold_completes(make_window, app):
    window = make_window()
    _connect_effect(window, app)
    overlay = window.disconnect_sequence
    overlay._duration_ms = 700
    window.boot.unplug_button.click()
    before = window.boot.operator_id.text()
    QTest.keyClicks(window.boot.operator_id, "input-must-not-pass")
    QTest.keyClick(overlay, Qt.Key.Key_Escape)
    assert window.boot.operator_id.text() == before
    assert overlay.is_running and window._signal_brand
    assert window.set_theme("light") is False
    window.resize(3440, 1440)
    _drain(app)
    assert overlay.geometry() == window.rect()
    _until(app, lambda: not window._signal_brand)
    assert not overlay._timer.isActive() and not overlay._input_filter_installed


def test_unplug_hides_owned_dialog_preserves_form_and_restores_its_logo(make_window, app):
    from PySide6.QtWidgets import QDialog
    from quantum_autopilot.console.widgets.brand_mark import BlochQMark

    window = make_window()
    _connect_effect(window, app)
    dialog = QDialog(window)
    mark = BlochQMark(48, parent=dialog)
    field = QLineEdit("unsaved fixture configuration", dialog)
    dialog.show()
    mark.show()
    _drain(app)
    assert mark.signal_mode and dialog.isVisible()
    window.boot.unplug_button.click()
    assert not dialog.isVisible()
    _until(app, lambda: not window._signal_brand)
    assert field.text() == "unsaved fixture configuration" and not mark.signal_mode


def test_repeated_connect_unplug_cycles_reuse_one_overlay_and_preserve_source(make_window, app):
    window = make_window()
    overlay, timer = window.disconnect_sequence, window.disconnect_sequence._timer
    window.provider.start()
    for selected in ("demo", "measured", "demo"):
        window.boot.select_source(selected)
        _connect_effect(window, app)
        window.boot.unplug_button.click()
        _until(app, lambda: not window._signal_brand)
        assert window.boot.selected_source == selected and window.provider.running
        assert window.disconnect_sequence is overlay and overlay._timer is timer
        assert not timer.isActive() and not overlay._input_filter_installed
        assert window._login_controller.principal.subject == SUBJECT


def test_close_while_unplugging_disposes_overlay_without_late_navigation(make_window, app):
    window = make_window()
    _connect_effect(window, app)
    overlay = window.disconnect_sequence
    window.boot.unplug_button.click()
    finished = QSignalSpy(overlay.finished)
    window.close()
    _drain(app)
    assert not overlay.is_running and not overlay._timer.isActive()
    assert not overlay._input_filter_installed and finished.count() == 0


def test_failed_source_returns_to_login_and_retries_same_source_after_verified_login(make_window, app):
    from quantum_autopilot.console.prepared_source import DeferredProvider

    window = make_window()
    calls, navigations = [], []

    def prepare():
        calls.append("prepare")
        if len(calls) == 1:
            raise RuntimeError("injected initial source failure")
        return "prepared UI fixture"

    source = DeferredProvider(prepare, lambda _: _ReadyInputFixture(), kind="demo",
                              security=window._security)
    window.source_factory = lambda _: lambda: source
    window.screen_changed.connect(navigations.append)
    window._login_verified = True  # The actual OS-session verification below exercises recovery.
    assert window.open_workspace("demo")
    assert window.current_screen == "splash"
    _until(app, lambda: not source.running)
    assert not source.ready and window._loading_source
    assert "topology" not in navigations
    window.splash.back_requested.emit()
    assert window.current_screen == "boot" and not window._loading_source
    window.boot.select_source("demo")
    window.boot.os_session_button.click()
    _until(app, lambda: window.current_screen == "topology", timeout_ms=5000)
    assert source.ready and len(calls) == 2
    assert navigations.count("topology") == 1
    assert window._login_verified and not window._loading_source


def test_uncommitted_initial_source_cannot_open_workspace_after_verified_login(make_window, app):
    from quantum_autopilot.console.prepared_source import DeferredProvider

    window = make_window()
    source = DeferredProvider(lambda: "fixture", lambda _: _ReadyInputFixture(),
                              kind="demo", security=window._security)
    assert window.switch_input(lambda: source)
    source.stop()
    window._back_to_login_from_loading()
    window._login_verified = True
    window.open_workspace("demo")
    assert window.current_screen == "splash"
    assert window._loading_source
    _until(app, lambda: source.ready and window.current_screen == "topology")


def test_theme_change_is_blocked_during_sequence_and_close_stops_it(make_window):
    window = make_window()
    _trigger(window)
    assert window.set_theme("light") is False
    assert theme.current_theme() == "dark"
    assert window.interface_sequence.is_running
    window.close()
    assert not window.interface_sequence._timer.isActive()
    assert not window.interface_sequence._input_filter_installed


def test_anonymous_backend_binding_disables_regular_sign_in(make_window, monkeypatch):
    window = make_window(principal=Principal("anonymous", "anonymous", source="anonymous"))
    calls = []
    monkeypatch.setattr(native_login, "authenticate_native_login", lambda *args: calls.append(args))
    requested = QSignalSpy(window.boot.login_requested)
    opened = QSignalSpy(window.boot.open_requested)
    window.boot.select_source("demo")
    window.boot.operator_id.setText(ACCOUNT)
    window.boot.password.setText(PASSWORD)
    assert not window.boot.open_button.isEnabled()
    assert window.boot.os_session_button.isHidden() and not window.boot.os_session_button.isEnabled()
    window.boot._open()
    assert requested.count() == opened.count() == 0 and calls == []
    assert not window.open_workspace("demo")
    assert not window._login_verified


def test_regular_login_denial_clears_secret_and_preserves_selected_source(make_window, app, monkeypatch):
    window = make_window()
    verified = []

    def deny(username, password):
        verified.append((username, password))
        raise AuthenticationError("native rejection containing " + PASSWORD)

    monkeypatch.setattr(native_login, "_verify_windows_password", deny)
    opened = QSignalSpy(window.boot.open_requested)
    window.boot.select_source("measured")
    window.boot.operator_id.setText(ACCOUNT)
    window.boot.password.setText(PASSWORD)
    window.boot.open_button.click()
    assert window.current_screen == "splash"
    assert window.boot.password.text() == ""
    assert not window.boot.operator_id.isEnabled() and not window.boot.password.isEnabled()
    _until(app, lambda: not window._login_controller.active)
    assert verified == [(ACCOUNT, PASSWORD)]
    assert opened.count() == 0 and not window._login_verified
    assert window.current_screen == "boot" and window.boot.selected_source == "measured"
    assert window.boot.password.text() == "" and window.boot.password.isEnabled()
    assert "denied" in window.boot.login_feedback.text().lower()
    assert PASSWORD not in window.boot.login_feedback.text()
    assert PASSWORD not in (window._security.root / "audit.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("use_password", [False, True])
def test_only_backend_verified_local_identity_requests_workspace(make_window, app, monkeypatch, use_password):
    window = make_window()
    # Streaming here is solely the 1 Hz artificial MockProvider UI fixture.
    window.provider.start()
    source_choices = []
    native_calls = []

    def verified_password(username, password):
        native_calls.append((username, password))
        return SUBJECT

    def ui_source(key):
        source_choices.append(key)
        return _ReadyInputFixture

    monkeypatch.setattr(native_login, "_verify_windows_password", verified_password)
    monkeypatch.setattr(window, "source_factory", ui_source)
    opened = QSignalSpy(window.boot.open_requested)
    authenticated = QSignalSpy(window.boot.authenticated)
    window.boot.select_source("demo")
    window.boot.operator_id.setText(ACCOUNT)
    expected = window._login_controller.principal
    if use_password:
        window.boot.password.setText(PASSWORD)
        window.boot.open_button.click()
    else:
        window.boot.os_session_button.click()
    assert opened.count() == authenticated.count() == 0
    _until(app, lambda: opened.count() == 1)
    assert authenticated.count() == 1 and opened.at(0) == ["demo"]
    assert source_choices == ["demo"]
    assert native_calls == ([(ACCOUNT, PASSWORD)] if use_password else [])
    assert window._login_verified and window.boot._login_verified
    assert window._login_controller.principal == expected
    assert expected.roles == frozenset({"researcher"})
    assert window._security._sessions == {}
    assert not window._effect_connected and not window.interface_sequence.isVisible()
    assert window.current_screen == "topology"
    assert window.boot.password.text() == ""


def test_controller_delayed_backend_verification_keeps_qt_heartbeat_alive(app, security, monkeypatch):
    principal = security.local_identity()
    original = native_login.authenticate_native_login
    entered, release = threading.Event(), threading.Event()
    worker_threads = []

    def delayed(*args):
        worker_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(2), "Test backend gate was not released"
        return original(*args)

    monkeypatch.setattr(native_login, "authenticate_native_login", delayed)
    controller = NativeLoginController(security, principal, RESOURCE)
    succeeded, failed = QSignalSpy(controller.succeeded), QSignalSpy(controller.failed)
    busy = QSignalSpy(controller.busy_changed)
    beats = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: beats.append(time.monotonic()))
    timer.start()
    try:
        assert controller.submit(ACCOUNT, None)
        _until(app, entered.is_set)
        assert controller.active and not controller.submit(ACCOUNT, None)
        QTest.qWait(120)
        assert len(beats) >= 4
        assert succeeded.count() == failed.count() == 0
        assert len(worker_threads) == 1 and worker_threads[0] != threading.get_ident()
        release.set()
        _until(app, lambda: succeeded.count() == 1)
        assert succeeded.at(0)[0] == principal and failed.count() == 0
        assert [busy.at(index)[0] for index in range(busy.count())] == [True, False]
        assert not controller.active
    finally:
        release.set()
        timer.stop()
        controller.shutdown()
        controller.deleteLater()
        _drain(app)


def test_controller_never_exposes_worker_exception_or_entered_secret(app, security, monkeypatch):
    def failure(*_args):
        raise RuntimeError("private backend details " + PASSWORD)

    monkeypatch.setattr(native_login, "authenticate_native_login", failure)
    controller = NativeLoginController(security, security.local_identity(), RESOURCE)
    failed = QSignalSpy(controller.failed)
    try:
        assert controller.submit(ACCOUNT, PASSWORD)
        _until(app, lambda: failed.count() == 1)
        message = failed.at(0)[0]
        assert "denied" in message.lower()
        assert PASSWORD not in message and "private backend details" not in message
        assert not controller.active
    finally:
        controller.shutdown()
        controller.deleteLater()
        _drain(app)
