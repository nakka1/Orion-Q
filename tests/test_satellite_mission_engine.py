"""Native/service integration preserves optical physics and durable radio evidence."""

import json
import time

import pytest
from websockets.sync.client import connect

from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from quantum_autopilot.control_plane.communicators import Communicator, defaults
from quantum_autopilot.network_engine.engine import NetworkEngine
from quantum_autopilot.network_engine.sources import IdleInputSource
from quantum_autopilot.network_engine.institutional import brazil_institutions
from quantum_autopilot.quantum_protocols.topology import expand_global
from quantum_autopilot.security import SecurityRuntime
from quantum_autopilot.security.identity import AuthorizationError
from tests.test_satellite_gateway import envelope


def radio_record():
    """Public Suomi100 identity/TLE; no hypothetical optical payload."""
    # Space-Track TLE archived by SatNOGS observation 15146588, 2026-10-08.
    orbit = {
        **defaults("orbit"),
        "model": "sgp4_tle",
        "orbital_class": "LEO",
        "epoch_utc": "2026-10-06T16:19:00.213312Z",
        "source": "Space-Track via SatNOGS observation 15146588",
        "tle_line1": "1 43804U 18099AY  26279.67986358  .00009315  00000-0  31628-3 0  9995",
        "tle_line2": "2 43804  97.4067 339.7642 0008189 238.3418 121.7024 15.30686120430469",
        "optical_payload": "none_classical_telemetry_only",
        "compatible_stations": [],
    }
    return Communicator(
        "SUOMI100-PUBLIC",
        "SUOMI100-PUBLIC",
        "Suomi100 · public radio capture",
        "satellite",
        "intermediate",
        capabilities=["classical_telemetry"],
        protocols=["CSP1"],
        technologies=[],
        optical={},
        configuration={},
        orbit=orbit,
        mission={
            "mission_id": "suomi100",
            "norad_catalog_id": 43804,
            "decoder_profile": envelope()["decoder_profile"],
        },
        integration={
            "mode": "measurement_file",
            "adapter": "measurement_file",
            "interface": "mission_file_v1",
            "service_address": "public observation 15146588",
            "credential_ref": "",
            "data_origin": "measured",
        },
    )


def engine_fixture(tmp_path):
    path = tmp_path / "network.sqlite"
    topology = expand_global(brazil_institutions())[0]
    store = SimulatedTopologyStore(path)
    store.initialize(topology)
    security = SecurityRuntime(tmp_path / ".security")
    engine = NetworkEngine(
        store, path, source=IdleInputSource(), topology_id=topology.topology_id, security=security
    )
    record = radio_record()
    engine.command(
        "save_communicator",
        {"expected_version": store.get(topology.topology_id).topology_version, "record": record.to_dict()},
    )
    satellite_id = record.node_id
    return engine, store, path, security, satellite_id


def test_unconfigured_radio_satellite_exposes_configuration_and_corrupt_sidecar_is_isolated(tmp_path):
    engine, store, path, security, satellite_id = engine_fixture(tmp_path)
    assert (
        engine.live_state().satellites[satellite_id]["satellite_mission_ingress"]["state"] == "not_configured"
    )
    engine.command(
        "provision_satellite_session", {"source_id": "mission_gateway", "session_reference": "sidecar-test"}
    )
    engine.command(
        "configure_technology_source",
        {
            "kind": "mission_websocket",
            "origin": "measurement_file",
            "satellite_ids": [satellite_id],
            "credential_ref": "vault:sidecar-test",
            "port": 0,
        },
    )
    engine.close_satellite_receivers()
    sidecar = engine.mission_receiver.store.path
    sidecar.write_bytes(b"intentionally truncated SQLite fixture")
    restored = NetworkEngine(
        store, path, source=IdleInputSource(), topology_id=engine.topology_id, security=security
    )
    try:
        failed = restored.mission_receiver.descriptor()
        assert failed["state"] == "unavailable" and failed["error_type"] == "DatabaseError"
        assert not failed["listener_active"] and restored.live_state().nodes
        assert sidecar.read_bytes() == b"intentionally truncated SQLite fixture"
    finally:
        restored.close_satellite_receivers()


