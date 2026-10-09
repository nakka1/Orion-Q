"""Optional fixed-plant allocation snapshots, independent of runtime controls."""
from ...model_intake.allocation_session import (
    MODEL_IDS, demo_allocation_input, evaluate_allocation_session, export_allocation_evaluation, load_allocation_input,
)
from .sync_session import SyncSession


class AllocationSession(SyncSession):
    window_title = "OrionQ · Hybrid allocation · Offline evaluation"
    heading = "HYBRID ALLOCATION · OFFLINE SNAPSHOT"
    input_description = "Fixed eleven-link allocation snapshot and drift history JSON"
    output_description = "Per-link admission and persistence comparison; no executed action"
    scope = ("SIMULATION ONLY · Fixed 11-link single-photon BB84 plant; 5 s steps, 30 s latency, 150 s forecast. "
             "No queue execution or topology remapping. Key budgets are NOT finite-key certificates.")
    export_filename = "allocation-evaluation.json"

    def __init__(self, model_id, parent=None):
        if model_id not in MODEL_IDS:
            raise ValueError("unknown allocation session checkpoint")
        self.model_id = model_id
        super().__init__(parent)

    def _demo_input(self):
        return demo_allocation_input(self.model_id)

    def _evaluate(self, path, text):
        return evaluate_allocation_session(self.model_id, path, text)

    def _load_input(self, path):
        return load_allocation_input(self.model_id, path)

    _export_result = staticmethod(export_allocation_evaluation)
