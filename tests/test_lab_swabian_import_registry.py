"""FileReader metadata/arrays fixtures, not .ttbin native decoding or licensing."""
from dataclasses import replace
import hashlib
from types import SimpleNamespace

import pytest

from quantum_autopilot.lab.adapter_registry import adapter_descriptors, create_photon_adapter, validate_adapter_configuration
from quantum_autopilot.lab.adapters import EmulatedPhotonAdapter, ReplayPhotonAdapter
from quantum_autopilot.lab.photon import AcquisitionError, PhotonConfig, analyze, digest, store_acquisition
from quantum_autopilot.lab.swabian_import import FileWriterContext, import_filewriter_record


def file_fixture(tmp_path, *, kinds=(7, 7), missing=False, config_change=False):
    paths = [tmp_path / name for name in ("fixture.ttbin", "fixture.1.ttbin", "fixture.2.ttbin")]
    for index, path in enumerate(paths):
        path.write_bytes(f"synthetic FileReader interface fixture {index}; not binary TimeTagger data".encode())
    native = {"fixture": True, "trigger_levels_v": [.35, .35], "input_delays_ps": [0, -125]}
    cfg = PhotonConfig(duration_ps=1000)
    context = FileWriterContext("source-experiment", "source-tagger", "synthetic-native-interface-fixture", "simulated",
                               cfg, 1000, 0, digest(native),
                               {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
    data = [SimpleNamespace(getTimestamps=lambda: [100, 101], getChannels=lambda: [1, 2],
                           getEventTypes=lambda: list(kinds), getMissedEvents=lambda: [0, 0], hasOverflows=False)]
    calls = []
    def factory(files):
        calls.append(files)
        return SimpleNamespace(getConfiguration=lambda: {"changed": True} if config_change and not data else native,
                               getChannelList=lambda: [1] if missing else [1, 2],
                               hasData=lambda: bool(data), getData=lambda size: data.pop())
    return paths, context, factory, calls


def test_filestream_normalization_retains_original_provenance_configuration_and_integer_ps(tmp_path):
    paths, context, factory, calls = file_fixture(tmp_path)
    result = import_filewriter_record(paths, context=context, event_type_map={7: "TimeTag"}, reader_factory=factory)
    assert calls == [[str(p.resolve()) for p in paths[1:]]]
    assert result.mode == "replay" and result.origin == "simulated" and result.experiment_id == "source-experiment"
    assert result.diagnostics["validation_level"] == "synthetic_sdk_interface"
    assert not result.diagnostics["hardware_validated"] and not result.diagnostics["virtual_replay_license_validated"]
    assert analyze(result)["coincidences"] == 1
    assert result.events[1].timestamp_ps - result.events[0].timestamp_ps == 1


@pytest.mark.parametrize("failure", ["header", "gap", "hash", "channels", "config", "event", "context"])
def test_file_import_rejects_incomplete_or_inconsistent_evidence(tmp_path, failure):
    paths, context, factory, _ = file_fixture(tmp_path, kinds=(7, 999) if failure == "event" else (7, 7),
                                            missing=failure == "channels", config_change=failure == "config")
    if failure == "header":
        paths = paths[:1]
        context = replace(context, file_sha256={paths[0].name: context.file_sha256[paths[0].name]})
    elif failure == "gap":
        paths[1].unlink()
        paths = [paths[0], paths[2]]
        context = replace(context, file_sha256={p.name: context.file_sha256[p.name] for p in paths})
    elif failure == "hash":
        paths[1].write_bytes(b"changed")
    elif failure == "context":
        context = replace(context, native_configuration_sha256="0" * 64)
    with pytest.raises(AcquisitionError):
        import_filewriter_record(paths, context=context, event_type_map={7: "TimeTag"}, reader_factory=factory)


def test_service_registry_swaps_distinct_implementations_without_rewriting_acquisition_core(tmp_path):
    cfg = PhotonConfig()
    emulator = create_photon_adapter("emulated")
    assert isinstance(emulator, EmulatedPhotonAdapter)
    emulator.connect()
    validate_adapter_configuration(emulator, cfg)
    emulator.configure(cfg)
    emulator.calibrate()
    original = emulator.acquire("registry-original")
    path = store_acquisition(original, tmp_path)
    replay = create_photon_adapter("orion-replay", {"manifest_path": path})
    assert isinstance(replay, ReplayPhotonAdapter) and type(replay) != type(emulator)
    for adapter in (replay,):
        adapter.connect()
        validate_adapter_configuration(adapter, cfg)
        adapter.configure(cfg)
        adapter.calibrate()
        result = adapter.acquire("registry-replay")
    assert result.events == original.events
    assert digest(result.configuration) == digest(original.configuration)
    descriptors = {d["adapter_id"]: d for d in adapter_descriptors()}
    assert descriptors["emulated-reference"]["available"]
    assert not descriptors["swabian-native"]["available"]
    with pytest.raises(AcquisitionError):
        create_photon_adapter("unknown")
    with pytest.raises(AcquisitionError):
        create_photon_adapter("emulated", {"serial": "no-hardware"})


def test_registered_service_acquisition_uses_same_steps_and_preserves_replay_configuration(tmp_path):
    from quantum_autopilot.lab.photon import run_registered_acquisition
    cfg = PhotonConfig(seed=812)
    original = run_registered_acquisition(tmp_path / "original", config=cfg, adapter_id="emulated")
    replay = run_registered_acquisition(tmp_path / "replay", config=cfg, adapter_id="orion-replay",
                                       manifest_path=original["artifacts"]["nominal"])
    expected = ["register", "connect", "configure", "calibrate", "acquire", "store", "analyze"]
    assert [s["action"] for s in original["journey"]] == [s["action"] for s in replay["journey"]] == expected
    assert replay["nominal"]["counts"] == original["nominal"]["counts"]
    assert replay["nominal"]["histogram"] == original["nominal"]["histogram"]
    assert replay["mode"] == "replay" and replay["origin"] == "simulated"
    assert replay["original_experiment_id"] == original["original_experiment_id"]
    assert replay["replay_run_id"] != replay["original_experiment_id"]
    assert not replay["hardware_validated"]
    with pytest.raises(AcquisitionError, match="configuration"):
        run_registered_acquisition(tmp_path / "rejected", config=replace(cfg, trigger_levels_v=(.5, .5)),
                                   adapter_id="orion-replay", manifest_path=original["artifacts"]["nominal"])
