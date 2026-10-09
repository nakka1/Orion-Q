"""CLI paths use the same durable gateway and decoder, including local TCP."""

import hashlib
import json
import socket
from threading import Thread

import pytest

from quantum_autopilot.quantum_protocols.mission_ingress import MissionFrameStore
from quantum_autopilot.quantum_protocols.satellite_gateway import GatewayOutbox
from quantum_autopilot.quantum_protocols.satellite_gateway_cli import main
from quantum_autopilot.quantum_protocols.satellite_missions import (
    SATNOGS_BEACON0_PROFILE,
    CSP1_BEACON0_PROFILE,
)
from quantum_autopilot.security import SecurityRuntime
from tests.test_satellite_gateway import envelope


def kiss(data):
    return b"\xc0\x00" + data.replace(b"\xdb", b"\xdb\xdd").replace(b"\xc0", b"\xdb\xdc") + b"\xc0"


def arguments(tmp_path):
    return ["--outbox", str(tmp_path / "outbox.sqlite"), "--report", str(tmp_path / "report.json")]


def test_kiss_file_exact_snapshot_timestamp_and_idempotent_import(tmp_path, capsys):
    # Derived CSP92 test record, explicitly not a new physical capture.
    raw = bytes.fromhex(envelope()["frame_hex"])[:92]
    capture = tmp_path / "derived-csp92.kiss"
    capture.write_bytes(kiss(raw))
    args = arguments(tmp_path) + [
        "kiss-file",
        str(capture),
        "--captured-at",
        "2026-10-08T13:54:20Z",
        "--station-id",
        "fixture-station",
        "--station-name",
        "Derived archive fixture",
    ]
    assert main(args) == 0 and main(args) == 0
    box = GatewayOutbox(tmp_path / "outbox.sqlite", source_id="mission_gateway", origin="measurement_file")
    assert box.statistics() == {"pending": 1}
    packet = json.loads(box.pending()[0]["body"])
    assert packet["decoder_profile"] == CSP1_BEACON0_PROFILE
    assert packet["received_at_utc"] == "2026-10-08T13:54:20+00:00"
    assert packet["provenance"]["capture_sha256"] == hashlib.sha256(capture.read_bytes()).hexdigest()
    assert not list(tmp_path.glob("satellite-capture-*.kiss"))
    store = MissionFrameStore(
        tmp_path / "mission.sqlite", source_id="mission_gateway", origin="measurement_file"
    )
    assert box.flush(lambda body: store.submit(json.loads(body), 1791468000.0)) == 1
    assert store.latest()["decoded"]["payload"]["eps"]["batt_v"] == 7295
    assert "secret" not in capsys.readouterr().out


def test_kiss_tcp_preserves_timestamp_and_disconnect_partial_drop(tmp_path):
    raw = bytes.fromhex(envelope()["frame_hex"])
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]

        def produce():
            with server.accept()[0] as client:
                # The published UTC from the real frame is supplied separately
                # by this emulated ground-station transport.
                timestamp = 1791467660000
                client.sendall(
                    b"\xc0\x09" + timestamp.to_bytes(8, "big") + b"\xc0" + kiss(raw) + b"\xc0\x00partial"
                )

        worker = Thread(target=produce)
        worker.start()
        args = arguments(tmp_path) + [
            "--profile",
            SATNOGS_BEACON0_PROFILE,
            "kiss-tcp",
            "--port",
            str(port),
            "--capture-id",
            "emulated-public-replay",
            "--station-id",
            "fixture",
            "--station-name",
            "Emulated station",
            "--timeout",
            "2",
        ]
        assert main(args) == 0
        worker.join(timeout=3)
        assert not worker.is_alive()
    result = json.loads((tmp_path / "report.json").read_text())
    assert result["capture"]["termination"] == "eof"
    assert result["capture"]["parser"]["partial_drops"] == 1
    box = GatewayOutbox(tmp_path / "outbox.sqlite", source_id="mission_gateway", origin="measurement_file")
    packet = json.loads(box.pending()[0]["body"])
    assert packet["provenance"]["timestamp_origin"] == "kiss_timestamp_extension"
    assert packet["received_at_utc"] == "2026-10-08T13:54:20+00:00"


def test_archive_secret_is_rejected_before_durable_queue(tmp_path):
    packet = envelope()
    packet["provenance"]["password"] = "must-never-persist"
    archive = tmp_path / "malicious.jsonl"
    archive.write_text(json.dumps(packet) + "\n")
    with pytest.raises(ValueError):
        main(arguments(tmp_path) + ["import-jsonl", str(archive)])
    box = GatewayOutbox(tmp_path / "outbox.sqlite", source_id="mission_gateway", origin="measurement_file")
    assert box.statistics() == {} and b"must-never-persist" not in box.path.read_bytes()


def test_oversized_archive_line_and_capture_are_bounded(tmp_path):
    archive = tmp_path / "oversized.jsonl"
    archive.write_bytes(b"x" * 40000)
    with pytest.raises(ValueError, match="limit"):
        main(arguments(tmp_path) + ["import-jsonl", str(archive)])
    capture = tmp_path / "oversized.kiss"
    capture.write_bytes(b"x" * 4096)
    with pytest.raises(ValueError, match="quota"):
        main(
            arguments(tmp_path)
            + [
                "kiss-file",
                str(capture),
                "--captured-at",
                "2026-10-08T13:54:20Z",
                "--station-id",
                "fixture",
                "--station-name",
                "Fixture",
                "--max-bytes",
                "100",
            ]
        )
    assert not list(tmp_path.glob("satellite-capture-*.kiss"))


def test_follow_stopped_receiver_preserves_error_and_pending_frame(tmp_path):
    security = SecurityRuntime(tmp_path / ".security")
    security.vault.put_bytes("orion-mission-session", b"not-used-with-stopped-receiver")
    box = GatewayOutbox(tmp_path / "outbox.sqlite", source_id="mission_gateway", origin="measurement_file")
    box.enqueue(envelope())
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    args = arguments(tmp_path) + [
        "deliver",
        "--security-root",
        str(security.root),
        "--url",
        f"ws://127.0.0.1:{port}/satellite/v1/frames",
        "--follow-seconds",
        ".3",
        "--interval",
        ".1",
    ]
    assert main(args) == 2
    result = json.loads((tmp_path / "report.json").read_text())
    assert result["state"] == "unavailable" and result["termination"] == "deadline"
    assert result["transport_failures"] > 0 and result["last_error_type"] is not None
    assert result["acknowledged"] == 0 and result["pending"] == 1
