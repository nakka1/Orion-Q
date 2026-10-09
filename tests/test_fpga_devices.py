"""FPGA software acquisition uses real port admission and ordinary link processing."""
from contextlib import closing
from dataclasses import replace
import json
import sqlite3

import numpy as np
import pytest

from quantum_autopilot.control_plane.fpga_devices import (
    FpgaDeviceConfiguration, FpgaDeviceError, FpgaDeviceRegistry, FpgaDeviceRuntime,
    PhysicalFpgaAdapter,
)
from quantum_autopilot.control_plane.topology import (
    SimulatedLink, SimulatedNodeState, SimulatedTopology, SimulatedTopologyStore,
)
from quantum_autopilot.network_engine.channel_io import ChannelIntake
from quantum_autopilot.network_engine.engine import NetworkEngine
from quantum_autopilot.network_engine.models import ModelRegistry
from quantum_autopilot.network_engine.sources import IdleInputSource, SimulatedInputSource
from quantum_autopilot.security import AuthorizationError, SecurityRuntime

pytestmark = pytest.mark.unit


def topology():
    return SimulatedTopology("sandbox-line-3", 0,
        (SimulatedNodeState("A", "endpoint", 0, "IDLE"),
         SimulatedNodeState("B", "repeater", 0, "IDLE", telemetry={"demonstration_repeater": {}})),
        (SimulatedLink("A-B", "A", "B", 25, .97),))


def make_engine(root, *, source=None, security=None):
    root.mkdir(parents=True, exist_ok=True)
    path = root / "journal.sqlite"
    store = SimulatedTopologyStore(path)
    store.initialize(topology())
    return NetworkEngine(store, path, source=source or SimulatedInputSource(19, "quiet"),
                         registry=ModelRegistry(environ={}), security=security)


def register(engine, **changes):
    config = FpgaDeviceConfiguration("fpga-b", "B", **changes)
    return engine.command("register_fpga_device", {"device": config.to_dict(), "expected_revision": 0})


def device(engine):
    return engine.live_state().technologies["fpga_devices"]["devices"][0]


@pytest.mark.parametrize("changes", [{"clock_hz": float("nan")}, {"timeout_s": 0}, {"retry_ticks": True},
                                    {"pipeline_cycles": 0}, {"connected": 1}, {"adapter_id": "fake"}])
def test_invalid_configuration_rejected(changes):
    with pytest.raises(ValueError):
        FpgaDeviceConfiguration("fpga-b", "B", **changes)


def test_registry_revisions_identity_and_restart_require_fresh_acquisition(tmp_path):
    engine = make_engine(tmp_path)
    result = register(engine)
    assert result["status"] == "persisted" and len(result["receipt_sha256"]) == 64
    assert device(engine)["state"]["connection_state"] == "disconnected"
    engine.step()
    assert device(engine)["state"]["acquisition_state"] == "processed"
    restart = make_engine(tmp_path)
    assert device(restart)["configuration"]["revision"] == 1
    assert device(restart)["state"]["session_id"] is None
    assert device(restart)["state"]["acquisition_state"] == "no_input"
    assert restart.fpga.registry.recent()[0]["kind"] == "processing_feedback"
    with pytest.raises(FpgaDeviceError, match="revision"):
        restart.command("configure_fpga_device", {"device_id": "fpga-b", "expected_revision": 0,
                                                  "changes": {"clock_hz": 80_000_000}})
    with pytest.raises(FpgaDeviceError, match="immutable"):
        restart.command("configure_fpga_device", {"device_id": "fpga-b", "expected_revision": 1,
                                                  "changes": {"node_id": "A"}})


