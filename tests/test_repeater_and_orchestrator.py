"""
tests/test_repeater_and_orchestrator.py

Unit tests for QuantumRepeaterNode (BBPSSW circuit + noise model) and
DigitalTwinOrchestrator (isolated latency profiling, admission control).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch
import torch.nn as nn
from quantum_twin.simulation.network import QuantumRepeaterNode
from quantum_twin.control.admission import DigitalTwinOrchestrator


# ---------------------------------------------------------------------
# QuantumRepeaterNode
# ---------------------------------------------------------------------
def test_bbpssw_circuit_has_expected_structure():
    qc = QuantumRepeaterNode.build_bbpssw_circuit()
    assert qc.num_qubits == 4
    assert qc.num_clbits == 2


def test_run_purification_returns_valid_success_rate():
    node = QuantumRepeaterNode(T1=50e-6, T2=30e-6, depol_prob=0.01, shots=128, seed=1)
    success_rate, counts = node.run_purification()
    assert 0.0 <= success_rate <= 1.0
    assert sum(counts.values()) == 128


def test_apply_latency_decay_reduces_success_rate_on_average():
    """Longer classical latency -> more memory decoherence -> lower (or equal)
    expected purification success rate, on average across repeated trials."""
    node_short = QuantumRepeaterNode(T1=50e-6, T2=30e-6, depol_prob=0.01, shots=256, seed=1)
    node_long = QuantumRepeaterNode(T1=50e-6, T2=30e-6, depol_prob=0.01, shots=256, seed=1)

    sim_short = node_short.apply_latency_decay(1e-7)   # negligible latency
    sim_long = node_long.apply_latency_decay(5e-4)      # latency >> T1, T2

    rate_short, _ = node_short.run_purification(simulator=sim_short)
    rate_long, _ = node_long.run_purification(simulator=sim_long)

    assert rate_long <= rate_short + 0.05  # allow small sampling noise


def test_zero_latency_close_to_base_simulator():
    """apply_latency_decay(0.0) should behave like the node's own base simulator
    (no extra decoherence channel added)."""
    node = QuantumRepeaterNode(T1=50e-6, T2=30e-6, depol_prob=0.01, shots=512, seed=42)
    sim_zero_latency = node.apply_latency_decay(0.0)
    rate_zero, _ = node.run_purification(simulator=sim_zero_latency)
    rate_base, _ = node.run_purification()  # base simulator
    assert abs(rate_zero - rate_base) < 0.15  # both should reflect only gate-level noise


def test_t1_t2_constraint_enforced():
    import pytest
    with pytest.raises(ValueError, match="T2 must be <= 2\\*T1"):
        QuantumRepeaterNode(T1=10e-6, T2=30e-6)


# ---------------------------------------------------------------------
# DigitalTwinOrchestrator
# ---------------------------------------------------------------------
class _ConstantModel(nn.Module):
    """Stub predictor that always outputs a fixed fidelity, for controlled tests."""
    def __init__(self, value):
        super().__init__()
        self.value = value

    def forward(self, x):
        return torch.full((x.shape[0], 1), self.value)


def test_blind_baseline_never_halts_and_never_calls_model():
    """The defining property of the blind policy: unconditional admission,
    and (by construction) no model is even passed in."""
    node = QuantumRepeaterNode(shots=64, seed=7)
    orch = DigitalTwinOrchestrator(model=None, quantum_node=node, threshold=0.65)

    X_test = torch.rand(10, 5, 3)
    y_test = torch.rand(10, 1)
    metrics = orch.run_blind_baseline(X_test, y_test)

    assert metrics["halted"] == 0
    assert metrics["attempted"] == 10
    assert metrics["avg_classical_latency_s"] == 0.0


def test_intelligent_policy_halts_when_model_predicts_below_threshold():
    """A model that always predicts 0.1 (well below threshold) must cause
    every sample to be halted, with zero QPU attempts."""
    node = QuantumRepeaterNode(shots=64, seed=7)
    model = _ConstantModel(0.1)
    orch = DigitalTwinOrchestrator(model=model, quantum_node=node, threshold=0.65)

    X_test = torch.rand(8, 5, 3)
    y_test = torch.rand(8, 1)
    metrics = orch.run_intelligent(X_test, y_test)

    assert metrics["halted"] == 8
    assert metrics["attempted"] == 0
    assert metrics["useful_pairs"] == 0


def test_intelligent_policy_attempts_when_model_predicts_above_threshold():
    """A model that always predicts 0.9 (well above threshold) must cause
    every sample to be attempted (never halted)."""
    node = QuantumRepeaterNode(shots=64, seed=7)
    model = _ConstantModel(0.9)
    orch = DigitalTwinOrchestrator(model=model, quantum_node=node, threshold=0.65)

    X_test = torch.rand(8, 5, 3)
    y_test = torch.rand(8, 1)
    metrics = orch.run_intelligent(X_test, y_test)

    assert metrics["halted"] == 0
    assert metrics["attempted"] == 8


def test_intelligent_latency_is_measured_and_nonzero():
    """The isolated profiling window should record a strictly positive
    (if tiny) forward-pass latency."""
    node = QuantumRepeaterNode(shots=64, seed=7)
    model = _ConstantModel(0.9)
    orch = DigitalTwinOrchestrator(model=model, quantum_node=node, threshold=0.65)

    X_test = torch.rand(5, 5, 3)
    y_test = torch.rand(5, 1)
    metrics = orch.run_intelligent(X_test, y_test)
    assert metrics["avg_measured_inference_latency_s"] > 0.0
    assert metrics["avg_classical_latency_s"] == 0.0


def test_configured_deployment_latency_drives_physics_not_measured_latency():
    """Master audit Section 23 regression guard: when deployment_latency_s
    is provided, the physical decoherence must use that CONFIGURED value
    (identical every step), while measured_inference_latency_s is recorded
    separately and is free to vary with machine timing noise."""
    import torch
    from quantum_twin.ml.lstm import EdgeLSTM
    from quantum_twin.control.admission import DigitalTwinOrchestrator
    from quantum_twin.simulation.network import QuantumRepeaterNode

    torch.manual_seed(0)
    model = EdgeLSTM(input_size=4, hidden_size=8)
    X_test = torch.rand(5, 5, 4)
    y_test = torch.rand(5, 1)

    node = QuantumRepeaterNode(shots=32, seed=7)
    orch = DigitalTwinOrchestrator(model=model, quantum_node=node, threshold=0.0)
    metrics = orch.run_intelligent(X_test, y_test, deployment_latency_s=500e-6)

    assert metrics["configured_deployment_latency_s"] == 500e-6
    physical_latencies = [entry["latency_s"] for entry in orch.log]
    assert all(abs(lat - 500e-6) < 1e-12 for lat in physical_latencies), (
        "Physical latency must be the CONFIGURED value on every step, not the measured one."
    )
    # measured_inference_latency_s must be present and NOT forced to the configured value
    assert all("measured_inference_latency_s" in entry for entry in orch.log)


def test_default_physics_latency_is_configured_not_wall_clock():
    """Omitting deployment latency must never inject wall-clock noise."""
    import torch
    from quantum_twin.ml.lstm import EdgeLSTM
    from quantum_twin.control.admission import DigitalTwinOrchestrator
    from quantum_twin.simulation.network import QuantumRepeaterNode

    torch.manual_seed(1)
    model = EdgeLSTM(input_size=4, hidden_size=8)
    X_test = torch.rand(5, 5, 4)
    y_test = torch.rand(5, 1)

    node = QuantumRepeaterNode(shots=32, seed=7)
    orch = DigitalTwinOrchestrator(model=model, quantum_node=node, threshold=0.0)
    metrics = orch.run_intelligent(X_test, y_test)

    assert metrics["configured_deployment_latency_s"] == 0.0
    for entry in orch.log:
        assert entry["latency_s"] == 0.0
        assert entry["measured_inference_latency_s"] > 0.0


def test_orchestrator_uses_input_fidelity_and_logs_purified_output():
    node = QuantumRepeaterNode(depol_prob=0.0, seed=11)
    orch = DigitalTwinOrchestrator(_ConstantModel(0.9), node, threshold=0.65)
    X_test = torch.rand(2, 5, 3)
    y_test = torch.tensor([[0.70], [0.90]])
    orch.run_intelligent(X_test, y_test, purification_uniforms=[0.0, 0.0])
    assert orch.log[0]["F_after"] != orch.log[1]["F_after"]
    import pytest
    assert orch.log[0]["F1_before"] == pytest.approx(0.70)
    assert orch.log[1]["F1_before"] == pytest.approx(0.90)


def test_blind_and_intelligent_can_share_event_randomness():
    X_test = torch.rand(3, 5, 3)
    y_test = torch.tensor([[0.70], [0.75], [0.80]])
    uniforms = [0.1, 0.9, 0.2]
    intelligent = DigitalTwinOrchestrator(
        _ConstantModel(0.9), QuantumRepeaterNode(depol_prob=0.0, seed=3), threshold=0.0
    )
    blind = DigitalTwinOrchestrator(
        None, QuantumRepeaterNode(depol_prob=0.0, seed=3), threshold=0.0
    )
    intelligent.run_intelligent(X_test, y_test, purification_uniforms=uniforms)
    blind.run_blind_baseline(X_test, y_test, purification_uniforms=uniforms)
    assert [row["sampled_success"] for row in intelligent.log] == [
        row["sampled_success"] for row in blind.log
    ]
