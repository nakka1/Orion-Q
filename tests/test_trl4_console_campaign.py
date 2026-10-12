"""Preparation regressions for truthful campaign acceptance; no product Qt run."""
import argparse
import ast
from contextlib import closing
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("trl4_console_campaign", ROOT / "tools/run_trl4_console_campaign.py")
campaign = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(campaign)

pytestmark = pytest.mark.unit


@pytest.fixture
def evidence():
    # This is a report-validator fixture, not native authentication evidence.
    hashes = {"src/quantum_autopilot/console/app.py": "a" * 64}
    return {"schema": campaign.SCHEMA,
            "cases": [campaign.case_result(ident, dict.fromkeys(checks, True)) for ident, checks in campaign.CASE_CRITERIA.items()],
            "actual_authentication": {"method": "current_windows_session", "request_count": 1,
                "backend_verified": True, "principal_source": "os", "authentication_bypassed": False,
                "credentials_supplied": False, "tick_advances": 3},
            "qt_platform": "windows", "source_unchanged": True, "source_hashes": hashes,
            "source_hashes_end": hashes.copy(), "physical_action_performed": False, "hardware_validated": False,
            "isolated_diagnostic": True, "frozen": False, "application_exit_code": 0, "errors": []}


def test_exact_complete_validator_fixture_passes_without_running_native(evidence):
    assert campaign.report_passes(evidence)
    assert len(campaign.CASE_CRITERIA) == 16


@pytest.mark.parametrize("field,value", [("method", "fixture"), ("request_count", 0), ("request_count", True),
    ("request_count", 2), ("backend_verified", False), ("backend_verified", 1), ("principal_source", "jwt"),
    ("authentication_bypassed", True), ("credentials_supplied", True), ("tick_advances", 2),
    ("tick_advances", float("inf")), ("tick_advances", True)])
def test_missing_or_fabricated_authentication_cannot_qualify(evidence, field, value):
    evidence["actual_authentication"][field] = value
    assert not campaign.report_passes(evidence)


@pytest.mark.parametrize("field,value", [("qt_platform", "offscreen"), ("frozen", True), ("frozen", "false"),
    ("isolated_diagnostic", False), ("physical_action_performed", True), ("hardware_validated", True),
    ("source_unchanged", False), ("source_hashes", {}), ("application_exit_code", 1),
    ("errors", [{"case": "ground_details"}])])
def test_wrong_scope_and_failed_execution_cannot_qualify(evidence, field, value):
    evidence[field] = value
    assert not campaign.report_passes(evidence)


@pytest.mark.parametrize("change", ["missing", "duplicate", "not_demonstrated", "failed", "empty", "extra", "numeric", "false", "malformed"])
def test_each_case_requires_exact_named_true_checks(evidence, change):
    row = evidence["cases"][0]
    if change == "missing":
        evidence["cases"].pop()
    elif change == "duplicate":
        evidence["cases"][-1] = copy.deepcopy(row)
    elif change in ("not_demonstrated", "failed"):
        row["status"] = change
    elif change == "empty":
        row["checks"] = {}
    elif change == "extra":
        row["checks"]["anything"] = True
    elif change == "malformed":
        row["id"] = []
    else:
        row["checks"][next(iter(row["checks"]))] = 1 if change == "numeric" else False
    assert not campaign.report_passes(evidence)


def test_changed_source_hashes_cannot_claim_unchanged(evidence):
    evidence["source_hashes_end"][next(iter(evidence["source_hashes_end"]))] = "b" * 64
    assert not campaign.report_passes(evidence)


@pytest.mark.parametrize("checks", [{}, {"backend_verified": True}, {"backend_verified": 1}])
def test_case_builder_refuses_incomplete_or_numeric_checks(checks):
    with pytest.raises(ValueError):
        campaign.case_result("native_entry", checks)


