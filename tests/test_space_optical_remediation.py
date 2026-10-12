"""Independent pointing/aperture comparison and advancing-time recovery.

Historical campaign oracles and evidence remain intact. These tests qualify the
new reduced model, not a physical terminal, mission attitude or secure key pool.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest
from scipy.integrate import quad
from scipy.special import i0e

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from space_validation_reference import (  # noqa: E402
    gaussian_capture, ground_geometry, relative_geometry,
)
from quantum_autopilot.quantum_protocols.contracts import DeviceCommand, ProtocolConfig  # noqa: E402
from quantum_autopilot.quantum_protocols.isl import isl_optics, terminal_compatibility  # noqa: E402
from quantum_autopilot.quantum_protocols.orbits import geo_reference_orbit, position, state_geometry  # noqa: E402
from quantum_autopilot.quantum_protocols.physics import (  # noqa: E402
    gaussian_aperture_budget, satellite_optics,
)
from quantum_autopilot.quantum_protocols.satellite_contracts import assemble, packets  # noqa: E402
from quantum_autopilot.quantum_protocols.satellite_processing import process_observation  # noqa: E402
from quantum_autopilot.quantum_protocols.sources import SimulatedAcquisitionSource  # noqa: E402

pytestmark = pytest.mark.unit


def geometry(**values):
    return {"range_km": 1000., "elevation_deg": 90., "radial_velocity_km_s": 0.,
            "angular_tracking_rate_rad_s": 0., "transverse_velocity_km_s": 0.,
            "point_ahead_urad": 0., "earth_obstructed": False, **values}


def parameters(**values):
    return {"wavelength_nm": 780., "divergence_urad": 3., "receiver_diameter_m": 1.,
            "transmitter_diameter_m": 2., "pointing_jitter_urad": 1., "tracking_latency_s": 0.,
            "zenith_optical_depth": 0., "optical_efficiency": 1., "insertion_loss_db": 0.,
            "doppler_residual_hz": 0., "terminal_fov_urad": 1000., **values}


@pytest.mark.parametrize("distance_km", [400., 1000., 35000.])
@pytest.mark.parametrize("sigma_urad", [.3, 1., 3., 6.])
def test_ground_and_isl_match_independent_convolution(distance_km, sigma_urad):
    g = geometry(range_km=distance_km)
    p = parameters(pointing_jitter_urad=sigma_urad)
    reference = gaussian_capture(distance_km*1000, 3e-6, .5, pointing_sigma_rad=sigma_urad*1e-6)
    ground = satellite_optics(g, p)
    orbital = isl_optics(g, p, p)
    for result in (ground, orbital):
        assert result["channel_transmission"] == pytest.approx(reference, rel=1e-12)
        assert result["pointing_sigma_per_axis_urad"] == sigma_urad
        assert result["pointing_jitter_convention"] == "gaussian_per_axis"
        assert result["pointing_model_legacy"] is False


def test_pointing_capture_matches_intact_author_jitter_numeric_result():
    # Frozen v1.1.0 authors' 2D quadrature at 780 nm / 1000 km / sigma=1 urad.
    result = satellite_optics(geometry(), parameters())
    assert result["channel_transmission"] == pytest.approx(.03771179524003175, rel=1e-6)
    assert result["pointing_radial_rms_urad"] == pytest.approx(math.sqrt(2))


@pytest.mark.parametrize("offset_urad", [.1, 1., 3., 10.])
def test_displaced_aperture_integral_matches_independent_bessel_quadrature(offset_urad):
    distance, wavelength, divergence, sigma, radius = 1e6, 780e-9, 3e-6, 1e-6, .5
    offset_m = distance * offset_urad * 1e-6
    beam_sq = (wavelength/(math.pi*divergence))**2 + (distance*divergence)**2 + 4*(distance*sigma)**2
    # Polar integral of normalized displaced Gaussian. The scaled Bessel
    # function keeps large-offset integrands stable without using chi-square.
    reference = quad(lambda r: 4*r/beam_sq * math.exp(-2*(r-offset_m)**2/beam_sq)
                     * i0e(4*r*offset_m/beam_sq), 0, radius, epsabs=1e-14, epsrel=1e-12)[0]
    actual = gaussian_aperture_budget(distance, wavelength, divergence, radius, sigma, offset_urad*1e-6)
    assert actual["aperture_capture_efficiency"] == pytest.approx(reference, rel=1e-10, abs=1e-15)


@pytest.mark.parametrize("longitude", [-60., 0., 35.])
def test_geo_ground_point_ahead_includes_rotation_without_artificial_tracking(longitude):
    state = position(geo_reference_orbit(longitude), 20.)
    expected = ground_geometry(state, (0., longitude))
    actual = state_geometry(state, (0., longitude))
    assert actual["point_ahead_urad"] == pytest.approx(17.40910348805765)
    assert actual["point_ahead_urad"] == pytest.approx(expected["point_ahead_urad"], rel=1e-12)
    assert actual["angular_tracking_rate_rad_s"] == 0
    assert actual["ecef_transverse_velocity_km_s"] == 0
    assert actual["radial_velocity_km_s"] == 0
    assert actual["transverse_velocity_km_s"] > 0


def test_received_leo_vector_uses_inertial_reference_and_keeps_doppler_range():
    state = {"ecef_km": [6878.137, 0., 0.], "velocity_ecef_km_s": [0., 7.5, 0.],
             "frame": "ECEF", "at_s": 0.}
    expected = ground_geometry(state, (0., 0.))
    actual = state_geometry(state, (0., 0.))
    assert actual["range_km"] == 500.
    assert actual["elevation_deg"] == 90.
    assert actual["point_ahead_urad"] == pytest.approx(expected["point_ahead_urad"], rel=1e-12)
    assert actual["point_ahead_urad"] != pytest.approx(50.03461428)
    assert actual["radial_velocity_km_s"] == 0.
    assert actual["angular_tracking_rate_rad_s"] == pytest.approx(7.5/500.)


@pytest.mark.parametrize("radius, longitude, latitude, velocity, station, altitude", [
    (6878., -38.55, -12.97, (1., 6., 2.), (-12.967, -38.446), 35.),
    (7378., 75., 88., (2., 1., 0.), (89., 10.), 50.),
])
def test_ground_point_ahead_reproduces_historical_non_equatorial_gaps(radius, longitude, latitude,
                                                                   velocity, station, altitude):
    lat, lon = map(math.radians, (latitude, longitude))
    state = {"ecef_km": [radius*math.cos(lat)*math.cos(lon), radius*math.cos(lat)*math.sin(lon),
                         radius*math.sin(lat)], "velocity_ecef_km_s": list(velocity), "frame": "ECEF", "at_s": 0.}
    expected = ground_geometry(state, station, altitude)
    actual = state_geometry(state, station, altitude)
    assert actual["point_ahead_urad"] == pytest.approx(expected["point_ahead_urad"], rel=1e-12)
    assert actual["range_km"] == pytest.approx(expected["range_km"], rel=1e-12)
    assert actual["radial_velocity_km_s"] == pytest.approx(expected["radial_velocity_km_s"], abs=1e-12)


def test_radial_rms_conversion_and_explicit_legacy_replay():
    baseline = satellite_optics(geometry(), parameters())
    radial = satellite_optics(geometry(), parameters(pointing_jitter_urad=math.sqrt(2),
                              pointing_jitter_convention="gaussian_radial_rms"))
    legacy = satellite_optics(geometry(), parameters(pointing_jitter_convention="legacy_offset_surrogate"))
    assert radial["channel_transmission"] == pytest.approx(baseline["channel_transmission"])
    old = -math.expm1(-2*.5**2/3.**2) * math.exp(-2/9)
    assert legacy["channel_transmission"] == pytest.approx(old)
    assert legacy["pointing_model_legacy"] is True
    assert legacy["channel_transmission"] != pytest.approx(baseline["channel_transmission"])


def test_point_ahead_compensation_changes_optical_coupling_in_both_models():
    g = geometry(point_ahead_urad=10.)
    good = parameters()
    bad = parameters(point_ahead_compensation=False)
    for process in (lambda p: satellite_optics(g, p), lambda p: isl_optics(g, p, p)):
        corrected, uncorrected = process(good), process(bad)
        assert corrected["point_ahead_residual_urad"] == 0.
        assert uncorrected["point_ahead_residual_urad"] == 10.
        assert uncorrected["channel_transmission"] < corrected["channel_transmission"] / 100


@pytest.mark.parametrize("change", [{"extra_loss_db": 40.}, {"pointing_enabled": False}, {"receiver_alignment_error_urad": 20.}])
def test_losses_alignment_and_disabled_pointing_remain_effective(change):
    p = parameters()
    baseline = satellite_optics(geometry(), p)
    changed = satellite_optics(geometry(), {**p, **change})
    assert changed["channel_transmission"] < baseline["channel_transmission"] / 100


def test_horizon_cloud_and_earth_obstruction_do_not_create_optical_transmissions():
    p = parameters()
    assert satellite_optics(geometry(elevation_deg=0.), p)["channel_transmission"] == 0
    assert satellite_optics(geometry(), p, cloud=True)["channel_transmission"] == 0
    blocked = isl_optics(geometry(earth_obstructed=True), p, p)
    assert blocked["channel_transmission"] == 0 and "earth_obstruction" in blocked["block_reasons"]


def test_diffraction_floor_is_gaussian_waist_not_airy_first_zero():
    p = parameters(wavelength_nm=1550., transmitter_diameter_m=.1, divergence_urad=.001)
    expected = 1550e-9/(math.pi*.05)*1e6
    for result in (satellite_optics(geometry(), p), isl_optics(geometry(), p, p)):
        assert result["effective_divergence_urad"] == pytest.approx(expected)
        assert result["beam_waist_m"] == pytest.approx(.05)


def test_optical_memory_encoding_requires_explicit_protocol_admission():
    measured = parameters(quantum_encoding="polarization_BBM92")
    memory = parameters(quantum_encoding="polarization_memory_pair")
    assert terminal_compatibility(memory, memory) == ("incompatible_quantum_encoding",)
    assert isl_optics(geometry(), memory, memory)["visible"] is False
    allowed = ("polarization_memory_pair",)
    assert terminal_compatibility(memory, memory, allowed_encodings=allowed) == ()
    assert isl_optics(geometry(), memory, memory, allowed_encodings=allowed)["visible"] is True
    assert terminal_compatibility(measured, memory, allowed_encodings=allowed) == ("incompatible_quantum_encoding",)


@pytest.mark.parametrize("convention", ["rms", "unknown", None])
def test_ambiguous_jitter_conventions_are_not_silently_used(convention):
    with pytest.raises(ValueError):
        satellite_optics(geometry(), parameters(pointing_jitter_convention=convention))


@pytest.mark.parametrize("value", [True, "1", None, float("nan"), float("inf"), -1.])
def test_invalid_jitter_values_cannot_create_an_optical_budget(value):
    with pytest.raises(ValueError):
        satellite_optics(geometry(), parameters(pointing_jitter_urad=value))


def moving_orbit(longitude):
    return {"model": "circular_reference", "orbital_class": "LEO", "epoch_utc": "2026-10-10T00:00:00+00:00",
            "epoch_network_s": 0., "source": "explicit simulated time-recovery fixture; not a mission ephemeris",
            "reference_latitude": 0., "reference_longitude": longitude, "altitude_km": 500.,
            "inclination_deg": 53., "orbit_phase_rad": 0., "communication_windows": [[0., 1.], [20., 21.]]}


@pytest.mark.parametrize("technology", ["satellite_doppler", "satellite_isl"])
@pytest.mark.parametrize("seed", [101, 202, 303])
def test_time_advances_through_physical_window_loss_and_recovery(tmp_path, technology, seed):
    common = {"pair_source_hz": 30000., "block_s": .5, "timeout_s": 4., "max_attempts": 2,
        "receiver_diameter_m": 3., "transmitter_diameter_m": .5, "divergence_urad": 10.,
        "pointing_jitter_urad": .3, "tracking_latency_s": 0., "optical_efficiency": .9,
        "detector_efficiency": .9, "insertion_loss_db": 0., "zenith_optical_depth": .05,
        "background_cps": 0., "dark_count_cps": 0., "timing_jitter_ps": 50., "sync_tolerance_s": 1e-6,
        "doppler_residual_hz": 0.}
    if technology == "satellite_doppler":
        endpoints, path, links, op = ("OGS-A", "OGS-B"), ("OGS-A", "SAT-A", "OGS-B"), ("DOWN-A", "DOWN-B"), "satellite_block"
        p = {**common, "orbit_record": moving_orbit(0.)}
        env = {"ground_positions": [(0., 0.), (0., .01)], "ground_altitudes_m": [0., 0.]}
    else:
        endpoints, path, links, op = ("SAT-A", "SAT-B"), ("SAT-A", "SAT-B"), ("ISL-AB",), "isl_block"
        p = {**common, "satellite_orbits": {"SAT-A": moving_orbit(0.), "SAT-B": moving_orbit(1.)}}
        env = {}
    cfg = ProtocolConfig("TIME-"+technology, (technology,), endpoints, path, links, seed=seed, parameters=p)
    source = SimulatedAcquisitionSource(seed)
    rows = []
    for start in (0., 10., 20.):
        cmd = DeviceCommand(f"capture-{start}", cfg.session_id, op, cfg.path,
            tuple(f"q{i}" for i in range(len(links))), tuple(f"c{i}" for i in range(len(links))),
            start, start+4., 1, {"duration_s": .5})
        acquisition = source.acquire(cmd, cfg, env)
        raw = acquisition.raw
        if technology == "satellite_doppler":
            raw = assemble(packets(raw, cmd, source_id=source.source_id, origin="simulated", sequence=acquisition.sequence), parameters=p)
            vector = raw["housekeeping"]["ecef_km"]
        else:
            vector = raw["housekeeping"][endpoints[0]]["ecef_km"]
            reference = relative_geometry(raw["housekeeping"][endpoints[0]], raw["housekeeping"][endpoints[1]])
            assert not reference["earth_obstructed"]
        result = process_observation(raw, {"parameters": p, "endpoints": endpoints, "environment": env, "origin": "simulated"})
        rows.append({"start_s": raw["start_s"], "end_s": raw["end_s"], "sequence": acquisition.sequence,
            "position_ecef_km": vector, "available": result["available"], "coincidences": result["coincidences"],
            "source_epoch_utc_s": raw["tdc"][0]["epoch_utc_s"], "certified_secret_bits": result["certified_secret_bits"]})
        (tmp_path / f"raw-{start}.json").write_text(acquisition.canonical_json(), encoding="utf-8")
        (tmp_path / f"result-{start}.json").write_text(json.dumps(result), encoding="utf-8")
        if technology == "satellite_isl":
            assert result["measurement_resources"]["stored_pairs"] == 0
    assert [row["start_s"] for row in rows] == [0., 10., 20.]
    assert [row["sequence"] for row in rows] == [1, 2, 3]
    assert [row["available"] for row in rows] == [True, False, True]
    assert rows[0]["coincidences"] > 0 and rows[1]["coincidences"] == 0 and rows[2]["coincidences"] > 0
    assert rows[0]["source_epoch_utc_s"] < rows[1]["source_epoch_utc_s"] < rows[2]["source_epoch_utc_s"]
    assert rows[0]["position_ecef_km"] != rows[1]["position_ecef_km"] != rows[2]["position_ecef_km"]
    assert all(row["certified_secret_bits"] == 0 for row in rows)
    (tmp_path / "temporal-evidence.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
