"""Explicit fixed-plant allocation shadow bridge; no queue update or executor."""
from dataclasses import asdict, dataclass, field
from datetime import timedelta, timezone
import json
from pathlib import Path
import re

from .contracts import NetworkStateSnapshot
from .optional_sync import MAX_SNAPSHOT_BYTES, _hash, _identifier, _json, _time, load_sync_shadow_snapshot
from quantum_autopilot.core import EvidenceKind
from quantum_autopilot.model_intake.allocation_session import (
    LINK_NAMES, MODEL_IDS, SCHEMA, _vector, evaluate_allocation_session, parse_allocation_input,
)
from quantum_autopilot.model_intake.sync_session import _number

TELEMETRY_KEY = "optional_allocation_window"
WINDOW_SCHEMA = "orion.sandbox-allocation-window.v1"
MAX_WINDOW_AGE_S = 5.0


def _tuple_vector(value, maximum=None):
    if not isinstance(value, (tuple, list)):
        raise ValueError("typed array required")
    return tuple(_vector(list(value), maximum))


@dataclass(frozen=True)
class AllocationSample:
    timestamp_utc: str
    measured_qber: tuple[float, ...]
    raman_qber_estimate: tuple[float, ...]
    pending_admission: tuple[tuple[float, ...], ...]
    key_pool_bits: tuple[float, ...]
    backlog_bits: tuple[float, ...]
    urgency_s: tuple[float | None, ...]

    def __post_init__(self):
        _time(self.timestamp_utc)
        for name in ("measured_qber", "raman_qber_estimate", "key_pool_bits", "backlog_bits"):
            limit = 1. if name in ("measured_qber", "raman_qber_estimate") else None
            object.__setattr__(self, name, _tuple_vector(getattr(self, name), limit))
        if not isinstance(self.pending_admission, (tuple, list)) or len(self.pending_admission) != 6:
            raise ValueError("six committed queue rows required, oldest first")
        queue = tuple(_tuple_vector(row, 1.) for row in self.pending_admission)
        if any(v not in (0., .25, .5, .75, 1.) for row in queue for v in row):
            raise ValueError("queue requires reviewed admission levels")
        object.__setattr__(self, "pending_admission", queue)
        if not isinstance(self.urgency_s, (tuple, list)) or len(self.urgency_s) != 11:
            raise ValueError("eleven urgency entries required")
        urgency = tuple(None if v is None else _number(v) for v in self.urgency_s)
        if any(v is not None and v <= 0 for v in urgency):
            raise ValueError("urgency must be positive seconds or null")
        object.__setattr__(self, "urgency_s", urgency)


@dataclass(frozen=True)
class AllocationDriftSample:
    timestamp_utc: str
    drift: tuple[float, ...]

    def __post_init__(self):
        _time(self.timestamp_utc)
        object.__setattr__(self, "drift", _tuple_vector(self.drift, 1.))


@dataclass(frozen=True)
class AllocationShadowWindow:
    source_id: str
    campaign_id: str
    stream_id: str
    node_id: str
    clock_id: str
    plant_sha256: str
    origin_timestamp_utc: str
    observation: AllocationSample
    history: tuple[AllocationDriftSample, ...]
    link_names: tuple[str, ...] = LINK_NAMES
    schema: str = WINDOW_SCHEMA
    evidence: str = "simulated"
    protocol: str = "single_photon_bb84"
    period_s: float = 5.
    latency_s: float = 30.
    horizon_s: float = 150.
    qber_unit: str = "fraction"
    volume_unit: str = "bit"
    time_unit: str = "s"
    queue_order: str = "oldest_first"
    drift_definition: str = "max(qber-raman-0.005,0)"

    def __post_init__(self):
        for name in ("source_id", "campaign_id", "stream_id", "node_id", "clock_id"):
            _identifier(getattr(self, name), name)
        if not isinstance(self.plant_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.plant_sha256):
            raise ValueError("plant_sha256 must identify the reviewed plant bytes")
        _time(self.origin_timestamp_utc)
        if (self.schema, self.evidence, self.qber_unit, self.volume_unit, self.time_unit,
                self.queue_order, self.drift_definition) != (
                WINDOW_SCHEMA, "simulated", "fraction", "bit", "s", "oldest_first", "max(qber-raman-0.005,0)"):
            raise ValueError("simulated schema, explicit units and reviewed queue/drift semantics required")
        if not isinstance(self.observation, AllocationSample):
            raise ValueError("typed allocation observation required")
        if (not isinstance(self.history, (tuple, list)) or len(self.history) > 5
                or any(not isinstance(row, AllocationDriftSample) for row in self.history)):
            raise ValueError("zero to five typed past drift samples required")
        if not isinstance(self.link_names, (list, tuple)) or tuple(self.link_names) != LINK_NAMES:
            raise ValueError("reviewed eleven-link order required; no topology remapping")
        object.__setattr__(self, "link_names", tuple(self.link_names))
        object.__setattr__(self, "history", tuple(self.history))
        self.model_input(MODEL_IDS[0])

    @classmethod
    def from_dict(cls, value):
        fields = set(cls.__dataclass_fields__)
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("invalid allocation window fields")
        obs = value["observation"]
        if not isinstance(obs, dict) or set(obs) != set(AllocationSample.__dataclass_fields__):
            raise ValueError("invalid allocation observation fields")
        rows = value["history"]
        if not isinstance(rows, list) or len(rows) > 5:
            raise ValueError("zero to five history entries required")
        history = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"timestamp_utc", "drift"}:
                raise ValueError("invalid drift sample fields")
            history.append(AllocationDriftSample(**row))
        return cls(**{**value, "observation": AllocationSample(**obs), "history": tuple(history)})

    def to_dict(self):
        return json.loads(_json(asdict(self)))

    def model_input(self, model_id):
        origin = _time(self.origin_timestamp_utc)
        observation = asdict(self.observation)
        stamp = observation.pop("timestamp_utc")
        observation["time_s"] = (_time(stamp) - origin).total_seconds()
        text = _json({"schema": SCHEMA, "model_id": model_id, "evidence": "simulated_offline",
                      "stream_id": self.stream_id, "protocol": self.protocol, "link_names": self.link_names,
                      "period_s": self.period_s, "latency_s": self.latency_s, "horizon_s": self.horizon_s,
                      "observation": observation,
                      "history_times_s": [(_time(row.timestamp_utc) - origin).total_seconds() for row in self.history],
                      "drift_history": [row.drift for row in self.history]})
        parse_allocation_input(model_id, text)
        return text

    def validate_context(self, snapshot):
        if snapshot.provenance.evidence_kind != EvidenceKind.SIMULATED:
            raise ValueError("only simulated snapshot evidence is admitted")
        if not snapshot.integrity_verified or snapshot.out_of_distribution:
            raise ValueError("requires integrity-verified snapshot not flagged OOD")
        expected = (snapshot.node_id, snapshot.provenance.source, snapshot.metadata.get("campaign_id"),
                    snapshot.metadata.get("clock_id"), snapshot.metadata.get("allocation_stream_id"),
                    snapshot.metadata.get("allocation_plant_sha256"))
        if (self.node_id, self.source_id, self.campaign_id, self.clock_id, self.stream_id, self.plant_sha256) != expected:
            raise ValueError("allocation node/source/campaign/clock/stream/plant must match snapshot")
        if _time(snapshot.metadata.get("allocation_origin_timestamp_utc")) != _time(self.origin_timestamp_utc):
            raise ValueError("allocation clock origin must match snapshot")
        age = (_time(snapshot.timestamp_utc) - _time(self.observation.timestamp_utc)).total_seconds()
        if not 0 <= age <= MAX_WINDOW_AGE_S:
            raise ValueError("allocation observation is future-dated or older than 5 s")
        return age


