"""Artificial SO(3) batches, opt-in UI and cancellation without physical actions."""
from concurrent.futures import Future
from dataclasses import replace
from datetime import datetime
import json
import os
import time

import numpy as np
import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.synthetic import SyntheticProvider, artificial_snapshot  # noqa: E402
from quantum_autopilot.console.synthetic_compensation import SyntheticCompensationStream  # noqa: E402
from quantum_autopilot.console.widgets import compensation_shadow as ui  # noqa: E402
from quantum_autopilot.console.widgets.model_catalog import ModelCatalog  # noqa: E402
from quantum_autopilot.control_plane.optional_compensation import TELEMETRY_KEY  # noqa: E402
from quantum_autopilot.model_intake.verification import verify_model_archive  # noqa: E402
from tests import test_optional_compensation_session as fixtures  # noqa: E402

compensation_archive = fixtures.compensation_archive
pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.install(instance)
    return instance


def frame(tick=0, seed=1337):
    snapshot = artificial_snapshot(tick=tick, seed=seed)
    snapshot.optional_compensation_states = SyntheticCompensationStream().push(snapshot, seed, tick)
    return snapshot


@pytest.fixture
def session(app, compensation_archive):
    dialog = ui.CompensationShadowSession(ui.MODEL_IDS[0])
    dialog.set_package(verify_model_archive(dialog.model_id, compensation_archive))
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


def test_batches_have_proper_rotations_independent_clocks_and_no_future():
    first, second = frame(), frame(1)
    assert len(first.optional_compensation_states) == 2
    clocks = set()
    for state, later in zip(first.optional_compensation_states, second.optional_compensation_states):
        window = state.telemetry[TELEMETRY_KEY]
        clocks.add(window["clock_id"])
        samples = window["samples"]
        assert len(samples) == 16 and "reference" not in window
        times = [datetime.fromisoformat(sample["timestamp_utc"]) for sample in samples]
        assert all((b-a).total_seconds() == .002 for a, b in zip(times, times[1:]))
        assert (datetime.fromisoformat(later.timestamp_utc)-datetime.fromisoformat(state.timestamp_utc)).total_seconds() == .032
        for sample in samples:
            rotation = np.asarray(sample["rotation"])
            assert np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-12)
            assert np.linalg.det(rotation) == pytest.approx(1.)
        assert state.provenance.evidence_kind.value == "simulated"
    assert len(clocks) == 2
    assert first.optional_compensation_states == frame().optional_compensation_states
    assert first.optional_compensation_states != frame(seed=3).optional_compensation_states


@pytest.mark.parametrize("change", ["dropout", "gap", "rollback", "campaign", "measured"])
def test_stream_discontinuities_never_backfill(change):
    stream = SyntheticCompensationStream()
    original = stream.push(artificial_snapshot(tick=3), 1337, 3)
    assert stream.push(artificial_snapshot(tick=3), 1337, 3) is original
    tick = 9 if change == "gap" else 1 if change == "rollback" else 4
    snapshot = artificial_snapshot(tick=tick, dropout=change == "dropout")
    if change == "campaign":
        snapshot.campaign = "changed"
    if change == "measured":
        snapshot.input_kind = "measured"
    result = stream.push(snapshot, 1337, tick)
    if change in ("dropout", "measured"):
        assert result == () and stream.last_tick is None
    else:
        assert len(result[0].telemetry[TELEMETRY_KEY]["samples"]) == 16
        assert result[0].request_id.endswith(f"/batch/{tick}")
        assert result[0].metadata["campaign_id"] == snapshot.campaign


def test_common_provider_plug_never_auto_loads_models(app, tmp_path, monkeypatch):
    from quantum_autopilot.model_intake import catalog
    monkeypatch.setattr(catalog, "open_optional_model", lambda *a, **k: pytest.fail("automatic load"))
    provider = SyntheticProvider(tmp_path, tmp_path / "state")
    seen = []
    provider.snapshot_changed.connect(seen.append)
    provider.advance()
    assert len(seen[-1].optional_compensation_states) == 2
    assert seen[-1].optional_sync_state is None
    provider.stop()
    assert provider.snapshot().optional_compensation_states == ()
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("model_id", ui.MODEL_IDS)
def test_all_four_checkpoints_are_opt_in(app, compensation_archive, model_id):
    dialog = ui.CompensationShadowSession(model_id)
    try:
        dialog.set_package(verify_model_archive(model_id, compensation_archive))
        dialog.show()
        dialog.accept_snapshot(frame())
        assert dialog.start_button.isEnabled() and not dialog.running and dialog.job.result is None
        dialog.start()
        settle(app, dialog.job)
        result = dialog.job.result
        assert result.status == "evaluated" and result.final_output_status == "model"
        assert not result.physical_action_performed and not result.policy_activated
        evaluation = json.loads(result.evaluation_json)
        assert evaluation["model_angular_error_rad"] is None
        assert [row["status"] for row in evaluation["steps"]] == ["warmup_persistence"] * 15 + ["model"]
        assert "Predicted SO(3):" in dialog.output.toPlainText()
        dialog.accept_snapshot(frame(1))
        settle(app, dialog.job)
        assert dialog.job.result.snapshot_sha256 != result.snapshot_sha256
    finally:
        dialog.close()
        dialog.deleteLater()


