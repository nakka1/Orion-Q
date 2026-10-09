"""Model registry and live records of the network engine: honest states, learned path, serialisation."""
import hashlib
import math
import zipfile

import numpy as np
import pytest

from quantum_autopilot.control_plane import keyrate_loop as kr
from quantum_autopilot.control_plane import polarisation_loop as pol
from quantum_autopilot.model_intake import artifacts
from quantum_autopilot.network_engine import (LinkBank, LinkProcessor, ModelRegistry, SimulatedInputSource,
                                              StepControls, state)
from quantum_autopilot.network_engine import models as registry_module
from quantum_autopilot.network_engine.models import FastSafeDetector, KeyRateActor, StaticKeyRate
from quantum_autopilot.network_engine.state import (Decision, DemandLive, LinkHistory, LinkLive, LiveState,
                                                    ModelStatus, NetEvent, NodeLive, SourceInfo)
from tests import test_polarisation_loop as compensation_helpers
from tests.test_network_engine_links import AT, run
from tests.test_network_engine_sources import topology
from tests.test_optional_keyrate import SyntheticQKD
from tests.test_sync_loop import SyntheticSync

pytestmark = pytest.mark.unit


class SyntheticQkdArchive(SyntheticQKD):
    """Duck-typed qkd archive (tests only): untrained actor (uniform policy) and a synchroniser ridge."""

    def __init__(self):
        super().__init__()
        self._sync = SyntheticSync().meta

    def json(self, name):
        return dict(self._sync) if name.endswith("sincronizador_ridge.json") else super().json(name)


# --------------------------------------------------------------------------
# registry without archives: the truth is "unavailable", and the baselines are named
# --------------------------------------------------------------------------

def test_without_archives_every_learned_role_is_unavailable_and_names_its_baseline():
    registry = ModelRegistry(environ={})
    by_role = {status.role: status for status in registry.status()}
    assert tuple(by_role) == state.MODEL_ROLES
    for role, baseline in (("compensation", "baseline.alpha_beta"), ("sync", "baseline.alpha_beta"),
                           ("keyrate", "baseline.static"), ("routing", "baseline.greedy_fidelity"),
                           ("allocation", "baseline.quantum_first")):
        assert by_role[role].state == "unavailable" and baseline in by_role[role].reason
        assert by_role[role].archive_sha256 is None and by_role[role].inferences == 0
    assert "ORION_QNET_ARCHIVE" in by_role["compensation"].reason and "ORION_QKD_ARCHIVE" in by_role["sync"].reason
    detector = by_role["detector"]
    assert detector.state == "active" and detector.model_id == "qkd.detector.boosting"
    assert detector.archive_sha256 is None and "no archive bound" in detector.reason
    assert registry.routing_archive() is None and registry.allocator_archive() is None
    assert [item.state for item in registry.archive_states()] == ["absent", "absent"]

    controller, status = registry.compensator("calibrado", subject="F10")
    assert isinstance(controller, pol.AlphaBeta) and (controller.alpha, controller.beta) == (0.01, 0.0)
    assert status.state == "unavailable"
    predictor, status = registry.synchroniser(10.0, subject="F10")
    assert type(predictor).__name__ == "AlphaBetaTracker" and status.state == "unavailable"
    actor, status = registry.keyrate_actor(10.0, subject="F10")
    assert isinstance(actor, StaticKeyRate) and actor.decide(None, None, None, span_km=10.0, domain="x") == (0, None)
    detector, status = registry.detector(10.0, subject="F10")
    assert isinstance(detector, FastSafeDetector) and status.in_contract is False and "outside" in status.reason
    assert registry.detector(35.0)[1].in_contract is True and registry.detector()[1].in_contract is None
    assert {item.role: item for item in registry.status()}["detector"].in_contract is False
    registry.release("F10")
    assert {item.role: item for item in registry.status()}["detector"].in_contract is None
    with pytest.raises(ValueError):
        registry.compensator("unknown")
    with pytest.raises(ValueError):
        ModelRegistry(environ={}, compensation_architecture="transformer")


