"""Functional Windows Qt campaign using ordinary boot and current OS-session login.

Only input is simulated. This source campaign supplies no skip-auth flag,
principal, credentials, service bootstrap, scientific default or result mock.
Collection deadlines do not qualify performance, hardware or calibration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import sqlite3
import struct
import subprocess
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "orion.trl4.console-campaign.v1"
PAGES = ("topology", "telemetry", "keys", "node", "communicators", "fpga", "audit")
CASE_CRITERIA = {
    "native_entry": ("backend_verified", "current_windows_session", "three_received_tick_advances", "simulated_real_pipeline"),
    "seven_screens": ("all_routes", "received_snapshot", "received_content"),
    "topology_views": ("same_nodes", "same_links", "selection_preserved"),
    "ground_details": ("clicked_identity", "all_received_fields", "no_pending_sections"),
    "satellite_details": ("clicked_identity", "all_received_fields", "no_pending_sections"),
    "geo_lifecycle": ("exclusive_renderer", "selection_preserved", "camera_preserved", "retired_timer_stopped"),
    "communicator_dynamic": ("satellite_orbit_visible", "terrestrial_orbit_absent", "manual_protocol_preserved"),
    "communicator_terrestrial": ("created_exact_readback", "edited_exact_readback", "persisted_record", "version_advanced"),
    "communicator_satellite": ("created_exact_readback", "edited_exact_readback", "persisted_record", "version_advanced"),
    "communicator_refusal": ("partial_coordinates_refused", "duplicate_refused", "no_persisted_change"),
    "fpga_emulated": ("configuration_readback", "connection_readback", "processed_receipt", "hardware_metrics_absent"),
    "key_audit_readback": ("keys_match_publication", "audit_match_publication", "unsigned_scope_visible"),
    "evidence_export": ("real_qt_picker", "chosen_new_destination", "source_completed", "unsigned_scope", "no_overwrite"),
    "ml_cancel_confirm_restore": ("cancel_no_command", "confirmed_on", "confirmed_off", "off_restored", "detector_independent"),
    "physical_boundary": ("actuation_locked", "no_physical_executor", "no_physical_action"),
    "cleanup": ("window_closed", "provider_stopped", "renderers_retired", "timers_stopped", "login_worker_closed"),
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes(root=ROOT):
    files = sorted((root / "src" / "quantum_autopilot").rglob("*.py"))
    files += [root / "tools" / "run_trl4_console_campaign.py"]
    return {path.relative_to(root).as_posix(): sha256(path) for path in files}


def environment():
    """Allow-list; never serialize environment variables or credentials."""
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "os": platform.system(), "machine": platform.machine(), "executable_sha256": sha256(sys.executable)}


def case_result(ident, checks=None, evidence=None, *, status="passed", reason=None):
    if ident not in CASE_CRITERIA or status not in ("passed", "failed", "not_demonstrated"):
        raise ValueError("invalid campaign case")
    checks = dict(checks or {})
    if any(type(value) is not bool for value in checks.values()):
        raise ValueError("case checks must be booleans")
    if status == "passed" and (set(checks) != set(CASE_CRITERIA[ident]) or not all(checks.values())):
        raise ValueError("incomplete successful case")
    return {"id": ident, "status": status, "checks": checks, "evidence": evidence or {}, "reason": reason}


def authentic_entry(value):
    if type(value) is not dict:
        return False
    return (value.get("method") == "current_windows_session" and type(value.get("request_count")) is int
            and value["request_count"] == 1 and value.get("backend_verified") is True
            and value.get("authentication_bypassed") is False and value.get("credentials_supplied") is False
            and value.get("principal_source") == "os" and type(value.get("tick_advances")) is int
            and value["tick_advances"] >= 3)


def report_passes(report):
    if type(report) is not dict:
        return False
    cases = report.get("cases", [])
    if (not isinstance(cases, list) or len(cases) != len(CASE_CRITERIA)
            or any(type(row) is not dict or type(row.get("id")) is not str for row in cases)
            or {row.get("id") for row in cases} != set(CASE_CRITERIA)):
        return False
    for row in cases:
        checks = row.get("checks")
        if (row.get("status") != "passed" or type(checks) is not dict
                or set(checks) != set(CASE_CRITERIA[row["id"]]) or any(value is not True for value in checks.values())):
            return False
    return (report.get("schema") == SCHEMA and report.get("qt_platform") == "windows"
            and authentic_entry(report.get("actual_authentication")) and report.get("source_unchanged") is True
            and bool(report.get("source_hashes")) and report.get("source_hashes") == report.get("source_hashes_end")
            and report.get("physical_action_performed") is False and report.get("hardware_validated") is False
            and report.get("isolated_diagnostic") is True and report.get("frozen") is False
            and report.get("application_exit_code") == 0 and not report.get("errors"))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _object(value, fields, name, *, exact=False):
    _require(type(value) is dict and (set(value) == set(fields) if exact else set(fields) <= set(value)),
             f"{name}: required object fields")
    return value


def _integer(value, name, minimum=0):
    _require(type(value) is int and value >= minimum, f"{name}: integer required")
    return value


def _number(value, name, minimum=0):
    _require(type(value) in (int, float) and math.isfinite(value) and value >= minimum,
             f"{name}: finite number required")
    return value


def _text(value, name):
    _require(type(value) is str and bool(value.strip()), f"{name}: nonempty text required")
    return value


def _hash(value, name):
    _require(type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None,
             f"{name}: SHA256 required")
    return value


def _json(value, name):
    def pairs(items):
        result = {}
        for key, item in items:
            _require(key not in result, f"{name}: duplicate JSON field")
            result[key] = item
        return result

    def constant(_):
        raise ValueError(f"{name}: nonfinite JSON value")

    def number(value):
        parsed = float(value)
        _require(math.isfinite(parsed), f"{name}: nonfinite JSON value")
        return parsed

    try:
        return json.loads(value, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)
    except (UnicodeError, json.JSONDecodeError, TypeError) as error:
        raise ValueError(f"{name}: invalid JSON") from error


def _record_hash(value, *, ascii_text=False):
    return hashlib.sha256(json.dumps(value, ensure_ascii=ascii_text, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _contained_file(directory, value):
    _text(value, "artifact path")
    # Reject traversal before resolution, including Windows separators when the
    # assessor runs on another OS; resolve also catches symlinks and junctions.
    parts = value.replace("\\", "/").split("/")
    _require(not Path(value).is_absolute() and not re.match(r"^[A-Za-z]:", value)
             and not value.startswith(("/", "\\")) and ".." not in parts,
             "artifact path escapes campaign directory")
    path = (directory / value).resolve()
    _require(path.is_relative_to(directory), "artifact path escapes campaign directory")
    if not path.is_file():
        raise FileNotFoundError(f"campaign artifact absent: {value}")
    return path


def _png(data, name):
    _require(data[:8] == b"\x89PNG\r\n\x1a\n", f"{name}: PNG signature required")
    offset, chunks = 8, []
    while offset < len(data):
        _require(offset + 12 <= len(data), f"{name}: truncated PNG")
        length = struct.unpack_from(">I", data, offset)[0]
        end = offset + 12 + length
        _require(end <= len(data), f"{name}: truncated PNG chunk")
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        _require(zlib.crc32(kind + payload) == crc, f"{name}: PNG chunk integrity")
        if not chunks:
            _require(kind == b"IHDR" and length == 13, f"{name}: PNG header required")
            width, height = struct.unpack_from(">II", payload)
            _require(0 < width <= 32768 and 0 < height <= 32768, f"{name}: PNG dimensions")
        chunks.append(kind)
        offset = end
        if kind == b"IEND":
            _require(length == 0 and offset == len(data), f"{name}: invalid PNG ending")
            break
    _require(b"IDAT" in chunks and chunks[-1:] == [b"IEND"], f"{name}: PNG image required")


def _geo_resources(value, *, active):
    fields = ("mode", "created", "destroyed", "renderer_instances", "active_renderers",
              "retiring", "orbit_timer_active", "transition_raster")
    _object(value, fields, "GEO resources", exact=True)
    _require(value["mode"] in ("map", "globe"), "GEO renderer mode")
    for key in fields[1:5]:
        _integer(value[key], "GEO " + key)
    _require(value["renderer_instances"] == value["created"] - value["destroyed"] == active
             and value["active_renderers"] == active and value["retiring"] is False,
             "GEO renderer lifecycle mismatch")
    for key in ("orbit_timer_active", "transition_raster"):
        _require(type(value[key]) is bool, "GEO timer/raster boolean required")
    if not active or value["mode"] == "map":
        _require(value["orbit_timer_active"] is False, "GEO retired/map orbit timer active")
    if not active:
        _require(value["transition_raster"] is False, "GEO cleanup retained transition raster")


def _detail_readback(value, *, satellite):
    fields = ("state", "ready", "complete", "mode", "presentation", "checks", "presented_tick",
              "source_kind", "network_time_s", "failed_checks", "widget_content_sha256", "widget_field_counts",
              "section_status_supported")
    _object(value, fields, "detail readback")
    _require(value["ready"] is True and value["complete"] is True and value["failed_checks"] == []
             and value["mode"] == "asynchronous-contract" and value["section_status_supported"] is True,
             "detail readback incomplete")
    _require(value["state"] == "ready", "detail state")
    _integer(value["presented_tick"], "detail tick")
    _number(value["network_time_s"], "detail network time")
    _require(value["source_kind"] == "simulated", "detail source kind")
    status = _object(value["presentation"], ("ready", "presentation_kind", "generation", "rendered_generation",
        "presented_tick", "source_kind", "network_time_s", "pending", "pending_items", "pending_core_rows",
        "expected_link_keys", "completed_link_keys", "expected_sections", "completed_sections", "error"), "detail presentation")
    _require(status["ready"] is True and status["presentation_kind"] == ("satellite" if satellite else "ground")
             and status["error"] == "", "detail presentation state")
    _integer(status["generation"], "detail generation")
    _require(type(status["rendered_generation"]) is int and status["rendered_generation"] == status["generation"],
             "detail stale generation")
    for key in ("pending", "pending_items", "pending_core_rows"):
        _require(type(status[key]) is int and status[key] == 0, "detail pending work")
    for key in ("presented_tick", "source_kind", "network_time_s"):
        _require(type(status[key]) is type(value[key]) and status[key] == value[key], "detail publication timestamp")
    for label in ("link_keys", "sections"):
        expected, completed = status["expected_" + label], status["completed_" + label]
        _require(type(expected) is list and type(completed) is list and completed == expected
                 and all(type(item) is str and item for item in expected) and len(set(expected)) == len(expected),
                 "detail materialization list")
    _require(bool(status["expected_sections"]), "detail sections absent")
    checks = _object(value["checks"], ("presentation_pending_empty", "presentation_link_completion",
        "presentation_section_completion", "presentation_core_rows_empty", "presentation_timestamp", "title"), "detail checks")
    expected = ("satellite_snapshot_values", "orbital_widget_values", "bridge_widget_values", "isl_widget_values",
                "operations_widget_values", "station_widget_values", "source_provenance_visible", "distinct_channels_visible") if satellite else (
                "live_node_metadata", "identity_widget_values", "state_widget_values", "calibration_widget_values",
                "calibration_links_widget_values", "route_widget_values", "connection_widget_values", "legend_link_keys",
                "legend_widget_values", "chart_link_keys", "chart_received_values", "chart_source_notes", "chart_network_time")
    _require(set(expected) <= set(checks) and all(item is True for item in checks.values()), "detail widget checks")
    counts = _object(value["widget_field_counts"], ("orbital", "bridge", "isl", "operations", "stations") if satellite else (
                     "identity", "state", "calibration", "calibration_links", "routes", "legend", "cards"), "detail field counts")
    for key, count in counts.items():
        _text(key, "detail field name")
        _integer(count, "detail field count")
    _require(counts["orbital" if satellite else "identity"] > 0, "detail fields absent")
    # Raw widget text is not serialized by the driver; validate this digest's
    # format only. This deliberately does not claim an independent recompute.
    _hash(value["widget_content_sha256"], "detail widget digest")


def _persistent_evidence(cases, directory, exported):
    path = _contained_file(directory, "state/demo/brazil-institutions-v1/sandbox_journal.sqlite")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for case_id, physical_type in (("communicator_terrestrial", "installation"), ("communicator_satellite", "satellite")):
            value = cases[case_id]
            rows = connection.execute("SELECT * FROM communicators WHERE node_id=?", (value["node_id"],)).fetchall()
            _require(len(rows) == 1, f"{case_id}: persisted communicator absent or ambiguous")
            row = rows[0]
            record = _object(_json(row["record_json"], case_id), ("device_id", "node_id", "revision", "name", "physical_type",
                             "integration", "location", "orbit"), case_id)
            _integer(record["revision"], case_id + " persisted revision")
            _require(record["device_id"] == row["device_id"] == value["node_id"] == record["node_id"] == row["node_id"]
                     and record["revision"] == row["revision"] == value["edited_revision"]
                     and record["name"] == value["node_id"] + " updated" and record["physical_type"] == physical_type
                     and _record_hash(record, ascii_text=True) == value["persisted_record_sha256"],
                     f"{case_id}: persisted communicator mismatch")
            _object(record["integration"], ("mode", "data_origin"), case_id + " integration")
            _require(record["integration"]["mode"] == record["integration"]["data_origin"] == "simulated",
                     f"{case_id}: communicator origin")
            _object(record["location"], (), case_id + " location")
            _object(record["orbit"], (), case_id + " orbit")
            if physical_type == "satellite":
                _require(record["location"] == {} and record["orbit"].get("compatible_stations") == ["CBPF", "CIMATEC"],
                         "satellite communicator geometry")
            else:
                _require(record["location"].get("latitude") == -12.95 and record["location"].get("longitude") == -38.5
                         and record["orbit"] == {}, "terrestrial communicator geometry")
            topology_row = connection.execute("SELECT * FROM simulated_topologies WHERE topology_id=?", (row["topology_id"],)).fetchone()
            _require(topology_row is not None, f"{case_id}: topology absent")
            topology = _object(_json(topology_row["state_json"], "persisted topology"), ("topology_id", "topology_version", "nodes"), "topology")
            _require(topology["topology_id"] == row["topology_id"]
                     and topology["topology_version"] == topology_row["topology_version"] >= value["topology_version_after"],
                     f"{case_id}: persisted topology version")
            _require(type(topology["nodes"]) is list and all(type(node) is dict for node in topology["nodes"]), "topology nodes")
            nodes = [node for node in topology["nodes"] if node.get("node_id") == value["node_id"]]
            _require(len(nodes) == 1 and type(nodes[0].get("telemetry")) is dict
                     and nodes[0]["telemetry"].get("communicator") == record, f"{case_id}: topology readback mismatch")
            _require(all(node.get("node_id") != "TRL4-NATIVE-INVALID" for node in topology["nodes"]), "refused topology node persisted")
            exported_nodes = [node for node in exported["topology"]["topology"]["nodes"] if node.get("node_id") == value["node_id"]]
            _require(len(exported_nodes) == 1 and type(exported_nodes[0].get("telemetry")) is dict
                     and exported_nodes[0]["telemetry"].get("communicator") == record, f"{case_id}: exported readback mismatch")
            versions = []
            for event_row in connection.execute("SELECT * FROM topology_events WHERE topology_id=? AND event_kind='communicator_saved' ORDER BY sequence",
                                                 (row["topology_id"],)):
                event = _object(_json(event_row["event_json"], "communicator event"), ("event_id", "topology_id", "version_before",
                    "version_after", "event_kind", "created_at_utc", "payload"), "communicator event")
                _require(_record_hash(event) == event_row["event_sha256"] and all(event[key] == event_row[key] for key in (
                    "event_id", "topology_id", "version_before", "version_after", "event_kind", "created_at_utc")), "communicator event integrity")
                payload = _object(event["payload"], ("records", "physical_action_performed"), "communicator event payload")
                _require(payload["physical_action_performed"] is False and type(payload["records"]) is list
                         and all(type(item) is dict for item in payload["records"]), "communicator event records")
                for item in payload["records"]:
                    if item.get("node_id") == value["node_id"]:
                        versions.append((item.get("revision"), event["version_before"], event["version_after"], item))
            _require(len(versions) == 2 and versions[0][:3] == (value["created_revision"], value["topology_version_before"], value["topology_version_before"] + 1)
                     and versions[1][:3] == (value["edited_revision"], value["topology_version_after"] - 1, value["topology_version_after"])
                     and versions[0][3] == {**record, "revision": value["created_revision"], "name": value["node_id"]}
                     and versions[1][3] == record, f"{case_id}: creation/edit events do not attest readback")
        refused = cases["communicator_refusal"]
        _require(refused["topology_version"] == cases["communicator_satellite"]["topology_version_after"], "refusal changed topology version")
        _require(connection.execute("SELECT COUNT(*) FROM communicators WHERE node_id='TRL4-NATIVE-INVALID'").fetchone()[0] == 0,
                 "refused communicator persisted")
        fpga = cases["fpga_emulated"]
        rows = connection.execute("SELECT * FROM fpga_devices WHERE device_id=?", (fpga["device_id"],)).fetchall()
        _require(len(rows) == 1, "FPGA configuration absent or ambiguous")
        row = rows[0]
        config = _object(_json(row["configuration_json"], "FPGA configuration"), ("schema", "device_id", "revision", "adapter_id", "connected", "pipeline_cycles"), "FPGA configuration")
        _integer(config["revision"], "FPGA persisted revision", 1)
        _require(_record_hash(config) == row["configuration_sha256"] == fpga["configuration_sha256"]
                 and config["device_id"] == row["device_id"] == fpga["device_id"] and config["revision"] == row["revision"] == fpga["revisions"][-1]
                 and config["schema"] == "orion.fpga-devices.v1" and config["adapter_id"] == "simulated" and config["connected"] is True,
                 "FPGA persisted configuration mismatch")
        _integer(config["pipeline_cycles"], "FPGA pipeline cycles", 1)
        feedback = fpga["last_feedback"]
        rows = connection.execute("SELECT * FROM fpga_device_receipts WHERE topology_id=? AND device_id=? AND record_sha256=?",
                                  (row["topology_id"], fpga["device_id"], feedback["record_sha256"])).fetchall()
        _require(len(rows) == 1, "FPGA feedback not retained in isolated journal")
        receipt_row = rows[0]
        receipt = _json(receipt_row["record_json"], "FPGA persisted receipt")
        _require(_record_hash(receipt) == receipt_row["record_sha256"] == feedback["record_sha256"]
                 and {**receipt, "record_sha256": receipt_row["record_sha256"]} == feedback
                 and all(receipt[key] == receipt_row[key] for key in ("topology_id", "device_id", "tick", "kind", "at_utc")),
                 "FPGA persisted receipt integrity")
    except sqlite3.DatabaseError as error:
        raise ValueError("invalid isolated campaign journal") from error
    finally:
        connection.close()


def validate_case_evidence(report, directory):
    """Pure material-evidence check: True, ValueError, or missing-file error.

    Imports no application/Qt code and opens the isolated SQLite journal read
    only. Authenticity/source freeze remain external assessor responsibilities.
    Widget-content digests have no serialized raw body and are format-checked
    only; files, persisted configurations and receipts are recomputed.
    """
    _require(report_passes(report), "incomplete successful console report")
    directory = Path(directory).resolve(strict=True)
    _require(directory.is_dir(), "campaign directory required")
    cases = {row["id"]: row.get("evidence") for row in report["cases"]}
    for ident, value in cases.items():
        _require(type(value) is dict and bool(value), f"{ident}: structured evidence required")
    entry = _object(cases["native_entry"], ("input_kind", "source_kind", "nodes", "tick"), "native entry", exact=True)
    _require(entry["input_kind"] == "demo" and entry["source_kind"] == "simulated", "native entry source")
    _integer(entry["nodes"], "native entry nodes", 1)
    _integer(entry["tick"], "native entry tick")
    screens = _object(cases["seven_screens"], PAGES, "seven screens", exact=True)
    for page, row in screens.items():
        _object(row, ("tick", "source_kind"), page, exact=True)
        _integer(row["tick"], page + " tick")
        _require(row["source_kind"] == "simulated" and row["tick"] >= entry["tick"], "screen publication source/time")
    views = _object(cases["topology_views"], ("views", "nodes", "links"), "topology views", exact=True)
    _integer(views["nodes"], "topology nodes", 1)
    _integer(views["links"], "topology links")
    _require(views["views"] == ["MATRIX", "GRAPH", "GEO"] and views["nodes"] == entry["nodes"], "topology view identity")
    detail_ids = []
    for ident, satellite in (("ground_details", False), ("satellite_details", True)):
        value = _object(cases[ident], ("readbacks",), ident, exact=True)
        rows = value["readbacks"]
        _require(type(rows) is list and len(rows) == (1 if satellite else 2), "detail readback count")
        ids = []
        for row in rows:
            _object(row, ("node_id", "readback"), ident, exact=True)
            ids.append(_text(row["node_id"], "detail node id"))
            _detail_readback(row["readback"], satellite=satellite)
        _require(len(set(ids)) == len(ids) and (satellite or set(ids) == {"CIMATEC", "CBPF"}), "detail node identities")
        detail_ids.extend(ids)
    geo = _object(cases["geo_lifecycle"], ("resources", "map_camera"), "GEO lifecycle", exact=True)
    _require(type(geo["resources"]) is list and len(geo["resources"]) == 4, "GEO lifecycle transitions")
    previous = None
    for mode, value in zip(("globe", "map", "globe", "map"), geo["resources"]):
        _geo_resources(value, active=1)
        _require(value["mode"] == mode, "GEO transition mode")
        if previous is not None:
            _require(value["created"] == previous["created"] + 1 and value["destroyed"] == previous["destroyed"] + 1,
                     "GEO transition creation/disposal")
        previous = value
    camera = geo["map_camera"]
    if camera is not None:
        _require(type(camera) is list and len(camera) == 3, "GEO map camera")
        for value in camera:
            _number(value, "GEO camera coordinate", -math.inf)
        _require(-180 <= camera[0] <= 180 and -90 <= camera[1] <= 90 and camera[2] > 0, "GEO map camera bounds")
    dynamic = _object(cases["communicator_dynamic"], ("type_order", "manual_protocol"), "dynamic communicator", exact=True)
    _require(dynamic == {"type_order": ["satellite", "installation"], "manual_protocol": "BB84"}, "dynamic communicator fields")
    for ident, node_id in (("communicator_terrestrial", "TRL4-NATIVE-GROUND"), ("communicator_satellite", "TRL4-NATIVE-SAT")):
        value = _object(cases[ident], ("node_id", "created_revision", "edited_revision", "topology_version_before",
            "topology_version_after", "persisted_record_sha256", "engine_status", "physical_action_performed"), ident, exact=True)
        for key in ("created_revision", "edited_revision", "topology_version_before", "topology_version_after"):
            _integer(value[key], ident + " " + key)
        _require(value["node_id"] == node_id and value["created_revision"] == 0 and value["edited_revision"] == 1
                 and value["topology_version_after"] == value["topology_version_before"] + 2
                 and value["engine_status"] == "saved" and value["physical_action_performed"] is False, "communicator mutation evidence")
        _hash(value["persisted_record_sha256"], ident + " digest")
    refusal = _object(cases["communicator_refusal"], ("topology_version", "commands_dispatched"), "communicator refusal", exact=True)
    _integer(refusal["topology_version"], "refusal topology version")
    _require(type(refusal["commands_dispatched"]) is int and refusal["commands_dispatched"] == 0, "refusal dispatched a command")
    fpga = _object(cases["fpga_emulated"], ("device_id", "revisions", "configuration_sha256", "last_feedback", "measured_fpga_latency_s", "temperature_c"), "FPGA evidence", exact=True)
    _text(fpga["device_id"], "FPGA device")
    _hash(fpga["configuration_sha256"], "FPGA configuration digest")
    _require(type(fpga["revisions"]) is list and len(fpga["revisions"]) == 4, "FPGA revisions")
    for revision in fpga["revisions"]:
        _integer(revision, "FPGA revision", 1)
    _require(all(b > a for a, b in zip(fpga["revisions"], fpga["revisions"][1:]))
             and fpga["measured_fpga_latency_s"] is None and fpga["temperature_c"] is None, "FPGA revision/hardware scope")
    feedback = _object(fpga["last_feedback"], ("schema", "device_id", "topology_id", "tick", "at_utc", "kind", "body", "physical_action_performed", "record_sha256"), "FPGA feedback", exact=True)
    _hash(feedback["record_sha256"], "FPGA receipt digest")
    _integer(feedback["tick"], "FPGA feedback tick")
    for key in ("topology_id", "at_utc"):
        _text(feedback[key], "FPGA feedback " + key)
    _require(feedback["schema"] == "orion.fpga-receipt.v1" and feedback["device_id"] == fpga["device_id"]
             and feedback["kind"] == "processing_feedback" and feedback["physical_action_performed"] is False, "FPGA feedback scope")
    body = _object(feedback["body"], ("acquisitions", "configuration_sha256", "origin", "modeled_pipeline_latency_s", "measured_fpga_latency_s", "host_read_ms"), "FPGA feedback body")
    _require(body["configuration_sha256"] == fpga["configuration_sha256"] and body["origin"] == "simulated"
             and body["measured_fpga_latency_s"] is None and type(body["acquisitions"]) is list and bool(body["acquisitions"]), "FPGA processing body")
    _number(body["modeled_pipeline_latency_s"], "FPGA modeled latency")
    _number(body["host_read_ms"], "FPGA host read duration")
    processed = False
    for item in body["acquisitions"]:
        _object(item, ("channel_receipt", "processing_status"), "FPGA acquisition")
        channel = _object(item["channel_receipt"], ("status", "stages"), "FPGA paired intake")
        _require(type(channel["stages"]) is list and all(type(stage) is str and stage for stage in channel["stages"]),
                 "FPGA paired intake stages")
        processed |= item["processing_status"] == "processed" and channel["status"] == "received" and bool(channel["stages"])
    _require(processed, "FPGA has no processed acquisition")
    audit = _object(cases["key_audit_readback"], ("key_scope", "audit_chain", "export_signature"), "key/audit evidence", exact=True)
    _text(audit["key_scope"], "key scope")
    chain = _object(audit["audit_chain"], ("valid", "count", "head", "checkpoint_count", "uncheckpointed_records"), "audit chain", exact=True)
    for key in ("count", "checkpoint_count", "uncheckpointed_records"):
        _integer(chain[key], "audit " + key)
    _hash(chain["head"], "audit head")
    _require(chain["valid"] is True and chain["count"] == chain["checkpoint_count"] + chain["uncheckpointed_records"]
             and audit["export_signature"] == "unsigned · local session", "audit/key scope mismatch")
    export = _object(cases["evidence_export"], ("destination", "sha256", "file_picker", "completed_count", "format", "scope"), "export evidence", exact=True)
    _require(export["destination"] == "qkd-evidence.json" and export["format"] == "unsigned_console_evidence_export_v1"
             and export["scope"] == "qkd" and export["file_picker"] == "real Qt widgets on Windows; Win32 picker not assessed", "export scope/destination")
    _hash(export["sha256"], "export digest")
    _integer(export["completed_count"], "export completed count", 1)
    ml = _object(cases["ml_cancel_confirm_restore"], ("control_revisions", "ml_control_enabled", "detector_before", "detector_after", "scope"), "ML evidence", exact=True)
    _require(type(ml["control_revisions"]) is list and len(ml["control_revisions"]) == 2, "ML revisions")
    for value in ml["control_revisions"]:
        _integer(value, "ML revision", 1)
    _require(ml["control_revisions"][1] > ml["control_revisions"][0] and ml["ml_control_enabled"] is False
             and ml["detector_before"] == ml["detector_after"]
             and ml["scope"] == "ML network permission is distinct from inference/detection; optional model defaults unchanged", "ML restoration/scope")
    _require(type(ml["detector_before"]) is list, "ML detector readback")
    for row in ml["detector_before"]:
        _require(type(row) is list and len(row) == 2 and all(type(item) is str and item.strip() for item in row), "ML detector record")
    physical = _object(cases["physical_boundary"], ("physical_actuation", "physical_executor_available", "physical_action_performed"), "physical boundary", exact=True)
    _require(all(value is False for value in physical.values()), "physical boundary unlocked")
    cleanup = _object(cases["cleanup"], ("provider_running", "geo_resources", "login_worker_closed"), "cleanup", exact=True)
    _require(cleanup["provider_running"] is False and cleanup["login_worker_closed"] is True, "cleanup incomplete")
    _geo_resources(cleanup["geo_resources"], active=0)
    _require(cleanup["geo_resources"]["created"] == geo["resources"][-1]["created"], "cleanup renderer identity")
    artifacts = _object(report.get("artifacts"), (), "campaign artifacts")
    required = {"native-entry.png", "communicator-terrestrial.png", "communicator-satellite.png", "fpga-emulated.png", "qkd-evidence.json"}
    required.update("screen-" + page + ".png" for page in PAGES)
    required.update("detail-" + ident + ".png" for ident in detail_ids)
    _require(required <= set(artifacts), "required campaign artifact references absent")
    for name, row in artifacts.items():
        path = _contained_file(directory, name)
        _object(row, ("sha256", "scope"), "artifact reference", exact=True)
        _hash(row["sha256"], "artifact digest")
        data = path.read_bytes()
        _require(hashlib.sha256(data).hexdigest() == row["sha256"], "campaign artifact digest mismatch")
        if name.endswith(".png"):
            _require(row["scope"] == "native_application_window", "capture scope")
            _png(data, name)
        else:
            _require(name == export["destination"] and row["scope"] == "unsigned_qkd_evidence"
                     and row["sha256"] == export["sha256"], "export artifact reference mismatch")
    exported = _object(_json(_contained_file(directory, export["destination"]).read_bytes(), "export JSON"),
                       ("health", "topology", "events", "qkd_example", "format", "scope"), "export payload")
    _require(exported["format"] == export["format"] and exported["scope"] == export["scope"], "export payload scope")
    for field in ("health", "topology", "events"):
        _require(type(exported[field]) is dict and bool(exported[field]), "export payload empty/wrong structure")
    health = _object(exported["health"], ("status", "physical_read_only", "automation_allowed"), "exported health")
    _require(health["status"] == "ok" and health["physical_read_only"] is True and health["automation_allowed"] is False,
             "exported health scope")
    envelope = _object(exported["topology"], ("topology", "persisted", "physical_topology"), "exported topology envelope")
    _require(envelope["persisted"] is True and envelope["physical_topology"] is False, "exported topology origin")
    _object(envelope["topology"], ("nodes",), "exported topology")
    _require(type(envelope["topology"]["nodes"]) is list and bool(envelope["topology"]["nodes"])
             and all(type(node) is dict for node in envelope["topology"]["nodes"]), "exported topology nodes")
    _object(exported["events"], ("events",), "exported events")
    _require(type(exported["events"]["events"]) is list and all(type(item) is dict for item in exported["events"]["events"]),
             "exported event rows")
    # This export case asserts inclusion, not availability or scientific
    # validation of the optional published-cell example.
    _require(exported["qkd_example"] is None or type(exported["qkd_example"]) is dict, "exported optional QKD example")
    _persistent_evidence(cases, directory, exported)
    return True


def prepare_output(path):
    path = Path(path).expanduser().resolve()
    if path.exists():
        raise ValueError("a new output directory is required")
    path.mkdir(parents=True)
    (path / "state").mkdir()
    return path


def validate_worker_root(path):
    path = Path(path).resolve()
    manifest_path = path / "manifest-before.json"
    if not manifest_path.is_file() or (path / "report.json").exists():
        raise ValueError("fresh campaign manifest required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    state = path / "state"
    if (manifest.get("schema") != SCHEMA or manifest.get("provider") != "demo"
            or manifest.get("state_root") != str(state) or not state.is_dir() or any(state.iterdir())
            or manifest.get("console_cases") != list(CASE_CRITERIA)):
        raise ValueError("isolated empty campaign state required")
    return path


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")


def size_value(value):
    try:
        width, height = map(int, value.lower().split("x"))
    except (AttributeError, ValueError) as error:
        raise argparse.ArgumentTypeError("size must be WIDTHxHEIGHT") from error
    if not (640 <= width <= 7680 and 480 <= height <= 4320):
        raise argparse.ArgumentTypeError("client size is outside diagnostic bounds")
    return width, height


class NotDemonstrated(Exception):
    """Unavailable product prerequisites do not pass by substituting a mock."""


class CaseFailure(Exception):
    pass


def uses_stage_viewport(widget, stage):
    """Parented desktop dialogs retain native input despite proxy ancestry."""
    return widget.window() is stage.content and (widget is stage.content or stage.content.isAncestorOf(widget))


def topology_models_match(matrix, graph, geo, net, *, active=None):
    """Compare full shared models, including parallel and disabled connections.

    Matrix.link_keys intentionally omits MULTI cells; graph.links intentionally
    includes enabled fibres only. Neither is a full publication readback.
    """
    def identities(nodes, links):
        return tuple(node.id for node in nodes), tuple((link.id, link.key, link.a, link.b) for link in links)

    expected = identities(net.nodes, net.links)
    models = {"MATRIX": (matrix.net.nodes, matrix.net.links), "GRAPH": (graph.nodes, graph.all_links),
              "GEO": (geo.net.nodes, geo.net.links)}
    if any(identities(*model) != expected for model in models.values()):
        return False
    if active is not None:
        nodes, links = models[active]
        return tuple(nodes) == tuple(net.nodes) and tuple(links) == tuple(net.links)
    return True


def run_worker(output, size, timeout_s):
    os.environ["QT_QPA_PLATFORM"] = "windows"
    sys.path.insert(0, str(ROOT / "src"))
    from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit, QScrollArea
    import PySide6
    from quantum_autopilot.console.app import build_parser, run
    from quantum_autopilot.console.performance_validation import _choose_combo_by_keyboard, _node_presentation_evidence
    from quantum_autopilot.product import product_version

    # A genuine, editable picker: its return value is never patched. The backend
    # still authorizes the destination. The report excludes the Win32 picker.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
    before = source_hashes()
    began = time.monotonic()
    cases, errors, artifacts = [], [], {}
    auth = {"method": None, "request_count": 0, "backend_verified": False,
            "authentication_bypassed": False, "credentials_supplied": False, "principal_source": None,
            "tick_advances": 0, "received_ticks": []}

    class Driver:
        def attach(self, app, window):
            self.app, self.window = app, window
            self.commands, self.exported = [], []
            self.auth_failed, self.last_tick, self.provider = False, None, None
            window.resize(*size)
            window.provider.snapshot_changed.connect(self.received)
            window.provider.export_completed.connect(self.exported.append)
            if window._login_controller is not None:
                window._login_controller.succeeded.connect(self.verified)
                window._login_controller.failed.connect(lambda *_: setattr(self, "auth_failed", True))
            for screen in window.screens.values():
                screen.command.connect(lambda verb, target: self.commands.append((verb, target)))
            QTimer.singleShot(0, window, self.execute)

        def verified(self, principal):
            auth["backend_verified"], auth["principal_source"] = True, principal.source
            auth["principal_subject_sha256"] = hashlib.sha256(principal.subject.encode()).hexdigest()

        def received(self, snap):
            if self.window.current_screen in self.window.screens and snap.live is not None:
                tick = snap.live.tick
                if type(tick) is int and (self.last_tick is None or tick > self.last_tick):
                    auth["tick_advances"] += int(self.last_tick is not None)
                    self.last_tick = tick
                    if len(auth["received_ticks"]) < 8:
                        auth["received_ticks"].append({"tick": tick, "source_kind": snap.live.source_kind})

        def wait(self, predicate, *, seconds=45.):
            deadline = min(began + timeout_s, time.monotonic() + seconds)
            while time.monotonic() < deadline:
                self.pump()
                if predicate():
                    return
                time.sleep(.01)  # Release the GIL for ordinary acquisition.
            raise CaseFailure("functional_collection_deadline")

        def check(self, value):
            if value is not True:
                raise CaseFailure("functional_readback_mismatch")

        def settle(self):
            self.wait(lambda: not self.window.provider.busy)

        def pump(self):
            self.app.processEvents()
            # execute() is a long diagnostic callback. Ordinary event-loop turns
            # deliver these deferred deletions; our bounded nested waits must too.
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

        def click(self, widget, position=None):
            parent = widget.parentWidget()
            while parent is not None:
                if isinstance(parent, QScrollArea):
                    parent.ensureWidgetVisible(widget, 12, 16)
                parent = parent.parentWidget()
            self.app.processEvents()
            if not widget.isVisible() or not widget.isEnabled():
                raise CaseFailure("control_unavailable")
            stage = self.window.centralWidget()
            point = position or widget.rect().center()
            if uses_stage_viewport(widget, stage):
                point = stage.mapFromScene(stage.proxy.mapToScene(QPointF(widget.mapTo(stage.content, point))))
                if not stage.viewport().rect().contains(point):
                    raise CaseFailure("control_outside_viewport")
                QTest.mouseClick(stage.viewport(), Qt.MouseButton.LeftButton, pos=point)
            else:
                QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=point)
            self.app.processEvents()

        def text(self, widget, value):
            self.click(widget)
            QTest.keyClick(widget, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClicks(widget, str(value))
            self.check(widget.text() == str(value))

        def combo(self, widget, ident):
            if widget.findData(ident) < 0:
                raise NotDemonstrated("required_selection_unavailable")
            _choose_combo_by_keyboard(widget, ident, self.click)
            self.app.processEvents()
            self.check(widget.currentData() == ident)

        def page(self, key):
            self.click(self.window.rail.buttons[key])
            self.wait(lambda: self.window.current_screen == key and self.window.screens[key]._snap is not None)
            self.settle()
            return self.window.screens[key]

        def tab(self, tabs, index):
            self.click(tabs.tabBar(), tabs.tabBar().tabRect(index).center())
            self.check(tabs.currentIndex() == index)

        def snapshot(self):
            return self.window.snapshot

        def capture(self, ident):
            deadline = time.monotonic() + .15
            while time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            path = output / (ident + ".png")
            pixels = self.window.screen().grabWindow(int(self.window.winId()))
            self.check(not pixels.isNull() and pixels.save(str(path)))
            artifacts[path.name] = {"sha256": sha256(path), "scope": "native_application_window"}

        def case(self, ident, action):
            print("TRL4 console: " + ident, flush=True)
            try:
                evidence = action() or {}
            except NotDemonstrated as error:
                cases.append(case_result(ident, status="not_demonstrated", reason=str(error)))
            except Exception as error:
                reason = str(error) if isinstance(error, CaseFailure) else type(error).__name__
                cases.append(case_result(ident, status="failed", reason=reason))
                errors.append({"case": ident, "error_type": type(error).__name__, "reason": reason})
            else:
                cases.append(case_result(ident, dict.fromkeys(CASE_CRITERIA[ident], True), evidence))

        def enter(self):
            self.wait(lambda: self.window.current_screen == "boot", seconds=90.)
            self.check(self.window._skip_auth is False and self.window.boot._fixture_entry is False)
            self.wait(lambda: self.window.boot.os_session_button.isEnabled(), seconds=90.)
            auth["method"], auth["request_count"] = "current_windows_session", 1
            self.click(self.window.boot.os_session_button)
            self.wait(lambda: self.auth_failed or (auth["backend_verified"] and self.window.current_screen == "topology"), seconds=90.)
            self.check(not self.auth_failed and self.window._login_verified is True)
            self.wait(lambda: auth["tick_advances"] >= 3, seconds=90.)
            snap = self.snapshot()
            self.check(snap.input_kind == "demo" and snap.live.source_kind == "simulated")
            self.provider = self.window.provider.active.delegate
            self.check(self.provider.security is self.window._security and self.provider.service is not None)
            self.check(self.provider._identity.source == "os")
            self.check(hashlib.sha256(self.provider._identity.subject.encode()).hexdigest() == auth["principal_subject_sha256"])
            self.capture("native-entry")
            return {"input_kind": snap.input_kind, "source_kind": snap.live.source_kind,
                    "nodes": len(snap.live.nodes), "tick": snap.live.tick}

        def screens(self):
            evidence = {}
            self.check(set(self.window.screens) == set(PAGES))
            for key in PAGES:
                screen = self.page(key)
                snap = screen._snap
                self.check(snap.live is not None and snap.live.source_kind == "simulated")
                if key == "telemetry":
                    self.check([x.text() for x in screen.card_titles] == [x.label for x in snap.telemetry.headline])
                    self.check([x[0].text() for x in screen.card_hosts] == [x.value for x in snap.telemetry.headline])
                elif key == "communicators":
                    self.check(screen.records == snap.live.communicators)
                elif key == "fpga":
                    self.check(len(screen.fpga_devices._devices) == len(snap.live.technologies["fpga_devices"]["devices"]))
                elif key == "audit":
                    self.check(screen.chain_label.text() == snap.ledger.chain_verified)
                elif key == "keys":
                    self.check(screen.scope_note.text() == snap.keys.scope_note)
                elif key == "topology":
                    self.check(set(screen.matrix.node_ids()) == {x.id for x in snap.topology.nodes})
                elif key == "node":
                    self.check({x["id"] for x in screen._base_nodes} == {x.id for x in snap.topology.nodes})
                evidence[key] = {"tick": snap.live.tick, "source_kind": snap.live.source_kind}
                self.capture("screen-" + key)
            return evidence

        def topology_views(self):
            topo = self.page("topology")
            selection = (self.window.selection.kind, self.window.selection.id)
            try:
                for mode in ("MATRIX", "GRAPH", "GEO"):
                    self.click(topo.toggles[mode])
                    self.wait(lambda: topo.view == mode)
                    self.check(set(topo.matrix.node_ids()) == {x.id for x in topo._snap.topology.nodes})
                    self.check(topology_models_match(topo.matrix, topo.graph, topo.geo, topo._net, active=mode))
                    self.check((self.window.selection.kind, self.window.selection.id) == selection)
            finally:
                if topo.view != "GEO":
                    self.click(topo.toggles["GEO"])
                    self.wait(lambda: topo.view == "GEO")
            return {"views": ["MATRIX", "GRAPH", "GEO"], "nodes": len(topo._net.nodes), "links": len(topo._net.links)}

        def mode(self, wanted):
            topo = self.page("topology")
            if topo.view != "GEO":
                self.click(topo.toggles["GEO"])
                self.wait(lambda: topo.view == "GEO")
            geo = topo.geo
            if geo.mode != wanted:
                self.click(geo.mode_button)
            self.wait(lambda: geo.mode == wanted and geo.renderer is not None and not geo.resource_stats()["retiring"])
            return geo

        def details(self, satellite=False):
            topo = self.page("topology")
            geo = self.mode("globe" if satellite else "map")
            ids = sorted(topo._net.satellites or {}) if satellite else [x for x in ("CIMATEC", "CBPF") if topo._net.node(x)]
            if not ids:
                raise NotDemonstrated("required_detail_node_unavailable")
            results = []
            for ident in ids[:1] if satellite else ids:
                for opened in list(topo.node_windows.windows.values()):
                    self.click(opened.close_button)
                if not satellite:
                    if geo.focus_node.currentData() == ident:
                        self.combo(geo.focus_node, "")
                    self.combo(geo.focus_node, ident)
                self.wait(lambda: geo.renderer is not None and not geo.resource_stats()["retiring"])
                renderer = geo.renderer
                if satellite:
                    item = next((x for x in renderer.orbital_items() if x["id"] == ident), None)
                    if item is None:
                        raise NotDemonstrated("satellite_handle_unavailable")
                    point = item["centre"].toPoint()
                else:
                    point = renderer.node_position(ident).toPoint()
                    self.check(renderer.hit(QPointF(point)) == ("node", ident))
                self.click(renderer, point)
                self.wait(lambda: ident in topo.node_windows.windows and topo.node_windows.windows[ident].presentation_status()["ready"])
                opened = topo.node_windows.windows[ident]
                self.check(self.window.selection.id == ident and opened.isVisible())
                readback = _node_presentation_evidence(opened, topo._snap, topo._net)
                self.check(readback["complete"] is True and all(readback["checks"].values()))
                self.check(opened.presentation_status()["pending_items"] == 0)
                self.capture("detail-" + ident)
                results.append({"node_id": ident, "readback": readback})
                self.click(opened.close_button)
            return {"readbacks": results}

        def geo_lifecycle(self):
            self.page("topology")
            geo = self.mode("map")
            selection = (self.window.selection.kind, self.window.selection.id)
            initial = geo.view_state()
            records = []
            for wanted in ("globe", "map", "globe", "map"):
                timer = getattr(geo.renderer, "_timer", None)
                geo = self.mode(wanted)
                self.app.processEvents()
                stats = geo.resource_stats()
                self.check(stats["renderer_instances"] == 1 and stats["active_renderers"] == 1)
                self.check((self.window.selection.kind, self.window.selection.id) == selection)
                if timer is not None:
                    try:
                        self.check(not timer.isActive())
                    except RuntimeError:
                        pass
                records.append(stats)
            self.check(geo.view_state() == initial)
            return {"resources": records, "map_camera": initial}

        def dynamic(self):
            form = self.page("communicators")
            self.click(form.new)
            for field in ("device_id", "node_id", "name"):
                self.text(form.fields[field], "TRL4-NATIVE-DRAFT")
            self.text(form.fields["protocols"], "BB84")
            self.combo(form.fields["physical_type"], "satellite")
            self.check(form.editor_tabs.isTabVisible(form.group_pages["orbit"]._tab_index))
            self.combo(form.fields["physical_type"], "installation")
            self.check(not form.editor_tabs.isTabVisible(form.group_pages["orbit"]._tab_index))
            self.check("BB84" in form.fields["protocols"].text())
            self.check(form.payload()["orbit"] == {})
            return {"type_order": ["satellite", "installation"], "manual_protocol": "BB84"}

        def register(self, satellite=False):
            form = self.page("communicators")
            ident = "TRL4-NATIVE-SAT" if satellite else "TRL4-NATIVE-GROUND"
            self.click(form.new)
            for field in ("device_id", "node_id", "name"):
                self.text(form.fields[field], ident)
            self.combo(form.fields["physical_type"], "satellite" if satellite else "installation")
            if satellite:
                self.tab(form.editor_tabs, form.group_pages["orbit"]._tab_index)
                self.text(form.group_fields["orbit"]["compatible_stations"], "CBPF, CIMATEC")
            else:
                self.tab(form.editor_tabs, form.where._tab_index)
                for field, value in (("latitude", "-12.95"), ("longitude", "-38.5")):
                    self.text(form.fields[field], value)
            created = form.payload()
            version = form._snap.topology.version
            self.click(form.save)
            self.wait(lambda: form.pending is None and ident in form.records and not self.window.provider.busy)
            self.check(form.records[ident] == created and "CONFIRMADO" in form.message.text())
            self.check(form._snap.topology.version > version)
            self.tab(form.editor_tabs, 0)
            self.text(form.fields["name"], ident + " updated")
            expected = {**form.payload(), "revision": created["revision"] + 1}
            self.click(form.save)
            self.wait(lambda: form.pending is None and form.records.get(ident) == expected and not self.window.provider.busy)
            self.click(form.reload)
            self.check(form.fields["name"].text() == expected["name"])
            persisted = self.provider.service.topology_store.get(form._snap.topology.id)
            self.check(persisted.node(ident).telemetry["communicator"] == expected)
            result = self.provider.last_network_result
            self.check(result["physical_action_performed"] is False)
            self.capture("communicator-" + ("satellite" if satellite else "terrestrial"))
            return {"node_id": ident, "created_revision": created["revision"], "edited_revision": expected["revision"],
                    "topology_version_before": version, "topology_version_after": persisted.topology_version,
                    "persisted_record_sha256": hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                    "engine_status": result.get("status"), "physical_action_performed": False}

        def refusal(self):
            form = self.page("communicators")
            before, commands = form._snap.topology.version, len(self.commands)
            self.click(form.new)
            for field in ("device_id", "node_id", "name"):
                self.text(form.fields[field], "TRL4-NATIVE-INVALID")
            self.tab(form.editor_tabs, form.where._tab_index)
            self.text(form.fields["latitude"], "-12.95")
            self.click(form.save)
            self.check(form.pending is None and len(self.commands) == commands and "recusado" in form.message.text().lower())
            self.click(form.new)
            for field in ("device_id", "node_id", "name"):
                self.text(form.fields[field], "TRL4-NATIVE-GROUND")
            self.click(form.save)
            self.check(form.pending is None and len(self.commands) == commands and "recusado" in form.message.text().lower())
            persisted = self.provider.service.topology_store.get(form._snap.topology.id)
            self.check(persisted.topology_version == before and "TRL4-NATIVE-INVALID" not in form.records)
            return {"topology_version": before, "commands_dispatched": len(self.commands) - commands}

        def fpga(self):
            panel = self.page("fpga").fpga_devices
            self.wait(lambda: panel.selector.count() > 0)
            item = next((x for x in panel._devices.values() if x["mode"] == "simulated"), None)
            if item is None:
                raise NotDemonstrated("no_simulated_fpga_registered")
            ident = item["configuration"]["device_id"]
            self.combo(panel.selector, ident)

            def current():
                return panel._devices[ident]

            revisions = [current()["configuration"]["revision"]]
            for _ in range(2):
                config = current()["configuration"].copy()
                self.click(panel.connect_button)
                self.wait(lambda: current()["configuration"]["revision"] > config["revision"] and not self.window.provider.busy)
                self.check(current()["configuration"]["connected"] is not config["connected"])
                revisions.append(current()["configuration"]["revision"])
            if not current()["configuration"]["connected"]:
                self.click(panel.connect_button)
                self.wait(lambda: current()["configuration"]["connected"] and not self.window.provider.busy)
            revision = current()["configuration"]["revision"]
            expected_cycles = panel.cycles.value() + 1
            self.click(panel.cycles)
            QTest.keyClick(panel.cycles, Qt.Key.Key_Up)
            self.check(panel.cycles.value() == expected_cycles)
            self.click(panel.save)
            self.wait(lambda: current()["configuration"]["revision"] > revision and not self.window.provider.busy)
            self.check(current()["configuration"]["pipeline_cycles"] == expected_cycles)
            self.wait(lambda: bool(current()["state"].get("last_receipts")))
            row = current()
            self.check(row["state"]["accepted_frames"] > 0)
            self.check(any(x["channel_receipt"].get("stages") for x in row["state"]["last_receipts"]))
            self.check(row["state"]["temperature_c"] is None and row["state"]["measured_fpga_latency_s"] is None)
            persisted = self.provider.service.fpga_device_state()
            self.check(any(x["configuration"] == row["configuration"] for x in persisted["devices"]))
            self.capture("fpga-emulated")
            return {"device_id": ident, "revisions": revisions + [row["configuration"]["revision"]],
                    "configuration_sha256": row["configuration_sha256"], "last_feedback": row["state"].get("last_feedback"),
                    "measured_fpga_latency_s": None, "temperature_c": None}

        def key_audit(self):
            keys = self.page("keys")
            snap = keys._snap
            self.check(keys.scope_note.text() == snap.keys.scope_note)
            self.check(keys.provenance.values() == {x.key: x.value for x in snap.keys.provenance})
            self.check(keys._sessions == list(snap.keys.sessions))
            key_scope = keys.scope_note.text()
            audit = self.page("audit")
            self.check(audit.chain_label.text() == audit._snap.ledger.chain_verified)
            self.check(audit.chain_note.text() == audit._snap.ledger.chain_note)
            self.check(audit._entries == list(audit._snap.ledger.entries))
            self.check(audit.export.values().get("Signature") == "unsigned · local session")
            status = self.provider.security.audit.verify()
            self.check(status["valid"] is True)
            return {"key_scope": key_scope, "audit_chain": status, "export_signature": "unsigned · local session"}

        def export(self):
            screen = self.page("keys")
            path = output / "qkd-evidence.json"
            picker_errors, picked = [], []

            def choose():
                try:
                    dialog = self.app.activeModalWidget()
                    if not isinstance(dialog, QFileDialog):
                        raise CaseFailure("qt_file_picker_not_available")
                    edit = dialog.findChild(QLineEdit, "fileNameEdit")
                    if edit is None:
                        dialog.reject()
                        raise CaseFailure("file_name_control_unavailable")
                    self.text(edit, str(path))
                    QTest.keyClick(edit, Qt.Key.Key_Return)
                    picked.append(True)
                except Exception as error:
                    picker_errors.append(type(error).__name__)
                    dialog = self.app.activeModalWidget()
                    if isinstance(dialog, QFileDialog):
                        dialog.reject()

            QTimer.singleShot(150, self.window, choose)
            self.click(screen.export_button)
            self.check(not picker_errors and bool(picked))
            self.wait(lambda: path.is_file() and str(path) in self.exported and not self.window.provider.busy)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.check(data["scope"] == "qkd" and data["format"] == "unsigned_console_evidence_export_v1")
            self.check(all(key in data for key in ("health", "topology", "events", "qkd_example")))
            digest, count = sha256(path), len(self.exported)
            QTimer.singleShot(150, self.window, choose)
            self.click(screen.export_button)
            self.check(not picker_errors and len(picked) == 2)
            self.check(sha256(path) == digest and len(self.exported) == count)
            artifacts[path.name] = {"sha256": digest, "scope": "unsigned_qkd_evidence"}
            return {"destination": path.name, "sha256": digest, "file_picker": "real Qt widgets on Windows; Win32 picker not assessed",
                    "completed_count": count, "format": data["format"], "scope": data["scope"]}

        def ml(self):
            panel = self.page("topology").ml_control
            if not panel.switch.isVisible() or not panel.switch.isEnabled():
                raise NotDemonstrated("ml_control_permission_unavailable")
            self.check(panel.switch.enabled_mode is False)
            start = len(self.commands)
            QTimer.singleShot(100, self.window, lambda: self.click(panel.dialog.cancel_button))
            self.click(panel.switch)
            self.check(panel.dialog is None and len(self.commands) == start and panel.switch.enabled_mode is False)
            revisions = []
            detector_before = [(x.id, x.state) for x in self.snapshot().live.models if x.role == "detector"]
            for enabled in (True, False):
                revision = panel._snapshot.live.policy["ml_control_revision"]
                QTimer.singleShot(100, self.window, lambda: self.click(panel.dialog.confirm_button))
                self.click(panel.switch)
                self.wait(lambda: panel._snapshot.live.policy["ml_control_revision"] > revision and panel.switch.enabled_mode is enabled
                          and not self.window.provider.busy)
                self.check(panel._snapshot.live.policy["ml_control_enabled"] is enabled)
                self.check(self.provider.last_network_result["physical_action_performed"] is False)
                revisions.append(panel._snapshot.live.policy["ml_control_revision"])
            self.check(panel.switch.enabled_mode is False)
            detector_after = [(x.id, x.state) for x in self.snapshot().live.models if x.role == "detector"]
            self.check(detector_after == detector_before)
            return {"control_revisions": revisions, "ml_control_enabled": False,
                    "detector_before": detector_before, "detector_after": detector_after,
                    "scope": "ML network permission is distinct from inference/detection; optional model defaults unchanged"}

        def physical(self):
            snap = self.snapshot()
            self.check(snap.capabilities.mode == "simulation" and "Physical actuation: unavailable" in snap.capabilities.describe())
            status = self.provider.service.control_plane_status()
            self.check(status["physical_executor_available"] is False)
            self.check(self.provider.last_network_result["physical_action_performed"] is False)
            return {"physical_actuation": False, "physical_executor_available": False, "physical_action_performed": False}

        def cleanup(self):
            geo = self.window.screens["topology"].geo
            timer = getattr(geo.renderer, "_timer", None)
            controller = self.window._login_controller
            self.window.close()
            self.pump()
            self.check(not self.window.isVisible() and not self.window.provider.running)
            self.check(geo.resource_stats()["active_renderers"] == 0 and geo.resource_stats()["renderer_instances"] == 0)
            self.check(not geo._activate_timer.isActive() and not geo._fade_guard.isActive())
            if timer is not None:
                try:
                    self.check(not timer.isActive())
                except RuntimeError:
                    pass
            self.check(controller is not None and controller._closed is True)
            return {"provider_running": False, "geo_resources": geo.resource_stats(), "login_worker_closed": True}

        def execute(self):
            try:
                self.case("native_entry", self.enter)
                if cases[-1]["status"] != "passed":
                    return
                jobs = [("seven_screens", self.screens), ("topology_views", self.topology_views),
                        ("ground_details", self.details), ("satellite_details", lambda: self.details(True)),
                        ("geo_lifecycle", self.geo_lifecycle), ("communicator_dynamic", self.dynamic),
                        ("communicator_terrestrial", self.register), ("communicator_satellite", lambda: self.register(True)),
                        ("communicator_refusal", self.refusal), ("fpga_emulated", self.fpga),
                        ("key_audit_readback", self.key_audit), ("evidence_export", self.export),
                        ("ml_cancel_confirm_restore", self.ml), ("physical_boundary", self.physical)]
                for ident, action in jobs:
                    self.case(ident, action)
            finally:
                self.case("cleanup", self.cleanup)
                self.app.quit()

    driver = Driver()
    args = build_parser().parse_args(["--provider", "demo", "--repository-root", str(ROOT), "--state-root", str(output / "state")])
    code = run(args, startup_monitor=driver)
    received_ids = {row["id"] for row in cases}
    for ident in CASE_CRITERIA:
        if ident not in received_ids:
            cases.append(case_result(ident, status="not_demonstrated", reason="native_entry_or_collection_incomplete"))
    end = source_hashes()
    report = {"schema": SCHEMA, "console_cases": list(CASE_CRITERIA), "case_criteria": CASE_CRITERIA, "cases": cases,
              "checks": {row["id"]: row["status"] == "passed" for row in cases}, "errors": errors,
              "actual_authentication": auth, "app_version": product_version(),
              "qt_platform": QApplication.platformName(), "frozen": bool(getattr(sys, "frozen", False)),
              "environment": {**environment(), "pyside6": PySide6.__version__},
              "source_hashes": before, "source_hashes_end": end, "source_unchanged": before == end,
              "elapsed_s": time.monotonic() - began, "application_exit_code": code,
              "state_root": str(output / "state"), "isolated_diagnostic": True, "artifacts": artifacts,
              "physical_action_performed": False, "hardware_validated": False,
              "experimental_calibration": "not_demonstrated", "performance_qualification": "not_demonstrated",
              "limitations": ["Source Qt campaign; frozen executable behavior is not demonstrated by this launcher.",
                              "Simulated input and emulated FPGA; no field/hardware or experimental calibration qualification.",
                              "Deadlines bound collection only; no TRL5 latency or throughput criteria are approved.",
                              "Qt file chooser exercised; Windows shell native file picker is not demonstrated.",
                              "Current Windows principal only; adversarial role/revocation cases are separate component tests.",
                              "Policy activation, OGS/mission live transports, lab KME issuance and every optional model dialog are outside this campaign."]}
    report["passed"] = report_passes(report)
    write_json(output / "report.json", report)
    return 0 if report["passed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--size", type=size_value, default=(1440, 900))
    parser.add_argument("--timeout-s", type=int, default=600)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 120 <= args.timeout_s <= 1200:
        parser.error("timeout must be 120..1200 seconds")
    if os.name != "nt":
        parser.error("native Windows Qt is required")
    if args.worker:
        return run_worker(validate_worker_root(args.output_dir), args.size, args.timeout_s)
    output = prepare_output(args.output_dir)
    manifest = {"schema": SCHEMA, "console_cases": list(CASE_CRITERIA), "case_criteria": CASE_CRITERIA,
                "provider": "demo", "input_scope": "simulated input / ordinary real service pipeline",
                "state_root": str(output / "state"), "size": list(args.size),
                "authentication": "current_windows_session via ordinary boot button; no supplied credentials/bypass",
                "file_picker": "real Qt widget dialog; Win32 shell picker not assessed",
                "timeout_s": args.timeout_s, "deadline_scope": "collection guard, no performance qualification",
                "source_hashes": source_hashes(), "environment": environment(),
                "physical_action_performed": False, "frozen": False}
    write_json(output / "manifest-before.json", manifest)
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--output-dir", str(output),
               "--size", f"{args.size[0]}x{args.size[1]}", "--timeout-s", str(args.timeout_s)]
    with (output / "worker.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            code = child.wait(timeout=args.timeout_s + 15)
        except subprocess.TimeoutExpired:
            child.kill()  # Only this freshly created, owned diagnostic process.
            child.wait(timeout=15)
            code = 1
            write_json(output / "watchdog.json", {"timed_out": True, "owned_pid": child.pid, "physical_action_performed": False})
    if not (output / "report.json").is_file():
        write_json(output / "report.json", {"schema": SCHEMA, "passed": False,
                    "console_cases": list(CASE_CRITERIA), "cases": [case_result(ident, status="not_demonstrated",
                        reason="owned_native_child_did_not_complete") for ident in CASE_CRITERIA],
                    "checks": dict.fromkeys(CASE_CRITERIA, False), "errors": [{"error_type": "IncompleteNativeChild"}],
                    "actual_authentication": {}, "qt_platform": "not_demonstrated", "frozen": False,
                    "source_hashes": manifest["source_hashes"], "source_unchanged": False,
                    "environment": environment(), "state_root": str(output / "state"), "isolated_diagnostic": True,
                    "physical_action_performed": False, "hardware_validated": False,
                    "performance_qualification": "not_demonstrated", "experimental_calibration": "not_demonstrated"})
    print(str(output / "report.json"), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
