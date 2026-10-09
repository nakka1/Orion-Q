"""
quantum_twin/evaluation/quantum.py
======================================

Re-exports quantum-physics validation/comparison utilities: fast-model
-vs-Aer-reference cross-validation for purification (channel and swapping
comparisons live alongside their respective modules in `quantum_twin.quantum`,
re-exported here too for discoverability under `evaluation`).
"""
from quantum_twin.quantum.purification import compare_analytical_vs_density_matrix

__all__ = ["compare_analytical_vs_density_matrix"]
