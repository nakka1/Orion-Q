"""LocalModelJob must carry the caller's explicit identity into its worker."""
import os
from threading import Event

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console.model_archive_check import LocalModelJob  # noqa: E402
from quantum_autopilot.console.service_provider import ServiceProvider  # noqa: E402
from quantum_autopilot.console.widgets.command_history import _export_captured  # noqa: E402
from quantum_autopilot.security import AuthenticationError  # noqa: E402
from tests.test_security_console import ControlledService  # noqa: E402

pytestmark = pytest.mark.unit


def test_local_export_job_does_not_replace_explicit_anonymous_actor_with_provider_admin(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = ControlledService(tmp_path)
    provider = ServiceProvider(service)  # Captures the authorized local OS owner.
    target = tmp_path / "anonymous-job-export.json"
    job = LocalModelJob()
    try:
        with service.security.identity_scope(None):
            # The same writer already refuses the synchronous anonymous caller.
            with pytest.raises(AuthenticationError):
                provider.export_captured_evidence("{}", str(tmp_path / "anonymous-direct.json"))
            # This is CommandHistory.export_evidence's exact asynchronous API.
            job.submit(_export_captured, "{}", str(target), provider.export_captured_evidence)
        finish(job)
        assert not job.active, "isolated local export job did not finish"
        assert not target.exists(), "anonymous job wrote evidence using the provider's OS administrator identity"
        assert job.result is None and "authenticated identity" in job.error
    finally:
        if job._future is not None:
            try:
                job._future.result(5)
            except PermissionError:
                pass
            job.poll()
        job.deleteLater()
        provider.deleteLater()
        app.processEvents()


def finish(job):
    """Wait only on the bounded Future; presentation polling remains on Qt."""
    if job._future is not None:
        try:
            job._future.result(5)
        except PermissionError:
            pass
        job.poll()


@pytest.mark.parametrize("role", ["auditor", "consumer"])
def test_local_export_job_retains_callers_without_export_permission(tmp_path, role):
    app = QApplication.instance() or QApplication([])
    service = ControlledService(tmp_path)
    provider = ServiceProvider(service)
    actor = service.security.grant_identity("service:local-job-" + role, [role], ["workspace:*"])
    target = tmp_path / (role + "-export.json")
    job = LocalModelJob()
    try:
        with service.security.identity_scope(actor):
            job.submit(_export_captured, "{}", str(target), provider.export_captured_evidence)
        finish(job)
        assert job.result is None and job.error
        assert not target.exists()
    finally:
        finish(job)
        job.deleteLater()
        provider.deleteLater()
        app.processEvents()


def test_local_export_job_revalidates_revocation_after_worker_started(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = ControlledService(tmp_path)
    provider = ServiceProvider(service)
    actor = service.security.grant_identity("service:local-job-operator", ["operator"], ["workspace:*"])
    target = tmp_path / "revoked-job-export.json"
    entered, release = Event(), Event()
    job = LocalModelJob()

    def waiting_export(text, path, writer):
        entered.set()
        assert release.wait(5), "test did not release the bounded export job"
        return _export_captured(text, path, writer)

    try:
        with service.security.identity_scope(actor):
            job.submit(waiting_export, "{}", str(target), provider.export_captured_evidence)
        assert entered.wait(3), "local export worker did not start"
        # The caller context has returned to the authorized OS owner here.
        service.security.revoke_identity(actor.subject)
        release.set()
        finish(job)
        assert job.result is None and "revoked" in job.error
        assert not target.exists()
        assert service.security.audit.verify()["valid"]
    finally:
        release.set()
        finish(job)
        job.deleteLater()
        provider.deleteLater()
        app.processEvents()
