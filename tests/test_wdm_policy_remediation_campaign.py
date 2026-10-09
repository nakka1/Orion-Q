"""Contract tests for the predeclared v5.26 campaign."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from run_wdm_policy_remediation_campaign import _calibration_config, _validate_config


CONFIG_PATH = Path("experiments/configs/wdm_policy_remediation_v526.json")


@pytest.mark.unit
def test_registered_policy_campaign_uses_fresh_disjoint_seeds():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    _validate_config(config)

    assert set(config["evaluation_seeds"]).isdisjoint(
        config["prohibited_prior_test_seeds"]
    )
    assert set(config["evaluation_seeds"]).isdisjoint(
        config["methodology_debug_seeds"]
    )
    assert config["control_task"] == "link_purification"


@pytest.mark.unit
def test_policy_campaign_rejects_reuse_of_observed_v525_test_seed():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    changed = deepcopy(config)
    changed["evaluation_seeds"][0] = config["prohibited_prior_test_seeds"][0]

    with pytest.raises(ValueError, match="disjoint"):
        _validate_config(changed)


@pytest.mark.unit
def test_normalized_utility_and_abstention_budget_are_explicit():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    calibration = _calibration_config(config)

    assert calibration.max_safety_abstention_rate_pct == 20.0
    assert calibration.max_policy_wait_rate_pct == 35.0
    assert calibration.useful_pair_reward == 1.0
    assert calibration.false_purification_penalty == 0.25
    assert calibration.probability_margin_grid[0] == 0.0
