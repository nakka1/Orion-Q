"""Full node recalibration: phases, effect on routing, persisted report, version conflicts."""
import threading

import pytest

from quantum_autopilot.control_plane.inventory import RecalibrateNode, SetLinkActive
from quantum_autopilot.control_plane.topology import _sha256
from quantum_autopilot.network_engine import IdleInputSource, SimulatedInputSource, commission
from quantum_autopilot.network_engine.state import CALIBRATION_PHASES
from tests.test_network_engine_engine import demand, make, node
from tests.test_network_engine_routing import square

pytestmark = pytest.mark.unit
TOPOLOGY = "sandbox-line-3"


def test_recalibration_runs_phase_by_phase_and_persists_its_report(tmp_path):
    engine, store, _ = make(tmp_path, square(), source=SimulatedInputSource(4, ()))
    for _ in range(8):
        engine.step()
    assert demand(engine.live_state()).route == ("A", "D", "C")
    version = store.get(TOPOLOGY).topology_version
    request = {"node_id": "D", "expected_version": version, "topology_id": TOPOLOGY}
    started = engine.command("recalibrate_node", request)
    assert started["status"] == "started" and started["physical_action_performed"] is False
    assert started["calibration"]["status"] == "running" and started["calibration"]["phase"] == "queued"
    assert set(started["calibration"]["links"]) == {"C-D", "D-A"}
    before = started["calibration"]["links"]["D-A"]
    assert before["qber_before"] is not None and before["sync_residual_ps_before"] is not None
    assert before["measured_before_at_tick"] == 8 and before["qber_after"] is None
    # The node left the effective graph in the command itself: the demand through it moved at once.
    assert started["affected_demands"][0]["route"] == ["A", "B", "C"]
    assert started["affected_demands"][0]["detail"]["trigger"] == "node D calibrating"
    assert [item["kind"] for item in started["decisions"]] == ["calibration", "reroute"]
    assert node(engine.live_state(), "D").status == "calibrating"

    phases = []
    for _ in range(5):
        state = engine.step()
        live = node(state, "D")
        phases.append(live.calibration.phase)
        if live.calibration.status == "running":
            assert live.status == "calibrating" and live.routes_through == ()
            statuses = {link.link_id: link for link in state.links}
            assert statuses["C-D"].status == statuses["D-A"].status == "calibrating"
            assert statuses["D-A"].qber is None and statuses["D-A"].wave == (), "nothing is published meanwhile"
            assert statuses["A-B"].status in ("up", "degraded")
            assert demand(state).route == ("A", "B", "C")
            assert 0.0 < live.calibration.progress < 1.0
            assert store.get(TOPOLOGY).topology_version == version, "nothing is recorded before the end"
    assert phases == ["polarisation", "sync", "commissioning", "memory", "complete"]
    assert phases[:4] == list(CALIBRATION_PHASES[1:5])

    final = node(state, "D")
    assert final.status == "up" and final.calibration.status == "complete" and final.calibration_count == 1
    assert final.calibration.duration_s == 5.0 and final.calibration.memory_reset is True
    assert demand(state).route == ("A", "D", "C"), "the node is back and the demand returns to it"
    assert "node D recalibration ended" in demand(state).detail["trigger"]
    topology = store.get(TOPOLOGY)
    assert topology.topology_version == version + 1 == state.topology_version
    control = topology.node("D").telemetry["console_control"]
    report = control["calibration"]
    assert control["calibration_count"] == 1 and control["last_action"] == "recalibrate"
    assert control["last_command_sha256"] == _sha256(request), "the journalled request is what the store hashed"
    assert report["report_sha256"] == final.calibration.report_sha256
    assert report["calibration_id"] == started["calibration"]["calibration_id"]
    assert report["memory_reset"] is True and report["observables"] == "available"
    assert report["physical_action_performed"] is False and report["duration_s"] == 5.0
    for link_id, distance in (("C-D", 5.5), ("D-A", 4.0)):
        entry = report["links"][link_id]
        assert entry["status"] == "ok" and entry["settling_periods"] == 5
        assert 0.0 < entry["qber_after"] < 0.11 and entry["key_rate_hz_after"] > 0
        assert entry["sync_residual_ps_after"] is not None and entry["polarisation_residual_mrad_after"] > 0
        expected = commission(distance)
        assert entry["signal_intensity"] == pytest.approx(expected.signal_intensity)
        assert entry["decoy_intensity"] == pytest.approx(expected.decoy_intensity)
        assert entry["commissioned_rate_hz"] == pytest.approx(expected.calibration_rate_hz)
        assert entry["qber_before"] is not None and entry["measured_before_at_tick"] == 8
    assert final.calibration.links == report["links"]
    kinds = [event.kind for event in state.events]
    assert "calibration_started" in kinds and "calibration_completed" in kinds
    outcomes = [row["record"]["outcome"] for row in reversed(engine.recent_decisions(200))
                if row["record"]["kind"] == "calibration"]
    assert outcomes == ["queued", "polarisation", "sync", "commissioning", "memory", "complete"]
    # The fibres are published again from the next tick, with a new session.
    state = engine.step()
    statuses = {link.link_id: link for link in state.links}
    assert statuses["D-A"].status in ("up", "degraded") and statuses["D-A"].provenance["period"] == 5
    assert statuses["D-A"].detector.label is None, "the detector takes a new baseline after a recalibration"


