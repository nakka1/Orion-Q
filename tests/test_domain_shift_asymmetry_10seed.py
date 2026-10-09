"""
tests/test_domain_shift_asymmetry_10seed.py

Unit tests for run_domain_shift_matrix_10seed.py (TCC improvement item
4), directly guarding against the real seeding bug found and fixed
during development.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest

from run_domain_shift_matrix_10seed import run_one_seed, load_config, summarize


def test_run_one_seed_is_genuinely_deterministic():
    """Regression guard for the real seeding bug found and fixed during
    development: calling run_one_seed() twice with the SAME seed must
    produce BYTE-IDENTICAL results -- the bug (missing np.random.seed/
    torch.manual_seed calls) would have made this fail, since training
    -time randomness was previously uncontrolled by the seed parameter.
    Uses a SMALL, fast config (not this project's full production-scale
    settings) specifically so this determinism property can be verified
    quickly -- the property being tested (does the same seed give the
    same result) does not require full-scale training to demonstrate."""
    cfg = load_config("configs/config.yaml")
    light_cfg = {
        "dataset": {**cfg["dataset"], "n_steps": 300, "window_size": 10},
        "loss": cfg["loss"], "model": {"hidden_size": 8},
    }
    result1 = run_one_seed(42, light_cfg)
    result2 = run_one_seed(42, light_cfg)
    assert result1["diag_A_MAE"] == result2["diag_A_MAE"]
    assert result1["B_to_A_MAE"] == result2["B_to_A_MAE"]


@pytest.mark.skipif(sys.platform != "win32", reason="Golden gravado no Windows de referencia; o valor exato depende de libm/BLAS/torch da plataforma.")
def test_run_one_seed_matches_corrected_t1_t2_physics_golden():
    """Current deterministic golden after the joint T1/T2 channel fix."""
    cfg = load_config("configs/config.yaml")
    result = run_one_seed(42, cfg)
    assert abs(result["diag_A_MAE"] - 0.33388847) < 1e-4
    assert abs(result["B_to_A_MAE"] - 0.45822167) < 1e-4


def test_summarize_returns_expected_statistics():
    values = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
    result = summarize(values)
    assert set(result.keys()) == {"mean", "median", "std", "CI95_low", "CI95_high"}
    assert result["mean"] == 0.3
    assert result["CI95_low"] < result["mean"] < result["CI95_high"]