def test_output_and_state_are_new_and_never_reused(tmp_path):
    output = campaign.prepare_output(tmp_path / "fresh")
    assert output.resolve() == output and list(output.iterdir()) == [output / "state"]
    assert not list((output / "state").iterdir())
    with pytest.raises(ValueError, match="new output"):
        campaign.prepare_output(output)


def test_direct_worker_cannot_open_operator_or_preexisting_state(tmp_path):
    output = campaign.prepare_output(tmp_path / "worker")
    with pytest.raises(ValueError, match="manifest"):
        campaign.validate_worker_root(output)
    manifest = {"schema": campaign.SCHEMA, "provider": "demo", "state_root": str(output / "state"),
                "console_cases": list(campaign.CASE_CRITERIA)}
    campaign.write_json(output / "manifest-before.json", manifest)
    assert campaign.validate_worker_root(output) == output
    (output / "state" / "operator-data.sqlite").write_bytes(b"existing")
    with pytest.raises(ValueError, match="empty"):
        campaign.validate_worker_root(output)


def test_worker_rejects_state_path_redirect_and_already_written_report(tmp_path):
    output = campaign.prepare_output(tmp_path / "redirect")
    manifest = {"schema": campaign.SCHEMA, "provider": "demo", "state_root": str(tmp_path / "operator"),
                "console_cases": list(campaign.CASE_CRITERIA)}
    campaign.write_json(output / "manifest-before.json", manifest)
    with pytest.raises(ValueError, match="isolated"):
        campaign.validate_worker_root(output)
    (output / "report.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        campaign.validate_worker_root(output)


def test_environment_is_allow_list_and_does_not_echo_secrets(monkeypatch):
    monkeypatch.setenv("ORION_TOKEN", "SECRET-MUST-NOT-APPEAR")
    monkeypatch.setenv("PASSWORD", "SECOND-SECRET")
    value = campaign.environment()
    assert set(value) == {"python", "implementation", "os", "machine", "executable_sha256"}
    assert "SECRET" not in json.dumps(value)


@pytest.mark.parametrize("value", ["bad", "0x900", "1440x0", "1440x900x2", "90000x900"])
def test_invalid_client_dimensions_refused(value):
    with pytest.raises(argparse.ArgumentTypeError):
        campaign.size_value(value)


def test_native_launcher_has_no_authentication_override_or_result_patch():
    # Inspect the runnable contract without importing Qt or executing app.run.
    tree = ast.parse((ROOT / "tools/run_trl4_console_campaign.py").read_text(encoding="utf-8"))
    constants = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert "--skip-auth" not in constants
    assert "--provider" in constants and "demo" in constants and "--state-root" in constants
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) and any(
        alias.name.startswith("unittest.mock") for alias in node.names) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "open_workspace" for node in ast.walk(tree))


def test_parented_desktop_dialog_never_routes_input_to_proxy_viewport():
    class Content:
        def isAncestorOf(self, _):
            return True  # Qt ownership can reach even a native desktop dialog.

    class Widget:
        def __init__(self, native_window):
            self.native_window = native_window

        def window(self):
            return self.native_window

    class Stage:
        content = Content()

    stage = Stage()
    assert campaign.uses_stage_viewport(Widget(stage.content), stage)
    assert not campaign.uses_stage_viewport(Widget(object()), stage)


