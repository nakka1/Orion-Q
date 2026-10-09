"""Frozen path execution, provenance and native confirmation."""
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from quantum_autopilot.control_plane.topology import SimulatedTopologyStore, default_simulated_topology, DEFAULT_TOPOLOGY_ID
from quantum_autopilot.control_plane.inventory import ConfigureSimulatedLink, AddSimulatedNode
from quantum_autopilot.control_plane.workspace_path import WorkspacePathRequest, run_workspace_path, read_workspace_path


def request(**changes):
    return WorkspacePathRequest(**dict(dict(run_id="run-1", source="alice", repeater="repeater-a",
        destination="bob", expected_version=0, target_fidelity=.8), **changes))


@pytest.mark.parametrize("changes", [
    {"run_id": ""}, {"run_id": "x" * 65}, {"source": "../alice"}, {"repeater": []},
    {"destination": "alice"}, {"expected_version": True}, {"expected_version": -1},
    {"target_fidelity": True}, {"target_fidelity": "0.8"}, {"target_fidelity": float("nan")},
    {"target_fidelity": float("inf")}, {"target_fidelity": 10**1000}, {"target_fidelity": -1},
    {"protocol": "multihop"}, {"topology_id": "other"},
])
def test_request_validation(changes):
    with pytest.raises(ValueError):
        request(**changes)


@pytest.mark.parametrize("f1,f2", [(1, 1), (.9, .9), (.8, .95), (0, 0), (.25, .25)])
def test_existing_executor_matches_werner_formula(tmp_path, f1, f2):
    store = SimulatedTopologyStore(tmp_path / "state.sqlite")
    base = default_simulated_topology()
    frozen = replace(base, links=(replace(base.links[0], nominal_pair_fidelity=f1), replace(base.links[1], nominal_pair_fidelity=f2)))
    store.initialize(frozen)
    result = run_workspace_path(store, request())
    record = result["record"]
    values = record["result"]
    expected = f1 * f2 + (1-f1)*(1-f2)/3
    assert values["conditional_fidelity"] == pytest.approx(expected, abs=1e-7)
    assert store.get(DEFAULT_TOPOLOGY_ID) == frozen
    assert record["source_topology"] == frozen.to_dict()
    assert record["workspace_mutated"] is False
    assert record["physical_action_performed"] is False
    assert values["execution"]["action"] == "SWAP"
    assert values["execution"]["performed_in_simulator"] is True
    assert values["snapshot"]["available_pair_fidelities"] == [f1, f2]
    assert values["snapshot"]["provenance"]["evidence_kind"] == "simulated"
    assert read_workspace_path(SimulatedTopologyStore(store.path), "run-1")["record_sha256"] == result["record_sha256"]


def test_blueprint_frozen_and_result_target_not_met(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "state.sqlite")
    result = run_workspace_path(store, request(target_fidelity=.99))["record"]
    assert not result["result"]["conditional_target_met"]
    assert not result["source_was_persisted"]
    assert store.get(DEFAULT_TOPOLOGY_ID) is None


def test_idempotent_retry_keeps_old_frozen_result(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "state.sqlite")
    original = run_workspace_path(store, request())
    store.add_inventory_node(AddSimulatedNode("n4", "endpoint", 0, "127.0.0.1", 7411))
    replay = run_workspace_path(store, request())
    assert replay["idempotent_replay"]
    assert replay["record"] == original["record"]
    with pytest.raises(ValueError, match="different request"):
        run_workspace_path(store, request(target_fidelity=.9))
    with pytest.raises(ValueError, match="version conflict"):
        run_workspace_path(store, request(run_id="new"))


@pytest.mark.parametrize("mutation", ["disabled", "missing", "parallel", "wrong_role"])
def test_unsupported_path_rejected_without_record(tmp_path, mutation):
    store = SimulatedTopologyStore(tmp_path / "state.sqlite")
    base = default_simulated_topology()
    if mutation == "disabled":
        base = replace(base, links=(replace(base.links[0], active=False), base.links[1]))
    elif mutation == "missing":
        base = replace(base, links=base.links[:1])
    elif mutation == "parallel":
        base = replace(base, links=(*base.links, replace(base.links[0], link_id="parallel")))
    store.initialize(base)
    with pytest.raises(ValueError):
        run_workspace_path(store, request(source="repeater-a", repeater="alice") if mutation == "wrong_role" else request())
    assert read_workspace_path(store) is None
    assert store.get(DEFAULT_TOPOLOGY_ID) == base


def test_link_edit_changes_next_run_not_previous_result(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "state.sqlite")
    before = run_workspace_path(store, request())["record"]
    link = default_simulated_topology().links[0]
    store.configure_inventory_link(ConfigureSimulatedLink("update", link.link_id, link.left_node_id, link.right_node_id, 42, .7, True, 0))
    after = run_workspace_path(store, request(run_id="second", expected_version=1))["record"]
    assert after["result"]["conditional_fidelity"] < before["result"]["conditional_fidelity"]
    assert after["source_topology_sha256"] != before["source_topology_sha256"]
    assert read_workspace_path(store, "run-1")["record"] == before


