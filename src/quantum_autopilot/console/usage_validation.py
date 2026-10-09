"""Exercise reading, hover help and frozen path results in the actual console."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QHelpEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractButton, QTabWidget, QToolTip

from quantum_autopilot.control_plane import NetworkStateSnapshot
from quantum_autopilot.control_plane.console_commands import ConsoleCommandJournal
from quantum_autopilot.control_plane.inventory import AddSimulatedNode
from quantum_autopilot.core import EvidenceKind, Provenance
from quantum_autopilot.qkd.dashboard_api import demo_service
from .responsive_validation import run_event_loop
from .service_provider import ServiceProvider
from .shell import ConsoleWindow


def _wait(predicate, timeout=35):
    deadline = monotonic() + timeout
    while not predicate():
        if monotonic() >= deadline:
            raise TimeoutError('The interface did not complete the local validation operation')
        run_event_loop(10)
    QApplication.processEvents()


def _scroll_check(edit, refresh):
    """Use a wheel event and verify that new text is painted without rereading."""
    paints = []

    class Observer(QObject):
        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Paint:
                paints.append(event.region().boundingRect())
            return False

    observer = Observer(edit)
    edit.viewport().installEventFilter(observer)
    edit.verticalScrollBar().setValue(0)
    run_event_loop(10)
    paints.clear()
    first = edit.firstVisibleBlock().blockNumber()
    point = edit.viewport().rect().center()
    event = QWheelEvent(QPointF(point), QPointF(edit.viewport().mapToGlobal(point)), QPoint(), QPoint(0, -720),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(edit.viewport(), event)
    run_event_loop(20)
    value, revision = edit.verticalScrollBar().value(), edit.document().revision()
    moved = value > 0 and edit.firstVisibleBlock().blockNumber() > first
    repainted = any(rect.height() >= edit.viewport().height() for rect in paints)
    for _ in range(12):
        refresh()
    run_event_loop(300)
    stable = edit.verticalScrollBar().value() == value and edit.document().revision() == revision
    edit.viewport().removeEventFilter(observer)
    observer.deleteLater()
    return {'wheel_moves_visible_text': moved, 'viewport_repainted': repainted, 'refresh_preserves_reading': stable}


def validate_usage_feedback(paths, destination, *, transparent=False):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance()
    service = demo_service(paths.repository_root, destination / 'state')
    provider = ServiceProvider(service, state_root=destination / 'state', poll_ms=1000)
    window = ConsoleWindow(provider, skip_auth=True)
    from quantum_autopilot.desktop_instance import mark_isolated_diagnostic_window
    mark_isolated_diagnostic_window(window)
    if transparent:
        window.setWindowOpacity(0)
        window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        window.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        window.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        for screen in window.screens.values():
            for dialog in screen.findChildren(QTabWidget):
                dialog.window().setWindowOpacity(0)
    checks, readings = {}, {}
    try:
        window.resize(1440, 900)
        window.show()
        window.provider.start()
        _wait(lambda: window._snap is not None and window._snap.capabilities.allows('run_workspace_path')
              and not window._busy)
        window.splash.finished.emit()
        window.show_screen('topology')
        topology = window.screens['topology']
        runner = topology.path_runner
        if transparent:
            runner.setWindowOpacity(0)
        page_changes = []

        class PageObserver(QObject):
            def eventFilter(self, watched, event):
                if event.type() == QEvent.Type.EnabledChange:
                    page_changes.append(watched.isEnabled())
                return False

        page_observer = PageObserver(topology)
        topology.installEventFilter(page_observer)
        for name, temporal, target, probabilities, ttl in (
            ('ideal_target_met', False, '.8', (1, 1), 3),
            ('ideal_target_not_met', False, '1', (1, 1), 3),
            ('temporal_success', True, '.8', (1, 1), 3),
            ('temporal_timeout', True, '.8', (0, 0), 3),
            ('temporal_expiry', True, '.8', (1, 0), 1),
        ):
            runner.begin()
            runner.resize(570, 550)
            runner.protocol.setCurrentIndex(1 if temporal else 0)
            for key, value in (('source', 'N-01'), ('repeater', 'R-07'), ('destination', 'N-09')):
                runner.fields[key].setCurrentText(value)
            runner.target.setText(target)
            if temporal:
                fields = dict(success_probability_left=probabilities[0], success_probability_right=probabilities[1],
                    attempt_interval_s=.001, max_attempts=3, memory_t1_s=.02, memory_t2_s=.02,
                    memory_ttl_steps=ttl, seed=7)
                for key, value in fields.items():
                    runner.temporal_fields[key].setText(str(value))
            QTest.mouseClick(runner.run, Qt.MouseButton.LeftButton)
            _wait(lambda: runner.pending is None and not window._busy)
            result = window._snap.workspace_path
            text = runner.results.toPlainText()
            readback = json.loads(text.split('PERSISTED RESULT READBACK\n', 1)[1]) if 'PERSISTED RESULT READBACK\n' in text else {}
            visible = runner.results.isVisible() and runner.results.viewport().height() > 70 and all(
                runner.rect().contains(runner.results.mapTo(runner, point))
                for point in (QPoint(0, 0), runner.results.rect().bottomRight()))
            # JSON tuples are arrays; compare the canonical representation.
            checks[name + '_persisted_result'] = result is not None and readback == json.loads(json.dumps(asdict(result)))
            checks[name + '_visible'] = visible and runner.tabs.currentWidget() is runner.result_page
            checks[name + '_outcome'] = result is not None and (
                result.target_met if name.endswith('target_met') or name == 'temporal_success' else
                not result.target_met if name.endswith('target_not_met') else
                result.outcome == 'acquisition_timeout')
            if name == 'temporal_expiry':
                checks[name + '_expired_pairs'] = result is not None and result.expired_pairs > 0
            runner.grab().save(str(destination / (name + '.png')))
            runner.close()
        checks['commands_keep_page_enabled'] = not page_changes and topology.isEnabled()
        topology.removeEventFilter(page_observer)

        runner.begin()
        runner.target.setText('nan')
        QTest.mouseClick(runner.run, Qt.MouseButton.LeftButton)
        checks['invalid_request_shows_refusal'] = runner.pending is None and 'REFUSED' in runner.status.text()
        runner.close()

        checks['unlocated_nodes_have_no_geographic_wires'] = not topology.queue.drawn_link_keys() and all(
            topology._net.node(link.a).located and topology._net.node(link.b).located
            for link, *_ in topology.geo.segments())
        topology._node_clicked('N-13')
        app.processEvents()
        node_window = topology.node_windows.windows.get('N-13')
        from .responsive_validation import wait_for_node_presentation
        unlocated_presentation = wait_for_node_presentation(
            node_window, lambda: topology._snap, lambda: topology._net)
        checks['unlocated_node_details_keep_connection'] = node_window is not None and 'N05-N13' in node_window.connection_texts()
        topology.node_windows.close_all()

        # Seed authentic, hashed read-only receipts in this isolated test journal.
        runtime = service.controller_runtime
        state = NetworkStateSnapshot(request_id=uuid4().hex, node_id='R-07',
            timestamp_utc=datetime.now(timezone.utc).isoformat(), state_version=window._snap.topology.version,
            predicted_fidelity=.9, prediction_uncertainty=.01, telemetry_age_s=.1, memory_state='STORING',
            available_pair_fidelities=(.9, .9), memory_ages_s=(0., 0.),
            telemetry={'validation_rows': list(range(300))}, integrity_verified=True, operator_interlock=True,
            out_of_distribution=False, provenance=Provenance(evidence_kind=EvidenceKind.SIMULATED,
                source='interface-reading-validation', metadata={'evidence_lane': 'bell_fidelity_control',
                'target_semantics': 'simulated_future_bell_fidelity'}))
        runtime.cycle(state)
        audit = window.screens['fpga'].policy_decisions
        if transparent:
            audit.setWindowOpacity(0)
        audit.show()
        audit.resize(960, 650)
        QTest.mouseClick(audit.load_button, Qt.MouseButton.LeftButton)
        _wait(lambda: not audit.job.active)
        QTest.mouseClick(audit.explain_button, Qt.MouseButton.LeftButton)
        _wait(lambda: not audit.job.active)
        tabs = audit.findChild(QTabWidget)
        for name in ('details', 'receipt'):
            edit = getattr(audit, name)
            tabs.setCurrentWidget(edit)
            app.processEvents()
            readings['decision_' + name] = _scroll_check(edit, audit._tick)
            audit.grab().save(str(destination / ('decision-' + name + '.png')))
        audit.close()

        journal = ConsoleCommandJournal(runtime.journal.path)
        command_id = journal.begin('add_simulated_node', asdict(AddSimulatedNode('READING-PROBE', 'endpoint',
            window._snap.topology.version, '127.0.0.1', 7411)))
        journal.transition(command_id, 'RUNNING')
        journal.transition(command_id, 'UNKNOWN', {'reason': 'execution_or_readback_failed',
            'error_type': 'IsolatedValidationFixture'})
        window.provider.active.request_refresh()
        _wait(lambda: any(row.command_id == command_id for row in window._snap.ledger.commands))
        history = window.screens['audit'].command_history
        if transparent:
            history.setWindowOpacity(0)
        history.show()
        history.table.selectRow(next(i for i, row in enumerate(history.records) if row.command_id == command_id))
        history.tabs.setCurrentWidget(history.detail)
        app.processEvents()
        readings['command_receipt'] = _scroll_check(history.detail, history.selected)
        QTest.mouseClick(history.check_button, Qt.MouseButton.LeftButton)
        _wait(lambda: not history.job.active)
        readings['command_evidence'] = _scroll_check(history.output, history._render)
        history.grab().save(str(destination / 'command-center.png'))
        history.close()
        for name, result in readings.items():
            checks.update({name + '_' + key: value for key, value in result.items()})

        buttons = window.canvas.findChildren(QAbstractButton)
        checks['buttons_have_descriptions'] = bool(buttons) and all(button.toolTip().strip() for button in buttons)
        topology.inspector.set_tab('links')
        app.processEvents()
        button = topology.run_path
        point = button.rect().center()
        app.sendEvent(button, QHelpEvent(QEvent.Type.ToolTip, point, button.mapToGlobal(point)))
        run_event_loop(180)  # Native Windows may animate the tooltip's initial show.
        checks['hover_displays_description'] = QToolTip.isVisible() and QToolTip.text() == button.toolTip()
        if transparent:
            for widget in app.topLevelWidgets():
                if widget.windowType() == Qt.WindowType.ToolTip:
                    widget.setWindowOpacity(0)
        QToolTip.hideText()
        checks['physical_executor_unavailable'] = not service.control_plane_status()['physical_executor_available']
        report = {'passed': all(checks.values()), 'checks': checks, 'readings': readings,
            'unlocated_node_presentation': unlocated_presentation,
            'button_count': len(buttons), 'qt_platform': app.platformName(),
            'hover_button_visible': button.isVisible(), 'hover_button_enabled': button.isEnabled(),
            'hover_text': QToolTip.text(),
            'scope': 'local software UI, isolated simulation state; no physical actuation'}
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
    (destination / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report
