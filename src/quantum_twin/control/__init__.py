"""quantum_twin.control -- admission control and decision policies.

LAZY LOADING (ninety-second addendum, TCC item 6): applies the same
PEP 562 fix the eighty-fifth/ninety-first addenda applied to
quantum_twin/__init__.py and quantum_twin/simulation/__init__.py,
proactively to THIS subpackage -- per the ninety-first addendum's own
"Honest limitations" note, which identified this exact file as still
carrying the same latent eager-import pattern (verified there by direct
inspection: 2 eager import lines) before any migration forced
the issue reactively.
"""

import importlib

_NAME_TO_SUBMODULE = {
    "DigitalTwinOrchestrator": "quantum_twin.control.admission",
    "PersistenceBaseline": "quantum_twin.control.policies",
    "MovingAverageBaseline": "quantum_twin.control.policies",
    "OraclePredictor": "quantum_twin.control.policies",
    "ThreeStateController": "quantum_twin.control.policies",
    "RiskAwareController": "quantum_twin.control.risk_aware",
    "RiskCostConfig": "quantum_twin.control.risk_aware",
    "PolicyEngine": "quantum_twin.control.policy",
    "RepeaterState": "quantum_twin.control.policy",
    "Action": "quantum_twin.control.policy",
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
    raise AttributeError(f"module 'quantum_twin.control' has no attribute '{name}'")


def __dir__():
    return sorted(_NAME_TO_SUBMODULE.keys())


__all__ = list(_NAME_TO_SUBMODULE.keys())
