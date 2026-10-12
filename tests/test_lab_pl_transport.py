"""Actual Linux process/socket verification; no FPGA or DMA hardware qualification."""
from contextlib import contextmanager
from dataclasses import asdict, replace
import hashlib
import multiprocessing
import os
from pathlib import Path
import socket
import struct
import sys
from threading import Thread
import time

import pytest

from quantum_autopilot.lab.photon import AcquisitionError
from quantum_autopilot.lab.pl_transport import (
    BackendIdentity, CORRELATE, COUNT, HEADER, MAGIC, MAX_METADATA, PLBackendServer,
    PREFIX, PROBED, RESULT, UnixPLCorrelationWorker, decode_frame, encode_frame,
    probe_linux_character_device, receive_frame, send_frame,
)
from quantum_autopilot.lab.processing_node import CPUReferenceWorker, EmulatedPLCorrelationWorker
from quantum_autopilot.lab.pspl import (
    CorrelationRequest, PSPLStateError, TimestampBlock, TimestampEvent, cpu_correlation, execute_correlation,
)

LINUX = sys.platform.startswith("linux") and hasattr(socket, "SO_PEERCRED")
native = pytest.mark.skipif(not LINUX, reason="real Linux kernel credentials/permissions required")


def identity(mode="cpu-external", generation=1):
    return BackendIdentity("test-correlation-worker", mode, generation,
                           hashlib.sha256(b"identified-test-implementation-no-bitstream").hexdigest())


def block(sequence=0, start=0, **kwargs):
    return TimestampBlock("transport-session", sequence, "test-clock", "test-configuration", start, 100,
        (TimestampEvent(start + 1, 1), TimestampEvent(start + 4, 2), TimestampEvent(start + 4, 2)),
        "simulated", "fixture-only", "fixture-only", **kwargs)


REQUEST = CorrelationRequest(1, 2, bin_width_ps=1, radius_ps=5)


class SlowWorker:
    def __init__(self, entered):
        self.entered = entered

    def correlate(self, wire, request):
        self.entered.set()
        time.sleep(.6)
        return CPUReferenceWorker().correlate(wire, request)


def _backend(path, descriptor, uid, stop, entered, mode, state_directory):
    if mode == "slow":
        worker = SlowWorker(entered)
    elif mode == "pl":
        worker = EmulatedPLCorrelationWorker()
    else:
        worker = CPUReferenceWorker()
    PLBackendServer(path, descriptor, worker, allowed_uid=uid, timeout_s=1,
                    state_directory=state_directory).serve(stop)


def _broken_backend(path, uid, stop, fault):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    os.chmod(path, 0o600)
    listener.listen(1)
    listener.settimeout(3)
    try:
        with listener.accept()[0] as connection:
            kind, sequence, meta, payload = receive_frame(connection, time.monotonic() + 3)
            if fault == "probe-slow":
                time.sleep(.12)
            send_frame(connection, encode_frame(PROBED, sequence, meta), time.monotonic() + 3)
            kind, sequence, meta, payload = receive_frame(connection, time.monotonic() + 3)
            if fault == "probe-slow":
                time.sleep(.12)
            bins = 2 * meta["correlation"]["radius_ps"] // meta["correlation"]["bin_width_ps"] + 1
            reply = {**meta, "pairs": 2, "bin_count": bins}
            histogram = [0] * bins
            histogram[8] = 2
            if fault == "binding":
                reply["configuration_id"] = "another-configuration"
            elif fault == "generation":
                reply["backend"] = {**reply["backend"], "generation": reply["backend"]["generation"] + 1}
            elif fault == "typed-sequence":
                reply["block_sequence"] = float(reply["block_sequence"])
            data = encode_frame(RESULT, sequence + (fault == "replay"), reply,
                                b"".join(COUNT.pack(n) for n in histogram))
            if fault == "partial":
                data = data[:-1]
            connection.sendall(data)
    finally:
        listener.close()
        while not stop.is_set():
            time.sleep(.01)


