"""Unit tests for the paired simulated-controller scorecard."""

import numpy as np
import pytest

from quantum_autopilot.control_plane import (
    ComparativeControlConfig,
    audit_repeater_policy_saturation,
    evaluate_controller_predictions,
    holm_adjust,
    paired_sign_flip_test,
)


@pytest.mark.unit
def test_scorecard_keeps_wait_safety_and_no_pair_states_distinct():
    rows, summary = evaluate_controller_predictions(
        "candidate",
        mean=[0.8, 0.5, 0.66, 0.9],
        uncertainty=[0.01, 0.01, 0.05, 0.01],
        actual_fidelity=[0.8, 0.8, 0.7, 0.9],
        availability=[1, 1, 1, 0],
        out_of_distribution=[0, 0, 0, 0],
        config=ComparativeControlConfig(max_wait_cycles=2),
    )

    assert [row["initial_action"] for row in rows] == [
        "PURIFY",
        "HALT",
        "WAIT",
        "NO_PAIR",
    ]
    assert rows[2]["effective_action"] == "PURIFY"
    assert rows[2]["wait_cycles"] == 2
    assert summary["pair_arrivals"] == 3
    assert summary["purification_attempts"] == 2
    assert summary["missed_good_pair_count"] == 1
    assert summary["physical_action_performed"] is False


@pytest.mark.unit
def test_high_uncertainty_forces_purification_candidate_to_halt():
    rows, summary = evaluate_controller_predictions(
        "unsafe",
        mean=[0.9],
        uncertainty=[0.21],
        actual_fidelity=[0.9],
        availability=[1],
        out_of_distribution=[0],
    )

    assert rows[0]["terminal_action_before_safety"] == "PURIFY"
    assert rows[0]["effective_action"] == "HALT"
    assert rows[0]["safety_forced_halt"] is True
    assert summary["safety_forced_halt_count"] == 1
    assert summary["purification_attempts"] == 0


@pytest.mark.unit
def test_reactive_rule_does_not_invent_forecast_waits():
    rows, summary = evaluate_controller_predictions(
        "reactive",
        mean=[0.7, 0.6],
        uncertainty=[0.0, 0.0],
        actual_fidelity=[0.8, 0.5],
        availability=[1, 1],
        out_of_distribution=[0, 0],
        controller_kind="reactive",
        inference_latency_s=0.0,
    )

    assert [row["initial_action"] for row in rows] == ["PURIFY", "HALT"]
    assert summary["initial_wait_count"] == 0
    assert summary["energy_inference_j_model_based"] == 0.0


@pytest.mark.unit
def test_bbpssw_expected_yield_is_not_same_as_input_classification_accuracy():
    _rows, summary = evaluate_controller_predictions(
        "always-good",
        mean=[0.9, 0.9],
        uncertainty=[0.0, 0.0],
        actual_fidelity=[0.9, 0.9],
        availability=[1, 1],
        out_of_distribution=[0, 0],
        controller_kind="oracle",
    )

    assert 0.0 < summary["expected_useful_pairs"] < 2.0
    assert 0.0 < summary["expected_yield_per_attempt_pct"] < 100.0
    assert summary["false_purification_count"] == 0


@pytest.mark.unit
def test_current_four_action_policy_saturation_is_detected_separately():
    result = audit_repeater_policy_saturation(
        "wide-prediction-range",
        mean=[0.0, 0.2, 0.5, 0.8, 0.95],
        uncertainty=[0.01] * 5,
        out_of_distribution=[0] * 5,
    )

    assert result["prediction_span"] == pytest.approx(0.95)
    assert result["intended_action_counts"] == {"SWAP": 5}
    assert result["intended_policy_saturated"] is True
    assert result["effective_action_counts"] == {"SWAP": 5}
    assert result["physical_action_performed"] is False


@pytest.mark.unit
def test_policy_audit_keeps_intent_but_safety_halts_ood_and_uncertainty():
    result = audit_repeater_policy_saturation(
        "guarded",
        mean=[0.8, 0.8, 0.8],
        uncertainty=[0.01, 0.21, 0.01],
        out_of_distribution=[0, 0, 1],
    )

    assert result["intended_action_counts"] == {"SWAP": 3}
    assert result["effective_action_counts"] == {"HALT": 2, "SWAP": 1}
    assert result["safety_violation_counts"] == {
        "input_out_of_distribution": 1,
        "prediction_uncertainty_too_high": 1,
    }


@pytest.mark.unit
def test_exact_sign_flip_and_holm_adjustment_are_deterministic():
    positive = paired_sign_flip_test([1.0, 1.0, 1.0], alternative="greater")
    adjusted = holm_adjust([0.01, 0.04, 0.03])

    assert positive["p_value"] == pytest.approx(1 / 8)
    assert adjusted.tolist() == pytest.approx([0.03, 0.06, 0.06])


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"mean": [0.5, 0.6]}, "align"),
        ({"uncertainty": [-0.1]}, "non-negative"),
        ({"mean": [1.1]}, r"\[0, 1\]"),
    ],
)
def test_scorecard_rejects_malformed_prediction_vectors(kwargs, message):
    values = {
        "mean": [0.5],
        "uncertainty": [0.1],
        "actual_fidelity": [0.5],
        "availability": [1],
        "out_of_distribution": [0],
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        evaluate_controller_predictions("bad", **values)


@pytest.mark.unit
def test_holm_adjustment_matches_original_order():
    adjusted = holm_adjust(np.array([0.20, 0.001, 0.03]))

    assert adjusted.tolist() == pytest.approx([0.20, 0.003, 0.06])
