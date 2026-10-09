"""
run_domain_shift_matrix_10seed.py
=====================================

TCC improvement item 4: "Nenhuma conclusão principal dependerá de uma
execução isolada." Extends the eighty-third addendum's single-seed
domain-shift matrix (which found a real, but explicitly-flagged-as
-unvalidated, directional asymmetry: B->A beating A's own diagonal) to
a genuine N=10-seed campaign, reporting mean/median/std/95% CI/effect
size/paired statistical test -- resolving that addendum's own stated
"Honest limitations" item directly, with real evidence rather than a
second single-seed run.

Reuses run_domain_shift_matrix.py's train_on_regime()/evaluate_cross_regime()
directly (not reimplemented), across the SAME 10 seeds used throughout
this project's other multi-seed campaigns, for the two asymmetry pairs
specifically (A<->B, A<->C) -- not the full 6-cell matrix at every seed
(which would need 30 total model trainings; this focuses compute on the
SPECIFIC claim needing validation: the asymmetry finding itself).

Usage:
    python run_domain_shift_matrix_10seed.py --config configs/config.yaml
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import argparse
import os

import numpy as np
import pandas as pd
import torch
from scipy import stats
import yaml

from run_domain_shift_matrix import train_on_regime, evaluate_cross_regime


SEEDS = [42, 123, 7, 2024, 31415, 99, 555, 8080, 271828, 16180]


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_one_seed(seed: int, cfg: dict) -> dict:
    """Trains fresh A/B/C models for ONE seed, evaluates the 4
    asymmetry-relevant cells (A->B, B->A, A->C, C->A), returns Delta_MAE
    for each relative to that seed's OWN A-regime diagonal.

    A REAL BUG was found and fixed here during development (not
    shipped): the first draft never called np.random.seed(seed)/
    torch.manual_seed(seed) before training -- meaning only the DATA
    generation (PhysicsConfig(SEED=seed)) was actually deterministic;
    model weight initialization and any training-time randomness were
    NOT controlled by the `seed` parameter at all, making the campaign's
    results non-reproducible with respect to seed. Caught by comparing
    a freshly-computed seed=42 result against the eighty-third
    addendum's own single-seed run (same nominal seed) and finding a
    large, unexplained discrepancy (diag_A_MAE 0.258 vs. 0.277;
    B_to_A_minus_diag_A +0.0005 vs. -0.0150) -- fixed by seeding BOTH
    RNGs explicitly at the start of every call, restoring genuine
    determinism."""
    np.random.seed(seed)
    torch.manual_seed(seed)

    trained_a = train_on_regime("A_ID_default", seed, cfg)
    trained_b = train_on_regime("B_drift_amplitude_2x", seed, cfg)
    trained_c = train_on_regime("C_correlation_time_2x", seed, cfg)

    diag_a_mae = trained_a["diag_metrics"]["MAE"]

    a_to_b = evaluate_cross_regime(trained_a, "B_drift_amplitude_2x", seed, cfg)
    b_to_a = evaluate_cross_regime(trained_b, "A_ID_default", seed, cfg)
    a_to_c = evaluate_cross_regime(trained_a, "C_correlation_time_2x", seed, cfg)
    c_to_a = evaluate_cross_regime(trained_c, "A_ID_default", seed, cfg)

    return {
        "seed": seed, "diag_A_MAE": diag_a_mae,
        "A_to_B_MAE": a_to_b["MAE"], "B_to_A_MAE": b_to_a["MAE"],
        "A_to_C_MAE": a_to_c["MAE"], "C_to_A_MAE": c_to_a["MAE"],
        "B_to_A_minus_diag_A": b_to_a["MAE"] - diag_a_mae,
        "C_to_A_minus_diag_A": c_to_a["MAE"] - diag_a_mae,
        "asymmetry_AB": abs(a_to_b["MAE"] - b_to_a["MAE"]),
        "asymmetry_AC": abs(a_to_c["MAE"] - c_to_a["MAE"]),
    }


def summarize(values: np.ndarray) -> dict:
    mean, std, median = values.mean(), values.std(ddof=1), np.median(values)
    se = std / np.sqrt(len(values))
    ci = stats.t.interval(0.95, df=len(values) - 1, loc=mean, scale=se)
    return {"mean": mean, "median": median, "std": std, "CI95_low": ci[0], "CI95_high": ci[1]}


def main(config_path: str = "configs/config.yaml", seeds: list = None):
    cfg = load_config(config_path)
    seeds = seeds or SEEDS
    os.makedirs("outputs", exist_ok=True)

    print(f"Running domain-shift asymmetry validation across {len(seeds)} seeds ...")
    rows = []
    for i, seed in enumerate(seeds):
        print(f"[{i+1}/{len(seeds)}] seed={seed} ...")
        result = run_one_seed(seed, cfg)
        rows.append(result)
        print(f"  B->A - diag_A = {result['B_to_A_minus_diag_A']:+.5f}   "
              f"C->A - diag_A = {result['C_to_A_minus_diag_A']:+.5f}")

    results_df = pd.DataFrame(rows)
    results_df.to_csv("outputs/domain_shift_asymmetry_10seed_raw.csv", index=False)

    b_minus_diag = results_df["B_to_A_minus_diag_A"].values
    c_minus_diag = results_df["C_to_A_minus_diag_A"].values
    asym_ab = results_df["asymmetry_AB"].values
    asym_ac = results_df["asymmetry_AC"].values

    print("\n" + "=" * 100)
    print(" DOMAIN SHIFT ASYMMETRY: 10-SEED VALIDATION ".center(100, "="))
    print("=" * 100)

    for name, values in [("B->A minus diag_A (negative = B->A beats A's own baseline)", b_minus_diag),
                          ("C->A minus diag_A", c_minus_diag),
                          ("Asymmetry |A->B - B->A| (drift amplitude)", asym_ab),
                          ("Asymmetry |A->C - C->A| (correlation time)", asym_ac)]:
        summary = summarize(values)
        t_stat, p_value = stats.ttest_1samp(values, 0.0)
        cohens_d = summary["mean"] / summary["std"] if summary["std"] > 0 else float("nan")
        print(f"\n{name}:")
        print(f"  values: {np.round(values, 5).tolist()}")
        print(f"  mean={summary['mean']:+.5f}  median={summary['median']:+.5f}  std={summary['std']:.5f}")
        print(f"  95% CI: [{summary['CI95_low']:+.5f}, {summary['CI95_high']:+.5f}]")
        print(f"  one-sample t-test vs. 0: p={p_value:.5f}   Cohen's d={cohens_d:.3f}")

    print("\n" + "=" * 100)
    summary_rows = []
    for name, values in [("B_to_A_minus_diag_A", b_minus_diag), ("C_to_A_minus_diag_A", c_minus_diag),
                          ("asymmetry_AB", asym_ab), ("asymmetry_AC", asym_ac)]:
        s = summarize(values)
        t_stat, p_value = stats.ttest_1samp(values, 0.0)
        summary_rows.append({"Metric": name, "N": len(values), "Mean": s["mean"], "Median": s["median"],
                              "Std": s["std"], "CI95_low": s["CI95_low"], "CI95_high": s["CI95_high"],
                              "p_vs_zero": p_value})
    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv("outputs/domain_shift_asymmetry_10seed_summary.csv", index=False)
    print(summary_df.to_string(index=False))
    print("\nSaved: outputs/domain_shift_asymmetry_10seed_raw.csv, "
          "outputs/domain_shift_asymmetry_10seed_summary.csv")

    return results_df, summary_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, default="configs/config.yaml")
    parser.add_argument("--seeds", type=int, nargs="+", default=None)
    args = parser.parse_args()
    main(args.config, args.seeds)
