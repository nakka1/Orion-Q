"""Exercise node and fibre operations through the same widgets as the operator.

Two journeys in the real window, offscreen or on a display:

* **service** — the application service with its network engine. A node is added
  on the Nodes screen without location, found in the queue under the map, opened
  in its floating window, recalibrated (backend), connected with the link editor,
  placed on the map with "Set location…", its fibre disabled and enabled, the node
  isolated and restored — every step through the live verbs
  (`recalibrate_node`, `set_node_location`, `set_link_active`,
  `set_node_isolation`) and confirmed by the backend readback. A refusal of the
  backend is checked too, and the persisted state is read again after a restart.
* **synthetic** — the developer fixture, which declares none of the live verbs:
  the same widgets must show the node and its fibre in every view and keep the
  node operations disabled.

Nothing is asserted from what the interface believes: every check reads the
provider's snapshot or the persisted records.
"""
from dataclasses import replace
import json
from pathlib import Path
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from .capabilities import SourceCapabilities
from .responsive_validation import run_event_loop, wait_for_node_presentation
from .service_provider import ServiceProvider
from .shell import ConsoleWindow
from .synthetic import SyntheticProvider

NODE_ID = 'VERIFY-ISOLATED-NODE'
LINK_ID = 'VERIFY-CONNECTION'
LATITUDE, LONGITUDE, PLACE = -12.9714, -38.5014, 'Pelourinho'
LIVE_VERBS = ('recalibrate_node', 'set_node_location', 'set_link_active', 'set_node_isolation')


def _idle(window):
    active = window.provider.active
    return (not window.provider.busy and getattr(active, '_future', None) is None
            and not getattr(active, '_selection_futures', ()))


def _readbacks_pending(window):
    """Service completion alone does not acknowledge a configuration in Qt."""
    nodes, topology = window.screens['node'], window.screens['topology']
    return (any(getattr(nodes, name, None) is not None
                for name in ('_pending_add', '_pending_stage', '_pending_network'))
            or topology._pending is not None or topology.link_editor.pending is not None)


def _wait_node_windows(window, deadline, timeout):
    topology = window.screens['topology']

    def remaining_ms():
        remaining = int((deadline - monotonic()) * 1000)
        if remaining <= 0:
            raise TimeoutError(f'node/link operation did not complete in {timeout} seconds')
        return min(5000, remaining)

    for opened in tuple(topology.node_windows.windows.values()):
        if opened.isVisible():
            wait_for_node_presentation(opened, lambda: topology._snap, lambda: topology._net,
                                       timeout_ms=remaining_ms())
    remaining_ms()  # A ready/no-window case must also honor the original deadline.


def _settle(window, timeout=30):
    deadline = monotonic() + timeout
    while True:
        QApplication.processEvents()
        if _idle(window) and not _readbacks_pending(window):
            _wait_node_windows(window, deadline, timeout)
            return
        if monotonic() >= deadline:
            raise TimeoutError(f'node/link operation did not complete in {timeout} seconds')
        run_event_loop(10)


def _resolved(window, topology, timeout=40):
    """Wait for the backend readback and the actual displayed node controls."""
    _settle(window, timeout=timeout)


def _node(provider, node_id=NODE_ID):
    return next((node for node in provider.snapshot().topology.nodes if node.id == node_id), None)


def _link(provider, link_id=LINK_ID):
    return next((link for link in provider.snapshot().topology.links if link.id == link_id), None)


def _add_node(window, nodes, app):
    window.show_screen('node')
    app.processEvents()
    for key, value in (('id', NODE_ID), ('address', '127.0.0.1'), ('port', '7411')):
        nodes.fields[key].setText(value)
    QTest.mouseClick(nodes.provision, Qt.MouseButton.LeftButton)
    _settle(window)


def _configure_link(window, topology, app, peer, *, active=True, existing=False):
    editor = topology.link_editor
    topology.inspector.set_tab('links')
    topology.edit_links.click()
    app.processEvents()
    if existing:
        editor.selection.setCurrentIndex(editor.selection.findData(LINK_ID))
    else:
        editor.fields['link_id'].setText(LINK_ID)
        editor.fields['left_node_id'].setCurrentText(peer)
        editor.fields['right_node_id'].setCurrentText(NODE_ID)
        editor.fields['distance_km'].setText('3.2')
        editor.fields['nominal_pair_fidelity'].setText('0.93')
    editor.active.setChecked(active)
    QTest.mouseClick(editor.save, Qt.MouseButton.LeftButton)
    _settle(window)
    confirmed = editor.pending is None and editor.note.text().startswith('CONFIRMED')
    editor.close()
    app.processEvents()
    return confirmed


