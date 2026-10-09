"""Tests for the simulated-WDM model boundary of the control sandbox."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import torch

from quantum_autopilot.control_plane import (
    ControllerRuntime,
    WDMControlRequest,
    WDMModelDrivenController,
    WDMModelInferenceAdapter,
    WDMModelSpec,
    WDMTelemetryPoint,
    hash_torch_model,
)
from quantum_autopilot.core import EvidenceKind, Provenance
from quantum_twin.ml.dual_head import EdgeLSTMDualHead
from quantum_twin.ml.lstm import EdgeLSTM
from quantum_twin.ml.probabilistic import EdgeLSTMProbabilistic


FEATURES = ("osnr_db", "BER")
ZERO_HASH = "0" * 64


def point_model(value: float = 0.9) -> EdgeLSTM:
    model = EdgeLSTM(input_size=2, hidden_size=3)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.head.bias.fill_(torch.logit(torch.tensor(value)))
    return model


def model_spec(model, **overrides) -> WDMModelSpec:
    values = {
        "model_name": "edge_lstm",
        "model_version": "simulation-test-v1",
        "output_kind": "point",
        "feature_columns": FEATURES,
        "window_size": 3,
        "feature_min": (10.0, 0.0),
        "feature_max": (30.0, 0.5),
        "model_sha256": hash_torch_model(model),
        "config_sha256": ZERO_HASH,
        "dataset_sha256": "1" * 64,
        "calibrated_point_uncertainty": 0.04,
        "uncertainty_calibration": "held-out temporal residual RMS",
    }
    values.update(overrides)
    return WDMModelSpec(**values)


def request(**overrides) -> WDMControlRequest:
    start = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    points = tuple(
        WDMTelemetryPoint(
            timestamp_utc=(start + timedelta(seconds=index)).isoformat(),
            values={"osnr_db": 20.0 + index, "BER": 0.02 + index * 0.01},
        )
        for index in range(3)
    )
    values = {
        "request_id": "wdm-cycle-001",
        "node_id": "repeater-a",
        "evaluated_at_utc": (start + timedelta(seconds=2.1)).isoformat(),
        "state_version": 1,
        "telemetry_window": points,
        "memory_state": "STORING",
        "available_pair_fidelities": (0.9, 0.9),
        "memory_ages_s": (0.0, 0.0),
        "provenance": Provenance(
            evidence_kind=EvidenceKind.SIMULATED,
            source="causal-wdm-simulator-test",
            timestamp=start.isoformat(),
        ),
        "integrity_verified": True,
        "operator_interlock": True,
    }
    values.update(overrides)
    return WDMControlRequest(**values)


@pytest.mark.unit
def test_wdm_spec_rejects_privileged_features_and_uncalibrated_point_model():
    model = point_model()
    with pytest.raises(ValueError, match="quantum-privileged"):
        model_spec(
            model,
            feature_columns=("osnr_db", "F_t"),
            feature_min=(0.0, 0.0),
            feature_max=(1.0, 1.0),
        )
    with pytest.raises(ValueError, match="calibrated uncertainty"):
        model_spec(model, calibrated_point_uncertainty=None)


@pytest.mark.unit
def test_adapter_rejects_model_hash_mismatch():
    model = point_model()
    with pytest.raises(ValueError, match="model_sha256"):
        WDMModelInferenceAdapter(model, model_spec(model, model_sha256="f" * 64))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("bad_request", "message"),
    [
        (
            lambda item: request(telemetry_window=item.telemetry_window[:-1]),
            "exactly 3",
        ),
        (
            lambda item: request(
                telemetry_window=(
                    item.telemetry_window[1],
                    item.telemetry_window[0],
                    item.telemetry_window[2],
                )
            ),
            "strictly increasing",
        ),
        (
            lambda item: request(
                telemetry_window=(
                    *item.telemetry_window[:-1],
                    WDMTelemetryPoint(
                        item.telemetry_window[-1].timestamp_utc,
                        {"osnr_db": 20.0},
                    ),
                )
            ),
            "missing features",
        ),
    ],
)
def test_adapter_rejects_malformed_temporal_windows(bad_request, message):
    model = point_model()
    adapter = WDMModelInferenceAdapter(model, model_spec(model))
    base = request()
    with pytest.raises(ValueError, match=message):
        adapter.predict(bad_request(base))


@pytest.mark.unit
def test_point_model_builds_lineage_complete_snapshot():
    model = point_model(0.9)
    adapter = WDMModelInferenceAdapter(model, model_spec(model))

    snapshot, prediction = adapter.build_snapshot(request())

    assert prediction.mean == pytest.approx(0.9)
    assert prediction.uncertainty == pytest.approx(0.04)
    assert prediction.out_of_distribution is False
    assert snapshot.predicted_fidelity == pytest.approx(0.9)
    assert snapshot.telemetry_age_s == pytest.approx(0.1)
    assert snapshot.telemetry == {"osnr_db": 22.0, "BER": 0.04}
    assert snapshot.provenance.evidence_kind is EvidenceKind.SIMULATED
    assert snapshot.provenance.artifact_hashes["wdm_model"] == hash_torch_model(model)
    assert snapshot.provenance.artifact_hashes["wdm_window"] == prediction.window_sha256
    assert snapshot.provenance.metadata["measured_quantum_target"] is False
    assert snapshot.metadata["wdm_model_inference"]["target_semantics"] == (
        "simulated_future_bell_fidelity"
    )


@pytest.mark.unit
def test_ood_window_is_propagated_to_fail_closed_supervisor(tmp_path):
    model = point_model(0.9)
    adapter = WDMModelInferenceAdapter(model, model_spec(model))
    base = request()
    bad_last = WDMTelemetryPoint(
        base.telemetry_window[-1].timestamp_utc,
        {"osnr_db": 35.0, "BER": 0.04},
    )
    controller = WDMModelDrivenController(
        ControllerRuntime(tmp_path / "wdm.sqlite"), adapter
    )

    result = controller.cycle(
        request(telemetry_window=(*base.telemetry_window[:-1], bad_last)),
        mode="simulation",
    )

    assert result["inference"]["out_of_distribution"] is True
    assert result["record"]["intent"]["policy_details"]["prediction_source"] == (
        "wdm_model_inference_adapter"
    )
    assert result["record"]["safety"]["effective_action"] == "HALT"
    assert "input_out_of_distribution" in {
        item["reason_code"] for item in result["record"]["safety"]["violations"]
    }
    assert result["record"]["execution"]["physical_action_performed"] is False


@pytest.mark.unit
def test_probabilistic_model_supplies_its_own_uncertainty():
    model = EdgeLSTMProbabilistic(input_size=2, hidden_size=3)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.mean_head.bias.fill_(torch.logit(torch.tensor(0.8)))
        model.logvar_head.bias.fill_(2.0 * torch.log(torch.tensor(0.05)))
    spec = model_spec(
        model,
        output_kind="probabilistic",
        calibrated_point_uncertainty=None,
        uncertainty_calibration="model output; external calibration remains model lineage",
    )

    prediction = WDMModelInferenceAdapter(model, spec).predict(request())

    assert prediction.mean == pytest.approx(0.8)
    assert prediction.uncertainty == pytest.approx(0.05)


@pytest.mark.unit
def test_dual_head_uses_availability_as_gate_not_multiplier():
    model = EdgeLSTMDualHead(input_size=2, hidden_size=3)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.availability_head.bias.fill_(torch.logit(torch.tensor(0.4)))
        model.fidelity_head.bias.fill_(torch.logit(torch.tensor(0.9)))
    spec = model_spec(model, model_name="edge_lstm_dual_head", output_kind="dual_head")

    prediction = WDMModelInferenceAdapter(model, spec).predict(request())

    assert prediction.availability_probability == pytest.approx(0.4)
    assert prediction.conditional_fidelity == pytest.approx(0.9)
    assert prediction.mean == 0.0


@pytest.mark.unit
def test_measured_telemetry_cannot_enter_simulated_model_lane():
    model = point_model()
    adapter = WDMModelInferenceAdapter(model, model_spec(model))
    measured = Provenance(
        evidence_kind=EvidenceKind.MEASURED,
        source="real-wdm-without-paired-quantum-target",
    )

    with pytest.raises(ValueError, match="simulated telemetry only"):
        adapter.predict(request(provenance=measured))


@pytest.mark.unit
def test_model_driven_shadow_cycle_is_audited_and_idempotent(tmp_path):
    model = point_model(0.9)
    controller = WDMModelDrivenController(
        ControllerRuntime(tmp_path / "wdm.sqlite"),
        WDMModelInferenceAdapter(model, model_spec(model)),
    )

    first = controller.cycle(request(), mode="shadow")
    replay = controller.cycle(request(), mode="shadow")

    assert first["record"]["intent"]["policy_details"]["prediction_source"] == (
        "wdm_model_inference_adapter"
    )
    assert first["record"]["intent"]["policy_details"]["prediction_model"][
        "model_name"
    ] == "edge_lstm"
    assert first["record"]["execution"]["status"] == "SHADOW_RECORDED"
    assert first["record"]["execution"]["physical_action_performed"] is False
    assert replay["idempotent_replay"] is True
    assert controller.status()["physical_action_available"] is False
