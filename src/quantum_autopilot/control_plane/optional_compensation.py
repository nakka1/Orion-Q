"""Typed polarisation shadow boundary. No actuator, policy or WDM conversion."""
from dataclasses import asdict, dataclass, field
from datetime import timedelta, timezone
import json
from pathlib import Path

from .contracts import NetworkStateSnapshot
from .optional_sync import MAX_SNAPSHOT_BYTES, _hash, _identifier, _json, _time
from quantum_autopilot.core import EvidenceKind
from quantum_autopilot.model_intake.compensation_session import (
    MODEL_IDS, SCHEMA, _validate_rotation, evaluate_compensation_session, parse_compensation_input,
)
from quantum_autopilot.model_intake.sync_session import _number

TELEMETRY_KEY = "optional_compensation_window"
WINDOW_SCHEMA = "orion.sandbox-compensation-window.v1"
CONVENTION = "author_export_so3_v1"
REGIMES = {"calibrado": (60., .02), "desenvolvimento": (40., .05)}
MAX_WINDOW_AGE_S = .002


@dataclass(frozen=True)
class PolarisationSample:
    timestamp_utc: str
    rotation: tuple[tuple[float, ...], ...]
    temperature_c: float
    temperature_rate_k_s: float
    vibration_rms_rad: float

    def __post_init__(self):
        _time(self.timestamp_utc)
        matrix = self.rotation
        if (not isinstance(matrix, (list, tuple)) or len(matrix) != 3
                or any(not isinstance(row, (list, tuple)) or len(row) != 3 for row in matrix)):
            raise ValueError("rotation requires a 3 by 3 matrix")
        matrix = tuple(tuple(_number(item) for item in row) for row in matrix)
        _validate_rotation([list(row) for row in matrix])
        object.__setattr__(self, "rotation", matrix)
        for name in ("temperature_c", "temperature_rate_k_s", "vibration_rms_rad"):
            object.__setattr__(self, name, _number(getattr(self, name)))
        if self.temperature_c < -273.15 or self.vibration_rms_rad < 0:
            raise ValueError("invalid temperature or negative vibration RMS")


