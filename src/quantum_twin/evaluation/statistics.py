"""
quantum_twin/evaluation/statistics.py
=========================================

Contains the REAL implementation of paired-statistical-significance
testing (paired t-test, sign test, Cohen's d, master audit Section
20/31) -- migrated here from experiments/run_statistical_significance.py
in the one-hundred-tenth addendum (TCC item 6's explicit "Nenhum módulo
de produção deve depender de run_*.py" rule), resolving a real,
previously-unaudited violation: this file, a genuine production module
inside the quantum_twin package, depended directly on a file living in
experiments/ (a script directory, per the one-hundred-ninth addendum's
own restructuring) simply because paired_analysis's REAL implementation
happened to be co-located with a run_*.py-named experiment script, not
because it was itself experiment-only content. Also re-exports the
reproducibility-manifest utilities (Section 26: config/environment
/dataset-hash/seeds/metrics recording and verification).

Root-level experiments/run_statistical_significance.py is now the thin
re-export (direction reversed) for its own `paired_analysis` name,
kept so the script's own `main()` (a genuine experiment driver,
reporting the DualHead/Predictive/Reactive vs. Blind yield differences
across 3 seeds -- correctly experiment-only content, left in
experiments/ unchanged) continues to work completely UNCHANGED.
"""
import numpy as np
from scipy import stats

from quantum_twin.evaluation.reproducibility import (
    save_experiment_manifest, compute_dataset_hash, verify_dataset_hash,
)


def paired_analysis(name_a: str, name_b: str, values_a: list, values_b: list) -> dict:
    a, b = np.array(values_a), np.array(values_b)
    diff = a - b
    n = len(diff)

    t_stat, p_ttest = stats.ttest_rel(a, b)
    n_positive = int((diff > 0).sum())
    sign_p = stats.binomtest(n_positive, n=n, p=0.5, alternative="greater").pvalue

    cohens_d = diff.mean() / diff.std(ddof=1) if diff.std(ddof=1) > 0 else float("inf")
    se = diff.std(ddof=1) / np.sqrt(n) if n > 1 else 0.0
    ci = stats.t.interval(0.95, df=n - 1, loc=diff.mean(), scale=se) if n > 1 and se > 0 else (diff.mean(), diff.mean())

    return {
        "Comparison": f"{name_a} vs. {name_b}", "N": n, "Mean Diff (pp)": round(float(diff.mean()), 2),
        "95% CI Low": round(float(ci[0]), 2), "95% CI High": round(float(ci[1]), 2),
        "Paired t-stat": round(float(t_stat), 3), "Paired t p-value": round(float(p_ttest), 4),
        "Sign test p-value": round(float(sign_p), 4), "Cohen's d": round(float(cohens_d), 3),
        "All seeds agree in direction": bool(n_positive == n or n_positive == 0),
    }


__all__ = ["paired_analysis", "save_experiment_manifest", "compute_dataset_hash", "verify_dataset_hash"]
