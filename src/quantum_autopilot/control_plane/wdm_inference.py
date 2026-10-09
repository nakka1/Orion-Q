"""Guarded WDM-model inference boundary for the control-plane sandbox.

This module connects the repository's existing fidelity predictors to the
non-physical sandbox without weakening its evidence boundary.  The first
supported lane accepts simulated WDM traces only, produces a typed fidelity
distribution and delegates the resulting snapshot to :class:`ControllerRuntime`.
It never performs physical actuation and never upgrades simulated fidelity to
measured evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

import numpy as np
import torch

from quantum_autopilot.control_plane.contracts import (
    ControlMode,
    NetworkStateSnapshot,
    _aware_timestamp,
    _finite,
    _nonempty,
)
from quantum_autopilot.core import EvidenceKind, Provenance
from quantum_autopilot.core.contracts import JSONSerializableMixin
from quantum_twin.optical.wdm import QuantumNetworkDatasetV3


WDM_INFERENCE_SCHEMA_VERSION = "1.0.0"


class WDMModelOutputKind(str, Enum):
    """Output signatures supported by the original WDM fidelity models."""

    POINT = "point"
    PROBABILISTIC = "probabilistic"
    DUAL_HEAD = "dual_head"


def _sha256_digest(name: str, value: Any) -> str:
    text = _nonempty(name, value).lower()
    if len(text) != 64:
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    try:
        int(text, 16)
    except ValueError as exc:
        raise ValueError(f"{name} must be a SHA-256 hex digest") from exc
    return text


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hash_torch_model(model: Any) -> str:
    """Hash tensor names, shapes, dtypes and bytes without serializing code."""

    state_dict = getattr(model, "state_dict", None)
    if not callable(state_dict):
        raise ValueError("model must expose state_dict() for artifact hashing")
    digest = hashlib.sha256()
    for name, tensor in sorted(state_dict().items()):
        if not isinstance(tensor, torch.Tensor):
            raise ValueError("model state_dict values must be tensors")
        value = tensor.detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(str(value.dtype).encode("ascii"))
        digest.update(_canonical_json(list(value.shape)).encode("ascii"))
        digest.update(value.numpy().tobytes(order="C"))
    return digest.hexdigest()


@dataclass(frozen=True)
class WDMModelSpec(JSONSerializableMixin):
    """Immutable preprocessing, lineage and output contract for one model."""

    model_name: str
    model_version: str
    output_kind: WDMModelOutputKind | str
    feature_columns: tuple[str, ...]
    window_size: int
    feature_min: tuple[float, ...]
    feature_max: tuple[float, ...]
    model_sha256: str
    config_sha256: str
    dataset_sha256: str
    calibrated_point_uncertainty: float | None = None
    uncertainty_calibration: str = ""
    availability_gate: float = 0.5
    training_evidence_kind: EvidenceKind | str = EvidenceKind.SIMULATED
    target_semantics: str = "simulated_future_bell_fidelity"
    schema_version: str = WDM_INFERENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_name", _nonempty("model_name", self.model_name))
        object.__setattr__(self, "model_version", _nonempty("model_version", self.model_version))
        object.__setattr__(self, "output_kind", WDMModelOutputKind(self.output_kind))
        features = tuple(_nonempty("feature column", item) for item in self.feature_columns)
        if not features or len(features) != len(set(features)):
            raise ValueError("feature_columns must be non-empty and unique")
        allowed = set(QuantumNetworkDatasetV3.WDM_FEATURE_COLUMNS)
        unsupported = sorted(set(features).difference(allowed))
        if unsupported:
            raise ValueError(
                "WDM inference cannot consume fidelity or quantum-privileged features: "
                f"{unsupported}"
            )
        object.__setattr__(self, "feature_columns", features)
        if isinstance(self.window_size, bool) or not isinstance(self.window_size, int):
            raise ValueError("window_size must be an integer")
        if self.window_size < 2:
            raise ValueError("window_size must be >= 2")
        minima = tuple(
            _finite(f"feature_min[{index}]", item)
            for index, item in enumerate(self.feature_min)
        )
        maxima = tuple(
            _finite(f"feature_max[{index}]", item)
            for index, item in enumerate(self.feature_max)
        )
        if len(minima) != len(features) or len(maxima) != len(features):
            raise ValueError("feature bounds must align with feature_columns")
        if any(high < low for low, high in zip(minima, maxima)):
            raise ValueError("every feature_max must be >= feature_min")
        object.__setattr__(self, "feature_min", minima)
        object.__setattr__(self, "feature_max", maxima)
        for name in ("model_sha256", "config_sha256", "dataset_sha256"):
            object.__setattr__(self, name, _sha256_digest(name, getattr(self, name)))
        uncertainty = self.calibrated_point_uncertainty
        if self.output_kind in {WDMModelOutputKind.POINT, WDMModelOutputKind.DUAL_HEAD}:
            if uncertainty is None:
                raise ValueError(
                    "point and dual-head models require held-out calibrated uncertainty"
                )
            uncertainty = _finite(
                "calibrated_point_uncertainty", uncertainty, minimum=0.0, maximum=1.0
            )
            object.__setattr__(
                self,
                "uncertainty_calibration",
                _nonempty("uncertainty_calibration", self.uncertainty_calibration),
            )
        elif uncertainty is not None:
            uncertainty = _finite(
                "calibrated_point_uncertainty", uncertainty, minimum=0.0, maximum=1.0
            )
        object.__setattr__(self, "calibrated_point_uncertainty", uncertainty)
        object.__setattr__(
            self,
            "availability_gate",
            _finite("availability_gate", self.availability_gate, minimum=0.0, maximum=1.0),
        )
        evidence_kind = EvidenceKind(self.training_evidence_kind)
        if evidence_kind is not EvidenceKind.SIMULATED:
            raise ValueError("the current WDM sandbox lane permits simulated training only")
        object.__setattr__(self, "training_evidence_kind", evidence_kind)
        object.__setattr__(
            self, "target_semantics", _nonempty("target_semantics", self.target_semantics)
        )
        if self.target_semantics != "simulated_future_bell_fidelity":
            raise ValueError("the current sandbox lane predicts simulated Bell fidelity only")
        if self.schema_version != WDM_INFERENCE_SCHEMA_VERSION:
            raise ValueError("unsupported WDM inference schema_version")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WDMModelSpec":
        values = dict(payload)
        for name in ("feature_columns", "feature_min", "feature_max"):
            if name in values:
                values[name] = tuple(values[name])
        return cls(**values)


@dataclass(frozen=True)
class WDMTelemetryPoint(JSONSerializableMixin):
    timestamp_utc: str
    values: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "timestamp_utc", _aware_timestamp("timestamp_utc", self.timestamp_utc)
        )
        if not isinstance(self.values, Mapping):
            raise ValueError("telemetry point values must be an object")
        object.__setattr__(self, "values", dict(self.values))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WDMTelemetryPoint":
        return cls(**dict(payload))


@dataclass(frozen=True)
class WDMControlRequest(JSONSerializableMixin):
    """Model-independent request used to construct a guarded snapshot."""

    request_id: str
    node_id: str
    evaluated_at_utc: str
    state_version: int
    telemetry_window: tuple[WDMTelemetryPoint, ...]
    memory_state: str
    available_pair_fidelities: tuple[float, ...]
    memory_ages_s: tuple[float, ...]
    provenance: Provenance | Mapping[str, Any]
    integrity_verified: bool = True
    operator_interlock: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _nonempty("request_id", self.request_id))
        object.__setattr__(self, "node_id", _nonempty("node_id", self.node_id))
        object.__setattr__(
            self,
            "evaluated_at_utc",
            _aware_timestamp("evaluated_at_utc", self.evaluated_at_utc),
        )
        if isinstance(self.state_version, bool) or not isinstance(self.state_version, int):
            raise ValueError("state_version must be an integer")
        if self.state_version < 0:
            raise ValueError("state_version must be >= 0")
        points = tuple(
            item if isinstance(item, WDMTelemetryPoint) else WDMTelemetryPoint.from_dict(item)
            for item in self.telemetry_window
        )
        if not points:
            raise ValueError("telemetry_window cannot be empty")
        object.__setattr__(self, "telemetry_window", points)
        pairs = tuple(
            _finite(
                f"available_pair_fidelities[{index}]", item, minimum=0.0, maximum=1.0
            )
            for index, item in enumerate(self.available_pair_fidelities)
        )
        ages = tuple(
            _finite(f"memory_ages_s[{index}]", item, minimum=0.0)
            for index, item in enumerate(self.memory_ages_s)
        )
        if len(pairs) != len(ages):
            raise ValueError("memory_ages_s must align with available_pair_fidelities")
        object.__setattr__(self, "available_pair_fidelities", pairs)
        object.__setattr__(self, "memory_ages_s", ages)
        provenance = self.provenance
        if isinstance(provenance, Mapping):
            provenance = Provenance.from_dict(provenance)
        if not isinstance(provenance, Provenance):
            raise ValueError("provenance must be provided")
        object.__setattr__(self, "provenance", provenance)
        for name in ("integrity_verified", "operator_interlock"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be an object")
        object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WDMControlRequest":
        values = dict(payload)
        for name in ("telemetry_window", "available_pair_fidelities", "memory_ages_s"):
            if name in values:
                values[name] = tuple(values[name])
        return cls(**values)


@dataclass(frozen=True)
class WDMFidelityPrediction(JSONSerializableMixin):
    mean: float
    uncertainty: float
    out_of_distribution: bool
    ood_fraction: float
    window_sha256: str
    model_name: str
    model_version: str
    output_kind: WDMModelOutputKind | str
    availability_probability: float | None = None
    conditional_fidelity: float | None = None
    schema_version: str = WDM_INFERENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "mean", _finite("mean", self.mean, minimum=0.0, maximum=1.0))
        object.__setattr__(
            self,
            "uncertainty",
            _finite("uncertainty", self.uncertainty, minimum=0.0),
        )
        if not isinstance(self.out_of_distribution, bool):
            raise ValueError("out_of_distribution must be boolean")
        object.__setattr__(
            self, "ood_fraction", _finite("ood_fraction", self.ood_fraction, minimum=0, maximum=1)
        )
        object.__setattr__(self, "window_sha256", _sha256_digest("window_sha256", self.window_sha256))
        object.__setattr__(self, "model_name", _nonempty("model_name", self.model_name))
        object.__setattr__(self, "model_version", _nonempty("model_version", self.model_version))
        object.__setattr__(self, "output_kind", WDMModelOutputKind(self.output_kind))
        for name in ("availability_probability", "conditional_fidelity"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self, name, _finite(name, value, minimum=0.0, maximum=1.0)
                )
        if self.schema_version != WDM_INFERENCE_SCHEMA_VERSION:
            raise ValueError("unsupported WDM inference schema_version")


class WDMModelInferenceAdapter:
    """Run an existing WDM model behind strict preprocessing and lineage gates."""

    def __init__(self, model: Any, spec: WDMModelSpec | Mapping[str, Any]) -> None:
        self.model = model
        self.spec = spec if isinstance(spec, WDMModelSpec) else WDMModelSpec.from_dict(spec)
        if hash_torch_model(model) != self.spec.model_sha256:
            raise ValueError("model state does not match model_sha256")

    @staticmethod
    def _as_scalar(value: Any, name: str) -> float:
        if isinstance(value, torch.Tensor):
            array = value.detach().cpu().numpy().reshape(-1)
        else:
            array = np.asarray(value).reshape(-1)
        if array.size != 1:
            raise ValueError(f"model {name} output must contain exactly one value")
        return _finite(name, float(array[0]))

    def _validated_window(self, request: WDMControlRequest) -> tuple[np.ndarray, str, float]:
        if len(request.telemetry_window) != self.spec.window_size:
            raise ValueError(
                f"telemetry_window must contain exactly {self.spec.window_size} points"
            )
        timestamps = [
            datetime.fromisoformat(point.timestamp_utc.replace("Z", "+00:00"))
            for point in request.telemetry_window
        ]
        if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
            raise ValueError("telemetry timestamps must be strictly increasing")
        evaluated_at = datetime.fromisoformat(request.evaluated_at_utc.replace("Z", "+00:00"))
        telemetry_age_s = (evaluated_at - timestamps[-1]).total_seconds()
        if telemetry_age_s < 0.0:
            raise ValueError("evaluated_at_utc cannot precede the latest telemetry point")

        rows: list[list[float]] = []
        canonical_points: list[dict[str, Any]] = []
        for row_index, point in enumerate(request.telemetry_window):
            missing = [name for name in self.spec.feature_columns if name not in point.values]
            if missing:
                raise ValueError(f"telemetry point {row_index} is missing features: {missing}")
            row = [
                _finite(f"telemetry[{row_index}].{name}", point.values[name])
                for name in self.spec.feature_columns
            ]
            rows.append(row)
            canonical_points.append(
                {"timestamp_utc": point.timestamp_utc, "values": dict(zip(self.spec.feature_columns, row))}
            )
        raw = np.asarray(rows, dtype=np.float64)
        minima = np.asarray(self.spec.feature_min, dtype=np.float64)
        maxima = np.asarray(self.spec.feature_max, dtype=np.float64)
        outside = (raw < minima) | (raw > maxima)
        ood_fraction = float(outside.sum() / outside.size)
        span = np.where(maxima > minima, maxima - minima, 1.0)
        scaled = ((raw - minima) / span).astype(np.float32)
        window_hash = hashlib.sha256(_canonical_json(canonical_points).encode("utf-8")).hexdigest()
        return scaled, window_hash, ood_fraction

    def predict(self, request: WDMControlRequest | Mapping[str, Any]) -> WDMFidelityPrediction:
        if isinstance(request, Mapping):
            request = WDMControlRequest.from_dict(request)
        if not isinstance(request, WDMControlRequest):
            raise ValueError("request must be a WDMControlRequest or mapping")
        if request.provenance.evidence_kind is not EvidenceKind.SIMULATED:
            raise ValueError("the current WDM model adapter accepts simulated telemetry only")
        scaled, window_hash, ood_fraction = self._validated_window(request)
        tensor = torch.from_numpy(scaled).unsqueeze(0)
        evaluator = getattr(self.model, "eval", None)
        if not callable(evaluator):
            raise ValueError("model must expose eval()")
        evaluator()
        parameters = getattr(self.model, "parameters", None)
        if callable(parameters):
            try:
                tensor = tensor.to(next(parameters()).device)
            except StopIteration:
                pass
        with torch.no_grad():
            output = self.model(tensor)

        availability_probability: float | None = None
        conditional_fidelity: float | None = None
        if self.spec.output_kind is WDMModelOutputKind.POINT:
            mean = self._as_scalar(output, "point prediction")
            uncertainty = float(self.spec.calibrated_point_uncertainty)
        elif self.spec.output_kind is WDMModelOutputKind.PROBABILISTIC:
            if not isinstance(output, Sequence) or len(output) != 2:
                raise ValueError("probabilistic model must return (mean, uncertainty)")
            mean = self._as_scalar(output[0], "mean")
            uncertainty = self._as_scalar(output[1], "uncertainty")
        else:
            if not isinstance(output, Sequence) or len(output) != 2:
                raise ValueError("dual-head model must return (availability, fidelity)")
            availability_probability = self._as_scalar(output[0], "availability")
            conditional_fidelity = self._as_scalar(output[1], "conditional fidelity")
            mean = (
                conditional_fidelity
                if availability_probability >= self.spec.availability_gate
                else 0.0
            )
            uncertainty = float(self.spec.calibrated_point_uncertainty)

        return WDMFidelityPrediction(
            mean=mean,
            uncertainty=uncertainty,
            out_of_distribution=ood_fraction > 0.0,
            ood_fraction=ood_fraction,
            window_sha256=window_hash,
            model_name=self.spec.model_name,
            model_version=self.spec.model_version,
            output_kind=self.spec.output_kind,
            availability_probability=availability_probability,
            conditional_fidelity=conditional_fidelity,
        )

    def build_snapshot(
        self, request: WDMControlRequest | Mapping[str, Any]
    ) -> tuple[NetworkStateSnapshot, WDMFidelityPrediction]:
        if isinstance(request, Mapping):
            request = WDMControlRequest.from_dict(request)
        prediction = self.predict(request)
        latest = request.telemetry_window[-1]
        latest_time = datetime.fromisoformat(latest.timestamp_utc.replace("Z", "+00:00"))
        evaluated_at = datetime.fromisoformat(request.evaluated_at_utc.replace("Z", "+00:00"))
        telemetry_age_s = (evaluated_at - latest_time).total_seconds()
        inference_metadata = {
            **prediction.to_dict(),
            "feature_columns": list(self.spec.feature_columns),
            "window_size": self.spec.window_size,
            "model_sha256": self.spec.model_sha256,
            "config_sha256": self.spec.config_sha256,
            "dataset_sha256": self.spec.dataset_sha256,
            "target_semantics": self.spec.target_semantics,
            "training_evidence_kind": self.spec.training_evidence_kind.value,
            "uncertainty_calibration": self.spec.uncertainty_calibration,
        }
        provenance = Provenance(
            evidence_kind=EvidenceKind.SIMULATED,
            source=f"{request.provenance.source}; predictor:{self.spec.model_name}",
            timestamp=request.evaluated_at_utc,
            git_commit=request.provenance.git_commit,
            config_hash=self.spec.config_sha256,
            dataset_hash=self.spec.dataset_sha256,
            artifact_hashes={
                **request.provenance.artifact_hashes,
                "wdm_model": self.spec.model_sha256,
                "wdm_window": prediction.window_sha256,
            },
            metadata={
                **request.provenance.metadata,
                "upstream_evidence_kind": request.provenance.evidence_kind.value,
                "evidence_lane": "bell_fidelity_control",
                "physical_calibration": False,
                "measured_quantum_target": False,
                "target_semantics": self.spec.target_semantics,
            },
        )
        snapshot = NetworkStateSnapshot(
            request_id=request.request_id,
            node_id=request.node_id,
            timestamp_utc=request.evaluated_at_utc,
            state_version=request.state_version,
            predicted_fidelity=prediction.mean,
            prediction_uncertainty=prediction.uncertainty,
            telemetry_age_s=telemetry_age_s,
            memory_state=request.memory_state,
            available_pair_fidelities=request.available_pair_fidelities,
            memory_ages_s=request.memory_ages_s,
            telemetry={name: latest.values[name] for name in self.spec.feature_columns},
            out_of_distribution=prediction.out_of_distribution,
            integrity_verified=request.integrity_verified,
            operator_interlock=request.operator_interlock,
            provenance=provenance,
            metadata={**request.metadata, "wdm_model_inference": inference_metadata},
        )
        return snapshot, prediction


class WDMModelDrivenController:
    """Compose the inference adapter with the existing audited runtime."""

    def __init__(self, runtime: Any, adapter: WDMModelInferenceAdapter) -> None:
        self.runtime = runtime
        self.adapter = adapter

    def cycle(
        self,
        request: WDMControlRequest | Mapping[str, Any],
        *,
        mode: ControlMode | str = ControlMode.SHADOW,
    ) -> dict[str, Any]:
        selected_mode = ControlMode(mode)
        snapshot, prediction = self.adapter.build_snapshot(request)
        result = self.runtime.cycle(snapshot, mode=selected_mode)
        return {"inference": prediction.to_dict(), **result}

    def status(self) -> dict[str, Any]:
        return {
            "model_name": self.adapter.spec.model_name,
            "model_version": self.adapter.spec.model_version,
            "model_output_kind": self.adapter.spec.output_kind.value,
            "feature_columns": list(self.adapter.spec.feature_columns),
            "window_size": self.adapter.spec.window_size,
            "training_evidence_kind": self.adapter.spec.training_evidence_kind.value,
            "target_semantics": self.adapter.spec.target_semantics,
            "default_mode": ControlMode.SHADOW.value,
            "physical_action_available": False,
        }


__all__ = [
    "WDMControlRequest",
    "WDMFidelityPrediction",
    "WDMModelDrivenController",
    "WDMModelInferenceAdapter",
    "WDMModelOutputKind",
    "WDMModelSpec",
    "WDMTelemetryPoint",
    "WDM_INFERENCE_SCHEMA_VERSION",
    "hash_torch_model",
]
