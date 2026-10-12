"""Adversarial assessment fixtures; no UI, SDK, hardware or production campaign."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import xml.etree.ElementTree as ET
import zlib

import pytest

SPEC = importlib.util.spec_from_file_location("trl4_assessment_test", Path(__file__).resolve().parents[1] / "tools/assess_trl4_software.py")
assessment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assessment)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def reference(frozen, source):
    checks = [{"name": name, **fields, "actual": fields["expected"], "absolute_error": 0.0, "passed": True}
              for name, fields in frozen["checks"].items()]
    report = {"schema": assessment.REPORT_SCHEMAS["reference-1"], "seed": 2026100900, "passed": True,
              "checks": checks, "source_hashes": source, "physical_action_performed": False,
              "classification": "analytic_verification", "experimental_calibration": "not_demonstrated",
              "acquisition_summaries": [], "experimental_reference_inventory": []}
    report["scientific_fingerprint_sha256"] = assessment.digest({key: report[key] for key in
                                                                ("seed", "checks", "acquisition_summaries", "experimental_reference_inventory")}, ascii=True)
    return report


def scenario(root, name):
    location = root / name
    location.mkdir(parents=True)
    unavailable = name == "unavailable"
    technologies = {"ENT": ["entanglement_swapping", "quantum_memory", "purification"], "TF": ["tf_qkd"], "SAT": ["satellite_doppler"]}
    sessions = {key: {"status": "unavailable" if unavailable else "operational", "counters": {"observations": 0 if unavailable else 1},
                      "metrics": {"certified_secret_bits": 0, "arm_transmissions": [0.01 if name == "degraded" else 0.2]},
                      "config": {"technologies": value}} for key, value in technologies.items()}
    snapshot = {"schema": "orion.technologies-live.v1", "physical_actuation": False, "backlog_before_clock": 0, "sessions": sessions}
    write(location / "snapshot.json", snapshot)
    science = assessment.digest({key: {field: value[field] for field in ("status", "counters", "metrics")}
                                 for key, value in sorted(sessions.items())})
    rows = []
    observations, satellite_observations = [], []

    def event(kind, command, at_s, detail=None):
        row = {"schema": "orion.technology-event.v1", "physical_actuation": False, "event": kind,
               "command_id": command, "at_s": at_s, "detail": detail or {}, "previous_sha256": rows[-1]["receipt_sha256"] if rows else ""}
        row["receipt_sha256"] = assessment.digest(row)
        rows.append(row)

    for key in sessions:
        event("configured", None, 0.0)
        if unavailable:
            event("acquisition_requested", key, 0.0)
            event("operation_error", key, 3.0, {"code": "timeout"})
            continue
        observation = {"schema": "orion.quantum-technology.v1", "origin": "simulated", "command_id": key,
                       "session_id": key, "sequence": 1, "source_id": "synthetic-test-source", "observed_at_s": 1.0,
                       "operation": "generate", "quantum_channel_ids": ["q1"], "classical_channel_ids": ["c1"],
                       "raw": {"bits": [0, 1], "time_ps": [100, 200]}}
        observation_hash = assessment.digest(observation)
        observations.append(observation)
        for index, stage in enumerate(assessment.STAGES):
            detail = {"classical_channel_ids": ["c1"], "associated_quantum_ids": ["q1"], "arrival_at_s": 5.5} if stage == "classical_transmitted" else {}
            if stage == "acquired":
                detail = {"observation": observation, "observation_sha256": observation_hash}
                if key == "SAT":
                    detail = {"observation_sha256": observation_hash, "observation_reference": "satellite_acquisitions:" + observation_hash}
                    satellite_observations.append((observation_hash, zlib.compress(json.dumps(observation, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())))
            event(stage, key, float(index + 1), detail)
    if name == "nominal":
        event("end_to_end_delivered", "ENT", 8.0)
    (location / "receipts.jsonl").write_text("\n".join(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    (location / "raw-source-simulated.jsonl").write_text("\n".join(json.dumps(row) for row in observations) + ("\n" if observations else ""), encoding="utf-8")
    with sqlite3.connect(location / "protocol.sqlite") as connection:
        connection.execute("CREATE TABLE technology_events(sequence INTEGER PRIMARY KEY, payload_json TEXT)")
        connection.execute("CREATE TABLE technology_configuration(session_id TEXT PRIMARY KEY, config_json TEXT)")
        connection.execute("CREATE TABLE satellite_acquisitions(sha256 TEXT PRIMARY KEY, observation_zlib BLOB)")
        connection.executemany("INSERT INTO technology_events VALUES (?,?)", [(i, json.dumps(row)) for i, row in enumerate(rows)])
        connection.executemany("INSERT INTO technology_configuration VALUES (?,?)", [(key, json.dumps(state["config"])) for key, state in sessions.items()])
        connection.executemany("INSERT INTO satellite_acquisitions VALUES (?,?)", satellite_observations)
    from collections import Counter
    return {"checks": dict.fromkeys(assessment.SCENARIO_CHECKS, True), "snapshot_path": "snapshot.json", "final_sessions": sessions,
            "scientific_fingerprint": science, "receipt_count": len(rows), "event_counts": dict(Counter(row["event"] for row in rows)),
            "completed_commands": 0 if unavailable else len(sessions), "physical_action_performed": False,
            "source": {"origin": "simulated", "raw_path": "raw-source-simulated.jsonl", "observations_recorded": len(observations)}}


def pipeline(root, source):
    scenarios = {name: scenario(root, name) for name in ("nominal", "degraded", "unavailable", "recovery")}
    report = {"schema": assessment.REPORT_SCHEMAS["pipeline-1"], "seed": 2026100900, "passed": True, "errors": [],
              "checks": dict.fromkeys(assessment.PIPELINE_CHECKS, True), "check_count": 49, "source_hashes": source,
              "physical_action_performed": False, "operator_state_accessed": False, "ml_control_enabled": False, "model_inference_invoked": False,
              "scope": "bounded_first_campaign",
              "coverage": {"technologies": sorted(assessment.TECHNOLOGIES), "frontend": "snapshot_contract_only", "hardware": False,
                           "full_global_network": False, "gui": False},
              "scenarios": scenarios, "duration_simulated_s": 35,
              "negative_cases": {fault: {"passed": True, "expected_code": code, "observed_codes": [code], "completed_commands": 0} for fault, code in assessment.FAULTS.items()},
              "replay": {"physical_measurement": False, "original_origin": "simulated", "replay_transport_origin": "measurement_file", "original_fingerprint": "a", "replay_fingerprint": "a"},
              "reproducibility": {"first": scenarios["nominal"]["scientific_fingerprint"], "second": scenarios["nominal"]["scientific_fingerprint"]},
              "communication": {"end_to_end_entanglement": {"delivered_pairs": 1, "rejected_deliveries": 0, "demonstrated": True, "impediment": None, "rejection_evidence": []}}}
    normalized = {"seed": report["seed"], "duration_simulated_s": report["duration_simulated_s"],
                  "scenarios": {name: {"science": state["scientific_fingerprint"], "event_counts": state["event_counts"], "completed_commands": state["completed_commands"]}
                                for name, state in sorted(scenarios.items())},
                  "negative_cases": report["negative_cases"], "replay": report["replay"], "communication": report["communication"]}
    report["scientific_fingerprint_sha256"] = assessment.digest(normalized)
    return report


def native(root, source):
    root.mkdir()
    phases = {name: {"planned_actions_expected": 2, "planned_actions_completed": 2, "failed_dispatches": 0,
                     "heartbeat_ms": {"p95": 9000.0, "maximum": 12000.0}}
              for name in ("map", "globe", "tabs", "swaps", "soak")}
    (root / "components.csv").write_text("phase,mode,renderer_instances,active_renderers,created,destroyed,orbit_timer_active\n" +
                                          "\n".join(f"{name},{'globe' if name == 'globe' else 'map'},1,1,3,2,{'True' if name == 'globe' else 'False'}" for name in phases) + "\n", encoding="utf-8")
    plan, actions, targets = {"phases": {}, "minimum_phase_duration_s": 4, "soak_minutes": 4 / 60,
                             "production_zoom_thresholds_modified": False}, [], 0
    for phase in phases:
        commands = []
        for index in range(2):
            command = {"index": index, "action": phase + "-action"}
            if phase in {"map", "globe"} or phase == "soak" and index == 0:
                command["target"] = "synthetic-target"
                targets += 1
            commands.append(command)
            action = {"phase": phase, "action": command["action"], "planned_command": command, "successful_dispatch": True}
            if "target" in command:
                action["functional_effect"] = {"content_complete_and_corresponds": True, "timed_out": False,
                                               "presentation_evidence": {"complete": True, "failed_checks": [], "checks": {"received_value": True}}}
            actions.append(action)
        plan["phases"][phase] = commands
    write(root / "action-plan.json", plan)
    write(root / "actions.json", actions)
    return {"schema": assessment.REPORT_SCHEMAS["native-ui"], "measurement_completed": True, "source_unchanged": True,
            "action_plan_sha256": assessment.sha(root / "action-plan.json"),
            "physical_action_performed": False, "platform": "windows", "exit_code": 0, "errors": [], "source_hashes_start": source, "source_hashes_end": source,
            "workspace_entry": {"state": "ready", "authentication_bypassed": False, "credentials_supplied_by_harness": False, "method": "current_windows_session"},
            "phases": phases, "functional_effect_checks": {"selections": targets, "complete_content_readbacks": targets,
                                                          "mismatches": [], "mode_transition_mismatches": [], "page_navigation_mismatches": [], "marker_hit_mismatches": [],
                                                          "page_navigations": 7, "mode_transitions": 3, "actual_mode_changes": 2, "planned_mode_changes": 2}}


@pytest.fixture(scope="session")
def package_template(tmp_path_factory):
    root = tmp_path_factory.mktemp("trl4-template") / "workspace"
    for name, content in {"src/component.py": "VALUE=1\n", "tests/test_component.py": "def test_one(): pass\ndef test_param(value): pass\n",
                          "tests/fixtures/records.bin": "synthetic fixture", "docs/manual.md": "bounded laboratory manual",
                          "pyproject.toml": "[project]\nname='fixture'\nversion='1'\n"}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    write(root / "outputs/known/assessment.json", {"passed": False, "gate": "G05"})
    collection = {"schema": "orion.trl4.pytest-collection.v1", "groups": {"core": ["tests/test_component.py::test_one", "tests/test_component.py::test_param[a]", "tests/test_component.py::test_param[b]"]}}
    write(root / "configs/qualification/trl4_collection_v1.json", collection)
    protocol = {"schema": assessment.SCHEMA, "protocol_id": "SOFT-ALPHA-TRL4-v1", "revision": 1, "target_scope": "SOFT-ALPHA", "seed": 2026100900,
                "test_groups": {"core": ["tests/test_component.py"]},
                "requirements": [{"id": "F01", "label": "integrated software", "acceptance": "all planned functional evidence",
                                  "production_files": ["src/component.py"], "test_files": ["tests/test_component.py"], "documents": ["docs/manual.md"],
                                  "limitations": ["synthetic assessment fixture"], "campaigns": ["reference", "pipeline", "native-ui"]}],
                "reports": {"group-core": "group-core.xml", "reference-1": "reference-1/report.json", "reference-2": "reference-2/report.json",
                            "pipeline-1": "pipeline-1/report.json", "pipeline-2": "pipeline-2/report.json", "native-ui": "native-ui/report.json"},
                "collection_manifest": "configs/qualification/trl4_collection_v1.json", "known_trl5_failure": "outputs/known/assessment.json", "source_documents": ["docs/manual.md"],
                "run_profile": {"performance_duration_seconds": 4, "performance_soak_minutes": 4 / 60},
                "native_action_plan": "configs/qualification/trl4_native_action_plan_v1.json",
                "native_expectations": {"planned_actions": dict.fromkeys(("map", "globe", "tabs", "swaps", "soak"), 2),
                                        "selections": 5, "page_navigations": 7, "planned_mode_changes": 2, "mode_transitions": 3}}
    write(root / assessment.PROTOCOL, protocol)
    oracle = {"schema": "orionq.trl5-analytic-acceptance.v1", "seed": 2026100900,
              "checks": {f"independent-{i}": {"expected": i / 10.0, "absolute_tolerance": 1e-12, "relative_tolerance": 1e-10,
                                            "inputs": {"parameter": i}, "reference_ids": ["reference-fixture"], "production_api": "fixture.kernel", "assumptions": "synthetic unit test"} for i in range(35)}}
    directory = root / "outputs/campaign"
    native_fixture = root / "outputs/native-fixture"
    native(native_fixture, {})
    shutil.copyfile(native_fixture / "action-plan.json", root / protocol["native_action_plan"])
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(assessment, "analytic_acceptance", lambda _root, _seed: deepcopy(oracle))
        patch.setattr(assessment, "expected_report_sources", fixture_source_universes)
        frozen = assessment.prepare(directory, root=root)
    suite = ET.Element("testsuite", tests="999999", failures="999999", skipped="999999")
    for case in frozen["test_cases"]["core"]:
        ET.SubElement(suite, "testcase", classname=case["classname"], name=case["name"])
    ET.ElementTree(suite).write(directory / "group-core.xml")
    source = {name: frozen["source_file_hashes"][name] for name in ("src/component.py", "pyproject.toml")}
    for repeat in (1, 2):
        write(directory / f"reference-{repeat}/report.json", reference(oracle, source))
        write(directory / f"pipeline-{repeat}/report.json", pipeline(directory / f"pipeline-{repeat}", source))
    write(directory / "native-ui/report.json", native(directory / "native-ui", source))
    return root, protocol, oracle


@pytest.fixture
def package(tmp_path, package_template, monkeypatch):
    template, protocol, oracle = package_template
    root = tmp_path / "workspace"
    # Each case mutates a private copy. These closed synthetic databases are
    # assessor input fixtures, not tests of crash recovery or storage latency.
    shutil.copytree(template, root)
    monkeypatch.setattr(assessment, "analytic_acceptance", lambda _root, _seed: deepcopy(oracle))
    monkeypatch.setattr(assessment, "expected_report_sources", fixture_source_universes)
    return root, root / "outputs/campaign", deepcopy(protocol)


def fixture_source_universes(_root, protocol, inventory):
    source = {name: inventory[name] for name in ("src/component.py", "pyproject.toml")}
    return {kind: dict(source) for kind in ["reference", "pipeline", "native-ui"] + (["console"] if "console" in protocol["reports"] else [])}


def change_report(directory, name, mutation):
    path = directory / name
    report = assessment.load_json(path)
    mutation(report)
    write(path, report)


def test_complete_package_requires_external_review_even_with_slow_ui(package):
    root, directory, _ = package
    result = assessment.assess(directory, root=root)
    assert result["passed"] and result["functional_package_status"] == "complete_for_review"
    assert result["assessment"] == "pending_external_review" and result["human_review"] == "pending"
    assert result["trl4_assigned"] is False and result["hardware_qualified"] is False
    assert result["requirements"][0]["functional_status"] == "pass"
    assert result["executed_testcase_count"] == 3  # forged suite counters ignored
    assert result["performance_qualification"] == "not_demonstrated"
    assert (directory / "source-after.json").is_file()


@pytest.mark.parametrize("mutation", ["remove_parameter", "remove_all", "duplicate", "skipped", "error", "failure", "wrong_module"])
def test_actual_junit_cases_not_flags_or_suite_counts(package, mutation):
    root, directory, _ = package
    path = directory / "group-core.xml"
    suite = ET.parse(path).getroot()
    if mutation == "remove_parameter":
        suite.remove(suite[-1])
    elif mutation == "remove_all":
        suite.clear()
    elif mutation == "duplicate":
        suite.append(deepcopy(suite[0]))
    elif mutation == "wrong_module":
        suite[0].set("classname", "tests.unplanned")
    else:
        ET.SubElement(suite[0], mutation)
    ET.ElementTree(suite).write(path)
    result = assessment.assess(directory, root=root)
    assert not result["passed"] and result["requirements"][0]["functional_status"] == "fail"
    assert any(error.startswith("group-core:") for error in result["errors"])


@pytest.mark.parametrize("name", ["group-core.xml", "reference-1/report.json", "pipeline-1/report.json", "native-ui/report.json"])
def test_missing_evidence_is_not_demonstrated(package, name):
    root, directory, _ = package
    (directory / name).unlink()
    result = assessment.assess(directory, root=root)
    assert not result["passed"] and result["requirements"][0]["functional_status"] == "not_demonstrated"


@pytest.mark.parametrize("target", ["src/component.py", "tests/fixtures/records.bin", "docs/manual.md", "configs/qualification/trl4_collection_v1.json"])
def test_changed_frozen_sources_and_fixtures_fail(package, target):
    root, directory, _ = package
    with (root / target).open("a", encoding="utf-8") as handle:
        handle.write("\nchanged")
    result = assessment.assess(directory, root=root)
    assert not result["passed"] and result["source_unchanged"] is False


def test_added_source_detected_but_live_result_documents_are_not_source(package):
    root, directory, _ = package
    (root / "src/added.py").write_text("new code", encoding="utf-8")
    result = assessment.assess(directory, root=root)
    assert not result["passed"] and any("added" in error for error in result["errors"])


def test_unmapped_live_status_document_does_not_invalidate_freeze(package):
    root, directory, _ = package
    live = root / "docs/product/roadmap.md"
    live.parent.mkdir(parents=True)
    live.write_text("new status, not a frozen contract", encoding="utf-8")
    assert assessment.assess(directory, root=root)["passed"]


@pytest.mark.parametrize("target", ["protocol.json", "analytic-acceptance.json", "source-before.json"])
def test_frozen_evidence_adulteration_fails(package, target):
    root, directory, _ = package
    data = assessment.load_json(directory / target)
    data["adulterated"] = True
    write(directory / target, data)
    assert not assessment.assess(directory, root=root)["passed"]


@pytest.mark.parametrize("path", ["../escape.xml", "/absolute.xml", "C:/private.xml", "group\\core.xml", "group/./core.xml"])
def test_path_escape_rejected_before_read(package, path):
    _, directory, _ = package
    with pytest.raises(ValueError):
        assessment.contained(directory, path)


@pytest.mark.parametrize("change", ["duplicate_requirement", "missing_production", "unplanned_mapping", "missing_collection", "duplicate_nodeid"])
def test_prepare_rejects_incomplete_or_ambiguous_scope(package, change):
    root, _, protocol = package
    if change == "duplicate_requirement":
        protocol["requirements"].append(deepcopy(protocol["requirements"][0]))
    elif change == "missing_production":
        protocol["requirements"][0]["production_files"] = ["src/missing.py"]
    elif change == "unplanned_mapping":
        protocol["requirements"][0]["test_files"] = ["tests/unplanned.py"]
    elif change == "missing_collection":
        (root / protocol["collection_manifest"]).unlink()
    else:
        path = root / protocol["collection_manifest"]
        collection = assessment.load_json(path)
        collection["groups"]["core"].append(collection["groups"]["core"][0])
        write(path, collection)
    write(root / assessment.PROTOCOL, protocol)
    with pytest.raises(ValueError):
        assessment.prepare(root / "outputs/second", root=root)


def test_revision_two_freezes_its_own_protocol_and_cases(package):
    root, _, protocol = package
    protocol["revision"] = 2
    write(root / assessment.PROTOCOL, protocol)
    directory = root / "outputs/revision-two"
    assessment.prepare(directory, root=root)
    manifest = assessment.load_json(directory / "freeze.json")
    assert manifest["revision"] == 2
    assert assessment.load_json(directory / "protocol.json") == protocol
    assert manifest["test_cases"] == assessment.collected_cases(protocol, root)


@pytest.mark.parametrize("revision", [0, -1, 3, True, 1.0, "2", None])
def test_unsupported_or_noninteger_revision_is_refused_before_freeze(package, revision):
    root, _, protocol = package
    protocol["revision"] = revision
    write(root / assessment.PROTOCOL, protocol)
    directory = root / "outputs/invalid-revision"
    with pytest.raises(ValueError, match="unsupported protocol revision"):
        assessment.prepare(directory, root=root)
    assert not (directory / "freeze.json").exists()


@pytest.mark.parametrize("mutation", ["expected", "input", "tolerance", "actual", "duplicate", "fingerprint", "wrong_seed", "unfrozen_source", "physical"])
def test_reference_acceptance_independent_and_frozen(package, mutation):
    root, directory, _ = package

    def corrupt(report):
        check = report["checks"][0]
        if mutation == "expected":
            check["expected"] = 9.0
        elif mutation == "input":
            check["inputs"]["parameter"] = 99
        elif mutation == "tolerance":
            check["absolute_tolerance"] = 100.0
        elif mutation == "actual":
            check["actual"] = 100.0
        elif mutation == "duplicate":
            report["checks"][1] = deepcopy(check)
        elif mutation == "fingerprint":
            report["scientific_fingerprint_sha256"] = "forged"
        elif mutation == "wrong_seed":
            report["seed"] += 1
        elif mutation == "unfrozen_source":
            report["source_hashes"] = {"outputs/private.py": "0" * 64}
        else:
            report["physical_action_performed"] = True

    change_report(directory, "reference-1/report.json", corrupt)
    assert not assessment.assess(directory, root=root)["passed"]


@pytest.mark.parametrize("mutation", ["nonboolean", "missing_check", "technology", "negative_success", "unavailable_success", "raw_count", "receipt", "sqlite", "snapshot", "pair_claim", "repetition"])
def test_pipeline_requires_material_causal_evidence(package, mutation):
    root, directory, _ = package
    location = directory / "pipeline-1"
    if mutation == "receipt":
        path = location / "nominal/receipts.jsonl"
        path.write_text(path.read_text().replace('"event": "configured"', '"event": "invented"', 1), encoding="utf-8")
    elif mutation == "sqlite":
        with sqlite3.connect(location / "nominal/protocol.sqlite") as connection:
            connection.execute("DELETE FROM technology_events WHERE sequence=0")
    elif mutation == "snapshot":
        path = location / "nominal/snapshot.json"
        data = assessment.load_json(path)
        data["sessions"]["ENT"]["metrics"]["certified_secret_bits"] = 100
        write(path, data)
    else:
        def corrupt(report):
            if mutation == "nonboolean":
                report["checks"]["source_files_unchanged"] = 1
            elif mutation == "missing_check":
                report["checks"].pop("negative.absent")
            elif mutation == "technology":
                report["coverage"]["technologies"].remove("tf_qkd")
            elif mutation == "negative_success":
                report["negative_cases"]["absent"]["completed_commands"] = 1
            elif mutation == "unavailable_success":
                report["scenarios"]["unavailable"]["completed_commands"] = 1
            elif mutation == "raw_count":
                report["scenarios"]["nominal"]["source"]["observations_recorded"] += 1
            elif mutation == "pair_claim":
                report["communication"]["end_to_end_entanglement"]["delivered_pairs"] = 5
            else:
                report["reproducibility"]["second"] = "different"
        change_report(directory, "pipeline-1/report.json", corrupt)
    result = assessment.assess(directory, root=root)
    assert not result["passed"] and any(error.startswith("pipeline-1:") for error in result["errors"])


@pytest.mark.parametrize("mutation", ["offscreen", "incomplete", "planned_missing", "readback", "anonymous", "source_end", "renderer", "timer", "missing_components"])
def test_native_requires_complete_readbacks_and_exclusive_renderers(package, mutation):
    root, directory, _ = package
    location = directory / "native-ui"
    if mutation in {"renderer", "timer"}:
        path = location / "components.csv"
        text = path.read_text()
        text = text.replace("map,map,1,1,3,2,False", "map,map,2,2,4,2,False" if mutation == "renderer" else "map,map,1,1,3,2,True")
        path.write_text(text, encoding="utf-8")
    elif mutation == "missing_components":
        (location / "components.csv").unlink()
    else:
        def corrupt(report):
            if mutation == "offscreen":
                report["platform"] = "offscreen"
            elif mutation == "incomplete":
                report["measurement_completed"] = False
            elif mutation == "planned_missing":
                report["phases"]["map"]["planned_actions_completed"] -= 1
            elif mutation == "readback":
                report["functional_effect_checks"]["complete_content_readbacks"] -= 1
            elif mutation == "anonymous":
                report["workspace_entry"]["authentication_bypassed"] = True
            else:
                report["source_hashes_end"]["src/component.py"] = "forged"
        change_report(directory, "native-ui/report.json", corrupt)
    assert not assessment.assess(directory, root=root)["passed"]


def test_nonfinite_and_duplicate_json_fields_rejected():
    for data in ('{"a":NaN}', '{"a":Infinity}', '{"a":1,"a":2}'):
        with pytest.raises(ValueError):
            assessment.loads(data)


def test_preserve_prior_evidence_and_reject_second_freeze(package):
    root, directory, _ = package
    prior = (root / "outputs/known/assessment.json").read_bytes()
    with pytest.raises(ValueError):
        assessment.prepare(directory, root=root)
    result = assessment.assess(directory, root=root)
    assert result["known_trl5_failure"]["sha256"] == assessment.sha(root / "outputs/known/assessment.json")
    assert (root / "outputs/known/assessment.json").read_bytes() == prior


@pytest.mark.parametrize("target", ["snapshot.json", "raw-source-simulated.jsonl", "receipts.jsonl", "protocol.sqlite"])
def test_missing_pipeline_auxiliary_is_not_demonstrated(package, target):
    root, directory, _ = package
    (directory / "pipeline-1/nominal" / target).unlink()
    result = assessment.assess(directory, root=root)
    assert not result["passed"]
    assert result["evidence"]["pipeline-1"]["status"] == "not_demonstrated"
    assert result["requirements"][0]["functional_status"] == "not_demonstrated"


@pytest.mark.parametrize("mutation", ["raw_bits", "raw_timestamp", "duplicate_command", "configuration", "compressed_satellite"])
def test_material_payload_not_just_line_or_row_counts(package, mutation):
    root, directory, _ = package
    location = directory / "pipeline-1/nominal"
    if mutation in {"configuration", "compressed_satellite"}:
        with sqlite3.connect(location / "protocol.sqlite") as connection:
            if mutation == "configuration":
                connection.execute("UPDATE technology_configuration SET config_json=? WHERE session_id='TF'",
                                   (json.dumps({"technologies": ["invented"]}),))
            else:
                value, blob = connection.execute("SELECT sha256,observation_zlib FROM satellite_acquisitions LIMIT 1").fetchone()
                observation = json.loads(zlib.decompress(blob))
                observation["raw"]["time_ps"][0] += 1
                connection.execute("UPDATE satellite_acquisitions SET observation_zlib=? WHERE sha256=?",
                                   (zlib.compress(json.dumps(observation).encode()), value))
    else:
        path = location / "raw-source-simulated.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if mutation == "raw_bits":
            rows[0]["raw"]["bits"][0] = 1
        elif mutation == "raw_timestamp":
            rows[0]["raw"]["time_ps"][0] += 1
        else:
            rows[-1] = deepcopy(rows[0])
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    result = assessment.assess(directory, root=root)
    assert not result["passed"] and result["evidence"]["pipeline-1"]["status"] == "fail"


@pytest.mark.parametrize("label", ["reference-1", "pipeline-1", "native-ui"])
def test_report_cannot_omit_mandatory_sources(package, label):
    root, directory, _ = package

    def omit(report):
        fields = ("source_hashes_start", "source_hashes_end") if label == "native-ui" else ("source_hashes",)
        for field in fields:
            report[field].pop("pyproject.toml")
    change_report(directory, label + "/report.json", omit)
    result = assessment.assess(directory, root=root)
    assert not result["passed"] and "source universe" in result["evidence"][label]["error"]


def test_two_individually_valid_references_must_repeat_science(package):
    root, directory, _ = package

    def change_within_tolerance(report):
        report["checks"][0]["actual"] = 5e-13
        report["checks"][0]["absolute_error"] = 5e-13
        report["scientific_fingerprint_sha256"] = assessment.digest({key: report[key] for key in
                                                                    ("seed", "checks", "acquisition_summaries", "experimental_reference_inventory")}, ascii=True)
    change_report(directory, "reference-2/report.json", change_within_tolerance)
    result = assessment.assess(directory, root=root)
    assert result["evidence"]["reference-1"]["passed"] and result["evidence"]["reference-2"]["passed"]
    assert not result["passed"] and not result["evidence"]["reference-repetition"]["passed"]


@pytest.mark.parametrize("mutation", ["omitted_command", "readback_false", "downsized_plan", "retarget_same_counts", "missing_plan", "missing_components"])
def test_native_material_action_plan_and_readbacks_are_not_only_counters(package, mutation):
    root, directory, _ = package
    location = directory / "native-ui"
    if mutation.startswith("missing_"):
        (location / ("action-plan.json" if mutation == "missing_plan" else "components.csv")).unlink()
    elif mutation in {"downsized_plan", "retarget_same_counts"}:
        plan = assessment.load_json(location / "action-plan.json")
        if mutation == "downsized_plan":
            plan["phases"]["map"].pop()
        else:
            plan["phases"]["map"][0]["target"] = "altered-target"
            path = location / "actions.json"
            actions = json.loads(path.read_text())
            actions[0]["planned_command"]["target"] = "altered-target"
            write(path, actions)
        write(location / "action-plan.json", plan)
        change_report(directory, "native-ui/report.json", lambda report: report.update(action_plan_sha256=assessment.sha(location / "action-plan.json")))
    else:
        path = location / "actions.json"
        actions = json.loads(path.read_text())
        if mutation == "omitted_command":
            actions.pop()
        else:
            actions[0]["functional_effect"]["presentation_evidence"]["checks"]["received_value"] = False
        write(path, actions)
    result = assessment.assess(directory, root=root)
    assert not result["passed"]
    assert result["evidence"]["native-ui"]["status"] == ("not_demonstrated" if mutation.startswith("missing_") else "fail")


def with_console(package, monkeypatch):
    root, old_directory, protocol = package
    protocol["console_cases"] = ["cleanup"]
    protocol["console_case_criteria"] = {"cleanup": ["provider_stopped", "timers_stopped"]}
    protocol["requirements"][0]["campaigns"].append("console")
    protocol["reports"]["console"] = "console/report.json"
    write(root / assessment.PROTOCOL, protocol)
    directory = root / "outputs/console-campaign"
    frozen = assessment.prepare(directory, root=root)
    for name in ("reference-1", "reference-2", "pipeline-1", "pipeline-2", "native-ui"):
        shutil.copytree(old_directory / name, directory / name)
    shutil.copyfile(old_directory / "group-core.xml", directory / "group-core.xml")
    source = frozen["expected_report_sources"]["console"]
    location = directory / "console"
    location.mkdir()
    artifact = location / "fixture-evidence.json"
    write(artifact, {"scope": "synthetic assessor unit fixture"})
    report = {"schema": assessment.REPORT_SCHEMAS["console"], "passed": True, "errors": [],
              "physical_action_performed": False, "hardware_validated": False, "qt_platform": "windows", "source_unchanged": True,
              "source_hashes": source, "source_hashes_end": source, "frozen": False, "isolated_diagnostic": True,
              "state_root": str(location / "state"), "console_cases": protocol["console_cases"], "case_criteria": protocol["console_case_criteria"],
              "cases": [{"id": "cleanup", "status": "passed", "checks": {"provider_stopped": True, "timers_stopped": True}, "evidence": {"provider_stopped": True}}],
              "checks": {"cleanup": True}, "actual_authentication": {"method": "current_windows_session", "backend_verified": True,
                                                                     "authentication_bypassed": False, "credentials_supplied": False, "request_count": 1},
              "artifacts": {artifact.name: {"sha256": assessment.sha(artifact)}}}
    write(location / "manifest-before.json", {key: report[key] for key in ("schema", "console_cases", "case_criteria", "source_hashes", "physical_action_performed")})
    write(location / "report.json", report)
    # The separate console checker has its own schema/artifact/SQL adversarial
    # suite. Here the dependency is explicit, avoiding Qt or a real service.
    monkeypatch.setattr(assessment, "console_material_evidence", lambda *_args: True)
    return root, directory


def test_console_criteria_are_prospective_and_material_checker_is_required(package, monkeypatch):
    root, directory = with_console(package, monkeypatch)
    monkeypatch.setattr(assessment, "console_material_evidence", lambda *_args: {"truthy_but_unverified": True})
    result = assessment.assess(directory, root=root)
    assert not result["passed"] and result["evidence"]["console"]["status"] == "fail"


def test_valid_console_contract(package, monkeypatch):
    root, directory = with_console(package, monkeypatch)
    assert assessment.assess(directory, root=root)["passed"]


@pytest.mark.parametrize("mutation", ["missing_check", "nonboolean", "duplicate_id", "wrong_criteria", "anonymous", "state_escape", "source_omission", "artifact_tampered", "artifact_missing"])
def test_console_case_contract_and_artifacts_cannot_be_forged(package, monkeypatch, mutation):
    root, directory = with_console(package, monkeypatch)
    if mutation.startswith("artifact_"):
        artifact = directory / "console/fixture-evidence.json"
        if mutation == "artifact_missing":
            artifact.unlink()
        else:
            artifact.write_text("changed", encoding="utf-8")
    else:
        def corrupt(report):
            if mutation == "missing_check":
                report["cases"][0]["checks"].pop("provider_stopped")
            elif mutation == "nonboolean":
                report["cases"][0]["checks"]["timers_stopped"] = 1
            elif mutation == "duplicate_id":
                report["cases"].append(deepcopy(report["cases"][0]))
            elif mutation == "wrong_criteria":
                report["case_criteria"]["cleanup"] = ["always_pass"]
            elif mutation == "anonymous":
                report["actual_authentication"]["backend_verified"] = False
            elif mutation == "state_escape":
                report["state_root"] = str(root / "operator-state")
            else:
                report["source_hashes"].pop("pyproject.toml")
                report["source_hashes_end"].pop("pyproject.toml")
        change_report(directory, "console/report.json", corrupt)
    result = assessment.assess(directory, root=root)
    assert not result["passed"]
    assert result["evidence"]["console"]["status"] == ("not_demonstrated" if mutation == "artifact_missing" else "fail")