@pytest.mark.parametrize("change", ["stop", "close", "source", "campaign", "dropout", "expire", "package", "rotation_frame"])
def test_late_workers_discarded(app, session, change):
    snapshot = frame()
    session.accept_snapshot(snapshot)
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
        if change == "source":
            snapshot = replace(snapshot, input_kind="measured")
        elif change == "campaign":
            snapshot = replace(snapshot, campaign="different")
        elif change == "rotation_frame":
            snapshot.optional_compensation_states[0].metadata["rotation_frame_id"] = "changed"
        else:
            snapshot = replace(snapshot, optional_compensation_states=())
        session.accept_snapshot(snapshot)
    pending.set_result(object())
    session.job.poll()
    assert session.job.result is None and not session.running
    assert not session.export_button.isEnabled()


def test_duplicate_cannot_renew_expired_input(session):
    snapshot = frame()
    session.accept_snapshot(snapshot)
    session._expired()
    session.accept_snapshot(snapshot)
    assert session.latest is None and not session.start_button.isEnabled()


def test_watchdog_and_duplicate_regime_refusal(app, session):
    session.watchdog.setInterval(25)
    session.accept_snapshot(frame())
    QTest.qWait(60)
    assert session.latest is None
    snapshot = frame(1)
    snapshot.optional_compensation_states *= 2
    session.accept_snapshot(snapshot)
    assert session.latest is None and not session.start_button.isEnabled()


def test_slow_worker_has_no_queue(app, session):
    session.accept_snapshot(frame())
    pending = Future()
    pending.set_running_or_notify_cancel()
    session.job._future = pending
    session.job._pending_generation = session.job.generation
    session.running = True
    for tick in range(1, 5):
        session.accept_snapshot(frame(tick))
        assert session.job._future is pending
    pending.set_result(object())
    session.job.poll()
    assert session.job.result is None
    session.accept_snapshot(frame(5))
    settle(app, session.job)
    assert session.job.result.status == "evaluated"


def test_catalog_selects_each_model_and_clears_context(app, compensation_archive):
    catalog = ModelCatalog()
    catalog.show()
    try:
        for model_id in ui.MODEL_IDS:
            row = next(i for i, item in enumerate(catalog.options) if item.model_id == model_id)
            catalog.table.selectRow(row)
            assert not catalog.shadow_button.isEnabled()
            catalog.checker.result = verify_model_archive(model_id, compensation_archive)
            catalog.checker.changed.emit()
            catalog.accept_shadow_snapshot(frame())
            catalog._open_shadow()
            dialog = catalog.compensation_shadow
            assert dialog.model_id == model_id and dialog.isVisible()
            assert dialog.start_button.isEnabled() and not dialog.running
            assert catalog.shadow_session is None
        catalog.checker.clear()
        assert not dialog.isVisible() and dialog.package is None
    finally:
        catalog.close()
        catalog.deleteLater()


def test_catalog_freshness_is_independent_per_regime(app, compensation_archive, monkeypatch):
    import quantum_autopilot.console.widgets.model_catalog as catalog_module
    now = [10.]
    monkeypatch.setattr(catalog_module.time, "monotonic", lambda: now[0])
    catalog = ModelCatalog()
    catalog.show()
    try:
        model_id = ui.MODEL_IDS[0]
        catalog.table.selectRow(next(i for i, item in enumerate(catalog.options) if item.model_id == model_id))
        catalog.checker.result = verify_model_archive(model_id, compensation_archive)
        catalog.checker.changed.emit()
        initial, later = frame(), frame(1)
        catalog.accept_shadow_snapshot(initial)
        now[0] = 15.
        later.optional_compensation_states = (initial.optional_compensation_states[0], later.optional_compensation_states[1])
        catalog.accept_shadow_snapshot(later)
        catalog._open_shadow()
        assert catalog.compensation_shadow.latest is None
        assert not catalog.compensation_shadow.start_button.isEnabled()
    finally:
        catalog.close()
        catalog.deleteLater()


def test_edge_screen_receives_provider_batches_without_commands(app, compensation_archive, tmp_path):
    from quantum_autopilot.console.screens.fpga import EdgeControlScreen
    screen = EdgeControlScreen()
    provider = SyntheticProvider(tmp_path, tmp_path / "state")
    commands = []
    screen.command.connect(lambda *args: commands.append(args))
    provider.snapshot_changed.connect(screen.apply)
    try:
        screen.apply(provider.snapshot())
        catalog = screen.model_catalog
        catalog.show()
        model_id = ui.MODEL_IDS[0]
        catalog.table.selectRow(next(i for i, item in enumerate(catalog.options) if item.model_id == model_id))
        catalog.checker.result = verify_model_archive(model_id, compensation_archive)
        catalog.checker.changed.emit()
        catalog._open_shadow()
        dialog = catalog.compensation_shadow
        dialog.start()
        settle(app, dialog.job)
        first = dialog.job.result
        provider.advance()
        settle(app, dialog.job)
        assert dialog.job.result.snapshot_sha256 != first.snapshot_sha256
        assert commands == []
        screen.apply(replace(provider.snapshot(), input_kind="measured"))
        assert dialog.package is None and not dialog.isVisible()
    finally:
        provider.stop()
        screen.model_catalog.close()
        screen.close()
        screen.deleteLater()


