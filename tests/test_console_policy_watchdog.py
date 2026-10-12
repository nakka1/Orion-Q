"""Policy panel timers render committed declarations; actions keep fresh authority."""
from dataclasses import replace
import hashlib
import time

import pytest

from tests import test_console_policy_review as shared
from quantum_autopilot.console.capabilities import SourceCapabilities
from quantum_autopilot.console.source_switch import SwitchableProvider
from quantum_autopilot.console.service_provider import ServiceProvider
from quantum_autopilot.console.widgets.policy_decisions import PolicyDecisionAudit
from quantum_autopilot.console.widgets.policy_context import PolicyContextInspection
from quantum_autopilot.console.widgets.policy_review import PolicyReview
from quantum_autopilot.control_plane.topology import default_simulated_topology

app, provider = shared.app, shared.provider
PANEL_TYPES = (PolicyDecisionAudit, PolicyContextInspection, PolicyReview)


def pump(app, predicate, timeout=8):
    end = time.monotonic()+timeout
    while not predicate() and time.monotonic()<end:
        app.processEvents()
        time.sleep(.005)  # Release the GIL for the actual guarded backend read.
    assert predicate()


@pytest.fixture
def source(provider):
    provider.service.controller_runtime
    provider.service.topology_store.initialize(default_simulated_topology())
    wrapper = SwitchableProvider(provider)
    wrapper.running = True
    provider.refresh()
    yield wrapper, provider, wrapper._snap
    wrapper.running = False


def open_panel(panel_type, source, app):
    view = panel_type()
    view.attach(source[0])
    view.show()
    app.processEvents()
    return view


def action(view):
    return view.load_baseline() if isinstance(view, PolicyReview) else view.load()


@pytest.mark.parametrize('panel_type', PANEL_TYPES)
def test_cold_cache_is_disabled_until_committed_snapshot(source, app, monkeypatch, panel_type):
    source[0]._snap = None
    monkeypatch.setattr(ServiceProvider, 'capabilities', property(lambda *_: pytest.fail('cold-cache Qt authority read')))
    monkeypatch.setattr(ServiceProvider, 'snapshot', lambda *_: pytest.fail('cold-cache Qt state read'))
    view = open_panel(panel_type, source, app)
    try:
        assert not view.load_button.isEnabled()
        assert not view.job.active
        source[0].snapshot_changed.emit(source[2])
        assert view.load_button.isEnabled()
    finally:
        view.close()


@pytest.mark.parametrize('panel_type', PANEL_TYPES)
def test_committed_frame_initializes_and_watchdog_never_reads_backend(source, app, monkeypatch, panel_type):
    def forbidden(*args, **kwargs):
        pytest.fail('Qt presentation tried to read guarded provider authority or state')
    monkeypatch.setattr(ServiceProvider, 'capabilities', property(forbidden))
    monkeypatch.setattr(ServiceProvider, 'snapshot', forbidden)
    view = open_panel(panel_type, source, app)
    try:
        assert view.load_button.isEnabled()
        for _ in range(12):
            view._tick()
        assert view.load_button.isEnabled()
        assert not view.job.active
        assert view._presented_capabilities == source[2].capabilities
    finally:
        view.close()


@pytest.mark.parametrize('panel_type', PANEL_TYPES)
def test_source_change_withdraws_then_new_commit_restores_affordances(source, app, monkeypatch, panel_type):
    view = open_panel(panel_type, source, app)
    monkeypatch.setattr(ServiceProvider, 'capabilities', property(lambda *_: pytest.fail('source-change Qt IO')))
    try:
        assert view.load_button.isEnabled()
        source[0]._generation += 1
        source[0].source_changed.emit('different source')
        assert not view.load_button.isEnabled()
        assert view._presented_capabilities == SourceCapabilities()
        assert not view.job.active
        source[0].snapshot_changed.emit(source[2])
        assert view.load_button.isEnabled()
        source[0].snapshot_changed.emit(replace(source[2], capabilities=SourceCapabilities()))
        assert not view.load_button.isEnabled()
        assert not view.export_button.isEnabled()
    finally:
        view.close()


@pytest.mark.parametrize('panel_type', PANEL_TYPES)
@pytest.mark.parametrize('tick_while_stopped', (True, False))
def test_stopped_source_withdraws_cached_affordances(source, app, monkeypatch, panel_type, tick_while_stopped):
    view = open_panel(panel_type, source, app)
    # Exercise the actual wrapper lifecycle while deliberately withholding the
    # new provider publication; the scientific worker is outside this UI test.
    monkeypatch.setattr(source[1], 'start', lambda: None)
    try:
        source[0].stop()
        if tick_while_stopped:
            view._tick()
            assert not view.load_button.isEnabled()
            assert not view.export_button.isEnabled()
        source[0].start()
        # Restart is deferred until the retired source releases its workers.
        pump(app, lambda: source[0].running)
        assert not source[1].stopping
        view._tick()
        assert not view.load_button.isEnabled()  # Restart is not a new declaration.
        source[0].snapshot_changed.emit(source[2])
        assert view.load_button.isEnabled()
    finally:
        view.close()


