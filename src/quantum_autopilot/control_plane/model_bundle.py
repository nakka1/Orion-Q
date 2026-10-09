"""Safe, hash-bound model bundles for local WDM shadow replay.

The bundle is an ``.npz`` container containing JSON encoded as ``uint8`` and
plain numeric tensors.  Loading always uses ``allow_pickle=False`` and rebuilds
one of a small set of local, whitelisted architectures before applying a
strictly shape-checked state dictionary.  No Python object, import path or
callable is deserialized from the artifact.
"""

from __future__ import annotations

import hashlib
import json
import math
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import torch

from quantum_autopilot.control_plane.safety import SafetyPolicyConfig, SafetySupervisor
from quantum_autopilot.control_plane.task_constrained_policy import (
    ControlTask,
    FrozenTaskPolicySpec,
    TaskConstrainedRepeaterPolicy,
)
from quantum_autopilot.control_plane.wdm_inference import (
    WDMControlRequest,
    WDMModelInferenceAdapter,
    WDMModelOutputKind,
    WDMModelSpec,
    hash_torch_model,
)
from quantum_twin.ml.architectures import EdgeGRU, EdgeTCN
from quantum_twin.ml.dual_head import EdgeLSTMDualHead
from quantum_twin.ml.lstm import EdgeLSTM
from quantum_twin.ml.probabilistic import EdgeLSTMProbabilistic


WDM_MODEL_BUNDLE_SCHEMA_VERSION = "1.0.0"
WDM_MODEL_BUNDLE_ARTIFACT_TYPE = "wdm_fidelity_shadow_model"
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_REPLAY_RECORDS = 250
_MANIFEST_KEY = "__manifest__"
_REPLAY_KEY = "__replay__"
_STATE_PREFIX = "state::"
_SUPPORTED_ARCHITECTURES = {
    "edge_lstm": WDMModelOutputKind.POINT,
    "edge_gru": WDMModelOutputKind.POINT,
    "edge_tcn": WDMModelOutputKind.POINT,
    "edge_lstm_probabilistic": WDMModelOutputKind.PROBABILISTIC,
    "edge_lstm_dual_head": WDMModelOutputKind.DUAL_HEAD,
}


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(name: str, value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{name} must be a SHA-256 hex digest") from exc
    return value.lower()


def _json_from_uint8(array: np.ndarray, *, name: str) -> Any:
    if array.dtype != np.uint8 or array.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional uint8 JSON payload")
    try:
        return json.loads(array.tobytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} is not valid UTF-8 JSON") from exc


def _architecture_description(model: Any) -> tuple[str, dict[str, int]]:
    if type(model) is EdgeLSTM:
        return "edge_lstm", {
            "input_size": int(model.lstm.input_size),
            "hidden_size": int(model.lstm.hidden_size),
            "num_layers": int(model.lstm.num_layers),
        }
    if type(model) is EdgeGRU:
        return "edge_gru", {
            "input_size": int(model.gru.input_size),
            "hidden_size": int(model.gru.hidden_size),
            "num_layers": int(model.gru.num_layers),
        }
    if type(model) is EdgeTCN:
        convolutions = [layer.conv for layer in model.tcn if hasattr(layer, "conv")]
        if not convolutions:
            raise ValueError("edge_tcn must contain causal convolution layers")
        return "edge_tcn", {
            "input_size": int(convolutions[0].in_channels),
            "hidden_size": int(convolutions[0].out_channels),
            "kernel_size": int(convolutions[0].kernel_size[0]),
            "num_layers": len(convolutions),
        }
    if type(model) is EdgeLSTMProbabilistic:
        return "edge_lstm_probabilistic", {
            "input_size": int(model.lstm.input_size),
            "hidden_size": int(model.lstm.hidden_size),
            "num_layers": int(model.lstm.num_layers),
        }
    if type(model) is EdgeLSTMDualHead:
        return "edge_lstm_dual_head", {
            "input_size": int(model.lstm.input_size),
            "hidden_size": int(model.lstm.hidden_size),
            "num_layers": int(model.lstm.num_layers),
        }
    raise ValueError("model architecture is not on the safe bundle allowlist")


def _integer_parameter(
    parameters: Mapping[str, Any], name: str, *, minimum: int, maximum: int
) -> int:
    value = parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"architecture parameter {name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(
            f"architecture parameter {name} must be in [{minimum}, {maximum}]"
        )
    return value


