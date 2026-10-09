"""Artificial routing stream and explicit GUI lifecycle without feedback."""
from concurrent.futures import Future
from dataclasses import replace
from datetime import datetime
import json
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from quantum_autopilot.console.synthetic import SyntheticProvider, artificial_snapshot  # noqa: E402
from quantum_autopilot.console import synthetic_routing as producer  # noqa: E402
from quantum_autopilot.console.widgets import routing_shadow as ui  # noqa: E402
from quantum_autopilot.console.widgets.model_catalog import ModelCatalog  # noqa: E402
from quantum_autopilot.control_plane.optional_routing import TELEMETRY_KEY, RoutingShadowWindow  # noqa: E402
from quantum_autopilot.model_intake import catalog as models  # noqa: E402
from quantum_autopilot.model_intake.verification import verify_model_archive  # noqa: E402
from tests import test_optional_routing_session as fixtures  # noqa: E402
from tests import test_console_compensation_shadow as gui  # noqa: E402

make_archive = fixtures.make_archive
routing_archive = fixtures.routing_archive
routing_package = fixtures.routing_package
app = gui.app
settle = gui.settle
pytestmark = pytest.mark.unit


@pytest.fixture
def package(routing_package):
    return routing_package

def frame(tick=0, seed=1337, stream=None):
    stream = producer.SyntheticRoutingStream() if stream is None else stream
    snapshot = artificial_snapshot(tick=tick, seed=seed)
    snapshot.optional_routing_state = stream.push(snapshot, seed, tick)
    return snapshot


@pytest.fixture
def session(app, package):
    dialog = ui.RoutingShadowSession(ui.MODEL_IDS[1])
    dialog.set_package(verify_model_archive(dialog.model_id, package))
    dialog.show()
    yield dialog
    dialog.close()
    settle(app, dialog.job)
    dialog.deleteLater()
    app.processEvents()


def test_stream_is_deterministic_and_independent(monkeypatch):
    monkeypatch.setattr(models, 'open_optional_model', lambda *a, **k: pytest.fail('automatic model load'))
    stream = producer.SyntheticRoutingStream()
    states = []
    counts = []
    for tick in range(8):
        state = frame(tick, stream=stream).optional_routing_state
        window = RoutingShadowWindow.from_dict(state.telemetry[TELEMETRY_KEY])
        window.validate_context(state)
        value = window.to_dict()
        assert value['decision_timestamp_utc'] == state.timestamp_utc
        assert value['resources']['state_version'] == tick
        # The author's environment: its legal actions (WAIT first) and the incumbent's choice as reference.
        assert value['candidates'][0]['action']['kind'] == 'WAIT'
        assert value['reference_action_id'] in {candidate['action_id'] for candidate in value['candidates']}
        assert value['source_id'] == 'author-plant-routing'
        counts.append(len(value['candidates']))
        states.append(state)
        assert state.provenance.evidence_kind.value == 'simulated'
    assert all(count >= 2 for count in counts) and len(set(counts)) > 1
    for a, b in zip(states, states[1:]):
        assert (datetime.fromisoformat(b.timestamp_utc) - datetime.fromisoformat(a.timestamp_utc)).total_seconds() == 1.
        assert a.metadata['routing_resource_sha256'] != b.metadata['routing_resource_sha256']
    assert states[0] == frame().optional_routing_state
    assert states[0] != frame(seed=3).optional_routing_state
    assert stream.push(artificial_snapshot(tick=7), 1337, 7) is states[-1]


@pytest.mark.parametrize('change', ['gap', 'rollback', 'campaign', 'seed', 'node', 'dropout', 'measured'])
def test_stream_discontinuities_reset_without_backfill(change):
    stream = producer.SyntheticRoutingStream()
    for tick in range(6):
        first = frame(tick, stream=stream).optional_routing_state
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
        assert state is None and stream.last_state is None
        state = frame(tick + 1, stream=stream).optional_routing_state
    assert state.state_version == (tick + 1 if change in ('dropout', 'measured') else tick)
    assert state.metadata['routing_stream_id'] != first.metadata['routing_stream_id']


