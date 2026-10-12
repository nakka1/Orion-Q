"""Regression for the input guards removed by optimize=1 in release 5.65."""
from __future__ import annotations

import ast
from dataclasses import fields
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from quantum_twin.core.config import PhysicsConfig
from quantum_twin.core.validation import finite_real
from quantum_twin.evaluation.model_selection import make_four_way_split, ModelSelectionProtocol, ProtocolViolationError
from quantum_twin.quantum.channel import QuantumChannel, QuantumNoiseChannel, pure_dephasing_probability


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("optimization", (0, 1, 2))
def test_scientific_guards_are_active_inside_cli_runtime(tmp_path, optimization):
    report = tmp_path / f"scientific-{optimization}.json"
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    env.pop("PYTHONOPTIMIZE", None)
    args = [sys.executable] + (["-" + "O" * optimization] if optimization else [])
    result = subprocess.run(args + ["-m", "quantum_autopilot.desktop", "--scientific-input-test", str(report)],
                            cwd=ROOT, env=env, text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stderr + result.stdout
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["passed"] and data["python_optimization"] == optimization
    assert data["checks_count"] >= 100
    assert all(row["passed"] for row in data["checks"])
    assert data["physical_action_performed"] is False
    assert data["hardware_validated"] is False
    rejected = [row for row in data["checks"] if row["outcome"] == "rejected"]
    assert len(rejected) == data["checks_count"] - 1
    assert all(row["error_type"] == "ValueError" and row["message"] for row in rejected)


@pytest.mark.parametrize("field", [field.name for field in fields(PhysicsConfig)])
@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf"), True, "1"))
def test_every_physical_config_field_rejects_non_numeric_or_non_finite(field, value):
    with pytest.raises(ValueError, match=field if field not in ("T1", "T2") else field):
        PhysicsConfig(**{field: value})


def test_numpy_scalars_are_compatible_and_inputs_are_not_coerced():
    cfg = PhysicsConfig(T1=np.float64(1.0), T2=np.float64(2.0), NUM_QUBITS=np.int64(2), SEED=np.int64(42))
    assert cfg.T2 == 2.0
    assert isinstance(cfg.T1, np.float64)


def test_mutated_config_cannot_be_saved_or_used_to_initialize_channel(tmp_path):
    cfg = PhysicsConfig()
    cfg.T2 = 3 * cfg.T1
    path = tmp_path / "invalid.json"
    with pytest.raises(ValueError, match="T2 must be"):
        cfg.save(path)
    assert not path.exists()
    with pytest.raises(ValueError, match="T2 must be"):
        QuantumChannel(cfg)


def test_json_loader_and_override_share_constructor_validation(tmp_path):
    path = tmp_path / "nan.json"
    path.write_text('{"T1": NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="T1"):
        PhysicsConfig.load(path)
    with pytest.raises(ValueError, match="STORAGE_TIME"):
        PhysicsConfig().with_overrides(STORAGE_TIME=-1.0)


def test_elapsed_and_probability_override_rejected_before_kraus_calculation():
    channel = QuantumNoiseChannel()
    with pytest.raises(ValueError, match="elapsed_time"):
        channel.apply(-1.0)
    with pytest.raises(ValueError, match="depol_prob"):
        channel.apply(0.0, float("nan"))
    with pytest.raises(ValueError, match="elapsed_time"):
        pure_dephasing_probability(float("inf"), 1.0, 2.0)


def test_four_way_split_rejects_negative_fractions_even_if_sum_is_one():
    with pytest.raises(ValueError, match="train_frac"):
        make_four_way_split(pd.DataFrame({"x": range(10)}), -0.2, 0.4, 0.4, 0.4)


@pytest.mark.parametrize("optimization", (0, 1))
def test_freeze_and_ui_acceptance_failure_are_enforced_under_optimization(optimization):
    # No assert in the child: it reports what actually happened after compiling
    # the production modules with the chosen optimizer.
    source = """
import json
import pandas as pd
from quantum_twin.evaluation.model_selection import make_four_way_split, ModelSelectionProtocol
from quantum_autopilot.console.ui_pipeline_validation import _require
protocol = ModelSelectionProtocol(make_four_way_split(pd.DataFrame({'x': range(10)})))
protocol.freeze()
rows = []
for action in (lambda: protocol.log_decision('threshold', .9, 'validation'),
               lambda: _require(False, 'injected scientific UI failure')):
    try:
        action()
    except Exception as error:
        rows.append({'rejected': True, 'type': type(error).__name__, 'message': str(error)})
    else:
        rows.append({'rejected': False})
_require(True, 'valid gate')
print(json.dumps(rows))
"""
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    env.pop("PYTHONOPTIMIZE", None)
    args = [sys.executable] + (["-O"] if optimization else [])
    result = subprocess.run(args + ["-c", source], cwd=ROOT, env=env, text=True, capture_output=True, timeout=45)
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    assert [row["type"] for row in rows] == ["ProtocolViolationError", "AssertionError"]
    assert all(row["rejected"] and row["message"] for row in rows)


def test_ui_pipeline_has_no_optimization_removable_acceptance_gates():
    source = ROOT / "src/quantum_autopilot/console/ui_pipeline_validation.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "_require"]
    assert len(calls) == 15
    assert all(len(node.args) == 2 and isinstance(node.args[1], ast.Constant) for node in calls)


def test_scientific_guard_modules_do_not_rely_on_assert():
    paths = ("core/config.py", "quantum/channel.py", "simulation/environment.py", "simulation/network.py",
             "simulation/multihop.py", "simulation/repeater_chain.py", "evaluation/model_selection.py",
             "evaluation/temporal_conformal.py", "ml/uncertainty.py", "control/admission.py",
             "core/runtime_validation.py", "core/validation.py")
    for path in paths:
        tree = ast.parse((ROOT / "src/quantum_twin" / path).read_text(encoding="utf-8"))
        assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree)), path


def test_rejected_tuning_cannot_modify_decision_log():
    protocol = ModelSelectionProtocol(make_four_way_split(pd.DataFrame({"x": range(20)})))
    protocol.freeze()
    with pytest.raises(ProtocolViolationError):
        protocol.log_decision("threshold", 0.9, "validation")
    assert protocol.decisions_log == []
    protocol.log_decision("MAE", 0.1, "test_evaluation")
    assert len(protocol.decisions_log) == 1


def test_real_scalar_guard_rejects_booleans_not_just_nan():
    with pytest.raises(ValueError, match="finite real"):
        finite_real("elapsed_time", True)


def test_real_scalar_guard_reports_unrepresentable_integer_as_invalid_input():
    with pytest.raises(ValueError, match="finite real"):
        finite_real("elapsed_time", 10 ** 1000)
