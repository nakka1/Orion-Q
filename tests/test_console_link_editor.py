"""Link configuration safety, persistence and native readback."""
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from itertools import combinations
from pathlib import Path

import pytest

from quantum_autopilot.control_plane.inventory import ConfigureSimulatedLink, AddSimulatedNode
from quantum_autopilot.control_plane.topology import (
    DEFAULT_TOPOLOGY_ID, SimulatedTopologyStore, SimulatedNodeState, SimulatedLink, default_simulated_topology,
)


def request(**changes):
    return ConfigureSimulatedLink(**dict(dict(mode="create", link_id="direct", left_node_id="alice",
        right_node_id="bob", distance_km=12.123456789, nominal_pair_fidelity=.931234567891,
        active=True, expected_version=0), **changes))


@pytest.mark.parametrize("changes", [
    {"mode": "upsert"}, {"mode": []}, {"link_id": ""}, {"link_id": "<x>"},
    {"left_node_id": None}, {"right_node_id": "alice"}, {"distance_km": True},
    {"distance_km": -1}, {"distance_km": 10001}, {"distance_km": 10**1000},
    {"distance_km": "12"}, {"distance_km": float("nan")}, {"distance_km": float("inf")},
    {"nominal_pair_fidelity": float("nan")}, {"nominal_pair_fidelity": True},
    {"nominal_pair_fidelity": -.01}, {"nominal_pair_fidelity": 1.01},
    {"active": 1}, {"expected_version": True}, {"expected_version": None},
    {"topology_id": "other"},
])
def test_strict_contract(changes):
    with pytest.raises(ValueError):
        request(**changes)


def test_persist_update_and_audit_keep_raw_values(tmp_path):
    path = tmp_path / "inventory.sqlite"
    store = SimulatedTopologyStore(path)
    created = store.configure_inventory_link(request())
    assert created.links[-1].nominal_pair_fidelity == .931234567891
    updated = store.configure_inventory_link(request(mode="update", active=False, expected_version=1))
    assert updated.nodes == created.nodes  # configuration does not fabricate/recompute pairs
    assert updated.topology_version == 2
    assert not updated.links[-1].active
    assert SimulatedTopologyStore(path).get(DEFAULT_TOPOLOGY_ID) == updated
    with sqlite3.connect(path) as connection:
        events = [json.loads(row[0]) for row in connection.execute("SELECT event_json FROM topology_events ORDER BY sequence")]
    assert [event["event_kind"] for event in events] == ["simulated_link_created", "simulated_link_updated"]
    assert events[-1]["payload"]["before"]["active"] is True
    assert events[-1]["payload"]["after"]["active"] is False
    assert events[-1]["payload"]["physical_action_performed"] is False


@pytest.mark.parametrize("changes", [
    {"left_node_id": "absent"}, {"mode": "update"}, {"right_node_id": "repeater-a"},
    {"left_node_id": "repeater-a", "right_node_id": "alice"}, {"expected_version": 5},
])
def test_invalid_topology_changes_do_not_persist_blueprint(tmp_path, changes):
    store = SimulatedTopologyStore(tmp_path / "inventory.sqlite")
    with pytest.raises(ValueError):
        store.configure_inventory_link(request(**changes))
    assert store.get(DEFAULT_TOPOLOGY_ID) is None
    assert store.list_events(DEFAULT_TOPOLOGY_ID) == []


def test_duplicate_rewire_and_unchanged_updates_fail_atomically(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "inventory.sqlite")
    created = store.configure_inventory_link(request())
    for command in (request(expected_version=1), request(mode="update", expected_version=1),
                    request(mode="update", expected_version=1, right_node_id="repeater-a")):
        with pytest.raises(ValueError):
            store.configure_inventory_link(command)
        assert store.get(DEFAULT_TOPOLOGY_ID) == created
        assert len(store.list_events(DEFAULT_TOPOLOGY_ID)) == 1


def test_node_and_link_writers_share_version_lock(tmp_path):
    stores = [SimulatedTopologyStore(tmp_path / "inventory.sqlite") for _ in range(2)]
    def write(index):
        try:
            if index:
                stores[index].configure_inventory_link(request())
            else:
                stores[index].add_inventory_node(AddSimulatedNode("N-12", "endpoint", 0, "127.0.0.1", 7411))
            return "saved"
        except ValueError as exc:
            assert "version conflict" in str(exc)
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2))) == ["conflict", "saved"]
    assert stores[0].get(DEFAULT_TOPOLOGY_ID).topology_version == 1
    assert len(stores[0].list_events(DEFAULT_TOPOLOGY_ID)) == 1


