"""Discrete acquisition, faithful quantum states and temporal UI boundaries."""
import json
import os
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest

from quantum_autopilot.control_plane.temporal_path import TemporalPathRequest, evaluate_temporal_path
from quantum_autopilot.control_plane.workspace_path import evaluate_path, WorkspacePathRequest, run_workspace_path, read_workspace_path
from quantum_autopilot.control_plane.topology import default_simulated_topology, SimulatedTopologyStore, DEFAULT_TOPOLOGY_ID


def request(**changes):
    return TemporalPathRequest(**dict(dict(run_id="timed", source="alice", repeater="repeater-a",
        destination="bob", expected_version=0, target_fidelity=.8), **changes))


@pytest.mark.parametrize("changes", [
    {"success_probability_left": True}, {"success_probability_right": -1},
    {"success_probability_left": 1.01}, {"success_probability_left": float("nan")},
    {"attempt_interval_s": 0}, {"attempt_interval_s": "0.01"}, {"attempt_interval_s": float("inf")},
    {"attempt_interval_s": 10**1000}, {"attempt_interval_s": 1e-10},
    {"max_attempts": True}, {"max_attempts": 0}, {"max_attempts": 1001},
    {"memory_t1_s": 0}, {"memory_t2_s": .001}, {"memory_t2_s": float("nan")},
    {"memory_ttl_steps": 0}, {"memory_ttl_steps": 1.5}, {"memory_ttl_steps": 1001},
    {"seed": -1}, {"seed": 2**32}, {"seed": True}, {"protocol": "unknown"},
    {"target_fidelity": float("nan")}, {"source": "bob"}, {"expected_version": True},
])
def test_temporal_contract(changes):
    with pytest.raises(ValueError):
        request(**changes)


def test_exact_json_contract_has_no_silent_defaults():
    payload = asdict(request())
    assert TemporalPathRequest.from_dict(payload) == request()
    for changed in (dict(payload, fake_measurement=True), {k: v for k, v in payload.items() if k != "seed"}):
        with pytest.raises(ValueError):
            TemporalPathRequest.from_dict(changed)


def test_immediate_availability_matches_ideal_without_memory_decay():
    topo = default_simulated_topology()
    temporal = evaluate_temporal_path(topo, request(success_probability_left=1, success_probability_right=1))
    ideal = evaluate_path(topo, WorkspacePathRequest("ideal", "alice", "repeater-a", "bob", 0, .8))
    assert temporal["conditional_fidelity"] == pytest.approx(ideal["conditional_fidelity"], abs=1e-10)
    assert temporal["ticks"] == 1
    assert temporal["elapsed_s"] == 1e-6
    assert temporal["memory_ages_s"] == [0, 0]
    assert temporal["accounting"]["consumed_per_link"] == [1, 1]
    assert temporal["quantum_execution"]["safety"]["authorized"]


def test_timeout_is_not_zero_fidelity_and_has_no_quantum_execution():
    result = evaluate_temporal_path(default_simulated_topology(), request(success_probability_left=0,
        success_probability_right=0, max_attempts=4))
    assert result["outcome"] == "acquisition_timeout"
    assert result["conditional_fidelity"] is None and result["conditional_target_met"] is None
    assert result["quantum_execution"] is None
    assert result["ticks"] == 4
    assert result["accounting"]["attempts_per_link"] == [4, 4]


def test_expiration_before_arrival_and_pair_accounting():
    result = evaluate_temporal_path(default_simulated_topology(), request(success_probability_left=1,
        success_probability_right=0, max_attempts=4, memory_ttl_steps=2))
    assert [row["expired"][0] for row in result["timeline"]] == [False, False, True, False]
    assert [row["attempted"][0] for row in result["timeline"]] == [True, False, True, False]
    assert result["accounting"]["generated_per_link"] == [2, 0]
    assert result["accounting"]["expired_per_link"] == [1, 0]
    assert result["accounting"]["discarded_at_end_per_link"] == [1, 0]
    assert result["accounting"]["final_occupied_slots"] == 0


def test_seed_replays_draws_and_fidelity_independent_of_request_id():
    a = evaluate_temporal_path(default_simulated_topology(), request(success_probability_left=1, success_probability_right=.25))
    b = evaluate_temporal_path(default_simulated_topology(), request(run_id="another", success_probability_left=1, success_probability_right=.25))
    assert a["timeline"] == b["timeline"]
    assert a["conditional_fidelity"] == pytest.approx(b["conditional_fidelity"], abs=1e-12)
    assert a["ticks"] == 2 and a["memory_ages_s"] == [1e-6, 0]
    assert a["accounting"]["attempts_per_link"] == [1, 2]
    assert a["conditional_fidelity"] < .8133
    q = a["quantum_execution"]
    assert q["density_matrices_preserved"]
    assert len(q["aged_density_matrices"]) == 2
    assert q["aged_pair_fidelities"][0] < q["input_fidelities"][0]