def test_export_selected_batch_and_small_window(app, session, tmp_path, monkeypatch):
    session.accept_snapshot(frame())
    session.start()
    settle(app, session.job)
    selected = session.job.result
    target = tmp_path / "selected.json"
    def choose(*args):
        session.accept_snapshot(frame(1))
        return str(target), ""
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", choose)
    session._export()
    assert json.loads(target.read_text())["snapshot_sha256"] == selected.snapshot_sha256
    settle(app, session.job)
    original = target.read_bytes()
    session._export()
    assert target.read_bytes() == original and "EXPORT REFUSED" in session.output.toPlainText()
    session.resize(640, 480)
    app.processEvents()
    assert session.size().width() == 640 and session.size().height() == 480
    for button in (session.start_button, session.stop_button, session.export_button, session.close_button):
        assert button.isVisible() and button.width() > 0
    assert session.grab().save(str(tmp_path / "compensation-640.png"))


@pytest.mark.parametrize("change", ["stop", "source", "expire"])
def test_export_invalidated_on_context_loss(app, session, tmp_path, monkeypatch, change):
    session.accept_snapshot(frame())
    session.start()
    settle(app, session.job)
    target = tmp_path / "invalid.json"
    def choose(*args):
        if change == "stop":
            session.stop()
        elif change == "expire":
            session._expired()
        else:
            session.accept_snapshot(replace(frame(), input_kind="measured"))
        return str(target), ""
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", choose)
    session._export()
    assert not target.exists()


def test_temporal_metrics_only_score_later_exact_sample(app, session, tmp_path):
    session.accept_snapshot(frame())
    session.start()
    settle(app, session.job)
    committed = session.job.result.serialized()
    assert session.temporal.report()["pending"] == 1
    for tick in (1, 2):
        session.accept_snapshot(frame(tick))
        settle(app, session.job)
    report = session.temporal.report()
    assert report["paired"] == 1 and report["pending"] == 2
    assert report["records"][0]["forecast"] == json.loads(committed)
    assert "Mean angle: model" in session.metrics.text()
    assert session.temporal_export_button.isEnabled()
    session.resize(640, 480)
    app.processEvents()
    assert session.size().width() == 640 and session.size().height() == 480
    assert session.output.height() > 60
    assert session.temporal_export_button.isVisible()
    assert session.grab().save(str(tmp_path / "temporal-640.png"))
    session.stop()
    assert session.temporal.report()["registered_predictions"] == 0
    assert not session.temporal_export_button.isEnabled()


def test_temporal_export_freezes_report_while_new_batches_arrive(app, session, tmp_path, monkeypatch):
    session.accept_snapshot(frame())
    session.start()
    settle(app, session.job)
    captured = session.temporal.serialized()
    target = tmp_path / "temporal.json"
    def choose(*args):
        session.accept_snapshot(frame(2))
        return str(target), ""
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", choose)
    session._export_temporal()
    assert target.read_text(encoding="utf-8") == captured
    settle(app, session.job)
    assert session.temporal.report()["paired"] == 1
    original = target.read_bytes()
    session._export_temporal()
    assert target.read_bytes() == original and "EXPORT REFUSED" in session.output.toPlainText()


@pytest.mark.parametrize("change", ["stop", "source", "expire", "close"])
def test_temporal_export_context_invalidates(app, session, tmp_path, monkeypatch, change):
    session.accept_snapshot(frame())
    session.start()
    settle(app, session.job)
    target = tmp_path / "invalid-temporal.json"
    def choose(*args):
        if change == "stop":
            session.stop()
        elif change == "source":
            session.accept_snapshot(replace(frame(), input_kind="measured"))
        elif change == "expire":
            session._expired()
        else:
            session.close()
        return str(target), ""
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", choose)
    session._export_temporal()
    assert not target.exists()


def test_invalid_order_stops_temporal_run_without_altering_report(app, session):
    session.accept_snapshot(frame(2))
    session.start()
    settle(app, session.job)
    captured = session.temporal.serialized()
    session.accept_snapshot(frame(1))
    assert not session.running and "TEMPORAL REFUSED" in session.metrics.text()
    assert session.temporal.serialized() == captured


def test_worker_finishing_after_target_arrival_cannot_register(app, session, compensation_archive):
    snapshot = frame()
    session.accept_snapshot(snapshot)
    session.temporal.observe(session.latest)
    pending = Future()
    pending.set_running_or_notify_cancel()
    session.job._future = pending
    session.job._pending_generation = session.job.generation
    session.running = True
    result = ui._evaluate(session.model_id, compensation_archive, session.latest)
    session.accept_snapshot(frame(2))
    pending.set_result(result)
    session.job.poll()
    assert session.job.result is None
    assert session.temporal.report()["registered_predictions"] == 0
