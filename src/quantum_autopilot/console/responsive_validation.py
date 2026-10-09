"""Offscreen acceptance matrix; also runs inside the packaged executable."""
from pathlib import Path
from time import perf_counter


def run_event_loop(ms: int) -> None:
    """Run the Qt event loop for `ms` milliseconds.

    A real `QEventLoop.exec()`: it releases the interpreter lock while it waits, as
    `QApplication.exec()` does in the running application. `QTest.qWait` and a busy
    `processEvents()` loop keep the lock, which starves the engine-stepping thread and the
    service worker and makes a validation run many times slower than the product.
    """

    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(max(1, int(ms)), loop.quit)
    loop.exec()


def wait_for_node_presentation(opened, snapshot, net, *, timeout_ms=5000):
    """Verify actual incremental controls without forcing a synchronous refresh.

    Snapshot/model may be getters for a shell receiving asynchronous selections.
    They refer to its already received UI context and do not query the SDK.
    The content deadline includes verification of all rendered fields.
    """
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 5000:
        raise ValueError('presentation timeout must be an integer within 1..5000 ms')
    if opened is None:
        raise ValueError('node window was not opened')
    from .performance_validation import _node_presentation_evidence

    began = perf_counter()
    while True:
        expected_snapshot = snapshot() if callable(snapshot) else snapshot
        expected_model = net() if callable(net) else net
        receipt = _node_presentation_evidence(opened, expected_snapshot, expected_model)
        status = receipt.get('presentation', {})
        if status.get('error'):
            raise RuntimeError('node presentation reported an error')
        if receipt.get('ready'):
            presented = opened.presentation_snapshot()
            requested = (isinstance(presented, tuple) and len(presented) == 2
                         and presented[0] is expected_snapshot and presented[1] is expected_model)
            # A fixture/file snapshot can explicitly supply no LiveState. Its
            # origin is still mandatory; absence of scientific clocks is data,
            # not an invitation to invent a tick or accept a different source.
            if (getattr(expected_snapshot, 'live', False) is None
                    and receipt.get('mode') == 'asynchronous-contract'
                    and receipt.get('widget_content_sha256')
                    and receipt['checks'].get('presentation_timestamp') is False):
                source_kind = getattr(expected_snapshot, 'input_kind', None)
                receipt['source_kind'] = source_kind
                receipt['checks']['presentation_timestamp'] = (
                    type(source_kind) is str and bool(source_kind.strip())
                    and status.get('source_kind') == source_kind
                    and all(status.get(key) is None for key in (
                        'core_tick', 'latest_tick', 'presented_tick', 'network_time_s'))
                    and receipt.get('presented_tick') is None
                    and receipt.get('network_time_s') is None)
                receipt['complete'] = bool(receipt['checks'] and all(receipt['checks'].values()))
            receipt['checks']['requested_context_presented'] = requested
            receipt['complete'] = (receipt.get('complete') is True and requested
                                   and bool(receipt['checks']) and all(receipt['checks'].values()))
            receipt['failed_checks'] = [key for key, passed in receipt['checks'].items() if not passed]
        elapsed_ms = (perf_counter() - began) * 1000
        if elapsed_ms > timeout_ms:
            raise TimeoutError('node presentation did not complete within its content deadline')
        if receipt.get('complete') is True:
            receipt['wait_elapsed_ms'] = elapsed_ms
            receipt['content_deadline_ms'] = timeout_ms
            return receipt
        run_event_loop(5)


