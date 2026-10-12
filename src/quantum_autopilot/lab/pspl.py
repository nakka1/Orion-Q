"""Versioned PS/PL transport contract and a bounded, explicitly emulated DMA.

This is not an AXI register driver or a real-time deadline qualification. Photon
timestamps and the injected DMA scheduling clock use integer picoseconds. Source
provenance is retained, never used to select a different correlation algorithm.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import deque
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import re
import struct
from threading import RLock
from typing import Protocol

from .photon import AcquisitionError, digest

ABI_MAJOR = 1
ABI_MINOR = 0
ABI_SCHEMA = "orion.lab.pspl.v1"
MAGIC = b"OQPL"
HEADER = struct.Struct("<4sHHIIIIQqqQQ")  # 64-byte application header, not an AMD BD.
EVENT = struct.Struct("<qiI")  # timestamp int64 ps, channel int32, quality flags uint32.
MAX_EVENTS = 65_536
MAX_METADATA_BYTES = 4096
MAX_WIRE_BYTES = HEADER.size + MAX_METADATA_BYTES + MAX_EVENTS * EVENT.size + 32
MAX_RING_BYTES = 64 * 1024 * 1024
FLAG_OVERFLOW = 1
FLAG_ERROR = 2


class PSPLStateError(AcquisitionError):
    """Ownership or acquisition state must be explicitly reconciled."""


def _integer(value, name, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise AcquisitionError(f"{name} requires bounded integer units")
    return value


def _identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/+@ ;=-]{0,511}", value):
        raise AcquisitionError(f"{name} requires an identified, bounded source")


@dataclass(frozen=True)
class TimestampEvent:
    timestamp_ps: int
    channel: int
    flags: int = 0

    def validate(self):
        _integer(self.timestamp_ps, "timestamp_ps")
        _integer(self.channel, "channel", -(2**31), 2**31 - 1)
        if self.channel == 0 and self.flags == 0:
            raise AcquisitionError("channel zero is reserved")
        _integer(self.flags, "flags", 0, FLAG_OVERFLOW | FLAG_ERROR)


@dataclass(frozen=True)
class TimestampBlock:
    session_id: str
    sequence: int
    clock_domain: str
    configuration_id: str
    start_ps: int
    duration_ps: int
    events: tuple[TimestampEvent, ...]
    origin: str
    source_id: str
    adapter_id: str
    clock_tick_ps: int = 1
    alignment_uncertainty_ps: int = 0
    clock_locked: bool = True
    lost_events: int = 0
    loss_count_exact: bool = False
    quality_flags: tuple[str, ...] = ()

    def validate(self):
        for name in ("session_id", "clock_domain", "configuration_id", "source_id", "adapter_id"):
            _identifier(getattr(self, name), name)
        _integer(self.sequence, "sequence", 0, 2**64 - 1)
        _integer(self.start_ps, "start_ps")
        _integer(self.duration_ps, "duration_ps", 1)
        if self.start_ps + self.duration_ps > 2**63 - 1:
            raise AcquisitionError("block interval exceeds signed 64-bit ps")
        _integer(self.clock_tick_ps, "clock_tick_ps", 1)
        _integer(self.alignment_uncertainty_ps, "alignment_uncertainty_ps")
        _integer(self.lost_events, "lost_events")
        if type(self.clock_locked) is not bool or type(self.loss_count_exact) is not bool:
            raise AcquisitionError("clock/loss flags must be boolean")
        if not isinstance(self.origin, str) or self.origin not in {"physical", "simulated"}:
            raise AcquisitionError("data origin must be explicit")
        if not isinstance(self.events, tuple) or len(self.events) > MAX_EVENTS:
            raise AcquisitionError("events require an immutable bounded tuple")
        if not isinstance(self.quality_flags, tuple) or len(self.quality_flags) > 32:
            raise AcquisitionError("quality flags require a bounded tuple")
        for flag in self.quality_flags:
            _identifier(flag, "quality_flag")
        previous = self.start_ps
        for event in self.events:
            if not isinstance(event, TimestampEvent):
                raise AcquisitionError("typed timestamp event required")
            event.validate()
            if not previous <= event.timestamp_ps < self.start_ps + self.duration_ps:
                raise AcquisitionError("event outside interval or clock regression")
            previous = event.timestamp_ps

    def _metadata(self):
        return {name: getattr(self, name) for name in (
            "session_id", "clock_domain", "configuration_id", "origin", "source_id", "adapter_id",
            "clock_locked", "lost_events", "loss_count_exact", "quality_flags")}

    def to_wire(self) -> bytes:
        self.validate()
        metadata = json.dumps(self._metadata(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(metadata) > MAX_METADATA_BYTES:
            raise AcquisitionError("PS/PL metadata limit exceeded")
        header = HEADER.pack(MAGIC, ABI_MAJOR, ABI_MINOR, HEADER.size, EVENT.size, len(metadata),
                             len(self.events), self.sequence, self.start_ps, self.duration_ps,
                             self.clock_tick_ps, self.alignment_uncertainty_ps)
        payload = header + metadata + b"".join(EVENT.pack(e.timestamp_ps, e.channel, e.flags) for e in self.events)
        return payload + hashlib.sha256(payload).digest()

    @classmethod
    def from_wire(cls, data: bytes) -> "TimestampBlock":
        if not isinstance(data, bytes) or not HEADER.size + 32 <= len(data) <= MAX_WIRE_BYTES:
            raise AcquisitionError("PS/PL transfer size invalid")
        (magic, major, minor, header_size, event_size, metadata_bytes, count, sequence,
         start, duration, tick, uncertainty) = HEADER.unpack_from(data)
        if (magic, major, minor, header_size, event_size) != (MAGIC, ABI_MAJOR, ABI_MINOR, HEADER.size, EVENT.size):
            raise AcquisitionError("unsupported PS/PL ABI")
        if metadata_bytes > MAX_METADATA_BYTES or count > MAX_EVENTS or len(data) != HEADER.size + metadata_bytes + count * EVENT.size + 32:
            raise AcquisitionError("PS/PL descriptor lengths inconsistent")
        if hashlib.sha256(data[:-32]).digest() != data[-32:]:
            raise AcquisitionError("PS/PL transfer integrity failed")
        def unique(pairs):
            result = {}
            for name, value in pairs:
                if name in result:
                    raise AcquisitionError("duplicate PS/PL metadata")
                result[name] = value
            return result
        try:
            metadata = json.loads(data[HEADER.size:HEADER.size + metadata_bytes], object_pairs_hook=unique)
            required = {"session_id", "clock_domain", "configuration_id", "origin", "source_id", "adapter_id",
                        "clock_locked", "lost_events", "loss_count_exact", "quality_flags"}
            if not isinstance(metadata, dict) or set(metadata) != required:
                raise AcquisitionError("PS/PL metadata fields invalid")
            if not isinstance(metadata["quality_flags"], list):
                raise AcquisitionError("PS/PL quality flags must be a list")
            metadata["quality_flags"] = tuple(metadata["quality_flags"])
            offset = HEADER.size + metadata_bytes
            events = tuple(TimestampEvent(*EVENT.unpack_from(data, offset + index * EVENT.size)) for index in range(count))
            block = cls(**metadata, sequence=sequence, start_ps=start, duration_ps=duration, events=events,
                        clock_tick_ps=tick, alignment_uncertainty_ps=uncertainty)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise AcquisitionError("invalid PS/PL metadata") from error
        block.validate()
        return block

    @classmethod
    def from_capture(cls, batch, *, session_id: str) -> "TimestampBlock":
        """Normalize actual committed input; raw timestamps do not imply QKD bits."""
        batch.validate()
        acquisition = batch.acquisition
        flags = {"TimeTag": 0, "OverflowBegin": FLAG_OVERFLOW, "OverflowEnd": FLAG_OVERFLOW,
                 "MissedEvents": FLAG_OVERFLOW, "Error": FLAG_ERROR}
        events = tuple(TimestampEvent(e.timestamp_ps, e.channel, flags[e.event_type]) for e in acquisition.events)
        block = cls(session_id, batch.source_sequence, acquisition.clock_domain,
                    acquisition.configuration["configuration_id"] if "configuration_id" in acquisition.configuration
                    else digest(acquisition.configuration),
                    acquisition.diagnostics.get("capture_start_ps", 0), acquisition.duration_ps, events,
                    acquisition.origin, acquisition.source_id, acquisition.adapter_id,
                    clock_locked=acquisition.clock_locked, lost_events=batch.lost_events or 0,
                    loss_count_exact=batch.loss_count_exact,
                    quality_flags=tuple(acquisition.diagnostics.get("quality_flags", ())))
        block.validate()
        return block


@dataclass(frozen=True)
class DMAConfig:
    slots: int = 8
    max_events_per_block: int = MAX_EVENTS
    descriptor_deadline_ps: int = 10**12
    abi_major: int = ABI_MAJOR
    abi_minor: int = ABI_MINOR

    def validate(self):
        _integer(self.slots, "slots", 2, 1024)
        _integer(self.max_events_per_block, "max_events_per_block", 1, MAX_EVENTS)
        _integer(self.descriptor_deadline_ps, "descriptor_deadline_ps", 1)
        if (type(self.abi_major) is not int or type(self.abi_minor) is not int
                or (self.abi_major, self.abi_minor) != (ABI_MAJOR, ABI_MINOR)):
            raise AcquisitionError("unsupported DMA ABI")
        if self.slots * (HEADER.size + MAX_METADATA_BYTES + self.max_events_per_block * EVENT.size + 32) > MAX_RING_BYTES:
            raise AcquisitionError("configured ring exceeds the application wire-buffer budget")


@dataclass(frozen=True)
class DMAOwnershipToken:
    session_id: str
    slot: int
    generation: int
    sequence: int


@dataclass(frozen=True)
class DMAReadLease:
    token: DMAOwnershipToken
    block: TimestampBlock
    acquire_fence: int


@dataclass
class _Slot:
    owner: str = "free"
    token: DMAOwnershipToken | None = None
    block: TimestampBlock | None = None
    submitted_ps: int = 0
    completion_fence: int = 0
    wire_bytes: int = 0
    acquire_fence: int = 0


class EmulatedDMARing:
    """Bounded scatter/gather ownership model; no mapped memory or hardware I/O.

    Completion order may differ, but PS delivery is strictly submission order.
    Uncertain state retains completed data for draining and blocks new work.
    """
    def __init__(self):
        self._lock = RLock()
        self._slots = []
        self._state = "unconfigured"
        self._connected = True
        self._config = None
        self._session = self._clock = self._configuration = None
        self._expected_submit = self._expected_acquire = 0
        self._last_end_ps = None
        self._now_ps = 0
        self._generation = self._fence = 0
        self._fences = deque(maxlen=256)
        self._cause = None
        self._overruns = self._known_lost = self._unknown_loss = 0
        self._submitted = self._completed = self._delivered = 0

    def configure(self, config: DMAConfig, *, session_id: str, clock_domain: str, configuration_id: str):
        config.validate()
        for name, value in (("session_id", session_id), ("clock_domain", clock_domain), ("configuration_id", configuration_id)):
            _identifier(value, name)
        with self._lock:
            if self._state not in {"unconfigured", "cancelled", "ready"} or any(s.owner != "free" for s in self._slots):
                raise PSPLStateError("configuration requires verified quiescent ownership")
            if not self._connected or session_id == self._session:
                raise PSPLStateError("configuration requires connection and a new session")
            self._config = config
            self._session, self._clock, self._configuration = session_id, clock_domain, configuration_id
            self._slots = [_Slot() for _ in range(config.slots)]
            self._expected_submit = self._expected_acquire = 0
            self._last_end_ps = None
            self._cause = None
            self._state = "ready"

    def start(self):
        with self._lock:
            if self._state != "ready" or not self._connected:
                raise PSPLStateError("acquisition requires verified ready configuration")
            self._state = "running"

    def _barrier(self, kind, token):
        self._fence += 1
        self._fences.append({"ordinal": self._fence, "kind": kind, "slot": token.slot,
                             "generation": token.generation, "sequence": token.sequence})
        return self._fence

    def _fault(self, cause, *, known_lost=0, unknown=False):
        self._state, self._cause = "uncertain", cause
        self._known_lost += known_lost
        self._unknown_loss += int(unknown)
        raise PSPLStateError(cause)

    def _poll(self, now_ps):
        _integer(now_ps, "DMA virtual now_ps")
        if now_ps < self._now_ps:
            raise AcquisitionError("DMA scheduling clock regression")
        self._now_ps = now_ps
        if self._state != "uncertain" and self._config is not None and any(
                s.owner == "pl" and now_ps - s.submitted_ps >= self._config.descriptor_deadline_ps for s in self._slots):
            self._fault("descriptor_deadline", unknown=True)

    def check_deadlines(self, *, now_ps: int):
        with self._lock:
            self._poll(now_ps)

    def submit(self, block: TimestampBlock, *, now_ps: int) -> DMAOwnershipToken:
        block.validate()  # Complete parameter validation before touching a descriptor.
        wire_bytes = len(block.to_wire())
        with self._lock:
            self._poll(now_ps)
            if self._state != "running" or not self._connected:
                raise PSPLStateError("DMA admission is blocked")
            if (block.session_id, block.clock_domain, block.configuration_id) != (self._session, self._clock, self._configuration):
                raise AcquisitionError("block session/clock/configuration mismatch")
            if len(block.events) > self._config.max_events_per_block:
                raise AcquisitionError("block exceeds negotiated capacity")
            if block.sequence != self._expected_submit or self._last_end_ps is not None and block.start_ps < self._last_end_ps:
                raise AcquisitionError("block sequence gap/replay or clock overlap")
            if block.lost_events or block.quality_flags or any(e.flags for e in block.events):
                self._fault("source_data_loss_or_error", known_lost=block.lost_events, unknown=not block.loss_count_exact)
            if not block.clock_locked:
                self._fault("source_clock_unlocked", unknown=True)
            available = next((i for i, s in enumerate(self._slots) if s.owner == "free"), None)
            if available is None:
                self._overruns += 1
                self._fault("dma_ring_overrun", known_lost=len(block.events))
            self._generation += 1
            token = DMAOwnershipToken(self._session, available, self._generation, block.sequence)
            self._barrier("sync_for_device_release", token)
            self._slots[available] = _Slot("pl", token, block, now_ps, wire_bytes=wire_bytes)
            self._expected_submit += 1
            self._last_end_ps = block.start_ps + block.duration_ps
            self._submitted += 1
            return token

    def _slot(self, token, owner):
        if (not isinstance(token, DMAOwnershipToken) or type(token.slot) is not int
                or type(token.generation) is not int or type(token.sequence) is not int
                or not 0 <= token.slot < len(self._slots) or token.generation <= 0 or token.sequence < 0):
            raise AcquisitionError("invalid ownership token")
        slot = self._slots[token.slot]
        if slot.token != token or slot.owner != owner:
            self._fault("ownership_violation", unknown=True)
        return slot

    def complete(self, token: DMAOwnershipToken, *, now_ps: int, transferred_bytes: int | None = None,
                 fence_visible: bool = True):
        if transferred_bytes is not None:
            _integer(transferred_bytes, "transferred_bytes", 0, MAX_WIRE_BYTES)
        if type(fence_visible) is not bool:
            raise AcquisitionError("completion fence flag must be boolean")
        with self._lock:
            self._poll(now_ps)
            if self._state not in {"running", "draining"}:
                raise PSPLStateError("unverified DMA completion is blocked")
            slot = self._slot(token, "pl")
            if not fence_visible or transferred_bytes is not None and transferred_bytes != slot.wire_bytes:
                self._fault("completion_fence_or_length_invalid", unknown=True)
            slot.completion_fence = self._barrier("device_completion_release", token)
            slot.owner = "completed"
            self._completed += 1

    def acquire(self, *, now_ps: int) -> DMAReadLease | None:
        with self._lock:
            self._poll(now_ps)
            slot = next((s for s in self._slots if s.token is not None and s.token.sequence == self._expected_acquire), None)
            if slot is None or slot.owner != "completed":
                return None
            if not slot.completion_fence:
                self._fault("completion_fence_missing", unknown=True)
            fence = self._barrier("sync_for_cpu_acquire", slot.token)
            slot.acquire_fence = fence
            slot.owner = "ps"
            self._expected_acquire += 1
            self._delivered += 1
            return DMAReadLease(slot.token, slot.block, fence)

    def release(self, lease: DMAReadLease):
        if not isinstance(lease, DMAReadLease):
            raise AcquisitionError("typed PS lease required")
        with self._lock:
            slot = self._slot(lease.token, "ps")
            if slot.block != lease.block or lease.acquire_fence != slot.acquire_fence:
                self._fault("PS_lease_mismatch", unknown=True)
            self._barrier("cpu_release", lease.token)
            self._slots[lease.token.slot] = _Slot()
            if self._state == "draining" and all(s.owner == "free" for s in self._slots):
                self._state = "cancelled"

    def cancel(self):
        """Stop admission; already submitted transfers remain drainable."""
        with self._lock:
            if self._state in {"running", "ready"}:
                self._state = "draining" if any(s.owner != "free" for s in self._slots) else "cancelled"

    def drain(self, *, now_ps: int) -> tuple[TimestampBlock, ...]:
        """Consume only completed buffers; never synthesize missing DMA work."""
        result = []
        with self._lock:
            while (lease := self.acquire(now_ps=now_ps)) is not None:
                result.append(lease.block)
                self.release(lease)
        return tuple(result)

    def disconnect(self):
        with self._lock:
            self._connected = False
            self._state, self._cause = "uncertain", "disconnected"
            self._unknown_loss += int(any(s.owner == "pl" for s in self._slots))

    def reconnect(self):
        with self._lock:
            self._connected = True  # Transport presence never restores acquisition authority.

    def mark_uncertain(self, cause: str):
        """Block after processing/persistence failure without accepting new DMA."""
        if cause not in {"processing_failed", "persistence_failed", "cancelled_during_processing", "source_block_invalid"}:
            raise AcquisitionError("unknown processing fault category")
        with self._lock:
            if self._state != "uncertain":
                self._state, self._cause = "uncertain", cause
                self._unknown_loss += int(any(s.owner == "pl" for s in self._slots))

    def discard_unverified(self):
        """Explicit emulator reset; unfinished data cannot become accepted evidence."""
        with self._lock:
            if self._state != "uncertain":
                raise PSPLStateError("discard requires uncertain state")
            if any(s.owner == "ps" for s in self._slots):
                raise PSPLStateError("PS still owns a buffer")
            dropped = sum(len(s.block.events) for s in self._slots if s.block is not None)
            self._unknown_loss += int(bool(dropped))
            self._slots = [_Slot() for _ in self._slots]
            return {"discarded_events": dropped, "accepted": False, "hardware_reset_performed": False}

    def verify_configuration(self, readback: dict, *, new_session_id: str):
        """Explicit virtual readback after quiescence, followed by a fresh session."""
        _identifier(new_session_id, "new_session_id")
        try:
            if not isinstance(readback, dict) or set(readback) != {"config", "clock_domain", "configuration_id"}:
                raise AcquisitionError("readback fields invalid")
            if not isinstance(readback["config"], dict):
                raise AcquisitionError("readback configuration missing")
            parsed = DMAConfig(**readback["config"])
            parsed.validate()
            readback = {"config": asdict(parsed), "clock_domain": readback["clock_domain"],
                        "configuration_id": readback["configuration_id"]}
        except (TypeError, AcquisitionError) as error:
            raise PSPLStateError("configuration readback is not a valid DMA configuration") from error
        with self._lock:
            expected = {"config": asdict(self._config) if self._config else None,
                        "clock_domain": self._clock, "configuration_id": self._configuration}
            if (self._state != "uncertain" or self._config is None or not self._connected or any(s.owner != "free" for s in self._slots)
                    or not isinstance(readback, dict) or readback != expected or new_session_id == self._session):
                raise PSPLStateError("configuration readback does not establish safe acquisition")
            self._session = new_session_id
            self._expected_submit = self._expected_acquire = 0
            self._last_end_ps = None
            self._state, self._cause = "ready", None

    def snapshot(self):
        with self._lock:
            return {"schema": ABI_SCHEMA, "transport": "emulated-axi-dma", "state": self._state,
                    "cause": self._cause, "connected": self._connected, "session_id": self._session,
                    "configuration_readback": {"config": asdict(self._config) if self._config else None,
                        "clock_domain": self._clock, "configuration_id": self._configuration},
                    "slots": len(self._slots), "ownership": [s.owner for s in self._slots],
                    "submitted_blocks": self._submitted, "completed_blocks": self._completed,
                    "delivered_blocks": self._delivered, "overruns": self._overruns,
                    "known_lost_events": self._known_lost, "unknown_loss_incidents": self._unknown_loss,
                    "virtual_now_ps": self._now_ps, "fence_count": self._fence, "recent_fences": list(self._fences),
                    "hardware_validated": False, "physical_actuation_locked": True}


@dataclass(frozen=True)
class CorrelationRequest:
    channel_a: int
    channel_b: int
    bin_width_ps: int = 50
    radius_ps: int = 2000
    delay_ps: int = 0
    max_pairs: int = 10_000_000

    def validate(self):
        TimestampEvent(0, self.channel_a).validate()
        TimestampEvent(0, self.channel_b).validate()
        if self.channel_a == self.channel_b:
            raise AcquisitionError("correlation requires distinct channels")
        _integer(self.bin_width_ps, "bin_width_ps", 1)
        _integer(self.radius_ps, "radius_ps", 1)
        _integer(self.delay_ps, "delay_ps", -(2**63) + 1, 2**63 - 1)
        _integer(self.max_pairs, "max_pairs", 1, 10_000_000)
        if self.radius_ps % self.bin_width_ps or 2 * self.radius_ps // self.bin_width_ps + 1 > 65537:
            raise AcquisitionError("correlation bins exceed bounded geometry")


class CorrelationWorker(Protocol):
    """CPU or future qualified PL implementation, receiving the same wire ABI."""
    def correlate(self, block_wire: bytes, request: CorrelationRequest) -> dict: ...


def cpu_correlation(block: TimestampBlock, request: CorrelationRequest) -> dict:
    """Deterministic all-pairs histogram; no bases, fidelity, QKD key or accidentals estimate."""
    block.validate()
    request.validate()
    if block.lost_events or block.quality_flags or not block.clock_locked or any(e.flags for e in block.events):
        raise PSPLStateError("incomplete or unsynchronized data cannot yield accepted correlation")
    a = [e.timestamp_ps for e in block.events if e.channel == request.channel_a]
    b = [e.timestamp_ps for e in block.events if e.channel == request.channel_b]
    bins = [0] * (2 * request.radius_ps // request.bin_width_ps + 1)
    pairs = 0
    for timestamp in a:
        center = timestamp + request.delay_ps
        lo, hi = bisect_left(b, center - request.radius_ps), bisect_right(b, center + request.radius_ps)
        if pairs + hi - lo > request.max_pairs:
            raise PSPLStateError("correlation pair budget exceeded; partial histogram rejected")
        for other in b[lo:hi]:
            bins[(other - center + request.radius_ps) // request.bin_width_ps] += 1
        pairs += hi - lo
    return {"schema": "orion.lab.pspl-correlation.v1", "session_id": block.session_id,
            "sequence": block.sequence, "block_sha256": hashlib.sha256(block.to_wire()).hexdigest(),
            "request": asdict(request), "counts": bins, "pairs": pairs,
            "bin_left_edges_ps": list(range(-request.radius_ps, request.radius_ps + 1, request.bin_width_ps)),
            "boundary": "inclusive radius; floor bins; last bin includes positive endpoint",
            "clock_domain": block.clock_domain, "cross_block_pairs_included": False}


def execute_correlation(block: TimestampBlock, request: CorrelationRequest, *, worker: CorrelationWorker | None = None):
    """Validate a delegated implementation against the CPU reference, fail closed.

    Qualification mode deliberately includes CPU comparison cost. Production
    offload without this comparison needs separately measured equivalence.
    """
    reference = cpu_correlation(block, request)
    if worker is None:
        return reference
    result = worker.correlate(block.to_wire(), request)
    try:
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise PSPLStateError("delegated correlation result is not valid JSON") from error
    if encoded != json.dumps(reference, sort_keys=True, separators=(",", ":"), allow_nan=False):
        raise PSPLStateError("delegated correlation differs from CPU reference")
    return json.loads(encoded)  # Detach the validated result from a worker's mutable buffers.


@dataclass(frozen=True)
class PortCommand:
    session_id: str
    command_id: str
    port: str
    operation: str
    parameters: dict = field(default_factory=dict)

    def prepare(self):
        for name in ("session_id", "command_id", "port"):
            _identifier(getattr(self, name), name)
        if self.operation not in {"route", "attenuation", "phase", "trigger"}:
            raise AcquisitionError("unknown PL port command")
        if not isinstance(self.parameters, dict):
            raise AcquisitionError("port parameters require an object")
        expected = {"route": {"target_port"}, "attenuation": {"attenuation_db"},
                    "phase": {"phase_rad"}, "trigger": {"period_ps", "width_ps"}}[self.operation]
        if set(self.parameters) != expected:
            raise AcquisitionError("port command requires explicit operation parameters and units")
        if self.operation == "route":
            _identifier(self.parameters["target_port"], "target_port")
        elif self.operation in {"attenuation", "phase"}:
            value = next(iter(self.parameters.values()))
            lower, upper = (0., 120.) if self.operation == "attenuation" else (-math.tau, math.tau)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
                raise AcquisitionError("port parameter exceeds application preparation limits")
        else:
            _integer(self.parameters["period_ps"], "period_ps", 1)
            _integer(self.parameters["width_ps"], "width_ps", 1)
            if self.parameters["width_ps"] > self.parameters["period_ps"]:
                raise AcquisitionError("trigger width exceeds period")
        try:
            payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as error:
            raise AcquisitionError("port parameters must be finite JSON") from error
        if len(payload.encode()) > MAX_METADATA_BYTES:
            raise AcquisitionError("port command exceeds bounded preparation payload")
        return {"schema": "orion.lab.pspl-port-command.v1", "command_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                "session_id": self.session_id, "command_id": self.command_id, "port": self.port,
                "operation": self.operation, "status": "prepared", "physical_actuation_locked": True,
                "physical_action_performed": False}

    def execute(self, *, mode="prepare"):
        result = self.prepare()
        if mode not in {"prepare", "simulate"}:
            raise PSPLStateError("physical PL port commands remain locked")
        return {**result, "status": "simulated" if mode == "simulate" else "prepared", "mode": mode}
