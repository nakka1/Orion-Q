"""Q05 native preparation is explicit, asynchronous, bounded and non-activating."""
from dataclasses import replace
import json
import os
from pathlib import Path
from threading import Event, get_ident
import time

import pytest

pytest.importorskip('PySide6')
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from quantum_autopilot.console.widgets import policy_review as ui  # noqa: E402
from quantum_autopilot.console.policy_review_binding import PolicyReviewBinding  # noqa: E402
from quantum_autopilot.console.source_switch import SwitchableProvider  # noqa: E402
from quantum_autopilot.control_plane import policy_preparation as backend  # noqa: E402
from quantum_autopilot.control_plane.topology import default_simulated_topology, _sha256  # noqa: E402
from quantum_autopilot.control_plane.policy import RepeaterSandboxPolicy  # noqa: E402
from quantum_autopilot.control_plane.inventory import AddSimulatedNode  # noqa: E402
from tests import test_console_inventory as shared  # noqa: E402

app, provider = shared.app, shared.provider


def wait(app, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(20)
    assert predicate()


def click(button):
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)


@pytest.fixture
def panel(provider, app):
    from quantum_autopilot.console.theme import install
    install(app)
    provider.service.controller_runtime
    provider.service.topology_store.initialize(default_simulated_topology())
    wrapper = SwitchableProvider(provider)
    wrapper.running = True
    view = ui.PolicyReview()
    view.attach(wrapper)
    view.show()
    app.processEvents()
    yield view, wrapper, provider.service
    view.close()
    wait(app, lambda: not view.job.active and not view.export_job.active)
    wrapper.stop()
    view.deleteLater()
    # Retire native windows on their Qt thread before a later worker can
    # collect the Python signal closures left by this test's provider.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def load(panel, app):
    view = panel[0]
    assert view.load_button.isEnabled()
    click(view.load_button)
    wait(app, lambda: not view.job.active)
    assert view.context, view.status.text()


def prepare(panel, app):
    view = panel[0]
    load(panel, app)
    view.threshold.setText('0.7')
    assert view.prepare_button.isEnabled()
    click(view.prepare_button)
    wait(app, lambda: not view.job.active)
    assert view.proposal, view.status.text()


def review(panel, app):
    view = panel[0]
    prepare(panel, app)
    click(view.review_button)
    wait(app, lambda: not view.job.active)
    assert view.review, view.status.text()


def test_explicit_background_work_baseline_proposal_and_no_mutation(panel, app, monkeypatch):
    view, _, service = panel
    runtime = service.controller_runtime
    before = runtime.journal.path.read_bytes()
    threads = []
    original = PolicyReviewBinding.run
    def run(self, *args):
        threads.append(get_ident())
        return original(self, *args)
    monkeypatch.setattr(PolicyReviewBinding, 'run', run)
    def forbidden(*args, **kwargs):
        pytest.fail('UI review must not activate or execute')
    monkeypatch.setattr(runtime, 'cycle', forbidden)
    monkeypatch.setattr(runtime.policy, 'decide', forbidden)
    assert view.context is None and not view.job.active
    assert not view.prepare_button.isEnabled() and not view.review_button.isEnabled()
    review(panel, app)
    assert len(threads) == 3 and all(t != get_ident() for t in threads)
    assert view.review['record']['status'] == 'CURRENT'
    assert view.review['record_sha256'] == _sha256(view.review['record'])
    assert 'Threshold: 0.65' in view.comparison.toPlainText()
    assert 'PROPOSED THRESHOLD: 0.7' in view.comparison.toPlainText()
    assert 'not approved' in view.status.text()
    assert runtime.policy.engine.threshold == .65 and runtime.journal.path.read_bytes() == before
    assert not view.review['record']['activation_available']
    for width, height in ((640, 500), (1000, 700)):
        view.resize(width, height)
        app.processEvents()
        assert view.comparison.height() > 140
        assert view.export_button.mapTo(view, view.export_button.rect().topRight()).x() < view.width()
        if folder := os.environ.get('ORION_POLICY_REVIEW_EVIDENCE'):
            out = Path(folder)
            out.mkdir(parents=True, exist_ok=True)
            assert view.grab().save(str(out/f'policy-review-{width}.png'))
            (out/'review.json').write_text(json.dumps(view.review,indent=2),encoding='utf-8')


@pytest.mark.parametrize('value', ['', 'nan', 'inf', '-0.01', '1.01', 'abc', '0,7', '0.65'])
def test_invalid_or_unchanged_draft_cannot_prepare(panel, app, value):
    view = panel[0]
    load(panel, app)
    view.threshold.setText(value)
    assert not view.prepare_button.isEnabled()
    view.prepare()
    assert not view.job.active and view.proposal is None