def test_provider_plug_stays_optional_and_stop_clears(app, tmp_path, monkeypatch):
    monkeypatch.setattr(models, 'open_optional_model', lambda *a, **k: pytest.fail('automatic model load'))
    p = SyntheticProvider(tmp_path, tmp_path / 'unused-state')
    assert p.snapshot().optional_routing_state is not None
    seen = []
    p.snapshot_changed.connect(seen.append)
    p.advance()
    assert seen[-1].optional_routing_state.state_version == 1
    p.stop()
    assert p.snapshot().optional_routing_state is None
    assert not (tmp_path / 'unused-state').exists()
    assert artificial_snapshot().optional_routing_state is None


@pytest.mark.parametrize('model_id', ui.MODEL_IDS)
def test_opt_in_candidate_evolution_and_no_feedback(app, package, model_id):
    dialog = ui.RoutingShadowSession(model_id)
    stream = producer.SyntheticRoutingStream()
    try:
        dialog.set_package(verify_model_archive(model_id, package))
        dialog.show()
        snap = frame(stream=stream)
        before = snap.optional_routing_state.to_json()
        dialog.accept_snapshot(snap)
        assert dialog.start_button.isEnabled() and not dialog.running and dialog.job.result is None
        QTest.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)
        settle(app, dialog.job)
        initial = dialog.job.result
        assert initial.status == 'evaluated'
        assert len(json.loads(initial.evaluation_json)['candidates']) == len(
            snap.optional_routing_state.telemetry[TELEMETRY_KEY]['candidates'])
        assert snap.optional_routing_state.to_json() == before
        for tick in range(1, 6):
            last = frame(tick, stream=stream)
            dialog.accept_snapshot(last)
            settle(app, dialog.job)
        result = dialog.job.result
        assert len(json.loads(result.evaluation_json)['candidates']) == len(
            last.optional_routing_state.telemetry[TELEMETRY_KEY]['candidates'])
        assert all(getattr(result, flag) is False for flag in ['resources_mutated', 'policy_activated',
                                                              'physical_action_performed', 'simulator_action_performed'])
        assert 'ORDERED CANDIDATES' in dialog.output.toPlainText()
        assert 'No action executed or scheduled' in dialog.output.toPlainText()
        assert dialog.export_button.isEnabled()
    finally:
        dialog.close()
        settle(app, dialog.job)
        dialog.deleteLater()


@pytest.mark.parametrize('change', ['stop', 'close', 'source', 'campaign', 'dropout', 'expire', 'package',
                                  'clock', 'stream', 'ruleset', 'origin', 'invalid', 'rollback', 'duplicate'])
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
        state = snap.optional_routing_state
        w = state.telemetry[TELEMETRY_KEY]
        if change == 'source':
            snap.input_kind = 'measured'
        elif change == 'campaign':
            snap.campaign = 'new'
        elif change == 'dropout':
            snap.optional_routing_state = None
        elif change in ('clock', 'stream', 'ruleset', 'origin'):
            names = {'clock': ('clock_id', 'clock_id'), 'stream': ('stream_id', 'routing_stream_id'),
                     'ruleset': ('feature_contract', 'routing_ruleset'),
                     'origin': ('origin_timestamp_utc', 'routing_origin_timestamp_utc')}
            key, meta = names[change]
            value = '2025-12-31T23:59:55+00:00' if change == 'origin' else 'other'
            w[key] = state.metadata[meta] = value
        elif change == 'invalid':
            w['task']['minimum_fidelity'] = 2.
        else:
            w['state_features']['TIME_REMAINING'] = .5
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
            assert 'ROUTING' in catalog.shadow_button.text()
            catalog._open_shadow()
            dialog = catalog.routing_shadow
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
            snap.optional_routing_state.telemetry[TELEMETRY_KEY]['state_features']['TIME_REMAINING'] = .5
        elif case == 'other_stream':
            snap.optional_sync_state = frame(3).optional_routing_state
        elif case == 'dropout':
            catalog.accept_shadow_snapshot(replace(snap, optional_routing_state=None))
        catalog.accept_shadow_snapshot(snap)
        catalog._open_shadow()
        assert catalog.routing_shadow.latest is None and not catalog.routing_shadow.start_button.isEnabled()
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
        dialog = catalog.routing_shadow
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
                snap.optional_routing_state = None
            session.accept_snapshot(snap)
        return str(path), ''
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', choose)
    session._export()
    assert not path.exists()


