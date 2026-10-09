"""Single-decision QKD actor session, not a rate forecast or device command."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .sync_session import MAX_INPUT_BYTES, _constant, _number, _unique

MODEL_ID = "qkd.keyrate.actor"
SCHEMA = "orion.optional-keyrate-input.v1"


def parse_keyrate_input(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("input must be UTF-8 JSON of at most 64 KiB")
    request = json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)
    fields = {"schema", "evidence", "stream_id", "decision_timestamp_s", "previous_period_end_s",
              "seed", "protocol", "link_span_km", "settings", "calibration", "previous"}
    if not isinstance(request, dict) or set(request) != fields or request["schema"] != SCHEMA:
        raise ValueError("invalid key-rate input schema or fields")
    if request["evidence"] != "simulated_offline":
        raise ValueError("only simulated_offline input is admitted; measured claims are refused")
    stream = request["stream_id"]
    if not isinstance(stream, str) or not stream.strip() or len(stream) > 160 or any(ord(c) < 32 for c in stream):
        raise ValueError("stream_id must be nonempty text up to 160 characters without control characters")
    if type(request["seed"]) is not int or not 0 <= request["seed"] <= 2**32 - 1:
        raise ValueError("seed must be an integer from 0 to 4294967295")
    span = _number(request["link_span_km"])
    if request["protocol"] != "decoy_state_bb84" or not 25 <= span <= 80:
        raise ValueError("reviewed model requires decoy_state_bb84 and a declared 25-80 km span")
    stamp = _number(request["decision_timestamp_s"])
    if stamp < 0:
        raise ValueError("decision timestamp must be nonnegative")
    end = request["previous_period_end_s"]
    if request["previous"] is None:
        if end is not None:
            raise ValueError("previous_period_end_s must be null when previous observables are absent")
    elif end is None or not 0 <= _number(end) <= stamp:
        raise ValueError("previous period must end at or before the decision timestamp")

    # Typed validation shares the existing adapter's box and feature arithmetic.
    # These imports do not construct a model or open its archive.
    from .keyrate import LinkCalibration, PeriodObservables, TransmitterSettings, build_observation

    objects = []
    for key, cls in (("settings", TransmitterSettings), ("calibration", LinkCalibration),
                     ("previous", PeriodObservables)):
        value = request[key]
        if key == "previous" and value is None:
            objects.append(None)
            continue
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError(f"{key}: invalid typed fields")
        objects.append(cls(**{name: _number(number) for name, number in value.items()}))
    settings, calibration, previous = objects
    build_observation(previous, settings, calibration)
    return request, settings, calibration, previous


def load_keyrate_input(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds 64 KiB")
    text = raw.decode("utf-8")
    parse_keyrate_input(text)
    return text


def demo_keyrate_input():
    """Hand-authored plausible values for UI demonstration, not a physical experiment."""
    return json.dumps({
        "schema": SCHEMA, "evidence": "simulated_offline", "stream_id": "demo/hand-authored/keyrate",
        "decision_timestamp_s": 1.0, "previous_period_end_s": 1.0, "seed": 1234,
        "protocol": "decoy_state_bb84", "link_span_km": 50.0,
        "settings": {"signal_intensity": .5, "decoy_intensity": .1, "vacuum_probability": .1,
                     "signal_probability": .7, "clock_rate_hz": 1e9},
        "calibration": {"calibration_gain": .001, "calibration_rate_hz": 1e5,
                        "dark_count_probability": 1e-6, "misalignment_error": .02, "dead_time_s": 50e-9},
        "previous": {"signal_gain": .001, "decoy_gain": .0002, "vacuum_gain": 1e-6,
                     "signal_error": .02, "decoy_error": .03, "single_photon_yield_lower": .001,
                     "single_photon_error_upper": .04, "key_rate_hz": 1e5},
    }, indent=2)


@dataclass(frozen=True)
class KeyRateEvaluation:
    evaluated_at_utc: str
    input_json: str
    input_sha256: str
    stream_id: str
    decision_timestamp_s: float
    archive_sha256: str
    seed: int
    numpy_version: str
    observation_source: str
    recommendation: dict
    baseline_settings: dict
    candidates: tuple[dict, ...]
    model_id: str = field(default=MODEL_ID, init=False)
    schema: str = field(default="orion.optional-keyrate-evaluation.v1", init=False)
    rng_algorithm: str = field(default="numpy.random.PCG64", init=False)
    baseline_action: str = field(default="hold current settings", init=False)
    outcome_comparison: str = field(default="unavailable; no counterfactual rate outcomes evaluated", init=False)
    evidence: str = field(default="simulated_offline", init=False)
    physical_action_performed: bool = field(default=False, init=False)

    def serialized(self):
        return json.dumps(asdict(self), indent=2, ensure_ascii=False, allow_nan=False)

    def describe(self):
        rec = self.recommendation
        settings = rec["proposed_settings"]
        comparisons = "\n".join(f"{key}: {value:.9g} -> {settings[key]:.9g}"
                                for key, value in self.baseline_settings.items())
        greedy = self.candidates[rec["greedy_index"]]["action_name"]
        return ("SIMULATED OFFLINE · RECOMMENDATION ONLY · NO CONTROL\n\n"
                f"Sampled action: {rec['action_name']}\nGreedy action (not substituted): {greedy}\n"
                f"Seed: {self.seed} · PCG64 · temperature: {rec['temperature']}\n\n"
                f"Observation source: {self.observation_source}\n\n"
                "HOLD-CURRENT BASELINE -> RECOMMENDED PARAMETERS\n" + comparisons + "\n\n"
                "Intensities: mean photons/pulse; probabilities: fractions; clock: Hz.\n"
                "Policy probabilities are sampling weights, not probabilities of success.\n"
                "No next-period key-rate forecast, generated keys, finite-key certificate, Bell fidelity "
                "or operational gain is claimed. Parameter differences are NOT performance differences.\n\n"
                "REPRODUCIBLE RECORD\n" + self.serialized())


def evaluate_keyrate_session(archive_path, input_text):
    request, settings, calibration, previous = parse_keyrate_input(input_text)
    import numpy as np
    from .catalog import open_optional_model
    from .keyrate import ACTIONS

    model = open_optional_model(MODEL_ID, archive_path=archive_path)
    recommendation = model.recommend(
        previous, settings, calibration, domain="simulated_offline", link_span_km=request["link_span_km"],
        protocol=request["protocol"], rng=np.random.Generator(np.random.PCG64(request["seed"])),
    )
    candidates = tuple({"index": i, "action_name": action, "probability": recommendation.probabilities[i],
                        "proposed_settings": asdict(model.preview_action(settings, i, domain="simulated_offline"))}
                       for i, action in enumerate(ACTIONS))
    result = KeyRateEvaluation(
        datetime.now(timezone.utc).isoformat(), input_text, hashlib.sha256(input_text.encode("utf-8")).hexdigest(),
        request["stream_id"], float(request["decision_timestamp_s"]), recommendation.archive_sha256,
        request["seed"], np.__version__, "calibration_bootstrap" if previous is None else "previous_period",
        asdict(recommendation), asdict(settings), candidates,
    )
    result.serialized()  # Enforce finite, JSON-safe output before showing/exporting it.
    return result


def export_keyrate_evaluation(result, path):
    raw = result.serialized().encode("utf-8")
    with Path(path).open("xb") as stream:
        stream.write(raw)