def _construct_model(
    architecture_name: str,
    parameters_value: Any,
    output_kind: WDMModelOutputKind,
) -> torch.nn.Module:
    if architecture_name not in _SUPPORTED_ARCHITECTURES:
        raise ValueError("model architecture is not on the safe bundle allowlist")
    if _SUPPORTED_ARCHITECTURES[architecture_name] is not output_kind:
        raise ValueError("architecture and model output_kind are inconsistent")
    if not isinstance(parameters_value, Mapping):
        raise ValueError("architecture parameters must be an object")
    parameters = dict(parameters_value)
    allowed = {"input_size", "hidden_size", "num_layers"}
    if architecture_name == "edge_tcn":
        allowed.add("kernel_size")
    unknown = sorted(set(parameters).difference(allowed))
    missing = sorted(allowed.difference(parameters))
    if unknown or missing:
        raise ValueError(
            f"invalid architecture parameters; missing={missing}, unknown={unknown}"
        )
    input_size = _integer_parameter(parameters, "input_size", minimum=1, maximum=128)
    hidden_size = _integer_parameter(parameters, "hidden_size", minimum=1, maximum=1024)
    num_layers = _integer_parameter(parameters, "num_layers", minimum=1, maximum=16)
    if architecture_name == "edge_lstm":
        return EdgeLSTM(input_size, hidden_size, num_layers)
    if architecture_name == "edge_gru":
        return EdgeGRU(input_size, hidden_size, num_layers)
    if architecture_name == "edge_tcn":
        kernel_size = _integer_parameter(parameters, "kernel_size", minimum=1, maximum=31)
        return EdgeTCN(input_size, hidden_size, kernel_size, num_layers)
    if architecture_name == "edge_lstm_probabilistic":
        return EdgeLSTMProbabilistic(input_size, hidden_size, num_layers)
    return EdgeLSTMDualHead(input_size, hidden_size, num_layers)


