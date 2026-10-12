"""Compatibility import; the scientific generator belongs to the headless core."""
import sys
from quantum_autopilot.control_plane import synthetic_compensation as _shared

sys.modules[__name__] = _shared
