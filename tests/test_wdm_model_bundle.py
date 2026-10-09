"""Security and round-trip tests for the v5.27 safe WDM model bundle."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import torch

from quantum_autopilot.control_plane import (
    FrozenTaskPolicySpec,
    WDMControlRequest,
    WDMModelSpec,
    WDMReplayRecord,
    WDMTelemetryPoint,
    export_wdm_model_bundle,
    hash_torch_model,
    load_wdm_model_bundle,
)
from quantum_autopilot.core import EvidenceKind, Provenance
from quantum_twin.ml.lstm import EdgeLSTM
from build_wdm_model_bundle import build_bundle


ZERO_HASH = "0" * 64
SCIENTIFIC_STATUS = {
    "approved_for_control": False,
    "physical_validation": False,
    "paired_measured_wdm_quantum_validation": False,
    "v5_25_registered_neural_improvements": "0/3",
    "v5_26_registered_neural_improvements": "0/3",
    "statement": "Simulated diagnostic model; not an approved controller.",
}


def _model() -> EdgeLSTM:
    model = EdgeLSTM(input_size=2, hidden_size=3)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.head.bias.fill_(torch.logit(torch.tensor(0.8)))
    return model


def _spec(model: EdgeLSTM) -> WDMModelSpec:
    return WDMModelSpec(
        model_name="edge_lstm_point",
        model_version="bundle-test-v1",
        output_kind="point",
        feature_columns=("osnr_db", "BER"),
        window_size=3,
        feature_min=(10.0, 0.0),
        feature_max=(30.0, 0.5),
        model_sha256=hash_torch_model(model),
        config_sha256=ZERO_HASH,
        dataset_sha256="1" * 64,
        calibrated_point_uncertainty=0.04,
        uncertainty_calibration="temporal calibration prefix residual RMS",
    )


def _policy() -> FrozenTaskPolicySpec:
    return FrozenTaskPolicySpec(
        controller_name="edge_lstm_point",
        calibration_id="bundle-test-calibration-prefix",
        calibration_sha256="2" * 64,
        calibration_samples=20,
        fidelity_threshold=0.65,
        purification_input_threshold=0.625,
        probability_margin=0.05,
        max_prediction_uncertainty=0.10,
        max_safety_abstention_rate_pct=20.0,
        max_policy_wait_rate_pct=35.0,
        useful_pair_reward=1.0,
        false_purification_penalty=0.25,
        missed_useful_pair_penalty=0.50,
        wait_penalty=0.01,
        purification_attempt_penalty=0.01,
        calibration_diagnostics={"selection_scope": "calibration_only"},
    )


def _record(index: int = 0) -> WDMReplayRecord:
    start = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    points = tuple(
        WDMTelemetryPoint(
            timestamp_utc=(start + timedelta(seconds=offset)).isoformat(),
            values={"osnr_db": 20.0 + offset, "BER": 0.02 + offset * 0.01},
        )
        for offset in range(3)
    )
    return WDMReplayRecord(
        request=WDMControlRequest(
            request_id=f"bundle-replay-{index}",
            node_id="repeater-bundle-test",
            evaluated_at_utc=(start + timedelta(seconds=2.1)).isoformat(),
            state_version=index,
            telemetry_window=points,
            memory_state="STORING",
            available_pair_fidelities=(0.8, 0.8),
            memory_ages_s=(0.0, 0.0),
            provenance=Provenance(
                evidence_kind=EvidenceKind.SIMULATED,
                source="bundle-test-simulated-trace",
                timestamp=start.isoformat(),
            ),
            integrity_verified=True,
            operator_interlock=True,
        ),
        simulated_actual_fidelity=0.79,
    )


def _export(tmp_path):
    model = _model()
    path = tmp_path / "model.wdmbundle.npz"
    result = export_wdm_model_bundle(
        path,
        model=model,
        model_spec=_spec(model),
        policy_spec=_policy(),
        replay_records=[_record()],
        scientific_status=SCIENTIFIC_STATUS,
        created_at_utc="2026-09-04T12:00:00+00:00",
    )
    return path, result


@pytest.mark.unit
def test_safe_bundle_round_trip_validates_model_policy_trace_and_hash(tmp_path):
    path, exported = _export(tmp_path)

    loaded = load_wdm_model_bundle(
        path, expected_bundle_sha256=exported["bundle_sha256"]
    )
    prediction = loaded.inference_adapter().predict(loaded.replay_records[0].request)
    status = loaded.status()

    assert prediction.mean == pytest.approx(0.8)
    assert status["bundle_hash_pinned"] is True
    assert status["architecture"]["name"] == "edge_lstm"
    assert status["policy"]["policy_spec_sha256"] == _policy().sha256
    assert status["replay_record_count"] == 1
    assert status["scientific_status"]["approved_for_control"] is False
    assert status["physical_action_available"] is False


@pytest.mark.unit
def test_bundle_refuses_overwrite_and_wrong_external_hash(tmp_path):
    path, _ = _export(tmp_path)
    model = _model()
    with pytest.raises(ValueError, match="already exists"):
        export_wdm_model_bundle(
            path,
            model=model,
            model_spec=_spec(model),
            policy_spec=_policy(),
            replay_records=[_record()],
            scientific_status=SCIENTIFIC_STATUS,
        )
    with pytest.raises(ValueError, match="expected_bundle_sha256"):
        load_wdm_model_bundle(path, expected_bundle_sha256="f" * 64)


@pytest.mark.unit
def test_bundle_rejects_pickle_payloads(tmp_path):
    path = tmp_path / "unsafe.npz"
    np.savez(
        path,
        __manifest__=np.asarray([{"callable": "malicious"}], dtype=object),
        __replay__=np.asarray([], dtype=np.uint8),
    )

    with pytest.raises(ValueError, match="allow_pickle=False"):
        load_wdm_model_bundle(path)


@pytest.mark.unit
def test_bundle_rejects_tensor_shape_tampering(tmp_path):
    source, _ = _export(tmp_path)
    tampered = tmp_path / "tampered.npz"
    with np.load(source, allow_pickle=False) as original:
        arrays = {name: original[name] for name in original.files}
    tensor_key = next(
        name
        for name, value in arrays.items()
        if name.startswith("state::") and value.ndim > 1
    )
    arrays[tensor_key] = arrays[tensor_key].reshape(-1)
    np.savez_compressed(tampered, **arrays)

    with pytest.raises(ValueError, match="shape"):
        load_wdm_model_bundle(tampered)


@pytest.mark.unit
def test_bundle_cannot_claim_physical_or_approved_status(tmp_path):
    model = _model()
    status = dict(SCIENTIFIC_STATUS)
    status["approved_for_control"] = True

    with pytest.raises(ValueError, match="approved_for_control"):
        export_wdm_model_bundle(
            tmp_path / "bad-status.npz",
            model=model,
            model_spec=_spec(model),
            policy_spec=_policy(),
            replay_records=[_record()],
            scientific_status=status,
        )


@pytest.mark.unit
def test_v527_builder_emits_a_loadable_uniform_holdout_bundle(tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    config = json.loads(
        (repository_root / "experiments/configs/wdm_policy_remediation_v526.json").read_text(
            encoding="utf-8"
        )
    )
    config = copy.deepcopy(config)
    config["n_steps"] = 180
    config["model"]["max_epochs"] = 1
    config["model"]["early_stopping_patience"] = 1
    output = tmp_path / "campaign-bundle.npz"

    result = build_bundle(config, output, seed=809, replay_records=5)
    loaded = load_wdm_model_bundle(
        output, expected_bundle_sha256=result["bundle_sha256"]
    )

    indices = [
        record.request.metadata["source_held_out_index"]
        for record in loaded.replay_records
    ]
    assert result["model_name"] == "edge_lstm_dual_head"
    assert len(loaded.replay_records) == 5
    assert indices == sorted(indices)
    assert len(set(indices)) == 5
    assert loaded.status()["scientific_status"]["approved_for_control"] is False
