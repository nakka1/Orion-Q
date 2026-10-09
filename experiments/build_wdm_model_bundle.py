"""Build the reproducible v5.27 DualHead bundle for local dashboard replay.

The artifact contains only numeric state tensors and JSON.  It is trained and
calibrated on simulated data, embeds a bounded held-out simulated trace and is
permanently labelled as diagnostic/not approved for physical control.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import torch

from quantum_autopilot.control_plane import (
    AbstentionCalibrationConfig,
    WDMControlRequest,
    WDMModelSpec,
    WDMReplayRecord,
    WDMTelemetryPoint,
    calibrate_link_policy_spec,
    export_wdm_model_bundle,
    hash_torch_model,
)
from quantum_autopilot.core import EvidenceKind, Provenance
from quantum_twin.core.config import PhysicsConfig
from quantum_twin.optical.wdm import QuantumNetworkDatasetV3
from run_wdm_controller_comparison_campaign import (
    _build_windows,
    _predict_models,
    _slice,
    _train_seed_models,
)


DEFAULT_CONFIG = Path("experiments/configs/wdm_policy_remediation_v526.json")
DEFAULT_OUTPUT = Path("outputs/wdm_model_bundle_v527/edge_lstm_dual_head.wdmbundle.npz")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _calibration_config(config: dict[str, Any]) -> AbstentionCalibrationConfig:
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


def build_bundle(
    config: dict[str, Any],
    output_path: str | Path,
    *,
    seed: int = 809,
    replay_records: int = 50,
) -> dict[str, Any]:
    """Train one predeclared seed and export its frozen DualHead shadow bundle."""

    if seed not in config.get("evaluation_seeds", []):
        raise ValueError("seed must be one of the predeclared v5.26 evaluation seeds")
    if not 1 <= replay_records <= 250:
        raise ValueError("replay_records must be in [1, 250]")
    np.random.seed(seed)
    torch.manual_seed(seed)
    feature_columns = tuple(QuantumNetworkDatasetV3.WDM_FEATURE_COLUMNS)
    physics = PhysicsConfig(SEED=seed)
    frame = QuantumNetworkDatasetV3(
        n_steps=int(config["n_steps"]), config=physics
    ).generate_dataset()
    window_size = int(config["window_size"])
    n_windows = len(frame) - window_size
    test_start = int(n_windows * (1.0 - float(config["test_fraction"])))
    train_feature_rows = test_start + window_size - 1
    windows = _build_windows(
        frame,
        feature_columns,
        window_size,
        fit_row_count=train_feature_rows,
    )
    calibration_count = max(
        4, int(test_start * float(config["calibration_fraction"]))
    )
    fit_count = test_start - calibration_count
    fit = _slice(windows, slice(0, fit_count))
    calibration = _slice(windows, slice(fit_count, test_start))
    held_out = _slice(windows, slice(test_start, None))
    models, calibration_state = _train_seed_models(seed, fit, calibration, config)
    calibration_predictions = _predict_models(
        models,
        calibration,
        point_sigma=float(calibration_state["point_sigma"]),
        probability_temperature=float(calibration_state["probability_temperature"]),
        dual_sigma=float(calibration_state["dual_sigma"]),
        availability_gate=float(config["decision"]["availability_gate"]),
    )
    controller_name = "edge_lstm_dual_head"
    dual_calibration = calibration_predictions[controller_name]
    policy_spec = calibrate_link_policy_spec(
        controller_name,
        f"seed-{seed}-temporal-calibration-prefix-v527-bundle",
        dual_calibration["mean"],
        dual_calibration["sigma"],
        calibration["y"],
        calibration["availability"],
        dual_calibration["ood"],
        config=_calibration_config(config),
    )
    model = models["dual_head"]
    model_sha256 = hash_torch_model(model)
    config_sha256 = _sha256_bytes(_canonical_json(config).encode("utf-8"))
    dataset_sha256 = _sha256_bytes(
        frame.to_csv(index=False, float_format="%.17g").encode("utf-8")
    )
    spec = WDMModelSpec(
        model_name=controller_name,
        model_version=f"v5.27-simulated-seed-{seed}",
        output_kind="dual_head",
        feature_columns=feature_columns,
        window_size=window_size,
        feature_min=tuple(float(item) for item in windows["scaler"].data_min_),
        feature_max=tuple(float(item) for item in windows["scaler"].data_max_),
        model_sha256=model_sha256,
        config_sha256=config_sha256,
        dataset_sha256=dataset_sha256,
        calibrated_point_uncertainty=float(calibration_state["dual_sigma"]),
        uncertainty_calibration="v5.26 temporal calibration-prefix residual RMS",
        availability_gate=float(config["decision"]["availability_gate"]),
    )
    count = min(replay_records, len(held_out["y"]))
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    trace: list[WDMReplayRecord] = []
    # Uniform temporal coverage is fixed without inspecting predictions, OOD
    # labels or targets. This avoids presenting only the earliest holdout regime
    # while also avoiding outcome-based cherry-picking for the dashboard.
    selected_held_out_indices = np.linspace(
        0, len(held_out["y"]) - 1, num=count, dtype=int
    ).tolist()
    for replay_index, held_out_index in enumerate(selected_held_out_indices):
        start = test_start + held_out_index
        points = tuple(
            WDMTelemetryPoint(
                timestamp_utc=(base_time + timedelta(seconds=row_index)).isoformat(),
                values={
                    name: float(frame.iloc[row_index][name])
                    for name in feature_columns
                },
            )
            for row_index in range(start, start + window_size)
        )
        target = float(held_out["y"][held_out_index])
        available = bool(held_out["availability"][held_out_index])
        trace.append(
            WDMReplayRecord(
                request=WDMControlRequest(
                    request_id=(
                        f"wdm-v527-{model_sha256[:12]}-{held_out_index:04d}"
                    ),
                    node_id="repeater-v527-shadow",
                    evaluated_at_utc=(
                        base_time + timedelta(seconds=start + window_size - 1, milliseconds=100)
                    ).isoformat(),
                    state_version=replay_index,
                    telemetry_window=points,
                    memory_state="STORING" if available else "IDLE",
                    available_pair_fidelities=(target, target) if available else (),
                    memory_ages_s=(0.0, 0.0) if available else (),
                    provenance=Provenance(
                        evidence_kind=EvidenceKind.SIMULATED,
                        source="QuantumNetworkDatasetV3 v5.27 frozen replay",
                        timestamp=base_time.isoformat(),
                        config_hash=config_sha256,
                        dataset_hash=dataset_sha256,
                        metadata={
                            "evidence_lane": "bell_fidelity_control",
                            "target_semantics": "simulated_future_bell_fidelity",
                            "physical_calibration": False,
                            "measured_quantum_target": False,
                        },
                    ),
                    integrity_verified=True,
                    operator_interlock=True,
                    metadata={
                        "trace_split": "held_out_simulated_suffix",
                        "replay_index": replay_index,
                        "source_held_out_index": held_out_index,
                        "trace_sampling": "uniform_temporal_without_outcome_selection",
                        "retrospective_target_excluded_from_inference": True,
                    },
                ),
                simulated_actual_fidelity=target,
            )
        )
    scientific_status = {
        "approved_for_control": False,
        "physical_validation": False,
        "paired_measured_wdm_quantum_validation": False,
        "v5_25_registered_neural_improvements": "0/3",
        "v5_26_registered_neural_improvements": "0/3",
        "statement": (
            "Frozen simulated diagnostic model; no statistically supported advantage "
            "over persistence and no measured WDM-to-quantum validation."
        ),
    }
    exported = export_wdm_model_bundle(
        output_path,
        model=model,
        model_spec=spec,
        policy_spec=policy_spec,
        replay_records=trace,
        scientific_status=scientific_status,
    )
    return {
        **exported,
        "seed": seed,
        "fit_windows": fit_count,
        "calibration_windows": calibration_count,
        "held_out_windows": len(held_out["y"]),
        "model_name": controller_name,
        "scientific_status": scientific_status,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=809)
    parser.add_argument("--replay-records", type=int, default=50)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    result = build_bundle(
        config,
        args.output,
        seed=args.seed,
        replay_records=args.replay_records,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
