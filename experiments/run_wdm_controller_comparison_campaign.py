"""Run the predeclared paired simulated-controller comparison (v5.25).

Every candidate sees the same held-out samples for each seed/regime. Prediction
quality, HALT/WAIT/PURIFY admission utility and the current four-action sandbox
policy are reported separately. No measured WDM or measured quantum target is
used, and no winner is selected or tuned on test data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import torch
from sklearn.preprocessing import MinMaxScaler

from quantum_autopilot.control_plane import (
    ComparativeControlConfig,
    audit_repeater_policy_saturation,
    evaluate_controller_predictions,
    hash_torch_model,
    holm_adjust,
    paired_sign_flip_test,
)
from quantum_twin.core.config import PhysicsConfig
from quantum_twin.ml.dual_head import EdgeLSTMDualHead, train_dual_head_robust
from quantum_twin.ml.lstm import EdgeLSTM, train_edge_lstm_robust
from quantum_twin.ml.probabilistic import (
    EdgeLSTMProbabilistic,
    calibrate_sigma_temperature,
    train_edge_lstm_probabilistic,
)
from quantum_twin.optical.wdm import QuantumNetworkDatasetV3


DEFAULT_CONFIG = Path("experiments/configs/wdm_controller_comparison_v525.json")


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _build_windows(
    frame: pd.DataFrame,
    feature_columns: tuple[str, ...],
    window_size: int,
    *,
    scaler: MinMaxScaler | None = None,
    fit_row_count: int | None = None,
) -> dict[str, Any]:
    raw = frame.loc[:, feature_columns].to_numpy(dtype=float)
    if scaler is None:
        if fit_row_count is None or fit_row_count < window_size + 2:
            raise ValueError("fit_row_count is required when fitting the scaler")
        scaler = MinMaxScaler()
        scaler.fit(raw[:fit_row_count])
    scaled = scaler.transform(raw)
    minima = np.asarray(scaler.data_min_, dtype=float)
    maxima = np.asarray(scaler.data_max_, dtype=float)
    windows = []
    targets = []
    availability = []
    last_fidelity = []
    ood = []
    for start in range(len(frame) - window_size):
        stop = start + window_size
        raw_window = raw[start:stop]
        windows.append(scaled[start:stop])
        targets.append(float(frame.iloc[stop]["F_t"]))
        availability.append(bool(frame.iloc[stop]["channel_available"]))
        last_fidelity.append(float(frame.iloc[stop - 1]["F_t"]))
        ood.append(bool(((raw_window < minima) | (raw_window > maxima)).any()))
    return {
        "X": torch.tensor(np.asarray(windows, dtype=np.float32)),
        "y": np.asarray(targets, dtype=float),
        "availability": np.asarray(availability, dtype=bool),
        "last_fidelity": np.asarray(last_fidelity, dtype=float),
        "ood": np.asarray(ood, dtype=bool),
        "scaler": scaler,
    }


def _slice(values: Mapping[str, Any], selected: slice) -> dict[str, Any]:
    return {
        key: (value[selected] if key != "scaler" else value)
        for key, value in values.items()
    }


def _rms_residual(prediction: np.ndarray, target: np.ndarray) -> float:
    return float(np.clip(np.sqrt(np.mean((prediction - target) ** 2)), 1e-6, 1.0))


def _predict_models(
    models: Mapping[str, Any],
    data: Mapping[str, Any],
    *,
    point_sigma: float,
    probability_temperature: float,
    dual_sigma: float,
    availability_gate: float,
) -> dict[str, dict[str, Any]]:
    X = data["X"]
    y = np.asarray(data["y"], dtype=float)
    last = np.asarray(data["last_fidelity"], dtype=float)
    false_ood = np.zeros(len(y), dtype=bool)
    model_ood = np.asarray(data["ood"], dtype=bool)
    with torch.no_grad():
        point = models["point"](X).cpu().numpy().reshape(-1)
        prob_mean_t, prob_sigma_t = models["probabilistic"](X)
        prob_mean = prob_mean_t.cpu().numpy().reshape(-1)
        prob_sigma = (
            prob_sigma_t.cpu().numpy().reshape(-1) * float(probability_temperature)
        )
        available_t, conditional_t = models["dual_head"](X)
        available_probability = available_t.cpu().numpy().reshape(-1)
        conditional = conditional_t.cpu().numpy().reshape(-1)
        dual_mean = np.where(available_probability >= availability_gate, conditional, 0.0)
    return {
        "persistence_target_history": {
            "mean": last,
            "sigma": np.full(len(y), models["persistence_sigma"]),
            "ood": false_ood,
            "kind": "distribution",
            "inference_latency_s": 0.0,
        },
        "reactive_current_fidelity": {
            "mean": last,
            "sigma": np.zeros(len(y), dtype=float),
            "ood": false_ood,
            "kind": "reactive",
            "inference_latency_s": 0.0,
        },
        "edge_lstm_point": {
            "mean": point,
            "sigma": np.full(len(y), point_sigma),
            "ood": model_ood,
            "kind": "distribution",
            "inference_latency_s": None,
        },
        "edge_lstm_probabilistic": {
            "mean": prob_mean,
            "sigma": prob_sigma,
            "ood": model_ood,
            "kind": "distribution",
            "inference_latency_s": None,
        },
        "edge_lstm_dual_head": {
            "mean": dual_mean,
            "sigma": np.full(len(y), dual_sigma),
            "ood": model_ood,
            "kind": "distribution",
            "inference_latency_s": None,
            "availability_probability": available_probability,
            "conditional_fidelity": conditional,
        },
        "oracle_future_fidelity": {
            "mean": y.copy(),
            "sigma": np.zeros(len(y), dtype=float),
            "ood": false_ood,
            "kind": "oracle",
            "inference_latency_s": 0.0,
        },
    }


def _train_seed_models(
    seed: int,
    fit: Mapping[str, Any],
    calibration: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    model_cfg = config["model"]
    threshold = float(config["decision"]["fidelity_threshold"])
    input_size = int(fit["X"].shape[-1])
    X_fit = fit["X"]
    y_fit = torch.tensor(np.asarray(fit["y"], dtype=np.float32)).reshape(-1, 1)
    availability_fit = torch.tensor(
        np.asarray(fit["availability"], dtype=np.float32)
    ).reshape(-1, 1)
    common = {
        "max_epochs": int(model_cfg["max_epochs"]),
        "lr": float(model_cfg["learning_rate"]),
        "batch_size": int(model_cfg["batch_size"]),
        "patience": int(model_cfg["early_stopping_patience"]),
        "verbose": False,
    }

    torch.manual_seed(seed * 10 + 1)
    point = EdgeLSTM(input_size=input_size, hidden_size=int(model_cfg["hidden_size"]))
    point, point_val_loss = train_edge_lstm_robust(
        point,
        X_fit,
        y_fit,
        threshold=threshold,
        lambda_penalty=float(model_cfg["lambda_false_positive"]),
        lambda_fn=float(model_cfg["lambda_false_negative"]),
        discard_penalty_weight=float(model_cfg["discard_penalty_weight"]),
        max_discard_rate=float(model_cfg["max_discard_rate"]),
        **common,
    )

    torch.manual_seed(seed * 10 + 2)
    probabilistic = EdgeLSTMProbabilistic(
        input_size=input_size, hidden_size=int(model_cfg["hidden_size"])
    )
    probabilistic, probabilistic_val_loss = train_edge_lstm_probabilistic(
        probabilistic,
        X_fit,
        y_fit,
        threshold=threshold,
        lambda_penalty=float(model_cfg["lambda_false_positive"]),
        discard_penalty_weight=float(model_cfg["discard_penalty_weight"]),
        max_discard_rate=float(model_cfg["max_discard_rate"]),
        **common,
    )

    torch.manual_seed(seed * 10 + 3)
    dual_head = EdgeLSTMDualHead(
        input_size=input_size, hidden_size=int(model_cfg["hidden_size"])
    )
    dual_head, dual_val_loss = train_dual_head_robust(
        dual_head,
        X_fit,
        availability_fit,
        y_fit,
        threshold=threshold,
        lambda_penalty=2.0,
        lambda_fn=2.0,
        **common,
    )
    for model in (point, probabilistic, dual_head):
        model.eval()

    X_cal = calibration["X"]
    y_cal = np.asarray(calibration["y"], dtype=float)
    availability_gate = float(config["decision"]["availability_gate"])
    with torch.no_grad():
        point_cal = point(X_cal).numpy().reshape(-1)
        prob_mu_t, prob_sigma_t = probabilistic(X_cal)
        prob_mu = prob_mu_t.numpy().reshape(-1)
        prob_sigma_raw = prob_sigma_t.numpy().reshape(-1)
        dual_available_t, dual_conditional_t = dual_head(X_cal)
        dual_available = dual_available_t.numpy().reshape(-1)
        dual_conditional = dual_conditional_t.numpy().reshape(-1)
        dual_cal = np.where(
            dual_available >= availability_gate, dual_conditional, 0.0
        )
    persistence_sigma = _rms_residual(
        np.asarray(calibration["last_fidelity"], dtype=float), y_cal
    )
    point_sigma = _rms_residual(point_cal, y_cal)
    dual_sigma = _rms_residual(dual_cal, y_cal)
    probability_temperature = calibrate_sigma_temperature(
        prob_mu, prob_sigma_raw, y_cal
    )
    models = {
        "point": point,
        "probabilistic": probabilistic,
        "dual_head": dual_head,
        "persistence_sigma": persistence_sigma,
    }
    lineage = {
        "point": {
            "model_sha256": hash_torch_model(point),
            "best_internal_validation_loss": float(point_val_loss),
            "calibrated_sigma": point_sigma,
        },
        "probabilistic": {
            "model_sha256": hash_torch_model(probabilistic),
            "best_internal_validation_loss": float(probabilistic_val_loss),
            "sigma_temperature": probability_temperature,
        },
        "dual_head": {
            "model_sha256": hash_torch_model(dual_head),
            "best_internal_validation_loss": float(dual_val_loss),
            "calibrated_sigma": dual_sigma,
        },
        "persistence": {"calibrated_sigma": persistence_sigma},
    }
    return models, {
        "lineage": lineage,
        "point_sigma": point_sigma,
        "probability_temperature": probability_temperature,
        "dual_sigma": dual_sigma,
    }


def _control_config(config: Mapping[str, Any]) -> ComparativeControlConfig:
    decision = config["decision"]
    resources = config["resource_accounting"]
    return ComparativeControlConfig(
        fidelity_threshold=float(decision["fidelity_threshold"]),
        confidence_k=float(decision["confidence_k"]),
        max_wait_cycles=int(decision["max_wait_cycles"]),
        max_prediction_uncertainty=float(decision["max_prediction_uncertainty"]),
        n_gates_per_purification=int(resources["n_gates_per_purification"]),
        neural_inference_latency_s=float(resources["neural_inference_latency_s"]),
        control_latency_s=float(resources["control_latency_s"]),
        wait_time_s=float(resources["wait_time_s"]),
        qpu_energy_per_gate_j=float(resources["qpu_energy_per_gate_j"]),
        inference_power_w=float(resources["inference_power_w"]),
        control_power_w=float(resources["control_power_w"]),
        memory_hold_power_w=float(resources["memory_hold_power_w"]),
    )


def _validate_config(config: Mapping[str, Any]) -> None:
    seeds = config.get("seeds")
    if not isinstance(seeds, list) or not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("seeds must be a non-empty unique list")
    if len(seeds) > 20:
        raise ValueError("exact paired sign-flip inference supports at most 20 seeds")
    if int(config["n_steps"]) < 120 or int(config["ood_n_steps"]) < 120:
        raise ValueError("n_steps and ood_n_steps must be >= 120")
    if int(config["window_size"]) < 2 or int(config["cycles_per_regime"]) < 1:
        raise ValueError("window_size must be >= 2 and cycles_per_regime must be positive")
    if not 0.05 <= float(config["test_fraction"]) <= 0.5:
        raise ValueError("test_fraction must be in [0.05, 0.5]")
    if not 0.05 <= float(config["calibration_fraction"]) <= 0.4:
        raise ValueError("calibration_fraction must be in [0.05, 0.4]")
    _control_config(config)


def run_campaign(config: Mapping[str, Any], output_dir: str | Path) -> dict[str, Any]:
    _validate_config(config)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    feature_columns = tuple(QuantumNetworkDatasetV3.WDM_FEATURE_COLUMNS)
    control_cfg = _control_config(config)
    config_hash = _sha256_bytes(_canonical_json(config).encode("utf-8"))
    scorecard_rows: list[dict[str, Any]] = []
    cycle_rows: list[dict[str, Any]] = []
    policy_rows: list[dict[str, Any]] = []
    lineage_rows: list[dict[str, Any]] = []
    dataset_rows: list[dict[str, Any]] = []

    for seed_value in config["seeds"]:
        seed = int(seed_value)
        np.random.seed(seed)
        torch.manual_seed(seed)
        id_physics = PhysicsConfig(SEED=seed)
        id_frame = QuantumNetworkDatasetV3(
            n_steps=int(config["n_steps"]), config=id_physics
        ).generate_dataset()
        n_windows = len(id_frame) - int(config["window_size"])
        test_start = int(n_windows * (1.0 - float(config["test_fraction"])))
        # Training windows end one row before their future target. The scaler
        # therefore stops at the final feature row, excluding the first test
        # target row even from unsupervised range estimation.
        train_feature_rows = test_start + int(config["window_size"]) - 1
        id_windows = _build_windows(
            id_frame,
            feature_columns,
            int(config["window_size"]),
            fit_row_count=train_feature_rows,
        )
        calibration_count = max(
            4, int(test_start * float(config["calibration_fraction"]))
        )
        fit_count = test_start - calibration_count
        if fit_count < 16:
            raise ValueError("temporal split leaves too few fit windows")
        fit = _slice(id_windows, slice(0, fit_count))
        calibration = _slice(id_windows, slice(fit_count, test_start))
        models, calibration_state = _train_seed_models(seed, fit, calibration, config)
        for model_name, model_lineage in calibration_state["lineage"].items():
            lineage_rows.append(
                {"seed": seed, "model": model_name, **model_lineage}
            )

        regimes: dict[str, dict[str, Any]] = {
            "id_held_out": _slice(id_windows, slice(test_start, None))
        }
        shift_config = config["domain_shift_regimes"]
        regime_physics = {
            "higher_depolarization_5x": id_physics.with_overrides(
                SEED=seed + 10_000,
                DEPOLARIZATION_P=id_physics.DEPOLARIZATION_P
                * float(
                    shift_config["higher_depolarization_5x"][
                        "DEPOLARIZATION_P_multiplier"
                    ]
                ),
            ),
            "double_link_distance": id_physics.with_overrides(
                SEED=seed + 20_000,
                DISTANCE_KM=id_physics.DISTANCE_KM
                * float(
                    shift_config["double_link_distance"]["DISTANCE_KM_multiplier"]
                ),
            ),
        }
        dataset_rows.append(
            {
                "seed": seed,
                "regime": "id_held_out",
                "dataset_sha256": _sha256_bytes(
                    id_frame.to_csv(index=False, float_format="%.17g").encode("utf-8")
                ),
                "physics": asdict(id_physics),
            }
        )
        for regime, physics in regime_physics.items():
            frame = QuantumNetworkDatasetV3(
                n_steps=int(config["ood_n_steps"]), config=physics
            ).generate_dataset()
            regimes[regime] = _build_windows(
                frame,
                feature_columns,
                int(config["window_size"]),
                scaler=id_windows["scaler"],
            )
            dataset_rows.append(
                {
                    "seed": seed,
                    "regime": regime,
                    "dataset_sha256": _sha256_bytes(
                        frame.to_csv(index=False, float_format="%.17g").encode("utf-8")
                    ),
                    "physics": asdict(physics),
                }
            )

        for regime, regime_data_all in regimes.items():
            cycles = min(int(config["cycles_per_regime"]), len(regime_data_all["y"]))
            regime_data = _slice(regime_data_all, slice(-cycles, None))
            predictions = _predict_models(
                models,
                regime_data,
                point_sigma=float(calibration_state["point_sigma"]),
                probability_temperature=float(
                    calibration_state["probability_temperature"]
                ),
                dual_sigma=float(calibration_state["dual_sigma"]),
                availability_gate=float(config["decision"]["availability_gate"]),
            )
            for controller in config["controllers"]:
                candidate = predictions[str(controller)]
                rows, summary = evaluate_controller_predictions(
                    str(controller),
                    candidate["mean"],
                    candidate["sigma"],
                    regime_data["y"],
                    regime_data["availability"],
                    candidate["ood"],
                    controller_kind=str(candidate["kind"]),
                    inference_latency_s=candidate["inference_latency_s"],
                    config=control_cfg,
                )
                for row in rows:
                    row.update({"seed": seed, "regime": regime})
                    if "availability_probability" in candidate:
                        index = int(row["index"])
                        row["predicted_availability_probability"] = float(
                            candidate["availability_probability"][index]
                        )
                        row["predicted_conditional_fidelity"] = float(
                            candidate["conditional_fidelity"][index]
                        )
                    cycle_rows.append(row)
                scorecard_rows.append({"seed": seed, "regime": regime, **summary})
                policy_rows.append(
                    {
                        "seed": seed,
                        "regime": regime,
                        **audit_repeater_policy_saturation(
                            str(controller),
                            candidate["mean"],
                            candidate["sigma"],
                            candidate["ood"],
                            config=control_cfg,
                        ),
                    }
                )

    scorecard = pd.DataFrame(scorecard_rows)
    cycles_frame = pd.DataFrame(cycle_rows)
    policy_frame = pd.DataFrame(policy_rows)
    lineage_frame = pd.DataFrame(lineage_rows)
    dataset_frame = pd.DataFrame(dataset_rows)
    numeric_columns = [
        column
        for column in scorecard.select_dtypes(include=[np.number]).columns
        if column != "seed"
    ]
    aggregate = (
        scorecard.groupby(["regime", "controller"], sort=True)[numeric_columns]
        .agg(["mean", "std"])
    )
    aggregate.columns = [f"{metric}_{stat}" for metric, stat in aggregate.columns]
    aggregate = aggregate.reset_index()

    primary = scorecard.loc[
        scorecard["regime"].eq(config["primary_regime"]),
        ["seed", "controller", config["primary_metric"]],
    ].pivot(index="seed", columns="controller", values=config["primary_metric"])
    statistical_rows = []
    raw_p = []
    for candidate in config["primary_candidates"]:
        differences = (
            primary[str(candidate)] - primary[str(config["primary_baseline"])]
        ).to_numpy(dtype=float)
        test = paired_sign_flip_test(differences, alternative="greater")
        raw_p.append(float(test["p_value"]))
        statistical_rows.append(
            {
                "candidate": candidate,
                "baseline": config["primary_baseline"],
                "regime": config["primary_regime"],
                "metric": config["primary_metric"],
                **test,
            }
        )
    adjusted = holm_adjust(raw_p)
    for row, adjusted_p in zip(statistical_rows, adjusted):
        row["holm_adjusted_p"] = float(adjusted_p)
        row["positive_and_familywise_significant"] = bool(
            row["mean_difference"] > 0.0
            and adjusted_p < float(config["familywise_alpha"])
        )
    statistics = pd.DataFrame(statistical_rows)

    paths = {
        "per_cycle.csv": cycles_frame,
        "per_seed_scorecard.csv": scorecard,
        "aggregate_scorecard.csv": aggregate,
        "primary_paired_tests.csv": statistics,
        "sandbox_policy_audit.csv": policy_frame,
        "model_lineage.csv": lineage_frame,
        "dataset_lineage.csv": dataset_frame,
    }
    for name, frame in paths.items():
        frame.to_csv(output / name, index=False)

    id_policy = policy_frame.loc[policy_frame["regime"].eq("id_held_out")]
    report = {
        "campaign": "paired simulated WDM controller comparison",
        "version": "5.25",
        "scientific_status": (
            "simulated controller evaluation; not measured WDM-to-quantum validation"
        ),
        "config_sha256": config_hash,
        "config": dict(config),
        "controller_count": len(config["controllers"]),
        "seed_count": len(config["seeds"]),
        "regime_count": 3,
        "paired_sample_rule": "same seed, regime and held-out indices for every controller",
        "winner_selected_on_test_data": False,
        "primary_results": statistical_rows,
        "primary_positive_count": int(
            statistics["positive_and_familywise_significant"].sum()
        ),
        "primary_test_count": len(statistics),
        "sandbox_policy_audit": {
            "fixed_state": {"memory_state": "STORING", "pair_fidelities": [0.9, 0.9]},
            "id_rows": int(len(id_policy)),
            "saturated_rows": int(id_policy["intended_policy_saturated"].sum()),
            "all_id_controller_seed_rows_saturated": bool(
                id_policy["intended_policy_saturated"].all()
            ),
            "interpretation": (
                "A saturated intended policy cannot demonstrate operational predictor gain; "
                "safety-driven effective HALT differences must be reported separately."
            ),
        },
        "resource_accounting_status": config["resource_accounting"]["status"],
        "evidence_kind": "simulated",
        "measured_quantum_target_present": False,
        "physical_action_performed": False,
    }
    report_path = output / "report.json"
    _write_json(report_path, report)
    manifest = {
        "schema_version": "1.0.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_sha256": config_hash,
        "artifacts": {
            path.name: _sha256_file(path)
            for path in [report_path, *(output / name for name in paths)]
        },
        "evidence_kind": "simulated",
        "physical_action_performed": False,
    }
    _write_json(output / "artifact_manifest.json", manifest)
    return {"report": report, "manifest": manifest, "output_dir": str(output)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/wdm_controller_comparison_v525"),
    )
    parser.add_argument("--seeds", type=int, nargs="+", default=None)
    parser.add_argument("--n-steps", type=int, default=None)
    parser.add_argument("--ood-n-steps", type=int, default=None)
    parser.add_argument("--cycles", type=int, default=None)
    parser.add_argument("--max-epochs", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.seeds is not None:
        config["seeds"] = args.seeds
    if args.n_steps is not None:
        config["n_steps"] = args.n_steps
    if args.ood_n_steps is not None:
        config["ood_n_steps"] = args.ood_n_steps
    if args.cycles is not None:
        config["cycles_per_regime"] = args.cycles
    if args.max_epochs is not None:
        config["model"]["max_epochs"] = args.max_epochs
    result = run_campaign(config, args.output_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
