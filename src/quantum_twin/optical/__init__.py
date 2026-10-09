"""quantum_twin.optical -- WDM-observable telemetry: generation and ingestion.

LAZY LOADING (ninety-second addendum, TCC item 6): applies the same
PEP 562 fix the eighty-fifth/ninety-first addenda applied to
quantum_twin/__init__.py and quantum_twin/simulation/__init__.py,
proactively to THIS subpackage -- per the ninety-first addendum's own
"Honest limitations" note, which identified this exact file as still
carrying the same latent eager-import pattern (verified there by direct
inspection: 3 eager import lines) before any migration forced
the issue reactively.
"""

import importlib

_NAME_TO_SUBMODULE = {
    "WDMTelemetryGenerator": "quantum_twin.optical.telemetry",
    "WDMTelemetry": "quantum_twin.optical.telemetry",
    "QuantumNetworkDatasetV3": "quantum_twin.optical.wdm",
    "TelemetrySource": "quantum_twin.optical.sources",
    "SyntheticTelemetrySource": "quantum_twin.optical.sources",
    "CSVTelemetrySource": "quantum_twin.optical.sources",
    "RealWDMTelemetrySource": "quantum_twin.optical.sources",
    "SantAnnaOpticalFailureSource": "quantum_twin.optical.santanna",
    "SANTANNA_SCHEMA": "quantum_twin.optical.santanna",
    "BristolNDFFTelemetrySource": "quantum_twin.optical.bristol",
    "BRISTOL_NDFF_SCHEMA": "quantum_twin.optical.bristol",
}


def __getattr__(name):
    """PEP 562: called only when normal attribute lookup fails -- i.e.
    the FIRST time this specific name is accessed. Imports ONLY the one
    submodule that name actually lives in, not the others."""
    if name in _NAME_TO_SUBMODULE:
        module = importlib.import_module(_NAME_TO_SUBMODULE[name])
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'quantum_twin.optical' has no attribute '{name}'")


def __dir__():
    return sorted(_NAME_TO_SUBMODULE.keys())


__all__ = list(_NAME_TO_SUBMODULE.keys())
