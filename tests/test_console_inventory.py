"""Persistent simulator inventory: transaction, native form and source boundaries."""
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from quantum_autopilot.control_plane.inventory import AddSimulatedNode
from quantum_autopilot.control_plane.topology import DEFAULT_TOPOLOGY_ID, SimulatedTopologyStore
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService


def request(**overrides):
    return AddSimulatedNode(**dict(dict(node_id="N-12", role="endpoint", expected_version=0,
                                       address="127.0.0.1", port=7411), **overrides))


@pytest.mark.parametrize("overrides", [
    {"node_id": "<script>"}, {"node_id": ""}, {"node_id": "x" * 65},
    {"role": "core"}, {"role": []}, {"port": True}, {"port": 0}, {"port": 65536},
    {"port": "7411"}, {"expected_version": True}, {"expected_version": -1},
    {"expected_version": None}, {"address": "example.com"}, {"address": "http://localhost"},
    {"topology_id": "other"},
])
def test_invalid_commands_rejected(overrides):
    with pytest.raises(ValueError):
        request(**overrides)


def test_credentials_and_unknown_fields_rejected():
    for field in ("auth", "serial", "banks", "physical_authorization"):
        with pytest.raises(ValueError):
            AddSimulatedNode.from_dict(dict(asdict(request()), **{field: "secret"}))


def test_persistence_event_and_no_fabricated_links(tmp_path):
    path = tmp_path / "inventory.sqlite"
    store = SimulatedTopologyStore(path)
    updated = store.add_inventory_node(request())
    reopened = SimulatedTopologyStore(path).get(DEFAULT_TOPOLOGY_ID)
    assert reopened == updated
    assert reopened.topology_version == 1
    assert len(reopened.nodes) == 4
    assert len(reopened.links) == 2
    node = reopened.nodes[-1]
    assert node.available_pair_fidelities == ()
    assert node.telemetry["inventory"]["scope"] == "metadata_only_no_connection"
    events = store.list_events(DEFAULT_TOPOLOGY_ID)
    assert len(events) == 1
    assert events[0]["event_kind"] == "simulated_node_added"
    with sqlite3.connect(path) as connection:
        event = json.loads(connection.execute("SELECT event_json FROM topology_events").fetchone()[0])
    assert event["payload"]["physical_action_performed"] is False
    for invalid in (request(node_id="N-13"), request(expected_version=1)):
        with pytest.raises(ValueError):
            store.add_inventory_node(invalid)
        assert store.get(DEFAULT_TOPOLOGY_ID) == updated
        assert store.list_events(DEFAULT_TOPOLOGY_ID) == events


def test_limit_rejects_without_mutation(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "inventory.sqlite")
    for index in range(29):
        last = store.add_inventory_node(request(node_id=f"extra-{index}", expected_version=index))
    with pytest.raises(ValueError, match="32"):
        store.add_inventory_node(request(node_id="overflow", expected_version=29))
    assert store.get(DEFAULT_TOPOLOGY_ID) == last


def test_independent_writers_cannot_overwrite_each_other(tmp_path):
    stores = [SimulatedTopologyStore(tmp_path / "inventory.sqlite") for _ in range(2)]
    def write(index):
        try:
            stores[index].add_inventory_node(request(node_id=f"N-{index}"))
            return "persisted"
        except ValueError as exc:
            assert "version conflict" in str(exc)
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2))) == ["conflict", "persisted"]
    assert len(stores[0].get(DEFAULT_TOPOLOGY_ID).nodes) == 4
    assert len(stores[0].list_events(DEFAULT_TOPOLOGY_ID)) == 1


