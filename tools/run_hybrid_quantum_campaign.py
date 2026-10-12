"""Frozen independent component qualification; never hardware or TRL admission."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/qualification/hybrid_quantum_v1.json"
WORKERS = {
    "sns": "sns_validation_campaign",
    "space": "space_validation_campaign",
    "protocol": "hybrid_protocol_campaign",
    "ingress": "hybrid_ingress_campaign",
}
ORACLES = (
    "tools/sns_validation_reference.py",
    "tools/space_validation_reference.py",
    "tools/hybrid_protocol_reference.py",
)
REQUIRED_FORBIDDEN = {"physical_hardware_validated", "security_certified", "physical_actuation",
                      "coherent_orbital_relay_validated", "trl_assigned",
                      "finite_sns_backend_validated", "whole_hybrid_qualified"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def load(path):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def within(path):
    result = Path(path).resolve()
    if not result.is_relative_to(ROOT.resolve()):
        raise ValueError("campaign paths must remain within the repository")
    return result


def validate_plan(plan):
    if plan.get("schema") != "orionq.hybrid-qualification.v1":
        raise ValueError("unsupported qualification plan")
    if plan.get("families") != list(WORKERS):
        raise ValueError("all four declared families required")
    forbidden = plan.get("forbidden_claims")
    if not isinstance(forbidden, list) or not all(isinstance(key, str) for key in forbidden) or \
            not REQUIRED_FORBIDDEN.issubset(forbidden):
        raise ValueError("qualification barriers cannot be removed from the plan")
    seeds = plan.get("seeds")
    if not isinstance(seeds, list) or len(seeds) < 3 or any(type(s) is not int or s < 0 for s in seeds):
        raise ValueError("at least three integer seeds required")
    if len(set(seeds)) != len(seeds):
        raise ValueError("distinct seeds required")
    timeout = plan.get("worker_timeout_s")
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError("bounded worker timeout required")
    for key in ("source_paths", "previous_freezes"):
        values = plan.get(key)
        if not isinstance(values, list) or not values or len(set(values)) != len(values):
            raise ValueError(f"unique nonempty {key} required")
        for item in values:
            if not isinstance(item, str) or Path(item).is_absolute():
                raise ValueError("repository-relative manifest paths required")
            within(ROOT / item)
    for item in plan.get("reference_asset_directories", []):
        if not isinstance(item, str) or Path(item).is_absolute():
            raise ValueError("repository-relative reference assets required")
        within(ROOT / item)


def changes(sources):
    return [relative for relative, expected in sources.items()
            if not within(ROOT / relative).is_file() or sha(ROOT / relative) != expected]


def previous_sources(frozen):
    fields = {"orionq.cross-simulator-freeze.v1": "source_hashes",
              "orionq.sequence-network-freeze.v1": "sources"}
    if frozen.get("schema") not in fields:
        raise ValueError("unsupported previous freeze schema")
    manifest = frozen.get(fields[frozen["schema"]])
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("empty previous source manifest")
    return manifest


def oracle_imports():
    """Check separation without executing external source text."""
    report = {}
    for relative in ORACLES:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8-sig"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(item.name for item in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        forbidden = [name for name in imports if name.startswith(("quantum_autopilot", "qnet_"))]
        report[relative] = {"imports": sorted(set(imports)), "forbidden": forbidden,
                            "passed": not forbidden}
    return report


def versions():
    packages = {}
    for name in ("numpy", "scipy", "pytest", "PySide6", "sgp4", "websockets"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    return {"python": sys.version, "executable": sys.executable, "platform": platform.platform(),
            "processor": platform.processor(), "packages": packages}


def evaluate_family(report, forbidden):
    checks = report.get("checks")
    if isinstance(checks, dict):
        passed = bool(checks) and all(value is True for value in checks.values())
        count = len(checks)
    elif isinstance(checks, list):
        passed = bool(checks) and all(row.get("passed") is True for row in checks)
        count = len(checks)
    else:
        passed, count = False, 0
    for case in report.get("cases", []):
        if "passed" in case and case["passed"] is not True:
            passed = False
        case_checks = case.get("checks", {})
        if isinstance(case_checks, dict):
            count += len(case_checks)
            passed = passed and all(value is True for value in case_checks.values())
    for key in ("passed", "covered_scope_passed"):
        if key in report and report[key] is not True:
            passed = False
    if report.get("status") in {"failed", "error", "running"}:
        passed = False
    unsafe = []

    def claims(value, path="report"):
        if isinstance(value, dict):
            for key, item in value.items():
                current = path + "." + key
                if key in forbidden and item is not False or key == "certified_secret_bits" and item != 0:
                    unsafe.append(current)
                claims(item, current)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                claims(item, f"{path}[{index}]")

    claims(report)
    return {"passed": passed and not unsafe and not report.get("error"),
            "check_count": count, "forbidden_claims": unsafe}


def worker(family, output, seeds):
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tools"))
    module = importlib.import_module(WORKERS[family])
    result = module.run_campaign(within(output), seeds=tuple(seeds))
    path = Path(output) / "report.json"
    if not path.exists():
        write(path, result)
    if load(path) != json.loads(json.dumps(result, allow_nan=False)):
        raise ValueError("worker return value disagrees with durable report")


def run_campaign(output, plan_path=PLAN):
    plan_path = within(plan_path)
    plan = load(plan_path)
    validate_plan(plan)
    output = within(output)
    if output.exists():
        raise FileExistsError("new output directory required; evidence is never overwritten")
    # Verify every prerequisite before beginning a scientific run.
    sources = {}
    previous = {}
    for relative in plan["previous_freezes"]:
        frozen = load(ROOT / relative)
        manifest = previous_sources(frozen)
        drift = changes(manifest)
        if drift:
            raise ValueError(f"previous frozen sources changed: {drift}")
        previous[relative] = {"sha256": sha(ROOT / relative), "source_count": len(manifest),
                              "changed_sources": drift}
        for item, expected in manifest.items():
            if item in sources and sources[item] != expected:
                raise ValueError("previous manifests disagree")
            sources[item] = expected
    for relative in plan["source_paths"]:
        path = within(ROOT / relative)
        sources[relative] = sha(path)
    # Include pinned author reference assets, if present, without loading them.
    for relative in plan.get("reference_asset_directories", []):
        reference_root = within(ROOT / relative)
        if not reference_root.is_dir():
            raise FileNotFoundError("pinned author reference asset directory required")
        for path in sorted(reference_root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                sources[path.relative_to(ROOT).as_posix()] = sha(path)
    independence = oracle_imports()
    if not all(row["passed"] for row in independence.values()):
        raise ValueError("reference module imports production")
    output.mkdir(parents=True)
    reference_copies = {}
    for relative in sources:
        if relative.startswith("outputs/"):
            destination = output / "references" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
            reference_copies[destination.relative_to(output).as_posix()] = sources[relative]
    write(output / "criteria.json", plan)
    freeze = {"schema": "orionq.hybrid-freeze.v1", "criteria_before_execution": True,
              "created_utc": datetime.now(timezone.utc).isoformat(), "sources": sources,
              "criteria_sha256": sha(output / "criteria.json"), "previous_freezes": previous,
              "reference_import_separation": independence, "reference_copies": reference_copies,
              "environment": versions()}
    write(output / "freeze.json", freeze)
    frozen_manifest_sha = sha(output / "freeze.json")
    started = time.perf_counter()
    families = {}
    for family in plan["families"]:
        directory = output / family
        args = [sys.executable, str(Path(__file__).resolve()), "--worker", family,
                "--output", str(directory), "--seeds", *map(str, plan["seeds"])]
        begin = time.perf_counter()
        with (output / f"{family}.stdout.txt").open("x", encoding="utf-8") as stdout, \
                (output / f"{family}.stderr.txt").open("x", encoding="utf-8") as stderr:
            try:
                process = subprocess.run(args, cwd=ROOT, stdout=stdout, stderr=stderr,
                                         timeout=plan["worker_timeout_s"], check=False)
                code, error = process.returncode, None
            except subprocess.TimeoutExpired:
                code, error = None, "worker_timeout"
        path = directory / "report.json"
        entry = {"returncode": code, "error": error, "elapsed_s": time.perf_counter() - begin,
                 "report_path": f"{family}/report.json", "report_sha256": sha(path) if path.is_file() else None}
        try:
            family_report = load(path)
            entry.update(evaluate_family(family_report, plan["forbidden_claims"]))
            entry.update(reported_status=family_report.get("status", "passed" if family_report.get("passed") is True else None),
                         case_count=len(family_report.get("cases", family_report.get("runs", []))),
                         limitations=family_report.get("limitations", []),
                         scientific_gaps=family_report.get("fidelity_gaps", []))
        except (OSError, ValueError, TypeError) as exc:
            entry.update(passed=False, check_count=0, error=f"report_unavailable: {type(exc).__name__}")
        entry["passed"] = entry["passed"] and code == 0 and not error
        families[family] = entry
    drift = changes(sources)
    previous_drift = [relative for relative, frozen in previous.items()
                      if sha(ROOT / relative) != frozen["sha256"]]
    checks = {"frozen_sources_unchanged": not drift,
              "previous_freezes_unchanged": not previous_drift,
              "prospective_manifest_unchanged": sha(output / "freeze.json") == frozen_manifest_sha,
              "criteria_unchanged": sha(output / "criteria.json") == freeze["criteria_sha256"],
              "reference_copies_unchanged": all((output / path).is_file() and sha(output / path) == expected
                                                for path, expected in reference_copies.items()),
              "references_separated": all(row["passed"] for row in independence.values()),
              **{family + "_declared_checks": row["passed"] for family, row in families.items()}}
    report = {"schema": "orionq.hybrid-qualification-report.v1",
              "status": "partial_verified" if all(checks.values()) else "failed",
              "scope": plan["scope"], "seeds": plan["seeds"], "checks": checks,
              "families": families, "changed_sources": drift, "changed_previous_freezes": previous_drift,
              "elapsed_s": time.perf_counter() - started, "freeze_sha256": frozen_manifest_sha,
              "physical_hardware_validated": False, "security_certified": False,
              "physical_actuation": False, "coherent_orbital_relay_validated": False,
              "trl_assigned": False, "certified_secret_bits": 0,
              "whole_hybrid_qualified": False, "finite_sns_backend_validated": False,
              "interpretation": "Only declared comparable components and software flow; physical model discrepancies and absent protocols remain open"}
    write(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=PLAN)
    parser.add_argument("--worker", choices=WORKERS)
    parser.add_argument("--seeds", type=int, nargs="+")
    args = parser.parse_args()
    if args.worker:
        if not args.seeds:
            parser.error("worker requires explicit seeds")
        worker(args.worker, args.output, args.seeds)
        return 0
    report = run_campaign(args.output, args.plan)
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["status"] == "partial_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