@contextmanager
def running(tmp_path, *, mode="cpu", allowed_uid=None, fault=None, generation=1, state_directory=None):
    tmp_path.chmod(0o700)
    path = str(tmp_path / "worker.sock")
    context = multiprocessing.get_context("spawn")
    stop, entered = context.Event(), context.Event()
    descriptor = identity("emulated-pl-external" if mode == "pl" else "cpu-external", generation=generation)
    uid = os.geteuid() if allowed_uid is None else allowed_uid
    target = _backend if fault is None else _broken_backend
    args = (path, descriptor, uid, stop, entered, mode, state_directory) if fault is None else (path, uid, stop, fault)
    process = context.Process(target=target, args=args)
    process.start()
    try:
        end = time.monotonic() + 5
        while not Path(path).is_socket() and time.monotonic() < end and process.is_alive():
            time.sleep(.01)
        assert Path(path).is_socket(), "external Linux process did not bind"
        yield path, descriptor, process, entered
    finally:
        stop.set()
        process.join(3)
        if process.is_alive():
            process.terminate()
            process.join(2)
        assert not process.is_alive()


def test_outer_abi_preserves_integer_units_and_inner_abi_without_reinterpretation():
    wire = block(start=2**53 + 123).to_wire()
    encoded = encode_frame(CORRELATE, 2**53 + 1, {"integer": 2**53 + 7}, wire)
    assert HEADER.size == 64 and PREFIX.size == 32 and encoded[:8] == b"OQPT\x01\x00\x00\x00"
    kind, sequence, metadata, payload = decode_frame(encoded)
    assert kind == CORRELATE and sequence == 2**53 + 1 and metadata["integer"] == 2**53 + 7
    assert payload == wire and TimestampBlock.from_wire(payload).start_ps == 2**53 + 123


@pytest.mark.parametrize("mutation", ["magic", "major", "flags", "metadata-limit", "checksum", "partial"])
def test_codec_rejects_invalid_frames(mutation):
    wire = bytearray(encode_frame(CORRELATE, 0, {"request": "fixture"}))
    if mutation == "magic":
        wire[0] ^= 1
    elif mutation == "major":
        wire[4] = 2
    elif mutation == "flags":
        wire[12] = 1
    elif mutation == "metadata-limit":
        struct.pack_into("<I", wire, 16, MAX_METADATA + 1)
    elif mutation == "checksum":
        wire[-1] ^= 1
    else:
        wire.pop()
    with pytest.raises(AcquisitionError):
        decode_frame(bytes(wire))


def test_codec_rejects_duplicate_keys_and_noncanonical_metadata():
    for metadata in (b'{"a":1,"a":2}', b'{"a": 1}'):
        prefix = PREFIX.pack(MAGIC, 1, 0, CORRELATE, 0, len(metadata), 0, 0)
        wire = prefix + hashlib.sha256(prefix + metadata).digest() + metadata
        with pytest.raises(AcquisitionError):
            decode_frame(wire)


@pytest.mark.parametrize("field,value", [("mode", []), ("generation", True), ("image_sha256", "a"),
                                       ("max_events", 0), ("backend_id", "unsafe\nbackend")])
def test_invalid_backend_identity_fails_before_transport(field, value):
    with pytest.raises(AcquisitionError):
        replace(identity(), **{field: value}).validate()


@native
@pytest.mark.parametrize("mode", ["cpu", "pl"])
def test_real_external_process_matches_reference_and_preserves_source(tmp_path, mode):
    with running(tmp_path, mode=mode) as (path, descriptor, process, _entered):
        worker = UnixPLCorrelationWorker(path, descriptor, peer_uid=os.geteuid(), timeout_s=2)
        try:
            assert worker.probe()["peer"]["pid"] == process.pid
            assert worker.snapshot()["peer"]["uid"] == os.geteuid()
            for n in range(5):
                value = block(n, start=n * 100)
                assert execute_correlation(value, REQUEST, worker=worker) == cpu_correlation(value, REQUEST)
            state = worker.snapshot()
            assert state["completed"] == 5 and state["automatic_retries"] == 0
            assert state["backend"] == asdict(descriptor)
            assert state["hardware_validated"] is False and state["physical_actuation_locked"]
            assert state["last_exchange"]["block_sequence"] == 4
        finally:
            worker.close()


@native
def test_server_rejects_wrong_kernel_uid_before_processing(tmp_path):
    with running(tmp_path, allowed_uid=os.geteuid() + 1) as (path, descriptor, _process, _):
        worker = UnixPLCorrelationWorker(path, descriptor, peer_uid=os.geteuid(), timeout_s=.3)
        with pytest.raises(PSPLStateError):
            worker.probe()
        assert worker.snapshot()["state"] == "uncertain" and worker.snapshot()["completed"] == 0


