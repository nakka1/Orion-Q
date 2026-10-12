"""Fixed-size QCOR accelerator ABI; OQPL provenance remains on the PS.

These are application payloads, never DMA descriptors or physical addresses.
The bounded RTL reference profile is deliberately smaller than OQPL ingress.
No codec operation configures or arms a physical output.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import struct

from .pspl import CorrelationRequest, EVENT, PSPLStateError, TimestampBlock


REQUEST = struct.Struct("<4sHHQIiiIQQqQ")
BIN = struct.Struct("<QII")
MAX_EVENTS = 256
MAX_BINS = 257
TERMINAL_INDEX = 0xFFFFFFFF
STATUS = {1: "loss", 2: "event_error", 4: "bad_request", 8: "malformed_stream",
          16: "pair_budget", 32: "arithmetic_or_input_range", 64: "ordering", 128: "timeout"}


@dataclass(frozen=True)
class GateCycleConfig:
    """Reference eight-port configuration, never an arm or device-control grant.

    A declared clock frequency is not proof of a calibrated physical clock.
    Board-specific clock-domain crossing and interlocks remain mandatory.
    """
    clock_hz: int
    mask: int
    period_cycles: int
    width_cycles: int
    watchdog_cycles: int

    @classmethod
    def from_picoseconds(cls, *, clock_hz: int, mask: int, period_ps: int,
                         width_ps: int, watchdog_ps: int):
        _uint(clock_hz, 10**12, "clock_hz")
        _uint(mask, 255, "port_mask")
        if not clock_hz or not mask:
            raise PSPLStateError("gate clock and port mask must be positive")
        converted = []
        for name, duration in (("period", period_ps), ("width", width_ps), ("watchdog", watchdog_ps)):
            _uint(duration, 2**63-1, name + "_ps")
            cycles, remainder = divmod(duration * clock_hz, 10**12)
            if remainder or not 1 <= cycles <= 2**32-1:
                raise PSPLStateError("gate duration is not exactly representable in local clock cycles")
            converted.append(cycles)
        period, width, watchdog = converted
        if width > period:
            raise PSPLStateError("gate width cannot exceed its period")
        return cls(clock_hz, mask, period, width, watchdog)


def _uint(value, limit, name):
    if type(value) is not int or not 0 <= value <= limit:
        raise PSPLStateError(f"invalid accelerator {name}")
    return value


def encode_request(block_wire: bytes, request: CorrelationRequest, *, request_id: int,
                   cycle_budget: int = 1_000_000) -> tuple[bytes, bytes]:
    """Validate the complete block before handing any payload to an accelerator."""
    block = TimestampBlock.from_wire(block_wire)
    request.validate()
    _uint(request_id, 2**64 - 1, "request_id")
    _uint(cycle_budget, 2**32 - 1, "cycle_budget")
    if cycle_budget == 0:
        raise PSPLStateError("accelerator watchdog requires a positive cycle budget")
    if len(block.events) > MAX_EVENTS or 2 * request.radius_ps // request.bin_width_ps + 1 > MAX_BINS:
        raise PSPLStateError("block exceeds negotiated RTL reference profile")
    if block.lost_events or block.quality_flags or not block.clock_locked or any(e.flags for e in block.events):
        raise PSPLStateError("incomplete source cannot enter the accelerator")
    header = REQUEST.pack(b"QCOR", 1, 0, request_id, len(block.events), request.channel_a,
                          request.channel_b, cycle_budget, request.bin_width_ps,
                          request.radius_ps, request.delay_ps, request.max_pairs)
    events = b"".join(EVENT.pack(e.timestamp_ps, e.channel, e.flags) for e in block.events)
    return header, events


def decode_histogram(payload: bytes, *, returned_request_id: int, expected_request_id: int,
                     block_wire: bytes, request: CorrelationRequest) -> dict:
    """Reject partial, reordered, stale or faulted output before publishing metrics.

    The transport must supply the RTL request-id sideband and enforce TLAST.
    Equality against the independent CPU reference remains the pipeline's gate.
    """
    block = TimestampBlock.from_wire(block_wire)
    encode_request(block_wire, request, request_id=expected_request_id)
    _uint(returned_request_id, 2**64 - 1, "returned_request_id")
    if returned_request_id != expected_request_id:
        raise PSPLStateError("accelerator returned a stale request identity")
    if not isinstance(payload, bytes) or not payload or len(payload) % BIN.size:
        raise PSPLStateError("accelerator returned a partial histogram")
    count = 2 * request.radius_ps // request.bin_width_ps + 1
    if len(payload) > MAX_BINS * BIN.size:
        raise PSPLStateError("accelerator output exceeds negotiated profile")
    rows = [BIN.unpack_from(payload, offset) for offset in range(0, len(payload), BIN.size)]
    if len(rows) == 1 and rows[0][1] == TERMINAL_INDEX:
        value, _index, status = rows[0]
        if value or not status or status & ~sum(STATUS):
            raise PSPLStateError("malformed accelerator terminal fault")
        causes = ",".join(name for bit, name in STATUS.items() if status & bit)
        raise PSPLStateError("accelerator rejected block: " + causes)
    if len(rows) != count or any(index != i or status for i, (_value, index, status) in enumerate(rows)):
        raise PSPLStateError("accelerator histogram ordering, status or length invalid")
    counts = [row[0] for row in rows]
    pairs = sum(counts)
    if pairs > request.max_pairs:
        raise PSPLStateError("accelerator exceeded pair budget")
    return {"schema": "orion.lab.pspl-correlation.v1", "session_id": block.session_id,
            "sequence": block.sequence, "block_sha256": hashlib.sha256(block_wire).hexdigest(),
            "request": asdict(request), "counts": counts, "pairs": pairs,
            "bin_left_edges_ps": list(range(-request.radius_ps, request.radius_ps + 1, request.bin_width_ps)),
            "boundary": "inclusive radius; floor bins; last bin includes positive endpoint",
            "clock_domain": block.clock_domain, "cross_block_pairs_included": False}
