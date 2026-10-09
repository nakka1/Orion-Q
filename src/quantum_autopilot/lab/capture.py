"""Cancelable, periodically drained acquisition with durable idempotent blocks.

Raw source loss is never repaired by generating tags. A process crash preserves only
committed drains; unknown in-flight loss is explicit on recovery.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from .photon import Acquisition, AcquisitionError, PhotonConfig, TimeTag, canonical_json, digest


def _process_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE, read-only.
        if not handle:
            # Access denial is uncertainty, not proof a writer died.
            return ctypes.get_last_error() == 5
        try:
            return api.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@dataclass(frozen=True)
class CaptureBatch:
    source_sequence: int
    acquisition: Acquisition
    produced_events: int | None = None
    lost_events: int | None = None
    loss_count_exact: bool = False

    def validate(self):
        self.acquisition.validate()
        if type(self.source_sequence) is not int or self.source_sequence < 0:
            raise AcquisitionError("source sequence must be nonnegative integer")
        if type(self.loss_count_exact) is not bool:
            raise AcquisitionError("loss accounting flag must be boolean")
        for value in (self.produced_events, self.lost_events):
            if value is not None and (type(value) is not int or value < 0):
                raise AcquisitionError("event accounting requires nonnegative integers")
        if self.loss_count_exact and (self.lost_events is None or self.produced_events != len(self.acquisition.events) + self.lost_events):
            raise AcquisitionError("exact source accounting does not conserve events")


class CaptureJournal:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.owner = str(uuid.uuid4())
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS captures (capture_id TEXT PRIMARY KEY, owner TEXT NOT NULL, pid INTEGER NOT NULL, status TEXT NOT NULL, unknown_inflight_loss INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS capture_blocks (capture_id TEXT NOT NULL, source_sequence INTEGER NOT NULL, payload_json TEXT NOT NULL, sha256 TEXT NOT NULL, previous_sha256 TEXT, PRIMARY KEY(capture_id,source_sequence))")

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def begin(self, capture_id: str):
        if not capture_id:
            raise AcquisitionError("capture_id required")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT owner,status FROM captures WHERE capture_id=?", (capture_id,)).fetchone()
            if current:
                raise AcquisitionError("capture id already exists; use an explicit new acquisition after recovery")
            db.execute("INSERT INTO captures VALUES (?, ?, ?, 'running', 0)", (capture_id, self.owner, os.getpid()))

    def append(self, capture_id: str, batch: CaptureBatch):
        batch.validate()
        serialized = canonical_json(asdict(batch))
        sha = digest(asdict(batch))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            duplicate = db.execute("SELECT sha256 FROM capture_blocks WHERE capture_id=? AND source_sequence=?",
                                   (capture_id, batch.source_sequence)).fetchone()
            if duplicate:
                if duplicate[0] != sha:
                    raise AcquisitionError("duplicate source sequence has different raw events")
                return {"duplicate": True, "sha256": sha}
            owner = db.execute("SELECT owner,status FROM captures WHERE capture_id=?", (capture_id,)).fetchone()
            if owner != (self.owner, "running"):
                raise AcquisitionError("capture ownership/state does not allow appending")
            previous = db.execute("SELECT source_sequence,sha256 FROM capture_blocks WHERE capture_id=? ORDER BY source_sequence DESC LIMIT 1", (capture_id,)).fetchone()
            expected = previous[0] + 1 if previous else 0
            if batch.source_sequence != expected:
                raise AcquisitionError("source sequence gap; record loss in the next contiguous block")
            db.execute("INSERT INTO capture_blocks VALUES (?,?,?,?,?)", (capture_id, batch.source_sequence,
                        serialized, sha, previous[1] if previous else None))
        return {"duplicate": False, "sha256": sha}

    def finish(self, capture_id: str, status="completed"):
        if status not in {"completed", "cancelled", "failed"}:
            raise AcquisitionError("invalid capture terminal status")
        with self._db() as db:
            changed = db.execute("UPDATE captures SET status=?,unknown_inflight_loss=CASE WHEN ?='failed' THEN 1 ELSE unknown_inflight_loss END WHERE capture_id=? AND owner=? AND status='running'",
                                 (status, status, capture_id, self.owner)).rowcount
            if changed != 1:
                raise AcquisitionError("capture ownership does not allow finishing")
        return self.summary(capture_id)

    def recover(self):
        recovered = []
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            for capture_id, pid in db.execute("SELECT capture_id,pid FROM captures WHERE status='running'").fetchall():
                if not _process_alive(pid):
                    db.execute("UPDATE captures SET status='interrupted',unknown_inflight_loss=1 WHERE capture_id=? AND status='running'", (capture_id,))
                    recovered.append(capture_id)
        # Integrity verification is independent from the liveness decision.
        for capture_id in recovered:
            self.summary(capture_id)
        return recovered

    def summary(self, capture_id: str):
        with self._db() as db:
            current = db.execute("SELECT status,unknown_inflight_loss FROM captures WHERE capture_id=?", (capture_id,)).fetchone()
            rows = db.execute("SELECT source_sequence,payload_json,sha256,previous_sha256 FROM capture_blocks WHERE capture_id=? ORDER BY source_sequence", (capture_id,)).fetchall()
        if current is None:
            raise AcquisitionError("capture missing")
        previous = None
        received = produced = lost = 0
        exact = not bool(current[1])
        flags, origins, adapters = set(), set(), set()
        for expected, (sequence, payload, sha, parent) in enumerate(rows):
            record = json.loads(payload)
            if sequence != expected or digest(record) != sha or parent != previous:
                raise AcquisitionError("capture block integrity/sequence invalid")
            previous = sha
            received += len(record["acquisition"]["events"])
            produced += record["produced_events"] or 0
            lost += record["lost_events"] or 0
            exact &= record["loss_count_exact"]
            flags.update(record["acquisition"]["diagnostics"].get("quality_flags", []))
            origins.add(record["acquisition"]["origin"])
            adapters.add(record["acquisition"]["adapter_id"])
        return {"schema": "orion.lab.capture-summary.v1", "capture_id": capture_id, "status": current[0],
                "blocks": len(rows), "received_events": received, "produced_events": produced if exact else None,
                "reported_lost_events": lost, "loss_count_exact": exact,
                "unknown_inflight_loss": bool(current[1]), "last_block_sha256": previous,
                "duplicate_events_written": 0,
                "quality_flags": sorted(flags), "origins": sorted(origins), "adapters": sorted(adapters),
                "complete": current[0] == "completed" and not flags and not bool(current[1]),
                "scope": "committed drains preserved; uncommitted hardware buffers cannot be recovered"}


def run_continuous_capture(source, journal: CaptureJournal, capture_id: str, *, cancel: threading.Event | None = None,
                           drain_interval_s: float = .01, on_block=None, timeout_s: float = 65.):
    """Same start/drain/stop and durable block contract for native and synthetic sources."""
    if isinstance(drain_interval_s, bool) or not isinstance(drain_interval_s, (int, float)) or not 0 < drain_interval_s <= 1:
        raise AcquisitionError("drain interval must be in (0,1] seconds")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise AcquisitionError("capture timeout must be finite positive seconds")
    journal.begin(capture_id)
    status = "completed"
    deadline = time.monotonic() + timeout_s
    try:
        source.start()
        while True:
            if time.monotonic() >= deadline:
                raise AcquisitionError("continuous capture timeout")
            if cancel is not None and cancel.is_set():
                status = "cancelled"
                source.stop()
                break
            batch = source.drain()
            if batch is not None:
                receipt = journal.append(capture_id, batch)
                if on_block:
                    on_block(batch, receipt)
            if source.finished:
                break
            if cancel is not None:
                cancel.wait(drain_interval_s)
            else:
                time.sleep(drain_interval_s)
        # Stop first, then drain the last already captured block. Cancellation
        # never discards a buffer that the source can still return.
        source.stop()
        while (batch := source.drain()) is not None:
            receipt = journal.append(capture_id, batch)
            if on_block:
                on_block(batch, receipt)
    except Exception:
        status = "failed"
        raise
    finally:
        try:
            source.stop()
        finally:
            result = journal.finish(capture_id, status)
    return result


class SyntheticRateSource:
    """Known rate in logical time with a bounded buffer, never a physical tagger."""
    def __init__(self, experiment_id="stream-fixture", *, events_per_interval=20, intervals=10,
                 buffer_capacity=100, interval_ps=10**9, configuration=None):
        if any(type(v) is not int or v <= 0 for v in (events_per_interval, intervals, buffer_capacity, interval_ps)):
            raise AcquisitionError("synthetic source parameters must be positive integers")
        if events_per_interval * 2 >= interval_ps:
            raise AcquisitionError("logical interval too short for generated integer ps events")
        self.experiment_id = experiment_id
        self.events_per_interval, self.intervals, self.capacity = events_per_interval, intervals, buffer_capacity
        self.interval_ps = interval_ps
        self.configuration = configuration or PhotonConfig()
        self.configuration.validate()
        self.sequence = 0
        self.started = False
        self.stopped = False

    @property
    def finished(self):
        return self.stopped or self.sequence >= self.intervals

    def start(self):
        if self.started:
            raise AcquisitionError("synthetic source already started")
        self.started = True

    def stop(self):
        self.stopped = True

    def drain(self):
        if not self.started or self.finished:
            return None
        cfg = self.configuration
        start = self.sequence * self.interval_ps
        received = min(self.events_per_interval, self.capacity)
        tags = [TimeTag(start + 2 * index, cfg.channels[index % 2], self.experiment_id, "synthetic-rate-source",
                        cfg.configuration_id, cfg.clock_domain, "simulated", "orion.known-rate.logical-time.v1")
                for index in range(received)]
        lost = self.events_per_interval - received
        acquisition = Acquisition(self.experiment_id, "synthetic-rate-source", "orion.synthetic-stream.v1", "emulation",
                                  "simulated", asdict(cfg), tags, self.interval_ps, cfg.clock_domain,
                                  "orion.known-rate.logical-time.v1", diagnostics={"capture_start_ps": start,
                                  "quality_flags": ["stream_buffer_saturated"] if lost else [],
                                  "logical_time_acceleration": True, "known_source_events": self.events_per_interval})
        batch = CaptureBatch(self.sequence, acquisition, self.events_per_interval, lost, True)
        self.sequence += 1
        return batch


class NativeStreamingSource:
    """Documented TimeTagStream periodic drain; only constructed by a ready adapter."""
    def __init__(self, adapter, experiment_id: str):
        self.adapter, self.experiment_id = adapter, experiment_id
        self.cfg = adapter.configuration
        self.stream = None
        self.sequence = 0
        self.stopped = False
        self.cursor_ps = None

    def start(self):
        self.adapter._require_acquisition_authority()
        self.adapter.tagger.clearOverflows()
        self.stream = self.adapter.sdk.TimeTagStream(self.adapter.tagger, self.cfg.buffer_capacity, list(self.cfg.channels))
        if not callable(getattr(self.stream, "isRunning", None)):
            self.stream.stop()
            raise AcquisitionError("native streaming requires documented isRunning contract")
        self.stream.startFor(self.cfg.duration_ps)

    @property
    def finished(self):
        return self.stopped or not self.stream.isRunning()

    def stop(self):
        self.stopped = True
        if self.stream is not None:
            self.stream.stop()

    def drain(self):
        import operator
        if self.stream is None:
            return None
        self.adapter._require_acquisition_authority()
        self.adapter._readback(self.cfg)
        buffer = self.stream.getData()
        arrays = (buffer.getTimestamps(), buffer.getChannels(), buffer.getEventTypes(), buffer.getMissedEvents())
        if len({len(a) for a in arrays}) != 1:
            raise AcquisitionError("native streaming arrays have inconsistent lengths")
        if not len(arrays[0]):
            return None
        start = self.cursor_ps if self.cursor_ps is not None else operator.index(buffer.tStart)
        end = operator.index(buffer.tGetData)
        if end <= start:
            raise AcquisitionError("native stream clock failed to advance")
        origin = "simulated" if self.adapter._test_only_transport else "physical"
        mode = "emulation" if self.adapter._test_only_transport else "hardware"
        source_id = f"{'synthetic-sdk-fixture' if self.adapter._test_only_transport else 'native-sdk'}:{self.adapter._identity['serial']}"
        events, flags = [], set()
        for timestamp, channel, kind, missed in zip(*arrays):
            event_type = self.adapter.event_type_map.get(operator.index(kind))
            if event_type is None:
                raise AcquisitionError("unknown native event type")
            timestamp = operator.index(timestamp)
            if not start <= timestamp < end:
                raise AcquisitionError("native stream event outside drained interval")
            events.append(TimeTag(timestamp, operator.index(channel), self.experiment_id,
                                  self.adapter._identity["device_id"], self.cfg.configuration_id,
                                  self.cfg.clock_domain, origin, source_id, event_type, operator.index(missed)))
            if event_type in {"OverflowBegin", "MissedEvents"}:
                flags.add("overflow")
            elif event_type == "Error":
                flags.add("native_error")
        if buffer.hasOverflows:
            flags.add("overflow")
        if buffer.size >= self.cfg.buffer_capacity:
            flags.add("stream_buffer_saturated")
        clock = self.adapter.tagger.getReferenceClockState()
        locked = not bool(clock.enabled) or bool(clock.is_locked)
        if not locked:
            flags.add("clock_unlocked")
        acquisition = Acquisition(self.experiment_id, self.adapter._identity["device_id"], self.adapter.adapter_id,
                                  mode, origin, asdict(self.cfg), events, end - start,
                                  self.cfg.clock_domain, source_id, locked,
                                  {"capture_start_ps": start, "quality_flags": sorted(flags),
                                   "native_configuration": self.adapter._native_configuration,
                                   "hardware_validated": False, "optical_bench_validated": False,
                                   "test_only_transport": self.adapter._test_only_transport})
        batch = CaptureBatch(self.sequence, acquisition, None,
                             sum(e.missed_events for e in events if e.event_type == "MissedEvents"), False)
        self.sequence += 1
        self.cursor_ps = end
        return batch


def run_capture_demo(output_path: Path | str, *, configuration=None):
    """Run observable nominal, overflow and cancellation through the same journal."""
    root = Path(output_path)
    root.mkdir(parents=True, exist_ok=True)
    journal = CaptureJournal(root / "capture.sqlite")
    results = {}
    for name, rate, capacity in (("nominal", 20, 100), ("overflow", 200, 30)):
        source = SyntheticRateSource(f"capture-{name}", events_per_interval=rate, buffer_capacity=capacity,
                                     configuration=configuration)
        results[name] = run_continuous_capture(source, journal, f"capture-{name}", drain_interval_s=.001)
    cancel = threading.Event()
    def on_block(batch, receipt):
        if batch.source_sequence == 2:
            cancel.set()
    results["cancelled"] = run_continuous_capture(SyntheticRateSource("capture-cancelled", intervals=1000,
                                                   configuration=configuration), journal, "capture-cancelled",
                                                   cancel=cancel, on_block=on_block, drain_interval_s=.001)
    result = {"schema": "orion.lab.capture-demo.v1", "mode": "emulation", "origin": "simulated",
              "logical_time_acceleration": True, "hardware_validated": False, "results": results,
              "artifact": str(journal.path.resolve()),
              "passed": results["nominal"]["received_events"] == 200 and results["nominal"]["complete"]
              and results["overflow"]["received_events"] == 300 and results["overflow"]["reported_lost_events"] == 1700
              and not results["overflow"]["complete"] and results["cancelled"]["blocks"] == 3
              and results["cancelled"]["status"] == "cancelled",
              "limits": ["Source advances in explicit logical-time intervals; this is not a hardware throughput benchmark.",
                         "Process-kill recovery is verified by separate subprocess regression tests.",
                         "Native buffers not committed before a crash are reported unknown and never synthesized."]}
    (root / "capture_summary.json").write_text(canonical_json(result), encoding="utf-8")
    return result
