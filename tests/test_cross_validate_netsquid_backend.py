"""Control-plane tests; these do not pretend to qualify an absent NetSquid SDK."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cross_validate_netsquid_backend",
                                            ROOT / "tools/cross_validate_netsquid_backend.py")
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)
pytestmark = pytest.mark.unit


def request():
    return {"schema": backend.INPUT_SCHEMA, "seed": 2026101001, "cases": [
        {"id": "fibre", "kind": "transmission",
         "parameters": {"length_km": 20., "attenuation_db_km": .2, "insertion_db": 1., "trials": 2048}},
        {"id": "stored", "kind": "memory",
         "parameters": {"fidelity": .9, "elapsed_s": .2, "coherence_s": 1.}},
        {"id": "swap", "kind": "swapping",
         "parameters": {"first": .9, "second": .8, "operation_error": 0.}},
        {"id": "purify", "kind": "purification",
         "parameters": {"first": .9, "second": .8, "operation_error": 0., "trials": 2048}},
        {"id": "message", "kind": "classical_delay",
         "parameters": {"distance_km": 100., "light_speed_km_s": 200000., "send_time_s": .01}},
    ]}


def test_all_contract_cases_are_valid_without_importing_sdk(monkeypatch):
    def forbidden():
        raise AssertionError("validation must not load SDK")
    monkeypatch.setattr(backend, "_load_backend", forbidden)
    data = request()
    assert backend.validate_request(data) is data


@pytest.mark.parametrize("value", [None, {}, [], {"schema": "bad", "seed": 3, "cases": []}])
def test_bad_top_level_inputs_are_rejected(value):
    with pytest.raises(ValueError):
        backend.validate_request(value)


@pytest.mark.parametrize("seed", [True, -1, 2**32, 1.5, "1", None])
def test_seed_requires_uint32(seed):
    data = request()
    data["seed"] = seed
    with pytest.raises(ValueError, match="seed"):
        backend.validate_request(data)


@pytest.mark.parametrize("kind,field,value", [
    ("transmission", "trials", True), ("transmission", "trials", 0),
    ("transmission", "trials", backend.MAX_TRIALS + 1),
    ("transmission", "length_km", -1.), ("transmission", "attenuation_db_km", float("nan")),
    ("transmission", "insertion_db", float("inf")), ("memory", "fidelity", 1.1),
    ("memory", "coherence_s", 0.), ("memory", "elapsed_s", -1.),
    ("swapping", "first", True), ("swapping", "operation_error", -0.01),
    ("purification", "first", .5), ("purification", "second", .4),
    ("classical_delay", "light_speed_km_s", 0.), ("classical_delay", "send_time_s", -1.),
])
def test_invalid_physical_configuration_rejected_before_sdk(monkeypatch, kind, field, value):
    imported = []
    monkeypatch.setattr(backend, "_load_backend", lambda: imported.append(True))
    data = request()
    next(case for case in data["cases"] if case["kind"] == kind)["parameters"][field] = value
    with pytest.raises(ValueError):
        backend.run_request(data)
    assert imported == []


@pytest.mark.parametrize("change", ["duplicate", "extra", "missing", "unknown", "blank"])
def test_ambiguous_or_incomplete_cases_are_rejected(change):
    data = request()
    if change == "duplicate":
        data["cases"].append(copy.deepcopy(data["cases"][0]))
    elif change == "extra":
        data["cases"][0]["parameters"]["supplied_result"] = .9
    elif change == "missing":
        del data["cases"][0]["parameters"]["length_km"]
    elif change == "unknown":
        data["cases"][0]["kind"] = "tf_qkd"
    else:
        data["cases"][0]["id"] = "  "
    with pytest.raises(ValueError):
        backend.validate_request(data)


@pytest.mark.parametrize("exception", [ModuleNotFoundError, ImportError, OSError, RuntimeError])
def test_absent_or_broken_sdk_never_produces_scientific_results(monkeypatch, exception):
    def absent():
        raise exception("credential-like string must not be serialized")
    monkeypatch.setattr(backend, "_load_backend", absent)
    report = backend.run_request(request())
    assert report["schema"] == backend.OUTPUT_SCHEMA
    assert report["backend"] == "netsquid"
    assert report["metadata"]["native_api_execution_demonstrated"] is False
    assert report["metadata"]["sdk_version"] is None
    assert report["metadata"]["availability"] == "not_demonstrated"
    assert len(report["cases"]) == 5
    assert all(case["status"] == "not_demonstrated" for case in report["cases"])
    assert all(case["metrics"] == {} and case["events"] == [] for case in report["cases"])
    assert "credential-like" not in json.dumps(report)


def test_cli_creates_explicit_negative_evidence_and_never_overwrites(monkeypatch, tmp_path):
    def absent():
        raise ModuleNotFoundError("netsquid")
    monkeypatch.setattr(backend, "_load_backend", absent)
    source, target = tmp_path / "input.json", tmp_path / "result.json"
    source.write_text(json.dumps(request()), encoding="utf-8")
    assert backend.main(["--input", str(source), "--output", str(target)]) == 0
    evidence = target.read_bytes()
    assert all(case["status"] == "not_demonstrated" for case in json.loads(evidence)["cases"])
    with pytest.raises(FileExistsError):
        backend.main(["--input", str(source), "--output", str(target)])
    assert target.read_bytes() == evidence


def test_invalid_json_is_rejected_and_output_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(backend, "_load_backend", lambda: pytest.fail("SDK import before validation"))
    source, target = tmp_path / "invalid.json", tmp_path / "never.json"
    source.write_text('{"schema":', encoding="utf-8")
    with pytest.raises(SystemExit):
        backend.main(["--input", str(source), "--output", str(target)])
    assert not target.exists()
