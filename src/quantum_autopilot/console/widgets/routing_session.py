"""Checkpoint-specific routing inspection, isolated from runtime control."""
from ...model_intake.routing_session import (
    MODEL_IDS, demo_routing_input, evaluate_routing_session, export_routing_evaluation, load_routing_input,
)
from .sync_session import SyncSession


class RoutingSession(SyncSession):
    window_title = "OrionQ · Routing · Offline evaluation"
    heading = "ROUTING · OFFLINE DECISION"
    input_description = "Named simulator features and ordered routing candidates JSON"
    output_description = "Routing scores, selection and declared reference; no executed action"
    scope = ("SIMULATION ONLY · 11 state / 23 action features. Nominal stochastic; long-deadline greedy. "
             "Candidate legality is not certified. Scores are not fidelity or success probabilities. No executor.")
    export_filename = "routing-evaluation.json"

    def __init__(self, model_id, parent=None):
        if model_id not in MODEL_IDS:
            raise ValueError("unknown routing session checkpoint")
        self.model_id = model_id
        super().__init__(parent)

    def _demo_input(self):
        return demo_routing_input(self.model_id)

    def _evaluate(self, path, text):
        return evaluate_routing_session(self.model_id, path, text)

    def _load_input(self, path):
        return load_routing_input(self.model_id, path)

    _export_result = staticmethod(export_routing_evaluation)
