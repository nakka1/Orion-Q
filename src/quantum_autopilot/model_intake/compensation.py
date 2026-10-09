"""Offline, opt-in polarisation prediction; never an actuator or fidelity model.

Numerical path reviewed against the author's pinned qnet export. Only the four
exported checkpoints are supported. Declaring an operating point is a contract,
not an automatic OOD detector or experimental validation of that declaration.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .artifacts import COMPENSATION_ROOT, ReviewedArchive
from .inference import _finite, _offline

PERIOD_S = 0.002
HORIZON_S = 0.04
WINDOW = 16
IDENTITY = np.eye(3)


def _rotation(value, shape=(3, 3)) -> np.ndarray:
    matrix = _finite(value, shape, "rotation")
    if (not np.allclose(matrix @ np.swapaxes(matrix, -1, -2), IDENTITY, atol=1e-7, rtol=0)
            or not np.allclose(np.linalg.det(matrix), 1.0, atol=1e-7, rtol=0)):
        raise ValueError("rotation must belong to SO(3); no implicit projection")
    return matrix


def _log_map(matrix: np.ndarray) -> np.ndarray:
    cosine = (matrix[0, 0] + matrix[1, 1] + matrix[2, 2] - 1.0) / 2.0
    skew = np.array([matrix[2, 1] - matrix[1, 2], matrix[0, 2] - matrix[2, 0],
                     matrix[1, 0] - matrix[0, 1]])
    if cosine >= 1.0 - 1e-12:
        return 0.5 * skew
    if cosine <= -1.0 + 1e-9:
        angle = float(np.arccos(np.clip((np.trace(matrix) - 1.0) / 2.0, -1.0, 1.0)))
        if angle <= np.pi - 1e-5:
            return angle / (2.0 * np.sin(angle)) * skew
        symmetric = (matrix + IDENTITY) / 2.0
        axis = symmetric[:, int(np.argmax(np.diag(symmetric)))]
        norm = float(np.linalg.norm(axis))
        axis = axis / norm if norm > 0 else np.zeros(3)
        sign = float(np.sign(np.dot(axis, skew)))
        return axis * (sign if sign != 0 else 1.0) * angle
    angle = np.arccos(cosine)
    return angle / (2.0 * np.sin(angle)) * skew


def _exp_map(vector: np.ndarray) -> np.ndarray:
    angle = float(np.linalg.norm(vector))
    if not np.isfinite(angle):
        raise ValueError("non-finite rotation increment")
    if angle < 1e-12:
        return IDENTITY.copy()
    axis = vector / angle
    cosine, sine = np.cos(angle), np.sin(angle)
    x, y, z = axis
    cross = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    return cosine * IDENTITY + sine * cross + (1.0 - cosine) * np.outer(axis, axis)


def _matrix_tuple(value):
    return tuple(tuple(float(v) for v in row) for row in value)


@dataclass(frozen=True)
class CompensationResult:
    model_id: str
    archive_sha256: str
    operating_point: str
    last_timestamp_s: float
    target_timestamp_s: float
    status: str
    predicted: tuple[tuple[float, ...], ...]
    persistence: tuple[tuple[float, ...], ...]
    features: tuple[float, ...] | None
    scaled: tuple[float, ...] | None
    increment: tuple[float, ...] | None
    evidence: str = field(default="simulated_offline", init=False)
    physical_action_performed: bool = field(default=False, init=False)


class CompensationReplayModel:
    """Explicit regime + architecture; stream state commits only on success."""

    def __init__(self, archive: ReviewedArchive, operating_point: str, architecture: str):
        if (archive.kind != "qnet" or operating_point not in {"calibrado", "desenvolvimento"}
                or architecture not in {"ridge", "mlp"}):
            raise ValueError("unsupported compensation checkpoint")
        prefix = COMPENSATION_ROOT + operating_point + "/model/" + architecture
        meta, arrays = archive.json(prefix + ".json"), archive.arrays(prefix + ".npz")
        spec = {"window": 16, "use_covariates": True, "horizon_scale": 0.002,
                "lags": None, "tone_lags": 0}
        if meta["kind"] != architecture or meta["spec"] != spec:
            raise ValueError("unsupported compensation feature specification")
        point = meta["operating_point"]
        frequency = 60.0 if operating_point == "calibrado" else 40.0
        drift = 0.02 if operating_point == "calibrado" else 0.05
        if (point["control_period_s"] != PERIOD_S or point["horizon_s"] != HORIZON_S
                or point["monitoring_photons_per_basis"] != 400
                or point["fibre"]["vibration_frequency_hz"] != frequency
                or point["fibre"]["thermal_drift_rate_rad_per_s"] != drift):
            raise ValueError("incompatible operating point metadata")
        keys = {"standardiser_mean", "standardiser_scale"}
        if architecture == "ridge":
            keys |= {"coef", "intercept"}
            self._coef = _finite(arrays["coef"], (3, 48), "coef")
            self._intercept = _finite(arrays["intercept"], (3,), "intercept")
        else:
            if meta["layers"] != 3 or meta["sizes"] != [48, 192, 192, 3]:
                raise ValueError("incompatible compensation MLP architecture")
            sizes = (48, 192, 192, 3)
            keys |= {f"{part}_{i}" for part in ("weight", "bias") for i in range(3)}
            self._layers = tuple(
                (_finite(arrays[f"weight_{i}"], (a, b), "weight"),
                 _finite(arrays[f"bias_{i}"], (b,), "bias"))
                for i, (a, b) in enumerate(zip(sizes[:-1], sizes[1:])))
        if set(arrays) != keys:
            raise ValueError("unexpected compensation arrays")
        self._mean = _finite(arrays["standardiser_mean"], (48,), "mean")
        self._scale = _finite(arrays["standardiser_scale"], (48,), "scale")
        if np.any(self._scale <= 0):
            raise ValueError("standardiser scale must be positive")
        self._operating_point, self._architecture = operating_point, architecture
        self._model_id = f"qnet.compensation.{operating_point}.{architecture}"
        self._sha256 = archive.sha256
        self.reset()

    def reset(self) -> None:
        """Explicit new stream; no history shared across campaigns/channels/models."""
        self._history: tuple[np.ndarray, ...] = ()
        self._last_time: float | None = None
        self._stream_id: str | None = None

    def _contract(self, domain, operating_point, horizon_s, monitoring_photons_per_basis):
        _offline(domain)
        if operating_point != self._operating_point:
            raise ValueError("operating point differs from checkpoint; no automatic model substitution")
        if isinstance(horizon_s, bool) or horizon_s != HORIZON_S:
            raise ValueError("compensator trained only for 40 ms horizon")
        if isinstance(monitoring_photons_per_basis, bool) or monitoring_photons_per_basis != 400:
            raise ValueError("checkpoint requires 400 monitoring photons per basis")

    def _calculate(self, history, covariates, time_s) -> CompensationResult:
        now = history[-1]
        if len(history) < WINDOW:
            return CompensationResult(self._model_id, self._sha256, self._operating_point,
                                      time_s, time_s + HORIZON_S, "warmup_persistence",
                                      _matrix_tuple(now), _matrix_tuple(now), None, None, None)
        with np.errstate(over="raise", divide="raise", invalid="raise"):
            features = np.concatenate([_log_map(history[-1-i] @ now.T) / PERIOD_S
                                       for i in range(1, WINDOW)] + [covariates])
            scaled = (features[None, :] - self._mean) / self._scale
            if self._architecture == "ridge":
                increment = (scaled @ self._coef.T + self._intercept).reshape(3)
            else:
                activation = scaled
                for i, (weight, bias) in enumerate(self._layers):
                    activation = activation @ weight + bias
                    if i < 2:
                        activation = np.maximum(activation, 0.0)
                increment = activation.reshape(3)
            predicted = _rotation(_exp_map(increment * PERIOD_S) @ now)
        return CompensationResult(self._model_id, self._sha256, self._operating_point,
                                  time_s, time_s + HORIZON_S, "model",
                                  _matrix_tuple(predicted), _matrix_tuple(now),
                                  tuple(features.tolist()), tuple(scaled.reshape(-1).tolist()),
                                  tuple(increment.tolist()))

    @staticmethod
    def _covariates(value):
        covariates = _finite(value, (3,), "temperature_C,dT_dt_K_s,vibration_rms_rad")
        if covariates[0] < -273.15 or covariates[2] < 0:
            raise ValueError("invalid temperature or negative vibration RMS")
        return covariates

    def predict_window(self, timestamps_s, history, covariates, *, domain: str,
                       operating_point: str, horizon_s: float = HORIZON_S,
                       monitoring_photons_per_basis: int = 400) -> CompensationResult:
        """Stateless window inference, independent of streaming history."""
        self._contract(domain, operating_point, horizon_s, monitoring_photons_per_basis)
        times = _finite(timestamps_s, (WINDOW,), "timestamps_s")
        if not np.allclose(np.diff(times), PERIOD_S, rtol=0, atol=1e-9):
            raise ValueError("16 ordered timestamps with 2 ms cadence required")
        rotations = _rotation(history, (WINDOW, 3, 3))
        return self._calculate(rotations, self._covariates(covariates), float(times[-1]))

    def predict(self, time_s, measured, covariates, *, domain: str, operating_point: str,
                stream_id: str, horizon_s: float = HORIZON_S,
                monitoring_photons_per_basis: int = 400) -> CompensationResult:
        self._contract(domain, operating_point, horizon_s, monitoring_photons_per_basis)
        if not isinstance(stream_id, str) or not stream_id.strip() or len(stream_id) > 256:
            raise ValueError("nonempty bounded stream identity required")
        if self._stream_id is not None and self._stream_id != stream_id:
            raise ValueError("stream changed: explicitly reset before switching campaign/channel")
        time = float(_finite([time_s], (1,), "time_s")[0])
        if self._last_time is not None and abs(time - self._last_time - PERIOD_S) > 1e-9:
            raise ValueError("stream requires consecutive 2 ms samples; reset after a gap")
        rotation = _rotation(measured)
        covariates = self._covariates(covariates)
        candidate = (self._history + (rotation,))[-WINDOW:]
        result = self._calculate(candidate, covariates, time)
        self._history, self._last_time, self._stream_id = candidate, time, stream_id
        return result
