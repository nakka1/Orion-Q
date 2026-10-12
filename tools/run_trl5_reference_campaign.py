"""Independent analytic checks of production model elements, never TRL certification.

No GUI, hardware, authentication, network calls or operational workspace is used.
The bibliography is checked offline; its URLs were inspected on 2026-10-09.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import sys
from urllib.parse import urlparse

import numpy as np

from quantum_autopilot.quantum_protocols import physics
from quantum_autopilot.quantum_protocols.contracts import (
    Acquisition, DeviceCommand, ProtocolConfig, validate_binding,
)
from quantum_autopilot.quantum_protocols.protocols import MemoryAdapter, PurificationAdapter, SwappingAdapter
from quantum_autopilot.quantum_protocols.sources import SimulatedAcquisitionSource
from quantum_autopilot.product import product_version

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "tests/fixtures/trl5_refs/references.json"
SCHEMA = "orion.trl5.reference-campaign.v1"
ALLOWED_HOSTS = {"www.nist.gov", "www.bipm.org", "arxiv.org", "qiskit.github.io", "science.nasa.gov"}
REQUIRED_REFERENCES = {"db", "light", "bbpssw", "swapping", "depolarizing", "orbit", "gavignet"}


def finite(value, name, low=0., high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name}: finite real number required")
    if value < low or high is not None and value > high:
        raise ValueError(f"{name}: outside supported range")
    return float(value)


def load_references(path=CATALOG):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema") != "orion.trl5.reference-catalog.v1":
        raise ValueError("invalid reference catalog schema")
    rows = document.get("references", [])
    if {row.get("id") for row in rows} != REQUIRED_REFERENCES or len(rows) != len(REQUIRED_REFERENCES):
        raise ValueError("missing or duplicate reference")
    for row in rows:
        url = urlparse(row.get("url", ""))
        if url.scheme != "https" or url.hostname not in ALLOWED_HOSTS or not url.path:
            raise ValueError("unattributed or unsupported reference URL")
        if not row.get("title") or not row.get("scope"):
            raise ValueError("reference title and bounded scope required")
    return document


def bell_probabilities(fidelity):
    fidelity = finite(fidelity, "fidelity", 0., 1.)
    return np.array([fidelity] + [(1. - fidelity) / 3.] * 3)


def bell_vectors():
    # Explicit computational-basis kets, independent of production Werner helpers.
    return np.array([[1, 0, 0, 1], [1, 0, 0, -1], [0, 1, 1, 0], [0, 1, -1, 0]]) / math.sqrt(2.)


def independent_bbpssw(first, second):
    """16-dimensional state, bilateral CNOT permutation, target Z projections."""
    vectors = bell_vectors()
    states = [sum(p * np.outer(v, v) for p, v in zip(bell_probabilities(f), vectors))
              for f in (first, second)]
    rho = np.kron(*states)
    unitary = np.zeros((16, 16))
    for index in range(16):
        a, b, c, d = ((index >> shift) & 1 for shift in (3, 2, 1, 0))
        destination = (a << 3) | (b << 2) | ((c ^ a) << 1) | (d ^ b)
        unitary[destination, index] = 1.
    output = unitary @ rho @ unitary.T
    retained = np.zeros((4, 4))
    for target in (0, 3):
        indices = [4 * source + target for source in range(4)]
        retained += output[np.ix_(indices, indices)]
    success = float(np.trace(retained))
    fidelity = float(vectors[0] @ retained @ vectors[0] / success)
    return success, fidelity


def independent_memory(fidelity, elapsed_s, coherence_s):
    """Exponentiate an isotropic four-state Markov generator in the Bell basis."""
    elapsed_s = finite(elapsed_s, "elapsed_s")
    coherence_s = finite(coherence_s, "coherence_s", 1e-12)
    generator = (np.ones((4, 4)) / 4. - np.eye(4)) / coherence_s
    eigenvalues, eigenvectors = np.linalg.eigh(generator)
    evolved = eigenvectors @ np.diag(np.exp(eigenvalues * elapsed_s)) @ eigenvectors.T @ bell_probabilities(fidelity)
    return float(evolved[0])


def independent_swap(first, second):
    # Bell Pauli error labels compose by XOR; identity has label zero.
    output = np.zeros(4)
    for a, pa in enumerate(bell_probabilities(first)):
        for b, pb in enumerate(bell_probabilities(second)):
            output[a ^ b] += pa * pb
    return float(output[0])


def independent_orbit(time_s, altitude_km):
    """RK4 integration of Newton's vector equation; no Kepler phase formula."""
    time_s = finite(time_s, "time_s")
    radius = 6371. + finite(altitude_km, "altitude_km", 100.)
    mu, spin = 398600.4418, 7.2921159e-5
    state = np.array([radius, 0., 0., 0., math.sqrt(mu / radius), 0.])

    def derivative(value):
        position = value[:3]
        return np.concatenate((value[3:], -mu * position / np.linalg.norm(position) ** 3))

    steps = max(1, math.ceil(time_s))
    dt = time_s / steps
    for _ in range(steps):
        k1 = derivative(state)
        k2 = derivative(state + dt * k1 / 2.)
        k3 = derivative(state + dt * k2 / 2.)
        k4 = derivative(state + dt * k3)
        state += dt * (k1 + 2. * k2 + 2. * k3 + k4) / 6.
    turn = spin * time_s
    rotation = np.array([[math.cos(turn), math.sin(turn), 0.],
                         [-math.sin(turn), math.cos(turn), 0.], [0., 0., 1.]])
    position = rotation @ state[:3]
    velocity = rotation @ state[3:] - np.cross([0., 0., spin], position)
    delta = position - np.array([6371., 0., 0.])
    distance = float(np.linalg.norm(delta))
    return distance, float(delta @ velocity / distance)


