"""Offline attack-detector session: one simulated session log in, per-period alarms out, no action."""
from ...model_intake.detector_session import (
    MODEL_ID, demo_detector_input, evaluate_detector_session, export_detector_evaluation, load_detector_input,
)
from .sync_session import SyncSession


class DetectorSession(SyncSession):
    model_id = MODEL_ID
    window_title = "OrionQ · QKD attack detector · Offline evaluation"
    heading = "QKD ATTACK DETECTOR · OFFLINE SESSION"
    input_description = "Simulated QKD session log JSON (17 public columns)"
    input_limit = "256 KiB"
    output_description = "Per-period calls and alarms versus the QBER alarm baseline; no executed action"
    scope = ("SIMULATION ONLY · Safe export of the author's detector (pickle hashed, never loaded). "
             "20-70 km, 1 s periods, trained transmitter settings. Alarms are labels for an operator, "
             "not security proofs, key decisions or device commands.")
    export_filename = "detector-evaluation.json"
    _demo_input = staticmethod(demo_detector_input)
    _evaluate = staticmethod(evaluate_detector_session)
    _load_input = staticmethod(load_detector_input)
    _export_result = staticmethod(export_detector_evaluation)