def test_editing_clears_proposal_and_review_but_retains_baseline(panel, app):
    view = panel[0]
    review(panel, app)
    context = view.context
    view.threshold.setText('.8')
    assert view.context == context and view.proposal is None and view.review is None
    assert view.prepare_button.isEnabled() and not view.export_button.isEnabled()
    click(view.prepare_button)
    wait(app, lambda: not view.job.active)
    assert view.proposal['record']['proposed_policy']['threshold'] == .8


@pytest.mark.parametrize('operation', ['inspect', 'prepare', 'review'])
@pytest.mark.parametrize('change', ['close', 'source', 'draft'])
def test_late_result_discard_and_no_queue(panel, app, monkeypatch, operation, change):
    view, wrapper, _ = panel
    if operation == 'prepare':
        load(panel, app)
        view.threshold.setText('.7')
    elif operation == 'review':
        prepare(panel, app)
    entered, release = Event(), Event()
    calls = []
    original = PolicyReviewBinding.run
    def slow(self, *args):
        calls.append(args)
        entered.set()
        assert release.wait(5)
        return original(self, *args)
    monkeypatch.setattr(PolicyReviewBinding, 'run', slow)
    click({'inspect':view.load_button, 'prepare':view.prepare_button, 'review':view.review_button}[operation])
    wait(app, entered.is_set)
    try:
        if change == 'close':
            view.close()
        elif change == 'source':
            wrapper._generation += 1
            wrapper.source_changed.emit('changed')
        else:
            view.threshold.setText('.9')
        view.load_baseline()
        assert len(calls) == 1 and not view.export_button.isEnabled()
    finally:
        release.set()
    wait(app, lambda: not view.job.active)
    assert view.context is None and view.proposal is None and view.review is None


def test_source_stop_during_sqlite_read_is_nonblocking(panel, app, monkeypatch):
    view, _, service = panel
    entered, release = Event(), Event()
    original = backend._read_topology
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(backend, '_read_topology', slow)
    click(view.load_button)
    wait(app, entered.is_set)
    try:
        start = time.monotonic()
        service.invalidate_policy_preparation()
        view._tick()
        assert time.monotonic() - start < .5
        assert view.context is None
    finally:
        release.set()
    wait(app, lambda: not view.job.active)
    assert view.context is None


def test_policy_change_is_reported_stale_on_review(panel, app):
    view, _, service = panel
    prepare(panel, app)
    service.controller_runtime.policy = RepeaterSandboxPolicy(.8)
    click(view.review_button)
    wait(app, lambda: not view.job.active)
    assert view.review['record']['status'] == 'STALE'
    assert 'policy_changed' in view.comparison.toPlainText()
    assert view.export_button.isEnabled()  # Historical stale evidence can be exported.


def test_topology_snapshot_change_clears_current_selection(panel, app):
    view, wrapper, service = panel
    review(panel, app)
    service.topology_store.add_inventory_node(AddSimulatedNode('extra','endpoint',0,'127.0.0.1',7411))
    wrapper.snapshot_changed.emit(wrapper.active.build())
    assert view.context is None and view.proposal is None and not view.export_button.isEnabled()


def test_unchanged_snapshot_keeps_review(panel, app):
    view, wrapper, _ = panel
    review(panel, app)
    captured = view.review
    wrapper.snapshot_changed.emit(wrapper.active.build())
    assert view.review == captured


def test_expiry_and_reopening_require_new_baseline(panel, app):
    view = panel[0]
    review(panel, app)
    view.deadline = time.monotonic() - 1
    view._tick()
    assert view.context is None and view.review is None and 'expired' in view.status.text()
    load(panel, app)
    view.close()
    view.show()
    assert view.context is None and not view.prepare_button.isEnabled()


@pytest.mark.parametrize('change', ['cancel', 'draft', 'source', 'expiry', 'close'])
def test_file_picker_context_changes_prevent_export(panel, app, monkeypatch, tmp_path, change):
    view, wrapper, _ = panel
    review(panel, app)
    target = tmp_path/'refused.json'
    def choose(*args):
        if change == 'draft':
            view.threshold.setText('.8')
        elif change == 'source':
            wrapper._generation += 1
        elif change == 'expiry':
            view.deadline = time.monotonic() - 1
        elif change == 'close':
            view.close()
        return ('' if change == 'cancel' else str(target)), 'JSON'
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', choose)
    click(view.export_button)
    assert not target.exists() and not view.export_job.active


def test_export_captures_review_without_re_read_and_refuses_overwrite(panel, app, monkeypatch, tmp_path):
    view, _, service = panel
    review(panel, app)
    captured = json.loads(json.dumps(view.review))
    target = tmp_path/'review.json'
    threads = []
    original = ui._export_captured
    def write(*args):
        threads.append(get_ident())
        return original(*args)
    monkeypatch.setattr(ui, '_export_captured', write)
    def choose(*args):
        service.controller_runtime.policy = RepeaterSandboxPolicy(.8)
        return str(target), 'JSON'
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', choose)
    click(view.export_button)
    wait(app, lambda: not view.export_job.active)
    assert json.loads(target.read_text(encoding='utf-8')) == captured
    assert threads[0] != get_ident()
    assert service.controller_runtime.policy.engine.threshold == .8
    prior = target.read_bytes()
    click(view.export_button)
    wait(app, lambda: not view.export_job.active)
    assert target.read_bytes() == prior and 'EXPORT REFUSED' in view.status.text()


