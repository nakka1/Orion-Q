"""
tests/conftest.py
====================

Master prompt Fase 21 (CI/CD) + master prompt v4 Fase 27: auto-applies
pytest markers (unit/physics/integration/statistical/slow/experimental/
benchmark) based on test FILE name patterns, rather than requiring every
test in this project's 51-file suite to be manually decorated one at a
time. This is a pragmatic, maintainable strategy for a large EXISTING
test suite -- new test files should still add explicit `@pytest.mark.X`
decorators where the auto-classification below wouldn't get it right,
but the common case (a whole file belongs to one category) is handled
automatically here.

Categories match the master prompt v4's explicit list:
    unit, physics, integration, statistical, slow, experimental, benchmark

Usage:
    pytest -m unit
    pytest -m physics
    pytest -m integration
    pytest -m statistical
    pytest -m slow
    pytest -m experimental
    pytest -m benchmark
"""

import gc
import os
import sys

import pytest

# One-hundred-ninth addendum (TCC item 6, "reestruturação completa" second
# half): the 64 run_*.py experiment scripts moved from the project root
# into experiments/. Many test files (26, per this addendum's own audit)
# import these scripts directly (e.g. `from run_domain_shift_experiment
# import build_windows`) -- this adds experiments/ to sys.path so those
# imports continue to resolve exactly as they did when the scripts lived
# in the project root, with zero test-file content changes required.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "experiments"))

# Direct quantum-physics validation/regression (channel, memory,
# purification, swapping, environment, multi-hop, causal interventions).
_PHYSICS_FILES = {
    "test_physics_regression.py", "test_physics_validation.py", "test_physics_engine.py",
    "test_purification.py", "test_quantum_channel.py", "test_fast_vs_aer_channel.py",
    "test_swapping_and_memory.py", "test_causal_v3.py", "test_environment.py",
    "test_repeater_chain.py", "test_repeater_and_orchestrator.py", "test_causal_chain.py",
    "test_closed_loop_multihop.py", "test_causal_intervention.py",
}

# Multi-component end-to-end tests (dataset + model pipelines,
# package-level integration, real filesystem/dataset round-trips).
_INTEGRATION_FILES = {
    "test_quantum_twin_package.py", "test_audit_methodology.py", "test_dataset.py",
    "test_dual_head_causal_dataset.py", "test_wdm_vs_privileged.py",
    "test_wdm_vs_privileged_dualhead.py", "test_wdm_vs_privileged_multiseed.py",
    "test_lag_analysis_dualhead.py", "test_causal_analysis.py",
    "test_master_experiment_db.py", "test_temporal_leakage_audit.py",
}

# Tests specifically about STATISTICAL methodology (hypothesis testing,
# equivalence testing, coverage/calibration, multi-seed sweep logic) --
# a new category, per the master prompt v4's explicit request that
# "statistical" be separated from generic "unit" tests.
_STATISTICAL_FILES = {
    "test_equivalence_testing.py", "test_temporal_conformal.py", "test_uncertainty_methods.py",
    "test_risk_aware_sensitivity.py", "test_santanna_optical_campaign.py",
    "test_santanna_cross_campaign_validation.py",
    "test_santanna_wdm_information.py",
    "test_qkd_multihead.py", "test_measured_quantum_prediction.py",
    "test_kappa_calibration.py",
}

# Tests specifically about LATENCY/throughput benchmarking -- a new
# category, distinct from "physics" or generic "unit" correctness tests.
_BENCHMARK_FILES = {
    "test_edge_ai_benchmark.py", "test_edge_e2e_benchmark.py",
}

# Files known to take noticeably longer per test -- real model training,
# multi-seed loops, or dense parameter sweeps.
_SLOW_FILES = {
    "test_dual_head_causal_dataset.py", "test_wdm_vs_privileged.py",
    "test_wdm_vs_privileged_dualhead.py", "test_wdm_vs_privileged_multiseed.py",
    "test_lag_analysis_dualhead.py", "test_causal_analysis.py", "test_causal_chain.py",
    "test_closed_loop_multihop.py", "test_robust_training.py", "test_edge_ai_benchmark.py",
    "test_domain_shift_asymmetry_10seed.py",
}

# Newer, less-battle-tested coverage for recently-added features --
# still real tests, just flagged for extra scrutiny/re-review before a
# release, per the master prompt v4's "experimental" category.
_EXPERIMENTAL_FILES = {
    "test_domain_shift_experiment.py", "test_controller_robustness.py",
    "test_risk_aware_sensitivity.py", "test_multi_metric_scorecard.py",
    "test_validation_taxonomy.py",
}


def pytest_collection_modifyitems(config, items):
    import pytest

    for item in items:
        filename = os.path.basename(str(item.fspath))
        if filename in _PHYSICS_FILES:
            item.add_marker(pytest.mark.physics)
        elif filename in _INTEGRATION_FILES:
            item.add_marker(pytest.mark.integration)
        elif filename in _STATISTICAL_FILES:
            item.add_marker(pytest.mark.statistical)
        elif filename in _BENCHMARK_FILES:
            item.add_marker(pytest.mark.benchmark)
        else:
            item.add_marker(pytest.mark.unit)

        if filename in _SLOW_FILES:
            item.add_marker(pytest.mark.slow)
        if filename in _EXPERIMENTAL_FILES:
            item.add_marker(pytest.mark.experimental)


# --- Qt wrappers must only be garbage-collected on the thread that owns them -------------
# CPython's cyclic collector runs on whichever thread happens to allocate. When that is a
# worker thread (a model check, the console's service worker, the network-engine stepper)
# and the garbage contains PySide6 wrappers, Shiboken defers the C++ deletion to the main
# thread ("runDeletionInMainThread"); if Qt has deleted the object through its parent in
# the meantime the deferred deletion is a double free and the process dies with SIGSEGV
# inside an unrelated event filter. The product confines collection to the UI thread
# (`console.ui_gc`); the test harness does the same: automatic collection is off while a
# Qt test runs and one collection is made on the main thread after it.

_QT_MODULES: dict = {}


def _uses_qt(item) -> bool:
    """True for a test whose module binds PySide6 or console (Qt) objects."""
    module = getattr(item, "module", None)
    name = getattr(module, "__name__", None)
    if name is None or "PySide6.QtWidgets" not in sys.modules:
        return False
    if name not in _QT_MODULES:
        _QT_MODULES[name] = any(
            str(getattr(value, "__module__", "") or getattr(value, "__name__", "")).startswith(
                ("PySide6", "quantum_autopilot.console"))
            for value in vars(module).values())
    return _QT_MODULES[name]


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    if _uses_qt(item):
        gc.disable()


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item, nextitem):
    if _uses_qt(item):
        # Young generations after every Qt test; a full collection every tenth one (a full
        # pass over a heap with torch and pandas loaded costs about as much as a small test).
        _QT_MODULES["#"] = count = _QT_MODULES.get("#", 0) + 1
        gc.collect(2 if count % 10 == 0 else 1)
        gc.enable()
