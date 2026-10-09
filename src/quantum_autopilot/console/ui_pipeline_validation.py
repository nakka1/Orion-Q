"""Native UI acceptance using a fresh demo service and its real asynchronous pipeline.

This diagnostic creates a separate marked window and state directory. It never
owns the operator's desktop mutex, accesses an operator journal, or actuates hardware.
An external timeout should bound the process in case the Qt loop becomes unresponsive.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch


def validate_ui_pipeline(output, repository_root=None):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QScrollArea

    from quantum_autopilot.desktop import bundled_repository_root
    from quantum_autopilot.desktop_instance import mark_isolated_diagnostic_window
    from quantum_autopilot.qkd.dashboard_api import demo_service

    from . import theme
    from .branding import app_icon, configure_windows_app_identity
    from .display import configure_high_dpi, show_main_window
    from .service_provider import ServiceProvider
    from .shell import ConsoleWindow
    from .ui_gc import UiThreadCollector

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    state_root = output / f"isolated-state-{time.time_ns()}"
    state_root.mkdir()
    checks, details, screenshots, errors = {}, {}, {}, []
    startup = {"provider_errors": [], "publications": []}
    began = time.monotonic()
    configure_windows_app_identity()
    configure_high_dpi()
    app = QApplication.instance() or QApplication([])
    app.setWindowIcon(app_icon())
    collector = UiThreadCollector(app)
    collector.start()
    theme.install(app)
    provider = window = None

    def wait(predicate, *, seconds=45., label="condition"):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            # QtTest's C++ wait holds the Python GIL, starving the actual service
            # and network workers under test. Sleep briefly outside Qt instead.
            time.sleep(.01)
        raise AssertionError("Timed out waiting for " + label)

    def click(widget):
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(widget, 10, 20)
            parent = parent.parentWidget()
        app.processEvents()
        if not widget.isVisible() or not widget.isEnabled():
            raise AssertionError("Requested UI control is not visible/enabled: " + widget.accessibleName())
        stage = window.centralWidget()
        root_point = widget.mapTo(stage.content, widget.rect().center())
        point = stage.mapFromScene(stage.proxy.mapToScene(QPointF(root_point)))
        if not stage.viewport().rect().contains(point):
            raise AssertionError("Requested UI control is outside the rendered viewport")
        QTest.mouseClick(stage.viewport(), Qt.MouseButton.LeftButton, pos=point)
        app.processEvents()

    def capture(name):
        path = output / (name + ".png")
        # Allow queued scene paints to reach the native surface. A grab of the
        # widget would synchronously repaint and conceal a stale proxy raster.
        deadline = time.monotonic() + .15
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        # Capture the actual native window, including the visible scrolling
        # viewport; no duplicate widget or separate renderer supplies the pixels.
        pixels = window.screen().grabWindow(int(window.winId()))
        if pixels.isNull() or not pixels.save(str(path)):
            raise RuntimeError("Cannot capture rendered application window")
        screenshots[name] = str(path)

    def run_case(name, action):
        print("UI pipeline: " + name, flush=True)
        try:
            action()
        except Exception as error:
            checks[name] = False
            errors.append({"case": name, "error": f"{type(error).__name__}: {error}"})
        else:
            checks[name] = True

    def snapshot():
        return window._snap

    def device(device_id):
        data = snapshot().live.technologies.get("fpga_devices", {})
        return next(item for item in data.get("devices", ()) if item["configuration"]["device_id"] == device_id)

    try:
        root = repository_root or bundled_repository_root()
        service = demo_service(root, state_root, preset="brazil-institutions-v1", scenario="quiet")
        provider = ServiceProvider(service, state_root=state_root / "demo")
        provider.error.connect(startup["provider_errors"].append)
        provider.snapshot_changed.connect(lambda snap: startup["publications"].append(
            {"elapsed_s": round(time.monotonic() - began, 3),
             "tick": snap.live.tick if snap.live is not None else None}))
        window = ConsoleWindow(provider, skip_auth=True, state_root=state_root)
        window.resize(1440, 900)
        mark_isolated_diagnostic_window(window)
        show_main_window(window)
        window.provider.start()
        wait(lambda: snapshot() is not None and snapshot().live is not None and snapshot().live.tick >= 2,
             seconds=120., label="committed real provider snapshot")
        startup["ready_s"] = time.monotonic() - began
        window.show_screen("topology")
        details["topology"] = {"nodes": len(snapshot().live.nodes), "links": len(snapshot().live.links),
                               "source": snapshot().live.source_kind, "state_root": str(state_root)}
        checks["real_demo_topology"] = len(snapshot().live.nodes) >= 71 and snapshot().live.source_kind == "simulated"
        topology = window.screens["topology"]

        def google_graph():
            node_id = next(ident for ident in ("US-GOOG-QA", "GOOGLE-QAI") if snapshot().live.node(ident) is not None)
            opened = time.monotonic()
            panel = topology._open_window(node_id)
            wait(lambda: panel.presentation_status()["ready"], label="Google node materialization")
            materialized_s = time.monotonic() - opened
            initial_presentation = panel.presentation_status()
            wait(lambda: any(item.points and any(value is not None for _at, value in item.points)
                             for item in panel.protocol_series._series), label="Google received protocol observable")
            selected = next(item for item in panel.protocol_series._series
                            if any(value is not None for _at, value in item.points))
            panel.protocol_series.selector.setCurrentIndex(panel.protocol_series.selector.findData(selected.key))
            assert panel.protocol_series.isVisible() and panel.chart.keys() == []
            delivered = panel.protocol_series.chart.visible_samples(selected.key)
            assert delivered and all(point in selected.points for point in delivered)
            assert "simulated" in panel.protocol_series.metadata.text()
            details["google"] = {"node_id": node_id, "metric": selected.metric, "unit": selected.unit,
                                 "technology": selected.technology, "points": delivered,
                                 "materialized_s": materialized_s,
                                 "initial_presentation_status": initial_presentation,
                                 "presentation_status": panel.presentation_status(),
                                 "metadata": panel.protocol_series.metadata.text()}
            capture("google-protocol-timeline")
            topology.node_windows.close(node_id)

        run_case("google_protocol_timeline", google_graph)

        def tf_graph():
            sessions = snapshot().live.technologies.get("sessions", {})
            ident, session = next((ident, session) for ident, session in sessions.items()
                                  if "tf_qkd" in session.get("config", {}).get("technologies", ()))
            node_id = session["config"]["path"][0]
            opened = time.monotonic()
            panel = topology._open_window(node_id)
            wait(lambda: panel.presentation_status()["ready"], label="TF-QKD node materialization")
            materialized_s = time.monotonic() - opened
            initial_presentation = panel.presentation_status()
            wait(lambda: any(item.technology == "tf_qkd" and item.metric == "conditional_key_estimate_bps"
                             and any(value is not None for _at, value in item.points)
                             for item in panel.protocol_series._series), label="TF-QKD block received")
            selected = next(item for item in panel.protocol_series._series
                            if item.technology == "tf_qkd" and item.metric == "conditional_key_estimate_bps")
            panel.protocol_series.selector.setCurrentIndex(panel.protocol_series.selector.findData(selected.key))
            assert selected.unit == "bit/s" and panel.protocol_series.chart.series[0].rate_hz == 0
            metrics = snapshot().live.technologies["sessions"][ident]["metrics"]
            assert metrics.get("certified_secret_bits") == 0
            assert not any(item.technology == "tf_qkd" and item.metric in ("wave", "fidelity", "bell_fidelity")
                           for item in panel.protocol_series._series)
            details["tf_qkd"] = {"session_id": ident, "node_id": node_id, "metric": selected.metric,
                                 "points": selected.points, "certified_secret_bits": metrics["certified_secret_bits"],
                                 "materialized_s": materialized_s,
                                 "initial_presentation_status": initial_presentation,
                                 "presentation_status": panel.presentation_status(),
                                 "metadata": panel.protocol_series.metadata.text()}
            capture("tf-qkd-received-timeline")
            certified = next(item for item in panel.protocol_series._series
                             if item.technology == "tf_qkd" and item.metric == "certified_secret_bits"
                             and item.session_id == selected.session_id)
            panel.protocol_series.selector.setCurrentIndex(panel.protocol_series.selector.findData(certified.key))
            assert "Bits secretos certificados" in panel.protocol_series.metadata.text()
            assert all(value is None or value == 0 for _at, value in certified.points)
            capture("tf-qkd-certified-zero")
            topology.node_windows.close(node_id)

        run_case("tf_qkd_observables_not_bell_wave", tf_graph)

        def cimatec_wave():
            panel = topology._open_window("CIMATEC")
            wait(lambda: panel.presentation_status()["ready"], label="CIMATEC node materialization")
            assert panel.chart.isVisible() and any(item.samples for item in panel.chart.series)
            details["cimatec"] = {"keys": panel.chart.keys(), "caption": panel.wave_label.text(),
                                  "samples": {item.key: len(item.samples) for item in panel.chart.series}}
            capture("cimatec-legacy-polarisation")
            topology.node_windows.close("CIMATEC")

        run_case("cimatec_preserves_legacy_wave", cimatec_wave)
        window.show_screen("fpga")

        def fpga_controls():
            panel = window.screens["fpga"].fpga_devices
            wait(lambda: panel.selector.count() > 0 and not window.provider.busy, label="FPGA inventory")
            simulated = next(item for item in snapshot().live.technologies["fpga_devices"]["devices"]
                             if item["mode"] == "simulated")
            panel.selector.setCurrentIndex(panel.selector.findData(simulated["configuration"]["device_id"]))
            device_id = panel.selector.currentData()
            before = device(device_id)["configuration"]
            revision, connected = before["revision"], before["connected"]
            click(panel.connect_button)
            wait(lambda: device(device_id)["configuration"]["revision"] > revision and not window.provider.busy,
                 label="persisted FPGA connection command")
            assert device(device_id)["configuration"]["connected"] != connected
            if not device(device_id)["configuration"]["connected"]:
                click(panel.connect_button)
                wait(lambda: device(device_id)["configuration"]["connected"] and not window.provider.busy,
                     label="FPGA reconnect")
            revision = device(device_id)["configuration"]["revision"]
            panel.cycles.setValue(panel.cycles.value() + 1)
            click(panel.save)
            wait(lambda: device(device_id)["configuration"]["revision"] > revision and not window.provider.busy,
                 label="FPGA configuration readback")
            wait(lambda: bool(device(device_id)["state"].get("last_receipts")), label="processed FPGA receipts")
            current = device(device_id)
            assert current["mode"] == "simulated" and current["state"]["accepted_frames"] > 0
            assert current["state"]["temperature_c"] is None and current["state"]["measured_fpga_latency_s"] is None
            assert any(row["channel_receipt"].get("stages") for row in current["state"]["last_receipts"])
            details["fpga"] = current
            capture("fpga-control-and-receipts")

        run_case("fpga_controls_and_real_processing_receipts", fpga_controls)
        window.show_screen("keys")

        def qkeys_export():
            path = state_root / "chosen-qkeys-evidence.json"
            completed = []
            window.provider.export_completed.connect(completed.append)
            wait(lambda: not window.provider.busy and window.screens["keys"].export_button.isEnabled(),
                 label="QKEYS export ready")
            with patch("quantum_autopilot.console.widgets.evidence_export.QFileDialog.getSaveFileName",
                       return_value=(str(path), "JSON (*.json)")):
                click(window.screens["keys"].export_button)
            wait(lambda: bool(completed) and path.is_file(), label="QKEYS export completed at chosen path")
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data.get("scope") == "qkd" and data.get("format") == "unsigned_console_evidence_export_v1"
            assert "qkd_example" in data
            details["qkeys"] = {"path": str(path), "completed": completed, "scope": data["scope"],
                                "format": data["format"], "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            capture("qkeys-chosen-evidence-export")

        run_case("qkeys_export_chosen_path", qkeys_export)
    except Exception as error:
        errors.append({"case": "initialization", "error": f"{type(error).__name__}: {error}"})
    finally:
        if provider is not None:
            startup.update({"steps_taken": provider.steps_taken,
                            "step_error": provider._step_error,
                            "publication_sequence": provider._delivered_publication_sequence,
                            "future_done": provider._future.done() if provider._future is not None else None,
                            "presentation_stats": dict(provider._presentation_stats)})
        details["startup"] = startup
        try:
            if window is not None:
                window.close()
                window.provider.stop()
            elif provider is not None:
                provider.stop()
        finally:
            collector.stop()
    expected = {"real_demo_topology", "google_protocol_timeline", "tf_qkd_observables_not_bell_wave",
                "cimatec_preserves_legacy_wave", "fpga_controls_and_real_processing_receipts", "qkeys_export_chosen_path"}
    report = {"schema": "orion.ui-pipeline-acceptance.v1", "passed": set(checks) == expected and all(checks.values()) and not errors,
              "frozen": bool(getattr(sys, "frozen", False)), "checks": checks, "details": details,
              "screenshots": screenshots, "errors": errors, "elapsed_s": time.monotonic() - began,
              "physical_action_performed": False, "hardware_validated": False, "isolated_diagnostic": True,
              "platform": QApplication.platformName(), "state_root": str(state_root)}
    if getattr(sys, "frozen", False):
        report["executable_sha256"] = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
    (output / "ui-pipeline-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