def test_native_driver_observes_deferred_cleanup_instead_of_fabricating_it():
    tree = ast.parse((ROOT / "tools/run_trl4_console_campaign.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "sendPostedEvents" for node in ast.walk(functions["pump"]))
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "pump" for node in ast.walk(functions["wait"]))
    assert "renderer_instances" in [node.value for node in ast.walk(functions["cleanup"]) if isinstance(node, ast.Constant)]


def _material_detail(satellite=False):
    common = ("presentation_pending_empty", "presentation_link_completion", "presentation_section_completion",
              "presentation_core_rows_empty", "presentation_timestamp", "title")
    specific = ("satellite_snapshot_values", "orbital_widget_values", "bridge_widget_values", "isl_widget_values",
                "operations_widget_values", "station_widget_values", "source_provenance_visible", "distinct_channels_visible") if satellite else (
                "live_node_metadata", "identity_widget_values", "state_widget_values", "calibration_widget_values",
                "calibration_links_widget_values", "route_widget_values", "connection_widget_values", "legend_link_keys",
                "legend_widget_values", "chart_link_keys", "chart_received_values", "chart_source_notes", "chart_network_time")
    counts = {"orbital": 1, "bridge": 1, "isl": 1, "operations": 1, "stations": 0} if satellite else {
        "identity": 1, "state": 1, "calibration": 1, "calibration_links": 0, "routes": 1, "legend": 0, "cards": 0}
    return {"state": "ready", "ready": True, "complete": True, "mode": "asynchronous-contract", "presented_tick": 4,
            "source_kind": "simulated", "network_time_s": 10., "failed_checks": [], "section_status_supported": True,
            "checks": dict.fromkeys(common + specific, True), "widget_field_counts": counts, "widget_content_sha256": "a" * 64,
            "presentation": {"ready": True, "presentation_kind": "satellite" if satellite else "ground",
                "generation": 2, "rendered_generation": 2, "presented_tick": 4, "source_kind": "simulated",
                "network_time_s": 10., "pending": 0, "pending_items": 0, "pending_core_rows": 0,
                "expected_link_keys": [], "completed_link_keys": [], "expected_sections": ["identity"],
                "completed_sections": ["identity"], "error": ""}}


def _fixture_png():
    # A genuine one-pixel PNG, only a validator fixture, never native evidence.
    def chunk(kind, data):
        return campaign.struct.pack(">I", len(data)) + kind + data + campaign.struct.pack(">I", campaign.zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", campaign.struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", campaign.zlib.compress(b"\0\0\0\0\0")) + chunk(b"IEND", b""))


def _artifact(report, directory, name, data, scope="native_application_window"):
    (directory / name).write_bytes(data)
    report["artifacts"][name] = {"sha256": campaign.sha256(directory / name), "scope": scope}


