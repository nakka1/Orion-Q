"""Simulated aerial-fibre polarisation plant for closed-loop evaluation. Never an actuator.

Transcribed, not imported, from the author's qnet_autopilot package, so ORION-Q
keeps its rule of reading external artifacts as data and never executing them:

* ``physics/fiber.py`` — ``PolarizationDriftModel.simulate(track_rotation=True)``
  (Ornstein-Uhlenbeck thermal drift, dT/dt coupling, Rice-synthesised vibration
  with harmonics, Poisson jumps) and its quaternion/Rodrigues plumbing;
* ``compensation/monitoring.py`` — ``measure_rotation`` (two probe states, three
  analyser axes, binomial photon counts, Gram-Schmidt back onto SO(3));
* ``compensation/closed_loop.py`` — ``bb84_qber``;
* ``physics/link.py`` — ``binary_entropy`` and ``bb84_secret_key_rate``.

The arithmetic and the random-draw order are the author's, so a seed produces
the same trajectory and the same monitoring noise as the original package;
``tests/test_polarisation_plant.py`` checks this against recorded fixtures.
The two operating points are the ones the exported checkpoints declare: the
calibrated aerial fibre (60 Hz power-line tone with 120/180 Hz harmonics,
0.02 rad/s thermal drift) and the earlier development point (40 Hz, 0.05 rad/s).
Evidence produced here is simulated; nothing in this module touches hardware.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

SAMPLE_INTERVAL_S = 1.0e-3
CONTROL_PERIOD_S = 2.0e-3
LATENCY_S = 40.0e-3
PHOTONS_PER_BASIS = 400
DETECTOR_EFFICIENCY = 0.85
SIFTED_RATE_HZ = 1.0e5
ERROR_CORRECTION_INEFFICIENCY = 1.16
BB84_QBER_THRESHOLD = 0.11
PROBE_STATES = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
BB84_BASES = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
IDENTITY = np.eye(3)


@dataclass(frozen=True)
class FibreParameters:
    """The author's phase-3 aerial-fibre operating point (``scripts/task3.py``)."""

    thermal_drift_rate_rad_per_s: float = 0.05
    thermal_correlation_time_s: float = 60.0
    vibration_amplitude_rad: float = 0.30
    vibration_frequency_hz: float = 40.0
    vibration_bandwidth_hz: float = 6.0
    vibration_num_tones: int = 8
    jump_rate_per_s: float = 0.05
    jump_scale_rad: float = 0.35
    thermal_per_kelvin_rad_per_s: float = 0.2
    vibration_harmonics: tuple[float, ...] = (1.0,)

    def __post_init__(self):
        values = asdict(self)
        harmonics = values.pop("vibration_harmonics")
        if (type(self.vibration_num_tones) is not int or self.vibration_num_tones < 1
                or not isinstance(self.vibration_harmonics, tuple) or not self.vibration_harmonics):
            raise ValueError("invalid vibration synthesis")
        for name, value in list(values.items()) + [("vibration_harmonics", h) for h in harmonics]:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.thermal_correlation_time_s <= 0 or sum(h * h for h in harmonics) <= 0:
            raise ValueError("positive correlation time and nonzero harmonics required")

    def declaration(self):
        """Field names and values exactly as the exported ``model/*.json`` declares them."""
        values = asdict(self)
        values["vibration_num_tones"] = float(self.vibration_num_tones)
        values["vibration_harmonics"] = list(self.vibration_harmonics)
        return values


OPERATING_POINTS = {
    "calibrado": FibreParameters(thermal_drift_rate_rad_per_s=0.02, vibration_frequency_hz=60.0,
                                 vibration_harmonics=(1.0, 0.5, 0.25)),
    "desenvolvimento": FibreParameters(),
}


@dataclass(frozen=True)
class PlantTrajectory:
    """Hidden truth of one run. Controllers only ever see ``measure_rotation`` output."""

    time_s: np.ndarray
    rotation: np.ndarray
    temperature_c: np.ndarray
    vibration_rms: np.ndarray
    jump_times_s: np.ndarray

    def covariates(self):
        """Sensor covariates at every sample: temperature, dT/dt, vibration RMS."""
        dt_s = float(self.time_s[1] - self.time_s[0]) if len(self.time_s) > 1 else 1.0
        return np.stack([self.temperature_c, np.gradient(self.temperature_c, dt_s), self.vibration_rms], axis=-1)


