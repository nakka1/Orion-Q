"""Validated, versioned emulation configuration; read-only dependency discovery.

The service holds its inter-process execution lock when writing this store.
Instrument connections, credential references and arbitrary replay paths are not
accepted by the local emulation API.
"""
from __future__ import annotations

from dataclasses import asdict, fields
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from uuid import uuid4

from .photon import PhotonConfig, canonical_json


def module_available(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def _configuration(cls, parameters):
    if not isinstance(parameters, dict) or set(parameters) - {item.name for item in fields(cls)}:
        raise ValueError("unknown_or_invalid_experimental_parameters")
    values = dict(parameters)
    if cls is PhotonConfig:
        for item in fields(cls):
            if isinstance(getattr(PhotonConfig(), item.name), tuple) and item.name in values:
                if not isinstance(values[item.name], (tuple, list)):
                    raise ValueError(f"{item.name} requires an array")
                values[item.name] = tuple(values[item.name])
    try:
        config = cls(**values)
        config.validate()
        canonical_json(asdict(config))
    except (TypeError, OverflowError, AttributeError, KeyError) as exc:
        raise ValueError("invalid_experimental_parameter_type") from exc
    return config


class LaboratoryConfigurationStore:
    schema = "orion.lab.configuration.v1"
    emulated_adapters = ("emulated", "emulated-reference")
    local_adapters = (*emulated_adapters, "orion-replay")

    def __init__(self, root):
        self.path = Path(root) / "configuration.json"

    @staticmethod
    def defaults():
        from .qkd import QkdConfig
        return {"photon": {"adapter_id": "emulated", "parameters": asdict(PhotonConfig())},
                "qkd": {"parameters": asdict(QkdConfig())}}

    def _read(self):
        if not self.path.exists():
            value = {"schema": self.schema, "revision": 0, "configuration": self.defaults()}
            return {**value, "sha256": hashlib.sha256(canonical_json(value).encode()).hexdigest()}
        from .service import unique_fields
        value = json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=unique_fields)
        check = dict(value)
        checksum = check.pop("sha256", None)
        if (value.get("schema") != self.schema or type(value.get("revision")) is not int
                or value["revision"] < 1 or checksum != hashlib.sha256(canonical_json(check).encode()).hexdigest()):
            raise ValueError("laboratory_configuration_integrity_invalid")
        self.validate(value["configuration"])
        return value

    @classmethod
    def validate(cls, value):
        from .qkd import QkdConfig
        if not isinstance(value, dict) or set(value) != {"photon", "qkd"}:
            raise ValueError("photon_and_qkd_configuration_required")
        photon, qkd = value["photon"], value["qkd"]
        if (not isinstance(photon, dict) or set(photon) - {"adapter_id", "parameters", "replay_run_id"}
                or not {"adapter_id", "parameters"} <= set(photon)
                or photon["adapter_id"] not in cls.local_adapters):
            raise ValueError("local_laboratory_requires_registered_emulation_adapter")
        if not isinstance(qkd, dict) or set(qkd) != {"parameters"}:
            raise ValueError("qkd_requires_parameters_only")
        p = _configuration(PhotonConfig, photon["parameters"])
        q = _configuration(QkdConfig, qkd["parameters"])
        selected = {"adapter_id": photon["adapter_id"], "parameters": asdict(p)}
        if photon["adapter_id"] == "orion-replay":
            from uuid import UUID
            reference = photon.get("replay_run_id")
            if not isinstance(reference, str) or str(UUID(reference)) != reference:
                raise ValueError("registered_replay_run_UUID_required")
            selected["replay_run_id"] = reference
        elif "replay_run_id" in photon:
            raise ValueError("replay_reference_only_applies_to_replay_adapter")
        return {"photon": selected,
                "qkd": {"parameters": asdict(q)}}

    def availability(self):
        dependencies = {name: module_available(name) for name in
                        ("numpy", "scipy", "cryptography", "TimeTagger", "pyvisa_sim", "sgp4", "websockets")}
        required = {"photon": [], "qkd": ["numpy", "scipy", "cryptography"],
                    "all": ["numpy", "scipy", "cryptography"]}
        experiments = {}
        for name, names in required.items():
            missing = [dependency for dependency in names if not dependencies[dependency]]
            experiments[name] = {"available": not missing, "required_dependencies": names,
                                 "missing_dependencies": missing, "mode": "emulation"}
        return {"schema": "orion.lab.availability.v2", "available": any(
                    item["available"] for item in experiments.values()),
                "experiments": experiments, "dependencies": dependencies,
                "adapters": [{"adapter_id": name, "mode": "emulation",
                              "available": True} for name in self.emulated_adapters],
                "external_integrations": {
                    "swabian_native": {"sdk_installed": dependencies["TimeTagger"],
                        "available_for_local_execution": False, "hardware_validated": False,
                        "native_replay_validated": False,
                        "requirements": ["official SDK", "applicable license", "complete FileWriter data and metadata"]},
                    "satellite_model": {"available": all(dependencies[name] for name in ("numpy", "scipy", "sgp4", "websockets")),
                        "required_dependencies": ["numpy", "scipy", "sgp4", "websockets"], "physical_link_validated": False}},
                "physical_action_performed": False}

    def state(self):
        value = self._read()
        return {**value, "availability": self.availability(), "physical_action_performed": False}

    def configure(self, request):
        if (not isinstance(request, dict) or set(request) - {"expected_revision", "photon", "qkd"}
                or not ({"photon", "qkd"} & set(request))):
            raise ValueError("configuration_requires_photon_or_qkd_and_optional_expected_revision")
        old = self._read()
        if "expected_revision" in request and (type(request["expected_revision"]) is not int
                or request["expected_revision"] != old["revision"]):
            raise ValueError("laboratory_configuration_revision_conflict")
        merged = json.loads(canonical_json(old["configuration"]))
        for name in ("photon", "qkd"):
            if name not in request:
                continue
            change = request[name]
            allowed = {"adapter_id", "parameters", "replay_run_id"} if name == "photon" else {"parameters"}
            if not isinstance(change, dict) or not change or set(change) - allowed:
                raise ValueError("invalid_configuration_fields")
            if "adapter_id" in change:
                merged[name]["adapter_id"] = change["adapter_id"]
                if change["adapter_id"] != "orion-replay":
                    merged[name].pop("replay_run_id", None)
            if "replay_run_id" in change:
                merged[name]["replay_run_id"] = change["replay_run_id"]
            if "parameters" in change:
                if not isinstance(change["parameters"], dict):
                    raise ValueError("parameters_require_object")
                merged[name]["parameters"].update(change["parameters"])
        validated = self.validate(merged)
        self._validate_selected_source(validated)
        value = {"schema": self.schema, "revision": old["revision"] + 1, "configuration": validated}
        value["sha256"] = hashlib.sha256(canonical_json(value).encode()).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".configuration-{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(canonical_json(value))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return self.state()

    def snapshot_for_run(self, experiment):
        value = self._read()
        if experiment in {"photon", "all"}:
            self._validate_selected_source(value["configuration"])
        availability = self.availability()
        if experiment not in availability["experiments"]:
            raise ValueError("unknown_laboratory_experiment")
        if not availability["experiments"][experiment]["available"]:
            raise ValueError("laboratory_dependencies_unavailable:" + ",".join(
                availability["experiments"][experiment]["missing_dependencies"]))
        return {"revision": value["revision"], "sha256": value["sha256"],
                "configuration": value["configuration"], "customized": value["revision"] > 0,
                "availability": availability["experiments"][experiment]}

    @staticmethod
    def photon_config(snapshot):
        return _configuration(PhotonConfig, snapshot["configuration"]["photon"]["parameters"])

    def replay_manifest(self, snapshot):
        selected = snapshot["configuration"]["photon"]
        folder = self.path.parent / selected["replay_run_id"] / "photon"
        if folder.resolve().parent.parent != self.path.parent.resolve():
            raise ValueError("replay_reference_outside_registered_laboratory")
        path = folder / "nominal.manifest.json"
        if path.is_symlink():
            raise ValueError("replay_reference_must_be_registered_local_file")
        return path

    def _validate_selected_source(self, configuration):
        selected = configuration["photon"]
        if selected["adapter_id"] != "orion-replay":
            return
        from .photon import digest, load_acquisition
        manifest = self.replay_manifest({"configuration": configuration})
        if not manifest.is_file():
            raise ValueError("registered_replay_acquisition_missing")
        acquisition = load_acquisition(manifest)
        if digest(acquisition.configuration) != _configuration(PhotonConfig, selected["parameters"]).configuration_id:
            raise ValueError("replay_requires_original_experimental_configuration")

    @staticmethod
    def qkd_config(snapshot):
        from .qkd import QkdConfig
        return _configuration(QkdConfig, snapshot["configuration"]["qkd"]["parameters"])