@native
@pytest.mark.parametrize("fault", ["binding", "generation", "typed-sequence", "replay", "partial"])
def test_reply_tamper_replay_and_partial_never_publish_or_retry(tmp_path, fault):
    with running(tmp_path, fault=fault) as (path, descriptor, _process, _):
        worker = UnixPLCorrelationWorker(path, descriptor, peer_uid=os.geteuid(), timeout_s=.4)
        with pytest.raises(PSPLStateError, match="uncertain"):
            worker.correlate(block().to_wire(), REQUEST)
        state = worker.snapshot()
        assert state["state"] == "uncertain" and state["completed"] == 0 and state["automatic_retries"] == 0
        with pytest.raises(PSPLStateError):
            worker.correlate(block().to_wire(), REQUEST)


@native
def test_deadline_does_not_accept_late_result_or_retry(tmp_path):
    with running(tmp_path, mode="slow") as (path, descriptor, _process, entered):
        worker = UnixPLCorrelationWorker(path, descriptor, peer_uid=os.geteuid(), timeout_s=.15)
        worker.probe()
        started = time.monotonic()
        with pytest.raises(PSPLStateError):
            worker.correlate(block().to_wire(), REQUEST)
        assert entered.is_set() and time.monotonic() - started < .5
        assert worker.snapshot()["state"] == "uncertain" and worker.snapshot()["completed"] == 0


@native
def test_cancel_interrupts_socket_wait_and_rejects_late_output(tmp_path):
    with running(tmp_path, mode="slow") as (path, descriptor, _process, entered):
        worker = UnixPLCorrelationWorker(path, descriptor, peer_uid=os.geteuid(), timeout_s=2)
        worker.probe()
        errors = []
        def call():
            try:
                worker.correlate(block().to_wire(), REQUEST)
            except PSPLStateError:
                errors.append("uncertain")
        thread = Thread(target=call)
        thread.start()
        assert entered.wait(2)
        worker.cancel()
        thread.join(.5)
        assert not thread.is_alive() and errors == ["uncertain"]
        assert worker.snapshot()["completed"] == 0 and worker.snapshot()["state"] == "uncertain"


@native
def test_busy_caller_is_not_queued_or_sent_to_backend(tmp_path):
    with running(tmp_path, mode="slow") as (path, descriptor, _process, entered):
        worker = UnixPLCorrelationWorker(path, descriptor, peer_uid=os.geteuid(), timeout_s=2)
        worker.probe()
        results = []
        thread = Thread(target=lambda: results.append(worker.correlate(block().to_wire(), REQUEST)))
        thread.start()
        assert entered.wait(2)
        with pytest.raises(PSPLStateError, match="busy"):
            worker.correlate(block(1, 100).to_wire(), REQUEST)
        thread.join(2)
        assert not thread.is_alive() and len(results) == 1 and worker.snapshot()["completed"] == 1
        assert worker.snapshot()["state"] == "ready"
        worker.close()


@native
def test_first_job_deadline_includes_connection_probe_and_correlation(tmp_path):
    with running(tmp_path, fault="probe-slow") as (path, descriptor, _process, _):
        worker = UnixPLCorrelationWorker(path, descriptor, peer_uid=os.geteuid(), timeout_s=.18)
        started = time.monotonic()
        with pytest.raises(PSPLStateError):
            worker.correlate(block().to_wire(), REQUEST)
        assert time.monotonic() - started < .4
        assert worker.snapshot()["state"] == "uncertain" and worker.snapshot()["completed"] == 0


@native
def test_replayed_block_or_configuration_change_rejected_across_connections(tmp_path):
    with running(tmp_path) as (path, descriptor, _process, _):
        original = UnixPLCorrelationWorker(path, descriptor, peer_uid=os.geteuid())
        original.correlate(block().to_wire(), REQUEST)
        original.close()
        for value in (block(), replace(block(1, 100), configuration_id="changed")):
            fresh = UnixPLCorrelationWorker(path, descriptor, peer_uid=os.geteuid())
            with pytest.raises(PSPLStateError):
                fresh.correlate(value.to_wire(), REQUEST)
            assert fresh.snapshot()["state"] == "uncertain"


