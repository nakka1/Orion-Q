"""Tests for persistent, non-physical topology scenarios."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from quantum_autopilot.control_plane import (
    ControlAction,
    ControllerRuntime,
    NetworkStateSnapshot,
    NodeRole,
    ScenarioDefinition,
    ScenarioStep,
    SimulatedLink,
    SimulatedNodeState,
    SimulatedTopology,
    SimulatedTopologyStore,
    TopologyScenarioRunner,
    default_simulated_topology,
    resilience_demo_scenario,
)
from quantum_autopilot.core import EvidenceKind, Provenance


def simulated_snapshot(node, request_id="topology-cycle"):
    now = datetime.now(timezone.utc).isoformat()
    return NetworkStateSnapshot(
        request_id=request_id,
        node_id=node.node_id,
        timestamp_utc=now,
        state_version=node.state_version,
        predicted_fidelity=0.90,
        prediction_uncertainty=0.01,
        telemetry_age_s=0.1,
        memory_state=node.memory_state,
        available_pair_fidelities=node.available_pair_fidelities,
        memory_ages_s=node.memory_ages_s,
        telemetry={"source": "topology-test"},
        integrity_verified=True,
        operator_interlock=True,
        provenance=Provenance(
            evidence_kind=EvidenceKind.SIMULATED,
            source="topology-test",
            timestamp=now,
            metadata={
                "evidence_lane": "bell_fidelity_control",
                "target_semantics": "simulated_future_bell_fidelity",
            },
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "values",
    [
        {"memory_state": "IDLE", "available_pair_fidelities": (0.9,), "memory_ages_s": (0.0,)},
        {"memory_state": "STORING"},
        {
            "memory_state": "STORING",
            "available_pair_fidelities": (0.9,),
            "memory_ages_s": (),
        },
        {
            "role": "endpoint",
            "memory_state": "STORING",
            "available_pair_fidelities": (0.9,),
            "memory_ages_s": (0.0,),
        },
        {
            "memory_state": "STORING",
            "available_pair_fidelities": (0.9, 0.9, 0.9),
            "memory_ages_s": (0.0, 0.0, 0.0),
        },
    ],
)
def test_simulated_node_contract_rejects_inconsistent_memory(values):
    defaults = {
        "node_id": "repeater-a",
        "role": "repeater",
        "state_version": 0,
        "memory_state": "IDLE",
    }
    with pytest.raises(ValueError):
        SimulatedNodeState(**{**defaults, **values})


@pytest.mark.unit
def test_topology_contract_rejects_dangling_links():
    node = SimulatedNodeState("alice", NodeRole.ENDPOINT, 0, "IDLE")
    link = SimulatedLink("bad-link", "alice", "missing", 10.0, 0.9)
    with pytest.raises(ValueError, match="endpoint must exist"):
        SimulatedTopology("invalid", 0, (node,), (link,))


@pytest.mark.unit
def test_describe_returns_unpersisted_blueprint_without_creating_state(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "topology.sqlite")
    runner = TopologyScenarioRunner(ControllerRuntime(tmp_path / "topology.sqlite"), store)

    description = runner.describe()

    assert description["persisted"] is False
    assert description["physical_topology"] is False
    assert store.get(description["topology"]["topology_id"]) is None


@pytest.mark.unit
def test_topology_initialization_persists_across_store_instances(tmp_path):
    path = tmp_path / "topology.sqlite"
    first_store = SimulatedTopologyStore(path)
    topology, created = first_store.initialize(default_simulated_topology())
    second_store = SimulatedTopologyStore(path)

    assert created is True
    assert second_store.get(topology.topology_id) == topology
    assert first_store.list_events(topology.topology_id)[0]["event_kind"] == "topology_initialized"


@pytest.mark.unit
def test_pair_injection_and_reset_increment_persisted_versions(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "topology.sqlite")
    initial, _ = store.initialize(default_simulated_topology())
    injected = store.inject_pairs(
        initial.topology_id,
        "repeater-a",
        (0.81, 0.82),
        (0.0, 0.2),
        telemetry={"phase": "injected"},
        scenario_id="scenario-1",
        step_id="inject",
    )
    reset = store.reset(initial.topology_id)

    assert injected.topology_version == initial.topology_version + 1
    assert injected.node("repeater-a").state_version == 1
    assert reset.topology_version == injected.topology_version + 1
    assert reset.node("repeater-a").state_version == 2
    assert reset.node("repeater-a").available_pair_fidelities == (0.90, 0.90)


@pytest.mark.unit
def test_resilience_scenario_persists_audited_fail_closed_timeline(tmp_path):
    path = tmp_path / "topology.sqlite"
    runner = TopologyScenarioRunner(
        ControllerRuntime(path),
        SimulatedTopologyStore(path),
    )

    result = runner.run(resilience_demo_scenario("resilience-001"))
    timeline = result["record"]["timeline"]

    assert [item["cycle"]["record"]["intent"]["action"] for item in timeline] == [
        "SWAP",
        "SWAP",
        "SWAP",
    ]
    assert [
        item["cycle"]["record"]["safety"]["effective_action"] for item in timeline
    ] == ["SWAP", "HALT", "SWAP"]
    assert timeline[1]["cycle"]["record"]["safety"]["violations"][0]["reason_code"] == (
        "prediction_uncertainty_too_high"
    )
    assert result["record"]["physical_action_performed"] is False
    assert result["record"]["final_topology"]["topology_version"] == 6
    assert result["record"]["final_topology"]["nodes"][1]["memory_state"] == "IDLE"
    assert len(result["record_sha256"]) == 64


@pytest.mark.unit
def test_scenario_idempotency_prevents_duplicate_topology_mutation(tmp_path):
    path = tmp_path / "topology.sqlite"
    runner = TopologyScenarioRunner(
        ControllerRuntime(path),
        SimulatedTopologyStore(path),
    )
    scenario = resilience_demo_scenario("resilience-idempotent")

    first = runner.run(scenario)
    replay = runner.run(scenario)

    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert replay["record"] == first["record"]
    assert runner.store.get(scenario.topology_id).topology_version == 6


@pytest.mark.unit
def test_scenario_id_reuse_with_changed_payload_is_rejected(tmp_path):
    path = tmp_path / "topology.sqlite"
    runner = TopologyScenarioRunner(
        ControllerRuntime(path),
        SimulatedTopologyStore(path),
    )
    original = resilience_demo_scenario("scenario-conflict")
    runner.run(original)
    changed_step = replace(original.steps[0], predicted_fidelity=0.89)
    changed = replace(original, steps=(changed_step, *original.steps[1:]))

    with pytest.raises(ValueError, match="different payload"):
        runner.run(changed)


@pytest.mark.unit
def test_scenario_record_and_topology_survive_new_runtime_instance(tmp_path):
    path = tmp_path / "topology.sqlite"
    scenario = resilience_demo_scenario("scenario-restart")
    first = TopologyScenarioRunner(ControllerRuntime(path), SimulatedTopologyStore(path))
    first_result = first.run(scenario)
    restarted = TopologyScenarioRunner(ControllerRuntime(path), SimulatedTopologyStore(path))

    replay = restarted.run(scenario)

    assert replay["idempotent_replay"] is True
    assert replay["record_sha256"] == first_result["record_sha256"]
    assert restarted.describe()["topology"]["topology_version"] == 6


@pytest.mark.unit
def test_custom_scenario_is_bounded_and_uses_simulated_provenance(tmp_path):
    path = tmp_path / "topology.sqlite"
    runner = TopologyScenarioRunner(ControllerRuntime(path), SimulatedTopologyStore(path))
    scenario = ScenarioDefinition(
        scenario_id="custom-001",
        topology_id="custom-line",
        reset_topology=True,
        steps=(
            ScenarioStep(
                "one-swap",
                "repeater-a",
                0.92,
                injected_pair_fidelities=(0.92, 0.91),
            ),
        ),
    )

    result = runner.run(scenario)
    cycle = result["record"]["timeline"][0]["cycle"]["record"]

    assert cycle["snapshot"]["provenance"]["evidence_kind"] == "simulated"
    assert cycle["execution"]["physical_action_performed"] is False
    with pytest.raises(ValueError, match="between 1 and 50"):
        ScenarioDefinition("empty", "custom-line", ())


@pytest.mark.unit
def test_invalid_scenario_target_is_rejected_before_reset_mutates_topology(tmp_path):
    path = tmp_path / "topology.sqlite"
    store = SimulatedTopologyStore(path)
    runner = TopologyScenarioRunner(ControllerRuntime(path), store)
    scenario = ScenarioDefinition(
        scenario_id="invalid-target",
        topology_id="protected-line",
        reset_topology=True,
        steps=(ScenarioStep("bad", "alice", 0.9),),
    )

    with pytest.raises(ValueError, match="repeater nodes only"):
        runner.run(scenario)

    assert store.get("protected-line") is None


@pytest.mark.unit
def test_shadow_cycle_does_not_mutate_persisted_topology(tmp_path):
    path = tmp_path / "topology.sqlite"
    store = SimulatedTopologyStore(path)
    topology, _ = store.initialize(default_simulated_topology())
    runtime = ControllerRuntime(path)
    cycle = runtime.cycle(simulated_snapshot(topology.node("repeater-a")), mode="shadow")

    after = store.apply_cycle(topology.topology_id, cycle)

    assert after == topology
    assert after.topology_version == 0


@pytest.mark.unit
def test_purification_transition_matches_simulator_result(tmp_path):
    class PurifyPolicy:
        name = "test-purify-policy"

        @staticmethod
        def decide(snapshot):
            return ControlAction.PURIFY, {"test": True}

    path = tmp_path / "topology.sqlite"
    store = SimulatedTopologyStore(path)
    topology, _ = store.initialize(default_simulated_topology())
    runtime = ControllerRuntime(path, policy=PurifyPolicy())
    cycle = runtime.cycle(
        simulated_snapshot(topology.node("repeater-a"), "purify-transition"),
        mode="simulation",
    )

    after = store.apply_cycle(topology.topology_id, cycle)
    node = after.node("repeater-a")
    details = cycle["record"]["execution"]["details"]

    if details["sampled_success"]:
        assert node.memory_state == "STORING"
        assert node.available_pair_fidelities == pytest.approx((details["F_after"],))
    else:
        assert node.memory_state == "IDLE"
        assert node.available_pair_fidelities == ()
    assert node.state_version == topology.node("repeater-a").state_version + 1


@pytest.mark.unit
def test_topology_event_limit_is_validated(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "topology.sqlite")
    with pytest.raises(ValueError, match="limit"):
        store.list_events("sandbox-line-3", 0)
