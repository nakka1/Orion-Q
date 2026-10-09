"""Checkpoint-specific compensation sessions with the existing offline lifecycle."""
from ...model_intake.compensation_session import (
    MODEL_IDS, demo_compensation_input, evaluate_compensation_session,
    export_compensation_evaluation, load_compensation_input,
)
from .sync_session import SyncSession


class CompensationSession(SyncSession):
    window_title = "OrionQ · Polarisation compensation · Offline evaluation"
    heading = "POLARISATION · OFFLINE COMPENSATION"
    input_description = "Simulated SO(3) rotations and named covariates JSON"
    output_description = "Warm-up, polarisation prediction and persistence; optional angular error"
    scope = ("SIMULATION ONLY · 1-16 samples at 2 ms; +40 ms horizon. First 15 use persistence. "
             "Declared regime, no automatic OOD validation. No actuator or Bell-fidelity claim.")
    export_filename = "compensation-evaluation.json"

    def __init__(self, model_id, parent=None):
        if model_id not in MODEL_IDS:
            raise ValueError("unknown compensation session checkpoint")
        self.model_id = model_id
        super().__init__(parent)

    def _demo_input(self):
        return demo_compensation_input(self.model_id)

    def _evaluate(self, path, text):
        return evaluate_compensation_session(self.model_id, path, text)

    def _load_input(self, path):
        return load_compensation_input(self.model_id, path)

    _export_result = staticmethod(export_compensation_evaluation)
