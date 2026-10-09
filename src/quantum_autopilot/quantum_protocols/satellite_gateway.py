"""Public frame acquisition and durable, idempotent delivery to mission ingress.

RF demodulation is performed by the configured external ground station. Public
downloads remain measurement files; reception and retrieval clocks are separate.
"""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..security.http_boundary import strict_object


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("public satellite redirect refused")


def radio_envelope(
    record,
    *,
    source_id,
    origin,
    satellite_id,
    mission_id,
    norad_id,
    profile,
    station_id,
    station_name,
    capture_id,
    index,
    capture_sha256=None,
):
    """Package a demodulated observation without inventing calibrated telemetry."""
    if not isinstance(capture_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", capture_id):
        raise ValueError("bounded explicit capture identifier required")
    if type(index) is not int or index < 0:
        raise ValueError("capture index must be nonnegative")
    frame = record["frame_bytes"]
    digest = hashlib.sha256(frame).hexdigest()
    provenance = {
        "provider": "external KISS demodulator",
        "norad_cat_id": norad_id,
        "sha256": digest,
        "timestamp_origin": record["timestamp_origin"],
        "kiss_port": record["kiss_port"],
    }
    if record.get("captured_at_utc") is not None:
        provenance["captured_at_utc"] = record["captured_at_utc"]
    if capture_sha256 is not None:
        provenance["capture_sha256"] = capture_sha256
    return {
        "schema": "orion.satellite-frame.v1",
        "source_id": source_id,
        "origin": origin,
        "satellite_id": satellite_id,
        "mission_id": mission_id,
        "reception_id": f"{capture_id}:{index}:{digest[:16]}",
        "received_at_utc": record["received_at_utc"],
        "station": {"id": station_id, "name": station_name},
        "frame_hex": frame.hex(),
        "decoder_profile": profile,
        "provenance": provenance,
    }


class PublicFrameClient:
    """Bounded HTTPS GETs only; no third-party credentials or telemetry uploads."""

    def __init__(self, *, timeout=5.0, opener=None):
        if not 0 < timeout <= 20:
            raise ValueError("public acquisition timeout must be 0..20 seconds")
        self.timeout = timeout
        self.opener = opener or build_opener(NoRedirect())

    def get(self, url, *, kind):
        parts = urlsplit(url)
        valid = (
            parts.scheme == "https"
            and not parts.username
            and not parts.password
            and parts.port in (None, 443)
            and not parts.fragment
        )
        if kind == "observation":
            valid = (
                valid
                and parts.hostname == "network.satnogs.org"
                and bool(re.fullmatch(r"/api/observations/\d+/", parts.path))
                and not parts.query
            )
            limit = 512 * 1024
        elif kind == "frame":
            valid = (
                valid
                and parts.hostname == "s3.eu-central-1.wasabisys.com"
                and parts.path.startswith("/satnogs-network/data_obs/")
                and not parts.query
            )
            limit = 4096
        else:
            raise ValueError("unknown public satellite request kind")
        if not valid:
            raise ValueError("public satellite endpoint is outside the configured allowlist")
        request = Request(url, headers={"User-Agent": "ORION-Q-satellite-gateway/1.0", "Accept": "*/*"})
        with self.opener.open(request, timeout=self.timeout) as response:
            if response.status != 200 or response.geturl() != url:
                raise ValueError("public satellite response status or URL changed")
            body = response.read(limit + 1)
            if not body or len(body) > limit:
                raise ValueError("public satellite response size rejected")
        return body

    def acquire_observation(
        self, observation_id, *, norad_id, satellite_id, source_id, profile, known_receptions=()
    ):
        if type(observation_id) is not int or observation_id < 1 or type(norad_id) is not int or norad_id < 1:
            raise ValueError("positive integer observation and NORAD identifiers required")
        if norad_id != 43804:
            raise ValueError("the implemented public mission adapter is Suomi100/NORAD43804")
        url = f"https://network.satnogs.org/api/observations/{observation_id}/"
        raw = self.get(url, kind="observation")
        observation = strict_object(raw)
        if observation.get("id") != observation_id or observation.get("norad_cat_id") != norad_id:
            raise ValueError("public observation identity differs from the configured mission")
        demod = observation.get("demoddata")
        if not isinstance(demod, list) or len(demod) > 256:
            raise ValueError("public observation frame catalogue exceeds limits")
        results = []
        known = set(known_receptions)
        start = datetime.fromisoformat(observation["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(observation["end"].replace("Z", "+00:00"))
        for item in demod:
            frame_url = item.get("payload_demod") if isinstance(item, dict) else None
            if not isinstance(frame_url, str):
                raise ValueError("missing public demodulated frame URL")
            parts = urlsplit(frame_url)
            match = re.fullmatch(
                r"data_" + str(observation_id) + r"_(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})",
                Path(parts.path).name,
            )
            if not match:
                raise ValueError("public frame does not belong to the observation")
            reception_id = str(observation_id) + ":" + Path(parts.path).name
            if reception_id in known:
                continue
            received = datetime.fromisoformat(f"{match[1]}T{match[2]}:{match[3]}:{match[4]}+00:00")
            if not start <= received <= end:
                raise ValueError("public frame reception lies outside its observation window")
            frame = self.get(frame_url, kind="frame")
            retrieved = datetime.now(timezone.utc).isoformat()
            results.append(
                {
                    "schema": "orion.satellite-frame.v1",
                    "source_id": source_id,
                    "origin": "measurement_file",
                    "satellite_id": satellite_id,
                    "mission_id": "suomi100",
                    "reception_id": reception_id,
                    "received_at_utc": received.isoformat(),
                    "station": {
                        "id": str(observation.get("station", "")),
                        "name": observation.get("station_name", ""),
                    },
                    "frame_hex": frame.hex(),
                    "decoder_profile": profile,
                    "provenance": {
                        "provider": "SatNOGS Network",
                        "url": frame_url,
                        "retrieved_at_utc": retrieved,
                        "sha256": hashlib.sha256(frame).hexdigest(),
                        "observation_id": observation_id,
                        "norad_cat_id": norad_id,
                        "observation_sha256": hashlib.sha256(raw).hexdigest(),
                    },
                }
            )
        return results


class GatewayOutbox:
    """Persist before send; lost responses retain the exact envelope for retry."""

    def __init__(self, path, *, source_id, origin, max_bytes=16 * 1024 * 1024):
        if not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", source_id):
            raise ValueError("invalid gateway source identity")
        if origin not in ("physical", "measurement_file", "simulated"):
            raise ValueError("invalid gateway origin")
        if type(max_bytes) is not int or not 4096 <= max_bytes <= 1024 * 1024 * 1024:
            raise ValueError("gateway evidence quota must be 4 KiB..1 GiB")
        self.path, self.source_id, self.origin, self.max_bytes = Path(path), source_id, origin, max_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                "CREATE TABLE IF NOT EXISTS gateway_identity(source_id TEXT,origin TEXT);"
                "CREATE TABLE IF NOT EXISTS gateway_outbox(sequence INTEGER PRIMARY KEY, reception_id TEXT UNIQUE,"
                "body TEXT NOT NULL, body_sha256 TEXT NOT NULL, state TEXT NOT NULL, response TEXT);"
            )
            identity = connection.execute("SELECT source_id,origin FROM gateway_identity").fetchone()
            if identity is not None and tuple(identity) != (source_id, origin):
                raise ValueError("gateway journal belongs to another source/origin")
            if identity is None:
                connection.execute("INSERT INTO gateway_identity VALUES(?,?)", (source_id, origin))

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def enqueue(self, envelope):
        envelope = dict(envelope)
        if envelope.get("source_id") != self.source_id or envelope.get("origin") != self.origin:
            raise ValueError("gateway envelope identity/origin mismatch")
        if "sequence" in envelope:
            raise ValueError("sequence is allocated durably by the gateway")
        reception_id = envelope.get("reception_id")
        if not isinstance(reception_id, str) or not 1 <= len(reception_id) <= 192:
            raise ValueError("bounded explicit reception identifier required")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                previous = connection.execute(
                    "SELECT * FROM gateway_outbox WHERE reception_id=?", (reception_id,)
                ).fetchone()
                if previous is not None:
                    prior = json.loads(previous["body"])
                    sequence = prior.pop("sequence")
                    if canonical(prior) != canonical(envelope):
                        raise ValueError("conflicting reception cannot replace gateway evidence")
                    connection.commit()
                    return sequence
                size = connection.execute(
                    "SELECT coalesce(sum(length(cast(body AS blob))),0) FROM gateway_outbox"
                ).fetchone()[0]
                sequence = connection.execute(
                    "SELECT coalesce(max(sequence),-1)+1 FROM gateway_outbox"
                ).fetchone()[0]
                envelope["sequence"] = sequence
                body = canonical(envelope)
                if len(body.encode()) > 32 * 1024 or size + len(body.encode()) > self.max_bytes:
                    raise ValueError(
                        "gateway evidence quota reached; preserve journal and configure a new source identity/session"
                    )
                from .mission_ingress import validate_frame_envelope

                validate_frame_envelope(envelope, now_s=time.time())
                connection.execute(
                    "INSERT INTO gateway_outbox VALUES(?,?,?,?,?,NULL)",
                    (sequence, reception_id, body, hashlib.sha256(body.encode()).hexdigest(), "pending"),
                )
                connection.commit()
                return sequence
            except Exception:
                connection.rollback()
                raise

    def known_receptions(self):
        with closing(self._connect()) as connection:
            return {row[0] for row in connection.execute("SELECT reception_id FROM gateway_outbox")}

    def statistics(self):
        with closing(self._connect()) as connection:
            return dict(connection.execute("SELECT state,count(*) FROM gateway_outbox GROUP BY state"))

    def pending(self, limit=32):
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("gateway batch must be 1..256")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM gateway_outbox WHERE state='pending' ORDER BY sequence LIMIT ?", (limit,)
            ).fetchall()
        for row in rows:
            if hashlib.sha256(row["body"].encode()).hexdigest() != row["body_sha256"]:
                raise ValueError("gateway evidence hash mismatch; delivery blocked")
        return [dict(row) for row in rows]

    def flush(self, send, *, limit=32, deadline=None):
        delivered = 0
        for row in self.pending(limit):
            if deadline is not None and time.monotonic() >= deadline:
                break
            response = send(row["body"])  # A timeout leaves this exact record pending.
            if not isinstance(response, dict) or response.get("status") not in (
                "decoded",
                "quarantined",
                "unavailable",
                "duplicate",
            ):
                raise ValueError("mission receiver did not durably acknowledge the frame")
            if response.get("envelope_sha256") != row["body_sha256"]:
                raise ValueError("mission receipt is not bound to this envelope")
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "UPDATE gateway_outbox SET state='acknowledged',response=? WHERE sequence=? AND state='pending'",
                    (canonical(response), row["sequence"]),
                )
            delivered += 1
        return delivered