@dataclass(frozen=True)
class AllocationShadowRecord:
    status: str
    reason: str
    detail: str
    selected_model_id: str | None
    snapshot_json: str
    snapshot_sha256: str
    evaluation_json: str | None = None
    effective_timestamp_utc: str | None = None
    window_age_s: float | None = None
    final_output_status: str | None = None
    schema: str = field(default="orion.sandbox-allocation-shadow.v1", init=False)
    mode: str = field(default="shadow", init=False)
    policy_activated: bool = field(default=False, init=False)
    physical_action_performed: bool = field(default=False, init=False)
    simulator_action_performed: bool = field(default=False, init=False)
    queue_mutated: bool = field(default=False, init=False)

    def serialized(self):
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, allow_nan=False)


def evaluate_optional_allocation(snapshot, *, model_id=None, archive_path=None):
    """Stateless opt-in comparison; OFF reads neither snapshot nor archive."""
    if model_id is None:
        return AllocationShadowRecord("off", "not_selected", "No input or weights read.", None, "", "")
    if model_id not in MODEL_IDS:
        raise ValueError("unsupported allocation checkpoint")
    if not isinstance(snapshot, NetworkStateSnapshot):
        raise ValueError("typed NetworkStateSnapshot required")
    text = _json(snapshot.to_dict())
    if len(text.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds 128 KiB")
    snapshot = NetworkStateSnapshot.from_dict(json.loads(text))

    def record(status, reason, detail, **kwargs):
        return AllocationShadowRecord(status, reason, detail, model_id, text, _hash(text), **kwargs)

    if TELEMETRY_KEY not in snapshot.telemetry:
        return record("unavailable", "missing_allocation_window", "No allocation window; no WDM/sync/SO(3) substitution.")
    try:
        window = AllocationShadowWindow.from_dict(snapshot.telemetry[TELEMETRY_KEY])
        age = window.validate_context(snapshot)
        request = window.model_input(model_id)
        effective = (_time(window.observation.timestamp_utc) + timedelta(seconds=30)).astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        return record("refused", "incompatible_allocation_window", str(exc))
    if archive_path is None:
        return record("unavailable", "missing_archive", "Explicit external QNET archive required.")
    try:
        evaluation = evaluate_allocation_session(model_id, archive_path, request)
        if evaluation.plant_sha256 != window.plant_sha256:
            return record("refused", "plant_identity_mismatch", "Input plant differs from reviewed archive plant.")
    except (ValueError, OSError, ArithmeticError) as exc:
        return record("refused", "model_evaluation_failed", str(exc))
    return record("evaluated", "shadow_only", "Admission recommendation only; no queue update or delivered-key claim.",
                  evaluation_json=evaluation.serialized(), effective_timestamp_utc=effective.isoformat(),
                  window_age_s=age, final_output_status=evaluation.recommendation["status"])


def export_allocation_shadow(result, path):
    raw = result.serialized().encode("utf-8")
    with Path(path).open("xb") as stream:
        stream.write(raw)


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", choices=MODEL_IDS, default=None)
    parser.add_argument("--qnet-zip", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError("output already exists; select a new path")
        snapshot = None if args.model is None else load_sync_shadow_snapshot(args.input)
        result = evaluate_optional_allocation(snapshot, model_id=args.model, archive_path=args.qnet_zip)
        export_allocation_shadow(result, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Allocation shadow refused: {exc}\n")
    print(f"{result.status}: {result.reason}; no action performed. Report: {args.output}")
    return 0 if result.status in {"off", "evaluated"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
