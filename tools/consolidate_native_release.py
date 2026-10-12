"""Consolidate completed native release evidence without executing the application.

This reader checks the mandatory build reports, packaged bytes, portable ZIP,
static source comparison and JUnit results. It neither launches the EXE nor
opens operator state. Automatic software acceptance is distinct from human
review and experimental TRL qualification. Existing evidence is never replaced.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def _tool(name):
    spec = importlib.util.spec_from_file_location(
        "orion_release_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _json(path):
    def invalid(value):
        raise ValueError("Nonfinite JSON value: " + value)
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError("Expected a report object: " + str(path))
    return value


def _artifact(path, root):
    path = Path(path).resolve(strict=True)
    relative = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
    return {"path": relative, "bytes": path.stat().st_size, "sha256": digest(path)}


def _passed(report, name, executable_sha256):
    if report.get("passed") is not True:
        raise ValueError(name + " has not passed")
    checks = report.get("checks")
    if isinstance(checks, dict) and any(value is not True for value in checks.values()):
        raise ValueError(name + " contains an unsuccessful check")
    if isinstance(checks, list) and any(
            not isinstance(row, dict) or row.get("passed") is not True for row in checks):
        raise ValueError(name + " contains an unsuccessful check")
    for field in ("executable_sha256", "executable_sha256_before", "executable_sha256_after"):
        if field in report and report[field] != executable_sha256:
            raise ValueError(name + " belongs to a different executable: " + field)


def mandatory_junits(build_script=None):
    """Read explicit build gates; new script gates cannot silently be omitted."""
    script = Path(build_script or ROOT / "tools/build_windows_console.ps1")
    names = re.findall(r'--junitxml\s+\(Join-Path\s+\$releaseRoot\s+"([^"/\\]+\.xml)"\)',
                       script.read_text(encoding="utf-8"))
    if not names or len(set(names)) != len(names):
        raise ValueError("Mandatory JUnit paths are absent or ambiguous in the build script")
    return tuple(names)


def junit_summary(paths, *, required_names=()):
    """Retain every execution and resolve each case by report modification time.

    Repeated cases in different build corpora remain separate executions. The
    unique-case view uses (classname, name); ties use lexical report order and
    then document order. No nested temporary fixture XML is counted.
    """
    paths = sorted((Path(path).resolve(strict=True) for path in paths),
                   key=lambda path: (path.stat().st_mtime_ns, str(path)))
    missing = set(required_names) - {path.name for path in paths}
    if missing:
        raise ValueError("Build is incomplete; missing JUnit gates: " + ", ".join(sorted(missing)))
    latest, totals, reports = {}, Counter(), []
    for path in paths:
        tree = ET.parse(path).getroot()
        if tree.tag not in {"testsuites", "testsuite"}:
            raise ValueError("Not a JUnit test suite: " + str(path))
        cases, counts = list(tree.iter("testcase")), Counter()
        for case in cases:
            if not case.get("name"):
                raise ValueError("JUnit test case has no identity: " + str(path))
            status = ("errors" if case.find("error") is not None else
                      "failures" if case.find("failure") is not None else
                      "skipped" if case.find("skipped") is not None else "passed")
            counts[status] += 1
            identity = (case.get("classname", ""), case.get("name"))
            latest[identity] = {"classname": identity[0], "name": identity[1],
                                "status": status, "report": str(path)}
        for suite in tree.iter("testsuite"):
            # Also detect collection errors represented only by suite counters.
            for key in ("failures", "errors"):
                declared = int(suite.get(key, "0"))
                if declared and not counts[key]:
                    raise ValueError("JUnit suite failure has no recorded case: " + str(path))
        if path.name in required_names and (not cases or counts["failures"] or counts["errors"]):
            raise ValueError("Mandatory JUnit gate is empty or failed: " + str(path))
        totals.update(counts)
        reports.append({"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size,
                        "mtime_ns": path.stat().st_mtime_ns, "executions": len(cases),
                        **{key: counts[key] for key in ("passed", "failures", "errors", "skipped")}})
    resolved = Counter(row["status"] for row in latest.values())
    return {"scope": "Selected root-level JUnit reports; not a full-repository test run.",
            "reports": reports, "report_count": len(reports), "executions": sum(totals.values()),
            "execution_counts": {key: totals[key] for key in ("passed", "failures", "errors", "skipped")},
            "unique_cases": len(latest),
            "latest_case_counts": {key: resolved[key] for key in ("passed", "failures", "errors", "skipped")},
            "latest_nonpassed_cases": [row for row in latest.values() if row["status"] != "passed"],
            "resolution": "Report mtime_ns, lexical path, then document order; identity is classname + name."}


def verify_frozen_report(path, exe, *, version):
    from quantum_autopilot.product import PRODUCT_NAME
    reader = _tool("verify_frozen_source")
    report = _json(path)
    sha = digest(exe)
    _passed(report, "frozen source comparison", sha)
    checks = {"executable_name", "all_required_modules_match", "all_required_resources_match",
              "excluded_resources_absent",
              "distribution_metadata_matches", "windows_version_matches", "executable_unchanged"}
    if (report.get("schema") != "orion.frozen-source-verification.v1"
            or report.get("source_version") != version or report.get("errors") != []
            or type(report.get("source_optimization")) is not int
            or report["source_optimization"] != 1
            or not isinstance(report.get("checks"), dict)
            or set(report.get("checks", {})) != checks
            or report.get("executable_sha256_before") != sha
            or report.get("executable_sha256_after") != sha
            or Path(report.get("executable", "")).resolve() != exe.resolve()
            or any(report.get(key) is not False for key in (
                "frozen_code_executed", "gui_started", "operator_state_accessed", "physical_action_performed"))):
        raise ValueError("Static source report identity or scope is invalid")
    modules, resources = report.get("modules"), report.get("resources")
    if (not isinstance(modules, list) or len(modules) != len(reader.MODULES)
            or any(not isinstance(row, dict) for row in modules)
            or {row.get("module") for row in modules} != set(reader.MODULES)
            or not isinstance(resources, list) or len(resources) != len(reader.RESOURCES)
            or any(not isinstance(row, dict) for row in resources)
            or {row.get("resource") for row in resources} != set(reader.RESOURCES)):
        raise ValueError("Static source comparison corpus is incomplete")
    for row in modules:
        source = ROOT / "src" / Path(*row["module"].split(".")).with_suffix(".py")
        if (row.get("passed") is not True or row.get("source_file_sha256") != digest(source)
                or row.get("source_code_sha256") != row.get("frozen_code_sha256")
                or not re.fullmatch(r"[0-9a-f]{64}", row.get("frozen_code_sha256", ""))):
            raise ValueError("Static source comparison is stale or mismatched: " + row["module"])
    for row in resources:
        source = ROOT / "src/quantum_autopilot" / row["resource"]
        bundled = exe.parent / "_internal/quantum_autopilot" / row["resource"]
        if (row.get("passed") is not True or row.get("source_sha256") != digest(source)
                or row.get("bundled_sha256") != digest(bundled)
                or row.get("source_sha256") != row.get("bundled_sha256")):
            raise ValueError("Static resource comparison is stale or mismatched: " + row["resource"])
    metadata = report.get("distribution_metadata", [])
    if (len(metadata) != 1 or metadata[0].get("version") != version
            or report.get("windows_version_info") != {
                "ProductName": PRODUCT_NAME, "OriginalFilename": exe.name,
                "FileVersion": version, "ProductVersion": version}):
        raise ValueError("Static product version identity is invalid")
    return {"modules": len(modules), "resources": len(resources), "version": version,
            "source_optimization": report["source_optimization"],
            "passed": True, "sha256": digest(path)}


def scientific_rejection_names():
    """The fixed v1 diagnostic corpus, without importing or executing physics.

    Names identify the actual invalid inputs, rather than accepting any 112
    rows labelled successful. A changed corpus requires a deliberate reader
    contract update; it cannot silently replace a missing scientific check.
    """
    fields = (
        "NUM_QUBITS", "T1", "T2", "DISTANCE_KM", "ALPHA_DB_PER_KM",
        "DEPOLARIZATION_P", "PHOTON_RATE_BASE", "STORAGE_TIME",
        "TRANSMISSION_EXPOSURE_TIME", "SEED", "TX_POWER_DBM", "NOISE_FLOOR_DBM",
        "KAPPA_PHASE", "KAPPA_DEPOL_FROM_BER", "KAPPA_ENV_T1T2", "OU_THETA_SIGMA",
        "OU_THETA_MEAN_REVERSION", "OU_SAMPLING_INTERVAL_STEPS")
    names = {f"PhysicsConfig.{name}.{value}" for name in fields
             for value in ("nan", "infinity", "boolean")}
    names.update("PhysicsConfig." + name for name in (
        "T1.0.0", "T2.0.0", "DISTANCE_KM.-1.0", "ALPHA_DB_PER_KM.-0.1",
        "PHOTON_RATE_BASE.-1.0", "STORAGE_TIME.-1.0", "TRANSMISSION_EXPOSURE_TIME.-1.0",
        "OU_THETA_SIGMA.-1.0", "OU_THETA_MEAN_REVERSION.-1.0", "KAPPA_ENV_T1T2.-1.0",
        "KAPPA_DEPOL_FROM_BER.-1.0", "DEPOLARIZATION_P.-0.1", "DEPOLARIZATION_P.1.1",
        "NUM_QUBITS.0", "NUM_QUBITS.2.5", "SEED.-1", "SEED.0.5",
        "OU_SAMPLING_INTERVAL_STEPS.0", "T2_bound"))
    names.update(f"{factory}.{values}" for factory in (
        "QuantumNoiseChannel", "QuantumRepeaterNode") for values in (
        "{'T1': 1.0, 'T2': 3.0}", "{'T1': 0.0}", "{'T2': nan}", "{'T1': inf}",
        "{'depol_prob': -0.1}", "{'depol_prob': 1.1}", "{'depol_prob': nan}",
        "{'depol_prob': True}"))
    names.update("QuantumRepeaterNode." + values for values in (
        "{'shots': 0}", "{'shots': 1.5}", "{'history_maxlen': 0}",
        "{'seed': True}", "{'depol_prob': 0.6}"))
    names.update(f"{factory}.elapsed.{elapsed}" for factory in (
        "QuantumNoiseChannel", "pure_dephasing") for elapsed in ("-1.0", "nan", "inf", "True"))
    names.update("QuantumNoiseChannel.override." + value for value in ("-0.1", "1.1", "nan", "True"))
    names.add("QuantumChannel.mutated_config")
    names.update("QuantumChannel.transmit." + name for name in (
        "negative_distance", "lost_photon_invalid_probability", "negative_exposure",
        "nan_storage", "boolean_storage"))
    return frozenset(names)


def verify_scientific_input(report, *, executable_sha256, source_optimization):
    """Check diagnostic results against independently verified compiled code.

    PyInstaller's optimize=1 compiles bundled modules with that optimization;
    the bootloader may still report sys.flags.optimize == 0. This runtime flag
    is recorded, never used as proof of the bundled bytecode optimization.
    The caller must first verify the complete static source report for this EXE.
    """
    _passed(report, "scientific input", executable_sha256)
    runtime_optimization = report.get("python_optimization")
    checks = report.get("checks")
    if (type(source_optimization) is not int or source_optimization != 1
            or type(runtime_optimization) is not int or runtime_optimization not in (0, 1, 2)
            or report.get("schema") != "orion.scientific-input-validation.v1"
            or report.get("frozen") is not True
            or type(report.get("checks_count")) is not int or report["checks_count"] != 113
            or not isinstance(checks, list) or len(checks) != 113
            or report.get("physical_action_performed") is not False
            or report.get("hardware_validated") is not False
            or report.get("scope") != (
                "Input rejection and local simulated-channel boundary checks; not experimental qualification")
            or not isinstance(report.get("python_version"), str) or not report["python_version"].strip()):
        raise ValueError("Scientific input evidence is incomplete or has an incompatible scope/optimization")
    expected = scientific_rejection_names()
    names = [row.get("name") for row in checks]
    if (any(not isinstance(name, str) or not name.strip() for name in names)
            or len(set(names)) != 113 or set(names) != expected | {"valid_physical_boundaries"}):
        raise ValueError("Scientific input corpus is incomplete, duplicated or contains incompatible names")
    for row in checks:
        if row["name"] in expected and (
                set(row) != {"name", "passed", "outcome", "error_type", "message"}
                or row.get("outcome") != "rejected" or row.get("error_type") != "ValueError"
                or not isinstance(row.get("message"), str) or not row["message"].strip()):
            raise ValueError("Scientific input did not record a valid rejection: " + row["name"])
    boundary = next(row for row in checks if row["name"] == "valid_physical_boundaries")
    numeric = ("pure_dephasing", "identity_fidelity", "fully_depolarized_fidelity", "fidelity_abs_tolerance")
    if (set(boundary) != {"name", "passed", "outcome", *numeric}
            or boundary.get("outcome") != "evaluated"
            or any(type(boundary.get(key)) not in (int, float)
                   or not math.isfinite(boundary[key]) for key in numeric)
            or boundary["fidelity_abs_tolerance"] != 1e-7
            or boundary["pure_dephasing"] != 0.0
            or abs(boundary["identity_fidelity"] - 1.0) > 1e-7
            or abs(boundary["fully_depolarized_fidelity"] - 0.25) > 1e-7):
        raise ValueError("Scientific input boundary values or numerical tolerance are invalid")
    return {"passed": True, "checks": 113, "rejected_inputs": len(expected), "boundaries": 1,
            "python_runtime_optimization": runtime_optimization,
            "compiled_source_optimization": source_optimization}


def verify_portable(release, product, exe, package_integrity, *, version):
    from quantum_autopilot.product import PRODUCT_NAME
    record = _json(release / "portable-package.json")
    archive = Path(record.get("archive", "")).resolve(strict=True)
    sha = digest(archive)
    if (archive.parent != release or archive.name != f"{PRODUCT_NAME}-{version}-native-windows-x64.zip"
            or record.get("sha256") != sha or record.get("bytes") != archive.stat().st_size
            or type(record.get("bytes")) is not int
            or record.get("executable_sha256") != digest(exe)
            or record.get("zip_crc_verified") is not True
            or record.get("files") != package_integrity["file_count"] + 1):
        raise ValueError("Portable ZIP record does not identify this complete package")
    checksum = Path(str(archive) + ".sha256")
    if checksum.read_text(encoding="utf-8").strip() != f"{sha}  {archive.name}":
        raise ValueError("Portable ZIP checksum sidecar differs")
    sums = {}
    for line in (product / "SHA256SUMS.txt").read_text(encoding="utf-8-sig").splitlines():
        value, relative = line.split("  ", 1)
        sums[f"{product.name}/{relative}"] = value.lower()
    sums[f"{product.name}/SHA256SUMS.txt"] = package_integrity["manifest_sha256"]
    with ZipFile(archive) as bundle:
        names = [entry.filename for entry in bundle.infolist()]
        if len(names) != len(set(names)) or set(names) != set(sums):
            raise ValueError("Portable ZIP entries differ from the verified package")
        if bundle.testzip() is not None:
            raise ValueError("Portable ZIP CRC verification failed")
        for entry in bundle.infolist():
            value = hashlib.sha256()
            with bundle.open(entry) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    value.update(block)
            if value.hexdigest() != sums[entry.filename]:
                raise ValueError("Portable ZIP bytes differ from the verified package: " + entry.filename)
    return {**record, "zip_crc_rechecked": True, "all_archived_bytes_match_package": True,
            "checksum_sidecar_sha256": digest(checksum)}


def verify_acceptance(acceptance, package_integrity):
    if (acceptance.get("schema") != "orion.windows_acceptance.v1"
            or acceptance.get("automatic_passed") is not True
            or acceptance.get("acceptance_status") != "manual_review_required"
            or acceptance.get("scope") != "local_software_checks_only"
            or acceptance.get("manual_review") != "pending"
            or acceptance.get("independent_machine_verified") is not False
            or acceptance.get("integrity_only") is not False or acceptance.get("error") is not None
            or acceptance.get("checks") != {
                "integrity": True, "self_test": True, "synthetic_stress_20_rounds": True}
            or acceptance.get("package") != package_integrity):
        raise ValueError("Native acceptance is incomplete, stale or conflates automatic and manual approval")


def consolidate(release_root, frozen_source_report, *, supplementary_tests=None):
    from quantum_autopilot.product import EXECUTABLE_NAME, PACKAGE_DIRECTORY, product_version
    from quantum_autopilot.windows_acceptance import verify_package
    package_reader = _tool("package_console_portable")
    release = Path(release_root).resolve(strict=True)
    product = release / PACKAGE_DIRECTORY
    exe = product / EXECUTABLE_NAME
    if not product.is_dir() or not exe.is_file():
        raise ValueError("Current native product package is missing")
    version, sha = product_version(), digest(exe)
    if not version or version == "development":
        raise ValueError("Canonical declared product version is required")
    frozen = verify_frozen_report(frozen_source_report, exe, version=version)
    junit = junit_summary(release.glob("*.xml"), required_names=mandatory_junits())
    supplementary = (junit_summary(Path(supplementary_tests).resolve(strict=True).glob("*.xml"))
                     if supplementary_tests is not None else None)
    if supplementary is not None and not supplementary["report_count"]:
        raise ValueError("Supplementary test directory contains no root-level JUnit reports")
    if supplementary is not None and (supplementary["latest_case_counts"]["errors"]
                                      or supplementary["latest_case_counts"]["failures"]):
        raise ValueError("Supplementary tests still have unresolved failures")
    gates = (
        ("branding", "branding-validation/branding-report.json", "validate_branding"),
        ("desktop", "desktop-behavior-validation/report.json", "validate_desktop_behavior"),
        ("roadmap", "roadmap-validation/report.json", "validate_roadmap"),
        ("optional_models", "optional-models-validation/optional-models-report.json", "validate_optional"),
        ("startup", "startup-validation/report.json", "validate_startup"),
        ("laboratory", "laboratory-validation/report.json", "validate_laboratory"),
        ("satellite_mission", "satellite-mission-validation/report.json", "validate_satellite_mission"),
        ("worker_campaign", "campaign-validation/report.json", None),
        ("self_test", "self-test.json", None),
        ("scientific_input", "scientific-input-validation.json", None),
        ("satellite", "satellite-validation/report.json", None),
        ("global_network", "global-validation/report.json", None),
    )
    evidence, artifacts = {}, [_artifact(ROOT / "tools/build_windows_console.ps1", release)]
    for name, relative, validator in gates:
        path = release / relative
        record = _json(path)
        _passed(record, name, sha)
        scientific = (verify_scientific_input(record, executable_sha256=sha,
                      source_optimization=frozen["source_optimization"])
                      if name == "scientific_input" else None)
        if validator is not None:
            options = {"evidence_root": path.parent} if name in {"branding", "desktop", "satellite_mission"} else {}
            getattr(package_reader, validator)(record, exe, **options)
        if name == "worker_campaign" and record.get("sha256") != sha:
            raise ValueError("Worker campaign belongs to a different executable")
        if name == "global_network" and (
                record.get("schema") != "orion.global-network-validation.v1"
                or record.get("frozen") is not True or record.get("full_campaign") is not True
                or record.get("executable_sha256") != sha
                or type(record.get("certified_secret_bits")) is not int
                or record["certified_secret_bits"] != 0 or record.get("physical_actuation") is not False):
            raise ValueError("Global campaign is incomplete or has an incompatible scope")
        if name == "satellite" and (
                record.get("schema") != "orion.satellite-validation.v2"
                or any(record.get(key) is not False for key in (
                    "physical_hardware_validated", "mission_ready", "security_certified", "physical_actuation"))):
            raise ValueError("Satellite evidence cannot claim hardware or mission qualification")
        evidence[name] = {"passed": True, "report": relative,
                          "executable_hash_provided": any(key in record for key in (
                              "sha256", "executable_sha256", "executable_sha256_before"))}
        if scientific is not None:
            evidence[name]["scientific_validation"] = scientific
        artifacts.append(_artifact(path, release))
    for folder in ("stress-validation", "native-acceptance/stress"):
        matches = list((release / folder).rglob("report.json"))
        if len(matches) != 1:
            raise ValueError("Exactly one completed stress report required: " + folder)
        record = _json(matches[0])
        _passed(record, folder, sha)
        if record.get("rounds_requested") != 20 or record.get("physical_action_performed") is not False:
            raise ValueError("Stress evidence is incomplete or permits physical action")
        evidence[folder] = {"passed": True, "report": matches[0].relative_to(release).as_posix()}
        artifacts.append(_artifact(matches[0], release))
    native_self = release / "native-acceptance/self-test.json"
    _passed(_json(native_self), "native acceptance self test", sha)
    artifacts.append(_artifact(native_self, release))
    package_integrity = verify_package(product)
    acceptance_path = release / "native-acceptance/acceptance-report.json"
    acceptance = _json(acceptance_path)
    verify_acceptance(acceptance, package_integrity)
    artifacts.append(_artifact(acceptance_path, release))
    artifacts.append(_artifact(frozen_source_report, release))
    portable = verify_portable(release, product, exe, package_integrity, version=version)
    artifacts.append(_artifact(release / "portable-package.json", release))
    if digest(exe) != sha:
        raise ValueError("Executable changed during read-only consolidation")
    return {
        "schema": "orion.native-release-consolidation.v1", "passed": True,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "version": version, "release_root": str(release), "executable": str(exe),
        "executable_sha256": sha, "gates": evidence, "artifacts": artifacts,
        "source_comparison": frozen, "package_integrity": package_integrity,
        "portable_package": portable, "build_tests": junit, "supplementary_tests": supplementary,
        "automatic_acceptance_passed": True, "manual_review": "pending",
        "independent_machine_verified": False, "application_executed_by_consolidator": False,
        "operator_state_accessed": False, "physical_action_performed": False,
        "scientific_scope": {
            "models": "conditional_computational_validation", "certified_secret_bits": 0,
            "physical_hardware_qualified": False, "trl5_demonstrated": False,
            "trl_qualification": "Separate identified human review and experimental evidence required.",
            "open_gate": "G05 full-network performance remains open."},
        "limitations": [
            "This consolidation reads existing evidence; it does not repeat native, GUI or protocol execution.",
            "JUnit execution counts include repeated cases; unique-case counts are reported separately.",
            "Hashes and ZIP CRC detect corruption; they do not authenticate authorship.",
            "Reports lacking an executable hash are recorded explicitly; other gates bind the exact binary.",
            "Automatic native software acceptance is not human approval, RF/optical qualification or TRL 5."],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--frozen-source-report", required=True, type=Path)
    parser.add_argument("--supplementary-tests", type=Path,
                        help="Optional directory containing only root-level supplementary JUnit reports.")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.suffix.lower() != ".json" or output.exists():
        parser.error("--output must be a previously nonexistent JSON report")
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    from quantum_autopilot.product import PACKAGE_DIRECTORY
    product = args.release_root.resolve() / PACKAGE_DIRECTORY
    if output == product or output.is_relative_to(product):
        parser.error("--output must be outside the checksummed product directory")
    try:
        report = consolidate(args.release_root, args.frozen_source_report,
                             supplementary_tests=args.supplementary_tests)
    except (OSError, ValueError, TypeError, KeyError, BadZipFile, ET.ParseError) as error:
        report = {"schema": "orion.native-release-consolidation.v1", "passed": False,
                  "created_at_utc": datetime.now(timezone.utc).isoformat(),
                  "release_root": str(args.release_root.resolve()),
                  "error": f"{type(error).__name__}: {error}",
                  "application_executed_by_consolidator": False, "manual_review": "pending"}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"passed": report["passed"], "report": str(output),
                      "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
