"""The transcribed plant reproduces the author's package (recorded fixtures), and nothing else leaks in."""
import json
from pathlib import Path

import numpy as np
import pytest

from quantum_autopilot.control_plane import polarisation_plant as plant

pytestmark = pytest.mark.unit
FIXTURE = Path(__file__).resolve().parent / "fixtures/polarisation_plant_reference_v1.npz"


@pytest.fixture(scope="module")
def reference():
    with np.load(FIXTURE, allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


@pytest.mark.parametrize("point", ["calibrado", "desenvolvimento"])
@pytest.mark.parametrize("seed", [0, 500, 1000])
def test_trajectory_and_monitoring_match_author_package(reference, point, seed):
    meta = json.loads(bytes(reference["meta_json"]))
    step = meta["subsample"]
    run = plant.simulate(plant.OPERATING_POINTS[point], meta["duration_s"], seed)
    key = f"{point}_{seed}"
    # Tolerance, not equality: libm may differ in the last ulp across platforms.
    np.testing.assert_allclose(run.rotation[::step], reference[key + "_rotation"], rtol=0, atol=1e-10)
    np.testing.assert_allclose(run.temperature_c[::step], reference[key + "_temperature_c"], rtol=0, atol=1e-10)
    np.testing.assert_allclose(run.vibration_rms[::step], reference[key + "_vibration_rms"], rtol=0, atol=1e-10)
    np.testing.assert_array_equal(run.jump_times_s, reference[key + "_jump_times_s"])
    measured = plant.measure_rotation(run.rotation[::2], np.random.default_rng(seed + 6000))[::step]
    np.testing.assert_allclose(measured, reference[key + "_measured"], rtol=0, atol=1e-10)
    qber = plant.bb84_qber(measured)
    np.testing.assert_allclose(qber, reference[key + "_qber"], rtol=0, atol=1e-12)
    np.testing.assert_allclose(plant.bb84_secret_key_rate(plant.SIFTED_RATE_HZ, qber),
                               reference[key + "_key_rate_hz"], rtol=1e-9, atol=1e-6)


def test_operating_points_are_the_checkpoints_declared_fibres():
    calibrated = plant.OPERATING_POINTS["calibrado"].declaration()
    assert calibrated["vibration_frequency_hz"] == 60.0
    assert calibrated["vibration_harmonics"] == [1.0, 0.5, 0.25]
    assert calibrated["thermal_drift_rate_rad_per_s"] == 0.02
    assert calibrated["vibration_num_tones"] == 8.0
    development = plant.OPERATING_POINTS["desenvolvimento"].declaration()
    assert (development["vibration_frequency_hz"], development["thermal_drift_rate_rad_per_s"]) == (40.0, 0.05)


def test_rotations_are_so3_and_monitoring_is_an_estimate():
    run = plant.simulate(plant.OPERATING_POINTS["calibrado"], 0.5, 3)
    products = np.einsum("nij,nkj->nik", run.rotation, run.rotation)
    np.testing.assert_allclose(products, np.broadcast_to(np.eye(3), products.shape), atol=1e-9)
    np.testing.assert_allclose(np.linalg.det(run.rotation), 1.0, atol=1e-9)
    measured = plant.measure_rotation(run.rotation, np.random.default_rng(1))
    np.testing.assert_allclose(np.linalg.det(measured), 1.0, atol=1e-9)
    error = plant.rotation_angle(np.einsum("nij,nkj->nik", measured, run.rotation))
    assert 0.0 < np.median(error) < 0.2  # binomial noise of 340 detected photons, not the truth


def test_qber_and_key_rate_limits():
    assert plant.bb84_qber(np.eye(3)) == 0.0
    flip = np.diag([-1.0, -1.0, 1.0])
    assert plant.bb84_qber(flip) == 1.0
    assert plant.bb84_secret_key_rate(1e5, 0.0) == 1e5
    assert plant.bb84_secret_key_rate(1e5, 0.12) == 0.0


@pytest.mark.parametrize("change", [
    dict(vibration_num_tones=0), dict(vibration_harmonics=()), dict(vibration_harmonics=(0.0,)),
    dict(thermal_correlation_time_s=0.0), dict(jump_rate_per_s=-1.0), dict(vibration_amplitude_rad=float("nan")),
    dict(thermal_drift_rate_rad_per_s=True),
])
def test_invalid_parameters_refused(change):
    with pytest.raises(ValueError):
        plant.FibreParameters(**change)


@pytest.mark.parametrize("seed", [-1, 1.0, True, None])
def test_seed_must_be_explicit(seed):
    with pytest.raises(ValueError):
        plant.simulate(plant.OPERATING_POINTS["calibrado"], 0.1, seed)
