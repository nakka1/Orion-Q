"""Bit-accurate fixed-point inference for the phase-5 estimators.

The format
----------

:class:`FixedPointFormat` is a two's-complement word of ``word_bits`` bits with
``fraction_bits`` of them below the binary point — the Qm.n an HDL engineer
would write down.  Two properties of real hardware are modelled and neither is
optional:

**Saturation.**  A fixed-point adder wraps.  Wrapping turns a large positive
residual into a large negative one, which in a control loop is not a small
error, it is a sign inversion.  Every implementation that matters clamps
instead, so the quantiser clamps, and :attr:`FixedPointFormat.saturations`
counts how often it had to — a count above zero on representative data means
the integer range is wrong, not that the model is.

**Rounding.**  ``round`` is round-half-away-from-zero, one adder and one bit
more than truncation.  ``truncate`` is free in hardware and biased toward
negative infinity; over a window of lagged differences that bias accumulates,
which is precisely why the mode is exposed rather than assumed.

The estimators
--------------

:class:`QuantisedRidge` and :class:`QuantisedBoosting` implement ``predict``
(and ``predict_proba``) with the signatures scikit-learn uses, because that is
what :class:`~qnet_autopilot.qkd.timing.LearnedSync` and
:class:`~qnet_autopilot.qkd.detection.AttackDetector` hold: both take
``model: object`` and call it.  A quantised model therefore substitutes for a
fitted one inside the *unmodified* evaluation harness, which is the only way
the comparison is worth anything — a separate quantised pipeline would be
comparing two pipelines, not two number formats.

What each one does with the format:

* the ridge quantises the inputs, the coefficients and the intercept, then
  accumulates the products in a wider accumulator and quantises once at the
  output.  That is the dataflow of a MAC over a shift register, which is what
  the RTL is;
* the boosting ensemble quantises the inputs and the split thresholds into the
  *same* format — so a comparison in hardware is a comparison of the integers
  that were actually stored — keeps the leaf ROM in a word sized on a single
  leaf, and accumulates the raw score in a wider one.  A sample flips branch
  only if it sits within one LSB of a threshold, so the error is not smooth in
  the word length and has to be measured rather than bounded.

The standardiser in front of both is folded into the coefficients where it can
be (the ridge) and applied in the input format where it cannot (the trees):
scaling in software and quantising afterwards would be measuring a device that
has a float unit on its input, which is not the device.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
from numpy.typing import NDArray

#: Word lengths worth sweeping.  Eighteen bits is the width of the hard
#: multiplier in every mainstream FPGA family, so it is the value a design
#: lands on unless something forces it wider; the rest bracket it.
DEFAULT_WORD_BITS = (8, 10, 12, 14, 16, 18, 24, 32)


@dataclass
class FixedPointFormat:
    """A two's-complement Qm.n word, with saturation and a rounding mode.

    Attributes:
        word_bits: total width, sign bit included.
        fraction_bits: bits below the binary point.  May exceed ``word_bits``
            when the quantity is always small (a probability, a normalised
            coefficient); may be negative for a quantity that is always large.
        signed: two's complement when true, unsigned when false.
        rounding: ``"round"`` (half away from zero) or ``"truncate"`` (toward
            negative infinity, what a bare shift does).
        saturations: how many values have been clamped since construction.
            Not an error — an instrument.  Read it after a run.

    Raises:
        ValueError: on a non-positive width or an unknown rounding mode.
    """

    word_bits: int = 18
    fraction_bits: int = 12
    signed: bool = True
    rounding: str = "round"
    saturations: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.word_bits <= 0:
            raise ValueError("word_bits must be positive")
        if self.rounding not in ("round", "truncate"):
            raise ValueError(
                f"rounding must be 'round' or 'truncate', got {self.rounding!r}")

    # -- the grid ---------------------------------------------------------

    @property
    def resolution(self) -> float:
        """The value of one least-significant bit."""
        return float(2.0 ** -self.fraction_bits)

    @property
    def minimum(self) -> float:
        """Most negative representable value."""
        if not self.signed:
            return 0.0
        return float(-(2 ** (self.word_bits - 1)) * self.resolution)

    @property
    def maximum(self) -> float:
        """Most positive representable value."""
        levels = 2 ** (self.word_bits - 1) - 1 if self.signed else 2 ** self.word_bits - 1
        return float(levels * self.resolution)

    def reset(self) -> "FixedPointFormat":
        """Forget the saturation count.  Returns ``self``, so it can be chained."""
        self.saturations = 0
        return self

    # -- conversion -------------------------------------------------------

    def to_integer(self, values) -> NDArray[np.int64]:
        """The integers the register would hold.

        Args:
            values: anything array-like, in engineering units.

        Returns:
            Integer codes, rounded and clamped to the word.
        """
        scaled = np.asarray(values, dtype=float) * (2.0 ** self.fraction_bits)
        if self.rounding == "round":
            codes = np.floor(np.abs(scaled) + 0.5) * np.sign(scaled)
        else:
            codes = np.floor(scaled)

        low = -(2 ** (self.word_bits - 1)) if self.signed else 0
        high = (2 ** (self.word_bits - 1) - 1 if self.signed
                else 2 ** self.word_bits - 1)
        clamped = np.clip(codes, low, high)
        self.saturations += int(np.count_nonzero(clamped != codes))
        return clamped.astype(np.int64)

    def from_integer(self, codes) -> NDArray[np.float64]:
        """Engineering units for integer codes."""
        return np.asarray(codes, dtype=float) * self.resolution

    def quantise(self, values) -> NDArray[np.float64]:
        """Snap to the grid: :meth:`to_integer` followed by :meth:`from_integer`.

        This is the function the rest of the module calls.  It returns floats
        because every consumer downstream expects floats, and the floats it
        returns are exactly representable in the format — the arithmetic is
        bit-accurate even though the container is not an integer.
        """
        return self.from_integer(self.to_integer(values))

    def describe(self) -> str:
        """``Q3.12 (18b)`` — how the format would be written in a datasheet."""
        integer_bits = self.word_bits - self.fraction_bits - (1 if self.signed else 0)
        return f"Q{integer_bits}.{self.fraction_bits} ({self.word_bits}b)"


def split_format(word_bits: int, values, signed: bool = True,
                 headroom_bits: int = 1, rounding: str = "round") -> FixedPointFormat:
    """Choose the binary point for a word that has to hold ``values``.

    The integer part is sized from the largest magnitude present plus
    ``headroom_bits``, and everything left over goes below the point.  This is
    what an engineer does with a histogram of the signal and a calculator, and
    doing it from the data rather than by hand is what makes a word-length
    sweep mean the same thing at every width.

    Args:
        word_bits: total width.
        values: representative data, in engineering units.
        signed: whether a sign bit is needed.
        headroom_bits: margin above the observed peak, in bits.
        rounding: passed through to the format.

    Returns:
        FixedPointFormat: with ``fraction_bits`` set so the peak fits.
    """
    peak = float(np.max(np.abs(np.asarray(values, dtype=float))))
    if not np.isfinite(peak) or peak <= 0.0:
        integer_bits = 0
    else:
        integer_bits = int(np.ceil(np.log2(peak))) + headroom_bits
    fraction_bits = word_bits - integer_bits - (1 if signed else 0)
    return FixedPointFormat(word_bits=word_bits, fraction_bits=fraction_bits,
                            signed=signed, rounding=rounding)


# ---------------------------------------------------------------------------
# the ridge
# ---------------------------------------------------------------------------

@dataclass
class QuantisedRidge:
    """A fitted linear model evaluated in fixed point.

    The dataflow is the hardware's: quantise each input, multiply by a
    quantised coefficient, accumulate in a wider word, add the intercept,
    quantise once at the output.  No intermediate is allowed more precision
    than the register that would hold it.

    Attributes:
        coefficients: one per feature, in the fitted model's units.
        intercept: the fitted offset.
        input_format: what the feature bus carries.
        coefficient_format: what the coefficient ROM holds.
        accumulator_format: the MAC's accumulator, and the output word.
        name: display name.
    """

    coefficients: NDArray[np.float64]
    intercept: float
    input_format: FixedPointFormat
    coefficient_format: FixedPointFormat
    accumulator_format: FixedPointFormat
    name: str = "ridge Q"

    def __post_init__(self) -> None:
        self.coefficients = np.asarray(self.coefficients, dtype=float).reshape(-1)
        self._codes = self.coefficient_format.to_integer(self.coefficients)
        self._quantised = self.coefficient_format.from_integer(self._codes)
        self._intercept = self.accumulator_format.quantise(self.intercept)[()]

    @property
    def coefficient_codes(self) -> NDArray[np.int64]:
        """The integers the coefficient ROM would be loaded with."""
        return self._codes

    def predict(self, features) -> NDArray[np.float64]:
        """Fixed-point inference, shaped like scikit-learn's ``predict``.

        Args:
            features: ``(n, k)`` in the fitted model's units.

        Returns:
            ``(n,)`` predictions, on the accumulator's grid.

        Raises:
            ValueError: on a feature-count mismatch.
        """
        matrix = np.atleast_2d(np.asarray(features, dtype=float))
        if matrix.shape[1] != len(self.coefficients):
            raise ValueError(
                f"{self.name} holds {len(self.coefficients)} coefficients, "
                f"got {matrix.shape[1]} features")

        inputs = self.input_format.quantise(matrix)
        # The products are exact in the accumulator's format only if it has at
        # least the sum of the two fraction counts; quantising each product is
        # what a MAC that truncates its multiplier output does, and is the
        # pessimistic reading.
        products = self.accumulator_format.quantise(inputs * self._quantised)
        total = self._intercept + np.sum(products, axis=1)
        return self.accumulator_format.quantise(total)

    @property
    def saturations(self) -> int:
        """Clamps across all three formats since construction."""
        return (self.input_format.saturations + self.coefficient_format.saturations
                + self.accumulator_format.saturations)

    def reset(self) -> "QuantisedRidge":
        """Forget the saturation counts.  Returns ``self``."""
        for fmt in (self.input_format, self.coefficient_format,
                    self.accumulator_format):
            fmt.reset()
        return self


def quantise_ridge(model, features: NDArray[np.float64], word_bits: int = 18,
                   accumulator_bits: int | None = None,
                   rounding: str = "round",
                   name: str | None = None) -> QuantisedRidge:
    """Quantise a fitted linear model to a word length, sizing the points from data.

    Args:
        model: anything carrying ``coef_`` and ``intercept_`` — a fitted
            :class:`sklearn.linear_model.Ridge` here.
        features: representative rows, used to size the input format.
        word_bits: width of the input and coefficient words.
        accumulator_bits: width of the accumulator; ``word_bits + 8`` when
            ``None``, which is the usual DSP-slice arrangement.
        rounding: ``"round"`` or ``"truncate"``.
        name: display name; derived from the width when ``None``.

    Returns:
        QuantisedRidge: ready to hand to
        :class:`~qnet_autopilot.qkd.timing.LearnedSync`.
    """
    coefficients = np.asarray(model.coef_, dtype=float).reshape(-1)
    intercept = float(np.asarray(model.intercept_, dtype=float).reshape(-1)[0])
    accumulator_bits = accumulator_bits or word_bits + 8

    matrix = np.atleast_2d(np.asarray(features, dtype=float))
    reach = np.abs(matrix) @ np.abs(coefficients)
    span = np.concatenate([reach, [abs(intercept), 1.0]])

    return QuantisedRidge(
        coefficients=coefficients,
        intercept=intercept,
        input_format=split_format(word_bits, matrix, rounding=rounding),
        coefficient_format=split_format(word_bits, np.append(coefficients, 1.0),
                                        rounding=rounding),
        accumulator_format=split_format(accumulator_bits, span, rounding=rounding),
        name=name or f"ridge {word_bits}b")


# ---------------------------------------------------------------------------
# the trees
# ---------------------------------------------------------------------------

@dataclass
class QuantisedBoosting:
    """A histogram gradient-boosting ensemble evaluated in fixed point.

    Each tree is flattened out of scikit-learn's node array into plain arrays
    — feature index, threshold, children, leaf value — which is the form a
    block RAM would hold and the form the traversal below walks.  Thresholds
    are quantised into the *input* format so that the comparison performed here
    is the comparison the comparator would perform on the stored integers.

    Attributes:
        trees: ``(iteration, class)`` grid of flattened trees.
        baseline: the ensemble's starting raw score per class.
        classes: label order, matching ``baseline``.
        input_format: the feature bus, shared with the thresholds.
        leaf_format: the leaf ROM — sized on a *single* leaf.
        accumulator_format: the raw-score accumulator — sized on their sum, and
            therefore wider by about the log of the number of rounds.
        name: display name.
    """

    trees: list[list[dict]]
    baseline: NDArray[np.float64]
    classes: tuple[str, ...]
    input_format: FixedPointFormat
    leaf_format: FixedPointFormat
    accumulator_format: FixedPointFormat
    name: str = "boosting Q"

    @property
    def nodes(self) -> int:
        """Total nodes across the ensemble — what the block RAM has to hold."""
        return int(sum(len(tree["is_leaf"]) for row in self.trees for tree in row))

    @property
    def rounds(self) -> int:
        """Boosting rounds kept.  The ensemble's cost is linear in this."""
        return len(self.trees)

    @property
    def depth(self) -> int:
        """Longest root-to-leaf path — the cycles one traversal takes."""
        return max((_depth(tree) for row in self.trees for tree in row), default=0)

    def _raw(self, features: NDArray[np.float64]) -> NDArray[np.float64]:
        """Raw scores per class, accumulated in the leaf format."""
        matrix = np.atleast_2d(np.asarray(features, dtype=float))
        inputs = self.input_format.quantise(matrix)
        scores = np.tile(self.accumulator_format.quantise(self.baseline),
                         (len(inputs), 1))

        for row in self.trees:
            for index, tree in enumerate(row):
                scores[:, index] = self.accumulator_format.quantise(
                    scores[:, index] + _walk(tree, inputs))
        return scores

    def raw_scores(self, features) -> NDArray[np.float64]:
        """The accumulated score per class, before any softmax.

        This is what a hardware implementation actually produces — the softmax
        is a decision the receiver makes afterwards, or not at all — so it is
        also what the RTL is checked against.  Comparing probabilities would
        compare an exponential as well as the ensemble.
        """
        return self._raw(features)

    def predict_proba(self, features) -> NDArray[np.float64]:
        """Class probabilities, softmax over the fixed-point raw scores.

        The softmax itself is left in floating point on purpose.  In hardware a
        classifier that only has to pick a class does not evaluate it at all —
        it takes the argmax of the raw scores — and a classifier that needs a
        calibrated threshold evaluates it in a small lookup table whose error is
        a property of that table, not of the ensemble.  Quantising it here would
        be attributing a table's error to the word length.
        """
        scores = self._raw(features)
        if scores.shape[1] == 1:  # binary, stored as one score
            scores = np.column_stack([-scores[:, 0], scores[:, 0]])
        shifted = scores - scores.max(axis=1, keepdims=True)
        weights = np.exp(shifted)
        return weights / weights.sum(axis=1, keepdims=True)

    def predict(self, features) -> NDArray[np.str_]:
        """The argmax class — what the comparator chain would emit."""
        scores = self._raw(features)
        if scores.shape[1] == 1:
            scores = np.column_stack([-scores[:, 0], scores[:, 0]])
        return np.asarray(self.classes, dtype=object)[np.argmax(scores, axis=1)]

    @property
    def saturations(self) -> int:
        """Clamps across all three formats since construction."""
        return (self.input_format.saturations + self.leaf_format.saturations
                + self.accumulator_format.saturations)

    def reset(self) -> "QuantisedBoosting":
        """Forget the saturation counts.  Returns ``self``."""
        for fmt in (self.input_format, self.leaf_format, self.accumulator_format):
            fmt.reset()
        return self


