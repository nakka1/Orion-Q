"""quantum_twin.ml -- predictive models, losses, and calibration.

LAZY LOADING (ninety-second addendum, TCC item 6): applies the same
PEP 562 fix the eighty-fifth/ninety-first addenda applied to
quantum_twin/__init__.py and quantum_twin/simulation/__init__.py,
proactively to THIS subpackage -- per the ninety-first addendum's own
"Honest limitations" note, which identified this exact file as still
carrying the same latent eager-import pattern (verified there by direct
inspection: 3 eager import lines) before any migration forced
the issue reactively.
"""

import importlib

_NAME_TO_SUBMODULE = {
    "EdgeLSTM": "quantum_twin.ml.lstm",
    "train_edge_lstm": "quantum_twin.ml.lstm",
    "train_edge_lstm_robust": "quantum_twin.ml.lstm",
    "EnsemblePredictor": "quantum_twin.ml.lstm",
    "train_edge_lstm_ensemble": "quantum_twin.ml.lstm",
    "EdgeLSTMDualHead": "quantum_twin.ml.dual_head",
    "DualHeadOrchestratorAdapter": "quantum_twin.ml.dual_head",
    "train_dual_head": "quantum_twin.ml.dual_head",
    "train_dual_head_robust": "quantum_twin.ml.dual_head",
    "EdgeLSTMProbabilistic": "quantum_twin.ml.probabilistic",
    "train_edge_lstm_probabilistic": "quantum_twin.ml.probabilistic",
    "EnsembleProbabilisticPredictor": "quantum_twin.ml.probabilistic",
    "train_ensemble_probabilistic": "quantum_twin.ml.probabilistic",
    "CS_MSELoss": "quantum_twin.ml.losses",
    "DualHeadLoss": "quantum_twin.ml.losses",
    "GaussianNLLWithCostSensitivity": "quantum_twin.ml.losses",
    "evaluate_calibration": "quantum_twin.ml.calibration",
    "calibrate_sigma_temperature": "quantum_twin.ml.calibration",
    "OpticalEdgeLSTM": "quantum_twin.ml.optical_sequence",
    "OpticalEdgeGRU": "quantum_twin.ml.optical_sequence",
    "OpticalEdgeTCN": "quantum_twin.ml.optical_sequence",
    "make_optical_sequence_model": "quantum_twin.ml.optical_sequence",
    "train_optical_sequence_model": "quantum_twin.ml.optical_sequence",
    "predict_optical_sequence_model": "quantum_twin.ml.optical_sequence",
    "QKDDualHead": "quantum_twin.ml.qkd_multihead",
    "QKDTriHead": "quantum_twin.ml.qkd_multihead",
    "QKDMultiHeadLoss": "quantum_twin.ml.qkd_multihead",
    "build_qkd_dominance_pairs": "quantum_twin.ml.qkd_multihead",
    "train_qkd_multihead": "quantum_twin.ml.qkd_multihead",
    "predict_qkd_multihead": "quantum_twin.ml.qkd_multihead",
    "QKDSingleTaskMLP": "quantum_twin.ml.qkd_multihead",
    "train_qkd_single_task": "quantum_twin.ml.qkd_multihead",
    "predict_qkd_single_task": "quantum_twin.ml.qkd_multihead",
}


def __getattr__(name):
    """PEP 562: called only when normal attribute lookup fails -- i.e.
    the FIRST time this specific name is accessed. Imports ONLY the one
    submodule that name actually lives in, not the others."""
    if name in _NAME_TO_SUBMODULE:
        module = importlib.import_module(_NAME_TO_SUBMODULE[name])
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'quantum_twin.ml' has no attribute '{name}'")


def __dir__():
    return sorted(_NAME_TO_SUBMODULE.keys())


__all__ = list(_NAME_TO_SUBMODULE.keys())
