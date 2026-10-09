"""Visible export requests remain authorized, exclusive and source-bound."""
import json
from concurrent.futures import ThreadPoolExecutor
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from quantum_autopilot.console.service_provider import ServiceProvider
from quantum_autopilot.console.source_switch import SwitchableProvider
from quantum_autopilot.console.input_sources import MeasuredFileProvider
from quantum_autopilot.security import AuthorizationError, AuthenticationError
from tests.test_security_console import ControlledService
from tests.test_console_service_provider import FakeService


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_explicit_export_writes_selected_path_emits_result_and_does_not_overwrite(app, tmp_path):
    provider = ServiceProvider(FakeService(), state_root=tmp_path / "state")
    paths = []
    provider.export_completed.connect(paths.append)
    target = tmp_path / "chosen.json"
    request = json.dumps({"scope": "qkd", "destination": str(target)})
    provider.export_record(request)
    assert json.loads(target.read_text())["scope"] == "qkd"
    assert paths == [str(target)]
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        provider.export_record(request)
    assert target.read_bytes() == original


@pytest.mark.parametrize("export_request", ['{"scope":"qkd","destination":"x","destination":"y"}',
                                      '{"scope":"qkd"}', '{"scope":1,"destination":"x.json"}'])
def test_invalid_export_rejected_before_write(app, tmp_path, export_request):
    provider = ServiceProvider(FakeService(), state_root=tmp_path / "not-created")
    with pytest.raises(ValueError):
        provider.export_record(export_request)
    assert not (tmp_path / "not-created").exists()


def test_captured_export_revalidates_identity_in_worker_and_revocation(app, tmp_path):
    service = ControlledService(tmp_path)
    auditor = service.security.grant_identity("service:auditor", ["auditor"], ["workspace:*"])
    with service.security.identity_scope(auditor):
        provider = ServiceProvider(service)
    target = tmp_path / "denied.json"
    with ThreadPoolExecutor(1) as worker:
        with pytest.raises(AuthorizationError):
            worker.submit(provider.export_captured_evidence, '{"receipt":"a"}', str(target)).result(5)
        service.security.revoke_identity(auditor.subject)
        with pytest.raises(AuthenticationError):
            worker.submit(provider.export_captured_evidence, '{}', str(target)).result(5)
    assert not target.exists()
    assert service.security.audit.verify()["valid"]


def test_source_switch_forwards_measured_export_completion(app, tmp_path):
    measured = MeasuredFileProvider(tmp_path / "input.json", tmp_path / "state")
    wrapper = SwitchableProvider(measured)
    paths = []
    wrapper.export_completed.connect(paths.append)
    wrapper.running = True
    target = tmp_path / "measurement.json"
    wrapper.dispatch("export_record", json.dumps({"scope": "qkd", "destination": str(target)}))
    assert paths == [str(target)]
    assert json.loads(target.read_text())["scope"] == "declared_measurements_read_only"
    wrapper.stop()


@pytest.mark.parametrize('captured', [True, False])
def test_revocation_during_export_preparation_prevents_file_creation(app, tmp_path, monkeypatch, captured):
    service = ControlledService(tmp_path)
    owner = service.security.local_identity()
    operator = service.security.grant_identity('service:exporter', ['administrator'], ['workspace:*'])
    with service.security.identity_scope(operator):
        provider = ServiceProvider(service, state_root=tmp_path / 'state')
    target = tmp_path / 'revoked-during-preparation.json'
    completed = []
    provider.export_completed.connect(completed.append)
    if captured:
        from quantum_autopilot.console import evidence_destination
        original = evidence_destination.export_destination

        def revoke_after_validation(*args, **kwargs):
            result = original(*args, **kwargs)
            with service.security.identity_scope(owner):
                service.security.revoke_identity(operator.subject)
            return result

        monkeypatch.setattr(evidence_destination, 'export_destination', revoke_after_validation)
        def callback():
            return provider.export_captured_evidence('{}', str(target))
    else:
        # A real export finishes preparation by querying workspace evidence.
        def revoke_after_read(_):
            with service.security.identity_scope(owner):
                service.security.revoke_identity(operator.subject)
            return None

        for name in ('control_plane_topology', 'control_plane_events', 'evidence_lanes'):
            monkeypatch.setattr(service, name, lambda *args, **kwargs: {}, raising=False)
        monkeypatch.setattr(provider, '_safety_events', lambda: [])
        monkeypatch.setattr(service, 'workspace_path_result', revoke_after_read, raising=False)
        def callback():
            return provider.export_record(json.dumps({'scope': 'qkd', 'destination': str(target)}))
    with pytest.raises(AuthenticationError):
        callback()
    assert not target.exists() and completed == []
