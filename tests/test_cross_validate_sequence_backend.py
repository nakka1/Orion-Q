"""Contracts plus independent subprocess execution of the actual SeQUeNCe runner.

The simulator tests require ORIONQ_SEQUENCE_PYTHON pointing at a separately
provisioned Python environment; missing hardware or missing dependencies never
produces a fabricated reference result.  Unit validation needs only stdlib.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "tools" / "cross_validate_sequence_backend.py"
spec = importlib.util.spec_from_file_location("sequence_cross_backend_contract", RUNNER)
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


def payload(cases=None):
    return {"schema": backend.INPUT_SCHEMA, "seed": 2026101001, "cases": cases or [
        {"id": "memory", "kind": "memory", "parameters": {"fidelity": .9, "elapsed_s": .2, "coherence_s": 2}}]}


@pytest.mark.parametrize("seed", [True, -1, 2**63, 1.0, "1", None])
def test_rejects_invalid_seed_without_loading_simulator(seed, monkeypatch):
    source = payload()
    source["seed"] = seed
    monkeypatch.setattr(backend, "_load_sequence", lambda *_: pytest.fail("must validate before importing SeQUeNCe"))
    with pytest.raises(ValueError, match="seed"):
        backend.run(source, "does-not-exist")


@pytest.mark.parametrize("change", ["schema", "empty", "duplicate", "bad_parameters", "missing_kind"])
def test_rejects_invalid_envelope(change):
    source = payload()
    if change == "schema":
        source["schema"] = "other-schema"
    elif change == "empty":
        source["cases"] = []
    elif change == "duplicate":
        source["cases"].append(dict(source["cases"][0]))
    elif change == "bad_parameters":
        source["cases"][0]["parameters"] = []
    else:
        del source["cases"][0]["kind"]
    with pytest.raises(ValueError):
        backend.validate_payload(source)


@pytest.mark.parametrize("name,value", [
    ("fidelity", -0.1), ("fidelity", 1.01), ("fidelity", float("nan")),
    ("fidelity", float("inf")), ("fidelity", True), ("elapsed_s", -.1),
    ("coherence_s", 0), ("coherence_s", -1)])
def test_rejects_invalid_memory_parameters(name, value):
    source = payload()
    source["cases"][0]["parameters"][name] = value
    with pytest.raises(ValueError):
        backend.validate_payload(source)


@pytest.mark.parametrize("trials", [0, -1, 100001, True, 1.0, "100"])
def test_rejects_invalid_trial_counts(trials):
    source = payload([{"id": "loss", "kind": "transmission", "parameters": {
        "length_km": 50, "attenuation_db_km": .2, "trials": trials}}])
    with pytest.raises(ValueError, match="trials"):
        backend.validate_payload(source)


def test_unknown_technology_is_not_silently_substituted():
    source = payload([{"id": "tf-qkd", "kind": "tf_qkd", "parameters": {}}])
    backend.validate_payload(source)
    assert "tf_qkd" not in backend.SUPPORTED_KINDS


def test_runner_has_no_product_or_compatibility_import():
    source = RUNNER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    assert not any(name.startswith(("quantum_autopilot", "qnet_network", "qnet_qkd")) for name in modules)
    assert "ensure_sequence" not in source
    assert "PINNED_COMMIT" in source and backend.PINNED_COMMIT == "3133c66d80a898c6c3cc9af9eec3fa94fdbe2233"


@pytest.fixture(scope="module")
def real_sequence_report(tmp_path_factory):
    executable = os.environ.get("ORIONQ_SEQUENCE_PYTHON")
    sequence_src = os.environ.get("SEQUENCE_SRC", "C:/SeQUeNCe")
    if not executable or not Path(executable).is_file():
        pytest.skip("ORIONQ_SEQUENCE_PYTHON must identify a real separately provisioned simulator environment")
    output = tmp_path_factory.mktemp("sequence-cross-real")
    cases = [
        {"id": "loss", "kind": "transmission", "parameters": {"length_km": 50, "attenuation_db_km": .2, "insertion_db": 2, "trials": 256}},
        {"id": "no_loss", "kind": "transmission", "parameters": {"length_km": 0, "attenuation_db_km": .2, "trials": 32}},
        {"id": "memory", "kind": "memory", "parameters": {"fidelity": .9, "elapsed_s": .2, "coherence_s": 2}},
        {"id": "memory_zero_time", "kind": "memory", "parameters": {"fidelity": .9, "elapsed_s": 0, "coherence_s": 2}},
        {"id": "memory_mixed", "kind": "memory", "parameters": {"fidelity": .25, "elapsed_s": 100, "coherence_s": 2}},
        {"id": "swap", "kind": "swapping", "parameters": {"first": .9, "second": .8, "trials": 256}},
        {"id": "swap_ideal", "kind": "swapping", "parameters": {"first": 1, "second": 1, "trials": 32}},
        {"id": "swap_noise", "kind": "swapping", "parameters": {"first": .9, "second": .8, "operation_error": .1}},
        {"id": "purify", "kind": "purification", "parameters": {"first": .9, "second": .8, "trials": 256}},
        {"id": "purify_ideal", "kind": "purification", "parameters": {"first": 1, "second": 1, "trials": 32}},
        {"id": "purify_mixed", "kind": "purification", "parameters": {"first": .25, "second": .25, "trials": 256}},
        {"id": "delay", "kind": "classical_delay", "parameters": {"distance_km": 50, "light_speed_km_s": 200000, "send_time_s": .002}},
        {"id": "delay_zero", "kind": "classical_delay", "parameters": {"distance_km": 0, "light_speed_km_s": 200000}},
        {"id": "purify_noise", "kind": "purification", "parameters": {"first": .9, "second": .8, "operation_error": .1}},
        {"id": "zero_length_insertion", "kind": "transmission", "parameters": {"length_km": 0, "attenuation_db_km": .2, "insertion_db": 2}},
        {"id": "tf-qkd", "kind": "tf_qkd", "parameters": {}},
    ]
    source = output / "input.json"
    source.write_text(json.dumps(payload(cases)), encoding="utf-8")
    destination = output / "output.json"
    completed = subprocess.run([executable, str(RUNNER), "--input", str(source), "--output", str(destination),
                                "--sequence-src", sequence_src], capture_output=True, text=True, timeout=180,
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert completed.returncode == 1, completed.stdout + completed.stderr  # Explicit unsupported cases included.
    assert destination.is_file(), completed.stderr
    report = json.loads(destination.read_text(encoding="utf-8"))
    assert not any(row["status"] == "error" for row in report["cases"]), report["cases"]
    return report, {row["id"]: row for row in report["cases"]}, source, destination, executable, sequence_src


def test_real_framework_identity_and_no_shims(real_sequence_report):
    report, *_ = real_sequence_report
    assert report["backend"] == "sequence" and report["schema"] == backend.OUTPUT_SCHEMA
    assert report["metadata"]["commit"] == backend.PINNED_COMMIT
    assert report["metadata"]["version"] == "1.2.0"
    assert report["metadata"]["shims"] == []
    assert len(report["metadata"]["source_sha256"]) > 50
    assert report["physical_hardware_validated"] is False
    assert report["system_trl_assigned"] is False


def test_real_channel_uses_received_events(real_sequence_report):
    _, rows, *_ = real_sequence_report
    loss = rows["loss"]
    assert loss["status"] == "pass"
    assert loss["metrics"]["transmission_probability"] == pytest.approx(10**(-1.2), abs=1e-12)
    assert 0 < loss["metrics"]["transmitted"] < loss["metrics"]["trials"]
    assert loss["metrics"]["received_before_run"] == 0
    assert loss["events"][-1]["count"] == loss["metrics"]["transmitted"]
    assert rows["no_loss"]["metrics"]["transmitted"] == 32


def test_real_memory_maps_pair_lifetime_and_runs_both_ends(real_sequence_report):
    _, rows, *_ = real_sequence_report
    row = rows["memory"]
    assert row["metrics"]["fidelity"] == pytest.approx(.25 + .65 * math.exp(-.1), abs=1e-12)
    assert row["metrics"]["framework_coherence_s"] == pytest.approx(16/3)
    assert row["events"][-1]["count"] == 2
    assert rows["memory_zero_time"]["metrics"]["fidelity"] == pytest.approx(.9)
    assert rows["memory_mixed"]["metrics"]["fidelity"] == pytest.approx(.25)


def test_real_swapping_executes_all_four_measurement_and_correction_branches(real_sequence_report):
    _, rows, *_ = real_sequence_report
    row = rows["swap"]
    assert row["metrics"]["fidelity"] == pytest.approx(.7266666666666667, abs=1e-12)
    assert row["metrics"]["framework_formalism_fidelity_difference"] < 1e-12
    assert row["metrics"]["accepted"] == row["metrics"]["trials"]
    assert [branch["outcome"] for branch in row["events"][1]["branches"]] == [[0, 0], [0, 1], [1, 0], [1, 1]]
    assert all(branch["retained_fidelity"] == pytest.approx(row["metrics"]["fidelity"])
               for branch in row["events"][1]["branches"])
    assert rows["swap_ideal"]["metrics"]["fidelity"] == pytest.approx(1)
    assert rows["swap_noise"]["metrics"]["fidelity"] < row["metrics"]["fidelity"]


def test_real_purification_reads_density_branches_not_final_success_coin(real_sequence_report):
    _, rows, *_ = real_sequence_report
    row = rows["purify"]
    assert row["metrics"]["fidelity"] == pytest.approx(.8831521739130435, abs=1e-12)
    assert row["metrics"]["success_probability"] == pytest.approx(.8177777777777778, abs=1e-12)
    assert row["metrics"]["framework_formalism_fidelity_difference"] < 1e-12
    assert 0 < row["metrics"]["accepted"] < row["metrics"]["trials"]
    branches = row["events"][1]["branches"]
    assert sum(b["probability"] for b in branches if b["accepted"]) == pytest.approx(row["metrics"]["success_probability"])
    assert [b["accepted"] for b in branches] == [True, False, False, True]
    assert rows["purify_ideal"]["metrics"]["fidelity"] == pytest.approx(1)
    assert rows["purify_ideal"]["metrics"]["accepted"] == 32
    assert rows["purify_mixed"]["metrics"]["fidelity"] == pytest.approx(.25)
    assert rows["purify_mixed"]["metrics"]["success_probability"] == pytest.approx(.5)


def test_real_classical_channel_cannot_arrive_before_propagation(real_sequence_report):
    _, rows, *_ = real_sequence_report
    row = rows["delay"]
    assert row["metrics"]["delay_s"] == .00025
    assert row["metrics"]["arrival_time_s"] == .00225
    assert row["metrics"]["received_before_arrival"] == 0
    assert row["events"][-1]["time_ps"] - row["events"][0]["time_ps"] == 250000000
    assert rows["delay_zero"]["metrics"]["arrival_time_s"] == 0


def test_unsupported_physics_is_explicit_not_a_pass(real_sequence_report):
    report, rows, *_ = real_sequence_report
    assert report["passed"] is False
    for name in ("purify_noise", "zero_length_insertion", "tf-qkd"):
        assert rows[name]["status"] == "not_demonstrated"
        assert rows[name]["metrics"] == {} and rows[name]["limitations"]


def test_cli_refuses_to_overwrite_existing_evidence(real_sequence_report):
    _, _, source, destination, executable, sequence_src = real_sequence_report
    original = destination.read_bytes()
    completed = subprocess.run([executable, str(RUNNER), "--input", str(source), "--output", str(destination),
                                "--sequence-src", sequence_src], capture_output=True, text=True, timeout=180)
    assert completed.returncode != 0 and destination.read_bytes() == original


def test_contract_import_requires_only_stdlib():
    completed = subprocess.run([sys.executable, "-I", "-c", "import importlib.util; "
        f"s=importlib.util.spec_from_file_location('backend', {str(RUNNER)!r}); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        "print(m.INPUT_SCHEMA)"], capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == backend.INPUT_SCHEMA
