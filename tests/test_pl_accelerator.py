from dataclasses import replace
import struct

import pytest

from quantum_autopilot.lab.pl_accelerator import BIN, REQUEST, GateCycleConfig, decode_histogram, encode_request
from quantum_autopilot.lab.pspl import CorrelationRequest, PSPLStateError, TimestampBlock, TimestampEvent, cpu_correlation


def fixture():
    start = 2**53 + 21
    block = TimestampBlock("session", 0, "clock", "config", start, 100,
        tuple(TimestampEvent(start + t, c) for t, c in ((0, -1), (1, 2), (5, 2), (10, -1), (15, 2))),
        "simulated", "fixture", "fixture-adapter")
    return block, CorrelationRequest(-1, 2, bin_width_ps=1, radius_ps=5)


def test_fixed_payload_preserves_large_integer_timestamps_and_signed_channels():
    block, request = fixture()
    header, events = encode_request(block.to_wire(), request, request_id=2**64-1, cycle_budget=777)
    assert REQUEST.size == 64 and len(events) == 5 * 16
    assert REQUEST.unpack(header) == (b"QCOR", 1, 0, 2**64-1, 5, -1, 2, 777, 1, 5, 0, 10_000_000)
    assert struct.unpack_from("<qiI", events) == (2**53 + 21, -1, 0)
    reference = cpu_correlation(block, request)
    response = b"".join(BIN.pack(n, i, 0) for i, n in enumerate(reference["counts"]))
    assert decode_histogram(response, returned_request_id=9, expected_request_id=9,
                            block_wire=block.to_wire(), request=request) == reference


@pytest.mark.parametrize("changes", [{"lost_events": 1}, {"clock_locked": False},
    {"quality_flags": ("overflow",)}, {"events": tuple(TimestampEvent(2**53+21, 1) for _ in range(257))}])
def test_profile_or_source_failure_rejects_before_encoding(changes):
    block, request = fixture()
    with pytest.raises(PSPLStateError):
        encode_request(replace(block, **changes).to_wire(), request, request_id=1)


@pytest.mark.parametrize("request_id,cycles", [(True, 1), (-1, 1), (2**64, 1), (1, 0), (1, 2**32)])
def test_invalid_identifiers_and_watchdogs(request_id, cycles):
    block, request = fixture()
    with pytest.raises(PSPLStateError):
        encode_request(block.to_wire(), request, request_id=request_id, cycle_budget=cycles)


@pytest.mark.parametrize("response", [b"", b"x", BIN.pack(0, 0, 0), BIN.pack(0, 0xFFFFFFFF, 0),
    BIN.pack(1, 0xFFFFFFFF, 128), BIN.pack(0, 0xFFFFFFFF, 256),
    b"".join(BIN.pack(0, i, 1 if i == 4 else 0) for i in range(11)),
    b"".join(BIN.pack(0, 10-i, 0) for i in range(11))])
def test_partial_reordered_and_invalid_faults_never_publish(response):
    block, request = fixture()
    with pytest.raises(PSPLStateError):
        decode_histogram(response, returned_request_id=1, expected_request_id=1,
                         block_wire=block.to_wire(), request=request)


@pytest.mark.parametrize("status", [1, 2, 4, 8, 16, 32, 64, 128, 129])
def test_fault_status_is_explicit_and_contains_no_metrics(status):
    block, request = fixture()
    with pytest.raises(PSPLStateError, match="rejected block"):
        decode_histogram(BIN.pack(0, 0xFFFFFFFF, status), returned_request_id=1, expected_request_id=1,
                         block_wire=block.to_wire(), request=request)


def test_stale_sideband_rejected():
    block, request = fixture()
    with pytest.raises(PSPLStateError, match="stale"):
        decode_histogram(BIN.pack(0, 0xFFFFFFFF, 128), returned_request_id=2, expected_request_id=1,
                         block_wire=block.to_wire(), request=request)


def test_gate_picoseconds_convert_exactly_without_arming_or_rounding():
    config = GateCycleConfig.from_picoseconds(clock_hz=100_000_000, mask=3,
        period_ps=100_000, width_ps=20_000, watchdog_ps=1_000_000)
    assert (config.period_cycles, config.width_cycles, config.watchdog_cycles) == (10, 2, 100)


@pytest.mark.parametrize("patch", [{"clock_hz": True}, {"mask": 256}, {"mask": 0},
    {"width_ps": 1}, {"width_ps": 200_000}, {"watchdog_ps": 0}, {"period_ps": 2**63}])
def test_gate_unrepresentable_or_invalid_timing_is_rejected(patch):
    value = dict(clock_hz=100_000_000, mask=3, period_ps=100_000, width_ps=20_000, watchdog_ps=1_000_000)
    with pytest.raises(PSPLStateError):
        GateCycleConfig.from_picoseconds(**{**value, **patch})
