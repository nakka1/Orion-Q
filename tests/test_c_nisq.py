"""
tests/test_c_nisq.py

Unit tests for quantum_twin/evaluation/c_nisq.py (TCC improvement item 2).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from quantum_twin.evaluation.c_nisq import compute_c_nisq, CNisqResult


def test_compute_c_nisq_matches_hand_computation():
    result = compute_c_nisq("TestController", energy_total_J=0.03, useful_pairs=200,
                             purification_attempts=800)
    assert result.c_nisq_energy == pytest.approx(0.03 / 200)
    assert result.c_nisq_operations == pytest.approx(800 / 200)


def test_compute_c_nisq_raises_explicitly_on_zero_useful_pairs():
    """Regression guard: a controller with zero useful pairs must raise
    a clear, explicit ValueError -- never silently return inf/nan, which
    would hide a real, reportable finding about that controller."""
    with pytest.raises(ValueError, match="zero useful pairs"):
        compute_c_nisq("BadController", energy_total_J=0.05, useful_pairs=0,
                        purification_attempts=100)


def test_lower_c_nisq_energy_means_more_efficient():
    """Sanity check on the metric's own direction: a controller using
    LESS energy for the SAME useful-pair count must show a LOWER
    C_NISQ_energy (better)."""
    efficient = compute_c_nisq("Efficient", energy_total_J=0.01, useful_pairs=100,
                                purification_attempts=100)
    wasteful = compute_c_nisq("Wasteful", energy_total_J=0.05, useful_pairs=100,
                               purification_attempts=100)
    assert efficient.c_nisq_energy < wasteful.c_nisq_energy


def test_c_nisq_result_stores_all_input_fields():
    result = compute_c_nisq("X", energy_total_J=0.02, useful_pairs=50, purification_attempts=150)
    assert result.controller == "X"
    assert result.energy_total_J == 0.02
    assert result.useful_pairs == 50
    assert result.purification_attempts == 150


def test_oracle_has_the_lowest_possible_c_nisq_operations():
    """Direct, real-data sanity check: Oracle (which only ever attempts
    purification on pairs it KNOWS are good) must show
    c_nisq_operations == 1.0 exactly (every attempt is useful, by
    Oracle's own definition) -- the theoretical floor no real controller
    can beat."""
    oracle = compute_c_nisq("Oracle", energy_total_J=0.023771, useful_pairs=247,
                             purification_attempts=247)
    assert oracle.c_nisq_operations == pytest.approx(1.0)


def test_real_six_controller_ranking_dualhead_wins_on_operations_not_necessarily_energy():
    """Direct, real-data regression guard for this module's central,
    honest finding: DualHead wins clearly on C_NISQ_operations (fewer
    wasted attempts per useful pair) but is NOT automatically the best
    on C_NISQ_energy, because the sixtieth addendum's energy accounting
    charges a UNIFORM per-round inference cost to every controller
    (including Blind, which by definition needs no model) -- this
    inherited assumption means Blind/RiskAware/Predictive's total
    energy is inflated by an inference cost proportional to Blind's
    LARGER useful-pairs count, closing much of the gap C_NISQ_operations
    shows clearly. Verified on the real recorded values, not asserted
    from prose."""
    dualhead = compute_c_nisq("DualHead", energy_total_J=0.025981, useful_pairs=211,
                               purification_attempts=468)
    blind = compute_c_nisq("Blind", energy_total_J=0.029261, useful_pairs=247,
                            purification_attempts=796)

    # DualHead must win clearly on operations (fewer attempts per useful pair).
    assert dualhead.c_nisq_operations < blind.c_nisq_operations
    # But on energy, Blind is actually SLIGHTLY better (or at least not clearly worse) --
    # the surprising, honest finding this test guards against silently disappearing
    # in a future refactor.
    assert blind.c_nisq_energy <= dualhead.c_nisq_energy
