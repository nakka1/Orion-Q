"""Explicit decoy-state actor shadow bridge; no settings update or executor."""
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path

from .contracts import NetworkStateSnapshot
from .optional_sync import MAX_SNAPSHOT_BYTES, _hash, _identifier, _json, _time, load_sync_shadow_snapshot
from quantum_autopilot.core import EvidenceKind
from quantum_autopilot.model_intake.keyrate_session import MODEL_ID, SCHEMA, evaluate_keyrate_session, parse_keyrate_input
from quantum_autopilot.model_intake.sync_session import _constant, _unique

TELEMETRY_KEY = "optional_keyrate_window"
WINDOW_SCHEMA = "orion.sandbox-keyrate-window.v1"
MAX_PREVIOUS_AGE_S = 1.
UNITS = {"intensity": "mean_photons_per_pulse", "probability": "fraction", "gain": "fraction",
         "error": "fraction", "yield": "fraction", "clock": "Hz", "key_rate": "bit/s", "time": "s", "distance": "km"}
WINDOW_FIELDS = {"schema", "evidence", "source_id", "campaign_id", "stream_id", "node_id", "clock_id",
                 "origin_timestamp_utc", "decision_timestamp_utc", "seed", "units", "observation_mode", "context", "previous"}
CONTEXT_FIELDS = {"link_id", "transmitter_id", "receiver_id", "protocol", "link_span_km", "settings_version",
                  "settings_timestamp_utc", "settings", "calibration_id", "calibration_timestamp_utc",
                  "calibration_valid_until_utc", "calibration"}


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != names:
        raise ValueError("invalid key-rate fields; expected " + ", ".join(sorted(names)))


def _request(value):
    context, previous = value["context"], value["previous"]
    origin = _time(value["origin_timestamp_utc"])
    text = _json({"schema": SCHEMA, "evidence": "simulated_offline", "stream_id": value["stream_id"],
                  "decision_timestamp_s": (_time(value["decision_timestamp_utc"]) - origin).total_seconds(),
                  "previous_period_end_s": None if previous is None else
                      (_time(previous["end_timestamp_utc"]) - origin).total_seconds(),
                  "seed": value["seed"], "protocol": context["protocol"], "link_span_km": context["link_span_km"],
                  "settings": context["settings"], "calibration": context["calibration"],
                  "previous": None if previous is None else previous["observables"]})
    parse_keyrate_input(text)
    return text


@dataclass(frozen=True)
class KeyRateShadowWindow:
    """Validated immutable JSON; nested data is detached on every read."""
    payload_json: str

    def __post_init__(self):
        if not isinstance(self.payload_json, str) or len(self.payload_json.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
            raise ValueError("key-rate window must be bounded JSON")
        value = json.loads(self.payload_json, object_pairs_hook=_unique, parse_constant=_constant)
        _fields(value, WINDOW_FIELDS)
        if (value["schema"], value["evidence"], value["units"]) != (WINDOW_SCHEMA, "simulated", UNITS):
            raise ValueError("simulated schema and explicit reviewed units required")
        for name in ("source_id", "campaign_id", "stream_id", "node_id", "clock_id"):
            _identifier(value[name], name)
        context = value["context"]
        _fields(context, CONTEXT_FIELDS)
        for name in ("link_id", "transmitter_id", "receiver_id", "calibration_id"):
            _identifier(context[name], name)
        if context["transmitter_id"] != value["node_id"] or context["transmitter_id"] == context["receiver_id"]:
            raise ValueError("distinct link endpoints required; snapshot node must be the transmitter")
        if type(context["settings_version"]) is not int or not 0 <= context["settings_version"] <= 2**63 - 1:
            raise ValueError("settings_version requires a nonnegative bounded integer")
        origin, decision = _time(value["origin_timestamp_utc"]), _time(value["decision_timestamp_utc"])
        settings_stamp = _time(context["settings_timestamp_utc"])
        calibration_stamp = _time(context["calibration_timestamp_utc"])
        expires = _time(context["calibration_valid_until_utc"])
        if not origin <= decision or settings_stamp > decision or not calibration_stamp <= decision < expires:
            raise ValueError("future settings/calibration or expired calibration at decision time")
        previous = value["previous"]
        if value["observation_mode"] == "calibration_bootstrap":
            if previous is not None or decision != origin:
                raise ValueError("explicit calibration bootstrap requires no prior data and the clock origin")
        elif value["observation_mode"] == "previous_period":
            _fields(previous, {"start_timestamp_utc", "end_timestamp_utc", "settings_sha256", "observables"})
            start, end = _time(previous["start_timestamp_utc"]), _time(previous["end_timestamp_utc"])
            if not origin <= start < end <= decision or not 0 <= (decision - end).total_seconds() <= MAX_PREVIOUS_AGE_S:
                raise ValueError("previous period must be causal, nonempty and end no more than 1 s before decision")
            if settings_stamp > start or calibration_stamp > start:
                raise ValueError("settings and calibration must be available by the previous period start")
            if previous["settings_sha256"] != _hash(_json(context["settings"])):
                raise ValueError("previous period must declare the same settings as the decision baseline")
        else:
            raise ValueError("explicit calibration_bootstrap or previous_period mode required")
        _request(value)  # Share all reviewed numeric, protocol, action-box and feature checks before weights.
        object.__setattr__(self, "payload_json", _json(value))

    @classmethod
    def from_dict(cls, value):
        return cls(_json(value))

    def to_dict(self):
        return json.loads(self.payload_json)

    def model_input(self):
        return _request(self.to_dict())

    @property
    def context_sha256(self):
        return _hash(_json(self.to_dict()["context"]))

    def validate_context(self, snapshot):
        value = self.to_dict()
        if snapshot.provenance.evidence_kind != EvidenceKind.SIMULATED:
            raise ValueError("only simulated snapshot evidence admitted")
        if not snapshot.integrity_verified or snapshot.out_of_distribution:
            raise ValueError("verified integrity and no OOD flag required")
        expected = (snapshot.node_id, snapshot.provenance.source, snapshot.metadata.get("campaign_id"),
                    snapshot.metadata.get("clock_id"), snapshot.metadata.get("keyrate_stream_id"))
        if tuple(value[key] for key in ("node_id", "source_id", "campaign_id", "clock_id", "stream_id")) != expected:
            raise ValueError("key-rate node/source/campaign/clock/stream must match snapshot")
        if (_time(snapshot.timestamp_utc) != _time(value["decision_timestamp_utc"])
                or _time(snapshot.metadata.get("keyrate_origin_timestamp_utc")) != _time(value["origin_timestamp_utc"])):
            raise ValueError("key-rate clock origin and decision must match snapshot")
        if (snapshot.metadata.get("keyrate_context_sha256") != self.context_sha256
                or type(snapshot.state_version) is not int
                or snapshot.state_version != value["context"]["settings_version"]):
            raise ValueError("link/settings/calibration identity or settings version differs from snapshot")
        previous = value["previous"]
        return None if previous is None else (_time(value["decision_timestamp_utc"]) -
                                              _time(previous["end_timestamp_utc"])).total_seconds()


@dataclass(frozen=True)
class KeyRateShadowRecord:
    status: str
    reason: str
    detail: str
    selected_model_id: str | None
    snapshot_json: str
    snapshot_sha256: str
    evaluation_json: str | None = None
    context_sha256: str | None = None
    previous_age_s: float | None = None
    observation_source: str | None = None
    schema: str = field(default="orion.sandbox-keyrate-shadow.v1", init=False)
    mode: str = field(default="shadow", init=False)
    policy_activated: bool = field(default=False, init=False)
    physical_action_performed: bool = field(default=False, init=False)
    simulator_action_performed: bool = field(default=False, init=False)
    settings_mutated: bool = field(default=False, init=False)

    def serialized(self):
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, allow_nan=False)


