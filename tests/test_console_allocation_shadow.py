"""Artificial allocation stream and explicit GUI lifecycle without feedback."""
from concurrent.futures import Future
from dataclasses import replace
from datetime import datetime
import hashlib
import json
import os
import zipfile

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from quantum_autopilot.console.synthetic import SyntheticProvider, artificial_snapshot  # noqa: E402
from quantum_autopilot.console import synthetic_allocation as producer  # noqa: E402
from quantum_autopilot.console.widgets import allocation_shadow as ui  # noqa: E402
from quantum_autopilot.console.widgets.model_catalog import ModelCatalog  # noqa: E402
from quantum_autopilot.control_plane.optional_allocation import TELEMETRY_KEY, AllocationShadowWindow  # noqa: E402
from quantum_autopilot.model_intake import catalog as models  # noqa: E402
from quantum_autopilot.model_intake.artifacts import ALLOCATION_ROOT  # noqa: E402
from quantum_autopilot.model_intake.verification import verify_model_archive  # noqa: E402
from tests import test_optional_allocation_session as fixtures  # noqa: E402
from tests import test_console_compensation_shadow as gui  # noqa: E402

make_archive = fixtures.make_archive
allocation_package = fixtures.allocation_package
app = gui.app
settle = gui.settle
pytestmark = pytest.mark.unit


@pytest.fixture
def package(allocation_package, monkeypatch):
    with zipfile.ZipFile(allocation_package) as archive:
        plant = hashlib.sha256(archive.read(ALLOCATION_ROOT + 'model/plant.json')).hexdigest()
    monkeypatch.setattr(producer, 'REVIEWED_PLANT_SHA256', plant)
    return allocation_package


def frame(tick=0, seed=1337, stream=None):
    stream = producer.SyntheticAllocationStream() if stream is None else stream
    snapshot = artificial_snapshot(tick=tick, seed=seed)
    snapshot.optional_allocation_state = stream.push(snapshot, seed, tick)
    return snapshot


@pytest.fixture
def session(app, package):
    dialog = ui.AllocationShadowSession(ui.MODEL_IDS[1])
    dialog.set_package(verify_model_archive(dialog.model_id, package))
    dialog.show()
    yield dialog
    dialog.close()
    settle(app, dialog.job)
    dialog.deleteLater()
    app.processEvents()


def test_stream_is_causal_deterministic_and_independent(monkeypatch):
    monkeypatch.setattr(models, 'open_optional_model', lambda *a, **k: pytest.fail('automatic model load'))
    stream = producer.SyntheticAllocationStream()
    prior = []
    states = []
    for tick in range(8):
        state = frame(tick, stream=stream).optional_allocation_state
        window = AllocationShadowWindow.from_dict(state.telemetry[TELEMETRY_KEY])
        window.validate_context(state)
        assert list(window.history) == prior[-5:]
        assert window.observation.timestamp_utc == state.timestamp_utc
        assert len(window.history) == min(tick, 5)
        assert 'reference' not in window.to_dict()
        prior.append(stream.history[-1])
        states.append(state)
        assert all(v in (0., .25, .5, .75, 1.) for row in window.observation.pending_admission for v in row)
        assert state.provenance.evidence_kind.value == 'simulated'
    for a, b in zip(states, states[1:]):
        assert (datetime.fromisoformat(b.timestamp_utc) - datetime.fromisoformat(a.timestamp_utc)).total_seconds() == 5.
        # The author's plant: the incumbent's queued commands advance one period per publication.
        assert b.telemetry[TELEMETRY_KEY]['observation']['pending_admission'][:-1] == \
            a.telemetry[TELEMETRY_KEY]['observation']['pending_admission'][1:]
        assert b.telemetry[TELEMETRY_KEY]['source_id'] == 'author-plant-allocation'
    assert states[0] == frame().optional_allocation_state
    assert states[0] != frame(seed=3).optional_allocation_state
    assert stream.push(artificial_snapshot(tick=7), 1337, 7) is states[-1]