def _depth(tree: dict) -> int:
    """Longest path from the root, walked iteratively over the node arrays."""
    is_leaf, left, right = tree["is_leaf"], tree["left"], tree["right"]
    frontier = [(0, 1)]
    deepest = 0
    while frontier:
        node, level = frontier.pop()
        deepest = max(deepest, level)
        if not is_leaf[node]:
            frontier.append((int(left[node]), level + 1))
            frontier.append((int(right[node]), level + 1))
    return deepest


def _walk(tree: dict, inputs: NDArray[np.float64]) -> NDArray[np.float64]:
    """Evaluate one flattened tree over every row, iteratively.

    Recursion per sample would be the obvious implementation and would take
    minutes over ninety thousand rows and four hundred rounds.  This walks the
    whole batch down the tree together, carrying an index per row — the same
    traversal, vectorised.
    """
    is_leaf = tree["is_leaf"]
    feature = tree["feature"]
    threshold = tree["threshold"]
    left, right = tree["left"], tree["right"]
    value = tree["value"]

    position = np.zeros(len(inputs), dtype=np.int64)
    active = ~is_leaf[position]
    while active.any():
        here = position[active]
        goes_left = inputs[active, feature[here]] <= threshold[here]
        position[active] = np.where(goes_left, left[here], right[here])
        active = ~is_leaf[position]
    return value[position]


