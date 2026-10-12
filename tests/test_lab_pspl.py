"""PS/PL contract verification, not DMA/FPGA or electrical qualification."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import random
import struct

import pytest

from quantum_autopilot.lab.capture import CaptureJournal, SyntheticRateSource
from quantum_autopilot.lab.photon import AcquisitionError
from quantum_autopilot.lab.pspl import (
    CorrelationRequest, DMAConfig, EmulatedDMARing,
    HEADER, MAX_METADATA_BYTES, PSPLStateError, PortCommand, TimestampBlock,
    TimestampEvent, cpu_correlation, execute_correlation,
)


def block(sequence=0, *, session="session-1", start=0, events=None, **kwargs):
    events = events if events is not None else (TimestampEvent(start + 1, 1), TimestampEvent(start + 4, 2))
    return TimestampBlock(session, sequence, "clock-1", "config-1", start, 100,
                          tuple(events), "simulated", "fixture-recording", "fixture-adapter", **kwargs)


def running_ring(*, slots=2, deadline=1000):
    ring = EmulatedDMARing()
    ring.configure(DMAConfig(slots=slots, descriptor_deadline_ps=deadline), session_id="session-1",
                   clock_domain="clock-1", configuration_id="config-1")
    ring.start()
    return ring


def rehash(data):
    return bytes(data[:-32]) + hashlib.sha256(data[:-32]).digest()


def test_wire_preserves_ps_above_float_precision_and_explicit_abi():
    start = 2**53 + 123
    original = block(start=start, events=[TimestampEvent(start + 1, -2), TimestampEvent(start + 3, 7)])
    wire = original.to_wire()
    assert wire[:8] == b"OQPL\x01\x00\x00\x00"
    assert wire[8:16] == b"\x40\x00\x00\x00\x10\x00\x00\x00"
    offset = 64 + int.from_bytes(wire[16:20], "little")
    assert int.from_bytes(wire[offset:offset + 8], "little", signed=True) == start + 1
    assert TimestampBlock.from_wire(wire) == original
    assert HEADER.size == 64


@pytest.mark.parametrize("offset,value", [(4, 2), (6, 1), (8, 63), (12, 8), (20, 1_000_000)])
def test_wire_rejects_incompatible_layout_before_decoding_events(offset, value):
    wire = bytearray(block().to_wire())
    size = 2 if offset in {4, 6} else 4
    wire[offset:offset + size] = value.to_bytes(size, "little")
    with pytest.raises(AcquisitionError):
        TimestampBlock.from_wire(rehash(wire))


def test_wire_detects_corruption_truncation_and_unknown_metadata():
    wire = bytearray(block().to_wire())
    wire[60] ^= 1
    with pytest.raises(AcquisitionError, match="integrity"):
        TimestampBlock.from_wire(bytes(wire))
    with pytest.raises(AcquisitionError, match="lengths"):
        TimestampBlock.from_wire(block().to_wire()[:-1])
    wire = block().to_wire()
    metadata_size = struct.unpack_from("<I", wire, 16)[0]
    metadata = json.loads(wire[64:64 + metadata_size])
    metadata["origin"] = []
    modified = json.dumps(metadata, separators=(",", ":")).encode()
    header = bytearray(wire[:64])
    struct.pack_into("<I", header, 16, len(modified))
    payload = bytes(header) + modified + wire[64 + metadata_size:-32]
    with pytest.raises(AcquisitionError, match="origin"):
        TimestampBlock.from_wire(payload + hashlib.sha256(payload).digest())


@pytest.mark.parametrize("timestamp", [True, 1.5, -1, 2**63])
def test_event_timestamp_is_exact_signed_ps(timestamp):
    with pytest.raises(AcquisitionError):
        TimestampEvent(timestamp, 1).validate()


def test_event_order_window_and_zero_channel_are_validated():
    with pytest.raises(AcquisitionError):
        block(events=[TimestampEvent(4, 1), TimestampEvent(3, 2)]).validate()
    with pytest.raises(AcquisitionError):
        block(events=[TimestampEvent(100, 1)]).validate()
    with pytest.raises(AcquisitionError):
        TimestampEvent(1, 0).validate()
    TimestampEvent(1, 0, flags=1).validate()  # A loss marker is not a detector click.


@pytest.mark.parametrize("config", [DMAConfig(slots=1), DMAConfig(slots=True),
    DMAConfig(max_events_per_block=0), DMAConfig(descriptor_deadline_ps=1.5),
    DMAConfig(abi_major=2), DMAConfig(slots=1024)])
def test_invalid_configuration_cannot_mutate_ring(config):
    ring = EmulatedDMARing()
    before = ring.snapshot()
    with pytest.raises(AcquisitionError):
        ring.configure(config, session_id="session-1", clock_domain="clock-1", configuration_id="config-1")
    assert ring.snapshot() == before


def test_completion_reordering_does_not_reorder_blocks_or_reuse_owned_buffer():
    ring = running_ring()
    zero, one = block(), block(1, start=100)
    a, b = ring.submit(zero, now_ps=0), ring.submit(one, now_ps=0)
    ring.complete(b, now_ps=1)
    assert ring.acquire(now_ps=2) is None
    ring.complete(a, now_ps=3)
    first, second = ring.acquire(now_ps=4), ring.acquire(now_ps=4)
    assert (first.block, second.block) == (zero, one)
    assert ring.snapshot()["ownership"] == ["ps", "ps"]
    ring.release(first)
    third = ring.submit(block(2, start=200), now_ps=5)
    assert third.slot == a.slot and third.generation > a.generation
    ring.release(second)
    ring.complete(third, now_ps=6)
    assert ring.drain(now_ps=6) == (block(2, start=200),)
    assert ring.snapshot()["delivered_blocks"] == 3


def test_cancel_finishes_inflight_and_drains_without_data_loss():
    ring = running_ring()
    a, b = ring.submit(block(), now_ps=0), ring.submit(block(1, start=100), now_ps=0)
    ring.complete(a, now_ps=1)
    ring.cancel()
    with pytest.raises(PSPLStateError):
        ring.submit(block(2, start=200), now_ps=2)
    assert ring.drain(now_ps=2) == (block(),)
    assert ring.snapshot()["state"] == "draining"
    ring.complete(b, now_ps=3)
    assert ring.drain(now_ps=3) == (block(1, start=100),)
    assert ring.snapshot()["state"] == "cancelled"
    assert ring.snapshot()["known_lost_events"] == 0


def test_overrun_blocks_acquisition_preserves_completed_blocks_and_requires_readback():
    ring = running_ring()
    a = ring.submit(block(), now_ps=0)
    ring.submit(block(1, start=100), now_ps=0)
    ring.complete(a, now_ps=1)
    with pytest.raises(PSPLStateError, match="overrun"):
        ring.submit(block(2, start=200), now_ps=2)
    assert ring.snapshot()["overruns"] == 1
    assert ring.snapshot()["known_lost_events"] == 2
    assert ring.drain(now_ps=2) == (block(),)
    with pytest.raises(PSPLStateError):
        ring.start()
    readback = ring.snapshot()["configuration_readback"]
    with pytest.raises(PSPLStateError):
        ring.verify_configuration(readback, new_session_id="session-2")
    assert ring.discard_unverified()["accepted"] is False
    with pytest.raises(PSPLStateError):
        ring.verify_configuration({**readback, "configuration_id": "wrong"}, new_session_id="session-2")
    ring.verify_configuration(readback, new_session_id="session-2")
    ring.start()
    recovered = block(session="session-2")
    token = ring.submit(recovered, now_ps=3)
    ring.complete(token, now_ps=4)
    assert ring.drain(now_ps=4) == (recovered,)
    assert ring.snapshot()["known_lost_events"] == 2


@pytest.mark.parametrize("failure", ["late", "short", "fence"])
def test_dma_failure_never_returns_successful_payload(failure):
    ring = running_ring(deadline=10)
    token = ring.submit(block(), now_ps=0)
    kwargs = {"now_ps": 10} if failure == "late" else {"now_ps": 1,
              "transferred_bytes": 1} if failure == "short" else {"now_ps": 1, "fence_visible": False}
    with pytest.raises(PSPLStateError):
        ring.complete(token, **kwargs)
    assert ring.snapshot()["state"] == "uncertain"
    assert ring.snapshot()["unknown_loss_incidents"] == 1
    assert ring.drain(now_ps=10) == ()
    with pytest.raises(PSPLStateError):
        ring.submit(block(1, start=100), now_ps=11)


def test_boundary_deadline_and_clock_regression_are_explicit():
    ring = running_ring(deadline=10)
    token = ring.submit(block(), now_ps=100)
    ring.complete(token, now_ps=109)
    assert ring.drain(now_ps=109) == (block(),)
    with pytest.raises(AcquisitionError, match="regression"):
        ring.check_deadlines(now_ps=108)


def test_disconnect_reconnect_does_not_restore_configuration_authority():
    ring = running_ring()
    token = ring.submit(block(), now_ps=0)
    ring.disconnect()
    ring.reconnect()
    with pytest.raises(PSPLStateError):
        ring.complete(token, now_ps=1)
    with pytest.raises(PSPLStateError):
        ring.start()
    ring.discard_unverified()
    readback = ring.snapshot()["configuration_readback"]
    with pytest.raises(PSPLStateError):
        ring.verify_configuration(readback, new_session_id="session-1")
    ring.verify_configuration(readback, new_session_id="session-2")
    assert ring.snapshot()["state"] == "ready"


def test_disconnection_before_initial_configuration_cannot_create_ready_state():
    ring = EmulatedDMARing()
    ring.disconnect()
    ring.reconnect()
    with pytest.raises(PSPLStateError):
        ring.verify_configuration(ring.snapshot()["configuration_readback"], new_session_id="session-1")
    assert ring.snapshot()["state"] == "uncertain"


def test_readback_does_not_accept_boolean_as_numeric_abi_version():
    ring = running_ring()
    ring.disconnect()
    ring.reconnect()
    readback = ring.snapshot()["configuration_readback"]
    readback["config"]["abi_major"] = True
    with pytest.raises(PSPLStateError):
        ring.verify_configuration(readback, new_session_id="session-2")
    assert ring.snapshot()["state"] == "uncertain"


def test_stale_token_and_duplicate_lease_release_block_without_duplicate_delivery():
    ring = running_ring()
    token = ring.submit(block(), now_ps=0)
    ring.complete(token, now_ps=1)
    lease = ring.acquire(now_ps=1)
    ring.release(lease)
    with pytest.raises(PSPLStateError, match="ownership"):
        ring.release(lease)
    assert ring.drain(now_ps=2) == ()
    assert ring.snapshot()["delivered_blocks"] == 1


def test_completed_capture_roundtrip_journal_dma_and_correlation(tmp_path):
    source = SyntheticRateSource(events_per_interval=20, intervals=1, interval_ps=100, buffer_capacity=100)
    source.start()
    batch = source.drain()
    journal = CaptureJournal(tmp_path / "capture.sqlite")
    journal.begin("session-1")
    journal.append("session-1", batch)
    journal.finish("session-1")
    original = TimestampBlock.from_capture(batch, session_id="session-1")
    ring = EmulatedDMARing()
    ring.configure(DMAConfig(), session_id=original.session_id, clock_domain=original.clock_domain,
                   configuration_id=original.configuration_id)
    ring.start()
    token = ring.submit(TimestampBlock.from_wire(original.to_wire()), now_ps=0)
    ring.complete(token, now_ps=1)
    drained = ring.drain(now_ps=1)
    assert drained == (original,)
    assert len(drained[0].events) == journal.summary("session-1")["received_events"] == 20
    result = cpu_correlation(drained[0], CorrelationRequest(1, 2, bin_width_ps=1, radius_ps=3))
    assert result["pairs"] > 0
    assert sum(result["counts"]) == result["pairs"]


def test_positive_loss_remains_a_valid_record_but_blocks_acceptance():
    source = SyntheticRateSource(events_per_interval=200, intervals=1, interval_ps=1000, buffer_capacity=30)
    source.start()
    raw = TimestampBlock.from_capture(source.drain(), session_id="session-1")
    assert TimestampBlock.from_wire(raw.to_wire()).lost_events == 170
    ring = EmulatedDMARing()
    ring.configure(DMAConfig(), session_id=raw.session_id, clock_domain=raw.clock_domain,
                   configuration_id=raw.configuration_id)
    ring.start()
    with pytest.raises(PSPLStateError, match="source_data_loss"):
        ring.submit(raw, now_ps=0)
    assert ring.snapshot()["known_lost_events"] == 170
    assert ring.snapshot()["submitted_blocks"] == 0
    with pytest.raises(PSPLStateError):
        cpu_correlation(raw, CorrelationRequest(1, 2))


@pytest.mark.parametrize("change", [{"clock_locked": False}, {"events": (TimestampEvent(1, 1, flags=2),)}])
def test_unsynchronized_or_invalid_payload_blocks_new_work(change):
    ring = running_ring()
    with pytest.raises(PSPLStateError):
        ring.submit(replace(block(), **change), now_ps=0)
    assert ring.snapshot()["state"] == "uncertain"
    assert ring.snapshot()["submitted_blocks"] == 0


def test_capture_saturation_without_exact_loss_count_stays_incomplete():
    source = SyntheticRateSource(events_per_interval=20, intervals=1, interval_ps=100, buffer_capacity=100)
    source.start()
    captured = source.drain()
    degraded = replace(captured, acquisition=replace(captured.acquisition,
        diagnostics={"capture_start_ps": 0, "quality_flags": ["stream_buffer_saturated"]}),
        lost_events=None, produced_events=None, loss_count_exact=False)
    raw = TimestampBlock.from_capture(degraded, session_id="session-1")
    assert TimestampBlock.from_wire(raw.to_wire()).quality_flags == ("stream_buffer_saturated",)
    with pytest.raises(PSPLStateError):
        cpu_correlation(raw, CorrelationRequest(1, 2))


@pytest.mark.parametrize("seed", [1, 19, 101])
def test_histogram_matches_independent_brute_force_with_delay_and_ties(seed):
    rng = random.Random(seed)
    events = sorted([TimestampEvent(rng.randrange(100), rng.choice((1, 2))) for _ in range(60)],
                    key=lambda e: e.timestamp_ps)
    raw = block(events=events)
    request = CorrelationRequest(1, 2, bin_width_ps=2, radius_ps=10, delay_ps=3)
    actual = cpu_correlation(raw, request)
    expected = [0] * 11
    for a in events:
        for b in events:
            delta = b.timestamp_ps - a.timestamp_ps - 3
            if a.channel == 1 and b.channel == 2 and -10 <= delta <= 10:
                expected[(delta + 10) // 2] += 1
    assert actual["counts"] == expected
    assert actual["pairs"] == sum(expected)
    physical = replace(raw, origin="physical", source_id="imported-physical-record")
    assert cpu_correlation(physical, request)["counts"] == expected


def test_exact_radius_and_overload_reject_partial_histogram():
    raw = block(events=[TimestampEvent(0, 2), TimestampEvent(10, 1), TimestampEvent(20, 2)])
    assert cpu_correlation(raw, CorrelationRequest(1, 2, bin_width_ps=5, radius_ps=10))["counts"] == [1, 0, 0, 0, 1]
    with pytest.raises(PSPLStateError, match="budget"):
        cpu_correlation(raw, CorrelationRequest(1, 2, bin_width_ps=5, radius_ps=10, max_pairs=1))


def test_delegated_worker_uses_wire_contract_and_rejects_unequal_result():
    raw, request = block(), CorrelationRequest(1, 2, bin_width_ps=1, radius_ps=5)
    class Worker:
        def correlate(self, wire, parameters):
            return cpu_correlation(TimestampBlock.from_wire(wire), parameters)
    assert execute_correlation(raw, request, worker=Worker()) == cpu_correlation(raw, request)
    class IncorrectWorker:
        def correlate(self, wire, parameters):
            return {"pairs": 100, "counts": [100]}
    with pytest.raises(PSPLStateError, match="differs"):
        execute_correlation(raw, request, worker=IncorrectWorker())
    class BooleanCounterWorker:
        def correlate(self, wire, parameters):
            result = cpu_correlation(TimestampBlock.from_wire(wire), parameters)
            result["pairs"] = True  # Python True == 1, but it is not a valid count.
            return result
    with pytest.raises(PSPLStateError, match="differs"):
        execute_correlation(raw, request, worker=BooleanCounterWorker())


def test_sustained_parallel_consumers_conserve_events_and_bound_ring_resources():
    ring = running_ring(slots=4)
    received = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for offset in range(0, 400, 4):
            for index in range(offset, offset + 4):
                token = ring.submit(block(index, start=index * 100), now_ps=offset)
                ring.complete(token, now_ps=offset)
            leases = list(pool.map(lambda _: ring.acquire(now_ps=offset), range(4)))
            received.extend(lease.block for lease in leases)
            list(pool.map(ring.release, leases))
    assert sorted(b.sequence for b in received) == list(range(400))
    assert sum(len(b.events) for b in received) == 800
    status = ring.snapshot()
    assert status["slots"] == 4 and status["ownership"] == ["free"] * 4
    assert status["delivered_blocks"] == 400 and len(status["recent_fences"]) == 256
    assert status["fence_count"] == 1600 and status["known_lost_events"] == 0


def test_port_commands_only_prepare_or_simulate_and_never_actuate():
    command = PortCommand("session-1", "command-1", "optical-port-1", "phase", {"phase_rad": .2})
    assert command.execute()["status"] == "prepared"
    simulated = command.execute(mode="simulate")
    assert simulated["status"] == "simulated"
    assert simulated["physical_actuation_locked"] and not simulated["physical_action_performed"]
    with pytest.raises(PSPLStateError, match="locked"):
        command.execute(mode="hardware")
    with pytest.raises(AcquisitionError):
        replace(command, parameters={"phase_rad": float("nan")}).execute()
    with pytest.raises(AcquisitionError):
        replace(command, parameters={"x": "x" * (MAX_METADATA_BYTES + 1)}).execute()


@pytest.mark.parametrize("operation,parameters", [("route", {"target_port": "port-2"}),
    ("attenuation", {"attenuation_db": 5.0}), ("trigger", {"period_ps": 1000, "width_ps": 100})])
def test_port_parameter_units_are_explicit(operation, parameters):
    result = PortCommand("session-1", "command-1", "port-1", operation, parameters).execute(mode="simulate")
    assert result["physical_action_performed"] is False
    with pytest.raises(AcquisitionError):
        PortCommand("session-1", "command-1", "port-1", operation, {}).prepare()
