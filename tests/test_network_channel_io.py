"""Paired-port admission, failure isolation and identical receiver processing."""
from dataclasses import replace
import json

import numpy as np
import pytest

from quantum_autopilot.control_plane.channels import ChannelPair
from quantum_autopilot.control_plane.topology import SimulatedLink, SimulatedNodeState, SimulatedTopology, SimulatedTopologyStore
from quantum_autopilot.network_engine.channel_io import AcquisitionFileSource, ChannelIntake, observation_record
from quantum_autopilot.network_engine.engine import NetworkEngine
from quantum_autopilot.network_engine.models import ModelRegistry
from quantum_autopilot.network_engine.sources import InputFrame, LinkCommand, SimulatedInputSource


def topology():
    return SimulatedTopology("sandbox-line-3", 0,
        (SimulatedNodeState("A", "endpoint", 0, "IDLE"), SimulatedNodeState("B", "endpoint", 0, "IDLE")),
        (SimulatedLink("A-B", "A", "B", 25, .97),))


@pytest.fixture
def acquisition():
    net = topology()
    return net, SimulatedInputSource(19, "quiet").frame(1, net)


def test_both_ports_receive_before_observations_reach_processing(acquisition):
    net, frame = acquisition
    intake = ChannelIntake()
    delivered = intake.ingest(frame, net)
    receipt = intake.receipts["A-B"]
    assert receipt["stages"] == ["input_validation", "quantum_send", "quantum_receive", "classical_send",
                                 "classical_receive", "observation_validation"]
    assert receipt["status"] == "received" and receipt["physical_transmission"] is False
    assert receipt["classical_payload_bytes"] > 1000
    assert receipt["quantum_record_sha256"] != receipt["classical_payload_sha256"]
    original, received = frame.links["A-B"], delivered.links["A-B"]
    assert original.qkd.row == received.qkd.row and original.qkd.row is not received.qkd.row
    assert received.quantum_record is None and received.reference == {}
    assert np.array_equal(original.polarisation.measured, received.polarisation.measured)
    assert not np.shares_memory(original.polarisation.measured, received.polarisation.measured)
    assert not received.polarisation.measured.flags.writeable


@pytest.mark.parametrize("fault", ["no_pair", "no_quantum", "wrong_pair", "wrong_receiver", "classical_off",
                                  "quantum_off", "too_large", "stale", "nan", "not_so3", "wrong_span", "wrong_count"])
def test_missing_failed_or_invalid_communication_never_delivers_node_values(acquisition, fault):
    net, frame = acquisition
    item = frame.links["A-B"]
    channels = json.loads(json.dumps(net.links[0].channels))
    if fault == "no_pair":
        net = replace(net, links=(replace(net.links[0], channels=None),))
    elif fault == "no_quantum":
        item = replace(item, quantum_record=None)
    elif fault in ("wrong_pair", "wrong_receiver", "wrong_count"):
        key, value = {"wrong_pair": ("associated_classical_id", "other"),
                      "wrong_receiver": ("receiver_id", "A"), "wrong_count": ("emitted_pulses", 1)}[fault]
        item = replace(item, quantum_record={**item.quantum_record, key: value})
    elif fault in ("classical_off", "quantum_off", "too_large"):
        channel = "quantum" if fault == "quantum_off" else "classical"
        channels[channel]["max_message_bytes" if fault == "too_large" else "enabled"] = 1024 if fault == "too_large" else False
        net = replace(net, links=(replace(net.links[0], channels=channels),))
    elif fault == "stale":
        frame = replace(frame, network_time_s=-3.0)
    elif fault == "nan":
        item = replace(item, sync=replace(item.sync, measured_offset_s=float("nan")))
    elif fault == "not_so3":
        item = replace(item, polarisation=replace(item.polarisation, measured=np.zeros_like(item.polarisation.measured)))
    elif fault == "wrong_span":
        item = replace(item, distance_km=26)
    intake = ChannelIntake()
    delivered = intake.ingest(replace(frame, links={"A-B": item}), net)
    assert delivered.links == {}
    assert intake.receipts["A-B"]["status"] == "rejected" and intake.receipts["A-B"]["error"]


def test_repeated_and_out_of_order_acquisitions_are_not_fresh_communication(acquisition):
    net, first = acquisition
    source = SimulatedInputSource(19, "quiet")
    source.frame(1, net)
    second = source.frame(2, net)
    intake = ChannelIntake()
    assert intake.ingest(first, net).links
    assert not intake.ingest(first, net).links
    assert intake.ingest(second, net).links
    assert not intake.ingest(replace(first, tick=3, network_time_s=3.), net).links


def test_legacy_inventory_is_preserved_without_fabricating_channel_configuration(acquisition):
    net, _ = acquisition
    payload = net.to_dict()
    payload["links"][0].pop("channels")
    loaded = SimulatedTopology.from_dict(payload)
    assert loaded.links[0].channels is None
    assert "channels" not in payload["links"][0]


