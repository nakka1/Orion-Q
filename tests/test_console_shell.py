"""Shared chrome: theme rebuild, palette, terminal grammar, journal, login sources."""
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.capabilities import SourceCapabilities  # noqa: E402
from quantum_autopilot.console.contract import CommandView, LedgerEntry  # noqa: E402
from quantum_autopilot.console.shell import ConsoleWindow  # noqa: E402
from quantum_autopilot.console.synthetic import SyntheticProvider  # noqa: E402
from quantum_autopilot.console.widgets import journal as journal_module  # noqa: E402
from quantum_autopilot.console.widgets import palette as palette_module  # noqa: E402
from quantum_autopilot.console.widgets.journal import (  # noqa: E402
    Feedback, TerminalVerb, journal_rows, parse_command, register_verb,
)
from quantum_autopilot.console.widgets.palette import (  # noqa: E402
    PaletteCommand, filter_commands, palette_commands, register_palette_provider,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.set_theme("dark")
    theme.install(instance)
    return instance


def drain(app):
    for _ in range(4):
        app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture()
def window(app, tmp_path):
    provider = SyntheticProvider(ROOT, tmp_path / "source")
    win = ConsoleWindow(provider, skip_auth=True, state_root=tmp_path)
    win._on_snapshot(provider.snapshot())
    win.resize(1440, 900)
    win.show()
    drain(app)
    yield win
    win.close()
    win.deleteLater()
    theme.set_theme("dark")
    theme.install(app)
    drain(app)


def test_one_shared_chrome_with_prototype_metrics(window, app):
    assert list(window.rail.buttons) == ["topology", "telemetry", "keys", "node", "communicators", "fpga", "audit"]
    assert [b.toolTip().splitlines()[0] for b in window.rail.buttons.values()] == [
        "[1] Topology", "[2] Fidelity & decoherence", "[3] Key management", "[4] Nodes",
        "[5] Comunicadores", "[6] Edge control", "[7] Access & audit"]
    assert window.rail.sign_out.toolTip().startswith("Sign out\n")
    assert all(len(b.toolTip().splitlines()) == 2 for b in window.rail.buttons.values())
    assert (window.topbar.height(), window.rail.width(), window.status.height()) == (52, 60, 30)
    assert window.rail.buttons["topology"].active and not window.rail.buttons["audit"].active
    assert not any(hasattr(screen, "rail") for screen in window.screens.values())
    assert window.topbar.crumb.full_text.startswith("/ topology")
    # The synthetic stream is a developer UI fixture; it is never labelled as the demonstration.
    assert window.topbar.source_button.text() == "Synthetic UI fixture ▾"
    assert window.active_source_key() == "synthetic"
    assert window.lab_button.text().startswith("DATA SOURCE · ") and "synthetic-stream" in window.lab_button.toolTip()
    assert window.status.lock.text() == "Physical locked"
    assert window.statusBar().currentMessage().startswith("Ready")
    window.rail.buttons["audit"].click()
    assert window.current_screen == "audit" and window.topbar.crumb.full_text == "/ access & audit"
    assert window.current_widget() is window.screens["audit"]
    window.rail.sign_out.click()
    assert window.current_screen == "boot" and window.canvas.currentWidget() is window.boot


def test_delayed_link_publication_preserves_topology_navigation(window, app):
    window.show_screen("topology")
    link = window.snapshot.topology.links[0]
    window.select_link(link.id or link.label, navigate=False)
    window.provider.snapshot_changed.emit(window.snapshot)
    drain(app)
    assert window.current_screen == "topology"
    assert window.screens["topology"].isVisible()
    assert window._pending_screen is None


def test_hidden_screens_keep_selection_without_presenting_stale_widgets(window, app, monkeypatch):
    """Selection fan-out must not rebuild every previously visited screen."""
    node_screen = window.screens["node"]
    telemetry = window.screens["telemetry"]
    keys = window.screens["keys"]
    window.show_screen("node")
    window.show_screen("telemetry")
    window.show_screen("keys")
    window.show_screen("topology")
    calls = []
    with monkeypatch.context() as spy:
        spy.setattr(node_screen, "_render_inventory", lambda snap: calls.append("inventory"))
        spy.setattr(node_screen, "_render_detail", lambda snap: calls.append("detail"))
        spy.setattr(telemetry, "refresh", lambda snap: calls.append("telemetry"))
        if keys.live_panel is not None:
            spy.setattr(keys.live_panel, "set_live", lambda live: calls.append("keys"))
        node_id = window.snapshot.topology.nodes[-1].id
        window.select_node(node_id, navigate=False)
        link_id = window.snapshot.topology.links[-1].id or window.snapshot.topology.links[-1].label
        window.select_link(link_id, navigate=False)
        assert node_screen._selected == node_id
        assert telemetry._link == link_id
        assert keys._fibre == link_id
        assert calls == []
    window.show_screen("node")
    assert node_screen._selected == node_id
    assert node_screen._snap is window.snapshot
    window.show_screen("telemetry")
    assert telemetry._link == link_id
    assert telemetry._snap is window.snapshot


def test_theme_toggle_rebuild_keeps_screen_selection_and_survives_five_toggles(window, app, capfd):
    node_id = window.snapshot.topology.nodes[2].id
    window.select_node(node_id)
    window.show_screen("topology")
    window.screens["topology"].set_view("MATRIX")
    window.journal.set_open(False)
    window.run_terminal("bogus")
    drain(app)
    baseline = None
    for index in range(5):
        wanted = "light" if theme.current_theme() == "dark" else "dark"
        old_screens = dict(window.screens)
        assert window.set_theme(wanted)
        drain(app)
        tokens = theme.TOKENS[wanted]
        assert theme.current_theme() == wanted and str(theme.C.bg) == tokens["bg"]
        assert all(window.screens[key] is not old for key, old in old_screens.items())
        assert window.current_screen == "topology" and window.current_widget() is window.screens["topology"]
        assert window.selection.kind == "node" and window.selection.id == node_id
        assert window.topbar.crumb.full_text == f"/ topology / {node_id}"
        assert window.screens["topology"].view == "MATRIX"
        assert not window.journal.is_open and [row.status for row in window.journal.feedback()] == ["ERR"]
        assert window.topbar.theme_toggle.current() == wanted
        assert tokens["bg"] in window.canvas.styleSheet() and tokens["raised"] in window.rail.styleSheet()
        image = window.grab().toImage()
        assert image.pixelColor(30, 500).name() == tokens["raised"]        # rail ground in the new theme
        assert window.screens["topology"].link_table.model().rowCount() == len(window.snapshot.topology.links)
        count = len(app.allWidgets())
        if index == 1:
            baseline = count
        elif index > 1:
            assert abs(count - baseline) <= 40, "widgets leak across theme rebuilds"
    # Live data keeps flowing into the rebuilt widgets and stale ones raise nothing.
    window._on_snapshot(window.snapshot)
    window.screens["fpga"].catalog_button.click()
    drain(app)
    dialog = window.screens["fpga"].model_catalog
    assert dialog.isVisible() and theme.TOKENS[theme.current_theme()]["raised"] in dialog.styleSheet() + app.styleSheet()
    assert not window.set_theme("dark" if theme.current_theme() == "light" else "light")   # open dialog blocks
    assert "Close the open secondary windows" in window.last_toast
    dialog.close()
    window._busy = True
    assert not window.toggle_theme()
    window._busy = False
    drain(app)
    errors = capfd.readouterr().err
    assert "Traceback" not in errors and "already deleted" not in errors


def test_ctrl_k_opens_palette_and_keys_drive_it(window, app):
    assert not window.palette.is_open()
    QTest.keyClick(window, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    drain(app)
    assert window.palette.is_open()
    items = window.palette.items()
    assert [c.label for c in items[:7]] == ["Go to Topology", "Go to Fidelity & decoherence", "Go to Key management",
                                             "Go to Nodes", "Go to Comunicadores", "Go to Edge control", "Go to Access & audit"]
    assert window.palette.current() is items[0]
    QTest.keyClick(window.palette.search, Qt.Key.Key_Down)
    assert window.palette.current() is items[1]
    QTest.keyClick(window.palette.search, Qt.Key.Key_Up)
    QTest.keyClicks(window.palette.search, "audit")
    assert [c.label for c in window.palette.items()] == ["Go to Access & audit"]
    QTest.keyClick(window.palette.search, Qt.Key.Key_Return)
    assert not window.palette.is_open() and window.current_screen == "audit"
    window.topbar.palette_button.click()
    assert window.palette.is_open()
    QTest.keyClick(window.palette.search, Qt.Key.Key_Escape)
    assert not window.palette.is_open()
    window.show_screen("boot")
    window.open_palette()
    assert not window.palette.is_open()        # no palette on the login screen


def test_palette_entries_come_from_real_state_and_capabilities(window, app):
    snap = window.snapshot
    commands = palette_commands(snap)
    labels = [c.label for c in commands]
    assert {c.group for c in commands} <= {"screen", "command", "select", "view", "session"}
    assert [c.group for c in commands] == sorted((c.group for c in commands), key=palette_module.GROUPS.index)
    for node in snap.topology.nodes:
        assert f"Select node {node.id}" in labels
    assert sum(label.startswith("Select link ") for label in labels) == len({journal_module.link_name(link) for link in snap.topology.links})
    assert "Run simulated scenario" in labels and "Export record" in labels
    assert "Replay WDM model (shadow)" not in labels            # the synthetic source does not declare it
    assert "Switch to light theme" in labels and "Sign out" in labels
    assert all(c.operation is None or snap.capabilities.allows(c.operation) for c in commands)
    read_only = replace(snap, capabilities=SourceCapabilities(
        adapter_id="read-only-test", mode="read_only", health="ready", operations=("export_record",)))
    assert [c.label for c in palette_commands(read_only) if c.group == "command"] == ["Export record"]
    assert [c.label for c in palette_commands(None) if c.group != "screen"] == [
        "Switch to light theme", "Change data source", "Sign out"]
    assert [c.label for c in filter_commands(commands, "select core")] == ["Select node CORE"] or all(
        "core" in f"{c.label} {c.keywords}".lower() for c in filter_commands(commands, "select core"))
    assert filter_commands(commands, "zzz-no-such") == []

    def extra(snapshot):
        yield PaletteCommand("Probe entry", "command", "probe_verb", "x")
    register_palette_provider(extra)
    calls = []
    register_verb(TerminalVerb("probe_verb", "probe_verb <x>", lambda win, target: calls.append(target)))
    try:
        window.open_palette()
        QTest.keyClicks(window.palette.search, "probe entry")
        window.palette.run_current()
        assert calls == ["x"]
    finally:
        palette_module.PALETTE_PROVIDERS.remove(extra)
        del journal_module.TERMINAL_VERBS["probe_verb"]


def test_palette_selection_and_theme_entries_execute(window, app):
    node_id = window.snapshot.topology.nodes[-1].id
    window.show_screen("keys")
    window.open_palette()
    QTest.keyClicks(window.palette.search, f"select node {node_id}")
    window.palette.run_current()
    assert window.selection.kind == "node" and window.selection.id == node_id and window.current_screen == "topology"
    window.open_palette()
    QTest.keyClicks(window.palette.search, "switch to light")
    window.palette.run_current()
    drain(app)
    assert theme.current_theme() == "light"
    assert "Switch to dark theme" in [c.label for c in palette_commands(window.snapshot)]
    window.run_palette_command(PaletteCommand("Sign out", "session", "sign_out"))
    assert window.current_screen == "boot"


def test_terminal_grammar_dispatches_real_verbs_and_refuses_disallowed(window, app, monkeypatch):
    assert parse_command("  /Go   nodes ") == ("go", "nodes") and parse_command("   ") is None
    sent = []
    monkeypatch.setattr(window, "_command", lambda verb, target: sent.append((verb, target)))
    assert window.run_terminal("run_scenario").status == "DISPATCHED"
    assert window.run_terminal("export campaign").status == "DISPATCHED"
    assert sent == [("run_scenario", ""), ("export", "campaign")]
    refused = window.run_terminal("replay_model")               # not declared by the synthetic source
    assert refused.status == "REFUSED" and "replay_model" in refused.text and len(sent) == 2
    assert window.run_terminal("frobnicate now").status == "ERR"
    assert window.run_terminal("go nowhere").status == "ERR"
    assert window.run_terminal("go nodes").ok and window.current_screen == "node"
    assert window.run_terminal("go edge").ok and window.current_screen == "fpga"
    node_id = window.snapshot.topology.nodes[1].id
    assert window.run_terminal(f"select_node {node_id.lower()}").ok
    assert window.selection.id == node_id and window.current_screen == "topology"
    assert ("select_node", node_id) in sent
    assert window.run_terminal("select_node no-such-node").status == "ERR"
    link = window.snapshot.topology.links[0]
    assert window.run_terminal(f"select_link {link.a} {link.b}").ok and window.selection.kind == "link"
    assert not any(verb == "select_link" for verb, _ in sent)   # this source cannot follow a link selection
    help_row = window.run_terminal("help")
    assert help_row.status == "HELP" and all(word in help_row.text for word in (
        "go <screen>", "theme", "select_node <id>", "select_link <id>", "run_scenario", "replay_model", "export"))
    # Input feedback is kept apart from backend rows and marked as such.
    assert all(row.origin == "input" for row in window.journal.feedback()) and len(window.journal.feedback()) == 3
    assert all(row.origin != "input" for row in window.journal.rows())
    window._busy = True
    assert window.run_terminal("run_scenario").status == "REFUSED"
    window._busy = False
    calls = []
    register_verb(TerminalVerb("probe_gate", "probe_gate <link>", lambda win, target: calls.append(target) or Feedback("ok"),
                               operation="configure_simulated_link"))
    try:
        assert window.run_terminal("probe_gate L-1").ok and calls == ["L-1"]
        # The live-network verbs are registered, and refused for a source that does not declare them.
        assert window.run_terminal("enable L-1").status == "REFUSED" and "set_link_active" in window.run_terminal("enable L-1").text
        assert window.run_terminal("inject thermal_drift L-1").status == "REFUSED"
        window._snap = replace(window._snap, capabilities=SourceCapabilities())
        assert window.run_terminal("probe_gate L-1").status == "REFUSED" and calls == ["L-1"]
    finally:
        del journal_module.TERMINAL_VERBS["probe_gate"]


def test_typed_prompt_runs_through_the_window(window, app):
    window.journal.prompt.setFocus()
    QTest.keyClicks(window.journal.prompt, "go audit")
    QTest.keyClick(window.journal.prompt, Qt.Key.Key_Return)
    assert window.current_screen == "audit" and window.journal.prompt.text() == ""
    QTest.keyClick(window.journal.prompt, Qt.Key.Key_Up)
    assert window.journal.prompt.text() == "go audit"
    QTest.keyClicks(window.journal.prompt, "1")                 # digits type; they do not switch screens here
    assert window.current_screen == "audit"
    window.journal.toggle.click()
    assert not window.journal.is_open and not window.journal.body.isVisible()
    assert window.journal.toggle.text().startswith("▸")
    window.journal.toggle.click()
    assert window.journal.is_open and window.journal.toggle.text().startswith("▾")


def test_journal_rows_reflect_snapshot_commands_and_entries(window, app):
    snap = window.snapshot
    ledger = replace(snap.ledger, entries=[
        LedgerEntry("10:00:05", "console", "export written", "abc", "export", "green"),
        LedgerEntry("10:00:01", "shadow", "SWAP · completed", "def", "system", "ink")],
        commands=(CommandView("22222222-0000-0000-0000-000000000000", "set_link_active", "RUNNING", "2026-10-04T10:00:07+00:00", "{}"),
                  CommandView("11111111-0000-0000-0000-000000000000", "add_network_node", "CONFIRMED", "2026-10-04T10:00:03+00:00", "{}")))
    rows = journal_rows(replace(snap, ledger=ledger))
    assert [(r.at, r.text, r.status, r.tone, r.origin) for r in rows] == [
        ("10:00:01", "shadow · SWAP · completed", "SYSTEM", "ink", "ledger"),
        ("10:00:03", "add_network_node · 11111111", "CONFIRMED", "green", "command"),
        ("10:00:05", "console · export written", "EXPORT", "green", "ledger"),
        ("10:00:07", "set_link_active · 22222222", "RUNNING", "amber", "command")]
    window._on_snapshot(replace(snap, ledger=ledger))
    assert window.journal.rows() == rows
    hosts = [window.journal.list_lay.itemAt(i).widget() for i in range(window.journal.list_lay.count())]
    window._on_snapshot(replace(snap, ledger=ledger))           # an identical poll rebuilds nothing
    assert hosts == [window.journal.list_lay.itemAt(i).widget() for i in range(window.journal.list_lay.count())]
    window._on_snapshot(replace(snap, ledger=replace(ledger, entries=[], commands=())))
    assert window.journal.rows() == [] and journal_rows(None) == []


def test_login_source_selection_calls_the_right_factory_and_opens_topology(app, tmp_path):
    provider = SyntheticProvider(ROOT, tmp_path / "initial")
    built = []

    def service_factory():
        built.append("service")
        return SyntheticProvider(ROOT, tmp_path / f"service-{len(built)}", profile="faults")

    window = ConsoleWindow(provider, state_root=tmp_path, service_factory=service_factory)
    window._on_snapshot(provider.snapshot())
    window.show()
    try:
        drain(app)
        assert window.current_screen == "boot" and window.canvas.currentWidget() is window.boot
        boot = window.boot
        assert list(boot.source_rows) == ["demo", "measured", "service"]
        assert [(row.option.label, row.option.note) for row in boot.source_rows.values()] == [
            ("Demonstration", "simulated input · real pipeline"),
            ("Measured telemetry (local JSON plug)", "read only"), ("Local service", "operator workspace")]
        # The synthetic UI fixture is not a login source: no row is marked for it.
        assert boot.selected_source is None and not any(row.on for row in boot.source_rows.values())
        assert not boot.open_button.isEnabled() and window.active_source_key() == "synthetic"
        assert boot.operator.full_text == window.snapshot.operator
        assert boot.build.text() == window.snapshot.boot.build
        assert window.boot.check_lay.count() == len(window.snapshot.boot.checks)
        assert boot.boundary_values() == [("Simulation", "ENABLED"), ("Shadow", "ENABLED"),
                                          ("Physical", "LOCKED"), ("Hardware access", "DISABLED")]
        assert not hasattr(window, "open_synthetic_lab")                         # no UI path to the fixture
        asked = []
        original = window.source_factory
        window.source_factory = lambda key: asked.append(key) or original(key)
        boot.source_rows["service"].click()
        assert boot.selected_source == "service" and not built and not asked     # marking applies nothing
        boot.open_button.click()
        drain(app)
        assert asked == ["service"] and built == ["service"]
        assert window.current_screen == "topology" and window.active_source_key() == "service"
        assert window.topbar.source_button.text() == "Local service ▾"
        window.topbar.source_button.click()                                      # back to login
        assert window.current_screen == "boot" and window.boot.selected_source == "service"
        window.boot.open_button.click()                                          # same source: no new switch
        assert asked == ["service"] and window.current_screen == "topology"
        # Demonstration goes through open_demo_source(): the real service pipeline on the
        # demonstration journal, with the backend's simulated input source.
        from quantum_autopilot.console.service_provider import ServiceProvider
        demo_calls = []
        original_demo = window.open_demo_source
        window.open_demo_source = lambda: demo_calls.append(1) or original_demo()
        window.provider.start()                                                  # as the application does
        window.rail.sign_out.click()
        window.boot.source_rows["demo"].click()
        window.boot.open_button.click()
        drain(app)
        assert demo_calls == [1] and asked == ["service", "demo"] and window.active_source_key() == "demo"
        active = window.provider.active
        assert isinstance(active, ServiceProvider) and active.is_demo and active.service.is_demo
        assert active.service.control_journal_path == (tmp_path / "demo" / "brazil-institutions-v1" / "sandbox_journal.sqlite").resolve()
        deadline = time.monotonic() + 30
        while window.snapshot.live is None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        snap = window.snapshot
        assert window.current_screen == "topology" and snap.input_kind == "demo"
        identity = active.service.security.current_identity()
        assert snap.operator == (identity.display_name or identity.subject)
        assert snap.security["subject"] == identity.subject and snap.security["source"] == "os"
        assert snap.capabilities.adapter_id == "assurance-demo-service"
        assert "input origin is identified in each live frame" in snap.capabilities.note
        assert snap.live.source_kind == "simulated"
        # Includes the additive GEO constellation and global operations installed
        # by the current demonstration backend (see test_global_geo_constellation).
        assert len(snap.topology.nodes) == 71 and len(snap.topology.links) == 131
        current_ids = {node.id for node in snap.topology.nodes}
        assert "UNIJORGE" not in current_ids
        assert {"CIMATEC", "CBPF", "IME-USP", "USP", "UNICAMP", "CIQUANTA",
                "PUC-RIO", "UFRJ", "IME-RIO", "IBM-WATSON", "IQMP",
                "GOOGLE-QAI", "BSC", "MS-QUANTUM", "IQC", "XANADU"} <= current_ids
        assert snap.live.policy["ml_control_enabled"] is False
        assert window.topbar.source_button.text() == "Demonstration ▾"
        window.show_screen("boot")
        assert window.boot.selected_source == "demo" and window.boot.source_rows["demo"].on
        window.show_screen("topology")
        # A cancelled measured-file dialog leaves the input and the screen alone.
        window._measured_file_factory = lambda: None
        window.show_screen("boot")
        window.boot.source_rows["measured"].click()
        window.boot.open_button.click()
        assert window.current_screen == "boot" and window.active_source_key() == "demo"
    finally:
        window.close()
        window.deleteLater()
        drain(app)


def test_service_row_is_disabled_without_a_service_factory_and_toast_is_transient(window, app):
    window.show_screen("boot")
    assert not window.boot.source_rows["service"].isEnabled()
    window.boot.select_source("service")
    assert window.boot.selected_source is None and not window.boot.open_button.isEnabled()
    window.show_screen("topology")
    window.toast("Export written")
    assert window._toast.isVisible() and window._toast.text() == "Export written"
    assert window._toast._timer.interval() == 2600 == theme.TOAST_MS
    window._toast._timer.timeout.emit()
    assert not window._toast.isVisible()
