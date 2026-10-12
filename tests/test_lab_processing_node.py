"""Same node pipeline for emulated acquisition and native SDK callback fixtures.

Fixtures here never use an installed SDK, physical instrument, or real FPGA.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from threading import Event
from types import SimpleNamespace

import pytest

from quantum_autopilot.lab.adapters import EmulatedPhotonAdapter, SwabianSDKAdapter
from quantum_autopilot.lab.capture import CaptureBatch, NativeStreamingSource
from quantum_autopilot.lab.photon import AcquisitionError, PhotonConfig
from quantum_autopilot.lab.processing_node import (
    CPUReferenceWorker, EmulatedPLCorrelationWorker, NodeProcessingError, NodeProcessingPipeline,
    ProcessingNodeConfig, ProcessingWorkerCapabilities, ProcessingWorkerRegistry,
)
from quantum_autopilot.lab.pspl import CorrelationRequest, DMAConfig, PSPLStateError, TimestampBlock, TimestampEvent


def raw_block(sequence=0, *, session="session-1", start=0, origin="simulated", **kwargs):
    return TimestampBlock(session, sequence, "clock-1", "config-1", start, 100,
        (TimestampEvent(start + 1, 1), TimestampEvent(start + 4, 2)), origin,
        "test-recording", "test-adapter", **kwargs)


def test_rejected_external_factory_cleans_up_its_resource():
    closed = []
    registry = ProcessingWorkerRegistry()
    worker = SimpleNamespace(correlate=CPUReferenceWorker().correlate, probe=lambda: {},
                             snapshot=lambda: {}, close=lambda: closed.append(True))
    registry.register("incomplete-external", lambda: worker, ProcessingWorkerCapabilities("cpu-external"))
    with pytest.raises(AcquisitionError):
        registry.resolve("incomplete-external", DMAConfig())
    assert closed == [True]


def test_failed_external_retirement_preserves_old_session_and_uncertain_ring():
    class Worker:
        def __init__(self, generation, fail_close=False):
            self.generation, self.fail_close, self.closed = generation, fail_close, 0
        correlate = CPUReferenceWorker().correlate
        def probe(self):
            return self.snapshot()
        def snapshot(self):
            return {"backend": {"backend_id": "fixture", "generation": self.generation}}
        def cancel(self):
            pass
        def close(self):
            self.closed += 1
            if self.fail_close:
                raise RuntimeError("fixture-retirement-failed")
    old, new = Worker(1, True), Worker(2)
    first, second = ProcessingWorkerRegistry(), ProcessingWorkerRegistry()
    for registry, worker in ((first, old), (second, new)):
        registry.register("external-fixture", lambda worker=worker: worker, ProcessingWorkerCapabilities("cpu-external"))
    pipeline = NodeProcessingPipeline(node_config(worker="external-fixture"), registry=first)
    pipeline.cancel()
    with pytest.raises(RuntimeError):
        pipeline.recover(pipeline.snapshot()["dma"]["configuration_readback"], new_session_id="session-2",
                         replacement_registry=second)
    state = pipeline.snapshot()
    assert state["blocked"] and state["session_id"] == "session-1" and state["dma"]["state"] == "uncertain"
    assert pipeline.worker is old and new.closed == 1


def test_failed_worker_snapshot_returns_sanitized_receipt_and_blocks_before_persistence():
    registry = ProcessingWorkerRegistry()
    class Worker:
        correlate = CPUReferenceWorker().correlate
        def probe(self):
            return {}
        def snapshot(self):
            raise RuntimeError("private-sdk-token-must-not-enter-receipts")
        def cancel(self):
            pass
        def close(self):
            pass
    registry.register("broken-snapshot", Worker, ProcessingWorkerCapabilities("cpu-external"))
    records = []
    def sink(record):
        assert pipeline.snapshot()["blocked"]
        records.append(record)
    pipeline = NodeProcessingPipeline(node_config(worker="broken-snapshot"), registry=registry, on_record=sink)
    with pytest.raises(NodeProcessingError) as failure:
        pipeline.process(raw_block(), now_ps=0)
    assert not failure.value.receipt["accepted"] and failure.value.receipt["metrics"] is None
    assert "private-sdk-token" not in json.dumps(failure.value.receipt)
    state = pipeline.snapshot()
    assert state["blocked"] and state["dma"]["state"] == "uncertain" and state["last_receipt"]
    assert records[0]["prepared_receipt"]["processing"]["accelerator_transport"]["state"] == "unavailable"


def node_config(raw=None, *, worker="cpu-reference", dma=None):
    raw = raw or raw_block()
    return ProcessingNodeConfig("N01", raw.session_id, raw.clock_domain, raw.configuration_id,
        CorrelationRequest(1, 2, bin_width_ps=1, radius_ps=5), worker_id=worker, dma=dma or DMAConfig())


def native_callback_capture(acquisition):
    """Actual native-normalization code with explicit test-only SDK callbacks."""
    cfg = PhotonConfig(**acquisition.configuration)
    adapter = SwabianSDKAdapter(event_type_map={7: "TimeTag"}, test_only_transport=True)
    adapter._clock_domain = cfg.clock_domain
    adapter._identity = {"serial": "callback-fixture", "device_id": "synthetic-sdk-fixture", "hardware_connected": False}
    levels, delays = {}, {}
    adapter.tagger = SimpleNamespace(
        getChannelList=lambda: list(cfg.channels), getTriggerLevelRange=lambda _: (-2.5, 2.5),
        setTriggerLevel=lambda c, v: levels.__setitem__(c, v), getTriggerLevel=lambda c: levels[c],
        setInputDelay=lambda c, v: delays.__setitem__(c, v), getInputDelay=lambda c: delays[c],
        sync=lambda **_: True, getConfiguration=lambda: {"fixture": True},
        getReferenceClockState=lambda: SimpleNamespace(enabled=False), disableReferenceClock=lambda: None,
        autoCalibration=lambda: [10., 10.], clearOverflows=lambda: None,
    )
    adapter.state = "connected"
    adapter.configure(cfg)
    adapter.calibrate()
    buffer = SimpleNamespace(getTimestamps=lambda: [e.timestamp_ps for e in acquisition.events],
        getChannels=lambda: [e.channel for e in acquisition.events],
        getEventTypes=lambda: [7] * len(acquisition.events), getMissedEvents=lambda: [0] * len(acquisition.events),
        hasOverflows=False, size=len(acquisition.events), tStart=0, tGetData=cfg.duration_ps)
    stream = SimpleNamespace(startFor=lambda _: None, getData=lambda: buffer,
                             isRunning=lambda: True, stop=lambda: None)
    adapter.sdk = SimpleNamespace(TimeTagStream=lambda *_: stream)
    source = NativeStreamingSource(adapter, acquisition.experiment_id)
    source.start()
    try:
        return source.drain()
    finally:
        source.stop()


def test_adapter_and_native_callback_follow_same_pipeline_with_distinct_workers(tmp_path):
    cfg = replace(PhotonConfig(), duration_ps=10**10, seed=123)
    adapter = EmulatedPhotonAdapter()
    adapter.connect()
    adapter.configure(cfg)
    adapter.calibrate()
    acquisition = adapter.acquire("processing-fixture")
    emulated = TimestampBlock.from_capture(CaptureBatch(0, acquisition), session_id="session-1")
    native = TimestampBlock.from_capture(native_callback_capture(acquisition), session_id="session-1")
    records = []
    def persist(record):
        records.append(record)
        (tmp_path / "prepared-record.json").write_text(json.dumps(record), encoding="utf-8")
    a = NodeProcessingPipeline(node_config(emulated), on_record=persist).process(emulated, now_ps=0)
    b = NodeProcessingPipeline(node_config(native, worker="emulated-pl-v1")).process(native, now_ps=0)
    assert a["metrics"] == b["metrics"]
    assert a["metrics"]["observations"] == len(acquisition.events) > 0
    assert a["processing"]["mode"] == "cpu" and b["processing"]["mode"] == "emulated-pl"
    assert a["provenance"]["adapter_id"] != b["provenance"]["adapter_id"]
    assert a["block_wire_sha256"] != b["block_wire_sha256"]
    assert a["persistence"] == "callback_acknowledged"
    saved = json.loads((tmp_path / "prepared-record.json").read_text())
    wire = bytes.fromhex(saved["block_wire_hex"])
    assert hashlib.sha256(wire).hexdigest() == a["block_wire_sha256"]
    assert TimestampBlock.from_wire(wire) == emulated
    assert saved["prepared_receipt"]["accepted"] is False
    assert a["transport"]["hardware_validated"] is False
    assert b["transport"]["physical_pl_driver_available"] is False


def test_data_origin_does_not_select_worker_or_change_metrics():
    simulated = raw_block()
    physical_shape_fixture = replace(simulated, origin="physical", source_id="physical-contract-fixture")
    a = NodeProcessingPipeline(node_config(worker="emulated-pl-v1")).process(simulated, now_ps=0)
    b = NodeProcessingPipeline(node_config(worker="emulated-pl-v1")).process(physical_shape_fixture, now_ps=0)
    assert a["metrics"] == b["metrics"]
    assert a["processing"] == b["processing"]
    assert b["provenance"]["origin"] == "physical"
    assert not b["transport"]["hardware_validated"] and not b["physical_action_performed"]


def test_protocol_metrics_are_computed_from_received_events_and_seeded_worker_equivalence():
    import random
    request = CorrelationRequest(1, 2, bin_width_ps=2, radius_ps=10, delay_ps=-3)
    for seed in (5, 81, 991):
        rng = random.Random(seed)
        events = sorted((TimestampEvent(rng.randrange(100), rng.choice((1, 2))) for _ in range(60)),
                        key=lambda e: e.timestamp_ps)
        raw = replace(raw_block(), events=tuple(events))
        config = replace(node_config(raw), correlation=request)
        cpu = NodeProcessingPipeline(config).process(raw, now_ps=0)
        pl = NodeProcessingPipeline(replace(config, worker_id="emulated-pl-v1")).process(raw, now_ps=0)
        expected = sum(-10 <= b.timestamp_ps - a.timestamp_ps + 3 <= 10
                       for a in events for b in events if a.channel == 1 and b.channel == 2)
        assert cpu["metrics"] == pl["metrics"]
        assert cpu["metrics"]["coincidence_pairs"] == expected
        assert sum(cpu["metrics"]["histogram"]) == expected


@pytest.mark.parametrize("changes", [{"lost_events": 3}, {"clock_locked": False},
    {"quality_flags": ("stream_buffer_saturated",)}, {"sequence": 2}, {"clock_domain": "wrong-clock"}])
def test_bad_blocks_get_diagnostic_record_no_success_and_block_following_acquisition(changes):
    records = []
    pipeline = NodeProcessingPipeline(node_config(), on_record=records.append)
    with pytest.raises(NodeProcessingError) as failure:
        pipeline.process(replace(raw_block(), **changes), now_ps=0)
    receipt = failure.value.receipt
    assert receipt["accepted"] is False and receipt["metrics"] is None
    assert records[0]["prepared_receipt"]["accepted"] is False
    assert pipeline.snapshot()["blocked"] and pipeline.snapshot()["dma"]["state"] == "uncertain"
    with pytest.raises(NodeProcessingError):
        pipeline.process(raw_block(), now_ps=1)
    assert pipeline.snapshot()["processed_blocks"] == 0


def test_transfer_deadline_blocks_processing_and_preserves_original_transport_cause():
    pipeline = NodeProcessingPipeline(node_config(dma=DMAConfig(descriptor_deadline_ps=10)))
    with pytest.raises(NodeProcessingError) as failure:
        pipeline.process(raw_block(), now_ps=0, completion_ps=10)
    assert failure.value.receipt["errors"][0]["transport_cause"] == "descriptor_deadline"
    assert pipeline.snapshot()["dma"]["unknown_loss_incidents"] == 1
    assert pipeline.snapshot()["dma"]["ownership"][0] == "pl"


def test_sink_failure_cannot_return_success_or_reuse_old_session_and_hides_exception_text():
    def failing_sink(_record):
        raise RuntimeError("fixture-private-token-must-not-enter-receipts")
    pipeline = NodeProcessingPipeline(node_config(), on_record=failing_sink)
    with pytest.raises(NodeProcessingError) as failure:
        pipeline.process(raw_block(), now_ps=0)
    assert "fixture-private-token" not in str(failure.value)
    assert "fixture-private-token" not in json.dumps(failure.value.receipt)
    assert failure.value.receipt["persistence"] == "uncertain"
    assert pipeline.snapshot()["dma"]["ownership"] == ["free"] * 8
    readback = pipeline.snapshot()["dma"]["configuration_readback"]
    with pytest.raises(PSPLStateError):
        pipeline.recover(readback, new_session_id="session-1")
    pipeline.on_record = None
    recovered = pipeline.recover(readback, new_session_id="session-2")
    assert not recovered["hardware_reset_performed"]
    assert pipeline.process(raw_block(session="session-2"), now_ps=1)["accepted"]


def blocked_worker_registry(entered, release):
    registry = ProcessingWorkerRegistry()
    class Worker:
        def correlate(self, wire, request):
            entered.set()
            assert release.wait(5)
            return CPUReferenceWorker().correlate(wire, request)
    registry.register("blocking-fixture", Worker, ProcessingWorkerCapabilities("cpu"))
    return registry


def test_concurrent_producer_receives_backpressure_and_can_retry_same_block():
    entered, release = Event(), Event()
    pipeline = NodeProcessingPipeline(node_config(worker="blocking-fixture"),
                                     registry=blocked_worker_registry(entered, release))
    with ThreadPoolExecutor(max_workers=1) as pool:
        active = pool.submit(pipeline.process, raw_block(), now_ps=0)
        assert entered.wait(5)
        try:
            with pytest.raises(NodeProcessingError) as rejection:
                pipeline.process(raw_block(1, start=100), now_ps=0)
            assert rejection.value.receipt["retryable"]
            assert not pipeline.snapshot()["blocked"]
            with pytest.raises(PSPLStateError, match="still owns"):
                pipeline.recover({}, new_session_id="new-session")
        finally:
            release.set()
        assert active.result(timeout=5)["accepted"]
    assert pipeline.process(raw_block(1, start=100), now_ps=1)["accepted"]
    assert pipeline.snapshot()["processed_blocks"] == 2
    assert pipeline.snapshot()["backpressure_rejections"] == 1


@pytest.mark.parametrize("interruption", ["cancel", "disconnect"])
def test_interruption_during_worker_never_publishes_final_metrics(interruption):
    entered, release = Event(), Event()
    records = []
    pipeline = NodeProcessingPipeline(node_config(worker="blocking-fixture"),
                                     registry=blocked_worker_registry(entered, release), on_record=records.append)
    with ThreadPoolExecutor(max_workers=1) as pool:
        active = pool.submit(pipeline.process, raw_block(), now_ps=0)
        assert entered.wait(5)
        getattr(pipeline, interruption)()
        release.set()
        with pytest.raises(NodeProcessingError):
            active.result(timeout=5)
    assert pipeline.snapshot()["processed_blocks"] == 0
    assert pipeline.snapshot()["blocked"]
    assert records[0]["prepared_receipt"]["metrics"] is None
    assert pipeline.snapshot()["dma"]["ownership"] == ["free"] * 8


def test_registry_validates_capabilities_before_instantiating_worker():
    registry = ProcessingWorkerRegistry()
    called = []
    def factory():
        called.append(True)
        return CPUReferenceWorker()
    registry.register("small-worker", factory, ProcessingWorkerCapabilities("cpu", max_events_per_block=2))
    with pytest.raises(AcquisitionError, match="incompatible"):
        NodeProcessingPipeline(node_config(worker="small-worker"), registry=registry)
    assert called == []
    pipeline = NodeProcessingPipeline(node_config(worker="small-worker", dma=DMAConfig(max_events_per_block=2)), registry=registry)
    assert pipeline.process(raw_block(), now_ps=0)["accepted"]
    with pytest.raises(AcquisitionError):
        registry.register("real-pl-not-yet-qualified", factory, ProcessingWorkerCapabilities("hardware", hardware_validated=True))


def test_faulty_registered_worker_is_rejected_by_cpu_reference_comparison():
    registry = ProcessingWorkerRegistry()
    class IncorrectWorker:
        def correlate(self, wire, request):
            result = EmulatedPLCorrelationWorker().correlate(wire, request)
            result["pairs"] += 1
            return result
    registry.register("wrong-worker", IncorrectWorker, ProcessingWorkerCapabilities("emulated-pl"))
    pipeline = NodeProcessingPipeline(node_config(worker="wrong-worker"), registry=registry)
    with pytest.raises(NodeProcessingError):
        pipeline.process(raw_block(), now_ps=0)
    assert pipeline.snapshot()["processed_blocks"] == 0 and pipeline.snapshot()["blocked"]


def test_empty_acquisition_produces_measured_zero_and_duplicate_block_cannot_run_twice():
    pipeline = NodeProcessingPipeline(node_config())
    result = pipeline.process(replace(raw_block(), events=()), now_ps=0)
    assert result["accepted"] and result["metrics"]["observations"] == 0
    assert result["metrics"]["coincidence_pairs"] == 0
    with pytest.raises(NodeProcessingError):
        pipeline.process(raw_block(), now_ps=1)
    assert pipeline.snapshot()["processed_blocks"] == 1


def test_sink_mutation_cannot_change_accepted_metrics():
    def sink(record):
        record["prepared_receipt"]["metrics"]["coincidence_pairs"] = 10_000
    pipeline = NodeProcessingPipeline(node_config(), on_record=sink)
    assert pipeline.process(raw_block(), now_ps=0)["metrics"]["coincidence_pairs"] == 1


def test_cancellation_during_persistence_preserves_prepared_record_without_final_acceptance():
    entered, release = Event(), Event()
    records = []
    def sink(record):
        records.append(record)
        entered.set()
        assert release.wait(5)
    pipeline = NodeProcessingPipeline(node_config(), on_record=sink)
    with ThreadPoolExecutor(max_workers=1) as pool:
        active = pool.submit(pipeline.process, raw_block(), now_ps=0)
        assert entered.wait(5)
        pipeline.cancel()
        release.set()
        with pytest.raises(NodeProcessingError) as failure:
            active.result(timeout=5)
    assert failure.value.receipt["accepted"] is False
    assert failure.value.receipt["persistence"] == "uncertain"
    assert len(records) == 1 and records[0]["prepared_receipt"]["accepted"] is False
    assert not pipeline.snapshot()["active"] and pipeline.snapshot()["processed_blocks"] == 0


def test_sustained_node_processing_conserves_counts_and_keeps_only_last_receipt():
    pipeline = NodeProcessingPipeline(node_config(worker="emulated-pl-v1"))
    observations = pairs = 0
    for sequence in range(200):
        result = pipeline.process(raw_block(sequence, start=sequence * 100), now_ps=sequence)
        observations += result["metrics"]["observations"]
        pairs += result["metrics"]["coincidence_pairs"]
    state = pipeline.snapshot()
    assert observations == 400 and pairs == 200
    assert state["processed_blocks"] == 200 and state["failed_blocks"] == 0
    assert state["last_receipt"]["sequence"] == 199 and not state["active"]
    assert state["dma"]["ownership"] == ["free"] * 8 and len(state["dma"]["recent_fences"]) == 256
