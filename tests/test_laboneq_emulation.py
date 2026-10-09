"""Emulation contracts, optional dependency isolation and verifiable failures."""
import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from quantum_autopilot.integrations.laboneq import emulation as bridge

ROOT = Path(__file__).resolve().parents[1]
REQUEST = json.loads((ROOT / "configs/integrations/laboneq_emulation_v1.json").read_bytes())


@pytest.mark.parametrize("field,value", [
    ("backend_mode", "physical"), ("setup_profile", "arbitrary_device"),
    ("experiment_kind", "swap"), ("amplitudes", [True]), ("amplitudes", [float("nan")]),
    ("amplitudes", [1.1]), ("amplitudes", []), ("amplitudes", [0.1] * 17),
    ("averages", 3), ("averages", True), ("drive_length_s", 33e-9),
    ("drive_length_s", 6e-6), ("timeout_s", 121), ("timeout_s", True),
    ("experiment_id", "../outside"),
])
def test_unsupported_requests_never_launch(field, value, tmp_path, monkeypatch):
    request = copy.deepcopy(REQUEST)
    request[field] = value
    monkeypatch.setattr(bridge.subprocess, "run", lambda *a, **k: pytest.fail("invalid request launched"))
    with pytest.raises(ValueError):
        bridge.run_emulation(request, tmp_path / "result")
    assert not (tmp_path / "result").exists()


def test_unknown_and_duplicate_fields_are_not_ignored():
    with pytest.raises(ValueError):
        bridge.validate_request({**REQUEST, "host": "device.local"})
    raw = json.dumps(REQUEST).replace('"backend_mode": "emulation"',
                                      '"backend_mode": "physical", "backend_mode": "emulation"')
    with pytest.raises(ValueError, match="duplicate"):
        bridge.read_request(raw)


def test_unavailable_dependency_produces_receipt_without_import_or_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "availability", lambda: {"status": "UNAVAILABLE", "version": None})
    monkeypatch.setattr(bridge.subprocess, "run", lambda *a, **k: pytest.fail("missing dependency launched"))
    output = tmp_path / "receipt"
    report = bridge.run_emulation(REQUEST, output)
    assert report["record"]["status"] == "UNAVAILABLE"
    assert report["record"]["physical_action_performed"] is False
    assert json.loads((output / "laboneq-emulation.json").read_bytes()) == report
    with pytest.raises(FileExistsError):
        bridge.run_emulation(REQUEST, output)


@pytest.mark.parametrize("failure,status", [("timeout", "TIMED_OUT"), ("compile", "FAILED"),
                                            ("malformed", "FAILED"), ("corruption", "FAILED")])
def test_worker_failure_keeps_status_and_never_exports_acquisition(tmp_path, monkeypatch, failure, status):
    monkeypatch.setattr(bridge, "availability", lambda: {"status": "AVAILABLE", "version": bridge.VERSION})

    def run(argv, **kwargs):
        assert argv[2] == bridge.WORKER_MODULE
        assert kwargs["timeout"] == REQUEST["timeout_s"]
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if failure == "compile":
            return subprocess.CompletedProcess(argv, 1, "", "compiler failed")
        result, work = Path(argv[-2]), Path(argv[-1])
        acquisition = {"shape": [3], "real": [0.0] * 3, "imag": [0.0] * 3,
                       "artifacts": {name: "0" * 64 for name in
                                     ("device_setup.json", "experiment.json", "compiled_experiment.json")}}
        for name in acquisition["artifacts"]:
            (work / name).write_bytes(b"changed")
        result.write_text(json.dumps(acquisition) if failure == "corruption" else "invalid-json")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(bridge.subprocess, "run", run)
    output = tmp_path / "failure"
    report = bridge.run_emulation(REQUEST, output)
    assert report["record"]["status"] == status and report["record"]["error"]
    assert report["record"]["acquisition"] is None
    assert not (output / "compiled_experiment.json").exists()


@pytest.mark.skipif(bridge.availability()["status"] != "AVAILABLE", reason="isolated LabOne Q 26.7.0 environment required")
def test_real_compiler_emulator_round_trip_and_manifest(tmp_path):
    output = tmp_path / "real-emulation"
    report = bridge.run_emulation(REQUEST, output)
    record = report["record"]
    assert record["status"] == "EMULATED", record["error"]
    assert record["acquisition"]["shape"] == [3]
    assert record["acquisition"]["axis_values"] == [[0.1, 0.2, 0.3]]
    assert json.loads((output / "experiment.json").read_bytes())["__data__"]["uid"] == REQUEST["experiment_id"]
    assert record["evidence"] == "mock_emulated_acquisition"
    assert record["physical_action_performed"] is False and record["policy_activated"] is False
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert len(manifest["files"]) == 5
    for name, sha in manifest["files"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == sha
    for name, sha in record["acquisition"]["artifacts"].items():
        assert manifest["files"][name] == sha
