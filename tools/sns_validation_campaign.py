"""Qualification of independent SNS reference and current reduced TF adapter.

The reference is deliberately isolated from Orion imports. Only this comparison
driver imports the adapter under test. Outputs distinguish verified numerical
components from an SNS finite protocol that the current backend cannot execute.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import sns_validation_reference as reference  # noqa: E402


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def reduced_observation(observation):
    strata = observation["strata"]
    return {"z_trials": observation["signal"]["trials"], "z_clicks": observation["signal"]["clicks"],
            "z_errors": observation["signal"]["errors"], "x_trials": observation["phase_slice"]["trials"],
            "x_clicks": observation["phase_slice"]["clicks"],
            "x_errors": observation["phase_slice"]["wrong_detector_clicks"],
            "vacuum": strata["00"],
            "weak": {"trials": strata["01"]["trials"] + strata["10"]["trials"],
                     "clicks": strata["01"]["clicks"] + strata["10"]["clicks"]},
            "strong": {"trials": strata["02"]["trials"] + strata["20"]["trials"],
                       "clicks": strata["02"]["clicks"] + strata["20"]["clicks"]}}


def source_contract_replay(raw, parameters, ident, output_dir):
    """Counts enter existing acquisition/queue/file/clock/channel contracts.

    This is component-level admission, not the complete ProtocolRuntime scheduler
    or authenticated classical SNS postprocessing. Queue origin is explicitly
    simulated; file replay records retain synthetic-fixture provenance.
    """
    from quantum_autopilot.quantum_protocols.contracts import Acquisition, DeviceCommand, ProtocolConfig, validate_binding
    from quantum_autopilot.quantum_protocols.protocols import TwinFieldAdapter
    from quantum_autopilot.quantum_protocols.sources import MeasurementFileSource, ReadOnlyQueueSource

    session_id = "SNS-VALIDATION-" + ident.replace(":", "-")
    command = DeviceCommand(session_id + "-block1", session_id, "tf_block", ("FIXTURE-A", "FIXTURE-C", "FIXTURE-B"),
                            ("Q-A-C", "Q-B-C"), ("C-A-C", "C-B-C"), 0.0, 2.0, 1, {"duration_s": 1.0})
    config = ProtocolConfig(session_id, ("tf_qkd",), ("FIXTURE-A", "FIXTURE-B"),
                            ("FIXTURE-A", "FIXTURE-C", "FIXTURE-B"), ("L-A-C", "L-C-B"), parameters)
    queue = ReadOnlyQueueSource("sns_independent_synthetic_fixture")
    queue.origin = "simulated"
    raw = {**raw, "qualification_provenance": "independent synthetic SNS count fixture; no detector connected"}
    acquisition = Acquisition(command.command_id, session_id, "tf_block", queue.source_id, "simulated", 1,
                              1.0, 0.0, command.quantum_channel_ids, command.classical_channel_ids, raw)
    queue.submit(acquisition)
    queue_record = queue.acquire(command, config, {})
    validate_binding(queue_record, command, last_sequence=0, source_id=queue.source_id, origin=queue.origin,
                     now_s=1.0, sync_tolerance_s=1e-6)
    queued = TwinFieldAdapter().process(queue_record.raw, {"parameters": parameters})
    file_record = replace(acquisition, origin="measurement_file", source_id="sns_fixture_file")
    path = output_dir / f"acquisition-{ident.replace(':', '-')}.jsonl"
    path.write_text(file_record.canonical_json() + "\n", encoding="utf-8")
    source = MeasurementFileSource(path, source_id="sns_fixture_file")
    admitted = source.acquire(command, config, {})
    validate_binding(admitted, command, last_sequence=0, source_id=source.source_id, origin=source.origin,
                     now_s=1.0, sync_tolerance_s=1e-6)
    replayed = TwinFieldAdapter().process(admitted.raw, {"parameters": parameters})
    return {"scope": "acquisition binding, associated channels, clock validation, queue/file one-shot admission and reduced adapter",
            "simulated_queue_matches_synthetic_file": queued == replayed,
            "acquired_once": queue.acquire(command, config, {}) is None and source.acquire(command, config, {}) is None,
            "physical_hardware_validated": False, "acquisition_sha256": _sha(path), "metrics": queued}


def run_campaign(output_dir: Path, seeds=(101, 202, 303)) -> dict:
    seeds = tuple(seeds)
    if not seeds or len(set(seeds)) != len(seeds) or any(type(seed) is not int or seed < 0 for seed in seeds):
        raise ValueError("nonempty distinct nonnegative integer seeds required")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    config_path = ROOT / "configs/qualification/sns_validation_v1.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    frozen = {str(path.relative_to(ROOT)): _sha(path) for path in
              (config_path, Path(__file__), ROOT / "tools/sns_validation_reference.py",
               ROOT / "src/quantum_autopilot/quantum_protocols/protocols.py")}
    _write(output_dir / "freeze.json", {"sources": frozen, "seeds": list(seeds), "criteria": config["criteria"]})
    rows, checks = [], []

    def check(case, name, passed, detail=None):
        checks.append({"case": case, "check": name, "passed": bool(passed), "detail": detail})

    for scenario in config["cases"]:
        for seed in seeds:
            ident = f"{scenario['id']}:{seed}"
            parameters = {**config["parameters"], **scenario["overrides"]}
            observation = reference.synthetic_observation(parameters, seed)
            _write(output_dir / f"observation-{scenario['id']}-{seed}.json", observation)
            raw = reduced_observation(observation)
            adapter_parameters = {"signal_mu": parameters["strong_mu"], "decoy_mu": parameters["weak_mu"],
                                  "send_probability": parameters["send_probability"],
                                  "ec_efficiency": parameters["ec_efficiency"]}
            admitted = source_contract_replay(raw, adapter_parameters, ident, output_dir)
            measured = admitted["metrics"]
            check(ident, "simulated_queue_and_synthetic_file_share_admission_contract", admitted["simulated_queue_matches_synthetic_file"])
            check(ident, "both_sources_consume_each_observation_once", admitted["acquired_once"])
            estimate = reference.asymptotic_decoy_yield(raw["vacuum"]["clicks"] / raw["vacuum"]["trials"],
                raw["weak"]["clicks"] / raw["weak"]["trials"], raw["strong"]["clicks"] / raw["strong"]["trials"],
                parameters["weak_mu"], parameters["strong_mu"])
            check(ident, "adapter_asymptotic_decoy_matches_independent_expression",
                  abs(measured["single_photon_yield_lower_estimate"] - estimate) <=
                  config["criteria"]["asymptotic_adapter_absolute_tolerance"])
            check(ident, "adapter_does_not_certify_key", measured["certified_secret_bits"] == 0)
            result = {"id": ident, "scenario": scenario["id"], "seed": seed, "parameters": parameters,
                      "observation_sha256": _sha(output_dir / f"observation-{scenario['id']}-{seed}.json"),
                      "adapter": measured, "component_acquisition": admitted, "reference_finite": None}
            try:
                result["reference_finite"] = reference.finite_sns_estimate(observation, parameters)
            except ValueError as exc:
                result["reference_rejection"] = str(exc)
                result["reference_rejection_type"] = type(exc).__name__
            expected_rejection = scenario["expectation"] == "symmetric_proof_rejected"
            actual_rejection = result["reference_finite"] is None
            check(ident, "reference_acceptance_or_rejection_matches_declared_case",
                  actual_rejection == expected_rejection and (not actual_rejection or
                  result.get("reference_rejection_type") == "SNSAssumptionError"))
            if result["reference_finite"]:
                finite = result["reference_finite"]
                check(ident, "finite_bound_not_above_asymptotic",
                      finite["single_photon_yield_lower"] <= finite["asymptotic_yield_estimate"] + 1e-12)
                check(ident, "reference_never_certifies_key", finite["certified_secret_bits"] == 0)
                if scenario["expectation"] == "positive_reference_budget":
                    check(ident, "nominal_has_conditional_reference_budget", finite["conditional_budget_bits"] > 0)
                elif scenario["expectation"] == "zero_reference_budget":
                    check(ident, "limitations_abort_reference_budget", finite["conditional_budget_bits"] == 0)
            # Independent LP is checked against exact Poisson-mixture truth, not empirical
            # gains treated as exact expectations. This is a distinct reference experiment.
            truth = observation["derived_physical_truth"]
            gains = [truth["gains"]["00"], (truth["gains"]["01"] + truth["gains"]["10"]) / 2,
                     (truth["gains"]["02"] + truth["gains"]["20"]) / 2]
            lp = reference.poisson_yield_lp([0, parameters["weak_mu"], parameters["strong_mu"]],
                                           [(max(0, q - 1e-12), min(1, q + 1e-12)) for q in gains])
            result["independent_poisson_lp"] = lp
            check(ident, "physical_poisson_reference_feasible", lp["feasible"])
            if lp["feasible"]:
                analytic = reference.asymptotic_decoy_yield(*gains, parameters["weak_mu"], parameters["strong_mu"])
                tolerance = config["criteria"]["lp_absolute_tolerance"]
                check(ident, "analytic_decoy_below_poisson_lp", analytic <= lp["yield_lower"] + tolerance)
                check(ident, "poisson_lp_below_known_single_photon_truth",
                      lp["yield_lower"] <= truth["single_photon_yield"] + tolerance)
            rows.append(result)
    for seed in seeds:
        nominal = next(row for row in rows if row["scenario"] == "nominal" and row["seed"] == seed)
        longer = next(row for row in rows if row["scenario"] == "longer_fiber" and row["seed"] == seed)
        check(f"loss:{seed}", "additional_fiber_loss_reduces_reference_budget",
              longer["reference_finite"]["conditional_budget_bits"] < nominal["reference_finite"]["conditional_budget_bits"])
    unchanged = all(_sha(ROOT / name) == digest for name, digest in frozen.items())
    check("freeze", "sources_unchanged_during_campaign", unchanged)
    report = {"schema": "orionq.sns-validation.v1", "status": "partial_verified" if all(c["passed"] for c in checks) else "failed",
              "covered_scope_passed": all(c["passed"] for c in checks), "checks": checks, "cases": rows,
              "case_count": len(rows), "check_count": len(checks), "sources": reference.SOURCES,
              "physical_hardware_validated": False, "security_certified": False,
              "finite_sns_backend_validated": False, "certified_secret_bits": 0,
              "limitations": [
                  "The current aggregate TF contract lacks S01/S10/S02/S20 source strata and phase-slice wrong-detector trial counts.",
                  "Current X QBER is a surrogate, not the finite SNS Z phase-error bound Eq. 16/26.",
                  "Finite reference consumes synthetic count fixtures, not authenticated end-to-end distributed SNS observations.",
                  "Acquisition parity verifies existing queue/file admission contracts only; full finite SNS classical exchange is not implemented.",
                  "Reference budget uses leakage accounting; it does not implement SNS reconciliation/verification/privacy amplification.",
                  "No optimized-paper key-rate curve, asymmetric-source proof or physical source/detector characterization is claimed.",
                  "Independent interval union allocation is reported separately and requires composable-security review."]}
    _write(output_dir / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_campaign(args.output)
    print(json.dumps({"status": report["status"], "cases": report["case_count"], "checks": report["check_count"]}))
    return 0 if report["covered_scope_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