def test_control_commands_use_only_the_classical_port(acquisition):
    net, frame = acquisition
    intake = ChannelIntake()
    intake.ingest(frame, net)
    calls = []

    class Sink:
        def actuate(self, commands):
            calls.append(commands)

    command = LinkCommand("A-B", recommission=True)
    intake.actuator(Sink(), net).actuate({"A-B": command})
    assert calls == [{"A-B": command}] and calls[0]["A-B"] is not command
    assert intake.receipts["A-B"]["control_messages"] == 1
    pair = net.links[0].channels
    disabled = {**pair, "classical": {**pair["classical"], "enabled": False}}
    intake.actuator(Sink(), replace(net, links=(replace(net.links[0], channels=disabled),))).actuate({"A-B": command})
    assert calls[-1] == {}


def test_unpaired_link_cannot_be_reported_as_a_successful_network_route(tmp_path):
    net = topology()
    net = replace(net, links=(replace(net.links[0], channels=None),))
    store = SimulatedTopologyStore(tmp_path / "unpaired.sqlite")
    store.initialize(net)
    engine = NetworkEngine(store, store.path, source=SimulatedInputSource(19, "quiet"), registry=ModelRegistry(environ={}))
    engine.command("define_pair", {"demand_id": "P1", "a": "A", "b": "B"})
    state = engine.step()
    assert state.links[0].status == "no_input" and state.links[0].qber is None
    assert state.demands[0].status == "no_route" and state.demands[0].route == ()


def test_published_channel_receipts_are_not_mutated_by_the_next_tick(tmp_path):
    net = topology()
    store = SimulatedTopologyStore(tmp_path / "snapshots.sqlite")
    store.initialize(net)
    engine = NetworkEngine(store, store.path, source=SimulatedInputSource(19, "quiet"), registry=ModelRegistry(environ={}))
    previous = engine.step()
    receipt = json.dumps(previous.links[0].provenance["channels"], sort_keys=True)
    engine.step()
    assert json.dumps(previous.links[0].provenance["channels"], sort_keys=True) == receipt


def test_channel_identifiers_cannot_be_reused_by_another_link():
    net = topology()
    second = SimulatedLink("second", "B", "A", 25, .97)
    configuration = second.channels
    configuration["classical"]["channel_id"] = net.links[0].channels["classical"]["channel_id"]
    with pytest.raises(ValueError, match="channel identifiers must be unique"):
        replace(net, links=(*net.links, replace(second, channels=configuration)))


def capture(tmp_path, frames, kind):
    records = []
    for frame in frames:
        links = []
        for item in frame.links.values():
            quantum = dict(item.quantum_record)
            quantum["scope"] = "recorded_measurement" if kind == "measured" else "statistical_simulation"
            links.append({"observation": observation_record(item), "quantum_record": quantum})
        records.append({"tick": frame.tick, "links": links})
    path = tmp_path / "acquisitions.json"
    path.write_text(json.dumps({"schema": "orion.channel-capture.v1", "source_id": "test-fixture",
                               "kind": kind, "frames": records}, allow_nan=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("kind", ["simulated", "measured"])
def test_simulation_and_raw_file_replay_follow_the_same_ports_and_compute_the_same_results(tmp_path, kind):
    # "measured" is a declared test-fixture label here, not real hardware evidence.
    net = topology()
    acquisition_source = SimulatedInputSource(19, "quiet")
    frames = [acquisition_source.frame(tick, net) for tick in range(1, 66)]
    path = capture(tmp_path, frames, kind)
    replay = AcquisitionFileSource(path, expected_kind=kind)
    simulation = SimulatedInputSource(19, "quiet")
    engines = []
    for label, source in (("simulation", simulation), ("replay", replay)):
        store = SimulatedTopologyStore(tmp_path / (label + ".sqlite"))
        store.initialize(net)
        engines.append(NetworkEngine(store, store.path, source=source, registry=ModelRegistry(environ={})))
    for _ in range(65):
        states = [engine.step() for engine in engines]
        left, right = (state.links[0] for state in states)
        for name in ("status", "qber", "fidelity", "skr_bps", "wave", "history", "qkd", "detector"):
            assert getattr(left, name) == getattr(right, name), name
        for live in (left, right):
            receipt = live.provenance["channels"]
            assert receipt["status"] == "received" and len(receipt["stages"]) == 6
            assert receipt["physical_transmission"] is False
    assert "not authenticated" in replay.info().note
    assert engines[1].step().links[0].qber is None, "end of capture must not repeat the last node state"
    with pytest.raises(ValueError, match="read-only"):
        replay.inject(None)


@pytest.mark.parametrize("change", ["missing_channel", "same_ids", "unsupported_driver"])
def test_channel_pair_is_mandatory_distinct_and_does_not_imply_hardware(change):
    pair = topology().links[0].channels
    if change == "missing_channel":
        pair.pop("quantum")
    elif change == "same_ids":
        pair["quantum"]["channel_id"] = pair["classical"]["channel_id"]
    else:
        pair["quantum"]["protocol"] = "physical-hardware"
    with pytest.raises(ValueError):
        ChannelPair.from_dict(pair)
