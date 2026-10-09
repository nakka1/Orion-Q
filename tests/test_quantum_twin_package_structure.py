"""
tests/test_quantum_twin_package_structure.py

Regression guard for quantum_twin/__init__.py's lazy subpackage loading
(established in the eighty-fifth addendum during this project's
incremental content-migration series), and for the package's structural
integrity following the one-hundred-fifteenth addendum's elimination of
every root-level backward-compatibility shim.

This file replaces tests/test_quantum_twin_lazy_loading.py (137 tests,
accumulated across the eighty-fifth through one-hundred-fourteenth
addenda), the vast majority of which specifically verified that a root
-level shim module and its corresponding quantum_twin.* location
resolved to the identical object -- a guarantee that no longer applies
now that the root-level shims have been deleted entirely (TCC item 6's
explicit "repositório profissional" requirement). This file keeps the
subset of that history genuinely still relevant: tests of the
package's own lazy-loading behavior, and a curated set of real,
non-obvious technical findings from that migration series whose
underlying code paths remain part of the current architecture. The
full narrative of every addendum this file's predecessor's tests once
guarded is preserved in docs/history.md, not duplicated here.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib


# ---------------------------------------------------------------------
# Core package lazy-loading behavior
# ---------------------------------------------------------------------

def test_quantum_twin_import_does_not_eagerly_load_all_subpackages():
    """Regression guard for the eighty-fifth addendum's central fix:
    importing bare `quantum_twin` must NOT immediately import every
    subpackage into sys.modules -- verified in a clean subprocess."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; import quantum_twin; "
         "loaded = [m for m in ['quantum_twin.core', 'quantum_twin.optical', 'quantum_twin.quantum', "
         "'quantum_twin.ml', 'quantum_twin.control', 'quantum_twin.simulation', 'quantum_twin.evaluation'] "
         "if m in sys.modules]; print(len(loaded))"],
        capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        timeout=30,
    )
    n_eagerly_loaded = int(result.stdout.strip())
    assert n_eagerly_loaded == 0, (
        f"Expected zero subpackages eagerly loaded by bare 'import quantum_twin', got {n_eagerly_loaded}"
    )


def test_quantum_twin_subpackage_accessible_via_attribute():
    import quantum_twin
    assert quantum_twin.quantum is not None
    assert quantum_twin.evaluation is not None


def test_quantum_twin_dir_lists_all_subpackages():
    import quantum_twin
    listed = dir(quantum_twin)
    for name in ["core", "optical", "quantum", "ml", "control", "simulation", "evaluation"]:
        assert name in listed


def test_quantum_twin_unknown_attribute_raises_attribute_error():
    import quantum_twin
    try:
        quantum_twin.this_does_not_exist
        assert False, "Expected AttributeError"
    except AttributeError:
        pass


