"""Bounded optional polarisation session: warm-up and one final forecast, no actuation."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from .sync_session import MAX_INPUT_BYTES, _constant, _number, _unique

MODEL_IDS = tuple(f"qnet.compensation.{point}.{kind}" for point in ("calibrado", "desenvolvimento")
                  for kind in ("ridge", "mlp"))
SCHEMA = "orion.optional-compensation-input.v1"


def _validate_rotation(value):
    if not isinstance(value, list) or len(value) != 3 or any(not isinstance(row, list) or len(row) != 3 for row in value):
        raise ValueError("rotation requires a numeric 3 by 3 matrix")
    from .compensation import _rotation
    _rotation([[_number(item) for item in row] for row in value])


def parse_compensation_input(model_id, text):
    if model_id not in MODEL_IDS:
        raise ValueError("unknown compensation checkpoint")
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("input must be UTF-8 JSON of at most 64 KiB")
    request = json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)
    fields = {"schema", "evidence", "stream_id", "model_id", "period_s", "horizon_s",
              "monitoring_photons_per_basis", "samples", "reference"}
    if not isinstance(request, dict) or set(request) != fields or request["schema"] != SCHEMA:
        raise ValueError("invalid compensation input schema or fields")
    if request["model_id"] != model_id:
        raise ValueError("checkpoint differs from selected model; no automatic substitution")
    if request["evidence"] != "simulated_offline":
        raise ValueError("only simulated_offline input is admitted; measured claims are refused")
    stream = request["stream_id"]
    if not isinstance(stream, str) or not stream.strip() or len(stream) > 160 or any(ord(c) < 32 for c in stream):
        raise ValueError("stream_id must be nonempty text up to 160 characters without control characters")
    if _number(request["period_s"]) != .002 or _number(request["horizon_s"]) != .04:
        raise ValueError("compensation requires 2 ms cadence and 40 ms horizon")
    if type(request["monitoring_photons_per_basis"]) is not int or request["monitoring_photons_per_basis"] != 400:
        raise ValueError("checkpoint requires 400 monitoring photons per basis")
    samples = request["samples"]
    if not isinstance(samples, list) or not 1 <= len(samples) <= 16:
        raise ValueError("session requires 1 to 16 consecutive samples")
    # Data-only validation before model construction or archive access.
    from .compensation import CompensationReplayModel

    previous_time = None
    covariate_fields = ("temperature_c", "temperature_rate_k_s", "vibration_rms_rad")
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {"timestamp_s", "rotation", "covariates"}:
            raise ValueError("sample requires timestamp_s, rotation and covariates only")
        stamp = _number(sample["timestamp_s"])
        if stamp < 0 or (previous_time is not None and abs(stamp - previous_time - .002) > 1e-9):
            raise ValueError("timestamps must be nonnegative and consecutive at 2 ms cadence")
        previous_time = stamp
        _validate_rotation(sample["rotation"])
        covariates = sample["covariates"]
        if not isinstance(covariates, dict) or set(covariates) != set(covariate_fields):
            raise ValueError("invalid named covariates")
        CompensationReplayModel._covariates([_number(covariates[name]) for name in covariate_fields])
    target_time = previous_time + .04
    if not math.isfinite(target_time) or target_time <= previous_time:
        raise ValueError("timestamp precision cannot represent the 40 ms horizon")
    reference = request["reference"]
    if reference is not None:
        if not isinstance(reference, dict) or set(reference) != {"target_timestamp_s", "rotation"}:
            raise ValueError("reference requires final target timestamp and rotation only")
        if abs(_number(reference["target_timestamp_s"]) - target_time) > 1e-9:
            raise ValueError("reference must match the final 40 ms forecast horizon")
        _validate_rotation(reference["rotation"])
    return request


def load_compensation_input(model_id, path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds 64 KiB")
    text = raw.decode("utf-8")
    parse_compensation_input(model_id, text)
    return text


def demo_compensation_input(model_id):
    if model_id not in MODEL_IDS:
        raise ValueError("unknown compensation checkpoint")

    def rotation(angle):
        c, s = math.cos(angle), math.sin(angle)
        return [[c, -s, 0.], [s, c, 0.], [0., 0., 1.]]

    return json.dumps({
        "schema": SCHEMA, "evidence": "simulated_offline", "stream_id": "demo/hand-authored/polarisation",
        "model_id": model_id, "period_s": .002, "horizon_s": .04, "monitoring_photons_per_basis": 400,
        "samples": [{"timestamp_s": i * .002, "rotation": rotation(i * .001),
                     "covariates": {"temperature_c": 20., "temperature_rate_k_s": 0., "vibration_rms_rad": .3}}
                    for i in range(16)],
        "reference": {"target_timestamp_s": .07, "rotation": rotation(.035)},
    }, indent=2)


@dataclass(frozen=True)
class CompensationEvaluation:
    model_id: str
    evaluated_at_utc: str
    input_json: str
    input_sha256: str
    stream_id: str
    archive_sha256: str
    steps: tuple[dict, ...]
    reference_rotation: list | None
    model_angular_error_rad: float | None
    persistence_angular_error_rad: float | None
    schema: str = field(default="orion.optional-compensation-evaluation.v1", init=False)
    evidence: str = field(default="simulated_offline", init=False)
    physical_action_performed: bool = field(default=False, init=False)

    def serialized(self):
        return json.dumps(asdict(self), indent=2, ensure_ascii=False, allow_nan=False)

    def describe(self):
        last = self.steps[-1]
        warmup = sum(step["status"] == "warmup_persistence" for step in self.steps)
        errors = ("Reference: NOT PROVIDED; angular errors unavailable." if self.reference_rotation is None else
                  f"Final output angular error: {self.model_angular_error_rad * 1000:.6g} mrad\n"
                  f"Persistence angular error: {self.persistence_angular_error_rad * 1000:.6g} mrad")
        return ("SIMULATED OFFLINE · POLARISATION · NO ACTUATOR\n\n"
                f"Checkpoint: {self.model_id}\nFinal status: {last['status']} ({len(self.steps)}/16 samples)\n"
                f"Warm-up outputs: {warmup}; learned outputs: {len(self.steps) - warmup}\n"
                f"Final target: {last['target_timestamp_s']:.9g} s (+40 ms)\n{errors}\n\n"
                "First 15 outputs are persistence, NOT learned forecasts. Errors score only the final output; "
                "an optional future rotation never enters the predictor.\n"
                "Regime is declared, not automatically detected or certified in-domain. "
                "No closed-loop gain, QBER/SKR improvement, Bell fidelity or actuator command is implied.\n\n"
                "REPRODUCIBLE RECORD (matrices in SO(3), errors in radians)\n" + self.serialized())


def evaluate_compensation_session(model_id, archive_path, input_text):
    request = parse_compensation_input(model_id, input_text)
    import numpy as np
    from .catalog import open_optional_model
    from .compensation_evaluation import _angles

    model = open_optional_model(model_id, archive_path=archive_path)
    point = model_id.split(".")[2]
    steps = []
    for sample in request["samples"]:
        cov = sample["covariates"]
        result = model.predict(
            sample["timestamp_s"], sample["rotation"],
            [cov["temperature_c"], cov["temperature_rate_k_s"], cov["vibration_rms_rad"]],
            domain="simulated_offline", operating_point=point, stream_id=request["stream_id"],
            horizon_s=request["horizon_s"], monitoring_photons_per_basis=request["monitoring_photons_per_basis"],
        )
        steps.append(asdict(result))
    reference = request["reference"]
    actual = None if reference is None else reference["rotation"]
    errors = (None, None) if actual is None else (
        float(_angles(np.asarray(result.predicted), np.asarray(actual))),
        float(_angles(np.asarray(result.persistence), np.asarray(actual))),
    )
    report = CompensationEvaluation(
        model_id, datetime.now(timezone.utc).isoformat(), input_text, hashlib.sha256(input_text.encode()).hexdigest(),
        request["stream_id"], result.archive_sha256, tuple(steps), actual, *errors,
    )
    report.serialized()
    return report


def export_compensation_evaluation(result, path):
    raw = result.serialized().encode("utf-8")
    with Path(path).open("xb") as stream:
        stream.write(raw)