@pytest.mark.parametrize('change', ['gap', 'rollback', 'campaign', 'seed', 'node', 'dropout', 'measured'])
def test_stream_discontinuities_reset_without_backfill(change):
    stream = producer.SyntheticAllocationStream()
    for tick in range(6):
        first = frame(tick, stream=stream).optional_allocation_state
    tick = 9 if change == 'gap' else 2 if change == 'rollback' else 6
    snap = artificial_snapshot(tick=tick, dropout=change == 'dropout')
    seed = 7 if change == 'seed' else 1337
    if change == 'campaign':
        snap.campaign = 'new-campaign'
    if change == 'node':
        snap.topology.nodes[0].id = 'other-node'
    if change == 'measured':
        snap.input_kind = 'measured'
    state = stream.push(snap, seed, tick)
    if change in ('dropout', 'measured'):
        assert state is None and not stream.history
        state = frame(tick + 1, stream=stream).optional_allocation_state
    assert state.telemetry[TELEMETRY_KEY]['history'] == []
    assert state.metadata['allocation_stream_id'] != first.metadata['allocation_stream_id']


def test_provider_plug_stays_optional_and_stop_clears(app, tmp_path, monkeypatch):
    monkeypatch.setattr(models, 'open_optional_model', lambda *a, **k: pytest.fail('automatic model load'))
    p = SyntheticProvider(tmp_path, tmp_path / 'unused-state')
    assert p.snapshot().optional_allocation_state is not None
    seen = []
    p.snapshot_changed.connect(seen.append)
    p.advance()
    assert len(seen[-1].optional_allocation_state.telemetry[TELEMETRY_KEY]['history']) == 1
    p.stop()
    assert p.snapshot().optional_allocation_state is None
    assert not (tmp_path / 'unused-state').exists()
    assert artificial_snapshot().optional_allocation_state is None


@pytest.mark.parametrize('model_id', ui.MODEL_IDS)
def test_opt_in_warmup_full_history_and_no_feedback(app, package, model_id):
    dialog = ui.AllocationShadowSession(model_id)
    stream = producer.SyntheticAllocationStream()
    try:
        dialog.set_package(verify_model_archive(model_id, package))
        dialog.show()
        snap = frame(stream=stream)
        before = snap.optional_allocation_state.to_json()
        dialog.accept_snapshot(snap)
        assert dialog.start_button.isEnabled() and not dialog.running and dialog.job.result is None
        QTest.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)
        settle(app, dialog.job)
        initial = dialog.job.result
        assert initial.status == 'evaluated'
        assert initial.final_output_status == ('persistence' if model_id.endswith('persistence') else 'warmup_persistence')
        assert snap.optional_allocation_state.to_json() == before
        for tick in range(1, 6):
            dialog.accept_snapshot(frame(tick, stream=stream))
            settle(app, dialog.job)
        result = dialog.job.result
        assert result.final_output_status == ('persistence' if model_id.endswith('persistence') else 'ridge')
        assert all(getattr(result, flag) is False for flag in ['queue_mutated', 'policy_activated',
                                                              'physical_action_performed', 'simulator_action_performed'])
        assert 'SELECTED / PERSISTENCE' in dialog.output.toPlainText()
        assert 'No action is scheduled' in dialog.output.toPlainText()
        assert dialog.export_button.isEnabled()
    finally:
        dialog.close()
        settle(app, dialog.job)
        dialog.deleteLater()


@pytest.mark.parametrize('change', ['stop', 'close', 'source', 'campaign', 'dropout', 'expire', 'package',
                                  'clock', 'stream', 'plant', 'origin', 'invalid', 'rollback', 'duplicate'])
def test_late_workers_discarded(app, session, change):
    session.accept_snapshot(frame(2))
    pending = Future()
    pending.set_running_or_notify_cancel()
    session.job._future = pending
    session.job._pending_generation = session.job.generation
    session.running = True
    if change == 'stop':
        session.stop()
    elif change == 'close':
        session.close()
    elif change == 'expire':
        session._expired()
    elif change == 'package':
        session.invalidate_package()
    else:
        snap = frame(1 if change == 'rollback' else 2 if change == 'duplicate' else 3)
        state = snap.optional_allocation_state
        w = state.telemetry[TELEMETRY_KEY]
        if change == 'source':
            snap.input_kind = 'measured'
        elif change == 'campaign':
            snap.campaign = 'new'
        elif change == 'dropout':
            snap.optional_allocation_state = None
        elif change in ('clock', 'stream', 'plant', 'origin'):
            names = {'clock': ('clock_id', 'clock_id'), 'stream': ('stream_id', 'allocation_stream_id'),
                     'plant': ('plant_sha256', 'allocation_plant_sha256'),
                     'origin': ('origin_timestamp_utc', 'allocation_origin_timestamp_utc')}
            key, meta = names[change]
            value = 'b'*64 if change == 'plant' else '2025-12-31T23:59:55+00:00' if change == 'origin' else 'other'
            w[key] = state.metadata[meta] = value
        elif change == 'invalid':
            w['observation']['urgency_s'][0] = 0
        else:
            w['observation']['backlog_bits'][0] = 1.
        session.accept_snapshot(snap)
    pending.set_result(object())
    session.job.poll()
    assert session.job.result is None and not session.running
    assert not session.export_button.isEnabled()