class MissionGatewayClient:
    def __init__(self, url, *, security, credential_ref):
        parts = urlsplit(url)
        if (
            parts.scheme != "ws"
            or parts.hostname not in ("127.0.0.1", "localhost", "::1")
            or parts.path != "/satellite/v1/frames"
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("gateway destination must be the local authenticated mission frames endpoint")
        if not isinstance(credential_ref, str) or not credential_ref.startswith("vault:"):
            raise ValueError("gateway credential must be a protected vault session reference")
        self.url, self.security, self.reference = url, security, credential_ref[6:]

    def send(self, body, *, timeout_s=9.0):
        from websockets.sync.client import connect

        if type(timeout_s) not in (float, int) or not 0 < timeout_s <= 9:
            raise ValueError("gateway send timeout must be 0..9 seconds")
        deadline = time.monotonic() + timeout_s
        token = self.security.vault.get_bytes(self.reference).decode("ascii")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("gateway credential lookup exceeded delivery deadline")
        with connect(
            self.url,
            additional_headers={"Authorization": "Bearer " + token},
            open_timeout=min(3.0, remaining),
            close_timeout=min(1.0, remaining),
            max_size=64 * 1024,
            compression=None,
        ) as channel:
            channel.send(body)
            return strict_object(
                channel.recv(timeout=min(5.0, max(0.0, deadline - time.monotonic()))).encode()
            )