def validate_background_polling(window, report_dir, *, page="topology"):
    """Keep visible controls steady while the service publishes live reads."""
    from time import monotonic
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QApplication, QAbstractButton

    app = QApplication.instance()
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    provider = window.provider.active
    old_interval = provider._timer.interval()

    def settle(condition):
        deadline = monotonic() + 30
        while not condition() and monotonic() < deadline:
            app.processEvents()
            run_event_loop(5)
        app.processEvents()
        if not condition():
            raise TimeoutError("service polling did not finish")

    # Initial loading is intentionally blocking; only later reads are passive.
    window.provider.start()
    provider._timer.stop()
    settle(lambda: provider._future is None and not window.provider.busy)
    window.show_screen(page)
    app.processEvents()
    # The screen body and the shared chrome (rail, top bar, journal toggle, source label).
    buttons = [button for button in window.workspace.findChildren(QAbstractButton) if button.isVisible()]
    enabled = [button.isEnabled() for button in buttons]

    class ControlEvents(QObject):
        def __init__(self):
            super().__init__()
            self.enabled, self.style = 0, 0

        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.EnabledChange:
                self.enabled += 1
            elif event.type() == QEvent.Type.StyleChange:
                self.style += 1
            return False

    observer = ControlEvents()
    for button in buttons:
        button.installEventFilter(observer)
    before = [button.grab().toImage() for button in buttons]
    frames, busy = [], []
    on_frame, on_busy = frames.append, busy.append
    window.provider.snapshot_changed.connect(on_frame)
    window.provider.busy_changed.connect(on_busy)
    window.grab().save(str(report_dir / "before-polling.png"))
    try:
        provider._timer.setInterval(60)
        provider._timer.start()
        settle(lambda: len(frames) >= 4)
        provider._timer.stop()
        settle(lambda: provider._future is None)
        checks = {"live_frames_continue": len(frames) >= 4,
            "background_does_not_block": busy == [] and not window._busy and not window.provider.busy,
            "controls_keep_enabled_state": bool(buttons) and enabled == [button.isEnabled() for button in buttons] and observer.enabled == 0,
            "styles_stay_installed": observer.style == 0,
            "button_render_stays_identical": all(image == button.grab().toImage() for image, button in zip(before, buttons)),
            "physical_controls_stay_locked": not window.screens["fpga"].flash.isEnabled()}
        window.grab().save(str(report_dir / "after-polling.png"))
        return {"passed": all(checks.values()), "checks": checks, "frames": len(frames), "buttons": len(buttons),
            "enabled_changes": observer.enabled, "style_changes": observer.style, "qt_platform": app.platformName(), "page": page,
            "scope": "live service reads and own Qt render captures; no commands submitted"}
    finally:
        provider._timer.stop()
        provider._timer.setInterval(old_interval)
        window.provider.snapshot_changed.disconnect(on_frame)
        window.provider.busy_changed.disconnect(on_busy)
        for button in buttons:
            button.removeEventFilter(observer)


def _unchanged_render_fraction(before, after):
    """Sample the composited viewport; a child grab can hide proxy corruption."""
    if before.size() != after.size():
        return 0.0
    points = [(x, y) for x in range(0, before.width(), 12) for y in range(0, before.height(), 12)]
    return sum(before.pixelColor(x, y) == after.pixelColor(x, y) for x, y in points) / len(points)


