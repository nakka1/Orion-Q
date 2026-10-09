"""Interactive row construction yields without disguising partial science data."""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.contract import Link, LiveLink, LiveNode, LiveView, Node  # noqa: E402
from quantum_autopilot.console.widgets.inspector import CommandStatus  # noqa: E402
from quantum_autopilot.console.widgets.network_views import build_net_model  # noqa: E402
from quantum_autopilot.console.widgets.node_window import NodeWindow, NodeWindows  # noqa: E402


def snapshot(degree=10, *, tick=1, source='fixture-A', kind='simulated'):
    """Identified inputs carry both channel receipts, measured samples and clock."""
    nodes = [Node('HUB', 'ENDPOINT', 'up', .5, .5)]
    nodes.extend(Node(f'PEER-{i}', 'ENDPOINT', 'up', .5, .5) for i in range(degree))
    links = [Link('HUB', f'PEER-{i}', f'FIBRE-{i}', id=f'FIBRE-{i}') for i in range(degree)]
    live_links = tuple(LiveLink(link.id, link.a, link.b, 2.5, 'ok', configured_active=True,
        effective=True, fidelity=.95-tick/1000, qber=.01+tick/10000,
        skr_bps=123+tick, stability=.98-tick/1000, wave=(.97, .98-tick/1000), wave_rate_hz=20,
        provenance={'channels': {'configuration': {
            'classical': {'channel_id': f'C-{i}'}, 'quantum': {'channel_id': f'Q-{i}'}},
            'status': 'validated'}}) for i, link in enumerate(links))
    live_node = LiveNode('HUB', 'endpoint', 'node', 'up', links=tuple(item.id for item in links),
                         temperature_c=20+tick/10)
    live = LiveView(tick, tick/20, kind, source, nodes=(live_node,), links=live_links,
                    source_seed=17, policy={'wave_rate_hz': 20, 'wave_window_s': 12},
                    technologies={'clock_s': tick/20})
    return SimpleNamespace(topology=SimpleNamespace(nodes=nodes, links=links), live=live)


