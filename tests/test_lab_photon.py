"""Independent numerical, provenance, state and failure contracts for the photon bench."""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, replace

import pytest

from quantum_autopilot.lab.adapters import AdapterUnavailable, EmulatedPhotonAdapter, ReplayPhotonAdapter, SwabianSDKAdapter
from quantum_autopilot.lab.photon import (Acquisition, AcquisitionError, MODEL_ID, PhotonConfig, TimeTag,
                                        analyze, digest, load_acquisition, run_photon_demo, store_acquisition)

pytestmark = pytest.mark.unit


def ready_adapter(config=None, device_id="test-tagger"):
    adapter = EmulatedPhotonAdapter(device_id)
    adapter.connect()
    adapter.configure(config or PhotonConfig())
    adapter.calibrate()
    return adapter


def independent_record(points, *, duration_ps=100_000, origin="simulated", start_ps=0):
    cfg = PhotonConfig(duration_ps=duration_ps, coincidence_window_ps=100,
                       histogram_radius_ps=200, histogram_bin_ps=50)
    source = MODEL_ID if origin == "simulated" else "identified-test-fixture"
    events = [TimeTag(timestamp, channel, "fixture", "tagger", cfg.configuration_id,
                      cfg.clock_domain, origin, source) for timestamp, channel in points]
    return Acquisition("fixture", "tagger", "fixture-adapter", "emulation" if origin == "simulated" else "hardware",
                       origin, asdict(cfg), events, duration_ps, cfg.clock_domain, source,
                       diagnostics={"model_id": MODEL_ID, "capture_start_ps": start_ps})


def test_known_integer_timestamps_independent_counts_window_edges_and_histogram():
    # Window is [-50,+50). The event exactly +50 is excluded; exactly -50 is included.
    record = independent_record([(1000, 2), (1050, 1), (1099, 2), (1100, 2), (1500, 1)])
    result = analyze(record)
    assert result["counts"] == {"1": 2, "2": 3}
    assert result["coincidences"] == 2
    assert result["histogram"]["counts"] == [0, 0, 0, 1, 1, 1, 0, 0]
    assert result["accidentals"]["poisson_estimate"] == pytest.approx(0.006)
    assert result["entanglement_claim"] is False


def test_integer_precision_above_javascript_safe_integer_survives_storage_replay(tmp_path):
    base = 2**53 + 101
    record = independent_record([(base, 1), (base + 1, 2)], origin="physical", start_ps=base)
    manifest = store_acquisition(record, tmp_path)
    restored = load_acquisition(manifest)
    assert restored.events[0].timestamp_ps == base
    assert restored.events[1].timestamp_ps - restored.events[0].timestamp_ps == 1
    assert analyze(restored)["coincidences"] == 1


@pytest.mark.parametrize("changes", [
    {"timestamp_unit": "ns"}, {"duration_ps": 1.5}, {"duration_ps": True},
    {"channels": (1, 1)}, {"channels": (True, 2)}, {"trigger_levels_v": (0.9, 0.35)},
    {"efficiency": (1.01, 0.5)}, {"jitter_sigma_ps": (float("nan"), 60)},
    {"detector_band_nm": (1500, 1600)}, {"optical_connector": "FC/APC"},
    {"pulse_level_v": 5.0}, {"source_transport": "REST"}, {"source_transport": "RS232"},
    {"input_delays_ps": (0.5, 0)}, {"clock_epoch_utc": "2026-10-07T00:00:00Z"},
])
def test_configuration_rejects_incompatible_spectrum_levels_connectors_units_and_transports(changes):
    with pytest.raises(AcquisitionError):
        replace(PhotonConfig(), **changes).validate()


def test_replay_preserves_original_origin_clock_experiment_and_applied_configuration(tmp_path):
    source = ready_adapter().acquire("original-experiment")
    manifest = store_acquisition(source, tmp_path)
    replay = ReplayPhotonAdapter(manifest)
    replay.connect()
    replay.configure(PhotonConfig())
    replay.calibrate()
    reproduced = replay.acquire("new-replay-run")
    assert reproduced.mode == "replay" and reproduced.origin == "simulated"
    assert reproduced.experiment_id == "original-experiment"
    assert reproduced.clock_domain == source.clock_domain
    assert reproduced.events == source.events
    assert digest(reproduced.configuration) == digest(source.configuration)
    assert reproduced.diagnostics["replay_run_id"] == "new-replay-run"
    assert reproduced.evidence_level == "simulation_replay"


