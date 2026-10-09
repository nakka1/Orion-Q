"""Read-only, asynchronous CelesTrak OMM integration with explicit evidence scope.

Public GP elements predict an orbit. They are never housekeeping, detector
events, a tracking lock, or evidence that an optical payload is operational.
No network access occurs until refresh() is explicitly requested.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from threading import Lock
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from .orbits import _gmst, _rotate_z, ecef_geodetic, validate_orbit
from .physics import OMEGA_EARTH, EARTH_KM

DOC = "https://celestrak.org/NORAD/documentation/gp-data-formats.php"
MAX_BYTES = 262144


def _utc(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        raise ValueError("explicit_UTC_epoch_required")
    return stamp.astimezone(timezone.utc)


def normalize_omm(value, expected_catalog_id):
    if isinstance(value, list):
        if len(value) != 1:
            raise ValueError("one_OMM_record_required")
        value = value[0]
    if not isinstance(value, dict):
        raise ValueError("OMM_record_required")
    fields = dict(value)
    for key, default in (("CENTER_NAME", "EARTH"), ("REF_FRAME", "TEME"),
                         ("TIME_SYSTEM", "UTC"), ("MEAN_ELEMENT_THEORY", "SGP4")):
        # These redundant fields are omitted specifically by CelesTrak JSON.
        if fields.setdefault(key, default) != default:
            raise ValueError("incompatible_OMM_frame_time_or_theory")
    if type(fields.get("NORAD_CAT_ID")) is not int or fields["NORAD_CAT_ID"] != expected_catalog_id:
        raise ValueError("OMM_catalog_identity_mismatch")
    for key in ("MEAN_MOTION", "ECCENTRICITY", "INCLINATION", "RA_OF_ASC_NODE",
                "ARG_OF_PERICENTER", "MEAN_ANOMALY", "BSTAR", "MEAN_MOTION_DOT", "MEAN_MOTION_DDOT"):
        number = fields.get(key)
        if type(number) not in (float, int) or not math.isfinite(number):
            raise ValueError("OMM_finite_numeric_field_required:" + key)
    for key in ("EPHEMERIS_TYPE", "ELEMENT_SET_NO", "REV_AT_EPOCH"):
        if type(fields.get(key)) is not int or fields[key] < 0:
            raise ValueError("OMM_nonnegative_integer_required:" + key)
    if (not 0 < fields["MEAN_MOTION"] < 20 or not 0 <= fields["ECCENTRICITY"] < 1
            or not 0 <= fields["INCLINATION"] <= 180 or any(not 0 <= fields[k] < 360
                for k in ("RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY"))):
        raise ValueError("OMM_element_out_of_range")
    if fields.get("CLASSIFICATION_TYPE") != "U" or not isinstance(fields.get("OBJECT_ID"), str):
        raise ValueError("unclassified_identified_OMM_required")
    epoch = fields.get("EPOCH")
    if not isinstance(epoch, str):
        raise ValueError("OMM_epoch_required")
    # CelesTrak's UTC JSON omits Z; the above TIME_SYSTEM supplies the frame.
    parsed = datetime.fromisoformat(epoch.replace("Z", "+00:00"))
    parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    fields["EPOCH"] = parsed.strftime("%Y-%m-%dT%H:%M:%S.%f")
    return fields


def _satrec(fields):
    from sgp4.api import Satrec
    from sgp4.omm import initialize
    satellite = Satrec()
    initialize(satellite, fields)
    if satellite.error:
        raise ValueError("OMM_SGP4_initialization_failed")
    return satellite


def _orbital_class(fields):
    satellite = _satrec(fields)
    radius = satellite.a * satellite.radiusearthkm
    perigee = radius * (1 - fields["ECCENTRICITY"]) - EARTH_KM
    apogee = radius * (1 + fields["ECCENTRICITY"]) - EARTH_KM
    if (abs(fields["MEAN_MOTION"] - OMEGA_EARTH * 86400 / (2 * math.pi)) < .005
            and fields["INCLINATION"] <= .1 and fields["ECCENTRICITY"] <= .001):
        return "GEO"
    if 100 <= perigee <= apogee <= 2000:
        return "LEO"
    if 2000 < perigee <= apogee < 35000:
        return "MEO"
    return "HEO"  # Elevated/eccentric or non-geostationary high orbit.


class PublicEphemerisSource:
    def __init__(self, catalog_id, cache_directory, *, timeout_s=10, min_refresh_s=7200,
                 max_age_s=259200, opener=urlopen, now=None):
        if type(catalog_id) is not int or not 1 <= catalog_id <= 999999999:
            raise ValueError("catalog_id_requires_1_to_9_digits")
        if (any(type(value) not in (float, int) or not math.isfinite(value)
                for value in (timeout_s, min_refresh_s, max_age_s))
                or not 0 < timeout_s <= 30 or min_refresh_s < 7200 or not 0 < max_age_s <= 604800):
            raise ValueError("invalid_timeout_refresh_or_validity")
        self.catalog_id, self.root = catalog_id, Path(cache_directory)
        self.url = "https://celestrak.org/NORAD/elements/gp.php?" + urlencode({"CATNR": catalog_id, "FORMAT": "JSON"})
        self.timeout_s, self.min_refresh_s, self.max_age_s = timeout_s, min_refresh_s, max_age_s
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._opener = opener
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="orion-public-orbit")
        self._future = None
        self._closed = False
        self._last_attempt = None
        self._record = None
        self._error = None
        path = self.root / f"celestrak-{catalog_id}.json"
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            check = dict(value)
            sha = check.pop("cache_sha256", None)
            if sha != hashlib.sha256(self._canonical(check)).hexdigest():
                raise ValueError("public_ephemeris_cache_integrity_invalid")
            if value.get("catalog_id") != catalog_id or value.get("source_url") != self.url:
                raise ValueError("public_ephemeris_cache_binding_invalid")
            normalize_omm(value["omm"], catalog_id)
            self._record = value
            self._last_attempt = _utc(value["retrieved_at_utc"])

    @staticmethod
    def _canonical(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

    def refresh(self, *, acknowledge_previous_error=False):
        with self._lock:
            if self._closed:
                raise ValueError("public_ephemeris_source_closed")
            if self._future is not None and not self._future.done():
                return self._future
            # Provider policy: a non-200/error stops retrieval for human review.
            if self._error is not None and not acknowledge_previous_error:
                return self._future
            current = self._now()
            if self._last_attempt and (current - self._last_attempt).total_seconds() < self.min_refresh_s:
                return self._future
            self._last_attempt = current
            self._future = self._executor.submit(self._fetch, current)
            return self._future

    def _fetch(self, current):
        try:
            request = Request(self.url, headers={"User-Agent": "ORION-Q-public-orbit-readonly/1.0", "Accept": "application/json"})
            with self._opener(request, timeout=self.timeout_s) as response:
                target = urlparse(response.geturl() if hasattr(response, "geturl") else self.url)
                if target.scheme != "https" or target.hostname != "celestrak.org":
                    raise ValueError("public_orbit_redirect_outside_provider")
                data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("public_orbit_response_too_large")
            from quantum_autopilot.lab.service import unique_fields
            fields = normalize_omm(json.loads(data.decode("utf-8"), object_pairs_hook=unique_fields), self.catalog_id)
            satellite = _satrec(fields)
            record = {"schema": "orion.public-ephemeris.v1", "catalog_id": self.catalog_id,
                      "object_name": fields.get("OBJECT_NAME", ""), "omm": fields,
                      "source_url": self.url, "retrieved_at_utc": current.isoformat(),
                      "raw_sha256": hashlib.sha256(data).hexdigest(), "raw_bytes": len(data),
                      "evidence_kind": "public_orbit_prediction", "documentation": DOC,
                      "payload_or_housekeeping_observed": False, "physical_link_validated": False,
                      "sgp4_epoch_utc": (datetime(1949, 12, 31, tzinfo=timezone.utc) + timedelta(
                          days=satellite.jdsatepoch + satellite.jdsatepochF - 2433281.5)).isoformat()}
            record["cache_sha256"] = hashlib.sha256(self._canonical(record)).hexdigest()
            self.root.mkdir(parents=True, exist_ok=True)
            temporary = self.root / f".public-ephemeris-{uuid4().hex}.tmp"
            try:
                with temporary.open("xb") as handle:
                    handle.write(self._canonical(record))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.root / f"celestrak-{self.catalog_id}.json")
                raw_target = self.root / f"celestrak-{record['raw_sha256']}.json"
                if not raw_target.exists():
                    with raw_target.open("xb") as handle:
                        handle.write(data)
            finally:
                temporary.unlink(missing_ok=True)
            with self._lock:
                self._record, self._error = record, None
            return self.snapshot()
        except Exception as exc:
            with self._lock:
                self._error = type(exc).__name__
            return self.snapshot()

    def snapshot(self, *, at_utc=None):
        with self._lock:
            record, error = self._record, self._error
            refreshing = self._future is not None and not self._future.done()
        now = _utc(at_utc) if at_utc is not None else self._now()
        age = None if record is None else (now - _utc(record["omm"]["EPOCH"] + "+00:00")).total_seconds()
        valid = age is not None and abs(age) <= self.max_age_s
        return {"schema": "orion.public-ephemeris-status.v1", "catalog_id": self.catalog_id,
                "state": "ready" if valid else "stale" if record else "unavailable",
                "refreshing": refreshing, "last_error_type": error, "epoch_age_s": age,
                "max_age_s": self.max_age_s, "source_url": self.url,
                "record": json.loads(self._canonical(record)) if record else None,
                "evidence_kind": "public_orbit_prediction", "physical_link_validated": False}

    def state(self, at_utc):
        stamp = _utc(at_utc)
        value = self.snapshot(at_utc=stamp)
        if value["state"] != "ready":
            raise ValueError("public_ephemeris_missing_or_stale")
        from sgp4.api import jday
        satellite = _satrec(value["record"]["omm"])
        jd, fr = jday(stamp.year, stamp.month, stamp.day, stamp.hour, stamp.minute,
                      stamp.second + stamp.microsecond / 1e6)
        error, position, velocity = satellite.sgp4(jd, fr)
        if error:
            raise ValueError(f"public_ephemeris_SGP4_error:{error}")
        ecef = _rotate_z(position, -_gmst(stamp))
        speed = _rotate_z(velocity, -_gmst(stamp))
        return {"ecef_km": list(ecef), "velocity_ecef_km_s": [speed[0] + OMEGA_EARTH * ecef[1],
                    speed[1] - OMEGA_EARTH * ecef[0], speed[2]], "at_utc": stamp.isoformat(),
                **ecef_geodetic(ecef), "frame": "ECEF", "evidence_kind": "public_orbit_prediction",
                "ephemeris_source": self.url, "frame_accuracy": "SGP4 TEME→GMST ECEF; UT1=UTC, zero polar motion",
                "payload_or_housekeeping_observed": False}

    def sample_orbit(self, at_utc, *, duration_s=3600, step_s=30, epoch_network_s=0, stations=()):
        if type(duration_s) is not int or type(step_s) is not int or not 1 <= step_s <= duration_s <= 86400:
            raise ValueError("bounded_integer_sampling_required")
        if math.ceil(duration_s / step_s) + 1 > 4096:
            raise ValueError("too_many_ephemeris_samples")
        if type(epoch_network_s) not in (float, int) or not math.isfinite(epoch_network_s) or epoch_network_s < 0:
            raise ValueError("finite_nonnegative_network_epoch_required")
        stamp = _utc(at_utc)
        times = list(range(0, duration_s, step_s)) + [duration_s]
        samples = []
        for offset in times:
            state = self.state(stamp + timedelta(seconds=offset))
            samples.append({"at_network_s": epoch_network_s + offset,
                            "ecef_km": state["ecef_km"], "velocity_ecef_km_s": state["velocity_ecef_km_s"]})
        first = self.state(stamp)
        altitude = first["altitude_km"]
        value = {"model": "state_vectors", "orbital_class": _orbital_class(self.snapshot()["record"]["omm"]),
                 "epoch_utc": stamp.isoformat(), "epoch_network_s": epoch_network_s,
                 "source": self.url + " · public OMM prediction; no housekeeping/payload", "state_vectors": samples,
                 "compatible_stations": list(stations), "communication_windows": [],
                 "reference_latitude": first["sat_latitude"], "reference_longitude": first["sat_longitude"],
                 "altitude_km": altitude, "inclination_deg": self.snapshot()["record"]["omm"]["INCLINATION"],
                 "eccentricity": self.snapshot()["record"]["omm"]["ECCENTRICITY"],
                 "orbit_phase_rad": 0, "evidence_kind": "public_orbit_prediction",
                 "optical_payload": "unknown; optical operation must be established by independent telemetry"}
        validate_orbit(value)
        return value

    def close(self):
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
