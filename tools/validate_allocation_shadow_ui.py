"""Native allocation shadow diagnostic with original external weights, no control."""
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
    from quantum_autopilot.control_plane.optional_allocation import MODEL_IDS, export_allocation_shadow

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
            dialog = catalog.allocation_shadow
            assert dialog is not None and dialog.isVisible() and dialog.start_button.isEnabled()
            assert not dialog.running and dialog.job.result is None
            initial = provider.snapshot().optional_allocation_state.to_json()
            QTest.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)
            for tick in range(6):
                if tick:
                    provider.advance()
                before = provider.snapshot().optional_allocation_state.to_json()
                settle(dialog.job)
                result = dialog.job.result
                assert result is not None and result.status == 'evaluated', dialog.output.toPlainText()
                expected = 'persistence' if model_id == MODEL_IDS[0] else 'ridge' if tick == 5 else 'warmup_persistence'
                assert result.final_output_status == expected
                assert provider.snapshot().optional_allocation_state.to_json() == before
                assert not any((result.policy_activated, result.physical_action_performed,
                                result.simulator_action_performed, result.queue_mutated))
                evaluation = json.loads(result.evaluation_json)
                assert evaluation['history_samples'] == tick
                assert not commands
                path = args.output / f'{model_id}-{tick}.json'
                export_allocation_shadow(result, path)
                checks.append({'model_id': model_id, 'tick': tick, 'status': result.final_output_status,
                               'history_samples': tick, 'snapshot_sha256': result.snapshot_sha256,
                               'archive_sha256': evaluation['archive_sha256'], 'plant_sha256': evaluation['plant_sha256'],
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
            if catalog.allocation_shadow is not None:
                settle(catalog.allocation_shadow.job)
            screen.close()
            screen.deleteLater()
            app.processEvents()
    assert not (args.output / 'unused-state').exists()
    with (args.output / 'report.json').open('x', encoding='utf-8') as stream:
        json.dump({'schema': 'orion.allocation-shadow-ui-diagnostic.v1', 'passed': True, 'checks': checks,
                   'commands_emitted': 0, 'physical_action_performed': False, 'queue_mutated': False,
                   'policy_activated': False,
                   'scope': 'native opt-in UI wiring, causal artificial history, no closed-loop or delivered-key evidence'},
                  stream, indent=2, allow_nan=False)
    print('12 native allocation evaluations passed; warm-up/full history, no commands or queue mutation.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
