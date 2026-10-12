"""Delivered stock and a memory reset, operated through the real console widgets."""
import os

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from tests.test_console_topology_live import Demo, flush, pump  # noqa: E402

pytestmark = pytest.mark.unit


def test_pair_reset_and_fresh_request_are_reflected_in_the_interface(tmp_path):
    app = QApplication.instance() or QApplication([])
    harness = Demo(app, tmp_path / "memory-lifecycle")
    try:
        window = harness.window
        pump(app, lambda: window.current_screen == "boot", 120)
        assert window._skip_auth is False and not window._login_verified
        assert window.open_workspace() is False
        controller = window._login_controller
        assert controller is not None
        verified = []
        controller.succeeded.connect(verified.append)
        assert window.boot.os_session_button.isEnabled()
        window.boot.os_session_button.click()
        pump(app, lambda: bool(verified) and window._login_verified
             and window.current_screen == "topology" and not controller.active, 120)
        assert verified[-1].source == "os"
        assert verified[-1].subject == harness.provider._identity.subject
        assert window.open_workspace()
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
