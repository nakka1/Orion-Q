"""Load the author's vendored scripts (``external/<package>/scripts``) without name clashes.

Both author trees ship modules called ``task3``/``task5``; each load happens with the
right scripts directory first on ``sys.path`` and the clashing names removed again, so a
network script and a QKD script can be compared in the same test session.
"""
import importlib
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLASHING = ("task3", "task4", "task5", "evaluate_phase5", "evaluate_actuator", "export_models")


def load(package, name):
    scripts = ROOT / "external" / package / "scripts"
    if not (scripts / f"{name}.py").exists():
        pytest.skip(f"vendored author scripts for {package} not present")
    saved = {key: sys.modules.pop(key) for key in CLASHING if key in sys.modules}
    sys.path.insert(0, str(scripts))
    try:
        module = importlib.import_module(name)
    finally:
        sys.path.remove(str(scripts))
        for key in CLASHING:
            sys.modules.pop(key, None)
        sys.modules.update(saved)
    return module
