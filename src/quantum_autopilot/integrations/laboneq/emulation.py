"""Bounded LabOne Q 26.7 experiment bridge. Physical connection is not exposed."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from time import monotonic

from quantum_autopilot.evidence_export import canonical, export_bundle, receipt

VERSION = "26.7.0"
WORKER_MODULE = "quantum_autopilot.integrations.laboneq.emulation"
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
REFERENCE = ("https://docs.zhinst.com/labone_q_user_manual/core/functionality_and_concepts/"
             "10_advanced_topics/tutorials/06_context_based_dsl.html")


def availability():
    if importlib.util.find_spec("laboneq") is None:
        return {"status": "UNAVAILABLE", "version": None, "required_version": VERSION}
    try:
        installed = importlib.metadata.version("laboneq")
    except importlib.metadata.PackageNotFoundError:
        return {"status": "UNAVAILABLE", "version": None, "required_version": VERSION}
    return {"status": "AVAILABLE" if installed == VERSION else "INCOMPATIBLE",
            "version": installed, "required_version": VERSION}


def validate_request(request):
    required = {"schema", "experiment_id", "backend_mode", "setup_profile", "experiment_kind",
                "amplitudes", "drive_length_s", "averages", "timeout_s"}
    if not isinstance(request, dict) or set(request) != required:
        raise ValueError("request fields do not match the emulation contract")
    if (request["schema"] != "orion.laboneq-emulation-request.v1" or request["backend_mode"] != "emulation"
            or request["setup_profile"] != "shfqc_demo_v1"
            or request["experiment_kind"] != "drive_readout_amplitude"):
        raise ValueError("only the declared SHFQC amplitude experiment in emulation is supported")
    if not isinstance(request["experiment_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}",
                                                                       request["experiment_id"]):
        raise ValueError("invalid experiment_id")
    amplitudes = request["amplitudes"]
    if not isinstance(amplitudes, list) or not 1 <= len(amplitudes) <= 16:
        raise ValueError("amplitudes must contain between 1 and 16 values")
    for value in amplitudes:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 1:
            raise ValueError("amplitudes must be finite numbers between -1 and 1")
    length = request["drive_length_s"]
    if (isinstance(length, bool) or not isinstance(length, (int, float)) or not math.isfinite(length)
            or not 32e-9 <= length <= 5e-6 or not math.isclose(length / 8e-9, round(length / 8e-9), abs_tol=1e-9)):
        raise ValueError("drive_length_s must be 32 ns to 5 us on an 8 ns grid")
    averages = request["averages"]
    if type(averages) is not int or not 1 <= averages <= 64 or averages & (averages - 1):
        raise ValueError("averages must be a power of two between 1 and 64")
    timeout = request["timeout_s"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout):
        raise ValueError("timeout_s must be numeric")
    if not 1 <= timeout <= 120:
        raise ValueError("timeout_s must be between 1 and 120")
    return json.loads(canonical(request))


def read_request(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate request field: {key}")
            result[key] = value
        return result
    return validate_request(json.loads(raw, object_pairs_hook=unique))


def _execute(request, directory):
    """Run only inside the bounded worker; never query options or connect physically."""
    validate_request(request)
    if availability()["status"] != "AVAILABLE":
        raise ValueError("LabOne Q 26.7.0 is required")
    import numpy as np
    from laboneq.contrib.example_helpers.generate_device_setup import generate_device_setup_qubits
    from laboneq.serializers import to_json
    from laboneq.simple import AcquisitionType, AveragingMode, Experiment, Session, SweepParameter, pulse_library

    setup, qubits = generate_device_setup_qubits(
        number_qubits=1, shfqc=[{"serial": "DEV12001", "number_of_channels": 2,
                                "readout_multiplex": 1, "options": None}],
        include_flux_lines=False, query_options=False, query_zsync=False,
        setup_name="orion_emulated_shfqc")
    experiment = Experiment(uid=request["experiment_id"], signals=["drive", "measure", "acquire"])
    for name in experiment.signals:
        experiment.map_signal(name, qubits[0].signals[name])
    sweep = SweepParameter(uid="orion_amplitude", values=request["amplitudes"])
    drive = pulse_library.const(uid="orion_drive", length=request["drive_length_s"], amplitude=1.0)
    readout = pulse_library.const(uid="orion_measure", length=1e-6, amplitude=0.2)
    kernel = pulse_library.const(uid="orion_kernel", length=1e-6, amplitude=1.0)
    with experiment.acquire_loop_rt(uid="orion_average", count=request["averages"],
                                    acquisition_type=AcquisitionType.INTEGRATION,
                                    averaging_mode=AveragingMode.CYCLIC):
        with experiment.sweep(uid="orion_sweep", parameter=sweep):
            with experiment.section(uid="orion_drive_section"):
                experiment.play("drive", drive, amplitude=sweep)
            with experiment.section(uid="orion_readout_section", play_after="orion_drive_section"):
                experiment.play("measure", readout)
                experiment.acquire("acquire", handle="orion_readout", kernel=kernel)
            with experiment.section(uid="orion_relax", length=1e-6):
                for name in experiment.signals:
                    experiment.reserve(name)
    session = Session(device_setup=setup, log_level=40)
    session.connect(do_emulation=True)
    try:
        compiled = session.compile(experiment)
        result = session.run(compiled).get_result("orion_readout")
    finally:
        session.disconnect()
    arrays = np.asarray(result.data)
    if arrays.shape != (len(request["amplitudes"]),) or not np.isfinite(arrays).all():
        raise ValueError("unexpected emulated acquisition shape or values")
    artifacts = {}
    for name, obj in (("device_setup.json", setup), ("experiment.json", experiment),
                      ("compiled_experiment.json", compiled)):
        raw = to_json(obj)
        raw = raw.encode("utf-8") if isinstance(raw, str) else raw
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ValueError("compiled artifact exceeds the declared size limit")
        with (Path(directory) / name).open("xb") as stream:
            stream.write(raw)
        artifacts[name] = hashlib.sha256(raw).hexdigest()
    return {"shape": list(arrays.shape), "axis_names": result.axis_name,
            "axis_values": [np.asarray(axis).tolist() for axis in result.axis],
            "real": arrays.real.tolist(), "imag": arrays.imag.tolist(), "artifacts": artifacts}


def run_emulation(request, output):
    request = validate_request(request)
    output = Path(output)
    if output.exists():
        raise FileExistsError("output already exists")
    dependencies = availability()
    versions = {}
    for name in ("numpy", "xarray", "zhinst-core", "zhinst-toolkit"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    start = monotonic()
    record = {"schema": "orion.laboneq-emulation-result.v1", "request": request,
              "request_sha256": hashlib.sha256(canonical(request).encode()).hexdigest(),
              "dependencies": dependencies, "status": dependencies["status"], "acquisition": None,
              "environment": {"python": sys.version, "os": platform.platform(), "versions": versions,
                              "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
              "error": None, "evidence": "mock_emulated_acquisition", "reference": REFERENCE,
              "physical_action_performed": False, "policy_activated": False,
              "limitations": ["Mock acquisition values are debugging data, not measured quantum outcomes.",
                              "No mapping of network PURIFY/SWAP or SO(3) corrections to physical protocols."]}
    artifact_bytes = {}
    with tempfile.TemporaryDirectory(prefix="orion-laboneq-", dir=output.parent) as work:
        work = Path(work)
        if dependencies["status"] == "AVAILABLE":
            source, result_path = work / "request.json", work / "worker.json"
            source.write_text(canonical(request), encoding="utf-8")
            try:
                process = subprocess.run([sys.executable, "-m", WORKER_MODULE, "--worker", str(source),
                                          str(result_path), str(work)], capture_output=True, text=True,
                                         timeout=request["timeout_s"], check=False,
                                         creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                if process.returncode != 0:
                    record.update(status="FAILED", error=process.stderr[-1500:])
                else:
                    if result_path.stat().st_size > 65536:
                        raise ValueError("emulated result exceeds 64 KiB")
                    acquisition = json.loads(result_path.read_text(encoding="utf-8"))
                    if (acquisition["shape"] != [len(request["amplitudes"])]
                            or set(acquisition["artifacts"]) != {"device_setup.json", "experiment.json", "compiled_experiment.json"}
                            or len(acquisition["real"]) != len(request["amplitudes"])
                            or len(acquisition["imag"]) != len(request["amplitudes"])):
                        raise ValueError("emulated result does not match the requested sweep")
                    canonical(acquisition)  # Refuse non-finite results before recording success.
                    for name, expected in acquisition["artifacts"].items():
                        path = work / name
                        if path.stat().st_size > MAX_ARTIFACT_BYTES:
                            raise ValueError("compiled artifact exceeds size limit")
                        raw = path.read_bytes()
                        if hashlib.sha256(raw).hexdigest() != expected:
                            raise ValueError("compiled artifact changed")
                        artifact_bytes[name] = raw
                    record.update(status="EMULATED", acquisition=acquisition)
            except subprocess.TimeoutExpired:
                record.update(status="TIMED_OUT", error="Emulation worker exceeded its declared budget.")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                artifact_bytes.clear()
                record.update(status="FAILED", error=f"{type(exc).__name__}: {exc}"[-1500:])
        record["duration_s"] = monotonic() - start
        report = receipt(record)
        text = ("# ORION-Q — LabOne Q emulation\n\n"
                f"Status: {record['status']}\n\nEvidence: mock_emulated_acquisition\n\n"
                f"LabOne Q: {dependencies['version']}\n\n"
                f"Request SHA256: {record['request_sha256']}\n\n"
                f"Receipt SHA256: {report['record_sha256']}\n\n"
                + (f"Error: {record['error']}\n\n" if record["error"] else "")
                + "\n".join(f"- {item}" for item in record["limitations"]) + "\n")
        export_bundle(report, text, output, stem="laboneq-emulation", extra_files=artifact_bytes)
    return report


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == "--worker":
        source, target, directory = map(Path, arguments[1:])
        target.write_text(json.dumps(_execute(read_request(source.read_bytes()), directory)),
                          encoding="utf-8")
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    try:
        with args.request.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("request exceeds 64 KiB")
        report = run_emulation(read_request(raw), args.output)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Emulation refused: {exc}\n")
    print(f"{report['record']['status']} | physical_action_performed=false | {args.output}")
    return 0 if report["record"]["status"] == "EMULATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