@pytest.mark.parametrize('size', [(640, 480), (1000, 700)])
def test_layout_and_detached_snapshot(app, session, size, tmp_path):
    snap = frame()
    session.accept_snapshot(snap)
    snap.optional_routing_state.telemetry[TELEMETRY_KEY]['state_features']['TIME_REMAINING'] = 0
    assert session.latest.telemetry[TELEMETRY_KEY]['state_features']['TIME_REMAINING'] != 0
    session.start()
    settle(app, session.job)
    session.resize(*size)
    app.processEvents()
    assert session.width() == size[0] and session.height() == size[1]
    bounds = QRect(QPoint(0, 0), session.size())
    for button in (session.start_button, session.stop_button, session.export_button, session.close_button):
        assert button.isVisible() and bounds.contains(QRect(button.mapTo(session, QPoint(0, 0)), button.size()))
    assert session.output.height() > 100
    assert session.grab().save(str(tmp_path / f'routing-{size[0]}.png'))


def test_catalog_rechecks_cached_payload_before_open(app, package):
    catalog = ModelCatalog()
    catalog.show()
    try:
        catalog.table.selectRow(next(i for i, o in enumerate(catalog.options) if o.model_id == ui.MODEL_IDS[1]))
        catalog.checker.result = verify_model_archive(ui.MODEL_IDS[1], package)
        catalog.checker.changed.emit()
        snap = frame()
        catalog.accept_shadow_snapshot(snap)
        snap.optional_routing_state.telemetry[TELEMETRY_KEY]['state_features']['TIME_REMAINING'] = .5
        catalog._open_shadow()
        assert catalog.routing_shadow.latest is None and not catalog.routing_shadow.start_button.isEnabled()
    finally:
        catalog.close()
        catalog.deleteLater()


def test_resource_versions_cannot_roll_back(app, session, package):
    from quantum_autopilot.control_plane.optional_sync import _hash, _json
    session.accept_snapshot(frame(2))
    snap = frame(3)
    state = snap.optional_routing_state
    state = replace(state, state_version=1)
    snap.optional_routing_state = state
    state.telemetry[TELEMETRY_KEY]['resources']['state_version'] = 1
    state.metadata['routing_resource_sha256'] = _hash(_json(state.telemetry[TELEMETRY_KEY]['resources']))
    session.accept_snapshot(snap)
    assert session.latest is None and not session.start_button.isEnabled()
    catalog = ModelCatalog()
    catalog.show()
    try:
        catalog.table.selectRow(next(i for i, o in enumerate(catalog.options) if o.model_id == ui.MODEL_IDS[1]))
        catalog.checker.result = verify_model_archive(ui.MODEL_IDS[1], package)
        catalog.checker.changed.emit()
        catalog.accept_shadow_snapshot(frame(2))
        catalog.accept_shadow_snapshot(snap)
        catalog._open_shadow()
        assert catalog.routing_shadow.latest is None
    finally:
        catalog.close()
        catalog.deleteLater()


@pytest.mark.parametrize('change', ['task', 'topology'])
def test_changed_task_or_topology_invalidates_session(app, session, change):
    from quantum_autopilot.control_plane.optional_sync import _hash, _json
    session.accept_snapshot(frame())
    snap = frame(1)
    state = snap.optional_routing_state
    value = state.telemetry[TELEMETRY_KEY]
    if change == 'task':
        value['task']['minimum_fidelity'] = .7 if value['task']['minimum_fidelity'] != .7 else .74
    else:
        value['resources']['nodes'][0]['memory_capacity'] = 9
        state.metadata['routing_resource_sha256'] = _hash(_json(value['resources']))
    session.accept_snapshot(snap)
    assert session.package is None and not session.isVisible() and not session.running
