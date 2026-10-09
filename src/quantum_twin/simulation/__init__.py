"""quantum_twin.simulation -- closed-loop environment, network topology, orchestration.

LAZY LOADING (ninety-first addendum, TCC item 6): applies the same
PEP 562 fix the eighty-fifth addendum applied to the TOP-LEVEL
quantum_twin/__init__.py, to THIS subpackage specifically. Root cause
found while attempting the network_topology.py migration: this file
previously eagerly imported ALL THREE of its own submodules
(.environment, .network, .orchestrator) at package-init time, so
accessing ANY single name from quantum_twin.simulation pulled in the
ENTIRE subpackage graph -- including `.environment`, which shims back
to root `environment.py`, which imports root `quantum_memory.py`
(already migrated, pointing INTO quantum_twin.quantum), which
transitively imports `entanglement_swapping.py` (root), which imports
`network_topology` (root, the very file being migrated) -- a real,
multi-hop circular import, structurally identical in KIND (though not
in specific path) to the one the eighty-fifth addendum fixed at the
top level."""

import importlib

_NAME_TO_SUBMODULE = {
    "QuantumRepeaterEnvironment": "quantum_twin.simulation.environment",
    "QuantumNode": "quantum_twin.simulation.network",
    "NetworkLink": "quantum_twin.simulation.network",
    "Repeater": "quantum_twin.simulation.network",
    "QuantumRepeaterNode": "quantum_twin.simulation.network",
    "DigitalTwinOrchestrator": "quantum_twin.simulation.orchestrator",
}


def __getattr__(name):
    """PEP 562: called only when normal attribute lookup fails -- i.e.
    the FIRST time this specific name is accessed. Imports ONLY the one
    submodule that name actually lives in, not the other two."""
    if name in _NAME_TO_SUBMODULE:
        module = importlib.import_module(_NAME_TO_SUBMODULE[name])
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'quantum_twin.simulation' has no attribute '{name}'")


def __dir__():
    return sorted(_NAME_TO_SUBMODULE.keys())


__all__ = list(_NAME_TO_SUBMODULE.keys())
