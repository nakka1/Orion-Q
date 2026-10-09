"""Native keyrate shadow diagnostic with original external weights, no control."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qkd-zip', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.console import theme
    from quantum_autopilot.console.screens.fpga import EdgeControlScreen
    from quantum_autopilot.console.synthetic import SyntheticProvider
    from quantum_autopilot.control_plane.optional_keyrate import MODEL_ID, export_keyrate_shadow
    from quantum_autopilot.model_intake.keyrate_session import evaluate_keyrate_session

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
    for model_id in (MODEL_ID,):
        screen = EdgeControlScreen()
        provider = SyntheticProvider(Path.cwd(), args.output / 'unused-state')
        commands = []
        screen.command.connect(lambda *a: commands.append(a))
        provider.snapshot_changed.connect(screen.apply)
        catalog = screen.model_catalog
        try:
            catalog.show()
            catalog.table.selectRow(next(i for i, row in enumerate(catalog.options) if row.model_id == model_id))
            catalog.checker.start(model_id, args.qkd_zip)
            settle(catalog.checker)
            assert catalog.checker.result is not None, catalog.checker.error
            screen.apply(provider.snapshot())
            QTest.mouseClick(catalog.shadow_button, Qt.MouseButton.LeftButton)
            dialog = catalog.keyrate_shadow
            assert dialog is not None and dialog.isVisible() and dialog.start_button.isEnabled()
            assert not dialog.running and dialog.job.result is None
            initial = provider.snapshot().optional_keyrate_state.to_json()
            QTest.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)
            for tick in range(6):
                if tick:
                    provider.advance()
                before = provider.snapshot().optional_keyrate_state.to_json()
                settle(dialog.job)
                result = dialog.job.result
                assert result is not None and result.status == 'evaluated', dialog.output.toPlainText()
                assert provider.snapshot().optional_keyrate_state.to_json() == before
                assert not any((result.policy_activated, result.physical_action_performed,
                                result.simulator_action_performed, result.settings_mutated))
                evaluation = json.loads(result.evaluation_json)
                assert len(evaluation['candidates']) == 9
                assert result.observation_source == ('calibration_bootstrap' if tick == 0 else 'previous_period')
                assert evaluation['baseline_settings'] == json.loads(before)['telemetry']['optional_keyrate_window']['context']['settings']
                offline = json.loads(evaluate_keyrate_session(args.qkd_zip,
                                                               evaluation['input_json']).serialized())
                compare = dict(evaluation)
                del compare['evaluated_at_utc']
                del offline['evaluated_at_utc']
                assert compare == offline
                assert not commands
                path = args.output / f'{model_id}-{tick}.json'
                export_keyrate_shadow(result, path)
                checks.append({'model_id': model_id, 'tick': tick, 'recommendation': evaluation['recommendation']['action_name'],
                               'candidate_count': len(evaluation['candidates']), 'observation_source': result.observation_source, 'seed': evaluation['seed'],
                               'offline_parity': True, 'snapshot_sha256': result.snapshot_sha256,
                               'archive_sha256': evaluation['archive_sha256'], 'context_sha256': result.context_sha256,
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
            if catalog.keyrate_shadow is not None:
                settle(catalog.keyrate_shadow.job)
            screen.close()
            screen.deleteLater()
            app.processEvents()
    assert not (args.output / 'unused-state').exists()
    with (args.output / 'report.json').open('x', encoding='utf-8') as stream:
        json.dump({'schema': 'orion.keyrate-shadow-ui-diagnostic.v1', 'passed': True, 'checks': checks,
                   'commands_emitted': 0, 'physical_action_performed': False, 'settings_mutated': False,
                   'policy_activated': False,
                   'scope': 'native opt-in UI wiring, independent artificial observables, no control or key-rate performance evidence'},
                  stream, indent=2, allow_nan=False)
    print('6 native QKD actor evaluations passed; explicit bootstrap and original-weight parity, no settings applied.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
