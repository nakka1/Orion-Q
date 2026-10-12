"""Source-independent, bounded per-node acquisition processing.

The current DMA and PL are emulators. A physical acquisition may use the same
wire/processing contract without claiming a physical PL driver is available.
Raw time tags alone do not supply QKD preparation, bases, bits or secure keys.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
import hashlib
from threading import Event, Lock, RLock
import time
from typing import Callable

from .photon import AcquisitionError, digest
from .pspl import (
    ABI_MAJOR, ABI_MINOR, MAX_EVENTS, CorrelationRequest, CorrelationWorker, DMAConfig,
    EmulatedDMARing, PSPLStateError, TimestampBlock, cpu_correlation, execute_correlation,
)


class NodeProcessingError(PSPLStateError):
    """A failed/uncertain block has a qualified diagnostic receipt, not metrics."""
    def __init__(self, cause, receipt):
        super().__init__(cause)
        self.receipt = receipt


@dataclass(frozen=True)
class ProcessingWorkerCapabilities:
    mode: str
    max_events_per_block: int = MAX_EVENTS
    abi_major: int = ABI_MAJOR
    abi_minor: int = ABI_MINOR
    hardware_validated: bool = False

    def validate(self):
        if not isinstance(self.mode, str) or self.mode not in {
                "cpu", "emulated-pl", "cpu-external", "emulated-pl-external", "linux-pl-driver"
                } or self.hardware_validated is not False:
            raise AcquisitionError("worker mode or hardware qualification unsupported")
        DMAConfig(max_events_per_block=self.max_events_per_block,
                  abi_major=self.abi_major, abi_minor=self.abi_minor).validate()


class CPUReferenceWorker:
    def correlate(self, block_wire: bytes, request: CorrelationRequest) -> dict:
        return cpu_correlation(TimestampBlock.from_wire(block_wire), request)


class EmulatedPLCorrelationWorker:
    """Independent sliding-window algorithm, executed in Python, never an FPGA."""
    def correlate(self, block_wire: bytes, request: CorrelationRequest) -> dict:
        block = TimestampBlock.from_wire(block_wire)
        request.validate()
        if block.lost_events or block.quality_flags or not block.clock_locked or any(e.flags for e in block.events):
            raise PSPLStateError("incomplete source cannot enter emulated PL correlation")
        left = [e.timestamp_ps for e in block.events if e.channel == request.channel_a]
        right = [e.timestamp_ps for e in block.events if e.channel == request.channel_b]
        counts = [0] * (2 * request.radius_ps // request.bin_width_ps + 1)
        lower = upper = pairs = 0
        for a in left:
            center = a + request.delay_ps
            while lower < len(right) and right[lower] < center - request.radius_ps:
                lower += 1
            upper = max(upper, lower)
            while upper < len(right) and right[upper] <= center + request.radius_ps:
                upper += 1
            if pairs + upper - lower > request.max_pairs:
                raise PSPLStateError("emulated PL pair budget exceeded")
            for index in range(lower, upper):
                counts[(right[index] - center + request.radius_ps) // request.bin_width_ps] += 1
            pairs += upper - lower
        return {"schema": "orion.lab.pspl-correlation.v1", "session_id": block.session_id,
                "sequence": block.sequence, "block_sha256": hashlib.sha256(block_wire).hexdigest(),
                "request": asdict(request), "counts": counts, "pairs": pairs,
                "bin_left_edges_ps": list(range(-request.radius_ps, request.radius_ps + 1, request.bin_width_ps)),
                "boundary": "inclusive radius; floor bins; last bin includes positive endpoint",
                "clock_domain": block.clock_domain, "cross_block_pairs_included": False}


class ProcessingWorkerRegistry:
    """Trusted service composition; implementation code cannot be selected by a source."""
    def __init__(self):
        self._lock = RLock()
        self._workers = {}
        self.register("cpu-reference", CPUReferenceWorker, ProcessingWorkerCapabilities("cpu"))
        self.register("emulated-pl-v1", EmulatedPLCorrelationWorker, ProcessingWorkerCapabilities("emulated-pl"))

    def register(self, worker_id: str, factory: Callable[[], CorrelationWorker], capabilities: ProcessingWorkerCapabilities):
        if not isinstance(worker_id, str) or not worker_id or len(worker_id) > 128 or not callable(factory):
            raise AcquisitionError("worker registration requires identifier and factory")
        if not isinstance(capabilities, ProcessingWorkerCapabilities):
            raise AcquisitionError("typed worker capabilities required")
        capabilities.validate()
        with self._lock:
            if worker_id in self._workers:
                raise AcquisitionError("worker identity already registered")
            self._workers[worker_id] = factory, capabilities

    def resolve(self, worker_id: str, config: DMAConfig):
        config.validate()
        with self._lock:
            if worker_id not in self._workers:
                raise AcquisitionError("processing worker is unavailable")
            factory, capabilities = self._workers[worker_id]
        if (config.abi_major, config.abi_minor) != (capabilities.abi_major, capabilities.abi_minor) or config.max_events_per_block > capabilities.max_events_per_block:
            raise AcquisitionError("worker/DMA capabilities incompatible")
        worker = factory()
        required = ("correlate",)
        if capabilities.mode.endswith("external") or capabilities.mode == "linux-pl-driver":
            required += ("probe", "snapshot", "cancel", "close")
        if any(not callable(getattr(worker, method, None)) for method in required):
            close = getattr(worker, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            raise AcquisitionError("worker does not implement required processing/lifecycle interfaces")
        return worker, capabilities

    def catalog(self):
        with self._lock:
            return {name: asdict(capabilities) for name, (_factory, capabilities) in self._workers.items()}


@dataclass(frozen=True)
class ProcessingNodeConfig:
    node_id: str
    session_id: str
    clock_domain: str
    configuration_id: str
    correlation: CorrelationRequest
    worker_id: str = "cpu-reference"
    dma: DMAConfig = field(default_factory=DMAConfig)

    def validate(self):
        if not isinstance(self.node_id, str) or not self.node_id or len(self.node_id) > 128 or any(c in self.node_id for c in "\r\n\x00"):
            raise AcquisitionError("processing node identity invalid")
        if not isinstance(self.worker_id, str) or not self.worker_id or len(self.worker_id) > 128:
            raise AcquisitionError("processing worker identity invalid")
        if not isinstance(self.correlation, CorrelationRequest) or not isinstance(self.dma, DMAConfig):
            raise AcquisitionError("typed protocol and DMA configuration required")
        self.correlation.validate()
        self.dma.validate()
        # This empty schema probe checks identifiers only; it is never ingested.
        TimestampBlock(self.session_id, 0, self.clock_domain, self.configuration_id, 0, 1, (),
                       "simulated", "configuration-validation", "configuration-validation").validate()


class NodeProcessingPipeline:
    """One active block/node; a busy producer receives explicit backpressure.

    Cancellation is observable between stages and after a worker returns. It
    does not forcibly interrupt Python/native code or claim physical deadlines.
    A persistence callback acknowledges the raw/prepared record, not a KME key.
    """
    def __init__(self, config: ProcessingNodeConfig, *, registry: ProcessingWorkerRegistry | None = None,
                 on_record: Callable[[dict], object] | None = None):
        config.validate()
        if on_record is not None and not callable(on_record):
            raise AcquisitionError("record sink must be callable")
        self.config = config
        self.registry = registry or ProcessingWorkerRegistry()
        self.worker, self.worker_capabilities = self.registry.resolve(config.worker_id, config.dma)
        self.on_record = on_record
        self.ring = EmulatedDMARing()
        self.ring.configure(config.dma, session_id=config.session_id, clock_domain=config.clock_domain,
                            configuration_id=config.configuration_id)
        self.ring.start()
        self._processing_lock = Lock()
        self._publication_lock = RLock()
        self._cancelled = Event()
        self._blocked = False
        self._active = False
        self._processed = self._failed = self._busy = 0
        self._last_receipt = None

    def _interruption(self):
        with self._publication_lock:
            if self._cancelled.is_set() or self._blocked:
                raise PSPLStateError("processing interrupted; explicit recovery required")

    def _base_receipt(self, block, wire, started):
        receipt = {"schema": "orion.lab.node-processing-receipt.v1", "node_id": self.config.node_id,
                "session_id": self.config.session_id, "sequence": block.sequence if wire is not None else None,
                "configuration_id": self.config.configuration_id,
                "block_wire_sha256": hashlib.sha256(wire).hexdigest() if wire is not None else None,
                "provenance": {"origin": block.origin, "source_id": block.source_id, "adapter_id": block.adapter_id,
                    "clock_domain": block.clock_domain} if wire is not None else {"origin": "unknown"},
                "clock": {"tick_ps": block.clock_tick_ps, "alignment_uncertainty_ps": block.alignment_uncertainty_ps,
                    "locked": block.clock_locked} if wire is not None else None,
                "transport": {"kind": "emulated-axi-dma", "abi_major": ABI_MAJOR, "abi_minor": ABI_MINOR,
                    "hardware_validated": False, "physical_pl_driver_available": False},
                "processing": {"worker_id": self.config.worker_id, "mode": self.worker_capabilities.mode,
                    "reference_comparison_required": True, "hardware_validated": False,
                    "cross_block_pairs_included": False, "hard_realtime_qualified": False},
                "accepted": False, "state": "prepared", "metrics": None, "errors": [],
                "persistence": "not_requested", "wall_elapsed_s": time.monotonic() - started,
                "physical_actuation_locked": True, "physical_action_performed": False}
        snapshot = getattr(self.worker, "snapshot", None)
        if callable(snapshot):
            try:
                state = snapshot()
                if not isinstance(state, dict):
                    raise PSPLStateError("worker state schema invalid")
                receipt["processing"]["accelerator_transport"] = deepcopy(state)
            except Exception as error:
                receipt["processing"]["accelerator_transport"] = {
                    "state": "unavailable", "error_type": type(error).__name__, "hardware_validated": False}
        return receipt

    def process(self, block: TimestampBlock, *, now_ps: int, completion_ps: int | None = None) -> dict:
        if not self._processing_lock.acquire(blocking=False):
            with self._publication_lock:
                self._busy += 1
            raise NodeProcessingError("node_processing_busy", {"schema": "orion.lab.node-processing-receipt.v1",
                "node_id": self.config.node_id, "accepted": False, "state": "backpressure", "retryable": True,
                "retry": "retain and retry the same unaccepted block", "physical_action_performed": False})
        started = time.monotonic()
        wire = lease = None
        record_attempted = False
        stage = "validation"
        with self._publication_lock:
            self._active = True
        try:
            self._interruption()
            if not isinstance(block, TimestampBlock):
                raise AcquisitionError("timestamp block required")
            wire = block.to_wire()
            normalized = TimestampBlock.from_wire(wire)
            stage = "dma"
            token = self.ring.submit(normalized, now_ps=now_ps)
            self.ring.complete(token, now_ps=now_ps if completion_ps is None else completion_ps)
            lease = self.ring.acquire(now_ps=now_ps if completion_ps is None else completion_ps)
            if lease is None or lease.block.sequence != block.sequence:
                raise PSPLStateError("DMA has not delivered the requested block")
            stage = "correlation"
            self._interruption()
            correlation = execute_correlation(lease.block, self.config.correlation, worker=self.worker)
            self._interruption()
            receipt = self._base_receipt(block, wire, started)
            worker_state = receipt["processing"].get("accelerator_transport")
            if (worker_state is not None and worker_state.get("state") == "unavailable"
                    or (self.worker_capabilities.mode.endswith("external") or self.worker_capabilities.mode == "linux-pl-driver")
                    and (worker_state is None or worker_state.get("state") != "ready")):
                raise PSPLStateError("worker state verification unavailable after processing")
            receipt["metrics"] = {"observations": len(block.events), "observation_kind": "detector-time-tags",
                "qkd_preparation_and_bases_available": False,
                "count_by_channel": {str(channel): count for channel, count in Counter(e.channel for e in block.events).items()},
                "coincidence_pairs": correlation["pairs"], "histogram": correlation["counts"],
                "coincidence_interval_ps": [-self.config.correlation.radius_ps, self.config.correlation.radius_ps],
                "coincidence_boundaries": correlation["boundary"],
                "histogram_bin_left_edges_ps": correlation["bin_left_edges_ps"],
                "correlation_request": correlation["request"], "lost_events": block.lost_events,
                "quality_flags": list(block.quality_flags)}
            receipt["processing"]["reference_compared"] = True
            if self.on_record is not None:
                stage = "persistence"
                record_attempted = True
                self.on_record({"schema": "orion.lab.node-processing-record.v1", "block_wire_hex": wire.hex(),
                    "block_wire_sha256": receipt["block_wire_sha256"], "prepared_receipt": deepcopy(receipt)})
                receipt["persistence"] = "callback_acknowledged"
            with self._publication_lock:
                self._interruption()
                self.ring.release(lease)
                lease = None
                receipt["accepted"], receipt["state"] = True, "processed"
                receipt["wall_elapsed_s"] = time.monotonic() - started
                receipt["receipt_sha256"] = digest(receipt)
                self._processed += 1
                self._last_receipt = deepcopy(receipt)
                return receipt
        except Exception as error:
            cause = ("cancelled_during_processing" if self._cancelled.is_set() else
                     "persistence_failed" if stage == "persistence" else
                     "source_block_invalid" if stage == "validation" else "processing_failed")
            with self._publication_lock:
                self._blocked = True
            self.ring.mark_uncertain(cause)
            receipt = self._base_receipt(block, wire, started)
            receipt.update(state="uncertain", errors=[{"stage": stage, "error_type": type(error).__name__, "cause": cause,
                "transport_cause": self.ring.snapshot()["cause"]}])
            if wire is not None and self.on_record is not None and not record_attempted:
                try:
                    self.on_record({"schema": "orion.lab.node-processing-record.v1", "block_wire_hex": wire.hex(),
                        "block_wire_sha256": receipt["block_wire_sha256"], "prepared_receipt": deepcopy(receipt)})
                    receipt["persistence"] = "callback_acknowledged"
                except Exception as persistence_error:
                    receipt["persistence"] = "uncertain"
                    receipt["errors"].append({"stage": "persistence", "error_type": type(persistence_error).__name__})
            elif stage == "persistence":
                receipt["persistence"] = "uncertain"
            receipt["receipt_sha256"] = digest(receipt)
            with self._publication_lock:
                self._blocked = True
                self._failed += 1
                self._last_receipt = deepcopy(receipt)
            raise NodeProcessingError(cause, receipt) from None
        finally:
            try:
                if lease is not None:
                    self.ring.release(lease)
            finally:
                with self._publication_lock:
                    self._active = False
                self._processing_lock.release()

    def cancel(self):
        with self._publication_lock:
            self._cancelled.set()
            self._blocked = True
            self.ring.cancel()
        cancel = getattr(self.worker, "cancel", None)
        if callable(cancel):
            cancel()

    def disconnect(self):
        with self._publication_lock:
            self._blocked = True
            self.ring.disconnect()
        cancel = getattr(self.worker, "cancel", None)
        if callable(cancel):
            cancel()

    def recover(self, readback: dict, *, new_session_id: str, replacement_registry=None):
        if not self._processing_lock.acquire(blocking=False):
            raise PSPLStateError("processing still owns node resources")
        replacement = None
        try:
            with self._publication_lock:
                if not self._blocked:
                    raise PSPLStateError("node recovery requires blocked state")
                if self.worker_capabilities.mode.endswith("external") or self.worker_capabilities.mode == "linux-pl-driver":
                    if not isinstance(replacement_registry, ProcessingWorkerRegistry):
                        raise PSPLStateError("external recovery requires explicit replacement and verified backend probe")
                    replacement = replacement_registry.resolve(self.config.worker_id, self.config.dma)
                    probe = getattr(replacement[0], "probe", None)
                    if not callable(probe):
                        raise PSPLStateError("external replacement does not support verification")
                    probe()
                    old_backend = self.worker.snapshot().get("backend")
                    new_backend = replacement[0].snapshot().get("backend")
                    if (not isinstance(old_backend, dict) or not isinstance(new_backend, dict)
                            or old_backend.get("backend_id") != new_backend.get("backend_id")
                            or new_backend.get("generation", -1) <= old_backend.get("generation", -1)):
                        raise PSPLStateError("external recovery requires a newer verified backend generation")
                    close = getattr(self.worker, "close", None)
                    if callable(close):
                        close()
                if self.ring.snapshot()["state"] != "uncertain":
                    self.ring.mark_uncertain("cancelled_during_processing")
                self.ring.reconnect()
                discarded = self.ring.discard_unverified()
                self.ring.verify_configuration(readback, new_session_id=new_session_id)
                self.config = replace(self.config, session_id=new_session_id)
                self.ring.start()
                if replacement is not None:
                    self.worker, self.worker_capabilities = replacement
                    self.registry = replacement_registry
                self._blocked = False
                self._cancelled.clear()
                return {"schema": "orion.lab.node-processing-recovery.v1", "node_id": self.config.node_id,
                        "session_id": new_session_id, "discard": discarded, "hardware_reset_performed": False,
                        "physical_actuation_locked": True}
        except Exception:
            self.ring.mark_uncertain("processing_failed")
            if replacement is not None and self.worker is not replacement[0]:
                replacement[0].close()
            raise
        finally:
            self._processing_lock.release()

    def snapshot(self):
        with self._publication_lock:
            return {"schema": "orion.lab.node-processing-state.v1", "node_id": self.config.node_id,
                    "session_id": self.config.session_id, "active": self._active, "blocked": self._blocked,
                    "processed_blocks": self._processed, "failed_blocks": self._failed,
                    "backpressure_rejections": self._busy, "worker_id": self.config.worker_id,
                    "worker_capabilities": asdict(self.worker_capabilities), "dma": self.ring.snapshot(),
                    "last_receipt": deepcopy(self._last_receipt), "physical_actuation_locked": True}
