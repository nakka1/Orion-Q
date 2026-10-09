"""Explicit, non-actuating synchronization sidecar for simulated sandbox inputs.

This is not a control policy. Missing timing observations are never synthesized
from WDM, Bell fidelity, memory age or host receipt times.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from .contracts import NetworkStateSnapshot, _aware_timestamp
from quantum_autopilot.core import EvidenceKind
from quantum_autopilot.model_intake.sync_session import (
    MODEL_ID, SCHEMA, SyncEvaluation, _constant, _number, _unique, evaluate_sync, parse_sync_input,
)

TELEMETRY_KEY = "optional_sync_window"
WINDOW_SCHEMA = "orion.sandbox-sync-window.v1"
MAX_SNAPSHOT_BYTES = 128 * 1024
MAX_WINDOW_AGE_S = 1.0


def _identifier(value, name):
    if (not isinstance(value, str) or not value.strip() or len(value) > 160
            or any(ord(c) < 32 for c in value)):
        raise ValueError(f"{name}: nonempty identifier up to 160 characters required")
    return value


def _time(value):
    return datetime.fromisoformat(_aware_timestamp("timestamp_utc", value).replace("Z", "+00:00"))


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SyncSample:
    timestamp_utc: str
    offset_s: float
    temperature_c: float

    def __post_init__(self):
        _time(self.timestamp_utc)
        object.__setattr__(self, "offset_s", _number(self.offset_s))
        object.__setattr__(self, "temperature_c", _number(self.temperature_c))


@dataclass(frozen=True)
class SyncShadowWindow:
    source_id: str
    campaign_id: str
    stream_id: str
    node_id: str
    clock_id: str
    samples: tuple[SyncSample, ...]
    schema: str = WINDOW_SCHEMA
    evidence: str = "simulated"
    offset_unit: str = "s"
    temperature_unit: str = "degC"

    def __post_init__(self):
        for name in ("source_id", "campaign_id", "stream_id", "node_id", "clock_id"):
            _identifier(getattr(self, name), name)
        if (self.schema, self.evidence, self.offset_unit, self.temperature_unit) != (
                WINDOW_SCHEMA, "simulated", "s", "degC"):
            raise ValueError("window requires the simulated schema, seconds and degC")
        if not isinstance(self.samples, (tuple, list)) or len(self.samples) != 8:
            raise ValueError("exactly eight synchronization samples required")
        if any(not isinstance(item, SyncSample) for item in self.samples):
            raise ValueError("samples must be typed SyncSample observations")
        object.__setattr__(self, "samples", tuple(self.samples))
        parse_sync_input(self.model_input())

    @classmethod
    def from_dict(cls, value):
        fields = {"source_id", "campaign_id", "stream_id", "node_id", "clock_id", "samples",
                  "schema", "evidence", "offset_unit", "temperature_unit"}
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("invalid synchronization window fields")
        rows = value["samples"]
        if not isinstance(rows, list) or len(rows) != 8:
            raise ValueError("exactly eight synchronization samples required")
        samples = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"timestamp_utc", "offset_s", "temperature_c"}:
                raise ValueError("invalid synchronization sample fields")
            samples.append(SyncSample(**row))
        return cls(**{**value, "samples": tuple(samples)})

    def to_dict(self):
        return {**asdict(self), "samples": [asdict(item) for item in self.samples]}

    def model_input(self):
        # Small relative times preserve subsecond precision; UTC anchor is retained
        # in the report. Only observed samples, never a future label, enter inference.
        origin = _time(self.samples[0].timestamp_utc)
        return _json({"schema": SCHEMA, "evidence": "simulated_offline", "stream_id": self.stream_id,
                      "timestamps_s": [(_time(s.timestamp_utc) - origin).total_seconds() for s in self.samples],
                      "offsets_s": [s.offset_s for s in self.samples],
                      "temperatures_c": [s.temperature_c for s in self.samples], "reference": None})

    def validate_context(self, snapshot):
        if snapshot.provenance.evidence_kind != EvidenceKind.SIMULATED:
            raise ValueError("only simulated snapshot evidence is admitted")
        if not snapshot.integrity_verified or snapshot.out_of_distribution:
            raise ValueError("snapshot must be integrity-verified and not flagged out of distribution")
        expected = (snapshot.node_id, snapshot.provenance.source,
                    snapshot.metadata.get("campaign_id"), snapshot.metadata.get("clock_id"))
        if (self.node_id, self.source_id, self.campaign_id, self.clock_id) != expected:
            raise ValueError("window node/source/campaign/clock must match snapshot context")
        age = (_time(snapshot.timestamp_utc) - _time(self.samples[-1].timestamp_utc)).total_seconds()
        if not 0 <= age <= MAX_WINDOW_AGE_S:
            raise ValueError("synchronization window is future-dated or older than one second")
        return age


@dataclass(frozen=True)
class SyncShadowRecord:
    status: str
    reason: str
    detail: str
    selected_model_id: str | None
    snapshot_json: str
    snapshot_sha256: str
    evaluation: SyncEvaluation | None = None
    target_timestamp_utc: str | None = None
    window_age_s: float | None = None
    schema: str = field(default="orion.sandbox-sync-shadow.v1", init=False)
    mode: str = field(default="shadow", init=False)
    policy_activated: bool = field(default=False, init=False)
    physical_action_performed: bool = field(default=False, init=False)
    simulator_action_performed: bool = field(default=False, init=False)

    def serialized(self):
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, allow_nan=False)


def evaluate_optional_sync(snapshot, *, model_id=None, archive_path=None):
    """Read-only opt-in. No runtime cycle, policy, executor or journal is invoked.

    OFF does not inspect even the snapshot. Invalid API inputs raise; incompatible
    telemetry and failed artifact admission return explicit refusal records.
    """
    if model_id is None:
        return SyncShadowRecord("off", "not_selected", "No model selected; no input or weights read.",
                                None, "", "")
    if model_id != MODEL_ID:
        raise ValueError("this bridge supports only qkd.sync.ridge.1s.h2s")
    if not isinstance(snapshot, NetworkStateSnapshot):
        raise ValueError("a NetworkStateSnapshot is required")
    # Detach mutable nested dictionaries and bind the exact admitted context.
    text = _json(snapshot.to_dict())
    if len(text.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds 128 KiB")
    snapshot = NetworkStateSnapshot.from_dict(json.loads(text))

    def record(status, reason, detail, **kwargs):
        return SyncShadowRecord(status, reason, detail, model_id, text, _hash(text), **kwargs)

    if TELEMETRY_KEY not in snapshot.telemetry:
        return record("unavailable", "missing_sync_window", "No synchronization window; no WDM substitution.")
    try:
        window = SyncShadowWindow.from_dict(snapshot.telemetry[TELEMETRY_KEY])
        age = window.validate_context(snapshot)
        target = (_time(window.samples[-1].timestamp_utc) + timedelta(seconds=2)).astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        return record("refused", "incompatible_sync_window", str(exc))
    if archive_path is None:
        return record("unavailable", "missing_archive", "Explicit external QKD archive required.")
    try:
        evaluation = evaluate_sync(archive_path, window.model_input())
    except (ValueError, OSError, ArithmeticError) as exc:
        return record("refused", "model_evaluation_failed", str(exc))
    return record("evaluated", "shadow_only", "Offset forecast versus persistence; no control or fidelity claim.",
                  evaluation=evaluation, target_timestamp_utc=target.isoformat(), window_age_s=age)


def export_sync_shadow(record, path):
    """Explicit report export, separate from the command journal, never overwrite."""
    raw = record.serialized().encode("utf-8")
    with Path(path).open("xb") as stream:
        stream.write(raw)


def load_sync_shadow_snapshot(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds 128 KiB")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
    if not isinstance(value, dict):
        raise ValueError("snapshot JSON must be an object")
    # Also reject numeric overflow such as 1e999 before contract construction.
    _json(value)
    try:
        return NetworkStateSnapshot.from_dict(value)
    except TypeError as exc:
        raise ValueError("invalid snapshot fields") from exc


def main(argv=None):
    """Source-only explicit replay entry point; no hardware, worker or UI launch."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Simulated NetworkStateSnapshot JSON")
    parser.add_argument("--output", required=True, type=Path, help="New report file; never overwrite")
    parser.add_argument("--model", choices=[MODEL_ID], default=None, help="Omit to remain OFF")
    parser.add_argument("--qkd-zip", type=Path, help="Explicit external reviewed QKD archive")
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError("output already exists; select a new report path")
        snapshot = None if args.model is None else load_sync_shadow_snapshot(args.input)
        result = evaluate_optional_sync(snapshot, model_id=args.model, archive_path=args.qkd_zip)
        export_sync_shadow(result, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Shadow evaluation refused: {exc}\n")
    print(f"{result.status}: {result.reason}; no action performed. Report: {args.output}")
    return 0 if result.status in {"off", "evaluated"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