def _views_show(topology, node_id, link_id=None, style=None):
    """The node (and its fibre) in every view of the one model."""

    placed = node_id in topology.geo.placed_ids()
    queued = node_id in topology.queue.node_ids()
    shown = (placed != queued and node_id in topology.matrix.node_ids()
             and node_id in {node.id for node in topology.graph.nodes})
    if link_id is None:
        return shown
    link = topology._net.link(link_id)
    located = link is not None and all(topology._net.node(ident).located for ident in (link.a, link.b))
    drawn = (link_id in topology.geo.drawn_link_keys()) == located and not topology.queue.drawn_link_keys()
    cell = next((cell for row in topology.matrix.records.grid for cell in row if cell.link == link_id), None)
    shown = shown and drawn and cell is not None and link_id in {link.id for link in topology.graph.all_links}
    return shown and (style is None or (cell.style == style and topology._net.link(link_id).style == style))


def _service_journey(window, provider, folder, checks, app, service, paths, destination):
    nodes, topology = window.screens['node'], window.screens['topology']
    before = provider.snapshot()
    peer = before.topology.nodes[0].id
    checks['live_verbs_declared'] = all(before.capabilities.allows(verb) for verb in LIVE_VERBS)

    # 1. a node added without location waits in the queue, with no line
    _add_node(window, nodes, app)
    node = _node(provider)
    if node is None:
        window.grab().save(str(folder / 'node-add-failure.png'))
        raise ValueError(f'node was not added: {nodes.form_note.text()}; {window.statusBar().currentMessage()}')
    checks['node_added_without_location'] = not node.located and len(provider.snapshot().topology.nodes) == \
        len(before.topology.nodes) + 1
    window.show_screen('topology')
    run_event_loop(30)
    app.processEvents()
    checks['geo_is_the_view_shown'] = topology.view == 'GEO' and topology.graph_stack.currentWidget() is topology.geo
    checks['new_node_waits_in_queue'] = (NODE_ID in topology.queue.node_ids() and NODE_ID not in topology.geo.placed_ids()
                                         and _views_show(topology, NODE_ID))
    checks['node_starts_without_lines'] = (len(provider.snapshot().topology.links) == len(before.topology.links)
        and not any(NODE_ID in (link.a, link.b) for link, *_rest in topology.geo.segments()))
    window.grab().save(str(folder / 'new-node-in-queue.png'))

    # 2. the queue item is clickable: selection + floating window
    topology.queue.reveal(NODE_ID)                 # the band pages when the chips do not fit
    app.processEvents()
    QTest.mouseClick(topology.queue.chips[NODE_ID], Qt.MouseButton.LeftButton)
    _settle(window)
    node_window = topology.node_windows.windows.get(NODE_ID)
    checks['queue_click_opens_floating_window'] = (node_window is not None and node_window.isVisible()
        and window.selection.kind == 'node' and window.selection.id == NODE_ID)
    if node_window is None:
        raise ValueError('queue click did not open the node window')

    # 3. full recalibration, executed by the backend and read back
    QTest.mouseClick(node_window.recalibrate, Qt.MouseButton.LeftButton)
    checks['recalibration_shows_pending'] = node_window.status.text().startswith('PENDING')
    _resolved(window, topology)
    checks['recalibration_confirmed_by_backend'] = (node_window.status.text().startswith('CONFIRMED')
        and _node(provider).calibration_count == 1)
    live_node = provider.snapshot().live.node(NODE_ID)
    checks['calibration_report_shown'] = (live_node.calibration is not None
        and node_window.calibration_kv.texts().get('Calibration') == live_node.calibration.calibration_id)

    # 4. a fibre to a located or unlocated peer: the connection is drawn from the queue
    checks['link_configuration_confirmed'] = _configure_link(window, topology, app, peer)
    checks['queued_node_keeps_its_connection'] = _views_show(topology, NODE_ID, LINK_ID)
    window.grab().save(str(folder / 'connected-node-in-queue.png'))

    # 5. "Set location…": the node moves onto the map with its data and its connection
    QTest.mouseClick(node_window.locate_button, Qt.MouseButton.LeftButton)
    dialog = topology.location_dialog
    checks['location_dialog_opens'] = dialog.isVisible() and dialog.node_id == NODE_ID
    dialog.fields['latitude'].setText(str(LATITUDE))
    dialog.fields['longitude'].setText(str(LONGITUDE))
    dialog.fields['place'].setText(PLACE)
    QTest.mouseClick(dialog.apply, Qt.MouseButton.LeftButton)
    _resolved(window, topology)
    node = _node(provider)
    checks['location_persisted_by_backend'] = (node.located and node.latitude == LATITUDE
        and node.longitude == LONGITUDE and node.place == PLACE and node.calibration_count == 1)
    position = topology.geo.node_position(NODE_ID)
    expected = topology.geo.transform().to_px(LONGITUDE, LATITUDE)
    checks['node_moved_from_queue_to_map'] = (NODE_ID not in topology.queue.node_ids() and position is not None
        and abs(position.x() - expected[0]) < 1e-6 and abs(position.y() - expected[1]) < 1e-6)
    checks['connection_preserved_on_map'] = _views_show(topology, NODE_ID, LINK_ID) and _link(provider) is not None
    dialog.close()
    window.grab().save(str(folder / 'located-node.png'))

    # 6. disable the fibre: the line stays, styled as disabled; then a refusal; then enable
    topology._link_clicked(LINK_ID)
    _settle(window)
    panel = topology.inspector.link_panel
    QTest.mouseClick(panel.toggle, Qt.MouseButton.LeftButton)
    checks['fibre_disable_shows_pending'] = panel.status.text().startswith('PENDING') and _link(provider).configured_active
    _resolved(window, topology)
    checks['fibre_disabled_by_backend'] = (_link(provider).configured_active is False
        and panel.status.text().startswith('CONFIRMED') and _views_show(topology, NODE_ID, LINK_ID, 'off'))
    window.grab().save(str(folder / 'disabled-fibre.png'))
    QTest.mouseClick(panel.toggle, Qt.MouseButton.LeftButton)
    _resolved(window, topology)
    checks['fibre_enabled_by_backend'] = _link(provider).configured_active is True

    # 7. isolate: the backend switches the node's fibres off; enabling one is refused; restore brings them back
    QTest.mouseClick(node_window.isolate, Qt.MouseButton.LeftButton)
    _resolved(window, topology)
    checks['isolation_confirmed_by_backend'] = (_node(provider).isolated and node_window.isolate.text() == 'Restore'
        and node_window.status.text().startswith('CONFIRMED') and _link(provider).configured_active is False)
    checks['isolated_node_and_fibre_stay_visible'] = _views_show(topology, NODE_ID, LINK_ID, 'off')
    window.grab().save(str(folder / 'isolated-node.png'))
    version = provider.snapshot().topology.version
    topology._link_clicked(LINK_ID)
    _settle(window)
    QTest.mouseClick(panel.toggle, Qt.MouseButton.LeftButton)
    _resolved(window, topology)
    checks['backend_refusal_is_shown'] = (panel.status.text().startswith('UNKNOWN') and 'isolated' in panel.status.text()
        and provider.snapshot().topology.version == version and _link(provider).configured_active is False)
    QTest.mouseClick(node_window.recalibrate, Qt.MouseButton.LeftButton)
    _resolved(window, topology)
    checks['recalibrate_preserves_isolation'] = _node(provider).isolated and _node(provider).calibration_count == 2
    QTest.mouseClick(node_window.isolate, Qt.MouseButton.LeftButton)
    _resolved(window, topology)
    checks['restore_brings_the_fibre_back'] = (not _node(provider).isolated and _link(provider).configured_active is True
        and node_window.isolate.text() == 'Isolate')

    # 8. the link editor still disables a fibre; the node stays, the line is drawn as disabled
    checks['editor_disables_link'] = (_configure_link(window, topology, app, peer, active=False, existing=True)
        and _link(provider).configured_active is False and _views_show(topology, NODE_ID, LINK_ID, 'off'))
    snapshot = provider.snapshot()
    checks['views_match_the_snapshot'] = (
        topology.matrix.node_ids() == [node.id for node in snapshot.topology.nodes]
        and sorted(topology.geo.placed_ids() + topology.queue.node_ids()) == sorted(node.id for node in snapshot.topology.nodes)
        and topology.matrix.link_keys() == {link.id for link in snapshot.topology.links}
        and {link.id for link in topology.graph.links} == {link.id for link in snapshot.topology.links
                                                           if link.configured_active is not False})
    topology.node_windows.close_all()
    window.grab().save(str(folder / 'disconnected-node.png'))
    window.show_screen('node')
    app.processEvents()
    window.grab().save(str(folder / 'nodes.png'))

    # 9. persistence and the journal
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
    restarted = AssuranceDashboardService(repository_root=paths.repository_root,
        control_journal_path=service.control_journal_path)
    reopened = ServiceProvider(restarted, state_root=destination / 'state').build()
    again = next(node for node in reopened.topology.nodes if node.id == NODE_ID)
    checks['state_survives_restart'] = (again.calibration_count == 2 and again.located and again.place == PLACE
        and not again.isolated and next(link for link in reopened.topology.links
                                        if link.id == LINK_ID).configured_active is False)
    from quantum_autopilot.control_plane.console_commands import read_commands
    receipts = read_commands(service.control_journal_path)
    verbs = {receipt['record']['verb'] for receipt in receipts}
    checks['live_verbs_journalled'] = set(LIVE_VERBS) <= verbs
    checks['refusal_recorded_as_unknown'] = any(receipt['record']['verb'] == 'set_link_active'
        and receipt['record']['events'][-1]['state'] == 'UNKNOWN' for receipt in receipts)
    checks['actions_never_physical'] = bool(receipts) and all(
        receipt['record']['physical_action_performed'] is False for receipt in receipts)