def _quaternions(axis, angle):
    axis = np.atleast_2d(np.asarray(axis, dtype=float))
    angle = np.atleast_1d(np.asarray(angle, dtype=float))
    norm = np.linalg.norm(axis, axis=-1, keepdims=True)
    unit = np.divide(axis, norm, out=np.zeros_like(axis), where=norm > 0)
    half = angle / 2.0
    return np.concatenate([np.cos(half)[:, None], unit * np.sin(half)[:, None]], axis=-1)


def _accumulate(step_vectors, time_s, jump_times, jump_quaternions):
    increments = _quaternions(step_vectors, np.linalg.norm(step_vectors, axis=-1))
    cumulative = np.empty_like(increments)
    w, x, y, z = 1.0, 0.0, 0.0, 0.0
    next_jump = 0
    for index in range(increments.shape[0]):
        aw, ax, ay, az = increments[index]
        w, x, y, z = (aw * w - ax * x - ay * y - az * z,
                      aw * x + ax * w + ay * z - az * y,
                      aw * y - ax * z + ay * w + az * x,
                      aw * z + ax * y - ay * x + az * w)
        while next_jump < jump_times.size and jump_times[next_jump] <= time_s[index]:
            jw, jx, jy, jz = jump_quaternions[next_jump]
            w, x, y, z = (jw * w - jx * x - jy * y - jz * z,
                          jw * x + jx * w + jy * z - jz * y,
                          jw * y - jx * z + jy * w + jz * x,
                          jw * z + jx * y - jy * x + jz * w)
            next_jump += 1
        cumulative[index] = (w, x, y, z)
    return cumulative


def _rotate_by_quaternion(quaternions, vector):
    w, q_vector = quaternions[:, 0:1], quaternions[:, 1:4]
    temp = np.cross(q_vector, vector) + w * vector
    return vector + 2.0 * np.cross(q_vector, temp)


def _rotate_about_axis(vectors, rotation_vectors):
    angles = np.linalg.norm(rotation_vectors, axis=-1, keepdims=True)
    axes = np.divide(rotation_vectors, angles, out=np.zeros_like(rotation_vectors), where=angles > 0)
    cos, sin = np.cos(angles), np.sin(angles)
    dot = np.sum(axes * vectors, axis=-1, keepdims=True)
    return vectors * cos + np.cross(axes, vectors) * sin + axes * dot * (1.0 - cos)


def simulate(parameters: FibreParameters, duration_s: float, seed: int,
             dt_s: float = SAMPLE_INTERVAL_S) -> PlantTrajectory:
    """One plant run; same draws, in the same order, as the author's ``simulate``."""
    if type(parameters) is not FibreParameters:
        raise ValueError("typed FibreParameters required")
    if type(seed) is not int or seed < 0:
        raise ValueError("explicit non-negative integer seed required")
    if not (np.isfinite(duration_s) and np.isfinite(dt_s)) or duration_s <= 0 or dt_s <= 0:
        raise ValueError("positive finite duration and step required")
    generator = np.random.default_rng(seed)
    count = int(round(duration_s / dt_s))
    time_s = np.arange(count, dtype=float) * dt_s

    # Temperature: 24 h sinusoid plus slow noise (the author's synthetic series).
    noise = np.cumsum(generator.normal(0.0, 0.01, size=count))
    noise -= noise.mean()
    temperature = 20.0 + 3.0 * np.sin(2 * np.pi * time_s / 86400.0) + 0.1 * noise

    # Thermal angular velocity: exactly discretised OU plus a dT/dt drive.
    decay = np.exp(-dt_s / parameters.thermal_correlation_time_s)
    sigma = parameters.thermal_drift_rate_rad_per_s
    increment_std = sigma * np.sqrt(max(1.0 - decay ** 2, 0.0))
    rate = np.empty((count, 3), dtype=float)
    current = generator.normal(0.0, sigma, size=3)
    shocks = generator.normal(0.0, increment_std, size=(count, 3))
    for index in range(count):
        current = decay * current + shocks[index]
        rate[index] = current
    if parameters.thermal_per_kelvin_rad_per_s and count > 1:
        axis = generator.normal(size=3)
        axis /= np.linalg.norm(axis)
        rate += parameters.thermal_per_kelvin_rad_per_s * np.gradient(temperature, dt_s)[:, None] * axis

    # Vibration: equal-power tones around each harmonic, random phases per axis.
    vibration = np.zeros((3, count), dtype=float)
    amplitude, tones = parameters.vibration_amplitude_rad, parameters.vibration_num_tones
    if amplitude > 0:
        weights = np.asarray(parameters.vibration_harmonics, dtype=float)
        weights = weights / float(np.sqrt(np.sum(weights ** 2)))
        for order, weight in enumerate(weights, start=1):
            if weight <= 0:
                continue
            frequencies = (parameters.vibration_frequency_hz * order + parameters.vibration_bandwidth_hz
                           * (generator.random((3, tones)) - 0.5))
            phases = generator.uniform(0.0, 2 * np.pi, size=(3, tones))
            phase = 2 * np.pi * frequencies[:, :, None] * time_s[None, None, :] + phases[:, :, None]
            vibration += amplitude * weight * np.sqrt(2.0 / tones) * np.sin(phase).sum(axis=1)
    vibration = vibration.T
    vibration_rms = np.linalg.norm(vibration, axis=-1)

    # Jumps: Poisson-timed rotations of exponential size.
    jump_times, jump_quaternions = np.empty(0), np.empty((0, 4))
    if parameters.jump_rate_per_s > 0:
        jumps = generator.poisson(parameters.jump_rate_per_s * duration_s)
        if jumps:
            jump_times = np.sort(generator.uniform(0.0, duration_s, size=jumps))
            axes = generator.normal(size=(jumps, 3))
            jump_quaternions = _quaternions(axes, generator.exponential(parameters.jump_scale_rad, size=jumps))

    thermal = _accumulate(rate * dt_s, time_s, jump_times, jump_quaternions)
    rotation = np.stack([_rotate_about_axis(_rotate_by_quaternion(thermal, axis), vibration)
                         for axis in IDENTITY], axis=-1)
    return PlantTrajectory(time_s, rotation, temperature, vibration_rms, jump_times)