def test_quantum_twin_simulation_does_not_eagerly_load_all_submodules():
    """A subprocess-based check confirming that accessing ONE name from
    quantum_twin.simulation does not eagerly import its sibling submodules."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; from quantum_twin.simulation import QuantumNode; "
         "loaded = [m for m in ['quantum_twin.simulation.environment', "
         "'quantum_twin.simulation.orchestrator'] if m in sys.modules]; print(len(loaded))"],
        capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        timeout=30,
    )
    n_eagerly_loaded = int(result.stdout.strip())
    assert n_eagerly_loaded == 0, (
        f"Expected zero OTHER submodules eagerly loaded by accessing QuantumNode, got {n_eagerly_loaded}"
    )


def _assert_subpackage_lazy(subpackage_dotted_name, one_name, other_submodules):
    """Helper: verifies accessing ONE name from a subpackage does not
    eagerly load the OTHER submodules listed, via a clean subprocess."""
    import subprocess
    other_submodules_repr = repr(other_submodules)
    result = subprocess.run(
        [sys.executable, "-c",
         f"import sys; from {subpackage_dotted_name} import {one_name}; "
         f"loaded = [m for m in {other_submodules_repr} if m in sys.modules]; print(len(loaded))"],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), timeout=30,
    )
    return int(result.stdout.strip())


def test_quantum_subpackage_is_lazy():
    n = _assert_subpackage_lazy(
        "quantum_twin.quantum", "QuantumChannel",
        ["quantum_twin.quantum.memory", "quantum_twin.quantum.purification",
         "quantum_twin.quantum.swapping", "quantum_twin.quantum.physics_engine"])
    assert n == 0


def test_evaluation_subpackage_is_lazy():
    n = _assert_subpackage_lazy(
        "quantum_twin.evaluation", "compute_confusion_matrix",
        ["quantum_twin.evaluation.quantum", "quantum_twin.evaluation.energy",
         "quantum_twin.evaluation.statistics"])
    assert n == 0


def test_optical_subpackage_is_lazy():
    """quantum_twin/optical/telemetry.py has a genuine, correct MODULE
    -LEVEL dependency on quantum_twin.optical.wdm (for WDMTelemetry) --
    not an unwanted eager-loading regression, since it's a real
    dependency between two related submodules of the SAME subpackage.
    What must still hold: the package's own lazy __getattr__ must not
    ALSO eagerly load .sources (an unrelated submodule)."""
    n = _assert_subpackage_lazy(
        "quantum_twin.optical", "WDMTelemetryGenerator",
        ["quantum_twin.optical.sources"])
    assert n == 0


def test_ml_subpackage_is_lazy():
    n = _assert_subpackage_lazy(
        "quantum_twin.ml", "EdgeLSTM",
        ["quantum_twin.ml.losses", "quantum_twin.ml.calibration"])
    assert n == 0


def test_control_subpackage_is_lazy():
    n = _assert_subpackage_lazy(
        "quantum_twin.control", "DigitalTwinOrchestrator", ["quantum_twin.control.policies"])
    assert n == 0


def test_core_subpackage_is_lazy():
    n = _assert_subpackage_lazy(
        "quantum_twin.core", "PhysicsConfig", ["quantum_twin.core.state"])
    assert n == 0


# ---------------------------------------------------------------------
# The original circular-import trigger, and the module central to a
# second, distinct cycle -- both must still import cleanly against the
# CURRENT (no-root-shim) architecture.
# ---------------------------------------------------------------------

def test_environment_module_the_original_cycle_trigger_still_imports_cleanly():
    """The exact module (quantum_twin.simulation.environment) whose
    import chain caused the sixty-third addendum's circular-import
    failure must still import without error."""
    import quantum_twin.simulation.environment as environment
    assert environment is not None


def test_evaluation_subpackage_free_of_the_second_distinct_cycle():
    """This project's migration series hit a SECOND, distinct circular
    -import cycle (caused by quantum_twin/evaluation/statistics.py
    pointing at a stale location) -- verified fixed: importing
    quantum_twin.evaluation (which eagerly imports .statistics) must not raise."""
    import quantum_twin.evaluation
    assert quantum_twin.evaluation is not None


def test_causal_chain_classes_decoupled_from_swapping_module_to_avoid_a_real_cycle():
    """Regression guard for a real circular-import found during this
    project's migration series: quantum_twin.simulation.causal_chain
    imports back from quantum_twin.quantum.swapping, so
    quantum_twin/quantum/__init__.py's lazy mapping for
    CausalSwappingChain/GatedCausalSwappingChain/MLGatedCausalSwappingChain
    points directly at quantum_twin.simulation.causal_chain, not through
    quantum_twin.quantum.swapping -- verify this decoupled path still works."""
    from quantum_twin.quantum import CausalSwappingChain, GatedCausalSwappingChain, MLGatedCausalSwappingChain
    assert CausalSwappingChain is not None
    assert GatedCausalSwappingChain is not None
    assert MLGatedCausalSwappingChain is not None


# ---------------------------------------------------------------------
# Real, non-obvious findings from the migration series, still relevant
# to the current codebase
# ---------------------------------------------------------------------

def test_module_docstring_terminology_check_targets_the_real_implementation():
    """Regression guard: a module-content self-audit (checking for
    required terminology disclosures) must target the file where the
    substantive docstring actually lives (quantum_twin.evaluation.energy),
    not a stale location."""
    import quantum_twin.evaluation.energy as energy_module
    assert "simulation-based energy estimate" in energy_module.__doc__
    assert "model-based energy analysis" in energy_module.__doc__


def test_c_nisq_and_temporal_leakage_are_reachable_via_the_evaluation_package():
    """Regression guard: two real gaps found during this project's
    migration series (CNisqResult/compute_c_nisq had never been added
    to quantum_twin/evaluation/__init__.py's lazy mapping at all) --
    verify package-level access now works."""
    from quantum_twin.evaluation import compute_c_nisq, run_full_audit, AuditResult
    assert all([compute_c_nisq, run_full_audit, AuditResult])


def test_telemetry_source_uses_one_formal_contract():
    """Package and submodules must expose one source contract, not two ABCs."""
    from quantum_twin.optical import TelemetrySource as TS_package_level
    from quantum_twin.optical.sources import TelemetrySource as TS_sources
    from quantum_twin.optical.interface import TelemetrySource as TS_interface
    assert TS_package_level is TS_sources
    assert TS_package_level is TS_interface


def test_paired_analysis_reproduces_the_documented_real_result():
    """Direct regression guard: paired_analysis must reproduce the
    exact documented DualHead-vs-Blind result cited throughout this
    project's history."""
    from quantum_twin.evaluation.statistics import paired_analysis
    result = paired_analysis("DualHead", "Blind", [42.13, 53.37, 50.54], [31.03, 49.62, 39.45])
    assert result["Mean Diff (pp)"] == 8.65
    assert result["Cohen's d"] == 2.039


def test_run_statistical_significance_main_still_works_as_a_standalone_script():
    """Direct functional check: experiments/run_statistical_significance.py's
    own main() (genuine experiment-driving content) must still run
    correctly, producing the same real results, importing paired_analysis
    from its real quantum_twin location."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "experiments"))
    import run_statistical_significance
    results_df = run_statistical_significance.main()
    dh_row = results_df[results_df["Comparison"] == "DualHead vs. Blind"].iloc[0]
    assert dh_row["Mean Diff (pp)"] == 8.65


# ---------------------------------------------------------------------
# TCC item 6's "repositório profissional" requirement: no root-level
# shim modules remain
# ---------------------------------------------------------------------

_FORMER_ROOT_SHIM_NAMES = [
    "baselines", "causal_chain", "causal_intervention", "closed_loop_multihop_environment",
    "controller_robustness", "dataset_v3", "device_management", "energy_model",
    "entanglement_swapping", "environment", "equivalence_testing", "evaluation",
    "master_experiment_db", "model_selection_protocol", "models", "models_architectures",
    "models_dual_head", "models_probabilistic", "models_robust_training", "multiple_comparisons",
    "network_topology", "orchestrator", "physics_config", "policy_engine", "purification",
    "quantum_channel", "quantum_channel_v3", "quantum_memory", "quantum_runtime_profiler",
    "repeater", "repeater_chain", "reproducibility", "risk_aware_controller", "seed_registry",
    "simple_baselines", "telemetry", "telemetry_interface", "telemetry_source",
    "temporal_conformal", "temporal_leakage_audit", "three_state_controller",
    "uncertainty_methods", "validation_taxonomy",
]


def test_no_root_level_shim_files_remain():
    """Regression guard for the one-hundred-fifteenth addendum's
    elimination of every root-level backward-compatibility shim
    (TCC item 6's explicit "repositório profissional" requirement):
    none of the forty-three former shim modules should exist as a
    root-level .py file any longer."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    still_present = [name for name in _FORMER_ROOT_SHIM_NAMES
                      if os.path.exists(os.path.join(project_root, f"{name}.py"))]
    assert still_present == [], f"Found root-level shim files that should have been deleted: {still_present}"


def test_no_production_module_imports_from_run_star_files():
    """Direct, formal audit for the TCC item 6 rule: grep every file
    under src/quantum_twin/ for a module-level import from any
    run_*.py file. Must find NONE."""
    import re
    from pathlib import Path
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pattern = re.compile(r"^\s*(from|import)\s+run_", re.MULTILINE)
    offenders = []
    for path in Path(project_root, "src", "quantum_twin").rglob("*.py"):
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path))
    assert not offenders, f"Found production-module dependencies on run_*.py files: {offenders}"
