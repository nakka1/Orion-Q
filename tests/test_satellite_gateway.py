"""Public capture acquisition and uncertain delivery use the production pipeline."""

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from quantum_autopilot.quantum_protocols.mission_ingress import MissionFrameStore
from quantum_autopilot.quantum_protocols.satellite_gateway import GatewayOutbox, PublicFrameClient, canonical
from quantum_autopilot.quantum_protocols.satellite_missions import SATNOGS_BEACON0_PROFILE

FIXTURES = Path(__file__).parent / "fixtures/satellite_public"
NOW = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc).timestamp()


def envelope(name="135420", satellite_id="SUOMI100"):
    frame = (FIXTURES / ("suomi100-frame-20261008-" + name + ".bin")).read_bytes()
    return {
        "schema": "orion.satellite-frame.v1",
        "source_id": "mission_gateway",
        "origin": "measurement_file",
        "satellite_id": satellite_id,
        "mission_id": "suomi100",
        "reception_id": "15146588:data_20261008-" + name,
        "received_at_utc": "2026-10-08T13:" + ("54:20" if name == "135420" else "55:20") + "Z",
        "station": {"id": "5112", "name": "Estação CIA_S4"},
        "frame_hex": frame.hex(),
        "decoder_profile": SATNOGS_BEACON0_PROFILE,
        "provenance": {
            "provider": "SatNOGS Network",
            "norad_cat_id": 43804,
            "sha256": hashlib.sha256(frame).hexdigest(),
        },
    }


def test_lost_ack_restart_retransmits_identical_real_frame_without_duplicate(tmp_path):
    box = GatewayOutbox(tmp_path / "outbox.sqlite", source_id="mission_gateway", origin="measurement_file")
    box.enqueue(envelope())
    intake = MissionFrameStore(
        tmp_path / "mission.sqlite", source_id="mission_gateway", origin="measurement_file"
    )
    sent = []

    def lost_ack(body):
        sent.append(body)
        assert intake.submit(json.loads(body), NOW)["status"] == "decoded"
        raise TimeoutError("response lost after durable commit")

    with pytest.raises(TimeoutError):
        box.flush(lost_ack)
    restarted = GatewayOutbox(box.path, source_id="mission_gateway", origin="measurement_file")
    recovered = MissionFrameStore(intake.path, source_id="mission_gateway", origin="measurement_file")

    def retry(body):
        sent.append(body)
        return recovered.submit(json.loads(body), NOW)

    assert restarted.flush(retry) == 1 and not restarted.pending()
    assert sent[0] == sent[1]
    assert recovered.snapshot()["frame_count"] == 1
    assert recovered.latest()["decoded"]["payload"]["eps"]["batt_v"] == 7295
    assert recovered.latest()["decoded"]["clock"]["qualified"] is False
    assert recovered.latest()["qualification"]["qkd_validated"] is False
    assert recovered.latest()["historical_replay"]


def test_two_public_frames_and_corruption_have_distinct_durable_results(tmp_path):
    box = GatewayOutbox(tmp_path / "outbox.sqlite", source_id="mission_gateway", origin="measurement_file")
    intake = MissionFrameStore(
        tmp_path / "mission.sqlite", source_id="mission_gateway", origin="measurement_file"
    )
    for name in ("135420", "135520"):
        box.enqueue(envelope(name))
    bad = envelope()
    bad["reception_id"] += ":corrupt"
    raw = bytearray.fromhex(bad["frame_hex"])
    raw[15] ^= 1
    bad["frame_hex"] = raw.hex()
    bad["provenance"]["sha256"] = hashlib.sha256(raw).hexdigest()
    box.enqueue(bad)
    assert box.flush(lambda body: intake.submit(json.loads(body), NOW)) == 3
    assert intake.snapshot()["counts"]["decoded"] == 2
    assert intake.snapshot()["counts"]["quarantined"] == 1
    assert intake.latest()["state"] == "quarantined" and intake.latest()["decoded"] is None
    assert not box.pending()


