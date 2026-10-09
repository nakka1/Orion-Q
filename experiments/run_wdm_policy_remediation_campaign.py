"""Run the predeclared v5.26 task-policy and abstention remediation.

The policy is calibrated only on each seed's temporal calibration prefix,
frozen and then evaluated on a held-out suffix. All seeds are disjoint from the
v5.25 campaign. Inputs and targets remain simulated and no action is physical.
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

from quantum_autopilot.control_plane import (
    AbstentionCalibrationConfig,
    audit_task_policy_sensitivity,
    calibrate_link_policy_spec,
    evaluate_link_policy_predictions,
    holm_adjust,
    paired_sign_flip_test,
)
from quantum_twin.core.config import PhysicsConfig
from quantum_twin.optical.wdm import QuantumNetworkDatasetV3
from run_wdm_controller_comparison_campaign import (
    _build_windows,
    _predict_models,
    _slice,
    _train_seed_models,
)


DEFAULT_CONFIG = Path("experiments/configs/wdm_policy_remediation_v526.json")


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


def _calibration_config(config: Mapping[str, Any]) -> AbstentionCalibrationConfig:
    policy = config["policy_calibration"]
    utility = policy["normalized_utility"]
    return AbstentionCalibrationConfig(
        fidelity_threshold=float(config["decision"]["fidelity_threshold"]),
        max_safety_abstention_rate_pct=float(
            policy["max_safety_abstention_rate_pct"]
        ),
        max_policy_wait_rate_pct=float(policy["max_policy_wait_rate_pct"]),
        probability_margin_grid=tuple(
            float(item) for item in policy["probability_margin_grid"]
        ),
        useful_pair_reward=float(utility["useful_pair_reward"]),
        false_purification_penalty=float(utility["false_purification_penalty"]),
        missed_useful_pair_penalty=float(utility["missed_useful_pair_penalty"]),
        wait_penalty=float(utility["wait_penalty"]),
        purification_attempt_penalty=float(utility["purification_attempt_penalty"]),
    )


def _validate_config(config: Mapping[str, Any]) -> None:
    seeds = config.get("evaluation_seeds")
    prior = config.get("prohibited_prior_test_seeds")
    if not isinstance(seeds, list) or not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("evaluation_seeds must be a non-empty unique list")
    if len(seeds) > 20:
        raise ValueError("exact paired sign-flip inference supports at most 20 seeds")
    if not isinstance(prior, list) or set(seeds) & set(prior):
        raise ValueError("evaluation_seeds must be disjoint from prohibited prior seeds")
    if config.get("control_task") != "link_purification":
        raise ValueError("this campaign evaluates the link_purification task")
    if int(config["n_steps"]) < 120 or int(config["ood_n_steps"]) < 120:
        raise ValueError("n_steps and ood_n_steps must be >= 120")
    if int(config["window_size"]) < 2 or int(config["cycles_per_regime"]) < 1:
        raise ValueError("window_size must be >= 2 and cycles_per_regime positive")
    if not 0.05 <= float(config["test_fraction"]) <= 0.5:
        raise ValueError("test_fraction must be in [0.05, 0.5]")
    if not 0.05 <= float(config["calibration_fraction"]) <= 0.4:
        raise ValueError("calibration_fraction must be in [0.05, 0.4]")
    _calibration_config(config)


def _aggregate(scorecard: pd.DataFrame) -> pd.DataFrame:
    numeric = [
        column
        for column in scorecard.select_dtypes(include=[np.number]).columns
        if column != "seed"
    ]
    result = scorecard.groupby(["regime", "controller"], sort=True)[numeric].agg(
        ["mean", "std"]
    )
    result.columns = [f"{metric}_{stat}" for metric, stat in result.columns]
    return result.reset_index()


def run_campaign(config: Mapping[str, Any], output_dir: str | Path) -> dict[str, Any]:
    _validate_config(config)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    config_hash = _sha256_bytes(_canonical_json(config).encode("utf-8"))
    feature_columns = tuple(QuantumNetworkDatasetV3.WDM_FEATURE_COLUMNS)
    calibration_cfg = _calibration_config(config)
    scorecard_rows: list[dict[str, Any]] = []
    cycle_rows: list[dict[str, Any]] = []
    policy_spec_rows: list[dict[str, Any]] = []
    sensitivity_rows: list[dict[str, Any]] = []
    model_rows: list[dict[str, Any]] = []
    dataset_rows: list[dict[str, Any]] = []

    for seed_value in config["evaluation_seeds"]:
        seed = int(seed_value)
        np.random.seed(seed)
        torch.manual_seed(seed)
        id_physics = PhysicsConfig(SEED=seed)
        id_frame = QuantumNetworkDatasetV3(
            n_steps=int(config["n_steps"]), config=id_physics
        ).generate_dataset()
        window_size = int(config["window_size"])
        n_windows = len(id_frame) - window_size
        test_start = int(n_windows * (1.0 - float(config["test_fraction"])))
        train_feature_rows = test_start + window_size - 1
        id_windows = _build_windows(
            id_frame,
            feature_columns,
            window_size,
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
        models, calibration_state = _train_seed_models(
            seed, fit, calibration, config
        )
        for model_name, lineage in calibration_state["lineage"].items():
            model_rows.append({"seed": seed, "model": model_name, **lineage})

        calibration_predictions = _predict_models(
            models,
            calibration,
            point_sigma=float(calibration_state["point_sigma"]),
            probability_temperature=float(
                calibration_state["probability_temperature"]
            ),
            dual_sigma=float(calibration_state["dual_sigma"]),
            availability_gate=float(config["decision"]["availability_gate"]),
        )
        frozen_specs = {}
        for controller in config["controllers"]:
            prediction = calibration_predictions[str(controller)]
            frozen = calibrate_link_policy_spec(
                str(controller),
                f"seed-{seed}-temporal-calibration-prefix",
                prediction["mean"],
                prediction["sigma"],
                calibration["y"],
                calibration["availability"],
                prediction["ood"],
                config=calibration_cfg,
            )
            frozen_specs[str(controller)] = frozen
            selected = frozen.calibration_diagnostics["selected"]
            policy_spec_rows.append(
                {
                    "seed": seed,
                    "controller": controller,
                    "policy_spec_sha256": frozen.sha256,
                    "calibration_sha256": frozen.calibration_sha256,
                    "calibration_samples": frozen.calibration_samples,
                    "output_fidelity_threshold": frozen.fidelity_threshold,
                    "purification_input_threshold": frozen.purification_input_threshold,
                    "max_prediction_uncertainty": frozen.max_prediction_uncertainty,
                    "probability_margin": frozen.probability_margin,
                    "calibration_safety_abstention_rate_pct": frozen.calibration_diagnostics[
                        "observed_high_uncertainty_abstention_rate_pct"
                    ],
                    "calibration_policy_wait_rate_pct": selected[
                        "policy_wait_rate_pct"
                    ],
                    "calibration_net_utility_per_safe_arrival": selected[
                        "net_utility_per_safe_arrival"
                    ],
                    "frozen_before_test": True,
                    "physical_calibration": False,
                }
            )
            sensitivity_rows.append(
                {
                    "seed": seed,
                    "controller": controller,
                    **audit_task_policy_sensitivity(frozen),
                }
            )

        regimes: dict[str, dict[str, Any]] = {
            "id_held_out": _slice(id_windows, slice(test_start, None))
        }
        shifts = config["domain_shift_regimes"]
        regime_physics = {
            "higher_depolarization_5x": id_physics.with_overrides(
                SEED=seed + 10_000,
                DEPOLARIZATION_P=id_physics.DEPOLARIZATION_P
                * float(
                    shifts["higher_depolarization_5x"][
                        "DEPOLARIZATION_P_multiplier"
                    ]
                ),
            ),
            "double_link_distance": id_physics.with_overrides(
                SEED=seed + 20_000,
                DISTANCE_KM=id_physics.DISTANCE_KM
                * float(shifts["double_link_distance"]["DISTANCE_KM_multiplier"]),
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
                frame, feature_columns, window_size, scaler=id_windows["scaler"]
            )
            dataset_rows.append(
                {
                    "seed": seed,
                    "regime": regime,
                    "dataset_sha256": _sha256_bytes(
                        frame.to_csv(index=False, float_format="%.17g").encode(
                            "utf-8"
                        )
                    ),
                    "physics": asdict(physics),
                }
            )

        for regime, regime_all in regimes.items():
            cycles = min(int(config["cycles_per_regime"]), len(regime_all["y"]))
            regime_data = _slice(regime_all, slice(-cycles, None))
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
                prediction = predictions[str(controller)]
                rows, summary = evaluate_link_policy_predictions(
                    str(controller),
                    frozen_specs[str(controller)],
                    prediction["mean"],
                    prediction["sigma"],
                    regime_data["y"],
                    regime_data["availability"],
                    prediction["ood"],
                )
                for row in rows:
                    cycle_rows.append({"seed": seed, "regime": regime, **row})
                scorecard_rows.append({"seed": seed, "regime": regime, **summary})

    scorecard = pd.DataFrame(scorecard_rows)
    cycles_frame = pd.DataFrame(cycle_rows)
    specs_frame = pd.DataFrame(policy_spec_rows)
    sensitivity_frame = pd.DataFrame(sensitivity_rows)
    model_frame = pd.DataFrame(model_rows)
    dataset_frame = pd.DataFrame(dataset_rows)
    aggregate = _aggregate(scorecard)

    primary = scorecard.loc[
        scorecard["regime"].eq(config["primary_regime"]),
        ["seed", "controller", config["primary_metric"]],
    ].pivot(index="seed", columns="controller", values=config["primary_metric"])
    statistical_rows: list[dict[str, Any]] = []
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

    if (cycles_frame["intended_action"] == "SWAP").any():
        raise RuntimeError("link-purification evaluation must never emit SWAP")
    sensitivity_ok = (
        sensitivity_frame[
            [
                "purify_and_swap_mutually_exclusive",
                "link_prediction_sensitive",
                "path_pair_sensitive",
                "path_prediction_invariant",
            ]
        ]
        .all(axis=None)
        .item()
    )
    if not sensitivity_ok:
        raise RuntimeError("task-policy sensitivity audit failed")

    paths = {
        "per_cycle.csv": cycles_frame,
        "per_seed_scorecard.csv": scorecard,
        "aggregate_scorecard.csv": aggregate,
        "primary_paired_tests.csv": statistics,
        "frozen_policy_specs.csv": specs_frame,
        "task_sensitivity_audit.csv": sensitivity_frame,
        "model_lineage.csv": model_frame,
        "dataset_lineage.csv": dataset_frame,
    }
    for name, frame in paths.items():
        frame.to_csv(output / name, index=False)

    id_scorecard = scorecard.loc[scorecard["regime"].eq("id_held_out")]
    calibration_budget_compliance = bool(
        (
            specs_frame["calibration_safety_abstention_rate_pct"]
            <= calibration_cfg.max_safety_abstention_rate_pct + 1e-12
        ).all()
        and (
            specs_frame["calibration_policy_wait_rate_pct"]
            <= calibration_cfg.max_policy_wait_rate_pct + 1e-12
        ).all()
    )
    report = {
        "campaign": "task-constrained WDM policy remediation",
        "version": "5.26",
        "scientific_status": (
            "simulated policy evaluation; not measured WDM-to-quantum validation"
        ),
        "config_sha256": config_hash,
        "config": dict(config),
        "fresh_seed_count": len(config["evaluation_seeds"]),
        "prior_seed_overlap": sorted(
            set(config["evaluation_seeds"])
            & set(config["prohibited_prior_test_seeds"])
        ),
        "paired_sample_rule": "same seed, regime and held-out indices for every controller",
        "policy_freeze_rule": (
            "controller-specific spec fitted on temporal calibration prefix and hashed "
            "before held-out suffix evaluation"
        ),
        "winner_selected_on_test_data": False,
        "primary_results": statistical_rows,
        "primary_positive_count": int(
            statistics["positive_and_familywise_significant"].sum()
        ),
        "primary_test_count": len(statistics),
        "calibration_budget_compliance": calibration_budget_compliance,
        "task_semantics_audit": {
            "rows": len(sensitivity_frame),
            "all_rows_passed": sensitivity_ok,
            "purify_and_swap_never_compete": True,
            "link_prediction_sensitive": True,
            "path_pair_sensitive": True,
            "path_prediction_invariant": True,
        },
        "held_out_saturation": {
            "id_controller_seed_rows": len(id_scorecard),
            "saturated_rows": int(id_scorecard["intended_policy_saturated"].sum()),
            "interpretation": (
                "Sensitivity probes establish policy responsiveness; held-out saturation "
                "is reported separately as a model/distribution outcome."
            ),
        },
        "abstention_categories": [
            "safety_ood_abstention",
            "safety_high_uncertainty_abstention",
            "policy_probability_abstention",
            "ordinary_low_fidelity_halt",
        ],
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
        default=Path("outputs/wdm_policy_remediation_v526"),
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
        config["evaluation_seeds"] = args.seeds
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
