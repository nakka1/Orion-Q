"""Fixed-point inference: the number format, and the estimators built on it.

The tests are grouped by what they are protecting:

* the **format** — saturation must clamp rather than wrap, because a wrapped
  residual in a control loop is a sign inversion and not a small error, and the
  two rounding modes must actually differ in the direction they claim to;
* the **convergence** — a quantised estimator must approach its float original
  as the word grows, monotonically enough that a sweep over widths is
  interpretable.  A quantiser that was subtly wrong would still produce a
  smooth-looking table, so the limit is tested against the float model directly;
* the **substitutability** — the whole method rests on the quantised model
  dropping into the unmodified evaluation harness, so the interfaces
  :class:`LearnedSync` and :class:`AttackDetector` rely on are exercised rather
  than assumed;
* the **ROM** — the hex the RTL is elaborated with is the model's own
  coefficients, so the two's-complement encoding is checked against values whose
  literals can be written down by hand.
"""

from __future__ import annotations

import numpy as np
import pytest

from qnet_qkd.hardware.fixed_point import (FixedPointFormat, QuantisedRidge,
                                                 coefficient_rom, quantise_boosting,
                                                 quantise_ridge, split_format)
from qnet_qkd.qkd.detection import FEATURE_NAMES, fit_boosted
from qnet_qkd.qkd.timing import LearnedSync


# ---------------------------------------------------------------------------
# the format
# ---------------------------------------------------------------------------

def test_format_grid_and_range():
    """Q3.4 in eight bits: the resolution, the rails, and the description."""
    fmt = FixedPointFormat(word_bits=8, fraction_bits=4)
    assert fmt.resolution == pytest.approx(1.0 / 16.0)
    assert fmt.maximum == pytest.approx(127.0 / 16.0)
    assert fmt.minimum == pytest.approx(-8.0)
    assert fmt.describe() == "Q3.4 (8b)"


def test_format_saturates_rather_than_wraps():
    """The property the whole module exists to model."""
    fmt = FixedPointFormat(word_bits=8, fraction_bits=4)
    values = fmt.quantise([100.0, -100.0, 1.0])
    assert values[0] == pytest.approx(fmt.maximum)
    assert values[1] == pytest.approx(fmt.minimum)
    assert values[2] == pytest.approx(1.0)
    assert fmt.saturations == 2
    assert fmt.reset().saturations == 0


def test_rounding_modes_differ_in_the_direction_they_claim():
    """Round is symmetric about zero; truncate walks toward minus infinity."""
    rounder = FixedPointFormat(word_bits=8, fraction_bits=2)
    truncator = FixedPointFormat(word_bits=8, fraction_bits=2, rounding="truncate")

    assert rounder.quantise(0.3)[()] == pytest.approx(0.25)
    assert rounder.quantise(-0.3)[()] == pytest.approx(-0.25)
    assert rounder.quantise(0.125)[()] == pytest.approx(0.25)   # half away from zero
    assert rounder.quantise(-0.125)[()] == pytest.approx(-0.25)

    assert truncator.quantise(0.3)[()] == pytest.approx(0.25)
    assert truncator.quantise(-0.3)[()] == pytest.approx(-0.5)

    # The bias is the reason the mode is swept rather than assumed.
    sample = np.linspace(-1.0, 1.0, 401)
    assert abs(np.mean(rounder.quantise(sample) - sample)) < 1e-3
    assert np.mean(truncator.quantise(sample) - sample) < -0.05


def test_format_rejects_nonsense():
    with pytest.raises(ValueError):
        FixedPointFormat(word_bits=0)
    with pytest.raises(ValueError):
        FixedPointFormat(rounding="nearest-even")


def test_split_format_places_the_point_above_the_peak():
    """Everything in the data must be representable without saturating."""
    values = np.array([-3.4, 0.2, 7.9])
    fmt = split_format(16, values)
    assert fmt.maximum > values.max()
    assert fmt.minimum < values.min()
    fmt.quantise(values)
    assert fmt.saturations == 0


# ---------------------------------------------------------------------------
# the ridge
# ---------------------------------------------------------------------------

def _fitted_ridge(seed: int = 0, features: int = 9):
    from sklearn.linear_model import Ridge

    generator = np.random.default_rng(seed)
    matrix = generator.normal(size=(600, features))
    targets = matrix @ generator.normal(size=features) + 0.25
    return Ridge(alpha=1.0).fit(matrix, targets), matrix


