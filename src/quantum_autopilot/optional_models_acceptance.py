"""Explicit local/offscreen smoke diagnostic for optional native model sessions.

No downloads, installation, device commands or access to operator journals.
External reviewed packages are read only when both paths are explicitly supplied.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time


def _wait(app, job):
    deadline = time.monotonic() + 30
    while job.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    if job.active:
        raise TimeoutError("optional model worker exceeded 30 seconds")
    if job.error:
        raise ValueError(job.error)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def run_checks(output, *, qkd_zip=None, qnet_zip=None):
    if bool(qkd_zip) != bool(qnet_zip):
        raise ValueError("supply both reviewed package paths or neither")
    output = Path(output).resolve()
    if getattr(sys, "frozen", False):
        package = Path(sys.executable).resolve().parent
        if output == package or package in output.parents:
            raise ValueError("diagnostic output must be outside the program package")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "orion.optional-models-acceptance.v1", "passed": False,
              "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "frozen": bool(getattr(sys, "frozen", False)),
              "executable_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
              "mode": "external_packages" if qkd_zip else "without_packages",
              "models": [], "physical_action_performed": False,
              "runtime_policy_activated": False, "independent_machine_verified": False,
              "scope": "local GUI/inference smoke; not a benchmark, scientific validation or full manual acceptance"}
    catalog = None
    dialogs = []
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from .console import theme
        from .console.widgets.model_catalog import ModelCatalog, SESSION_MODELS
        from .console.widgets.sync_session import SyncSession
        from .console.widgets.keyrate_session import KeyRateSession
        from .console.widgets.detector_session import DetectorSession
        from .console.widgets.compensation_session import CompensationSession
        from .console.widgets.routing_session import RoutingSession
        from .console.widgets.allocation_session import AllocationSession

        app = QApplication.instance() or QApplication([])
        theme.install(app)
        catalog = ModelCatalog()
        catalog.show()
        expected = {o.model_id for o in catalog.options if o.status == "offline_only"}
        _require(len(expected) == 11 and set(SESSION_MODELS) == expected, "offline session coverage differs")
        _require(all(not o.default_enabled and not o.runtime_available for o in catalog.options), "unsafe catalog defaults")
        for row, option in enumerate(catalog.options):
            catalog.table.selectRow(row)
            _require(not catalog.evaluate_button.isEnabled(), "session enabled without package check")
            _require(catalog.checker.result is None and not catalog.checker.active, "automatic package read")
            if option.status != "offline_only":
                _require(option.status == "blocked_pickle", "unknown blocked status")
                continue
            if not qkd_zip:
                if option.model_id.startswith("qnet.allocator."):
                    session = AllocationSession(option.model_id)
                elif option.model_id.startswith("qnet.routing."):
                    session = RoutingSession(option.model_id)
                elif option.model_id.startswith("qnet.compensation."):
                    session = CompensationSession(option.model_id)
                elif option.model_id == "qkd.detector.boosting":
                    session = DetectorSession()
                else:
                    session = KeyRateSession() if option.model_id == "qkd.keyrate.actor" else SyncSession()
                dialogs.append(session)
                _require(session.package is None and session.job.result is None and not session.job.active,
                         "session accessed a package automatically")
                _require(not session.run_button.isEnabled() and not session.export_button.isEnabled(),
                         "unverified execution or export enabled")
                report["models"].append({"model_id": option.model_id, "unloaded": True})
                continue
            path = qkd_zip if option.archive_kind == "qkd" else qnet_zip
            catalog.checker.start(option.model_id, str(path))
            _wait(app, catalog.checker)
            catalog._open_session()
            session = catalog.offline_session
            _require(session is not None and session.model_id == option.model_id, "wrong session dispatched")
            _require(not session.editor.toPlainText() and session.job.result is None, "old input reused")
            session.demo_button.clicked.emit()
            _require(not session.job.active and session.job.result is None, "DEMO executed a model")
            session.run_button.clicked.emit()
            _wait(app, session.job)
            result = session.job.result
            _require(result is not None and result.model_id == option.model_id, "wrong evaluation result")
            _require(result.physical_action_performed is False, "unexpected actuation flag")
            destination = output / (option.model_id + ".json")
            session._export_result(result, destination)
            try:
                session._export_result(result, destination)
            except FileExistsError:
                pass
            else:
                raise ValueError("export overwrote an existing file")
            report["models"].append({"model_id": option.model_id, "archive_sha256": result.archive_sha256,
                                     "result": destination.name, "result_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()})
            session.editor.appendPlainText(" ")
            _require(session.job.result is None and not session.export_button.isEnabled(), "edit retained stale result")
        catalog.checker.clear()
        if catalog.offline_session is not None:
            _require(catalog.offline_session.package is None, "forget retained package context")
        _require(len(report["models"]) == 11, "not all offline options checked")
        if qkd_zip:
            from .network_engine.validation import validate_model_retention
            report["network_model_retention"] = validate_model_retention(
                output / "network-model-retention", qnet_zip=qnet_zip, qkd_zip=qkd_zip)
            _require(report["network_model_retention"]["passed"], "live model retention regression failed")
        report["passed"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        for dialog in dialogs:
            dialog.close()
            dialog.deleteLater()
        if catalog is not None:
            catalog.close()
            catalog.deleteLater()
        with (output / "optional-models-report.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="new diagnostics directory only")
    parser.add_argument("--qkd-zip", type=Path)
    parser.add_argument("--qnet-zip", type=Path)
    args = parser.parse_args(argv)
    if bool(args.qkd_zip) != bool(args.qnet_zip):
        parser.error("supply both reviewed package paths or neither")
    report = run_checks(args.output_dir, qkd_zip=args.qkd_zip, qnet_zip=args.qnet_zip)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