def test_quantum_kernel_preserves_non_werner_states():
    from qiskit.quantum_info import DensityMatrix
    from quantum_twin.quantum.swapping import WernerStateSwapping
    zero = DensityMatrix.from_label("00")
    kernel = WernerStateSwapping()
    actual = kernel.swap_states(zero, zero)["F_t"]
    scalar_projection = kernel.swap({"F_t": .5, "success": True}, {"F_t": .5, "success": True})["F_t"]
    assert actual == pytest.approx(.5, abs=1e-7)
    assert abs(actual - scalar_projection) > .1
    for bad in (DensityMatrix.from_label("0"), DensityMatrix(np.zeros((4, 4))), np.eye(4)):
        with pytest.raises(ValueError):
            kernel.swap_states(bad, zero)


@pytest.mark.parametrize("left,right", [(a, b) for a in range(4) for b in range(4)])
def test_density_matrix_swap_bell_basis_corrections(left, right):
    from qiskit.quantum_info import DensityMatrix
    from quantum_twin.quantum.swapping import WernerStateSwapping
    vectors = [np.array(value)/np.sqrt(2) for value in ((1, 0, 0, 1), (1, 0, 0, -1), (0, 1, 1, 0), (0, 1, -1, 0))]
    result = WernerStateSwapping().swap_states(DensityMatrix(vectors[left]), DensityMatrix(vectors[right]))
    assert result["F_t"] == pytest.approx(1 if left == right else 0, abs=1e-7)


def test_quantum_memory_matches_existing_channel_on_actual_state():
    from qiskit.quantum_info import DensityMatrix
    from quantum_twin.quantum.channel import QuantumChannel
    from quantum_twin.quantum.swapping import werner_state, WernerStateSwapping
    from quantum_twin.core.config import PhysicsConfig
    req = request(success_probability_left=1, success_probability_right=.25)
    result = evaluate_temporal_path(default_simulated_topology(), req)
    matrix = result["quantum_execution"]["aged_density_matrices"][0]
    actual = DensityMatrix(np.array(matrix["real"]) + 1j*np.array(matrix["imag"]))
    expected = QuantumChannel(PhysicsConfig(T1=req.memory_t1_s, T2=req.memory_t2_s)).apply_decoherence_to_state(
        werner_state(.9), depol_prob=0, exposure_time=1e-6)
    np.testing.assert_allclose(actual.data, expected.data, atol=1e-12)
    assert not np.allclose(actual.data, werner_state(result["quantum_execution"]["aged_pair_fidelities"][0]).data)
    assert result["conditional_fidelity"] == pytest.approx(WernerStateSwapping().swap_states(expected, werner_state(.9))["F_t"], abs=1e-12)


def test_temporal_persistence_and_strict_idempotency(tmp_path):
    store = SimulatedTopologyStore(tmp_path / "state.sqlite")
    topo = default_simulated_topology()
    store.initialize(topo)
    req = request(success_probability_left=0, success_probability_right=0, max_attempts=2)
    first = run_workspace_path(store, req)
    assert first["record"]["schema"] == "orion.workspace_path.temporal.v1"
    assert run_workspace_path(store, req)["idempotent_replay"]
    assert store.get(DEFAULT_TOPOLOGY_ID) == topo
    assert read_workspace_path(SimulatedTopologyStore(store.path))["record"] == first["record"]
    with pytest.raises(ValueError, match="different request"):
        run_workspace_path(store, replace(req, seed=8))


@pytest.fixture
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    return widgets.QApplication.instance() or widgets.QApplication([])


@pytest.fixture
def provider(tmp_path, app):
    from quantum_autopilot.console.service_provider import ServiceProvider
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
    return ServiceProvider(AssuranceDashboardService(repository_root=Path(__file__).resolve().parents[1],
        control_journal_path=tmp_path / "state.sqlite"), state_root=tmp_path)


def filled_runner(provider):
    from quantum_autopilot.console.screens.topology import TopologyScreen
    screen = TopologyScreen()
    screen.apply(provider.build())
    runner = screen.path_runner
    runner.begin()
    runner.protocol.setCurrentIndex(1)
    for key, value in (("source", "alice"), ("repeater", "repeater-a"), ("destination", "bob")):
        runner.fields[key].setCurrentText(value)
    runner.target.setText(".8")
    payload = asdict(request(success_probability_left=0, success_probability_right=0, max_attempts=3))
    for key, field in runner.temporal_fields.items():
        field.setText(str(payload[key]))
    return screen, runner


