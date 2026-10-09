"""Adversarial tests for the v5.26 task-constrained policy."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from quantum_autopilot.control_plane import (
    AbstentionCalibrationConfig,
    ControlAction,
    ControlTask,
    ControllerRuntime,
    FrozenTaskPolicySpec,
    NetworkStateSnapshot,
    TaskConstrainedRepeaterPolicy,
    audit_task_policy_sensitivity,
    calibrate_link_policy_spec,
    evaluate_link_policy_predictions,
)
from quantum_autopilot.core import EvidenceKind, Provenance


def spec(**overrides):
    values = {
        "controller_name": "edge_lstm_dual_head",
        "calibration_id": "calibration-seed-601",
        "calibration_sha256": "a" * 64,
        "calibration_samples": 20,
        "fidelity_threshold": 0.65,
        "purification_input_threshold": 0.625,
        "probability_margin": 0.10,
        "max_prediction_uncertainty": 0.50,
        "max_safety_abstention_rate_pct": 20.0,
        "max_policy_wait_rate_pct": 35.0,
        "useful_pair_reward": 1.0,
        "false_purification_penalty": 0.25,
        "missed_useful_pair_penalty": 0.50,
        "wait_penalty": 0.01,
        "purification_attempt_penalty": 0.01,
    }
    values.update(overrides)
    return FrozenTaskPolicySpec(**values)


def snapshot(predicted=0.9, uncertainty=0.05, pairs=(0.8, 0.8), **overrides):
    values = {
        "request_id": "task-policy-test",
        "node_id": "repeater-a",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "state_version": 1,
        "predicted_fidelity": predicted,
        "prediction_uncertainty": uncertainty,
        "telemetry_age_s": 0.0,
        "memory_state": "STORING" if pairs else "IDLE",
        "available_pair_fidelities": pairs,
        "memory_ages_s": tuple(0.0 for _ in pairs),
        "integrity_verified": True,
        "operator_interlock": True,
        "provenance": Provenance(
            evidence_kind=EvidenceKind.SIMULATED,
            source="task-policy-test",
            metadata={"evidence_lane": "bell_fidelity_control"},
        ),
    }
    values.update(overrides)
    return NetworkStateSnapshot(**values)


@pytest.mark.unit
def test_task_action_sets_make_purify_and_swap_mutually_exclusive():
    link = TaskConstrainedRepeaterPolicy(ControlTask.LINK_PURIFICATION, spec())
    path = TaskConstrainedRepeaterPolicy(ControlTask.PATH_EXTENSION, spec())

    assert [item.value for item in link.allowed_actions] == ["HALT", "WAIT", "PURIFY"]
    assert [item.value for item in path.allowed_actions] == ["HALT", "WAIT", "SWAP"]


@pytest.mark.unit
def test_link_policy_responds_to_prediction_without_reading_pair_values():
    policy = TaskConstrainedRepeaterPolicy("link_purification", spec())

    low, low_details = policy.decide(snapshot(predicted=0.3, pairs=(0.99, 0.99)))
    uncertain, _ = policy.decide(snapshot(predicted=0.625, uncertainty=0.2))
    high, high_details = policy.decide(snapshot(predicted=0.9, pairs=(0.51, 0.52)))

    assert (low, uncertain, high) == (
        ControlAction.HALT,
        ControlAction.WAIT,
        ControlAction.PURIFY,
    )
    assert low_details["pair_values_used_for_action"] is False
    assert high_details["prediction_semantics"] == (
        "future_fidelity_of_each_raw_same_link_pair"
    )


@pytest.mark.unit
def test_path_policy_uses_two_link_pairs_and_ignores_candidate_prediction():
    policy = TaskConstrainedRepeaterPolicy("path_extension", spec())

    bad, bad_details = policy.decide(snapshot(predicted=0.99, pairs=(0.5, 0.5)))
    good_low_prediction, details = policy.decide(
        snapshot(predicted=0.01, uncertainty=0.99, pairs=(0.9, 0.9))
    )
    good_high_prediction, _ = policy.decide(
        snapshot(predicted=0.99, uncertainty=0.001, pairs=(0.9, 0.9))
    )

    assert bad is ControlAction.HALT
    assert bad_details["expected_swapped_fidelity"] < 0.65
    assert good_low_prediction is good_high_prediction is ControlAction.SWAP
    assert details["prediction_used"] is False
    assert details["input_link_pair_fidelities"] == [0.9, 0.9]


@pytest.mark.unit
def test_task_policy_fails_closed_on_ambiguous_pair_selection_and_waits_for_second():
    policy = TaskConstrainedRepeaterPolicy("link_purification", spec())

    one, one_details = policy.decide(snapshot(pairs=(0.8,)))
    many, many_details = policy.decide(snapshot(pairs=(0.8, 0.8, 0.8)))

    assert one is ControlAction.WAIT
    assert one_details["reason_code"] == "awaiting_second_task_input_pair"
    assert many is ControlAction.HALT
    assert many_details["reason_code"] == "ambiguous_pair_selection"


@pytest.mark.unit
def test_calibration_is_deterministic_budgeted_and_hash_bound():
    calibration = {
        "mean": [0.2, 0.3, 0.4, 0.55, 0.62, 0.66, 0.7, 0.75, 0.8, 0.9],
        "uncertainty": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
        "actual_fidelity": [0.2, 0.3, 0.4, 0.55, 0.62, 0.66, 0.7, 0.75, 0.8, 0.9],
        "availability": [1] * 10,
        "out_of_distribution": [0] * 10,
    }
    first = calibrate_link_policy_spec(
        "point", "calibration-only", **calibration
    )
    second = calibrate_link_policy_spec(
        "point", "calibration-only", **calibration
    )

    assert first == second
    assert first.sha256 == second.sha256
    assert first.purification_input_threshold == pytest.approx(0.625)
    assert first.calibration_diagnostics["selection_scope"] == "calibration_only"
    assert (
        first.calibration_diagnostics[
            "observed_high_uncertainty_abstention_rate_pct"
        ]
        <= first.max_safety_abstention_rate_pct
    )
    assert (
        first.calibration_diagnostics["selected"]["policy_wait_rate_pct"]
        <= first.max_policy_wait_rate_pct
    )
    with pytest.raises(FrozenInstanceError):
        first.probability_margin = 0.0
    with pytest.raises(TypeError):
        first.calibration_diagnostics["selected"]["probability_margin"] = 0.5


@pytest.mark.unit
def test_evaluation_separates_ood_uncertainty_policy_wait_and_low_fidelity():
    rows, summary = evaluate_link_policy_predictions(
        "point",
        spec(),
        mean=[0.9, 0.9, 0.625, 0.3, 0.9],
        uncertainty=[0.1, 0.6, 0.1, 0.1, 0.1],
        actual_fidelity=[0.8, 0.8, 0.8, 0.3, 0.8],
        availability=[1, 1, 1, 1, 1],
        out_of_distribution=[1, 0, 0, 0, 0],
    )

    assert [row["outcome_category"] for row in rows] == [
        "safety_ood_abstention",
        "safety_high_uncertainty_abstention",
        "policy_probability_abstention",
        "ordinary_low_fidelity_halt",
        "purification_attempt",
    ]
    assert summary["purification_attempts"] == 1
    assert summary["safety_ood_abstention_count"] == 1
    assert summary["safety_high_uncertainty_abstention_count"] == 1
    assert summary["policy_wait_count"] == 1
    assert summary["ordinary_low_fidelity_halt_count"] == 1
    assert all(row["physical_action_performed"] is False for row in rows)


@pytest.mark.unit
def test_future_fidelity_oracle_is_an_operational_upper_bound_after_bbpssw_inversion():
    _rows, summary = evaluate_link_policy_predictions(
        "oracle",
        spec(probability_margin=0.0, max_prediction_uncertainty=0.01),
        mean=[0.60, 0.625, 0.70],
        uncertainty=[0.0, 0.0, 0.0],
        actual_fidelity=[0.60, 0.625, 0.70],
        availability=[1, 1, 1],
        out_of_distribution=[0, 0, 0],
    )

    assert summary["purification_attempts"] == 2
    assert summary["false_purification_count"] == 0
    assert summary["missed_useful_pair_count"] == 0


@pytest.mark.unit
def test_sensitivity_audit_proves_separation_and_non_saturation():
    audit = audit_task_policy_sensitivity(spec())

    assert audit["purify_and_swap_mutually_exclusive"] is True
    assert audit["link_prediction_sensitive"] is True
    assert audit["path_pair_sensitive"] is True
    assert audit["path_prediction_invariant"] is True
    assert set(audit["link_probe_actions"]) == {"HALT", "WAIT", "PURIFY"}
    assert set(audit["path_probe_actions"]) == {"HALT", "SWAP"}


@pytest.mark.unit
def test_runtime_records_task_and_frozen_policy_hash(tmp_path):
    frozen = spec(probability_margin=0.0)
    runtime = ControllerRuntime(
        tmp_path / "task-policy.sqlite",
        policy=TaskConstrainedRepeaterPolicy("link_purification", frozen),
    )

    result = runtime.cycle(snapshot(), mode="shadow")
    details = result["record"]["intent"]["policy_details"]

    assert result["record"]["intent"]["action"] == "PURIFY"
    assert details["control_task"] == "link_purification"
    assert details["policy_spec_sha256"] == frozen.sha256
    assert result["record"]["execution"]["physical_action_performed"] is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    [
        {"probability_margin_grid": ()},
        {"probability_margin_grid": (0.1, 0.1)},
        {"max_policy_wait_rate_pct": 101.0},
        {"useful_pair_reward": -1.0},
    ],
)
def test_calibration_config_rejects_invalid_search_spaces(kwargs):
    with pytest.raises(ValueError):
        AbstentionCalibrationConfig(**kwargs)


@pytest.mark.unit
def test_calibration_and_evaluation_reject_misaligned_or_unusable_vectors():
    with pytest.raises(ValueError, match="align"):
        calibrate_link_policy_spec(
            "point",
            "bad",
            mean=[0.7, 0.8],
            uncertainty=[0.1],
            actual_fidelity=[0.7],
            availability=[1],
            out_of_distribution=[0],
        )
    with pytest.raises(ValueError, match="available non-OOD"):
        calibrate_link_policy_spec(
            "point",
            "bad",
            mean=[0.7],
            uncertainty=[0.1],
            actual_fidelity=[0.7],
            availability=[0],
            out_of_distribution=[0],
        )
    with pytest.raises(ValueError, match="non-negative"):
        evaluate_link_policy_predictions(
            "point",
            spec(),
            mean=[0.7],
            uncertainty=[-0.1],
            actual_fidelity=[0.7],
            availability=[1],
            out_of_distribution=[0],
        )