def evaluate_optional_keyrate(snapshot, *, model_id=None, archive_path=None):
    """OFF reads neither input nor weights; explicit selection only observes a decision."""
    if model_id is None:
        return KeyRateShadowRecord("off", "not_selected", "No input or weights read.", None, "", "")
    if model_id != MODEL_ID:
        raise ValueError("unsupported key-rate checkpoint")
    if not isinstance(snapshot, NetworkStateSnapshot):
        raise ValueError("typed NetworkStateSnapshot required")
    text = _json(snapshot.to_dict())
    if len(text.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds 128 KiB")
    snapshot = NetworkStateSnapshot.from_dict(json.loads(text))

    def record(status, reason, detail, **kwargs):
        return KeyRateShadowRecord(status, reason, detail, model_id, text, _hash(text), **kwargs)

    if TELEMETRY_KEY not in snapshot.telemetry:
        return record("unavailable", "missing_keyrate_window", "Explicit decoy-state input required; no substitute telemetry.")
    try:
        window = KeyRateShadowWindow.from_dict(snapshot.telemetry[TELEMETRY_KEY])
        age = window.validate_context(snapshot)
        request = window.model_input()
    except (ValueError, ArithmeticError) as exc:
        return record("refused", "incompatible_keyrate_window", str(exc))
    if archive_path is None:
        return record("unavailable", "missing_archive", "Explicit external QKD archive required.")
    try:
        evaluation = evaluate_keyrate_session(archive_path, request)
    except (ValueError, OSError, ArithmeticError) as exc:
        return record("refused", "model_evaluation_failed", str(exc))
    return record("evaluated", "shadow_only", "Parameter recommendation versus hold only; no rate outcome or setting applied.",
                  evaluation_json=evaluation.serialized(), context_sha256=window.context_sha256,
                  previous_age_s=age, observation_source=evaluation.observation_source)


def export_keyrate_shadow(result, path):
    with Path(path).open("xb") as stream:
        stream.write(result.serialized().encode("utf-8"))


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", choices=(MODEL_ID,), default=None)
    parser.add_argument("--qkd-zip", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError("output already exists; select a new path")
        snapshot = None if args.model is None else load_sync_shadow_snapshot(args.input)
        result = evaluate_optional_keyrate(snapshot, model_id=args.model, archive_path=args.qkd_zip)
        export_keyrate_shadow(result, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Key-rate shadow refused: {exc}\n")
    print(f"{result.status}: {result.reason}; no action performed. Report: {args.output}")
    return 0 if result.status in {"off", "evaluated"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