def _flatten(predictor, input_format: FixedPointFormat,
             leaf_format: FixedPointFormat) -> dict:
    """Pull one scikit-learn ``TreePredictor`` into plain quantised arrays."""
    nodes = predictor.nodes
    return {
        "is_leaf": np.asarray(nodes["is_leaf"], dtype=bool),
        "feature": np.asarray(nodes["feature_idx"], dtype=np.int64),
        "threshold": input_format.quantise(nodes["num_threshold"]),
        "left": np.asarray(nodes["left"], dtype=np.int64),
        "right": np.asarray(nodes["right"], dtype=np.int64),
        "value": leaf_format.quantise(nodes["value"]),
    }


def quantise_boosting(detector, features: NDArray[np.float64], word_bits: int = 18,
                      leaf_bits: int | None = None, rounding: str = "round",
                      input_quantile: float = 1.0, rounds: int | None = None,
                      name: str | None = None) -> QuantisedBoosting:
    """Quantise a fitted boosting detector to a word length.

    The standardiser is applied to ``features`` before the input format is
    sized, because standardised units are what the thresholds live in and
    therefore what the bus would carry.

    Args:
        detector: an :class:`~qnet_autopilot.qkd.detection.AttackDetector`
            wrapping a fitted ``HistGradientBoostingClassifier``.
        features: representative rows, *unstandardised*.
        word_bits: width of the feature, threshold and leaf words.
        leaf_bits: width of the raw-score accumulator; ``word_bits + 8`` when
            ``None``, the usual arrangement for a sum of several hundred terms.
        rounding: ``"round"`` or ``"truncate"``.
        input_quantile: size the feature bus on this quantile of ``|z|``
            instead of on its peak, letting the tail saturate.  One standardised
            feature with a 30-sigma outlier costs five integer bits that every
            other row then does without, so a narrow word is better spent
            clipping the tail — which is what a designer does, and what
            :attr:`QuantisedBoosting.saturations` then counts.  ``1.0`` sizes on
            the peak and clips nothing.
        rounds: keep only the first this many boosting rounds.  Boosting is
            additive and ordered, so a prefix of the ensemble *is* the model
            that early stopping would have produced — truncating here is a
            modelling decision with a measurable cost, not an approximation of
            one.  It is the lever that decides whether the ensemble fits a
            small fabric at all.  ``None`` keeps every round.
        name: display name; derived from the width when ``None``.

    Returns:
        QuantisedBoosting: substitutable for ``detector.model``.

    Raises:
        AttributeError: if the model is not a fitted histogram booster.
    """
    model = detector.model
    if not hasattr(model, "_predictors"):
        raise AttributeError(
            f"{type(model).__name__} is not a fitted HistGradientBoostingClassifier")

    predictors = (model._predictors if rounds is None
                  else model._predictors[:rounds])
    if not predictors:
        raise ValueError("rounds must keep at least one boosting round")

    scaled = detector.standardiser.transform(np.asarray(features, dtype=float))
    thresholds = np.concatenate(
        [predictor.nodes["num_threshold"][~predictor.nodes["is_leaf"].astype(bool)]
         for row in predictors for predictor in row] or [np.zeros(1)])
    leaves = np.concatenate(
        [predictor.nodes["value"] for row in predictors for predictor in row])
    baseline = np.asarray(model._baseline_prediction, dtype=float).reshape(-1)

    span = np.abs(np.concatenate([scaled.reshape(-1), thresholds]))
    if input_quantile < 1.0:
        span = np.array([np.quantile(span, input_quantile)])
    input_format = split_format(word_bits, span, rounding=rounding)
    leaf_format = split_format(word_bits, np.append(leaves, 1.0), rounding=rounding)
    # The raw score is a sum of up to ``max_iter`` leaves, so the accumulator
    # needs the log of that many bits above the largest single leaf.  Sizing it
    # on the worst case rather than on the observed sum is the pessimistic
    # reading, and the one a designer has to build to.
    kept = len(predictors)
    reach = np.append(np.abs(leaves) * kept, np.abs(baseline) + 1.0)
    accumulator_format = split_format(leaf_bits or word_bits + 8, reach,
                                      rounding=rounding)

    trees = [[_flatten(predictor, input_format, leaf_format) for predictor in row]
             for row in predictors]
    suffix = "" if rounds is None else f" x{kept}"
    return QuantisedBoosting(
        trees=trees, baseline=baseline, classes=tuple(detector.classes),
        input_format=input_format, leaf_format=leaf_format,
        accumulator_format=accumulator_format,
        name=name or f"boosting {word_bits}b{suffix}")


def coefficient_rom(model: QuantisedRidge, radix: int = 16) -> Sequence[str]:
    """The coefficient ROM as HDL-ready two's-complement literals.

    Args:
        model: a quantised ridge.
        radix: 16 for hex, 2 for binary.

    Returns:
        One string per coefficient, then the intercept.

    Raises:
        ValueError: on an unsupported radix.
    """
    if radix not in (2, 16):
        raise ValueError("radix must be 2 or 16")

    width = model.coefficient_format.word_bits
    codes = list(model.coefficient_codes)
    codes.append(int(model.accumulator_format.to_integer(model.intercept)[()]))
    digits = width if radix == 2 else (width + 3) // 4

    literals = []
    for code in codes:
        unsigned = int(code) & ((1 << width) - 1)
        body = (format(unsigned, f"0{digits}b") if radix == 2
                else format(unsigned, f"0{digits}x"))
        literals.append(f"{width}'{'b' if radix == 2 else 'h'}{body}")
    return literals
