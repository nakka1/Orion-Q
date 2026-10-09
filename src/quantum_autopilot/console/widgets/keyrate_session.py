"""Typed QKD actor session sharing lifecycle, not data, with synchronization."""
from ...model_intake.keyrate_session import (
    MODEL_ID, demo_keyrate_input, evaluate_keyrate_session, export_keyrate_evaluation, load_keyrate_input,
)
from .sync_session import SyncSession


class KeyRateSession(SyncSession):
    model_id = MODEL_ID
    window_title = "OrionQ · QKD transmitter recommendation · Offline evaluation"
    heading = "QKD TRANSMITTER · OFFLINE RECOMMENDATION"
    input_description = "Simulated QKD actor input JSON"
    output_description = "Recommended parameters versus hold-current baseline, not key-rate performance"
    scope = ("SIMULATION ONLY · Recommendation vs hold-current parameters. "
             "No key-rate forecast, performance gain or device command. Explicit seed; no automatic execution.")
    export_filename = "keyrate-recommendation.json"
    _demo_input = staticmethod(demo_keyrate_input)
    _evaluate = staticmethod(evaluate_keyrate_session)
    _load_input = staticmethod(load_keyrate_input)
    _export_result = staticmethod(export_keyrate_evaluation)
