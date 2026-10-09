"""A timing fallback must not impersonate GMPY to algebra or training."""
import importlib
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.unit
@pytest.mark.parametrize("package", ["qnet_network", "qnet_qkd"])
@pytest.mark.parametrize("order", ["shim-first", "sympy-first"])
def test_timing_fallback_coexists_with_symbolic_math_and_optimizer(package, order):
    code = f"""
import importlib
import sys
if {order!r} == 'sympy-first':
    import sympy
compat = importlib.import_module({package!r} + '.compat')
compat._install_gmpy2_shim()
import gmpy2
assert gmpy2.version() == '0.0.0'
assert gmpy2.__orion_q_shim__ is True
gmpy2.get_context().precision = 80
assert str(gmpy2.floor(gmpy2.mpfr('9007199254740993.75'))) == '9007199254740993'
import sympy
from sympy.external.gmpy import GROUND_TYPES
assert GROUND_TYPES != 'gmpy'
assert sympy.factorint(720) == {{2: 4, 3: 2, 5: 1}}
assert abs(float(sympy.sqrt(2).evalf(50)) - 2 ** .5) < 1e-15
import torch
weight = torch.tensor([1.0], requires_grad=True)
optimizer = torch.optim.Adam([weight], lr=.1)
(weight ** 2).sum().backward()
optimizer.step()
assert 0 < weight.item() < 1
"""
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.unit
@pytest.mark.parametrize("package", ["qnet_network", "qnet_qkd"])
def test_forced_bootstrap_reports_an_existing_timing_shim_honestly(package, monkeypatch):
    compat = importlib.import_module(package + ".compat")
    shim = importlib.import_module(package + ".compat._gmpy2_shim")
    sequence = types.ModuleType("sequence")
    sequence.__file__ = str(ROOT / "sequence" / "__init__.py")
    monkeypatch.setitem(sys.modules, "sequence", sequence)
    monkeypatch.setitem(sys.modules, "gmpy2", shim)
    monkeypatch.setattr(compat, "_STATUS", None)
    status = compat.bootstrap(force=True)
    assert "gmpy2" in status.shimmed
    assert "gmpy2" not in status.real
    assert status.fully_native is False


@pytest.mark.unit
@pytest.mark.parametrize("package", ["qnet_network", "qnet_qkd"])
def test_bootstrap_preserves_an_installed_gmpy_module(package, monkeypatch):
    compat = importlib.import_module(package + ".compat")
    installed = types.ModuleType("gmpy2")
    installed.__file__ = "gmpy2.pyd"
    sequence = types.ModuleType("sequence")
    sequence.__file__ = str(ROOT / "sequence" / "__init__.py")
    monkeypatch.setitem(sys.modules, "sequence", sequence)
    monkeypatch.setitem(sys.modules, "gmpy2", installed)
    monkeypatch.setattr(compat, "_STATUS", None)
    status = compat.bootstrap(force=True)
    assert sys.modules["gmpy2"] is installed
    assert "gmpy2" in status.real
    assert "gmpy2" not in status.shimmed