@pytest.fixture
def material_evidence(tmp_path, evidence):
    """Synthetic schemas/records to exercise the checker; no Qt/auth campaign."""
    directory = tmp_path / "synthetic-checker-fixture"
    directory.mkdir()
    database = directory / "state/demo/brazil-institutions-v1/sandbox_journal.sqlite"
    database.parent.mkdir(parents=True)
    report = copy.deepcopy(evidence)
    report["artifacts"] = {}
    cases = {row["id"]: row for row in report["cases"]}

    def put(ident, value):
        cases[ident]["evidence"] = value

    put("native_entry", {"input_kind": "demo", "source_kind": "simulated", "nodes": 3, "tick": 3})
    put("seven_screens", {name: {"source_kind": "simulated", "tick": 4} for name in campaign.PAGES})
    put("topology_views", {"views": ["MATRIX", "GRAPH", "GEO"], "nodes": 3, "links": 1})
    put("ground_details", {"readbacks": [{"node_id": name, "readback": _material_detail()} for name in ("CIMATEC", "CBPF")]})
    put("satellite_details", {"readbacks": [{"node_id": "SAT-FIXTURE", "readback": _material_detail(True)}]})
    resources = [{"mode": mode, "created": count, "destroyed": count - 1, "renderer_instances": 1,
                  "active_renderers": 1, "retiring": False, "orbit_timer_active": mode == "globe", "transition_raster": False}
                 for count, mode in enumerate(("globe", "map", "globe", "map"), 4)]
    put("geo_lifecycle", {"resources": resources, "map_camera": [-38.5, -12.95, .0001]})
    put("communicator_dynamic", {"type_order": ["satellite", "installation"], "manual_protocol": "BB84"})
    records = []
    for ident, name, physical_type, before in (("communicator_terrestrial", "TRL4-NATIVE-GROUND", "installation", 1),
                                               ("communicator_satellite", "TRL4-NATIVE-SAT", "satellite", 3)):
        record = {"device_id": name, "node_id": name, "name": name + " updated", "revision": 1,
                  "physical_type": physical_type, "integration": {"mode": "simulated", "data_origin": "simulated"},
                  "location": {} if physical_type == "satellite" else {"latitude": -12.95, "longitude": -38.5},
                  "orbit": {"compatible_stations": ["CBPF", "CIMATEC"]} if physical_type == "satellite" else {}}
        records.append(record)
        put(ident, {"node_id": name, "created_revision": 0, "edited_revision": 1, "topology_version_before": before,
                    "topology_version_after": before + 2, "persisted_record_sha256": campaign._record_hash(record, ascii_text=True),
                    "engine_status": "saved", "physical_action_performed": False})
    put("communicator_refusal", {"topology_version": 5, "commands_dispatched": 0})
    configuration = {"schema": "orion.fpga-devices.v1", "device_id": "FPGA-FIXTURE", "node_id": "CBPF", "label": "fixture",
                     "adapter_id": "simulated", "enabled": True, "connected": True, "clock_hz": 100000000., "pipeline_cycles": 257,
                     "timeout_s": 2., "retry_ticks": 3, "auto_reconnect": True, "revision": 5}
    configuration_hash = campaign._record_hash(configuration)
    receipt = {"schema": "orion.fpga-receipt.v1", "device_id": "FPGA-FIXTURE", "topology_id": "sandbox-line-3", "tick": 5,
        "at_utc": "2026-10-09T00:00:00+00:00", "kind": "processing_feedback", "physical_action_performed": False,
        "body": {"acquisitions": [{"channel_receipt": {"status": "received", "stages": ["input_validation", "quantum_receive", "classical_receive"]},
                                   "processing_status": "processed"}], "configuration_sha256": configuration_hash, "origin": "simulated",
                 "modeled_pipeline_latency_s": 257 / 100000000., "measured_fpga_latency_s": None, "host_read_ms": 1.}}
    put("fpga_emulated", {"device_id": "FPGA-FIXTURE", "revisions": [1, 2, 3, 5], "configuration_sha256": configuration_hash,
                          "last_feedback": {**receipt, "record_sha256": campaign._record_hash(receipt)},
                          "measured_fpga_latency_s": None, "temperature_c": None})
    put("key_audit_readback", {"key_scope": "validator fixture only", "export_signature": "unsigned · local session",
        "audit_chain": {"valid": True, "count": 4, "checkpoint_count": 4, "uncheckpointed_records": 0, "head": "b" * 64}})
    put("ml_cancel_confirm_restore", {"control_revisions": [1, 2], "ml_control_enabled": False,
        "detector_before": [["fixture-detector", "active"]], "detector_after": [["fixture-detector", "active"]],
        "scope": "ML network permission is distinct from inference/detection; optional model defaults unchanged"})
    put("physical_boundary", {"physical_actuation": False, "physical_executor_available": False, "physical_action_performed": False})
    put("cleanup", {"provider_running": False, "login_worker_closed": True,
                    "geo_resources": {**resources[-1], "destroyed": 7, "renderer_instances": 0, "active_renderers": 0}})
    topology = {"topology_id": "sandbox-line-3", "topology_version": 5,
                "nodes": [{"node_id": record["node_id"], "telemetry": {"communicator": record}} for record in records]}
    payload = {"scope": "qkd", "format": "unsigned_console_evidence_export_v1",
               "health": {"status": "ok", "physical_read_only": True, "automation_allowed": False},
               "topology": {"topology": topology, "persisted": True, "physical_topology": False},
               "events": {"events": []}, "qkd_example": None}
    _artifact(report, directory, "qkd-evidence.json", json.dumps(payload).encode(), "unsigned_qkd_evidence")
    put("evidence_export", {"destination": "qkd-evidence.json", "sha256": report["artifacts"]["qkd-evidence.json"]["sha256"],
        "file_picker": "real Qt widgets on Windows; Win32 picker not assessed", "completed_count": 1,
        "format": "unsigned_console_evidence_export_v1", "scope": "qkd"})
    names = ["native-entry", "communicator-terrestrial", "communicator-satellite", "fpga-emulated",
             "detail-CIMATEC", "detail-CBPF", "detail-SAT-FIXTURE"] + ["screen-" + page for page in campaign.PAGES]
    for name in names:
        _artifact(report, directory, name + ".png", _fixture_png())
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executescript("""
            CREATE TABLE simulated_topologies(topology_id TEXT,topology_version INTEGER,updated_at_utc TEXT,state_json TEXT);
            CREATE TABLE communicators(topology_id TEXT,device_id TEXT,node_id TEXT,revision INTEGER,record_json TEXT);
            CREATE TABLE topology_events(sequence INTEGER PRIMARY KEY,event_id TEXT,topology_id TEXT,version_before INTEGER,
              version_after INTEGER,event_kind TEXT,event_sha256 TEXT,created_at_utc TEXT,event_json TEXT);
            CREATE TABLE fpga_devices(topology_id TEXT,device_id TEXT,revision INTEGER,configuration_json TEXT,configuration_sha256 TEXT);
            CREATE TABLE fpga_device_receipts(sequence INTEGER PRIMARY KEY,topology_id TEXT,device_id TEXT,tick INTEGER,
              kind TEXT,at_utc TEXT,record_json TEXT,record_sha256 TEXT);
        """)
        connection.execute("INSERT INTO simulated_topologies VALUES(?,?,?,?)", ("sandbox-line-3", 5, "fixture-time", json.dumps(topology)))
        for index, record in enumerate(records):
            connection.execute("INSERT INTO communicators VALUES(?,?,?,?,?)", ("sandbox-line-3", record["device_id"], record["node_id"], 1, json.dumps(record)))
            for revision in (0, 1):
                event = {"event_id": f"fixture-{index}-{revision}", "topology_id": "sandbox-line-3", "version_before": 1 + index * 2 + revision,
                         "version_after": 2 + index * 2 + revision, "event_kind": "communicator_saved", "created_at_utc": "fixture-time",
                         "payload": {"records": [{**record, "revision": revision, "name": record["node_id"] if revision == 0 else record["name"]}],
                                     "physical_action_performed": False}}
                connection.execute("INSERT INTO topology_events VALUES(?,?,?,?,?,?,?,?,?)", (1 + index * 2 + revision, event["event_id"],
                    "sandbox-line-3", event["version_before"], event["version_after"], event["event_kind"], campaign._record_hash(event),
                    event["created_at_utc"], json.dumps(event)))
        connection.execute("INSERT INTO fpga_devices VALUES(?,?,?,?,?)", ("sandbox-line-3", "FPGA-FIXTURE", 5, json.dumps(configuration), configuration_hash))
        connection.execute("INSERT INTO fpga_device_receipts VALUES(?,?,?,?,?,?,?,?)", (1, "sandbox-line-3", "FPGA-FIXTURE", 5,
            receipt["kind"], receipt["at_utc"], json.dumps(receipt), campaign._record_hash(receipt)))
    return report, directory, database


