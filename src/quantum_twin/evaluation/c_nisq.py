"""
quantum_twin/evaluation/c_nisq.py
=====================================

TCC improvement item 2: formalizes a single, central "quantum resource
efficiency" metric -- C_NISQ -- synthesizing the many separate
dimensions this project already tracks (fidelity, yield, useful pairs,
QPU operations, purification attempts, latency, memory, energy) into
ONE number that answers a different question than "which model predicts
best": "which POLICY extracts the most useful quantum value per unit of
quantum resource consumed."

FORMAL DEFINITION -- stated and justified HERE, in writing, BEFORE this
module is applied to any controller's real data, per the explicit
instruction that this definition "não [deve ser] escolhida apenas para
favorecer o modelo" (must not be chosen merely to favor [any particular]
model):

    C_NISQ = E_total_consumed_J / N_useful_pairs

where:
    E_total_consumed_J is the controller's TOTAL resource-consumption
        energy (Joules) -- REUSING this project's own existing,
        already-validated `energy_model.estimate_energy_breakdown()`
        six-way split (E_QPU + E_inference + E_memory + E_communication
        + E_optical + E_control), NOT a hand-picked subset of resource
        dimensions chosen after seeing which controller wins. Energy in
        Joules was chosen as the unifying currency specifically because
        it is the ONE dimension this project already expresses every
        other resource (QPU gate operations, classical inference,
        memory hold time, communication, control-decision cost) IN, via
        code that predates this metric's own formalization (see the
        energy_model.py module docstring). Choosing a NEW, C_NISQ
        -specific weighting scheme instead would have required
        introducing fresh, unvalidated weights -- a real risk of
        tuning the metric to favor a particular outcome, avoided here
        by reusing the pre-existing accounting.

    N_useful_pairs is the controller's real, already-established
        "useful pair" count -- REUSING the exact same `is_useful`
        criterion (success_rate >= cutoff AND true_fidelity >=
        threshold) established in `orchestrator.py`'s
        `run_intelligent()`/`run_blind_baseline()` and reused
        identically throughout this project's controller comparisons
        (forty-sixth, sixtieth, seventy-third addenda) -- not a new,
        C_NISQ-specific definition of "useful."

Lower C_NISQ is better: fewer Joules of quantum-relevant resource
consumed per useful pair extracted. A SECOND, complementary,
operationally simpler metric is also reported (not instead of, but
alongside C_NISQ_energy, so no single formula is privileged as "the"
answer): C_NISQ_operations = purification_attempts / useful_pairs --
the "waste ratio" in units of raw QPU operation counts, for a reader
who wants an energy-model-independent sanity check on the same underlying question.
"""

from dataclasses import dataclass


@dataclass
class CNisqResult:
    controller: str
    energy_total_J: float
    useful_pairs: int
    purification_attempts: int
    c_nisq_energy: float          # Joules per useful pair
    c_nisq_operations: float      # purification attempts per useful pair


def compute_c_nisq(controller: str, energy_total_J: float, useful_pairs: int,
                    purification_attempts: int) -> CNisqResult:
    """
    Computes both C_NISQ variants for one controller's real, already
    -measured resource-consumption data. Raises explicitly (rather than
    silently returning inf/nan) if useful_pairs is zero -- a controller
    that never produces a useful pair has an ill-defined "resource per
    useful pair" ratio, and this should be surfaced as a real finding
    about that controller, not hidden behind a silent infinity.
    """
    if useful_pairs <= 0:
        raise ValueError(
            f"Controller '{controller}' produced zero useful pairs -- C_NISQ is undefined "
            f"(division by zero), not silently reported as infinity. This is itself a real "
            f"finding about this controller's performance, worth stating directly."
        )
    c_nisq_energy = energy_total_J / useful_pairs
    c_nisq_operations = purification_attempts / useful_pairs
    return CNisqResult(
        controller=controller, energy_total_J=energy_total_J, useful_pairs=useful_pairs,
        purification_attempts=purification_attempts, c_nisq_energy=c_nisq_energy,
        c_nisq_operations=c_nisq_operations,
    )