def test_event_failure_rolls_back_update(tmp_path):
    path = tmp_path / "inventory.sqlite"
    store = SimulatedTopologyStore(path)
    before = store.configure_inventory_link(request())
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TRIGGER fail_link_event BEFORE INSERT ON topology_events BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.configure_inventory_link(request(mode="update", expected_version=1, active=False))
    assert store.get(DEFAULT_TOPOLOGY_ID) == before
    assert len(store.list_events(DEFAULT_TOPOLOGY_ID)) == 1


def test_limit_is_not_a_physical_constraint(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "inventory.sqlite")
    nodes = tuple(SimulatedNodeState(f"n{i}", "endpoint", 0, "IDLE") for i in range(32))
    pairs = list(combinations(nodes, 2))
    links = tuple(SimulatedLink(f"l{i}", a.node_id, b.node_id, 1, .9) for i, (a, b) in enumerate(pairs[:256]))
    before = replace(default_simulated_topology(), nodes=nodes, links=links)
    store.initialize(before)
    a, b = pairs[256]
    with pytest.raises(ValueError, match="256"):
        store.configure_inventory_link(request(left_node_id=a.node_id, right_node_id=b.node_id))
    assert store.get(DEFAULT_TOPOLOGY_ID) == before


@pytest.fixture
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    return widgets.QApplication.instance() or widgets.QApplication([])


@pytest.fixture
def provider(tmp_path, app):
    from quantum_autopilot.console.service_provider import ServiceProvider
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
    return ServiceProvider(AssuranceDashboardService(repository_root=Path(__file__).resolve().parents[1],
        control_journal_path=tmp_path / "journal.sqlite"), state_root=tmp_path)


def filled_editor(provider):
    from quantum_autopilot.console.screens.topology import TopologyScreen
    screen = TopologyScreen()
    screen.apply(provider.build())
    editor = screen.link_editor
    editor.begin()
    editor.fields["link_id"].setText("direct")
    editor.fields["left_node_id"].setCurrentText("alice")
    editor.fields["right_node_id"].setCurrentText("bob")
    editor.fields["distance_km"].setText("12.123456789")
    editor.fields["nominal_pair_fidelity"].setText("0.931234567891")
    return screen, editor


@pytest.mark.parametrize("size", [(1440, 900), (1920, 1080), (3440, 1440)])
def test_link_editor_click_preserves_the_shown_shell(provider, app, tmp_path, size):
    """Exercise the embedded screen, not an isolated form or a hidden shell."""
    from PySide6.QtCore import QCoreApplication, QEvent
    from quantum_autopilot.console.shell import ConsoleWindow
    from quantum_autopilot.console.responsive_validation import validate_link_editor_window

    window = ConsoleWindow(provider, skip_auth=True)
    window._on_snapshot(provider.build())
    window.resize(*size)
    window.show()
    try:
        result = validate_link_editor_window(window, tmp_path / "render")
        assert result["passed"], result
    finally:
        window.screens["topology"].link_editor.close()
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_create_then_edit_waits_for_numeric_readback(provider):
    screen, editor = filled_editor(provider)
    captured = []
    editor.command.connect(lambda verb, body: captured.append((verb, body)))
    editor.submit()
    assert editor.pending and not editor.save.isEnabled()
    assert len(screen.graph.links) == 2
    provider.snapshot_changed.connect(screen.apply)
    provider.dispatch(*captured.pop())
    assert editor.pending is None
    assert "CONFIRMED" in editor.note.text()
    assert len(screen.graph.links) == 3
    assert any(event.kind == "SIMULATED_LINK_CREATED" for event in provider.snapshot().topology.events)
    # the three views are one model: the new fibre is in the matrix and in the Links tab as well
    assert "direct" in screen.matrix.link_keys() and screen.link_table.model().rowCount() == 3
    assert editor.fields["nominal_pair_fidelity"].text() == "0.931234567891"
    assert not editor.fields["left_node_id"].isEnabled()
    editor.active.setChecked(False)
    editor.submit()
    provider.dispatch(*captured.pop())
    assert len(screen.graph.links) == 2
    disabled = provider.snapshot().topology.links[-1]
    assert disabled.configured_active is False
    assert disabled.provenance == "simulated"
    assert disabled.qber == "not in this lane"
    assert disabled.id not in {link.id for link in screen.graph.links}
    assert screen.matrix.cell_for("alice", "bob").style == "off"       # still listed, drawn as disabled
    assert editor.version == 2
    screen.resize(960, 640)
    screen.show()
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    # In a small frame the topology metrics (inspector, nothing selected) scroll instead of being clipped.
    page, panel = screen.inspector._pages["insp"], screen.inspector.network_panel
    assert screen.inspector.insp_stack.currentWidget() is panel
    assert page.verticalScrollBar().maximum() > 0
    assert panel.kv.height() >= panel.kv.lay.minimumSize().height()
    editor.close()
    screen.close()


