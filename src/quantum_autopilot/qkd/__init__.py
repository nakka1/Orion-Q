"""Read-only QKD evidence adapter for the TCC assurance product."""

from importlib import import_module

from quantum_autopilot.qkd.api_contract import API_VERSION

_EXPORTS = {
    **{name: "claim_registry_adapter" for name in (
        "CONDITION_SCOPES", "REQUESTED_USES", "ClaimRegistryAdapter", "build_assurance_workload")},
    **{name: "operating_point" for name in (
        "GRID_CONFIG_RELATIVE_PATH", "GRID_CONFIG_SHA256", "QBER_UNIT_STATUSES", "TIME_BASES",
        "GridClassification", "IrelandQCIGridClassifier", "OBSERVATION_LEVELS", "QKDOperatingPoint")},
    "AssuranceDashboardService": "dashboard_api", "create_server": "dashboard_api",
}


def __getattr__(name):
    """Read-only contracts do not construct/import the local service for GUI clients."""
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module("quantum_autopilot.qkd." + _EXPORTS[name]), name)
    globals()[name] = value
    return value

__all__ = [
    "CONDITION_SCOPES",
    "GRID_CONFIG_RELATIVE_PATH",
    "GRID_CONFIG_SHA256",
    "GridClassification",
    "IrelandQCIGridClassifier",
    "OBSERVATION_LEVELS",
    "QBER_UNIT_STATUSES",
    "QKDOperatingPoint",
    "REQUESTED_USES",
    "TIME_BASES",
    "API_VERSION",
    "AssuranceDashboardService",
    "ClaimRegistryAdapter",
    "build_assurance_workload",
    "create_server",
]
