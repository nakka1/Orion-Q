"""Freeze and assess the bounded first SOFT-DT campaign; never assign a TRL.

Evidence is local integrity data, not authenticated authorship or certification.
The full seven-gate protocol needs broader campaigns and an independent reviewer.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = Path("configs/qualification/trl5_software_v1.json")
REPORTS = {
    "reference-1": ("reference-1/report.json", "orion.trl5.reference-campaign.v1"),
    "reference-2": ("reference-2/report.json", "orion.trl5.reference-campaign.v1"),
    "pipeline-1": ("pipeline-1/report.json", "orion.trl5.pipeline-campaign.v1"),
    "pipeline-2": ("pipeline-2/report.json", "orion.trl5.pipeline-campaign.v1"),
    "native-ui": ("performance-baseline/report.json", "orion.console-performance.v1"),
    "runtime": ("runtime-baseline/diagnostic.json", "orion.runtime-diagnostic.v1"),
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def finite_tree(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite evidence")
    if isinstance(value, dict):
        for item in value.values():
            finite_tree(item)
    elif isinstance(value, list):
        for item in value:
            finite_tree(item)


def load_json(path):
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("evidence exceeds the size limit")
    result = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(result, dict):
        raise ValueError("evidence must be an object")
    finite_tree(result)
    return result


def contained(base, name):
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("relative evidence path required")
    target = (base / relative).resolve()
    if not target.is_relative_to(base.resolve()):
        raise ValueError("evidence escapes its directory")
    return target


def write_new(path, value):
    # Exclusive creation preserves existing campaign evidence.
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def source_inventory(root):
    paths = [path for path in (root / "src").rglob("*") if path.is_file()
             and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}]
    paths += [root / name for name in (
        "pyproject.toml", "tools/benchmark_console_performance.py",
        "tools/run_trl5_reference_campaign.py", "tools/run_trl5_pipeline_campaign.py",
        "tools/assess_trl5_software.py", "tests/fixtures/trl5_refs/references.json",
        "data/reference/gavignet_2024_published_aggregate.csv", "data/reference/README.md",
    )]
    # Include the recovery/performance regressions added during qualification,
    # rather than relying on a manually maintained list of test filenames.
    paths += list((root / "tools").glob("*trl5*.py"))
    paths += [path for path in (root / "tests").rglob("*.py")
              if "__pycache__" not in path.parts]
    paths = sorted(set(paths))
    return {path.relative_to(root).as_posix(): sha(path) for path in paths if path.is_file()}


def nonnegative_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("non-negative finite numeric measurement required")
    return value


def validate_measurements(label, report):
    if label == "runtime":
        for field in ("samples", "budget_exceeded", "failed_attempts"):
            if type(report.get(field)) is not int or report[field] < 0:
                raise ValueError(f"invalid runtime {field}")
        nonnegative_number(report.get("budget_ms"))
        for attempt in report.get("attempts", []):
            nonnegative_number(attempt.get("total_ms"))
    elif label == "native-ui":
        for phase in report.get("phases", {}).values():
            for field in ("heartbeat_ms", "action_to_all_fields_verified_ms"):
                for statistic in ("p95", "p99", "maximum"):
                    value = phase.get(field, {}).get(statistic)
                    if value is not None:
                        nonnegative_number(value)


def analytic_acceptance(root, seed):
    """Freeze only independent expectations and tolerances, not measured outputs."""
    specification = importlib.util.spec_from_file_location("trl5_reference_freeze", root / "tools/run_trl5_reference_campaign.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    report = module.run_campaign(seed)
    fields = ("expected", "absolute_tolerance", "relative_tolerance", "reference_ids", "production_api", "inputs", "assumptions")
    return {"schema": "orionq.trl5-analytic-acceptance.v1", "seed": seed,
            "checks": {check["name"]: {field: check[field] for field in fields if field in check}
                       for check in report["checks"]}}


def prepare(directory, root=ROOT):
    root, directory = root.resolve(), directory.resolve()
    protocol_path = root / PROTOCOL
    protocol = load_json(protocol_path)
    if protocol.get("schema") != "orionq.trl5-software-protocol.v1":
        raise ValueError("unsupported protocol")
    required = [root / "tools" / f"run_trl5_{kind}_campaign.py" for kind in ("reference", "pipeline")]
    if not all(path.is_file() for path in required):
        raise ValueError("campaign runners must exist before freezing")
    inventory = source_inventory(root)
    versions = {}
    for package in ("quantum-twin", "numpy", "scipy", "PySide6", "psutil", "pytest"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "unavailable"
    git = subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", "status", "--porcelain=v1"],
                         cwd=root, capture_output=True, check=False)
    seed = protocol["seeds"]["nominal"][0]
    acceptance = analytic_acceptance(root, seed)
    if source_inventory(root) != inventory:
        raise ValueError("source changed while preparing analytic acceptance")
    manifest = {
        "schema": "orionq.trl5-freeze.v1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_id": protocol["protocol_id"], "protocol_revision": protocol["protocol_revision"],
        "protocol_sha256": sha(protocol_path), "qualification_scope": protocol["target_scope"],
        "product_version": protocol["scope"]["baseline_version"], "source_file_hashes": inventory,
        "source_revision_or_manifest_sha256": hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest(),
        "source_dirty_state": {"git_status_available": git.returncode == 0,
                               "dirty": bool(git.stdout) if git.returncode == 0 else None,
                               "status_sha256": hashlib.sha256(git.stdout).hexdigest()},
        "dependency_versions": versions,
        "environment": {"os_build": platform.platform(), "cpu": platform.processor(),
                        "logical_cpus": os.cpu_count(), "python": sys.version,
                        "gpu_and_driver": "Recorded separately by the native UI sampler; pending at freeze",
                        "working_directory": str(root)},
        "dataset_hashes": {name: value for name, value in inventory.items() if name.startswith("data/")},
        "oracle_hashes": {name: value for name, value in inventory.items() if "trl5_reference" in name or "trl5_refs" in name},
        "seed_sets": protocol["seeds"], "initial_trial_seed": seed,
        "commands": [
            f"tools/benchmark_console_performance.py --output {directory / 'performance-baseline'} --duration 15 --soak 3 --size 3440x1440",
            f"-m quantum_autopilot.control_plane.runtime_diagnostic --output {directory / 'runtime-baseline'} --samples 50 --warmup 5 --budget-ms 10 --condition idle",
            *[f"tools/run_trl5_{kind}_campaign.py --output-dir {directory / (kind + '-' + str(repeat))} --seed {seed}"
              for kind in ("reference", "pipeline") for repeat in (1, 2)],
        ],
        "evidence_limits": ["First bounded trials only; not the full gate coverage.",
                            "Earlier runtime overruns were known before this freeze.",
                            "New freeze hashes preserve integrity, not authenticated authorship.",
                            "Same-machine repetition is not independent-environment reproduction."],
    }
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "protocol.json").write_bytes(protocol_path.read_bytes())
    write_new(directory / "analytic-acceptance.json", acceptance)
    manifest["analytic_acceptance_sha256"] = sha(directory / "analytic-acceptance.json")
    write_new(directory / "freeze.json", manifest)
    return manifest


def assess(directory, root=ROOT):
    directory, root = directory.resolve(), root.resolve()
    manifest = load_json(directory / "freeze.json")
    protocol = load_json(directory / "protocol.json")
    acceptance = load_json(directory / "analytic-acceptance.json")
    failures, missing, evidence, reports = [], [], {}, {}
    report_failures = {"G02": [], "G03": [], "G05": []}
    if manifest.get("schema") != "orionq.trl5-freeze.v1":
        raise ValueError("unsupported freeze manifest")
    if sha(directory / "protocol.json") != manifest["protocol_sha256"] or sha(root / PROTOCOL) != manifest["protocol_sha256"]:
        failures.append("Frozen protocol identity changed")
    if sha(directory / "analytic-acceptance.json") != manifest["analytic_acceptance_sha256"]:
        failures.append("Frozen analytic acceptance changed")
    for name, expected in manifest["source_file_hashes"].items():
        target = contained(root, name)
        if not target.is_file() or sha(target) != expected:
            failures.append(f"Frozen source changed: {name}")
    for name in source_inventory(root).keys() - manifest["source_file_hashes"].keys():
        failures.append(f"Source added or omitted from freeze: {name}")
    for label, (name, schema) in REPORTS.items():
        path = contained(directory, name)
        if not path.is_file():
            missing.append(f"Missing {label} report")
            continue
        try:
            report = load_json(path)
            if report.get("schema") != schema:
                raise ValueError("unexpected report schema")
            validate_measurements(label, report)
            for source, expected in report.get("source_hashes", {}).items():
                source_path = contained(root, source.replace("\\", "/"))
                if manifest["source_file_hashes"].get(source.replace("\\", "/")) != expected or sha(source_path) != expected:
                    raise ValueError(f"unfrozen or changed source: {source}")
            if label.startswith(("reference", "pipeline")):
                if not report.get("source_hashes") or report.get("seed") != manifest["initial_trial_seed"]:
                    raise ValueError("source identity or declared seed missing")
                if report.get("physical_action_performed") is not False:
                    raise ValueError("physical boundary not evidenced")
                checks = report.get("checks")
                checks_pass = (all(value is True for value in checks.values()) if isinstance(checks, dict)
                               else all(check.get("passed") is True for check in checks) if isinstance(checks, list) else False)
                if not checks or report.get("passed") is not True or not checks_pass or report.get("errors"):
                    raise ValueError("campaign checks failed or absent")
                if label.startswith("reference"):
                    if {check["name"] for check in report["checks"]} != set(acceptance["checks"]):
                        raise ValueError("analytic check set differs from frozen oracle")
                    for check in report["checks"]:
                        for field, expected in acceptance["checks"][check["name"]].items():
                            if check.get(field) != expected:
                                raise ValueError(f"analytic {field} differs from frozen oracle")
                        nonnegative_number(check["absolute_tolerance"])
                        nonnegative_number(check["relative_tolerance"])
                        error = abs(check["actual"] - check["expected"])
                        if error > check["absolute_tolerance"] + check["relative_tolerance"] * abs(check["expected"]):
                            raise ValueError("numeric oracle mismatch despite passed flag")
            reports[label] = report
            evidence[label] = {"path": name, "sha256": sha(path)}
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
            gate_id = "G02" if label.startswith("reference") else "G03" if label.startswith("pipeline") else "G05"
            report_failures[gate_id].append(f"{label}: {error}")
    runtime = reports.get("runtime", {})
    performance_failures = report_failures["G05"]
    if runtime:
        if runtime.get("budget_ms") != protocol["performance_requirements"]["runtime"]["budget_ms"]:
            performance_failures.append("Runtime budget differs from the frozen 10 ms requirement")
        attempts = runtime.get("attempts", [])
        if not attempts or len(attempts) != runtime.get("samples"):
            performance_failures.append("Runtime raw sample count missing or inconsistent")
        raw_overruns = sum(item.get("total_ms", math.inf) > 10.0 for item in attempts)
        if raw_overruns != runtime.get("budget_exceeded"):
            performance_failures.append("Runtime overrun counter contradicts raw samples")
        if raw_overruns or runtime.get("budget_exceeded", 0) > 0 or runtime.get("failed_attempts", 0) > 0:
            performance_failures.append(f"Runtime exceeded budget: {runtime.get('budget_exceeded')} / {runtime.get('samples')}")
    ui = reports.get("native-ui", {})
    if ui:
        if ui.get("measurement_completed") is not True or ui.get("errors") or ui.get("source_unchanged") is not True:
            performance_failures.append("Native UI measurement incomplete, errored or source changed")
        limits = protocol["performance_requirements"]["native_ui"]
        if not ui.get("phases"):
            performance_failures.append("Native UI phase measurements absent")
        for name, phase in ui.get("phases", {}).items():
            heartbeat = phase.get("heartbeat_ms", {})
            for statistic, maximum in (("p95", limits["heartbeat_gap_p95_max_ms"]), ("p99", limits["heartbeat_gap_p99_max_ms"]), ("maximum", limits["heartbeat_gap_max_ms"])):
                measured = heartbeat.get(statistic)
                if measured is None:
                    missing.append(f"UI {name} heartbeat {statistic} absent")
                elif measured > maximum:
                    performance_failures.append(f"UI {name} heartbeat {statistic}: {heartbeat[statistic]:.3f} ms > {maximum} ms")
            detail = phase.get("action_to_all_fields_verified_ms", {})
            if detail.get("maximum") is not None and detail["maximum"] > limits["detail_complete_max_ms"]:
                performance_failures.append(f"UI {name} complete detail exceeded 1000 ms")
    repetitions = {}
    for kind in ("reference", "pipeline"):
        first, second = reports.get(kind + "-1"), reports.get(kind + "-2")
        if first and second:
            fingerprint = first.get("scientific_fingerprint_sha256")
            repetitions[kind] = bool(fingerprint and fingerprint == second.get("scientific_fingerprint_sha256"))
            if not repetitions[kind]:
                report_failures["G02" if kind == "reference" else "G03"].append(f"{kind} same-seed scientific outputs differ")
    gaps = {
        "G01": ["Complete relevant-environment and critical-function mapping still requires admission review"],
        "G02": ["All-family reference admission, TF-QKD security estimator and physical-loss calibration remain incomplete"],
        "G03": ["Bounded three-session runtime witnesses do not qualify the full topology/API/UI and every source"],
        "G04": ["Complete process/crash/key lifecycle, saturation and authorization fault matrix is pending"],
        "G05": protocol["initial_baseline_protocol"]["does_not_demonstrate"],
        "G06": ["Same-host repeats do not establish reconstruction in a second isolated environment"],
        "G07": ["Named independent expert and authenticated review absent"],
    }
    gates = []
    for gate in protocol["gates"]:
        gate_failures = failures if gate["id"] == "G01" else report_failures.get(gate["id"], [])
        gates.append({"id": gate["id"], "name": gate["name"], "status": "fail" if gate_failures else "not_demonstrated",
                      "evidence_paths": [item["path"] for item in evidence.values()],
                      "evidence_sha256": {label: item["sha256"] for label, item in evidence.items()},
                      "criteria_checked": ["Bounded initial trial only"], "missing_criteria": gaps[gate["id"]],
                      "failures": gate_failures, "dataset_limits": manifest["evidence_limits"]})
    all_failures = failures + [failure for values in report_failures.values() for failure in values]
    return {"schema": "orionq.trl5-software-report.v1", "protocol_id": manifest["protocol_id"],
            "protocol_sha256": manifest["protocol_sha256"], "scope": protocol["scope"],
            "source_identity": manifest["source_revision_or_manifest_sha256"], "environment": manifest["environment"],
            "datasets_and_limits": manifest["evidence_limits"], "reference_admission": "partial_analytic_only",
            "gate_results": gates, "local_trial_passed": not all_failures and not missing,
            "missing_evidence": missing + [gap for values in gaps.values() for gap in values],
            "observed_failures": all_failures, "same_seed_repetitions": repetitions,
            "evidence": evidence, "limitations": manifest["evidence_limits"],
            "assessment_status": "fail" if all_failures else "not_demonstrated",
            "trl5_demonstrated": False, "trl5_claim_allowed": False, "physical_action_performed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "assess"))
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        result = prepare(args.directory)
        print(json.dumps({"protocol_sha256": result["protocol_sha256"], "directory": str(args.directory)}))
        return 0
    result = assess(args.directory)
    write_new(args.directory / "assessment.json", result)
    print(json.dumps({"assessment_status": result["assessment_status"], "trl5_demonstrated": False,
                      "observed_failures": result["observed_failures"]}))
    return 1 if result["observed_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
