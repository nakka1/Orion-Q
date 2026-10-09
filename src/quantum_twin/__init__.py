"""
quantum_twin/
================

Master audit Section 27: the target package architecture the audit
requests --

    quantum_twin/
        core/         config.py, state.py
        optical/      channel.py, telemetry.py, wdm.py, sources.py
        quantum/      channel.py, memory.py, purification.py, swapping.py
        ml/           lstm.py, losses.py, calibration.py
        control/      admission.py, policies.py
        simulation/   environment.py, network.py, orchestrator.py
        evaluation/   prediction.py, quantum.py, energy.py, statistics.py

DESIGN CHOICE, stated explicitly (not hidden): this package is a
COMPATIBILITY / RE-EXPORT LAYER over the existing flat modules at the
repository root (`physics_config.py`, `quantum_channel_v3.py`,
`models.py`, etc.), NOT a physical relocation of ~54 already-tested files
into new locations. Section 27 itself asks for GRADUAL reorganization
("Quando apropriado, reorganizar gradualmente") and Section 1 explicitly
forbids unnecessary full rewrites and silently changing behavior --
physically moving every module would require rewriting every internal
import across the entire codebase (54 files, 207 passing tests) in one
pass, with no way to partially validate the change before it's complete.
That risk was judged to outweigh the benefit here.

This layer gives the REQUESTED namespace/architecture immediately and
safely: `from quantum_twin.quantum import QuantumMemory` works today,
resolving to the same, unmodified, already-tested `quantum_memory.py`
class. A later, genuinely gradual migration could move each flat module's
CONTENTS into its package location one at a time, updating this file to
import directly instead of re-exporting, without ever breaking the public
`quantum_twin.*` interface established here.

LAZY SUBPACKAGE LOADING (eighty-fifth addendum, TCC item 6): the sixty
-third addendum found and documented a real circular-import bug when
attempting to migrate `QuantumMemory`'s real implementation into
`quantum_twin/quantum/memory.py` -- the ROOT CAUSE, identified precisely
in that addendum, was this file EAGERLY importing every subpackage at
package-init time (the `from quantum_twin import core, optical, ...`
line that used to be here), so importing ANY single symbol from ANYWHERE
inside `quantum_twin/` pulled in the ENTIRE package graph, including
subpackages that (at the time) still shimmed back to root files -- any
future migration reachable from a shim that transitively imports back to
the SAME module being migrated would hit an analogous cycle.

Fixed here via PEP 562 module-level `__getattr__`/`__dir__`: each
subpackage is now imported ONLY the first time it is actually accessed
(`quantum_twin.quantum`, `quantum_twin.evaluation`, etc.), not eagerly at
`quantum_twin` import time. This directly unblocks future incremental
module migrations without requiring every subpackage to be import-safe
simultaneously -- verified in this same addendum by re-attempting (and
this time succeeding at) the exact QuantumMemory migration the sixty
-third addendum had to revert.
"""

import importlib

_SUBPACKAGES = ["core", "optical", "quantum", "ml", "control", "simulation", "evaluation"]


def __getattr__(name):
    """PEP 562: called only when normal attribute lookup fails -- i.e.
    the FIRST time `quantum_twin.<subpackage>` is accessed. Caches the
    imported module into this module's own namespace afterward, so
    subsequent accesses resolve through ordinary attribute lookup
    (this function is not called again for the same name)."""
    if name in _SUBPACKAGES:
        module = importlib.import_module(f"quantum_twin.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module 'quantum_twin' has no attribute '{name}'")


def __dir__():
    return sorted(_SUBPACKAGES)


__all__ = _SUBPACKAGES