def test_replay_cannot_change_original_acquisition_configuration(tmp_path):
    manifest = store_acquisition(ready_adapter().acquire("exp"), tmp_path)
    replay = ReplayPhotonAdapter(manifest)
    replay.connect()
    with pytest.raises(AcquisitionError, match="cannot rewrite"):
        replay.configure(replace(PhotonConfig(), input_delays_ps=(0, 0)))


def test_corrupt_data_and_metadata_rejected(tmp_path):
    source = ready_adapter().acquire("exp")
    manifest = store_acquisition(source, tmp_path)
    data = json.loads(manifest.read_text())
    events = tmp_path / data["events_file"]
    events.write_bytes(events.read_bytes() + b"{}\n")
    with pytest.raises(AcquisitionError, match="event integrity"):
        load_acquisition(manifest)
    manifest = store_acquisition(source, tmp_path)
    data = json.loads(manifest.read_text())
    data["origin"] = "physical"
    manifest.write_text(json.dumps(data))
    with pytest.raises(AcquisitionError, match="manifest integrity"):
        load_acquisition(manifest)


def test_timestamp_float_or_regression_rejected_without_sorting():
    record = independent_record([(200, 1), (100, 2)])
    with pytest.raises(AcquisitionError, match="regression"):
        analyze(record)
    record = independent_record([(100.0, 1)])
    with pytest.raises(AcquisitionError, match="integer"):
        analyze(record)


def test_every_event_must_match_recorded_configuration_identity_clock_and_origin():
    record = independent_record([(100, 1)])
    for change in ({"clock_domain": "another-clock"}, {"origin": "physical"},
                   {"device_id": "another-device"}, {"configuration_id": "rewritten"}, {"source_id": "other"}):
        damaged = replace(record, events=[replace(record.events[0], **change)])
        with pytest.raises(AcquisitionError, match="provenance"):
            analyze(damaged)


@pytest.mark.parametrize("fault,flag", [
    ("overflow", "overflow"), ("desync", "clock_unlocked"), ("clock_drift", "clock_drift"),
    ("disconnect", "disconnected"), ("detector_unavailable", "detector_unavailable"),
])
def test_faults_mark_analysis_incomplete_and_require_reconnection_when_appropriate(fault, flag):
    adapter = ready_adapter()
    record = adapter.acquire("fault", fault=fault)
    result = analyze(record)
    assert flag in result["quality_flags"] and result["complete"] is False
    if fault == "overflow":
        assert result["missed_events"] > 0 and result["counts"]["1"] + result["counts"]["2"] == 20
    adapter.disconnect()
    adapter.connect()
    adapter.configure(PhotonConfig())
    adapter.calibrate()
    assert analyze(adapter.acquire("recovered"))["complete"] is True


def test_model_sensitivity_to_loss_and_seed():
    baseline = analyze(ready_adapter().acquire("baseline"))
    loss = analyze(ready_adapter().acquire("loss", fault="loss"))
    other = analyze(ready_adapter(replace(PhotonConfig(), seed=811)).acquire("other"))
    assert loss["coincidences"] < baseline["coincidences"] / 10
    assert other["counts"] != baseline["counts"]
    same = analyze(ready_adapter().acquire("repeated"))
    assert same["counts"] == baseline["counts"] and same["coincidences"] == baseline["coincidences"]


def test_state_machine_prevents_acquiring_before_calibration():
    adapter = EmulatedPhotonAdapter()
    with pytest.raises(AcquisitionError):
        adapter.acquire("exp")
    adapter.connect()
    adapter.configure(PhotonConfig())
    with pytest.raises(AcquisitionError):
        adapter.acquire("exp")
    adapter.calibrate()
    adapter.disconnect()
    with pytest.raises(AcquisitionError):
        adapter.acquire("exp")


def test_concurrent_acquisition_is_rejected_instead_of_reusing_active_device(monkeypatch):
    adapter = ready_adapter()
    started, release = threading.Event(), threading.Event()
    original = adapter._generate
    result = []

    def blocking(*args):
        started.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(adapter, "_generate", blocking)
    worker = threading.Thread(target=lambda: result.append(adapter.acquire("worker")))
    worker.start()
    try:
        assert started.wait(2)
        with pytest.raises(AcquisitionError):
            adapter.acquire("concurrent")
        with pytest.raises(AcquisitionError):
            adapter.configure(PhotonConfig())
    finally:
        release.set()
        worker.join(timeout=3)
    assert not worker.is_alive() and len(result) == 1