def test_archives_come_from_the_environment_or_set_archive_and_only_pinned_bytes_are_adopted(tmp_path):
    wrong = tmp_path / "not-the-export.zip"
    with zipfile.ZipFile(wrong, "w") as target:
        target.writestr("readme.json", "{}")
    registry = ModelRegistry(environ={"ORION_QKD_ARCHIVE": str(wrong), "ORION_QNET_ARCHIVE": str(tmp_path / "no.zip")})
    qkd, qnet = registry.archive_state("qkd"), registry.archive_state("qnet")
    assert (qkd.state, qkd.origin) == ("rejected", "environment") and "hash differs" in qkd.reason
    assert qnet.state == "rejected" and registry.archive("qkd") is None and registry.archive("qnet") is None
    by_role = {status.role: status for status in registry.status()}
    assert by_role["keyrate"].state == "unavailable" and "rejected" in by_role["keyrate"].reason
    assert registry.set_archive("qkd", "//server/share/x.zip").state == "rejected"       # no network paths
    assert registry.set_archive("qkd", None).state == "absent"
    with pytest.raises(ValueError):
        registry.set_archive("other", wrong)
    assert qkd.to_dict()["kind"] == "qkd"


def test_unverified_test_archives_are_refused_unless_the_registry_was_built_for_tests():
    with pytest.raises(PermissionError):
        ModelRegistry(environ={}).register_test_archive("qkd", SyntheticQkdArchive())
    registry = ModelRegistry(environ={}, allow_unverified=True)
    with pytest.raises(ValueError):
        registry.register_test_archive("qnet", SyntheticQkdArchive())                       # wrong kind
    adopted = registry.register_test_archive("qkd", SyntheticQkdArchive())
    assert adopted.state == "test_unverified" and "TEST ARCHIVE" in adopted.reason


# --------------------------------------------------------------------------
# learned-controller path (duck-typed and synthetic archives, tests only)
# --------------------------------------------------------------------------

@pytest.fixture()
def learned(tmp_path, monkeypatch):
    """A registry with a synthetic qnet ZIP adopted through the real pinned-hash path
    (the pin is monkeypatched to the synthetic bytes) and a duck-typed qkd archive."""
    archive = compensation_helpers._archive(tmp_path, monkeypatch)
    registry = ModelRegistry(environ={"ORION_QNET_ARCHIVE": str(tmp_path / "qnet.zip")}, allow_unverified=True)
    assert registry.archive_state("qnet").state == "verified"
    assert registry.archive_state("qnet").sha256 == archive.sha256 == artifacts.PINS["qnet"]
    registry.register_test_archive("qkd", SyntheticQkdArchive())
    return registry


def test_learned_models_are_active_inside_their_contract_and_fall_back_outside_it(learned):
    controller, status = learned.compensator("calibrado", subject="F50")
    assert isinstance(controller, pol.LearnedCheckpoint) and controller.controller_id == \
        "qnet.compensation.calibrado.ridge"
    assert status.state == "active" and status.in_contract is True and len(status.archive_sha256) == 64
    assert learned.routing_archive() is learned.archive("qnet") is learned.allocator_archive()

    predictor, status = learned.synchroniser(50.0, subject="F50")
    assert predictor.controller_id == "qkd.sync.ridge.1s.h2s" and status.state == "active"
    predictor, status = learned.synchroniser(10.0, subject="F10")
    assert type(predictor).__name__ == "AlphaBetaTracker" and status.state == "fallback"
    assert "50 km" in status.reason and status.in_contract is False

    actor, status = learned.keyrate_actor(50.0, subject="F50", seed=3)
    assert isinstance(actor, KeyRateActor) and status.state == "active"
    actor, status = learned.keyrate_actor(10.0, subject="F10")
    assert isinstance(actor, StaticKeyRate) and status.state == "fallback" and "25-80 km" in status.reason

    by_role = {item.role: item for item in learned.status()}
    assert by_role["sync"].state == "active" and "1 of 2 fibres" in by_role["sync"].reason
    assert by_role["keyrate"].state == "active" and by_role["keyrate"].in_contract is False
    assert by_role["routing"].state == "active" and "no fibre assigned" in by_role["routing"].reason
    learned.release("F50")
    assert {item.role: item for item in learned.status()}["sync"].state == "fallback"
    learned.set_role_status(ModelStatus("qnet.routing.nominal", "routing", "fallback", "outside the 4-node chain"))
    learned.note("routing", inferences=7, faults=1, latency_ms=2.5)
    routing = {item.role: item for item in learned.status()}["routing"]
    assert (routing.state, routing.inferences, routing.faults, routing.last_latency_ms) == ("fallback", 7, 1, 2.5)
    with pytest.raises(ValueError):
        learned.set_role_status("active")