def _validated_scientific_status(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("scientific_status must be an object")
    status = dict(value)
    required = {
        "approved_for_control",
        "physical_validation",
        "paired_measured_wdm_quantum_validation",
        "v5_25_registered_neural_improvements",
        "v5_26_registered_neural_improvements",
        "statement",
    }
    if set(status) != required:
        raise ValueError("scientific_status fields do not match the bundle schema")
    for name in (
        "approved_for_control",
        "physical_validation",
        "paired_measured_wdm_quantum_validation",
    ):
        if status[name] is not False:
            raise ValueError(f"{name} must remain false for this simulated bundle lane")
    for name in (
        "v5_25_registered_neural_improvements",
        "v5_26_registered_neural_improvements",
        "statement",
    ):
        if not isinstance(status[name], str) or not status[name].strip():
            raise ValueError(f"scientific_status.{name} cannot be empty")
    return status


@dataclass(frozen=True)
class WDMReplayRecord:
    """One server-configured trace record; the target is retrospective only."""

    request: WDMControlRequest
    simulated_actual_fidelity: float | None = None

    def __post_init__(self) -> None:
        request = self.request
        if isinstance(request, Mapping):
            request = WDMControlRequest.from_dict(request)
            object.__setattr__(self, "request", request)
        if not isinstance(request, WDMControlRequest):
            raise ValueError("replay request must be a WDMControlRequest")
        value = self.simulated_actual_fidelity
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("simulated_actual_fidelity must be numeric")
            value = float(value)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError("simulated_actual_fidelity must be in [0, 1]")
            object.__setattr__(self, "simulated_actual_fidelity", value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "simulated_actual_fidelity": self.simulated_actual_fidelity,
        }


@dataclass(frozen=True)
class LoadedWDMModelBundle:
    """Validated in-memory bundle returned by the safe loader."""

    bundle_sha256: str
    model: torch.nn.Module
    model_spec: WDMModelSpec
    policy_spec: FrozenTaskPolicySpec
    architecture_name: str
    architecture_parameters: Mapping[str, int]
    replay_records: tuple[WDMReplayRecord, ...]
    scientific_status: Mapping[str, Any]
    created_at_utc: str
    expected_bundle_sha256: str | None = None

    def inference_adapter(self) -> WDMModelInferenceAdapter:
        return WDMModelInferenceAdapter(self.model, self.model_spec)

    def policy(self) -> TaskConstrainedRepeaterPolicy:
        return TaskConstrainedRepeaterPolicy(ControlTask.LINK_PURIFICATION, self.policy_spec)

    def supervisor(self) -> SafetySupervisor:
        return SafetySupervisor(
            SafetyPolicyConfig(
                max_prediction_uncertainty=self.policy_spec.max_prediction_uncertainty
            )
        )

    def status(self) -> dict[str, Any]:
        return {
            "configured": True,
            "valid": True,
            "bundle_schema_version": WDM_MODEL_BUNDLE_SCHEMA_VERSION,
            "bundle_sha256": self.bundle_sha256,
            "bundle_hash_pinned": self.expected_bundle_sha256 is not None,
            "expected_bundle_sha256": self.expected_bundle_sha256,
            "architecture": {
                "name": self.architecture_name,
                "parameters": dict(self.architecture_parameters),
            },
            "model": {
                "name": self.model_spec.model_name,
                "version": self.model_spec.model_version,
                "output_kind": self.model_spec.output_kind.value,
                "model_sha256": self.model_spec.model_sha256,
                "feature_columns": list(self.model_spec.feature_columns),
                "window_size": self.model_spec.window_size,
                "training_evidence_kind": self.model_spec.training_evidence_kind.value,
                "target_semantics": self.model_spec.target_semantics,
            },
            "policy": {
                "task": ControlTask.LINK_PURIFICATION.value,
                "policy_spec_sha256": self.policy_spec.sha256,
                "max_prediction_uncertainty": self.policy_spec.max_prediction_uncertainty,
                "probability_margin": self.policy_spec.probability_margin,
            },
            "replay_record_count": len(self.replay_records),
            "scientific_status": dict(self.scientific_status),
            "created_at_utc": self.created_at_utc,
            "default_mode": "shadow",
            "physical_action_available": False,
        }


def export_wdm_model_bundle(
    path: str | Path,
    *,
    model: torch.nn.Module,
    model_spec: WDMModelSpec,
    policy_spec: FrozenTaskPolicySpec,
    replay_records: Sequence[WDMReplayRecord | Mapping[str, Any]],
    scientific_status: Mapping[str, Any],
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    """Write a non-pickle model bundle, refusing to overwrite any file."""

    target = Path(path).resolve()
    if target.exists():
        raise ValueError("bundle destination already exists")
    if not isinstance(model_spec, WDMModelSpec):
        raise ValueError("model_spec must be a WDMModelSpec")
    if not isinstance(policy_spec, FrozenTaskPolicySpec):
        raise ValueError("policy_spec must be a FrozenTaskPolicySpec")
    if hash_torch_model(model) != model_spec.model_sha256:
        raise ValueError("model does not match model_spec.model_sha256")
    architecture_name, architecture_parameters = _architecture_description(model)
    expected_kind = _SUPPORTED_ARCHITECTURES[architecture_name]
    if model_spec.output_kind is not expected_kind:
        raise ValueError("architecture and model output_kind are inconsistent")
    if architecture_parameters["input_size"] != len(model_spec.feature_columns):
        raise ValueError("architecture input_size must match feature_columns")
    if policy_spec.controller_name != model_spec.model_name:
        raise ValueError("policy controller_name must match model_name")
    records = tuple(
        item if isinstance(item, WDMReplayRecord) else WDMReplayRecord(**dict(item))
        for item in replay_records
    )
    if not 1 <= len(records) <= MAX_REPLAY_RECORDS:
        raise ValueError(f"replay_records must contain 1 to {MAX_REPLAY_RECORDS} items")
    request_ids = [item.request.request_id for item in records]
    if len(request_ids) != len(set(request_ids)):
        raise ValueError("replay request_id values must be unique")
    for item in records:
        if len(item.request.telemetry_window) != model_spec.window_size:
            raise ValueError("every replay window must match model_spec.window_size")
    status = _validated_scientific_status(scientific_status)
    replay_payload = {
        "schema_version": WDM_MODEL_BUNDLE_SCHEMA_VERSION,
        "records": [item.to_dict() for item in records],
    }
    replay_bytes = _canonical_json_bytes(replay_payload)
    arrays: dict[str, np.ndarray] = {
        _REPLAY_KEY: np.frombuffer(replay_bytes, dtype=np.uint8).copy()
    }
    state_entries = []
    for name, tensor in sorted(model.state_dict().items()):
        value = tensor.detach().cpu().contiguous().numpy()
        if value.dtype != np.float32 or not np.isfinite(value).all():
            raise ValueError("bundle state tensors must be finite float32 arrays")
        key = f"{_STATE_PREFIX}{name}"
        arrays[key] = value
        state_entries.append(
            {
                "name": name,
                "array_key": key,
                "shape": list(value.shape),
                "dtype": str(value.dtype),
                "sha256": _sha256_bytes(value.tobytes(order="C")),
            }
        )
    timestamp = created_at_utc or datetime.now(timezone.utc).isoformat()
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("created_at_utc must be valid ISO-8601") from exc
    if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
        raise ValueError("created_at_utc must include a timezone")
    manifest = {
        "schema_version": WDM_MODEL_BUNDLE_SCHEMA_VERSION,
        "artifact_type": WDM_MODEL_BUNDLE_ARTIFACT_TYPE,
        "created_at_utc": timestamp,
        "serialization": "numpy_npz_allow_pickle_false",
        "architecture": {
            "name": architecture_name,
            "parameters": architecture_parameters,
        },
        "model_spec": model_spec.to_dict(),
        "state": {
            "semantic_sha256": model_spec.model_sha256,
            "entries": state_entries,
        },
        "replay": {
            "sha256": _sha256_bytes(replay_bytes),
            "record_count": len(records),
        },
        "policy": {
            "task": ControlTask.LINK_PURIFICATION.value,
            "spec": policy_spec.to_dict(),
            "spec_sha256": policy_spec.sha256,
        },
        "scientific_status": status,
        "physical_action_available": False,
    }
    arrays[_MANIFEST_KEY] = np.frombuffer(
        _canonical_json_bytes(manifest), dtype=np.uint8
    ).copy()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as stream:
            np.savez_compressed(stream, **arrays)
    except Exception:
        if target.exists() and target.stat().st_size == 0:
            target.unlink()
        raise
    if target.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError("generated bundle exceeds the size limit")
    return {
        "path": str(target),
        "bundle_sha256": _sha256_file(target),
        "model_sha256": model_spec.model_sha256,
        "policy_spec_sha256": policy_spec.sha256,
        "replay_record_count": len(records),
        "serialization": manifest["serialization"],
        "physical_action_available": False,
    }


def _inspect_npz_container(path: Path) -> None:
    size = path.stat().st_size
    if size <= 0 or size > MAX_BUNDLE_BYTES:
        raise ValueError("bundle file size is outside the allowed range")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > 256:
                raise ValueError("bundle contains an invalid number of entries")
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)):
                raise ValueError("bundle contains duplicate entries")
            if any(
                entry.is_dir()
                or entry.flag_bits & 0x1
                or "/" in entry.filename
                or "\\" in entry.filename
                or not entry.filename.endswith(".npy")
                for entry in entries
            ):
                raise ValueError("bundle contains an unsafe entry")
            total = sum(entry.file_size for entry in entries)
            if total > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                raise ValueError("bundle uncompressed size exceeds the safety limit")
    except zipfile.BadZipFile as exc:
        raise ValueError("bundle is not a valid NPZ container") from exc