def test_raw_acquisition_and_computed_link_values_match_ordinary_pipeline(tmp_path):
    ordinary = make_engine(tmp_path / "ordinary")
    routed = make_engine(tmp_path / "fpga")
    register(routed)
    original_intake = routed.channel_intake
    original_bank = routed.bank
    expected, actual = ordinary.step().links[0], routed.step().links[0]
    assert original_intake is routed.channel_intake and original_bank is routed.bank
    assert (actual.qber, actual.skr_bps, actual.fidelity, actual.stability) == \
           (expected.qber, expected.skr_bps, expected.fidelity, expected.stability)
    assert actual.qkd == expected.qkd
    row = device(routed)
    assert row["state"]["accepted_frames"] == 1 and row["state"]["acquisition_state"] == "processed"
    receipt = row["state"]["last_receipts"][0]
    assert receipt["channel_receipt"]["status"] == "received"
    assert receipt["channel_receipt"]["stages"] == ["input_validation", "quantum_send", "quantum_receive",
        "classical_send", "classical_receive", "observation_validation"]
    assert receipt["qber"] == actual.qber and receipt["skr_bps"] == actual.skr_bps
    assert receipt["header"]["physical_action_performed"] is False
    assert receipt["header"]["observations_sha256"] and receipt["header"]["quantum_record_sha256"]
    assert row["state"]["measured_fpga_latency_s"] is None and row["state"]["temperature_c"] is None
    assert row["state"]["last_feedback"]["body"]["modeled_pipeline_latency_s"] == 256 / 100_000_000
    assert row["history"][0]["at_network_s"] == routed.live_state().network_time_s


def test_transport_copies_raw_arrays_and_never_supplies_finished_metrics(tmp_path):
    net = topology()
    store = SimulatedTopologyStore(tmp_path / "journal.sqlite")
    store.initialize(net)
    runtime = FpgaDeviceRuntime(store.path, net.topology_id)
    runtime.register({"device": {"device_id": "fpga-b", "node_id": "B"}, "expected_revision": 0}, net)
    source = SimulatedInputSource(19, "quiet")
    frame = source.frame(1, net)
    delivered = runtime.transfer_frame(frame, net)
    original, copied = frame.links["A-B"], delivered.links["A-B"]
    assert original.qkd.row == copied.qkd.row and original.qkd.row is not copied.qkd.row
    assert np.array_equal(original.polarisation.measured, copied.polarisation.measured)
    assert not np.shares_memory(original.polarisation.measured, copied.polarisation.measured)
    assert not copied.polarisation.measured.flags.writeable
    assert runtime.snapshot()["devices"][0]["state"]["acquisition_state"] == "received"
    assert runtime.snapshot()["devices"][0]["state"]["accepted_frames"] == 0
    assert ChannelIntake().ingest(delivered, net).links


def test_disconnect_reconnect_and_reconfigure_require_new_readback(tmp_path):
    engine = make_engine(tmp_path)
    register(engine)
    engine.step()
    first_session = device(engine)["state"]["session_id"]
    engine.command("set_fpga_connection", {"device_id": "fpga-b", "expected_revision": 1, "connected": False})
    assert engine.live_state().links[0].qber is None  # the command itself clears stale instantaneous values
    assert device(engine)["configuration"]["revision"] == 2
    engine.step()
    assert engine.live_state().links[0].status == "no_input" and engine.live_state().links[0].qber is None
    engine.command("set_fpga_connection", {"device_id": "fpga-b", "expected_revision": 2, "connected": True})
    assert device(engine)["state"]["session_id"] is None
    engine.step()
    assert device(engine)["state"]["acquisition_state"] == "processed"
    assert device(engine)["state"]["session_id"] != first_session
    engine.command("configure_fpga_device", {"device_id": "fpga-b", "expected_revision": 3,
        "changes": {"clock_hz": 50_000_000, "pipeline_cycles": 512, "timeout_s": 3, "retry_ticks": 2}})
    engine.step()
    assert device(engine)["state"]["last_feedback"]["body"]["modeled_pipeline_latency_s"] == 512 / 50_000_000