def test_compensation_checkpoint_of_another_plant_is_refused_by_its_contract(tmp_path, monkeypatch):
    other = {**pol.plant.OPERATING_POINTS["calibrado"].declaration(), "vibration_frequency_hz": 50.0}
    compensation_helpers._archive(tmp_path, monkeypatch, fibre=other)
    registry = ModelRegistry(environ={})
    assert registry.set_archive("qnet", tmp_path / "qnet.zip").state == "verified"
    controller, status = registry.compensator("calibrado", subject="F10")
    assert isinstance(controller, pol.AlphaBeta) and status.state == "fallback"
    assert "declared_training_fibre_differs_from_plant" in status.reason and status.in_contract is False


def test_learned_controllers_act_on_a_50_km_fibre_and_their_decisions_are_recorded(learned):
    source, network = SimulatedInputSource(3, "quiet"), topology([50])
    bank, steps, frames = run(source, network, range(14), bank=LinkBank(learned, seed=5))
    live = steps[-1].links["F50"]
    assert live.controllers["compensation"] == "qnet.compensation.calibrado.ridge"
    assert live.controllers["sync"] == "qkd.sync.ridge.1s.h2s" and live.controllers["keyrate"] == "qkd.keyrate.actor"
    assert {live.controllers[role + ".model_state"] for role in ("compensation", "sync", "keyrate")} == {"active"}
    assert live.status in ("up", "degraded") and live.qber is not None

    decisions = [decision for step in steps for decision in step.decisions]
    keyrate = [decision for decision in decisions if decision.kind == "keyrate"]
    # An untrained (uniform) actor moves the transmitter almost every period; each move is one record.
    assert len(keyrate) >= 8 and all(decision.outcome == "settings_proposed" for decision in keyrate)
    assert all(decision.model_state == "active" and decision.in_contract and not decision.fallback_used
               and decision.controller_id == "qkd.keyrate.actor" and decision.verify() for decision in keyrate)
    assert all("->" in decision.summary and "bit/s" in decision.summary for decision in keyrate)
    # The proposal is what the transmitter adopted in the next period (checked bit for bit by the processing).
    commands = [step.commands.get("F50") for step in steps]
    environment = kr.make_link(50.0)
    for tick, command in enumerate(commands[:-1]):
        if command is not None:
            environment.settings = environment.apply(command.keyrate_action)
        row = frames[tick + 1].links["F50"].qkd.row
        assert (row["signal_intensity"], row["decoy_intensity"]) == (environment.settings.signal_intensity,
                                                                     environment.settings.decoy_intensity)
    assert sum(command is not None for command in commands) == len(keyrate)
    assert not any(decision.outcome == "fallback_entered" for decision in decisions)
    alignment = next(decision for decision in decisions if decision.outcome == "initial_alignment")
    assert alignment.controller_id == "qnet.compensation.calibrado.ridge" and alignment.model_state == "active"
    acquisition = next(decision for decision in decisions if decision.outcome == "acquisition")
    assert acquisition.controller_id == "qkd.sync.ridge.1s.h2s" and acquisition.in_contract is True

    by_role = {item.role: item for item in bank.model_status()}
    assert by_role["compensation"].state == "active" and by_role["compensation"].inferences == 14 * 500
    assert by_role["sync"].inferences == 14 and by_role["keyrate"].inferences == 14
    assert by_role["keyrate"].last_latency_ms is not None and by_role["compensation"].faults == 0
    # The detector's baseline was taken at other transmitter settings: its records must say so.
    assert live.provenance["detector_in_contract"] is False

    # Same seeds, same decisions: the actor's sampling stream is per fibre and reproducible.
    _, again, _ = run(SimulatedInputSource(3, "quiet"), network, range(14), bank=LinkBank(learned, seed=5))
    assert [d.to_dict() for step in again for d in step.decisions] == [d.to_dict() for d in decisions]