@pytest.fixture
def gui():
    app = QApplication.instance() or QApplication([])
    theme.set_theme('dark'); theme.install(app)
    host = QWidget(); host.resize(1200, 800); host.show()
    yield app, host
    host.close(); host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def wait_for(predicate, timeout=4000):
    for _ in range(timeout//10):
        if predicate():
            return
        QTest.qWait(10)
    assert predicate(), 'visual presentation did not complete within the test deadline'


def open_incremental(host, snap):
    net = build_net_model(snap)
    registry = NodeWindows(host, incremental=True)
    window = registry.open('HUB', prepare=lambda w: w.refresh(snap, net, CommandStatus()))
    return registry, window, net


def test_default_contract_remains_synchronous_and_full_readback(gui):
    _app, host = gui
    snap = snapshot()
    window = NodeWindow('HUB', host)
    assert window.refresh(snap, build_net_model(snap), CommandStatus())
    status = window.presentation_status()
    assert status['ready'] and status['pending'] == status['pending_items'] == 0
    assert status['expected_link_keys'] == status['completed_link_keys']
    assert len(window.connection_texts()) == len(window.legend_labels()) == 10
    assert window.content_texts()['title'] == 'HUB'
    assert window.content_texts()['cards'] == window.connection_texts()
    assert window.presentation_snapshot()[0] is snap


def test_real_timer_yields_one_card_per_turn_then_presents_exact_snapshot(gui):
    _app, host = gui
    snap = snapshot()
    registry, window, net = open_incremental(host, snap)
    status = window.presentation_status()
    assert not status['ready'] and status['presented_tick'] is None
    assert status['pending'] == 1 and status['pending_items'] == 24
    assert status['expected_sections'] == ('identity', 'state', 'calibration', 'calibration_links', 'routes')
    assert status['completed_sections'] == ('calibration_links',)
    assert not window.connection_texts() and window.presentation_snapshot() is None
    assert window.chart.keys() == [link.key for link in net.links_of('HUB')]
    assert window.chart.network_time_s == snap.live.network_time_s
    assert '0/10 conexões' in window.content_texts()['loading']
    counts = []
    core_rows = []
    window.materialized.connect(lambda: counts.append(len(window.connection_texts())))
    window.materialized.connect(lambda: core_rows.append(sum(len(widget.rows) for widget in (
        window.identity, window.state, window.calibration_kv, window.calibration_links, window.routes))))
    wait_for(lambda: window.presentation_status()['ready'])
    assert counts == [0]*18 + list(range(1, 11))
    assert core_rows == list(range(1, 19)) + [18]*10
    assert status['pending_core_rows'] == 18
    assert window.presentation_status()['pending_core_rows'] == 0
    assert window.presentation_status()['completed_sections'] == status['expected_sections']
    assert window.presentation_snapshot() == (snap, net)
    assert window.presentation_status()['step_max_ms'] > 0
    assert not window.content_texts()['loading']
    assert all(f'C-{i} ↔ Q-{i}' in card['controllers']
               for i, card in enumerate(window.connection_texts().values()))
    registry.shutdown()


def test_latest_generation_coalesces_during_construction_without_restart(gui):
    _app, host = gui
    registry, window, _net = open_incremental(host, snapshot())
    wait_for(lambda: len(window._cards) >= 2)
    retained = dict(window._cards)
    latest = snapshot(tick=9)
    latest_net = build_net_model(latest)
    queue_before = tuple(window._pending_widgets)
    window.refresh(latest, latest_net, CommandStatus())
    assert tuple(window._pending_widgets)[:len(queue_before)] == queue_before
    assert all(window._cards[key] is card for key, card in retained.items())
    assert window.chart.network_time_s == .45
    assert window.presentation_status()['core_tick'] == 9
    assert window.presentation_snapshot() is None
    wait_for(lambda: window.presentation_status()['ready'])
    reference = NodeWindow('HUB', host)
    reference.refresh(latest, latest_net, CommandStatus())
    assert window.connection_texts() == reference.connection_texts()
    assert window.legend_labels() == reference.legend_labels()
    assert window.identity.texts() == reference.identity.texts()
    assert window.state.texts() == reference.state.texts()
    assert window.chart.series == reference.chart.series
    status = window.presentation_status()
    assert status['presented_tick'] == 9 and status['network_time_s'] == .45
    assert status['completed_link_keys'] == status['expected_link_keys']
    assert window.presentation_snapshot()[0] is latest
    registry.shutdown()


def test_close_cancels_timer_and_reopen_uses_new_window(gui):
    _app, host = gui
    snap = snapshot()
    registry, window, net = open_incremental(host, snap)
    wait_for(lambda: bool(window._cards))
    timer = window._presentation_timer
    registry.close('HUB')
    assert not timer.isActive()
    assert window.presentation_status()['state'] == 'cancelled'
    assert window.presentation_status()['pending_items'] == 0
    replacement = registry.open('HUB', prepare=lambda w: w.refresh(snap, net, CommandStatus()))
    assert replacement is not window and not replacement.presentation_status()['ready']
    wait_for(lambda: replacement.presentation_status()['ready'])
    assert len(registry.windows) == 1
    registry.shutdown()


def test_hidden_window_pauses_and_resumes_latest_source(gui):
    _app, host = gui
    registry, window, _net = open_incremental(host, snapshot())
    wait_for(lambda: bool(window._cards))
    window.hide()
    count = len(window._cards)
    latest = snapshot(tick=4)
    window.refresh(latest, build_net_model(latest), CommandStatus())
    QTest.qWait(80)
    assert len(window._cards) == count
    assert window.presentation_status()['state'] == 'paused'
    assert window.presentation_status()['pending'] == 0
    window.show()
    wait_for(lambda: window.presentation_status()['ready'])
    assert window.presentation_snapshot()[0] is latest
    registry.shutdown()


def test_source_change_retires_old_controls_and_received_chart_prefix(gui):
    _app, host = gui
    registry, window, _net = open_incremental(host, snapshot(tick=5))
    wait_for(lambda: len(window._cards) >= 2)
    old_cards = dict(window._cards)
    latest = snapshot(3, tick=6, source='fixture-B', kind='measured')
    window.refresh(latest, build_net_model(latest), CommandStatus())
    assert not window._cards
    assert window.presentation_status()['source_kind'] == 'measured'
    assert window.presentation_status()['source_label'] == 'fixture-B'
    assert window.presentation_snapshot() is None
    assert window.chart._received == window.chart._latest
    wait_for(lambda: window.presentation_status()['ready'])
    assert len(window._cards) == 3
    assert all(window._cards[key] is not card for key, card in old_cards.items() if key in window._cards)
    assert window.presentation_snapshot()[0] is latest
    registry.shutdown()


def test_link_removal_and_reorder_preserve_only_compatible_rows(gui):
    _app, host = gui
    registry, window, _net = open_incremental(host, snapshot(4))
    wait_for(lambda: window.presentation_status()['ready'])
    retained = dict(window._cards)
    latest = snapshot(2, tick=2)
    latest.topology.links.reverse()
    latest.live = replace(latest.live, links=tuple(reversed(latest.live.links)))
    window.refresh(latest, build_net_model(latest), CommandStatus())
    assert not window.presentation_status()['ready']
    wait_for(lambda: window.presentation_status()['ready'])
    assert list(window.connection_texts()) == ['FIBRE-1', 'FIBRE-0']
    assert all(window._cards[key] is retained[key] for key in window._cards)
    assert window.presentation_status()['expected_link_keys'] == ('FIBRE-1', 'FIBRE-0')
    registry.shutdown()


def test_materialized_controls_receive_capability_gate_before_next_turn(gui):
    _app, host = gui
    snap = snapshot(3)
    registry, window, _net = open_incremental(host, snap)
    calls = []
    def gate():
        calls.append(len(window._cards))
        for card in window._cards.values():
            card.toggle.setEnabled(False)
    registry.changed.connect(gate)
    wait_for(lambda: window.presentation_status()['ready'])
    assert calls == [0]*18+[1, 2, 3]
    assert not any(card.toggle.isEnabled() for card in window._cards.values())
    registry.shutdown()


def test_node_removal_cancels_incomplete_presentation(gui):
    _app, host = gui
    registry, window, _net = open_incremental(host, snapshot())
    latest = snapshot(0)
    latest.topology.nodes.clear()
    latest.live = replace(latest.live, nodes=(), links=())
    assert not window.refresh(latest, build_net_model(latest), CommandStatus())
    assert window.presentation_status()['state'] == 'removed'
    assert window.presentation_status()['pending_items'] == window.presentation_status()['pending'] == 0
    assert window.presentation_snapshot() is None
    registry.shutdown()


def test_factory_failure_is_explicit_preserves_work_and_recovers(gui, monkeypatch):
    from quantum_autopilot.console.widgets import node_window as module
    _app, host = gui
    factory = module._FibreCard
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda kind, error, tb: errors.append(kind.__name__))
    def failed_factory(*_args, **_kwargs):
        raise RuntimeError('credential=never-expose-this')
    monkeypatch.setattr(module, '_FibreCard', failed_factory)
    registry, window, _net = open_incremental(host, snapshot(2))
    wait_for(lambda: window.presentation_status()['state'] == 'error')
    status = window.presentation_status()
    assert status['state'] == 'error'
    assert not status['ready'] and status['pending'] == 0
    assert status['pending_items'] == 3  # one legend succeeded; its card remains first.
    assert status['completed_link_keys'] == ()
    assert window.presentation_snapshot() is None
    assert 'RuntimeError' in status['error']
    assert 'never-expose' not in str(status) + str(window.content_texts())
    assert not errors
    monkeypatch.setattr(module, '_FibreCard', factory)
    latest = snapshot(2, tick=8)
    net = build_net_model(latest)
    window.refresh(latest, net, CommandStatus())
    wait_for(lambda: window.presentation_status()['ready'])
    assert not window.presentation_status()['error']
    assert window.presentation_snapshot() == (latest, net)
    assert len(window.connection_texts()) == 2
    registry.shutdown()


