"""Classical mission intake: durable receipts, honest decode states and identity."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import Event, Thread

import pytest

from quantum_autopilot.quantum_protocols.contracts import ProtocolError, canonical
from quantum_autopilot.quantum_protocols.mission_ingress import (
    FRAME_SCHEMA, MissionFrameStore, MissionWebSocketServer,
)
from quantum_autopilot.quantum_protocols.satellite_missions import (
    SATNOGS_BEACON0_PROFILE, UnknownMissionError, default_registry,
)

NOW = datetime(2026, 10, 8, 14, 0, tzinfo=timezone.utc).timestamp()


def frame_packet(sequence=0, *, reception_id=None, satellite_id="43804", frame=b"\x01\x02"):
    return {"schema": FRAME_SCHEMA, "source_id": "mission_gateway", "origin": "physical",
        "satellite_id": satellite_id, "mission_id": "suomi100", "sequence": sequence,
        "reception_id": reception_id or f"15146588:frame-{sequence}.bin",
        "received_at_utc": "2026-10-08T13:59:00Z", "station": {"id": "station1", "name": "Public station"},
        "frame_hex": frame.hex(), "decoder_profile": SATNOGS_BEACON0_PROFILE,
        "provenance": {"provider": "SatNOGS Network", "url": "https://network.satnogs.org/api/observations/15146588/",
            "retrieved_at_utc": "2026-10-08T13:59:30Z", "sha256": "1"*64,
            "observation_id": 15146588, "norad_cat_id": 43804, "observation_sha256": "2"*64}}


def decoded_fixture(mission_id, frame, *, profile):
    return {"mission_id": mission_id, "profile": profile, "decoder_id": "fixture", "decoder_version": "1",
        "payload": {"eps": {"timestamp_unix_s": 2000000000, "voltage_raw": frame[0]}},
        "clock": {"qualified": False, "onboard_timestamps": {"eps": {"unix_s": 2000000000,
                    "interpreted_utc": "2033-05-18T03:33:20+00:00"}}},
        "qualification": {"housekeeping_validated": False, "ogs_validated": False,
                          "qkd_validated": False, "physical_hardware_validated": False}}


def store(tmp_path, **kwargs):
    return MissionFrameStore(tmp_path / "classical.sqlite", decoder=decoded_fixture, **kwargs)


def test_real_public_frame_uses_registry_without_claiming_ogs_or_fixing_onboard_clock(tmp_path):
    frame = (Path(__file__).parent / "fixtures/satellite_public/suomi100-frame-20261008-135420.bin").read_bytes()
    intake = MissionFrameStore(tmp_path / "real-public.sqlite", origin="measurement_file",
                               allowed_satellite_ids=[43804], decoder=default_registry().decode_frame)
    packet = frame_packet(frame=frame)
    packet["origin"] = "measurement_file"
    packet["received_at_utc"] = "2026-10-08T13:54:20Z"
    result = intake.submit(packet, NOW)
    assert result["status"] == "decoded" and result["receipt"] == "accepted"
    latest = intake.latest()
    assert latest["envelope"] == packet and latest["received_at_utc"] == packet["received_at_utc"]
    assert latest["historical_replay"] and latest["origin"] == "measurement_file"
    assert latest["decoded"]["integrity"]["verified"] is True
    assert all(item["interpreted_utc"].startswith("2033-") for item in latest["decoded"]["clock"]["onboard_timestamps"].values())
    assert not any(latest["qualification"][name] for name in ("ogs_validated", "qkd_validated", "physical_hardware_validated"))
    assert result["frame_sha256"] == hashlib.sha256(frame).hexdigest()
    assert result["envelope_sha256"] == hashlib.sha256(canonical(packet).encode()).hexdigest()


def test_all_origins_share_same_decoder_and_preserve_published_reception(tmp_path):
    intake = store(tmp_path)
    for index, origin in enumerate(("physical", "measurement_file", "simulated")):
        packet = frame_packet(index)
        packet["origin"] = origin
        assert intake.submit(packet, NOW)["status"] == "decoded"
        latest = intake.latest()
        assert latest["origin"] == origin
        assert latest["fields_raw"]["eps"]["timestamp_unix_s"] == 2000000000
        assert latest["received_at_utc"] == "2026-10-08T13:59:00Z"
        assert latest["historical_replay"] == (origin == "measurement_file")
    old = frame_packet(3)
    old["received_at_utc"] = "2025-01-01T00:00:00Z"
    intake.submit(old, NOW)
    assert intake.latest()["origin"] == "physical" and intake.latest()["historical_replay"]


def test_duplicate_old_envelope_survives_restart_and_never_redecodes(tmp_path):
    intake = store(tmp_path, origin="physical")
    first, second = frame_packet(), frame_packet(1)
    original = intake.submit(first, NOW)
    intake.submit(second, NOW)
    reopened = MissionFrameStore(intake.path, origin="physical", decoder=lambda *_args, **_kwargs: pytest.fail("duplicate decoded again"))
    result = reopened.submit(copy.deepcopy(first), NOW + 100)
    assert result["status"] == "duplicate" and result["journal_id"] == original["journal_id"]
    assert result["envelope_sha256"] == original["envelope_sha256"]
    assert reopened.snapshot()["counters"] == {"accepted": 2, "duplicates": 1, "rejected": 0, "sequence_gaps": 0}
    assert reopened.snapshot()["counts"] == {"received": 0, "decoded": 2, "quarantined": 0, "unavailable": 0}


def test_conflicting_reception_and_sequence_preserve_original_and_allow_valid_gap(tmp_path):
    intake = store(tmp_path)
    packet = frame_packet()
    original = intake.submit(packet, NOW)
    conflict = copy.deepcopy(packet)
    conflict["frame_hex"] = "0304"
    with pytest.raises(ProtocolError, match="conflicting reception"):
        intake.submit(conflict, NOW)
    stale = frame_packet(reception_id="other", sequence=0)
    with pytest.raises(ProtocolError, match="sequence"):
        intake.submit(stale, NOW)
    intake.submit(frame_packet(3), NOW)
    assert intake.query()[0]["frame_sha256"] == original["frame_sha256"]
    assert intake.snapshot()["counters"]["sequence_gaps"] == 2
    assert intake.snapshot()["frame_count"] == 2


def test_sequences_belong_to_source_across_satellites_not_separate_streams(tmp_path):
    intake = store(tmp_path, allowed_satellite_ids=["43804", "other"])
    intake.submit(frame_packet(0), NOW)
    intake.submit(frame_packet(1, satellite_id="other"), NOW)
    intake.submit(frame_packet(2), NOW)
    assert intake.snapshot()["counters"]["sequence_gaps"] == 0
    with pytest.raises(ProtocolError, match="sequence"):
        intake.submit(frame_packet(2, reception_id="conflict-other", satellite_id="other"), NOW)
    assert intake.snapshot()["frame_count"] == 3


@pytest.mark.parametrize("mutate", [
    lambda p: p.pop("station"), lambda p: p.update(extra=True),
    lambda p: p.update(sequence=True), lambda p: p.update(sequence=-1),
    lambda p: p.update(frame_hex="abc"), lambda p: p.update(frame_hex="01 02"),
    lambda p: p.update(received_at_utc="2026-10-08T14:00:00"),
    lambda p: p.update(received_at_utc="2026-10-08T14:00:00+01:00"),
    lambda p: p.update(received_at_utc="2026-10-09T14:00:00Z"),
    lambda p: p["station"].update(latitude=1),
    lambda p: p["station"].update(latitude=float("nan"), longitude=1),
    lambda p: p["provenance"].update(qber=0),
    lambda p: p["provenance"].update(url="https://user:secret@example.org/"),
    lambda p: p["provenance"].update(url="https://example.org/?token=secret"),
    lambda p: p["provenance"].update(observation_sha256="wrong"),
    lambda p: p["provenance"].update(norad_cat_id=True),
])
def test_invalid_envelopes_never_enter_journal_or_decoder(tmp_path, mutate):
    intake = MissionFrameStore(tmp_path / "invalid.sqlite", decoder=lambda *_a, **_k: pytest.fail("invalid decoded"))
    packet = frame_packet()
    mutate(packet)
    with pytest.raises((ProtocolError, ValueError)):
        intake.submit(packet, NOW)
    assert intake.snapshot()["frame_count"] == 0 and intake.snapshot()["counters"]["rejected"] == 1


def test_source_origin_satellite_binding_and_frame_size_are_enforced(tmp_path):
    intake = store(tmp_path, origin="physical", allowed_satellite_ids=[43804], max_frame_bytes=2)
    for change in ({"source_id": "other"}, {"origin": "simulated"}, {"satellite_id": "other"}, {"frame_hex": "000102"}):
        packet = frame_packet()
        packet.update(change)
        with pytest.raises(ProtocolError):
            intake.submit(packet, NOW)
    assert intake.submit(frame_packet(satellite_id=43804), NOW)["status"] == "decoded"
    with pytest.raises(ProtocolError, match="another source"):
        MissionFrameStore(intake.path, source_id="other", origin="physical")


def test_unknown_disabled_or_profile_unavailable_remains_durable_until_explicit_retry(tmp_path):
    registry = default_registry()
    intake = MissionFrameStore(tmp_path / "unknown.sqlite", decoder=registry.decode_frame)
    packet = frame_packet()
    packet["mission_id"] = "unregistered"
    result = intake.submit(packet, NOW)
    assert result["status"] == "unavailable"
    assert intake.latest()["envelope"] == packet
    intake.decoder = decoded_fixture
    retried = intake.retry_decode(result["journal_id"])
    assert retried["status"] == "decoded" and retried["journal_id"] == result["journal_id"]
    assert intake.snapshot()["counters"]["accepted"] == 1 and intake.snapshot()["frame_count"] == 1
    assert intake.snapshot()["counts"]["unavailable"] == 0


@pytest.mark.parametrize("failure", ["checksum", "nonfinite", "derived", "qualification", "oversize"])
def test_decoder_failures_are_quarantined_without_false_qualification(tmp_path, failure):
    def decoder(*args, **kwargs):
        if failure == "checksum":
            raise ValueError("SECRET-FIXTURE checksum")
        result = decoded_fixture(*args, **kwargs)
        if failure == "nonfinite": result["payload"]["bad"] = float("nan")
        if failure == "derived": result["payload"]["secure_key"] = "SECRET-FIXTURE"
        if failure == "qualification": result["qualification"]["qkd_validated"] = True
        if failure == "oversize": result["payload"]["long"] = "x" * 4096
        return result
    intake = MissionFrameStore(tmp_path / "quarantine.sqlite", decoder=decoder, max_decoder_bytes=2048)
    packet = frame_packet()
    ack = intake.submit(packet, NOW)
    assert ack["status"] == "quarantined" and ack["receipt"] == "accepted"
    assert intake.latest()["envelope"] == packet and intake.latest()["decoded"] is None
    assert intake.snapshot()["counts"]["quarantined"] == 1
    assert "SECRET-FIXTURE" not in canonical(intake.snapshot())


def test_received_is_durable_before_decode_and_backpressure_does_not_consume_sequence(tmp_path):
    entered, release = Event(), Event()
    results = []
    def blocking(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return decoded_fixture(*args, **kwargs)
    intake = MissionFrameStore(tmp_path / "pending.sqlite", decoder=blocking, max_pending=1)
    worker = Thread(target=lambda: results.append(intake.submit(frame_packet(), NOW)))
    worker.start()
    try:
        assert entered.wait(3)
        assert intake.snapshot()["latest"]["state"] == "received"
        with sqlite3.connect(intake.path) as conn:
            assert conn.execute("SELECT state FROM mission_frames").fetchone() == ("received",)
        with pytest.raises(ProtocolError, match="full"):
            intake.submit(frame_packet(1), NOW)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and results[0]["status"] == "decoded"
    assert intake.submit(frame_packet(1), NOW)["status"] == "decoded"
    assert intake.snapshot()["counters"]["accepted"] == 2


def test_crash_during_decode_is_recovered_once_on_restart(tmp_path):
    def interrupted(*_a, **_k):
        raise KeyboardInterrupt()
    intake = MissionFrameStore(tmp_path / "interrupted.sqlite", decoder=interrupted)
    packet = frame_packet()
    with pytest.raises(KeyboardInterrupt):
        intake.submit(packet, NOW)
    assert intake.snapshot()["counts"]["received"] == 1
    restored = MissionFrameStore(intake.path, decoder=decoded_fixture)
    assert restored.snapshot()["counts"] == {"received": 0, "decoded": 1, "quarantined": 0, "unavailable": 0}
    assert restored.snapshot()["counters"]["accepted"] == 1
    assert restored.submit(packet, NOW)["status"] == "duplicate"


def test_quotas_preserve_frames_and_bound_query_with_pagination(tmp_path):
    intake = store(tmp_path, max_records=3, max_query=2)
    for index in range(3): intake.submit(frame_packet(index), NOW)
    with pytest.raises(ProtocolError, match="quota"):
        intake.submit(frame_packet(3), NOW)
    first = intake.frames.query()
    second = intake.frames.query(after_id=first[-1]["journal_id"])
    assert len(first) == 2 and len(second) == 1
    assert intake.frames.latest(satellite_id=43804)["sequence"] == 2
    with pytest.raises(ProtocolError): intake.query(limit=3)
    assert intake.snapshot()["frame_count"] == 3


def test_persistence_budget_can_keep_raw_frame_and_quarantine_decoder_output(tmp_path):
    packet = frame_packet()
    size = len(canonical(packet).encode())
    intake = store(tmp_path, max_persisted_bytes=size + 10)
    assert intake.submit(packet, NOW)["status"] == "quarantined"
    assert intake.latest()["reason_code"] == "storage_full" and intake.latest()["envelope"] == packet
    assert intake.snapshot()["persisted_bytes"] == size


def test_snapshots_are_cached_independent_and_latest_by_satellite_survives_restart(tmp_path, monkeypatch):
    intake = store(tmp_path, allowed_satellite_ids=["43804", "other"])
    intake.submit(frame_packet(), NOW)
    intake.submit(frame_packet(1, satellite_id="other"), NOW)
    intake.submit(frame_packet(2), NOW)
    restored = MissionFrameStore(intake.path, decoder=decoded_fixture, allowed_satellite_ids=["43804", "other"])
    monkeypatch.setattr(restored, "_connect", lambda: pytest.fail("snapshot queried SQLite"))
    snapshot = restored.snapshot()
    assert snapshot["latest_by_satellite"]["43804"]["sequence"] == 2
    assert snapshot["latest_by_satellite"]["other"]["sequence"] == 1
    snapshot["latest_by_satellite"]["43804"]["envelope"]["origin"] = "simulated"
    assert restored.snapshot()["latest_by_satellite"]["43804"]["origin"] == "physical"


def test_presentation_projection_is_cached_slim_bounded_and_keeps_full_evidence(tmp_path, monkeypatch):
    def large_decoder(*args, **kwargs):
        result = decoded_fixture(*args, **kwargs)
        result["payload"]["large_capture"] = "x" * 10000
        return result
    intake = MissionFrameStore(tmp_path / "presentation.sqlite", decoder=large_decoder)
    intake.submit(frame_packet(), NOW)
    complete = intake.latest()
    assert complete["envelope"]["frame_hex"] == "0102"
    assert len(complete["decoded"]["payload"]["large_capture"]) == 10000
    monkeypatch.setattr(intake, "_connect", lambda: pytest.fail("presentation queried SQLite"))
    presentation = intake.presentation_snapshot()
    latest = presentation["latest"]
    assert latest["status"] == "decoded" and latest["station"]["id"] == "station1"
    assert latest["received_at_utc"] == "2026-10-08T13:59:00Z"
    assert latest["presentation_limited"] and latest["details_available"]
    assert "decode.payload" in latest["presentation_omitted"]
    assert "frame_hex" not in canonical(presentation) and "fields_raw" not in canonical(presentation)
    assert len(canonical(latest).encode()) <= 4096
    latest["station"]["id"] = "mutated"
    assert intake.presentation_snapshot()["latest"]["station"]["id"] == "station1"


def test_default_registry_instance_is_local_and_disabled_decode_never_admitted(tmp_path):
    frame = (Path(__file__).parent / "fixtures/satellite_public/suomi100-frame-20261008-135420.bin").read_bytes()
    intake = MissionFrameStore(tmp_path / "default-registry.sqlite")
    intake.submit(frame_packet(frame=frame), NOW)
    intake.registry.set_enabled("suomi100", False)
    disabled = intake.submit(frame_packet(1, frame=frame), NOW)
    assert disabled["status"] == "unavailable"
    intake.registry.set_enabled("suomi100", True)
    assert intake.retry_decode(disabled["journal_id"])["status"] == "decoded"
    assert intake.snapshot()["frame_count"] == 2


def test_kiss_capture_provenance_is_preserved_and_norad_conflict_is_quarantined(tmp_path):
    packet = frame_packet()
    packet["provenance"].update(timestamp_origin="KISS timestamp extension", captured_at_utc="2026-10-08T13:58:59Z",
                                kiss_port=15, capture_sha256="3"*64)
    def mission_decoder(*args, **kwargs):
        result = decoded_fixture(*args, **kwargs)
        result["norad_cat_id"] = 43804
        return result
    intake = MissionFrameStore(tmp_path / "kiss.sqlite", decoder=mission_decoder)
    assert intake.submit(packet, NOW)["status"] == "decoded"
    assert intake.latest()["envelope"]["provenance"] == packet["provenance"]
    conflicting = copy.deepcopy(packet)
    conflicting.update(sequence=1, reception_id="other")
    conflicting["provenance"]["norad_cat_id"] = 25544
    result = intake.submit(conflicting, NOW)
    assert result["status"] == "quarantined" and result["reason_code"] == "mission_identity_mismatch"
    assert intake.latest()["envelope"] == conflicting and intake.latest()["decoded"] is None


@pytest.mark.parametrize("field,value", [("frame_sha256", "0" * 64), ("frame_bytes", 99), ("frame_bytes", True)])
def test_decoder_byte_metadata_cannot_contradict_the_journal(tmp_path, field, value):
    def contradicting(*args, **kwargs):
        result = decoded_fixture(*args, **kwargs)
        result[field] = value
        return result
    intake = MissionFrameStore(tmp_path / "contradiction.sqlite", decoder=contradicting)
    result = intake.submit(frame_packet(), NOW)
    assert result["status"] == "quarantined" and result["reason_code"] == "decoder_identity_mismatch"
    assert intake.latest()["envelope"]["frame_hex"] == "0102"


def mission_binding():
    return {"mission_id": "suomi100", "norad_catalog_id": 43804, "decoder_profile": SATNOGS_BEACON0_PROFILE}


@pytest.mark.parametrize("failure", ["mission", "profile", "missing_norad", "norad", "unbound"])
def test_explicit_mission_binding_quarantines_before_decoder_but_preserves_receipt(tmp_path, failure):
    packet = frame_packet()
    binding = mission_binding()
    if failure == "mission": packet["mission_id"] = "other"
    if failure == "profile": packet["decoder_profile"] = "other"
    if failure == "missing_norad": packet["provenance"].pop("norad_cat_id")
    if failure == "norad": packet["provenance"]["norad_cat_id"] = 25544
    if failure == "unbound": packet["satellite_id"] = "other"
    intake = MissionFrameStore(tmp_path / "bindings.sqlite", allowed_satellite_ids=[43804, "other"],
        allowed_mission_bindings={"43804": binding}, decoder=lambda *_a, **_k: pytest.fail("binding failure decoded"))
    result = intake.submit(packet, NOW)
    assert result["status"] == "quarantined" and result["receipt"] == "accepted"
    assert result["reason_code"] in {"mission_identity_mismatch", "mission_profile_mismatch", "mission_identity_missing", "mission_binding_missing"}
    assert intake.latest()["envelope"] == packet and intake.snapshot()["frame_count"] == 1


def test_mission_binding_is_explicit_without_inference_from_numeric_app_id(tmp_path):
    intake = store(tmp_path, allowed_satellite_ids=["25544"], allowed_mission_bindings={"25544": mission_binding()})
    packet = frame_packet(satellite_id="25544")
    assert intake.submit(packet, NOW)["status"] == "decoded"
    # Here "25544" is an app ID, explicitly bound to Suomi100/NORAD43804.
    assert intake.latest()["satellite_id"] == "25544"
    assert intake.latest()["envelope"]["provenance"]["norad_cat_id"] == 43804


def test_simulated_missing_identity_is_unqualified_and_decoder_still_must_match_binding(tmp_path):
    packet = frame_packet()
    packet["origin"] = "simulated"
    packet["provenance"].pop("norad_cat_id")
    intake = store(tmp_path, allowed_mission_bindings={"43804": mission_binding()})
    assert intake.submit(packet, NOW)["status"] == "decoded"
    assert intake.latest()["origin"] == "simulated" and intake.latest()["qualification"]["qkd_validated"] is False
    def wrong_identity(*args, **kwargs):
        result = decoded_fixture(*args, **kwargs)
        result["norad_cat_id"] = 25544
        return result
    intake.decoder = wrong_identity
    packet.update(sequence=1, reception_id="simulated-other")
    result = intake.submit(packet, NOW)
    assert result["status"] == "quarantined" and result["reason_code"] == "mission_identity_mismatch"


def test_other_journal_and_corrupt_hash_are_never_silently_reused(tmp_path):
    unrelated = tmp_path / "ogs.sqlite"
    with sqlite3.connect(unrelated) as conn: conn.execute("CREATE TABLE ogs_identity(id TEXT)")
    with pytest.raises(ProtocolError, match="own journal"):
        MissionFrameStore(unrelated)
    intake = store(tmp_path)
    intake.submit(frame_packet(), NOW)
    with sqlite3.connect(intake.path) as conn:
        conn.execute("UPDATE mission_frames SET envelope_sha256=?", ("0"*64,))
    with pytest.raises(ProtocolError, match="hash mismatch"):
        MissionFrameStore(intake.path, decoder=decoded_fixture)


@pytest.fixture
def secured(tmp_path):
    pytest.importorskip("websockets")
    from quantum_autopilot.security import SecurityRuntime
    runtime = SecurityRuntime(tmp_path / "security")
    identity = runtime.grant_identity("service:mission", ["operator"], ["source:mission_gateway/*"])
    token = runtime.issue_service_session(identity.subject)
    runtime.vault.put_bytes("mission-session", token.encode())
    intake = store(tmp_path, origin="physical", allowed_satellite_ids=[43804])
    server = MissionWebSocketServer(intake, lambda: NOW, security=runtime, credential_ref="vault:mission-session")
    try:
        yield runtime, identity, token, intake, server
    finally:
        server.close()


def test_websocket_authenticated_ingest_queries_and_audit_follow_validation(secured, monkeypatch):
    from websockets.sync.client import connect
    runtime, _, token, intake, server = secured
    headers = {"Authorization": "Bearer " + token}
    with connect(f"ws://127.0.0.1:{server.port}/satellite/v1/frames", additional_headers=headers) as channel:
        channel.send(canonical(frame_packet()))
        assert json.loads(channel.recv())["status"] == "decoded"
        channel.send('{"schema":"SECRET-FIXTURE","schema":"duplicated"}')
        rejected = json.loads(channel.recv())
        assert rejected == {"status": "rejected", "receipt": "not_admitted", "code": "invalid_packet"}
        bad = frame_packet(1, frame=b"x")
        bad["satellite_id"] = "spoofed"
        channel.send(canonical(bad))
        assert json.loads(channel.recv())["code"] == "wrong_source"
        with monkeypatch.context() as patch:
            patch.setattr(intake, "submit", lambda *_a, **_k: (_ for _ in ()).throw(sqlite3.OperationalError("SECRET-FIXTURE")))
            channel.send(canonical(frame_packet(1)))
            assert json.loads(channel.recv()) == {"status": "rejected", "receipt": "not_admitted", "code": "journal_unavailable"}
    with connect(f"ws://127.0.0.1:{server.port}/satellite/v1/status", additional_headers=headers) as channel:
        channel.send('{"query":"frames.latest","satellite_id":"43804"}')
        assert json.loads(channel.recv())["frame"]["sequence"] == 0
        channel.send('{"query":"frames.query","limit":1}')
        assert len(json.loads(channel.recv())["frames"]) == 1
        channel.send('{"query":"status"}')
        assert json.loads(channel.recv())["counts"]["decoded"] == 1
    rows = [json.loads(line) for line in runtime.audit.path.read_text().splitlines()]
    operations = [(row["operation"], row["result"]) for row in rows if row["operation"].startswith("mission.")]
    assert operations[:6] == [("mission.receive", "completed"), ("mission.ingest", "accepted"),
                             ("mission.receive", "completed"), ("mission.ingest", "rejected"),
                             ("mission.receive", "completed"), ("mission.ingest", "rejected")]
    assert intake.snapshot()["frame_count"] == 1
    audit = runtime.audit.path.read_text()
    assert token not in audit and "SECRET-FIXTURE" not in audit and runtime.audit.verify()["valid"]


def test_revoked_open_websocket_does_not_admit_later_frame(secured):
    from websockets.sync.client import connect
    runtime, identity, token, intake, server = secured
    with connect(f"ws://127.0.0.1:{server.port}/satellite/v1/frames", additional_headers={"Authorization": "Bearer " + token}) as channel:
        channel.send(canonical(frame_packet()))
        assert json.loads(channel.recv())["status"] == "decoded"
        runtime.revoke_identity(identity.subject)
        channel.send(canonical(frame_packet(1)))
        assert json.loads(channel.recv()) == {"status": "rejected", "code": "authentication_required"}
    assert intake.snapshot()["frame_count"] == 1


def test_resource_scope_anonymous_browser_and_unsafe_bind_rejected(secured):
    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect
    runtime, identity, token, intake, server = secured
    address = f"ws://127.0.0.1:{server.port}/satellite/v1/frames"
    with pytest.raises(InvalidStatus): connect(address)
    with pytest.raises(InvalidStatus): connect(address, additional_headers={"Authorization": "Bearer " + token}, origin="https://example.org")
    runtime.grant_identity(identity.subject, ["operator"], ["source:mission_gateway/status"])
    with pytest.raises(InvalidStatus): connect(address, additional_headers={"Authorization": "Bearer " + token})
    with pytest.raises(ProtocolError, match="local mission"):
        MissionWebSocketServer(intake, lambda: NOW, security=runtime, credential_ref="vault:mission-session", host="0.0.0.0")
    assert intake.snapshot()["frame_count"] == 0


def test_environment_credentials_or_absent_security_never_open_server(tmp_path):
    intake = store(tmp_path)
    with pytest.raises(ProtocolError, match="common backend"):
        MissionWebSocketServer(intake, lambda: NOW, security=None, credential_ref="env:MISSION_TOKEN")


def test_quarantined_frame_has_receipt_but_no_accepted_decode_audit(secured):
    from websockets.sync.client import connect
    runtime, _, token, intake, server = secured
    intake.decoder = lambda *_a, **_k: (_ for _ in ()).throw(ValueError("SECRET-FIXTURE"))
    with connect(f"ws://127.0.0.1:{server.port}/satellite/v1/frames", additional_headers={"Authorization": "Bearer " + token}) as channel:
        channel.send(canonical(frame_packet()))
        response = json.loads(channel.recv())
    assert response["status"] == "quarantined" and response["receipt"] == "accepted"
    rows = [json.loads(line) for line in runtime.audit.path.read_text().splitlines()]
    events = [row for row in rows if row["operation"] == "mission.ingest"]
    assert len(events) == 1 and events[0]["result"] == "rejected"
    assert events[0]["details"]["classical_message_accepted"] is True
    assert events[0]["details"]["accepted_events"] == 0
    assert "SECRET-FIXTURE" not in runtime.audit.path.read_text()
