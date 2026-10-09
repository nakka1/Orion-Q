"""Exercise every plain-text console view without packages or submitted actions."""
from pathlib import Path
import json

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, Signal, QTimer, QSignalBlocker
from PySide6.QtGui import QColor, QTextCursor, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QScrollArea, QTabWidget

from . import theme
from .responsive_validation import run_event_loop


def text_dialog_cases():
    """Real widget classes; confirmation records below are display fixtures only."""
    from .widgets.campaign_manifest_editor import CampaignManifestEditor
    from .widgets.campaign_runner import CampaignRunner
    from .widgets.command_history import CommandHistory
    from .widgets.controller_comparison import ControllerComparison
    from .widgets.model_catalog import ModelCatalog, SESSION_MODELS
    from .widgets.path_runner import PathRunner
    from .widgets.polarisation_control import (
        PolarisationControl, PolarisationApprovalConfirmation, PolarisationActivationConfirmation,
    )
    from .widgets.policy_activation_confirmation import PolicyActivationConfirmation
    from .widgets.policy_approval_confirmation import PolicyApprovalConfirmation
    from .widgets.policy_context import PolicyContextInspection
    from .widgets.policy_decisions import PolicyDecisionAudit
    from .widgets.policy_recovery_confirmation import PolicyRecoveryConfirmation
    from .widgets.policy_review import PolicyReview
    from .widgets.allocation_session import AllocationSession
    from .widgets.allocation_shadow import AllocationShadowSession
    from .widgets.compensation_session import CompensationSession
    from .widgets.compensation_shadow import CompensationShadowSession
    from .widgets.detector_session import DetectorSession
    from .widgets.keyrate_session import KeyRateSession
    from .widgets.keyrate_shadow import KeyRateShadowSession
    from .widgets.routing_session import RoutingSession
    from .widgets.routing_shadow import RoutingShadowSession
    from .widgets.sync_session import SyncSession
    from .widgets.sync_shadow import SyncShadowSession
    from ..control_plane.temporal_campaign import TemporalCampaignRequest, TemporalRegime

    for cls in (PolicyContextInspection, PolicyReview, PolicyDecisionAudit, CommandHistory,
                ModelCatalog, CampaignRunner, PathRunner, ControllerComparison, PolarisationControl):
        yield cls.__name__, cls

    digest = '0' * 64
    proposal = {'record': {'context': {'baseline': {'policy_name': 'display-fixture', 'threshold': .65},
                                      'topology_id': 'display-fixture', 'topology_version': 0},
                           'proposed_policy': {'threshold': .7}, 'proposal_id': 'display-fixture'},
                'record_sha256': digest}
    review = {'record': {'proposal': proposal, 'review_id': 'display-fixture',
                         'reviewed_at_utc': 'display-fixture', 'current_context_sha256': digest},
              'record_sha256': digest}
    approval = {'record': {'approved_review': review, 'approval_id': 'display-fixture',
                           'request': {'operator_label': 'display-fixture'}, 'recorded_at_utc': 'display-fixture'},
                'record_sha256': digest}
    recovery = {'record': {'context': {'retained_policy': {'policy_name': 'display-fixture', 'threshold': .65},
                                      'fault_id': 'display-fixture', 'failed_activation_id': 'display-fixture',
                                      'failed_record_observation': 'display-fixture', 'runtime_instance_id': 'display-fixture'},
                           'observed_at_utc': 'display-fixture'}, 'record_sha256': digest}
    yield 'PolicyApprovalConfirmation', lambda: PolicyApprovalConfirmation(review)
    yield 'PolicyActivationConfirmation', lambda: PolicyActivationConfirmation(approval)
    yield 'PolicyRecoveryConfirmation', lambda: PolicyRecoveryConfirmation(recovery)

    polar = {'record': {'current_policy': {'controller_id': 'display-fixture'},
                       'proposed_policy': {'controller_id': 'display-fixture', 'alpha_beta': {'alpha': .5, 'beta': .1}},
                       'support': None, 'rollback_of': None, 'proposal_id': 'display-fixture'}, 'record_sha256': digest}
    polar_approval = {'record': {'approval_id': 'display-fixture', 'operator_label': 'display-fixture'}}
    yield 'PolarisationApprovalConfirmation', lambda: PolarisationApprovalConfirmation(polar, {'record_sha256': digest})
    yield 'PolarisationActivationConfirmation', lambda: PolarisationActivationConfirmation(polar, polar_approval)

    class DraftJob(QObject):
        changed = Signal()
        active = False
        allowed = True
        request = TemporalCampaignRequest(
            campaign_id='display-fixture', source='alice', repeater='repeater-a', destination='bob',
            expected_version=0, target_fidelity=.8, attempt_interval_s=1e-6, max_attempts=4, seeds=(0, 1),
            regimes=(TemporalRegime('baseline', 0, 0, .00005, .00003, 2),
                     TemporalRegime('candidate', 1, 1, .00005, .00003, 2)), baseline_id='baseline')

        def _context(self):
            return 'display-fixture'

    def draft_editor():
        job = DraftJob()
        view = CampaignManifestEditor(job)
        job.setParent(view)
        return view

    yield 'CampaignManifestEditor', draft_editor

    for model_id in SESSION_MODELS:
        if model_id.startswith('qnet.allocator.'):
            session, shadow = AllocationSession, AllocationShadowSession
        elif model_id.startswith('qnet.routing.'):
            session, shadow = RoutingSession, RoutingShadowSession
        elif model_id.startswith('qnet.compensation.'):
            session, shadow = CompensationSession, CompensationShadowSession
        elif model_id == 'qkd.detector.boosting':
            yield model_id + '-offline', DetectorSession
            continue
        elif model_id == 'qkd.keyrate.actor':
            yield model_id + '-offline', KeyRateSession
            yield model_id + '-shadow', lambda: KeyRateShadowSession('qkd.keyrate.actor')
            continue
        else:
            yield model_id + '-offline', SyncSession
            yield model_id + '-shadow', SyncShadowSession
            continue
        yield model_id + '-offline', lambda cls=session, mid=model_id: cls(mid)
        yield model_id + '-shadow', lambda cls=shadow, mid=model_id: cls(mid)