def validate_link_editor_window(window, report_dir):
    """Click the real configuration button in the shown service-backed shell."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance()
    window.show_screen("topology")
    topology = window.screens["topology"]
    editor = topology.link_editor
    stage = window.centralWidget()
    editor.close()
    for _ in range(4):
        app.processEvents()
    before = stage.viewport().grab().toImage()
    scene_rect, transform = stage.sceneRect(), stage.transform()
    checks, stable, input_states = {}, [], []
    for attempt in range(2):
        QTest.mouseClick(topology.edit_links, Qt.MouseButton.LeftButton)
        for _ in range(4):
            app.processEvents()
        fraction = _unchanged_render_fraction(before, stage.viewport().grab().toImage())
        stable.append(fraction)
        checks[f"open_{attempt}"] = editor.isVisible() and editor.allowed()
        checks[f"native_dialog_{attempt}"] = editor.graphicsProxyWidget() is None and editor.windowHandle() is not None
        checks[f"main_canvas_{attempt}"] = (
            window.current_widget() is topology and stage.proxy.widget() is window.canvas
            and stage.sceneRect() == scene_rect and stage.transform() == transform and fraction >= .95)
        window.grab().save(str(report_dir / f"shell-open-{attempt}.png"))
        editor.grab().save(str(report_dir / f"link-editor-{attempt}.png"))
        # Editing inputs and opening a combo popup must not create a scene window.
        combo = editor.fields["left_node_id"]
        combo.showPopup()
        app.processEvents()
        checks[f"native_combo_{attempt}"] = combo.view().window().graphicsProxyWidget() is None
        combo.hidePopup()
        # Closing the native popup and returning keyboard focus are separate
        # Windows events. Click the field after processing that transition.
        QTest.qWait(30)
        field = editor.fields["link_id"]
        field.setFocus()
        QTest.qWait(30)
        QTest.mouseClick(field, Qt.MouseButton.LeftButton)
        QTest.qWait(30)
        QTest.keyClicks(field, "render-probe", delay=2)
        checks[f"editable_{attempt}"] = field.text() == "render-probe" and editor.pending is None
        input_states.append({"text": field.text(), "has_focus": field.hasFocus(), "enabled": field.isEnabled()})
        editor.grab().save(str(report_dir / f"link-editor-input-{attempt}.png"))
        editor.close()
        app.processEvents()
        checks[f"closed_{attempt}"] = not editor.isVisible() and _unchanged_render_fraction(
            before, stage.viewport().grab().toImage()) >= .95
    return {"passed": all(checks.values()), "checks": checks, "unchanged_render_fraction": stable,
            "input_states": input_states,
            "scope": "real button clicks, input editing and combo popups; no configuration submitted"}


def validate_dialog_views(window, report_dir):
    """Open secondary views without selecting packages or submitting actions."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QApplication, QAbstractButton, QDialog
    from .widgets.allocation_session import AllocationSession
    from .widgets.allocation_shadow import AllocationShadowSession
    from .widgets.compensation_session import CompensationSession
    from .widgets.compensation_shadow import CompensationShadowSession
    from .widgets.detector_session import DetectorSession
    from .widgets.keyrate_session import KeyRateSession
    from .widgets.keyrate_shadow import KeyRateShadowSession
    from .widgets.model_catalog import SESSION_MODELS
    from .widgets.routing_session import RoutingSession
    from .widgets.routing_shadow import RoutingShadowSession
    from .widgets.sync_session import SyncSession
    from .widgets.sync_shadow import SyncShadowSession

    app = QApplication.instance()
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    dialogs, created, seen = [], [], set()
    for screen in window.screens.values():
        for dialog in screen.findChildren(QDialog):
            if id(dialog) not in seen:
                seen.add(id(dialog))
                dialogs.append((type(dialog).__name__, dialog))
    for model_id in SESSION_MODELS:
        if model_id.startswith("qnet.allocator."):
            session, shadow = AllocationSession(model_id), AllocationShadowSession(model_id)
        elif model_id.startswith("qnet.routing."):
            session, shadow = RoutingSession(model_id), RoutingShadowSession(model_id)
        elif model_id.startswith("qnet.compensation."):
            session, shadow = CompensationSession(model_id), CompensationShadowSession(model_id)
        elif model_id == "qkd.detector.boosting":
            session, shadow = DetectorSession(), None
        elif model_id == "qkd.keyrate.actor":
            session, shadow = KeyRateSession(), KeyRateShadowSession(model_id)
        else:
            session, shadow = SyncSession(), SyncShadowSession()
        dialogs.append((model_id + "-offline", session))
        created.append(session)
        if shadow is not None:
            dialogs.append((model_id + "-shadow", shadow))
            created.append(shadow)
    checks = {}
    for name, dialog in dialogs:
        dialog.show()
        for width, height in ((960, 640), (640, 480)):
            dialog.resize(width, height)
            for _ in range(4):
                app.processEvents()
            checks[f"dialog_native_{name}_{width}"] = (
                dialog.graphicsProxyWidget() is None and dialog.windowHandle() is not None)
            scroll = dialog.content_scroll
            body = scroll.widget()
            checks[f"dialog_scroll_{name}_{width}"] = (
                body.width() <= scroll.viewport().width() + 2 or scroll.horizontalScrollBar().maximum() > 0
            ) and (body.height() <= scroll.viewport().height() + 2 or scroll.verticalScrollBar().maximum() > 0)
            buttons = [b for b in dialog.findChildren(QAbstractButton) if b.isVisibleTo(dialog)]
            checks[f"dialog_bounds_{name}_{width}"] = all(
                (body if body.isAncestorOf(b) else dialog).rect().contains(
                    b.mapTo(body if body.isAncestorOf(b) else dialog, QPoint(0, 0)))
                and (body if body.isAncestorOf(b) else dialog).rect().contains(
                    b.mapTo(body if body.isAncestorOf(b) else dialog, b.rect().bottomRight())) for b in buttons)
            checks[f"dialog_keyboard_{name}_{width}"] = all(
                b.focusPolicy() != Qt.FocusPolicy.NoFocus for b in buttons if b.isEnabled())
            if width == 960:
                dialog.grab().save(str(report_dir / f"{name}.png"))
        if hasattr(dialog, "package"):
            checks[f"dialog_models_off_{name}"] = dialog.package is None and not dialog.job.active
        dialog.hide()
    for dialog in created:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()
    return {"passed": all(checks.values()), "views": len(dialogs), "checks": checks,
            "scope": "secondary views without packages or submitted actions; confirmations covered by lifecycle tests"}