def test_quantised_ridge_converges_on_the_float_model():
    """Four bits of word buy about four bits of agreement, and the limit is exact."""
    model, matrix = _fitted_ridge()
    reference = model.predict(matrix)

    errors = []
    for word_bits in (8, 12, 16, 20, 24):
        quantised = quantise_ridge(model, matrix, word_bits=word_bits)
        errors.append(float(np.max(np.abs(quantised.predict(matrix) - reference))))

    assert errors == sorted(errors, reverse=True)
    assert errors[-1] < 1e-5
    assert errors[0] > errors[-1] * 100


def test_quantised_ridge_output_lies_on_the_accumulator_grid():
    """Bit-accurate means the answer is representable, not merely close."""
    model, matrix = _fitted_ridge()
    quantised = quantise_ridge(model, matrix, word_bits=12)
    predicted = quantised.predict(matrix[:32])
    codes = predicted / quantised.accumulator_format.resolution
    assert np.allclose(codes, np.round(codes))


def test_quantised_ridge_checks_its_feature_count():
    model, matrix = _fitted_ridge()
    quantised = quantise_ridge(model, matrix, word_bits=12)
    with pytest.raises(ValueError, match="coefficients"):
        quantised.predict(matrix[:, :3])


def test_quantised_ridge_substitutes_into_learned_sync():
    """The interface the whole comparison rests on."""
    model, matrix = _fitted_ridge(features=9)
    quantised = quantise_ridge(model, matrix, word_bits=14)
    predictor = LearnedSync(model=quantised, window=8, scale=1.0e-9)
    predictor.reset()

    offsets = np.linspace(0.0, 4.0e-9, 20)
    for step, offset in enumerate(offsets):
        prediction = predictor.predict(float(step), float(offset), 21.0, 2.0)
        assert np.isfinite(prediction)


def test_truncation_costs_more_than_rounding_at_the_same_width():
    """The trade the sweep exists to quantify, on the estimator itself."""
    model, matrix = _fitted_ridge()
    reference = model.predict(matrix)
    rounded = quantise_ridge(model, matrix, word_bits=8, rounding="round")
    truncated = quantise_ridge(model, matrix, word_bits=8, rounding="truncate")

    assert (np.mean(np.abs(truncated.predict(matrix) - reference))
            > np.mean(np.abs(rounded.predict(matrix) - reference)))


def test_coefficient_rom_encodes_twos_complement():
    """The literals the RTL is elaborated with."""
    quantised = QuantisedRidge(
        coefficients=np.array([1.0, -1.0, 0.0]), intercept=0.0,
        input_format=FixedPointFormat(word_bits=8, fraction_bits=4),
        coefficient_format=FixedPointFormat(word_bits=8, fraction_bits=4),
        accumulator_format=FixedPointFormat(word_bits=16, fraction_bits=8))
    literals = coefficient_rom(quantised, radix=16)
    assert literals[0] == "8'h10"    # +1.0 in Q3.4
    assert literals[1] == "8'hf0"    # -1.0, two's complement
    assert literals[2] == "8'h00"
    assert coefficient_rom(quantised, radix=2)[1] == "8'b11110000"
    with pytest.raises(ValueError):
        coefficient_rom(quantised, radix=10)


# ---------------------------------------------------------------------------
# the trees
# ---------------------------------------------------------------------------

def _fitted_boosting(seed: int = 0):
    generator = np.random.default_rng(seed)
    rows = 1200
    matrix = generator.normal(size=(rows, len(FEATURE_NAMES)))
    labels = np.where(matrix[:, 0] + 0.5 * matrix[:, 1] > 0.7, "pns",
                      np.where(matrix[:, 2] > 0.5, "thermal_drift", "normal"))
    mask = np.zeros(rows, dtype=bool)
    mask[:900] = True
    return fit_boosted(matrix, labels, mask, iterations=30, seed=seed), matrix, mask


def test_quantised_boosting_agrees_with_the_float_ensemble_when_wide():
    """A wide word must reproduce the float decision on essentially every row."""
    detector, matrix, mask = _fitted_boosting()
    reference = detector.predict(matrix[~mask])
    quantised = quantise_boosting(detector, matrix[mask], word_bits=20)
    predicted = quantised.predict(detector.standardiser.transform(matrix[~mask]))
    assert float(np.mean(predicted == reference)) > 0.99


def test_quantised_boosting_agreement_grows_with_the_word():
    """The curve the sweep reports has to be monotone enough to read."""
    detector, matrix, mask = _fitted_boosting()
    reference = detector.predict(matrix[~mask])
    scaled = detector.standardiser.transform(matrix[~mask])

    agreement = []
    for word_bits in (6, 10, 16, 20):
        quantised = quantise_boosting(detector, matrix[mask], word_bits=word_bits)
        agreement.append(float(np.mean(quantised.predict(scaled) == reference)))
    assert agreement == sorted(agreement)