def test_polling_does_not_silently_rebase_form(provider):
    screen, editor = filled_editor(provider)
    provider.service.add_simulated_node(asdict(AddSimulatedNode("N-12", "endpoint", 0, "127.0.0.1", 7411)))
    editor.set_snapshot(provider.build())
    assert editor.version == 0
    captured = []
    editor.command.connect(lambda verb, body: captured.append((verb, body)))
    editor.submit()
    with pytest.raises(ValueError, match="version conflict") as error:
        provider.dispatch(*captured.pop())
    editor.command_failed(str(error.value))
    assert "UNCONFIRMED" in editor.note.text()
    assert editor.fields["link_id"].text() == "direct"
    editor.reload.clicked.emit()
    assert editor.version == 1
    editor.close()
    screen.close()


def test_source_switch_and_busy_cannot_mutate(provider):
    from quantum_autopilot.console.synthetic import artificial_snapshot
    from quantum_autopilot.console.capabilities import SourceCapabilities
    screen, editor = filled_editor(provider)
    captured = []
    editor.command.connect(lambda *command: captured.append(command))
    editor.set_busy(True)
    editor.submit()
    assert not captured
    editor.set_busy(False)
    editor.submit()
    assert editor.pending
    measured = replace(artificial_snapshot(), input_kind="measured", capabilities=SourceCapabilities(mode="read_only"))
    screen.apply(measured)
    assert editor.pending is None
    assert not editor.isVisible()
    editor.submit()
    assert len(captured) == 1
    assert not screen.edit_links.isVisible()
    assert provider.service.topology_store.get(DEFAULT_TOPOLOGY_ID) is None
    screen.close()


def test_dispatch_rejects_extra_and_duplicate_json_fields(provider):
    payload = asdict(request())
    with pytest.raises(ValueError):
        provider.configure_simulated_link(json.dumps(dict(payload, fidelity_measured=.99)))
    with pytest.raises(ValueError):
        provider.configure_simulated_link(json.dumps(payload)[:-1] + ',"active":false}')
    assert provider.service.topology_store.get(DEFAULT_TOPOLOGY_ID) is None


def test_native_shell_async_dispatch_confirms_after_navigation(provider, app):
    import time
    from quantum_autopilot.console.shell import ConsoleWindow
    window = ConsoleWindow(provider, skip_auth=True)
    def settle(condition):
        deadline = time.monotonic() + 15
        while not condition() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        app.processEvents()
        assert condition()
    try:
        window.provider.start()
        settle(lambda: window._snap is not None and not window._busy)
        window.show_screen("topology")
        editor = window.screens["topology"].link_editor
        editor.begin()
        for key, value in (("link_id", "direct"), ("distance_km", "12.1"), ("nominal_pair_fidelity", "0.931234567891")):
            editor.fields[key].setText(value)
        editor.fields["left_node_id"].setCurrentText("alice")
        editor.fields["right_node_id"].setCurrentText("bob")
        editor.submit()
        window.show_screen("node")
        settle(lambda: editor.pending is None and not window._busy)
        assert "CONFIRMED" in editor.note.text()
        assert window._snap.topology.links[-1].id == "direct"
        assert window._snap.topology.links[-1].nominal_pair_fidelity == .931234567891
        editor.close()
    finally:
        window.close()
