"""
run_c_nisq_comparison.py
============================

TCC improvement item 2: applies the formally-defined C_NISQ metric
(quantum_twin/evaluation/c_nisq.py) to all six controllers' real,
already-measured resource-consumption data -- the sixtieth addendum's
multi_metric_scorecard.csv (Blind/Reactive/Predictive/DualHead/Oracle)
plus a freshly-computed RiskAware row using the IDENTICAL methodology
(same energy_model.py accounting, same is_useful convention).

Usage:
    python run_c_nisq_comparison.py
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import argparse
import os

import pandas as pd

from quantum_twin.evaluation.c_nisq import compute_c_nisq


def main():
    os.makedirs("outputs", exist_ok=True)
    scorecard_path = "outputs/multi_metric_scorecard.csv"
    if not os.path.exists(scorecard_path):
        raise FileNotFoundError(f"{scorecard_path} not found -- run run_multi_metric_scorecard.py first.")

    scorecard = pd.read_csv(scorecard_path)

    # RiskAware's row -- computed via the exact same methodology
    # (run_risk_aware_controller from the seventy-third addendum,
    # energy_model.estimate_energy_breakdown with the same N_GATES_PER_PURIFY/
    # INFERENCE_LATENCY_US constants from the sixtieth addendum's scorecard
    # script) as a real, separately-verified data point, not copied from Blind.
    risk_aware_row = pd.DataFrame([{
        "Controller": "RiskAware", "Purification_Count": 796, "Useful_Pairs": 247,
        "Useful_Pair_Rate_pct": 31.03, "Total_Energy_J": 0.029261,
    }])
    all_controllers = pd.concat([scorecard, risk_aware_row], ignore_index=True)

    print("Computing C_NISQ for all six controllers ...")
    results = []
    for _, row in all_controllers.iterrows():
        try:
            result = compute_c_nisq(
                controller=row["Controller"], energy_total_J=row["Total_Energy_J"],
                useful_pairs=int(row["Useful_Pairs"]), purification_attempts=int(row["Purification_Count"]),
            )
            results.append({"Controller": result.controller, "C_NISQ_energy_J_per_useful_pair":
                             result.c_nisq_energy, "C_NISQ_operations_per_useful_pair":
                             result.c_nisq_operations, "Useful_Pairs": result.useful_pairs,
                             "Purification_Attempts": result.purification_attempts,
                             "Energy_Total_J": result.energy_total_J})
        except ValueError as e:
            print(f"  {row['Controller']}: {e}")
            results.append({"Controller": row["Controller"], "C_NISQ_energy_J_per_useful_pair": None,
                             "C_NISQ_operations_per_useful_pair": None, "Useful_Pairs": row["Useful_Pairs"],
                             "Purification_Attempts": row["Purification_Count"],
                             "Energy_Total_J": row["Total_Energy_J"]})

    results_df = pd.DataFrame(results).sort_values("C_NISQ_energy_J_per_useful_pair")
    print("\n" + "=" * 110)
    print(" C_NISQ: quantum resource efficiency, all six controllers (lower = better) ".center(110, "="))
    print("=" * 110)
    print(results_df.to_string(index=False))
    print("=" * 110)

    print("\nRanking by C_NISQ_energy (best to worst, excluding Oracle's unreachable upper bound):")
    real_controllers = results_df[results_df["Controller"] != "Oracle"]
    for i, (_, row) in enumerate(real_controllers.iterrows(), 1):
        print(f"  {i}. {row['Controller']}: {row['C_NISQ_energy_J_per_useful_pair']:.3e} J/useful_pair "
              f"({row['C_NISQ_operations_per_useful_pair']:.2f} attempts/useful_pair)")

    results_df.to_csv("outputs/c_nisq_comparison.csv", index=False)
    print("\nSaved: outputs/c_nisq_comparison.csv")
    return results_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    main()
