"""Evidence-aware assurance and sandbox control layer for the NISQ Predictive Twin.

The package exposes domain-neutral decisions, a read-only QKD assurance adapter
and a non-physical, fail-closed quantum-network control sandbox. Qiskit/Aer
backs local simulation; IBM Quantum service integrations are discontinued.
Physical control remains outside this package boundary.
"""

from quantum_autopilot.core import (
    Budget,
    CandidatePlan,
    Decision,
    EvidenceKind,
    Outcome,
    Prediction,
    Provenance,
    SLO,
    SystemSnapshot,
    Workload,
)

__all__ = [
    "Budget",
    "CandidatePlan",
    "Decision",
    "EvidenceKind",
    "Outcome",
    "Prediction",
    "Provenance",
    "SLO",
    "SystemSnapshot",
    "Workload",
]

from quantum_autopilot.product import product_version

__version__ = product_version()