def comparison(name, actual, expected, reference, api, inputs, *, atol=1e-12, rtol=1e-10,
               assumptions="Ideal reduced model; configured parameters, no measured device calibration."):
    actual, expected = float(actual), float(expected)
    valid = math.isfinite(actual) and math.isfinite(expected)
    error = abs(actual - expected) if valid else None
    return {"name": name, "passed": valid and error <= atol + rtol * abs(expected),
            "classification": "analytic_verification", "experimental_calibration": "not_demonstrated",
            "reference_ids": [reference], "production_api": api, "inputs": inputs,
            "actual": actual if math.isfinite(actual) else None,
            "expected": expected if math.isfinite(expected) else None, "absolute_error": error,
            "absolute_tolerance": atol, "relative_tolerance": rtol, "assumptions": assumptions}


def process_fixture(adapter, operation, raw, context, sequence):
    """Use the production binding contract before the production processor.

    These are analytic synthetic outcomes, explicitly origin=simulated. They
    never pretend that bibliography or published summary means are device data.
    """
    command = DeviceCommand(f"analytic-{sequence}", "reference", operation, ("A", "B"),
                            ("Q",), ("C",), 0., 2., 1)
    acquisition = Acquisition(command.command_id, command.session_id, operation, "analytic-fixture",
                              "simulated", sequence, 1., 0., ("Q",), ("C",), raw)
    validate_binding(acquisition, command, last_sequence=sequence - 1, source_id="analytic-fixture",
                     origin="simulated", now_s=1., sync_tolerance_s=1e-6)
    return adapter.process(acquisition.raw, context)


