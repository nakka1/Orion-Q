"""Window shell: one shared chrome around the six screens.

Layout of the logical canvas (inside the responsive stage):

    splash | login (boot) | workspace
                              top bar (52)   mark · orion-q · breadcrumb · Ctrl K · Light/Dark · source
                              icon rail (60) | screen body (scrolls when it cannot fit)
                              journal · terminal (resizable, closable)
                              status bar (30)
    overlays: command palette, toast

Screens render only their body. The shell owns navigation, selection, the
palette, the terminal, the toast, the status text, the data-source switch and
the theme. Tokens are baked into widget styles when a widget is built, so a
theme change rebuilds the canvas (`set_theme`) and re-applies the cached
snapshot and the captured navigation / selection / view state.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from quantum_autopilot.product import PRODUCT_NAME

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QMainWindow, QMessageBox, QScrollArea,
    QStackedWidget, QWidget,
)

from . import live_commands, theme
from .contract import Snapshot
from .providers import DataProvider
from .screens import SCREENS, BootScreen, SplashScreen
from .screens.boot import SOURCE_OPTIONS
from .stage import ScaledStage
from .theme import C, DESIGN_H, DESIGN_W
from .widgets import hbox, scoped_style, vbox
from .widgets.chrome import IconRail, StatusBar, TopBar
from .widgets.journal import JournalPanel, JournalRow, journal_rows, run_text, short_clock
from .widgets.terminal_workspace import TerminalWorkspace
from .widgets.palette import CommandPalette, PaletteCommand, palette_commands
from .widgets.toast import Toast

READY = "Ready · physical actuation locked"
SYNTHETIC_SHORT = "Synthetic UI fixture"     # developer flag --synthetic; not a login source

live_commands.register()    # isolate / restore / recalibrate / enable / disable / locate / pairs / inject


@dataclass(frozen=True)
class Selection:
    """What the operator is inspecting. UI state; the backend is told when it can follow."""

    kind: str | None = None     # "node" | "link" | None
    id: str = ""


class ConsoleWindow(QMainWindow):
    selection_changed = Signal(object)   # Selection
    screen_changed = Signal(str)         # screen key, "boot" or "splash"
    theme_changed = Signal(str)

    def __init__(self, provider: DataProvider, skip_auth: bool = False, parent=None, *, service_factory=None,
                 state_root=None, demo_factory=None, security=None) -> None:
        from .branding import app_icon, configure_windows_app_identity
        configure_windows_app_identity()
        super().__init__(parent)
        self.setWindowIcon(app_icon())
        from .source_switch import SwitchableProvider
        provider = provider if isinstance(provider, SwitchableProvider) else SwitchableProvider(provider)
        self.provider = provider
        self.service_factory = service_factory
        self.demo_factory = demo_factory
        self.input_state_root = state_root
        active = provider.active
        self._security = security or getattr(active, "security", None)
        self._login_controller = None
        self._login_verified = False
        self._effect_connected = False
        self._signal_brand = False
        self._loading_source = False
        self._loading_reason = "startup"
        if self._security is not None:
            from .login_session import NativeLoginController
            from pathlib import Path
            principal = getattr(active, "_identity", None) or self._security.current_identity()
            self._login_controller = NativeLoginController(
                self._security, principal, "workspace:" + Path(state_root or self._security.root.parent).resolve().as_posix(), self)
            self._login_controller.succeeded.connect(self._login_succeeded)
            self._login_controller.failed.connect(self._login_failed)
            self._login_controller.busy_changed.connect(self._login_busy_changed)
        self.setWindowTitle(f"{PRODUCT_NAME} · Quantum Network Console")
        self.setMinimumSize(320, 240)
        from .window_chrome import FramelessControls
        self.window_controls = FramelessControls(self)

        self._snap: Snapshot | None = None
        self._busy = False
        self._pending_screen = None
        self._current = "splash"
        self._skip_auth = skip_auth
        self._source_key: str | None = None
        self._feedback: list[JournalRow] = []
        self._status_message = "Starting local service…"
        self._splash_note = ""
        self.selection = Selection()
        self.last_toast = ""
        self.screens: dict[str, QWidget] = {}

        from .campaign_job import CampaignJob
        self.campaign_job = CampaignJob(provider, self)
        self._close_after_campaign = False
        self.campaign_job.idle.connect(self._campaign_idle)

        self._build_ui()
        from .display import DisplayObserver
        self._display_observer = DisplayObserver(self, fit=True)

        for index, key in enumerate(self.screens, start=1):
            QShortcut(QKeySequence(str(index)), self, lambda k=key: self._shortcut_screen(k))
        QShortcut(QKeySequence("Esc"), self, self._escape)
        QShortcut(QKeySequence("F11"), self, self._toggle_fullscreen)
        QShortcut(QKeySequence("Alt+F4"), self, self.close)
        QShortcut(QKeySequence("Ctrl++"), self, lambda: self.centralWidget().change_zoom(0.1))
        QShortcut(QKeySequence("Ctrl+-"), self, lambda: self.centralWidget().change_zoom(-0.1))
        QShortcut(QKeySequence("Ctrl+0"), self, lambda: self.centralWidget().reset_zoom())
        QShortcut(QKeySequence("F6"), self, self.choose_data_source)
        QShortcut(QKeySequence("Ctrl+K"), self, self.open_palette)
        QShortcut(QKeySequence("Ctrl+J"), self, self.toggle_terminal)

        self.splash.start()
        provider.snapshot_changed.connect(self._on_snapshot)
        provider.source_changed.connect(self._on_source_changed)
        if hasattr(provider, "busy_changed"):
            provider.busy_changed.connect(self._on_busy)
            provider.error.connect(self._on_error)
            provider.progress.connect(self._on_progress)
        if hasattr(provider, "export_completed"):
            provider.export_completed.connect(self._on_export_completed)
        if hasattr(provider, "loading_progress"):
            provider.loading_progress.connect(self._on_loading_progress)

    # ------------------------------------------------------------ building
    def _build_ui(self, zoom: float = 1.0) -> None:
        """Create every widget from the active theme's tokens."""

        self.setStyleSheet(theme.build_qss() + f"QMainWindow{{background:{C.bg};}}")
        self.setPalette(theme.build_palette())
        self.window_controls.bar.refresh_theme()
        self.canvas = QStackedWidget()
        self.canvas.setProperty("orionSignalBrand", self._signal_brand)
        self.canvas.resize(DESIGN_W, DESIGN_H)
        scoped_style(self.canvas, f"background:{C.bg};")

        self.splash = SplashScreen()
        self.splash.finished.connect(self._splash_done)
        self.splash.retry_requested.connect(self._retry_loading)
        self.splash.back_requested.connect(self._back_to_login_from_loading)
        self.canvas.addWidget(self.splash)

        self.boot = BootScreen(options=self.source_options())
        self.boot.open_requested.connect(self.open_workspace)
        self.boot.login_requested.connect(self._login_requested)
        self.boot.effect_requested.connect(self._show_interface_sequence)
        self.boot.unplug_requested.connect(self._show_disconnect_sequence)
        local = self._login_controller.principal if self._login_controller is not None else None
        self.boot.configure_login(available=local is not None and local.source == "os",
                                  display_name=local.display_name if local is not None else "",
                                  fixture=local is None and (getattr(self.provider, "is_synthetic", False)
                                            or type(self.provider.active).__name__ == "MockProvider"))
        self.boot.set_effect_connected(self._effect_connected)
        self.canvas.addWidget(self.boot)

        self.workspace = QWidget()
        column = vbox(self.workspace, (0, 0, 0, 0), 0)
        self.topbar = TopBar()
        self.topbar.palette_requested.connect(self.open_palette)
        self.topbar.theme_selected.connect(self.set_theme)
        self.topbar.source_requested.connect(self.choose_data_source)
        column.addWidget(self.topbar)
        middle = QWidget()
        row = hbox(middle, (0, 0, 0, 0), 0)
        self.rail = IconRail()
        self.rail.navigate.connect(self.show_screen)
        self.rail.setMinimumHeight(self.rail.minimumSizeHint().height())
        self.rail_host = QScrollArea()
        self.rail_host.setWidgetResizable(True)
        self.rail_host.setFrameShape(QFrame.Shape.NoFrame)
        self.rail_host.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rail_host.setFixedWidth(self.rail.width())
        self.rail_host.setWidget(self.rail)
        row.addWidget(self.rail_host)
        self.screen_stack = QStackedWidget()
        row.addWidget(self.screen_stack, 1)
        self.journal = JournalPanel()
        self.journal.submitted.connect(self.run_terminal)
        self.journal.palette_requested.connect(self.open_palette)
        self.journal.close_requested.connect(lambda: self.set_terminal_visible(False))
        self.terminal_workspace = TerminalWorkspace(middle, self.journal)
        self.terminal_workspace.visibility_changed.connect(self._terminal_visibility_changed)
        column.addWidget(self.terminal_workspace, 1)
        self.status = StatusBar()
        self.status.capabilities_requested.connect(self.show_capabilities)
        self.status.terminal_requested.connect(self.toggle_terminal)
        self.status.terminal_button.setChecked(self.terminal_workspace.terminal_visible)
        self.status.set_effect_connected(self._effect_connected)
        self.lab_button = self.status.source      # older attribute name
        column.addWidget(self.status)
        self.canvas.addWidget(self.workspace)

        self.screens = {}
        self._hosts: dict[str, QScrollArea] = {}
        for cls in SCREENS:
            screen = cls()
            screen.navigate.connect(self.show_screen)
            screen.inspect_node.connect(self._inspect)
            screen.command.connect(self._command)
            screen.select.connect(self._screen_select)
            screen.notify.connect(self.toast)
            host = QScrollArea()
            host.setWidgetResizable(True)
            host.setFrameShape(QFrame.Shape.NoFrame)
            host.setWidget(screen)
            self.screen_stack.addWidget(host)
            self.screens[screen.key] = screen
            self._hosts[screen.key] = host
        self._attach_screens()

        self.palette = CommandPalette(self.canvas)
        self.palette.run_requested.connect(self.run_palette_command)
        self._toast = Toast(self.canvas)

        stage = ScaledStage(self.canvas)
        stage.user_zoom = zoom
        self.setCentralWidget(stage)      # Qt schedules the previous stage for deletion
        from .widgets.emergency_signal import EmergencySignalOverlay
        old_sequence = getattr(self, "interface_sequence", None)
        if old_sequence is not None:
            old_sequence.stop()
            old_sequence.deleteLater()
        self.interface_sequence = EmergencySignalOverlay(self)
        self.interface_sequence.finished.connect(self._interface_sequence_finished)
        self.interface_sequence.hide()
        from .widgets.disconnect_signal import DisconnectSignalOverlay
        old_disconnect = getattr(self, "disconnect_sequence", None)
        if old_disconnect is not None:
            old_disconnect.dispose()
            old_disconnect.deleteLater()
        self.disconnect_sequence = DisconnectSignalOverlay(self)
        self.disconnect_sequence.finished.connect(self._disconnect_sequence_finished)
        self.disconnect_sequence.hide()
        self._apply_signal_brand()
        self.status.showMessage(self._status_message)
        from .widgets.button_help import install_control_help
        install_control_help(self.canvas)

    def _attach_screens(self) -> None:
        """Hand shell-owned services to the widgets that use them."""

        topology, audit, edge = (self.screens.get(key) for key in ("topology", "audit", "fpga"))
        runner = getattr(topology, "campaign_runner", None)
        if runner is not None:
            runner.attach(self.campaign_job)
        history = getattr(audit, "command_history", None)
        if history is not None:
            history.attach(self.provider)
        for name in ("policy_review", "policy_decisions", "policy_context"):
            widget = getattr(edge, name, None)
            if widget is not None:
                widget.attach(self.provider)

    # --------------------------------------------------------------- theme
    def set_theme(self, name: str) -> bool:
        """Switch between "dark" and "light" by rebuilding the canvas.

        Refused (False, with a toast) while an operation or a secondary window
        is active: those hold work that a rebuild would discard.
        """

        if name not in theme.THEMES:
            raise ValueError(f"unknown theme: {name!r}")
        if name == theme.current_theme():
            return True
        reason = self._rebuild_blocker()
        if reason:
            self.toast(reason)
            self.status.showMessage(reason)
            return False
        state = self._capture_state()
        old_screens = list(self.screens.values())
        for screen in old_screens:
            screen.shutdown()
        # Stop the outgoing tree participating in every stylesheet/layout
        # change while its replacement is constructed. Deletion stays deferred:
        # a theme button may still be returning from its clicked signal.
        old_stage = self.takeCentralWidget()
        old_stage.hide()
        old_stage.setUpdatesEnabled(False)
        old_stage.setParent(None)
        old_stage.deleteLater()
        self.setUpdatesEnabled(False)
        try:
            theme.set_theme(name)
            app = QApplication.instance()
            # A global QApplication stylesheet repolishes every outgoing widget
            # and every other Qt window before building the replacement. Scope
            # the live appearance to this window and its owned dialogs instead.
            theme.install(app, root=self)
            self._build_ui(zoom=state["zoom"])
            self._restore_state(state)
        finally:
            self.setUpdatesEnabled(True)
        self.theme_changed.emit(name)
        return True

    def toggle_theme(self) -> bool:
        return self.set_theme("light" if theme.current_theme() == "dark" else "dark")

    def _rebuild_blocker(self) -> str | None:
        if (self.interface_sequence.isVisible() or self.disconnect_sequence.isVisible()
                or self._login_controller is not None and self._login_controller.active):
            return "Finish the current sign-in or interface sequence before changing theme."
        if self._busy or self.provider.busy:
            return "Wait for the current operation to finish before changing the theme."
        if self.campaign_job.active:
            return "A campaign is running; change the theme when it has finished."
        for screen in self.screens.values():
            for dialog in screen.findChildren(QDialog):
                if dialog.isVisible():
                    return "Close the open secondary windows before changing the theme."
                for name in ("job", "export_job", "checker"):
                    if getattr(getattr(dialog, name, None), "active", False):
                        return "A background check is still running; change the theme when it has finished."
        return None

    def _capture_state(self) -> dict:
        return {
            "page": self._current,
            "zoom": self.centralWidget().user_zoom,
            "journal_open": self.journal.is_open,
            "terminal_visible": self.terminal_workspace.terminal_visible,
            "terminal_height": self.terminal_workspace.preferred_height,
            "journal_scroll": self.journal.scroll.verticalScrollBar().value(),
            "journal_follow": self.journal._stick,
            "prompt": self.journal.prompt.text(),
            "history": list(self.journal.prompt.history),
            "boot": self.boot.save_state(),
            "screens": {key: screen.save_state() for key, screen in self.screens.items()},
        }

    def _restore_state(self, state: dict) -> None:
        self.terminal_workspace.set_terminal_visible(state["terminal_visible"], focus=False)
        self.journal.set_open(state["journal_open"])
        self.terminal_workspace.set_terminal_height(state["terminal_height"])
        self.journal.prompt.setText(state["prompt"])
        self.journal.prompt.history = list(state["history"])
        self.journal.set_feedback(self._feedback)
        snap = self._snap
        if snap is not None:
            self.boot.apply(snap)
            for screen in self.screens.values():
                screen.apply(snap)
            self._legacy_snapshot(snap)
        self.boot.restore_state(state["boot"])
        for key, screen in self.screens.items():
            screen.restore_state(state["screens"].get(key, {}))
            screen.on_selection(self.selection)
        self._apply_busy(self._busy)
        page = state["page"]
        if page == "splash":
            self.splash.note(self._splash_note or "Loading local service, evidence and model metadata…")
            self.canvas.setCurrentWidget(self.splash)
        else:
            self._current = "splash"      # force the page switch below
            self.show_screen(page)
        self._current = page
        self._update_chrome()
        panel = self.journal

        def restore_read_position():
            # The new scroll area initially has the viewport's height. Establish
            # the record layout before restoring a value that would clamp to 0.
            panel.list_lay.activate()
            panel.list_host.adjustSize()
            bar = panel.scroll.verticalScrollBar()
            panel._stick = state["journal_follow"]
            bar.setValue(bar.maximum() if panel._stick else state["journal_scroll"])
            panel._stick = state["journal_follow"]

        QTimer.singleShot(0, panel, restore_read_position)

    # ---------------------------------------------------------- data source
    def active_source_key(self) -> str:
        """"demo" | "measured" | "service" for the input that is active now.

        "synthetic" is returned for the developer UI fixture (`--synthetic`); it is not
        one of the login rows, so no row is marked while it is active.
        """

        if self._source_key is not None:
            return self._source_key
        if getattr(self.provider, "is_demo", False):
            return "demo"
        if self.provider.is_synthetic:
            return "synthetic"
        return "measured" if self.provider.is_read_only else "service"

    def source_options(self):
        active = self.active_source_key()
        return tuple(replace(option, enabled=option.key != "service" or self.service_factory is not None
                             or active == "service") for option in SOURCE_OPTIONS)

    def source_short(self) -> str:
        key = self.active_source_key()
        return next((option.short for option in SOURCE_OPTIONS if option.key == key), SYNTHETIC_SHORT)

    def source_factory(self, key: str):
        """The ONE mapping from a login data-source key to a provider factory.

        Returns a zero-argument callable that builds a fresh `DataProvider`, or
        None when the operator cancelled. Override or patch this to change what
        a source means.
        """

        if key == "demo":
            return self.open_demo_source()
        if key == "measured":
            return self._measured_file_factory()
        if key == "service":
            if self.service_factory is None:
                raise ValueError("Local service is not configured for this session")
            return self.service_factory
        raise ValueError(f"unknown data source: {key!r}")

    def open_demo_source(self):
        """Factory for the "Demonstration" source.

        The demonstration is the ordinary `ServiceProvider` over `demo_service(...)`: the
        same service class, engine and processing as normal operation, on its own journal
        (`<state_root>/demo/sandbox_journal.sqlite`), with the backend's simulated INPUT
        source. Nothing is generated on the UI side.
        """

        if self.demo_factory is not None:
            return self.demo_factory
        from pathlib import Path
        from quantum_autopilot.desktop import bundled_repository_root, default_state_root
        state_root = Path(self.input_state_root or default_state_root())

        def factory():
            from quantum_autopilot.qkd.dashboard_api import demo_service
            from .service_provider import ServiceProvider
            return ServiceProvider(demo_service(bundled_repository_root(), state_root, preset="brazil-institutions-v1"),
                                   state_root=state_root / "demo")
        return factory

    def _measured_file_factory(self):
        from .input_sources import MeasuredFileProvider
        path, _ = QFileDialog.getOpenFileName(self, "Local acquisition bridge (read only)", "", "Telemetry JSON (*.json)")
        if not path:
            return None
        root = self._input_root()
        return lambda: MeasuredFileProvider(path, root)

    def apply_source(self, key: str) -> bool:
        """Replace the input with the source `key` through `source_factory`."""

        try:
            factory = self.source_factory(key)
        except Exception as exc:
            self._on_error(str(exc))
            return False
        if factory is None or not self.switch_input(factory):
            return False
        self._source_key = key
        self.boot.select_source(key)
        self._update_chrome()
        return True

    def open_workspace(self, key: str | None = None) -> bool:
        """Login button: apply the marked source if it is not active, open Topology."""

        if self._security is not None and not self._login_verified and not self._skip_auth:
            self.boot.login_failed("Confirm the Windows identity before opening the workspace.")
            return False
        key = key or self.boot.selected_source or self.active_source_key()
        if key != self.active_source_key() and not self.apply_source(key):
            return False
        self._enter()
        return True

    def show_capabilities(self):
        description = self._snap.capabilities.describe() if self._snap is not None else "Waiting for an authorized input frame."
        QMessageBox.information(self, f"{PRODUCT_NAME} · Input capabilities", description)

    def choose_data_source(self):
        """F6 / source button: back to the login screen, where sources are chosen."""

        if self._signal_brand:
            self._show_disconnect_sequence()
            return
        self.show_screen("boot")

    def _input_root(self):
        from uuid import uuid4
        from pathlib import Path
        from quantum_autopilot.desktop import default_state_root
        return Path(self.input_state_root or default_state_root()) / "inputs" / uuid4().hex

    def switch_input(self, factory):
        self._loading_reason = "source"
        self._loading_source = True
        self.splash.start("Preparing the selected input source…")
        self.canvas.setCurrentWidget(self.splash)
        self._current = "splash"
        QApplication.instance().processEvents()
        try:
            self.provider.switch(factory)
            if not self.provider.running:
                self.provider.start()
            return True
        except Exception as exc:
            self._loading_source = False
            self._loading_reason = ""
            self.splash.abort("Unable to load the selected input source.")
            self.show_screen("boot")
            self._on_error(str(exc))
            return False

    def _on_source_changed(self, _label):
        self._source_key = None       # inferred from the provider until apply_source names it
        catalog = getattr(self.screens.get("fpga"), "model_catalog", None)
        if catalog is not None:
            catalog.checker.clear()
        self.campaign_job.refresh_source()
        self._pending_screen = None
        self.selection = Selection()
        self._snap = self.provider.snapshot()
        self.boot.apply(self._snap)
        for screen in self.screens.values():
            screen.apply(self._snap)
            screen.on_selection(self.selection)
        self._on_busy(False)
        self.boot.set_sources(self.source_options(), self.active_source_key())
        self._set_status("Input changed · " + self.provider.source_label)
        self._update_chrome()

    def open_measured_input(self):
        return self.apply_source("measured")

    def closeEvent(self, event):
        if self.campaign_job.active:
            self._close_after_campaign = True
            self.campaign_job.cancel()
            self._set_status("Closing after campaign cancellation / persisted-result verification…")
            event.ignore()
            return
        self.campaign_job.shutdown()
        self.interface_sequence.stop()
        self.disconnect_sequence.dispose()
        self.splash.dispose()
        if self._login_controller is not None:
            self._login_controller.shutdown()
        for screen in self.screens.values():
            screen.shutdown()
        self.provider.stop()
        super().closeEvent(event)

    def _campaign_idle(self):
        if self._close_after_campaign:
            self.close()

    # ------------------------------------------------------------- routing
    @property
    def snapshot(self) -> Snapshot | None:
        return self._snap

    @property
    def current_screen(self) -> str:
        return self._current

    def current_widget(self) -> QWidget:
        """The splash, the login screen or the screen body being shown."""

        page = self.canvas.currentWidget()
        return self.screens[self._current] if page is self.workspace and self._current in self.screens else page

    def statusBar(self) -> StatusBar:  # noqa: N802 - keeps the QMainWindow call site working
        return self.status

    def _set_status(self, message: str) -> None:
        self._status_message = message
        self.status.showMessage(message)

    def toast(self, message: str) -> None:
        """Transient UI notice (2.6 s). Not a record."""

        self.last_toast = message
        self._toast.show_message(message)

    def _splash_done(self) -> None:
        if self._loading_reason == "login":
            return
        if self._loading_source:
            self._loading_source = False
            self._loading_reason = ""
            if self._login_verified or self._security is None or self._skip_auth:
                self._enter()
            else:
                self.show_screen("boot")
            return
        self._loading_reason = ""
        if self._skip_auth:
            self._enter()
        else:
            self.show_screen("boot")

    def _enter(self) -> None:
        if self._loading_source:
            return
        active = self.provider.active
        if getattr(active, "ready", True) is False:
            self._loading_source = True
            self._loading_reason = "source"
            progress = getattr(active, "loading_state", {})
            self.splash.start("Waiting for the selected source to be ready…",
                              completed=progress.get("completed", 0), total=progress.get("total", 5))
            self.canvas.setCurrentWidget(self.splash)
            self._current = "splash"
            if not active.running:
                refresh = getattr(active, "request_refresh", None)
                if callable(refresh):
                    refresh()
                elif not self.provider.running:
                    self.provider.start()
            return
        self.show_screen("topology")

    def _back_to_login_from_loading(self):
        if self._signal_brand:
            self._show_disconnect_sequence()
            return
        self._loading_source = False
        self._loading_reason = ""
        self.show_screen("boot")

    def _login_requested(self, username, password):
        if self._login_controller is not None:
            self._login_source = self.boot.selected_source
            self._login_controller.submit(username, password)

    def _login_busy_changed(self, busy):
        self.boot.set_login_busy(busy)
        if busy:
            self._loading_reason = "login"
            self.splash.start("Verifying Windows identity and backend permissions…", total=2)
            self.canvas.setCurrentWidget(self.splash)
            self._current = "splash"

    def _login_succeeded(self, _identity):
        self._loading_reason = ""
        self.show_screen("boot")
        self.boot.select_source(getattr(self, '_login_source', self.boot.selected_source))
        self._login_verified = True
        self.boot.login_succeeded()

    def _login_failed(self, message):
        self._login_verified = False
        self._loading_reason = ""
        self.splash.abort(message)
        self.show_screen("boot")
        self.boot.select_source(getattr(self, '_login_source', self.boot.selected_source))
        self.boot.login_failed(message)

    def _show_interface_sequence(self):
        if (self.disconnect_sequence.isVisible()
                or self._login_controller is not None and self._login_controller.active):
            return
        self._login_verified = False
        self._sequence_source = self.boot.selected_source
        self.boot.reset_login()
        self._signal_brand = True
        self._apply_signal_brand()
        self.interface_sequence.start()

    def _apply_signal_brand(self):
        """Keep the display variant local to this window and its current session."""
        from .widgets.brand_mark import BlochQMark, OrionQWordmark, signal_app_icon
        from .widgets.geo_view import GeoMap
        from .branding import app_icon

        self.setProperty("orionSignalBrand", self._signal_brand)
        self.canvas.setProperty("orionSignalBrand", self._signal_brand)
        self.boot.set_effect_mode(self._signal_brand)
        self.topbar.set_effect_mode(self._signal_brand)
        self.splash.back_button.setText("Unplug from E.V.A." if self._signal_brand else "Back to sign in")
        exit_name = "Unplug from E.V.A." if self._signal_brand else "Sign out"
        self.rail.sign_out.setAccessibleName(exit_name)
        self.rail.sign_out.setToolTip(exit_name)
        for widget_type in (BlochQMark, OrionQWordmark, GeoMap):
            # QGraphicsProxyWidget's logical canvas has a separate QObject
            # ownership tree from native dialogs parented to this window.
            for owner in (self, self.canvas):
                for widget in owner.findChildren(widget_type):
                    widget.set_signal_mode(self._signal_brand)
        self.setWindowIcon(signal_app_icon() if self._signal_brand else app_icon())

    def _interface_sequence_finished(self):
        if self.interface_sequence.completed:
            self._effect_connected = True
        self.show_screen("boot")
        self.boot.select_source(getattr(self, '_sequence_source', self.boot.selected_source))
        self.boot.reset_login()
        self.boot.set_effect_connected(self._effect_connected)
        self.status.set_effect_connected(self._effect_connected)
        self.boot.operator_id.setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_disconnect_sequence(self):
        if (not self._signal_brand or self.interface_sequence.isVisible()
                or self.disconnect_sequence.isVisible()
                or self._login_controller is not None and self._login_controller.active):
            return
        self._disconnect_source = self.boot.selected_source
        self._login_verified = False
        self.boot.reset_login()
        self.palette.close_palette()
        # Native secondary windows must not cover the full-viewport sequence.
        # Hiding preserves their unsaved forms and leaves backend operations intact.
        for owner in (self, self.canvas):
            for dialog in owner.findChildren(QDialog):
                if dialog.isVisible():
                    dialog.hide()
        self.disconnect_sequence.start()

    def _disconnect_sequence_finished(self):
        self._signal_brand = False
        self._effect_connected = False
        self._loading_source = False
        self._loading_reason = ""
        self._apply_signal_brand()
        self.boot.set_effect_connected(False)
        self.status.set_effect_connected(False)
        self.show_screen("boot")
        self.boot.select_source(getattr(self, "_disconnect_source", self.boot.selected_source))
        self.boot.reset_login()
        principal = self._login_controller.principal if self._login_controller is not None else None
        self.boot.operator_id.setText(principal.display_name if principal is not None and principal.source == "os" else "")
        self.boot.login_feedback.setText("Confirm your Windows identity to open the workspace." if principal is not None else
                                         "Select a data source to open the workspace.")
        self.boot.operator_id.setFocus(Qt.FocusReason.OtherFocusReason)

    def _retry_loading(self):
        if self._login_controller is not None and self._login_controller.active:
            return
        self._loading_reason = "source" if self._loading_source else "startup"
        self.splash.start("Retrying the selected input source…")
        self._current = "splash"
        active = self.provider.active
        refresh = getattr(active, "request_refresh", None)
        if callable(refresh):
            refresh()
        elif not self.provider.running:
            self.provider.start()

    def _shortcut_screen(self, key: str) -> None:
        if self._current not in ("boot", "splash") and not self.palette.is_open():
            self.show_screen(key)

    def show_screen(self, key: str) -> None:
        if key == "boot":
            if self._signal_brand and self._current not in ("boot", "splash"):
                self._show_disconnect_sequence()
                return
            self.palette.close_palette()
            if self._snap is not None:
                self.boot.apply(self._snap)
            self.boot.set_sources(self.source_options(), self.active_source_key())
            self.boot.reset_login()
            self._login_verified = False
            self.canvas.setCurrentWidget(self.boot)
            self._current = key
            self.screen_changed.emit(key)
            return
        if self._snap is None:
            return
        screen = self.screens[key]
        screen.apply(self._snap)
        self.screen_stack.setCurrentWidget(self._hosts[key])
        self.canvas.setCurrentWidget(self.workspace)
        changed = self._current != key
        self._current = key
        screen.setEnabled(not self._busy)
        self._update_chrome()
        if changed:
            self.screen_changed.emit(key)

    # ----------------------------------------------------------- selection
    def _set_selection(self, selection: Selection) -> None:
        if selection == self.selection:
            return
        self.selection = selection
        for key, screen in self.screens.items():
            if key == self._current:
                screen.on_selection(selection)
            else:
                screen.cache_selection(selection)
        self._update_chrome()
        self.selection_changed.emit(selection)

    def select_node(self, node_id: str, navigate: bool = True) -> None:
        """Inspect a node: set the selection, show Topology, tell the backend if it follows."""

        self._set_selection(Selection("node", node_id))
        if navigate:
            self.show_screen("topology")
        if self._snap is not None and self._snap.capabilities.allows("select_node") and not self._busy:
            self._command("select_node", node_id)

    def select_link(self, link_id: str, navigate: bool = True) -> None:
        self._set_selection(Selection("link", link_id))
        if navigate:
            self.show_screen("topology")
        if self._snap is not None and self._snap.capabilities.allows("select_link") and not self._busy:
            self._command("select_link", link_id)

    def _screen_select(self, kind: str, ident: str) -> None:
        (self.select_node if kind == "node" else self.select_link)(ident, navigate=False)

    def _inspect(self, node_id: str) -> None:
        if self._busy:
            return
        self._set_selection(Selection("node", node_id))
        self._pending_screen = "node"
        self._command("select_node", node_id)

    # ------------------------------------------------- palette and terminal
    def _terminal_visibility_changed(self, visible: bool) -> None:
        self.status.terminal_button.setChecked(visible)

    def set_terminal_visible(self, visible: bool) -> None:
        self.terminal_workspace.set_terminal_visible(visible)
        if not visible:
            self.status.terminal_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def toggle_terminal(self) -> None:
        if self.canvas.currentWidget() is self.workspace:
            self.set_terminal_visible(not self.terminal_workspace.terminal_visible)

    def open_palette(self) -> None:
        if self.canvas.currentWidget() is not self.workspace:
            return
        self.palette.open_with(palette_commands(self._snap))

    def close_palette(self) -> None:
        self.palette.close_palette()

    def run_palette_command(self, command: PaletteCommand):
        feedback = self.run_verb(command.verb, command.target)
        if feedback is not None and not feedback.ok:
            self.toast(feedback.text)
        return feedback

    def run_verb(self, verb: str, target: str = ""):
        """Run a terminal verb (`widgets.journal.TERMINAL_VERBS`) with a target."""

        return self.run_terminal(f"{verb} {target}".strip())

    def run_terminal(self, line: str):
        """Dispatch a typed line. Returns the `Feedback` shown in the input strip."""

        feedback = run_text(self, line)
        if feedback is None:
            return None
        self._feedback.append(JournalRow("", feedback.text, feedback.status, feedback.tone, "input"))
        del self._feedback[:-3]
        self.journal.set_feedback(self._feedback)      # the journal may be a rebuilt one
        return feedback

    # ------------------------------------------------------------ commands
    def _command(self, verb: str, target: str) -> None:
        topology, node = self.screens.get("topology"), self.screens.get("node")
        if self._busy:
            self._set_status("Wait for the current operation to finish.")
            self.toast("Wait for the current operation to finish.")
            if verb in {'add_simulated_node', 'stage_node'} and hasattr(node, "command_failed"):
                node.command_failed("Provider busy; request not dispatched")
            if verb == 'apply_simulated_node_action' and hasattr(topology, "command_failed"):
                topology.command_failed('Provider busy; request not dispatched')
            if verb == "configure_simulated_link" and hasattr(topology, "link_editor"):
                topology.link_editor.command_failed("Provider busy; request not dispatched")
            if verb in {"run_workspace_path", "run_temporal_path"} and hasattr(topology, "path_runner"):
                topology.path_runner.command_failed("Provider busy; request not dispatched")
            return
        # Inspecting a fibre must retain the view the operator is using. A
        # delayed selection publication is data, not a navigation request.
        # Read-only acquisition files retain their established telemetry view.
        if verb == "select_link" and getattr(self.provider, "is_read_only", False):
            self._pending_screen = "telemetry"
        dispatch = getattr(self.provider, "dispatch", None)
        if callable(dispatch):
            try:
                accepted = dispatch({"export": "export_record"}.get(verb, verb), target)
                if accepted is False and verb in {"add_simulated_node", 'stage_node', 'apply_simulated_node_action', "configure_simulated_link", "run_workspace_path", "run_temporal_path",
                                                  "add_network_node", "set_node_location", "set_node_isolation", "set_link_active", "recalibrate_node"}:
                    self._on_error("Provider did not accept the inventory request")
            except Exception as exc:
                self._on_error(str(exc))
            return
        handler = getattr(self.provider, {
            "recalibrate": "recalibrate", "isolate": "isolate",
            "acknowledge": "acknowledge", "export": "export_record",
        }.get(verb, verb), None)
        if callable(handler):
            handler(target)

    def _apply_busy(self, busy) -> None:
        topology = self.screens.get("topology")
        if hasattr(topology, "campaign_runner") and getattr(self, "_campaign_busy", None) != busy:
            self._campaign_busy = busy
            topology.campaign_runner.refresh()
        for name in ("link_editor", "path_runner"):
            widget = getattr(topology, name, None)
            if widget is not None:
                widget.set_busy(busy)
        for screen in self.screens.values():
            # Keep read-only content, scrolling and navigation available while
            # commands run. Disabling the page repaints its entire graphics proxy.
            screen.commands_busy = busy
            screen.apply_command_capabilities()
            if hasattr(screen, "flash"):
                screen.flash.setEnabled(False)

    def _on_busy(self, busy):
        self._busy = busy
        if busy:
            self._set_status("Working… local simulation only.")
        elif not self._status_message.startswith(("Operation failed:", "Controller test")):
            self._set_status(READY)
        self._apply_busy(busy)

    def _on_error(self, message):
        self._pending_screen = None
        topology, node = self.screens.get("topology"), self.screens.get("node")
        for target in (node, topology, self.screens.get('communicators'), getattr(topology, "link_editor", None), getattr(topology, "path_runner", None)):
            if hasattr(target, "command_failed"):
                target.command_failed(message)
        self._splash_note = f"Unable to load: {message}"
        if self._current == "splash":
            self.splash.abort(self._splash_note)
        else:
            self.splash.note(self._splash_note)
        self._set_status(f"Operation failed: {message}")

    def _on_progress(self, message) -> None:
        self._splash_note = message
        self.splash.note(message)
        self._set_status(message)

    def _on_loading_progress(self, progress):
        if self._loading_reason != "login" and self._current == "splash":
            self.splash.set_progress(progress['completed'], progress['total'], progress['message'])

    def _on_export_completed(self, path: str) -> None:
        self.toast(f"Evidence exported: {path}")
        self._set_status(f"Evidence exported: {path}")

    def _toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def _escape(self) -> None:
        if self.disconnect_sequence.isVisible():
            return  # The black hold completes before normal sign-in is restored.
        if self.interface_sequence.isVisible():
            self.interface_sequence.stop()
            self._interface_sequence_finished()
            return
        if self.palette.is_open():
            self.palette.close_palette()
            return
        hidden = False
        for screen in self.screens.values():
            overlay = getattr(screen, "node_popover", None)
            if overlay is not None and overlay.isVisible():
                overlay.hide()
                hidden = True
        if not hidden and self.isFullScreen():
            self.showNormal()

    # ------------------------------------------------------------ data in
    def _legacy_snapshot(self, snap: Snapshot) -> None:
        topology = self.screens.get("topology")
        for name in ("link_editor", "path_runner"):
            widget = getattr(topology, name, None)
            if widget is not None:
                widget.set_snapshot(snap)

    def _update_chrome(self) -> None:
        """Refresh the chrome from real state. Labels only: no restyle, no enable change."""

        snap = self._snap
        screen = self.screens.get(self._current)
        if screen is not None:
            self.rail.set_active(self._current)
            self.topbar.set_crumb(screen.breadcrumb(snap, self.selection))
        self.topbar.set_source(self.source_short())
        self.status.set_source("DATA SOURCE · " + self.provider.source_label,
                               snap.capabilities.describe() if snap is not None else None)
        if snap is not None:
            campaign = snap.campaign if len(snap.campaign) <= 32 else snap.campaign[:31] + "…"
            clock = f"{campaign} · {short_clock(snap.at)}"
            stats = snap.presentation_stats
            wall_ms = stats.get("last_network_step_wall_ms")
            period_s = stats.get("network_step_period_s")
            if isinstance(wall_ms, (int, float)) and isinstance(period_s, (int, float)) and wall_ms > period_s * 1000:
                clock += f" · COMPUTE {wall_ms / 1000:.1f}s/{period_s:.1f}s"
            self.status.set_clock(clock)
            self.status.clock.setToolTip(
                "Backend steps preserve scientific event order; display frames may be coalesced.\n"
                f"Step duration: {wall_ms if wall_ms is not None else 'pending'} ms; target period: {period_s} s.\n"
                f"Step overruns: {stats.get('network_step_overruns', 0)}; pending protocol events: {stats.get('protocol_pending_events', 0)}.\n"
                f"Pending selections: {stats.get('selection_jobs_pending', 0)}; rejected queue requests: {stats.get('selection_queue_rejections', 0)}.\n"
                f"Coalesced GUI polls: {stats.get('coalesced_gui_polls', 0)}; obsolete selection frames: {stats.get('coalesced_selection_frames', 0)}."
            )
        self.journal.set_rows(journal_rows(snap))

    def _on_snapshot(self, snap: Snapshot) -> None:
        self.campaign_job.refresh_source(snap)
        if self._snap is None and not self._busy:
            self._set_status("Ready · SYNTHETIC UI FIXTURE · no hardware" if getattr(self.provider, "is_synthetic", False)
                             else READY)
            self._on_busy(False)
        self._snap = snap
        if self.interface_sequence.isVisible() or self.disconnect_sequence.isVisible():
            return  # Presentation only; acquisition and durable backend events continue.
        if self._loading_reason == "login" and self._current == "splash":
            self._update_chrome()
            return
        if self.selection.kind == "node" and all(node.id != self.selection.id for node in snap.topology.nodes):
            self.selection = Selection()
        self._legacy_snapshot(snap)
        if self._current == "splash" and self._loading_reason != "login":
            if snap.capabilities.health == "starting" or (snap.capabilities.health == "unavailable"
                    and not (getattr(self.provider, "is_synthetic", False)
                             or type(self.provider.active).__name__ == "MockProvider")):
                return  # Source-switch clearing frame is not readiness evidence.
            self._update_chrome()
            self.splash.complete()
            return
        if self._pending_screen:
            key, self._pending_screen = self._pending_screen, None
            self.show_screen(key)
            return
        if self._current == "boot":
            self.boot.apply(snap)
        else:
            self.screens[self._current].apply(snap)
        self._update_chrome()


__all__ = ["ConsoleWindow", "Selection"]