def test_temporal_ui_timeout_readback_and_export(provider, tmp_path, app):
    screen, runner = filled_runner(provider)
    captured = []
    runner.command.connect(lambda *value: captured.append(value))
    provider.snapshot_changed.connect(screen.apply)
    runner.submit()
    assert runner.pending and not runner.run.isEnabled()
    assert captured[0][0] == "run_temporal_path"
    provider.dispatch(*captured[0])
    assert runner.pending is None
    assert "ACQUISITION_TIMEOUT" in runner.note.text()
    assert "F unavailable" in runner.note.text()
    assert provider.snapshot().workspace_path.fidelity is None
    assert provider.snapshot().observations == ()
    provider.export_record("temporal-path")
    exported = json.loads(next(tmp_path.glob("console_export_*.json")).read_text())
    assert len(exported["workspace_path"]["record"]["result"]["timeline"]) == 3
    runner.resize(570, 550)
    runner.show()
    app.processEvents()
    assert runner.form_scroll.verticalScrollBar().maximum() > 0
    runner.close()
    screen.close()


def test_all_request_parameters_must_match_confirmation(provider):
    screen, runner = filled_runner(provider)
    captured = []
    runner.command.connect(lambda *value: captured.append(value))
    runner.submit()
    provider.dispatch(*captured[0])
    # A receipt with matching ID/route/target but DIFFERENT seed must not confirm.
    runner.pending = replace(runner.pending, seed=runner.pending.seed+1)
    runner.set_snapshot(provider.snapshot())
    assert runner.pending is not None
    assert "CONFIRMED" not in runner.note.text()
    runner.command_failed("parameter conflict")
    runner.close()
    screen.close()


def test_missing_fields_and_measured_source_fail_closed(provider):
    from quantum_autopilot.console.synthetic import artificial_snapshot
    from quantum_autopilot.console.capabilities import SourceCapabilities
    screen, runner = filled_runner(provider)
    captured = []
    runner.command.connect(lambda *value: captured.append(value))
    runner.temporal_fields["seed"].clear()
    runner.submit()
    assert not captured
    screen.apply(replace(artificial_snapshot(), input_kind="measured", capabilities=SourceCapabilities(mode="read_only")))
    assert not runner.isVisible()
    assert all(not field.text() for field in runner.temporal_fields.values())
    runner.submit()
    assert not captured
    assert provider.service.workspace_path_result() is None
    screen.close()


def test_temporal_capability_is_required_even_via_ideal_verb(provider, monkeypatch):
    original = provider.service.health
    monkeypatch.setattr(provider.service, "health", lambda: dict(original(), workspace_temporal_path_available=False))
    assert provider.capabilities.allows("run_workspace_path")
    assert not provider.capabilities.allows("run_temporal_path")
    with pytest.raises(ValueError, match="unavailable"):
        provider.dispatch("run_workspace_path", json.dumps(asdict(request())))
    assert provider.service.workspace_path_result() is None


@pytest.mark.parametrize("seed", range(5))
def test_resource_accounting_for_seeded_acquisition(seed):
    result = evaluate_temporal_path(default_simulated_topology(), request(seed=seed,
        success_probability_left=.4, success_probability_right=.1, max_attempts=8, memory_ttl_steps=2))
    counts = result["accounting"]
    for side in range(2):
        assert counts["generated_per_link"][side] == counts["expired_per_link"][side] + counts["consumed_per_link"][side] + counts["discarded_at_end_per_link"][side]
        assert counts["attempts_per_link"][side] <= result["ticks"]
    assert counts["final_occupied_slots"] == 0
    for row in result["timeline"]:
        for age in row["memory_ages_s"]:
            assert age is None or 0 <= age < 2e-6


def test_temporal_native_async_confirmation(provider, app):
    import time
    from quantum_autopilot.console.shell import ConsoleWindow
    window = ConsoleWindow(provider, skip_auth=True)
    def settle(condition):
        deadline = time.monotonic() + 15
        while not condition() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        app.processEvents()
        assert condition()
    try:
        window.provider.start()
        settle(lambda: window._snap is not None and not window._busy)
        window.show_screen("topology")
        runner = window.screens["topology"].path_runner
        runner.begin()
        runner.protocol.setCurrentIndex(1)
        for key, value in (("source", "alice"), ("repeater", "repeater-a"), ("destination", "bob")):
            runner.fields[key].setCurrentText(value)
        runner.target.setText(".8")
        payload = asdict(request(success_probability_left=0, success_probability_right=0, max_attempts=3))
        for key, field in runner.temporal_fields.items():
            field.setText(str(payload[key]))
        runner.submit()
        window.show_screen("node")
        settle(lambda: runner.pending is None and not window._busy)
        assert "CONFIRMED" in runner.note.text()
        assert "ACQUISITION_TIMEOUT" in runner.note.text()
        assert window._snap.workspace_path.fidelity is None
        runner.close()
    finally:
        window.close()
