"""Independent SNS decoy/finite-count reference; imports no Orion physics.

Jiang et al., arXiv:1904.00192v3, Eqs. 5 and 15--26. This is a numerical
qualification oracle, NOT an authenticated SNS protocol or a security certificate.
The input must contain the joint source strata and wrong-detector phase-slice
counts; an aggregate X QBER cannot be substituted for those observations.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.integrate import quad
from scipy.optimize import linprog
from scipy.special import i0

SOURCES = [
    {"id": "sns_original", "url": "https://arxiv.org/abs/1805.09222", "version": "v9",
     "year": 2018, "use": "SNS source/window definitions and asymptotic structure"},
    {"id": "sns_finite", "url": "https://arxiv.org/html/1904.00192v3", "version": "v3",
     "year": 2019, "use": "Eqs. 5, 15--26; Table 1 parameter reference"},
    {"id": "decoy_lp", "url": "https://arxiv.org/abs/quant-ph/0503005", "year": 2005,
     "use": "Poisson photon-number mixture and yield constraints; independent LP implementation"},
]


class SNSAssumptionError(ValueError):
    """The symmetric SNS estimator cannot establish its required assumptions."""


def probability(value, name="probability", *, exclusive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name}: finite real required")
    if value < 0 or value > 1 or exclusive and not 0 < value < 1:
        raise ValueError(f"{name}: outside probability range")
    return float(value)


def count(value, name="count"):
    if type(value) is not int or value < 0:
        raise ValueError(f"{name}: nonnegative integer required")
    return value


def row(value):
    n, k = count(value["trials"], "trials"), count(value["clicks"], "clicks")
    if n < 1 or k > n:
        raise ValueError("positive trials and bounded clicks required")
    return n, k


def entropy(p):
    probability(p)
    return 0.0 if p in (0.0, 1.0) else -p * math.log2(p) - (1 - p) * math.log2(1 - p)


def _bisect(f, lo, hi):
    """Monotone sign-changing root in count space, independent of backend code."""
    sign = f(lo)
    for _ in range(100):
        mid = (lo + hi) / 2
        current = f(mid)
        if (current > 0) == (sign > 0):
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def expected_count_interval(observed, trials, failure=1e-10):
    """Inverse multiplicative Chernoff, Eqs. 17--21; each interval failure <=xi.

    Solve mu - x + x log(x/mu) = log(2/xi). At x=0, the upper limit
    is log(2/xi), from Pr[X=0] <= exp(-mu), rather than an invalid 0/0.
    """
    count(observed)
    count(trials)
    if trials < 1 or observed > trials:
        raise ValueError("bounded observation and positive trials required")
    probability(failure, "failure", exclusive=True)
    threshold = math.log(2 / failure)
    if observed == 0:
        return 0.0, min(float(trials), threshold)

    def distance(mean):
        return mean - observed + observed * math.log(observed / mean) - threshold

    lower = _bisect(distance, max(1e-300, observed * 1e-15), float(observed))
    upper_search = max(observed * 2.0, observed + threshold * 2.0)
    while distance(upper_search) < 0:
        upper_search *= 2
    upper = _bisect(distance, float(observed), upper_search)
    return max(0.0, lower), min(float(trials), upper)


def observed_count_interval(mean, maximum, failure=1e-10):
    """Forward Chernoff, Eqs. 22--25; continuous count bounds before rounding."""
    count(maximum)
    probability(failure, "failure", exclusive=True)
    if not isinstance(mean, (int, float)) or not math.isfinite(mean) or not 0 <= mean <= maximum:
        raise ValueError("finite bounded expected count required")
    if mean == 0:
        return 0.0, 0.0
    threshold = math.log(2 / failure)

    def distance(observed):
        return mean if observed == 0 else observed * math.log(observed / mean) - observed + mean

    lower = 0.0 if mean <= threshold else _bisect(lambda x: distance(x) - threshold, 0.0, mean)
    upper_search = max(mean * 2, mean + threshold * 2)
    while distance(upper_search) < threshold:
        upper_search *= 2
    upper = _bisect(lambda x: distance(x) - threshold, mean, upper_search)
    return lower, min(float(maximum), upper)


def asymptotic_decoy_yield(q0, qweak, qstrong, weak_mu, strong_mu):
    """Eq. 15, symmetric one-arm source reduction; independent expression."""
    for q in (q0, qweak, qstrong):
        probability(q)
    if not 0 < weak_mu < strong_mu:
        raise ValueError("ordered positive decoy intensities required")
    numerator = strong_mu ** 2 * math.exp(weak_mu) * qweak
    numerator -= weak_mu ** 2 * math.exp(strong_mu) * qstrong
    numerator -= (strong_mu ** 2 - weak_mu ** 2) * q0
    return min(1.0, max(0.0, numerator / (weak_mu * strong_mu * (strong_mu - weak_mu))))


def poisson_yield_lp(intensities, gain_intervals, photon_cutoff=16):
    """Minimize Y1 with Poisson yield constraints and an adversarial tail.

    Truncated photon yields are [0,1]. Omitted probability may contribute any
    gain in [0, tail], hence lower-tail <= retained mixture <= upper.
    This is a different numerical algorithm, not a copy of Orion's estimator.
    """
    if len(intensities) != len(gain_intervals) or photon_cutoff < 2:
        raise ValueError("one interval per intensity and cutoff >=2 required")
    constraints, right = [], []
    for intensity, interval in zip(intensities, gain_intervals):
        if not isinstance(intensity, (int, float)) or not math.isfinite(intensity) or intensity < 0:
            raise ValueError("nonnegative finite intensity required")
        lo, hi = interval
        probability(lo)
        probability(hi)
        if lo > hi:
            raise ValueError("ordered gain interval required")
        weights = [math.exp(-intensity) * intensity ** n / math.factorial(n)
                   for n in range(photon_cutoff + 1)]
        tail = max(0.0, 1 - sum(weights))
        constraints.extend((weights, [-weight for weight in weights]))
        right.extend((hi, tail - lo))
    objective = np.zeros(photon_cutoff + 1)
    objective[1] = 1.0
    result = linprog(objective, A_ub=np.array(constraints), b_ub=np.array(right),
                     bounds=(0.0, 1.0), method="highs",
                     options={"primal_feasibility_tolerance": 1e-9, "dual_feasibility_tolerance": 1e-9})
    return {"feasible": bool(result.success), "yield_lower": float(result.fun) if result.success else None,
            "photon_cutoff": photon_cutoff, "message": result.message}


def finite_sns_estimate(observation, parameters):
    """Published symmetric SNS finite estimator on explicitly stratified fixtures.

    Failure allocation is deliberately conservative: six observed intervals and
    two forward intervals are reported separately; this implementation has not
    undergone composable-security review and exports no key material.
    """
    if observation.get("schema") != "orionq.sns-reference-observation.v1":
        raise ValueError("joint-source SNS reference schema required; aggregate X QBER is insufficient")
    assumptions = observation.get("assumptions", {})
    needed = ("symmetric_sources", "phase_randomized", "trusted_source_preparation",
              "independent_choices", "one_detector_heralding", "phase_slice_selection")
    if not all(assumptions.get(name) is True for name in needed):
        raise SNSAssumptionError("symmetric SNS proof assumptions are not established")
    weak, strong, signal = (parameters[name] for name in ("weak_mu", "strong_mu", "signal_mu"))
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
           for value in (weak, strong, signal)) or not 0 < weak < strong or signal <= 0:
        raise ValueError("ordered positive source intensities required")
    send = probability(parameters["send_probability"], "send_probability", exclusive=True)
    xi = probability(parameters.get("failure_probability", 1e-10), "failure_probability", exclusive=True)
    values, intervals = {}, {}
    for name in ("00", "01", "10", "02", "20"):
        n, k = row(observation["strata"][name])
        lo, hi = expected_count_interval(k, n, xi)
        values[name] = k / n
        intervals[name] = (lo / n, hi / n)
    numerator = strong ** 2 * math.exp(weak) * (intervals["01"][0] + intervals["10"][0])
    numerator -= weak ** 2 * math.exp(strong) * (intervals["02"][1] + intervals["20"][1])
    numerator -= 2 * (strong ** 2 - weak ** 2) * intervals["00"][1]
    lower_yield = max(0.0, min(1.0, numerator / (2 * weak * strong * (strong - weak))))
    phase = observation["phase_slice"]
    phase_n = count(phase["trials"])
    phase_k = count(phase["wrong_detector_clicks"])
    phase_clicks = count(phase["clicks"])
    delta = phase["delta_rad"]
    if (phase_n < 1 or phase_k > phase_clicks or phase_clicks > phase_n or isinstance(delta, bool)
            or not isinstance(delta, (int, float)) or not math.isfinite(delta) or not 0 < delta <= math.pi):
        raise ValueError("bounded wrong-detector phase-slice observations required")
    _, upper_t_count = expected_count_interval(phase_k, phase_n, xi)
    upper_t = upper_t_count / phase_n
    phase_expected = 0.5 if lower_yield == 0 else max(0.0, min(0.5,
        (upper_t - 0.5 * math.exp(-2 * weak) * intervals["00"][0]) /
        (2 * weak * math.exp(-2 * weak) * lower_yield)))
    nz, nt = row(observation["signal"])
    errors = count(observation["signal"]["errors"])
    if errors > nt:
        raise ValueError("signal errors exceed clicks")
    n1_trials = nz * 2 * send * (1 - send) * signal * math.exp(-signal)
    n1_expected = n1_trials * lower_yield
    n1_low, _ = observed_count_interval(n1_expected, nz, xi)
    n1_low = min(float(nt), n1_low)  # Untagged key bits cannot exceed all observed Z bits.
    _, phase_errors_upper = observed_count_interval(n1_expected * phase_expected, nz, xi)
    phase_observed = min(0.5, phase_errors_upper / n1_expected) if n1_expected else 0.5
    qber = errors / nt if nt else 0.5
    ec_efficiency = parameters.get("ec_efficiency", 1.1)
    if isinstance(ec_efficiency, bool) or not isinstance(ec_efficiency, (int, float)) or not math.isfinite(ec_efficiency) or ec_efficiency < 1:
        raise ValueError("finite error-correction efficiency >=1 required")
    leak = math.ceil(ec_efficiency * nt * entropy(qber))
    eps_cor = probability(parameters.get("eps_cor", xi), "eps_cor", exclusive=True)
    eps_pa = probability(parameters.get("eps_pa", xi), "eps_pa", exclusive=True)
    eps_hat = probability(parameters.get("eps_hat", xi), "eps_hat", exclusive=True)
    penalties = math.log2(2 / eps_cor) + 2 * math.log2(1 / (math.sqrt(2) * eps_pa * eps_hat))
    budget = max(0, math.floor(n1_low * (1 - entropy(phase_observed)) - leak - penalties))
    asym_yield = asymptotic_decoy_yield(values["00"], (values["01"] + values["10"]) / 2,
                                       (values["02"] + values["20"]) / 2, weak, strong)
    return {"scope": "numerical Eq. 5/15/16/17--26 reference, not a full protocol implementation",
            "single_photon_yield_lower": lower_yield, "asymptotic_yield_estimate": asym_yield,
            "phase_error_expected_upper": phase_expected, "phase_error_observed_upper": phase_observed,
            "untagged_bits_lower": math.floor(n1_low), "qber": qber, "ec_leak_bits": leak,
            "finite_security_penalty_bits": penalties, "conditional_budget_bits": budget,
            "gain_intervals": intervals, "statistical_intervals": 8, "statistical_union_bound": 8 * xi,
            "security_certified": False, "certified_secret_bits": 0}


def threshold_click_probability(mean_left, mean_right, dark_per_gate):
    """Exactly one click of two independent Poisson threshold detectors."""
    probability(dark_per_gate)
    if min(mean_left, mean_right) < 0:
        raise ValueError("nonnegative detected optical mean required")
    no_left = (1 - dark_per_gate) * math.exp(-mean_left)
    no_right = (1 - dark_per_gate) * math.exp(-mean_right)
    return no_left * (1 - no_right) + no_right * (1 - no_left)


def synthetic_observation(parameters, seed):
    """Independent ideal optical fixture, based on Poisson arrivals/beamsplitter.

    Table 1 contributes detector/fiber/error parameters. Distances, source
    probabilities and intensities are explicit campaign assumptions, not a fit
    or digitization of the paper's optimized key-rate curves. Counts use seeded
    binomial draws; final yields or key results are never injected as inputs.
    """
    rng = np.random.default_rng(seed)
    total = count(parameters["pulses"])
    pz, p0, p1 = (probability(parameters[name], name, exclusive=True) for name in ("pz", "p0", "p1"))
    if p0 + p1 >= 1:
        raise ValueError("p0+p1 must be <1")
    send = probability(parameters["send_probability"], "send_probability", exclusive=True)
    dark = probability(parameters["dark_per_gate"], "dark_per_gate")
    detection = probability(parameters["detector_efficiency"], "detector_efficiency")
    eta_a, eta_b = [detection * 10 ** (-parameters["attenuation_db_km"] * length / 10)
                    for length in parameters["arm_lengths_km"]]
    mu1, mu2, muz = (parameters[name] for name in ("weak_mu", "strong_mu", "signal_mu"))
    trials = {"00": int(((1 - pz) ** 2 * p0 ** 2 + 2 * (1 - pz) * pz * p0 * (1 - send)) * total)}
    for suffix, source_p in (("1", p1), ("2", 1 - p0 - p1)):
        n = int(((1 - pz) ** 2 * p0 * source_p + (1 - pz) * pz * (1 - send) * source_p) * total)
        trials["0" + suffix] = trials[suffix + "0"] = n
    gains = {"00": threshold_click_probability(0, 0, dark)}
    for suffix, mu in (("1", mu1), ("2", mu2)):
        gains["0" + suffix] = threshold_click_probability(mu * eta_b / 2, mu * eta_b / 2, dark)
        gains[suffix + "0"] = threshold_click_probability(mu * eta_a / 2, mu * eta_a / 2, dark)
    strata = {name: {"trials": n, "clicks": int(rng.binomial(n, gains[name]))} for name, n in trials.items()}
    delta = parameters["phase_slice_rad"]
    phase_trials = 2 * int(delta / (2 * math.pi) * (1 - pz) ** 2 * p1 ** 2 * total)
    error = probability(parameters["misalignment_error"], "misalignment_error")

    def phase_prob(phi, wrong_detector=True):
        total_mean = mu1 * (eta_a + eta_b)
        interference = 2 * mu1 * math.sqrt(eta_a * eta_b) * math.cos(phi)
        bright, faint = (total_mean + interference) / 2, (total_mean - interference) / 2
        wrong = (1 - dark) * math.exp(-bright) * (1 - (1 - dark) * math.exp(-faint))
        right = (1 - dark) * math.exp(-faint) * (1 - (1 - dark) * math.exp(-bright))
        return (1 - error) * wrong + error * right if wrong_detector else wrong + right

    wrong_gain = quad(phase_prob, -delta / 2, delta / 2, epsabs=1e-14)[0] / delta
    phase_gain = quad(lambda phi: phase_prob(phi, False), -delta / 2, delta / 2, epsabs=1e-14)[0] / delta
    phase_counts = rng.multinomial(phase_trials, [wrong_gain, phase_gain - wrong_gain, 1 - phase_gain])
    nz = int(pz ** 2 * total)
    category = rng.multinomial(nz, [(1 - send) ** 2, send * (1 - send), send * (1 - send), send ** 2])
    one_a = threshold_click_probability(muz * eta_a / 2, muz * eta_a / 2, dark)
    one_b = threshold_click_probability(muz * eta_b / 2, muz * eta_b / 2, dark)
    # Average exactly-one-click over random phase of the both-send contribution.
    total_mean = muz * (eta_a + eta_b)
    phase_amplitude = muz * math.sqrt(eta_a * eta_b)
    both = 2 * (1 - dark) * math.exp(-total_mean / 2) * float(i0(phase_amplitude))
    both -= 2 * (1 - dark) ** 2 * math.exp(-total_mean)
    counts = [int(rng.binomial(int(n), min(1.0, max(0.0, gain)))) for n, gain in
              zip(category, (gains["00"], one_a, one_b, both))]
    return {"schema": "orionq.sns-reference-observation.v1", "origin": "independent_synthetic_optical_fixture",
            "seed": seed, "strata": strata, "phase_slice": {"trials": phase_trials,
                "wrong_detector_clicks": int(phase_counts[0]), "clicks": int(phase_counts[0] + phase_counts[1]),
                "delta_rad": delta},
            "signal": {"trials": nz, "clicks": sum(counts), "errors": counts[0] + counts[3]},
            "assumptions": {"symmetric_sources": parameters["arm_lengths_km"][0] == parameters["arm_lengths_km"][1],
                "phase_randomized": True, "trusted_source_preparation": True, "independent_choices": True,
                "one_detector_heralding": True, "phase_slice_selection": True},
            "derived_physical_truth": {"arm_transmissions_including_detector": [eta_a, eta_b],
                "gains": gains, "phase_wrong_detector_gain": wrong_gain,
                "single_photon_yield": ((eta_a + eta_b) / 2) * (1 - dark) +
                    (1 - (eta_a + eta_b) / 2) * 2 * dark * (1 - dark)}}
