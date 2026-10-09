"""
quantum_twin/core/state.py
=============================

Re-exports the WDM-observable vs. quantum-privileged data contracts
(`WDMTelemetry`, `QuantumStateTarget`) from `quantum_twin.optical.wdm`
(migrated from root-level dataset_v3.py in the one-hundred-sixth
addendum) -- the master audit's Section 3 central data-contract
requirement.
"""
from quantum_twin.optical.wdm import WDMTelemetry, QuantumStateTarget

__all__ = ["WDMTelemetry", "QuantumStateTarget"]