def test_topology_changes_are_held_while_a_node_is_calibrating(tmp_path):
    engine, store, _ = make(tmp_path, square())
    engine.step()
    engine.command("recalibrate_node", {"node_id": "B"})
    for verb, payload in (("set_link_active", {"link_id": "A-C", "active": False}),
                          ("set_node_isolation", {"node_id": "A", "isolated": True}),
                          ("set_node_location", {"node_id": "A", "latitude": -12.9, "longitude": -38.4, "place": ""}),
                          ("add_network_node", {"node_id": "E", "role": "endpoint", "address": "10.0.0.5",
                                                "port": 1})):
        with pytest.raises(ValueError, match=f"{verb} refused: node B is being recalibrated"):
            engine.command(verb, payload)
    with pytest.raises(ValueError, match="one recalibration runs at a time"):
        engine.command("recalibrate_node", {"node_id": "D"})
    with pytest.raises(ValueError, match="cannot be changed while a node is being recalibrated"):
        engine.command("configure_network_input", {"kind": "none"})
    assert store.get(TOPOLOGY).topology_version == 0
    for _ in range(5):
        engine.step()
    assert node(engine.live_state(), "B").calibration.status == "complete"
    assert engine.command("set_link_active", {"link_id": "A-C", "active": False})["status"] == "persisted"


def test_a_topology_change_behind_the_engine_fails_the_record_honestly(tmp_path):
    engine, store, _ = make(tmp_path, square(), source=SimulatedInputSource(4, ()))
    for _ in range(3):
        engine.step()
    engine.command("recalibrate_node", {"node_id": "D"})
    engine.step()
    store.set_link_active(SetLinkActive("A-C", False, 0))        # the store used directly: not held
    state = None
    for _ in range(4):
        state = engine.step()
    live = node(state, "D")
    assert live.calibration.status == "failed" and live.calibration.report_sha256 is None
    assert live.status == "up" and live.calibration_count == 0
    assert "console_control" not in store.get(TOPOLOGY).node("D").telemetry, "nothing was persisted"
    assert store.get(TOPOLOGY).topology_version == 1
    failed = next(event for event in state.events if event.kind == "calibration_failed")
    assert "version conflict" in failed.message and "nothing was persisted" in failed.message
    assert failed.severity == "warning"
    row = next(item for item in engine.recent_decisions(100) if item["record"]["kind"] == "calibration")
    assert row["record"]["outcome"] == "failed"
    assert demand(state).route == ("A", "D", "C"), "the node is back in the graph either way"
    # A new request against the current version succeeds.
    result = engine.run_calibration({"node_id": "D", "expected_version": 1, "topology_id": TOPOLOGY})
    assert result["status"] == "persisted" and result["topology_version"] == 2
    assert result["calibration"]["status"] == "complete"
    assert result["topology"] == store.get(TOPOLOGY).to_dict()


def test_a_stale_request_is_refused_before_anything_starts(tmp_path):
    engine, store, _ = make(tmp_path, square())
    engine.command("set_link_active", {"link_id": "A-C", "active": False})
    with pytest.raises(ValueError, match="topology version conflict: expected 0, found 1"):
        engine.command("recalibrate_node", {"node_id": "D", "expected_version": 0, "topology_id": TOPOLOGY})
    with pytest.raises(ValueError, match="outside topology"):
        engine.command("recalibrate_node", {"node_id": "Z"})
    with pytest.raises(ValueError, match="invalid node-recalibration fields"):
        engine.command("recalibrate_node", {"node_id": "D", "report": {}})
    assert not engine.calibration.running and node(engine.live_state(), "D").calibration is None