@pytest.mark.parametrize("kind", ["timeout", "disconnect"])
def test_timeout_backoff_and_reconnection_from_actual_responses(tmp_path, kind):
    engine = make_engine(tmp_path)
    register(engine, retry_ticks=2)
    engine.step()
    before = device(engine)["state"]["session_id"]
    engine.command("inject_fpga_fault", {"device_id": "fpga-b", "kind": kind, "duration_ticks": 3})
    engine.step()
    assert device(engine)["state"]["last_received_tick"] == 1
    assert engine.live_state().links[0].qber is None
    engine.step()
    assert device(engine)["state"]["connection_state"] == "timeout"
    assert device(engine)["state"]["timeouts"] == 1
    assert device(engine)["state"]["next_retry_tick"] == 5
    engine.step()
    assert device(engine)["state"]["connection_state"] == "timeout"
    engine.step()
    assert device(engine)["state"]["session_id"] != before
    assert device(engine)["state"]["acquisition_state"] == "processed"
    assert device(engine)["state"]["last_received_tick"] == 5
    assert any(row["kind"] == "timeout" for row in engine.fpga.registry.recent())


@pytest.mark.parametrize("kind", ["invalid_payload", "overrun"])
def test_fault_never_advances_processed_receiver_values(tmp_path, kind):
    engine = make_engine(tmp_path)
    register(engine)
    engine.command("inject_fpga_fault", {"device_id": "fpga-b", "kind": kind, "duration_ticks": 1})
    engine.step()
    assert device(engine)["state"]["acquisition_state"] == "rejected"
    assert device(engine)["state"]["accepted_frames"] == 0
    assert device(engine)["state"]["last_error"]
    assert engine.live_state().links[0].qber is None


def test_physical_contract_and_incompatible_sources_fabricate_no_readiness(tmp_path):
    engine = make_engine(tmp_path / "physical")
    register(engine, adapter_id="physical-contract")
    engine.step()
    physical = device(engine)
    assert physical["state"]["connection_state"] == "unavailable"
    assert physical["state"]["accepted_frames"] == 0 and physical["state"]["session_id"] is None
    assert engine.live_state().links[0].qber is None
    assert PhysicalFpgaAdapter().capabilities()["available"] is False
    with pytest.raises(FpgaDeviceError):
        PhysicalFpgaAdapter().connect(FpgaDeviceConfiguration("fpga-b", "B", adapter_id="physical-contract"))
    idle = make_engine(tmp_path / "idle", source=IdleInputSource())
    register(idle)
    idle.step()
    assert device(idle)["state"]["connection_state"] == "source_incompatible"
    with pytest.raises(ValueError, match="active simulated"):
        idle.command("inject_fpga_fault", {"device_id": "fpga-b", "kind": "timeout", "duration_ticks": 1})


@pytest.mark.parametrize("fault", ["classical_off", "size", "quantum_off"])
def test_existing_channel_limits_still_gate_fpga_processing(tmp_path, fault):
    net = topology()
    channels = json.loads(json.dumps(net.links[0].channels))
    if fault == "size":
        channels["classical"]["max_message_bytes"] = 1024
    else:
        channels["quantum" if fault == "quantum_off" else "classical"]["enabled"] = False
    net = replace(net, links=(replace(net.links[0], channels=channels),))
    store = SimulatedTopologyStore(tmp_path / "journal.sqlite")
    store.initialize(net)
    engine = NetworkEngine(store, store.path, source=SimulatedInputSource(19, "quiet"), registry=ModelRegistry(environ={}))
    register(engine)
    engine.step()
    assert device(engine)["state"]["accepted_frames"] == 0
    assert engine.live_state().links[0].qber is None and engine.live_state().links[0].skr_bps is None
    if fault == "quantum_off":
        assert device(engine)["state"]["last_receipts"][0]["channel_receipt"]["status"] == "rejected"