def test_population_failure_retires_partial_card_without_false_completion(gui, monkeypatch):
    from quantum_autopilot.console.widgets import node_window as module
    _app, host = gui
    show_link = module._FibreCard.show_link
    retired = []
    def failed_population(self, *_args, **_kwargs):
        self.destroyed.connect(lambda: retired.append(True))
        raise ValueError('token=never-expose-this')
    monkeypatch.setattr(module._FibreCard, 'show_link', failed_population)
    registry, window, _net = open_incremental(host, snapshot(1))
    wait_for(lambda: window.presentation_status()['state'] == 'error')
    assert window.connection_texts() == {}
    assert window.presentation_snapshot() is None
    assert window.presentation_status()['pending_items'] == 1
    assert not window.presentation_status()['completed_link_keys']
    assert window.presentation_status()['ready'] is False
    assert 'ValueError' in window.content_texts()['loading']
    assert 'never-expose' not in str(window.content_texts())
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert retired == [True]
    monkeypatch.setattr(module._FibreCard, 'show_link', show_link)
    latest = snapshot(1, tick=3)
    window.refresh(latest, build_net_model(latest), CommandStatus())
    wait_for(lambda: window.presentation_status()['ready'])
    assert len(window.connection_texts()) == 1
    registry.shutdown()


def test_core_rows_error_is_explicit_and_recovers_all_latest_fields(gui, monkeypatch):
    _app, host = gui
    registry, window, _net = open_incremental(host, snapshot(1))
    original = window.identity.set_rows
    def failure(_rows):raise ValueError('password=do-not-display')
    monkeypatch.setattr(window.identity, 'set_rows', failure)
    wait_for(lambda: window.presentation_status()['state'] == 'error')
    status = window.presentation_status()
    assert not status['ready'] and status['pending'] == 0
    assert status['completed_sections'] == ('calibration_links',)
    assert window.identity.isHidden() and window.presentation_snapshot() is None
    assert 'do-not-display' not in str(status) + str(window.content_texts())
    monkeypatch.setattr(window.identity, 'set_rows', original)
    latest = snapshot(1, tick=6)
    net = build_net_model(latest)
    window.refresh(latest, net, CommandStatus())
    wait_for(lambda: window.presentation_status()['ready'])
    reference = NodeWindow('HUB', host);reference.refresh(latest, net, CommandStatus())
    assert window.content_texts()['identity'] == reference.content_texts()['identity']
    assert window.content_texts()['state'] == reference.content_texts()['state']
    assert status['expected_sections'] == window.presentation_status()['completed_sections']
    assert window.presentation_snapshot() == (latest, net)
    registry.shutdown()