def test_timeout_duplicates_and_new_start(app, session):
    snapshot = frame()
    session.watchdog.setInterval(25)
    session.accept_snapshot(snapshot)
    QTest.qWait(60)
    assert session.latest is None and not session.start_button.isEnabled()
    session.accept_snapshot(snapshot)
    assert session.latest is None and not session.watchdog.isActive()
    session.watchdog.setInterval(3500)
    session.accept_snapshot(frame(1))
    assert session.start_button.isEnabled() and not session.running and session.job.result is None


def test_slow_worker_never_queues(app, session):
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
    assert session.job.result is None and session.running
    session.accept_snapshot(frame(5))
    settle(app, session.job)
    assert session.job.result.status == 'evaluated'


def test_worker_error_stops_auto_retry(app, session, package):
    session.accept_snapshot(frame())
    package.write_bytes(b'changed')
    session.start()
    settle(app, session.job)
    assert session.job.result.status == 'refused' and not session.running
    assert not session.export_button.isEnabled()
    session.accept_snapshot(frame(1))
    assert not session.job.active and session.job.result is None


def test_catalog_both_models_and_forget(app, package):
    catalog = ModelCatalog()
    catalog.show()
    try:
        for model_id in ui.MODEL_IDS:
            catalog.table.selectRow(next(i for i, o in enumerate(catalog.options) if o.model_id == model_id))
            assert not catalog.shadow_button.isEnabled()
            catalog.checker.result = verify_model_archive(model_id, package)
            catalog.checker.changed.emit()
            catalog.accept_shadow_snapshot(frame())
            assert 'ALLOCATION' in catalog.shadow_button.text()
            catalog._open_shadow()
            dialog = catalog.allocation_shadow
            assert dialog.model_id == model_id and dialog.isVisible() and dialog.start_button.isEnabled()
            assert not dialog.running and catalog.compensation_shadow is None and catalog.shadow_session is None
        catalog.checker.clear()
        assert dialog.package is None and not dialog.isVisible()
    finally:
        catalog.close()
        catalog.deleteLater()


@pytest.mark.parametrize('case', ['duplicate', 'altered_duplicate', 'rollback', 'other_stream', 'dropout'])
def test_catalog_cannot_reopen_stale_input(app, package, monkeypatch, case):
    import quantum_autopilot.console.widgets.model_catalog as module
    now = [10.]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    catalog = ModelCatalog()
    catalog.show()
    try:
        catalog.table.selectRow(next(i for i, o in enumerate(catalog.options) if o.model_id == ui.MODEL_IDS[1]))
        catalog.checker.result = verify_model_archive(ui.MODEL_IDS[1], package)
        catalog.checker.changed.emit()
        snap = frame(2)
        catalog.accept_shadow_snapshot(snap)
        now[0] = 11. if case in ('rollback', 'altered_duplicate') else 15.
        if case == 'rollback':
            snap = frame(1)
        elif case == 'altered_duplicate':
            snap.optional_allocation_state.telemetry[TELEMETRY_KEY]['observation']['backlog_bits'][0] = 1.
        elif case == 'other_stream':
            snap.optional_sync_state = frame(3).optional_allocation_state
        elif case == 'dropout':
            catalog.accept_shadow_snapshot(replace(snap, optional_allocation_state=None))
        catalog.accept_shadow_snapshot(snap)
        catalog._open_shadow()
        assert catalog.allocation_shadow.latest is None and not catalog.allocation_shadow.start_button.isEnabled()
    finally:
        catalog.close()
        catalog.deleteLater()