def test_registered_satellite_receives_radio_frame_and_restart_keeps_evidence_without_listener(tmp_path):
    engine, topology, path, security, satellite_id = engine_fixture(tmp_path)
    try:
        result = engine.command(
            "provision_satellite_session",
            {"source_id": "mission_gateway", "session_reference": "orion-mission-session"},
        )
        assert result["credential_ref"] == "vault:orion-mission-session" and "token" not in result
        source_before = engine.technologies.source
        engine.command(
            "configure_technology_source",
            {
                "kind": "mission_websocket",
                "source_id": "mission_gateway",
                "origin": "measurement_file",
                "satellite_ids": [satellite_id],
                "credential_ref": result["credential_ref"],
                "port": 0,
            },
        )
        port = engine.mission_receiver.server.port
        token = security.vault.get_bytes("orion-mission-session").decode()
        packet = envelope(satellite_id=satellite_id)
        packet["sequence"] = 0
        with connect(
            f"ws://127.0.0.1:{port}/satellite/v1/frames",
            additional_headers={"Authorization": "Bearer " + token},
        ) as channel:
            channel.send(json.dumps(packet))
            response = json.loads(channel.recv(timeout=5))
        assert response["status"] == "decoded"
        engine.step()
        state = engine.live_state().to_dict()
        mission = state["satellites"][satellite_id]["satellite_mission_ingress"]
        assert mission["latest"]["satellite_id"] == satellite_id
        assert mission["latest"]["decode"]["payload"]["eps"]["batt_v"] == 7295
        assert mission["listener_active"] and mission["latest"]["decode"]["clock"]["qualified"] is False
        assert not state["satellites"][satellite_id]["physical_tracking_validated"]
        assert engine.technologies.source is source_before
        assert state["satellites"][satellite_id]["optical_payload_available"] is False
        changed = topology.get(engine.topology_id).node(satellite_id).telemetry["communicator"].copy()
        changed["mission"] = {**changed["mission"], "norad_catalog_id": 25544}
        with pytest.raises(ValueError, match="stop mission"):
            engine.command(
                "save_communicator",
                {"expected_version": topology.get(engine.topology_id).topology_version, "record": changed},
            )
        engine.command("configure_technology_source", {"kind": "stop_mission"})
        assert (
            engine.mission_receiver.descriptor()["latest"]
            and not engine.mission_receiver.descriptor()["listener_active"]
        )
        restored = NetworkEngine(
            topology, path, source=IdleInputSource(), topology_id=engine.topology_id, security=security
        )
        try:
            after = restored.live_state().to_dict()["technologies"]["satellite_mission_ingress"]
            assert (
                not after["listener_active"] and after["latest"]["decode"]["payload"]["eps"]["batt_v"] == 7295
            )
        finally:
            restored.close_satellite_receivers()
        assert token not in security.audit.path.read_text() and security.audit.verify()["valid"]
    finally:
        engine.close_satellite_receivers()


def test_radio_origin_and_mission_mismatch_cannot_be_admitted_as_other_satellite(tmp_path):
    engine, _, _, _, satellite_id = engine_fixture(tmp_path)
    try:
        engine.command(
            "provision_satellite_session",
            {"source_id": "mission_gateway", "session_reference": "mismatch-test"},
        )
        with pytest.raises(ValueError, match="integration mode"):
            engine.command(
                "configure_technology_source",
                {
                    "kind": "mission_websocket",
                    "origin": "physical",
                    "satellite_ids": [satellite_id],
                    "credential_ref": "vault:mismatch-test",
                    "port": 0,
                },
            )
        engine.command(
            "configure_technology_source",
            {
                "kind": "mission_websocket",
                "origin": "measurement_file",
                "satellite_ids": [satellite_id],
                "credential_ref": "vault:mismatch-test",
                "port": 0,
            },
        )
        bad = envelope(satellite_id=satellite_id)
        bad["sequence"] = 0
        bad["provenance"]["norad_cat_id"] = 25544
        response = engine.mission_receiver.store.submit(bad, time.time())
        assert response["status"] == "quarantined"
        assert engine.mission_receiver.store.latest()["decoded"] is None
    finally:
        engine.close_satellite_receivers()


def test_receiver_configuration_blocks_missing_binding_and_operator_provisioning(tmp_path):
    engine, _, _, security, satellite_id = engine_fixture(tmp_path)
    try:
        operator = security.grant_identity("service:operator-test", ["operator"], ["*"])
        with security.identity_scope(operator), pytest.raises(AuthorizationError):
            engine.command("provision_satellite_session", {"source_id": "mission_gateway"})
        engine.command(
            "provision_satellite_session",
            {"source_id": "mission_gateway", "session_reference": "test-mission"},
        )
        with pytest.raises(ValueError, match="registered"):
            engine.command(
                "configure_technology_source",
                {
                    "kind": "mission_websocket",
                    "source_id": "mission_gateway",
                    "satellite_ids": ["MISSING"],
                    "credential_ref": "vault:test-mission",
                    "port": 0,
                },
            )
        assert engine.mission_receiver.server is None
        with pytest.raises(ValueError):
            engine.command(
                "configure_technology_source",
                {
                    "kind": "mission_websocket",
                    "satellite_ids": [satellite_id],
                    "credential_ref": "env:LEGACY",
                    "port": 0,
                },
            )
        assert engine.mission_receiver.server is None
    finally:
        engine.close_satellite_receivers()
