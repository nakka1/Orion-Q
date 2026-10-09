"""
tests/test_policy_engine.py

Unit tests for policy_engine.py (TCC improvement item 3).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quantum_twin.control.policy import PolicyEngine, RepeaterState, Action
from quantum_twin.control.risk_aware import RiskAwareController


def test_policy_engine_matches_risk_aware_controller_without_swap():
    """Regression guard for the central mathematical equivalence this
    module relies on: argmax_a E[U(a|s)] with U=-C must give the EXACT
    same decision as the original argmin_a E[C(a)] formulation, across
    several representative (mu, sigma) points -- verified directly, not
    just assumed from the U=-C definition alone."""
    threshold = 0.65
    engine = PolicyEngine(threshold=threshold)
    original = RiskAwareController(threshold=threshold)

    for mu, sigma in [(0.9, 0.02), (0.3, 0.02), (0.65, 0.1), (0.5, 0.3), (0.8, 0.15)]:
        state = RepeaterState(telemetry={}, predicted_fidelity=mu, uncertainty=sigma,
                               memory_state="IDLE", repeater_state=None)
        result = engine.decide(state)
        assert result["action"].value == original.decide(mu, sigma)


def test_swap_action_absent_without_a_partner_pair():
    engine = PolicyEngine(threshold=0.65)
    state = RepeaterState(telemetry={}, predicted_fidelity=0.85, uncertainty=0.02,
                           memory_state="IDLE", repeater_state=None)
    result = engine.decide(state)
    assert Action.SWAP not in result["utilities"]


def test_swap_action_present_and_wins_with_two_high_fidelity_pairs():
    """Direct sanity check: with both pairs genuinely good, SWAP should
    be available AND should win (positive utility, beating HALT/WAIT/
    PURIFY's net costs)."""
    engine = PolicyEngine(threshold=0.65)
    state = RepeaterState(telemetry={}, predicted_fidelity=0.9, uncertainty=0.01,
                           memory_state="OCCUPIED", repeater_state={"partner_pair_fidelity": 0.9})
    result = engine.decide(state)
    assert Action.SWAP in result["utilities"]
    assert result["action"] == Action.SWAP
    assert result["utilities"][Action.SWAP] > 0.0


def test_swap_utility_is_low_with_a_bad_partner_pair():
    """A partner pair with LOW fidelity should make SWAP much less
    attractive (or losing) than with a high-fidelity partner -- verified
    by comparing SWAP utility directly across the two scenarios."""
    engine = PolicyEngine(threshold=0.65)
    good_partner_state = RepeaterState(telemetry={}, predicted_fidelity=0.9, uncertainty=0.01,
                                        memory_state="OCCUPIED", repeater_state={"partner_pair_fidelity": 0.9})
    bad_partner_state = RepeaterState(telemetry={}, predicted_fidelity=0.9, uncertainty=0.01,
                                       memory_state="OCCUPIED", repeater_state={"partner_pair_fidelity": 0.1})
    good_result = engine.decide(good_partner_state)
    bad_result = engine.decide(bad_partner_state)
    assert good_result["utilities"][Action.SWAP] > bad_result["utilities"][Action.SWAP]


def test_decide_returns_full_utility_breakdown_not_just_the_winner():
    """Regression guard: decide() must always return the FULL utility
    dict, never just the winning action alone -- so a caller can see
    how close the runner-up was."""
    engine = PolicyEngine(threshold=0.65)
    state = RepeaterState(telemetry={}, predicted_fidelity=0.7, uncertainty=0.1,
                           memory_state="IDLE", repeater_state=None)
    result = engine.decide(state)
    assert set(result.keys()) == {"action", "utilities"}
    assert len(result["utilities"]) >= 3  # at least HALT/WAIT/PURIFY always present


def test_action_enum_has_exactly_the_four_named_actions():
    assert set(a.value for a in Action) == {"HALT", "WAIT", "PURIFY", "SWAP"}