@dataclass(frozen=True)
class CompensationShadowWindow:
    source_id: str
    campaign_id: str
    stream_id: str
    node_id: str
    clock_id: str
    rotation_frame_id: str
    operating_point: str
    vibration_frequency_hz: float
    thermal_drift_rate_rad_s: float
    samples: tuple[PolarisationSample, ...]
    schema: str = WINDOW_SCHEMA
    evidence: str = "simulated"
    rotation_convention: str = CONVENTION
    period_s: float = .002
    horizon_s: float = .04
    monitoring_photons_per_basis: int = 400
    temperature_unit: str = "degC"
    temperature_rate_unit: str = "K/s"
    vibration_unit: str = "rad_rms"

    def __post_init__(self):
        for name in ("source_id", "campaign_id", "stream_id", "node_id", "clock_id", "rotation_frame_id"):
            _identifier(getattr(self, name), name)
        if (self.schema, self.evidence, self.rotation_convention, self.temperature_unit,
                self.temperature_rate_unit, self.vibration_unit) != (
                WINDOW_SCHEMA, "simulated", CONVENTION, "degC", "K/s", "rad_rms"):
            raise ValueError("incompatible polarisation schema, evidence, convention or units")
        if not isinstance(self.operating_point, str) or self.operating_point not in REGIMES:
            raise ValueError("unknown declared compensation regime")
        declared = (_number(self.vibration_frequency_hz), _number(self.thermal_drift_rate_rad_s))
        if declared != REGIMES[self.operating_point]:
            raise ValueError("frequency/drift declaration differs from the checkpoint regime")
        if _number(self.period_s) != .002 or _number(self.horizon_s) != .04:
            raise ValueError("requires 2 ms cadence and 40 ms horizon")
        if type(self.monitoring_photons_per_basis) is not int or self.monitoring_photons_per_basis != 400:
            raise ValueError("requires 400 monitoring photons per basis")
        if not isinstance(self.samples, (tuple, list)) or not 1 <= len(self.samples) <= 16:
            raise ValueError("one to sixteen consecutive polarisation samples required")
        if any(not isinstance(item, PolarisationSample) for item in self.samples):
            raise ValueError("typed PolarisationSample observations required")
        object.__setattr__(self, "samples", tuple(self.samples))
        # Validate the exact existing feature contract before any archive is opened.
        self.model_input(f"qnet.compensation.{self.operating_point}.ridge")

    @classmethod
    def from_dict(cls, value):
        fields = set(cls.__dataclass_fields__)
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("invalid polarisation window fields")
        rows = value["samples"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
            raise ValueError("one to sixteen samples required")
        samples = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(PolarisationSample.__dataclass_fields__):
                raise ValueError("invalid polarisation sample fields")
            samples.append(PolarisationSample(**row))
        return cls(**{**value, "samples": tuple(samples)})

    def to_dict(self):
        return json.loads(_json(asdict(self)))

    def model_input(self, model_id):
        if model_id not in MODEL_IDS or model_id.split(".")[2] != self.operating_point:
            raise ValueError("selected checkpoint differs from the declared regime")
        origin = _time(self.samples[0].timestamp_utc)
        request = {"schema": SCHEMA, "evidence": "simulated_offline", "stream_id": self.stream_id,
                   "model_id": model_id, "period_s": self.period_s, "horizon_s": self.horizon_s,
                   "monitoring_photons_per_basis": self.monitoring_photons_per_basis,
                   "reference": None,
                   "samples": [{"timestamp_s": (_time(item.timestamp_utc) - origin).total_seconds(),
                                "rotation": item.rotation,
                                "covariates": {name: getattr(item, name) for name in (
                                    "temperature_c", "temperature_rate_k_s", "vibration_rms_rad")}}
                               for item in self.samples]}
        text = _json(request)
        parse_compensation_input(model_id, text)
        return text

    def validate_context(self, snapshot):
        if snapshot.provenance.evidence_kind != EvidenceKind.SIMULATED:
            raise ValueError("only simulated snapshot evidence is admitted")
        if not snapshot.integrity_verified or snapshot.out_of_distribution:
            raise ValueError("requires integrity-verified snapshot not flagged OOD")
        expected = (snapshot.node_id, snapshot.provenance.source, snapshot.metadata.get("campaign_id"),
                    snapshot.metadata.get("clock_id"), snapshot.metadata.get("rotation_frame_id"),
                    snapshot.metadata.get("rotation_convention"), snapshot.metadata.get("compensation_operating_point"))
        declared = (self.node_id, self.source_id, self.campaign_id, self.clock_id, self.rotation_frame_id,
                    self.rotation_convention, self.operating_point)
        if declared != expected:
            raise ValueError("polarisation node/source/campaign/clock/frame/convention/regime must match snapshot")
        age = (_time(snapshot.timestamp_utc) - _time(self.samples[-1].timestamp_utc)).total_seconds()
        if not 0 <= age <= MAX_WINDOW_AGE_S:
            raise ValueError("polarisation window is future-dated or older than 2 ms")
        return age


@dataclass(frozen=True)
class CompensationShadowRecord:
    status: str
    reason: str
    detail: str
    selected_model_id: str | None
    snapshot_json: str
    snapshot_sha256: str
    evaluation_json: str | None = None
    target_timestamp_utc: str | None = None
    window_age_s: float | None = None
    final_output_status: str | None = None
    schema: str = field(default="orion.sandbox-compensation-shadow.v1", init=False)
    mode: str = field(default="shadow", init=False)
    policy_activated: bool = field(default=False, init=False)
    physical_action_performed: bool = field(default=False, init=False)
    simulator_action_performed: bool = field(default=False, init=False)

    def serialized(self):
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, allow_nan=False)


def evaluate_optional_compensation(snapshot, *, model_id=None, archive_path=None):
    """Explicit selection only; OFF reads neither snapshot nor archive."""
    if model_id is None:
        return CompensationShadowRecord("off", "not_selected", "No input or weights read.", None, "", "")
    if model_id not in MODEL_IDS:
        raise ValueError("unsupported compensation checkpoint")
    if not isinstance(snapshot, NetworkStateSnapshot):
        raise ValueError("typed NetworkStateSnapshot required")
    text = _json(snapshot.to_dict())
    if len(text.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds 128 KiB")
    snapshot = NetworkStateSnapshot.from_dict(json.loads(text))

    def record(status, reason, detail, **kwargs):
        return CompensationShadowRecord(status, reason, detail, model_id, text, _hash(text), **kwargs)

    if TELEMETRY_KEY not in snapshot.telemetry:
        return record("unavailable", "missing_compensation_window", "No polarisation window; no WDM/sync substitution.")
    try:
        window = CompensationShadowWindow.from_dict(snapshot.telemetry[TELEMETRY_KEY])
        age = window.validate_context(snapshot)
        request = window.model_input(model_id)
        target = (_time(window.samples[-1].timestamp_utc) + timedelta(milliseconds=40)).astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        return record("refused", "incompatible_compensation_window", str(exc))
    if archive_path is None:
        return record("unavailable", "missing_archive", "Explicit external QNET archive required.")
    try:
        evaluation = evaluate_compensation_session(model_id, archive_path, request)
    except (ValueError, OSError, ArithmeticError) as exc:
        return record("refused", "model_evaluation_failed", str(exc))
    status = evaluation.steps[-1]["status"]
    return record("evaluated", "shadow_only", "SO(3) output only; warm-up is persistence, not a learned forecast.",
                  evaluation_json=evaluation.serialized(), target_timestamp_utc=target.isoformat(),
                  window_age_s=age, final_output_status=status)


def export_compensation_shadow(result, path):
    raw = result.serialized().encode("utf-8")
    with Path(path).open("xb") as stream:
        stream.write(raw)
