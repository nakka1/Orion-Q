"""Artificial stream, explicit opt-in, bounded work and stale-context rejection."""
from concurrent.futures import Future
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import time
import zipfile

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.synthetic import SyntheticProvider, artificial_snapshot  # noqa: E402
from quantum_autopilot.console.synthetic_sync import SyntheticSyncStream  # noqa: E402
from quantum_autopilot.console.widgets import sync_shadow as ui  # noqa: E402
from quantum_autopilot.console.widgets.model_catalog import ModelCatalog  # noqa: E402
from quantum_autopilot.model_intake import artifacts  # noqa: E402
from quantum_autopilot.model_intake.verification import verify_model_archive  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.install(instance)
    return instance


@pytest.fixture
def package(tmp_path, monkeypatch):
    path = tmp_path / "qkd.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(artifacts.QKD_ROOT + "modelos/sincronizador_ridge.json", json.dumps({
            "window": 8, "scale": 1e-9, "update_period_s": 1., "horizon_s": 2.,
            "coefficients": [0.] * 9, "intercept": .5}))
    monkeypatch.setitem(artifacts.PINS, "qkd", hashlib.sha256(path.read_bytes()).hexdigest())
    return verify_model_archive(ui.MODEL_ID, path)


def frames(seed=1337):
    stream = SyntheticSyncStream()
    for tick in range(30):
        snapshot = artificial_snapshot(seed=seed, tick=tick)
        snapshot.optional_sync_state = stream.push(snapshot, seed, tick)
        yield snapshot


def ready_frame():
    return list(frames())[7]


@pytest.fixture
def session(app, package):
    dialog = ui.SyncShadowSession()
    dialog.set_package(package)
    dialog.show()
    yield dialog
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def settle(app, job):
    deadline = time.monotonic() + 10
    while job.active and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert not job.active


def test_stream_observes_eight_frames_without_backfill():
    first = list(frames())
    assert all(row.optional_sync_state is None for row in first[:7])
    state = first[7].optional_sync_state
    assert state is not None
    assert len(state.telemetry["optional_sync_window"]["samples"]) == 8
    assert state.to_json() == list(frames())[7].optional_sync_state.to_json()
    assert state.to_json() != list(frames(seed=3))[7].optional_sync_state.to_json()


@pytest.mark.parametrize("fault", ["dropout", "gap", "rollback", "campaign", "measured"])
def test_stream_resets_on_discontinuity(fault):
    stream = SyntheticSyncStream()
    for tick in range(8):
        assert (stream.push(artificial_snapshot(tick=tick), 1337, tick) is None) == (tick < 7)
    tick = 9 if fault == "gap" else 2 if fault == "rollback" else 8
    snapshot = artificial_snapshot(tick=tick, dropout=fault == "dropout")
    if fault == "campaign":
        snapshot.campaign = "different"
    if fault == "measured":
        snapshot.input_kind = "measured"
    assert stream.push(snapshot, 1337, tick) is None
    assert len(stream.samples) <= 1


def test_provider_uses_common_snapshot_plug_and_does_not_load_models(app, tmp_path, monkeypatch):
    from quantum_autopilot.model_intake import catalog
    monkeypatch.setattr(catalog, "open_optional_model", lambda *a, **k: pytest.fail("automatic model load"))
    provider = SyntheticProvider(tmp_path, tmp_path / "state")
    seen = []
    provider.snapshot_changed.connect(seen.append)
    for _ in range(7):
        provider.advance()
    assert seen[-1].optional_sync_state is not None
    assert not (tmp_path / "state").exists()
    provider.stop()
    assert provider.snapshot().optional_sync_state is None
    provider.advance()
    assert provider.snapshot().optional_sync_state is None


def test_requires_explicit_start_and_streams_predictions(app, session):
    iterator = frames()
    for _ in range(8):
        session.accept_snapshot(next(iterator))
    assert session.start_button.isEnabled() and not session.running
    assert not session.job.active and session.job.result is None
    session.start()
    settle(app, session.job)
    first = session.job.result
    assert first.status == "evaluated" and not first.physical_action_performed
    assert first.evaluation.reference_offset_s is None
    assert "Persistence:" in session.output.toPlainText()
    session.accept_snapshot(next(iterator))
    settle(app, session.job)
    assert session.job.result.snapshot_sha256 != first.snapshot_sha256
    assert session.running
    session.stop()
    assert session.job.result is None and not session.running


