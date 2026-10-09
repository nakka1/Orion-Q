"""Telemetry, Keys, Nodes, Edge control and Audit on the REAL backend — no mocks.

Each test drives an actual `AssuranceDashboardService` through `ServiceProvider`:
the demonstration service (`demo_service`: simulated INPUT, real pipeline) or the
ordinary local service with its idle input. What is asserted is that the screens
show what the backend delivered — the same numbers, rows and order — and nothing
else: no fixture of the HTML prototype, no value made up by the interface.
"""
from __future__ import annotations

import re
import json
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton, QApplication, QComboBox, QLabel, QLineEdit, QTableView,
)

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.providers import MockProvider  # noqa: E402
from quantum_autopilot.console.screens.audit import AuditScreen  # noqa: E402
from quantum_autopilot.console.screens.fpga import EdgeControlScreen  # noqa: E402
from quantum_autopilot.console.screens.keys import KeysScreen  # noqa: E402
from quantum_autopilot.console.screens.node import NodeScreen  # noqa: E402
from quantum_autopilot.console.screens.telemetry import TelemetryScreen, backend_link_thresholds  # noqa: E402
from quantum_autopilot.console.service_provider import ServiceProvider  # noqa: E402
from quantum_autopilot.console.synthetic import artificial_snapshot  # noqa: E402
from quantum_autopilot.console.widgets.cards import fmt_bits, fmt_fidelity, fmt_qber, fmt_rate  # noqa: E402
from quantum_autopilot.console.widgets.demo_panel import model_row_values  # noqa: E402
from quantum_autopilot.console.widgets.location_fields import parse_location  # noqa: E402
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService, demo_service  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCREEN_TYPES = (TelemetryScreen, KeysScreen, NodeScreen, EdgeControlScreen, AuditScreen)

#: Values that exist only in the HTML prototype's fixtures. None may reach a screen.
PROTOTYPE_FIXTURES = (
    "OP-2291", "PI-0104", "KC-0330", "OB-1177", "C-2026-037", "HSM 02", "QKD-LAB-3", "512 blocks", "4.7 Mb",
    "1×10⁻⁹", "1e-9", "10⁶ bits", "41 892", "S-4468", "TERM 07", "VAULT RM", "CUSTODIAN", "kms/ring04",
    "QKD-TX 1102", "RX 1108", "c93f…7a20", "3f9a…c214", "02:38:44", "02:31:44", "yield 61%", "re-priming",
    "NV-diamond", "Ramsey", "Hahn", "T₂*", "BBM92", "Barrett", "Vault sealed", "Your role", "rev 4",
    "needs PI + custodian", "tomography every 5 min", "DAQ 250 ms", "Memory registers · 128", "entangled 96",
    "10.0.4.12:7000", "operating threshold 0.900", "N01↔N02", "R07↔CORE", "ring04", "Ring 04",
)


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.set_theme("dark")
    theme.install(instance)
    return instance


