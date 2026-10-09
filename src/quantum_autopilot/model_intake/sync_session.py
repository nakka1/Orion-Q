"""Bounded single-window synchronization evaluation; simulated evidence only."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

MODEL_ID = "qkd.sync.ridge.1s.h2s"
SCHEMA = "orion.optional-sync-input.v1"
MAX_INPUT_BYTES = 64 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")


def _number(value):
    if type(value) not in (float, int):
        raise ValueError("expected a finite number, not a boolean or string")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError("number outside finite float range") from exc
    if not math.isfinite(number):
        raise ValueError("expected a finite number")
    return number


def parse_sync_input(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("input must be UTF-8 JSON of at most 64 KiB")
    value = json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)
    fields = {"schema", "evidence", "stream_id", "timestamps_s", "offsets_s", "temperatures_c", "reference"}
    if not isinstance(value, dict) or set(value) != fields or value["schema"] != SCHEMA:
        raise ValueError("invalid synchronization input schema or fields")
    if value["evidence"] != "simulated_offline":
        raise ValueError("only simulated_offline input is admitted; measured claims are refused")
    stream = value["stream_id"]
    if not isinstance(stream, str) or not stream.strip() or len(stream) > 160 or any(ord(c) < 32 for c in stream):
        raise ValueError("stream_id must be nonempty text up to 160 characters, without control characters")
    for key in ("timestamps_s", "offsets_s", "temperatures_c"):
        if not isinstance(value[key], list) or len(value[key]) != 8:
            raise ValueError(f"{key}: exactly eight samples required")
        value[key] = [_number(item) for item in value[key]]
    times = value["timestamps_s"]
    if any(abs((b - a) - 1.0) > 1e-6 for a, b in zip(times, times[1:])):
        raise ValueError("timestamps require ordered one-second cadence")
    reference = value["reference"]
    if reference is not None:
        if not isinstance(reference, dict) or set(reference) != {"target_timestamp_s", "offset_s"}:
            raise ValueError("reference must be null or a target timestamp and simulated offset")
        reference = {key: _number(item) for key, item in reference.items()}
        if abs(reference["target_timestamp_s"] - (times[-1] + 2.0)) > 1e-6:
            raise ValueError("reference must refer to the two-second forecast horizon")
        value["reference"] = reference
    return value


def load_sync_input(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds 64 KiB")
    text = raw.decode("utf-8")
    parse_sync_input(text)
    return text


def demo_sync_input():
    """Hand-authored demonstration, not measured data or a performance benchmark."""
    return json.dumps({"schema": SCHEMA, "evidence": "simulated_offline",
                       "stream_id": "demo/hand-authored/sync",
                       "timestamps_s": list(range(8)),
                       "offsets_s": [i * 1e-10 for i in range(8)],
                       "temperatures_c": [20.0] * 8,
                       "reference": {"target_timestamp_s": 9.0, "offset_s": 9e-10}}, indent=2)


@dataclass(frozen=True)
class SyncEvaluation:
    schema: str
    evaluated_at_utc: str
    input_json: str
    input_sha256: str
    stream_id: str
    archive_sha256: str
    model_id: str
    target_timestamp_s: float
    predicted_offset_s: float
    persistence_offset_s: float
    reference_offset_s: float | None
    model_absolute_error_s: float | None
    persistence_absolute_error_s: float | None
    evidence: str = "simulated_offline"
    physical_action_performed: bool = False

    def serialized(self):
        return json.dumps(asdict(self), indent=2, ensure_ascii=False, allow_nan=False)

    def describe(self):
        errors = ("Reference: NOT PROVIDED; prediction errors are unavailable." if self.reference_offset_s is None else
                  f"Simulated reference: {self.reference_offset_s * 1e9:.6g} ns\n"
                  f"Model absolute error: {self.model_absolute_error_s * 1e9:.6g} ns\n"
                  f"Persistence absolute error: {self.persistence_absolute_error_s * 1e9:.6g} ns")
        return (f"SIMULATED OFFLINE · SINGLE WINDOW · NO CONTROL\n\n"
                f"Model: {self.model_id}\nTarget timestamp: {self.target_timestamp_s:.9g} s (+2 s)\n"
                f"Predicted offset: {self.predicted_offset_s * 1e9:.6g} ns\n"
                f"Persistence offset: {self.persistence_offset_s * 1e9:.6g} ns\n{errors}\n\n"
                "The future reference was NOT supplied to the predictor. No training or tuning. "
                "One window does not establish predictive gain, experimental validity or Bell fidelity.\n\n"
                "REPRODUCIBLE RECORD (units in seconds)\n" + self.serialized())


def evaluate_sync(archive_path, input_text):
    request = parse_sync_input(input_text)  # refuse malformed input before reading weights
    from .catalog import open_optional_model

    model = open_optional_model(MODEL_ID, archive_path=archive_path)
    prediction = model.predict(request["timestamps_s"], request["offsets_s"], request["temperatures_c"],
                               domain="simulated_offline")
    reference = request["reference"]
    actual = None if reference is None else reference["offset_s"]
    errors = (None, None) if actual is None else (
        abs(prediction.predicted_offset_s - actual), abs(prediction.persistence_offset_s - actual))
    for number in (prediction.predicted_offset_s, prediction.persistence_offset_s, actual, *errors):
        if number is not None and (not math.isfinite(number) or not math.isfinite(number * 1e9)):
            raise ValueError("result outside finite display range")
    return SyncEvaluation(
        "orion.optional-sync-evaluation.v1", datetime.now(timezone.utc).isoformat(), input_text,
        hashlib.sha256(input_text.encode("utf-8")).hexdigest(), request["stream_id"],
        prediction.archive_sha256, prediction.model_id, prediction.target_timestamp_s,
        prediction.predicted_offset_s, prediction.persistence_offset_s, actual, *errors,
    )


def export_sync_evaluation(result, path):
    """Explicit, exclusive creation: never overwrite an existing report or input."""
    raw = result.serialized().encode("utf-8")
    with Path(path).open("xb") as stream:
        stream.write(raw)
