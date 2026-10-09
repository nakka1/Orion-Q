"""Explicit native shadow smoke with external reviewed weights; no performance claim."""
import argparse
import json
import os
from pathlib import Path
import time


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qnet-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.console import theme
    from quantum_autopilot.console.synthetic import SyntheticProvider
    from quantum_autopilot.console.widgets.compensation_shadow import CompensationShadowSession
    from quantum_autopilot.control_plane.optional_compensation import MODEL_IDS, export_compensation_shadow
    from quantum_autopilot.model_intake.verification import verify_model_archive

    args.output.mkdir(parents=False, exist_ok=False)
    app = QApplication.instance() or QApplication([])
    theme.install(app)
    provider = SyntheticProvider(Path.cwd(), args.output / "unused-state")
    checks = []
    try:
        for model_id in MODEL_IDS:
            dialog = CompensationShadowSession(model_id)
            try:
                package = verify_model_archive(model_id, args.qnet_zip)
                dialog.set_package(package)
                dialog.show()
                for batch in range(2):
                    provider.advance()
                    dialog.accept_snapshot(provider.snapshot())
                    if batch == 0:
                        assert dialog.job.result is None and not dialog.running
                        dialog.start()
                    deadline = time.monotonic() + 15
                    while dialog.job.active and time.monotonic() < deadline:
                        app.processEvents()
                        QTest.qWait(10)
                    assert not dialog.job.active, "worker timeout"
                    result = dialog.job.result
                    assert result is not None and result.status == "evaluated", dialog.output.toPlainText()
                    assert result.final_output_status == "model" and not result.physical_action_performed
                    assert json.loads(result.evaluation_json)["reference_rotation"] is None
                    export_compensation_shadow(result, args.output / f"{model_id}-{batch}.json")
                    checks.append({"model_id": model_id, "batch": batch, "snapshot_sha256": result.snapshot_sha256,
                                   "archive_sha256": package.sha256, "status": result.final_output_status})
                dialog.resize(640, 480)
                app.processEvents()
                assert dialog.grab().save(str(args.output / f"{model_id}-640.png"))
            finally:
                dialog.close()
                dialog.deleteLater()
                app.processEvents()
    finally:
        provider.stop()
    with (args.output / "report.json").open("x", encoding="utf-8") as stream:
        json.dump({"passed": True, "checks": checks, "physical_action_performed": False,
                   "scope": "native UI/software wiring only; artificial SO(3); no performance or physics validation"},
                  stream, indent=2, allow_nan=False)
    print("8 native batch evaluations passed across four checkpoints; no actuation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
