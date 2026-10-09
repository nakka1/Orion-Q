"""Dynamic node details stay readable and reachable in small floating windows."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.contract import Link, Node  # noqa: E402
from quantum_autopilot.console.widgets.inspector import CommandStatus  # noqa: E402
from quantum_autopilot.console.widgets.network_views import build_net_model  # noqa: E402
from quantum_autopilot.console.widgets.node_window import NodeWindow  # noqa: E402


def snapshot(degree):
    nodes = [Node("HUB", "REPEATER", "up", .5, .5)]
    nodes.extend(Node(f"PEER-{i}", "ENDPOINT", "up", .5, .5) for i in range(degree))
    links = [Link("HUB", f"PEER-{i}", f"FIBRE-{i}", id=f"FIBRE-{i}") for i in range(degree)]
    return SimpleNamespace(topology=SimpleNamespace(nodes=nodes, links=links), live=None)


def settle(app):
    for _ in range(6):
        app.processEvents()


@pytest.mark.parametrize("mode", ["dark", "light"])
@pytest.mark.parametrize("size", [(400, 300), (700, 520), (1280, 800)])
@pytest.mark.parametrize("degree", [4, 24])
def test_node_window_scrolls_dynamic_details_without_compressing_rows(mode, size, degree, tmp_path):
    app = QApplication.instance() or QApplication([])
    theme.set_theme(mode)
    theme.install(app)
    host = QWidget()
    host.resize(*size)
    window = NodeWindow("HUB", host)
    window.fit_host()
    host.show()
    window.show()
    settle(app)
    try:
        # Populate after showing the empty window, as the topology's click handler does.
        for count in (1, degree):
            snap = snapshot(count)
            assert window.refresh(snap, build_net_model(snap), CommandStatus())
            # Wrapped field heights settle through Qt layout events after a
            # publication; inspect the painted geometry before scrolling.
            settle(app)
            assert window.legend_host.y() >= window.chart.geometry().bottom() + 1
            for row in window._legend.values():
                assert row.height() >= row.peer.fontMetrics().height() + 12
            for row in window.identity.rows.values():
                assert row.height() >= row.key_label.fontMetrics().height() + 12
            assert window.scroll.verticalScrollBar().maximum() > 0

        window.grab().save(str(tmp_path / "node-top.png"))
        # The last connection and its action must be reachable by actual scrolling.
        last = list(window._cards.values())[-1]
        window.scroll.ensureWidgetVisible(last.toggle, 0, 0)
        settle(app)
        point = last.toggle.mapTo(window.scroll.viewport(), QPoint(0, 0))
        assert 0 <= point.y() <= window.scroll.viewport().height() - last.toggle.height()
        assert window.scroll.verticalScrollBar().value() > 0
        assert window.recalibrate.isVisible() and window.isolate.isVisible()
        assert window.recalibrate.y() == window.isolate.y()
        window.grab().save(str(tmp_path / "node-last-connection.png"))
    finally:
        host.close()
        host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        theme.set_theme("dark")
        theme.install(app)


def test_protocol_memory_occupancy_is_independent_of_legacy_inventory():
    """Received bank metrics cannot be hidden by a configured legacy IDLE field."""
    from quantum_autopilot.console.contract import LiveNode, LiveView
    app = QApplication.instance() or QApplication([])
    theme.install(app)
    host = QWidget()
    window = NodeWindow('HUB', host)
    configured = snapshot(0)
    node = LiveNode('HUB', 'repeater', 'repeater', 'up', memory_state='IDLE')
    try:
        for left, right in ((2, 1), (0, 0)):
            sessions = {ident: {'config': {'path': ['HUB', 'REMOTE'],
                'technologies': ['quantum_memory'], 'parameters': {'memory_capacity': 8}},
                'status': 'operational',
                'metrics': {'memory_qubit_occupancy': {'HUB': count}}}
                for ident, count in (('BANK-A', left), ('BANK-B', right))}
            configured.live = LiveView(1, 1., 'simulated', 'Synthetic UI observations',
                                      nodes=(node,), technologies={'sessions': sessions})
            assert window.refresh(configured, build_net_model(configured), CommandStatus())
            texts = window.state.texts()
            assert texts['Memory state · legacy inventory'] == 'IDLE'
            assert texts['Stored pairs · legacy inventory'] == 'none stored'
            assert f'{left + right} qubits ocupados observados' in texts['Memória de protocolos']
            assert '2 bancos por sessão' in texts['Memória de protocolos']
            assert f'BANK-A: {left} qubits ocupados / 8 modos' in texts['Bancos de memória']
            assert f'BANK-B: {right} qubits ocupados / 8 modos' in texts['Bancos de memória']
        sessions['BANK-A']['metrics'] = {}
        assert window.refresh(configured, build_net_model(configured), CommandStatus())
        assert '1 aguardando aquisição' in window.state.texts()['Memória de protocolos']
        assert 'BANK-A: aguardando aquisição' in window.state.texts()['Bancos de memória']
    finally:
        host.close()
        host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
