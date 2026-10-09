"""Adversarial tests for the non-physical quantum control-plane sandbox."""

from datetime import datetime, timezone

import pytest

from quantum_autopilot.control_plane import (
    ControlAction,
    ControlExecutionResult,
    ControlIntent,
    ControlJournal,
    ControlMode,
    ControllerRuntime,
    DryRunExecutor,
    NetworkStateSnapshot,
    RepeaterSandboxPolicy,
    SafetyPolicyConfig,
    SafetySupervisor,
    SimulatedQuantumExecutor,
)
from quantum_autopilot.core import EvidenceKind, Provenance


def snapshot(**overrides):
    values = {
        "request_id": "request-001",
        "node_id": "repeater-a",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "state_version": 1,
        "predicted_fidelity": 0.90,
        "prediction_uncertainty": 0.01,
        "telemetry_age_s": 0.1,
        "memory_state": "STORING",
        "available_pair_fidelities": (0.90, 0.90),
        "memory_ages_s": (0.0, 0.0),
        "telemetry": {"osnr_db": 24.0},
        "out_of_distribution": False,
        "integrity_verified": True,
        "operator_interlock": True,
        "provenance": Provenance(
            evidence_kind=EvidenceKind.SIMULATED,
            source="control-plane-test",
            metadata={
                "evidence_lane": "bell_fidelity_control",
                "target_semantics": "simulated_future_bell_fidelity",
            },
        ),
    }
    values.update(overrides)
    return NetworkStateSnapshot(**values)


def intent(state, action):
    return ControlIntent(
        intent_id="intent-001",
        request_id=state.request_id,
        node_id=state.node_id,
        action=action,
        expected_state_version=state.state_version,
        issued_at_utc=datetime.now(timezone.utc).isoformat(),
        policy_name="test-policy",
    )


@pytest.mark.unit
def test_control_modes_recognize_physical_only_for_explicit_refusal():
    assert {item.value for item in ControlMode} == {
        "assurance",
        "shadow",
        "simulation",
        "physical",
    }
    assert ControlMode("physical") is ControlMode.PHYSICAL


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    [
        {"predicted_fidelity": 1.1},
        {"prediction_uncertainty": -0.1},
        {"telemetry_age_s": float("nan")},
        {"state_version": -1},
        {"memory_state": "UNKNOWN"},
        {"memory_ages_s": (0.0,)},
        {"timestamp_utc": "2026-09-04T12:00:00"},
    ],
)
def test_network_state_contract_rejects_invalid_inputs(overrides):
    with pytest.raises(ValueError):
        snapshot(**overrides)


@pytest.mark.unit
def test_execution_result_cannot_claim_a_physical_action():
    with pytest.raises(ValueError, match="physical action"):
        ControlExecutionResult(
            status="IMPOSSIBLE",
            action="SWAP",
            performed_in_simulator=False,
            physical_action_performed=True,
        )


@pytest.mark.unit
def test_original_repeater_policy_is_reused_and_exposes_utilities():
    action, details = RepeaterSandboxPolicy().decide(snapshot())

    assert action is ControlAction.SWAP
    assert set(details["utilities"]) == {"HALT", "WAIT", "PURIFY", "SWAP"}
    assert details["physical_calibration"] is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    [
        ({"integrity_verified": False}, "state_integrity_unverified"),
        ({"operator_interlock": False}, "operator_interlock_open"),
        ({"out_of_distribution": True}, "input_out_of_distribution"),
        ({"telemetry_age_s": 6.0}, "stale_telemetry"),
        ({"prediction_uncertainty": 0.21}, "prediction_uncertainty_too_high"),
        (
            {
                "provenance": Provenance(
                    evidence_kind=EvidenceKind.MEASURED,
                    source="not-a-simulation",
                )
            },
            "simulation_mode_requires_simulated_state",
        ),
    ],
)
def test_safety_supervisor_fails_closed(overrides, reason_code):
    state = snapshot(**overrides)
    result = SafetySupervisor().assess(ControlMode.SIMULATION, intent(state, "SWAP"), state)

    assert result.authorized is False
    assert result.effective_action is ControlAction.HALT
    assert reason_code in {item.reason_code for item in result.violations}


@pytest.mark.unit
def test_safety_preconditions_block_pair_operations_without_two_pairs():
    state = snapshot(
        available_pair_fidelities=(0.9,),
        memory_ages_s=(0.0,),
    )
    result = SafetySupervisor().assess(ControlMode.SIMULATION, intent(state, "PURIFY"), state)

    assert result.effective_action is ControlAction.HALT
    assert "insufficient_entangled_pairs" in {
        item.reason_code for item in result.violations
    }


@pytest.mark.unit
def test_halt_remains_available_when_other_state_gates_fail():
    state = snapshot(integrity_verified=False, operator_interlock=False)
    result = SafetySupervisor().assess(ControlMode.SIMULATION, intent(state, "HALT"), state)

    assert result.authorized is True
    assert result.effective_action is ControlAction.HALT
    assert len(result.violations) == 2


