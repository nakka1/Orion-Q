"""Screens must build offscreen, reflow with their viewport and remain usable."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.providers import MockProvider  # noqa: E402
from quantum_autopilot.console.screens import SCREENS, BootScreen, SplashScreen  # noqa: E402
from quantum_autopilot.console.shell import ConsoleWindow  # noqa: E402
from quantum_autopilot.console.stage import ScaledStage  # noqa: E402
from quantum_autopilot.console.synthetic import artificial_snapshot  # noqa: E402
from quantum_autopilot.console.theme import DESIGN_H, DESIGN_W, MIN_CONTENT_W  # noqa: E402
from quantum_autopilot.console.widgets.network_views import matrix_cells  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.install(instance)
    return instance


@pytest.fixture()
def snapshot():
    return MockProvider().snapshot()


def _describe(widget) -> str:
    """The widget and its ancestors, for a geometry assertion that names the culprit."""

    chain = []
    while widget is not None:
        text = getattr(widget, "text", None)
        value = text() if callable(text) else ""
        chain.append(f"{type(widget).__name__}(w={widget.width()}, name={widget.accessibleName()!r}, "
                     f"text={str(value)[:60]!r})")
        widget = widget.parentWidget()
    return " < ".join(chain[:6])


@pytest.mark.unit
def test_every_screen_accepts_reference_and_wide_frames(app, snapshot):
    for cls in (BootScreen, *SCREENS):
        screen = cls()
        screen.apply(snapshot)
        screen.resize(DESIGN_W, DESIGN_H)
        screen.show()
        app.processEvents()
        assert screen.size().width() == DESIGN_W
        screen.resize(1920, 1080)
        app.processEvents()
        assert screen.size().width() == 1920
        assert screen.size().height() == 1080
        for child in screen.findChildren(QWidget):
            if child.isWindow():
                continue
            assert child.width() <= 1920, _describe(child)
        screen.close()


@pytest.mark.unit
def test_small_windows_scroll_instead_of_shrinking_text(app):
    screen = SplashScreen()
    stage = ScaledStage(screen)
    stage.resize(640, 400)
    stage.show()
    app.processEvents()

    assert screen.size().width() == MIN_CONTENT_W
    scale = stage.transform().m11()
    assert scale >= 1.0
    assert abs(stage.transform().m22() - scale) < 1e-6   # uniform: no distortion
    assert stage.horizontalScrollBar().maximum() > 0
    stage.close()


@pytest.mark.unit
def test_action_buttons_support_keyboard_and_respect_disabled_state(app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from quantum_autopilot.console.widgets import Btn

    button = Btn("OPEN WORKSPACE")
    activations = []
    button.clicked.connect(lambda: activations.append(True))
    button.show()
    button.setFocus()
    app.processEvents()
    QTest.keyClick(button, Qt.Key.Key_Space)
    QTest.keyClick(button, Qt.Key.Key_Return)
    assert len(activations) == 2
    button.setEnabled(False)
    QTest.keyClick(button, Qt.Key.Key_Return)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert len(activations) == 2
    button.close()


@pytest.mark.unit
def test_actual_view_buttons_keep_the_selected_route(app, snapshot):
    screen = next(cls for cls in SCREENS if cls.key == "topology")()
    screen.apply(snapshot)
    screen.show()
    assert list(screen.toggles) == ["GEO", "MATRIX", "GRAPH"]          # GEO is the main view and comes first
    assert screen.view == "GEO" and screen.graph_stack.currentWidget() is screen.geo
    for name, expected in (("MATRIX", screen.matrix), ("GEO", screen.geo), ("GRAPH", screen.graph)):
        screen.toggles[name].click()
        assert screen.graph_stack.currentWidget() is expected
        assert screen.view == name and screen.toggles[name].active
    screen.close()


@pytest.mark.unit
def test_dialog_controls_scroll_into_view_on_keyboard_focus(app):
    from quantum_autopilot.console.widgets.adaptive_dialog import AdaptiveDialog
    from quantum_autopilot.console.widgets import Btn, vbox

    dialog = AdaptiveDialog()
    layout = vbox(dialog)
    buttons = [Btn(f"ACTION {i}") for i in range(18)]
    for button in buttons:
        layout.addWidget(button)
    dialog.show()
    dialog.resize(400, 300)
    app.processEvents()
    scroll = dialog.content_scroll
    assert scroll.verticalScrollBar().maximum() > 0
    buttons[-1].setFocus()
    app.processEvents()
    assert buttons[-1].hasFocus()
    assert scroll.verticalScrollBar().value() > 0
    dialog.close()


@pytest.mark.unit
def test_dialog_initial_window_fits_the_available_work_area(app):
    from quantum_autopilot.console.widgets.adaptive_dialog import AdaptiveDialog
    from quantum_autopilot.console.widgets import Btn, vbox

    dialog = AdaptiveDialog()
    vbox(dialog).addWidget(Btn("CLOSE"))
    dialog.resize(2000, 1600)
    dialog.show()
    app.processEvents()
    area = dialog.screen().availableGeometry().adjusted(16, 40, -16, -16)
    assert dialog.width() <= area.width()
    assert dialog.height() <= area.height()
    assert dialog.screen().availableGeometry().contains(dialog.frameGeometry())
    dialog.close()


@pytest.mark.unit
def test_long_identifiers_remain_available_in_bounded_labels(app):
    from quantum_autopilot.console.widgets.prims import L

    value = "baseline_no_signal_no_added_fibre_full_sweep"
    label = L(value, elide=True)
    label.resize(90, 24)
    label.show()
    app.processEvents()
    # Elision is performed while painting. Mutating QLabel.text on resize made
    # its layout hints oscillate and froze the interface in earlier builds.
    assert label.displayed_text().endswith("…")
    assert label.text() == value
    stable_hint = label.sizeHint()
    assert label.toolTip() == value
    assert label.accessibleName() == value
    label.resize(600, 24)
    app.processEvents()
    assert label.displayed_text() == value
    assert label.text() == value
    assert label.sizeHint() == stable_hint
    label.close()


@pytest.mark.unit
def test_topology_long_node_name_and_metric_columns_are_readable(app):
    from dataclasses import replace
    from quantum_autopilot.console.theme import mono
    from PySide6.QtGui import QFontMetrics

    screen = next(cls for cls in SCREENS if cls.key == "topology")()
    snap = artificial_snapshot()
    original = snap.topology.nodes[0]
    named = replace(original, id="repeater-a")
    # No links are needed to verify a declared node identifier.
    screen.set_view("GRAPH")
    screen.graph.set_topology([named], [])
    screen.resize(DESIGN_W, DESIGN_H)
    screen.show()
    app.processEvents()
    rect, node_id = screen.graph._boxes[0]
    assert node_id == named.id
    assert rect.width() - 16 >= QFontMetrics(mono(10)).horizontalAdvance(named.id)
    screen._set_links([("alice ↔ repeater-a", "0.9900", "3.33 %", "240 kb/s", theme.C.ink, theme.C.ink)])
    for col, value in ((1, "0.9900"), (2, "3.33 %"), (3, "240 kb/s")):
        assert screen.link_table.columnWidth(col) >= screen.link_table.fontMetrics().horizontalAdvance(value) + 18
    screen.close()


@pytest.mark.unit
def test_topology_view_toggles_switch_the_canvas(app, snapshot):
    screen = next(cls for cls in SCREENS if cls.key == "topology")()
    screen.apply(snapshot)

    assert screen.view == "GEO" and screen.graph_stack.currentWidget() is screen.geo
    # The reference fixture declares no coordinates: nothing is placed on the map, every node
    # waits in the queue, and the matrix still lists all of them.
    assert not screen.geo.available and screen.geo.placed_ids() == []
    assert screen.queue.node_ids() == [node.id for node in snapshot.topology.nodes]
    screen.set_view("MATRIX")
    assert screen.matrix.node_ids() == [node.id for node in snapshot.topology.nodes]
    assert screen.graph_stack.currentWidget() is screen.matrix
    screen.set_view("GRAPH")
    assert screen.graph_stack.currentWidget() is screen.graph


@pytest.mark.unit
def test_overlays_open_only_on_click(app, snapshot):
    screen = next(cls for cls in SCREENS if cls.key == "topology")()
    screen.apply(snapshot)
    screen.resize(DESIGN_W, DESIGN_H)
    screen.show()
    app.processEvents()
    assert not hasattr(screen, "rail") and not hasattr(screen, "profile")   # chrome belongs to the shell
    assert not screen.node_popover.isVisible()
    first, second = snapshot.topology.nodes[0].id, snapshot.topology.nodes[1].id
    screen.graph.node_selected.emit(first)
    app.processEvents()
    assert screen.node_popover.isVisible() and list(screen.node_windows.windows) == [first]
    window = screen.node_windows.windows[first]
    assert window.isVisible() and not window.isWindow() and window.parentWidget() is screen   # floats above the map
    screen.set_view("MATRIX")                      # a node window is not tied to one view
    assert screen.node_popover.isVisible()
    screen.matrix.node_selected.emit(second)       # more than one may be open
    assert list(screen.node_windows.windows) == [first, second]
    screen.node_popover.hide()                     # Esc (the shell calls this): the topmost one closes
    assert list(screen.node_windows.windows) == [first]
    window.close_button.click()                    # its own ✕
    app.processEvents()
    assert not screen.node_popover.isVisible() and not screen.node_windows.windows
    screen.close()


@pytest.mark.unit
def test_edge_screen_is_aggregate_only(app, snapshot):
    screen = next(cls for cls in SCREENS if cls.key == "fpga")()
    screen.apply(snapshot)
    assert "NO PER-SHOT" in screen.status
    assert not screen.flash.isEnabled()


@pytest.mark.unit
def test_unavailable_fpga_stays_disabled_after_busy_operation(app):
    window = ConsoleWindow(MockProvider(), skip_auth=True)
    window._on_snapshot(MockProvider().snapshot())
    window._on_busy(True)
    window._on_busy(False)
    assert not window.screens["fpga"].flash.isEnabled()
    window.close()


@pytest.mark.unit
def test_matrix_uses_direct_links_only_and_retains_missing_values():
    snapshot = artificial_snapshot()
    rows = matrix_cells(snapshot.topology.nodes, snapshot.topology.links)
    names = [node.id for node in snapshot.topology.nodes]
    first = names[0]
    direct = next(link for link in snapshot.topology.links if first in (link.a, link.b))
    peer = direct.b if direct.a == first else direct.a

    assert rows[0][0][0] == first
    assert rows[0][1][0] == "—"
    assert rows[0][names.index(peer) + 1][0] != "·"
    absent = next(
        name for name in names[1:]
        if not any({link.a, link.b} == {first, name} for link in snapshot.topology.links)
    )
    assert rows[0][names.index(absent) + 1][0] == "·"


@pytest.mark.unit
def test_nodes_provision_waits_for_shared_source_readback(app, snapshot):
    screen = next(cls for cls in SCREENS if cls.key == "node")()
    screen.apply(artificial_snapshot())
    base_count = len(screen._base_nodes)
    screen.fields["id"].setText("N-LOCAL")
    screen.fields["address"].setText("127.0.0.1")
    screen.fields["port"].setText("7411")
    captured = []
    screen.command.connect(lambda verb, body: captured.append((verb, body)))
    screen._provision()

    assert len(screen._base_nodes) == base_count
    assert not screen._shadow_nodes
    assert captured[0][0] == 'stage_node'
    assert screen._pending_stage['id'] == 'N-LOCAL'
    assert 'PENDING' in screen.form_note.text()


@pytest.mark.unit
def test_audit_filters_narrow_the_ledger(app, snapshot):
    screen = next(cls for cls in SCREENS if cls.key == "audit")()
    screen.apply(snapshot)
    total = screen.entry_scroll.model().rowCount()

    screen.set_filter("KEY")
    narrowed = screen.entry_scroll.model().rowCount()

    assert narrowed <= total
    assert narrowed == sum(entry.category == "key" for entry in snapshot.ledger.entries)
    screen.set_filter("ALL")
    assert screen.entry_scroll.model().rowCount() == total


@pytest.mark.unit
def test_commands_reach_the_provider(app):
    class Recorder(MockProvider):
        @property
        def capabilities(self):
            from quantum_autopilot.console.capabilities import SourceCapabilities
            return SourceCapabilities(adapter_id="test-recorder", mode="simulation", health="ready",
                                      operations=("run_scenario", "export_record"))

        def __init__(self):
            super().__init__()
            self.calls = []

        def run_scenario(self, target):
            self.calls.append(("run_scenario", target))

        def export_record(self, scope):
            self.calls.append(("export", scope))

    provider = Recorder()
    window = ConsoleWindow(provider, skip_auth=True)
    window.provider.start()
    window.show_screen("topology")
    window.screens["topology"].recalibrate.clicked.emit()
    window.show_screen("audit")
    window.screens["audit"].filters["ALL"].clicked.emit()

    assert ("run_scenario", "") in provider.calls
    window.close()


@pytest.mark.unit
def test_window_opens_on_the_loading_screen(app):
    window = ConsoleWindow(MockProvider())

    assert window.canvas.currentWidget() is window.splash
    window.splash.finished.emit()
    assert window.canvas.currentWidget() is window.boot


@pytest.mark.unit
def test_map_labels_move_instead_of_covering_a_neighbour(app):
    """Two nodes closer than a label is wide: the labels take different sides; markers never move."""
    from types import SimpleNamespace

    from PySide6.QtCore import QPointF, QRectF

    from quantum_autopilot.console.widgets.figures import _node_boxes, node_rect, place_node_labels

    def node(name):
        return SimpleNamespace(id=name, kind="node", health="up", status="up", isolated=False)

    items = [("R-07", QPointF(300, 200), node("R-07"), ""), ("E2E-Q1", QPointF(330, 200), node("E2E-Q1"), ""),
             ("N-01", QPointF(372, 200), node("N-01"), ""), ("FAR", QPointF(700, 60), node("FAR"), "END")]
    sides = place_node_labels(items, QRectF(0, 0, 1000, 400))
    assert sides["FAR"] == "right" and set(sides.values()) <= {"right", "left", "below", "above"}
    assert len({sides[name] for name in ("R-07", "E2E-Q1")}) == 2          # the default side would collide
    boxes = {name: _node_boxes(centre, item, badge, None, sides[name]) for name, centre, item, badge in items}
    labels = {name: (value[1] if value[2] is None else value[1].united(value[2])) for name, value in boxes.items()}
    names = list(labels)
    for index, first in enumerate(names):
        for second in names[index + 1:]:
            assert not labels[first].intersects(labels[second]), (first, second, sides)
        for other in names:
            if other != first:                                              # no label over another marker
                assert not labels[first].intersects(boxes[other][0].adjusted(2, 4, -2, -4)), (first, other, sides)
    for name, centre, item, badge in items:                                 # the click target follows the label
        assert node_rect(centre, item, badge=badge, side=sides[name]).contains(centre)
        assert node_rect(centre, item, badge=badge, side=sides[name]).contains(labels[name].center())
    # the default side is the prototype's: marker, 7 px gap, label
    square, label, chip, text = _node_boxes(QPointF(100, 100), node("N-02"), "", None, "right")
    assert label.left() == 100 + 7 + 7 and text == "N-02" and chip is None


@pytest.mark.unit
def test_mock_fixture_declares_itself_and_cannot_pass_for_a_data_source(app):
    """`--provider mock` is a labelled UI fixture: never "service", never "measured", never a login row."""
    provider = MockProvider()
    snapshot = provider.snapshot()
    assert provider.is_synthetic is True and snapshot.input_kind == "synthetic"
    assert "MEASURED" not in snapshot.topology.ring.upper() and "FIXTURE" in snapshot.topology.ring.upper()
    window = ConsoleWindow(provider, skip_auth=True)
    try:
        assert window.active_source_key() == "synthetic"
        assert window.provider.source_label == "SYNTHETIC · UI FIXTURE"
        assert window.source_short() == "Synthetic UI fixture"
        assert "synthetic" not in {option.key for option in window.source_options()}
    finally:
        window.close()
