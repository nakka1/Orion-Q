"""Operational preparation/detection records, not inferred bits from time tags."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import random
from pathlib import Path

from .photon import AcquisitionError, canonical_json, digest


@dataclass(frozen=True)
class ClockReference:
    domain: str
    tick_ps: int = 1
    epoch_utc: str | None = None
    alignment_uncertainty_ps: int = 0
    locked: bool = True

    def validate(self):
        if not self.domain or type(self.tick_ps) is not int or self.tick_ps <= 0:
            raise AcquisitionError("clock requires an identified domain and integer ps tick")
        if type(self.alignment_uncertainty_ps) is not int or self.alignment_uncertainty_ps < 0:
            raise AcquisitionError("clock alignment uncertainty must be integer nonnegative ps")
        if type(self.locked) is not bool:
            raise AcquisitionError("clock locked must be boolean")
        if self.epoch_utc is not None:
            from datetime import datetime
            try:
                parsed = datetime.fromisoformat(self.epoch_utc.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("timezone absent")
            except (TypeError, ValueError) as exc:
                raise AcquisitionError("clock epoch requires explicit timezone") from exc


@dataclass(frozen=True)
class PulseObservation:
    pulse_id: int
    timestamp_ps: int
    prepared_basis: int
    prepared_bit: int
    analyzed_basis: int
    analyzed_bit: int | None
    detected: bool
    detection_timestamp_ps: int | None = None

    def validate(self):
        if type(self.pulse_id) is not int or self.pulse_id < 0:
            raise AcquisitionError("pulse_id must be a nonnegative integer")
        if type(self.timestamp_ps) is not int or not 0 <= self.timestamp_ps < 2**63:
            raise AcquisitionError("preparation timestamp must be signed 64-bit integer ps")
        for name in ("prepared_basis", "prepared_bit", "analyzed_basis"):
            if type(getattr(self, name)) is not int or getattr(self, name) not in (0, 1):
                raise AcquisitionError(f"{name} requires explicit binary preparation/analysis record")
        if type(self.detected) is not bool:
            raise AcquisitionError("detected must be boolean")
        if self.detected:
            if type(self.analyzed_bit) is not int or self.analyzed_bit not in (0, 1):
                raise AcquisitionError("a detected pulse requires an explicit analyzed bit")
            if type(self.detection_timestamp_ps) is not int or not self.timestamp_ps <= self.detection_timestamp_ps < 2**63:
                raise AcquisitionError("detection timestamp must follow preparation in the aligned clock")
        elif self.analyzed_bit is not None or self.detection_timestamp_ps is not None:
            raise AcquisitionError("an undetected pulse cannot supply a bit or detection timestamp")


@dataclass(frozen=True)
class ObservationBlock:
    experiment_id: str
    device_id: str
    protocol: str
    origin: str
    mode: str
    source_id: str
    clock: ClockReference
    observations: list[PulseObservation]
    duration_ps: int
    adapter_id: str
    configuration_id: str
    raw_acquisition_sha256: str | None = None
    source_record: str | None = None
    capture_start_ps: int = 0

    def validate(self):
        self.clock.validate()
        if not all((self.experiment_id, self.device_id, self.source_id, self.adapter_id, self.configuration_id)):
            raise AcquisitionError("observation provenance incomplete")
        if self.protocol != "BB84-single-photon":
            raise AcquisitionError("unsupported observation protocol")
        if self.origin not in {"simulated", "physical"} or self.mode not in {"emulation", "hardware", "replay"}:
            raise AcquisitionError("invalid observation mode/origin")
        if self.mode == "emulation" and self.origin != "simulated" or self.mode == "hardware" and self.origin != "physical":
            raise AcquisitionError("observation mode contradicts data origin")
        if self.mode == "replay" and not self.source_record:
            raise AcquisitionError("replayed observations require the original source record")
        if type(self.duration_ps) is not int or not 0 < self.duration_ps < 2**63:
            raise AcquisitionError("observation duration must be integer ps")
        if type(self.capture_start_ps) is not int or not 0 <= self.capture_start_ps < 2**63 - self.duration_ps:
            raise AcquisitionError("capture start must preserve signed 64-bit ps interval")
        if self.raw_acquisition_sha256 is not None and (len(self.raw_acquisition_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.raw_acquisition_sha256)):
            raise AcquisitionError("raw acquisition hash must be SHA256 hex")
        previous_id, previous_time = -1, -1
        for pulse in self.observations:
            pulse.validate()
            if pulse.pulse_id <= previous_id or pulse.timestamp_ps < previous_time:
                raise AcquisitionError("duplicate/out-of-order pulse or clock regression")
            if not self.capture_start_ps <= pulse.timestamp_ps < self.capture_start_ps + self.duration_ps or (
                    pulse.detection_timestamp_ps is not None and not self.capture_start_ps <= pulse.detection_timestamp_ps
                    < self.capture_start_ps + self.duration_ps):
                raise AcquisitionError("pulse lies outside observation interval")
            previous_id, previous_time = pulse.pulse_id, pulse.timestamp_ps

    def to_dict(self):
        return {"schema": "orion.lab.observation-block.v1", **asdict(self)}

    @property
    def observation_sha256(self):
        return digest(self.to_dict())

    @classmethod
    def from_dict(cls, value):
        payload = dict(value)
        if payload.pop("schema", None) != "orion.lab.observation-block.v1":
            raise AcquisitionError("unknown observation schema")
        result = cls(**{**payload, "clock": ClockReference(**payload["clock"]),
                        "observations": [PulseObservation(**p) for p in payload["observations"]]})
        result.validate()
        return result


def persist_observation_block(block: ObservationBlock, path: Path | str) -> Path:
    block.validate()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(canonical_json({"observation": block.to_dict(), "sha256": block.observation_sha256}), encoding="utf-8")
    return target


def load_observation_block(path: Path | str) -> ObservationBlock:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("sha256") != digest(payload["observation"]):
        raise AcquisitionError("observation block integrity failed")
    return ObservationBlock.from_dict(payload["observation"])


def synthetic_bb84_observations(*, seed: int = 20261007, count: int = 80000, loss_db: float = 3.,
                                efficiency: float = .7, error_probability: float = .01,
                                experiment_id: str = "observation-demo", duration_ps: int = 10**12) -> ObservationBlock:
    """Identified public-seed single-photon fixture; not secret or hardware data."""
    if type(seed) is not int or type(count) is not int or not 1 <= count <= 1_000_000:
        raise AcquisitionError("fixture seed/count invalid")
    for value in (loss_db, efficiency, error_probability):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise AcquisitionError("fixture parameters must be finite")
    if loss_db < 0 or not 0 <= efficiency <= 1 or not 0 <= error_probability <= .5:
        raise AcquisitionError("fixture optical probabilities invalid")
    if type(duration_ps) is not int or not count * 2 <= duration_ps < 2**63:
        raise AcquisitionError("fixture duration must permit distinct preparation/detection times")
    parameters = {"seed": seed, "count": count, "loss_db": loss_db, "efficiency": efficiency,
                  "error_probability": error_probability, "duration_ps": duration_ps}
    rng = random.Random(seed)
    probability = efficiency * 10 ** (-loss_db / 10)
    pulses = []
    for index in range(count):
        prepared_bit, prepared_basis, analyzed_basis = (rng.randrange(2) for _ in range(3))
        detected = rng.random() < probability
        analyzed_bit = None
        if detected:
            analyzed_bit = prepared_bit if prepared_basis == analyzed_basis else rng.randrange(2)
            if rng.random() < error_probability:
                analyzed_bit ^= 1
        timestamp = index * duration_ps // count
        pulses.append(PulseObservation(index, timestamp, prepared_basis, prepared_bit, analyzed_basis,
                                       analyzed_bit, detected, timestamp + 1 if detected else None))
    block = ObservationBlock(experiment_id, "synthetic-bb84-source", "BB84-single-photon", "simulated", "emulation",
                             f"orion.single-photon-observations.public-seed.v1:{seed}",
                             ClockReference("synthetic-aligned-clock"), pulses, duration_ps,
                             "orion.observation-fixture.v1", digest(parameters))
    block.validate()
    return block


def bind_acquisition_observations(acquisition, pulses: list[PulseObservation], *, detector_bit_map: dict[int, int],
                                  preparation_source_id: str, analysis_source_id: str,
                                  clock_reference: ClockReference | None = None) -> ObservationBlock:
    """Join externally supplied preparation/analysis records to actual normalized clicks.

    The map describes measured detector wiring; bases remain explicit analyzer records.
    Coincidence-only SPDC records are not converted into a BB84 single-photon experiment.
    The caller must identify that the source was an appropriate single-photon experiment.
    """
    from collections import Counter
    from .photon import analyze
    acquisition.validate()
    if not analyze(acquisition)["complete"]:
        raise AcquisitionError("incomplete acquisition cannot feed accepted QKD observations")
    if not preparation_source_id or not analysis_source_id:
        raise AcquisitionError("preparation and analyzer records must have independent source identities")
    if acquisition.origin == "physical" and clock_reference is None:
        raise AcquisitionError("physical preparation/detection join requires an explicit measured clock reference")
    clock = clock_reference or ClockReference(acquisition.clock_domain, locked=acquisition.clock_locked)
    clock.validate()
    if clock.domain != acquisition.clock_domain or not clock.locked or not acquisition.clock_locked:
        raise AcquisitionError("preparation/detection clock is not aligned with the acquisition")
    if set(detector_bit_map) != set(acquisition.configuration["channels"]) or any(
            type(bit) is not int or bit not in (0, 1) for bit in detector_bit_map.values()):
        raise AcquisitionError("detector wiring map must specify every configured channel")
    observed = Counter((event.timestamp_ps, detector_bit_map[event.channel])
                       for event in acquisition.events if event.event_type == "TimeTag")
    claimed = Counter((pulse.detection_timestamp_ps, pulse.analyzed_bit) for pulse in pulses if pulse.detected)
    if observed != claimed:
        raise AcquisitionError("preparation/analysis records do not match acquired clicks exactly")
    raw_hash = digest(asdict(acquisition))
    block = ObservationBlock(acquisition.experiment_id, acquisition.device_id, "BB84-single-photon",
                             acquisition.origin, acquisition.mode,
                             f"{acquisition.source_id};prepare={preparation_source_id};analyze={analysis_source_id}",
                             clock, list(pulses),
                             acquisition.duration_ps, acquisition.adapter_id, digest(acquisition.configuration),
                             raw_hash, acquisition.source_record,
                             acquisition.diagnostics.get("capture_start_ps", 0))
    block.validate()
    return block
