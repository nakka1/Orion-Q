"""No live network: external OMM contract, async recovery and backend propagation."""
from datetime import datetime, timedelta, timezone
import io
import json
import time
from threading import Event

import pytest

from quantum_autopilot.quantum_protocols.public_ephemeris import PublicEphemerisSource, normalize_omm
from quantum_autopilot.quantum_protocols.orbits import position, state_geometry

FIELDS = {"OBJECT_NAME": "fixture-public-orbit", "OBJECT_ID": "1998-067A", "NORAD_CAT_ID": 25544,
          "EPOCH": "2026-10-06T23:34:32.785824", "MEAN_MOTION": 15.48757, "ECCENTRICITY": .00068249,
          "INCLINATION": 51.631, "RA_OF_ASC_NODE": 106.8391, "ARG_OF_PERICENTER": 231.9092,
          "MEAN_ANOMALY": 128.1281, "BSTAR": 8.5983062e-5, "MEAN_MOTION_DOT": 4.251e-5,
          "MEAN_MOTION_DDOT": 0, "EPHEMERIS_TYPE": 0, "CLASSIFICATION_TYPE": "U",
          "ELEMENT_SET_NO": 999, "REV_AT_EPOCH": 58908}
EPOCH = datetime(2026, 10, 6, 23, 34, 32, 785824, tzinfo=timezone.utc)


def opener(_request, **_kwargs):
    return io.BytesIO(json.dumps([FIELDS]).encode())


def test_async_single_request_cache_and_no_duplicated_listeners(tmp_path):
    wait, started = Event(), Event()
    calls = []
    def slow(request, **kwargs):
        calls.append(request.full_url)
        started.set()
        wait.wait(3)
        return opener(request, **kwargs)
    source = PublicEphemerisSource(25544, tmp_path, opener=slow, now=lambda: EPOCH)
    try:
        began = time.monotonic()
        future = source.refresh()
        assert time.monotonic() - began < .25
        assert started.wait(1)
        assert source.refresh() is future and source.snapshot()["state"] == "unavailable"
        wait.set()
        future.result(3)
        assert source.snapshot()["state"] == "ready" and len(calls) == 1
        assert source.refresh() is future and len(calls) == 1
        restored = PublicEphemerisSource(25544, tmp_path, opener=lambda *_a, **_k: pytest.fail("duplicate fetch"), now=lambda: EPOCH)
        try:
            assert restored.snapshot()["state"] == "ready"
            assert restored.refresh() is None
        finally:
            restored.close()
    finally:
        wait.set()
        source.close()


@pytest.mark.parametrize("change", [{"REF_FRAME": "ITRF"}, {"TIME_SYSTEM": "TAI"},
    {"MEAN_ELEMENT_THEORY": "KEPLER"}, {"NORAD_CAT_ID": 999}, {"MEAN_MOTION": float("nan")},
    {"MEAN_MOTION": True}, {"ECCENTRICITY": 1.1}, {"EPHEMERIS_TYPE": True}])
def test_incompatible_public_data_rejected(change):
    with pytest.raises(ValueError):
        normalize_omm({**FIELDS, **change}, 25544)


def test_no_fallback_on_error_and_expiry_blocks_predictions(tmp_path):
    now = [EPOCH]
    source = PublicEphemerisSource(25544, tmp_path, opener=opener, now=lambda: now[0])
    try:
        source.refresh().result(3)
        now[0] += timedelta(days=4)
        assert source.snapshot()["state"] == "stale"
        with pytest.raises(ValueError, match="missing_or_stale"):
            source.state(now[0])
        attempts = []
        def fail(*_a, **_k):
            attempts.append(1)
            raise TimeoutError()
        source._opener = fail
        source.refresh().result(3)
        assert source.snapshot()["last_error_type"] == "TimeoutError"
        now[0] += timedelta(hours=3)
        source.refresh()
        assert len(attempts) == 1  # human review required after retrieval error
    finally:
        source.close()


def test_public_prediction_uses_existing_backend_without_claiming_photons(tmp_path):
    source = PublicEphemerisSource(25544, tmp_path, opener=opener, now=lambda: EPOCH)
    try:
        source.refresh().result(3)
        orbit = source.sample_orbit(EPOCH, duration_s=300, step_s=30)
        backend = position(orbit, 150)
        direct = source.state(EPOCH + timedelta(seconds=150))
        assert backend["ecef_km"] == pytest.approx(direct["ecef_km"], abs=1e-10)
        assert backend["velocity_ecef_km_s"] == pytest.approx(direct["velocity_ecef_km_s"], abs=1e-10)
        assert 350 < backend["altitude_km"] < 500
        geometry = state_geometry(backend, (backend["sat_latitude"], backend["sat_longitude"]))
        assert geometry["elevation_deg"] == pytest.approx(90, abs=1e-6)
        assert not direct["payload_or_housekeeping_observed"]
        assert source.snapshot()["evidence_kind"] == "public_orbit_prediction"
        with pytest.raises(ValueError, match="cobertura"):
            position(orbit, 301)
    finally:
        source.close()


def test_tampered_cache_refused(tmp_path):
    source = PublicEphemerisSource(25544, tmp_path, opener=opener, now=lambda: EPOCH)
    source.refresh().result(3)
    source.close()
    path = tmp_path / "celestrak-25544.json"
    value = json.loads(path.read_text())
    value["omm"]["MEAN_MOTION"] = 16
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="integrity"):
        PublicEphemerisSource(25544, tmp_path)


@pytest.mark.parametrize("parameters", [{"min_refresh_s": float("nan")},
    {"min_refresh_s": float("inf")}, {"timeout_s": True}, {"max_age_s": float("nan")}])
def test_nonfinite_resource_limits_rejected(tmp_path, parameters):
    with pytest.raises(ValueError, match="invalid_timeout"):
        PublicEphemerisSource(25544, tmp_path, **parameters)


@pytest.mark.parametrize("inclination,eccentricity,expected", [(0, .0001, "GEO"),
    (30, .0001, "HEO"), (0, .3, "HEO")])
def test_high_orbit_is_not_inferred_to_be_geostationary(inclination, eccentricity, expected):
    from quantum_autopilot.quantum_protocols.public_ephemeris import _orbital_class
    fields = {**FIELDS, "MEAN_MOTION": 1.0027379, "INCLINATION": inclination, "ECCENTRICITY": eccentricity}
    assert _orbital_class(fields) == expected