def test_event_failure_rolls_back_topology(tmp_path):
    path = tmp_path / "inventory.sqlite"
    store = SimulatedTopologyStore(path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TRIGGER fail_event BEFORE INSERT ON topology_events BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        store.add_inventory_node(request())
    assert store.get(DEFAULT_TOPOLOGY_ID) is None
    assert store.list_events(DEFAULT_TOPOLOGY_ID) == []


@pytest.fixture
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    return widgets.QApplication.instance() or widgets.QApplication([])


@pytest.fixture
def provider(tmp_path, app):
    from quantum_autopilot.console.service_provider import ServiceProvider
    service = AssuranceDashboardService(repository_root=Path(__file__).resolve().parents[1],
                                        control_journal_path=tmp_path / "journal.sqlite")
    return ServiceProvider(service, state_root=tmp_path, replay_limit=3)


def filled_view(provider):
    from quantum_autopilot.console.screens.node import NodeScreen
    view = NodeScreen()
    view.apply(provider.build())
    for name, value in (("id", "N-12"), ("address", "127.0.0.1"), ("port", "7411")):
        view.fields[name].setText(value)
    return view


def test_native_form_waits_for_readback_and_survives_restart(provider, tmp_path):
    """The service carries a network engine, so the form sends `add_network_node`."""
    from quantum_autopilot.console.service_provider import ServiceProvider
    from quantum_autopilot.control_plane.inventory import AddNetworkNode
    view = filled_view(provider)
    captured = []
    view.command.connect(lambda verb, body: captured.append((verb, body)))
    assert set(view.fields) == {"id", "address", "port", "latitude", "longitude", "place"}   # no credentials asked
    assert not view.roles["CORE"].isEnabled()
    assert not view.location.isHidden()
    before = len(view.rows)
    view._provision()
    assert view._pending_network and "PENDING" in view.form_note.text()
    assert not view.provision.isEnabled()
    assert len(view.rows) == before == 3
    assert not view._shadow_nodes
    assert captured[0][0] == "add_network_node"
    sent = json.loads(captured[0][1])
    assert set(sent) == set(AddNetworkNode.__dataclass_fields__)
    assert (sent["latitude"], sent["longitude"], sent["place"]) == (None, None, "")
    provider.snapshot_changed.connect(view.apply)
    provider.dispatch(*captured[0])
    assert view._pending_network is None
    assert len(view.rows) == 4 and "N-12" in view.rows
    assert "CONFIRMED" in view.form_note.text() and "queue" in view.form_note.text()
    assert view.fields["id"].text() == ""
    assert view._selected == "N-12" and view.rows["N-12"].on
    assert view.record.values()["Connection address"] == "127.0.0.1"
    assert view.origin_chip.text() == "PERSISTED"
    restarted_service = AssuranceDashboardService(repository_root=provider.service.repository_root,
                                                  control_journal_path=tmp_path / "journal.sqlite")
    reopened = ServiceProvider(restarted_service, state_root=tmp_path).build()
    assert reopened.topology.nodes[-1].connection_address == "127.0.0.1"
    assert reopened.topology.nodes[-1].connection_port == 7411
    assert reopened.topology.version == 1
    assert reopened.topology.persisted
    view.resize(960, 640)
    view.show()
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    assert view.log_scroll.width() <= 250 and view.rows["N-12"].toolTip().startswith("N-12")
    view.close()


def test_persistent_simulator_form_keeps_its_own_request(provider):
    """A source that only declares `add_simulated_node` keeps the older, location-free request."""
    from quantum_autopilot.console.capabilities import SourceCapabilities
    snap = provider.build()
    limited = replace(snap, capabilities=SourceCapabilities(
        adapter_id="simulator-inventory-only", mode="simulation", health="ready",
        operations=("add_simulated_node", "reload")))
    from quantum_autopilot.console.screens.node import NodeScreen
    view = NodeScreen()
    view.apply(limited)
    for name, value in (("id", "N-12"), ("address", "127.0.0.1"), ("port", "7411")):
        view.fields[name].setText(value)
    captured = []
    view.command.connect(lambda verb, body: captured.append((verb, body)))
    assert view.location.isHidden() and view.provision.property("operation") == "add_simulated_node"
    view._provision()
    assert view._pending_add and not view.provision.isEnabled()
    assert captured[0][0] == "add_simulated_node"
    assert set(json.loads(captured[0][1])) == set(asdict(request()))
    provider.service.add_simulated_node(json.loads(captured[0][1]))
    view.apply(replace(provider.build(), capabilities=limited.capabilities))
    assert view._pending_add is None and "CONFIRMED" in view.form_note.text()
    assert "N-12" in view.rows
    view.close()


def test_stale_form_fails_without_false_success(provider):
    view = filled_view(provider)
    captured = []
    view.command.connect(lambda verb, body: captured.append((verb, body)))
    provider.service.add_simulated_node(asdict(request(node_id="external")))
    view._provision()
    with pytest.raises(ValueError, match="version conflict") as error:
        provider.dispatch(*captured[0])
    view.command_failed(str(error.value))
    assert view._pending_add is None and view._pending_network is None
    assert "UNCONFIRMED" in view.form_note.text()
    assert view.fields["id"].text() == "N-12"
    assert not view._shadow_nodes
    assert "N-12" not in [n.node_id for n in provider.service.topology_store.get(DEFAULT_TOPOLOGY_ID).nodes]
    view.close()


def test_source_switch_clears_pending_and_cannot_write(provider):
    from quantum_autopilot.console.synthetic import artificial_snapshot
    from quantum_autopilot.console.capabilities import SourceCapabilities
    view = filled_view(provider)
    view._provision()
    assert view._pending_network
    measured = replace(artificial_snapshot(), input_kind="measured",
                       capabilities=SourceCapabilities(mode="read_only"))
    view.apply(measured)
    assert view._pending_network is None and view._pending_add is None
    assert not view.provision.isEnabled()
    view._provision()
    assert not view._shadow_nodes
    assert provider.service.topology_store.get(DEFAULT_TOPOLOGY_ID) is None
    view.close()


def test_demonstration_preserves_edited_workspace(provider):
    provider.service.add_simulated_node(asdict(request()))
    before = provider.service.topology_store.get(DEFAULT_TOPOLOGY_ID)
    provider.run_scenario()
    assert provider.service.topology_store.get(DEFAULT_TOPOLOGY_ID) == before
    assert provider.service.topology_store.list_events(DEFAULT_TOPOLOGY_ID)[0]["event_kind"] == "simulated_node_added"