def test_outbox_preserves_conflicts_identity_hash_and_quota(tmp_path):
    box = GatewayOutbox(
        tmp_path / "outbox.sqlite", source_id="mission_gateway", origin="measurement_file", max_bytes=4096
    )
    first = envelope()
    sequence = box.enqueue(first)
    assert box.enqueue(first) == sequence
    conflict = copy.deepcopy(first)
    conflict["station"]["name"] = "Different station"
    with pytest.raises(ValueError, match="conflicting"):
        box.enqueue(conflict)
    with pytest.raises(ValueError, match="another source"):
        GatewayOutbox(box.path, source_id="other", origin="measurement_file")
    first["reception_id"] += ":big"
    first["provenance"]["padding"] = "x" * 4096
    with pytest.raises(ValueError, match="quota"):
        box.enqueue(first)
    pending = box.pending()
    assert len(pending) == 1 and "Estação" in pending[0]["body"]
    with pytest.raises(ValueError, match="bound"):
        box.flush(lambda body: {"status": "decoded", "envelope_sha256": "0" * 64})
    assert len(box.pending()) == 1


def test_outbox_parallel_producers_allocate_unique_sequences(tmp_path):
    box = GatewayOutbox(tmp_path / "outbox.sqlite", source_id="mission_gateway", origin="measurement_file")

    def submit(index):
        packet = envelope()
        packet["reception_id"] += ":" + str(index)
        return box.enqueue(packet)

    with ThreadPoolExecutor(max_workers=4) as workers:
        sequences = list(workers.map(submit, range(16)))
    assert sorted(sequences) == list(range(16))
    assert len(box.pending()) == 16


class Response:
    def __init__(self, url, body):
        self.url, self.body, self.status = url, body, 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def geturl(self):
        return self.url

    def read(self, count):
        return self.body[:count]


class PublicFixture:
    def __init__(self, norad=43804):
        self.norad, self.calls = norad, []

    def open(self, request, timeout):
        url = request.full_url
        self.calls.append(url)
        if "/api/observations/" in url:
            frame_url = "https://s3.eu-central-1.wasabisys.com/satnogs-network/data_obs/2026/10/8/13/15146588/data_15146588_2026-10-08T13-54-20"
            raw = canonical(
                {
                    "id": 15146588,
                    "norad_cat_id": self.norad,
                    "station": 5112,
                    "station_name": "CIA_S4",
                    "start": "2026-10-08T13:51:12Z",
                    "end": "2026-10-08T13:59:04Z",
                    "demoddata": [{"payload_demod": frame_url}],
                }
            ).encode()
        else:
            raw = (FIXTURES / "suomi100-frame-20261008-135420.bin").read_bytes()
        assert "Authorization" not in request.headers
        return Response(url, raw)


def test_public_adapter_checks_actual_identity_and_preserves_receipt_and_origin():
    provider = PublicFixture()
    client = PublicFrameClient(opener=provider)
    args = dict(
        norad_id=43804, satellite_id="SUOMI100", source_id="mission_gateway", profile=SATNOGS_BEACON0_PROFILE
    )
    packet = client.acquire_observation(15146588, **args)[0]
    assert packet["origin"] == "measurement_file" and packet["received_at_utc"].startswith(
        "2026-10-08T13:54:20"
    )
    assert packet["provenance"]["retrieved_at_utc"] != packet["received_at_utc"]
    count = len(provider.calls)
    assert not client.acquire_observation(15146588, known_receptions=[packet["reception_id"]], **args)
    assert len(provider.calls) == count + 1  # Catalogue only; no repeated frame download.
    with pytest.raises(ValueError, match="identity"):
        PublicFrameClient(opener=PublicFixture(25544)).acquire_observation(15146588, **args)


@pytest.mark.parametrize(
    "url",
    [
        "http://network.satnogs.org/api/observations/1/",
        "https://127.0.0.1/api/observations/1/",
        "https://network.satnogs.org@evil.invalid/api/observations/1/",
        "https://network.satnogs.org/api/observations/1/?token=secret",
    ],
)
def test_public_adapter_refuses_unapproved_endpoints_before_network(url):
    provider = PublicFixture()
    with pytest.raises(ValueError):
        PublicFrameClient(opener=provider).get(url, kind="observation")
    assert not provider.calls
