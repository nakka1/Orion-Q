"""Freeze and inspect a laboratory software evidence package; never assign TRL.

The checks below establish local consistency and functional coverage. Hashes
are integrity evidence, not authenticated authorship or independent review.
No instrument, credential store or operator workspace is accessed.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
import zlib

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "configs/qualification/trl4_software_v1.json"
SCHEMA = "orionq.trl4-software-protocol.v1"
REPORT_SCHEMAS = {
    "reference-1": "orion.trl5.reference-campaign.v1",
    "reference-2": "orion.trl5.reference-campaign.v1",
    "pipeline-1": "orion.trl5.pipeline-campaign.v1",
    "pipeline-2": "orion.trl5.pipeline-campaign.v1",
    "native-ui": "orion.console-performance.v1",
    "console": "orion.trl4.console-campaign.v1",
}
TECHNOLOGIES = {"entanglement_swapping", "quantum_memory", "purification", "tf_qkd", "satellite_doppler"}
STAGES = ("acquired", "validated", "synchronized", "protocol_processed", "classical_transmitted", "classical_received", "metrics_computed")
SCENARIO_CHECKS = {
    "causal_stage_order_and_paired_classical_receive", "configuration_persisted",
    "export_matches_persisted_receipts", "frontend_snapshot_contract",
    "no_certified_secret_material", "no_physical_actuation", "no_unreported_event_backlog", "receipt_hash_chain_valid",
}
FAULTS = {"absent": "timeout", "late": "timeout", "precomputed": "precomputed_result",
          "unsynchronized": "unsynchronized", "wrong_binding": "wrong_binding",
          "wrong_classical": "wrong_channel", "wrong_quantum": "wrong_channel"}
PIPELINE_CHECKS = {f"{scenario}.{check}" for scenario in ("nominal", "degraded", "unavailable", "recovery") for check in SCENARIO_CHECKS} | {
    *(f"negative.{fault}" for fault in FAULTS), "source_files_unchanged", "same_seed.scientific_fingerprint_equal",
    "replay.identical_processing_and_scientific_results", "nominal.delivery_outcome_assessed",
    "nominal.five_technologies_processed", "nominal.real_pipeline_completions",
    "degraded.physical_losses_reduce_transmission", "unavailable.explicit_timeout_and_retry",
    "unavailable.no_success_without_observations", "recovery.outage_then_causal_resumption",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value, *, ascii=False):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=ascii, allow_nan=False).encode("utf-8")).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def require_file(path, message):
    if not Path(path).is_file():
        raise FileNotFoundError(message)


def finite(value):
    if isinstance(value, float):
        require(math.isfinite(value), "non-finite evidence")
    elif isinstance(value, dict):
        for item in value.values():
            finite(item)
    elif isinstance(value, list):
        for item in value:
            finite(item)


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, f"duplicate JSON field: {key}")
        result[key] = value
    return result


def loads(value):
    result = json.loads(value, object_pairs_hook=_pairs)
    finite(result)
    return result


def load_json(path):
    path = Path(path)
    require(path.stat().st_size <= 64 * 1024 * 1024, "evidence exceeds size limit")
    result = loads(path.read_text(encoding="utf-8-sig"))
    require(isinstance(result, dict), "JSON evidence must be an object")
    return result


def relative_name(name):
    require(isinstance(name, str) and bool(name) and "\\" not in name, "normalized relative path required")
    require(not PurePosixPath(name).is_absolute() and not PureWindowsPath(name).drive,
            "absolute path forbidden")
    require(all(part not in {".", "..", ""} for part in name.split("/")), "path escape or noncanonical path")
    return name


def contained(base, name):
    name = relative_name(name)
    target = (Path(base) / name).resolve()
    require(target.is_relative_to(Path(base).resolve()), "path escapes its root")
    return target


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def integer(value, message):
    require(type(value) is int and value >= 0, message)
    return value


def number(value, message="finite numeric observation required"):
    require(type(value) in (int, float) and math.isfinite(value), message)
    return value


def node_identity(nodeid):
    require(isinstance(nodeid, str), "invalid collected nodeid")
    head, separator, parameters = nodeid.partition("[")
    parts = head.split("::")
    require(len(parts) >= 2 and parts[0].endswith(".py"), "collected testcase must name a Python module")
    name = relative_name(parts[0])
    require(name.startswith("tests/") and all(parts[1:]), "invalid collected test identity")
    return name, (name[:-3].replace("/", ".") + ("." + ".".join(parts[1:-1]) if len(parts) > 2 else ""), parts[-1] + separator + parameters)


def validate_protocol(protocol, root):
    require(protocol.get("schema") == SCHEMA and protocol.get("protocol_id") == "SOFT-ALPHA-TRL4-v1",
            "unsupported protocol identity")
    revision = protocol.get("revision")
    require(type(revision) is int and revision in (1, 2) and protocol.get("target_scope") == "SOFT-ALPHA",
            "unsupported protocol revision or scope")
    integer(protocol.get("seed"), "declared integer seed required")
    groups = protocol.get("test_groups")
    require(isinstance(groups, dict) and bool(groups), "test groups missing")
    modules = []
    for group, paths in groups.items():
        require(re.fullmatch(r"[a-z][a-z0-9_-]*", group) is not None, "invalid group name")
        require(isinstance(paths, list) and bool(paths), "empty test group")
        for name in paths:
            require(name.startswith("tests/") and name.endswith(".py") and "::" not in name, "test file path required; selectors forbidden")
            require(contained(root, name).is_file(), f"required test file missing: {name}")
            modules.append(name)
    require(len(modules) == len(set(modules)), "test module appears in multiple groups")
    requirements = protocol.get("requirements")
    require(isinstance(requirements, list) and bool(requirements), "requirements missing")
    ids = []
    mapped = set()
    for requirement in requirements:
        require(isinstance(requirement, dict), "invalid requirement")
        ids.append(requirement.get("id"))
        require(isinstance(ids[-1], str) and bool(ids[-1]), "requirement id missing")
        for field in ("label", "acceptance"):
            require(bool(requirement.get(field)), f"requirement {field} missing")
        for field in ("production_files", "test_files", "documents"):
            paths = requirement.get(field)
            require(isinstance(paths, list) and bool(paths), f"required {field} missing")
            for name in paths:
                require(contained(root, name).is_file(), f"required source missing: {name}")
        require(set(requirement["test_files"]) <= set(modules), "requirement mapped to unplanned test module")
        mapped.update(requirement["test_files"])
        require(isinstance(requirement.get("limitations"), list), "requirement limitations must be explicit")
        campaigns = requirement.get("campaigns", [])
        require(isinstance(campaigns, list) and len(campaigns) == len(set(campaigns))
                and set(campaigns) <= {"reference", "pipeline", "native-ui", "console"}, "invalid requirement campaigns")
    require(len(ids) == len(set(ids)), "duplicate requirement id")
    require(mapped == set(modules), "planned test module has no requirement mapping")
    expected_reports = {f"group-{group}": f"group-{group}.xml" for group in groups}
    expected_reports.update({f"{kind}-{repeat}": f"{kind}-{repeat}/report.json"
                             for kind in ("reference", "pipeline") for repeat in (1, 2)})
    expected_reports["native-ui"] = "native-ui/report.json"
    if "console" in protocol.get("reports", {}):
        expected_reports["console"] = "console/report.json"
    require(protocol.get("reports") == expected_reports, "report paths must match the fixed report layout")
    require(not any("console" in r.get("campaigns", []) for r in requirements) or "console" in expected_reports,
            "console evidence required but no console report planned")
    if "console" in expected_reports:
        cases = protocol.get("console_cases")
        require(isinstance(cases, list) and bool(cases) and len(cases) == len(set(cases)), "unique planned console case ids required")
        require(all(isinstance(case, str) and case for case in cases), "invalid console case id")
        criteria = protocol.get("console_case_criteria")
        require(isinstance(criteria, dict) and set(criteria) == set(cases), "console criteria must match planned cases")
        for checks in criteria.values():
            require(isinstance(checks, list) and bool(checks) and len(checks) == len(set(checks))
                    and all(isinstance(name, str) and name for name in checks), "unique nonempty console criteria required")
    for name in protocol.get("source_documents", []) + protocol.get("fixtures", []):
        require(contained(root, name).exists(), f"declared source missing: {name}")
    require(contained(root, protocol.get("collection_manifest")).is_file(), "pre-run pytest collection manifest required")
    require(contained(root, protocol.get("known_trl5_failure")).is_file(), "known TRL5 assessment missing")
    require(contained(root, protocol.get("native_action_plan")).is_file(), "prospective native action plan missing")
    native = protocol.get("native_expectations")
    require(isinstance(native, dict) and set(native.get("planned_actions", {})) == {"map", "globe", "tabs", "swaps", "soak"},
            "prospective native work counts missing")
    for value in native["planned_actions"].values():
        require(integer(value, "invalid native work count") > 0, "empty native work count")
    for field in ("selections", "page_navigations", "planned_mode_changes", "mode_transitions"):
        require(integer(native.get(field), "missing native functional expectation") > 0, "empty native functional expectation")
    return expected_reports


def collected_cases(protocol, root):
    collection = load_json(contained(root, protocol["collection_manifest"]))
    require(collection.get("schema") == "orion.trl4.pytest-collection.v1", "unsupported collection manifest")
    require(set(collection.get("groups", {})) == set(protocol["test_groups"]), "collection groups disagree with protocol")
    result, identities = {}, set()
    for group, paths in protocol["test_groups"].items():
        cases = collection["groups"][group]
        require(isinstance(cases, list) and bool(cases), "empty collected group")
        result[group] = []
        represented = set()
        for nodeid in cases:
            path, identity = node_identity(nodeid)
            require(path in paths, "collected testcase belongs to unplanned module")
            require(identity not in identities, "duplicate collected testcase identity")
            identities.add(identity)
            represented.add(path)
            result[group].append({"nodeid": nodeid, "file": path, "classname": identity[0], "name": identity[1]})
        require(represented == set(paths), "planned test module absent from collection")
    return result


def source_inventory(root, protocol):
    """All code and test fixtures, plus frozen operating/design documentation."""
    paths = set()
    names = ["src", "tests", "tools", "pyproject.toml", PROTOCOL,
             "data/reference", protocol["collection_manifest"], protocol["native_action_plan"], *protocol.get("source_documents", []),
             *protocol.get("fixtures", [])]
    for requirement in protocol["requirements"]:
        names.extend(requirement["production_files"] + requirement["test_files"] + requirement["documents"])
    for name in names:
        target = contained(root, name)
        candidates = target.rglob("*") if target.is_dir() else [target]
        for path in candidates:
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
                relative = path.relative_to(root).as_posix()
                # resolve also rejects symlinked source files outside the workspace.
                require(contained(root, relative).is_file(), "invalid source inventory entry")
                paths.add(relative)
    return {name: sha(contained(root, name)) for name in sorted(paths)}


def expected_report_sources(root, protocol, inventory):
    """Prospective complete producer universes, never inferred from reports."""
    reference = {"tools/run_trl5_reference_campaign.py", "tests/fixtures/trl5_refs/references.json",
                 "tests/test_trl5_reference_campaign.py", "data/reference/gavignet_2024_published_aggregate.csv", "data/reference/README.md"}
    reference.update(f"src/quantum_autopilot/quantum_protocols/{name}.py" for name in ("physics", "contracts", "protocols", "sources"))
    runner = ast.parse(contained(root, "tools/run_trl5_pipeline_campaign.py").read_text(encoding="utf-8-sig"))
    declared = [node for node in runner.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "SOURCE_FILES" for target in node.targets)]
    require(len(declared) == 1, "pipeline producer source declaration missing or ambiguous")
    pipeline = set(ast.literal_eval(declared[0].value))
    native = {name for name in inventory if name.startswith("src/quantum_autopilot/")
              and Path(name).suffix.lower() in {".py", ".json", ".yaml", ".yml", ".csv", ".npz"}}
    native.update({"pyproject.toml", "tools/benchmark_console_performance.py"})
    universes = {"reference": reference, "pipeline": pipeline, "native-ui": native}
    if "console" in protocol["reports"]:
        universes["console"] = {name for name in inventory if name.startswith("src/quantum_autopilot/") and name.endswith(".py")}
        universes["console"].add("tools/run_trl4_console_campaign.py")
    for kind, names in universes.items():
        require(bool(names) and names <= set(inventory), f"mandatory {kind} producer sources missing from freeze")
    return {kind: {name: inventory[name] for name in sorted(names)} for kind, names in universes.items()}


def analytic_acceptance(root, seed):
    """Reuse the independent analytic freeze contract without editing TRL5 tools."""
    spec = importlib.util.spec_from_file_location("orion_trl5_acceptance", root / "tools/assess_trl5_software.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.analytic_acceptance(root, seed)


def environment():
    versions = {}
    for name in ("quantum-twin", "numpy", "scipy", "PySide6", "psutil", "pytest", "cryptography"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "unavailable"
    return {"platform": platform.platform(), "python": sys.version, "python_executable": sys.executable,
            "processor": platform.processor(), "logical_cpus": os.cpu_count(), "sqlite": sqlite3.sqlite_version,
            "versions": versions, "sdk_and_hardware_availability": "not inferred or probed"}


def prepare(directory, root=ROOT):
    root, directory = Path(root).resolve(), Path(directory).resolve()
    require(directory.is_relative_to(root) and not directory.exists(), "new evidence directory inside workspace required")
    protocol_path = contained(root, PROTOCOL)
    protocol = load_json(protocol_path)
    validate_protocol(protocol, root)
    collection = collected_cases(protocol, root)
    inventory = source_inventory(root, protocol)
    producer_sources = expected_report_sources(root, protocol, inventory)
    acceptance = analytic_acceptance(root, protocol["seed"])
    require(len(acceptance.get("checks", {})) == 35, "independent 35-check analytic acceptance missing")
    require(inventory == source_inventory(root, protocol), "source changed during freeze")
    known = contained(root, protocol["known_trl5_failure"])
    profile = protocol.get("run_profile", {})
    commands = [f"-m pytest {' '.join(paths)} -q --basetemp {directory / 'pytest-temp' / group} --junitxml={directory / ('group-' + group + '.xml')}"
                for group, paths in protocol["test_groups"].items()]
    commands += [f"tools/run_trl5_{kind}_campaign.py --output-dir {directory / (kind + '-' + str(repeat))} --seed {protocol['seed']}"
                 + (f" --seconds {profile.get('pipeline_simulated_seconds', 35)} --timeout-seconds {profile.get('pipeline_timeout_seconds', 180)}" if kind == "pipeline" else "")
                 for kind in ("reference", "pipeline") for repeat in (1, 2)]
    size = profile.get("window_px", [3440, 1440])
    commands += [f"tools/benchmark_console_performance.py --output {directory / 'native-ui'} --duration {profile.get('performance_duration_seconds', 15)} --soak {profile.get('performance_soak_minutes', 3)} --size {size[0]}x{size[1]}"]
    if "console" in protocol["reports"]:
        size = profile.get("console_window_px", [1440, 900])
        commands += [f"tools/run_trl4_console_campaign.py --output-dir {directory / 'console'} --size {size[0]}x{size[1]} --timeout-s {profile.get('console_timeout_seconds', 600)}"]
    manifest = {"schema": "orionq.trl4-freeze.v1", "protocol_id": protocol["protocol_id"],
                "revision": protocol["revision"], "target_scope": protocol["target_scope"], "seed": protocol["seed"],
                "created_at_utc": datetime.now(timezone.utc).isoformat(), "protocol_sha256": sha(protocol_path),
                "source_file_hashes": inventory, "source_manifest_sha256": digest(inventory),
                "expected_report_sources": producer_sources,
                "native_action_plan_sha256": sha(contained(root, protocol["native_action_plan"])),
                "test_cases": collection, "commands": commands, "environment": environment(),
                "test_environment": {"PYTHONPATH": "src", "QT_QPA_PLATFORM": profile.get("pytest_platform", "offscreen")},
                "native_environment": {"QT_QPA_PLATFORM": profile.get("native_platform", "windows")},
                "known_trl5_failure": {"path": protocol["known_trl5_failure"], "sha256": sha(known)},
                "limitations": ["Integrity hashes do not authenticate authorship or prevent whole-package forgery.",
                                "Repeated declared seed is not holdout validation.", "External human review remains mandatory.",
                                "No hardware or SDK qualification is inferred from software tests."]}
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "protocol.json").write_bytes(protocol_path.read_bytes())
    write_new(directory / "source-before.json", inventory)
    write_new(directory / "analytic-acceptance.json", acceptance)
    manifest["source_before_sha256"] = sha(directory / "source-before.json")
    manifest["analytic_acceptance_sha256"] = sha(directory / "analytic-acceptance.json")
    write_new(directory / "freeze.json", manifest)
    return manifest


def junit_cases(path, planned):
    raw = Path(path).read_bytes()
    require(len(raw) <= 64 * 1024 * 1024 and b"<!DOCTYPE" not in raw.upper() and b"<!ENTITY" not in raw.upper(), "unsafe XML evidence")
    tree = ET.fromstring(raw)
    require(tree.tag in {"testsuite", "testsuites"}, "unexpected JUnit root")
    expected = {(case["classname"], case["name"]): case["file"] for case in planned}
    actual, counts = set(), Counter()
    for case in tree.iter("testcase"):
        identity = (case.get("classname"), case.get("name"))
        require(identity not in actual, "duplicate JUnit testcase identity")
        require(identity in expected, f"unplanned JUnit testcase: {identity}")
        require(not any(child.tag in {"failure", "error", "skipped"} for child in case), f"failed, errored or skipped testcase: {identity}")
        if case.get("file"):
            require(relative_name(case.get("file")) == expected[identity], "JUnit file identity mismatch")
        actual.add(identity)
        counts[expected[identity]] += 1
    require(not any(element.tag in {"failure", "error", "skipped"} for element in tree.iter()), "JUnit errors outside testcase")
    require(actual == set(expected), f"missing JUnit testcases: {len(set(expected) - actual)}")
    # Counts declared on testsuite elements are deliberately ignored.
    return {"passed": True, "testcase_count": len(actual), "module_counts": dict(counts), "counter_policy": "actual testcase elements only"}


def source_hashes(report, manifest, root, kind, field="source_hashes"):
    hashes = report.get(field)
    require(isinstance(hashes, dict) and bool(hashes), f"{field} missing")
    require(hashes == manifest["expected_report_sources"][kind], f"complete {kind} producer source universe required")
    for name, expected in hashes.items():
        relative_name(name)
        require(manifest["source_file_hashes"].get(name) == expected and sha(contained(root, name)) == expected,
                f"unfrozen or changed reported source: {name}")


def validate_reference(report, acceptance):
    checks = report.get("checks")
    require(isinstance(checks, list) and len(checks) == 35, "expected 35 analytic checks")
    names = [item.get("name") for item in checks]
    require(len(names) == len(set(names)) and set(names) == set(acceptance["checks"]), "analytic check identity mismatch")
    for check in checks:
        require(check.get("passed") is True, "analytic check not passed")
        expected = acceptance["checks"][check["name"]]
        for field, value in expected.items():
            require(digest(check.get(field)) == digest(value), f"analytic acceptance changed: {check['name']}.{field}")
        actual = number(check.get("actual"))
        target = number(expected["expected"])
        atol = number(expected["absolute_tolerance"])
        rtol = number(expected["relative_tolerance"])
        require(atol >= 0 and rtol >= 0 and abs(actual - target) <= atol + rtol * abs(target), "analytic value outside frozen tolerance")
        require(number(check.get("absolute_error")) == abs(actual - target), "reported analytic error mismatch")
    normalized = {"seed": report["seed"], "checks": checks, "acquisition_summaries": report["acquisition_summaries"],
                  "experimental_reference_inventory": report["experimental_reference_inventory"]}
    require(report.get("scientific_fingerprint_sha256") == digest(normalized, ascii=True), "reference scientific fingerprint mismatch")
    require(report.get("experimental_calibration") == "not_demonstrated" and report.get("classification") == "analytic_verification",
            "analytic verification mislabeled as experimental calibration")
    return {"passed": True, "check_count": len(checks), "classification": "analytic_verification"}


def _normalized(value):
    if isinstance(value, dict):
        return {key: _normalized(item) for key, item in value.items()
                if key not in {"origin", "source_id", "observation_origin", "observation_source_id"}}
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    return value


def scenario_evidence(directory, name, state):
    location = contained(directory, name)
    snapshot = load_json(contained(location, state["snapshot_path"]))
    require(snapshot.get("schema") == "orion.technologies-live.v1" and snapshot.get("physical_actuation") is False,
            "invalid frontend snapshot or physical boundary")
    require(snapshot.get("backlog_before_clock") == 0 and state.get("final_sessions") == snapshot.get("sessions"), "snapshot/state mismatch or backlog")
    science = digest({key: {"status": session["status"], "counters": session["counters"], "metrics": _normalized(session["metrics"])}
                      for key, session in sorted(snapshot["sessions"].items())})
    require(science == state["scientific_fingerprint"], "scenario scientific fingerprint mismatch")
    path = contained(location, "receipts.jsonl")
    require(path.stat().st_size <= 64 * 1024 * 1024, "receipt evidence too large")
    rows = [loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    require(bool(rows) and len(rows) == state["receipt_count"], "receipt count mismatch")
    previous, grouped = "", defaultdict(list)
    for row in rows:
        require(row.get("schema") == "orion.technology-event.v1" and row.get("physical_actuation") is False, "invalid or physical receipt")
        require(row["previous_sha256"] == previous and row["receipt_sha256"] == digest({k: v for k, v in row.items() if k != "receipt_sha256"}), "receipt chain invalid")
        previous = row["receipt_sha256"]
        if row["command_id"]:
            grouped[row["command_id"]].append(row)
    require(dict(Counter(row["event"] for row in rows)) == state["event_counts"], "scenario event count mismatch")
    completed = 0
    for events in grouped.values():
        names = [row["event"] for row in events]
        if "metrics_computed" not in names:
            continue
        completed += 1
        require(all(stage in names for stage in STAGES), "completed command lacks causal stage")
        selected = [events[names.index(stage)] for stage in STAGES]
        require([names.index(stage) for stage in STAGES] == sorted(names.index(stage) for stage in STAGES), "causal stage order invalid")
        require(all(a["at_s"] <= b["at_s"] for a, b in zip(selected, selected[1:])), "causal time order invalid")
        sent, received = selected[-3:-1]
        require(bool(sent["detail"]["classical_channel_ids"] and sent["detail"]["associated_quantum_ids"]), "paired classical/quantum channels absent")
        require(received["at_s"] >= sent["detail"]["arrival_at_s"] > sent["at_s"], "classical receipt violates propagation causality")
    require(completed == state["completed_commands"], "completed command count mismatch")
    db = contained(location, "protocol.sqlite")
    require_file(db, "persisted protocol database missing")
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as connection:
        stored = [loads(row[0]) for row in connection.execute("SELECT payload_json FROM technology_events ORDER BY sequence")]
        configurations = list(connection.execute("SELECT session_id,config_json FROM technology_configuration"))
        configured = {session_id: loads(config_json) for session_id, config_json in configurations}
        satellite_observations = {}
        for value, blob in connection.execute("SELECT sha256,observation_zlib FROM satellite_acquisitions"):
            decoder = zlib.decompressobj()
            decoded = decoder.decompress(blob, 64 * 1024 * 1024)
            require(decoder.eof and not decoder.unconsumed_tail, "satellite acquisition exceeds bounded decompression")
            satellite_observations[value] = hashlib.sha256(decoded).hexdigest()
    require(stored == rows and digest(configured) == digest({key: session["config"] for key, session in snapshot["sessions"].items()}),
            "SQLite/receipt/full-configuration mismatch")
    require(len(configurations) == len(configured) == len(snapshot["sessions"]), "duplicate persisted configuration identity")
    for session in snapshot["sessions"].values():
        require(session["metrics"].get("certified_secret_bits", 0) == 0, "certified material unexpectedly admitted")
    source = state["source"]
    require(source.get("origin") == "simulated", "scenario source not explicitly simulated")
    raw = contained(location, source["raw_path"])
    observations = [loads(line) for line in raw.read_text(encoding="utf-8").splitlines() if line]
    require(len(observations) == source["observations_recorded"], "raw observation count mismatch")
    raw_by_command, raw_hashes = {}, {}
    for observation in observations:
        require(observation.get("schema") == "orion.quantum-technology.v1" and observation.get("origin") == "simulated",
                "raw observation schema or provenance invalid")
        command_id = observation.get("command_id")
        require(isinstance(command_id, str) and command_id not in raw_by_command, "raw command identity missing or duplicated")
        require(command_id in grouped, "raw observation lacks actual acquisition command")
        raw_by_command[command_id] = observation
        raw_hashes[command_id] = digest(observation)
    for row in rows:
        if row["event"] != "acquired":
            continue
        command_id, detail = row["command_id"], row["detail"]
        observation = raw_by_command.get(command_id)
        require(observation is not None and detail.get("observation_sha256") == raw_hashes[command_id],
                "raw payload/bits/timestamps do not match acquired receipt hash")
        if "observation" in detail:
            require(digest(detail["observation"]) == raw_hashes[command_id], "inline acquisition differs from raw source")
        else:
            value = detail["observation_sha256"]
            require(detail.get("observation_reference") == "satellite_acquisitions:" + value
                    and satellite_observations.get(value) == value,
                    "durable satellite acquisition reference missing or changed")
    return {"receipt_count": len(rows), "completed_commands": completed, "observations": len(observations), "scientific_fingerprint": science}


def validate_pipeline(report, directory):
    require(set(report.get("checks", {})) == PIPELINE_CHECKS and all(value is True for value in report["checks"].values()), "pipeline requires all 49 named boolean checks")
    require(report.get("check_count") == len(PIPELINE_CHECKS), "pipeline check count mismatch")
    require(set(report.get("coverage", {}).get("technologies", [])) == TECHNOLOGIES, "five technology coverage missing")
    require(report.get("coverage", {}).get("frontend") == "snapshot_contract_only" and report.get("coverage", {}).get("hardware") is False,
            "bounded pipeline coverage mislabeled")
    require(report.get("scope") == "bounded_first_campaign" and report["coverage"].get("full_global_network") is False
            and report["coverage"].get("gui") is False, "bounded pipeline scope mislabeled")
    require(report.get("operator_state_accessed") is False and report.get("ml_control_enabled") is False and report.get("model_inference_invoked") is False,
            "operator state or optional ML boundary changed")
    scenarios = report.get("scenarios")
    require(isinstance(scenarios, dict) and set(scenarios) == {"nominal", "degraded", "unavailable", "recovery"}, "causal scenario coverage missing")
    details = {}
    for name, state in scenarios.items():
        require(set(state.get("checks", {})) == SCENARIO_CHECKS and all(v is True for v in state["checks"].values()), "scenario checks missing or nonboolean")
        require(state.get("physical_action_performed") is False, "scenario physical boundary missing")
        details[name] = scenario_evidence(directory, name, state)
    require(details["nominal"]["completed_commands"] > 0 and details["recovery"]["completed_commands"] > 0, "nominal/recovery processing absent")
    actual_technologies = {technology for session in scenarios["nominal"]["final_sessions"].values()
                           for technology in session.get("config", {}).get("technologies", [])}
    require(actual_technologies == TECHNOLOGIES and all(session["counters"].get("observations", 0) > 0
                                                      for session in scenarios["nominal"]["final_sessions"].values()),
            "all five configured technologies must receive observations")
    transmissions = []
    for session_id, state in scenarios["nominal"]["final_sessions"].items():
        baseline = state["metrics"].get("arm_transmissions", [])
        impaired = scenarios["degraded"]["final_sessions"].get(session_id, {}).get("metrics", {}).get("arm_transmissions", [])
        if baseline:
            require(len(baseline) == len(impaired), "degraded arm comparison missing")
            transmissions.extend((number(a), number(b)) for a, b in zip(baseline, impaired))
    require(bool(transmissions) and all(0 <= impaired <= baseline <= 1 for baseline, impaired in transmissions)
            and any(impaired < baseline for baseline, impaired in transmissions), "degradation did not lower measured transmission")
    require(details["unavailable"]["completed_commands"] == 0 and details["unavailable"]["observations"] == 0, "unavailable source fabricated success")
    require(scenarios["unavailable"]["event_counts"].get("operation_error", 0) > 0,
            "outage timeout/failure evidence missing")
    negatives = report.get("negative_cases", {})
    require(set(negatives) == set(FAULTS), "negative fault cases missing")
    for fault, code in FAULTS.items():
        case = negatives[fault]
        require(case.get("passed") is True and case.get("expected_code") == code and code in case.get("observed_codes", [])
                and case.get("completed_commands") == 0, "negative acquisition contract not evidenced")
    replay = report.get("replay", {})
    require(replay.get("physical_measurement") is False and replay.get("original_origin") == "simulated"
            and replay.get("replay_transport_origin") == "measurement_file"
            and replay.get("original_fingerprint") == replay.get("replay_fingerprint") and bool(replay.get("original_fingerprint")), "synthetic replay mismatch")
    repeat = report.get("reproducibility", {})
    require(repeat.get("first") == repeat.get("second") == scenarios["nominal"]["scientific_fingerprint"], "internal nominal repetition mismatch")
    outcome = report.get("communication", {}).get("end_to_end_entanglement", {})
    delivered = integer(outcome.get("delivered_pairs"), "invalid delivered pair count")
    rejected = integer(outcome.get("rejected_deliveries"), "invalid rejected pair count")
    require(delivered == scenarios["nominal"]["event_counts"].get("end_to_end_delivered", 0)
            and rejected == scenarios["nominal"]["event_counts"].get("delivery_rejected", 0), "communication totals mismatch actual receipts")
    require(outcome.get("demonstrated") is (delivered > 0) and len(outcome.get("rejection_evidence", [])) == rejected,
            "delivery and refusal confused")
    require(delivered > 0 or bool(outcome.get("impediment")), "undelivered final communication must retain impediment")
    normalized = {"seed": report["seed"], "duration_simulated_s": report["duration_simulated_s"],
                  "scenarios": {name: {"science": state["scientific_fingerprint"], "event_counts": state["event_counts"], "completed_commands": state["completed_commands"]}
                                for name, state in sorted(scenarios.items())},
                  "negative_cases": negatives, "replay": replay, "communication": report["communication"]}
    require(report.get("scientific_fingerprint_sha256") == digest(normalized), "pipeline scientific fingerprint mismatch")
    return {"passed": True, "check_count": len(PIPELINE_CHECKS), "scenarios": details,
            "delivered_pairs": delivered, "rejected_deliveries": rejected, "final_communication_demonstrated": delivered > 0,
            "scope": "bounded first campaign; frontend snapshot contract only"}


def validate_native(report, directory, manifest, root, protocol):
    require(report.get("measurement_completed") is True and report.get("source_unchanged") is True
            and report.get("platform") == "windows" and report.get("exit_code") == 0 and not report.get("errors"), "native measurement incomplete or errored")
    source_hashes(report, manifest, root, "native-ui", "source_hashes_start")
    source_hashes(report, manifest, root, "native-ui", "source_hashes_end")
    require(report["source_hashes_start"] == report["source_hashes_end"], "native source changed")
    entry = report.get("workspace_entry", {})
    require(entry.get("state") == "ready" and entry.get("authentication_bypassed") is False
            and entry.get("credentials_supplied_by_harness") is False and entry.get("method") == "current_windows_session", "native authentication not evidenced")
    phases = report.get("phases", {})
    require(set(phases) == {"map", "globe", "tabs", "swaps", "soak"}, "planned native phases missing")
    plan_path = contained(directory, "action-plan.json")
    plan = load_json(plan_path)
    require(sha(plan_path) == report.get("action_plan_sha256"), "native action plan identity mismatch")
    expected_plan_path = contained(root, protocol["native_action_plan"])
    require(sha(expected_plan_path) == manifest["native_action_plan_sha256"]
            and digest(plan) == digest(load_json(expected_plan_path)), "native targets/actions/order differ from prospectively frozen plan")
    require(set(plan.get("phases", {})) == set(phases) and plan.get("production_zoom_thresholds_modified") is False,
            "native action plan phases or operational thresholds changed")
    profile = protocol.get("run_profile", {})
    frozen_expectations = protocol["native_expectations"]
    require(plan.get("minimum_phase_duration_s") == profile.get("performance_duration_seconds", 15)
            and plan.get("soak_minutes") == profile.get("performance_soak_minutes", 3), "native workload duration differs from frozen profile")
    actions_path = contained(directory, "actions.json")
    require(actions_path.stat().st_size <= 64 * 1024 * 1024, "native action evidence too large")
    actions = loads(actions_path.read_text(encoding="utf-8-sig"))
    require(isinstance(actions, list), "native actions must be a list")
    actual_commands, target_readbacks = defaultdict(set), 0
    for action in actions:
        if "planned_command" not in action:
            continue
        phase, command = action.get("phase"), action["planned_command"]
        require(phase in phases and isinstance(command, dict), "unplanned native action")
        index = integer(command.get("index"), "invalid planned command index")
        require(index < len(plan["phases"][phase]) and index not in actual_commands[phase], "duplicated or invalid native planned command")
        require(command == plan["phases"][phase][index] and action.get("action") == command.get("action")
                and action.get("successful_dispatch") is True, "native action differs from plan or failed dispatch")
        actual_commands[phase].add(index)
        for field in ("mode_transition_matches", "page_navigation_matches", "marker_hit_matches_requested"):
            require(field not in action or action[field] is True, "native individual action mismatch")
        if "target" in command:
            target_readbacks += 1
            effect = action.get("functional_effect", {})
            presentation = effect.get("presentation_evidence", {})
            require(effect.get("content_complete_and_corresponds") is True and effect.get("timed_out") is False,
                    "native target readback incomplete")
            require(presentation.get("complete") is True and presentation.get("failed_checks") == []
                    and isinstance(presentation.get("checks"), dict) and bool(presentation["checks"])
                    and all(value is True for value in presentation["checks"].values()), "native received field checks incomplete")
    planned = 0
    for phase in phases.values():
        expected = integer(phase.get("planned_actions_expected"), "planned native action count missing")
        require(expected > 0 and integer(phase.get("planned_actions_completed"), "completed action count invalid") == expected
                and integer(phase.get("failed_dispatches"), "dispatch failure count invalid") == 0,
                "native planned actions incomplete")
        planned += expected
    for name, phase in phases.items():
        expected_count = max(1, math.ceil((plan["soak_minutes"] * 60 if name == "soak" else plan["minimum_phase_duration_s"]) / 2))
        require(len(plan["phases"][name]) == phase["planned_actions_expected"] == expected_count
                == frozen_expectations["planned_actions"][name]
                and actual_commands[name] == set(range(expected_count)), "native planned work omitted or actions missing")
    effects = report.get("functional_effect_checks", {})
    selections = integer(effects.get("selections"), "selection count missing")
    require(selections > 0 and effects.get("complete_content_readbacks") == selections == target_readbacks
            == frozen_expectations["selections"], "native selection content incomplete")
    for field in ("mismatches", "mode_transition_mismatches", "page_navigation_mismatches", "marker_hit_mismatches"):
        require(effects.get(field) == [], f"native {field} present or absent evidence")
    require(effects.get("page_navigations", 0) > 0 and effects.get("mode_transitions", 0) > 0
            and effects.get("actual_mode_changes", -1) >= effects.get("planned_mode_changes", 1) > 0, "native navigation not evidenced")
    for field in ("page_navigations", "planned_mode_changes", "mode_transitions"):
        require(integer(effects.get(field), "invalid native functional count") == frozen_expectations[field], "native navigation differs from frozen expectation")
    import csv
    path = contained(directory, "components.csv")
    require_file(path, "native renderer components missing")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    require(bool(rows), "native renderer evidence empty")
    observed_phases, modes = set(), set()
    for row in rows:
        instances, active = int(row["renderer_instances"]), int(row["active_renderers"])
        require(0 <= active <= instances <= 1, "duplicate or invalid renderer instances")
        require(int(row["created"]) - int(row["destroyed"]) == instances, "renderer lifecycle accounting mismatch")
        require(row["orbit_timer_active"] in {"True", "False"}, "invalid orbital timer state")
        require(row["mode"] != "map" or row["orbit_timer_active"] == "False", "inactive globe timer retained")
        if row["phase"] in phases:
            observed_phases.add(row["phase"])
            modes.add(row["mode"])
    require(observed_phases == set(phases) and modes >= {"map", "globe"}, "native renderer phases incomplete")
    observed = {name: {field: state.get(field) for field in ("heartbeat_ms", "action_to_all_fields_verified_ms", "stage_paint_ms", "process_tree_cpu_percent", "process_tree_rss_mib")}
                for name, state in phases.items()}
    return {"passed": True, "planned_actions": planned, "selection_readbacks": selections, "renderer_samples": len(rows),
            "observed_performance": observed, "performance_qualification": "not_demonstrated",
            "performance_policy": "No TRL5 threshold was applied, relaxed or replaced."}


def console_material_evidence(report, directory, root):
    spec = importlib.util.spec_from_file_location("orion_trl4_console_evidence", root / "tools/run_trl4_console_campaign.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_case_evidence(report, directory)


def validate_console(report, protocol, directory, root):
    cases = report.get("cases")
    require(isinstance(cases, list) and bool(cases), "console functional cases missing")
    ids = [case.get("id") for case in cases]
    require(len(ids) == len(set(ids)) and set(ids) == set(protocol["console_cases"]), "console planned case mismatch")
    require(report.get("qt_platform") == "windows" and report.get("source_unchanged") is True, "console native/source boundary absent")
    require(report.get("isolated_diagnostic") is True and report.get("frozen") is False
            and Path(report.get("state_root", "")).resolve() == (directory / "state").resolve(),
            "console diagnostic state root or source scope changed")
    require(report.get("console_cases") == protocol["console_cases"]
            and report.get("case_criteria") == protocol["console_case_criteria"], "console frozen case criteria mismatch")
    authentication = report.get("actual_authentication", {})
    require(authentication.get("method") == "current_windows_session" and authentication.get("backend_verified") is True
            and authentication.get("authentication_bypassed") is False and authentication.get("credentials_supplied") is False,
            "console backend authentication not evidenced")
    require(integer(authentication.get("request_count"), "invalid authentication request count") > 0, "console authentication request absent")
    require(report.get("hardware_validated") is False, "console hardware scope mislabeled")
    for case in cases:
        require(case.get("status") == "passed" and isinstance(case.get("checks"), dict) and bool(case["checks"])
                and all(value is True for value in case["checks"].values()), "console case incomplete or checks not boolean")
        require(set(case["checks"]) == set(protocol["console_case_criteria"][case["id"]]), "console per-case criteria missing or added")
        require(bool(case.get("evidence")), "console functional readback evidence missing")
    require(isinstance(report.get("checks"), dict) and bool(report["checks"])
            and set(report["checks"]) == set(protocol["console_cases"])
            and all(value is True for value in report["checks"].values()), "console global functional checks absent")
    before = load_json(contained(directory, "manifest-before.json"))
    require(before.get("schema") == report["schema"] and before.get("console_cases") == protocol["console_cases"]
            and before.get("case_criteria") == protocol["console_case_criteria"]
            and before.get("source_hashes") == report["source_hashes"] and before.get("physical_action_performed") is False,
            "console prospectively recorded manifest mismatch")
    artifacts = report.get("artifacts")
    require(isinstance(artifacts, dict) and bool(artifacts), "console material artifacts missing")
    for name, artifact in artifacts.items():
        require(sha(contained(directory, name)) == artifact.get("sha256"), "console artifact changed or missing")
    material = console_material_evidence(report, directory, root)
    require(material is True, "console material evidence was not verified")
    return {"passed": True, "case_count": len(cases), "case_ids": ids, "hardware_validated": False}


def assess(directory, root=ROOT):
    root, directory = Path(root).resolve(), Path(directory).resolve()
    require(directory.is_relative_to(root), "evidence directory outside workspace")
    manifest = load_json(directory / "freeze.json")
    protocol = load_json(directory / "protocol.json")
    require(manifest.get("schema") == "orionq.trl4-freeze.v1", "unsupported freeze manifest")
    failures, evidence, reports = [], {}, {}
    try:
        validate_protocol(protocol, root)
        require(sha(directory / "protocol.json") == manifest["protocol_sha256"] == sha(contained(root, PROTOCOL)), "frozen protocol changed")
        require(manifest.get("protocol_id") == protocol["protocol_id"] and manifest.get("revision") == protocol["revision"]
                and manifest.get("seed") == protocol["seed"] and manifest.get("target_scope") == protocol["target_scope"], "freeze protocol identity mismatch")
        require(sha(directory / "source-before.json") == manifest["source_before_sha256"], "source-before manifest altered")
        require(load_json(directory / "source-before.json") == manifest["source_file_hashes"]
                and digest(manifest["source_file_hashes"]) == manifest["source_manifest_sha256"], "frozen source manifest mismatch")
        require(collected_cases(protocol, root) == manifest["test_cases"], "frozen testcase universe altered")
        require(sha(directory / "analytic-acceptance.json") == manifest["analytic_acceptance_sha256"], "frozen analytic acceptance changed")
        acceptance = load_json(directory / "analytic-acceptance.json")
        known = manifest["known_trl5_failure"]
        require(known["path"] == protocol["known_trl5_failure"] and sha(contained(root, known["path"])) == known["sha256"], "known TRL5 evidence changed")
        after = source_inventory(root, protocol)
        require(after == manifest["source_file_hashes"], "source added, removed or changed after freeze")
        require(expected_report_sources(root, protocol, after) == manifest["expected_report_sources"], "frozen producer source universes changed")
        write_new(directory / "source-after.json", after)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        failures.append(f"freeze: {exc}")
        acceptance = {}
    if not failures:
        for label, name in protocol["reports"].items():
            try:
                path = contained(directory, name)
                require_file(path, f"missing report {label}")
                if label.startswith("group-"):
                    result = junit_cases(path, manifest["test_cases"][label[6:]])
                else:
                    report = load_json(path)
                    require(report.get("schema") == REPORT_SCHEMAS[label], "unexpected report schema")
                    require(report.get("physical_action_performed") is False, "physical action boundary not evidenced")
                    require((label == "native-ui" or report.get("passed") is True) and not report.get("errors"), "report contains failures or errors")
                    if label == "native-ui":
                        result = validate_native(report, path.parent, manifest, root, protocol)
                    else:
                        kind = label.split("-")[0] if label.startswith(("reference", "pipeline")) else label
                        source_hashes(report, manifest, root, kind)
                        if label.startswith(("reference", "pipeline")):
                            require(report.get("seed") == manifest["seed"], "declared seed mismatch")
                        if label.startswith("reference"):
                            result = validate_reference(report, acceptance)
                        elif label.startswith("pipeline"):
                            result = validate_pipeline(report, path.parent)
                        else:
                            source_hashes(report, manifest, root, "console", "source_hashes_end")
                            require(report["source_hashes"] == report["source_hashes_end"], "console source changed during campaign")
                            result = validate_console(report, protocol, path.parent, root)
                    reports[label] = report
                evidence[label] = {**result, "status": "pass", "path": name, "sha256": sha(path)}
            except (ValueError, KeyError, TypeError, OSError, ET.ParseError, sqlite3.Error, zlib.error) as exc:
                failures.append(f"{label}: {exc}")
                evidence[label] = {"passed": False, "status": "not_demonstrated" if isinstance(exc, FileNotFoundError) else "fail", "path": name, "error": str(exc)}
        for kind in ("reference", "pipeline"):
            names = [f"{kind}-1", f"{kind}-2"]
            equal = all(name in reports for name in names) and reports[names[0]].get("scientific_fingerprint_sha256") == reports[names[1]].get("scientific_fingerprint_sha256")
            if not equal:
                failures.append(f"{kind}: scientific repetition mismatch or missing report")
            evidence[f"{kind}-repetition"] = {"passed": bool(equal)}
    modules = Counter()
    for item in evidence.values():
        if item.get("passed") is True:
            modules.update(item.get("module_counts", {}))
    requirements = []
    for requirement in protocol.get("requirements", []):
        covered = all(modules.get(name, 0) > 0 for name in requirement["test_files"])
        campaigns = {kind: all(evidence.get(name, {}).get("passed") is True for name in
                               ([f"{kind}-1", f"{kind}-2", f"{kind}-repetition"] if kind in {"reference", "pipeline"} else [kind]))
                     for kind in requirement.get("campaigns", [])}
        required_groups = {f"group-{group}" for group, paths in protocol["test_groups"].items()
                           if set(paths) & set(requirement["test_files"])}
        needed_campaigns = {name for kind in requirement.get("campaigns", []) for name in
                            ([f"{kind}-1", f"{kind}-2", f"{kind}-repetition"] if kind in {"reference", "pipeline"} else [kind])}
        explicit_failure = any(item.startswith("freeze:") for item in failures) or any(
            evidence.get(name, {}).get("status") == "fail" for name in required_groups | needed_campaigns)
        explicit_failure |= any(kind in requirement.get("campaigns", []) and evidence.get(f"{kind}-repetition", {}).get("passed") is False
                                and all(f"{kind}-{repeat}" in reports for repeat in (1, 2)) for kind in ("reference", "pipeline"))
        ready = not explicit_failure and covered and all(campaigns.values())
        requirements.append({"id": requirement["id"], "label": requirement["label"],
                             "functional_status": "pass" if ready else "fail" if explicit_failure else "not_demonstrated",
                             "module_testcase_counts": {name: modules.get(name, 0) for name in requirement["test_files"]},
                             "campaigns": campaigns, "limitations": requirement["limitations"]})
    complete = not failures and bool(requirements) and all(r["functional_status"] == "pass" for r in requirements)
    result = {"schema": "orionq.trl4-software-assessment.v1", "protocol_id": protocol.get("protocol_id"),
              "target_scope": "SOFT-ALPHA", "functional_package_status": "complete_for_review" if complete else "incomplete",
              "assessment": "pending_external_review", "trl4_assigned": False, "human_review": "pending",
              "physical_action_performed": False, "hardware_qualified": False, "passed": bool(complete),
              "errors": failures, "requirements": requirements, "evidence": evidence,
              "executed_testcase_count": sum(modules.values()), "source_unchanged": not any(f.startswith("freeze:") for f in failures),
              "validated_passed_testcase_count": sum(modules.values()),
              "testcase_count_scope": "Only physical testcase elements in wholly validated passing groups; invalid groups are not counted as approved execution.",
              "known_trl5_failure": manifest.get("known_trl5_failure"),
              "performance_qualification": "not_demonstrated", "environment": environment(),
              "limitations": ["TRL assignment requires independent human review; this tool does not certify a TRL.",
                              "The known TRL5 failure remains a separate, unchanged assessment.",
                              "Performance observations do not relax or replace any TRL5 thresholds.",
                              "Synthetic observations and fixtures do not qualify connected hardware or secure QKD generation.",
                              "Integrity hashes do not establish authenticated authorship or resist whole-package replacement."]}
    write_new(directory / "assessment.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "assess"))
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare(args.directory) if args.action == "prepare" else assess(args.directory)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({"schema": "orionq.trl4-tool-error.v1", "error": str(exc), "trl4_assigned": False}))
        return 2
    print(json.dumps({key: result[key] for key in ("schema", "functional_package_status", "passed", "trl4_assigned") if key in result}))
    return 0 if args.action == "prepare" or result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
