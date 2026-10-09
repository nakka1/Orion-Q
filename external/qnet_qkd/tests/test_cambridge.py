"""The Cambridge field-data checks: loaders and estimators, on data with known answers.

The real files are not committed to the test suite — they are someone else's
data, and a test that silently skips when they are absent tests nothing.  So
every estimator here is run on synthetic data built to have a known answer,
written in the *same file format* the repository publishes, and the loaders are
checked against that format's quirks: a QBER column labelled percent that holds
fractions, and an empty column in the header.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qnet_qkd.validation import cambridge as cb


def _write_series(path, qber, rate, start="2016-11-30"):
    times = pd.date_range(start, periods=len(qber), freq="D")
    lines = ['"Timestamp"\t"Secure bit rate (bps)"\t\t"Quantum bit error rate (%)"']
    for time, r, q in zip(times, rate, qber):
        lines.append(f"{time:%Y %b %d} 00:00:30\t{r:.6g}\t\t{q:.5f}")
    path.write_text("\n".join(lines) + "\n")
    return path


def _write_sweep(path, dbm, qber, error):
    lines = ['"Optical Receiver Power (dBm)"\t\t"Pre-FEC bit error rate"\t'
             '"Quantum bit error rate (%)"\t"Error Bar (%)"']
    for d, q, e in zip(dbm, qber, error):
        lines.append(f"{d}\t\t1E-5\t{q}\t{e}")
    path.write_text("\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------

def test_series_loader_reads_the_published_format(tmp_path):
    path = _write_series(tmp_path / "2b_1.txt", [0.027, 0.029], [2.5e6, 2.4e6])
    frame = cb.load_key_rate_series(path)
    assert list(frame.columns) == ["time", "key_rate_bps", "qber"]
    assert frame["qber"].iloc[0] == pytest.approx(0.027)
    assert frame["time"].iloc[1] - frame["time"].iloc[0] == pd.Timedelta(days=1)


def test_series_loader_refuses_percent(tmp_path):
    """A column that really were in percent would silently scale every result."""
    path = _write_series(tmp_path / "2b_1.txt", [2.7, 2.9], [2.5e6, 2.4e6])
    with pytest.raises(ValueError, match="percent"):
        cb.load_key_rate_series(path)


def test_sweep_loader_converts_to_milliwatts(tmp_path):
    path = _write_sweep(tmp_path / "4b.txt", [0.0, 10.0], [0.024, 0.025], [4e-4, 4e-4])
    frame = cb.load_coexistence_sweep(path)
    assert frame["received_mw"].tolist() == pytest.approx([1.0, 10.0])


# ---------------------------------------------------------------------------
# 1. the threshold
# ---------------------------------------------------------------------------

def test_threshold_is_at_budget_on_a_stationary_series():
    """If nothing changes after commissioning, the rule fires about at budget."""
    generator = np.random.default_rng(0)
    qber = 0.027 + 0.001 * generator.standard_normal(4000)
    series = pd.DataFrame({"time": pd.date_range("2020-01-01", periods=4000),
                           "key_rate_bps": 2.5e6, "qber": qber})
    result = cb.threshold_false_alarms(series, baseline_days=2000, target=0.05)
    assert result.alarm_rate == pytest.approx(0.05, abs=0.015)


def test_threshold_counts_a_drift_it_was_not_calibrated_on():
    """A step after commissioning is what the real series does, and it is caught."""
    generator = np.random.default_rng(1)
    qber = 0.027 + 0.0005 * generator.standard_normal(200)
    qber[100:150] += 0.005
    series = pd.DataFrame({"time": pd.date_range("2020-01-01", periods=200),
                           "key_rate_bps": 2.5e6, "qber": qber})
    result = cb.threshold_false_alarms(series, baseline_days=60, target=0.01)
    assert result.alarm_rate > 0.3
    assert result.episodes >= 1
    assert result.excess > 10


def test_threshold_refuses_a_baseline_with_nothing_after():
    series = pd.DataFrame({"time": pd.date_range("2020-01-01", periods=10),
                           "key_rate_bps": 1.0, "qber": 0.02})
    with pytest.raises(ValueError):
        cb.threshold_false_alarms(series, baseline_days=10)


# ---------------------------------------------------------------------------
# 2. the rate sensitivity
# ---------------------------------------------------------------------------

def test_within_month_removes_a_confounding_loss_drift():
    """The reason the estimator exists: slow loss drift biases the naive slope."""
    generator = np.random.default_rng(2)
    days = 540
    times = pd.date_range("2016-12-01", periods=days)
    month = np.asarray(times.month + 12 * times.year)
    # QBER and loss both have a slow monthly component that is *correlated*,
    # plus a fast daily component that is not.
    slow = np.sin(month / 3.0)
    qber = 0.028 + 0.002 * slow + 0.001 * generator.standard_normal(days)
    loss = 0.15 * slow
    rate = np.exp(np.log(2.5e6) - 30.0 * (qber - 0.028) + loss
                  + 0.01 * generator.standard_normal(days))
    series = pd.DataFrame({"time": times, "key_rate_bps": rate, "qber": qber})

    whole = cb._ols_slope(qber, np.log(rate), "whole")
    result = cb.rate_sensitivity(series, episode=("2017-02-01", "2017-03-15"),
                                 inefficiencies=(1.16,))
    assert abs(whole.value - (-30.0)) > 10.0          # the naive fit is biased
    assert result.within_month.value == pytest.approx(-30.0, abs=3.0)


def test_model_slope_steepens_with_the_error_correction_inefficiency():
    """f is the one unpublished number the prediction moves with."""
    shallow = cb.model_rate_sensitivity(1.0)
    steep = cb.model_rate_sensitivity(1.2)
    assert steep < shallow < 0


def test_cost_per_point_reads_the_slope_correctly():
    slope = cb.Slope(value=-30.0, half_width=1.0, rows=10, r_squared=0.9, label="")
    assert slope.cost_per_point == pytest.approx(1 - np.exp(-0.30))


def test_degradation_days_separates_loss_from_error():
    times = pd.date_range("2020-01-01", periods=40)
    qber = np.full(40, 0.027)
    rate = np.full(40, 2.5e6)
    rate[10] = 1.5e6                               # photons lost, QBER unchanged
    qber[20] = 0.040                               # alignment lost, rate on the line
    rate[20] = 2.5e6 * np.exp(-30.0 * 0.013)
    series = pd.DataFrame({"time": times, "key_rate_bps": rate, "qber": qber})
    kinds = cb.degradation_days(series, slope=-30.0)["kind"]
    assert kinds.iloc[10] == "perda"
    assert kinds.iloc[20] == "erro"
    assert (kinds.drop([10, 20]) == "normal").all()


# ---------------------------------------------------------------------------
# 3. coexistence
# ---------------------------------------------------------------------------

def test_coexistence_fit_prefers_milliwatts_on_raman_shaped_data(tmp_path):
    dbm = np.array([6.0, 3.0, 0.0, -5.0, -10.0, -15.0])
    qber = 0.024 + 0.0015 * 10 ** (dbm / 10)
    path = _write_sweep(tmp_path / "4b.txt", dbm, qber, np.full(6, 3e-4))
    fit = cb.coexistence_fit(cb.load_coexistence_sweep(path))
    assert fit.chi2_linear_mw < 1e-6
    assert fit.chi2_constant > fit.chi2_linear_db > fit.chi2_linear_mw
    assert fit.slope_per_mw == pytest.approx(0.0015, rel=1e-3)


def test_coexistence_coupling_is_solved_not_scaled(tmp_path):
    """The implied coupling must reproduce the measured slope through the model."""
    dbm = np.array([4.0, 0.0, -5.0, -10.0, -15.0])
    qber = 0.024 + 0.001 * 10 ** (dbm / 10)
    path = _write_sweep(tmp_path / "4b.txt", dbm, qber, np.full(5, 3e-4))
    fit = cb.coexistence_fit(cb.load_coexistence_sweep(path))
    assert 0 < fit.implied_coupling < fit.default_coupling
    assert fit.model_slope > fit.slope_per_mw