def test_a_learned_compensator_that_starts_failing_is_dropped_for_the_standby_and_recorded(learned, monkeypatch):
    real = pol.LearnedCheckpoint.propose

    def failing(self, time_s, measured, covariates):
        if 0.6 <= time_s < 5.0:
            raise ValueError("checkpoint refused the sample")
        return real(self, time_s, measured, covariates)

    monkeypatch.setattr(pol.LearnedCheckpoint, "propose", failing)
    source, network = SimulatedInputSource(3, "quiet"), topology([50])
    processor = LinkProcessor("F50", "n0", "n1", 50.0, learned)
    made = []
    for tick in range(3):
        live, decisions = processor.step(source.frame(tick, network).links["F50"],
                                         controls=StepControls(tick, AT, float(tick)))
        made.append(decisions)
    entered = [decision for decision in made[0] if decision.outcome == "fallback_entered"]
    assert len(entered) == 1 and entered[0].controller_id == "baseline.alpha_beta"
    assert entered[0].model_state == "fallback" and entered[0].fallback_used is True
    assert "8 consecutive refused proposals" in entered[0].summary
    assert live.controllers["compensation"] == "baseline.alpha_beta"
    assert live.controllers["compensation.model_state"] == "fallback" and live.status == "degraded"
    assert not any(decision.kind == "compensation" for decision in made[2])       # entered once, not every tick
    assert {item.role: item for item in learned.status()}["compensation"].state == "fallback"
    # Re-activation is never automatic; a recommissioning is a new activation and is recorded.
    monkeypatch.setattr(pol.LearnedCheckpoint, "propose", real)
    processor.reset()
    live, decisions = processor.step(source.frame(3, network).links["F50"], controls=StepControls(3, AT, 3.0))
    assert [decision.outcome for decision in decisions if decision.kind == "compensation"] == ["fallback_left"]
    assert live.controllers["compensation"] == "qnet.compensation.calibrado.ridge"


def test_an_actor_that_refuses_is_held_at_the_commissioned_settings(learned, monkeypatch):
    def refuse(self, *args, **kwargs):
        raise ValueError("outside the reviewed box")

    monkeypatch.setattr(registry_module.KeyRateReplayModel, "recommend", refuse)
    source, network = SimulatedInputSource(3, "quiet"), topology([50])
    bank, steps, _ = run(source, network, range(10), bank=LinkBank(learned))
    assert all(step.commands == {} for step in steps)
    entered = [decision for step in steps for decision in step.decisions if decision.outcome == "fallback_entered"]
    assert len(entered) == 1 and entered[0].tick == 7 and entered[0].kind == "keyrate"       # eighth refusal
    live = steps[-1].links["F50"]
    assert live.controllers["keyrate"] == "baseline.static" and live.controllers["keyrate.model_state"] == "fallback"
    assert live.qkd.signal_intensity == kr.commissioned(50.0).signal_intensity
    assert {item.role: item for item in bank.model_status()}["keyrate"].faults == 8


# --------------------------------------------------------------------------
# frozen records
# --------------------------------------------------------------------------