def _case(report, ident):
    return next(row["evidence"] for row in report["cases"] if row["id"] == ident)


def test_material_checker_verifies_isolated_records_and_real_artifact_bytes_read_only(material_evidence):
    report, directory, database = material_evidence
    before = campaign.sha256(database)
    assert campaign.validate_case_evidence(report, directory) is True
    assert campaign.sha256(database) == before


@pytest.mark.parametrize("ident", list(campaign.CASE_CRITERIA))
@pytest.mark.parametrize("arbitrary", [{}, "truthy", {"arbitrary": True}])
def test_all_cases_reject_arbitrary_or_empty_material_evidence(material_evidence, ident, arbitrary):
    report, directory, _ = material_evidence
    next(row for row in report["cases"] if row["id"] == ident)["evidence"] = arbitrary
    with pytest.raises(ValueError):
        campaign.validate_case_evidence(report, directory)


@pytest.mark.parametrize("name", ["detail-CBPF.png", "qkd-evidence.json", "state/demo/brazil-institutions-v1/sandbox_journal.sqlite"])
def test_absent_auxiliary_file_is_not_demonstrated_not_success(material_evidence, name):
    report, directory, _ = material_evidence
    (directory / name).unlink()
    with pytest.raises(FileNotFoundError):
        campaign.validate_case_evidence(report, directory)