def test_initialized_service_capability_and_native_entry(provider, app):
    from quantum_autopilot.console.shell import ConsoleWindow
    from quantum_autopilot.console.capabilities import SourceCapabilities, synthetic_capabilities
    assert not provider.capabilities.allows('review_policy_preparation')
    provider.service.controller_runtime
    assert provider.capabilities.allows('review_policy_preparation')
    assert not provider.capabilities.allows('set_decision_source')
    assert not synthetic_capabilities().allows('review_policy_preparation')
    with pytest.raises(ValueError):
        SourceCapabilities(mode='read_only', operations=('review_policy_preparation',))
    provider.service.topology_store.initialize(default_simulated_topology())
    window = ConsoleWindow(provider, skip_auth=True)
    window.provider.running = True
    edge = window.screens['fpga']
    edge.apply(provider.build())
    assert edge.policy_review.provider is window.provider and edge.policy_review_button.isEnabled()
    assert not edge.ml.isEnabled() and not edge.deterministic.isEnabled()
    click(edge.policy_review_button)
    assert edge.policy_review.isVisible() and not edge.policy_review.job.active
    edge.apply(replace(provider.build(), capabilities=SourceCapabilities()))
    assert not edge.policy_review_button.isEnabled()
    window.close()
    assert not edge.policy_review.isVisible()


def test_watchdog_preserves_receipt_selection_and_scroll(panel, app):
    view = panel[0]
    review(panel, app)
    view.tabs.setCurrentWidget(view.receipt)
    app.processEvents()
    cursor = view.receipt.textCursor()
    cursor.setPosition(30)
    cursor.setPosition(70, cursor.MoveMode.KeepAnchor)
    view.receipt.setTextCursor(cursor)
    bar = view.receipt.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    selected, position = cursor.selectedText(), bar.value()
    for _ in range(3):
        view._tick()
    assert view.receipt.textCursor().selectedText() == selected
    assert bar.value() == position


@pytest.mark.parametrize('change', ['capability', 'runtime', 'generation'])
def test_context_binding_changes_require_fresh_baseline(panel, app, change):
    view, wrapper, service = panel
    review(panel, app)
    binding = view.binding
    if change == 'capability':
        wrapper.running = False
    elif change == 'runtime':
        service._controller_runtime = None
    else:
        service.invalidate_policy_preparation()
    view._tick()
    assert binding.cancelled.is_set()
    assert view.context is None and view.review is None
    assert not view.export_button.isEnabled()


def test_capture_error_discards_selection_and_allows_explicit_retry(panel, app, monkeypatch):
    view = panel[0]
    original = backend._read_topology
    def unavailable(*args):
        raise ValueError('topology unavailable')
    monkeypatch.setattr(backend, '_read_topology', unavailable)
    click(view.load_button)
    wait(app, lambda: not view.job.active)
    assert view.context is None and view.binding is None
    assert 'REFUSED' in view.status.text()
    assert not view.prepare_button.isEnabled()
    monkeypatch.setattr(backend, '_read_topology', original)
    load(panel, app)


def test_accepted_export_can_finish_after_close_without_restoring_selection(panel, app, monkeypatch, tmp_path):
    view = panel[0]
    review(panel, app)
    captured = json.loads(json.dumps(view.review))
    target = tmp_path / 'accepted.json'
    entered, release = Event(), Event()
    original = ui._export_captured
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(ui, '_export_captured', slow)
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', lambda *args: (str(target), 'JSON'))
    click(view.export_button)
    wait(app, entered.is_set)
    try:
        view.close()
        view.show()
        view.load_baseline()
        assert not view.job.active  # No second operation while an accepted write finishes.
    finally:
        release.set()
    wait(app, lambda: not view.export_job.active)
    assert json.loads(target.read_text(encoding='utf-8')) == captured
    assert view.context is None and view.review is None
    assert view.export_job.result is None and 'EXPORTED' not in view.status.text()


def test_panel_session_does_not_replace_service_api_proposal(panel, app):
    view, _, service = panel
    context = service.policy_preparation_context()
    proposal = service.prepare_policy_change(dict(mode='simulation', policy_id=backend.POLICY_ID,
        threshold=.75, optional_model_id=None, expected_context_sha256=context['record']['context_sha256']))
    review(panel, app)
    assert view.proposal['record']['proposal_id'] != proposal['record']['proposal_id']
    view.close()
    receipt = service.review_policy_change(proposal['record']['proposal_id'], proposal['record_sha256'])
    assert receipt['record']['status'] == 'CURRENT'