def test_records_validate_their_enumerations_and_refuse_invented_numbers():
    with pytest.raises(ValueError):
        LinkLive("L", "a", "b", 1.0, "fine")
    with pytest.raises(ValueError):
        LinkLive("L", "a", "b", 1.0, "up", qber=float("nan"))
    with pytest.raises(ValueError):
        LinkLive("L", "a", "b", 1.0, "up", fidelity=1.2)
    with pytest.raises(ValueError):
        ModelStatus("m", "oracle", "active")
    with pytest.raises(ValueError):
        ModelStatus("m", "sync", "maybe")
    with pytest.raises(ValueError):
        DemandLive("d", "a", "b", "lost")
    with pytest.raises(ValueError):
        NodeLive("n", "endpoint", None, "up", located=True)
    with pytest.raises(ValueError):
        LinkHistory((0.0, 1.0), (0.1,), (0.9,), (1.0,))
    with pytest.raises(ValueError):
        SourceInfo("magic", "x")
    with pytest.raises(ValueError):
        NetEvent.create(tick=0, at_utc=AT, kind="k", subject="s", severity="loud")
    with pytest.raises(ValueError):
        LinkLive.from_dict({"link_id": "L", "a": "a", "b": "b", "distance_km": 1.0, "status": "up", "extra": 1})
    with pytest.raises(ValueError):
        LiveState(0, AT, 0.0, 1, SourceInfo("none", "x"), links=({"link_id": "L"},))
    empty = LinkLive("L", "a", "b", 1.0, "no_input")
    assert empty.qber is None and empty.wave == () and empty.to_dict()["history"]["t_s"] == []
    assert LinkLive.from_dict(empty.to_dict()) == empty


def test_decision_hash_is_canonical_and_tamper_evident():
    arguments = dict(tick=3, at_utc=AT, kind="keyrate", controller_id="qkd.keyrate.actor", model_state="active",
                     subject="F50", summary="mu +25%", inputs_sha256="0" * 64, outcome="settings_proposed",
                     in_contract=True, fallback_used=False)
    decision = Decision.create(**arguments)
    assert decision.verify() and decision == Decision.create(**arguments) and decision.physical_action_performed is False
    body = decision.to_dict()
    record_hash = body.pop("record_sha256")
    assert record_hash == hashlib.sha256(state.canonical_json(body).encode("utf-8")).hexdigest()
    tampered = Decision.from_dict({**decision.to_dict(), "summary": "mu -20%"})
    assert not tampered.verify()
    with pytest.raises(ValueError):
        Decision.from_dict({**decision.to_dict(), "physical_action_performed": True})
    with pytest.raises(ValueError):
        Decision.create(**{**arguments, "kind": "guess"})
    with pytest.raises(ValueError):
        state.canonical_json({"value": math.inf})


def test_full_live_state_round_trip_including_nodes_demands_and_events():
    from quantum_autopilot.network_engine.state import CalibrationLive

    calibration = CalibrationLive("cal-1", "complete", "complete", 1.0, 4, AT, 5.0,
                                  {"F10": {"qber_before": 0.03, "qber_after": 0.02}}, True, "a" * 64)
    node = NodeLive("n0", "repeater", "core", "up", True, -12.97, -38.5, "Pelourinho", ("F10",), "idle",
                    (0.93, 0.91), 24.5, calibration, 1, ("d1",), "127.0.0.1", 9000)
    queued = NodeLive("n1", "endpoint", None, "isolated")
    demand = DemandLive("d1", "n0", "n1", "rerouted", ("n0", "n1"), ("F10",), 0.91, 0.8, "qnet.routing.nominal",
                        False, True, "alarm on F12", 7)
    event = NetEvent.create(tick=7, at_utc=AT, kind="input_event", subject="F10", severity="warning",
                            message="intercept_resend started", source="input")
    live = LinkLive("F10", "n0", "n1", 10.0, "up", 0.02, 0.97, 4e6, 6e6, 2.0, 0.99, 480.0, 0.74, (0.9, 0.91),
                    LinkHistory((0.0,), (0.02,), (0.97,), (4e6,)), controllers={"sync": "baseline.alpha_beta"},
                    provenance={"period": 1, "status_reasons": []})
    live_state = LiveState(7, AT, 7.0, 3, SourceInfo("simulated", "demo", 1, "demo", "note"), (live,),
                           (node, queued), (demand,), (), ModelRegistry(environ={}).status(), (event,))
    assert LiveState.from_json(live_state.to_json()) == live_state
    assert live_state.to_dict()["nodes"][0]["calibration"]["links"]["F10"]["qber_after"] == 0.02
    assert np.isclose(live_state.to_dict()["nodes"][0]["pairs"][0], 0.93)