def test_idle_source_recalibration_reports_measurements_unavailable(tmp_path):
    engine, store, _ = make(tmp_path, square(), source=IdleInputSource())
    engine.step()
    result = engine.run_calibration({"node_id": "D"})
    assert result["status"] == "persisted"
    report = store.get(TOPOLOGY).node("D").telemetry["console_control"]["calibration"]
    assert report["observables"] == "unavailable" and report["memory_reset"] is True
    for entry in report["links"].values():
        assert entry["status"] == "no_observables"
        for name in ("qber_before", "qber_after", "sync_residual_ps_before", "sync_residual_ps_after",
                     "key_rate_hz_after", "signal_intensity", "decoy_intensity", "polarisation_residual_mrad_after"):
            assert entry[name] is None, name
        assert entry["commissioned_rate_hz"] > 0, "the commissioning target of the length is a calculation"
    assert node(engine.live_state(), "D").calibration_count == 1


def test_a_node_without_fibres_is_recalibrated_memory_only(tmp_path):
    engine, store, _ = make(tmp_path)                           # demo preset: N-12 has no fibre
    routes = {item.demand_id: item.route for item in engine.live_state().demands}
    result = engine.run_calibration({"node_id": "N-12"})
    report = store.get(TOPOLOGY).node("N-12").telemetry["console_control"]["calibration"]
    assert result["status"] == "persisted" and report["links"] == {} and report["memory_reset"] is True
    assert {item.demand_id: item.route for item in engine.live_state().demands} == routes
    assert not [item for item in engine.recent_decisions(100) if item["record"]["kind"] == "reroute"
                and "N-12" in item["record"]["summary"]]


def test_blocking_recalibration_shows_its_phases_to_another_thread(tmp_path):
    engine, store, _ = make(tmp_path, square())
    engine.step()
    seen, stop = set(), threading.Event()

    def reader():
        while not stop.is_set():
            calibration = node(engine.live_state(), "B").calibration
            if calibration is not None:
                seen.add((calibration.status, calibration.phase))

    thread = threading.Thread(target=reader)
    thread.start()
    result = engine.run_calibration({"node_id": "B", "expected_version": 0, "topology_id": TOPOLOGY})
    stop.set()
    thread.join()
    assert result["status"] == "persisted" and node(engine.live_state(), "B").calibration.status == "complete"
    assert {phase for status, phase in seen if status == "running"} >= {"polarisation", "memory"}
    request = RecalibrateNode("B", 0)
    assert store.get(TOPOLOGY).node("B").telemetry["console_control"]["last_command_sha256"] == _sha256(
        {"node_id": request.node_id, "expected_version": 0, "topology_id": TOPOLOGY})


def test_a_restarted_engine_shows_the_persisted_calibration(tmp_path):
    from quantum_autopilot.network_engine import ModelRegistry, NetworkEngine
    engine, store, path = make(tmp_path, square())
    engine.run_calibration({"node_id": "B"})
    again = NetworkEngine(store, path, registry=ModelRegistry(environ={}), seed=7)
    live = node(again.live_state(), "B")
    assert live.calibration.status == "complete" and live.calibration_count == 1
    assert live.calibration.report_sha256 == node(engine.live_state(), "B").calibration.report_sha256


def test_blocking_recalibration_can_keep_the_network_cadence(tmp_path):
    """`tick_interval_s` paces the procedure's own ticks, so network time does not run ahead."""
    import time

    engine, _, _ = make(tmp_path, square(), source=SimulatedInputSource(4, ()))
    for _ in range(3):
        engine.step()
    tick = engine.live_state().tick
    started = time.monotonic()
    result = engine.run_calibration({"node_id": "D"}, tick_interval_s=0.05)
    elapsed = time.monotonic() - started
    ticks = engine.live_state().tick - tick
    assert result["status"] == "persisted" and ticks >= 5
    assert elapsed >= 0.05 * ticks, "every tick of the procedure waits one interval"
    for bad in (-0.1, 11, True, "1"):
        with pytest.raises(ValueError, match="tick_interval_s"):
            engine.run_calibration({"node_id": "D"}, tick_interval_s=bad)
    assert engine.calibration.hold_reason() is None, "a refused interval starts nothing"
