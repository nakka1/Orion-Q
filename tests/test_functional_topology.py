"""Persisted node actions and operator workflows, without device actuation."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from quantum_autopilot.control_plane.inventory import SimulatedNodeAction
from quantum_autopilot.control_plane.topology import DEFAULT_TOPOLOGY_ID, SimulatedTopologyStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def app():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.console import theme
    application = QApplication.instance() or QApplication([])
    theme.install(application)
    return application


def action(action='isolate', node_id='repeater-a', version=0):
    return SimulatedNodeAction(action, node_id, version)


@pytest.mark.parametrize('overrides', [dict(action='physical'), dict(action=[]), dict(node_id='<node>'),
    dict(node_id=''), dict(expected_version=True), dict(expected_version=-1), dict(topology_id='other')])
def test_invalid_node_action_rejected(overrides):
    with pytest.raises(ValueError):
        SimulatedNodeAction(**{**asdict(action()), **overrides})


def test_no_credentials_or_physical_authority_accepted():
    for key in ('mode', 'physical_authorization', 'auth', 'prediction'):
        with pytest.raises(ValueError):
            SimulatedNodeAction.from_dict({**asdict(action()), key: 'unexpected'})


def test_isolation_is_atomic_and_retains_disabled_configuration(tmp_path):
    path = tmp_path / 'topology.sqlite'
    store = SimulatedTopologyStore(path)
    isolated = store.apply_inventory_node_action(action())
    node = isolated.node('repeater-a')
    assert node.memory_state == 'IDLE' and node.available_pair_fidelities == ()
    assert node.telemetry['console_control']['isolated'] is True
    assert all(not link.active for link in isolated.links)
    assert len(isolated.links) == 2
    assert SimulatedTopologyStore(path).get(DEFAULT_TOPOLOGY_ID) == isolated
    event = store.list_events(DEFAULT_TOPOLOGY_ID)[-1]
    assert event['event_kind'] == 'simulated_node_isolated'
    with sqlite3.connect(path) as connection:
        full_event = json.loads(connection.execute('SELECT event_json FROM topology_events').fetchone()[0])
    assert set(full_event['payload']['disabled_links']) == {link.link_id for link in isolated.links}
    assert full_event['payload']['physical_action_performed'] is False
    with pytest.raises(ValueError, match='already isolated'):
        store.apply_inventory_node_action(action(version=1))
    assert store.get(DEFAULT_TOPOLOGY_ID) == isolated


def test_recalibration_resets_memory_without_improving_fidelities_or_reconnecting(tmp_path):
    store = SimulatedTopologyStore(tmp_path / 'topology.sqlite')
    calibrated = store.apply_inventory_node_action(action('recalibrate'))
    assert calibrated.node('repeater-a').telemetry['console_control']['calibration_count'] == 1
    assert calibrated.node('repeater-a').available_pair_fidelities == ()
    assert all(link.nominal_pair_fidelity == 0.9 for link in calibrated.links)
    isolated = store.apply_inventory_node_action(action(version=1))
    again = store.apply_inventory_node_action(action('recalibrate', version=2))
    assert again.links == isolated.links
    assert again.node('repeater-a').telemetry['console_control']['isolated'] is True
    assert again.node('repeater-a').telemetry['console_control']['calibration_count'] == 2


@pytest.mark.parametrize('invalid', ['stale', 'missing'])
def test_rejected_actions_leave_topology_and_audit_unchanged(tmp_path, invalid):
    store = SimulatedTopologyStore(tmp_path / 'topology.sqlite')
    before = store.apply_inventory_node_action(action('recalibrate'))
    events = store.list_events(DEFAULT_TOPOLOGY_ID)
    request = action(version=0) if invalid == 'stale' else action(node_id='missing', version=1)
    with pytest.raises(ValueError):
        store.apply_inventory_node_action(request)
    assert store.get(DEFAULT_TOPOLOGY_ID) == before
    assert store.list_events(DEFAULT_TOPOLOGY_ID) == events


def test_event_failure_rolls_back_node_and_all_incident_links(tmp_path):
    path = tmp_path / 'topology.sqlite'
    store = SimulatedTopologyStore(path)
    before = store.apply_inventory_node_action(action('recalibrate'))
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TRIGGER fail_action BEFORE INSERT ON topology_events BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='injected failure'):
        store.apply_inventory_node_action(action(version=1))
    assert SimulatedTopologyStore(path).get(DEFAULT_TOPOLOGY_ID) == before


def test_competing_actions_cannot_overwrite_a_newer_topology(tmp_path):
    path = tmp_path / 'topology.sqlite'
    stores = [SimulatedTopologyStore(path) for _ in range(2)]
    def run(index):
        try:
            stores[index].apply_inventory_node_action(action('recalibrate'))
            return 'confirmed'
        except ValueError as error:
            assert 'version conflict' in str(error)
            return 'stale'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, range(2))) == ['confirmed', 'stale']
    assert stores[0].get(DEFAULT_TOPOLOGY_ID).topology_version == 1


def test_unknown_node_action_can_be_observed_without_claiming_execution(tmp_path):
    from quantum_autopilot.control_plane.console_commands import ConsoleCommandJournal
    from quantum_autopilot.control_plane.command_reconciliation import reconcile_command
    from quantum_autopilot.control_plane.topology import _sha256
    path = tmp_path / 'topology.sqlite'
    store = SimulatedTopologyStore(path)
    request = asdict(action())
    journal = ConsoleCommandJournal(path)
    command_id = journal.begin('apply_simulated_node_action', request)
    journal.transition(command_id, 'RUNNING')
    store.apply_inventory_node_action(action())
    record = journal.transition(command_id, 'UNKNOWN', {'reason': 'execution_or_readback_failed', 'error_type': 'OSError'})
    report = reconcile_command(path, command_id, _sha256(record))
    assert report['record']['comparison']['target_status'] == 'matches_current_state'
    assert report['record']['comparison']['command_effect_proven'] is False
    assert report['record']['journal_mutated'] is False


def test_native_operator_journey_for_service_and_synthetic_sources(app, tmp_path):
    from quantum_autopilot.console.topology_validation import validate_functional_topology
    report = validate_functional_topology(SimpleNamespace(repository_root=ROOT), tmp_path / 'journeys')
    assert report['passed'], json.dumps(report, indent=2)
    assert {run['source'] for run in report['runs']} == {'service', 'synthetic'}


@pytest.mark.parametrize('node_id', ['alice', 'bob', 'repeater-a'])
def test_isolation_changes_only_the_selected_nodes_links(tmp_path, node_id):
    from quantum_autopilot.control_plane.topology import default_simulated_topology
    store = SimulatedTopologyStore(tmp_path / 'topology.sqlite')
    before = default_simulated_topology()
    after = store.apply_inventory_node_action(action(node_id=node_id))
    for original, updated in zip(before.links, after.links):
        if node_id in (original.left_node_id, original.right_node_id):
            assert updated.active is False
        else:
            assert updated == original


def test_isolation_disables_actual_path_execution(tmp_path):
    from quantum_autopilot.control_plane.workspace_path import WorkspacePathRequest, run_workspace_path
    store = SimulatedTopologyStore(tmp_path / 'topology.sqlite')
    isolated = store.apply_inventory_node_action(action())
    request = WorkspacePathRequest('disconnected', 'alice', 'repeater-a', 'bob', 1, .7)
    with pytest.raises(ValueError, match='enabled link'):
        run_workspace_path(store, request)
    assert store.get(DEFAULT_TOPOLOGY_ID) == isolated


def test_current_graph_can_be_clicked_before_its_next_paint(app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from quantum_autopilot.console.contract import Node
    from quantum_autopilot.console.widgets.figures import TopologyGraph
    graph = TopologyGraph()
    graph.resize(700, 600)
    graph.show()
    app.processEvents()
    node = Node('just-added', 'ENDPOINT', x=.7, y=.6)
    selected = []
    graph.node_selected.connect(selected.append)
    graph.set_topology([node], [])
    QTest.mouseClick(graph, Qt.MouseButton.LeftButton, pos=graph._position(node).toPoint())
    assert selected == [node.id]
    graph.close()


def test_all_32_node_identifiers_remain_separately_clickable(app):
    from quantum_autopilot.console.service_provider import ServiceProvider
    from quantum_autopilot.console.widgets.figures import TopologyGraph
    from tests.test_console_service_provider import FakeService
    payload = {'persisted': True, 'topology': {'topology_id': DEFAULT_TOPOLOGY_ID, 'topology_version': 0,
        'nodes': [{'node_id': f'long-identifier-{index:02d}-' + 'a'*30, 'role': 'endpoint', 'memory_state': 'IDLE'}
                  for index in range(32)], 'links': []}}
    snap = ServiceProvider(FakeService(topology=payload)).build()
    graph = TopologyGraph()
    graph.set_topology(snap.topology.nodes, [])
    graph.resize(550, 550)
    graph.show()
    app.processEvents()
    rectangles = [graph._node_rect(node) for node in graph.nodes]
    assert len(rectangles) == 32
    assert all(not a.intersects(b) for i, a in enumerate(rectangles) for b in rectangles[i+1:])
    assert all(graph.rect().contains(rect.toRect()) for rect in rectangles)
    graph.close()


def test_local_node_remove_clears_its_links_and_updates_keep_controls(app, tmp_path):
    from quantum_autopilot.console.synthetic import SyntheticProvider
    from quantum_autopilot.console.screens.node import NodeScreen
    from quantum_autopilot.control_plane.inventory import ConfigureSimulatedLink
    provider = SyntheticProvider(ROOT, tmp_path)
    payload = dict(id='local', role='ENDPOINT', address='127.0.0.1', port=7411, protocol='QNET-TLS')
    provider.dispatch('stage_node', json.dumps(payload))
    provider.dispatch('configure_simulated_link', json.dumps(asdict(
        ConfigureSimulatedLink('create', 'local-link', 'N-01', 'local', 1, .9, True, 1))))
    view = NodeScreen()
    view.apply(provider.snapshot())
    row = view.rows['local']
    view._pick('local')
    remove = view.remove_button
    assert not remove.isHidden() and remove.property('operation') == 'remove_staged_node'
    assert view.origin_chip.text() == 'SHADOW'
    provider.advance()
    view.apply(provider.snapshot())
    assert view.rows['local'] is row and view.remove_button is remove and not remove.isHidden()
    provider.dispatch('remove_staged_node', 'local')
    assert all(node.id != 'local' for node in provider.snapshot().topology.nodes)
    assert all('local' not in (link.a, link.b) for link in provider.snapshot().topology.links)
    with pytest.raises(ValueError):
        provider.dispatch('stage_node', json.dumps({**payload, 'id': 'espaço'}))
    view.close()
