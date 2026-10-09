"""Native routing shadow diagnostic with original external weights, no control."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qnet-zip', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.console import theme
    from quantum_autopilot.console.screens.fpga import EdgeControlScreen
    from quantum_autopilot.console.synthetic import SyntheticProvider
    from quantum_autopilot.control_plane.optional_routing import MODEL_IDS, export_routing_shadow
    from quantum_autopilot.model_intake.routing_session import evaluate_routing_session

    args.output.mkdir(parents=False, exist_ok=False)
    app = QApplication.instance() or QApplication([])
    theme.install(app)

    def settle(job):
        deadline = time.monotonic() + 15
        while job.active and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(10)
        if job.active:
            raise ValueError('worker timeout')

    checks = []
    for model_id in MODEL_IDS:
        screen = EdgeControlScreen()
        provider = SyntheticProvider(Path.cwd(), args.output / 'unused-state')
        commands = []
        screen.command.connect(lambda *a: commands.append(a))
        provider.snapshot_changed.connect(screen.apply)
        catalog = screen.model_catalog
        try:
            catalog.show()
            catalog.table.selectRow(next(i for i, row in enumerate(catalog.options) if row.model_id == model_id))
            catalog.checker.start(model_id, args.qnet_zip)
            settle(catalog.checker)
            assert catalog.checker.result is not None, catalog.checker.error
            screen.apply(provider.snapshot())
            QTest.mouseClick(catalog.shadow_button, Qt.MouseButton.LeftButton)
            dialog = catalog.routing_shadow
            assert dialog is not None and dialog.isVisible() and dialog.start_button.isEnabled()
            assert not dialog.running and dialog.job.result is None
            initial = provider.snapshot().optional_routing_state.to_json()
            QTest.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)
            for tick in range(6):
                if tick:
                    provider.advance()
                before = provider.snapshot().optional_routing_state.to_json()
                settle(dialog.job)
                result = dialog.job.result
                assert result is not None and result.status == 'evaluated', dialog.output.toPlainText()
                assert provider.snapshot().optional_routing_state.to_json() == before
                assert not any((result.policy_activated, result.physical_action_performed,
                                result.simulator_action_performed, result.resources_mutated))
                evaluation = json.loads(result.evaluation_json)
                assert len(evaluation['candidates']) == (6, 6, 5, 5, 5, 4)[tick]
                offline = json.loads(evaluate_routing_session(model_id, args.qnet_zip,
                                                               evaluation['input_json']).serialized())
                compare = dict(evaluation)
                del compare['evaluated_at_utc']
                del offline['evaluated_at_utc']
                assert compare == offline
                assert not commands
                path = args.output / f'{model_id}-{tick}.json'
                export_routing_shadow(result, path)
                checks.append({'model_id': model_id, 'tick': tick, 'recommendation': evaluation['recommendation_action_id'],
                               'candidate_count': len(evaluation['candidates']), 'seed': evaluation['seed'],
                               'offline_parity': True, 'snapshot_sha256': result.snapshot_sha256,
                               'archive_sha256': evaluation['archive_sha256'], 'resource_sha256': result.resource_sha256,
                               'report_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
            for width, height in ((640, 480), (1000, 700)):
                dialog.resize(width, height)
                app.processEvents()
                assert dialog.width() == width and dialog.height() == height
                bounds = QRect(QPoint(0, 0), dialog.size())
                for button in (dialog.start_button, dialog.stop_button, dialog.export_button, dialog.close_button):
                    assert bounds.contains(QRect(button.mapTo(dialog, QPoint(0, 0)), button.size()))
                assert dialog.grab().save(str(args.output / f'{model_id}-{width}.png'))
            with (args.output / f'{model_id}-initial-input.json').open('x', encoding='utf-8') as stream:
                stream.write(initial)
        finally:
            provider.stop()
            catalog.close()
            if catalog.routing_shadow is not None:
                settle(catalog.routing_shadow.job)
            screen.close()
            screen.deleteLater()
            app.processEvents()
    assert not (args.output / 'unused-state').exists()
    with (args.output / 'report.json').open('x', encoding='utf-8') as stream:
        json.dump({'schema': 'orion.routing-shadow-ui-diagnostic.v1', 'passed': True, 'checks': checks,
                   'commands_emitted': 0, 'physical_action_performed': False, 'resources_mutated': False,
                   'policy_activated': False,
                   'scope': 'native opt-in UI wiring, illustrative artificial features/resources, no control or routing performance evidence'},
                  stream, indent=2, allow_nan=False)
    print('12 native routing evaluations passed; ordered candidates and original-weight parity, no commands or resource mutation.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
