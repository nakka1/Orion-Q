"""
run_domain_shift_matrix.py
==============================

TCC improvement item 5: expands domain-shift testing from the
seventy-sixth addendum's one-directional A->{B,C,D} into the genuinely
requested TRAIN x TEST matrix:

    Train  Test
    A      A
    A      B
    A      C
    B      A
    B      C
    C      A

-- including the REVERSE-direction cells (B->A, C->A) this project had
never tested before, using the SAME three OU-parameter regimes already
defined and validated in run_ou_domain_shift.py (reused directly, not
redefined, to avoid silently diverging regime definitions between the
two scripts).

For each (train_regime, test_regime) cell, trains a fresh EdgeLSTM on
train_regime's ID split, evaluates zero-shot on test_regime's test
split (same scaler-reuse discipline established in the seventy-sixth
addendum, avoiding that addendum's own found-and-fixed scaler-refit
bug), and reports MAE/RMSE/R2 plus Delta_MAE relative to that row's own
diagonal (train_regime -> train_regime) baseline.

Usage:
    python run_domain_shift_matrix.py --config configs/config.yaml
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import argparse
import os

import numpy as np
import pandas as pd
import torch
import yaml

from quantum_twin.core.config import PhysicsConfig
from quantum_twin.optical.wdm import QuantumNetworkDatasetV3
from quantum_twin.ml.lstm import EdgeLSTM, train_edge_lstm
from run_domain_shift_experiment import build_windows
from run_ou_domain_shift import REGIMES, regression_metrics, generate_regime_dataset


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# Only the three genuinely distinct regimes (A, B, C) -- D (sampling interval)
# is excluded from the matrix for tractability (a full 4x4 matrix would need
# 16 trained models; this 3x3 REVERSE-inclusive matrix already covers the
# item's explicit "quando possivel" reverse-direction request with 6 cells,
# reusing A/B/C's already-validated regime definitions).
MATRIX_REGIMES = ["A_ID_default", "B_drift_amplitude_2x", "C_correlation_time_2x"]

# The exact 6 cells the TCC item names explicitly (A->A, A->B, A->C, B->A, B->C, C->A) --
# NOT the full symmetric 9-cell matrix, matching the item's own stated scope.
REQUESTED_CELLS = [
    ("A_ID_default", "A_ID_default"), ("A_ID_default", "B_drift_amplitude_2x"),
    ("A_ID_default", "C_correlation_time_2x"), ("B_drift_amplitude_2x", "A_ID_default"),
    ("B_drift_amplitude_2x", "C_correlation_time_2x"), ("C_correlation_time_2x", "A_ID_default"),
]


def train_on_regime(regime_name: str, seed: int, cfg: dict):
    """Trains a fresh EdgeLSTM on ONE regime's ID split, returning the
    trained model, the fitted scaler (reused transform-only for every
    OOD evaluation against this model), and the regime's own test split
    for the diagonal (train_regime -> train_regime) baseline."""
    ds_cfg, loss_cfg = cfg["dataset"], cfg["loss"]
    threshold = loss_cfg["threshold"]
    window_size = ds_cfg["window_size"]
    columns = QuantumNetworkDatasetV3.FEATURE_COLUMNS

    _, df = generate_regime_dataset(REGIMES[regime_name], seed, ds_cfg["n_steps"])
    split_idx = int(len(df) * (1.0 - ds_cfg["test_size"]))
    train_df = df.iloc[:split_idx].reset_index(drop=True)
    test_df = df.iloc[split_idx - window_size:].reset_index(drop=True)

    X_train, y_train, _avail, scaler = build_windows(
        train_df, columns, window_size, fit_scaler=True, fit_row_count=len(train_df))
    X_test, y_test, _avail2, _ = build_windows(test_df, columns, window_size, scaler=scaler)

    model = EdgeLSTM(input_size=len(columns), hidden_size=cfg["model"]["hidden_size"])
    model = train_edge_lstm(model, X_train, y_train, threshold=threshold, lambda_penalty=0.9,
                             lambda_fn=4.0, discard_penalty_weight=25.0, max_discard_rate=0.60,
                             epochs=150, lr=0.018, verbose=False)
    model.eval()

    with torch.no_grad():
        diag_preds = model(X_test).numpy().ravel()
    diag_metrics = regression_metrics(diag_preds, y_test.squeeze(-1).numpy())

    return {"model": model, "scaler": scaler, "split_idx": split_idx, "window_size": window_size,
            "columns": columns, "diag_metrics": diag_metrics}


def evaluate_cross_regime(trained: dict, test_regime_name: str, seed: int, cfg: dict) -> dict:
    """Evaluates an ALREADY-trained model (from train_on_regime) zero
    -shot on a DIFFERENT regime's test split, reusing the training
    regime's OWN fitted scaler transform-only -- never refitting, per
    the seventy-sixth addendum's established, bug-fixed discipline."""
    ds_cfg = cfg["dataset"]
    _, df = generate_regime_dataset(REGIMES[test_regime_name], seed, ds_cfg["n_steps"])
    test_df = df.iloc[trained["split_idx"] - trained["window_size"]:].reset_index(drop=True)
    X_test, y_test, _avail, _ = build_windows(test_df, trained["columns"], trained["window_size"],
                                               scaler=trained["scaler"])
    with torch.no_grad():
        preds = trained["model"](X_test).numpy().ravel()
    return regression_metrics(preds, y_test.squeeze(-1).numpy())


