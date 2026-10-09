"""Delivered stock and a memory reset, operated through the real console widgets."""
import os

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from tests.test_console_topology_live import Demo, flush  # noqa: E402

pytestmark = pytest.mark.unit


def test_pair_reset_and_fresh_request_are_reflected_in_the_interface(tmp_path):
    app = QApplication.instance() or QApplication([])
    harness = Demo(app, tmp_path / "memory-lifecycle")
    try:
        harness.window.splash.finished.emit()
        assert harness.window.open_workspace()
        topo = harness.topo

        def request():
            topo.inspector.show_demand("P2")
            panel = topo.inspector.pairs_panel
            for _ in range(12):
                harness.settle()
                assert panel.request.isEnabled()
                panel.request.click()
                harness.done()
                if harness.live.demand("P2").pair["status"] == "delivered":
                    return harness.live.demand("P2").pair
            pytest.fail("the quiet simulated plant delivered no pair in 12 episodes")

        original = request()
        harness.click_node("N-03")
        window = topo.node_windows.windows["N-03"]
        window.isolate.click()
        harness.done()
        topo.inspector.show_demand("P2")
        panel = topo.inspector.pairs_panel
        assert harness.live.demand("P2").pair["status"] == panel.pair_kv.value("Pair") == "invalidated"
        assert panel.pair_kv.value("Historical F") == f"{original['fidelity']:.4f}"
        assert "N-03" in panel.pair_kv.rows["Pair"].toolTip()
        assert not panel.consume.isEnabled() and not panel.purify.isEnabled()
        window.isolate.click()  # the same widget now restores this node
        harness.done()
        fresh = request()
        assert panel.pair_kv.value("Current F") == f"{fresh['fidelity']:.4f}"
        assert not panel.pair_kv.rows["Pair"].toolTip()
        assert panel.consume.isEnabled() and panel.purify.isEnabled()
        panel.consume.click()
        harness.done()
        assert harness.live.demand("P2").pair["status"] == "consumed"
        assert not panel.consume.isEnabled()
        assert not harness.errors
    finally:
        harness.window.close()
        harness.window.deleteLater()
        flush(app)