@pytest.mark.parametrize("change", ["stop", "close", "source", "campaign", "dropout", "expire", "package"])
def test_late_worker_results_are_discarded(app, session, change):
    frame = ready_frame()
    session.accept_snapshot(frame)
    pending = Future()
    pending.set_running_or_notify_cancel()
    session.job._future = pending
    session.job._pending_generation = session.job.generation
    session.running = True
    if change == "stop":
        session.stop()
    elif change == "close":
        session.close()
    elif change == "expire":
        session._expired()
    elif change == "package":
        session.invalidate_package()
    else:
        frame = replace(frame, input_kind="measured") if change == "source" else (
            replace(frame, campaign="another") if change == "campaign" else
            replace(frame, optional_sync_state=None))
        session.accept_snapshot(frame)
    pending.set_result(object())
    session.job.poll()
    assert session.job.result is None and not session.running
    assert not session.export_button.isEnabled()


def test_duplicate_frame_cannot_renew_expired_input(session):
    frame = ready_frame()
    session.accept_snapshot(frame)
    session._expired()
    session.accept_snapshot(frame)
    assert session.latest is None and not session.start_button.isEnabled()


def test_real_watchdog_timeout_requires_explicit_restart(app, session):
    session.watchdog.setInterval(25)
    session.accept_snapshot(ready_frame())
    QTest.qWait(60)
    app.processEvents()
    assert session.latest is None and not session.start_button.isEnabled()


def test_edge_screen_receives_provider_frames_and_clears_on_new_campaign(app, package, tmp_path):
    from quantum_autopilot.console.screens.fpga import EdgeControlScreen
    provider = SyntheticProvider(tmp_path, tmp_path / "state")
    screen = EdgeControlScreen()
    commands = []
    screen.command.connect(lambda *args: commands.append(args))
    provider.snapshot_changed.connect(screen.apply)
    screen.show()
    try:
        for _ in range(7):
            provider.advance()
        catalog = screen.model_catalog
        catalog.show()
        catalog.table.selectRow(2)
        catalog.checker.result = package
        catalog.checker.changed.emit()
        catalog._open_shadow()
        dialog = catalog.shadow_session
        assert dialog.start_button.isEnabled()
        dialog.start()
        settle(app, dialog.job)
        assert dialog.job.result.status == "evaluated"
        provider.advance()
        settle(app, dialog.job)
        assert json.loads(dialog.job.result.snapshot_json)["state_version"] == 8
        screen.apply(artificial_snapshot(seed=42))
        assert dialog.package is None and not dialog.isVisible() and not dialog.running
        assert commands == []
    finally:
        provider.stop()
        screen.model_catalog.close()
        screen.close()
        screen.deleteLater()


def test_busy_worker_drops_intermediate_frames_without_queue(app, session):
    rows = list(frames())
    session.accept_snapshot(rows[7])
    pending = Future()
    pending.set_running_or_notify_cancel()
    session.job._future = pending
    session.job._pending_generation = session.job.generation
    session.running = True
    for row in rows[8:12]:
        session.accept_snapshot(row)
        assert session.job._future is pending
    pending.set_result(object())
    session.job.poll()
    assert session.job.result is None
    session.accept_snapshot(rows[12])
    settle(app, session.job)
    assert session.job.result.status == "evaluated"


def test_archive_change_stops_stream(app, session, package):
    session.accept_snapshot(ready_frame())
    Path(package.path).write_bytes(b"changed")
    session.start()
    settle(app, session.job)
    assert not session.running and session.job.result.status == "refused"


