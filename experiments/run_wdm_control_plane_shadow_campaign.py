"""Train an existing WDM fidelity model and replay it through the sandbox.

This is an explicitly simulated product-integration campaign.  It demonstrates
that EdgeLSTM, EdgeGRU or EdgeTCN predictions can drive audited shadow decisions;
it is not evidence that WDM predicts fidelity on a physical quantum network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
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
from quantum_twin.core.config import PhysicsConfig
from quantum_twin.ml.architectures import EdgeGRU, EdgeTCN
from quantum_twin.ml.dual_head import EdgeLSTMDualHead, train_dual_head_robust
from quantum_twin.ml.lstm import EdgeLSTM, train_edge_lstm_robust
from quantum_twin.optical.wdm import QuantumNetworkDatasetV3


SUPPORTED_MODELS = ("edge_lstm", "edge_gru", "edge_tcn", "edge_lstm_dual_head")


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _make_model(name: str, input_size: int, hidden_size: int):
    if name == "edge_lstm":
        return EdgeLSTM(input_size=input_size, hidden_size=hidden_size)
    if name == "edge_gru":
        return EdgeGRU(input_size=input_size, hidden_size=hidden_size)
    if name == "edge_tcn":
        return EdgeTCN(input_size=input_size, hidden_channels=hidden_size)
    if name == "edge_lstm_dual_head":
        return EdgeLSTMDualHead(input_size=input_size, hidden_size=hidden_size)
    raise ValueError(f"model must be one of {SUPPORTED_MODELS}")


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def run_campaign(args: argparse.Namespace) -> dict[str, object]:
    if args.n_steps < 100:
        raise ValueError("n_steps must be >= 100")
    if args.cycles < 1:
        raise ValueError("cycles must be >= 1")
    if not 0.05 <= args.calibration_fraction <= 0.4:
        raise ValueError("calibration_fraction must be between 0.05 and 0.4")
    if not 0.05 <= args.test_fraction <= 0.5:
        raise ValueError("test_fraction must be between 0.05 and 0.5")
    if args.telemetry_age_s < 0.0:
        raise ValueError("telemetry_age_s must be non-negative")
    if not 0.0 <= args.availability_gate <= 1.0:
        raise ValueError("availability_gate must be in [0, 1]")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    journal_path = output_dir / "shadow_journal.sqlite"
    feature_columns = tuple(QuantumNetworkDatasetV3.WDM_FEATURE_COLUMNS)
    physics = PhysicsConfig(SEED=int(args.seed))
    dataset = QuantumNetworkDatasetV3(n_steps=int(args.n_steps), config=physics)
    frame = dataset.generate_dataset()
    X_train, y_train, X_test, y_test, scaler, _ = dataset.preprocess(
        frame,
        window_size=int(args.window_size),
        test_size=float(args.test_fraction),
        feature_set="wdm_only",
    )
    calibration_count = max(4, int(len(X_train) * float(args.calibration_fraction)))
    fit_count = len(X_train) - calibration_count
    if fit_count < 8 or len(X_test) < 1:
        raise ValueError("campaign split leaves too few fit, calibration or replay windows")

    torch.manual_seed(int(args.seed))
    model = _make_model(args.model, len(feature_columns), int(args.hidden_size))
    output_kind = "point"
    if args.model == "edge_lstm_dual_head":
        target_availability = torch.tensor(
            frame["channel_available"].iloc[int(args.window_size):].to_numpy(dtype=np.float32),
            dtype=torch.float32,
        ).reshape(-1, 1)
        availability_train = target_availability[:len(X_train)]
        model, validation_loss = train_dual_head_robust(
            model,
            X_train[:fit_count],
            availability_train[:fit_count],
            y_train[:fit_count],
            max_epochs=int(args.max_epochs),
            lr=float(args.learning_rate),
            batch_size=int(args.batch_size),
            patience=int(args.patience),
            verbose=False,
        )
        output_kind = "dual_head"
    else:
        model, validation_loss = train_edge_lstm_robust(
            model,
            X_train[:fit_count],
            y_train[:fit_count],
            max_epochs=int(args.max_epochs),
            lr=float(args.learning_rate),
            batch_size=int(args.batch_size),
            patience=int(args.patience),
            verbose=False,
        )
    model.eval()
    with torch.no_grad():
        if output_kind == "dual_head":
            probability, conditional = model(X_train[fit_count:])
            calibration_predictions = torch.where(
                probability >= float(args.availability_gate),
                conditional,
                torch.zeros_like(conditional),
            ).cpu().numpy().reshape(-1)
        else:
            calibration_predictions = model(X_train[fit_count:]).cpu().numpy().reshape(-1)
    calibration_targets = y_train[fit_count:].cpu().numpy().reshape(-1)
    absolute_residuals = np.abs(calibration_predictions - calibration_targets)
    # RiskAwareController interprets this field as a Gaussian sigma.  A
    # conformal quantile would be a prediction-interval half-width, not a
    # standard deviation, so point models use held-out residual RMS here.
    calibrated_uncertainty = float(np.sqrt(np.mean(absolute_residuals ** 2)))
    calibrated_uncertainty = min(max(calibrated_uncertainty, 1e-6), 1.0)

    config_payload = {
        "model": args.model,
        "seed": int(args.seed),
        "n_steps": int(args.n_steps),
        "window_size": int(args.window_size),
        "test_fraction": float(args.test_fraction),
        "calibration_fraction": float(args.calibration_fraction),
        "hidden_size": int(args.hidden_size),
        "max_epochs": int(args.max_epochs),
        "learning_rate": float(args.learning_rate),
        "batch_size": int(args.batch_size),
        "patience": int(args.patience),
        "feature_set": "wdm_only",
        "target": "simulated_future_bell_fidelity",
        "mode": "shadow",
        "availability_gate": float(args.availability_gate),
    }
    config_sha256 = _sha256_bytes(_canonical_json(config_payload).encode("utf-8"))
    dataset_sha256 = _sha256_bytes(
        frame.to_csv(index=False, float_format="%.17g").encode("utf-8")
    )
    spec = WDMModelSpec(
        model_name=args.model,
        model_version=f"simulated-shadow-seed-{int(args.seed)}",
        output_kind=output_kind,
        feature_columns=feature_columns,
        window_size=int(args.window_size),
        feature_min=tuple(float(item) for item in scaler.data_min_),
        feature_max=tuple(float(item) for item in scaler.data_max_),
        model_sha256=hash_torch_model(model),
        config_sha256=config_sha256,
        dataset_sha256=dataset_sha256,
        calibrated_point_uncertainty=calibrated_uncertainty,
        uncertainty_calibration="temporal holdout residual RMS (global Gaussian sigma)",
        availability_gate=float(args.availability_gate),
    )
    controller = WDMModelDrivenController(
        ControllerRuntime(journal_path),
        WDMModelInferenceAdapter(model, spec),
    )

    n_windows = len(frame) - int(args.window_size)
    test_start = int(n_windows * (1.0 - float(args.test_fraction)))
    replay_count = min(int(args.cycles), len(X_test))
    base_time = datetime.now(timezone.utc)
    rows: list[dict[str, object]] = []
    complete_records: list[dict[str, object]] = []
    for replay_index in range(replay_count):
        start = test_start + replay_index
        points = tuple(
            WDMTelemetryPoint(
                timestamp_utc=(base_time + timedelta(seconds=row_index)).isoformat(),
                values={name: float(frame.iloc[row_index][name]) for name in feature_columns},
            )
            for row_index in range(start, start + int(args.window_size))
        )
        evaluated_at = base_time + timedelta(
            seconds=start + int(args.window_size) - 1 + float(args.telemetry_age_s)
        )
        request = WDMControlRequest(
            request_id=f"wdm-shadow-{int(args.seed)}-{replay_index:04d}",
            node_id="repeater-a",
            evaluated_at_utc=evaluated_at.isoformat(),
            state_version=replay_index,
            telemetry_window=points,
            memory_state="STORING",
            available_pair_fidelities=(0.90, 0.90),
            memory_ages_s=(0.0, 0.0),
            provenance=Provenance(
                evidence_kind=EvidenceKind.SIMULATED,
                source="QuantumNetworkDatasetV3 causal WDM trace",
                timestamp=base_time.isoformat(),
                config_hash=config_sha256,
                dataset_hash=dataset_sha256,
                metadata={
                    "physical_calibration": False,
                    "measured_quantum_target": False,
                },
            ),
            integrity_verified=True,
            operator_interlock=True,
            metadata={"replay_index": replay_index},
        )
        result = controller.cycle(request, mode="shadow")
        record = result["record"]
        actual_fidelity = float(y_test[replay_index].item())
        rows.append(
            {
                "replay_index": replay_index,
                "predicted_fidelity": result["inference"]["mean"],
                "prediction_uncertainty": result["inference"]["uncertainty"],
                "availability_probability": result["inference"][
                    "availability_probability"
                ],
                "conditional_fidelity": result["inference"]["conditional_fidelity"],
                "simulated_actual_fidelity": actual_fidelity,
                "absolute_error": abs(float(result["inference"]["mean"]) - actual_fidelity),
                "out_of_distribution": result["inference"]["out_of_distribution"],
                "intended_action": record["intent"]["action"],
                "effective_action": record["safety"]["effective_action"],
                "authorized": record["safety"]["authorized"],
                "record_sha256": result["record_sha256"],
                "physical_action_performed": record["execution"]["physical_action_performed"],
            }
        )
        complete_records.append(result)

    predictions = pd.DataFrame(rows)
    predictions_path = output_dir / "shadow_predictions.csv"
    records_path = output_dir / "shadow_records.json"
    predictions.to_csv(predictions_path, index=False)
    _write_json(records_path, complete_records)
    actions = Counter(str(item["effective_action"]) for item in rows)
    summary = {
        "campaign": "simulated_wdm_control_plane_shadow",
        "scientific_status": "product integration on simulated data; not experimental validation",
        "central_wdm_hypothesis_status": "open for paired measured WDM and quantum outcomes",
        "physical_action_performed": False,
        "controller": controller.status(),
        "model": spec.to_dict(),
        "training": {
            "fit_windows": fit_count,
            "calibration_windows": calibration_count,
            "test_windows_available": len(X_test),
            "best_internal_validation_loss": float(validation_loss),
            "calibrated_uncertainty": calibrated_uncertainty,
        },
        "replay": {
            "cycles": replay_count,
            "mean_absolute_error": float(predictions["absolute_error"].mean()),
            "ood_cycles": int(predictions["out_of_distribution"].sum()),
            "authorized_cycles": int(predictions["authorized"].sum()),
            "effective_action_counts": dict(sorted(actions.items())),
        },
        "config": config_payload,
    }
    summary_path = output_dir / "summary.json"
    _write_json(summary_path, summary)
    manifest = {
        "schema_version": "1.0.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifacts": {
            path.name: _sha256_bytes(path.read_bytes())
            for path in (summary_path, predictions_path, records_path, journal_path)
        },
        "physical_action_performed": False,
        "evidence_kind": "simulated",
    }
    _write_json(output_dir / "manifest.json", manifest)
    return {"summary": summary, "manifest": manifest, "output_dir": str(output_dir)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", choices=SUPPORTED_MODELS, default="edge_lstm")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n-steps", type=int, default=800)
    parser.add_argument("--window-size", type=int, default=20)
    parser.add_argument("--test-fraction", type=float, default=0.25)
    parser.add_argument("--calibration-fraction", type=float, default=0.15)
    parser.add_argument("--availability-gate", type=float, default=0.5)
    parser.add_argument("--cycles", type=int, default=25)
    parser.add_argument("--telemetry-age-s", type=float, default=0.1)
    parser.add_argument("--hidden-size", type=int, default=16)
    parser.add_argument("--max-epochs", type=int, default=40)
    parser.add_argument("--learning-rate", type=float, default=0.01)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--patience", type=int, default=8)
    args = parser.parse_args()
    result = run_campaign(args)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
