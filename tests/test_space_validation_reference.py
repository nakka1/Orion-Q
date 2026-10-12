"""Independent optical laws, units, frame conventions and tamper gates."""
from __future__ import annotations

import ast
import math
import sys
from pathlib import Path

import pytest

from tests.qualification_fixtures import FIXTURES, extract_qualification_fixture

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from space_validation_reference import (  # noqa: E402
    WGS84_A_KM, WGS84_B_KM, atmospheric_capture, bernoulli_photons,
    carrier_doppler, gaussian_capture, ground_ecef, ground_geometry,
    relative_geometry, validate_author_tree,
)

pytestmark = pytest.mark.unit


def state(xyz, velocity=(0., 0., 0.), at=0):
    return {"ecef_km": list(xyz), "velocity_ecef_km_s": list(velocity), "at_s": at, "frame": "ECEF"}


@pytest.mark.parametrize("distance", [0., 400000., 1000000., 35000000.])
def test_gaussian_capture_conserves_power_and_aperture_order(distance):
    small = gaussian_capture(distance, 3e-6, .5)
    large = gaussian_capture(distance, 3e-6, 1.)
    assert 0 < small <= large <= 1
    assert gaussian_capture(distance, 3e-6, .5, pointing_sigma_rad=1e-6) <= small


def test_gaussian_reference_matches_archived_author_numeric_fixture():
    # Archived v1.1.0 eta_dif integral, 780 nm, 1000 km, 3 urad, 0.5 m.
    assert gaussian_capture(1_000_000., 3e-6, .5, pointing_sigma_rad=1e-6) == pytest.approx(.03771179524003175, rel=1e-6)
    assert gaussian_capture(2_000_000., 3e-6, .5) < gaussian_capture(1_000_000., 3e-6, .5)


@pytest.mark.parametrize("kwargs", [{"distance_m": -1}, {"divergence_rad": 0}, {"receiver_radius_m": 0},
                                   {"wavelength_m": -1}, {"pointing_sigma_rad": -1}, {"distance_m": math.nan}])
def test_reference_rejects_invalid_optical_units(kwargs):
    args = {"distance_m": 1e6, "divergence_rad": 3e-6, "receiver_radius_m": .5, **kwargs}
    with pytest.raises(ValueError):
        gaussian_capture(**args)


@pytest.mark.parametrize("elevation", [20, 30, 60, 90])
def test_atmospheric_transmission_is_no_larger_than_zenith(elevation):
    assert 0 < atmospheric_capture(elevation) <= .8


def test_atmosphere_blocks_below_horizon():
    assert atmospheric_capture(0) == atmospheric_capture(-10) == 0


def test_wgs84_equator_and_poles_match_defining_axes():
    assert ground_ecef(0, 0).tolist() == pytest.approx([WGS84_A_KM, 0, 0])
    assert ground_ecef(90, 0).tolist() == pytest.approx([0, 0, WGS84_B_KM], abs=1e-9)
    assert ground_ecef(0, 90, 1000).tolist() == pytest.approx([0, WGS84_A_KM+1, 0], abs=1e-9)


def test_segment_los_is_blocked_by_earth_but_visible_above_limb():
    assert relative_geometry(state([7000, 0, 0]), state([-7000, 0, 0]))["earth_obstructed"]
    assert not relative_geometry(state([7000, -100, 0]), state([7000, 100, 0]))["earth_obstructed"]


@pytest.mark.parametrize("right", [state([7000, 100, 0], at=1), {**state([7000, 100, 0]), "frame": "TEME"}])
def test_geometry_rejects_unsynchronized_or_mixed_frames(right):
    with pytest.raises(ValueError):
        relative_geometry(state([7000, 0, 0]), right)


def test_geo_ground_point_ahead_restores_earth_rotation():
    radius = (398600.4418 / 7.2921159e-5**2)**(1/3)
    g = ground_geometry(state([radius, 0, 0]), (0., 0.))
    assert g["elevation_deg"] == pytest.approx(90.)
    assert g["radial_velocity_km_s"] == 0
    assert g["point_ahead_urad"] == pytest.approx(17.40910348805765)


@pytest.mark.parametrize("wavelength", [780, 850, 1550])
def test_doppler_receding_blue_red_sign_and_zero(wavelength):
    assert carrier_doppler(7.5, wavelength) < 0 < carrier_doppler(-7.5, wavelength)
    assert carrier_doppler(0, wavelength) == 0
    assert carrier_doppler(7.5, wavelength) == -carrier_doppler(-7.5, wavelength)


def test_raw_reference_events_reproduce_seed_without_final_results():
    assert bernoulli_photons(42, 1_000_000, .01) == bernoulli_photons(42, 1_000_000, .01)
    row = bernoulli_photons(42, 1_000_000, .01)
    assert abs(row["detected"] - row["expected"]) < 6*row["std"]
    assert not {"qber", "final_key", "conditional_key_estimate_bits"} & row.keys()


def test_reference_has_no_orion_imports():
    tree = ast.parse((REPO / "tools/space_validation_reference.py").read_text(encoding="utf-8"))
    names = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    names += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert all(not (name or "").startswith("quantum_autopilot") for name in names)


@pytest.fixture
def author_archive(tmp_path):
    archive = FIXTURES / "zenodo-6384558-v1.1.0.zip"
    unpacked = extract_qualification_fixture(archive.name, tmp_path / "author")
    roots = list(unpacked.iterdir())
    assert len(roots) == 1 and roots[0].is_dir()
    return roots[0], archive


def test_author_tree_tampering_rejected_before_import(author_archive):
    root, archive = author_archive
    assert len(validate_author_tree(root, archive)) == 67
    file = root / "scenarios/three_satellites/common_functions.py"
    file.write_text(file.read_text(encoding="utf-8") + "\nraise RuntimeError('tampered')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly match"):
        validate_author_tree(root, archive)


def test_extra_upstream_module_rejected(author_archive):
    root, archive = author_archive
    assert len(validate_author_tree(root, archive)) == 67
    (root / "injected.py").write_text("raise RuntimeError('untrusted')", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly match"):
        validate_author_tree(root, archive)
