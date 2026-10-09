"""Calibrate KAPPA_DEPOL_FROM_BER only from synchronized measured fidelity."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from quantum_twin.evaluation.kappa_calibration import (
    KappaCalibrationConfig,
    calibrate_kappa_from_measured_fidelity,
)
from quantum_twin.quantum.paired_telemetry import (
    PairedQuantumProfile,
    PairedQuantumTelemetrySource,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fit the simulation BER coupling on a temporal training prefix."
    )
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--train-fraction", type=float, default=0.7)
    parser.add_argument("--kappa-max", type=float, default=100.0)
    parser.add_argument("--candidate-count", type=int, default=501)
    parser.add_argument("--bootstrap-replicates", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if not 0.0 < args.train_fraction < 1.0:
        parser.error("--train-fraction must be between zero and one")

    source = PairedQuantumTelemetrySource(
        args.data,
        manifest_path=args.manifest,
        profile=PairedQuantumProfile.MEASURED_BELL_FIDELITY,
    )
    data = source.require_valid()
    order = np.argsort(data["timestamp_utc"].to_numpy(), kind="stable")
    split = int(np.floor(len(order) * args.train_fraction))
    config = KappaCalibrationConfig(
        kappa_max=args.kappa_max,
        candidate_count=args.candidate_count,
        bootstrap_replicates=args.bootstrap_replicates,
        seed=args.seed,
    )
    result = calibrate_kappa_from_measured_fidelity(
        data,
        train_indices=order[:split],
        test_indices=order[split:],
        config=config,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result.candidate_metrics.to_csv(args.output_dir / "candidate_metrics.csv", index=False)
    result.test_predictions.to_csv(args.output_dir / "held_out_predictions.csv", index=False)
    (args.output_dir / "report.json").write_text(
        json.dumps(result.report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result.report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