def test_artifact_digest_recomputed_and_tampered_bytes_refused(material_evidence):
    report, directory, _ = material_evidence
    (directory / "detail-CBPF.png").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest mismatch"):
        campaign.validate_case_evidence(report, directory)


def test_nonimage_bytes_cannot_pass_even_with_matching_artifact_digest(material_evidence):
    report, directory, _ = material_evidence
    _artifact(report, directory, "detail-CBPF.png", b"not a PNG")
    with pytest.raises(ValueError, match="PNG signature"):
        campaign.validate_case_evidence(report, directory)


@pytest.mark.parametrize("path", ["../outside.png", "state/../../../outside.png", "..\\outside.png", "C:\\outside.png", "/outside.png"])
def test_artifact_paths_cannot_escape_isolated_output(material_evidence, path):
    report, directory, _ = material_evidence
    report["artifacts"][path] = {"sha256": "a" * 64, "scope": "native_application_window"}
    with pytest.raises(ValueError, match="escapes"):
        campaign.validate_case_evidence(report, directory)


def test_missing_required_artifact_reference_cannot_hide_missing_capture(material_evidence):
    report, directory, _ = material_evidence
    del report["artifacts"]["detail-CBPF.png"]
    with pytest.raises(ValueError, match="references absent"):
        campaign.validate_case_evidence(report, directory)


@pytest.mark.parametrize("change", ["nonfinite", "boolean_tick", "pending", "stale_generation", "missing_widget_check", "invalid_digest"])
def test_detail_completion_requires_received_timestamps_and_actual_materialization_schema(material_evidence, change):
    report, directory, _ = material_evidence
    value = _case(report, "ground_details")["readbacks"][0]["readback"]
    if change == "nonfinite":
        value["network_time_s"] = float("nan")
    elif change == "boolean_tick":
        value["presented_tick"] = True
    elif change == "pending":
        value["presentation"]["pending_items"] = 1
    elif change == "stale_generation":
        value["presentation"]["rendered_generation"] = 1
    elif change == "missing_widget_check":
        del value["checks"]["identity_widget_values"]
    else:
        value["widget_content_sha256"] = "truthy"
    with pytest.raises(ValueError):
        campaign.validate_case_evidence(report, directory)


@pytest.mark.parametrize("change", ["wrong_scope", "arbitrary_health", "different_communicator", "duplicate_json", "nonfinite_json"])
def test_export_rejected_after_matching_digest_if_material_payload_is_invalid(material_evidence, change):
    report, directory, _ = material_evidence
    path = directory / "qkd-evidence.json"
    payload = json.loads(path.read_bytes())
    if change == "wrong_scope":
        payload["scope"] = "hardware"
    elif change == "arbitrary_health":
        payload["health"] = {"arbitrary": True}
    elif change == "different_communicator":
        payload["topology"]["topology"]["nodes"][0]["telemetry"]["communicator"]["name"] = "changed"
    if change == "duplicate_json":
        data = b'{"scope":"qkd","scope":"qkd"}'
    elif change == "nonfinite_json":
        data = b'{"arbitrary":NaN}'
    else:
        data = json.dumps(payload).encode()
    _artifact(report, directory, "qkd-evidence.json", data, "unsigned_qkd_evidence")
    _case(report, "evidence_export")["sha256"] = campaign.sha256(path)
    with pytest.raises(ValueError):
        campaign.validate_case_evidence(report, directory)


