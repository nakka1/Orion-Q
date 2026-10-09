"""quantum_twin.quantum -- channel, memory, purification, swapping, physics engine.

LAZY LOADING (ninety-second addendum, TCC item 6): applies the same
PEP 562 fix the eighty-fifth/ninety-first addenda applied to
quantum_twin/__init__.py and quantum_twin/simulation/__init__.py,
proactively to THIS subpackage -- per the ninety-first addendum's own
"Honest limitations" note, which identified this exact file as still
carrying the same latent eager-import pattern (verified there by direct
inspection: 5 eager import lines) before any migration forced
the issue reactively.
"""

import importlib

_NAME_TO_SUBMODULE = {
    "QuantumChannel": "quantum_twin.quantum.channel",
    "QuantumNoiseChannel": "quantum_twin.quantum.channel",
    "QuantumMemory": "quantum_twin.quantum.memory",
    "MultiMemoryBank": "quantum_twin.quantum.memory",
    "bbpssw_analytical": "quantum_twin.quantum.purification",
    "DensityMatrixBBPSSW": "quantum_twin.quantum.purification",
    "compare_analytical_vs_density_matrix": "quantum_twin.quantum.purification",
    "WernerStateSwapping": "quantum_twin.quantum.swapping",
    "werner_state": "quantum_twin.quantum.swapping",
    "CausalSwappingChain": "quantum_twin.simulation.causal_chain",
    "GatedCausalSwappingChain": "quantum_twin.simulation.causal_chain",
    "MLGatedCausalSwappingChain": "quantum_twin.simulation.causal_chain",
    "QuantumPhysicsEngine": "quantum_twin.quantum.physics_engine",
    "ReferenceEngine": "quantum_twin.quantum.physics_engine",
    "AnalyticalEngine": "quantum_twin.quantum.physics_engine",
    "FastEngine": "quantum_twin.quantum.physics_engine",
    "PhysicsRegime": "quantum_twin.quantum.physics_engine",
    "DEFAULT_REGIMES": "quantum_twin.quantum.physics_engine",
    "run_engine_benchmark": "quantum_twin.quantum.physics_engine",
    "benchmark_object_reuse_effect": "quantum_twin.quantum.physics_engine",
    "QKDCoexistenceSource": "quantum_twin.quantum.qkd_coexistence",
    "QKD_COEXISTENCE_SCHEMA": "quantum_twin.quantum.qkd_coexistence",
    "PairedQuantumProfile": "quantum_twin.quantum.paired_telemetry",
    "PairedQuantumTelemetrySource": "quantum_twin.quantum.paired_telemetry",
    "PAIRED_QUANTUM_SCHEMA": "quantum_twin.quantum.paired_telemetry",
    "assert_experimental_quantum_targets": "quantum_twin.quantum.paired_telemetry",
    "create_paired_quantum_acquisition_bundle": "quantum_twin.quantum.paired_telemetry",
    "BristolHCFCoexistenceSource": "quantum_twin.quantum.bristol_hcf_coexistence",
    "BRISTOL_HCF_COEXISTENCE_SCHEMA": "quantum_twin.quantum.bristol_hcf_coexistence",
    "DryadSTIKETelemetrySource": "quantum_twin.quantum.dryad_stike",
    "DRYAD_STIKE_SCHEMA": "quantum_twin.quantum.dryad_stike",
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
    raise AttributeError(f"module 'quantum_twin.quantum' has no attribute '{name}'")


def __dir__():
    return sorted(_NAME_TO_SUBMODULE.keys())


__all__ = list(_NAME_TO_SUBMODULE.keys())
