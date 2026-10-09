"""What survives the trip from float64 to an FPGA.

Every model in this package is fitted and scored in double precision, and none
of that precision exists in the device that would run it.  A synchroniser
sitting next to the time-tagger has an 18-bit multiplier and a carry chain; a
detector sharing the fabric with the counting path has block RAM and
comparators.  The question this subpackage answers is the only one that can
invalidate the rest of an embedding effort before a line of RTL is written:
*what does the quantisation cost?*

The answer is obtained by re-running the existing evaluations — the same
episodes, the same seeds, the same scoring functions — with the inference
arithmetic replaced by a bit-accurate fixed-point model.  Nothing else changes,
so any difference in residual, key rate or F1 is attributable to the word
length and to nothing else.

:mod:`.fixed_point`
    The number format, and the two quantised estimators that plug into the
    interfaces :class:`~qnet_autopilot.qkd.timing.LearnedSync` and
    :class:`~qnet_autopilot.qkd.detection.AttackDetector` already accept.

Deliberately absent: any claim about a specific device.  Resource counts and
:math:`F_{max}` belong to a synthesis tool and a target, and quoting them from
a simulation would be inventing them.  What is here is arithmetic, and
arithmetic is portable.
"""
from __future__ import annotations

from . import fixed_point
from .fixed_point import (FixedPointFormat, QuantisedBoosting, QuantisedRidge,
                          quantise_boosting, quantise_ridge)

__all__ = [
    "FixedPointFormat",
    "QuantisedBoosting",
    "QuantisedRidge",
    "fixed_point",
    "quantise_boosting",
    "quantise_ridge",
]
