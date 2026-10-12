"""Control invariants on the same contracts for instrument and simulator data."""
from copy import deepcopy
import multiprocessing
import time

import pytest

from quantum_autopilot.control_plane.mixed_contracts import ControllerObservation, digest
from quantum_autopilot.control_plane.mixed_controller import MixedController, route_policy


def configuration(count=3, session="test1", capacity=1):
    return {"schema": "orion.mixed-controller.configuration.v1", "freshness_s": 60,
            "nodes": [{"node_id": f"N{n:02}", "session_id": f"{session}-N{n:02}", "clock_domain": "aligned-test-clock",
                       "capacity": capacity, "capabilities": ["classical_control", "time_tagging", "correlation"]}
                      for n in range(1, count + 1)],
            "links": [{"link_id": f"L{n:02}-{n+1:02}", "a": f"N{n:02}", "b": f"N{n+1:02}",
                       "classical_channel_id": f"C{n:02}-{n+1:02}", "quantum_channel_id": f"Q{n:02}-{n+1:02}"}
                      for n in range(1, count)]}


def record(config, node_id, sequence, kind, data, *, origin="simulated", timestamp=None):
    node = next(n for n in config["nodes"] if n["node_id"] == node_id)
    observation = ControllerObservation(f"{node['session_id']}-{sequence}", node_id, node["session_id"], sequence,
                                        sequence * 100 + 100 if timestamp is None else timestamp,
                                        node["clock_domain"], kind, data)
    return {"observation": observation.to_dict(), "provenance": {"source_id": "fixture-source", "adapter_id": "fixture-adapter",
            "origin": origin, "record_sha256": digest(observation.to_dict())}}


def ready_records(config):
    records = []
    for node in config["nodes"]:
        node_id = node["node_id"]
        records.extend([record(config, node_id, 0, "session_started", {"configuration_id": "a" * 64, "verified": True}),
                        record(config, node_id, 1, "resources", {"available": node["capacity"]})])
        for index, link in enumerate(link for link in config["links"] if node_id in {link["a"], link["b"]}):
            records.append(record(config, node_id, 2 + index, "link_state", {"link_id": link["link_id"],
                "classical_state": "up", "quantum_state": "unknown"}))
    return records


def demand(request_id="demand-1", count=3, operation="classical_control"):
    return {"request_id": request_id, "source": "N01", "target": f"N{count:02}", "operation": operation, "ttl_s": 30}


@pytest.fixture
def controller(tmp_path):
    core = MixedController(tmp_path / "mixed.sqlite")
    yield core
    core.close()


def test_no_input_never_admits_nominal_resources(controller):
    controller.configure(configuration())
    result = controller.demand(demand())
    assert result["status"] == "blocked" and not result["route"]
    assert "no_observed_resources" in result["blocked_nodes"]["N01"]
    assert result["quantum_result"] is None and result["physical_action_performed"] is False


def test_eleven_nodes_share_one_observation_and_route_pipeline(controller):
    config = configuration(11)
    controller.configure(config)
    records = ready_records(config)
    controller.ingest({"records": records})
    receipt = controller.demand(demand(count=11))
    assert receipt["status"] == "reserved"
    assert receipt["route"] == [f"N{n:02}" for n in range(1, 12)]
    assert len(receipt["links"]) == 10
    assert controller.demand(demand(count=11)) == receipt
    assert controller.demand(demand("second", 11))["status"] == "blocked"
    assert controller.release({"request_id": "demand-1"})["status"] == "released"
    assert controller.demand(demand("third", 11))["status"] == "reserved"


def test_adapter_identity_and_origin_do_not_reach_policy(tmp_path):
    config = configuration()
    decisions = []
    for index, source_name in enumerate(("swabian-hardware-record", "sequence-native-callback")):
        from quantum_autopilot.security.equipment import EquipmentAuthority
        # Physical provenance is admitted only into a bound resource namespace;
        # both origins still supply identical states to the pure route policy.
        config = configuration()
        for node in config["nodes"]:
            node["resource_identity"] = {"namespace": "fixture", "serial": node["node_id"], "endpoint": "fixture://local"}
        core = MixedController(tmp_path / f"{index}.sqlite", resource_authority=EquipmentAuthority(tmp_path / "authority"))
        core.configure(config)
        records = ready_records(config)
        for item in records:
            item["provenance"].update(source_id=source_name, adapter_id=source_name, origin="physical" if index == 0 else "simulated")
        core.ingest({"records": records})
        state = core.status()
        decisions.append(route_policy(config, state["nodes"], source="N01", target="N03", operation="classical_control",
                                      now_ns=time.time_ns()))
        assert state["audit_origin_counts"] == {"physical" if index == 0 else "simulated": len(records)}
        assert state["certified_key_bits"] == 0
        core.close()
    assert decisions[0] == decisions[1]


