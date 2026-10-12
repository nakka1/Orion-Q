"""Informational presentation preserves notifications without repeated gates."""
from dataclasses import replace

import pytest

pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest

from quantum_autopilot.console.capabilities import SourceCapabilities
from quantum_autopilot.console.providers import MockProvider
from quantum_autopilot.console.screens.topology import TopologyScreen
from quantum_autopilot.console.widgets.inspector import CommandStatus
from quantum_autopilot.console.widgets.network_views import build_net_model
from quantum_autopilot.console.widgets.node_window import NodeWindows
from tests.test_console_node_incremental import gui, snapshot, wait_for, open_incremental


def test_legacy_rows_preserved_and_only_new_controls_gated(gui):
    _app, host = gui
    registry, window, _net = open_incremental(host, snapshot(3))
    legacy, controls, order = [], [], []

    def gate():
        controls.append(len(window._cards)); order.append('gate')
        for card in window._cards.values():card.toggle.setEnabled(False)

    def legacy_observer():
        legacy.append(len(window._cards)); order.append('legacy')
        assert not any(card.toggle.isEnabled() for card in window._cards.values())

    registry.controls_changed.connect(gate)
    registry.changed.connect(legacy_observer)
    wait_for(lambda: window.presentation_status()['ready'])
    assert legacy == [0]*18+[1, 2, 3]
    assert controls == [1, 2, 3]
    assert order == ['legacy']*18 + ['gate', 'legacy']*3
    registry.close('HUB')
    assert order[-2:] == ['gate', 'legacy'] and not registry.windows
    registry.shutdown()


def test_readiness_recovery_gated_before_legacy_and_input(gui):
    _app, host = gui
    snap = snapshot(1)
    unavailable = replace(snap.live.links[0], configured_active=None)
    snap.live = replace(snap.live, links=(unavailable,))
    registry, window, _net = open_incremental(host, snap)
    events = []

    def gate():
        events.append('gate')
        for card in window._cards.values():card.toggle.setEnabled(False)

    registry.controls_changed.connect(gate)
    registry.changed.connect(lambda: events.append('legacy'))
    wait_for(lambda: window.presentation_status()['ready'])
    card = next(iter(window._cards.values()))
    assert card.toggle.property('ready') is False and not card.toggle.isEnabled()
    events.clear()
    ready = snapshot(1, tick=2)
    ready.topology.links = [replace(link, configured_active=True) for link in ready.topology.links]
    window.refresh(ready, build_net_model(ready), CommandStatus())
    wait_for(lambda: window.presentation_status()['ready'])
    assert card.toggle.property('ready') is True and not card.toggle.isEnabled()
    assert events == ['gate', 'legacy']
    registry.shutdown()


@pytest.mark.parametrize('allowed,busy,pending', [(True,False,False),(False,False,False),
                                                (True,True,False),(True,False,True)])
def test_topology_gates_incremental_actions_before_input(gui, allowed, busy, pending):
    app, _host = gui

    class SpyTopology(TopologyScreen):
        def __init__(self):
            self.gate_cards = []
            super().__init__()

        def apply_command_capabilities(self):
            if hasattr(self, 'node_windows'):
                self.gate_cards.append(sum(len(w._cards) for w in self.node_windows.windows.values()))
            super().apply_command_capabilities()

    screen = SpyTopology();screen.resize(1200,800);screen.show()
    synthetic = snapshot(3)
    synthetic.topology.links = [replace(link, configured_active=True) for link in synthetic.topology.links]
    base = MockProvider().snapshot()
    caps = SourceCapabilities(adapter_id='widget-fixture', mode='simulation', health='ready',
                              operations=('set_link_active',) if allowed else ())
    base = replace(base, topology=replace(base.topology, nodes=synthetic.topology.nodes,
                  links=synthetic.topology.links), live=synthetic.live, capabilities=caps)
    screen.apply(base)
    screen.commands_busy = busy
    window = screen._open_window('HUB')
    if pending:screen._pending = {'verb': 'set_link_active'}
    legacy = []
    screen.node_windows.changed.connect(lambda: legacy.append(len(window._cards)))
    screen.gate_cards.clear()
    wait_for(lambda: window.presentation_status()['ready'])
    assert screen.gate_cards == [1,2,3]
    assert legacy == [0]*18+[1,2,3]
    enabled = allowed and not busy and not pending
    assert all(card.toggle.isEnabled() == enabled for card in window._cards.values())
    window_events = []
    window.action.connect(lambda *args: window_events.append(args))
    first = next(iter(window._cards.values()))
    QTest.mouseClick(first.toggle, Qt.MouseButton.LeftButton)
    assert bool(window_events) == enabled
    screen.shutdown();screen.close();screen.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def test_synchronous_creation_keeps_conservative_gating(gui):
    _app, host = gui
    snap = snapshot(2);net = build_net_model(snap)
    registry = NodeWindows(host)
    events = []
    registry.controls_changed.connect(lambda: events.append('gate'))
    registry.changed.connect(lambda: events.append('legacy'))
    window = registry.open('HUB', prepare=lambda w:w.refresh(snap, net, CommandStatus()))
    assert window.presentation_status()['ready']
    assert events == ['gate','legacy','gate','legacy']
    registry.close('HUB')
    assert events[-2:] == ['gate','legacy']
    registry.shutdown()
