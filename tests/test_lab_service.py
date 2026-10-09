"""Laboratory receipts, HTTP validation and native console readback."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
from threading import Thread

import pytest

from tests.security_fixtures import provision_test_kme_identities

from quantum_autopilot.lab.service import LaboratoryService
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService, make_handler

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.integration


def test_read_only_history_does_not_create_files(tmp_path):
    service = LaboratoryService(tmp_path / "lab")
    provision_test_kme_identities(service.security)
    assert service.history()["runs"] == []
    assert not service.root.exists()


@pytest.mark.parametrize("payload", [{"experiment": "hardware"}, {"experiment": "photon", "mode": "hardware"},
                                      {"experiment": "photon", "output": "../outside"}, [], None])
def test_request_cannot_escalate_scope_or_choose_arbitrary_files(tmp_path, payload):
    with pytest.raises(ValueError):
        LaboratoryService(tmp_path / "lab").run(payload)


def test_completed_receipt_survives_service_restart(tmp_path, monkeypatch):
    from quantum_autopilot.lab import qkd
    monkeypatch.setattr(qkd, "run_qkd_demo", lambda folder, **kwargs: {"mode": "emulation", "all_passed": True})
    service = LaboratoryService(tmp_path / "lab")
    provision_test_kme_identities(service.security)
    receipt = service.run({"experiment": "qkd"})
    restored = LaboratoryService(service.root).history()["runs"][0]
    assert restored == receipt
    assert restored["status"] == "completed" and restored["physical_action_performed"] is False
    assert json.loads((service.root / receipt["run_id"] / "receipt.json").read_text()) == receipt


def test_failure_remains_failure_and_releases_lock(tmp_path, monkeypatch):
    from quantum_autopilot.lab import qkd
    def failure(folder, **kwargs):
        raise RuntimeError("secret_payload_that_must_not_be_logged")
    monkeypatch.setattr(qkd, "run_qkd_demo", failure)
    service = LaboratoryService(tmp_path / "lab")
    provision_test_kme_identities(service.security)
    with pytest.raises(RuntimeError):
        service.run({"experiment": "qkd"})
    record = service.history()["runs"][0]
    assert record["status"] == "failed" and record["error_type"] == "RuntimeError"
    assert "secret_payload" not in json.dumps(record)
    assert service._execution_lock.acquire(False)
    service._execution_lock.release()


def test_concurrent_operation_is_refused(tmp_path):
    service = LaboratoryService(tmp_path)
    provision_test_kme_identities(service.security)
    service._execution_lock.acquire()
    try:
        with pytest.raises(ValueError, match="laboratory_busy"):
            service.run({"experiment": "photon"})
    finally:
        service._execution_lock.release()


def test_failed_acceptance_cannot_be_journalled_completed(tmp_path, monkeypatch):
    from quantum_autopilot.lab import qkd
    monkeypatch.setattr(qkd, "run_qkd_demo", lambda folder, **kwargs: {"all_passed": False})
    service = LaboratoryService(tmp_path)
    provision_test_kme_identities(service.security)
    with pytest.raises(ValueError, match="acceptance_checks_failed"):
        service.run({"experiment": "qkd"})
    assert service.history()["runs"][0]["status"] == "failed"


def test_changed_receipt_cannot_be_reported_as_verified_history(tmp_path, monkeypatch):
    import sqlite3
    from quantum_autopilot.lab import qkd
    monkeypatch.setattr(qkd, "run_qkd_demo", lambda folder, **kwargs: {"all_passed": True})
    service = LaboratoryService(tmp_path)
    provision_test_kme_identities(service.security)
    result = service.run({"experiment": "qkd"})
    result["mode"] = "hardware"
    with sqlite3.connect(service.database) as connection:
        connection.execute("UPDATE runs SET receipt_json = ?", (json.dumps(result),))
    with pytest.raises(ValueError, match="receipt_hash_invalid"):
        service.history()


def test_api_rejects_duplicate_fields_before_mutation(tmp_path):
    from http.server import ThreadingHTTPServer
    service = AssuranceDashboardService(ROOT, control_journal_path=tmp_path / "journal.sqlite")
    provision_test_kme_identities(service.security)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        # This route has no side effect before validating the exact fields.
        connection.request("POST", "/api/v1/laboratory/runs", body='{"experiment":"hardware","experiment":"unknown"}',
                           headers={"Content-Type": "application/json",
                                    "Authorization": "Bearer " + service.security.issue_local_session()})
        response = connection.getresponse()
        result = json.loads(response.read())
        assert response.status == 400
        assert result == {"error": "invalid_json"}
        assert not service.laboratory.root.exists()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(5)


def test_native_panel_emits_exact_request_and_shows_backend_receipt(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.console.service_provider import ServiceProvider
    from quantum_autopilot.console.widgets.laboratory import LaboratoryPanel
    from quantum_autopilot.lab import qkd
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(qkd, "run_qkd_demo", lambda folder, **kwargs: {"mode": "emulation", "all_passed": True})
    service = AssuranceDashboardService(ROOT, control_journal_path=tmp_path / "journal.sqlite")
    provision_test_kme_identities(service.security)
    provider = ServiceProvider(service, state_root=tmp_path)
    panel = LaboratoryPanel()
    try:
        panel.set_snapshot(provider.build())
        requests = []
        panel.command.connect(lambda *args: requests.append(args))
        panel.buttons["qkd"].click()
        assert requests == [("run_lab_experiment", '{"experiment": "qkd"}')]
        provider.dispatch(*requests[0])
        panel.set_snapshot(provider.snapshot())
        assert "completed" in panel.evidence.toPlainText()
        assert service.laboratory.history()["runs"][0]["run_id"] in panel.evidence.toPlainText()
    finally:
        panel.close()
        panel.deleteLater()
        provider.stop()
        app.processEvents()
