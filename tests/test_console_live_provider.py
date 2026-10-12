"""Live network through the console provider — the REAL backend, no mocks.

Every test builds an actual `AssuranceDashboardService` (the demonstration one through
`demo_service`, or the ordinary one with its idle input source) on a temporary state
root and drives it through `ServiceProvider`, exactly as the application does. What is
asserted is what the backend produced and what the snapshot carries of it.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.contract import (  # noqa: E402
    CALIBRATION_PHASES, DEMAND_STATUSES, LINK_STATUSES, LiveView, Snapshot,
)
from quantum_autopilot.console.providers import MockProvider  # noqa: E402
from quantum_autopilot.console.service_provider import (  # noqa: E402
    LIVE_POLL_MS, ServiceProvider, live_view_from_state,
)
from quantum_autopilot.console.shell import ConsoleWindow  # noqa: E402
from quantum_autopilot.console.source_switch import SwitchableProvider  # noqa: E402
from quantum_autopilot.console.widgets.journal import journal_rows  # noqa: E402
from quantum_autopilot.console.widgets.palette import palette_commands  # noqa: E402
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService, demo_service  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STEP_THREAD = "console-network-step"
LOCATED = {"N-01", "N-02", "N-03", "R-07", "CORE", "N-05", "N-09", "N-11"}
UNLOCATED = {"N-12", "N-13"}


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.set_theme("dark")
    theme.install(instance)
    return instance


def pump(app, condition, timeout=30.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert condition()


def stepping_threads():
    return [thread for thread in threading.enumerate() if thread.name == STEP_THREAD and thread.is_alive()]


def stop_provider(app, provider):
    """Keep Qt alive while accepted work and exclusive resources are drained."""
    provider.stop()
    pump(app, lambda: not provider.stopping)
    assert provider._shutdown_error is None


class Harness:
    """A started provider plus what it emitted."""

    def __init__(self, app, provider):
        self.app, self.provider = app, provider
        self.errors: list[str] = []
        self.snapshots: list[Snapshot] = []
        provider.error.connect(self.errors.append)
        provider.snapshot_changed.connect(self.snapshots.append)
        provider.start()
        pump(app, lambda: provider._snap is not None and provider._snap.live is not None)

    @property
    def snap(self) -> Snapshot:
        return self.provider.snapshot()

    @property
    def live(self) -> LiveView:
        return self.snap.live

    def wait_ticks(self, count):
        target = self.live.tick + count
        pump(self.app, lambda: self.live.tick >= target)

    def run(self, verb, payload):
        """Dispatch like the UI does (JSON text) and wait for the command to end."""
        pump(self.app, lambda: not self.provider._foreground_busy)
        assert self.provider.dispatch(verb, json.dumps(payload)) is True
        pump(self.app, lambda: not self.provider._foreground_busy)
        return self.snap

    def last_command(self):
        return self.snap.ledger.commands[0]


@pytest.fixture()
def demo(app, tmp_path):
    provider = ServiceProvider(demo_service(ROOT, tmp_path), state_root=tmp_path, poll_ms=100, step_period_s=0.1)
    try:
        harness = Harness(app, provider)
        yield harness
    finally:
        stop_provider(app, provider)
        assert not provider.stepping


@pytest.fixture()
def idle(app, tmp_path):
    service = AssuranceDashboardService(ROOT, control_journal_path=tmp_path / "operator.sqlite")
    provider = ServiceProvider(service, state_root=tmp_path, poll_ms=100, step_period_s=0.1)
    try:
        harness = Harness(app, provider)
        yield harness
    finally:
        stop_provider(app, provider)


# ----------------------------------------------------------------- snapshot
def test_demo_snapshot_maps_the_backend_state(demo):
    assert LIVE_POLL_MS == 1000 and ServiceProvider(demo.provider.service)._timer.interval() == 1000
    demo.wait_ticks(3)
    pump(demo.app, lambda: all(link.qber is not None for link in demo.live.links))
    snap = demo.snap
    live = snap.live
    assert snap.input_kind == "demo"
    identity = demo.provider.service.security.current_identity()
    assert snap.operator == (identity.display_name or identity.subject)
    assert snap.security["subject"] == identity.subject and snap.security["source"] == "os"
    assert live.source_kind == "simulated" and "Simulated input" in live.source_label and live.source_note
    assert live.tick >= 3 and live.network_time_s == float(live.tick) and live.schema == "orion.network-live.v1"

    nodes = {node.id: node for node in snap.topology.nodes}
    assert set(nodes) == LOCATED | UNLOCATED
    assert {node_id for node_id, node in nodes.items() if node.located} == LOCATED
    assert all(nodes[node_id].latitude is None and nodes[node_id].place is None for node_id in UNLOCATED)
    assert nodes["N-01"].place == "Piatã · SENAI CIMATEC area" and nodes["N-01"].latitude == pytest.approx(-12.938)
    assert nodes["CORE"].kind == "core" and nodes["R-07"].kind == "repeater" and nodes["N-05"].kind == "node"
    assert all(node.status == "up" and node.health == "up" for node in nodes.values())
    assert all(0.0 <= node.x <= 1.0 and 0.0 <= node.y <= 1.0 for node in nodes.values())   # logical GRAPH layout
    assert {node.id for node in live.unlocated_nodes} == UNLOCATED
    assert live.node("N-13").links == ("N05-N13",) and live.node("N-01").address == "10.71.0.1"
    assert live.node("N-01").port == 4433 and live.node("N-01").temperature_c is not None

    assert len(snap.topology.links) == 11 == len(live.links)
    by_id = {link.id: link for link in snap.topology.links}
    for link in live.links:
        assert link.status in LINK_STATUSES and link.effective and link.configured_active is True
        for value in (link.qber, link.fidelity, link.skr_bps, link.sifted_rate_hz, link.sync_residual_ps,
                      link.gate_efficiency, link.polarisation_residual_mrad, link.stability):
            assert isinstance(value, float)
        assert link.fidelity == pytest.approx(1 - 1.5 * link.qber)       # the backend's Werner estimate, untouched
        assert link.input == "simulated" and link.display_provenance == "simulated"
        assert link.wave and link.wave_rate_hz == 20.0 and len(link.history.t_s) == len(link.history.qber) >= 1
        assert link.qkd.protocol == "decoy_state_bb84" and link.qkd.signal_intensity is not None
        assert {controller.role for controller in link.controllers} >= {"compensation", "sync", "keyrate", "detector"}
        assert link.classical_admission is not None and link.classical_admission.controller_id
        assert {note.role for note in link.domain_notes} >= {"detector", "keyrate", "sync", "compensation"}
        assert link.provenance["physical_action_performed"] is False
        shown = by_id[link.id]
        assert shown.provenance == "simulated" and shown.status == link.status and shown.effective
        assert shown.fidelity == f"{link.fidelity:.4f}" and shown.qber == f"{link.qber * 100:.2f} %"
        assert shown.skr.endswith("bit/s") and shown.stability == link.stability
    assert by_id["N01-N02"].distance_km == pytest.approx(live.link("N01-N02").distance_km)
    assert live.link("N05-N13").distance_km == 6.5                        # configured: one end has no location

    assert {demand.id for demand in live.demands} == {"P1", "P2", "P3", "P4"}
    assert all(demand.status in DEMAND_STATUSES and demand.route and demand.policy_id for demand in live.demands)
    assert live.demand("P1").min_fidelity == 0.7 and "trigger" in live.demand("P1").detail
    assert {model.role for model in live.models} == {"compensation", "sync", "keyrate", "detector", "routing",
                                                     "allocation"}
    assert live.decisions and all(len(decision.record_sha256) == 64 for decision in live.decisions)
    assert any(event.kind == "input_source_changed" for event in live.events)

    # The boot checks, the counts and the journal are read from the same backend state.
    checks = {check.body: check.status for check in snap.boot.checks}
    assert checks["input source"] == "SIMULATED INPUT · BACKEND ENGINE"
    assert checks["network topology"].startswith("10 NODES (8 LOCATED) · 11 FIBRES")
    states = [model.state for model in live.models]
    assert checks["model registry"] == (f"{states.count('active')} ACTIVE · {states.count('fallback')} FALLBACK · "
                                        f"{states.count('unavailable')} BASELINE ONLY")
    assert checks["demonstration journal"] == "DEMO/SANDBOX_JOURNAL.SQLITE"
    assert [count.key for count in snap.counts][:3] == ["10/10 NODES UP", "11/11 FIBRES IN SERVICE",
                                                        "4/4 DEMANDS ROUTED"]
    digests = {entry.digest for entry in snap.ledger.entries}
    assert any(decision.record_sha256[:16] in digests for decision in live.decisions)
    assert any(event.id in digests for event in live.events)
    assert any("reroute" in row.text for row in journal_rows(snap))
    # The mapping is a pure function of the backend payload.
    state = demo.provider.service.network_live_state()
    assert live_view_from_state(state, demo.provider.service.control_plane_topology()).links[0].id == "N01-N02"


def test_wave_grows_with_the_ticks_and_only_the_stepping_thread_advances_time(demo):
    first = demo.live
    demo.wait_ticks(3)
    later = demo.live
    assert later.tick >= first.tick + 3
    for link in later.links:
        assert len(link.wave) > len(first.link(link.id).wave) or len(link.wave) == 240
        assert len(link.wave) == min(240, 20 * later.tick)
        assert all(-1.0 <= sample <= 1.0 for sample in link.wave)
    # Reading the state does not step the engine.
    demo.provider._step_hold.set()
    time.sleep(.5)
    tick = demo.provider.service.network_live_state()["tick"]
    for _ in range(20):
        demo.provider.build()
    time.sleep(.3)
    assert demo.provider.service.network_live_state()["tick"] == tick
    demo.provider._step_hold.clear()
    demo.wait_ticks(1)


def test_stepping_thread_starts_and_stops_cleanly(app, tmp_path):
    provider = ServiceProvider(demo_service(ROOT, tmp_path), poll_ms=100, step_period_s=0.05)
    received = []
    provider.snapshot_changed.connect(received.append)
    try:
        assert not provider.stepping and not stepping_threads()
        assert provider.build().live.tick == 0                    # an unstarted provider reads, never steps
        provider.start()
        # Startup admits the complete first read before enabling the engine.
        pump(app, lambda: bool(received) and provider.stepping)
        assert provider.stepping and len(stepping_threads()) == 1 and stepping_threads()[0].daemon
        pump(app, lambda: provider.steps_taken >= 3)
        stop_provider(app, provider)
        assert not provider.stepping and not stepping_threads() and provider._executor is None
        tick = provider.service.network_live_state()["tick"]
        time.sleep(.4)
        assert provider.service.network_live_state()["tick"] == tick       # no stepping after stop()
        first_run_frames = len(received)
        provider.start()                                                   # and it can be started again
        pump(app, lambda: len(received) > first_run_frames and provider.stepping)
        pump(app, lambda: provider.service.network_live_state()["tick"] > tick)
        stop_provider(app, provider)
        assert not stepping_threads()
    finally:
        stop_provider(app, provider)


def test_source_switch_does_not_leak_a_stepping_thread(app, tmp_path):
    first = ServiceProvider(demo_service(ROOT, tmp_path / "a"), poll_ms=100, step_period_s=0.05)
    plug = SwitchableProvider(first)
    received = []
    plug.snapshot_changed.connect(received.append)
    second = None
    try:
        plug.start()
        pump(app, lambda: bool(received) and received[-1].live is not None and received[-1].live.tick >= 2)
        assert plug.is_demo and plug.source_label.startswith("DEMONSTRATION") and len(stepping_threads()) == 1
        plug.switch(lambda: MockProvider())
        pump(app, lambda: not first.stopping)
        assert not first.stepping and not stepping_threads() and not plug.is_demo
        tick = first.service.network_live_state()["tick"]
        count = len(received)
        time.sleep(.3)
        app.processEvents()
        assert first.service.network_live_state()["tick"] == tick
        assert all(snapshot.live is None for snapshot in received[count:])   # the old source is silent
        second = ServiceProvider(demo_service(ROOT, tmp_path / "b"), poll_ms=100, step_period_s=0.05)
        plug.switch(lambda: second)
        pump(app, lambda: plug.snapshot().live is not None and plug.snapshot().input_kind == "demo")
        assert second.stepping and len(stepping_threads()) == 1
        plug.stop()
        pump(app, lambda: not second.stopping)
        assert not stepping_threads()
    finally:
        plug.stop()
        stop_provider(app, first)
        if second is not None:
            stop_provider(app, second)


# ----------------------------------------------------------------- commands
def test_disabling_a_routed_fibre_is_confirmed_and_the_backend_reroutes(demo):
    demo.wait_ticks(2)
    before = demo.live.demand("P1")
    assert before.status in ("routed", "rerouted") and before.route_links
    target = before.route_links[1]
    affected = [demand.id for demand in demo.live.demands if target in demand.route_links]
    version = demo.snap.topology.version

    snap = demo.run("set_link_active", {"link_id": target, "active": False})   # bookkeeping filled by the provider
    assert not demo.errors
    command = demo.last_command()
    record = json.loads(command.serialized)["record"]
    assert (command.verb, command.state) == ("set_link_active", "CONFIRMED")
    assert [event["state"] for event in record["events"]] == ["REQUESTED", "RUNNING", "CONFIRMED"]
    assert record["request"] == {"link_id": target, "active": False, "expected_version": version,
                                 "topology_id": snap.topology.id}
    assert record["physical_action_performed"] is False and snap.topology.version == version + 1

    shown = next(link for link in snap.topology.links if link.id == target)
    assert shown.configured_active is False and shown.status == "disabled" and shown.effective is False
    assert (shown.fidelity, shown.qber, shown.skr) == ("—", "—", "—") and shown.kind == "fault"
    live_link = snap.live.link(target)
    assert live_link.status == "disabled" and not live_link.effective and live_link.qber is None
    assert live_link.configured_active is False and live_link.status_reasons == ("imposed: disabled",)
    for demand_id in affected:
        demand = snap.live.demand(demand_id)
        assert target not in demand.route_links
        if demand.status in ("no_route", "blocked"):
            assert demand.reason and not demand.route            # the backend's own explanation
        else:
            assert demand.status == "rerouted" and demand.route != before.route or demand_id != "P1"
            assert target in " ".join(demand.detail["trigger"].split()) or demand.detail["previous_route"]
    assert any(decision.kind in ("reroute", "no_route") and decision.subject in affected and decision.tick > 0
               for decision in snap.live.decisions)
    assert [count.key for count in snap.counts][1] == "10/11 FIBRES IN SERVICE"
    assert any(event.kind == "LINK_DEACTIVATED" for event in snap.topology.events)
    assert any(row.origin == "command" and row.text.startswith("set_link_active") and row.status == "CONFIRMED"
               for row in journal_rows(snap))

    snap = demo.run("set_link_active", {"link_id": target, "active": True})
    assert demo.last_command().state == "CONFIRMED" and not demo.errors
    assert snap.live.link(target).configured_active is True
    # The engine publishes the fibre's own status again from the next network second it processes.
    pump(demo.app, lambda: demo.live.link(target).status != "disabled" and demo.live.link(target).qber is not None)
    assert demo.live.link(target).effective


def test_isolating_and_restoring_a_node(demo):
    demo.wait_ticks(2)
    snap = demo.run("set_node_isolation", {"node_id": "R-07", "isolated": True})
    assert demo.last_command().state == "CONFIRMED" and not demo.errors
    node = next(item for item in snap.topology.nodes if item.id == "R-07")
    assert node.isolated and node.status == "isolated" and node.health == "down" and "ISOLATED" in node.role
    live = snap.live
    assert live.node("R-07").status == "isolated" and live.node("R-07").routes_through == ()
    for link_id in live.node("R-07").links:
        assert live.link(link_id).status in ("isolated", "disabled") and not live.link(link_id).effective
    assert all("R-07" not in demand.route for demand in live.demands)
    assert any(demand.status in ("rerouted", "no_route", "blocked") for demand in live.demands)

    snap = demo.run("set_node_isolation", {"node_id": "R-07", "isolated": False})
    assert demo.last_command().state == "CONFIRMED" and not demo.errors
    node = next(item for item in snap.topology.nodes if item.id == "R-07")
    assert not node.isolated and node.status != "isolated"
    # Each fibre's own status is published again from the next network second.
    pump(demo.app, lambda: all(demo.live.link(link_id).effective for link_id in demo.live.node("R-07").links))
    assert all(demo.live.link(link_id).configured_active for link_id in demo.live.node("R-07").links)


def test_locating_an_unlocated_node_keeps_its_fibres(demo):
    before = demo.live.link("N05-N13")
    assert before.distance_km == 6.5 and not demo.live.node("N-13").located
    snap = demo.run("set_node_location", {"node_id": "N-13", "latitude": -13.005, "longitude": -38.46,
                                          "place": "Amaralina"})
    assert demo.last_command().state == "CONFIRMED" and not demo.errors
    node = next(item for item in snap.topology.nodes if item.id == "N-13")
    assert node.located and (node.latitude, node.longitude, node.place) == (-13.005, -38.46, "Amaralina")
    live = snap.live
    assert live.node("N-13").located and live.node("N-13").place == "Amaralina"
    assert live.node("N-13").links == ("N05-N13",) and {link.id for link in live.links} >= {"N05-N13"}
    assert {node.id for node in live.unlocated_nodes} == {"N-12"}
    link = next(item for item in snap.topology.links if item.id == "N05-N13")
    assert (link.a, link.b) == ("N-05", "N-13") and link.configured_active is True
    assert 0.5 < link.distance_km < 3.0 and link.distance_km != 6.5       # geodesic, computed by the backend
    assert live.link("N05-N13").distance_km == pytest.approx(link.distance_km)

    snap = demo.run("set_node_location", {"node_id": "N-13"})            # back to the queue
    assert demo.last_command().state == "CONFIRMED"
    assert not next(item for item in snap.topology.nodes if item.id == "N-13").located
    assert snap.live.node("N-13").links == ("N05-N13",)


def test_adding_nodes_with_and_without_location(demo):
    snap = demo.run("add_network_node", {"node_id": "N-20", "role": "endpoint", "address": "10.71.0.20",
                                         "port": 4433, "latitude": -12.99, "longitude": -38.44,
                                         "place": "Stiep"})
    assert demo.last_command().state == "CONFIRMED" and not demo.errors
    snap = demo.run("add_network_node", {"node_id": "N-21", "role": "repeater", "address": "10.71.0.21",
                                         "port": 4433})
    assert demo.last_command().state == "CONFIRMED" and not demo.errors
    nodes = {node.id: node for node in snap.topology.nodes}
    assert nodes["N-20"].located and nodes["N-20"].place == "Stiep" and nodes["N-20"].connection_address == "10.71.0.20"
    assert not nodes["N-21"].located and nodes["N-21"].kind == "repeater" and nodes["N-21"].latitude is None
    live = snap.live
    assert live.node("N-20").located and live.node("N-20").links == () and live.node("N-20").status in ("up", "down")
    assert {node.id for node in live.unlocated_nodes} == UNLOCATED | {"N-21"}
    assert len(live.nodes) == 12 and len(live.links) == 11


def test_full_recalibration_shows_its_phases_and_persists_a_report(demo):
    demo.wait_ticks(8)                                   # let the fibres settle so there is a "before"
    version = demo.snap.topology.version
    seen: list[tuple] = []

    def record(snapshot):
        node = snapshot.live.node("R-07") if snapshot.live is not None else None
        if node is not None and node.calibration is not None:
            seen.append((snapshot.live.tick, node.status, node.calibration.status, node.calibration.phase,
                         node.calibration.progress, tuple(snapshot.live.link(link_id).status for link_id in node.links),
                         next(item for item in snapshot.topology.nodes if item.id == "R-07").status))
    demo.provider.snapshot_changed.connect(record)
    snap = demo.run("recalibrate_node", {"node_id": "R-07"})
    assert not demo.errors
    command = demo.last_command()
    assert (command.verb, command.state) == ("recalibrate_node", "CONFIRMED")
    assert snap.topology.version == version + 1

    running = [item for item in seen if item[2] == "running"]
    phases = [item[3] for item in running]
    assert len(set(phases)) >= 3, seen                   # successive snapshots, while the command was running
    assert phases == sorted(phases, key=CALIBRATION_PHASES.index)
    assert all(item[1] == "calibrating" and item[6] == "calibrating" for item in running)
    assert all(status == "calibrating" for item in running for status in item[5])
    progress = [item[4] for item in running]
    assert progress == sorted(progress) and all(0.0 <= value <= 1.0 for value in progress)

    node = snap.live.node("R-07")
    assert node.status != "calibrating" and node.calibration_count == 1
    calibration = node.calibration
    assert calibration.status == "complete" and calibration.phase == "complete" and calibration.progress == 1.0
    assert len(calibration.report_sha256) == 64 and calibration.memory_reset is True and calibration.duration_s > 0
    assert {link.link_id for link in calibration.links} == set(node.links)
    assert all(link.qber_after is not None and link.detail for link in calibration.links)
    shown = next(item for item in snap.topology.nodes if item.id == "R-07")
    assert shown.calibration_count == 1 and shown.status != "calibrating"
    # The report is in the persisted topology, not only in the live view.
    stored = demo.provider.service.topology_store.get(snap.topology.id)
    control = next(item for item in stored.nodes if item.node_id == "R-07").telemetry["console_control"]
    assert control["calibration"]["report_sha256"] == calibration.report_sha256
    assert control["calibration_count"] == 1
    assert any(decision.kind == "calibration" and decision.subject == "R-07" for decision in snap.live.decisions)
    assert any(event.kind == "NODE_RECALIBRATED" for event in snap.topology.events) or any(
        "node_recalibrated" in entry.action for entry in snap.ledger.entries)
    tick = snap.live.tick
    demo.wait_ticks(2)                                   # the stepping thread took over again
    assert demo.live.tick >= tick + 2 and all(demo.live.link(link_id).effective for link_id in node.links)


def test_backend_refusals_reach_the_error_signal_with_the_backend_message(demo):
    demo.run("set_node_isolation", {"node_id": "N-11", "isolated": True})
    assert demo.last_command().state == "CONFIRMED" and not demo.errors
    version = demo.snap.topology.version
    demo.run("set_link_active", {"link_id": "N09-N11", "active": True})   # an end is isolated: the store refuses
    assert len(demo.errors) == 1 and "isolated" in demo.errors[0] and "N-11" in demo.errors[0]
    pump(demo.app, lambda: demo.last_command().verb == "set_link_active")
    command = demo.last_command()
    assert (command.verb, command.state) == ("set_link_active", "UNKNOWN") and command.command_id in demo.errors[0]
    assert demo.snap.topology.version == version and demo.live.link("N09-N11").status == "isolated"

    demo.errors.clear()
    demo.run("purify_pair", {"demand_id": "P2"})                          # an engine verb: nothing to purify
    assert demo.errors == ["demand P2 holds no delivered pair to purify; request one first"]
    demo.errors.clear()
    demo.run("set_link_active", {"link_id": "NO-SUCH", "active": False})
    assert len(demo.errors) == 1 and "NO-SUCH" in demo.errors[0]
    demo.errors.clear()
    demo.run("configure_network_input", {"kind": "unsupported-hardware"})
    assert demo.errors == ["input kind must be simulated, none or measured"]
    assert demo.snap.topology.version == version


def test_pair_operations_and_input_events_run_in_the_backend(demo):
    demo.wait_ticks(2)
    snap = demo.run("request_pair", {"demand_id": "P2"})
    assert not demo.errors
    pair = snap.live.demand("P2").pair
    assert pair is not None and pair["status"] in ("delivered", "failed") and pair["physical_action_performed"] is False
    assert any(event.kind in ("pair_delivered", "pair_failed") and event.subject == "P2" for event in snap.live.events)
    assert demo.provider.last_network_result["verb"] == "request_pair"
    if pair["status"] == "delivered":
        assert pair["route"] == list(snap.live.demand("P2").route) and 0.0 <= pair["fidelity"] <= 1.0
        snap = demo.run("consume_pair", {"demand_id": "P2"})
        assert not demo.errors and snap.live.demand("P2").pair["status"] == "consumed"

    snap = demo.run("define_pair", {"demand_id": "P9", "a": "N-02", "b": "N-11"})
    assert not demo.errors and snap.live.demand("P9").status in DEMAND_STATUSES and snap.live.demand("P9").a == "N-02"
    snap = demo.run("remove_pair", {"demand_id": "P9"})
    assert not demo.errors and snap.live.demand("P9") is None

    snap = demo.run("inject_input_event", {"kind": "loss_event", "link_id": "N01-N02", "strength": 1.0,
                                           "duration_s": 30.0})
    assert not demo.errors
    assert any(event.kind == "input_event_injected" and event.subject == "N01-N02" and event.source == "operator"
               for event in snap.live.events)
    pump(demo.app, lambda: any(event.kind == "input_event_started" and event.subject == "N01-N02"
                               and event.source == "input_source" for event in demo.live.events))
    demo.run("inject_input_event", {"kind": "no_such_event", "link_id": "N01-N02"})
    assert len(demo.errors) == 1 and "unknown input event" in demo.errors[0]


def test_capabilities_per_source(demo, idle):
    live_ops = {"read_network_live", "add_network_node", "set_node_location", "set_node_isolation",
                "set_link_active", "recalibrate_node", "define_pair", "remove_pair", "request_pair", "purify_pair",
                "consume_pair", "set_model_archive"}
    demo_caps, idle_caps = demo.snap.capabilities, idle.snap.capabilities
    assert demo_caps.adapter_id == "assurance-demo-service" and demo_caps.mode == "simulation"
    assert "input origin is identified in each live frame" in demo_caps.note
    assert live_ops | {"inject_input_event"} <= set(demo_caps.operations)
    assert idle_caps.adapter_id == "assurance-local-service" and idle_caps.mode == "simulation"
    assert live_ops <= set(idle_caps.operations) and not idle_caps.allows("inject_input_event")
    assert demo_caps.allows("configure_network_input") and idle_caps.allows("configure_network_input")
    assert demo.provider.source_label.startswith("DEMONSTRATION") and idle.provider.source_label.startswith("SERVICE")


def test_raw_acquisition_file_flows_through_the_provider_and_is_identified_as_replay(app, tmp_path):
    from types import SimpleNamespace
    from quantum_autopilot.console.live_commands import _input_source
    from quantum_autopilot.control_plane.topology import DEFAULT_TOPOLOGY_ID
    from quantum_autopilot.network_engine.channel_io import observation_record
    from quantum_autopilot.network_engine.sources import SimulatedInputSource

    service = demo_service(ROOT, tmp_path / "workspace")
    provider = ServiceProvider(service, state_root=tmp_path / "workspace")
    provider.refresh()
    net = service.topology_store.get(DEFAULT_TOPOLOGY_ID)
    # Explicitly artificial test data; the declared measured label is not hardware evidence.
    frame = SimulatedInputSource(19, "quiet").frame(1, net)
    path = tmp_path / "raw acquisition fixture.json"
    path.write_text(json.dumps({"schema": "orion.channel-capture.v1", "source_id": "artificial-provider-test",
        "kind": "measured", "frames": [{"tick": 1, "links": [
            {"observation": observation_record(item), "quantum_record": {
                **item.quantum_record, "scope": "recorded_measurement"}} for item in frame.links.values()]}]}),
        encoding="utf-8")
    ui = SimpleNamespace(_command=provider.dispatch)
    try:
        feedback = _input_source(ui, f'measured "{path}"')
        assert feedback.status == "DISPATCHED"
        service.network_step()
        provider.refresh()
        snap = provider.snapshot()
        assert snap.live.source_kind == "measured" and "Raw acquisition replay" in snap.live.source_label
        assert "RAW ACQUISITION REPLAY" in provider.source_label
        assert provider.source_label == snap.boot.subtitle
        assert "file replay" in snap.boot.notice
        assert not snap.capabilities.allows("inject_input_event")
        assert all(link.provenance["channels"]["status"] == "received" for link in snap.live.links)
        assert all(link.provenance["channels"]["physical_transmission"] is False for link in snap.live.links)
        service.network_step()
        provider.refresh()
        assert all(link.qber is None for link in provider.snapshot().live.links), "EOF must not repeat node values"
        _input_source(ui, "none")
        assert provider.snapshot().live.source_kind == "none" and "NO INPUT" in provider.source_label
    finally:
        stop_provider(app, provider)


# ------------------------------------------------------------ normal operation
def test_idle_service_shows_configuration_and_routing_but_no_observables(idle):
    idle.wait_ticks(3)
    snap = idle.snap
    live = snap.live
    assert snap.input_kind == "service" and not idle.provider.is_demo
    identity = idle.provider.service.security.current_identity()
    assert snap.operator == (identity.display_name or identity.subject)
    assert snap.security["subject"] == identity.subject and snap.security["source"] == "os"
    assert live.source_kind == "none" and live.tick >= 3
    assert live.links and all(link.status == "no_input" and link.input == "none" for link in live.links)
    for link in live.links:
        assert link.wave == () and link.history.qber in ((), (None,) * len(link.history.t_s))
        assert all(value is None for value in (
            link.qber, link.fidelity, link.skr_bps, link.sifted_rate_hz, link.sync_residual_ps,
            link.gate_efficiency, link.polarisation_residual_mrad, link.stability))
        assert link.detector.label is None and link.detector.attack_probability is None
        assert link.classical_admission is None and link.display_provenance == "assumed"
    for link in snap.topology.links:
        assert (link.fidelity, link.qber, link.skr) == ("—", "—", "—")
        assert link.provenance == "assumed" and link.status == "no_input" and link.effective is True
        assert link.nominal_pair_fidelity == 0.9 and link.distance_km > 0       # configuration is still shown
    checks = {check.body: check.status for check in snap.boot.checks}
    assert checks["input source"] == "NO INPUT FEED · CONFIGURATION ONLY"
    assert "No input feed is configured" in snap.topology.caveat

    ends = [node.id for node in snap.topology.nodes if node.kind == "node"]
    snap = idle.run("define_pair", {"demand_id": "D1", "a": ends[0], "b": ends[-1]})
    assert not idle.errors
    demand = snap.live.demand("D1")
    assert demand.status in ("routed", "blocked") and demand.policy_id and demand.detail["trigger"]
    if demand.status == "routed":
        assert demand.route[0] == ends[0] and demand.route[-1] == ends[-1] and demand.route_links
    else:
        assert demand.reason
    assert any(decision.subject == "D1" for decision in snap.live.decisions)     # routing ran in the backend

    with pytest.raises(ValueError, match="unavailable"):
        idle.provider.dispatch("inject_input_event", json.dumps({"kind": "loss_event",
                                                                 "link_id": snap.topology.links[0].id}))
    with pytest.raises(ValueError, match="unavailable|only be injected"):
        idle.provider._engine_command("inject_input_event", json.dumps({"kind": "loss_event",
                                                                        "link_id": snap.topology.links[0].id}))
    assert not any(event.kind.startswith("input_event") for event in idle.live.events)


def test_sources_without_an_engine_carry_no_live_view(app, tmp_path):
    from quantum_autopilot.console.synthetic import SyntheticProvider
    from tests.test_console_service_provider import FakeService

    assert MockProvider()._build().live is None
    assert SyntheticProvider(ROOT, tmp_path / "synthetic").snapshot().live is None
    provider = ServiceProvider(FakeService())
    snap = provider.build()
    assert snap.live is None and not provider.has_network_engine and provider._timer.interval() == 4000
    assert not snap.capabilities.allows("set_link_active") and snap.input_kind == "service"
    assert [link.qber for link in snap.topology.links] == ["not in this lane"] * 2
    provider.start()
    assert not provider.stepping and not stepping_threads()
    stop_provider(app, provider)


# ------------------------------------------------------- terminal and palette
@pytest.fixture()
def window(app, tmp_path):
    provider = ServiceProvider(demo_service(ROOT, tmp_path), state_root=tmp_path, poll_ms=100, step_period_s=0.1)
    win = ConsoleWindow(provider, skip_auth=True, state_root=tmp_path)
    errors = []
    win.provider.error.connect(errors.append)
    win.provider.start()
    pump(app, lambda: win.snapshot is not None and win.snapshot.live is not None and win.snapshot.live.tick >= 2)
    win.splash.finished.emit()
    win.resize(1440, 900)
    win.show()
    app.processEvents()
    win.errors = errors
    yield win
    win.close()
    pump(app, lambda: not provider.stopping)
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    assert not stepping_threads()


def test_terminal_verbs_build_the_real_backend_requests(window, app, monkeypatch):
    snap = window.snapshot
    book = {"expected_version": snap.topology.version, "topology_id": snap.topology.id}
    sent = []
    monkeypatch.setattr(window, "_command", lambda verb, target: sent.append((verb, json.loads(target))))
    lines = [
        ("isolate n-05", "set_node_isolation", {"node_id": "N-05", "isolated": True, **book}),
        ("restore N-05", "set_node_isolation", {"node_id": "N-05", "isolated": False, **book}),
        ("recalibrate R-07", "recalibrate_node", {"node_id": "R-07", **book}),
        ("disable R07-CORE", "set_link_active", {"link_id": "R07-CORE", "active": False, **book}),
        ("enable r07 core", "set_link_active", {"link_id": "R07-CORE", "active": True, **book}),
        ("locate N-12 -12.9716 -38.5016 Barra · Farol", "set_node_location",
         {"node_id": "N-12", "latitude": -12.9716, "longitude": -38.5016, "place": "Barra · Farol", **book}),
        ("unlocate N-01", "set_node_location", {"node_id": "N-01", "latitude": None, "longitude": None,
                                                "place": "", **book}),
        ("request_pair p1", "request_pair", {"demand_id": "P1"}),
        ("purify_pair P1", "purify_pair", {"demand_id": "P1"}),
        ("consume_pair P1", "consume_pair", {"demand_id": "P1"}),
        ("define_pair P7 N-01 N-11 0.75", "define_pair", {"demand_id": "P7", "a": "N-01", "b": "N-11",
                                                          "min_fidelity": 0.75}),
        ("remove_pair P4", "remove_pair", {"demand_id": "P4"}),
        ("inject thermal_drift N01-N02 0.8 45", "inject_input_event",
         {"kind": "thermal_drift", "link_id": "N01-N02", "strength": 0.8, "duration_s": 45.0}),
        ("inject pns CORE-N05", "inject_input_event", {"kind": "pns", "link_id": "CORE-N05"}),
        ("model_archive qnet none", "set_model_archive", {"kind": "qnet", "path": None}),
    ]
    for line, verb, payload in lines:
        feedback = window.run_terminal(line)
        assert feedback.status == "DISPATCHED", (line, feedback)
        assert sent[-1] == (verb, payload), line
    count = len(sent)
    for line in ("isolate N-99", "enable NO-LINK", "request_pair P99", "locate N-12 south west",
                 "inject earthquake N01-N02", "locate N-12", "model_archive tensor x"):
        assert window.run_terminal(line).status == "ERR", line
    assert len(sent) == count
    assert all(usage in window.run_terminal("help").text for usage in (
        "isolate <node>", "restore <node>", "recalibrate <node>", "enable <link>", "disable <link>",
        "locate <node> <lat> <lon> [place]", "request_pair <demand>", "inject <kind> <link>"))


def test_palette_entries_are_generated_from_the_backend_state(window, app):
    snap = window.snapshot
    commands = palette_commands(snap)
    labels = {command.label: command for command in commands}
    for node in snap.topology.nodes:
        assert labels[f"Recalibrate {node.id} (full)"].operation == "recalibrate_node"
        assert labels[f"Isolate {node.id}"].operation == "set_node_isolation"
    for link in snap.topology.links:
        assert (labels[f"Disable fibre {link.id}"].verb, labels[f"Disable fibre {link.id}"].target) == ("disable", link.id)
        assert f"Inject input event intercept_resend on {link.id}" in labels
    for demand in snap.live.demands:
        assert any(label.startswith(f"Request pair {demand.id} ") for label in labels)
    assert not any(label.startswith(("Purify pair", "Consume pair", "Enable fibre", "Restore ")) for label in labels)
    assert all(command.operation is None or snap.capabilities.allows(command.operation) for command in commands)
    assert not any("synthetic" in label.lower() or "lab" == label.lower() for label in labels)

    # Entries follow the state: run two of them for real and read the next snapshot.
    window.run_palette_command(labels["Disable fibre N11-N05"])
    pump(app, lambda: not window.provider.busy and window.snapshot.live.link("N11-N05").status == "disabled")
    window.run_palette_command(labels["Isolate N-02"])
    pump(app, lambda: not window.provider.busy and window.snapshot.live.node("N-02").status == "isolated")
    assert not window.errors
    after = {command.label for command in palette_commands(window.snapshot)}
    assert {"Enable fibre N11-N05", "Restore N-02"} <= after
    assert "Disable fibre N11-N05" not in after and "Isolate N-02" not in after
    rows = window.journal.rows()
    assert sum(row.origin == "command" and row.status == "CONFIRMED" for row in rows) == 2
    assert [command.verb for command in window.snapshot.ledger.commands[:2]] == ["set_node_isolation",
                                                                                 "set_link_active"]


def test_terminal_dispatch_reaches_the_backend_and_refusals_are_shown(window, app):
    assert window.run_terminal("disable R07-CORE").status == "DISPATCHED"
    pump(app, lambda: not window.provider.busy and window.snapshot.live.link("R07-CORE").status == "disabled")
    assert window.snapshot.ledger.commands[0].state == "CONFIRMED" and not window.errors
    assert window.run_terminal("isolate CORE").status == "DISPATCHED"
    pump(app, lambda: not window.provider.busy and window.snapshot.live.node("CORE").status == "isolated")
    assert window.run_terminal("enable R07-CORE").status == "DISPATCHED"      # refused by the backend: CORE isolated
    pump(app, lambda: bool(window.errors) and not window.provider.busy)
    assert "isolated" in window.errors[0]
    assert window.statusBar().currentMessage().startswith("Operation failed:") and "isolated" in window.statusBar().currentMessage()
    assert window.snapshot.live.link("R07-CORE").status in ("disabled", "isolated")


def test_inject_is_refused_outside_the_demonstration(app, tmp_path):
    service = AssuranceDashboardService(ROOT, control_journal_path=tmp_path / "operator.sqlite")
    provider = ServiceProvider(service, state_root=tmp_path, poll_ms=100, step_period_s=0.1)
    win = ConsoleWindow(provider, skip_auth=True, state_root=tmp_path)
    try:
        win.provider.start()
        pump(app, lambda: win.snapshot is not None and win.snapshot.live is not None)
        link = win.snapshot.topology.links[0]
        feedback = win.run_terminal(f"inject loss_event {link.id}")
        assert feedback.status == "REFUSED" and "inject_input_event" in feedback.text
        assert "assurance-local-service" in feedback.text
        assert not any(command.verb == "inject" for command in palette_commands(win.snapshot))
        assert win.active_source_key() == "service"
        identity = service.security.current_identity()
        assert win.snapshot.operator == (identity.display_name or identity.subject)
        assert win.snapshot.security["subject"] == identity.subject
        dispatched = win.run_terminal(f"disable {link.id}")
        assert dispatched.status == "DISPATCHED", dispatched.text   # the other verbs work here too
        pump(app, lambda: not win.provider.busy and win.snapshot.live.link(link.id).status == "disabled")
    finally:
        win.close()
        pump(app, lambda: not provider.stopping)
        win.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    assert not stepping_threads()


# ----------------------------------------------------------------- launcher
def test_desktop_flags_select_the_demonstration_and_forward_model_archives(tmp_path, monkeypatch, capsys):
    from quantum_autopilot import desktop
    from quantum_autopilot import desktop_instance
    from quantum_autopilot.console import app as console_app
    from contextlib import AbstractContextManager

    class Owner(AbstractContextManager):
        is_primary = True
        def __exit__(self, *args): pass
    monkeypatch.setattr(desktop_instance, 'acquire_desktop_instance', Owner)

    for argv in (["--demo", "--synthetic", "reference"], ["--demo", "--telemetry-file", str(tmp_path / "t.json")],
                 ["--demo", "--ui", "web"], ["--qnet-archive", str(tmp_path / "missing.zip")]):
        with pytest.raises(SystemExit):
            desktop.main(argv)
    capsys.readouterr()

    archive = tmp_path / "qnet.zip"
    archive.write_bytes(b"not a model archive")
    captured = []
    monkeypatch.setattr(console_app, "run", lambda args, **kwargs: captured.append(args) or 0)
    assert desktop.main(["--demo", "--state-root", str(tmp_path / "state"), "--qnet-archive", str(archive)]) == 0
    args = captured[0]
    assert args.provider == "demo" and args.qnet_archive == archive and args.qkd_archive is None
    assert desktop.main(["--state-root", str(tmp_path / "state")]) == 0
    assert captured[1].provider == "service" and captured[1].qnet_archive is None

    # The archive goes to the engine's model registry, which verifies it itself and says why
    # a role stays on its baseline. The demonstration lives under <state-root>/demo/.
    service = console_app.build_demo_service(args)
    assert service.is_demo and service.control_journal_path == (tmp_path / "state" / "demo" / "brazil-institutions-v1" / "sandbox_journal.sqlite").resolve()
    models = service.network_live_state()["models"]
    routing = next(model for model in models if model["role"] == "routing")
    assert routing["state"] == "disabled" and service.network_live_state()["policy"]["ml_control_enabled"] is False
    assert service.network_engine.registry.archive_state("qnet").state == "rejected"
    plain = demo_service(ROOT, tmp_path / "plain")
    assert "not provided" in next(model for model in plain.network_live_state()["models"]
                                  if model["role"] == "routing")["reason"]
    assert console_app.build_parser().parse_args(["--provider", "demo"]).poll_ms is None
