"""Numerical ports of reviewed exports, limited to offline simulated replay.

This module cannot issue commands. Routing features must already have been
computed by the originating simulator: ORION's physical feature bridge is NOT
implemented here. Action scores and critic returns are not Bell fidelities.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .artifacts import QKD_ROOT, ROUTING_ROOT, ReviewedArchive


def _finite(value, shape: tuple[int, ...], name: str) -> np.ndarray:
    raw = np.asarray(value)
    if raw.dtype.kind not in "fi" or raw.shape != shape:
        raise ValueError(f"{name}: expected real numeric shape {shape}")
    array = np.array(raw, dtype=np.float64, copy=True)
    if not np.isfinite(array).all():
        raise ValueError(f"{name}: non-finite values")
    array.flags.writeable = False
    return array


def _offline(domain: str) -> None:
    if domain != "simulated_offline":
        raise ValueError("only simulated_offline inputs admitted; no physical feature bridge")


@dataclass(frozen=True)
class RoutingResult:
    model_id: str
    archive_sha256: str
    logits: tuple[float, ...]
    probabilities: tuple[float, ...]
    critic_return: float
    greedy_index: int
    sampled_index: int
    evidence: str = field(default="simulated_offline", init=False)
    physical_action_performed: bool = field(default=False, init=False)


class RoutingReplayModel:
    """Actor 34-64-64-1; critic 11-64-64-1. No action executor."""

    def __init__(self, archive: ReviewedArchive, variant: str):
        if archive.kind != "qnet" or variant not in {"nominal", "prazo_longo"}:
            raise ValueError("unsupported routing export")
        prefix = ROUTING_ROOT + variant + "/model/policy"
        meta = archive.json(prefix + ".json")
        arrays = archive.arrays(prefix + ".npz")
        expected_keys = {f"{role}_{i}" for role in ("actor", "critic") for i in range(6)}
        if set(arrays) != expected_keys or meta["layers"] != {"actor": 3, "critic": 3}:
            raise ValueError("unexpected model layers/arrays")
        if meta["features"]["state_count"] != 11 or meta["features"]["action_count"] != 23:
            raise ValueError("unexpected routing feature contract")
        self._networks = {}
        for role, sizes in (("actor", (34, 64, 64, 1)), ("critic", (11, 64, 64, 1))):
            if meta["sizes"][role] != list(sizes):
                raise ValueError("unexpected network architecture")
            self._networks[role] = tuple(
                (_finite(arrays[f"{role}_{2*i}"], (a, b), "weight"),
                 _finite(arrays[f"{role}_{2*i+1}"], (b,), "bias"))
                for i, (a, b) in enumerate(zip(sizes[:-1], sizes[1:]))
            )
        expected_temperature = 1.0 if variant == "nominal" else 0.0
        if meta["temperature"] != expected_temperature:
            raise ValueError("unexpected deployment temperature")
        self._temperature = expected_temperature
        self._model_id = f"qnet.routing.{variant}"
        self._sha256 = archive.sha256

    def _forward(self, role: str, inputs: np.ndarray) -> np.ndarray:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            activation = inputs
            for i, (weight, bias) in enumerate(self._networks[role]):
                activation = activation @ weight + bias
                if i < 2:
                    activation = np.maximum(activation, 0.0)
        if not np.isfinite(activation).all():
            raise ValueError("non-finite model output")
        return activation

    def evaluate(self, state_features, action_features, actions, *, domain: str,
                 rng: np.random.Generator) -> RoutingResult:
        _offline(domain)
        if not isinstance(rng, np.random.Generator):
            raise ValueError("explicit replay random generator required")
        if not isinstance(actions, (list, tuple)) or not 1 <= len(actions) <= 1024:
            raise ValueError("one to 1024 ordered candidate actions required")
        state = _finite(state_features, (11,), "state_features")
        features = _finite(action_features, (len(actions), 23), "action_features")
        kinds = ("WAIT", "GENERATE", "PURIFY", "SWAP", "DELIVER", "DISCARD")
        for row, action in zip(features, actions):
            if not isinstance(action, dict) or action.get("kind") not in kinds:
                raise ValueError("unsupported action description")
            expected = np.zeros(6)
            expected[kinds.index(action["kind"])] = 1.0
            if not np.array_equal(row[:6], expected):
                raise ValueError("action kind and feature row differ")
        repeated = np.broadcast_to(state, (len(actions), 11))
        logits = self._forward("actor", np.concatenate([repeated, features], axis=1))[:, 0]
        greedy = int(np.argmax(logits))
        if self._temperature == 0:
            probabilities = np.zeros_like(logits)
            probabilities[greedy] = 1.0
            selected = greedy
        else:
            probabilities = np.exp((logits - np.max(logits)) / self._temperature)
            probabilities /= probabilities.sum()
            selected = int(rng.choice(len(probabilities), p=probabilities))
        value = float(self._forward("critic", state[None, :])[0, 0])
        return RoutingResult(self._model_id, self._sha256, tuple(logits.tolist()),
                             tuple(probabilities.tolist()), value, greedy, selected)


@dataclass(frozen=True)
class SyncResult:
    archive_sha256: str
    last_timestamp_s: float
    target_timestamp_s: float
    predicted_offset_s: float
    persistence_offset_s: float
    model_id: str = field(default="qkd.sync.ridge.1s.h2s", init=False)
    evidence: str = field(default="simulated_offline", init=False)
    physical_action_performed: bool = field(default=False, init=False)


class SyncReplayModel:
    """Eight samples, one second cadence, fixed two second horizon, no warm-up substitution."""

    def __init__(self, archive: ReviewedArchive):
        if archive.kind != "qkd":
            raise ValueError("QKD export required")
        config = archive.json(QKD_ROOT + "modelos/sincronizador_ridge.json")
        if (config["window"] != 8 or config["scale"] != 1e-9
                or config["update_period_s"] != 1.0 or config["horizon_s"] != 2.0):
            raise ValueError("unsupported synchronizer contract")
        self._coefficients = _finite(config["coefficients"], (9,), "coefficients")
        self._intercept = float(_finite([config["intercept"]], (1,), "intercept")[0])
        self._sha256 = archive.sha256

    def predict(self, timestamps_s, offsets_s, temperatures_c, *, domain: str,
                horizon_s: float = 2.0) -> SyncResult:
        _offline(domain)
        if isinstance(horizon_s, bool) or horizon_s != 2.0:
            raise ValueError("model trained only for a two second horizon")
        times = _finite(timestamps_s, (8,), "timestamps_s")
        offsets = _finite(offsets_s, (8,), "offsets_s")
        temperatures = _finite(temperatures_c, (8,), "temperatures_c")
        if not np.allclose(np.diff(times), 1.0, rtol=0, atol=1e-6):
            raise ValueError("eight ordered samples with one second cadence required")
        with np.errstate(over="raise", invalid="raise"):
            lags = [(offsets[-1-i] - offsets[-1]) / 1e-9 for i in range(1, 8)]
            delta = temperatures[-1] - temperatures[0]
            features = np.array(lags + [delta, delta / 7])
            predicted = float(offsets[-1] + (features @ self._coefficients + self._intercept) * 1e-9)
        if not np.isfinite(predicted):
            raise ValueError("non-finite synchronizer output")
        return SyncResult(self._sha256, float(times[-1]), float(times[-1] + 2.0),
                          predicted, float(offsets[-1]))
