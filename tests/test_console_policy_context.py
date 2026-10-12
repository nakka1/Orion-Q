"""Native contextual inspection remains explicit, private, cancellable and read-only."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Event, get_ident
import time
from types import SimpleNamespace

import pytest

from tests import test_console_policy_review as shared
from quantum_autopilot.console.source_switch import SwitchableProvider
from quantum_autopilot.console.widgets import policy_context as ui
from quantum_autopilot.control_plane import policy_context as backend
from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.topology import default_simulated_topology, _sha256
from quantum_autopilot.core import Provenance, EvidenceKind

app, provider = shared.app, shared.provider
click, wait = shared.click, shared.wait


@pytest.fixture
def panel(provider, app, monkeypatch):
    from quantum_autopilot.console.theme import install

    install(app)
    service = provider.service
    service.controller_runtime
    topology, _ = service.topology_store.initialize(default_simulated_topology())
    node = next(n for n in topology.nodes if n.role.value == "repeater")
    stamp = datetime.now(timezone.utc)
    monkeypatch.setattr(backend, "_now", lambda: stamp)
    state = NetworkStateSnapshot(
        request_id="native-context",
        node_id=node.node_id,
        timestamp_utc=stamp.isoformat(),
        state_version=node.state_version,
        predicted_fidelity=0.9,
        prediction_uncertainty=0.01,
        telemetry_age_s=0.1,
        memory_state=node.memory_state,
        available_pair_fidelities=node.available_pair_fidelities,
        memory_ages_s=node.memory_ages_s,
        telemetry=node.telemetry,
        integrity_verified=True,
        operator_interlock=False,
        provenance=Provenance(
            evidence_kind=EvidenceKind.SIMULATED,
            source="native-isolated-fixture",
            timestamp=stamp.isoformat(),
            metadata=dict(
                topology_id=topology.topology_id,
                evidence_lane="bell_fidelity_control",
                target_semantics="simulated_future_bell_fidelity",
                physical_calibration=False,
            ),
        ),
    )
    wrapper = SwitchableProvider(provider)
    wrapper.running = True
    provider.refresh()
    view = ui.PolicyContextInspection()
    view.attach(wrapper)
    view.show()
    app.processEvents()
    yield view, wrapper, service, state.to_dict()
    view.close()
    wait(app, lambda: not view.job.active and not view.export_job.active)
    wrapper.running = False


def load(panel, app):
    view = panel[0]
    click(view.load_button)
    wait(app, lambda: not view.job.active)
    assert view.context, view.status.text()


def observe(panel, app, candidate=0):
    view = panel[0]
    load(panel, app)
    view.candidate.setCurrentIndex(candidate)
    view.input.setPlainText(json.dumps(panel[3]))
    click(view.observe_button)
    wait(app, lambda: not view.job.active)
    assert view.observation, view.status.text()
    return view.observation


def test_explicit_private_background_session_preserves_api_and_database(panel, app, monkeypatch):
    view, _, service, _ = panel
    assert view.context is None and view.observation is None and not view.job.active
    api = service.policy_compatibility_context()
    session = service._policy_compatibility
    runtime = service._controller_runtime
    before = runtime.journal.path.read_bytes()

    def forbidden(*a, **kw):
        pytest.fail("inspection must never decide or execute")

    monkeypatch.setattr(runtime, "cycle", forbidden)
    monkeypatch.setattr(runtime.policy, "decide", forbidden)
    threads = []
    original = ui.PolicyContextBinding.run

    def run(self, *args):
        threads.append(get_ident())
        return original(self, *args)

    monkeypatch.setattr(ui.PolicyContextBinding, "run", run)
    result = observe(panel, app)
    assert result["record"]["status"] == "OBSERVED_SUPPORTED_INPUT"
    assert result["record_sha256"] == _sha256(result["record"])
    assert all(t != get_ident() for t in threads) and len(threads) == 2
    assert service._policy_compatibility is session
    assert view.context["record"]["context_sha256"] != api["record"]["context_sha256"]
    assert runtime.journal.path.read_bytes() == before
    assert not result["record"]["selection_available"]
    assert "Selection / activation: UNAVAILABLE" in view.details.toPlainText()
    assert json.loads(view.receipt.toPlainText()) == result
    if directory := os.environ.get("ORION_POLICY_CONTEXT_UI_EVIDENCE"):
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        (out / "observation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        for w, h in ((640, 480), (1000, 700)):
            view.resize(w, h)
            app.processEvents()
            assert view.details.height() > 160
            assert view.export_button.mapTo(view, view.export_button.rect().bottomRight()).x() < view.width()
            assert view.grab().save(str(out / f"context-{w}.png"))


def test_optional_candidate_blocked_and_edit_clears_export(panel, app):
    result = observe(panel, app, 4)
    view = panel[0]
    assert result["record"]["status"] == "BLOCKED"
    assert "CANDIDATE_ACTION_MAPPING_BLOCKED" in view.details.toPlainText()
    view.input.insertPlainText(" ")
    assert view.observation is None and not view.export_button.isEnabled()
    assert view.context is not None
    assert not view.job.active


@pytest.mark.parametrize(
    "bad",
    ["{}", '{"x":1,"x":2}', "NaN", "x" * 65537],
    ids=["incomplete", "duplicate", "nonfinite", "oversized"],
)
def test_malformed_or_oversized_input_refuses_and_discards_context(panel, app, bad):
    view = panel[0]
    load(panel, app)
    view.input.setPlainText(bad)
    click(view.observe_button)
    wait(app, lambda: not view.job.active)
    assert view.context is None and view.observation is None and not view.export_button.isEnabled()
    assert "REFUSED" in view.status.text()


@pytest.mark.parametrize(
    "change", ["close", "source", "runtime", "stop", "candidate", "input", "path", "expiry"]
)
def test_invalidation_during_work_is_nonblocking_and_discards_late_receipt(
    panel, app, monkeypatch, change, tmp_path
):
    view, wrapper, service, state = panel
    load(panel, app)
    view.input.setPlainText(json.dumps(state))
    entered, release = Event(), Event()
    original = ui._observe_text

    def blocked(*a):
        entered.set()
        assert release.wait(5)
        return original(*a)

    monkeypatch.setattr(ui, "_observe_text", blocked)
    click(view.observe_button)
    wait(app, entered.is_set)
    start = time.monotonic()
    try:
        if change == "close":
            view.close()
        elif change == "source":
            service.invalidate_policy_preparation()
            view._tick()
        elif change == "runtime":
            service._controller_runtime = None
            view._tick()
        elif change == "stop":
            wrapper.stop()
            view._tick()
        elif change == "candidate":
            view.candidate.setCurrentIndex(1)
        elif change == "input":
            view.input.insertPlainText(" ")
        elif change == "path":
            service.control_journal_path = tmp_path / "changed.sqlite"
            view._tick()
        else:
            view.deadline = time.monotonic() - 1
            view._tick()
        assert time.monotonic() - start < 1
        assert view.context is None and view.observation is None
        assert not view.load_button.isEnabled()
    finally:
        release.set()
    wait(app, lambda: not view.job.active)
    assert view.context is None and view.observation is None and not view.export_button.isEnabled()


@pytest.mark.parametrize("change", ["candidate", "input", "source", "close", "cancel", "expiry"])
def test_export_chooser_rechecks_selection(panel, app, monkeypatch, tmp_path, change):
    view, _, service, _ = panel
    observe(panel, app)
    target = tmp_path / "never.json"

    def choose(*a):
        if change == "candidate":
            view.candidate.setCurrentIndex(1)
        elif change == "input":
            view.input.insertPlainText(" ")
        elif change == "source":
            service.invalidate_policy_preparation()
        elif change == "close":
            view.close()
        elif change == "expiry":
            view.deadline = time.monotonic() - 1
        return ("" if change == "cancel" else str(target)), ""

    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", choose)
    click(view.export_button)
    wait(app, lambda: not view.export_job.active)
    assert not target.exists()


def test_export_captured_receipt_off_thread_and_no_overwrite(panel, app, monkeypatch, tmp_path):
    view = panel[0]
    captured = observe(panel, app)
    target = tmp_path / "receipt.json"
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", lambda *a: (str(target), ""))
    threads = []
    original = ui._export_captured

    def export(*a):
        threads.append(get_ident())
        return original(*a)

    monkeypatch.setattr(ui, "_export_captured", export)
    click(view.export_button)
    wait(app, lambda: not view.export_job.active)
    assert json.loads(target.read_text(encoding="utf-8")) == captured
    assert threads[0] != get_ident()
    before = target.read_bytes()
    click(view.export_button)
    wait(app, lambda: not view.export_job.active)
    assert target.read_bytes() == before and "EXPORT REFUSED" in view.status.text()


def test_topology_change_and_idle_expiry_clear_receipts(panel, app):
    view = panel[0]
    observe(panel, app)
    version = view.context["record"]["context"]["topology_version"]
    view._snapshot(SimpleNamespace(
        topology=SimpleNamespace(persisted=True, version=version + 1),
        capabilities=panel[1].capabilities,
    ))
    assert view.context is None and view.observation is None
    observe(panel, app)
    view.deadline = time.monotonic() - 1
    view._tick()
    assert view.context is None and not view.export_button.isEnabled()


def test_missing_runtime_does_not_initialize_it(provider, app):
    wrapper = SwitchableProvider(provider)
    wrapper.running = True
    view = ui.PolicyContextInspection()
    view.attach(wrapper)
    view.show()
    app.processEvents()
    try:
        assert provider.service._controller_runtime is None
        assert not wrapper.capabilities.allows("observe_policy_context")
        assert not view.load_button.isEnabled()
        assert not provider.service.control_journal_path.exists()
    finally:
        view.close()
        wrapper.running = False


def test_synthetic_source_has_no_context_access(app):
    from quantum_autopilot.console.providers import MockProvider

    wrapper = SwitchableProvider(MockProvider())
    wrapper.running = True
    view = ui.PolicyContextInspection()
    view.attach(wrapper)
    view.show()
    app.processEvents()
    try:
        assert not view.load_button.isEnabled() and view.context is None
    finally:
        view.close()
        wrapper.running = False