def aggregate_integrity(root):
    path = root / "data/reference/gavignet_2024_published_aggregate.csv"
    readme = root / "data/reference/README.md"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 6 or len({row["condition_id"] for row in rows}) != 6:
        raise ValueError("invalid published aggregate granularity")
    for row in rows:
        if row["observation_level"] not in {"published_condition_mean", "published_24h_mean"}:
            raise ValueError("aggregate is not an independent raw observation")
        for field, maximum in (("secure_key_rate_kbps", None), ("qber_percent", 100.), ("fiber_length_km", None)):
            finite(float(row[field]), field, high=maximum)
        if not row["source_location"] or not row["transcription_note"]:
            raise ValueError("published aggregate provenance missing")
    if "2312.11924" not in readme.read_text(encoding="utf-8"):
        raise ValueError("published aggregate citation missing")
    return {"classification": "published_aggregate_integrity", "rows": len(rows),
            "reference_ids": ["gavignet"], "path": str(path.relative_to(root)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "experimental_calibration": "not_demonstrated", "model_fit_performed": False,
            "reason": "No raw samples, uncertainties, matched model observables or independent calibration split."}


def run_campaign(seed=20261009, *, root=ROOT, catalog=CATALOG):
    if type(seed) is not int or not 0 <= seed < 2 ** 64:
        raise ValueError("seed must be an unsigned 64-bit integer")
    root = Path(root).resolve()
    catalog = Path(catalog).resolve()
    if not catalog.is_relative_to(root):
        raise ValueError("reference catalog must be inside the source root")
    references = load_references(catalog)
    checks = []
    acquisition_summaries = []
    # Exact decadic power anchors, not another implementation of transmission.
    for length, insertion, expected in ((0., 0., 1.), (50., 0., .1), (100., 0., .01), (50., 10., .01)):
        checks.append(comparison(f"fibre_power_{length}_{insertion}", physics.transmission(length, .2, insertion),
                                 expected, "db", "physics.transmission", {"length_km": length, "alpha_db_km": .2,
                                 "insertion_db": insertion}, assumptions="Power dB convention; alpha=.2 dB/km is assumed, not fitted."))
    source = SimulatedAcquisitionSource(seed)
    config = ProtocolConfig("reference", ("quantum_memory", "entanglement_swapping"), ("A", "B"),
                            ("A", "B"), ("L",), parameters={"route_factor": 1., "insertion_loss_db": 0.,
                            "attenuation_db_km": .2}, seed=seed)
    for length in (10., 50., 100.):
        command = DeviceCommand(f"flight-{length}", "reference", "generate", ("A", "B"), ("Q",), ("C",),
                                0., 3., 1, {"length_km": length, "duration_s": 1.})
        acquisition = source.acquire(command, config, {})
        validate_binding(acquisition, command, last_sequence=acquisition.sequence - 1, source_id=source.source_id,
                         origin="simulated", now_s=acquisition.observed_at_s, sync_tolerance_s=1e-6)
        acquisition_summaries.append({"operation": acquisition.operation, "source_id": acquisition.source_id,
                                      "origin": acquisition.origin, "sequence": acquisition.sequence,
                                      "length_km": length, "observed_at_s": acquisition.observed_at_s,
                                      "raw": acquisition.raw})
        checks.append(comparison(f"fibre_flight_{length}", acquisition.raw["quantum_flight_s"],
                                 length * 1000. * 1.468 / 299792458., "light", "SimulatedAcquisitionSource.acquire",
                                 {"length_km": length, "group_index_assumed": 1.468}))
    for index, (first, second) in enumerate(((.51, .51), (.6, .9), (.8, .8), (.9, .99), (1., 1.)), 1):
        expected_p, expected_f = independent_bbpssw(first, second)
        context = {"first_fidelity": first, "second_fidelity": second, "operation_error": 0., "operation": "purify"}
        actual = process_fixture(PurificationAdapter(), "purify", {"alice_outcome": 0, "bob_outcome": 0}, context, index)
        for metric, expected in (("success_probability", expected_p), ("fidelity", expected_f)):
            checks.append(comparison(f"bbpssw_{index}_{metric}", actual[metric], expected, "bbpssw",
                                     "Acquisition/validate_binding/PurificationAdapter.process", context,
                                     assumptions="Independent Werner inputs, ideal CNOT/readout, accepted equal outcomes, no gate noise."))
        swapped = process_fixture(SwappingAdapter(), "bsm", {"bsm_success": 1, "bell_outcome": 2}, context, index)
        checks.append(comparison(f"swap_{index}", swapped["fidelity"], independent_swap(first, second), "swapping",
                                 "Acquisition/validate_binding/SwappingAdapter.process", context,
                                 assumptions="Independent Bell-diagonal inputs, ideal heralded BSM and Pauli-frame correction."))
    for index, (fidelity, elapsed, coherence) in enumerate(((1., 0., 1.), (.9, .2, 1.), (.8, 2., .5), (.25, 3., 1.)), 1):
        context = {"fidelity": fidelity, "elapsed_s": elapsed, "coherence_s": coherence}
        actual = process_fixture(MemoryAdapter(), "readout", {"readout": 1}, context, index)
        checks.append(comparison(f"memory_{index}", actual["fidelity"], independent_memory(fidelity, elapsed, coherence),
                                 "depolarizing", "Acquisition/validate_binding/MemoryAdapter.process", context,
                                 assumptions="Isotropic continuous-time depolarization to I/4, coherence_s=1/rate; excludes T1/T2 amplitude damping."))
    for time_s, altitude in ((0., 500.), (300., 500.), (900., 1000.)):
        geometry = physics.circular_ephemeris(time_s, latitude=0., longitude=0., altitude_km=altitude, inclination_deg=0.)
        expected_range, expected_radial = independent_orbit(time_s, altitude)
        for metric, expected in (("range_km", expected_range), ("radial_velocity_km_s", expected_radial)):
            checks.append(comparison(f"orbit_{time_s}_{metric}", geometry[metric], expected, "orbit",
                                     "physics.circular_ephemeris", {"time_s": time_s, "altitude_km": altitude},
                                     atol=2e-7, rtol=1e-9, assumptions="Equatorial circular two-body initial state; independently integrated Newton ODE, spherical rotating Earth."))
        optics = physics.satellite_optics(geometry, {})
        checks.append(comparison(f"vacuum_flight_{time_s}", optics["flight_time_s"], expected_range * 1000. / 299792458.,
                                 "light", "physics.satellite_optics", {"range_km_independent": expected_range}))
    aggregate = aggregate_integrity(root)
    paths = [Path(__file__).resolve(), catalog, root / "tests/test_trl5_reference_campaign.py",
             root / "data/reference/gavignet_2024_published_aggregate.csv",
             root / "data/reference/README.md"]
    paths += [root / f"src/quantum_autopilot/quantum_protocols/{name}.py"
              for name in ("physics", "contracts", "protocols", "sources")]
    source_hashes = {path.relative_to(root).as_posix():
                     hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    normalized = {"seed": seed, "checks": checks, "acquisition_summaries": acquisition_summaries,
                  "experimental_reference_inventory": [aggregate]}
    scientific_fingerprint = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":"),
                                                      allow_nan=False).encode("utf-8")).hexdigest()
    return {"schema": SCHEMA, "passed": all(check["passed"] for check in checks), "seed": seed,
            "app_version": product_version(), "scientific_fingerprint_sha256": scientific_fingerprint,
            "scope": "Analytic source qualification of quantum_autopilot.quantum_protocols reduced kernels, acquisition binding and processing adapters.",
            "classification": "analytic_verification", "experimental_calibration": "not_demonstrated",
            "physical_action": False, "physical_action_performed": False, "trl5_demonstrated": False,
            "checks": checks, "references": references, "experimental_reference_inventory": [aggregate],
            "acquisition_summaries": acquisition_summaries,
            "limitations": ["Passing analytic checks does not demonstrate TRL5, hardware qualification or security certification.",
                            "No device-specific coefficient fit, measurement uncertainty or relevant physical environment demonstrated.",
                            "No atmospheric/turbulence/pointing loss calibration; satellite optics flight time only is verified.",
                            "Published aggregate means are not raw acquisitions and do not calibrate these model elements.",
                            "Protocol acquisition binding and processors are exercised; full application/runtime scheduling is outside scope."],
            "source_hashes": source_hashes, "environment": {"python": sys.version, "platform": platform.platform(),
            "numpy": np.__version__, "python_executable": sys.executable,
            "package_version": importlib.metadata.version("quantum-twin"),
            "gui_used": False, "network_used": False, "hardware_used": False}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261009)
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    if output.exists():
        raise ValueError("reference campaign requires a new output directory")
    report = run_campaign(args.seed)
    output.mkdir(parents=True)
    path = output / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]), "report": str(path),
                      "experimental_calibration": "not_demonstrated", "physical_action": False}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