def main(config_path: str = "configs/config.yaml"):
    cfg = load_config(config_path)
    seed = cfg["seed"]
    np.random.seed(seed)
    torch.manual_seed(seed)

    print(f"Training fresh models on each of {len(MATRIX_REGIMES)} regimes ...")
    trained_models = {}
    for regime_name in MATRIX_REGIMES:
        print(f"  Training on {regime_name} ...")
        trained_models[regime_name] = train_on_regime(regime_name, seed, cfg)

    print(f"\nEvaluating the {len(REQUESTED_CELLS)} requested Train x Test matrix cells ...")
    rows = []
    for train_regime, test_regime in REQUESTED_CELLS:
        trained = trained_models[train_regime]
        if train_regime == test_regime:
            metrics = trained["diag_metrics"]
        else:
            metrics = evaluate_cross_regime(trained, test_regime, seed, cfg)

        diag_mae = trained["diag_metrics"]["MAE"]
        delta_mae = metrics["MAE"] - diag_mae
        rows.append({"Train": train_regime, "Test": test_regime, "MAE": metrics["MAE"],
                     "RMSE": metrics["RMSE"], "R2": metrics["R2"], "Delta_MAE_vs_diagonal": delta_mae})
        print(f"  {train_regime} -> {test_regime}: MAE={metrics['MAE']:.5f} "
              f"(Delta={delta_mae:+.5f} vs. this train regime's own diagonal)")

    results_df = pd.DataFrame(rows)
    print("\n" + "=" * 100)
    print(" DOMAIN SHIFT MATRIX: Train x Test (6 cells, including reverse direction) ".center(100, "="))
    print("=" * 100)
    print(results_df.to_string(index=False))
    print("=" * 100)

    print("\nGeneralization asymmetry check (A->B vs. B->A, A->C vs. C->A):")
    for train_a, test_b in [("A_ID_default", "B_drift_amplitude_2x"), ("A_ID_default", "C_correlation_time_2x")]:
        forward = results_df[(results_df["Train"] == train_a) & (results_df["Test"] == test_b)]
        reverse = results_df[(results_df["Train"] == test_b) & (results_df["Test"] == train_a)]
        if len(forward) > 0 and len(reverse) > 0:
            f_mae = forward["MAE"].iloc[0]
            r_mae = reverse["MAE"].iloc[0]
            print(f"  {train_a}->{test_b}: MAE={f_mae:.5f}  vs.  {test_b}->{train_a}: MAE={r_mae:.5f}  "
                  f"(asymmetry: {abs(f_mae - r_mae):.5f})")

    os.makedirs("outputs", exist_ok=True)
    results_df.to_csv("outputs/domain_shift_matrix.csv", index=False)
    print("\nSaved: outputs/domain_shift_matrix.csv")
    return results_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, default="configs/config.yaml")
    args = parser.parse_args()
    main(args.config)
