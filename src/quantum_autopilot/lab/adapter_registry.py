"""Adapter selection boundary; the protocol core consumes a common PhotonAdapter."""
from __future__ import annotations

from .adapters import AdapterUnavailable, EmulatedPhotonAdapter, ReplayPhotonAdapter, SwabianSDKAdapter
from .instrument import InstrumentCapabilities
from .photon import AcquisitionError


def adapter_descriptors():
    native = SwabianSDKAdapter().availability()
    return [
        {"adapter_id": "emulated", "implementation": "EmulatedPhotonAdapter", "mode": "emulation",
         "available": True, "physical_action_performed": False, "options": ["device_id"],
         "capabilities": ["integer_ps", "configuration_validation", "calibration_model", "fault_injection"]},
        {"adapter_id": "emulated-reference", "implementation": "EmulatedPhotonAdapter", "mode": "emulation",
         "available": True, "physical_action_performed": False, "options": ["device_id"],
         "scope": "alternate device configuration using the same identified model"},
        {"adapter_id": "orion-replay", "implementation": "ReplayPhotonAdapter", "mode": "replay",
         "available": False, "dependency": "validated ORION JSONL acquisition manifest", "options": ["manifest_path"],
         "scope": "different adapter implementation; preserves original source and configuration"},
        {"adapter_id": "swabian-native", "implementation": "SwabianSDKAdapter", "mode": "hardware",
         "available": False, "sdk_installed": native["installed"],
         "dependency": "SDK, connected authorized instrument and validated experimental setup",
         "hardware_validated": False, "options": ["serial", "address", "event_type_map"]},
    ]


def create_photon_adapter(adapter_id="emulated", options=None, *, security=None, principal=None, authority=None):
    options = dict(options or {})
    choices = {d["adapter_id"]: d for d in adapter_descriptors()}
    if adapter_id not in choices:
        raise AcquisitionError("unknown photon adapter")
    if set(options) - set(choices[adapter_id]["options"]):
        raise AcquisitionError("unknown adapter option")
    if adapter_id in {"emulated", "emulated-reference"}:
        adapter = EmulatedPhotonAdapter(options.get("device_id", "tagger-emulator-a" if adapter_id == "emulated"
                                                     else "tagger-emulator-alternate"))
    elif adapter_id == "orion-replay":
        if not options.get("manifest_path"):
            raise AdapterUnavailable("ORION replay requires a validated acquisition manifest")
        adapter = ReplayPhotonAdapter(options["manifest_path"])
    else:
        if security is None or authority is None:
            raise AdapterUnavailable("physical adapter requires authenticated runtime and shared equipment authority")
        if not options.get("serial"):
            raise AcquisitionError("physical adapter requires explicit serial identity")
        adapter = SwabianSDKAdapter(**options)
    # The registry provides construction only, never automatic connect or fallback.
    if security is not None:
        from quantum_autopilot.security.equipment import AuthorizedPhotonAdapter
        adapter = AuthorizedPhotonAdapter(adapter, security, principal, authority=authority)
    return adapter


def validate_adapter_configuration(adapter, configuration):
    configuration.validate()
    if isinstance(adapter, SwabianSDKAdapter):
        return InstrumentCapabilities.from_dict(adapter.capabilities()).validate_configuration(configuration)
    if isinstance(adapter, ReplayPhotonAdapter):
        if adapter.record is None:
            raise AcquisitionError("connect replay source before validating original configuration")
        from .photon import digest
        if configuration.configuration_id != digest(adapter.record.configuration):
            raise AcquisitionError("replay must preserve original acquisition configuration")
    return True