def _synthetic_journey(window, provider, folder, checks, app):
    nodes, topology = window.screens['node'], window.screens['topology']
    before = provider.snapshot()
    peer = before.topology.nodes[0].id
    checks['live_verbs_not_declared'] = not any(before.capabilities.allows(verb) for verb in LIVE_VERBS)
    _add_node(window, nodes, app)
    if _node(provider) is None:
        window.grab().save(str(folder / 'node-add-failure.png'))
        raise ValueError(f'node was not added: {nodes.form_note.text()}; {window.statusBar().currentMessage()}')
    window.show_screen('topology')
    run_event_loop(30)
    app.processEvents()
    checks['node_reaches_every_view'] = _views_show(topology, NODE_ID)
    checks['node_starts_without_lines'] = all(NODE_ID not in (link.a, link.b) for link in provider.snapshot().topology.links)
    topology.set_view('GRAPH')
    app.processEvents()
    topology.graph.grab().save(str(folder / 'new-node.png'))
    new_node = next(node for node in topology.graph.nodes if node.id == NODE_ID)
    QTest.mouseClick(topology.graph, Qt.MouseButton.LeftButton, pos=topology.graph._position(new_node).toPoint())
    _settle(window)
    node_window = topology.node_windows.windows.get(NODE_ID)
    checks['new_node_clickable'] = node_window is not None and node_window.isVisible()
    if node_window is None:
        raise ValueError('node click did not open the node window')
    checks['undeclared_operations_stay_disabled'] = not any(button.isEnabled() for button in (
        node_window.recalibrate, node_window.isolate, node_window.locate_button))
    checks['no_wave_is_invented'] = all(not node_window.chart.visible_samples(key) for key in node_window.chart.keys())
    checks['link_configuration_confirmed'] = _configure_link(window, topology, app, peer)
    checks['views_draw_configured_connection'] = _views_show(topology, NODE_ID, LINK_ID)
    window.grab().save(str(folder / 'connected-node.png'))
    checks['editor_disables_link'] = (_configure_link(window, topology, app, peer, active=False, existing=True)
        and _views_show(topology, NODE_ID, LINK_ID, 'off'))
    checks['unconnected_node_remains_visible'] = NODE_ID in {node.id for node in topology.graph.nodes}
    provider.advance()
    provider.advance()
    app.processEvents()
    checks['updates_preserve_edits'] = (_node(provider) is not None and _link(provider) is not None
        and _link(provider).configured_active is False and _views_show(topology, NODE_ID, LINK_ID, 'off'))
    topology.node_windows.close_all()


