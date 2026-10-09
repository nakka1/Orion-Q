"""Guard committed UI frames, bounded matrix allocation and lookup semantics."""
import os
from dataclasses import asdict, replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from quantum_autopilot.console.capabilities import SourceCapabilities  # noqa: E402
from quantum_autopilot.console.providers import MockProvider  # noqa: E402
from quantum_autopilot.console.source_switch import SwitchableProvider  # noqa: E402
from quantum_autopilot.console.widgets.network_views import (  # noqa: E402
    MatrixModel, NetModel, NetNode, NetLink, matrix_grid,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def node(name):
    return NetNode(name, "ENDPOINT", "node", "up", "up", False, True, 0., 0., "site", 0., 0.)


def link(name, a, b, **kw):
    base = dict(key=name, id=name, a=a, b=b, style="calm", status="up", tone="green",
                fidelity="0.950", qber="1%", skr="10", distance_km=4.,
                configured_active=True, label="", fidelity_value=.95)
    return NetLink(**(base | kw))


def test_indices_preserve_first_match_self_links_and_public_shape():
    first, duplicate = node("A"), replace(node("A"), role="CORE")
    links = (link("one", "A", "B", id="alias"), link("alias", "B", "A"),
             link("self", "A", "A"), link("pair", "A", "B", style="pair"))
    net = NetModel(nodes=(first, duplicate, node("B")), links=links)
    shape = asdict(net)
    assert net.node("A") is first and net.node("missing") is None
    assert net.link("alias") is links[0] and net.link("missing") is None
    assert net.links_of("A") == links[:3]
    assert net.links_of("absent") == ()
    assert net.located == net.nodes and net.unlocated == ()
    assert asdict(net) == shape  # caches are not protocol fields


def test_matrix_defers_cells_and_bounds_cache_for_large_inventory(app):
    net = NetModel(nodes=tuple(node(str(i)) for i in range(300)),
                   links=(link("edge", "0", "1"),))
    model = MatrixModel(); model.set_net(net)
    assert model.rowCount() == 300 and model.columnCount() == 301
    assert len(model._cells) == 0 and len(model._pairs) == 1
    assert model.cell(model.index(0, 1)).text == "—"
    assert model.cell(model.index(0, 2)).text == "0.950"
    assert model.cell(model.index(2, 1)).text == "·"
    for row in range(20):
        for column in range(1, 301):
            model.cell(model.index(row, column))
    assert len(model._cells) == 2048
    assert not model.cell(model.index(0, 0))


@pytest.mark.parametrize("status,text", [("disabled", "OFF"), ("isolated", "ISO"),
                                         ("calibrating", "CAL"), ("compromised", "ALARM")])
def test_matrix_refreshes_latest_status_and_keeps_export_compatible(app, status, text):
    net = NetModel(nodes=(node("A"), node("B")), links=(link("edge", "A", "B"),))
    model = MatrixModel(); model.set_net(net)
    assert model.cell(model.index(0, 2)).text == "0.950"
    changed = replace(net, links=(replace(net.links[0], status=status),))
    model.set_net(changed)
    assert len(model._cells) == 0
    assert model.cell(model.index(0, 2)).text == text
    assert model.cell(model.index(1, 1)).text == text
    assert model.grid == matrix_grid(changed)
    assert model.grid[0][0].text == "—"


class WorkerAuthorizedProvider(MockProvider):
    error = Signal(str)
    dispatch_validate_backend_permissions = True

    def __init__(self):
        super().__init__()
        self.reads, self.dispatched = 0, []

    @property
    def capabilities(self):
        self.reads += 1
        return SourceCapabilities(adapter_id="worker-fixture", mode="simulation", health="ready",
                                  operations=("select_node",))

    def dispatch(self, verb, target):
        # Actual authorization is the real ServiceProvider worker's contract;
        # this fixture isolates redundant UI capability calls.
        self.dispatched.append((verb, target))
        return True


def test_ui_precheck_uses_committed_frame_but_public_getter_stays_fresh(app):
    active = WorkerAuthorizedProvider(); plug = SwitchableProvider(active)
    plug.running = True
    plug._snap = active.snapshot()
    before = active.reads
    assert plug.dispatch("select_node", "N-01")
    active.error.emit("operation rejected")
    assert active.reads == before
    assert active.dispatched == [("select_node", "N-01")]
    assert plug.capabilities.allows("select_node")
    assert active.reads == before + 1
    plug.stop()


def test_no_frame_or_not_running_denies_worker_ui_dispatch(app):
    active = WorkerAuthorizedProvider(); plug = SwitchableProvider(active)
    plug.running = True
    with pytest.raises(Exception):
        plug.dispatch("select_node", "N-01")
    plug._snap = active.snapshot(); plug.running = False
    with pytest.raises(Exception):
        plug.dispatch("select_node", "N-01")
    assert active.dispatched == []
    plug.stop()


def test_sources_without_worker_authorization_contract_keep_fresh_precheck(app):
    active = WorkerAuthorizedProvider(); active.dispatch_validate_backend_permissions = False
    plug = SwitchableProvider(active); plug.running = True; plug._snap = active.snapshot()
    before = active.reads
    plug.dispatch("select_node", "N-01")
    assert active.reads == before + 1
    plug.stop()


def test_pending_readback_uses_progress_and_elapsed_time_instead_of_frame_count(app, monkeypatch):
    from types import SimpleNamespace
    from quantum_autopilot.console.contract import LiveView, LiveNode, LiveCalibration
    from quantum_autopilot.console.screens import topology as module
    snap = MockProvider().snapshot()
    clock = [1000.]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    pending = module._Pending("recalibrate_node", ("node", "N-01"),
                              frozenset(item.command_id for item in snap.ledger.commands), frozenset(),
                              last_progress_at=clock[0])
    screen = SimpleNamespace(_pending=pending, _command_status=None)
    calibration = LiveCalibration("cal-1", phase="sync", progress=.3)
    node = LiveNode("N-01", "endpoint", "node", "calibrating", calibration=calibration)
    live = LiveView(1, 1., "simulated", "identified fixture", nodes=(node,))
    snap = replace(snap, live=live)
    for _ in range(100):
        module.TopologyScreen._resolve_pending(screen, snap)
    assert screen._pending is pending and screen._command_status.pending
    assert "sync" in screen._command_status.text
    clock[0] += 20.
    newer = replace(snap, live=replace(live, nodes=(replace(node, calibration=replace(calibration, progress=.5)),)))
    module.TopologyScreen._resolve_pending(screen, newer)
    assert pending.last_progress_at == clock[0]
    clock[0] += module.PENDING_READBACK_TIMEOUT_S + .1
    module.TopologyScreen._resolve_pending(screen, newer)
    assert screen._pending is None and screen._command_status.text.startswith("UNRESOLVED")