@pytest.mark.parametrize("statement", [
    "UPDATE communicators SET revision=2 WHERE node_id='TRL4-NATIVE-GROUND'",
    "UPDATE communicators SET record_json='{}' WHERE node_id='TRL4-NATIVE-SAT'",
    "UPDATE simulated_topologies SET topology_version=3",
    "DELETE FROM topology_events WHERE sequence=1",
    "UPDATE topology_events SET event_sha256='bad' WHERE sequence=2",
    "UPDATE fpga_devices SET configuration_sha256='bad'",
    "UPDATE fpga_devices SET revision=4",
    "UPDATE fpga_device_receipts SET tick=999",
    "UPDATE fpga_device_receipts SET record_json='{}'",
    "DELETE FROM fpga_device_receipts",
])
def test_persisted_record_event_and_receipt_tampering_cannot_qualify(material_evidence, statement):
    report, directory, database = material_evidence
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(statement)
    with pytest.raises(ValueError):
        campaign.validate_case_evidence(report, directory)


@pytest.mark.parametrize("ident,field,value", [
    ("native_entry", "nodes", True), ("geo_lifecycle", "map_camera", [0., float("inf"), 1.]),
    ("communicator_refusal", "commands_dispatched", 1), ("fpga_emulated", "temperature_c", 25.),
    ("fpga_emulated", "last_feedback", None), ("ml_cancel_confirm_restore", "ml_control_enabled", True),
    ("physical_boundary", "physical_action_performed", 0), ("cleanup", "login_worker_closed", False),
])
def test_material_evidence_rejects_nonfinite_and_unattested_operational_claims(material_evidence, ident, field, value):
    report, directory, _ = material_evidence
    _case(report, ident)[field] = value
    with pytest.raises(ValueError):
        campaign.validate_case_evidence(report, directory)


def test_validator_does_not_import_qt_or_app_and_never_opens_sqlite_writable():
    tree = ast.parse((ROOT / "tools/run_trl4_console_campaign.py").read_text(encoding="utf-8"))
    top_imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert all(not any(alias.name.startswith(("PySide6", "quantum_autopilot")) for alias in node.names) for node in top_imports)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_persistent_evidence")
    constants = [node.value for node in ast.walk(function) if isinstance(node, ast.Constant)]
    assert "?mode=ro" in constants


def test_full_view_models_preserve_parallel_and_disabled_links():
    from types import SimpleNamespace

    nodes = [SimpleNamespace(id=name) for name in ("CIMATEC", "CBPF")]
    links = [SimpleNamespace(id=name, key=name, a="CIMATEC", b="CBPF", qber=.02)
             for name in ("parallel-1", "parallel-2", "disabled")]
    net = SimpleNamespace(nodes=nodes, links=links)
    matrix = SimpleNamespace(net=net, link_keys=lambda: set())  # MULTI cells have no single key.
    graph = SimpleNamespace(nodes=nodes, links=links[:2], all_links=links)
    geo = SimpleNamespace(net=net)
    assert campaign.topology_models_match(matrix, graph, geo, net)
    graph.all_links = links[:-1]
    assert not campaign.topology_models_match(matrix, graph, geo, net)
    graph.all_links = links
    matrix.net = SimpleNamespace(nodes=nodes, links=links[:1])
    assert not campaign.topology_models_match(matrix, graph, geo, net)


