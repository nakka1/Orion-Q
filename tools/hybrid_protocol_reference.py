"""Independent, standard-library reference for conditional BBM92 processing.

This module imports no OrionQ physics, processing, NumPy or SciPy code. Matching
is a bipartite graph: a coincidence is admitted only if both vertices have
degree one. The finite-sample deviation is found by inverting the logarithm of
the Serfling tail in Tomamichel & Leverrier, arXiv:1506.08458v3, Eq. 76.
Toeplitz hashing uses exact GF(2) row dot products, never floating-point FFT.

These component references do not establish a composable mission security
proof. In particular, a tail failure probability epsilon corresponds to a
smoothing parameter sqrt(epsilon), not epsilon itself (paper Eq. 79).
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right

REFERENCE = {
    "title": "A largely self-contained and complete security proof for quantum key distribution",
    "authors": "Marco Tomamichel and Anthony Leverrier",
    "url": "https://arxiv.org/html/1506.08458",
    "version": "arXiv:1506.08458v3, 2017-07-10; Quantum 1, 14 (2017)",
    "checked_on": "2026-10-10",
    "scope": "Eq. 76 sampling tail; Eq. 79 smoothing distinction; Eq. 90 leftover hashing",
}


def binary_entropy(probability):
    if not 0 <= probability <= 1:
        raise ValueError("a probability in [0,1] is required")
    if probability in (0, 1):
        return 0.0
    return -(probability * math.log(probability) +
             (1 - probability) * math.log1p(-probability)) / math.log(2)


def serfling_log_tail(n, k, deviation):
    """Log probability from Eq. 76 for a uniform sample without replacement."""
    if n <= 0 or k <= 0 or deviation < 0:
        raise ValueError("positive population/sample and nonnegative deviation required")
    return -2 * deviation**2 * n * k**2 / ((n + k) * (k + 1))


def finite_reference(n, k, x_errors, leakage, verification_bits, parameters=None):
    """Numerical budget check under the implementation's explicit hypotheses.

    This checks arithmetic, not the hypotheses, basis randomness, source trust,
    classical authentication budget or an overall composable epsilon allocation.
    """
    p = parameters or {}
    if any(type(v) is not int or v < 0 for v in (n, k, x_errors, leakage, verification_bits)) or x_errors > k:
        raise ValueError("nonnegative integer counts and errors <= sample required")
    eps = p.get("parameter_estimation_error", 1e-10)
    pa = p.get("privacy_amplification_error", 1e-10)
    if not 0 < eps < 1 or not 0 < pa < 1:
        raise ValueError("probabilities must lie strictly between zero and one")
    if n == 0 or k < p.get("min_parameter_sample", 20):
        return {"phase_error_upper": 0.5, "sampling_deviation": None, "budget_bits": 0,
                "abort_reason": "insufficient_parameter_sample"}
    # Independent numerical inversion instead of copying the closed-form root.
    target = math.log(eps)
    lower, upper = 0.0, 1.0
    while serfling_log_tail(n, k, upper) > target:
        upper *= 2
    for _ in range(100):
        middle = (lower + upper) / 2
        if serfling_log_tail(n, k, middle) > target:
            lower = middle
        else:
            upper = middle
    deviation = (lower + upper) / 2
    phase = min(0.5, x_errors / k + deviation)
    # Conservatively omit the favorable +2 constant permitted by Eq. 90.
    budget = max(0, math.floor(n * (1 - binary_entropy(phase)) - leakage -
                               verification_bits + 2 * math.log(pa) / math.log(2)))
    return {"phase_error_upper": phase, "sampling_deviation": deviation,
            "budget_bits": budget,
            "sampling_failure_probability": eps, "smoothing_parameter": math.sqrt(eps),
            "abort_reason": "" if budget else "finite_key_budget_nonpositive"}


def toeplitz_gf2(bits, seed, length):
    """Exact Toeplitz matrix product, T[i,j] = seed[n-1+i-j], over GF(2)."""
    bits, seed = list(bits), list(seed)
    if type(length) is not int or length < 0 or len(seed) != len(bits) + length - 1:
        raise ValueError("seed must contain n+m-1 binary entries")
    if any(v not in (0, 1) for v in bits + seed):
        raise ValueError("binary inputs required")
    if length == 0:
        return []
    if not bits:
        raise ValueError("nonempty input required")
    # Python integer bit_count evaluates the row dot product exactly. The row
    # mask is shifted once per output bit; no OrionQ/SciPy implementation used.
    vector = sum(int(v) << j for j, v in enumerate(reversed(bits)))
    row = sum(int(v) << j for j, v in enumerate(seed[:len(bits)]))
    result = []
    for i in range(length):
        result.append((vector & row).bit_count() & 1)
        if i + 1 < length:
            row = (row >> 1) | (int(seed[len(bits) + i]) << (len(bits) - 1))
    return result


def aligned_tags(batch, reference_epoch):
    """Pure integer epoch/clock/flight correction; reject wrap before matching."""
    shift = (batch["epoch_utc_s"] - reference_epoch[0]) * 10**12
    shift += batch["epoch_fraction_ps"] - reference_epoch[1] - batch["clock_offset_ps"]
    if not -(2**63) <= shift < 2**63:
        raise ValueError("alignment exceeds signed 64-bit range")
    delays = batch.get("flight_time_ps", [batch["propagation_delay_ps"]] * len(batch["time_ps"]))
    rows = []
    for stamp, detector, delay in zip(batch["time_ps"], batch["detector"], delays):
        value = stamp - delay + shift
        if not -(2**63) <= value < 2**63:
            raise ValueError("aligned timestamp exceeds signed 64-bit range")
        rows.append((value, detector))
    return sorted(rows)


def observation_reference(raw, parameters):
    """Build a coincidence graph, sift bases and compute observed error counts."""
    epoch = (raw["tdc"][0]["epoch_utc_s"], raw["tdc"][0]["epoch_fraction_ps"])
    duration = raw["end_s"] - raw["start_s"]
    maximum = round(duration * 10**12)
    gate = round(parameters.get("coincidence_window_s", 1e-9) * 10**12 / 2)
    streams = []
    for batch, track in zip(raw["tdc"], raw["tracking"]):
        windows = [(round((a - raw["start_s"]) * 10**12),
                    round((b - raw["start_s"]) * 10**12)) for a, b in track["open_intervals_s"]]
        streams.append([(t, d) for t, d in aligned_tags(batch, epoch)
                        if track["locked"] and 0 <= t < maximum and any(a <= t < b for a, b in windows)])
    left, right = streams
    times_left = [r[0] for r in left]
    times_right = [r[0] for r in right]
    # Degree lookup by interval bisection is a sparse adjacency-matrix query,
    # independently structured from the production two-pointer traversal.
    left_ranges = [(bisect_left(times_right, t - gate), bisect_right(times_right, t + gate)) for t in times_left]
    right_ranges = [(bisect_left(times_left, t - gate), bisect_right(times_left, t + gate)) for t in times_right]
    matches = []
    for i, (first, last) in enumerate(left_ranges):
        if last - first == 1 and right_ranges[first] == (i, i + 1):
            matches.append((i, first))
    z, x, residuals = [], [], []
    for i, j in matches:
        a, b = left[i][1], right[j][1]
        residuals.append(left[i][0] - right[j][0])
        if a // 2 == b // 2:
            (z if a // 2 == 0 else x).append((a % 2, b % 2))
    errors_z = sum(a != b for a, b in z)
    errors_x = sum(a != b for a, b in x)
    sifted = len(z) + len(x)
    return {"coincidences": len(matches), "sifted_bits": sifted,
            "key_basis_bits": len(z), "parameter_sample_bits": len(x),
            "qber": (errors_x + errors_z) / sifted if sifted else None,
            "qber_z": errors_z / len(z) if z else None, "qber_x": errors_x / len(x) if x else None,
            "z_errors": errors_z, "x_errors": errors_x, "z_pairs": z, "x_pairs": x,
            "residual_rms_ps": math.sqrt(sum(v * v for v in residuals) / len(residuals)) if residuals else None,
            "accepted_indices": matches, "usable_tags": [len(left), len(right)],
            "graph_ambiguous_vertices": sum(b - a > 1 for a, b in left_ranges + right_ranges)}