def validate_functional_topology(paths, destination):
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    state = destination / 'state' / uuid4().hex
    service = AssuranceDashboardService(repository_root=paths.repository_root,
        control_journal_path=state / 'functional.sqlite3')
    service_provider = ServiceProvider(service, state_root=state, replay_limit=1)
    service_provider.refresh()
    sources = [('service', service_provider), ('synthetic', SyntheticProvider(
        paths.repository_root, destination / 'synthetic', seed=7))]
    runs = []
    app = QApplication.instance()
    for name, provider in sources:
        folder = destination / name
        folder.mkdir(exist_ok=True)
        window = ConsoleWindow(provider, skip_auth=True)
        from quantum_autopilot.desktop_instance import mark_isolated_diagnostic_window
        mark_isolated_diagnostic_window(window)
        window.resize(1440, 900)
        window.show()
        window.provider.start()
        _settle(window)
        run_event_loop(250)
        checks = {}
        topology = window.screens['topology']
        try:
            if name == 'service':
                _service_journey(window, provider, folder, checks, app, service, paths, destination)
            else:
                _synthetic_journey(window, provider, folder, checks, app)
            read_only = replace(provider.snapshot(), input_kind='measured',
                capabilities=SourceCapabilities(adapter_id='validation-read-only', mode='read_only'))
            topology.apply(read_only)
            from .widgets import Btn
            gated = [button for button in topology.findChildren(Btn) if button.property('operation')]
            checks['read_only_controls_locked'] = bool(gated) and not any(button.isEnabled() for button in gated)
        finally:
            topology.link_editor.close()
            topology.location_dialog.close()
            window.close()
            window.deleteLater()
            app.processEvents()
        runs.append({'source': name, 'passed': all(checks.values()), 'checks': checks,
                     'physical_action_performed': False})
    report = {'passed': all(run['passed'] for run in runs), 'runs': runs,
              'qt_platform': app.platformName(), 'physical_action_performed': False}
    (destination / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