def test_quantised_boosting_probabilities_are_a_distribution():
    """``AttackDetector`` reads a probability off this; it has to be one."""
    detector, matrix, mask = _fitted_boosting()
    quantised = quantise_boosting(detector, matrix[mask], word_bits=16)
    proba = quantised.predict_proba(detector.standardiser.transform(matrix[~mask]))
    assert proba.shape == (int((~mask).sum()), len(detector.classes))
    assert np.allclose(proba.sum(axis=1), 1.0)
    assert (proba >= 0.0).all()


def test_quantised_boosting_substitutes_into_the_detector():
    """Swapping the model must leave the detector's own machinery working."""
    from copy import deepcopy

    detector, matrix, mask = _fitted_boosting()
    candidate = deepcopy(detector)
    candidate.model = quantise_boosting(detector, matrix[mask], word_bits=16)

    predicted = candidate.predict(matrix[~mask])
    assert predicted.shape == (int((~mask).sum()),)
    assert set(predicted) <= set(detector.classes)
    assert candidate.attack_probability(matrix[~mask]).max() <= 1.0


def test_clipping_the_input_bus_trades_range_for_resolution():
    """A quantile-sized bus saturates outliers and buys fraction bits."""
    detector, matrix, mask = _fitted_boosting()
    peak = quantise_boosting(detector, matrix[mask], word_bits=10)
    clipped = quantise_boosting(detector, matrix[mask], word_bits=10,
                                input_quantile=0.99)
    assert (clipped.input_format.fraction_bits
            > peak.input_format.fraction_bits)

    # Clipped hard enough, the tail actually hits the rail — and the count is
    # what tells an engineer the bus was sized too tight rather than leaving it
    # to be discovered as a wrong answer.
    tight = quantise_boosting(detector, matrix[mask], word_bits=10,
                              input_quantile=0.5)
    tight.predict(detector.standardiser.transform(matrix[~mask]))
    assert tight.saturations > 0


def test_pruning_keeps_a_prefix_and_shrinks_the_memory():
    """Boosting is ordered, so a prefix is a model — and a much smaller one."""
    detector, matrix, mask = _fitted_boosting()
    full = quantise_boosting(detector, matrix[mask], word_bits=14)
    short = quantise_boosting(detector, matrix[mask], word_bits=14, rounds=5)

    assert short.rounds == 5
    assert full.rounds > short.rounds
    assert short.nodes < full.nodes
    assert short.depth <= full.depth
    assert "x5" in short.name

    # The prefix is the same trees, not re-fitted ones: round zero is identical.
    assert np.array_equal(full.trees[0][0]["threshold"],
                          short.trees[0][0]["threshold"])


def test_pruning_to_nothing_is_refused():
    detector, matrix, mask = _fitted_boosting()
    with pytest.raises(ValueError, match="at least one"):
        quantise_boosting(detector, matrix[mask], word_bits=14, rounds=0)


def test_raw_scores_back_the_probabilities():
    """The RTL is checked against the scores, so they have to be the real ones."""
    detector, matrix, mask = _fitted_boosting()
    quantised = quantise_boosting(detector, matrix[mask], word_bits=16)
    scaled = detector.standardiser.transform(matrix[~mask])

    scores = quantised.raw_scores(scaled)
    proba = quantised.predict_proba(scaled)
    assert scores.shape == proba.shape
    assert np.array_equal(np.argmax(scores, axis=1), np.argmax(proba, axis=1))

    # Every score lies on the accumulator's grid — bit-accurate, not merely close.
    codes = scores / quantised.accumulator_format.resolution
    assert np.allclose(codes, np.round(codes))


def test_depth_counts_the_longest_path():
    """The figure that says how many cycles one traversal costs."""
    detector, matrix, mask = _fitted_boosting()
    quantised = quantise_boosting(detector, matrix[mask], word_bits=14, rounds=3)
    assert quantised.depth >= 2
    assert quantised.depth <= quantised.nodes


def test_quantise_boosting_rejects_a_model_that_is_not_a_booster():
    from qnet_qkd.qkd.detection import fit_forest

    generator = np.random.default_rng(0)
    matrix = generator.normal(size=(200, len(FEATURE_NAMES)))
    labels = np.where(matrix[:, 0] > 0.0, "pns", "normal")
    mask = np.ones(200, dtype=bool)
    forest = fit_forest(matrix, labels, mask, trees=5)
    with pytest.raises(AttributeError, match="HistGradientBoosting"):
        quantise_boosting(forest, matrix, word_bits=12)