def orthonormalise(columns):
    first, second = columns[..., 0], columns[..., 1]
    first = first / np.maximum(np.linalg.norm(first, axis=-1, keepdims=True), 1e-12)
    second = second - np.sum(second * first, axis=-1, keepdims=True) * first
    second = second / np.maximum(np.linalg.norm(second, axis=-1, keepdims=True), 1e-12)
    return np.stack([first, second, np.cross(first, second)], axis=-1)


def measure_rotation(rotation, generator: np.random.Generator,
                     photons_per_basis: int = PHOTONS_PER_BASIS,
                     detector_efficiency: float = DETECTOR_EFFICIENCY):
    """Finite-photon estimate of the fibre rotation; always a genuine SO(3) element."""
    photons = max(int(round(photons_per_basis * detector_efficiency)), 1)
    arriving = np.einsum("...ij,kj->...ki", np.asarray(rotation, dtype=float), PROBE_STATES)
    counts = generator.binomial(photons, np.clip((1.0 + arriving) / 2.0, 0.0, 1.0))
    return orthonormalise(np.moveaxis(2.0 * counts / photons - 1.0, -2, -1))


def rotation_angle(matrix):
    trace = np.trace(np.asarray(matrix, dtype=float), axis1=-2, axis2=-1)
    return np.arccos(np.clip((trace - 1.0) / 2.0, -1.0, 1.0))


def bb84_qber(residual):
    """QBER of a residual rotation, averaged over BB84's two Stokes bases."""
    rotated = np.einsum("...ij,kj->...ki", np.asarray(residual, dtype=float), BB84_BASES)
    alignment = np.einsum("...ki,ki->...k", rotated, BB84_BASES)
    return np.mean((1.0 - alignment) / 2.0, axis=-1)


def binary_entropy(probability):
    p = np.clip(np.asarray(probability, dtype=float), 0.0, 1.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        entropy = -p * np.log2(p) - (1 - p) * np.log2(1 - p)
    return np.where((p <= 0) | (p >= 1), 0.0, entropy)


def bb84_secret_key_rate(sifted_rate_hz, qber):
    """Asymptotic one-way BB84 rate, clamped at zero (the author's link model)."""
    entropy = binary_entropy(qber)
    fraction = 1.0 - ERROR_CORRECTION_INEFFICIENCY * entropy - entropy
    return np.maximum(np.asarray(sifted_rate_hz, dtype=float) * fraction, 0.0)


__all__ = ["BB84_QBER_THRESHOLD", "CONTROL_PERIOD_S", "FibreParameters", "LATENCY_S", "OPERATING_POINTS",
           "PHOTONS_PER_BASIS", "PlantTrajectory", "SAMPLE_INTERVAL_S", "SIFTED_RATE_HZ", "bb84_qber",
           "bb84_secret_key_rate", "measure_rotation", "rotation_angle", "simulate"]