@pytest.mark.unit
def test_safety_thresholds_are_explicit_and_validated():
    config = SafetyPolicyConfig(max_telemetry_age_s=0.5, max_prediction_uncertainty=0.05)
    state = snapshot(telemetry_age_s=0.6, prediction_uncertainty=0.06)
    result = SafetySupervisor(config).assess(
        ControlMode.SIMULATION,
        intent(state, "SWAP"),
        state,
    )
    assert len(result.violations) == 2
    with pytest.raises(ValueError):
        SafetyPolicyConfig(max_telemetry_age_s=-1)


@pytest.mark.unit
@pytest.mark.parametrize("mode", [ControlMode.SHADOW, ControlMode.ASSURANCE])
def test_dry_run_modes_never_perform_simulated_or_physical_actions(mode):
    state = snapshot()
    result = DryRunExecutor().execute(intent(state, "SWAP"), state, mode=mode)

    assert result.performed_in_simulator is False
    assert result.physical_action_performed is False
    assert result.details["would_execute"] == "SWAP"


@pytest.mark.unit
def test_simulated_executor_runs_real_werner_swap_without_physical_action():
    state = snapshot()
    result = SimulatedQuantumExecutor().execute(
        intent(state, "SWAP"),
        state,
        mode=ControlMode.SIMULATION,
    )

    assert result.status == "SIMULATED"
    assert result.performed_in_simulator is True
    assert result.physical_action_performed is False
    assert result.details["success"] is True
    assert result.details["F_t"] == pytest.approx(0.8133333373)


@pytest.mark.unit
def test_simulated_executor_runs_deterministic_qiskit_backed_purification():
    state = snapshot()
    executor = SimulatedQuantumExecutor()

    first = executor.execute(
        intent(state, "PURIFY"),
        state,
        mode=ControlMode.SIMULATION,
    )
    second = executor.execute(
        intent(state, "PURIFY"),
        state,
        mode=ControlMode.SIMULATION,
    )

    assert first.details == second.details
    assert first.details["qpu_operations"] == 4
    expected_memory = "STORING" if first.details["sampled_success"] else "IDLE"
    assert first.details["resulting_memory_state"] == expected_memory
    assert first.physical_action_performed is False


@pytest.mark.unit
def test_wait_rejects_nonfinite_simulated_duration():
    state = snapshot(metadata={"wait_time_s": float("inf")})
    with pytest.raises(ValueError, match="finite and non-negative"):
        SimulatedQuantumExecutor().execute(
            intent(state, "WAIT"),
            state,
            mode=ControlMode.SIMULATION,
        )


@pytest.mark.unit
def test_runtime_persists_complete_cycle_and_is_idempotent(tmp_path):
    runtime = ControllerRuntime(tmp_path / "journal.sqlite")
    state = snapshot()

    first = runtime.cycle(state, mode="simulation")
    replay = runtime.cycle(state, mode="simulation")

    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert replay["record"] == first["record"]
    assert replay["record_sha256"] == first["record_sha256"]
    assert first["record"]["intent"]["action"] == "SWAP"
    assert first["record"]["execution"]["status"] == "SIMULATED"
    assert len(first["record_sha256"]) == 64
    assert runtime.journal.list_recent()[0]["request_id"] == state.request_id


@pytest.mark.unit
def test_runtime_rejects_idempotency_key_reuse_with_different_payload(tmp_path):
    runtime = ControllerRuntime(tmp_path / "journal.sqlite")
    runtime.cycle(snapshot(), mode="simulation")

    with pytest.raises(ValueError, match="different mode or payload"):
        runtime.cycle(snapshot(predicted_fidelity=0.2), mode="simulation")


@pytest.mark.unit
def test_runtime_shadow_mode_records_policy_decision_without_simulation(tmp_path):
    runtime = ControllerRuntime(tmp_path / "journal.sqlite")
    result = runtime.cycle(snapshot(), mode="shadow")

    assert result["record"]["intent"]["action"] == "SWAP"
    assert result["record"]["execution"]["status"] == "SHADOW_RECORDED"
    assert result["record"]["execution"]["performed_in_simulator"] is False


@pytest.mark.unit
def test_runtime_turns_unsafe_policy_decision_into_audited_simulated_halt(tmp_path):
    runtime = ControllerRuntime(tmp_path / "journal.sqlite")
    result = runtime.cycle(snapshot(operator_interlock=False), mode="simulation")

    assert result["record"]["intent"]["action"] == "SWAP"
    assert result["record"]["safety"]["effective_action"] == "HALT"
    assert result["record"]["execution"]["action"] == "HALT"
    assert result["record"]["execution"]["physical_action_performed"] is False


@pytest.mark.unit
def test_journal_validates_list_limit(tmp_path):
    journal = ControlJournal(tmp_path / "journal.sqlite")
    with pytest.raises(ValueError, match="limit"):
        journal.list_recent(0)
