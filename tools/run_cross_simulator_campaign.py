"""Prospective, isolated comparison with real simulator processes, never TRL admission.

No fallback impersonates an unavailable simulator. Deterministic state checks
and simultaneous statistical equivalence are separate evidence classes.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL = ROOT / "configs/qualification/cross_simulators_v1.json"
SOURCE_FILES = (
    "tools/run_cross_simulator_campaign.py", "tools/cross_validate_sequence_backend.py",
    "tools/cross_validate_netsquid_backend.py", "tests/test_cross_simulator_campaign.py",
    "tests/test_cross_validate_sequence_backend.py", "tests/test_cross_validate_netsquid_backend.py",
    "configs/qualification/cross_simulators_v1.json", "docs/product/orionq_cross_simulators.md",
)
METRICS = {
    "transmission": ("transmission_probability",), "memory": ("fidelity",),
    "swapping": ("fidelity",), "purification": ("fidelity", "success_probability"),
    "classical_delay": ("delay_s",),
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def load(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def write(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def finite(value, name, low=None, high=None):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name}: finite number required")
    if low is not None and value < low or high is not None and value > high:
        raise ValueError(f"{name}: outside supported range")
    return float(value)


def protocol_validate(plan):
    # Both runners validate every input before importing their frameworks.
    from cross_validate_sequence_backend import validate_payload
    from cross_validate_netsquid_backend import validate_request
    if plan.get("schema") != "orionq.cross-simulator-protocol.v1":
        raise ValueError("unsupported protocol")
    seeds = plan.get("seeds")
    if not isinstance(seeds, list) or len(seeds) < 2 or len(set(seeds)) != len(seeds):
        raise ValueError("distinct repeated seeds required")
    if type(plan.get("trials_per_seed")) is not int or plan["trials_per_seed"] < 1:
        raise ValueError("positive trials required")
    for seed in seeds:
        request = payload(plan, seed)
        validate_payload(request)
        validate_request(request)
    kinds = [case["kind"] for case in plan["cases"]]
    if set(kinds) != set(METRICS):
        raise ValueError("declared five component types required")
    proportions = sum(kind in {"transmission", "purification"} for kind in kinds)
    if type(plan.get("statistical_family_size")) is not int or plan["statistical_family_size"] < proportions * 2:
        raise ValueError("family size must cover both simulators, including missing backend")
    finite(plan["statistical_family_alpha"], "family alpha", 1e-12, .05)
    finite(plan["deterministic_absolute_tolerance"], "deterministic tolerance", 1e-15, 1e-6)
    finite(plan["numerical_domain_roundoff"], "domain roundoff", 0, 1e-12)
    finite(plan["time_absolute_tolerance_s"], "time tolerance", 1e-15, 1e-6)
    finite(plan["default_equivalence_margin"], "equivalence margin", 1e-6, .1)
    ids = {case["id"] for case in plan["cases"]}
    if set(plan.get("equivalence_margins", {})) - ids:
        raise ValueError("margin for undeclared case")
    for margin in plan.get("equivalence_margins", {}).values():
        finite(margin, "equivalence margin", 1e-6, .1)
    for case in plan["cases"]:
        p = case["parameters"]
        if case["kind"] in {"swapping", "purification"} and p["operation_error"] != 0:
            raise ValueError("this protocol admits only equivalent ideal local operations")
        if case["kind"] == "purification" and min(p["first"], p["second"]) <= .5:
            raise ValueError("purification admission requires both input fidelities above 0.5")
        if case["kind"] == "classical_delay" and not math.isclose(p["light_speed_km_s"], 299792.458/1.468,
                                                                rel_tol=0, abs_tol=1e-9):
            raise ValueError("delay cases must match the runtime fiber index")


def payload(plan, seed):
    cases = []
    for case in plan["cases"]:
        parameters = dict(case["parameters"])
        if case["kind"] in {"transmission", "purification"}:
            parameters["trials"] = plan["trials_per_seed"]
        cases.append({"id": case["id"], "kind": case["kind"], "parameters": parameters})
    return {"schema": "orionq.cross-simulator-input.v1", "seed": seed, "cases": cases}


def source_inventory(root=ROOT):
    names = set(SOURCE_FILES) | {"pyproject.toml"}
    names.update(path.relative_to(root).as_posix() for path in (root / "src").rglob("*")
                 if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"})
    return {name: sha(root / name) for name in sorted(names)}


def prepare(directory, protocol=DEFAULT_PROTOCOL):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT):
        raise ValueError("evidence must stay inside workspace")
    plan = load(protocol)
    protocol_validate(plan)
    before = source_inventory()
    directory.mkdir(parents=True, exist_ok=False)
    write(directory / "protocol.json", plan)
    requests = {}
    for seed in plan["seeds"]:
        name = f"input-{seed}.json"
        write(directory / name, payload(plan, seed))
        requests[name] = sha(directory / name)
    freeze = {"schema": "orionq.cross-simulator-freeze.v1", "protocol_id": plan["protocol_id"],
              "created_utc": datetime.now(timezone.utc).isoformat(), "protocol_sha256": sha(directory/"protocol.json"),
              "source_hashes": before, "source_manifest_sha256": digest(before), "requests": requests,
              "criteria_defined_before_execution": True, "physical_action_performed": False,
              "orion_python": sys.version, "platform": platform.platform()}
    if before != source_inventory():
        raise ValueError("sources changed during freeze")
    write(directory / "freeze.json", freeze)
    return freeze


def _runtime_delay(parameters, directory):
    """Observe real runtime intake -> classical receipt in isolated simulation state."""
    from quantum_autopilot.network_engine.institutional import brazil_institutions
    from quantum_autopilot.quantum_protocols.topology import expand_global
    from quantum_autopilot.quantum_protocols.runtime import ProtocolRuntime
    from quantum_autopilot.quantum_protocols.sources import SimulatedAcquisitionSource
    topology = expand_global(brazil_institutions())[0]
    selected = None
    nodes = []
    for node in topology.nodes:
        sessions = []
        for config in node.telemetry.get("technology_sessions", ()):
            if config["session_id"] == "RIO-ENTANGLEMENT":
                config = {**config, "parameters": {**config["parameters"], "route_factor": 1.,
                          "classical_latency_s": 0., "classical_jitter_s": 0.}, "seed": 2026101000}
                selected = config
                sessions.append(config)
        nodes.append(replace(node, telemetry={**node.telemetry, "technology_sessions": sessions}))
    if selected is None:
        raise ValueError("runtime fixture session missing")
    links = tuple(replace(link, distance_km=parameters["distance_km"]/len(selected["link_ids"]))
                  if link.link_id in selected["link_ids"] else link for link in topology.links)
    topology = replace(topology, nodes=tuple(nodes), links=links)
    directory.mkdir(parents=True, exist_ok=False)
    runtime = ProtocolRuntime(directory/"journal.sqlite", topology.topology_id, source=SimulatedAcquisitionSource())
    try:
        runtime.advance(topology, 1.)
        event_path = directory / "events.jsonl"
        runtime.export_events(event_path)
        events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
        for event in events:
            if event["event"] != "classical_transmitted":
                continue
            command = event["command_id"]
            received = next((row for row in events if row["command_id"] == command and row["event"] == "classical_received"), None)
            if received is not None:
                delay = received["at_s"] - event["at_s"]
                if delay < 0 or abs(received["at_s"] - event["detail"]["arrival_at_s"]) > 2e-12:
                    raise ValueError("runtime classical causality violated")
                return {"delay_s": delay}, {"source": "ProtocolRuntime._intake and received receipt",
                    "command_id": command, "sent_at_s": event["at_s"], "received_at_s": received["at_s"],
                    "events_sha256": sha(event_path), "scope": "Configured transit duration; absolute epoch differs"}
        raise ValueError("no completed real runtime classical receipt")
    finally:
        close = getattr(runtime, "close", None)
        if close:
            close()


def orion_metrics(case, directory):
    from quantum_autopilot.quantum_protocols import physics
    from quantum_autopilot.quantum_protocols.protocols import MemoryAdapter, SwappingAdapter, PurificationAdapter
    p, kind = case["parameters"], case["kind"]
    provenance = {"scope": "Production reduced kernel and observation processing adapter; not full resource scheduling"}
    if kind == "transmission":
        metrics = {"transmission_probability": physics.transmission(p["length_km"], p["attenuation_db_km"], p["insertion_db"])}
    elif kind == "memory":
        result = MemoryAdapter().process({"readout": 1}, {"fidelity": p["fidelity"], "elapsed_s": p["elapsed_s"], "coherence_s": p["coherence_s"]})
        metrics = {"fidelity": result["fidelity"]}
    elif kind in {"swapping", "purification"}:
        context = {"operation": "bsm", "first_fidelity": p["first"], "second_fidelity": p["second"],
                   "operation_error": p["operation_error"], "parameters": {"bsm_kind": "abstract_heralded"}}
        if kind == "swapping":
            result = SwappingAdapter().process({"bsm_success": 1, "bell_outcome": 2}, context)
            metrics = {"fidelity": result["fidelity"]}
        else:
            result = PurificationAdapter().process({"alice_outcome": 0, "bob_outcome": 0}, context)
            metrics = {name: result[name] for name in METRICS[kind]}
    else:
        metrics, provenance = _runtime_delay(p, directory)
    return {"id": case["id"], "kind": kind, "metrics": metrics, "provenance": provenance}


def interval(successes, trials, alpha):
    from scipy.stats import beta
    if type(successes) is not int or type(trials) is not int or not 0 <= successes <= trials or trials < 1:
        raise ValueError("invalid observed event counts")
    return [0. if successes == 0 else float(beta.ppf(alpha/2, successes, trials-successes+1)),
            1. if successes == trials else float(beta.ppf(1-alpha/2, successes+1, trials-successes))]


def validate_result(report, backend, request, domain_roundoff=1e-12):
    if report.get("schema") != "orionq.cross-simulator-output.v1" or report.get("backend") != backend:
        raise ValueError("backend/schema mismatch")
    metadata = report.get("metadata", {})
    if backend == "sequence":
        if report.get("seed") != request["seed"] or metadata.get("commit") != "3133c66d80a898c6c3cc9af9eec3fa94fdbe2233":
            raise ValueError("SeQUeNCe seed/commit mismatch")
        if not metadata.get("source_sha256") or metadata.get("shims") != [] or metadata.get("product_physics_imported") is not False:
            raise ValueError("independent SeQUeNCe source provenance missing")
    elif metadata.get("seed") != request["seed"] or metadata.get("request_sha256") != digest(request):
        raise ValueError("NetSquid request/seed mismatch")
    rows = report.get("cases")
    if not isinstance(rows, list) or len(rows) != len(request["cases"]):
        raise ValueError("backend case universe missing")
    expected = {case["id"]: case for case in request["cases"]}
    if len({row.get("id") for row in rows}) != len(rows) or {row.get("id") for row in rows} != set(expected):
        raise ValueError("backend case IDs duplicated or missing")
    for row in rows:
        case = expected[row["id"]]
        if backend == "sequence" and row.get("parameters") != case["parameters"]:
            raise ValueError("SeQUeNCe parameter binding mismatch")
        if row.get("kind") != case["kind"] or row.get("status") not in {"pass", "not_demonstrated", "error"}:
            raise ValueError("invalid case status/kind")
        if row["status"] != "pass":
            if row.get("metrics") or row.get("events"):
                raise ValueError("unexecuted case must not contain scientific results")
            continue
        if not isinstance(row.get("events"), list) or not row["events"]:
            raise ValueError("native evidence missing")
        for metric in METRICS[case["kind"]]:
            low, high = (0, None) if metric == "delay_s" else (-domain_roundoff, 1+domain_roundoff)
            finite(row["metrics"].get(metric), metric, low, high)
            accepted_units = {"s"} if metric == "delay_s" else {"1", "probability", "fidelity", "dimensionless"}
            if row.get("units", {}).get(metric) not in accepted_units:
                raise ValueError("metric units incompatible")
        if case["kind"] in {"transmission", "purification"}:
            counter = "transmitted" if case["kind"] == "transmission" else "accepted"
            observed, trials = row["metrics"].get(counter), row["metrics"].get("trials")
            if type(observed) is not int or type(trials) is not int or not 0 <= observed <= trials or trials != case["parameters"]["trials"]:
                raise ValueError("invalid/missing native counts")
        if case["kind"] == "classical_delay" and row["metrics"].get("received_before_arrival", 0) != 0:
            raise ValueError("classical receive before causal arrival")


def compare(plan, backend, reports, orion):
    if len(reports) != len(plan["seeds"]):
        raise ValueError("all prescribed repetitions required")
    for seed, report in zip(plan["seeds"], reports):
        validate_result(report, backend, payload(plan, seed), plan["numerical_domain_roundoff"])
    originals = {row["id"]: row for row in orion}
    if set(originals) != {case["id"] for case in plan["cases"]} or len(originals) != len(orion):
        raise ValueError("OrionQ case universe mismatch")
    indexes = [{row["id"]: row for row in report["cases"]} for report in reports]
    results = []
    for case in plan["cases"]:
        rows = [index[case["id"]] for index in indexes]
        errors = [row.get("reason", "native error") for row in rows if row["status"] == "error"]
        status = "error" if errors else "not_demonstrated" if any(row["status"] != "pass" for row in rows) else "pass"
        result = {"id": case["id"], "kind": case["kind"], "status": status,
                  "deterministic_checks": [], "statistical_equivalence": None,
                  "limitations": sorted({limit for row in rows for limit in row.get("limitations", [])})}
        if status != "pass":
            result["reasons"] = [row.get("reason", row["status"]) for row in rows if row["status"] != "pass"]
            results.append(result)
            continue
        for name in METRICS[case["kind"]]:
            if backend == "netsquid" and name in {"transmission_probability", "success_probability"}:
                # Native NetSquid publishes observed proportions, not exact
                # channel/branch probabilities. Assess only by the frozen CI.
                continue
            expected = finite(originals[case["id"]]["metrics"][name], name)
            tolerance = plan["time_absolute_tolerance_s"] if name == "delay_s" else plan["deterministic_absolute_tolerance"]
            differences = [abs(row["metrics"][name]-expected) for row in rows]
            passed = all(value <= tolerance for value in differences)
            result["deterministic_checks"].append({"metric": name, "orion": expected,
                "reference": [row["metrics"][name] for row in rows], "maximum_absolute_error": max(differences),
                "tolerance": tolerance, "passed": passed})
            if not passed:
                result["status"] = "fail"
        if case["kind"] in {"transmission", "purification"}:
            counter = "transmitted" if case["kind"] == "transmission" else "accepted"
            probability = "transmission_probability" if case["kind"] == "transmission" else "success_probability"
            successes = sum(row["metrics"][counter] for row in rows)
            trials = sum(row["metrics"]["trials"] for row in rows)
            alpha = plan["statistical_family_alpha"]/plan["statistical_family_size"]
            bounds = interval(successes, trials, alpha)
            expected = originals[case["id"]]["metrics"][probability]
            margin = plan.get("equivalence_margins", {}).get(case["id"], plan["default_equivalence_margin"])
            target = [max(0., expected-margin), min(1., expected+margin)]
            passed = target[0] <= bounds[0] and bounds[1] <= target[1]
            if expected in (0., 1.):
                passed = passed and successes == int(expected*trials)
            result["statistical_equivalence"] = {"successes": successes, "trials": trials,
                "empirical_probability": successes/trials, "expected_probability": expected,
                "simultaneous_interval": bounds, "equivalence_interval": target, "margin": margin,
                "family_alpha": plan["statistical_family_alpha"], "family_size": plan["statistical_family_size"],
                "per_interval_alpha": alpha, "passed": passed}
            if not passed:
                result["status"] = "fail"
        results.append(result)
    status = "fail" if any(row["status"] in {"fail", "error"} for row in results) else "not_demonstrated" if any(row["status"] != "pass" for row in results) else "pass"
    return {"backend": backend, "status": status, "cases": results,
            "passed_cases": sum(row["status"] == "pass" for row in results)}


def run(directory, sequence_python, netsquid_python, sequence_src):
    directory = Path(directory).resolve()
    freeze = load(directory/"freeze.json")
    plan = load(directory/"protocol.json")
    protocol_validate(plan)
    if sha(directory/"protocol.json") != freeze["protocol_sha256"] or source_inventory() != freeze["source_hashes"]:
        raise ValueError("frozen source or criteria changed")
    for name, checksum in freeze["requests"].items():
        if sha(directory/name) != checksum:
            raise ValueError("input changed after freeze")
    orion = [orion_metrics(case, directory/f"orion-{case['id']}") for case in plan["cases"]]
    write(directory/"orion.json", orion)
    executions, comparisons = [], []
    for backend, interpreter in (("sequence", sequence_python), ("netsquid", netsquid_python)):
        reports = []
        for seed in plan["seeds"]:
            output = directory/f"{backend}-{seed}.json"
            command = [str(Path(interpreter).resolve()), str(ROOT/f"tools/cross_validate_{backend}_backend.py"),
                       "--input", str(directory/f"input-{seed}.json"), "--output", str(output)]
            if backend == "sequence":
                command += ["--sequence-src", str(Path(sequence_src).resolve())]
            start = time.perf_counter()
            with (directory/f"{backend}-{seed}.stdout.log").open("x", encoding="utf-8") as stdout, (directory/f"{backend}-{seed}.stderr.log").open("x", encoding="utf-8") as stderr:
                try:
                    result = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr,
                                            timeout=plan["backend_timeout_s"], check=False)
                    returncode, failure = result.returncode, None
                except (OSError, subprocess.TimeoutExpired) as exc:
                    returncode, failure = None, type(exc).__name__
            record = {"backend": backend, "seed": seed, "command": command, "returncode": returncode,
                      "failure": failure, "elapsed_s": time.perf_counter()-start,
                      "input_sha256": sha(directory/f"input-{seed}.json"),
                      "output_sha256": sha(output) if output.exists() else None}
            executions.append(record)
            write(directory/f"{backend}-{seed}.execution.json", record)
            if not output.exists():
                raise ValueError(f"{backend}: missing native result, inspect preserved logs")
            report = load(output)
            validate_result(report, backend, payload(plan, seed), plan["numerical_domain_roundoff"])
            if returncode not in (0, 1) or returncode == 1 and all(row["status"] == "pass" for row in report["cases"]):
                raise ValueError("backend execution failed despite claimed complete results")
            reports.append(report)
        if backend == "sequence" and any(item["metadata"]["source_sha256"] != reports[0]["metadata"]["source_sha256"] for item in reports):
            raise ValueError("SeQUeNCe sources changed between repetitions")
        comparisons.append(compare(plan, backend, reports, orion))
    unchanged = source_inventory() == freeze["source_hashes"]
    report = {"schema": "orionq.cross-simulator-campaign.v1", "protocol_id": plan["protocol_id"],
              "source_unchanged": unchanged, "source_manifest_sha256": freeze["source_manifest_sha256"],
              "backends": comparisons, "executions": executions, "orion_evidence_sha256": sha(directory/"orion.json"),
              "complete_dual_backend_validation": unchanged and all(row["status"] == "pass" for row in comparisons),
              "physical_action_performed": False, "hardware_qualified": False, "trl_assigned": False,
              "certified_secret_bits": 0, "limitations": plan["limitations"]}
    write(directory/"report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    preparation = sub.add_parser("prepare")
    preparation.add_argument("--output", required=True, type=Path)
    preparation.add_argument("--protocol", default=DEFAULT_PROTOCOL, type=Path)
    execution = sub.add_parser("run")
    execution.add_argument("--output", required=True, type=Path)
    execution.add_argument("--sequence-python", required=True, type=Path)
    execution.add_argument("--sequence-src", default=Path(os.environ.get('SEQUENCE_SRC', str(Path(__file__).resolve().parents[2] / 'dependencies' / 'SeQUeNCe'))), type=Path)
    execution.add_argument("--netsquid-python", default=Path(sys.executable), type=Path)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        result = prepare(args.output, args.protocol)
        print(canonical({"prepared": True, "source_manifest_sha256": result["source_manifest_sha256"]}))
        return 0
    result = run(args.output, args.sequence_python, args.netsquid_python, args.sequence_src)
    print(canonical({"backends": [{"backend": row["backend"], "status": row["status"], "passed_cases": row["passed_cases"]}
                                for row in result["backends"]], "source_unchanged": result["source_unchanged"],
                     "complete_dual_backend_validation": result["complete_dual_backend_validation"]}))
    return 0 if result["complete_dual_backend_validation"] else 2


if __name__ == "__main__":
    sys.exit(main())
