"""Execute backend HTTP, native laboratory controls and published-record replay.

All mutable state stays in the selected output directory. No hardware endpoint
or cloud job is contacted. Receipts retain explicit simulation provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread

def main():
    # Windows spawned KME/SAE children import the entry module again. Keep the
    # heavy dashboard/Qiskit imports out of their startup/receiver deadline.
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService, make_handler
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("laboratory_diagnostic_requires_empty_directory")
    output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    service = AssuranceDashboardService(root, control_journal_path=output / "workspace.sqlite")
    from quantum_autopilot.lab.validation import provision_validation_identities
    provision_validation_identities(service.security, output)
    from quantum_autopilot.security.http_boundary import BoundedHTTPServer, HTTPPolicy
    policy = HTTPPolicy(operation_timeout_seconds=180)
    server = BoundedHTTPServer(("127.0.0.1", 0), make_handler(service, http_policy=policy), policy=policy)
    worker = Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
    worker.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=180)
    authentication = {"Authorization": "Bearer " + service.security.issue_local_session()}
    try:
        connection.request("GET", "/api/v1/laboratory", headers=authentication)
        response = connection.getresponse()
        initial = json.loads(response.read())
        assert response.status == 200 and initial["runs"] == []
        connection.request("POST", "/api/v1/laboratory/runs", json.dumps({"experiment": "all"}),
                           headers={"Content-Type": "application/json", **authentication})
        response = connection.getresponse()
        receipt = json.loads(response.read())
        assert response.status == 200, receipt
        assert receipt["status"] == "completed" and receipt["physical_action_performed"] is False
        assert receipt["results"]["photon"]["passed"] and receipt["results"]["qkd"]["all_passed"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(5)

    # Exercise an actual native control and verify it reads the same persisted backend journal.
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.console.service_provider import ServiceProvider
    from quantum_autopilot.console.screens.keys import KeysScreen
    from quantum_autopilot.console import theme
    app = QApplication.instance() or QApplication([])
    theme.install(app)
    provider = ServiceProvider(service, state_root=output)
    screen = KeysScreen()
    commands = []
    provider.snapshot_changed.connect(screen.apply)
    screen.command.connect(lambda *values: commands.append(values))
    screen.command.connect(provider.dispatch)
    try:
        screen.resize(1400, 1000)
        screen.apply(provider.build())
        screen.show()
        app.processEvents()
        button = screen.laboratory_panel.buttons["photon"]
        assert button.isEnabled()
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert commands == [("run_lab_experiment", '{"experiment": "photon"}')]
        readback = service.laboratory.history()
        assert len(readback["runs"]) == 2 and readback["runs"][0]["status"] == "completed"
        assert readback["runs"][0]["run_id"] in screen.laboratory_panel.evidence.toPlainText()
        assert screen.grab().save(str(output / "native_laboratory.png"))
        photon_first = receipt["results"]["photon"]["nominal"]
        photon_second = readback["runs"][0]["results"]["photon"]["nominal"]
        repeated = all(photon_first[key] == photon_second[key] for key in ("counts", "coincidences", "histogram"))
        assert repeated
    finally:
        screen.close()
        screen.deleteLater()
        provider.stop()
        app.processEvents()

    restored = AssuranceDashboardService(root, control_journal_path=output / "workspace.sqlite").laboratory_state()
    assert restored["runs"] == readback["runs"]

    # Replay available experimental summaries as summaries, preserving their statistical granularity.
    from quantum_twin.evaluation.public_qkd_evidence import run_public_qkd_evidence_audit, write_public_qkd_evidence_artifacts
    reference = root / "data/reference/gavignet_2024_published_aggregate.csv"
    published = run_public_qkd_evidence_audit(reference)
    published_manifest = write_public_qkd_evidence_artifacts(published, output / "published_aggregate_replay")
    report = {"schema": "orion.lab.integration.v1", "mode": "emulation_and_published_aggregate_replay",
              "http_run_id": receipt["run_id"], "native_run_id": readback["runs"][0]["run_id"],
              "checks": {"backend_http_run": True, "native_button_dispatch": True, "same_journal_readback": True,
                         "repeated_photon_metrics_equal": repeated, "completed_runs_survive_service_restart": True},
              "experimental_reference": {"path": str(reference),
                  "sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
                  "level": "published_aggregate_replay", "raw_photon_events": False,
                  "manifest": published_manifest},
              "physical_action_performed": False, "native_render": str(output / "native_laboratory.png")}
    report["security"] = {"common_runtime": True, "test_principals_explicitly_provisioned": True,
                          "isolated_diagnostic_directory": True,
                          "audit": service.security.audit.verify(),
                          "authenticated_classical_protocol": receipt["results"]["qkd"]["qkd_session"].get("classical_authentication", {}),
                          "tls_version": receipt["results"]["qkd"]["delivery"]["tls_version"]}
    (output / "integration_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"all_checks_passed": all(report["checks"].values()), "http_run_id": receipt["run_id"],
                      "native_run_id": report["native_run_id"], "output": str(output)}, indent=2))


if __name__ == "__main__":
    main()
