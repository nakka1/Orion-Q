"""Service API of the live network: lazy engine, commands, demo bootstrapping, tracked recalibration."""
import json

import pytest

from quantum_autopilot.control_plane import command_reconciliation
from quantum_autopilot.control_plane.console_commands import ConsoleCommandJournal, read_commands, validated_request
from quantum_autopilot.control_plane.topology import DEFAULT_TOPOLOGY_ID, _sha256
from quantum_autopilot.network_engine import IdleInputSource, LiveState, NetworkEngine, SimulatedInputSource
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService, demo_journal_path, demo_service
from tests.test_network_engine_routing import square

pytestmark = pytest.mark.unit


def service_on(tmp_path, topology=None):
    service = AssuranceDashboardService(control_journal_path=tmp_path / "journal.sqlite")
    if topology is not None:
        service.topology_store.initialize(topology)
    return service


def test_normal_mode_engine_is_lazy_and_idle_by_default(tmp_path):
    service = service_on(tmp_path, square())
    assert service._network_engine is None and service.is_demo is False
    engine = service.network_engine
    assert isinstance(engine, NetworkEngine) and service.network_engine is engine
    assert isinstance(engine.source, IdleInputSource) and engine.store is service.topology_store
    state = service.network_live_state()
    assert state["tick"] == 0 and state["source"]["kind"] == "none" and state["demands"] == []
    assert json.loads(json.dumps(state)) == state, "plain JSON"
    stepped = service.network_step()
    assert stepped["tick"] == 1 and service.network_live_state() == stepped
    assert LiveState.from_dict(stepped).to_dict() == stepped
    assert {link["status"] for link in stepped["links"]} == {"no_input"}


def test_service_round_trip_of_commands(tmp_path):
    service = service_on(tmp_path, square())
    defined = service.network_command("define_pair", {"demand_id": "P1", "a": "A", "b": "C"})
    assert defined["status"] == "persisted" and defined["affected_demands"][0]["route"] == ["A", "D", "C"]
    off = service.network_command("set_link_active", {"link_id": "D-A", "active": False})
    assert off["affected_demands"][0]["route"] == ["A", "B", "C"]
    assert service.network_live_state()["demands"][0]["status"] == "rerouted"
    rows = service.network_decisions(10)
    assert [row["record"]["kind"] for row in rows] == ["reroute", "reroute"]
    pair = service.network_command("request_pair", {"demand_id": "P1"})
    assert pair["status"] == "delivered" and pair["pair"]["route"] == ["A", "B", "C"]
    configured = service.configure_network_input("simulated", seed=3, scenario="quiet")
    assert configured["status"] == "configured" and configured["source"]["kind"] == "simulated"
    assert isinstance(service.network_engine.source, SimulatedInputSource)
    live = service.network_step()
    assert {link["link_id"]: link["status"] for link in live["links"]}["D-A"] == "disabled"
    assert any(link["qber"] is not None for link in live["links"])
    assert service.configure_network_input("none")["source"]["kind"] == "none"
    with pytest.raises(FileNotFoundError):
        service.configure_network_input("measured", path=str(tmp_path / "missing-acquisition.json"))
    assert service.network_engine.source.kind == "none"
    with pytest.raises(ValueError, match="unknown scenario"):
        service.configure_network_input("simulated", scenario="fireworks")
    archive = service.network_command("set_model_archive", {"kind": "qnet", "path": str(tmp_path / "missing.zip")})
    assert archive["status"] == "rejected" and archive["archive"]["state"] == "rejected"
    roles = {item["role"]: item for item in service.network_live_state()["models"]}
    assert roles["routing"]["state"] == "unavailable" and "rejected" in roles["routing"]["reason"]


def test_store_pass_throughs_notify_a_running_engine(tmp_path):
    service = service_on(tmp_path, square())
    service.network_command("define_pair", {"demand_id": "P1", "a": "A", "b": "C"})
    version = service.topology_store.get(DEFAULT_TOPOLOGY_ID).topology_version
    result = service.set_link_active({"link_id": "C-D", "active": False, "expected_version": version,
                                      "topology_id": DEFAULT_TOPOLOGY_ID})
    assert result["status"] == "persisted"
    demand = service.network_live_state()["demands"][0]          # no tick in between
    assert demand["route"] == ["A", "B", "C"] and demand["detail"]["trigger"] == "fibre C-D disabled"
    service.network_command("recalibrate_node", {"node_id": "B"})
    with pytest.raises(ValueError, match="set_node_isolation refused: node B is being recalibrated"):
        service.set_node_isolation({"node_id": "A", "isolated": True, "expected_version": version + 1,
                                    "topology_id": DEFAULT_TOPOLOGY_ID})
    assert service.topology_store.get(DEFAULT_TOPOLOGY_ID).topology_version == version + 1


