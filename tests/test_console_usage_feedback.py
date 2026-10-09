"""Visible path results, stable busy pages, hover help and geographic omissions."""
import json
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

pytest.importorskip('PySide6')
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPoint, QEvent  # noqa: E402
from PySide6.QtGui import QHelpEvent  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QToolTip  # noqa: E402

from tests import test_workspace_path as shared  # noqa: E402
from quantum_autopilot.console.providers import MockProvider  # noqa: E402
from quantum_autopilot.console.shell import ConsoleWindow  # noqa: E402
from quantum_autopilot.console.widgets import Btn  # noqa: E402
from quantum_autopilot.console.widgets.button_help import button_description  # noqa: E402
from quantum_autopilot.console.contract import Node, Link  # noqa: E402
from quantum_autopilot.console.widgets.geo_map import GeoMap  # noqa: E402
from quantum_autopilot.console.widgets.network_views import build_net_model  # noqa: E402

app, provider = shared.app, shared.provider


def test_geographic_routes_break_at_unlocated_nodes_but_keep_inventory(app):
    nodes = (Node('a', 'ENDPOINT', latitude=-12.938, longitude=-38.389), Node('unknown', 'ENDPOINT'),
             Node('b', 'ENDPOINT', latitude=-13., longitude=-38.505))
    links = (Link('a', 'unknown', 'a-u', id='a-u'), Link('unknown', 'b', 'u-b', id='u-b'))
    net = build_net_model(SimpleNamespace(topology=SimpleNamespace(nodes=nodes, links=links), live=None))
    geo = GeoMap()
    try:
        geo.set_model(replace(net, route=('a', 'unknown', 'b'), layer='entanglement'))
        geo.set_queue_anchors({'a-u': 100, 'u-b': 200}, {'unknown': 150})
        assert not geo.segments() and not geo.route_segments()
        assert len(geo.net.links_of('unknown')) == 2
        placed = replace(net.nodes[1], located=True, latitude=-12.98, longitude=-38.45)
        geo.set_model(replace(net, nodes=(placed, net.nodes[0], net.nodes[2]), route=('a', 'unknown', 'b')))
        assert len(geo.segments()) == 2 and len(geo.route_segments()) == 1
    finally:
        geo.close()


@pytest.mark.parametrize('target', ['.8', '1'])
@pytest.mark.parametrize('size', [(570, 550), (960, 650)])
def test_confirmed_path_opens_visible_results_with_full_receipt(provider, app, target, size):
    screen, runner = shared.filled_runner(provider)
    try:
        runner.resize(*size)
        runner.target.setText(target)
        captured = []
        runner.command.connect(lambda *command: captured.append(command))
        provider.snapshot_changed.connect(screen.apply)
        runner.run.click()
        provider.dispatch(*captured[0])
        app.processEvents()
        assert runner.pending is None
        assert runner.tabs.currentWidget() is runner.result_page
        assert runner.results.isVisible() and runner.results.viewport().height() > 70
        assert runner.rect().contains(runner.results.mapTo(runner, QPoint(0, 0)))
        assert runner.rect().contains(runner.results.mapTo(runner, runner.results.rect().bottomRight()))
        assert 'CONFIRMED' in runner.results.toPlainText()
        payload = json.loads(runner.results.toPlainText().split('PERSISTED RESULT READBACK\n', 1)[1])
        result = provider.snapshot().workspace_path
        assert payload['record_hash'] == result.record_hash
        assert payload['run_id'] == runner.run_id
        assert payload['target_met'] == (target == '.8')
        runner.reload.click()
        assert runner.tabs.currentWidget() is runner.form_scroll
        assert 'PREVIOUS RESULT' in runner.note.text()
    finally:
        runner.close()
        screen.close()


@pytest.mark.parametrize('target', ['nan', 'inf', '-1', '2', ''])
def test_invalid_request_is_visible_and_never_dispatched(provider, app, target):
    screen, runner = shared.filled_runner(provider)
    try:
        captured = []
        runner.command.connect(lambda *args: captured.append(args))
        runner.target.setText(target)
        runner.run.click()
        app.processEvents()
        assert 'REFUSED' in runner.status.text()
        assert not captured and runner.pending is None and runner.run.isEnabled()
        assert provider.service.workspace_path_result() is None
    finally:
        runner.close()
        screen.close()


def test_backend_error_is_shown_in_results_and_can_be_retried(provider, app):
    screen, runner = shared.filled_runner(provider)
    try:
        runner.submit()
        runner.command_failed('Topology version changed')
        assert runner.tabs.currentWidget() is runner.result_page
        assert 'UNCONFIRMED' in runner.results.toPlainText()
        assert 'Topology version changed' in runner.status.text()
        assert runner.pending is None and runner.run.isEnabled()
    finally:
        runner.close()
        screen.close()


def test_busy_command_keeps_page_and_reading_controls_enabled(app, provider):
    window = ConsoleWindow(provider, skip_auth=True)
    try:
        window.provider.running = True
        window._on_snapshot(provider.build())
        window.show_screen('topology')
        topology = window.screens['topology']
        window._on_busy(True)
        assert topology.isEnabled() and topology.geo.isEnabled()
        assert not topology.recalibrate.isEnabled() and not topology.isolate.isEnabled()
        assert all(button.isEnabled() for button in topology.toggles.values())
        window._on_snapshot(provider.build())
        assert not topology.recalibrate.isEnabled()
        window._on_busy(False)
        assert topology.recalibrate.isEnabled()
    finally:
        window.close()


def test_hover_help_is_displayed_and_tracks_dynamic_action(app):
    button = Btn('Isolate')
    try:
        button.show()
        app.processEvents()
        point = button.rect().center()
        app.sendEvent(button, QHelpEvent(QEvent.Type.ToolTip, point, button.mapToGlobal(point)))
        app.processEvents()
        assert QToolTip.isVisible() and 'simulated network' in QToolTip.text()
        button.setText('Restore')
        assert 'Restore' in button.toolTip()
        button.setToolTip('Custom explanation')
        button.setText('Isolate')
        assert button.toolTip() == 'Custom explanation'
    finally:
        QToolTip.hideText()
        button.close()


def test_all_workspace_buttons_expose_help_in_both_themes(app):
    from quantum_autopilot.console import theme
    for name in ('dark', 'light'):
        theme.set_theme(name)
        theme.install(app)
        window = ConsoleWindow(MockProvider(), skip_auth=True)
        try:
            window._on_snapshot(window.provider.active.snapshot())
            buttons = window.canvas.findChildren(QAbstractButton)
            missing = [b.text() or b.accessibleName() for b in buttons if not b.toolTip().strip()]
            assert len(buttons) > 100 and not missing, missing
            assert 'persisted result' in button_description('RUN FROZEN PATH')
            assert 'without running new cycles' in button_description('READ LATEST 20')
        finally:
            window.close()
    theme.set_theme('dark')
    theme.install(app)