def test_swabian_clicks_do_not_create_quantum_memory_or_entanglement(controller):
    config = configuration()
    controller.configure(config)
    controller.ingest({"records": ready_records(config)})
    status = controller.status()
    seq = status["nodes"]["N01"]["sequence"] + 1
    controller.ingest({"records": [record(config, "N01", seq, "timetags", {"events": [{"timestamp_ps": 10, "channel": 1}],
                                                                                   "lost_events": 0})]})
    assert controller.demand(demand(operation="entanglement"))["status"] == "blocked"
    assert controller.demand(demand("correlate", operation="correlation"))["status"] == "reserved"


@pytest.mark.parametrize("fault", ["gap", "clock", "loss", "channel", "resources"])
def test_uncertainty_and_failure_invalidate_active_reservations(controller, fault):
    config = configuration()
    controller.configure(config)
    controller.ingest({"records": ready_records(config)})
    assert controller.demand(demand())["status"] == "reserved"
    state = controller.status()["nodes"]["N02"]
    sequence, timestamp = state["sequence"] + 1, state["timestamp_ps"] + 100
    kind, data = "heartbeat", {}
    if fault == "gap":
        sequence += 1
    elif fault == "clock":
        timestamp -= 200
    elif fault == "loss":
        kind, data = "timetags", {"events": [], "lost_events": 2}
    elif fault == "channel":
        kind, data = "link_state", {"link_id": "L01-02", "classical_state": "down", "quantum_state": "unknown"}
    elif fault == "resources":
        kind, data = "resources", {"available": 0}
    controller.ingest({"records": [record(config, "N02", sequence, kind, data, timestamp=timestamp)]})
    status = controller.status()
    assert status["reservations"][0]["status"].startswith("invalidated_")
    assert controller.demand(demand("after-fault"))["status"] == "blocked"


def test_duplicate_blocks_are_idempotent_and_conflicts_roll_back(controller):
    config = configuration()
    controller.configure(config)
    records = ready_records(config)
    controller.ingest({"records": records})
    assert all(row["status"] == "duplicate" for row in controller.ingest({"records": records})["records"])
    changed = deepcopy(records[0])
    changed["observation"]["data"]["configuration_id"] = "b" * 64
    with pytest.raises(ValueError):
        controller.ingest({"records": [changed]})
    assert controller.status()["audit_origin_counts"] == {"simulated": len(records)}


def test_reconfiguration_does_not_reuse_sessions(controller):
    config = configuration()
    controller.configure(config)
    controller.ingest({"records": ready_records(config)})
    changed = deepcopy(config)
    changed["freshness_s"] = 30
    with pytest.raises(ValueError, match="new acquisition sessions"):
        controller.configure(changed)
    assert controller.configure(config)["status"] == "unchanged"
    controller.configure(configuration(session="recovery"))
    assert all(row["status"] == "no_input" for row in controller.status()["nodes"].values())


def test_expiration_and_stale_observations_never_hold_or_reactivate_slots(controller, monkeypatch):
    config = configuration()
    controller.configure(config)
    controller.ingest({"records": ready_records(config)})
    assert controller.demand(demand())["status"] == "reserved"
    now = time.time_ns()
    monkeypatch.setattr("quantum_autopilot.control_plane.mixed_controller.time.time_ns", lambda: now + 61 * 10**9)
    status = controller.status()
    assert status["reservations"][0]["status"] == "expired"
    assert status["nodes"]["N01"]["status"] == "stale_observations"
    assert controller.demand(demand("stale"))["status"] == "blocked"


def test_live_process_authority_is_exclusive(controller):
    with pytest.raises(RuntimeError, match="live process"):
        MixedController(controller.path)