def test_native_sdk_absence_is_explicit_and_does_not_fall_back_to_simulated(monkeypatch):
    native = SwabianSDKAdapter()
    monkeypatch.setattr(native, "availability", lambda: {"installed": False})
    with pytest.raises(AdapterUnavailable, match="not installed"):
        native.connect()
    assert native.tagger is None and native.state == "registered"


def test_native_virtual_requires_source_record_and_never_uses_empty_simulation():
    with pytest.raises(AcquisitionError, match="FileWriter"):
        SwabianSDKAdapter(mode="replay")
    with pytest.raises(AcquisitionError, match="supports hardware or native replay"):
        SwabianSDKAdapter(mode="emulation")


def test_demo_repeats_faults_and_adapter_substitution_with_explicit_limit(tmp_path):
    result = run_photon_demo(tmp_path)
    assert result["passed"] is True
    assert result["hardware_validated"] is False
    assert result["experimental_replay_validated"] is False
    assert result["substitution"]["emulation_to_replay_equal"] is True
    assert len(result["faults"]) == 7
    assert all(case["detected"] and case["recovery_complete"] for case in result["faults"])
    assert all((tmp_path / name).is_file() for name in ("nominal.events.jsonl", "nominal.manifest.json", "photon_summary.json"))



def fake_native_adapter(monkeypatch, *, mode="hardware", address=None, replay_file=None, event_types=(7, 7),
                        timestamps=None, saturated=False, fail_creation=False):
    # An explicitly synthetic interface double tests call routing only, never manufacturer fidelity.
    from types import SimpleNamespace
    from quantum_autopilot.lab import adapters as module
    calls = []
    base = 2**53 + 11
    timestamps = list(timestamps or [base + 100, base + 101])
    native = SwabianSDKAdapter(mode=mode, address=address, replay_file=replay_file,
                              serial="synthetic-contract-double" if mode == "hardware" else "",
                              event_type_map={7: "TimeTag"}, test_only_transport=True)
    buffer = SimpleNamespace(getTimestamps=lambda: timestamps, getChannels=lambda: [1, 2],
                             getEventTypes=lambda: list(event_types), getMissedEvents=lambda: [0, 0],
                             hasOverflows=False, size=100_000 if saturated else 2, tStart=base)
    stream = SimpleNamespace(startFor=lambda n: calls.append(("startFor", n)),
                             waitUntilFinished=lambda **kw: True, getData=lambda: buffer,
                             getCaptureDuration=lambda: 100_000, stop=lambda: calls.append(("stop",)))
    tagger = SimpleNamespace(getSerial=lambda: "synthetic-contract-double",
                             getModel=lambda: "Time Tagger Ultra", getPcbVersion=lambda: "synthetic PCB",
                             getDeviceLicense=lambda: {"synthetic_fixture": True},
                             getConfiguration=lambda: {"synthetic_interface_double": True},
                             getChannelList=lambda: [1, 2], getTriggerLevelRange=lambda c: [-2.5, 2.5],
                             setTriggerLevel=lambda c, v: calls.append(("trigger", c, v)),
                             getTriggerLevel=lambda c: .35,
                             setInputDelay=lambda c, v: calls.append(("delay", c, v)),
                             getInputDelay=lambda c: 0 if c == 1 else -125,
                             sync=lambda **kw: True, autoCalibration=lambda: [10, 10],
                             getReferenceClockState=lambda: SimpleNamespace(enabled=False),
                             disableReferenceClock=lambda: None,
                             clearOverflows=lambda: None, getOverflows=lambda: 0)

    def factory(kind, *args):
        calls.append((kind, *args))
        return tagger

    def stream_factory(*args):
        if fail_creation:
            raise RuntimeError("synthetic factory failure")
        return stream

    sdk = SimpleNamespace(createTimeTagger=lambda *args: factory("hardware", *args),
                          createTimeTaggerNetwork=lambda *args: factory("network", *args),
                          createTimeTaggerVirtual=lambda *args: factory("virtual", *args),
                          freeTimeTagger=lambda tag: calls.append(("free",)),
                          TimeTagStream=stream_factory)
    monkeypatch.setattr(native, "availability", lambda: {"installed": True})
    monkeypatch.setattr(module.importlib, "import_module", lambda name: sdk)
    return native, calls, base


