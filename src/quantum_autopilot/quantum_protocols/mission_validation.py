"""Source/frozen reception qualification, isolated from all operator state."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
from threading import Thread


def validate_mission_reception(output):
    from PySide6.QtWidgets import QApplication
    from websockets.sync.client import connect
    from websockets.exceptions import InvalidStatus
    from ..console.widgets.satellite_telemetry import SatelliteTelemetryDialog
    from ..control_plane.topology import SimulatedTopologyStore
    from ..network_engine.engine import NetworkEngine
    from ..network_engine.institutional import brazil_institutions
    from ..network_engine.sources import IdleInputSource
    from ..security import SecurityRuntime
    from .satellite_gateway import GatewayOutbox, MissionGatewayClient, radio_envelope
    from .satellite_radio import receive_kiss_tcp
    from .topology import expand_global

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
    fixtures = (
        root / "satellite_public"
        if getattr(sys, "frozen", False)
        else root / "tests/fixtures/satellite_public"
    )
    metadata = json.loads((fixtures / "provenance.json").read_text(encoding="utf-8"))
    record = json.loads(
        (root / "configs/integrations/suomi100_public_satellite.json").read_text(encoding="utf-8")
    )
    satellite_id = record["node_id"]
    report = {
        "schema": "orion.satellite-mission-qualification.v1",
        "passed": False,
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "qt_platform": os.environ.get("QT_QPA_PLATFORM", "windows"),
        "origin": "measurement_file",
        "live_local_rf_reception": False,
        "physical_optical_qkd_qualified": False,
        "ground_station_transport": "emulated local TCP with preserved externally demodulated real frames",
        "cases": [],
        "frame_hashes": [],
        "error_type": None,
    }
    engine = dialog = None

    def check(name, passed, evidence=None):
        report["cases"].append({"case": name, "passed": bool(passed), "evidence": evidence})
        if not passed:
            raise RuntimeError("mission qualification failed")

    try:
        app = QApplication.instance() or QApplication([])
        report["qt_platform"] = app.platformName()
        from ..console.theme import install

        install(app)
        topology = expand_global(brazil_institutions())[0]
        path = output / "network.sqlite"
        store = SimulatedTopologyStore(path)
        store.initialize(topology)
        security = SecurityRuntime(output / ".security")
        engine = NetworkEngine(
            store, path, source=IdleInputSource(), topology_id=topology.topology_id, security=security
        )
        engine.command(
            "save_communicator",
            {"record": record, "expected_version": store.get(topology.topology_id).topology_version},
        )
        check(
            "radio_satellite_has_no_quantum_payload",
            not engine.live_state().satellites[satellite_id]["optical_payload_available"],
        )
        dialog = SatelliteTelemetryDialog()
        commands = []

        def dispatch(verb, payload):
            commands.append(verb)
            engine.command(verb, json.loads(payload))

        dialog.command.connect(dispatch)
        dialog.set_metrics({}, satellite_id=satellite_id, allowed=True, allowed_provision=True)
        dialog.mission_origin.setCurrentText("measurement_file")
        dialog.mission_provision.click()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        dialog.mission_port.setValue(port)
        dialog.mission_start.click()
        check(
            "native_buttons_provision_and_start",
            commands == ["provision_satellite_session", "configure_technology_source"]
            and engine.mission_receiver.server is not None,
        )
        address = f"ws://127.0.0.1:{engine.mission_receiver.server.port}/satellite/v1/frames"
        client = MissionGatewayClient(
            address, security=security, credential_ref="vault:orion-mission-session"
        )
        box = GatewayOutbox(output / "outbox.sqlite", source_id="mission_gateway", origin="measurement_file")
        wire = bytearray()

        def escape(value):
            return value.replace(b"\xdb", b"\xdb\xdd").replace(b"\xc0", b"\xdb\xdc")

        for source in metadata["fixtures"]:
            frame = (fixtures / source["file"]).read_bytes()
            digest = hashlib.sha256(frame).hexdigest()
            report["frame_hashes"].append(digest)
            check("original_frame_" + source["file"], digest == source["sha256"])
            stamp = int(
                datetime.fromisoformat(source["reception_time_utc"].replace("Z", "+00:00")).timestamp() * 1000
            )
            wire.extend(b"\xc0" + escape(b"\x09" + stamp.to_bytes(8, "big")) + b"\xc0")
            wire.extend(b"\xc0" + escape(b"\x00" + frame) + b"\xc0")
        wire.extend(b"\xc0\x00incomplete")
        status, station_errors = {}, []
        with socket.socket() as station:
            station.bind(("127.0.0.1", 0))
            station.listen()
            station.settimeout(5.0)

            def transmit():
                try:
                    with station.accept()[0] as channel:
                        for offset in range(0, len(wire), 17):
                            channel.sendall(wire[offset : offset + 17])
                except OSError as exc:
                    station_errors.append(type(exc).__name__)

            worker = Thread(target=transmit, daemon=True)
            worker.start()
            with closing(
                receive_kiss_tcp(
                    "127.0.0.1", station.getsockname()[1], max_frames=10, timeout_s=5.0, report=status
                )
            ) as observations:
                for index, observation in enumerate(observations):
                    packet = radio_envelope(
                        observation,
                        source_id="mission_gateway",
                        origin="measurement_file",
                        satellite_id=satellite_id,
                        mission_id="suomi100",
                        norad_id=43804,
                        profile=record["mission"]["decoder_profile"],
                        station_id="fixture-CIA_S4",
                        station_name="CIA_S4 public capture over emulated transport",
                        capture_id="qualification-public-15146588",
                        index=index,
                    )
                    packet["provenance"].update(url=metadata["fixtures"][index]["source_url"])
                    box.enqueue(packet)
            worker.join(timeout=5.0)
            check(
                "tcp_clock_and_partial_disconnect",
                not worker.is_alive()
                and not station_errors
                and status["frames_returned"] == 2
                and status["parser"]["partial_drops"] == 1,
                status,
            )
        sent = []

        def lose_response(body):
            sent.append(body)
            check("commit_before_lost_response", client.send(body)["status"] == "decoded")
            raise TimeoutError("intentional qualification interruption")

        try:
            box.flush(lose_response, limit=1)
        except TimeoutError:
            pass
        box = GatewayOutbox(box.path, source_id="mission_gateway", origin="measurement_file")

        def deliver(body):
            sent.append(body)
            return client.send(body)

        check(
            "identical_retry_without_duplicate",
            box.flush(deliver) == 2
            and sent[0] == sent[1]
            and engine.mission_receiver.store.snapshot()["frame_count"] == 2,
        )
        engine.step()
        mission = engine.live_state().satellites[satellite_id]["satellite_mission_ingress"]
        dialog.set_metrics(
            {}, satellite_id=satellite_id, allowed=True, allowed_provision=True, mission_ingress=mission
        )
        dialog.tabs.setCurrentWidget(dialog.mission)
        dialog.resize(1100, 720)
        dialog.show()
        app.processEvents()
        text = "\n".join(
            dialog.mission.item(r, c).text()
            for r in range(dialog.mission.rowCount())
            for c in range(dialog.mission.columnCount())
        )
        check(
            "native_screen_receives_raw_eps_com_obc",
            all(value in text for value in ("7285", "-117", "suomi100")),
        )
        check(
            "onboard_clock_and_units_unqualified",
            mission["latest"]["decode"]["clock"]["qualified"] is False
            and mission["latest"]["decode"]["units"]["qualified"] is False,
        )
        check(
            "no_tracking_or_key_fabrication",
            not engine.live_state().satellites[satellite_id]["physical_tracking_validated"]
            and mission["latest"]["qualification"]["qkd_validated"] is False,
        )
        dialog.grab().save(str(output / "native-mission-reception.png"))
        sample = engine.mission_receiver.store.latest()["envelope"].copy()
        sample["sequence"] = 2
        sample["reception_id"] += ":corrupted"
        corrupted = bytearray.fromhex(sample["frame_hex"])
        corrupted[15] ^= 1
        sample["frame_hex"] = corrupted.hex()
        sample["provenance"]["sha256"] = hashlib.sha256(corrupted).hexdigest()
        check("corrupted_crc_is_quarantined", client.send(json.dumps(sample))["status"] == "quarantined")
        sample["sequence"] = 3
        sample["reception_id"] += ":wrong-norad"
        sample["provenance"]["norad_cat_id"] = 25544
        check("wrong_satellite_is_quarantined", client.send(json.dumps(sample))["status"] == "quarantined")
        try:
            with connect(address, open_timeout=3):
                anonymous_blocked = False
        except InvalidStatus as exc:
            anonymous_blocked = exc.response.status_code == 401
        check("anonymous_rejected", anonymous_blocked)
        token = security.vault.get_bytes("orion-mission-session").decode("ascii")
        security.revoke_session(token)
        try:
            client.send(json.dumps(sample))
            revoked_blocked = False
        except InvalidStatus as exc:
            revoked_blocked = exc.response.status_code in (401, 403)
        check("revoked_session_rejected", revoked_blocked)
        dialog.mission_stop.click()
        check(
            "native_stop_keeps_receipts",
            not engine.mission_receiver.descriptor()["listener_active"]
            and engine.mission_receiver.store.snapshot()["frame_count"] == 4,
        )
        restored = NetworkEngine(
            store, path, source=IdleInputSource(), topology_id=topology.topology_id, security=security
        )
        try:
            check(
                "restart_keeps_receipts_without_listener",
                restored.mission_receiver.store.snapshot()["frame_count"] == 4
                and not restored.mission_receiver.descriptor()["listener_active"],
            )
        finally:
            restored.close_satellite_receivers()
        check(
            "authenticated_audit_without_secrets",
            security.audit.verify()["valid"] and token not in security.audit.path.read_text(),
        )
        report.update(
            passed=True,
            ingress=engine.mission_receiver.store.snapshot(),
            outbox=box.statistics(),
            topology_components=len(store.get(topology.topology_id).nodes),
        )
    except Exception as exc:
        report["error_type"] = type(exc).__name__
    finally:
        if engine is not None:
            engine.close_satellite_receivers()
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        (output / "report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
        )
    return report