def test_heartbeat_does_not_keep_expired_channel_or_resource_reservations_alive(controller, monkeypatch):
    config = configuration()
    controller.configure(config)
    controller.ingest({"records": ready_records(config)})
    request = demand()
    request["ttl_s"] = 300
    assert controller.demand(request)["status"] == "reserved"
    states = controller.status()["nodes"]
    later = time.time_ns() + 61 * 10**9
    monkeypatch.setattr("quantum_autopilot.control_plane.mixed_controller.time.time_ns", lambda: later)
    controller.ingest({"records": [record(config, n, s["sequence"] + 1, "heartbeat", {}, timestamp=s["timestamp_ps"] + 1)
                                    for n, s in states.items()]})
    assert controller.status()["reservations"][0]["status"] == "invalidated_stale_resources"
    assert controller.demand(demand("stale-links"))["status"] == "blocked"


def test_admission_rechecked_under_transaction_before_mutation(controller):
    config = configuration()
    controller.configure(config)
    def denied():
        raise PermissionError("revoked during wait")
    with pytest.raises(PermissionError):
        controller.ingest({"records": ready_records(config)}, admit=denied)
    assert controller.status()["audit_origin_counts"] == {}


def test_fault_reason_cannot_impersonate_ready_or_resurrect_session(controller):
    config = configuration()
    controller.configure(config)
    forged = ready_records(config)
    for item in forged:
        if item["observation"]["kind"] == "session_started":
            item["observation"].update(kind="fault", data={"reason": "ready"})
    controller.ingest({"records": forged})
    assert controller.demand(demand())["status"] == "blocked"
    for node_id, state in controller.status()["nodes"].items():
        assert state["status"] == "fault" and state["fault_reason"] == "ready"
        assert state["configuration_id"] is None
        controller.ingest({"records": [record(config, node_id, state["sequence"] + 1,
            "session_started", {"configuration_id": "a" * 64, "verified": True})]})
    assert controller.demand(demand("no-resurrection"))["status"] == "blocked"


def test_correlation_publication_bound_to_verified_configuration(controller):
    config = configuration()
    controller.configure(config)
    controller.ingest({"records": ready_records(config)})
    state = controller.status()["nodes"]["N01"]
    metrics = {"block_sha256": "b" * 64, "configuration_id": "a" * 64,
               "first_channel": 1, "second_channel": 2, "window_ps": 1000,
               "event_count": 5, "coincidence_pairs": 2}
    item = record(config, "N01", state["sequence"] + 1, "correlation", metrics)
    controller.ingest({"records": [item]})
    assert controller.status()["nodes"]["N01"]["analysis"] == metrics
    rejected = record(config, "N01", state["sequence"] + 2, "correlation",
                      {**metrics, "configuration_id": "c" * 64})
    with pytest.raises(ValueError, match="verified session"):
        controller.ingest({"records": [rejected]})
    assert controller.status()["nodes"]["N01"]["analysis"] == metrics


@pytest.mark.parametrize("field", ["classical_state", "quantum_state", "origin"])
def test_bad_json_types_rejected_before_persistence(controller, field):
    config = configuration()
    controller.configure(config)
    item = record(config, "N01", 0, "link_state", {"link_id": "L01-02", "classical_state": "up", "quantum_state": "unknown"})
    if field == "origin":
        item["provenance"][field] = []
    else:
        item["observation"]["data"][field] = []
    with pytest.raises(ValueError):
        controller.ingest({"records": [item]})
    assert controller.status()["audit_origin_counts"] == {}


def _abandon(path):
    core = MixedController(path)
    config = configuration()
    core.configure(config)
    core.ingest({"records": ready_records(config)})
    assert core.demand(demand())["status"] == "reserved"
    # Exit without cleanup to model process loss with a persisted reservation.


def test_restart_invalidates_reservations_and_requires_new_session(tmp_path):
    path = tmp_path / "restart.sqlite"
    child = multiprocessing.get_context("spawn").Process(target=_abandon, args=(path,))
    child.start()
    child.join(20)
    assert child.exitcode == 0
    core = MixedController(path)
    state = core.status()
    assert state["reservations"][0]["status"] == "invalidated_restart"
    assert all(row["status"] == "reverification_required" for row in state["nodes"].values())
    assert core.demand(demand("restarted"))["status"] == "blocked"
    core.close()


@pytest.mark.parametrize("field,value", [("freshness_s", True), ("freshness_s", 0), ("schema", "unknown")])
def test_invalid_configuration_does_not_replace_state(controller, field, value):
    config = configuration()
    controller.configure(config)
    broken = deepcopy(config)
    broken[field] = value
    with pytest.raises(ValueError):
        controller.configure(broken)
    assert controller.status()["revision"] == 1
