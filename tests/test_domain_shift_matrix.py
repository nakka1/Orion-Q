"""
tests/test_domain_shift_matrix.py

Unit tests for run_domain_shift_matrix.py (TCC improvement item 5).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_domain_shift_matrix import MATRIX_REGIMES, REQUESTED_CELLS
from run_ou_domain_shift import REGIMES


def test_all_matrix_regimes_are_valid_defined_regimes():
    """Regression guard: every regime named in MATRIX_REGIMES must
    actually exist in run_ou_domain_shift.REGIMES -- a typo or drift
    between the two files would otherwise fail silently at runtime with
    a confusing KeyError deep inside training."""
    for regime_name in MATRIX_REGIMES:
        assert regime_name in REGIMES


def test_requested_cells_only_reference_valid_matrix_regimes():
    for train_regime, test_regime in REQUESTED_CELLS:
        assert train_regime in MATRIX_REGIMES
        assert test_regime in MATRIX_REGIMES


def test_requested_cells_include_the_exact_six_named_in_the_tcc_item():
    """Regression guard for the TCC item's exact requested matrix:
    A->A, A->B, A->C, B->A, B->C, C->A -- no more, no fewer, matching
    the item's own stated scope (not the full symmetric 9-cell matrix)."""
    expected = {
        ("A_ID_default", "A_ID_default"), ("A_ID_default", "B_drift_amplitude_2x"),
        ("A_ID_default", "C_correlation_time_2x"), ("B_drift_amplitude_2x", "A_ID_default"),
        ("B_drift_amplitude_2x", "C_correlation_time_2x"), ("C_correlation_time_2x", "A_ID_default"),
    }
    assert set(REQUESTED_CELLS) == expected


def test_diagonal_cell_a_to_a_is_present():
    """The A->A diagonal cell (the ID baseline every Delta_MAE is
    measured against) must be present in the requested cells."""
    assert ("A_ID_default", "A_ID_default") in REQUESTED_CELLS