def pump(app, condition, timeout=60.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert condition()


class Harness:
    """A started provider, driven the way the window drives it."""

    def __init__(self, app, provider):
        self.app, self.provider = app, provider
        self.errors: list[str] = []
        provider.error.connect(self.errors.append)
        provider.start()
        pump(app, lambda: provider._snap is not None and provider._snap.live is not None)

    @property
    def snap(self):
        return self.provider.snapshot()

    @property
    def live(self):
        return self.snap.live

    def wait_ticks(self, count):
        target = self.live.tick + count
        pump(self.app, lambda: self.live.tick >= target)

    def dispatch(self, verb, body):
        """Send exactly what a screen emitted (`command(verb, json)`) and wait for the command to end."""
        pump(self.app, lambda: not self.provider._foreground_busy)
        assert self.provider.dispatch(verb, body) is True
        pump(self.app, lambda: not self.provider._foreground_busy, timeout=180.0)
        return self.snap

    def run(self, verb, payload):
        return self.dispatch(verb, json.dumps(payload))


@pytest.fixture(scope="module")
def demo(app, tmp_path_factory):
    root = tmp_path_factory.mktemp("demo")
    provider = ServiceProvider(demo_service(ROOT, root), state_root=root, poll_ms=100, step_period_s=0.1)
    harness = Harness(app, provider)
    pump(app, lambda: harness.live.tick >= 6 and all(link.qber is not None for link in harness.live.links))
    yield harness
    provider.stop()


@pytest.fixture(scope="module")
def idle(app, tmp_path_factory):
    root = tmp_path_factory.mktemp("idle")
    service = AssuranceDashboardService(ROOT, control_journal_path=root / "operator.sqlite")
    provider = ServiceProvider(service, state_root=root, poll_ms=100, step_period_s=0.1)
    harness = Harness(app, provider)
    yield harness
    provider.stop()


def close(*widgets):
    for widget in widgets:
        widget.shutdown() if hasattr(widget, "shutdown") else None
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def visible_texts(widget) -> list[str]:
    """Every text a screen can show: labels, buttons, inputs, list items, table cells and headers."""

    texts = []
    for label in widget.findChildren(QLabel):
        texts += [label.text(), label.toolTip()]
    for button in widget.findChildren(QAbstractButton):
        texts.append(button.text())
    for field in widget.findChildren(QLineEdit):
        texts += [field.text(), field.placeholderText()]
    for combo in widget.findChildren(QComboBox):
        texts += [combo.itemText(index) for index in range(combo.count())]
    for table in widget.findChildren(QTableView):
        model = table.model()
        if model is None:
            continue
        texts += [str(model.headerData(column, table.horizontalHeader().orientation()) or "")
                  for column in range(model.columnCount())]
        texts += [str(model.data(model.index(row, column)) or "")
                  for row in range(model.rowCount()) for column in range(model.columnCount())]
    return [text for text in texts if text]


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def assert_no_prototype_fixture(widget):
    # Command ids are random UUIDs: one of them can spell "1e-9" across a hyphen, so identifiers of
    # that shape are removed before looking for the prototype's literals.
    haystack = _UUID.sub("<uuid>", "\n".join(visible_texts(widget)))
    found = [token for token in PROTOTYPE_FIXTURES if token in haystack]
    assert not found, f"prototype fixture text shown by {type(widget).__name__}: {found}"


def history_points(xs, ys):
    return [(float(x), float(y)) for x, y in zip(xs, ys) if y is not None]


# ---------------------------------------------------------------- telemetry
def test_telemetry_plots_the_backend_history_point_for_point(app, demo):
    snap = demo.snap
    live = snap.live
    screen = TelemetryScreen()
    screen.apply(snap)
    link = live.links[0]
    assert screen._link == link.id and not screen.link_row.isHidden()
    assert list(screen.link_picker.ids) == [item.id for item in live.links]

    slots = screen._slot_links
    assert slots["a"] == link.id and len(set(slots.values())) == 3
    plotted = dict(screen.chart.plotted())
    assert list(plotted) == [slots[key] for key in ("a", "b", "c")]
    for link_id, points in plotted.items():
        history = live.link(link_id).history
        assert points == history_points(history.t_s, history.fidelity)      # no extra, missing or moved point
        assert len(points) <= len(history.t_s) <= 300
    assert screen.chart.x_labels[0] == f"t {live.link(slots['a']).history.t_s[0]:.0f} s"

    # the other metrics are the other backend series, untouched
    for metric, values in (("qber", link.history.qber), ("skr", link.history.skr_bps)):
        screen._set_metric(metric)
        assert dict(screen.chart.plotted())[link.id] == history_points(link.history.t_s, values)
    assert screen.chart.threshold is None                              # the backend declares none for key rate
    screen._set_metric("qber")
    # the line is the threshold the RUNNING engine applies and publishes, not a module default
    running = demo.provider.service.network_engine.bank.policy.qber_down
    assert screen.chart.threshold == running == live.policy["qber_down"] == backend_link_thresholds(live)["qber"]
    assert backend_link_thresholds(None) == {}
    from dataclasses import replace as _replace
    other = _replace(live, policy={**live.policy, "qber_down": 0.07, "fidelity_at_qber_down": 0.895})
    screen.apply(_replace(snap, live=other))
    assert screen.chart.threshold == 0.07                              # follows the published value
    screen.apply(_replace(snap, live=_replace(live, policy={})))
    assert screen.chart.threshold is None                              # not published: no line, no default
    screen.apply(snap)
    screen._set_metric("fidelity")

    # series chips switch a series off and on; nothing else changes
    screen.series_chips["b"].click()
    assert [label for label, _ in screen.chart.plotted()] == [slots["a"], slots["c"]]
    screen.series_chips["b"].click()
    assert [label for label, _ in screen.chart.plotted()] == [slots["a"], slots["b"], slots["c"]]

    # headline cards: the selected fibre's numbers as formatted backend values
    fidelity, qber, rate = screen.live_cards
    assert fidelity.value.text() == fmt_fidelity(link.fidelity) == f"{link.fidelity:.4f}"
    assert qber.value.text() == f"{link.qber * 100:.2f}" and qber.unit.text() == "%"
    assert f"{rate.value.text()} {rate.unit.text()}" == fmt_rate(link.skr_bps)
    assert {card.chip.text() for card in screen.live_cards} == {"SIMULATED"}
    assert fidelity.note.isHidden()
    fidelity.toggle.click()
    assert not fidelity.note.isHidden() and "not a tomography result" in fidelity.note.text()
    assert [title.text() for title in screen.card_titles] == [metric.label for metric in snap.telemetry.headline]
    assert [host[0].text() for host in screen.card_hosts] == [metric.value for metric in snap.telemetry.headline]
    assert_no_prototype_fixture(screen)
    close(screen)


def test_telemetry_link_selection_is_shared_with_the_shell(app, demo):
    from quantum_autopilot.console.shell import Selection
    snap = demo.snap
    screen = TelemetryScreen()
    screen.apply(snap)
    other = snap.live.links[4]
    screen.on_selection(Selection("link", other.id))            # a link selected elsewhere
    assert screen._link == other.id and screen.link_picker.current() == other.id
    assert screen.live_cards[0].value.text() == fmt_fidelity(other.fidelity)
    assert dict(screen.chart.plotted())[other.id] == history_points(other.history.t_s, other.history.fidelity)
    picked = []
    screen.select.connect(lambda kind, ident: picked.append((kind, ident)))
    screen.link_picker.buttons[snap.live.links[2].id].click()   # a link selected here
    assert picked == [("link", snap.live.links[2].id)] and screen._link == snap.live.links[2].id
    state = screen.save_state()
    rebuilt = TelemetryScreen()                                  # a theme change rebuilds the screen
    rebuilt.apply(snap)
    rebuilt.restore_state(state)
    assert rebuilt._link == snap.live.links[2].id
    close(screen, rebuilt)


# --------------------------------------------------------------------- keys
def test_keys_live_qkd_section_shows_the_qkd_model_in_action(app, demo):
    snap = demo.snap
    live = snap.live
    screen = KeysScreen()
    screen.apply(snap)
    panel = screen.live_panel
    assert panel is not None and not screen.live_host.isHidden()
    link = live.link(panel.current())
    assert link.id == live.links[0].id
    values = panel.values()
    keyrate = next(item for item in link.controllers if item.role == "keyrate")
    assert values["Protocol"] == link.qkd.protocol == "decoy_state_bb84"
    assert values["Signal intensity μ"] == f"{link.qkd.signal_intensity:.4f}"
    assert values["Decoy intensity ν"] == f"{link.qkd.decoy_intensity:.4f}"
    assert values["Clock rate"] == fmt_rate(link.qkd.clock_rate_hz, "Hz")
    assert values["Signal gain"] == f"{link.qkd.signal_gain:.6f}"
    assert values["Decoy gain"] == f"{link.qkd.decoy_gain:.6f}"
    assert values["QBER"] == fmt_qber(link.qber)
    assert values["Sifted rate"] == fmt_rate(link.sifted_rate_hz, "Hz")
    assert values["Secret key rate"] == fmt_rate(link.skr_bps)
    assert values["Key pool"] == fmt_bits(link.qkd.key_pool_bits)
    assert values["Discarded on alarms"] == fmt_bits(link.qkd.discarded_bits)
    assert values["Last key-rate action"] == link.qkd.last_action
    assert values["Key-rate controller"].startswith(keyrate.controller_id) and keyrate.model_state in values["Key-rate controller"]
    assert values["Detector state"] == (link.detector.state or "—")
    assert values["Detector label"] == (link.detector.label or "—")

    # the live charts are the backend history of that fibre
    assert dict(panel.skr_chart.plotted())[f"{link.id} key rate"] == history_points(link.history.t_s, link.history.skr_bps)
    assert dict(panel.qber_chart.plotted())[f"{link.id} QBER"] == history_points(link.history.t_s, link.history.qber)

    # one table row per fibre, in backend order
    model = panel.table.model()
    assert [model.data(model.index(row, 0)) for row in range(model.rowCount())] == [item.id for item in live.links]
    assert [model.data(model.index(row, 5)) for row in range(model.rowCount())] == \
        [fmt_bits(item.qkd.key_pool_bits) for item in live.links]

    # real time: the pool accumulates because the backend adds each period's key to it
    before_pool, before_points = link.qkd.key_pool_bits, len(link.history.t_s)
    demo.wait_ticks(3)
    later = demo.snap
    screen.apply(later)
    new_link = later.live.link(link.id)
    assert new_link.qkd.key_pool_bits > before_pool and len(new_link.history.t_s) > before_points
    assert panel.values()["Key pool"] == fmt_bits(new_link.qkd.key_pool_bits)
    assert len(dict(panel.skr_chart.plotted())[f"{link.id} key rate"]) == \
        len(history_points(new_link.history.t_s, new_link.history.skr_bps))

    # another fibre: by the panel's own selector and by the shell selection
    other = later.live.links[3]
    picked = []
    screen.select.connect(lambda kind, ident: picked.append((kind, ident)))
    panel.picker.buttons[other.id].click()
    assert picked == [("link", other.id)] and panel.values()["Key pool"] == fmt_bits(other.qkd.key_pool_bits)
    assert_no_prototype_fixture(screen)
    close(screen)


# -------------------------------------------------------------------- nodes
def node_view(harness, node_id, address):
    view = NodeScreen()
    view.apply(harness.snap)
    captured = []
    view.command.connect(lambda verb, body: captured.append((verb, body)))
    harness.provider.snapshot_changed.connect(view.apply)
    view.fields["id"].setText(node_id)
    view.fields["address"].setText(address)
    view.fields["port"].setText("4433")
    return view, captured


def release(harness, view):
    harness.provider.snapshot_changed.disconnect(view.apply)
    close(view)


def test_node_form_with_a_location_puts_the_node_on_the_map(app, demo):
    view, captured = node_view(demo, "N-31", "10.71.0.31")
    assert view.form_title.text() == "Add node" and view.provision.property("operation") == "add_network_node"
    assert not view.location.isHidden() and "queue on the Topology screen" in view.location.hint.text()
    view.fields["latitude"].setText("-12.9714")
    view.fields["longitude"].setText("-38.5014")
    view.fields["place"].setText("Pelourinho")
    view.roles["REPEATER"].click()
    view.provision.click()
    assert [verb for verb, _ in captured] == ["add_network_node"]
    sent = json.loads(captured[0][1])
    assert {key: sent[key] for key in ("node_id", "role", "address", "port", "latitude", "longitude", "place")} == {
        "node_id": "N-31", "role": "repeater", "address": "10.71.0.31", "port": 4433,
        "latitude": -12.9714, "longitude": -38.5014, "place": "Pelourinho"}
    assert "PENDING" in view.form_note.text() and not view.provision.isEnabled()
    assert "N-31" not in view.rows                                   # nothing optimistic
    snap = demo.dispatch(*captured[0])
    assert not demo.errors
    node = snap.live.node("N-31")
    assert node.located and (node.latitude, node.longitude, node.place) == (-12.9714, -38.5014, "Pelourinho")
    assert snap.ledger.commands[0].verb == "add_network_node" and snap.ledger.commands[0].state == "CONFIRMED"
    pump(app, lambda: view._pending_network is None)
    note = view.form_note.text()
    assert "CONFIRMED" in note and "placed on the map at -12.97140, -38.50140 (Pelourinho)" in note
    assert "N-31" in view.rows and view._selected == "N-31" and view.rows["N-31"].on
    assert view.record.values()["Location"] == "-12.97140, -38.50140"
    assert view.record.values()["Place"] == "Pelourinho"
    assert all(view.fields[name].text() == "" for name in view.fields)
    assert_no_prototype_fixture(view)
    release(demo, view)


def test_node_form_without_a_location_completes_and_queues_the_node(app, demo):
    view, captured = node_view(demo, "N-32", "10.71.0.32")
    assert view.location.is_empty()
    view.provision.click()
    sent = json.loads(captured[0][1])
    assert captured[0][0] == "add_network_node"
    assert sent["latitude"] is None and sent["longitude"] is None and sent["place"] == ""
    snap = demo.dispatch(*captured[0])
    assert not demo.errors
    node = snap.live.node("N-32")
    assert node is not None and not node.located and node.latitude is None and node.longitude is None
    assert "N-32" in {item.id for item in snap.live.unlocated_nodes}
    pump(app, lambda: view._pending_network is None)
    note = view.form_note.text()
    assert "CONFIRMED" in note and "waits in the queue on the Topology screen" in note
    assert view.record.values()["Location"] == "not located · queue"
    assert "unlocated" in view._row_parts["N-32"][1].full_text
    release(demo, view)


@pytest.mark.parametrize("latitude,longitude,place,reason", [
    ("-12.95", "", "", "both latitude and longitude"),
    ("", "-38.50", "", "both latitude and longitude"),
    ("", "", "Rio Vermelho", "place label needs latitude and longitude"),
    ("-95", "-38.5", "", "Latitude must be between -90 and 90"),
    ("-12.9", "200", "", "Longitude must be between -180 and 180"),
    ("south", "-38.5", "", "Latitude must be a number"),
])
def test_node_form_refuses_an_invalid_or_half_filled_location(app, demo, latitude, longitude, place, reason):
    view, captured = node_view(demo, "N-33", "10.71.0.33")
    version = demo.snap.topology.version
    view.fields["latitude"].setText(latitude)
    view.fields["longitude"].setText(longitude)
    view.fields["place"].setText(place)
    view.provision.click()
    assert captured == [] and view._pending_network is None
    assert "REFUSED" in view.form_note.text() and reason in view.form_note.text()
    assert view.provision.isEnabled() and view.fields["id"].text() == "N-33"
    assert demo.snap.topology.version == version and demo.snap.live.node("N-33") is None
    with pytest.raises(ValueError):
        parse_location(latitude, longitude, place)
    release(demo, view)


def test_known_places_fill_the_location_and_stay_editable(app, demo):
    view, _captured = node_view(demo, "N-34", "10.71.0.34")
    places = view.location.places
    assert places and view.location.place_combo.count() == len(places) + 1
    view.location.place_combo.setCurrentIndex(3)
    view.location._place_chosen(3)
    place = places[2]
    assert view.location.value() == (round(place.lat, 5), round(place.lon, 5), place.name)
    assert not view.location.outside_map()
    view.location.clear_button.click()
    assert view.location.is_empty() and view.location.value() == (None, None, "")
    release(demo, view)


def test_node_detail_is_the_backend_record_and_actions_are_real_commands(app, demo):
    snap = demo.snap
    view = NodeScreen()
    view.apply(snap)
    captured, picked, routes = [], [], []
    view.command.connect(lambda verb, body: captured.append((verb, body)))
    view.select.connect(lambda kind, ident: picked.append((kind, ident)))
    view.navigate.connect(routes.append)
    demo.provider.snapshot_changed.connect(view.apply)
    assert list(view.rows) == [node.id for node in snap.topology.nodes]
    view.rows["R-07"].click()
    assert picked == [("node", "R-07")] and view._selected == "R-07"
    node = snap.live.node("R-07")
    assert view.node_id.text() == "R-07" and view.status_chip.text() == node.status.upper()
    assert [row[0].split(" · ")[0] for row in view.links.rows()] == [link.id for link in snap.live.node_links("R-07")]
    record = view.record.values()
    assert record["Location"] == f"{node.latitude:.5f}, {node.longitude:.5f}" and record["Place"] == node.place
    assert record["Connection address"] == node.address and record["Port"] == str(node.port)
    assert [row[0].split(" · ")[0] for row in view.routes.rows()] == list(node.routes_through)
    view.map_button.click()
    assert routes == ["topology"] and picked[-1] == ("node", "R-07")

    # isolate: a real tracked command; the state shown afterwards is the backend's
    assert view.isolate.text() == "Isolate" and view.isolate.property("operation") == "set_node_isolation"
    view.isolate.click()
    verb, body = captured[-1]
    assert verb == "set_node_isolation" and json.loads(body)["isolated"] is True and json.loads(body)["node_id"] == "R-07"
    assert view.status_chip.text() == node.status.upper()            # unchanged until the backend answers
    snap = demo.dispatch(verb, body)
    assert not demo.errors and snap.live.node("R-07").status == "isolated"
    pump(app, lambda: view.status_chip.text() == "ISOLATED")
    assert view.isolate.text() == "Restore" and "Node status isolated" in view.alert_body.text()
    view.isolate.click()
    verb, body = captured[-1]
    assert json.loads(body)["isolated"] is False
    demo.dispatch(verb, body)
    pump(app, lambda: demo.live.node("R-07").status != "isolated" and view.isolate.text() == "Isolate")

    # recalibrate: the backend engine runs it and writes the report the card shows
    assert view.recalibrate.property("operation") == "recalibrate_node"
    view.recalibrate.click()
    verb, body = captured[-1]
    assert verb == "recalibrate_node" and json.loads(body)["node_id"] == "R-07"
    snap = demo.dispatch(verb, body)
    assert not demo.errors
    calibration = snap.live.node("R-07").calibration
    assert calibration is not None and calibration.status == "complete" and calibration.report_sha256
    pump(app, lambda: view.calibration.values().get("Report SHA-256") == calibration.report_sha256[:16])
    assert view.calibration.values()["Calibration id"] == calibration.calibration_id
    assert view.calibration_title.text() == f"Calibration · {snap.live.node('R-07').calibration_count} recorded"
    assert_no_prototype_fixture(view)
    demo.provider.snapshot_changed.disconnect(view.apply)
    close(view)


# ------------------------------------------------------------- edge control
def test_edge_feed_and_model_table_are_the_backend_lists(app, demo):
    snap = demo.snap
    live = snap.live
    screen = EdgeControlScreen()
    screen.apply(snap)
    panel = screen.demo_panel
    assert panel is not None and not screen.demo_host.isHidden()

    # model status == live.models
    assert panel.model_rows() == [model_row_values(model) for model in live.models]
    assert [row[1] for row in panel.model_rows()] == [model.id for model in live.models]
    assert {row[2] for row in panel.model_rows()} <= {"active", "fallback", "unavailable"}
    reported = {model.id: model for model in live.models}
    for model_id, row in screen.catalog_rows.items():
        expected = f"LIVE · {reported[model_id].state.upper()}" if model_id in reported else "OFFLINE ONLY"
        assert row.sign.text() == expected

    # decision feed == live.decisions, newest first
    assert panel.decision_ids() == [decision.id for decision in reversed(live.decisions)]
    model = panel.decisions_table.model()
    assert model.rowCount() == len(live.decisions)
    newest = live.decisions[-1]
    assert [model.data(model.index(0, column)) for column in (1, 2, 3, 4, 5, 6)] == [
        newest.kind, newest.controller_id, newest.model_state, newest.subject, newest.summary, newest.outcome]
    for row, decision in enumerate(reversed(live.decisions)):
        flags = model.data(model.index(row, 7))
        assert ("OUT OF CONTRACT" in flags) == (not decision.in_contract)
        assert ("FALLBACK" in flags) == decision.fallback_used

    # filter chips narrow by kind and keep the order
    for kind in ("allocation", "sync", "reroute"):
        panel.filters[kind].click()
        assert panel.kind() == kind
        assert panel.decision_ids() == [d.id for d in reversed(live.decisions) if d.kind == kind]
    panel.filters["all"].click()
    assert panel.kind() is None and len(panel.decision_ids()) == len(live.decisions)

    # a decision's subject selects that fibre / node
    picked = []
    screen.select.connect(lambda kind, ident: picked.append((kind, ident)))
    on_link = next(d for d in reversed(live.decisions) if live.link(d.subject) is not None)
    panel.select_decision(on_link.id)
    assert picked == [("link", on_link.subject)] and on_link.record_sha256[:16] in panel.decision_detail.text()

    # demands summary == live.demands
    assert [row[0] for row in panel.demands.rows()] == [demand.id for demand in live.demands]
    assert_no_prototype_fixture(screen)
    close(screen)


def test_edge_feed_updates_after_a_real_fibre_change(app, demo):
    snap = demo.snap
    screen = EdgeControlScreen()
    screen.apply(snap)
    demo.provider.snapshot_changed.connect(screen.apply)
    panel = screen.demo_panel
    known = {decision.id for decision in snap.live.decisions}
    routed = next(demand for demand in snap.live.demands if demand.route_links)
    link_id = routed.route_links[0]

    after = demo.run("set_link_active", {"link_id": link_id, "active": False,
                                         "expected_version": snap.topology.version, "topology_id": snap.topology.id})
    assert not demo.errors and after.live.link(link_id).status == "disabled"
    demo.wait_ticks(2)
    live = demo.live
    new = [decision for decision in live.decisions if decision.id not in known]
    assert any(decision.kind in ("reroute", "no_route") for decision in new)      # the backend re-planned
    pump(app, lambda: panel.decision_ids() == [d.id for d in reversed(demo.live.decisions)])
    panel.set_kind("reroute")
    assert panel.decision_ids() == [d.id for d in reversed(demo.live.decisions) if d.kind == "reroute"]
    panel.set_kind(None)
    assert [row[0] for row in panel.demands.rows()] == [demand.id for demand in demo.live.demands]
    changed = demo.live.demand(routed.id)
    row = next(row for row in panel.demands.rows() if row[0] == routed.id)
    assert changed.status in row[1] and (link_id not in changed.route_links)

    # re-enable it: again only what the backend reports
    current = demo.snap
    demo.run("set_link_active", {"link_id": link_id, "active": True,
                                 "expected_version": current.topology.version, "topology_id": current.topology.id})
    demo.wait_ticks(2)
    pump(app, lambda: panel.decision_ids() == [d.id for d in reversed(demo.live.decisions)])
    assert panel.model_rows() == [model_row_values(model) for model in demo.live.models]
    demo.provider.snapshot_changed.disconnect(screen.apply)
    close(screen)


def test_input_event_control_exists_only_in_the_demonstration(app, demo, idle):
    from quantum_autopilot.console.live_commands import input_event_kinds
    snap = demo.snap
    screen = EdgeControlScreen()
    screen.apply(snap)
    demo.provider.snapshot_changed.connect(screen.apply)
    panel = screen.demo_panel
    assert snap.capabilities.allows("inject_input_event")
    assert not panel.inject_box.isHidden() and panel.inject_button.isEnabled()
    assert panel.inject_button.property("operation") == "inject_input_event"
    assert [panel.kind_combo.itemText(i) for i in range(panel.kind_combo.count())] == list(input_event_kinds())
    assert [panel.link_combo.itemText(i) for i in range(panel.link_combo.count())] == [item.id for item in snap.live.links]
    assert "SIMULATED INPUT" in panel.inject_note.text() and "real backend pipeline" in panel.inject_note.text()

    captured = []
    screen.command.connect(lambda verb, body: captured.append((verb, body)))
    target = snap.live.links[1].id
    panel.set_injection("intercept_resend", target, 0.8, 90)
    known = {event.id for event in snap.live.events}
    panel.inject_button.click()
    assert captured == [("inject_input_event", json.dumps(
        {"kind": "intercept_resend", "link_id": target, "strength": 0.8, "duration_s": 90.0}))]
    after = demo.dispatch(*captured[0])
    assert not demo.errors
    injected = [event for event in after.live.events if event.id not in known and event.kind == "input_event_injected"]
    assert len(injected) == 1 and injected[0].subject == target and injected[0].source == "operator"
    pump(app, lambda: any("input_event_injected" in row[1] and target in row[1] for row in panel.timeline.rows()))
    demo.wait_ticks(2)
    pump(app, lambda: any(event.source == "input_source" and event.subject == target
                          for event in demo.live.events if event.id not in known))
    pump(app, lambda: any("input_event_started" in row[1] and "intercept_resend" in row[1]
                          for row in panel.timeline.rows()))
    demo.provider.snapshot_changed.disconnect(screen.apply)

    # the ordinary local service: the engine is there, the input control is not
    idle_snap = idle.snap
    assert idle_snap.live is not None and not idle_snap.capabilities.allows("inject_input_event")
    plain = EdgeControlScreen()
    plain.apply(idle_snap)
    plain_panel = plain.demo_panel
    assert plain_panel.inject_box.isHidden() and not plain_panel.inject_button.isEnabled()
    assert "only be injected in the Demonstration source" in plain_panel.inject_note.text()
    sent = []
    plain.command.connect(lambda verb, body: sent.append(verb))
    plain_panel.inject_button.click()
    plain_panel._inject()
    assert sent == []
    assert plain_panel.model_rows() == [model_row_values(model) for model in idle_snap.live.models]
    assert plain_panel.decision_ids() == [d.id for d in reversed(idle_snap.live.decisions)]
    close(screen, plain)


# -------------------------------------------------------------------- audit
def test_audit_filters_are_the_record_kinds_the_backend_has(app, demo):
    snap = demo.snap
    ledger, live = snap.ledger, snap.live
    screen = AuditScreen()
    screen.apply(snap)
    model = screen.entry_scroll.model()
    assert model.rowCount() == len(ledger.entries)
    hashes = {decision.record_sha256[:16] for decision in live.decisions}
    events = {event.id for event in live.events}
    screen.set_filter("DECISIONS")
    assert model.rowCount() == sum(entry.digest in hashes for entry in ledger.entries) > 0
    assert all(model.data(model.index(row, 3)) in hashes for row in range(model.rowCount()))
    screen.set_filter("EVENTS")
    assert model.rowCount() == sum(entry.digest in events for entry in ledger.entries) > 0
    screen.set_filter("COMMANDS")
    assert model.rowCount() == len(ledger.commands)
    assert [model.data(model.index(row, 3)) for row in range(model.rowCount())] == \
        [command.command_id for command in ledger.commands]
    screen.set_filter("CALIBRATION")
    assert model.rowCount() == sum(entry.category == "calibration" for entry in ledger.entries)
    screen.set_filter("ALL")
    assert screen.chain_label.text() == ledger.chain_verified
    assert screen.sessions.values() == {item.key: item.value for item in ledger.sessions}
    assert screen.export.values()["Signature"] == "unsigned · local session"
    assert screen.commands_button.property("operation") == "read_console_commands"
    assert_no_prototype_fixture(screen)
    close(screen)


# ------------------------------------------------------- honesty, all sources
@pytest.mark.parametrize("source", ["demo", "idle"])
def test_no_prototype_fixture_reaches_any_screen(app, demo, idle, source):
    harness = demo if source == "demo" else idle
    snap = harness.snap
    for screen_type in SCREEN_TYPES:
        screen = screen_type()
        screen.apply(snap)
        if isinstance(screen, EdgeControlScreen):
            for row in screen.catalog_rows.values():
                row.set_open(True)
        if isinstance(screen, AuditScreen):
            for name in ("DECISIONS", "EVENTS", "COMMANDS", "ALL"):
                screen.set_filter(name)
                assert_no_prototype_fixture(screen)
        assert_no_prototype_fixture(screen)
        close(screen)


def test_idle_service_shows_honest_empty_states(app, idle):
    snap = idle.snap
    live = snap.live
    assert live is not None and all(link.qber is None for link in live.links)
    telemetry = TelemetryScreen()
    telemetry.apply(snap)
    if live.links:
        assert {card.value.text() for card in telemetry.live_cards} == {"—"}      # no estimate, no number
        assert all(points == [] for _label, points in telemetry.chart.plotted())
    else:
        assert telemetry.link_row.isHidden() and telemetry.live_card_row.isHidden()
    keys = KeysScreen()
    keys.apply(snap)
    if live.links:
        assert keys.live_panel.values()["Secret key rate"] == "—" and keys.live_panel.values()["Key pool"] == "—"
    else:
        assert keys.live_panel.values() == {}
    assert keys.pool.isHidden() == (not snap.keys.pool_blocks)
    node = NodeScreen()
    node.apply(snap)
    assert list(node.rows) == [item.id for item in snap.topology.nodes]
    edge = EdgeControlScreen()
    edge.apply(snap)
    assert edge.period.text() == "—" and not edge.flash.isEnabled()
    assert "Unavailable" in edge.latency_note.text()
    close(telemetry, keys, node, edge)


def measured_snapshot(tmp_path):
    from datetime import datetime, timezone
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    stamp = datetime.now(timezone.utc).isoformat()
    fixture = {"schema": "orion.telemetry.v1", "source_id": "SOFTWARE TEST FIXTURE - NOT REAL HARDWARE",
               "campaign_id": "screens-live-test", "timestamp_utc": stamp, "evidence_kind": "measured",
               "nodes": [{"id": "fixture-a", "x": .2, "y": .5}, {"id": "fixture-b", "x": .8, "y": .5}],
               "links": [{"id": "fixture-link", "a": "fixture-a", "b": "fixture-b", "channel": "fixture-channel",
                          "device_id": "fixture-device", "measurement_id": "fixture-reading", "timestamp_utc": stamp,
                          "evidence_kind": "measured", "ber": 1e-6, "osnr_db": 24, "power_dbm": -8.5}]}
    path = tmp_path / "measured.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    return MeasuredFileProvider(path, tmp_path / "measured").snapshot()


@pytest.mark.parametrize("source", ["mock", "synthetic", "load", "measured"])
def test_screens_render_for_sources_without_a_network_engine(app, tmp_path, source):
    snap = {"mock": lambda: MockProvider().snapshot(), "synthetic": artificial_snapshot,
            "load": lambda: artificial_snapshot("load"), "measured": lambda: measured_snapshot(tmp_path)}[source]()
    assert snap.live is None
    for screen_type in SCREEN_TYPES:
        screen = screen_type()
        screen.apply(snap)
        screen.resize(theme.DESIGN_W, theme.DESIGN_H)
        screen.show()
        app.processEvents()
        if isinstance(screen, TelemetryScreen):
            assert screen.link_row.isHidden() and screen.live_card_row.isHidden()
            assert [title.text() for title in screen.card_titles] == [m.label for m in snap.telemetry.headline]
            expected = [(series.label, [(float(i), float(v)) for i, v in enumerate(series.points)])
                        for series in snap.telemetry.fidelity.series]
            assert screen.chart.plotted() == expected
            assert screen.claim_text.text() == snap.telemetry.claim.claim
        if isinstance(screen, KeysScreen):
            assert screen.live_panel is None and screen.live_host.isHidden()
            assert screen.session_scroll.model().rowCount() == len(snap.keys.sessions)
            assert len(screen.pipeline.stages) == len(snap.keys.pipeline)
        if isinstance(screen, NodeScreen):
            assert list(screen.rows) == [node.id for node in snap.topology.nodes]
            assert screen.location.isHidden()                       # no live network: no location request
            assert screen.provision.property("operation") in ("stage_node", "add_simulated_node")
            for button in (screen.recalibrate, screen.isolate):
                assert not button.isEnabled()                        # verbs this source does not declare
        if isinstance(screen, EdgeControlScreen):
            assert screen.demo_panel is None and screen.demo_host.isHidden()
            assert all(row.sign.text() in ("OFFLINE ONLY", "BLOCKED") for row in screen.catalog_rows.values())
        if isinstance(screen, AuditScreen):
            assert screen.entry_scroll.model().rowCount() == len(snap.ledger.entries)
            assert screen.filters["DECISIONS"].isHidden() and screen.filters["EVENTS"].isHidden()
        close(screen)


def test_every_action_button_names_its_operation(app, demo):
    """Capability gating needs the Qt property on every button that sends a backend command."""
    from quantum_autopilot.console.capabilities import OPERATIONS
    from quantum_autopilot.console.widgets import Btn
    snap = demo.snap
    local_only = {"Show on map", "Polarisation loop", "Open model catalog", "Flash unavailable",
                  "Remove shadow record"}
    for screen_type in SCREEN_TYPES:
        screen = screen_type()
        screen.apply(snap)
        buttons = [button for button in screen.findChildren(Btn) if button.window() is screen]   # not the dialogs
        assert buttons
        for button in buttons:
            operation = button.property("operation")
            if button.text() in local_only and not operation:
                continue
            assert operation in OPERATIONS, f"{screen_type.__name__}: {button.text()!r} has no operation"
            assert button.isEnabled() == snap.capabilities.allows(operation) or operation == "remove_staged_node"
        close(screen)


def test_theme_rebuild_keeps_view_state(app, demo):
    snap = demo.snap
    edge = EdgeControlScreen()
    edge.apply(snap)
    edge.demo_panel.set_kind("sync")
    edge.demo_panel.set_injection("pns", snap.live.links[2].id, 0.3, 45)
    first = next(iter(edge.catalog_rows))
    edge.catalog_rows[first].set_open(True)
    state = edge.save_state()
    audit = AuditScreen()
    audit.apply(snap)
    audit.set_filter("DECISIONS")
    audit_state = audit.save_state()
    theme.set_theme("light")
    try:
        rebuilt = EdgeControlScreen()
        rebuilt.apply(snap)
        rebuilt.restore_state(state)
        assert rebuilt.demo_panel.kind() == "sync" and rebuilt.catalog_rows[first].open
        assert rebuilt.demo_panel.inject_payload() == {"kind": "pns", "link_id": snap.live.links[2].id,
                                                       "strength": 0.3, "duration_s": 45.0}
        assert rebuilt.demo_panel.decision_ids() == [d.id for d in reversed(snap.live.decisions) if d.kind == "sync"]
        rebuilt_audit = AuditScreen()
        rebuilt_audit.apply(snap)
        rebuilt_audit.restore_state(audit_state)
        assert rebuilt_audit._filter == "DECISIONS" and rebuilt_audit.filters["DECISIONS"].active
    finally:
        theme.set_theme("dark")
    close(edge, audit, rebuilt, rebuilt_audit)


# ------------------------------------------------- through the real window
def test_real_window_shares_selection_and_runs_the_commands(app, tmp_path):
    """Shell + screens + provider + backend together: nothing is dispatched by the test itself."""
    from quantum_autopilot.console.shell import ConsoleWindow, Selection
    provider = ServiceProvider(demo_service(ROOT, tmp_path), state_root=tmp_path, poll_ms=100, step_period_s=0.1)
    window = ConsoleWindow(provider, skip_auth=True, state_root=tmp_path)
    window.resize(1440, 900)
    window.show()
    window.provider.start()
    try:
        pump(app, lambda: window.snapshot is not None and window.snapshot.live is not None
             and window.snapshot.live.tick >= 4 and not window.provider.busy)
        window.splash.finished.emit()
        live = window.snapshot.live
        telemetry, keys, node, edge = (window.screens[key] for key in ("telemetry", "keys", "node", "fpga"))

        # a link selected in the shell is the link of Telemetry and of the live QKD panel
        link = live.links[5].id
        window.select_link(link, navigate=False)
        window.show_screen("telemetry")
        assert telemetry._link == link and telemetry.link_picker.current() == link
        window.show_screen("keys")
        assert keys.live_panel.current() == link
        keys.live_panel.picker.buttons[live.links[1].id].click()
        assert window.selection == Selection("link", live.links[1].id)
        window.show_screen("telemetry")
        assert telemetry._link == live.links[1].id

        # Nodes: inventory click selects in the shell; "Show on map" opens Topology on that node
        window.show_screen("node")
        node.rows["N-05"].click()
        assert window.selection == Selection("node", "N-05")
        pump(app, lambda: not window.provider.busy)
        node.map_button.click()
        assert window.current_screen == "topology" and window.selection == Selection("node", "N-05")

        # the form: PENDING until the backend's readback, then CONFIRMED and queued (no location given)
        window.show_screen("node")
        pump(app, lambda: not window.provider.busy)
        node.fields["id"].setText("N-41")
        node.fields["address"].setText("10.71.0.41")
        node.fields["port"].setText("4433")
        node.provision.click()
        assert "PENDING" in node.form_note.text()
        pump(app, lambda: node._pending_network is None and not window.provider.busy)
        assert "CONFIRMED" in node.form_note.text() and "queue on the Topology screen" in node.form_note.text()
        assert window.last_toast == "N-41 confirmed · in the Topology queue"
        assert not node.form_note.visibleRegion().isEmpty()            # the answer is on screen, not below the fold
        assert window.snapshot.live.node("N-41") is not None and not window.snapshot.live.node("N-41").located
        assert window.snapshot.ledger.commands[0].state == "CONFIRMED"

        # Edge control: the inject button reaches the backend through the shell
        window.show_screen("fpga")
        panel = edge.demo_panel
        target = window.snapshot.live.links[0].id
        panel.set_injection("loss_event", target, 0.6, 30)
        before = {event.id for event in window.snapshot.live.events}
        panel.inject_button.click()
        pump(app, lambda: any(event.kind == "input_event_injected" and event.subject == target
                              for event in window.snapshot.live.events if event.id not in before))
        pump(app, lambda: not window.provider.busy)
        pump(app, lambda: panel.decision_ids() == [d.id for d in reversed(window.snapshot.live.decisions)])

        # a decision's subject becomes the shell selection
        decision = next(d for d in reversed(window.snapshot.live.decisions)
                        if window.snapshot.live.link(d.subject) is not None)
        panel.select_decision(decision.id)
        assert window.selection == Selection("link", decision.subject)
        assert window.last_toast == f"Selected link {decision.subject}"

        # a theme change rebuilds the screens and keeps the selection and the live sections
        pump(app, lambda: not window.provider.busy)
        assert window.set_theme("light")
        rebuilt = window.screens["fpga"]
        assert rebuilt is not edge and rebuilt.demo_panel is not None
        assert rebuilt.demo_panel.decision_ids() == [d.id for d in reversed(window.snapshot.live.decisions)]
        assert window.screens["telemetry"]._link == decision.subject
        for key in ("telemetry", "keys", "node", "fpga", "audit"):
            window.show_screen(key)
            assert_no_prototype_fixture(window.screens[key])
        assert window.set_theme("dark")
    finally:
        window.close()
        theme.set_theme("dark")
        theme.install(app)
