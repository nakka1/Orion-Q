"""Durable intake of classical mission frames, separate from OGS/QKD records.

The caller supplies frames already recovered by a ground-station receiver.
Nothing here captures RF, fabricates tracking/time tags, or operates hardware.
One store owns one source journal; snapshots are cached after durable commits.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from threading import Lock, Thread
from urllib.parse import parse_qsl, urlsplit

from .contracts import ProtocolError, canonical

FRAME_SCHEMA = "orion.satellite-frame.v1"
STATUS_SCHEMA = "orion.satellite-mission-status.v1"
MAX_MESSAGE_BYTES = 512 * 1024
MAX_REPLY_BYTES = 1024 * 1024
STATES = ("received", "decoded", "quarantined", "unavailable")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}\Z")
_HEX = re.compile(r"(?:[0-9a-fA-F]{2})+\Z")
_DERIVED = {"final_key", "secure_key", "secret_key", "skr_bps", "final_fidelity",
            "conditional_key_estimate_bits", "certified_secret_bits", "qber", "coincidences"}
_CREDENTIALS = {"authorization", "password", "secret", "token", "api_key", "access_token", "credential"}


def _identifier(value, name):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ProtocolError("invalid_envelope", f"{name}: bounded identifier required")
    return value


def _reception_id(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}", value) is None:
        raise ProtocolError("invalid_envelope", "reception_id: bounded identifier required")
    return value


def _satellite_id(value):
    if type(value) is int and 1 <= value <= 999999999:
        return str(value)
    return _identifier(value, "satellite_id")


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ProtocolError("invalid_envelope", f"{name}: bounded integer required")
    return value


def _finite(value, name, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ProtocolError("invalid_envelope", f"{name}: bounded finite number required")
    return float(value)


def _utc(value, name):
    if not isinstance(value, str) or len(value) > 64:
        raise ProtocolError("invalid_envelope", f"{name}: explicit UTC timestamp required")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
            raise ValueError()
        seconds = stamp.timestamp()
        if not 0 <= seconds <= 253402300799:
            raise ValueError()
    except (ValueError, OverflowError, OSError):
        raise ProtocolError("invalid_envelope", f"{name}: explicit UTC timestamp required") from None
    return stamp, seconds


def _text(value, name, maximum):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ProtocolError("invalid_envelope", f"{name}: bounded text required")
    return value


def _plain_json(value, *, depth=0, nodes=None):
    """Bound direct Python callers too; transport JSON has its own strict parser."""
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if depth > 16 or nodes[0] > 10000:
        raise ProtocolError("message_limit", "JSON structure limit exceeded")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 160:
                raise ProtocolError("invalid_envelope", "JSON field names must be bounded strings")
            if key.lower() in _DERIVED | _CREDENTIALS:
                raise ProtocolError("forbidden_field", "credentials and derived QKD fields are not mission telemetry")
            _plain_json(child, depth=depth + 1, nodes=nodes)
    elif isinstance(value, list):
        for child in value:
            _plain_json(child, depth=depth + 1, nodes=nodes)
    elif value is not None and type(value) not in (str, int, float, bool):
        raise ProtocolError("invalid_envelope", "plain JSON values required")
    elif type(value) is float and not math.isfinite(value):
        raise ProtocolError("invalid_envelope", "finite JSON values required")


def _public_url(value):
    _text(value, "provenance.url", 2048)
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ProtocolError("invalid_envelope", "public provenance URL required; no embedded credentials")
    if any(key.lower() in _CREDENTIALS for key, _ in parse_qsl(parsed.query)):
        raise ProtocolError("forbidden_field", "credentials do not belong in provenance URLs")


def validate_frame_envelope(envelope, *, now_s, max_frame_bytes=65536, max_future_skew_s=300.):
    required = {"schema", "source_id", "origin", "satellite_id", "mission_id", "sequence",
                "reception_id", "received_at_utc", "station", "frame_hex", "decoder_profile", "provenance"}
    if not isinstance(envelope, dict) or set(envelope) != required or envelope.get("schema") != FRAME_SCHEMA:
        raise ProtocolError("invalid_envelope", "satellite-frame v1 envelope fields required")
    _plain_json(envelope)
    _identifier(envelope["source_id"], "source_id")
    _satellite_id(envelope["satellite_id"])
    for name in ("mission_id", "decoder_profile"):
        _identifier(envelope[name], name)
    _reception_id(envelope["reception_id"])
    if envelope["origin"] not in ("physical", "measurement_file", "simulated"):
        raise ProtocolError("wrong_source", "explicit frame origin required")
    _integer(envelope["sequence"], "sequence", 0, 2**63 - 1)
    now = _finite(now_s, "now_s", 0, 253402300799)
    _, received = _utc(envelope["received_at_utc"], "received_at_utc")
    if received > now + max_future_skew_s:
        raise ProtocolError("future_reception", "reception timestamp exceeds the clock skew policy")
    station = envelope["station"]
    if (not isinstance(station, dict) or not {"id", "name"} <= set(station)
            or set(station) - {"id", "name", "latitude", "longitude"}):
        raise ProtocolError("invalid_envelope", "station identity and optional latitude/longitude required")
    _identifier(station["id"], "station.id")
    _text(station["name"], "station.name", 160)
    if ("latitude" in station) != ("longitude" in station):
        raise ProtocolError("invalid_envelope", "station coordinates require both latitude and longitude")
    if "latitude" in station:
        _finite(station["latitude"], "station.latitude", -90, 90)
        _finite(station["longitude"], "station.longitude", -180, 180)
    hexadecimal = envelope["frame_hex"]
    if (not isinstance(hexadecimal, str) or not 2 <= len(hexadecimal) <= max_frame_bytes * 2
            or not _HEX.fullmatch(hexadecimal)):
        raise ProtocolError("invalid_frame", "nonempty, bounded, contiguous hex bytes required")
    provenance = envelope["provenance"]
    if (not isinstance(provenance, dict) or "provider" not in provenance
            or set(provenance) - {"provider", "url", "retrieved_at_utc", "sha256", "observation_id",
                                 "norad_cat_id", "observation_sha256", "timestamp_origin",
                                 "captured_at_utc", "kiss_port", "capture_sha256"}):
        raise ProtocolError("invalid_envelope", "bounded provider provenance required")
    _text(provenance["provider"], "provenance.provider", 160)
    if "url" in provenance:
        _public_url(provenance["url"])
    for name in ("retrieved_at_utc", "captured_at_utc"):
        if name in provenance:
            _, retrieved = _utc(provenance[name], name)
            if retrieved > now + max_future_skew_s:
                raise ProtocolError("future_reception", "provenance timestamp exceeds the clock skew policy")
    for name in ("sha256", "observation_sha256", "capture_sha256"):
        if name in provenance and (not isinstance(provenance[name], str)
                                  or not re.fullmatch(r"[0-9a-fA-F]{64}", provenance[name])):
            raise ProtocolError("invalid_envelope", "provenance SHA-256 must be 64 hex characters")
    if "observation_id" in provenance:
        _integer(provenance["observation_id"], "observation_id", 0, 2**63 - 1)
    if "norad_cat_id" in provenance:
        _integer(provenance["norad_cat_id"], "norad_cat_id", 1, 999999999)
    if "kiss_port" in provenance:
        _integer(provenance["kiss_port"], "kiss_port", 0, 15)
    if "timestamp_origin" in provenance:
        _text(provenance["timestamp_origin"], "timestamp_origin", 64)
    # Provider hashes may cover HTTP/file artifacts, not the contained frame.
    # Keep them separate from the byte hash computed by this ingress.
    return bytes.fromhex(hexadecimal), received


class MissionFrameStore:
    """Source-bound classical journal. Identical envelopes are durable no-ops.

    ``frames.latest`` and ``frames.query`` are aliases for the corresponding
    store methods. A pending ``received`` row survives a crash and is decoded
    on restart. Unavailable/quarantined frames require explicit ``retry_decode``.
    """

    def __init__(self, path, *, source_id="mission_gateway", origin=None, allowed_satellite_ids=None,
                 allowed_mission_bindings=None,
                 decoder=None, max_frame_bytes=65536, max_pending=32, max_pending_bytes=8*1024*1024,
                 max_persisted_bytes=64*1024*1024, max_records=100000, max_query=128,
                 max_decoder_bytes=256*1024, max_future_skew_s=300., historical_after_s=300.,
                 recover_pending=True):
        self.source_id = _identifier(source_id, "source_id")
        if origin is not None and origin not in ("physical", "measurement_file", "simulated"):
            raise ProtocolError("wrong_source", "explicit mission origin required")
        self.origin = origin
        if allowed_satellite_ids is not None and not isinstance(allowed_satellite_ids, (list, tuple, set, frozenset)):
            raise ProtocolError("invalid_envelope", "satellite bindings require a bounded collection")
        self.allowed_satellite_ids = None if allowed_satellite_ids is None else frozenset(
            _satellite_id(value) for value in allowed_satellite_ids)
        if self.allowed_satellite_ids is not None and len(self.allowed_satellite_ids) > 256:
            raise ProtocolError("message_limit", "mission source may bind at most 256 satellites")
        self.allowed_mission_bindings = None
        if allowed_mission_bindings is not None:
            if not isinstance(allowed_mission_bindings, dict) or len(allowed_mission_bindings) > 256:
                raise ProtocolError("invalid_binding", "bounded explicit mission bindings required")
            bindings = {}
            for satellite_id, binding in allowed_mission_bindings.items():
                ident = _satellite_id(satellite_id)
                if (ident in bindings or not isinstance(binding, dict)
                        or set(binding) != {"mission_id", "norad_catalog_id", "decoder_profile"}):
                    raise ProtocolError("invalid_binding", "mission, NORAD and profile binding required")
                bindings[ident] = {"mission_id": _identifier(binding["mission_id"], "mission_id"),
                    "norad_catalog_id": _integer(binding["norad_catalog_id"], "norad_catalog_id", 0, 999999999),
                    "decoder_profile": _identifier(binding["decoder_profile"], "decoder_profile")}
            self.allowed_mission_bindings = bindings
        for name, value, high in (("max_frame_bytes", max_frame_bytes, 65536),
                ("max_pending", max_pending, 1024), ("max_pending_bytes", max_pending_bytes, 64*1024*1024),
                ("max_persisted_bytes", max_persisted_bytes, 1024*1024*1024),
                ("max_records", max_records, 1000000), ("max_query", max_query, 128),
                ("max_decoder_bytes", max_decoder_bytes, 256*1024)):
            _integer(value, name, 1, high)
            setattr(self, name, value)
        self.max_future_skew_s = _finite(max_future_skew_s, "max_future_skew_s", 0, 3600)
        self.historical_after_s = _finite(historical_after_s, "historical_after_s", 0, 86400)
        if type(recover_pending) is not bool:
            raise ProtocolError("invalid_envelope", "explicit recovery flag required")
        self.registry = None
        self._registry_lock = Lock()
        self.decoder = decoder if decoder is not None else self._decode_registered
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        self._active = set()
        self.frames = self
        with closing(self._connect()) as conn, conn:
            existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            allowed = {"mission_frames", "mission_streams", "mission_identity", "mission_stats", "sqlite_sequence"}
            if existing - allowed:
                raise ProtocolError("wrong_journal", "mission telemetry requires its own journal")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS mission_identity(source_id TEXT,origin TEXT,version INTEGER);
                CREATE TABLE IF NOT EXISTS mission_frames(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, reception_id TEXT UNIQUE NOT NULL,
                    satellite_id TEXT NOT NULL,mission_id TEXT NOT NULL,origin TEXT NOT NULL,
                    sequence INTEGER NOT NULL,envelope_sha256 TEXT NOT NULL,frame_sha256 TEXT NOT NULL,
                    envelope TEXT NOT NULL,received_at_utc TEXT NOT NULL,ingested_at_s REAL NOT NULL,
                    historical_replay INTEGER NOT NULL,state TEXT NOT NULL,reason_code TEXT,
                    decoded TEXT,decoded_sha256 TEXT,stored_bytes INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS mission_filter ON mission_frames(satellite_id,mission_id,id);
                CREATE INDEX IF NOT EXISTS mission_state ON mission_frames(state,id);
                CREATE UNIQUE INDEX IF NOT EXISTS mission_sequence ON mission_frames(sequence);
                CREATE TABLE IF NOT EXISTS mission_streams(
                    satellite_id TEXT,mission_id TEXT,origin TEXT,last_sequence INTEGER,
                    PRIMARY KEY(satellite_id,mission_id,origin));
                CREATE TABLE IF NOT EXISTS mission_stats(
                    id INTEGER PRIMARY KEY CHECK(id=1),accepted INTEGER DEFAULT 0,duplicates INTEGER DEFAULT 0,
                    rejected INTEGER DEFAULT 0,sequence_gaps INTEGER DEFAULT 0,total INTEGER DEFAULT 0,
                    received INTEGER DEFAULT 0,decoded INTEGER DEFAULT 0,quarantined INTEGER DEFAULT 0,
                    unavailable INTEGER DEFAULT 0,pending_bytes INTEGER DEFAULT 0,persisted_bytes INTEGER DEFAULT 0);
                INSERT OR IGNORE INTO mission_stats(id) VALUES(1);
            """)
            identity = conn.execute("SELECT source_id,origin,version FROM mission_identity").fetchone()
            if identity and tuple(identity) != (self.source_id, self.origin, 1):
                raise ProtocolError("wrong_source", "persistent mission journal belongs to another source/origin")
            if not identity:
                conn.execute("INSERT INTO mission_identity VALUES(?,?,1)", (self.source_id, self.origin))
            conn.commit()
            self._refresh_cache(conn)
        if recover_pending:
            self.decode_pending(limit=self.max_pending)

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5.)
        conn.row_factory = sqlite3.Row
        return conn

    def _decode_registered(self, mission_id, frame, *, profile):
        with self._registry_lock:
            if self.registry is None:
                from .satellite_missions import default_registry
                self.registry = default_registry()
            registry = self.registry
        return registry.decode_frame(mission_id, frame, profile=profile)

    def _record(self, row):
        if row is None:
            return None
        envelope_text, decoded_text = row["envelope"], row["decoded"]
        if hashlib.sha256(envelope_text.encode()).hexdigest() != row["envelope_sha256"]:
            raise ProtocolError("journal_integrity", "mission envelope hash mismatch")
        envelope = json.loads(envelope_text)
        if hashlib.sha256(bytes.fromhex(envelope["frame_hex"])).hexdigest() != row["frame_sha256"]:
            raise ProtocolError("journal_integrity", "mission byte hash mismatch")
        if decoded_text is not None and hashlib.sha256(decoded_text.encode()).hexdigest() != row["decoded_sha256"]:
            raise ProtocolError("journal_integrity", "mission decode hash mismatch")
        decoded = json.loads(decoded_text) if decoded_text is not None else None
        return {"journal_id": row["id"], "state": row["state"], "reason_code": row["reason_code"],
                "source_id": self.source_id, "origin": row["origin"], "satellite_id": row["satellite_id"],
                "mission_id": row["mission_id"], "sequence": row["sequence"],
                "reception_id": row["reception_id"], "received_at_utc": row["received_at_utc"],
                "ingested_at_utc": datetime.fromtimestamp(row["ingested_at_s"], timezone.utc).isoformat(),
                "historical_replay": bool(row["historical_replay"]),
                "reception_time_scope": "source-reported reception; separate from onboard clock",
                "envelope_sha256": row["envelope_sha256"], "frame_sha256": row["frame_sha256"],
                "envelope": envelope, "decoded": decoded,
                "fields_raw": decoded.get("payload") if decoded else None,
                "qualification": {"classical_only": True, "decoded": row["state"] == "decoded",
                    "ogs_validated": False, "qkd_validated": False,
                    "physical_hardware_validated": False, "local_rf_acquisition_validated": False}}

    def _refresh_cache(self, conn, changed_id=None):
        stats = dict(conn.execute("SELECT * FROM mission_stats WHERE id=1").fetchone())
        if not hasattr(self, "_latest_by_satellite"):
            rows = conn.execute("""SELECT f.* FROM mission_frames f JOIN
                (SELECT max(id) AS id FROM mission_frames GROUP BY satellite_id) last
                ON f.id=last.id ORDER BY f.id DESC LIMIT 256""")
            self._latest_by_satellite = {row["satellite_id"]: self._record(row) for row in rows}
            self._latest = self._record(conn.execute("SELECT * FROM mission_frames ORDER BY id DESC LIMIT 1").fetchone())
            self._presentation_by_satellite = {ident: self._present(record) for ident, record in self._latest_by_satellite.items()}
            self._presentation_latest = self._present(self._latest) if self._latest else None
        elif changed_id is not None:
            changed = self._record(conn.execute("SELECT * FROM mission_frames WHERE id=?", (changed_id,)).fetchone())
            previous = self._latest_by_satellite.get(changed["satellite_id"])
            if previous is None or previous["journal_id"] <= changed_id:
                self._latest_by_satellite[changed["satellite_id"]] = changed
                self._presentation_by_satellite[changed["satellite_id"]] = self._present(changed)
            if self._latest is None or self._latest["journal_id"] <= changed_id:
                self._latest = changed
                self._presentation_latest = self._presentation_by_satellite[changed["satellite_id"]]
        latest = self._latest
        self._snapshot = {"schema": STATUS_SCHEMA, "source_id": self.source_id, "origin": self.origin,
            "state": latest["state"] if latest else "unavailable", "latest": latest,
            "latest_by_satellite": self._latest_by_satellite,
            "reason_code": latest["reason_code"] if latest else "no_frames_received",
            "counters": {name: stats[name] for name in ("accepted", "duplicates", "rejected", "sequence_gaps")},
            "counts": {name: stats[name] for name in STATES}, "frame_count": stats["total"],
            "pending_bytes": stats["pending_bytes"], "persisted_bytes": stats["persisted_bytes"],
            "limits": {"pending_frames": self.max_pending, "persisted_bytes": self.max_persisted_bytes,
                       "frame_bytes": self.max_frame_bytes, "query_frames": self.max_query},
            "classical_only": True, "ogs_validated": False, "qkd_validated": False,
            "physical_hardware_validated": False, "physical_actuation": False}
        self._presentation_snapshot = {key: value for key, value in self._snapshot.items()
                                       if key not in ("latest", "latest_by_satellite")}
        self._presentation_snapshot.update(latest=self._presentation_latest,
            latest_by_satellite=self._presentation_by_satellite,
            accepted=stats["accepted"], quarantined=stats["quarantined"], rejected=stats["rejected"],
            unavailable=stats["unavailable"], last_received_at_utc=latest["received_at_utc"] if latest else None)

    @staticmethod
    def _present(record):
        envelope = record["envelope"]
        preview = {name: record[name] for name in ("journal_id", "satellite_id", "mission_id", "source_id",
            "origin", "sequence", "reception_id", "received_at_utc", "ingested_at_utc", "historical_replay", "frame_sha256", "envelope_sha256", "reason_code")}
        preview.update(status=record["state"], station=envelope["station"], provenance=envelope["provenance"],
                       qualification=record["qualification"], decode=None, presentation_limited=False)
        decoded = record["decoded"]
        if decoded is not None:
            preview["decode"] = {key: decoded[key] for key in ("header", "payload", "integrity", "clock",
                "qualification", "units", "decoder_id", "decoder_version", "mission_id", "profile") if key in decoded}
        omitted = []
        # Omit complete bulky sections, never shorten scientific values or
        # substitute units/time. Full evidence remains in frames.latest/query.
        for section in ("units", "header", "payload", "clock", "integrity", "qualification"):
            if len(canonical(preview).encode()) <= 4096:
                break
            if preview["decode"] is not None and section in preview["decode"]:
                preview["decode"].pop(section)
                omitted.append("decode." + section)
        if len(canonical(preview).encode()) > 3500 and preview["decode"] is not None:
            preview["decode"] = None
            omitted.append("decode")
        for field in ("url", "provider"):
            if len(canonical(preview).encode()) <= 3900:
                break
            if field in preview["provenance"]:
                preview["provenance"] = {key: value for key, value in preview["provenance"].items() if key != field}
                omitted.append("provenance." + field)
        if omitted:
            preview.update(presentation_limited=True, presentation_omitted=omitted, details_available=True)
        return preview

    def _check_mission_binding(self, envelope):
        if self.allowed_mission_bindings is None:
            return
        binding = self.allowed_mission_bindings.get(_satellite_id(envelope["satellite_id"]))
        if binding is None:
            raise ProtocolError("mission_binding_missing", "source has no explicit binding for this app satellite")
        if envelope["mission_id"] != binding["mission_id"]:
            raise ProtocolError("mission_identity_mismatch", "frame mission differs from configured app binding")
        if envelope["decoder_profile"] != binding["decoder_profile"]:
            raise ProtocolError("mission_profile_mismatch", "frame profile differs from configured app binding")
        expected = binding["norad_catalog_id"]
        supplied = envelope["provenance"].get("norad_cat_id")
        if expected and supplied is None and envelope["origin"] in ("physical", "measurement_file"):
            raise ProtocolError("mission_identity_missing", "received mission frame requires explicit NORAD provenance")
        if expected and supplied is not None and supplied != expected:
            raise ProtocolError("mission_identity_mismatch", "frame NORAD differs from configured app binding")

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self._snapshot)

    def presentation_snapshot(self):
        """Bounded UI projection; no frame hex, duplicate fields, SQLite or decode."""
        with self.lock:
            return copy.deepcopy(self._presentation_snapshot)

    def _rejected(self):
        with self.lock, closing(self._connect()) as conn, conn:
            conn.execute("UPDATE mission_stats SET rejected=rejected+1 WHERE id=1")
            conn.commit()
            self._refresh_cache(conn)

    def submit(self, envelope, now_s):
        try:
            frame, received_s = validate_frame_envelope(envelope, now_s=now_s,
                max_frame_bytes=self.max_frame_bytes, max_future_skew_s=self.max_future_skew_s)
            body = canonical(envelope)
            size = len(body.encode())
            if size > MAX_MESSAGE_BYTES:
                raise ProtocolError("message_limit", "mission envelope exceeds transport limit")
            if (envelope["source_id"] != self.source_id
                    or self.origin is not None and envelope["origin"] != self.origin
                    or self.allowed_satellite_ids is not None and _satellite_id(envelope["satellite_id"]) not in self.allowed_satellite_ids):
                raise ProtocolError("wrong_source", "frame source, origin or satellite binding rejected")
            sha = hashlib.sha256(body.encode()).hexdigest()
            byte_hash = hashlib.sha256(frame).hexdigest()
            satellite, mission, origin = _satellite_id(envelope["satellite_id"]), envelope["mission_id"], envelope["origin"]
            with self.lock, closing(self._connect()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute("SELECT * FROM mission_frames WHERE reception_id=?", (envelope["reception_id"],)).fetchone()
                if existing:
                    self._record(existing)
                    if existing["envelope_sha256"] != sha:
                        raise ProtocolError("replay", "conflicting reception identity; original frame preserved")
                    conn.execute("UPDATE mission_stats SET duplicates=duplicates+1 WHERE id=1")
                    conn.commit()
                    self._refresh_cache(conn)
                    return {"status": "duplicate", "receipt": "duplicate", "admission": existing["state"],
                            "journal_id": existing["id"], "sequence": existing["sequence"],
                            "envelope_sha256": sha, "frame_sha256": byte_hash}
                # The gateway outbox allocates one sequence for the source,
                # even when successive frames belong to different satellites.
                stream = conn.execute("SELECT sequence FROM mission_frames ORDER BY sequence DESC LIMIT 1").fetchone()
                if stream and envelope["sequence"] <= stream[0]:
                    raise ProtocolError("replay", "stale/conflicting source sequence; journal preserved")
                stats = conn.execute("SELECT * FROM mission_stats WHERE id=1").fetchone()
                if satellite not in self._latest_by_satellite and len(self._latest_by_satellite) >= 256:
                    raise ProtocolError("backpressure", "mission source satellite catalogue is full")
                if stats["received"] >= self.max_pending or stats["pending_bytes"] + size > self.max_pending_bytes:
                    raise ProtocolError("backpressure", "mission decode intake full; retry the identical envelope later")
                if stats["total"] >= self.max_records or stats["persisted_bytes"] + size > self.max_persisted_bytes:
                    raise ProtocolError("storage_full", "mission evidence quota reached; existing frames preserved")
                gap = max(0, envelope["sequence"] - stream[0] - 1) if stream else 0
                historical = origin == "measurement_file" or now_s - received_s > self.historical_after_s
                result = conn.execute("""INSERT INTO mission_frames(reception_id,satellite_id,mission_id,origin,
                    sequence,envelope_sha256,frame_sha256,envelope,received_at_utc,ingested_at_s,
                    historical_replay,state,stored_bytes) VALUES(?,?,?,?,?,?,?,?,?,?,?,'received',?)""",
                    (envelope["reception_id"], satellite, mission, origin, envelope["sequence"], sha,
                     byte_hash, body, envelope["received_at_utc"], now_s, int(historical), size))
                journal_id = result.lastrowid
                conn.execute("""INSERT INTO mission_streams VALUES(?,?,?,?) ON CONFLICT(satellite_id,mission_id,origin)
                    DO UPDATE SET last_sequence=excluded.last_sequence""", (satellite, mission, origin, envelope["sequence"]))
                conn.execute("""UPDATE mission_stats SET accepted=accepted+1,total=total+1,received=received+1,
                    sequence_gaps=sequence_gaps+?,pending_bytes=pending_bytes+?,persisted_bytes=persisted_bytes+? WHERE id=1""",
                    (gap, size, size))
                conn.commit()
                self._refresh_cache(conn, journal_id)
        except (ProtocolError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
            self._rejected()
            raise
        return self._decode_received(journal_id)

    def _decode_received(self, journal_id):
        with self.lock, closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM mission_frames WHERE id=?", (journal_id,)).fetchone()
            record = self._record(row)
            if record is None:
                raise ProtocolError("unknown_frame", "mission frame not found")
            if row["state"] != "received" or journal_id in self._active:
                return self._ack(record)
            self._active.add(journal_id)
        decoded_text = None
        decoded_hash = None
        code = None
        try:
            envelope = record["envelope"]
            try:
                self._check_mission_binding(envelope)
                frame = bytes.fromhex(envelope["frame_hex"])
                decoded = self.decoder(envelope["mission_id"], frame,
                                       profile=envelope["decoder_profile"])
                if not isinstance(decoded, dict):
                    raise ValueError()
                _plain_json(decoded)
                if (decoded.get("mission_id", envelope["mission_id"]) != envelope["mission_id"]
                        or decoded.get("profile", envelope["decoder_profile"]) != envelope["decoder_profile"]):
                    raise ProtocolError("decoder_identity_mismatch", "decoder mission/profile differs from the frame")
                if ("frame_sha256" in decoded and decoded["frame_sha256"] != record["frame_sha256"]
                        or "frame_bytes" in decoded and (type(decoded["frame_bytes"]) is not int or decoded["frame_bytes"] != len(frame))):
                    raise ProtocolError("decoder_identity_mismatch", "decoder byte identity differs from journal evidence")
                decoder_norad = decoded.get("norad_cat_id")
                provider_norad = envelope["provenance"].get("norad_cat_id")
                if decoder_norad is not None:
                    _integer(decoder_norad, "decoder norad_cat_id", 1, 999999999)
                if decoder_norad is not None and provider_norad is not None and decoder_norad != provider_norad:
                    raise ProtocolError("mission_identity_mismatch", "decoder and provider satellite identities differ")
                bound = None if self.allowed_mission_bindings is None else self.allowed_mission_bindings.get(record["satellite_id"])
                if bound and bound["norad_catalog_id"] and decoder_norad is not None and decoder_norad != bound["norad_catalog_id"]:
                    raise ProtocolError("mission_identity_mismatch", "decoder satellite identity differs from configured binding")
                qualification = decoded.get("qualification", {})
                if not isinstance(qualification, dict) or any(qualification.get(name, False) is not False
                        for name in ("ogs_validated", "qkd_validated", "physical_hardware_validated")):
                    raise ValueError()
                decoded_text = canonical(decoded)
                if len(decoded_text.encode()) > self.max_decoder_bytes:
                    raise ProtocolError("decoder_limit", "decoder output exceeds the configured bound")
                decoded_hash = hashlib.sha256(decoded_text.encode()).hexdigest()
                state = "decoded"
            except Exception as exc:
                unavailable = isinstance(exc, ImportError) or type(exc).__name__ in {
                    "UnknownMissionError", "DisabledMissionError", "UnknownProfileError"}
                state = "unavailable" if unavailable else "quarantined"
                failure_code = getattr(exc, "code", None)
                code = (failure_code if failure_code in {"unknown_mission", "disabled_mission", "unknown_profile"}
                        else "decoder_unavailable") if unavailable else (
                    failure_code if failure_code in {"decoder_limit", "mission_identity_mismatch", "decoder_identity_mismatch",
                        "mission_identity_missing", "mission_binding_missing", "mission_profile_mismatch",
                        "invalid_frame_type", "invalid_frame_length", "unsupported_beacon", "csp_profile_mismatch",
                        "invalid_checksum", "decoder_failed"} else "decode_failed")
                decoded_text = decoded_hash = None
            with self.lock, closing(self._connect()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                current = conn.execute("SELECT * FROM mission_frames WHERE id=?", (journal_id,)).fetchone()
                if current["state"] == "received":
                    stats = conn.execute("SELECT * FROM mission_stats WHERE id=1").fetchone()
                    extra = len(decoded_text.encode()) if decoded_text is not None else 0
                    if stats["persisted_bytes"] + extra > self.max_persisted_bytes:
                        state, code, decoded_text, decoded_hash, extra = "quarantined", "storage_full", None, None, 0
                    conn.execute("UPDATE mission_frames SET state=?,reason_code=?,decoded=?,decoded_sha256=?,stored_bytes=stored_bytes+? WHERE id=?",
                                 (state, code, decoded_text, decoded_hash, extra, journal_id))
                    conn.execute(f"UPDATE mission_stats SET received=received-1,{state}={state}+1,pending_bytes=pending_bytes-?,persisted_bytes=persisted_bytes+? WHERE id=1",
                                 (current["stored_bytes"], extra))
                conn.commit()
                self._refresh_cache(conn, journal_id)
                current = self._record(conn.execute("SELECT * FROM mission_frames WHERE id=?", (journal_id,)).fetchone())
            return self._ack(current)
        finally:
            with self.lock:
                self._active.discard(journal_id)

    @staticmethod
    def _ack(record):
        return {"status": record["state"], "receipt": "accepted", "admission": record["state"],
                "journal_id": record["journal_id"], "sequence": record["sequence"],
                "envelope_sha256": record["envelope_sha256"], "frame_sha256": record["frame_sha256"],
                "reason_code": record["reason_code"]}

    def decode_pending(self, *, limit=None):
        limit = min(8, self.max_pending) if limit is None else limit
        _integer(limit, "limit", 1, self.max_pending)
        with self.lock, closing(self._connect()) as conn:
            pending = [row[0] for row in conn.execute("SELECT id FROM mission_frames WHERE state='received' ORDER BY id LIMIT ?", (limit,))]
        return [self._decode_received(journal_id) for journal_id in pending]

    def retry_decode(self, journal_id):
        """Explicit retry after decoder/profile becomes available, never new reception."""
        _integer(journal_id, "journal_id", 1, 2**63-1)
        with self.lock, closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM mission_frames WHERE id=?", (journal_id,)).fetchone()
            if row is None:
                raise ProtocolError("unknown_frame", "mission frame not found")
            self._record(row)
            if row["state"] == "decoded":
                return self._ack(self._record(row))
            if row["state"] != "received":
                stats = conn.execute("SELECT * FROM mission_stats WHERE id=1").fetchone()
                if stats["received"] >= self.max_pending or stats["pending_bytes"] + row["stored_bytes"] > self.max_pending_bytes:
                    raise ProtocolError("backpressure", "mission decode intake full")
                prior = row["state"]
                conn.execute("UPDATE mission_frames SET state='received',reason_code=NULL WHERE id=?", (journal_id,))
                conn.execute(f"UPDATE mission_stats SET {prior}={prior}-1,received=received+1,pending_bytes=pending_bytes+? WHERE id=1", (row["stored_bytes"],))
                conn.commit()
                self._refresh_cache(conn, journal_id)
        return self._decode_received(journal_id)

    def query(self, *, after_id=0, limit=None, satellite_id=None, mission_id=None, state=None):
        limit = min(20, self.max_query) if limit is None else limit
        _integer(after_id, "after_id", 0, 2**63-1)
        _integer(limit, "limit", 1, self.max_query)
        clauses, arguments = ["id> ?"], [after_id]
        if satellite_id is not None:
            clauses.append("satellite_id=?")
            arguments.append(_satellite_id(satellite_id))
        if mission_id is not None:
            clauses.append("mission_id=?")
            arguments.append(_identifier(mission_id, "mission_id"))
        if state is not None:
            if state not in STATES:
                raise ProtocolError("invalid_query", "unknown mission frame state")
            clauses.append("state=?")
            arguments.append(state)
        with self.lock, closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM mission_frames WHERE " + " AND ".join(clauses) + " ORDER BY id LIMIT ?", (*arguments, limit))
            result, size = [], 0
            for row in rows:
                item = self._record(row)
                cost = len(canonical(item).encode())
                if size + cost > MAX_REPLY_BYTES - 4096:
                    break
                result.append(item)
                size += cost
            return result

    def latest(self, *, satellite_id=None, mission_id=None):
        if satellite_id is None and mission_id is None:
            return self.snapshot()["latest"]
        clauses, arguments = [], []
        if satellite_id is not None:
            clauses.append("satellite_id=?")
            arguments.append(_satellite_id(satellite_id))
        if mission_id is not None:
            clauses.append("mission_id=?")
            arguments.append(_identifier(mission_id, "mission_id"))
        with self.lock, closing(self._connect()) as conn:
            return self._record(conn.execute("SELECT * FROM mission_frames WHERE " + " AND ".join(clauses) + " ORDER BY id DESC LIMIT 1", arguments).fetchone())


class MissionWebSocketServer:
    """Loopback-only classical frame transport with revocable common identity."""

    def __init__(self, store, clock, *, security, credential_ref, port=0, host="127.0.0.1"):
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ProtocolError("unsafe_bind", "local mission gateway only; remote stations require authenticated TLS")
        if security is None:
            raise ProtocolError("authentication_required", "mission ingress requires common backend identity")
        if not isinstance(credential_ref, str) or not credential_ref.startswith("vault:") or not credential_ref[6:]:
            raise ProtocolError("missing_credential", "mission ingress requires a vault: session reference")
        _integer(port, "port", 0, 65535)
        self.store, self.clock, self.security = store, clock, security
        try:
            token = security.vault.get_bytes(credential_ref[6:]).decode("ascii")
            self._subject = self._authenticate(token).subject
        except (PermissionError, ValueError, UnicodeError, RuntimeError):
            raise ProtocolError("authentication_required", "mission session unavailable or revoked") from None
        try:
            from websockets.sync.server import serve
        except ImportError:
            raise ProtocolError("adapter_unavailable", "WebSocket mission adapter unavailable") from None
        self._clients, self._lock = set(), Lock()

        def handshake(connection, request):
            if request.path not in ("/satellite/v1/frames", "/satellite/v1/status"):
                return connection.respond(404, "unknown mission stream")
            if request.headers.get("Origin") is not None:
                return connection.respond(403, "browser-origin mission connections rejected")
            if len(request.headers.get_all("Authorization")) != 1:
                return connection.respond(401, "authenticated mission gateway required")
            try:
                principal = self._request_identity(request.headers.get("Authorization", ""))
                with security.identity_scope(principal):
                    self._authorize(request.path)
            except PermissionError:
                return connection.respond(403, "mission identity or resource permission rejected")

        self.server = serve(self._handle, host, port, process_request=handshake, origins=[None],
            max_size=MAX_MESSAGE_BYTES, max_queue=4, compression=None, open_timeout=3.,
            ping_interval=10., ping_timeout=10., close_timeout=1., server_header=None)
        self.port = self.server.socket.getsockname()[1]
        self.thread = Thread(target=self.server.serve_forever, name="orion-mission-frames", daemon=True)
        self.thread.start()

    def _authenticate(self, token):
        return self.security.authenticate_oidc(token) if token.count(".") == 2 else self.security.session(token)

    def _request_identity(self, authorization):
        from ..security import AuthenticationError
        if not isinstance(authorization, str) or not authorization.startswith("Bearer ") or len(authorization) > 16400:
            raise AuthenticationError("authenticated mission identity required")
        principal = self._authenticate(authorization[7:])
        if principal.subject != self._subject:
            raise AuthenticationError("mission identity binding rejected")
        return principal

    def _authorize(self, path):
        return self.security.authorize("sources.read" if path.endswith("/status") else "ingestion.write",
                                       "source:" + self.store.source_id + "/" + path.rsplit("/", 1)[-1])

    def _receive(self, path, data):
        from ..security.http_boundary import strict_object
        packet = strict_object(data)
        if path.endswith("/frames"):
            return self.store.submit(packet, self.clock())
        query = packet.get("query")
        if query == "status" and set(packet) == {"query"}:
            snapshot = self.store.presentation_snapshot()
            # Status is a summary; full frames remain accessible by bounded
            # latest/query requests. A large catalogue must not make a reply
            # grow with all historical payload bytes.
            size = len(canonical({k: v for k, v in snapshot.items() if k != "latest_by_satellite"}).encode())
            limited = {}
            for ident, record in snapshot["latest_by_satellite"].items():
                cost = len(canonical(record).encode()) + len(ident) + 8
                if size + cost > MAX_REPLY_BYTES - 4096:
                    snapshot["catalogue_reply_truncated"] = True
                    break
                limited[ident] = record
                size += cost
            snapshot["latest_by_satellite"] = limited
            return snapshot
        allowed = {"query", "satellite_id", "mission_id"}
        if query == "frames.latest" and set(packet) <= allowed:
            return {"schema": STATUS_SCHEMA, "query": query,
                    "frame": self.store.latest(**{k: v for k, v in packet.items() if k != "query"})}
        if query == "frames.query" and set(packet) <= allowed | {"after_id", "limit", "state"}:
            frames = self.store.query(**{k: v for k, v in packet.items() if k != "query"})
            return {"schema": STATUS_SCHEMA, "query": query, "frames": frames,
                    "next_after_id": frames[-1]["journal_id"] if frames else packet.get("after_id", 0)}
        raise ProtocolError("invalid_query", "mission status query rejected")

    def _handle(self, connection):
        from websockets.exceptions import ConnectionClosed
        with self._lock:
            if len(self._clients) >= 4:
                connection.close(1013, "mission connection capacity reached")
                return
            self._clients.add(connection)
        try:
            while True:
                try:
                    message = connection.recv(timeout=30.)
                except TimeoutError:
                    break
                principal = None
                path = connection.request.path
                resource = "source:" + self.store.source_id + "/" + path.rsplit("/", 1)[-1]
                try:
                    principal = self._request_identity(connection.request.headers.get("Authorization", ""))
                    with self.security.identity_scope(principal):
                        self._authorize(path)
                        data = message.encode("utf-8") if isinstance(message, str) else message
                        artifact = hashlib.sha256(data).hexdigest()
                        self.security.audit.record(principal, "mission.receive", resource, "completed",
                            {"artifact_sha256": artifact, "stage": "observation"})
                        try:
                            result = self._receive(path, data)
                        except sqlite3.Error:
                            result = {"status": "rejected", "receipt": "not_admitted", "code": "journal_unavailable"}
                        except (ProtocolError, ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
                            code = exc.code if isinstance(exc, ProtocolError) else "invalid_packet"
                            result = {"status": "rejected", "receipt": "not_admitted", "code": code}
                        admitted = result.get("admission") == "decoded"
                        outcome = "rejected" if result.get("status") == "rejected" or (
                            path.endswith("/frames") and not admitted) else "accepted"
                        self.security.audit.record(principal, "mission.ingest" if path.endswith("/frames") else "mission.query",
                            resource, outcome, {"artifact_sha256": artifact,
                                "sequence": result.get("sequence"), "accepted_events": int(admitted),
                                "rejected_events": int(outcome == "rejected"),
                                "classical_message_accepted": result.get("receipt") in ("accepted", "duplicate")})
                except PermissionError:
                    connection.send(canonical({"status": "rejected", "code": "authentication_required"}))
                    connection.close(1008, "mission identity unavailable or permission revoked")
                    break
                connection.send(canonical(result))
        except ConnectionClosed:
            pass
        finally:
            with self._lock:
                self._clients.discard(connection)
            connection.close()

    def close(self):
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            client.close()
        self.server.shutdown()
        self.thread.join(timeout=3.)