@pytest.mark.parametrize("address,factory", [(None, "hardware"), ("fixture.invalid:41101", "network")])
def test_native_documented_factory_routing_contract_only(monkeypatch, address, factory):
    adapter, calls, base = fake_native_adapter(monkeypatch, address=address)
    identity = adapter.connect()
    assert calls[0][0] == factory
    assert identity["serial"] == "synthetic-contract-double"
    adapter.configure(PhotonConfig(duration_ps=100_000))
    adapter.calibrate()
    record = adapter.acquire("synthetic-interface-contract")
    assert record.events[0].timestamp_ps == base + 100
    assert record.clock_domain.startswith("swabian:synthetic-contract-double:")
    assert analyze(record)["coincidences"] == 1
    assert record.diagnostics["optical_bench_validated"] is False
    adapter.disconnect()
    assert calls[-1] == ("free",)


def test_native_virtual_factory_receives_file_without_claiming_decode_or_license(tmp_path, monkeypatch):
    filename = tmp_path / "interface-double.ttbin"
    filename.write_bytes(b"explicitly synthetic test fixture, not a native file")
    adapter, calls, _ = fake_native_adapter(monkeypatch, mode="replay", replay_file=filename)
    adapter.connect()
    assert calls[0] == ("virtual", str(filename))
    with pytest.raises(AdapterUnavailable, match="metadata"):
        adapter.configure(PhotonConfig())
    adapter.disconnect()


@pytest.mark.parametrize("changes,expected", [
    ({"event_types": (7, 999)}, "unknown native event type"),
    ({"timestamps": [100.5, 101]}, "integer"),
    ({"fail_creation": True}, "synthetic factory failure"),
])
def test_native_invalid_stream_fails_explicitly_and_enters_fault_state(monkeypatch, changes, expected):
    adapter, _, _ = fake_native_adapter(monkeypatch, **changes)
    adapter.connect()
    adapter.configure(PhotonConfig(duration_ps=100_000))
    adapter.calibrate()
    with pytest.raises((AcquisitionError, RuntimeError, TypeError), match=expected):
        adapter.acquire("synthetic-interface-contract")
    assert adapter.state == "fault"
    adapter.disconnect()


def test_native_local_buffer_saturation_does_not_depend_on_stream_overflow(monkeypatch):
    adapter, _, _ = fake_native_adapter(monkeypatch, saturated=True)
    adapter.connect()
    adapter.configure(PhotonConfig(duration_ps=100_000))
    adapter.calibrate()
    record = adapter.acquire("synthetic-interface-contract")
    result = analyze(record)
    assert result["complete"] is False and "stream_buffer_saturated" in result["quality_flags"]



def test_deposited_physical_aggregates_replay_preserves_rates_units_times_and_device_origin(tmp_path):
    from pathlib import Path
    from quantum_autopilot.lab.photon import replay_experimental_aggregates
    root = Path(__file__).resolve().parents[1] / "data/raw/bristol-hcf-coex/61193flfovbn2puknndsr3qm5"
    if not root.is_dir():
        pytest.skip("experimental dataset is unavailable in this checkout")
    result = replay_experimental_aggregates(root, tmp_path)
    assert result["origin"] == "physical" and result["mode"] == "replay"
    assert result["level"] == "experimental_aggregate"
    assert result["source_doi"] == "10.5523/bris.61193flfovbn2puknndsr3qm5"
    assert len(result["reported_summary"]) == 12
    assert result["time_reference"]["chunk_time_column_original"] == [600.0 * i for i in range(12)]
    assert result["reported_summary"][0]["QBER AB"] == pytest.approx(0.044932882024546)
    assert result["reported_summary"][0]["Counts A"] == pytest.approx(14148.740000000002)
    assert result["histograms"]["AB"]["peak_delay_ps"] == 0
    assert result["histograms"]["BC"]["peak_delay_ps"] == -10
    assert all(type(value) is int for value in result["histograms"]["AB"]["x_ps"])
    assert result["native_file_is_header_only"] is True and result["raw_native_replay_available"] is False
    assert result["secret_material_generated"] is False and result["certified_secret_bits"] == 0
    assert result["source_devices"][0]["manufacturer"] == "Photon Spot"
    assert all(file["matches_deposit_metadata"] for file in result["source_files"])