def test_atomic_failure_and_tampered_record_fail_closed(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "state.sqlite")
    with sqlite3.connect(store.path) as conn:
        conn.execute("CREATE TRIGGER fail_path BEFORE INSERT ON workspace_path_runs BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        run_workspace_path(store, request())
    assert read_workspace_path(store) is None
    assert store.get(DEFAULT_TOPOLOGY_ID) is None
    with sqlite3.connect(store.path) as conn:
        conn.execute("DROP TRIGGER fail_path")
    run_workspace_path(store, request())
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE workspace_path_runs SET record_sha256 = 'tampered'")
    with pytest.raises(ValueError, match="integrity"):
        read_workspace_path(store)
    with pytest.raises(ValueError, match="integrity"):
        run_workspace_path(store, request())


def test_two_store_instances_execute_same_request_once(tmp_path):
    stores = [SimulatedTopologyStore(tmp_path / "state.sqlite") for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda s: run_workspace_path(s, request()), stores))
    assert sorted(r["idempotent_replay"] for r in results) == [False, True]
    assert results[0]["record_sha256"] == results[1]["record_sha256"]


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
        control_journal_path=tmp_path / "state.sqlite"), state_root=tmp_path)


def filled_runner(provider):
    from quantum_autopilot.console.screens.topology import TopologyScreen
    screen = TopologyScreen()
    screen.apply(provider.build())
    runner = screen.path_runner
    runner.begin()
    for key, value in (("source", "alice"), ("repeater", "repeater-a"), ("destination", "bob")):
        runner.fields[key].setCurrentText(value)
    runner.target.setText("0.8")
    return screen, runner


def test_ui_waits_for_persisted_readback(provider):
    screen, runner = filled_runner(provider)
    captured = []
    runner.command.connect(lambda *command: captured.append(command))
    runner.submit()
    assert runner.pending and not runner.run.isEnabled()
    assert screen._snap.workspace_path is None
    provider.snapshot_changed.connect(screen.apply)
    provider.dispatch(*captured[0])
    assert runner.pending is None
    assert "CONFIRMED" in runner.note.text()
    assert provider.snapshot().workspace_path.target_met
    assert provider.service.topology_store.get(DEFAULT_TOPOLOGY_ID) is None
    assert provider.snapshot().observations == ()
    runner.close()
    screen.close()


def test_ui_source_switch_and_version_pinning(provider):
    from quantum_autopilot.console.synthetic import artificial_snapshot
    from quantum_autopilot.console.capabilities import SourceCapabilities
    screen, runner = filled_runner(provider)
    provider.service.topology_store.add_inventory_node(AddSimulatedNode("n4", "endpoint", 0, "127.0.0.1", 7411))
    runner.set_snapshot(provider.build())
    assert runner.version == 0
    runner.submit()
    assert runner.pending
    screen.apply(replace(artificial_snapshot(), input_kind="measured", capabilities=SourceCapabilities(mode="read_only")))
    assert runner.pending is None
    assert not runner.isVisible()
    assert not runner.run.isEnabled()
    runner.submit()
    assert provider.service.workspace_path_result() is None
    screen.close()


def test_service_rejects_extra_fields(provider):
    with pytest.raises(ValueError):
        provider.run_workspace_path(json.dumps(dict(asdict(request()), physical=True)))
    assert provider.service.workspace_path_result() is None


def test_unsigned_export_contains_full_frozen_result(provider, tmp_path):
    provider.run_workspace_path(json.dumps(asdict(request())))
    provider.export_record("workspace-path")
    files = list(tmp_path.glob("console_export_*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    result = payload["workspace_path"]["record"]
    assert result["source_topology"]["topology_version"] == 0
    assert result["result"]["execution"]["action"] == "SWAP"
    assert payload["workspace_path"]["record_sha256"] == provider.snapshot().workspace_path.record_hash


def test_custom_node_ids_and_links_not_demo_preset(tmp_path):
    from quantum_autopilot.control_plane.topology import SimulatedLink
    store = SimulatedTopologyStore(tmp_path / "state.sqlite")
    nodes = tuple(AddSimulatedNode(name, role, 0, "127.0.0.1", 7411).node()
                  for name, role in (("lab-west", "endpoint"), ("relay-7", "repeater"), ("lab-east", "endpoint")))
    base = replace(default_simulated_topology(), nodes=nodes,
        links=(SimulatedLink("west", "lab-west", "relay-7", 12, .95), SimulatedLink("east", "relay-7", "lab-east", 20, .8)))
    store.initialize(base)
    result = run_workspace_path(store, request(source="lab-west", repeater="relay-7", destination="lab-east"))["record"]
    assert result["result"]["conditional_fidelity"] == pytest.approx(.95*.8+.05*.2/3, abs=1e-7)
    assert result["result"]["total_distance_km"] == 32
    assert store.get(DEFAULT_TOPOLOGY_ID) == base


def test_native_async_result_after_navigation(provider, app):
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
        runner = window.screens["topology"].path_runner
        runner.begin()
        for key, value in (("source", "alice"), ("repeater", "repeater-a"), ("destination", "bob")):
            runner.fields[key].setCurrentText(value)
        runner.target.setText(".8")
        runner.submit()
        window.show_screen("node")
        settle(lambda: runner.pending is None and not window._busy)
        assert "CONFIRMED" in runner.note.text()
        assert window._snap.workspace_path.route == ("alice", "repeater-a", "bob")
        runner.close()
    finally:
        window.close()
