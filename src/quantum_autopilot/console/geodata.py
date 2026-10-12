"""Compatibility import for shared geographic data; rendering stays in console."""
import sys
from quantum_autopilot.geography import geodata as _shared

# Preserve every legacy public name and the same caches, rather than making a
# second geographic data layer in the GUI. Backend imports the shared module.
sys.modules[__name__] = _shared