def reveal_text(dialog, edit):
    """Reveal nested tab pages before measuring or sending wheel events."""
    for tab in dialog.findChildren(QTabWidget):
        for index in range(tab.count()):
            page = tab.widget(index)
            if page is edit or page.isAncestorOf(edit):
                tab.setCurrentIndex(index)
                break
    run_event_loop(10)
    parent = edit.parentWidget()
    while parent is not None and parent is not dialog:
        if isinstance(parent, QScrollArea):
            parent.ensureWidgetVisible(edit, 0, 0)
            run_event_loop(10)
        parent = parent.parentWidget()
    run_event_loop(10)


def prepare_display_fixture(dialog):
    # This corpus is not a captured policy observation. Source/expiry watchdogs
    # would correctly clear it. Real receipt/watchdog tests remain separate.
    for timer in dialog.findChildren(QTimer):
        timer.stop()


def probe_text_scroll(edit):
    """Verify full paint regions before a grab or selection can hide stale pixels."""
    paints = []

    class Paints(QObject):
        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Paint:
                paints.append(event.region())
            return False

    watcher = Paints(edit)
    edit.viewport().installEventFilter(watcher)
    before_text = edit.toPlainText()
    before_cursor = edit.textCursor()
    before_wrap = edit.lineWrapMode()
    before_enabled = edit.isEnabled()
    # Do not reinterpret the labelled painting corpus as a new policy input.
    # Real input invalidation and worker signals are tested on captured data.
    blocker = QSignalBlocker(edit)
    checks = {}
    sample = '\n'.join(f'{i:04d} · SCROLL PROBE · linha de evidência Ω {i * 17}' for i in range(240))
    try:
        # Standalone context editors have no attached source; enable only this
        # disposable rendering fixture. No operation buttons are clicked.
        edit.setEnabled(True)
        edit.setPlainText(sample)
        edit.verticalScrollBar().setValue(0)
        run_event_loop(20)

        def painted():
            # Bounding boxes can span unpainted holes; require the full region.
            from PySide6.QtGui import QRegion
            region = QRegion()
            for paint in paints:
                region = region.united(paint)
            return region.contains(edit.viewport().rect())

        def wheel(delta):
            p = edit.viewport().rect().center()
            event = QWheelEvent(QPointF(p), QPointF(edit.viewport().mapToGlobal(p)), QPoint(), QPoint(0, delta),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(edit.viewport(), event)
            run_event_loop(20)

        paints.clear()
        wheel(-120)  # Three lines; do not mask small-step blitting with a whole-page wheel.
        checks['wheel_down'] = edit.verticalScrollBar().value() > 0 and edit.firstVisibleBlock().blockNumber() > 0
        checks['wheel_paints_all_pixels'] = painted() and not edit.textCursor().hasSelection()
        # Inspect the widget's rendered pixels only after recording the wheel
        # paint. A grab forces painting, so it cannot prove scroll repainting.
        rendered = edit.viewport().grab().toImage()
        checks['theme_background_painted'] = rendered.pixelColor(rendered.width() - 5, rendered.height() // 2) == QColor(str(theme.C.void))
        paints.clear()
        value = edit.verticalScrollBar().value()
        QTest.keyClick(edit, Qt.Key.Key_PageDown)
        run_event_loop(20)
        checks['page_down'] = edit.verticalScrollBar().value() > value and painted()
        paints.clear()
        edit.verticalScrollBar().setValue(edit.verticalScrollBar().maximum())
        run_event_loop(20)
        checks['scrollbar_to_end'] = edit.verticalScrollBar().value() == edit.verticalScrollBar().maximum() and painted()
        paints.clear()
        value = edit.verticalScrollBar().value()
        wheel(120)
        checks['wheel_up'] = edit.verticalScrollBar().value() < value and painted()

        cursor = edit.textCursor()
        cursor.setPosition(60)
        cursor.setPosition(100, QTextCursor.MoveMode.KeepAnchor)
        edit.setTextCursor(cursor)
        edit.verticalScrollBar().setValue(20)
        run_event_loop(20)
        scroll = edit.verticalScrollBar().value()
        revision = edit.document().revision()
        selected = edit.textCursor().selectedText()
        if edit.isReadOnly():
            for _ in range(8):
                edit.set_evidence(sample)
            run_event_loop(20)
            checks['refresh_keeps_scroll_selection_and_revision'] = (
                edit.verticalScrollBar().value() == scroll and edit.document().revision() == revision
                and edit.textCursor().selectedText() == selected)
            QTest.keyClicks(edit, 'must-not-edit')
            checks['read_only'] = edit.toPlainText() == sample
        else:
            QTest.keyClicks(edit, 'editable-input')
            changed = edit.toPlainText()
            edit.undo()
            checks['edit_and_undo'] = changed != sample and edit.toPlainText() == sample
            edit.redo()
            checks['redo'] = edit.toPlainText() == changed

        # Long unwrapped JSON lines use the same viewport hook horizontally.
        edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        edit.setPlainText(('0123456789' * 200 + '\n') * 40)
        run_event_loop(20)
        paints.clear()
        edit.horizontalScrollBar().setValue(edit.horizontalScrollBar().maximum())
        run_event_loop(20)
        checks['horizontal_scroll'] = edit.horizontalScrollBar().value() > 0 and painted()
        checks['opaque_viewport'] = edit.viewport().testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        return checks
    finally:
        edit.viewport().removeEventFilter(watcher)
        watcher.deleteLater()
        edit.setLineWrapMode(before_wrap)
        edit.setPlainText(before_text)
        edit.setTextCursor(before_cursor)
        edit.setEnabled(before_enabled)
        blocker.unblock()


def probe_captured_receipt(edit):
    """Paint the actual receipt on a wheel step before any text selection."""
    paints = []

    class Paints(QObject):
        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Paint:
                paints.append(event.region())
            return False

    watcher = Paints(edit)
    edit.viewport().installEventFilter(watcher)
    try:
        edit.verticalScrollBar().setValue(0)
        run_event_loop(30)
        paints.clear()
        revision = edit.document().revision()
        captured = edit.toPlainText()
        p = edit.viewport().rect().center()
        event = QWheelEvent(QPointF(p), QPointF(edit.viewport().mapToGlobal(p)), QPoint(), QPoint(0, -120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(edit.viewport(), event)
        run_event_loop(30)
        from PySide6.QtGui import QRegion
        region = QRegion()
        for paint in paints:
            region = region.united(paint)
        scroll = edit.verticalScrollBar().value()
        first = edit.firstVisibleBlock().text()
        checks = {'real_receipt_wheel_scrolls': scroll > 0,
                  'real_receipt_paints_without_selection': region.contains(edit.viewport().rect())
                    and not edit.textCursor().hasSelection()}
        rendered = edit.viewport().grab().toImage()
        checks['real_receipt_background_readable'] = rendered.pixelColor(rendered.width() - 5, rendered.height() // 2) == QColor(str(theme.C.void))
        run_event_loop(550)  # Source/expiry watchdog remains active.
        checks['real_receipt_watchdog_keeps_reading'] = (edit.toPlainText() == captured
            and edit.document().revision() == revision and edit.verticalScrollBar().value() == scroll
            and edit.firstVisibleBlock().text() == first)
        return checks
    finally:
        edit.viewport().removeEventFilter(watcher)
        watcher.deleteLater()


def validate_context_receipt(repository_root, destination, *, native_quiet=False):
    """Create a private simulated observation through the real inspection UI."""
    from datetime import datetime, timezone
    from time import monotonic
    from .service_provider import ServiceProvider
    from .source_switch import SwitchableProvider
    from .widgets.policy_context import PolicyContextInspection, _export_captured
    from ..control_plane.contracts import NetworkStateSnapshot
    from ..control_plane.topology import default_simulated_topology
    from ..core import Provenance, EvidenceKind
    from ..qkd.dashboard_api import AssuranceDashboardService

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    service = AssuranceDashboardService(repository_root=Path(repository_root),
                                        control_journal_path=destination / 'state.sqlite')
    runtime = service.controller_runtime
    topology, _ = service.topology_store.initialize(default_simulated_topology())
    provider = SwitchableProvider(ServiceProvider(service, state_root=destination))
    provider.running = True
    view = PolicyContextInspection()
    view.attach(provider)
    if native_quiet:
        view.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        view.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        view.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
    view.show()

    def wait():
        deadline = monotonic() + 30
        while view.job.active or view.export_job.active:
            if monotonic() > deadline:
                raise TimeoutError('Context observation worker did not finish')
            run_event_loop(10)
        QApplication.processEvents()

    before = runtime.journal.path.read_bytes()
    try:
        view.load_button.click()
        wait()
        if view.context is None:
            raise RuntimeError(view.status.text())
        node = next(node for node in topology.nodes if node.role.value == 'repeater')
        stamp = datetime.now(timezone.utc).isoformat()
        state = NetworkStateSnapshot(
            request_id='scroll-validation', node_id=node.node_id, timestamp_utc=stamp,
            state_version=node.state_version, predicted_fidelity=.9, prediction_uncertainty=.01,
            telemetry_age_s=.1, memory_state=node.memory_state,
            available_pair_fidelities=node.available_pair_fidelities, memory_ages_s=node.memory_ages_s,
            telemetry=node.telemetry, integrity_verified=True, operator_interlock=False,
            provenance=Provenance(evidence_kind=EvidenceKind.SIMULATED, source='isolated-scroll-validation',
                timestamp=stamp, metadata=dict(topology_id=topology.topology_id,
                evidence_lane='bell_fidelity_control', target_semantics='simulated_future_bell_fidelity',
                physical_calibration=False)))
        view.input.setPlainText(json.dumps(state.to_dict()))
        view.observe_button.click()
        wait()
        if view.observation is None:
            raise RuntimeError(view.status.text())
        result = view.observation
        view.tabs.setCurrentWidget(view.receipt)
        reveal_text(view, view.receipt)
        checks = probe_captured_receipt(view.receipt)
        captured = view.receipt.toPlainText()
        view.export_job.submit(_export_captured, captured + '\n', str(destination / 'captured.json'),
                               provider.active.export_captured_evidence)
        wait()
        if view.export_job.error:
            raise RuntimeError(view.export_job.error)
        checks['captured_export_equals_observation'] = json.loads((destination / 'captured.json').read_text(encoding='utf-8')) == result
        checks['private_inspection_keeps_journal'] = runtime.journal.path.read_bytes() == before
        checks['inspection_does_not_authorize_selection'] = not result['record']['selection_available']
        view.grab().save(str(destination / 'captured-receipt-after-wheel.png'))
        return {'passed': all(checks.values()), 'checks': checks,
                'observation_status': result['record']['status'], 'record_sha256': result['record_sha256']}
    finally:
        view.close()
        wait()
        view.deleteLater()
        provider.running = False
        run_event_loop(20)


def validate_text_scrolling(destination, *, native_quiet=False, repository_root=None):
    """Test real dialogs with a labelled display corpus; no policy operations."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance()
    previous_theme = theme.current_theme()
    cases = []
    try:
        for mode in ('dark', 'light'):
            theme.set_theme(mode)
            theme.install(app)
            for name, factory in text_dialog_cases():
                dialog = factory()
                try:
                    if native_quiet:
                        # Keep native opacity (and real scroll blitting); do not
                        # activate the user's desktop or accept external input.
                        dialog.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
                        dialog.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
                        dialog.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
                    dialog.resize(960, 680)
                    dialog.show()
                    prepare_display_fixture(dialog)
                    fields = dialog.findChildren(QPlainTextEdit)
                    for index, field in enumerate(fields):
                        reveal_text(dialog, field)
                        field_name = next((key for key, value in vars(dialog).items() if value is field), f'text-{index}')
                        checks = probe_text_scroll(field)
                        cases.append({'dialog': name, 'field': field_name, 'theme': mode,
                                      'editable': not field.isReadOnly(), 'checks': checks, 'passed': all(checks.values())})
                        if name in ('PolicyContextInspection', 'PolicyReview', 'CampaignRunner'):
                            field.setPlainText('\n'.join(f'{i:04d} · VISUAL SCROLL PROBE · Ω' for i in range(240)))
                            field.verticalScrollBar().setValue(80)
                            run_event_loop(20)
                            dialog.grab().save(str(destination / f'{mode}-{name}-{field_name}.png'))
                finally:
                    dialog.close()
                    dialog.deleteLater()
                    from PySide6.QtCore import QCoreApplication
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        report = {'passed': bool(cases) and all(case['passed'] for case in cases), 'cases': cases,
                  'case_count': len(cases), 'check_count': sum(len(case['checks']) for case in cases),
                  'dialog_count': len({case['dialog'] for case in cases}), 'qt_platform': app.platformName(),
                  'native_window_opacity': 1, 'physical_action_performed': False,
                  'scope': 'real text widgets; synthetic display corpus; no packages, approvals or submitted operations'}
        if repository_root is not None:
            report['captured_context'] = validate_context_receipt(repository_root, destination / 'context', native_quiet=native_quiet)
            report['passed'] = report['passed'] and report['captured_context']['passed']
        (destination / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        return report
    finally:
        theme.set_theme(previous_theme)
        theme.install(app)