def test_hidden_view_metrics_may_coalesce_but_active_view_must_match_received_net():
    from types import SimpleNamespace

    nodes = [SimpleNamespace(id="CBPF")]
    current = SimpleNamespace(id="link", key="link", a="CBPF", b="CBPF", qber=.02)
    previous = SimpleNamespace(id="link", key="link", a="CBPF", b="CBPF", qber=.03)
    net = SimpleNamespace(nodes=nodes, links=[current])
    matrix = SimpleNamespace(net=net)
    graph = SimpleNamespace(nodes=nodes, all_links=[previous])
    geo = SimpleNamespace(net=net)
    assert campaign.topology_models_match(matrix, graph, geo, net, active="MATRIX")
    assert not campaign.topology_models_match(matrix, graph, geo, net, active="GRAPH")


def _pure_driver_methods(*names):
    """Compile selected driver methods without importing/running Qt."""
    tree = ast.parse((ROOT / "tools/run_trl4_console_campaign.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    namespace = {"topology_models_match": campaign.topology_models_match}
    exec(compile(ast.Module(body=[functions[name] for name in names], type_ignores=[]), "<pure-driver-fixture>", "exec"), namespace)
    return namespace


def test_driver_restores_geo_after_matrix_readback_failure_and_later_case_recovers_it():
    from types import SimpleNamespace

    geo = SimpleNamespace(mode="map", renderer=object(), resource_stats=lambda: {"retiring": False})
    topo = SimpleNamespace(view="GEO", geo=geo, toggles={name: name for name in ("MATRIX", "GRAPH", "GEO")},
        matrix=SimpleNamespace(node_ids=lambda: []), _snap=SimpleNamespace(topology=SimpleNamespace(nodes=[SimpleNamespace(id="CBPF")])))
    clicks = []

    class Driver:
        window = SimpleNamespace(selection=SimpleNamespace(kind="node", id="CBPF"))

        def page(self, key):
            assert key == "topology"
            return topo

        def click(self, button):
            clicks.append(button)
            topo.view = button

        def wait(self, predicate):
            assert predicate()

        def check(self, value):
            if not value:
                raise campaign.CaseFailure("fixture_readback_failure")

    methods, driver = _pure_driver_methods("topology_views", "mode"), Driver()
    with pytest.raises(campaign.CaseFailure, match="fixture_readback_failure"):
        methods["topology_views"](driver)
    assert clicks == ["MATRIX", "GEO"] and topo.view == "GEO"
    topo.view = "GRAPH"  # Independent case restores controls even after another failure.
    assert methods["mode"](driver, "map") is geo
    assert topo.view == "GEO" and clicks[-1] == "GEO"


def test_key_audit_driver_reads_kv_values_and_normalizes_snapshot_sequences():
    from types import SimpleNamespace

    class Values:
        def __init__(self, rows):
            self.rows = rows

        def values(self):
            return self.rows

    class Text:
        def __init__(self, value):
            self.value = value

        def text(self):
            return self.value

    keys_data = SimpleNamespace(scope_note="fixture", provenance=(SimpleNamespace(key="Source", value="simulated"),), sessions=("session",))
    ledger = SimpleNamespace(chain_verified="verified", chain_note="fixture scope", entries=("entry",))
    keys = SimpleNamespace(_snap=SimpleNamespace(keys=keys_data), scope_note=Text("fixture"),
                           provenance=Values({"Source": "simulated"}), _sessions=["session"])
    audit = SimpleNamespace(_snap=SimpleNamespace(ledger=ledger), chain_label=Text("verified"), chain_note=Text("fixture scope"),
                            _entries=["entry"], export=Values({"Signature": "unsigned · local session"}))

    class Driver:
        provider = SimpleNamespace(security=SimpleNamespace(audit=SimpleNamespace(verify=lambda: {"valid": True})))

        def page(self, key):
            return keys if key == "keys" else audit

        def check(self, value):
            assert value is True

    result = _pure_driver_methods("key_audit")["key_audit"](Driver())
    assert result == {"key_scope": "fixture", "audit_chain": {"valid": True}, "export_signature": "unsigned · local session"}