def load_wdm_model_bundle(
    path: str | Path,
    *,
    expected_bundle_sha256: str | None = None,
) -> LoadedWDMModelBundle:
    """Load and completely validate a safe bundle without unpickling objects."""

    source = Path(path).resolve()
    if not source.is_file():
        raise ValueError("configured WDM model bundle does not exist")
    _inspect_npz_container(source)
    bundle_sha256 = _sha256_file(source)
    pinned = None
    if expected_bundle_sha256 is not None:
        pinned = _digest("expected_bundle_sha256", expected_bundle_sha256)
        if bundle_sha256 != pinned:
            raise ValueError("bundle does not match expected_bundle_sha256")
    try:
        with np.load(source, allow_pickle=False) as container:
            keys = set(container.files)
            if _MANIFEST_KEY not in keys or _REPLAY_KEY not in keys:
                raise ValueError("bundle is missing manifest or replay data")
            manifest = _json_from_uint8(container[_MANIFEST_KEY], name="manifest")
            replay_payload = _json_from_uint8(container[_REPLAY_KEY], name="replay")
            if not isinstance(manifest, Mapping):
                raise ValueError("manifest must be an object")
            required_manifest = {
                "schema_version",
                "artifact_type",
                "created_at_utc",
                "serialization",
                "architecture",
                "model_spec",
                "state",
                "replay",
                "policy",
                "scientific_status",
                "physical_action_available",
            }
            if set(manifest) != required_manifest:
                raise ValueError("manifest fields do not match the bundle schema")
            if manifest["schema_version"] != WDM_MODEL_BUNDLE_SCHEMA_VERSION:
                raise ValueError("unsupported WDM model bundle schema_version")
            if manifest["artifact_type"] != WDM_MODEL_BUNDLE_ARTIFACT_TYPE:
                raise ValueError("unsupported WDM model bundle artifact_type")
            if manifest["serialization"] != "numpy_npz_allow_pickle_false":
                raise ValueError("unsupported model serialization")
            if manifest["physical_action_available"] is not False:
                raise ValueError("model bundle cannot enable physical action")
            model_spec = WDMModelSpec.from_dict(manifest["model_spec"])
            architecture = manifest["architecture"]
            if not isinstance(architecture, Mapping) or set(architecture) != {
                "name",
                "parameters",
            }:
                raise ValueError("architecture contract is invalid")
            architecture_name = architecture["name"]
            if not isinstance(architecture_name, str):
                raise ValueError("architecture name must be a string")
            model = _construct_model(
                architecture_name,
                architecture["parameters"],
                model_spec.output_kind,
            )
            if int(architecture["parameters"]["input_size"]) != len(
                model_spec.feature_columns
            ):
                raise ValueError("architecture input_size must match feature_columns")
            state_contract = manifest["state"]
            if not isinstance(state_contract, Mapping) or set(state_contract) != {
                "semantic_sha256",
                "entries",
            }:
                raise ValueError("state contract is invalid")
            if _digest(
                "state.semantic_sha256", state_contract["semantic_sha256"]
            ) != model_spec.model_sha256:
                raise ValueError("state semantic hash does not match model spec")
            entries = state_contract["entries"]
            if not isinstance(entries, list) or not entries:
                raise ValueError("state entries must be a non-empty list")
            expected_state = model.state_dict()
            loaded_state: dict[str, torch.Tensor] = {}
            expected_array_keys = {_MANIFEST_KEY, _REPLAY_KEY}
            names_seen: set[str] = set()
            for entry in entries:
                if not isinstance(entry, Mapping) or set(entry) != {
                    "name",
                    "array_key",
                    "shape",
                    "dtype",
                    "sha256",
                }:
                    raise ValueError("state entry contract is invalid")
                name = entry["name"]
                array_key = entry["array_key"]
                if (
                    not isinstance(name, str)
                    or not isinstance(array_key, str)
                    or array_key != f"{_STATE_PREFIX}{name}"
                    or name in names_seen
                ):
                    raise ValueError("state entry name is invalid or duplicated")
                names_seen.add(name)
                expected_array_keys.add(array_key)
                if name not in expected_state or array_key not in keys:
                    raise ValueError("state entry does not match the architecture")
                array = container[array_key]
                expected_tensor = expected_state[name]
                if array.dtype != np.float32 or entry["dtype"] != "float32":
                    raise ValueError("state arrays must use float32")
                if list(array.shape) != entry["shape"] or tuple(array.shape) != tuple(
                    expected_tensor.shape
                ):
                    raise ValueError("state array shape does not match the architecture")
                if not np.isfinite(array).all():
                    raise ValueError("state arrays must contain only finite values")
                if _sha256_bytes(array.tobytes(order="C")) != _digest(
                    "state entry sha256", entry["sha256"]
                ):
                    raise ValueError("state tensor hash mismatch")
                loaded_state[name] = torch.from_numpy(array.copy())
            if keys != expected_array_keys or names_seen != set(expected_state):
                raise ValueError("bundle state keys do not exactly match the architecture")
            model.load_state_dict(loaded_state, strict=True)
            model.eval()
            if hash_torch_model(model) != model_spec.model_sha256:
                raise ValueError("loaded model does not match model_sha256")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("unable to read WDM model bundle") from exc

    if not isinstance(replay_payload, Mapping) or set(replay_payload) != {
        "schema_version",
        "records",
    }:
        raise ValueError("replay contract is invalid")
    if replay_payload["schema_version"] != WDM_MODEL_BUNDLE_SCHEMA_VERSION:
        raise ValueError("unsupported replay schema_version")
    replay_contract = manifest["replay"]
    if not isinstance(replay_contract, Mapping) or set(replay_contract) != {
        "sha256",
        "record_count",
    }:
        raise ValueError("replay manifest contract is invalid")
    replay_bytes = _canonical_json_bytes(replay_payload)
    if _sha256_bytes(replay_bytes) != _digest("replay sha256", replay_contract["sha256"]):
        raise ValueError("replay payload hash mismatch")
    raw_records = replay_payload["records"]
    if not isinstance(raw_records, list) or not 1 <= len(raw_records) <= MAX_REPLAY_RECORDS:
        raise ValueError("replay record count is outside the allowed range")
    if replay_contract["record_count"] != len(raw_records):
        raise ValueError("replay record_count does not match its payload")
    records = tuple(WDMReplayRecord(**dict(item)) for item in raw_records)
    if any(len(item.request.telemetry_window) != model_spec.window_size for item in records):
        raise ValueError("replay window does not match model_spec.window_size")
    request_ids = [item.request.request_id for item in records]
    if len(request_ids) != len(set(request_ids)):
        raise ValueError("replay request_id values must be unique")
    policy = manifest["policy"]
    if not isinstance(policy, Mapping) or set(policy) != {"task", "spec", "spec_sha256"}:
        raise ValueError("policy contract is invalid")
    if policy["task"] != ControlTask.LINK_PURIFICATION.value:
        raise ValueError("bundle policy must use the link_purification task")
    if not isinstance(policy["spec"], Mapping):
        raise ValueError("policy spec must be an object")
    policy_spec = FrozenTaskPolicySpec(**dict(policy["spec"]))
    if policy_spec.sha256 != _digest("policy spec sha256", policy["spec_sha256"]):
        raise ValueError("policy spec hash mismatch")
    if policy_spec.controller_name != model_spec.model_name:
        raise ValueError("policy controller_name must match model_name")
    scientific_status = _validated_scientific_status(manifest["scientific_status"])
    timestamp = manifest["created_at_utc"]
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("bundle created_at_utc is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("bundle created_at_utc must include a timezone")
    return LoadedWDMModelBundle(
        bundle_sha256=bundle_sha256,
        expected_bundle_sha256=pinned,
        model=model,
        model_spec=model_spec,
        policy_spec=policy_spec,
        architecture_name=architecture_name,
        architecture_parameters=dict(architecture["parameters"]),
        replay_records=records,
        scientific_status=scientific_status,
        created_at_utc=timestamp,
    )


__all__ = [
    "LoadedWDMModelBundle",
    "MAX_REPLAY_RECORDS",
    "WDMReplayRecord",
    "WDM_MODEL_BUNDLE_ARTIFACT_TYPE",
    "WDM_MODEL_BUNDLE_SCHEMA_VERSION",
    "export_wdm_model_bundle",
    "load_wdm_model_bundle",
]