def test_pass_throughs_work_without_an_engine(tmp_path):
    service = service_on(tmp_path, square())
    result = service.set_link_active({"link_id": "C-D", "active": False, "expected_version": 0,
                                      "topology_id": DEFAULT_TOPOLOGY_ID})
    assert result["status"] == "persisted" and service._network_engine is None


def test_recalibrate_node_satisfies_the_tracked_command_readback(tmp_path):
    """The console's lifecycle (REQUESTED -> RUNNING -> CONFIRMED) around ``service.recalibrate_node``."""
    service = service_on(tmp_path, square())
    service.network_step()
    path = service.topology_store.path
    request = validated_request("recalibrate_node", {"node_id": "D", "expected_version": service.network_live_state()['topology_version'],
                                                     "topology_id": DEFAULT_TOPOLOGY_ID})
    journal = ConsoleCommandJournal(path)
    command_id = journal.begin("recalibrate_node", request)
    journal.transition(command_id, "RUNNING")
    result = service.recalibrate_node(request)
    persisted = service.topology_store.get(request["topology_id"])
    assert result["status"] == "persisted" and result["physical_action_performed"] is False
    assert persisted.topology_version == request["expected_version"] + 1
    assert persisted.to_dict() == result["topology"]
    # Read-only reconciliation (unresolved commands only) compares the hash of the journalled request.
    stored = read_commands(path)[0]
    comparison = command_reconciliation.reconcile_command(path, command_id, stored["record_sha256"])["record"][
        "comparison"]
    assert comparison["target_status"] == "matches_current_state"
    assert comparison["version_relation"] == "at_expected_plus_one"
    record = journal.transition(command_id, "CONFIRMED", {"topology_version": persisted.topology_version,
                                                         "topology_sha256": _sha256(persisted.to_dict())})
    assert [event["state"] for event in record["events"]] == ["REQUESTED", "RUNNING", "CONFIRMED"]
    assert result["calibration"]["status"] == "complete" and result["calibration"]["report_sha256"]
    assert service.network_live_state()["tick"] == 6, "the procedure took five network seconds"
    with pytest.raises(ValueError, match="version conflict"):
        service.recalibrate_node(request)


def test_demo_service_bootstraps_its_own_state_and_runs_the_same_engine(tmp_path):
    normal = service_on(tmp_path / "normal" if (tmp_path / "normal").mkdir() is None else tmp_path, square())
    demo = demo_service(state_root=tmp_path / "state", seed=5)
    assert demo.is_demo and demo.control_journal_path == demo_journal_path(tmp_path / "state")
    assert demo.control_journal_path == (tmp_path / "state" / "demo" / "sandbox_journal.sqlite").resolve()
    assert demo.control_journal_path != normal.control_journal_path
    assert type(demo) is type(normal) and type(demo.network_engine) is type(normal.network_engine)
    assert type(demo.network_engine).step is type(normal.network_engine).step
    assert isinstance(demo.network_engine.source, SimulatedInputSource)
    assert isinstance(normal.network_engine.source, IdleInputSource)
    state = demo.network_live_state()
    assert state["source"] == {**state["source"], "kind": "simulated", "seed": 5, "scenario": "demo"}
    assert len(state["nodes"]) == 10 and len(state["links"]) == 11
    assert [item["demand_id"] for item in state["demands"]] == ["P1", "P2", "P3", "P4"]
    assert [item["node_id"] for item in state["nodes"] if not item["located"]] == ["N-12", "N-13"]
    live = demo.network_step()
    assert live["tick"] == 1 and all(link["qber"] is not None for link in live["links"])
    assert all(item["status"] == "routed" and item["in_contract"] is False for item in live["demands"])
    core = next(item for item in live["nodes"] if item["node_id"] == "CORE")
    assert core["kind"] == "core" and core["temperature_c"] is not None and len(core["routes_through"]) == 4
    injected = demo.network_command("inject_input_event", {"kind": "loss_event", "link_id": "R07-CORE",
                                                           "strength": 0.5, "duration_s": 10.0})
    assert injected["status"] == "accepted"
    assert any(event["kind"] == "input_event_started" for event in demo.network_step()["events"])
    # The demonstration never touches the operator's journal, and reopening it keeps its state.
    assert normal.topology_store.get(DEFAULT_TOPOLOGY_ID).links != demo.topology_store.get(DEFAULT_TOPOLOGY_ID).links
    demo.network_command("remove_pair", {"demand_id": "P4"})
    again = demo_service(state_root=tmp_path / "state", seed=5)
    assert [item["demand_id"] for item in again.network_live_state()["demands"]] == ["P1", "P2", "P3"]
    with pytest.raises(ValueError, match="derived from state_root"):
        demo_service(state_root=tmp_path / "state", control_journal_path=tmp_path / "x.sqlite")
