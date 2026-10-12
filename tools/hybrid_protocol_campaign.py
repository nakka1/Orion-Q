"""Frozen-input comparative BBM92 time-tag campaign, with no physical actuation.

run_campaign(output, seeds) returns a JSON-compatible report and preserves every
input, actual result, reference result, check and failure. Physical-origin cases
are synthetic contract exercises through the read-only OGS adapter; they are
never advertised as satellite measurements or hardware qualification.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hybrid_protocol_reference import REFERENCE, finite_reference, observation_reference, toeplitz_gf2  # noqa: E402


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def fixture_config():
    from quantum_autopilot.network_engine.institutional import brazil_institutions
    from quantum_autopilot.quantum_protocols.contracts import ProtocolConfig
    from quantum_autopilot.quantum_protocols.topology import expand_global

    topology = expand_global(brazil_institutions())[0]
    raw = next(c for node in topology.nodes for c in node.telemetry.get("technology_sessions", [])
               if c["session_id"] == "RIO-SATELLITE")
    return ProtocolConfig.from_dict(raw), topology


def make_command(config, name, duration):
    from quantum_autopilot.quantum_protocols.contracts import DeviceCommand

    return DeviceCommand(name, config.session_id, "satellite_block", config.path,
                         ("q-reference-a", "q-reference-b"), ("c-reference-a", "c-reference-b"),
                         0.0, duration + 2.0, 1, {"duration_s": duration})


def generated_fixture(config, seed, name, changes=None, duration=1.0):
    from quantum_autopilot.quantum_protocols.sources import SimulatedAcquisitionSource

    cfg = replace(config, parameters={**config.parameters, **(changes or {})}, seed=seed)
    cmd = make_command(cfg, name, duration)
    env = {"ground_positions": [(-22.953888889, -43.173611111), (-22.8606982, -43.2310552)],
           "ground_altitudes_m": [0.0, 0.0]}
    acquisition = SimulatedAcquisitionSource(seed).acquire(cmd, cfg, env)
    return acquisition.raw, cmd, cfg


def replace_tags(raw, left, right, left_detectors=None, right_detectors=None):
    """Prepare raw, explicitly synthetic detector events, never protocol metrics."""
    raw = copy.deepcopy(raw)
    for index, stamps in enumerate((left, right)):
        ds = (left_detectors, right_detectors)[index]
        raw["tdc"][index].update(time_ps=list(stamps), detector=list(ds if ds is not None else [0] * len(stamps)),
                                 flight_time_ps=[0] * len(stamps), propagation_delay_ps=0,
                                 clock_offset_ps=0, epoch_utc_s=1700000000, epoch_fraction_ps=0,
                                 dead_time_discarded=0)
        raw["tracking"][index].update(locked=True, open_intervals_s=[[raw["start_s"], raw["end_s"]]])
    raw["emitted_pairs"] = max(len(left), len(right))
    raw["parameter_provenance"] = {"fixture": "synthetic detector observations; no measured satellite signal"}
    return raw


def process_checked(raw, config):
    """Run the actual adapter while independently checking every universal hash."""
    import numpy as np
    from quantum_autopilot.quantum_protocols import satellite_processing as implementation
    from quantum_autopilot.quantum_protocols.protocols import SatelliteAdapter

    hashes = []
    original_hash = implementation.toeplitz

    def checked_hash(bits, seed, length):
        actual = original_hash(bits, seed, length)
        expected = toeplitz_gf2(bits, seed, length)
        hashes.append({"input_bits": len(bits), "output_bits": length,
                       "exact_gf2_match": actual.tolist() == expected,
                       "output_sha256": hashlib.sha256(bytes(actual)).hexdigest()})
        return actual

    with patch.object(implementation, "toeplitz", checked_hash):
        result = SatelliteAdapter().process(raw, {"parameters": config.parameters, "endpoints": config.endpoints})
    ref = observation_reference(raw, config.parameters)
    comparisons = {}
    for key in ("coincidences", "sifted_bits", "key_basis_bits", "parameter_sample_bits", "qber", "qber_z", "qber_x"):
        comparisons["graph_" + key] = result[key] == ref[key]
    comparisons["graph_residual_rms"] = result["coincidence_residual_rms_ps"] == ref["residual_rms_ps"]
    comparisons["no_certified_keys"] = result["certified_secret_bits"] == 0
    comparisons["all_hashes_exact_gf2"] = all(row["exact_gf2_match"] for row in hashes)

    # Observe the public reconciliation transcript independently of its control
    # flow. Each disclosed Alice parity and Bob response costs one bit; public
    # index/permutation choices do not themselves reveal a bit value.
    z_pairs = ref["z_pairs"]
    transcript = []
    if z_pairs:
        alice = np.asarray([a for a, _ in z_pairs], dtype=np.uint8)
        bob = np.asarray([b for _, b in z_pairs], dtype=np.uint8)
        seed = int(raw["public_randomness"]["seed_hex"], 16)
        corrected, leakage, count = implementation.reconcile(
            alice, bob, np.random.default_rng(seed), ref["z_errors"] / len(z_pairs),
            classical_exchange=lambda stage, item: transcript.append({"stage": stage, **item}))
        parities = [row for row in transcript if "indices" in row]
        comparisons["public_leakage_count"] = leakage == 2 * len(parities) == result["error_correction_leak_bits"]
        comparisons["public_alice_parities"] = all(row["alice_parity"] ==
            sum(int(alice[i]) for i in row["indices"]) % 2 for row in parities)
        comparisons["public_permutations"] = all(sorted(row["public_permutation"]) == list(range(len(alice)))
            for row in transcript if "public_permutation" in row)
        comparisons["corrected_count"] = count == result["reconciliation_corrected_bits"]
        comparisons["reconciliation_verification_agrees"] = bool(np.array_equal(alice, corrected)) == result["reconciliation_verified"]
    bound = finite_reference(ref["key_basis_bits"], ref["parameter_sample_bits"], ref["x_errors"],
                             result["error_correction_leak_bits"], result["verification_tag_bits"], config.parameters)
    for key in ("phase_error_upper", "sampling_deviation"):
        actual, expected = result["finite_key"][key], bound[key]
        comparisons["finite_" + key] = actual == expected if actual is None or expected is None else math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-14)
    comparisons["finite_budget_bits"] = bound["budget_bits"] == result["finite_key"]["budget_bits"]
    comparisons["pa_matches_verified_budget"] = result["privacy_amplification_executed_bits"] == (bound["budget_bits"] if result["reconciliation_verified"] else 0)
    # Store only observed statistics, seeds and exact-hash digests; the public
    # simulated fixtures themselves remain archived for independent replay.
    reference = {k: v for k, v in ref.items() if k not in ("z_pairs", "x_pairs", "accepted_indices")}
    reference["finite_key"] = bound
    return result, reference, comparisons, hashes, transcript


def ingress_compare(output, raw, command, config):
    """The same synthetic capture enters three installed acquisition adapters."""
    from quantum_autopilot.quantum_protocols.contracts import Acquisition
    from quantum_autopilot.quantum_protocols.satellite_contracts import packets
    from quantum_autopilot.quantum_protocols.satellite_ingress import OGSStore, SatelliteStreamSource
    from quantum_autopilot.quantum_protocols.sources import MeasurementFileSource, ReadOnlyQueueSource

    original = process_checked(raw, config)[0]
    results = {}
    evidence = {"input_kind": "synthetic fixture for origin contract comparison",
                "physical_measurement": False, "physical_hardware_validated": False}
    for origin in ("measurement_file", "physical"):
        acquisition = Acquisition(command.command_id, config.session_id, command.operation,
                                  "hybrid-contract-fixture", origin, 1, raw["end_s"], 0.0,
                                  command.quantum_channel_ids, command.classical_channel_ids, copy.deepcopy(raw))
        if origin == "measurement_file":
            path = output / "synthetic-measurement-file.jsonl"
            path.write_text(acquisition.canonical_json() + "\n", encoding="utf-8")
            source = MeasurementFileSource(path, source_id=acquisition.source_id)
        else:
            source = ReadOnlyQueueSource(acquisition.source_id)
            source.submit(acquisition)
        got = source.acquire(command, config, {})
        results[origin] = {"same_metrics": process_checked(got.raw, config)[0] == original,
                           "same_raw": got.raw == raw, "consume_once": source.acquire(command, config, {}) is None,
                           "adapter_physical_validated": source.physical_validated}
    for origin in ("simulated", "physical"):
        store = OGSStore(output / ("ogs-" + origin + ".sqlite"), source_id="hybrid-ogs-fixture", origin=origin)
        acks = [store.submit(packet, raw["end_s"]) for packet in
                packets(raw, command, source_id=store.source_id, origin=origin, sequence=1)]
        source = SatelliteStreamSource(store, lambda: raw["end_s"])
        got = source.acquire(command, config, {})
        restored = process_checked(got.raw, config)[0]
        # Packet assembly retains only the typed satellite observation fields;
        # optional acquisition bookkeeping such as quantum_flight_s is ignored.
        results["ogs_" + origin] = {"same_metrics": restored == original,
                                    "consume_once": source.acquire(command, config, {}) is None,
                                    "three_streams_accepted": all(a["status"] == "accepted" for a in acks),
                                    "adapter_physical_validated": source.physical_validated}
    checks = {k + "_equivalent": value["same_metrics"] and value["consume_once"] and
              value.get("same_raw", True) and value.get("three_streams_accepted", True) and
              value["adapter_physical_validated"] is False for k, value in results.items()}
    evidence["adapters"] = results
    write_json(output / "ingress-comparison.json", evidence)
    return checks, evidence


def run_campaign(output, seeds=(1001, 1002, 1003)):
    from quantum_autopilot.quantum_protocols.contracts import ProtocolError
    from quantum_autopilot.quantum_protocols.protocols import SatelliteAdapter

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    seeds = tuple(seeds)
    report = {"schema": "orion.hybrid-bbm92-validation.v1", "passed": False, "seeds": list(seeds),
              "sources": [REFERENCE], "cases": [], "checks": {}, "physical_actuation": False,
              "physical_hardware_validated": False, "security_certified": False,
              "scope": "BBM92 computational components and installed observation adapters; no real photon capture",
              "acceptance_criteria": {"coincidences_sifting_qber": "exact graph/count agreement",
                                      "toeplitz": "every actual hash equals exact GF(2) product",
                                      "sampling_deviation": "independent log-tail inversion; rel_tol=1e-12, abs_tol=1e-14",
                                      "finite_bits": "exact integer budget agreement",
                                      "excess_loss_and_jitter": "coincidences < 10% of paired nominal seed",
                                      "generated_finite_exercise": "positive conditional bits, certified bits always zero",
                                      "source_origin": "identical processed metrics and exactly-once observation consumption"},
              "assumptions": ["trusted basis-independent detection and source model", "uniform independent bases for the theoretical bound",
                              "authenticated classical exchanges assumed, authentication cost not included",
                              "tail epsilon is a failure probability; smoothing is its square root",
                              "6 m aperture exercise is hypothetical, not a mission performance claim",
                              "scheduled-basis synthetic fixtures are arithmetic checks, not secure key sources"]}
    try:
        if not seeds or any(type(seed) is not int or seed < 0 for seed in seeds) or len(set(seeds)) != len(seeds):
            raise ValueError("at least one distinct nonnegative integer seed is required")
        config, topology = fixture_config()
        SatelliteAdapter().validate(config, topology)
        write_json(output / "configuration.json", config.to_dict())
        report["checks"]["actual_adapter_accepts_registered_session"] = True
        for seed in seeds:
            generated = {}
            specs = [("nominal", {}, 1.0), ("extra-loss", {"extra_loss_db": 100.0}, 1.0),
                     ("jitter", {"timing_jitter_ps": 1e6}, 1.0),
                     ("finite-hypothetical-aperture", {"pair_source_hz": 100000.0, "receiver_diameter_m": 6.0, "optical_qber": 0.001}, 6.0)]
            for name, changes, duration in specs:
                raw, command, cfg = generated_fixture(config, seed, f"{seed}-{name}", changes, duration)
                generated[name] = (raw, command, cfg)
            base, command, cfg = generated["nominal"]
            gate = round(cfg.parameters.get("coincidence_window_s", 1e-9) * 1e12 / 2)
            fixtures = {
                "gate-boundary": replace_tags(base, [10000, 30000], [10000 + gate, 30000 + gate + 1]),
                "ambiguous-cluster": replace_tags(base, [10000, 10001, 10002, 50000], [10001, 50000]),
                "half-open-window": replace_tags(base, [0, 499999999999, 500000000000, 999999999999, 1000000000000],
                                                   [0, 499999999999, 500000000000, 999999999999, 1000000000000],
                                                   [0, 2, 0, 2, 0], [0, 3, 0, 2, 0]),
                "no-tracking-lock": replace_tags(base, [10000], [10000]),
                "empty-capture": replace_tags(base, [], []),
                "epoch-rollover": copy.deepcopy(base),
            }
            for track in fixtures["half-open-window"]["tracking"]:
                track["open_intervals_s"] = [[0.0, 0.5]]
            fixtures["no-tracking-lock"]["tracking"][1]["locked"] = False
            fixtures["epoch-rollover"]["tdc"][1]["epoch_utc_s"] += 1
            fixtures["epoch-rollover"]["tdc"][1]["time_ps"] = [v - 10**12 for v in fixtures["epoch-rollover"]["tdc"][1]["time_ps"]]
            generated.update({name: (raw, command, cfg) for name, raw in fixtures.items()})
            actual_results = {}
            for name, (raw, cmd, item_cfg) in generated.items():
                case_dir = output / f"seed-{seed}" / name
                case_dir.mkdir(parents=True, exist_ok=False)
                write_json(case_dir / "input.json", {"observation": raw, "command": cmd.to_dict(), "configuration": item_cfg.to_dict()})
                try:
                    result, reference, checks, hashes, transcript = process_checked(raw, item_cfg)
                    actual_results[name] = result
                    write_json(case_dir / "actual.json", result)
                    write_json(case_dir / "reference.json", reference)
                    write_json(case_dir / "hashes.json", hashes)
                    write_json(case_dir / "public-reconciliation.json", transcript)
                    write_json(case_dir / "checks.json", checks)
                    report["cases"].append({"seed": seed, "name": name, "passed": all(checks.values()),
                                            "checks": checks, "coincidences": result["coincidences"],
                                            "conditional_bits": result["conditional_key_estimate_bits"]})
                except Exception as exc:
                    error = {"seed": seed, "name": name, "passed": False, "error": f"{type(exc).__name__}: {exc}"}
                    write_json(case_dir / "failure.json", error)
                    report["cases"].append(error)
            for name, mutate, expected in (
                    ("lost-tags", lambda r: r["tdc"][0].update(lost_tags=1), "tdc_overflow"),
                    ("uncertain-clock", lambda r: r["tdc"][0].update(clock_uncertainty_ps=1e12), "unsynchronized"),
                    ("epoch-overflow", lambda r: r["tdc"][1].update(epoch_utc_s=10**11), "invalid_tdc"),
                    ("expired-housekeeping", lambda r: r["housekeeping"].update(valid_until_s=0.0), "stale_ephemeris")):
                raw = copy.deepcopy(base)
                mutate(raw)
                case_dir = output / f"seed-{seed}" / name
                case_dir.mkdir(parents=True, exist_ok=False)
                write_json(case_dir / "input.json", raw)
                actual_code = "accepted"
                try:
                    SatelliteAdapter().process(raw, {"parameters": cfg.parameters, "endpoints": cfg.endpoints})
                except ProtocolError as exc:
                    actual_code = exc.code
                row = {"seed": seed, "name": name, "passed": actual_code == expected,
                       "expected_error": expected, "actual_error": actual_code,
                       "checks": {"rejected_before_metrics": actual_code == expected}}
                write_json(case_dir / "checks.json", row)
                report["cases"].append(row)
            checks = report["checks"]
            checks[f"seed_{seed}_loss_reduces_coincidences"] = actual_results["extra-loss"]["coincidences"] < actual_results["nominal"]["coincidences"] / 10
            checks[f"seed_{seed}_jitter_reduces_coincidences"] = actual_results["jitter"]["coincidences"] < actual_results["nominal"]["coincidences"] / 10
            checks[f"seed_{seed}_finite_generated_postprocessing"] = actual_results["finite-hypothetical-aperture"]["conditional_key_estimate_bits"] > 0
            checks[f"seed_{seed}_gate_boundary_one_pair"] = actual_results["gate-boundary"]["coincidences"] == 1
            checks[f"seed_{seed}_ambiguous_only_isolated_pair"] = actual_results["ambiguous-cluster"]["coincidences"] == 1
            checks[f"seed_{seed}_window_half_open"] = actual_results["half-open-window"]["coincidences"] == 2
            checks[f"seed_{seed}_epoch_invariance"] = actual_results["epoch-rollover"] == actual_results["nominal"]
            checks[f"seed_{seed}_lock_aborts"] = actual_results["no-tracking-lock"]["coincidences"] == 0
            ingress_dir = output / f"seed-{seed}" / "adapter-origin-equivalence"
            ingress_dir.mkdir(exist_ok=False)
            origin_checks, _ = ingress_compare(ingress_dir, base, command, cfg)
            checks.update({f"seed_{seed}_" + k: v for k, v in origin_checks.items()})
        report["passed"] = all(report["checks"].values()) and all(row["passed"] for row in report["cases"])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["case_count"] = len(report["cases"])
    report["check_count"] = len(report["checks"]) + sum(len(row.get("checks", {})) for row in report["cases"])
    write_json(output / "cases.json", report["cases"])
    write_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1001, 1002, 1003])
    args = parser.parse_args()
    report = run_campaign(args.output, args.seeds)
    print(json.dumps({k: report.get(k) for k in ("passed", "case_count", "check_count", "error")}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