def test_catalog_verification_and_source_change(app, package):
    catalog = ModelCatalog()
    catalog.show()
    try:
        catalog.table.selectRow(2)
        assert not catalog.shadow_button.isEnabled()
        catalog.checker.result = package
        catalog.checker.changed.emit()
        catalog.accept_shadow_snapshot(ready_frame())
        catalog._open_shadow()
        dialog = catalog.shadow_session
        assert dialog.isVisible() and dialog.start_button.isEnabled() and not dialog.running
        catalog.checker.clear()
        assert not dialog.isVisible() and dialog.package is None
    finally:
        catalog.close()
        catalog.deleteLater()


def test_export_and_small_window(app, session, tmp_path, monkeypatch):
    session.accept_snapshot(ready_frame())
    session.start()
    settle(app, session.job)
    target = tmp_path / "export.json"
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", lambda *a: (str(target), ""))
    session._export()
    original = target.read_bytes()
    session._export()
    assert target.read_bytes() == original and "EXPORT REFUSED" in session.output.toPlainText()
    session.resize(640, 480)
    app.processEvents()
    for button in (session.start_button, session.stop_button, session.export_button, session.close_button):
        assert button.isVisible() and button.width() > 0
    assert session.grab().save(str(tmp_path / "shadow-640.png"))


def test_stale_export_chooser_cannot_export_old_frame(app, session, tmp_path, monkeypatch):
    session.accept_snapshot(ready_frame())
    session.start()
    settle(app, session.job)
    target = tmp_path / "stale.json"
    def choose(*args):
        session._expired()
        return str(target), ""
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", choose)
    session._export()
    assert not target.exists()


def test_export_captures_selected_frame_while_stream_advances(app, session, tmp_path, monkeypatch):
    rows = list(frames())
    session.accept_snapshot(rows[7])
    session.start()
    settle(app, session.job)
    selected = session.job.result
    target = tmp_path / "selected.json"
    def choose(*args):
        session.accept_snapshot(rows[8])
        return str(target), ""
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", choose)
    session._export()
    assert json.loads(target.read_text())["snapshot_sha256"] == selected.snapshot_sha256
    settle(app, session.job)
    assert session.job.result.snapshot_sha256 != selected.snapshot_sha256


def test_temporal_scores_only_after_future_frame_arrives(app, session):
    rows = list(frames())
    session.accept_snapshot(rows[7])
    session.start()
    settle(app, session.job)
    first = session.job.result.serialized()
    assert session.temporal.report()["pending"] == 1
    assert session.temporal.report()["paired"] == 0
    session.accept_snapshot(rows[8])
    settle(app, session.job)
    assert session.temporal.report()["paired"] == 0
    session.accept_snapshot(rows[9])
    settle(app, session.job)
    report = session.temporal.report()
    assert report["paired"] == 1 and report["pending"] == 2
    assert report["records"][0]["forecast"] == json.loads(first)
    assert "MAE: model" in session.metrics.text()
    assert session.temporal_export_button.isEnabled()
    session.stop()
    assert session.temporal.report()["registered_predictions"] == 0
    assert not session.temporal_export_button.isEnabled()


def test_temporal_export_freezes_report_while_frames_arrive(app, session, tmp_path, monkeypatch):
    rows = list(frames())
    session.accept_snapshot(rows[7])
    session.start()
    settle(app, session.job)
    captured = session.temporal.serialized()
    target = tmp_path / "temporal.json"
    def choose(*args):
        session.accept_snapshot(rows[9])
        return str(target), ""
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", choose)
    session._export_temporal()
    assert target.read_text(encoding="utf-8") == captured
    settle(app, session.job)
    assert session.temporal.report()["paired"] == 1
    original = target.read_bytes()
    session._export_temporal()
    assert target.read_bytes() == original
    assert "EXPORT REFUSED" in session.output.toPlainText()


@pytest.mark.parametrize("change", ["stop", "source", "expired"])
def test_temporal_export_invalidated_by_context_loss(app, session, tmp_path, monkeypatch, change):
    frame = ready_frame()
    session.accept_snapshot(frame)
    session.start()
    settle(app, session.job)
    target = tmp_path / "invalid.json"
    def choose(*args):
        if change == "stop":
            session.stop()
        elif change == "expired":
            session._expired()
        else:
            session.accept_snapshot(replace(frame, input_kind="measured"))
        return str(target), ""
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", choose)
    session._export_temporal()
    assert not target.exists()
