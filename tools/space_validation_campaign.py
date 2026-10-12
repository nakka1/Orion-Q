"""Independent optical comparison and unchanged backend observation replay.

No mission, hardware, key security or coherent orbital relay is qualified here.
Reference calculations live in space_validation_reference without Orion imports.
The author code executes in a subprocess, preserving its source and license.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from space_validation_reference import (  # noqa: E402
    AUTHOR_ARCHIVE_SHA256, OMEGA_RAD_S, SOURCES, atmospheric_capture,
    bernoulli_photons, carrier_doppler, gaussian_capture, ground_geometry,
    relative_geometry, sha256, validate_author_tree,
)

CRITERIA = {
    "author_numeric_relative_tolerance": 1e-8,
    "author_jitter_quadrature_relative_tolerance": 1e-6,
    "orion_far_field_relative_tolerance": .01,
    "range_relative_tolerance": 1e-10,
    "angle_absolute_tolerance_deg": 1e-8,
    "velocity_absolute_tolerance_km_s": 1e-10,
    "doppler_relative_tolerance": 1e-12,
    "loss_or_jitter_coincidence_ratio_max": .1,
    "bernoulli_sigma_limit": 6,
    "certified_secret_bits": 0,
    "stored_isl_pairs": 0,
    "scope": "comparisons under stated reduced hypotheses; discrepancies preserved as fidelity gaps",
}


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _author_root():
    roots = list((REPO / "outputs" / "space_validation_preparation_20261010" / "author-v1.1.0").glob("*"))
    return next((p for p in roots if (p / "scenarios/three_satellites/common_functions.py").is_file()), None)


def _probe_author(output, cases, root, *, jitter=False):
    prefix = "author-jitter" if jitter else "author"
    casefile = output / (prefix + "-inputs.json")
    resultfile = output / (prefix + "-results.json")
    _write(casefile, cases)
    command = [sys.executable, str(REPO / "tools/space_validation_reference.py"),
               "--author-root", str(root), "--cases", str(casefile), "--output", str(resultfile)]
    if jitter:
        command.append("--include-jitter")
    completed = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=60)
    (output / (prefix + "-probe.stdout.txt")).write_text(completed.stdout, encoding="utf-8")
    (output / (prefix + "-probe.stderr.txt")).write_text(completed.stderr, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"independent author probe failed ({completed.returncode}); see captured stderr")
    return json.loads(resultfile.read_text(encoding="utf-8"))


def _state(radius, longitude, *, velocity=(0., 0., 0.), latitude=0.):
    lat, lon = map(math.radians, (latitude, longitude))
    return {"ecef_km": [radius*math.cos(lat)*math.cos(lon), radius*math.cos(lat)*math.sin(lon), radius*math.sin(lat)],
            "velocity_ecef_km_s": list(velocity), "at_s": 0., "at_network_s": 0., "frame": "ECEF"}


def _geometry_cases(report, check):
    from quantum_autopilot.quantum_protocols.isl import inter_satellite_geometry
    from quantum_autopilot.quantum_protocols.orbits import state_geometry

    radius = (398600.4418 / OMEGA_RAD_S**2)**(1/3)
    fixtures = [("GEO-visible", _state(radius, -60), _state(radius, 0)),
                ("GEO-obstructed", _state(radius, -170), _state(radius, 10)),
                ("LEO-visible", _state(6878, -5), _state(6878, 5)),
                ("LEO-obstructed", _state(6878, -30), _state(6878, 30)),
                ("polar-obstructed", _state(6878, 0, latitude=90), _state(6878, 0, latitude=-90)),
                ("moving-visible", _state(6878, -5, velocity=(0, 7, 0)),
                 _state(7200, 5, velocity=(-1, 6, .3)))]
    for name, left, right in fixtures:
        expected = relative_geometry(left, right)
        actual = inter_satellite_geometry(left, right)
        row = {"case_id": name, "group": "isl_geometry", "inputs": {"left": left, "right": right},
               "reference": expected, "orion": actual}
        report["cases"].append(row)
        check(name + ":LOS", actual["earth_obstructed"] == expected["earth_obstructed"])
        for key in ("range_km", "flight_time_s", "point_ahead_urad", "radial_velocity_km_s", "transverse_velocity_km_s"):
            check(name + ":" + key, math.isclose(actual[key], expected[key], rel_tol=1e-10, abs_tol=1e-9))
    # Arbitrary input fixtures are independent of Orion orbit propagation.
    ground_fixtures = [("equatorial-GEO", _state(radius, 0), (0., 0.), 0.),
                       ("Salvador-LEO", _state(6878, -38.55, latitude=-12.97, velocity=(1, 6, 2)),
                        (-12.967, -38.446), 35.),
                       ("polar-OGS", _state(7378, 75, latitude=88, velocity=(2, 1, 0)), (89., 10.), 50.)]
    for name, state, station, altitude in ground_fixtures:
        reference = ground_geometry(state, station, altitude)
        actual = state_geometry(state, station, altitude)
        report["cases"].append({"case_id": name, "group": "ground_geometry",
            "inputs": {"state": state, "station": station, "altitude_m": altitude},
            "reference": reference, "orion": actual})
        for key in ("range_km", "radial_velocity_km_s", "azimuth_deg", "elevation_deg"):
            check(name + ":" + key, math.isclose(actual[key], reference[key], rel_tol=1e-10, abs_tol=1e-8))
        discrepancy = abs(actual["point_ahead_urad"] - reference["point_ahead_urad"])
        report["fidelity_gaps"].append({"case_id": name + ":ground-point-ahead",
            "orion_urad": actual["point_ahead_urad"], "inertial_reference_urad": reference["point_ahead_urad"],
            "absolute_discrepancy_urad": discrepancy,
            "reason": "Ground PAA uses satellite ECEF transverse velocity; ISL restores Earth rotation. Beacon-to-outgoing inertial convention differs.",
            "qualification": "not qualified for operational aiming; mission convention/calibration required"})


def _optical_cases(report, check, author):
    from quantum_autopilot.quantum_protocols.physics import satellite_optics
    from quantum_autopilot.quantum_protocols.isl import isl_optics

    for row, inp in zip(author["cases"], report["author_inputs"]):
        check(inp["case_id"] + ":author-integral", math.isclose(row["author_eta"], row["analytical_eta"],
              rel_tol=CRITERIA["author_numeric_relative_tolerance"], abs_tol=1e-15))
        check(inp["case_id"] + ":atmosphere", math.isclose(row["author_atmosphere"], atmospheric_capture(inp["elevation_deg"]), rel_tol=1e-12))
        geometry = {"range_km": inp["distance_m"]/1000, "elevation_deg": inp["elevation_deg"],
                    "radial_velocity_km_s": 0., "transverse_velocity_km_s": 0.,
                    "angular_tracking_rate_rad_s": 0., "point_ahead_urad": 0., "earth_obstructed": False}
        params = {"wavelength_nm": 780., "divergence_urad": inp["divergence_rad"]*1e6,
                  "receiver_diameter_m": 2*inp["receiver_radius_m"], "transmitter_diameter_m": .3,
                  "pointing_jitter_urad": 0., "tracking_latency_s": 0., "optical_efficiency": 1.,
                  "insertion_loss_db": 0., "extra_loss_db": 0., "zenith_optical_depth": -math.log(.8),
                  "doppler_residual_hz": 0., "min_elevation_deg": 10.}
        expected = row["author_eta"] * row["author_atmosphere"]
        ground = satellite_optics(geometry, params)
        vacuum = isl_optics(geometry, params, params)
        ground_error = abs(ground["channel_transmission"]-expected)/expected
        vacuum_error = abs(vacuum["channel_transmission"]-row["author_eta"])/row["author_eta"]
        report["cases"].append({"case_id": inp["case_id"], "group": "independent_optics",
            "inputs": inp, "author_capture": row["author_eta"], "reference_ground_eta": expected,
            "orion_ground_eta": ground["channel_transmission"], "orion_isl_eta": vacuum["channel_transmission"],
            "ground_relative_error": ground_error, "isl_relative_error": vacuum_error})
        check(inp["case_id"] + ":ground-farfield", ground_error <= CRITERIA["orion_far_field_relative_tolerance"])
        check(inp["case_id"] + ":vacuum-farfield", vacuum_error <= CRITERIA["orion_far_field_relative_tolerance"])
    # Quantify the differing interpretations of jitter rather than hiding them.
    for sigma_urad in (.3, 1., 3., 6.):
        inp = report["author_inputs"][1]
        expected = gaussian_capture(inp["distance_m"], inp["divergence_rad"], inp["receiver_radius_m"], pointing_sigma_rad=sigma_urad*1e-6)
        geometry = {"range_km": inp["distance_m"]/1000, "elevation_deg": 90., "radial_velocity_km_s": 0.}
        params = {"wavelength_nm": 780., "divergence_urad": 3., "receiver_diameter_m": 1.,
                  "pointing_jitter_urad": sigma_urad, "zenith_optical_depth": 0., "optical_efficiency": 1.,
                  "insertion_loss_db": 0., "doppler_residual_hz": 0.}
        actual = satellite_optics(geometry, params)["channel_transmission"]
        report["fidelity_gaps"].append({"case_id": f"jitter-{sigma_urad}-urad", "group": "pointing-distribution",
            "orion_eta": actual, "gaussian_2d_jitter_reference_eta": expected,
            "relative_discrepancy": abs(actual-expected)/expected,
            "reason": "Orion applies exp(-2(sigma/theta)^2) offset/RMS surrogate; author convolves per-axis Gaussian pointing distribution over finite aperture.",
            "qualification": "no distribution parity claimed; measured jitter distribution and aperture model needed"})
    for radial in (-7.5, 0., 7.5):
        for wavelength in (780., 850., 1550.):
            geom = {"range_km": 500., "elevation_deg": 90., "radial_velocity_km_s": radial}
            actual = satellite_optics(geom, {"wavelength_nm": wavelength, "doppler_residual_hz": 0.})
            expected = carrier_doppler(radial, wavelength)
            check(f"Doppler:{radial}:{wavelength}", math.isclose(actual["doppler_shift_hz"], expected, rel_tol=1e-12, abs_tol=1e-6))
            check(f"Doppler-compensation:{radial}:{wavelength}", abs(actual["doppler_residual_hz"]) < 1e-6)
            report["cases"].append({"case_id": f"Doppler:{radial}:{wavelength}", "group": "carrier_doppler",
                "radial_km_s": radial, "wavelength_nm": wavelength, "reference_hz": expected,
                "orion_hz": actual["doppler_shift_hz"]})


def _capture_cases(output, report, check, seeds):
    from quantum_autopilot.quantum_protocols.contracts import Acquisition, DeviceCommand, ProtocolConfig, ProtocolError
    from quantum_autopilot.quantum_protocols.orbits import geo_reference_orbit
    from quantum_autopilot.quantum_protocols.sources import SimulatedAcquisitionSource, MeasurementFileSource
    from quantum_autopilot.quantum_protocols.satellite_contracts import assemble, packets
    from quantum_autopilot.quantum_protocols.satellite_processing import process_observation
    from quantum_autopilot.quantum_protocols.topology import expand_global
    from quantum_autopilot.network_engine.institutional import brazil_institutions

    topology = expand_global(brazil_institutions())[0]
    rawcfg = next(c for n in topology.nodes for c in n.telemetry.get("technology_sessions", []) if c["session_id"] == "RIO-SATELLITE")
    ground = ProtocolConfig.from_dict(rawcfg)
    isl = ProtocolConfig("QUAL-ISL", ("satellite_isl",), ("GEO-A", "GEO-B"), ("GEO-A", "GEO-B"), ("ISL-AB",),
        parameters={"satellite_orbits": {"GEO-A": geo_reference_orbit(-60.), "GEO-B": geo_reference_orbit(0.)},
            "pair_source_hz": 20000., "block_s": .5, "timeout_s": 4., "max_attempts": 2,
            "transmitter_diameter_m": 2., "receiver_diameter_m": 4., "divergence_urad": .4,
            "pointing_jitter_urad": .05, "tracking_latency_s": 0., "optical_efficiency": .8,
            "insertion_loss_db": 0., "background_cps": 1., "dark_count_cps": 5.,
            "timing_jitter_ps": 50., "sync_tolerance_s": 1e-6})
    environment = {"ground_positions": [(-22.953888889, -43.173611111), (-22.8606982, -43.2310552)],
                   "ground_altitudes_m": [0., 0.]}
    settings = [("nominal", {}, "nominal"), ("degraded", {}, "degraded"),
                ("loss", {"extra_loss_db": 100.}, "nominal"),
                ("timing-jitter", {"timing_jitter_ps": 1e6}, "nominal"),
                ("unavailable", {}, "unavailable"), ("recovery", {}, "recovery")]
    for technology, base, operation, duration, env in (
        ("satellite_doppler", ground, "satellite_block", 1., environment),
        ("satellite_isl", isl, "isl_block", .5, {}),
    ):
        for seed in seeds:
            summaries = {}
            source = SimulatedAcquisitionSource(seed)
            physical_blocks = [("cloud-blocked", {"cloud": True}, "nominal")] if technology == "satellite_doppler" else [
                ("earth-obstructed", {"satellite_orbits": {"GEO-A": geo_reference_orbit(-170.), "GEO-B": geo_reference_orbit(10.)}}, "nominal"),
                ("pointing-disabled", {"pointing_enabled": False}, "nominal")]
            for name, changes, scenario in settings[:-1] + physical_blocks + settings[-1:]:
                case_id = f"{technology}-{seed}-{name}"
                cfg = replace(base, seed=seed, scenario=scenario, parameters={**base.parameters, **changes})
                count = len(cfg.link_ids)
                cmd = DeviceCommand(case_id, cfg.session_id, operation, cfg.path,
                    tuple(f"q-{i}" for i in range(count)), tuple(f"c-{i}" for i in range(count)),
                    0., duration+2., 1, {"duration_s": duration})
                record = source.acquire(cmd, cfg, env)
                if record is None:
                    summaries[name] = {"acquisition": "unavailable", "coincidences": 0}
                    report["cases"].append({"case_id": case_id, "group": "backend_capture",
                        "technology": technology, "seed": seed, "scenario": scenario, **summaries[name]})
                    check(case_id + ":absence-explicit", name == "unavailable")
                    continue
                raw_path = output / (case_id + ".acquisition.jsonl")
                # Preserve simulated payload provenance while recording the
                # acquisition envelope as an explicit measurement-file fixture.
                file_record = replace(record, origin="measurement_file", source_id="measurement_file")
                raw_path.write_text(file_record.canonical_json() + "\n", encoding="utf-8")
                replay = MeasurementFileSource(raw_path).acquire(cmd, cfg, env)
                check(case_id + ":file-ingress-equal", replay.raw == record.raw)
                raw = replay.raw
                if technology == "satellite_doppler":
                    messages = packets(raw, cmd, source_id="qualification-fixture", origin="simulated", sequence=1)
                    _write(output / (case_id + ".ogs-packets.json"), messages)
                    raw = assemble(messages, parameters=cfg.parameters)
                    # quantum_flight_s is an optional simulator diagnostic;
                    # the OGS contract uses per-tag referenced flight_time_ps.
                    check(case_id + ":three-stream-contract-preserved", all(raw[k] == replay.raw[k] for k in raw))
                context = {"parameters": cfg.parameters, "endpoints": cfg.endpoints, "environment": env,
                           "origin": "simulated", "duration_s": duration}
                result = process_observation(raw, context)
                alternate = process_observation(copy.deepcopy(raw), {**context, "origin": "measurement_file"})
                check(case_id + ":processing-origin-equivalence", result == alternate)
                check(case_id + ":original-vs-ingress-processing", result == process_observation(replay.raw, context))
                _write(output / (case_id + ".result.json"), result)
                summary = {"emitted_pairs": raw["emitted_pairs"], "source_sequence": record.sequence,
                    "epoch_scope": "independent block at network_time=0; continuous RNG/sequence only, not causal scheduler recovery",
                    "detector_tags": [len(b["time_ps"]) for b in raw["tdc"]],
                    "coincidences": result["coincidences"], "sifted_bits": result["sifted_bits"], "qber": result["qber"],
                    "conditional_key_estimate_bits": result["conditional_key_estimate_bits"],
                    "certified_secret_bits": result["certified_secret_bits"],
                    "available": result["available"], "postprocessing": result["postprocessing"],
                    "observation_sha256": sha256(raw_path), "result_sha256": sha256(output/(case_id+".result.json"))}
                summaries[name] = summary
                report["cases"].append({"case_id": case_id, "group": "backend_capture", "technology": technology,
                    "seed": seed, "scenario": scenario, "parameters": cfg.parameters, **summary})
                check(case_id + ":no-certified-material", result["certified_secret_bits"] == 0)
                check(case_id + ":raw-not-final-metrics", not (set(raw) & {"qber", "coincidences", "final_key", "skr_bps"}))
                if technology == "satellite_isl":
                    check(case_id + ":consumed-not-relay", result["measurement_resources"]["stored_pairs"] == 0)
                if name in {"cloud-blocked", "earth-obstructed", "pointing-disabled"}:
                    check(case_id + ":physical-unavailability", not result["available"] and result["coincidences"] == 0)
                if name == "nominal":
                    mutations = [("lost-tags", "tdc_overflow", lambda r: r["tdc"][0].update(lost_tags=1)),
                        ("clock-uncertainty", "unsynchronized", lambda r: r["tdc"][0].update(clock_uncertainty_ps=1e9)),
                        ("derived-key", "precomputed_result", lambda r: r.update(final_key="synthetic-forbidden-field"))]
                    if technology == "satellite_doppler":
                        mutations.append(("wrong-frame", "invalid_ephemeris", lambda r: r["housekeeping"].update(frame="TEME")))
                    else:
                        mutations.append(("wrong-frame", "invalid_ephemeris", lambda r: r["housekeeping"][cfg.endpoints[0]].update(frame="TEME")))
                    for invalid_name, expected_error, mutate in mutations:
                        invalid = copy.deepcopy(raw)
                        mutate(invalid)
                        observed_error = None
                        try:
                            # Adulteration enters through the same typed
                            # Acquisition admission as file/device data.
                            admitted = Acquisition.from_dict({**record.to_dict(), "raw": invalid})
                            process_observation(admitted.raw, context)
                        except ProtocolError as exc:
                            observed_error = exc.code
                        report["cases"].append({"case_id": case_id + ":" + invalid_name, "group": "invalid-input",
                            "technology": technology, "seed": seed, "reference_observation_sha256": sha256(raw_path),
                            "expected_error": expected_error, "observed_error": observed_error})
                        check(case_id + ":reject-" + invalid_name, observed_error == expected_error)
            nominal = summaries["nominal"]["coincidences"]
            check(f"{technology}-{seed}:nominal-activity", nominal > 0)
            for name in ("loss", "timing-jitter", "degraded"):
                check(f"{technology}-{seed}:{name}-reduces-counts", summaries[name]["coincidences"] < max(1, nominal * .1))
            check(f"{technology}-{seed}:recovery-observed", summaries["recovery"]["coincidences"] > 0)
            check(f"{technology}-{seed}:continuous-source-sequence", summaries["recovery"]["source_sequence"] > summaries["nominal"]["source_sequence"])


def run_campaign(output_dir: Path, seeds=(101, 202, 303), *, author_root=None):
    output = Path(output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("preserve previous evidence: campaign output must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema": "orion.space-independent-validation.v1", "status": "running", "checks": [], "cases": [],
              "fidelity_gaps": [], "seeds": list(seeds), "criteria": CRITERIA, "sources": SOURCES,
              "physical_hardware_validated": False, "mission_ready": False, "security_certified": False,
              "physical_actuation": False, "coherent_orbital_relay_validated": False,
              "limitations": ["Comparisons validate reduced equations and observation processing, not a real satellite/OGS or measured channel.",
                  "Author repeater memory/BSM scenarios are not executed end-to-end: Orion ISL currently measures and consumes pairs.",
                  "Ground pointing and jitter-model discrepancies remain explicit; Sun/attitude/refraction/EOP and calibrated APT absent.",
                  "No usable keys, hardware actuation, physical RF/payload evidence or TRL advancement is inferred.",
                  "File origin equivalence exercises software contracts only; no forged physical provenance is generated.",
                  "Capture cases exercise acquisition/file/OGS assembly/protocol processor; they do not qualify distributed authenticated transport, service scheduling or hardware adaptation."]}
    def check(name, passed):
        report["checks"].append({"check_id": name, "passed": bool(passed)})
    try:
        if not seeds or any(type(x) is not int or x < 0 for x in seeds) or len(set(seeds)) != len(seeds):
            raise ValueError("distinct nonnegative configured seeds required")
        root = Path(author_root) if author_root else _author_root()
        if root is None:
            raise FileNotFoundError("pinned author v1.1.0 archive is required; no Orion-derived oracle fallback")
        archive = REPO / "outputs/space_validation_preparation_20261010/zenodo-6384558-v1.1.0.zip"
        if not archive.is_file() or sha256(archive) != AUTHOR_ARCHIVE_SHA256:
            raise ValueError("independent author archive hash mismatch or missing")
        report["author_tree_hashes"] = validate_author_tree(root, archive)
        report["author_inputs"] = [{"case_id": f"Gaussian-{distance}-km", "distance_m": distance*1000.,
            "divergence_rad": 3e-6, "sender_radius_m": .15, "receiver_radius_m": .5, "wavelength_m": 780e-9,
            "elevation_deg": elevation} for distance, elevation in ((400, 90), (1000, 60), (2000, 30), (35000, 20))]
        author = _probe_author(output, report["author_inputs"], root)
        report["author_provenance"] = {k: v for k, v in author.items() if k != "cases"}
        check("author-source-unmodified", author["upstream_unchanged"])
        jitter_input = [{**report["author_inputs"][1], "case_id": "Gaussian-jitter-1urad", "pointing_sigma_rad": 1e-6}]
        jitter_probe = _probe_author(output, jitter_input, root, jitter=True)
        jitter_row = jitter_probe["cases"][0]
        check("author-jitter-source-unmodified", jitter_probe["upstream_unchanged"])
        check("author-jitter-numerical-integral", math.isclose(jitter_row["author_eta"], jitter_row["analytical_eta"],
              rel_tol=CRITERIA["author_jitter_quadrature_relative_tolerance"]))
        report["cases"].append({"case_id": jitter_row["case_id"], "group": "author-jitter-oracle-check", **jitter_row})
        _geometry_cases(report, check)
        _optical_cases(report, check, author)
        for seed in seeds:
            for probability in (.001, .01, .1):
                photons = bernoulli_photons(seed, 1_000_000, probability)
                report["cases"].append({"case_id": f"reference-photons-{seed}-{probability}", "group": "raw-reference-events", **photons})
                check(f"reference-photons-{seed}-{probability}:binomial", abs(photons["detected"]-photons["expected"]) <= 6*photons["std"])
        _capture_cases(output, report, check, seeds)
        report["checks_passed"] = sum(r["passed"] for r in report["checks"])
        report["checks_total"] = len(report["checks"])
        report["status"] = "partial_verified" if all(r["passed"] for r in report["checks"]) else "failed"
    except Exception as exc:
        report["status"] = "error"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        _write(output / "report.json", report)
        raise
    _write(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", nargs="+", type=int, default=[101, 202, 303])
    parser.add_argument("--author-root", type=Path)
    args = parser.parse_args()
    result = run_campaign(args.output, args.seeds, author_root=args.author_root)
    print(json.dumps({"status": result["status"], "checks_total": result["checks_total"],
                      "checks_passed": result["checks_passed"], "fidelity_gaps": len(result["fidelity_gaps"])}))
    return 0 if result["status"] == "partial_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
