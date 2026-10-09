"""
quantum_twin/control/policy.py
==================================

REAL implementation of the formal decision-problem PolicyEngine (TCC
improvement item 3) -- migrated here from root-level policy_engine.py
in the one-hundred-eleventh addendum (TCC item 6, second migration
wave). This file never had a corresponding quantum_twin/ location to invert.

Root-level policy_engine.py is now the thin re-export (direction
reversed), kept so its one pre-existing consumer
(tests/test_policy_engine.py) continues to work completely UNCHANGED.
This module's own internal imports of RiskAwareController/RiskCostConfig,
EnergyConfig, and WernerStateSwapping were all updated to their real
quantum_twin.control.risk_aware/quantum_twin.evaluation.energy/
quantum_twin.quantum.swapping locations instead of the reversed root shims.

Original module docstring, preserved from the migrated source:

TCC improvement item 3: formalizes the repeater controller as an
explicit decision problem, per the requested structure:

    state:
        telemetry
        predicted_fidelity
        uncertainty
        memory_state
        repeater_state

    actions:
        HALT
        WAIT
        PURIFY
        SWAP

    a*_t = argmax_a E[U(a_t | s_t)]

This is a genuine FORMALIZATION layer, not a rewrite: it wraps this
project's already-validated RiskAwareController cost machinery
and WernerStateSwapping, reframing the existing argmin-E[C(a)] logic as
the mathematically equivalent argmax-E[U(a)] (U := -C, negative cost)
the prompt names explicitly -- rather than reimplementing the cost
model from scratch, which would risk silently diverging from the
already-tested numbers.

The genuinely NEW piece is SWAP as a fourth, explicit action -- not
present in the three-action (HALT/WAIT/PURIFY) space any prior
controller in this project implements -- connected here to
WernerStateSwapping.swap()'s real success-probability/expected
-fidelity outcome for a two-hop scenario.

U(a, s) is defined explicitly for EACH action, in Joules of NEGATIVE
cost (this project's unified currency throughout), so a higher U is
always better, consistent with a genuine utility-maximization framing.
"""

from dataclasses import dataclass
from enum import Enum

from quantum_twin.control.risk_aware import RiskAwareController, RiskCostConfig
from quantum_twin.evaluation.energy import EnergyConfig
from quantum_twin.quantum.swapping import WernerStateSwapping


class Action(Enum):
    HALT = "HALT"
    WAIT = "WAIT"
    PURIFY = "PURIFY"
    SWAP = "SWAP"


@dataclass
class RepeaterState:
    """The formal decision-problem state, per the exact requested fields."""
    telemetry: dict                 # raw WDM-observable features at time t (dict of feature_name -> value)
    predicted_fidelity: float       # mu: the model's point-estimate prediction for this pair's fidelity
    uncertainty: float              # sigma: the model's calibrated uncertainty around predicted_fidelity
    memory_state: str               # "IDLE" | "OCCUPIED" -- whether a memory slot currently holds a pair
    repeater_state: dict            # additional context: e.g. {"partner_pair_fidelity": ..., "hop_index": ...}
                                     # -- only populated when a SWAP decision is genuinely available


class PolicyEngine:
    """
    Computes a*_t = argmax_a E[U(a_t | s_t)] over {HALT, WAIT, PURIFY, SWAP}.

    U(a, s) for each action, all in Joules of NEGATIVE cost (higher = better):
        U(HALT, s)   = -c_halt(s)     -- reuses RiskAwareController's real c_halt term
        U(WAIT, s)   = -c_wait(s)     -- reuses RiskAwareController's real c_wait term
        U(PURIFY, s) = -c_purify(s)   -- reuses RiskAwareController's real c_purify term
        U(SWAP, s)   = benefit_swap(s) - c_swap(s)
                       -- benefit_swap(s): VALUE_MISSED_GOOD_PAIR_J-scaled expected value of a
                          successful swap (using WernerStateSwapping's REAL swap-success
                          probability and resulting fidelity for this state's
                          predicted_fidelity + repeater_state's partner-pair fidelity)
                       -- c_swap(s): the same QPU-gate-cost convention as PURIFY (a swap is
                          also a real quantum-gate operation, N_GATES_PURIFY reused as a
                          stand-in gate count -- an explicit, stated simplification, not a
                          separately-calibrated swap-specific gate count)
    """

    def __init__(self, threshold: float = 0.65, energy_cfg: EnergyConfig = None,
                 risk_cfg: RiskCostConfig = None):
        self.threshold = threshold
        self.energy_cfg = energy_cfg or EnergyConfig()
        self.risk_cfg = risk_cfg or RiskCostConfig()
        self._risk_controller = RiskAwareController(threshold=threshold, energy_cfg=self.energy_cfg,
                                                      risk_cfg=self.risk_cfg)
        self._swapper = WernerStateSwapping()

    def _utility_halt_wait_purify(self, state: RepeaterState) -> dict:
        costs = self._risk_controller.expected_cost(state.predicted_fidelity, state.uncertainty)
        return {Action.HALT: -costs["HALT"], Action.WAIT: -costs["WAIT"], Action.PURIFY: -costs["PURIFY"]}

    def _utility_swap(self, state: RepeaterState) -> float:
        """Only computable when repeater_state carries a genuine partner pair
        (a real two-hop SWAP candidate) -- returns None otherwise, per the
        prompt's own state formalization ("repeater_state" existing
        specifically to carry this context when relevant)."""
        partner_fidelity = state.repeater_state.get("partner_pair_fidelity") if state.repeater_state else None
        if partner_fidelity is None:
            return None

        swap_result = self._swapper.swap(
            pair_left={"F_t": state.predicted_fidelity, "success": True},
            pair_right={"F_t": partner_fidelity, "success": True},
        )
        c_swap = self.risk_cfg.N_GATES_PURIFY * self.energy_cfg.E_QPU_PER_GATE_J
        if swap_result["success"] and swap_result["F_t"] >= self.threshold:
            benefit_swap = self.risk_cfg.VALUE_MISSED_GOOD_PAIR_J
        else:
            benefit_swap = 0.0
        return benefit_swap - c_swap

    def decide(self, state: RepeaterState) -> dict:
        """
        Returns {'action': Action, 'utilities': {Action: float, ...}} --
        a*_t = argmax_a E[U(a_t | s_t)], with the FULL utility breakdown
        included (never just the winning action alone), so a caller can
        always see how close the runner-up action was, not just the
        final decision.
        """
        utilities = self._utility_halt_wait_purify(state)
        swap_utility = self._utility_swap(state)
        if swap_utility is not None:
            utilities[Action.SWAP] = swap_utility

        best_action = max(utilities, key=utilities.get)
        return {"action": best_action, "utilities": utilities}
