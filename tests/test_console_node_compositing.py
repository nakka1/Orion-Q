"""Exercise input, overlays and fresh pixels through the responsive viewport."""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.contract import Link, Node  # noqa: E402
from quantum_autopilot.console.stage import ResponsiveStage  # noqa: E402
from quantum_autopilot.console.widgets.inspector import CommandStatus  # noqa: E402
from quantum_autopilot.console.widgets.network_views import build_net_model  # noqa: E402
from quantum_autopilot.console.widgets.node_window import NodeWindow, NodeWindows  # noqa: E402
from quantum_autopilot.console.widgets.palette import CommandPalette, PaletteCommand  # noqa: E402
from quantum_autopilot.console.widgets.toast import Toast  # noqa: E402


def settle(app):
    # Scene updates are coalesced by a timer, including cached item invalidation.
    QTest.qWait(30)
    for _ in range(6):
        app.processEvents()


@pytest.fixture(params=[('dark', 1.0), ('light', 1.0), ('dark', 1.25), ('light', 1.25)])
def canvas(request):
    app = QApplication.instance() or QApplication([])
    previous = theme.current_theme()
    mode, zoom = request.param
    theme.set_theme(mode)
    theme.install(app)
    root = QWidget()
    host = QWidget(root)
    host.setGeometry(0, 0, 1600, 960)
    windows = NodeWindows(host)
    palette, toast = CommandPalette(root), Toast(root)
    stage = ResponsiveStage(root)
    stage.resize(1800, 1120)
    stage.user_zoom = zoom
    stage.show()
    stage._arrange()
    settle(app)
    nodes = [Node(f'N-{i}', 'REPEATER', 'up', .5, .5) for i in range(3)]
    nodes += [Node(f'PEER-{i}', 'ENDPOINT', 'up', .5, .5) for i in range(24)]
    links = [Link('N-0', f'PEER-{i}', f'FIBRE-{i}', id=f'FIBRE-{i}') for i in range(24)]
    snap = SimpleNamespace(topology=SimpleNamespace(nodes=nodes, links=links), live=None)
    net = build_net_model(snap)
    for i in range(3):
        panel = windows.open(f'N-{i}', position=QPoint(400 + i * 30, 60 + i * 20))
        panel.refresh(snap, net, CommandStatus())
        panel.chart.freeze(1.0)
    settle(app)
    try:
        yield app, stage, root, windows, palette, toast
    finally:
        windows.shutdown()
        stage.close()
        stage.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        theme.set_theme(previous)
        theme.install(app)


def viewport_point(stage, widget, point=None):
    local = point or widget.rect().center()
    root_point = widget.mapTo(stage.content, local)
    return stage.mapFromScene(stage.proxy.mapToScene(QPointF(root_point)))


def click(app, stage, widget):
    point = viewport_point(stage, widget)
    assert stage.viewport().rect().contains(point)
    QTest.mouseClick(stage.viewport(), Qt.MouseButton.LeftButton, pos=point)
    settle(app)


def test_overlapping_nodes_raise_dispatch_close_and_reopen(canvas):
    app, stage, _root, windows, _palette, _toast = canvas
    actions, locations = [], []
    windows.action.connect(lambda *args: actions.append(args))
    windows.locate.connect(locations.append)
    panel = windows.windows['N-0']
    # Reopening an existing window must raise both its pixels and its input.
    assert windows.open('N-0') is panel
    settle(app)
    click(app, stage, panel.recalibrate)
    click(app, stage, panel.isolate)
    click(app, stage, panel.locate_button)
    assert actions == [('recalibrate_node', 'node', 'N-0', {}),
                       ('set_node_isolation', 'node', 'N-0', {'isolated': True})]
    assert locations == ['N-0']
    click(app, stage, panel.close_button)
    assert set(windows.windows) == {'N-1', 'N-2'}
    windows.close_all()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settle(app)
    remaining = stage.scene().items()
    assert all(not isinstance(item.widget(), NodeWindow) for item in remaining)
    assert all(item.widget() is not None for item in remaining)
    fresh = windows.open('N-0', position=QPoint(400, 60))
    settle(app)
    click(app, stage, fresh.close_button)
    assert not windows.windows
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settle(app)
    assert stage.scene().items() == remaining


def test_palette_and_notice_remain_above_node_panels(canvas):
    app, stage, _root, _windows, palette, toast = canvas
    runs = []
    palette.run_requested.connect(runs.append)
    command = PaletteCommand('Go to telemetry', 'screen', 'go', 'telemetry')
    palette.open_with([command])
    settle(app)
    # This row overlaps all three panels: test the viewport's actual hit target.
    click(app, stage, palette._rows[0])
    assert runs == [command] and not palette.is_open()
    toast.show_message('NEW TICK', duration_ms=30000)
    settle(app)
    image = stage.viewport().grab().toImage()
    sample = viewport_point(stage, toast, QPoint(8, 8))
    dpr = image.devicePixelRatio()
    assert image.pixelColor(round(sample.x() * dpr), round(sample.y() * dpr)).name() == theme.C.ink


def test_node_updates_pixels_and_scrolls_after_zoom(canvas):
    app, stage, _root, windows, _palette, _toast = canvas
    panel = windows.open('N-0')
    settle(app)
    before = stage.viewport().grab().toImage()
    panel.title.setText('LATEST LIVE TICK')
    settle(app)
    after = stage.viewport().grab().toImage()
    assert before != after
    last = list(panel._cards.values())[-1]
    panel.scroll.ensureWidgetVisible(last.toggle, 0, 0)
    settle(app)
    assert panel.scroll.verticalScrollBar().value() > 0
    selected = []
    panel.select.connect(lambda *args: selected.append(args))
    click(app, stage, last.name)
    assert selected == [('link', 'FIBRE-23')]
    stage.change_zoom(.1)
    settle(app)
    click(app, stage, panel.close_button)
    assert 'N-0' not in windows.windows
