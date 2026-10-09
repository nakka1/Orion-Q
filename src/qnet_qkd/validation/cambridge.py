"""The phase-5 physics against a deployed network: the Cambridge Quantum Network.

Dynes et al. (2019), *npj Quantum Information* 5:101, published the data behind
their figures under CC-BY 4.0 (doi:10.17863/CAM.44524).  Two of those files are
measurements a model in this package makes a prediction about:

``2b_1.txt``
    575 daily snapshots, Nov 2016 – Jul 2018, of the secure key rate and the
    QBER of one link (the CAPE–TREL span: its mean rate, 2.67 Mbit/s over the
    days present, matches the 2.58 Mbit/s the paper quotes for that link over
    all days, outages included).  A decoy-state BB84 system, GHz-clocked,
    room-temperature InGaAs detectors.

``4b.txt``
    QBER against the received power of a 100 Gbit/s classical channel sharing
    the fibre — six points, with error bars.

What these data can test, and what they cannot, is decided by what they
contain.  They contain no per-intensity gains, no vacuum counts, and no
attacks: the attack detector, whose features are half decoy observables, has
nothing to be evaluated on here.  What *is* testable:

1. **The premise of the detector** — that a QBER threshold, calibrated to a
   false-alarm budget at commissioning, fires for reasons that are not an
   eavesdropper.  Every day in this series is benign, so every alarm is false.
   :func:`threshold_false_alarms` measures how many.

2. **The key-rate physics** — how much secret key one percentage point of QBER
   costs.  The absolute rate depends on receiver numbers the paper does not
   publish (dead time, internal loss, the finite-key block), but the
   *sensitivity* d ln R / d QBER is set almost entirely by the entropy terms of
   the GLLP bound, and is a prediction with very little freedom in it.
   :func:`rate_sensitivity` measures it three ways, because the naive
   regression over the whole series is confounded by slow changes in loss that
   move the rate without moving the QBER.

3. **The coexistence physics** — that Raman noise from a classical channel
   raises the QBER linearly in launched power (milliwatts, not decibels).
   :func:`coexistence_fit` tests the shape against the alternatives, and
   reports the magnitude as the effective noise coupling the data imply,
   because the magnitude depends on the filter and the gate, neither of which
   the paper gives.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.typing import NDArray

#: The span 2b_1 describes, as the paper gives it.
CAPE_TREL_LENGTH_KM = 10.6
CAPE_TREL_LOSS_DB = 3.9

#: Transmitter and receiver, as the paper gives them.  The weak decoy of the
#: paper (~1e-4 photons) is treated as the vacuum state.
SIGNAL_INTENSITY = 0.4
DECOY_INTENSITY = 0.1
DETECTOR_EFFICIENCY = 0.30
AFTERPULSE_PROBABILITY = 0.03


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def load_key_rate_series(path: str | Path) -> pd.DataFrame:
    """Read ``2b_1.txt``: timestamp, secure key rate, QBER.

    The file's header says "Quantum bit error rate (%)" and its values are
    fractions (0.0268, not 2.68) — the loader keeps them as fractions, which is
    what the rest of this package uses, and checks that they are.  The header
    also carries an empty column between the two quantities; it is dropped.

    Returns:
        Columns ``time`` (datetime), ``key_rate_bps``, ``qber`` (fraction),
        sorted by time.

    Raises:
        ValueError: if the QBER column looks like percent after all, or the
            file does not have the three expected quantities.
    """
    frame = pd.read_csv(path, sep="\t").dropna(axis=1, how="all")
    if frame.shape[1] != 3:
        raise ValueError(f"expected timestamp, rate and QBER; got {list(frame.columns)}")
    frame.columns = ["time", "key_rate_bps", "qber"]
    frame["time"] = pd.to_datetime(frame["time"], format="%Y %b %d %H:%M:%S")
    if frame["qber"].max() > 0.5:
        raise ValueError("QBER values above 0.5 — the column is in percent, "
                         "not the fraction this loader expects")
    return frame.sort_values("time").reset_index(drop=True)


def load_coexistence_sweep(path: str | Path) -> pd.DataFrame:
    """Read ``4b.txt``: classical receiver power, pre-FEC BER, QBER and its error.

    Returns:
        Columns ``received_dbm``, ``pre_fec_ber``, ``qber``, ``qber_error``
        (both fractions), and ``received_mw``.
    """
    frame = pd.read_csv(path, sep="\t").dropna(axis=1, how="all")
    if frame.shape[1] != 4:
        raise ValueError(f"expected four columns; got {list(frame.columns)}")
    frame.columns = ["received_dbm", "pre_fec_ber", "qber", "qber_error"]
    frame["received_mw"] = 10.0 ** (frame["received_dbm"] / 10.0)
    return frame


# ---------------------------------------------------------------------------
# 1. the QBER threshold on benign data
# ---------------------------------------------------------------------------

@dataclass
class ThresholdResult:
    """How a commissioning-calibrated QBER threshold fares on a benign series.

    Attributes:
        baseline_days: rows the threshold was calibrated on.
        target: false-alarm budget it was calibrated to.
        threshold: the resulting QBER level, as a fraction.
        alarm_rate: share of subsequent days above it.
        episodes: separate runs of alarm days — what an operator gets as
            tickets.
        days: subsequent days scored.
    """

    baseline_days: int
    target: float
    threshold: float
    alarm_rate: float
    episodes: int
    days: int

    @property
    def excess(self) -> float:
        """How many times over budget the alarm rate ran."""
        return self.alarm_rate / self.target if self.target > 0 else float("inf")


def threshold_false_alarms(series: pd.DataFrame, baseline_days: int = 60,
                           target: float = 0.01) -> ThresholdResult:
    """Calibrate the deployed rule at commissioning and count what it flags after.

    The rule is the one :class:`~qnet_autopilot.qkd.detection.QberThreshold`
    implements: score each period as its QBER's distance from the baseline mean
    in baseline standard deviations, and set the alarm level at the
    ``1 - target`` quantile of the baseline's own scores.  So on the baseline
    the rule fires exactly at budget by construction; everything after is what
    it does once the link has been left to run.

    Args:
        series: from :func:`load_key_rate_series`.
        baseline_days: rows taken as the commissioning period.
        target: false-alarm budget, as a share of periods.

    Returns:
        ThresholdResult.

    Raises:
        ValueError: if the baseline leaves nothing to score.
    """
    if baseline_days >= len(series):
        raise ValueError("the baseline covers the whole series; nothing to score")

    baseline = series["qber"].iloc[:baseline_days]
    after = series["qber"].iloc[baseline_days:]
    mean, spread = float(baseline.mean()), float(baseline.std())
    sigma = float(np.quantile((baseline - mean) / spread, 1.0 - target))
    level = mean + sigma * spread

    alarm = (after > level).to_numpy()
    onsets = int(np.count_nonzero(alarm & ~np.concatenate([[False], alarm[:-1]])))
    return ThresholdResult(baseline_days=baseline_days, target=target,
                           threshold=level, alarm_rate=float(alarm.mean()),
                           episodes=onsets, days=len(after))


# ---------------------------------------------------------------------------
# 2. how much key a point of QBER costs
# ---------------------------------------------------------------------------

@dataclass
class Slope:
    """A fitted d ln R / d QBER with its uncertainty.

    Attributes:
        value: the slope, per unit QBER (so -30 means one percentage point of
            QBER costs 1 - exp(-0.30) = 26% of the key).
        half_width: half the 95% interval.
        rows: points it was fitted on.
        r_squared: of the fit.
        label: what was fitted.
    """

    value: float
    half_width: float
    rows: int
    r_squared: float
    label: str

    @property
    def cost_per_point(self) -> float:
        """Share of the key lost per percentage point of QBER."""
        return float(1.0 - np.exp(self.value / 100.0))


def _ols_slope(x: NDArray[np.float64], y: NDArray[np.float64], label: str) -> Slope:
    coefficients = np.polyfit(x, y, 1)
    residual = y - np.polyval(coefficients, x)
    spread = np.sum((x - x.mean()) ** 2)
    error = float(np.sqrt(residual.var(ddof=2) / spread)) if spread > 0 else float("inf")
    r_squared = float(1.0 - residual.var() / y.var()) if y.var() > 0 else 0.0
    return Slope(value=float(coefficients[0]), half_width=1.96 * error,
                 rows=len(x), r_squared=r_squared, label=label)


@dataclass
class RateSensitivity:
    """d ln R / d QBER in the data, three ways, and in the model.

    Attributes:
        whole: over every day — confounded by loss changes that move the rate
            without the QBER, so it is biased toward zero and reported to show
            the bias, not as the answer.
        within_month: after removing each calendar month's mean from both
            quantities — slow loss and efficiency drift largely cancel.
        episode: over a single, clean excursion where the QBER ramped for three
            weeks and was then corrected — a natural experiment in which the
            error moved and the loss, as far as the rate can tell, did not.
        model: the same slope from :mod:`qnet_autopilot.physics.decoy` at the
            paper's operating point, varying only the misalignment.
        model_by_inefficiency: the model slope at several error-correction
            inefficiencies ``f`` — the one unpublished number the slope is
            sensitive to.
    """

    whole: Slope
    within_month: Slope
    episode: Slope
    model: float
    model_by_inefficiency: dict[float, float] = field(default_factory=dict)


def rate_sensitivity(series: pd.DataFrame,
                     episode: tuple[str, str] = ("2017-08-22", "2017-09-12"),
                     inefficiencies: tuple[float, ...] = (1.0, 1.05, 1.10, 1.16, 1.20)
                     ) -> RateSensitivity:
    """How steeply the key rate falls with the QBER, measured and predicted.

    Args:
        series: from :func:`load_key_rate_series`.
        episode: the clean excursion, as inclusive ISO dates.
        inefficiencies: error-correction inefficiencies to evaluate the model at.

    Returns:
        RateSensitivity.
    """
    rate = np.log(series["key_rate_bps"].to_numpy())
    qber = series["qber"].to_numpy()
    whole = _ols_slope(qber, rate, "série inteira")

    month = series["time"].dt.to_period("M")
    centred_q = qber - series.groupby(month)["qber"].transform("mean").to_numpy()
    centred_r = rate - pd.Series(rate).groupby(month.to_numpy()).transform("mean").to_numpy()
    within = _ols_slope(centred_q, centred_r, "intra-mês")

    selector = ((series["time"] >= episode[0])
                & (series["time"] <= pd.Timestamp(episode[1]) + pd.Timedelta(days=1)))
    clean = _ols_slope(qber[selector.to_numpy()], rate[selector.to_numpy()],
                       f"rampa {episode[0]} a {episode[1]}")

    low, high = float(qber[selector.to_numpy()].min()), float(qber[selector.to_numpy()].max())
    by_f = {f: model_rate_sensitivity(f, (low, high)) for f in inefficiencies}
    from qnet_qkd.physics import constants as C
    model = model_rate_sensitivity(C.DEFAULT_ERROR_CORRECTION_INEFFICIENCY, (low, high))
    return RateSensitivity(whole=whole, within_month=within, episode=clean,
                           model=model, model_by_inefficiency=by_f)


def model_rate_sensitivity(inefficiency: float,
                           qber_range: tuple[float, float] = (0.029, 0.040),
                           dead_time_s: float = 20e-9) -> float:
    """d ln R / d QBER from the decoy-state physics at the paper's operating point.

    Only the misalignment moves; the span, the intensities, the detector and the
    clock are the paper's.  The slope is taken over ``qber_range`` so that it is
    compared with the data over the same interval — the GLLP rate is not
    exactly exponential in the error, and comparing slopes over different
    ranges would compare curvatures.
    """
    from qnet_qkd.physics.decoy import DecoySettings, decoy_key_rate_hz, observe
    from qnet_qkd.qkd.keyrate import receiver_response
    from qnet_qkd.qkd.session import LinkConditions

    settings = DecoySettings(signal_intensity=SIGNAL_INTENSITY,
                             decoy_intensity=DECOY_INTENSITY,
                             vacuum_probability=0.1, signal_probability=0.7,
                             clock_rate_hz=1.0e9, sifting_factor=0.5)
    errors, rates = [], []
    for misalignment in np.linspace(0.015, 0.050, 36):
        link = LinkConditions(length_km=CAPE_TREL_LENGTH_KM,
                              attenuation_db_per_km=CAPE_TREL_LOSS_DB / CAPE_TREL_LENGTH_KM,
                              excess_loss_db=0.0, coupling_efficiency=1.0,
                              detector_efficiency=DETECTOR_EFFICIENCY,
                              dark_count_rate_hz=1.0e3,
                              misalignment_error=float(misalignment))
        response = receiver_response(link, settings, None, dead_time_s,
                                     AFTERPULSE_PROBABILITY)
        rate = decoy_key_rate_hz(observe(response, settings), inefficiency)
        if rate > 0:
            errors.append(float(response.error_rate(SIGNAL_INTENSITY)))
            rates.append(rate)

    errors, rates = np.asarray(errors), np.log(np.asarray(rates))
    inside = (errors >= qber_range[0]) & (errors <= qber_range[1])
    if inside.sum() < 3:
        raise ValueError(f"the model does not cover QBER {qber_range}")
    return float(np.polyfit(errors[inside], rates[inside], 1)[0])


def degradation_days(series: pd.DataFrame, slope: float,
                     rate_drop: float = 0.25) -> pd.DataFrame:
    """Sort the bad days into the two physical kinds the simulator generates.

    A day whose rate is well below what its QBER predicts has lost *photons* —
    loss or efficiency, the ``loss_event`` class.  A day whose QBER is well
    above the series' median has lost *alignment* — the error-driven drift the
    ``thermal_drift`` class models.  The line that separates them is the
    measured rate–QBER slope, anchored at the series' median day.

    Args:
        series: from :func:`load_key_rate_series`.
        slope: d ln R / d QBER to predict the rate from the QBER with.
        rate_drop: how far below prediction (as a share) counts as a loss day.

    Returns:
        The series with ``predicted_bps``, ``shortfall`` and ``kind`` added;
        ``kind`` is ``"perda"``, ``"erro"`` or ``"normal"``.
    """
    frame = series.copy()
    anchor_q = float(frame["qber"].median())
    anchor_r = float(np.log(frame["key_rate_bps"]).median())
    predicted = np.exp(anchor_r + slope * (frame["qber"] - anchor_q))
    frame["predicted_bps"] = predicted
    frame["shortfall"] = 1.0 - frame["key_rate_bps"] / predicted

    spread = float(frame["qber"].std())
    error_day = frame["qber"] > anchor_q + 2.0 * spread
    loss_day = frame["shortfall"] > rate_drop
    frame["kind"] = np.where(loss_day, "perda", np.where(error_day, "erro", "normal"))
    return frame


# ---------------------------------------------------------------------------
# 3. coexistence
# ---------------------------------------------------------------------------

@dataclass
class CoexistenceFit:
    """Which functional form the QBER-against-power data support, and how strongly.

    Attributes:
        chi2_constant: no dependence on classical power (5 degrees of freedom).
        chi2_linear_mw: linear in milliwatts — the Raman form (4 dof).
        chi2_linear_db: linear in decibels — the wrong form, as a control (4 dof).
        p_constant, p_linear_mw, p_linear_db: the corresponding p-values.
        baseline: fitted QBER with no classical light.
        slope_per_mw: fitted QBER rise per milliwatt received.
        slope_error: its standard error.
        model_slope: the coexistence model's slope with its default hardware.
        implied_coupling: the product ``rho * filter_nm * gate_s`` the data
            imply, in the units of
            :class:`~qnet_autopilot.physics.coexistence.CoexistenceParameters`
            — the only combination of the three that the data can pin down.
        default_coupling: the same product at the model's defaults.
    """

    chi2_constant: float
    chi2_linear_mw: float
    chi2_linear_db: float
    p_constant: float
    p_linear_mw: float
    p_linear_db: float
    baseline: float
    slope_per_mw: float
    slope_error: float
    model_slope: float
    implied_coupling: float
    default_coupling: float


def _weighted_linear(x, y, weight):
    design = np.vstack([np.ones_like(x), x]).T
    normal = design.T @ (design * weight[:, None])
    beta = np.linalg.solve(normal, design.T @ (weight * y))
    covariance = np.linalg.inv(normal)
    chi2 = float(np.sum(weight * (y - design @ beta) ** 2))
    return beta, covariance, chi2


def coexistence_fit(sweep: pd.DataFrame, length_km: float = CAPE_TREL_LENGTH_KM,
                    loss_db: float = CAPE_TREL_LOSS_DB,
                    direction: str = "forward") -> CoexistenceFit:
    """Test the Raman form of the coexistence penalty, and size its magnitude.

    The received classical power is turned into launched power by adding the
    span's loss back; the model is then evaluated at the paper's signal level
    and detector efficiency, with its own default Raman coefficient, filter and
    gate.  The Raman counts are proportional to the product of those three, so
    the product is the one thing the data can pin down: it is found by solving
    for the Raman coefficient at which the model's slope equals the measured
    one — solved rather than scaled, because at the default product the
    background is *not* small against the signal and the rise is no longer
    linear in it.
    """
    from scipy import stats

    from qnet_qkd.physics.coexistence import (CoexistenceParameters,
                                                    coexistence_qber)

    weight = 1.0 / sweep["qber_error"].to_numpy() ** 2
    qber = sweep["qber"].to_numpy()
    power_mw = sweep["received_mw"].to_numpy()

    level = float(np.sum(weight * qber) / weight.sum())
    chi2_constant = float(np.sum(weight * (qber - level) ** 2))
    beta, covariance, chi2_mw = _weighted_linear(power_mw, qber, weight)
    _, _, chi2_db = _weighted_linear(sweep["received_dbm"].to_numpy(), qber, weight)

    from scipy.optimize import brentq

    defaults = CoexistenceParameters()
    signal = SIGNAL_INTENSITY * 10.0 ** (-loss_db / 10.0) * DETECTOR_EFFICIENCY
    launched_w = power_mw * 10.0 ** (loss_db / 10.0) * 1e-3

    def slope_at(raman: float) -> float:
        parameters = CoexistenceParameters(
            raman_coefficient_per_km_nm=raman,
            detector_efficiency=DETECTOR_EFFICIENCY,
            attenuation_db_per_km=loss_db / length_km,
            dark_count_rate_hz=1.0e3,
            optical_error_rate=float(beta[0]))
        predicted = coexistence_qber(signal, launched_w, length_km, 0.0,
                                     parameters, direction)
        return float(np.polyfit(power_mw, predicted, 1)[0])

    model_slope = slope_at(defaults.raman_coefficient_per_km_nm)
    fitted_raman = float(np.exp(brentq(
        lambda log_raman: slope_at(float(np.exp(log_raman))) - float(beta[1]),
        np.log(1e-14), np.log(1e-6))))

    coupling = (defaults.raman_coefficient_per_km_nm * defaults.filter_bandwidth_nm
                * defaults.detection_window_s)
    implied = fitted_raman * defaults.filter_bandwidth_nm * defaults.detection_window_s
    points = len(sweep)
    return CoexistenceFit(
        chi2_constant=chi2_constant, chi2_linear_mw=chi2_mw, chi2_linear_db=chi2_db,
        p_constant=float(stats.chi2.sf(chi2_constant, points - 1)),
        p_linear_mw=float(stats.chi2.sf(chi2_mw, points - 2)),
        p_linear_db=float(stats.chi2.sf(chi2_db, points - 2)),
        baseline=float(beta[0]), slope_per_mw=float(beta[1]),
        slope_error=float(np.sqrt(covariance[1, 1])),
        model_slope=model_slope,
        implied_coupling=implied,
        default_coupling=coupling)