@native
@pytest.mark.parametrize("kind", ["parent", "socket", "symlink"])
def test_socket_filesystem_permissions_are_verified(tmp_path, kind):
    with running(tmp_path) as (path, descriptor, _process, _):
        actual = path
        if kind == "parent":
            tmp_path.chmod(0o755)
        elif kind == "socket":
            os.chmod(path, 0o666)
        else:
            actual = str(tmp_path / "alias.sock")
            os.symlink(path, actual)
        worker = UnixPLCorrelationWorker(actual, descriptor, peer_uid=os.geteuid(), timeout_s=.3)
        with pytest.raises(PSPLStateError):
            worker.probe()
        assert worker.snapshot()["completed"] == 0


@native
def test_missing_driver_and_regular_file_are_not_hardware(tmp_path):
    assert probe_linux_character_device(tmp_path / "missing") == {
        "available": False, "state": "device_missing", "hardware_validated": False}
    (tmp_path / "regular").write_bytes(b"not a kernel driver")
    with pytest.raises(PSPLStateError):
        probe_linux_character_device(tmp_path / "regular")
    with pytest.raises(PSPLStateError):
        PLBackendServer(tmp_path / "driver.sock", identity("linux-pl-driver"),
                        CPUReferenceWorker(), allowed_uid=os.geteuid())


def _attempt_backend(path, descriptor, result, state_directory):
    try:
        PLBackendServer(path, descriptor, CPUReferenceWorker(), allowed_uid=os.geteuid(),
                        state_directory=state_directory).serve(
            multiprocessing.get_context("spawn").Event())
    except PSPLStateError:
        result.put("generation_or_exclusivity_rejected")


def refused_start(path, descriptor, state_directory=None):
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_attempt_backend, args=(str(path), descriptor, queue, state_directory))
    process.start()
    try:
        assert queue.get(timeout=5) == "generation_or_exclusivity_rejected"
        process.join(2)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        queue.close()
        queue.join_thread()


@native
def test_exclusive_producer_and_restart_generation_are_durable(tmp_path):
    with running(tmp_path) as (path, descriptor, _process, _):
        worker = UnixPLCorrelationWorker(path, descriptor, peer_uid=os.geteuid())
        assert worker.correlate(block().to_wire(), REQUEST)["pairs"] == 2
        worker.close()
        refused_start(tmp_path / "second.sock", replace(descriptor, generation=2))
    # Lock release is not authority to erase the persisted consumed generation.
    refused_start(tmp_path / "restarted.sock", descriptor)
    with running(tmp_path, generation=2) as (path, descriptor, _process, _):
        old = UnixPLCorrelationWorker(path, descriptor, peer_uid=os.geteuid())
        with pytest.raises(PSPLStateError):
            old.correlate(block().to_wire(), REQUEST)
        fresh = UnixPLCorrelationWorker(path, descriptor, peer_uid=os.geteuid())
        try:
            value = replace(block(), session_id="explicit-new-ps-session")
            assert fresh.correlate(value.to_wire(), REQUEST)["pairs"] == 2
        finally:
            fresh.close()
    ledgers = sorted(tmp_path.glob(".orion-pl-*"))
    assert len(ledgers) == 2 and all(p.stat().st_mode & 0o077 == 0 for p in ledgers)
    generations = next(p for p in ledgers if p.suffix == ".generations")
    assert [item[0] for item in COUNT.iter_unpack(generations.read_bytes())] == [1, 2]


@native
def test_runtime_socket_directory_recreation_cannot_erase_durable_generation(tmp_path):
    runtime, persistent = tmp_path / "run", tmp_path / "persistent"
    runtime.mkdir(mode=0o700)
    persistent.mkdir(mode=0o700)
    with running(runtime, state_directory=persistent) as (path, descriptor, _process, _):
        worker = UnixPLCorrelationWorker(path, descriptor, peer_uid=os.geteuid())
        worker.correlate(block().to_wire(), REQUEST)
        worker.close()
    frozen = {p.name: p.read_bytes() for p in persistent.iterdir()}
    assert not list(runtime.iterdir())
    runtime.rmdir()  # Empty test directory only; no ledger deletion.
    runtime.mkdir(mode=0o700)
    refused_start(runtime / "worker.sock", descriptor, state_directory=persistent)
    assert frozen == {p.name: p.read_bytes() for p in persistent.iterdir()}
    with running(runtime, generation=2, state_directory=persistent) as (path, descriptor, _process, _):
        worker = UnixPLCorrelationWorker(path, descriptor, peer_uid=os.geteuid())
        try:
            fresh = replace(block(), session_id="session-after-runtime-directory-recreation")
            assert worker.correlate(fresh.to_wire(), REQUEST)["pairs"] == 2
        finally:
            worker.close()