def validate_responsive_ui(paths, report_dir):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication
    from . import theme
    from .shell import ConsoleWindow
    from .synthetic import SyntheticProvider, artificial_snapshot

    app = QApplication.instance()
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    provider = SyntheticProvider(paths.repository_root, paths.state_root / "ui-matrix")
    window = ConsoleWindow(provider)
    from quantum_autopilot.desktop_instance import mark_isolated_diagnostic_window
    mark_isolated_diagnostic_window(window)
    window._on_snapshot(provider.snapshot())
    window.show()
    checks, timings = {}, []
    for kind, expected in (("mono", "JetBrains Mono"), ("text", "Inter")):
        checks[f"font_{kind}"] = theme.family(kind) == expected
    checks["font_two_families_only"] = {theme.family(kind) for kind in ("industrial", "display", "jp", "text", "mono")} == {
        "Inter", "JetBrains Mono"}
    checks["tokens_dark_default"] = theme.current_theme() != "dark" or (
        str(theme.C.bg), str(theme.C.acc)) == ("#141413", "#d97757")
    for width, height in ((960, 540), (1280, 720), (1280, 800), (1366, 768), (1920, 1080),
                          (2560, 1440), (3440, 1440), (3840, 2160), (5120, 2160), (1024, 768)):
        window.resize(width, height)
        for page in ("boot", *window.screens):
            start = perf_counter()
            window.show_screen(page)
            for _ in range(4):
                app.processEvents()
            stage = window.centralWidget()
            scale = stage.transform().m11()
            content = stage.sceneRect()
            viewport = stage.viewport().size()
            checks[f"fill_{width}x{height}_{page}"] = (
                content.width() * scale >= viewport.width() - 2
                and content.height() * scale >= viewport.height() - 2
                and scale >= 1
                and (content.width() * scale <= viewport.width() + 2
                     or stage.horizontalScrollBar().maximum() > 0)
                and (content.height() * scale <= viewport.height() + 2
                     or stage.verticalScrollBar().maximum() > 0))
            checks[f"no_outer_scroll_{width}x{height}_{page}"] = (
                stage.horizontalScrollBar().maximum() == 0 and stage.verticalScrollBar().maximum() == 0)
            if page != "boot":
                checks[f"chrome_{width}x{height}_{page}"] = (
                    window.topbar.isVisible() and window.status.isVisible()
                    and window.canvas.rect().contains(window.topbar.geometry())
                    and window.canvas.rect().contains(window.status.geometry()))
            timings.append((perf_counter() - start) * 1000)
            if width in (960, 1280, 1920) or (width in (3440, 3840, 5120) and page == "topology"):
                window.grab().save(str(report_dir / f"orion-{width}x{height}-{page}.png"))

        window.open_palette()
        for _ in range(4):
            app.processEvents()
        checks[f"palette_fits_{width}x{height}"] = window.palette.rect().contains(window.palette.panel.geometry())
        window.close_palette()

    window.resize(1280, 800)
    snap = artificial_snapshot("load")
    window._on_snapshot(snap)
    window.show_screen("topology")
    for _ in range(4):
        app.processEvents()
    table = window.screens["topology"].link_table
    topology = window.screens["topology"]
    checks["rail_seven_routes"] = list(window.rail.buttons) == [
        "topology", "telemetry", "keys", "node", "communicators", "fpga", "audit"]
    checks["rail_shared_not_per_screen"] = not any(hasattr(view, "rail") for view in window.screens.values())
    checks["chrome_metrics"] = (window.topbar.height(), window.rail.width(), window.status.height()) == (
        theme.TOPBAR_H, theme.RAIL_W, theme.STATUSBAR_H)
    checks["palette_click_only_initial"] = not window.palette.is_open()
    window.topbar.palette_button.click()
    app.processEvents()
    checks["palette_click_opens"] = window.palette.is_open() and bool(window.palette.items())
    window.grab().save(str(report_dir / "orion-palette-click.png"))
    window.close_palette()
    checks["palette_closes"] = not window.palette.is_open()
    topology.set_view("GEO")
    checks["synthetic_geo_available"] = topology.geo.available
    window.grab().save(str(report_dir / "orion-synthetic-geo.png"))
    topology.set_view("MATRIX")
    checks["matrix_keeps_direct_values"] = topology.matrix.table.model().rowCount() == len(snap.topology.nodes)
    window.grab().save(str(report_dir / "orion-synthetic-matrix.png"))
    topology.set_view("GRAPH")
    first_node = snap.topology.nodes[0].id
    checks["node_window_initially_closed"] = not topology.node_windows.windows
    topology.graph.node_selected.emit(first_node)
    app.processEvents()
    node_window = topology.node_windows.windows.get(first_node)
    checks["node_window_click_opens"] = node_window is not None and node_window.isVisible()
    # a fixture carries no engine state: the floating window must not draw a wave for it
    checks["node_window_invents_no_wave"] = node_window is not None and all(
        not node_window.chart.visible_samples(key) for key in node_window.chart.keys())
    window.grab().save(str(report_dir / "orion-node-click.png"))
    topology.node_windows.close_all()
    checks["node_window_closes"] = not topology.node_windows.windows
    checks["injected_links_256"] = table.model().rowCount() == 256
    table.scrollToBottom()
    app.processEvents()
    rect = table.visualRect(table.model().index(255, 0))
    checks["last_link_visible"] = rect.top() >= 0 and rect.bottom() < table.viewport().height()
    window.grab().save(str(report_dir / "orion-load-links-bottom.png"))
    window._on_snapshot(artificial_snapshot("load", dropout=True))
    app.processEvents()
    checks["dropout_clears_links"] = table.model().rowCount() == 0
    window._on_snapshot(snap)
    app.processEvents()
    checks["recovery_restores_links"] = table.model().rowCount() == 256
    for page, attr in (("audit", "entry_scroll"), ("keys", "session_scroll"), ("node", "log_scroll")):
        window.show_screen(page)
        for _ in range(4):
            app.processEvents()
        scroll = getattr(window.screens[page], attr)
        checks[f"overflow_scroll_{page}"] = scroll.verticalScrollBar().maximum() > 0
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        window.grab().save(str(report_dir / f"orion-load-{page}-bottom.png"))
        # The scroll area must itself remain inside the screen, not merely exist. A screen whose
        # body is a scrolling page (`screen.page`) shows the table once the page is scrolled to it.
        screen = window.screens[page]
        body = getattr(screen, "page", None)
        if body is not None and body.isAncestorOf(scroll):
            body.ensureWidgetVisible(scroll, 0, 0)
            app.processEvents()
            area = body.viewport()
            top = scroll.mapTo(area, QPoint(0, 0))
            bottom = scroll.mapTo(area, QPoint(scroll.width(), scroll.height()))
            checks[f"scroll_bounds_{page}"] = (top.y() >= 0 and bottom.x() <= area.width()
                                               and bottom.y() <= area.height())
        else:
            bottom = scroll.mapTo(screen, QPoint(scroll.width(), scroll.height()))
            checks[f"scroll_bounds_{page}"] = bottom.x() <= screen.width() and bottom.y() <= screen.height()
    # A declared-measurement fixture tests the read-only plug, never physical evidence.
    import json
    from datetime import datetime, timezone
    from uuid import uuid4
    from .input_sources import MeasuredFileProvider
    stamp = datetime.now(timezone.utc).isoformat()
    fixture = {"schema": "orion.telemetry.v1", "source_id": "SOFTWARE TEST FIXTURE - NOT REAL HARDWARE",
        "campaign_id": "self-test-fixture-only", "timestamp_utc": stamp, "evidence_kind": "measured",
        "nodes": [{"id": "fixture-a", "x": .2, "y": .5}, {"id": "fixture-b", "x": .8, "y": .5}],
        "links": [{"id": "fixture-link", "a": "fixture-a", "b": "fixture-b", "channel": "fixture-channel",
            "device_id": "fixture-device", "measurement_id": "fixture-reading", "timestamp_utc": stamp,
            "evidence_kind": "measured", "ber": 1e-6, "osnr_db": 24, "power_dbm": -8.5}]}
    paths.state_root.mkdir(parents=True, exist_ok=True)
    fixture_path = paths.state_root / f"software-fixture-{uuid4().hex}.json"
    with fixture_path.open("x", encoding="utf-8") as handle:
        json.dump(fixture, handle)
    window.provider.start()
    window.show_screen("topology")
    screen_ids = [id(view) for view in window.screens.values()]
    checks["input_switch_accepted"] = window.switch_input(lambda: MeasuredFileProvider(fixture_path, paths.state_root / "measured-fixture"))
    app.processEvents()
    checks["input_same_screens"] = screen_ids == [id(view) for view in window.screens.values()]
    checks["input_old_timer_stopped"] = not provider.timer.isActive()
    checks["input_no_invented_fidelity"] = window._snap.topology.links[0].fidelity == "—"
    measured_topology = window.screens["topology"]
    measured_topology.set_view("GEO")
    checks["measured_geo_requires_coordinates"] = not measured_topology.geo.available
    measured_topology.set_view("MATRIX")
    matrix_model = measured_topology.matrix.table.model()
    # the measured fibre has no fidelity: its cell says "—" (a fibre without value), never a number
    checks["matrix_no_invented_fidelity"] = matrix_model.data(matrix_model.index(0, 2)) in ("·", "—")
    checks["measured_provisioning_locked"] = not window.screens["node"].provision.isEnabled()
    checks["edge_flash_locked"] = not window.screens["fpga"].flash.isEnabled()
    try:
        window.provider.dispatch("isolate", "fixture-a")
    except ValueError:
        checks["input_read_only"] = True
    else:
        checks["input_read_only"] = False
    window.grab().save(str(report_dir / "orion-measured-plug-SOFTWARE-FIXTURE.png"))
    window.resize(1920, 1080)
    window.show_screen("telemetry")
    for _ in range(4):
        app.processEvents()
    checks["measured_numeric_observations"] = len(window._snap.observations) == 3 and all(
        o.provenance == "measured" for o in window._snap.observations)
    checks["measured_no_inferred_fidelity_observation"] = all(o.quantity != "fidelity" for o in window._snap.observations)
    checks["measured_first_receipt_visible"] = any(v.key == "FIRST HOST READ" for v in window._snap.telemetry.conditions)
    window.grab().save(str(report_dir / "orion-measured-telemetry-SOFTWARE-FIXTURE.png"))
    checks["input_return_to_synthetic_fixture"] = window.switch_input(
        lambda: SyntheticProvider(paths.repository_root, paths.state_root / "synthetic-restored"))
    checks["input_synthetic_fixture_active"] = (window._snap.input_kind == "synthetic"
                                                and window.active_source_key() == "synthetic")
    dialogs = validate_dialog_views(window, report_dir / "dialogs")
    checks["secondary_dialogs"] = dialogs["passed"]
    window.close()
    app.processEvents()
    return {"passed": all(checks.values()), "checks": checks,
            "render_ms": {"max": max(timings), "mean": sum(timings)/len(timings)}, "dialogs": dialogs,
            "scope": "offscreen software rendering; not hardware timing or human acceptance"}
