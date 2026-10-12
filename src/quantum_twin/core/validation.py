"""Physical input guards that remain active in optimized Python builds.

These checks validate units/ranges, without changing the channel equations or
substituting clipped values for invalid measurements or configurations.
"""
from __future__ import annotations

import math
from numbers import Integral, Real


def finite_real(name: str, value: Real, *, minimum: float | None = None,
                maximum: float | None = None, positive: bool = False) -> None:
    """Require a finite real scalar, optionally with inclusive bounds."""
    try:
        finite = not isinstance(value, bool) and isinstance(value, Real) and math.isfinite(value)
    except (OverflowError, TypeError):
        finite = False
    if not finite:
        raise ValueError(f"{name} must be a finite real number")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")


def integer(name: str, value: int, *, minimum: int = 0) -> None:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def relaxation_times(T1: float, T2: float) -> None:
    """Check seconds and the total-coherence constraint T2 <= 2*T1."""
    finite_real("T1 (s)", T1, positive=True)
    finite_real("T2 (s)", T2, positive=True)
    if T2 > 2.0 * T1:
        raise ValueError("Physical constraint violated: T2 must be <= 2*T1")


def probability(name: str, value: float) -> None:
    finite_real(name, value, minimum=0.0, maximum=1.0)