def test_scoped_authorization_and_authenticated_readback_audit(tmp_path):
    security = SecurityRuntime(tmp_path / "security")
    engine = make_engine(tmp_path / "workspace", security=security)
    security.grant_identity("viewer", ["auditor"], ["*"], source="service")
    with security.identity_scope(security.principal("viewer")), pytest.raises(AuthorizationError):
        register(engine)
    assert engine.fpga.snapshot()["devices"] == []
    register(engine)
    security.grant_identity("other-node", ["operator"], ["workspace:" + (tmp_path / "workspace").as_posix() + "/nodes/A"], source="service")
    with security.identity_scope(security.principal("other-node")):
        engine.step()
    assert device(engine)["state"]["connection_state"] == "authorization_denied"
    assert engine.live_state().links[0].qber is None
    engine.command("set_fpga_connection", {"device_id": "fpga-b", "expected_revision": 1, "connected": True})
    engine.step()
    feedback = device(engine)["state"]["last_feedback"]
    rows = [json.loads(row) for row in security.audit.path.read_text().splitlines()]
    completed = [row for row in rows if row["operation"] == "adapters.acquire" and row["result"] == "completed"]
    assert completed[-1]["details"]["artifact_sha256"] == feedback["record_sha256"]
    assert completed[-1]["details"]["mode"] == "simulated"
    assert any(row["operation"] == "adapters.configure" and row["result"] == "denied" for row in rows)
    assert security.audit.verify()["count"] == len(rows)


@pytest.mark.parametrize("table,column", [("fpga_devices", "configuration_json"), ("fpga_device_receipts", "record_json")])
def test_persisted_configuration_and_feedback_tampering_detected(tmp_path, table, column):
    engine = make_engine(tmp_path)
    register(engine)
    engine.step()
    with closing(sqlite3.connect(engine.fpga.registry.path)) as connection, connection:
        rows = connection.execute(f"SELECT rowid,{column} FROM {table}").fetchall()
        changed = json.loads(rows[-1][1])
        changed["label" if table == "fpga_devices" else "tick"] = "tampered"
        connection.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (json.dumps(changed), rows[-1][0]))
    with pytest.raises(FpgaDeviceError, match="hash"):
        FpgaDeviceRuntime(engine.fpga.registry.path, engine.topology_id)


def test_registry_enforces_receiver_uniqueness_with_independent_runtimes(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "journal.sqlite")
    store.initialize(topology())
    one, two = FpgaDeviceRegistry(store.path, "sandbox-line-3"), FpgaDeviceRegistry(store.path, "sandbox-line-3")
    one.save(FpgaDeviceConfiguration("one", "B"), 0)
    with pytest.raises(FpgaDeviceError, match="one receiver"):
        two.save(FpgaDeviceConfiguration("two", "B"), 0)


def test_demo_registration_is_explicit_and_persistent(tmp_path):
    engine = make_engine(tmp_path)
    assert engine.fpga.snapshot()["devices"] == []
    assert engine.seed_demo_fpga() == 1
    assert engine.seed_demo_fpga() == 0
    engine.step()
    assert device(engine)["configuration"]["device_id"] == "FPGA-DEMO-1"
    assert device(engine)["state"]["acquisition_state"] == "processed"
    restart = make_engine(tmp_path)
    assert restart.seed_demo_fpga() == 0 and len(restart.fpga.registry.configurations()) == 1


@pytest.mark.parametrize("clock,cycles,expected", [(1_000, 1_500, "rejected"), (1_000, 1_000_000_000, "timeout")])
def test_configured_pipeline_timing_causally_gates_acquisition(tmp_path, clock, cycles, expected):
    engine = make_engine(tmp_path)
    register(engine, clock_hz=clock, pipeline_cycles=cycles)
    engine.step()
    engine.step()
    assert device(engine)["state"]["connection_state"] == expected
    assert device(engine)["state"]["accepted_frames"] == 0
    assert engine.live_state().links[0].qber is None
    engine.command("configure_fpga_device", {"device_id": "fpga-b", "expected_revision": 1,
        "changes": {"clock_hz": 100_000_000, "pipeline_cycles": 256}})
    engine.step()
    assert device(engine)["state"]["acquisition_state"] == "processed"