@pytest.mark.parametrize('panel_type', PANEL_TYPES)
@pytest.mark.parametrize('denial', ('anonymous', 'revoked', 'grant_changed'))
def test_action_revalidates_stale_presentation_and_never_queues_denied_work(source, app, panel_type, denial):
    view = open_panel(panel_type, source, app)
    service, provider = source[1].service, source[1]
    security = service.security
    principal = security.grant_identity('watchdog-action', ['researcher'], ['*'])
    try:
        with security.identity_scope(principal):
            action(view)
        pump(app, lambda: not view.job.active)
        assert not view.job.error
        assert view.load_button.isEnabled()
        journal_hash = hashlib.sha256(service.control_journal_path.read_bytes()).hexdigest()
        provider._identity = principal
        if denial == 'revoked':
            security.revoke_identity(principal.subject)
        elif denial == 'grant_changed':
            security.grant_identity(principal.subject, ['consumer'], ['*'])
        if denial == 'anonymous':
            with security.identity_scope(None):
                action(view)
        else:
            action(view)
        assert 'REFUSED' in view.status.text()
        assert not view.job.active
        assert hashlib.sha256(service.control_journal_path.read_bytes()).hexdigest() == journal_hash
        # The old display declaration still exists, but cannot grant authority.
        assert view._presented_capabilities == source[2].capabilities
    finally:
        view.close()


@pytest.mark.parametrize('panel_type', (PolicyContextInspection, PolicyReview))
def test_watchdog_expiry_and_recovery_generation_remain_live(source, app, panel_type):
    view = open_panel(panel_type, source, app)
    try:
        action(view)
        pump(app, lambda: not view.job.active)
        assert view.context is not None
        view.deadline = time.monotonic()-1
        view._tick()
        assert view.context is None and view.deadline is None
        action(view)
        pump(app, lambda: not view.job.active)
        assert view.context is not None
        source[1].service.invalidate_policy_preparation()
        view._tick()
        assert view.context is None and not view.export_button.isEnabled()
    finally:
        view.close()


def test_audit_source_reset_blocks_job_changed_reentry(source, app):
    view = open_panel(PolicyDecisionAudit, source, app)
    changes = []
    view.job.changed.connect(lambda: changes.append('read'))
    view.export_job.changed.connect(lambda: changes.append('export'))
    try:
        epoch = view._epoch
        source[0].source_changed.emit('withdrawn')
        assert view._epoch == epoch+1
        assert changes == []
        assert view.history is None and view.explanation is None
        assert not view.load_button.isEnabled()
    finally:
        view.close()


@pytest.mark.parametrize('panel_type', PANEL_TYPES)
def test_action_does_not_swallow_unrelated_boundary_errors(source, app, monkeypatch, panel_type):
    view = open_panel(panel_type, source, app)
    method = ('policy_decision_store_path' if panel_type is PolicyDecisionAudit else
              'policy_context_source' if panel_type is PolicyContextInspection else 'policy_review_source')
    def unexpected():
        raise RuntimeError('unexpected adapter bug')
    monkeypatch.setattr(source[1], method, unexpected)
    try:
        with pytest.raises(RuntimeError, match='unexpected adapter bug'):
            action(view)
        assert not view.job.active
    finally:
        view.close()


@pytest.mark.parametrize('denial', ('anonymous', 'revoked', 'grant_changed'))
def test_active_readback_rechecks_current_permission_before_creating_binding(source, app, denial):
    view = open_panel(PolicyReview, source, app)
    service, provider = source[1].service, source[1]
    principal = service.security.grant_identity('watchdog-readback', ['operator'], ['*'])
    try:
        assert view.read_active_button.isEnabled() and view.binding is None
        provider._identity = principal
        if denial == 'revoked':
            service.security.revoke_identity(principal.subject)
        elif denial == 'grant_changed':
            service.security.grant_identity(principal.subject, ['consumer'], ['*'])
        if denial == 'anonymous':
            with service.security.identity_scope(None):
                view.read_active()
        else:
            view.read_active()
        assert 'READBACK REFUSED' in view.status.text()
        assert view.binding is None and not view.job.active
    finally:
        view.close()