def test_screen_plug_no_commands(app, package, tmp_path):
    from quantum_autopilot.console.screens.fpga import EdgeControlScreen
    screen = EdgeControlScreen()
    p = SyntheticProvider(tmp_path, tmp_path / 'unused')
    commands = []
    screen.command.connect(lambda *a: commands.append(a))
    p.snapshot_changed.connect(screen.apply)
    try:
        screen.apply(p.snapshot())
        catalog = screen.model_catalog
        catalog.show()
        catalog.table.selectRow(next(i for i, o in enumerate(catalog.options) if o.model_id == ui.MODEL_IDS[1]))
        catalog.checker.result = verify_model_archive(ui.MODEL_IDS[1], package)
        catalog.checker.changed.emit()
        catalog._open_shadow()
        dialog = catalog.allocation_shadow
        dialog.start()
        settle(app, dialog.job)
        first = dialog.job.result
        p.advance()
        settle(app, dialog.job)
        assert dialog.job.result.snapshot_sha256 != first.snapshot_sha256 and not commands
        screen.apply(replace(p.snapshot(), input_kind='measured'))
        assert dialog.package is None and not dialog.isVisible()
    finally:
        p.stop()
        screen.model_catalog.close()
        screen.close()
        screen.deleteLater()


def test_export_captures_frame_and_never_overwrites(app, session, tmp_path, monkeypatch):
    session.accept_snapshot(frame())
    session.start()
    settle(app, session.job)
    selected = session.job.result
    path = tmp_path / 'frame.json'
    def choose(*args):
        session.accept_snapshot(frame(1))
        return str(path), ''
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', choose)
    session._export()
    assert json.loads(path.read_text())['snapshot_sha256'] == selected.snapshot_sha256
    settle(app, session.job)
    original = path.read_bytes()
    session._export()
    assert path.read_bytes() == original and 'EXPORT REFUSED' in session.output.toPlainText()


@pytest.mark.parametrize('change', ['stop', 'close', 'source', 'dropout', 'expire', 'package'])
def test_export_context_loss_cancels(app, session, tmp_path, monkeypatch, change):
    session.accept_snapshot(frame())
    session.start()
    settle(app, session.job)
    path = tmp_path / 'invalid.json'
    def choose(*args):
        if change == 'stop':
            session.stop()
        elif change == 'close':
            session.close()
        elif change == 'expire':
            session._expired()
        elif change == 'package':
            session.invalidate_package()
        else:
            snap = frame()
            if change == 'source':
                snap.input_kind = 'measured'
            else:
                snap.optional_allocation_state = None
            session.accept_snapshot(snap)
        return str(path), ''
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', choose)
    session._export()
    assert not path.exists()


@pytest.mark.parametrize('size', [(640, 480), (1000, 700)])
def test_layout_and_detached_snapshot(app, session, size, tmp_path):
    snap = frame()
    session.accept_snapshot(snap)
    snap.optional_allocation_state.telemetry[TELEMETRY_KEY]['observation']['backlog_bits'][0] = 12345.
    assert session.latest.telemetry[TELEMETRY_KEY]['observation']['backlog_bits'][0] != 12345.
    session.start()
    settle(app, session.job)
    session.resize(*size)
    app.processEvents()
    assert session.width() == size[0] and session.height() == size[1]
    bounds = QRect(QPoint(0, 0), session.size())
    for button in (session.start_button, session.stop_button, session.export_button, session.close_button):
        assert button.isVisible() and bounds.contains(QRect(button.mapTo(session, QPoint(0, 0)), button.size()))
    assert session.output.height() > 100
    assert session.grab().save(str(tmp_path / f'allocation-{size[0]}.png'))


def test_catalog_rechecks_cached_payload_before_open(app, package):
    catalog = ModelCatalog()
    catalog.show()
    try:
        catalog.table.selectRow(next(i for i, o in enumerate(catalog.options) if o.model_id == ui.MODEL_IDS[1]))
        catalog.checker.result = verify_model_archive(ui.MODEL_IDS[1], package)
        catalog.checker.changed.emit()
        snap = frame()
        catalog.accept_shadow_snapshot(snap)
        snap.optional_allocation_state.telemetry[TELEMETRY_KEY]['observation']['backlog_bits'][0] = 1.
        catalog._open_shadow()
        assert catalog.allocation_shadow.latest is None and not catalog.allocation_shadow.start_button.isEnabled()
    finally:
        catalog.close()
        catalog.deleteLater()
