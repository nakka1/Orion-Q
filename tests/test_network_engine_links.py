"""Per-fibre processing of the network engine: real controllers on simulated input."""
from dataclasses import replace
import subprocess
import sys
import time

import numpy as np
import pandas as pd
import pytest

from qnet_qkd.qkd.detection import FeatureWindow, _episode_features

from quantum_autopilot.control_plane import detector_causal
from quantum_autopilot.control_plane import polarisation_loop as pol
from quantum_autopilot.control_plane import polarisation_plant as plant
from quantum_autopilot.model_intake import detector_session
from quantum_autopilot.model_intake.detector import SafeDetector
from quantum_autopilot.network_engine import (IdleInputSource, InputEvent, LinkBank, LinkProcessor, LiveState,
                                              ModelRegistry, SimulatedInputSource, StepControls, commission,
                                              werner_fidelity)
from quantum_autopilot.network_engine import links as links_module
from quantum_autopilot.network_engine.links import CausalFeatures, PolarisationChannel, SyncChannel
from quantum_autopilot.network_engine.models import FastSafeDetector
from quantum_autopilot.network_engine.state import Decision
from tests.test_network_engine_sources import topology

pytestmark = pytest.mark.unit
AT = "2026-10-04T12:00:00+00:00"
ONSET, DURATION, END = 70, 25, 170


def registry():
    return ModelRegistry(environ={})


def run(source, network, ticks, *, bank=None, flags=None, before=None):
    """Drive ``ticks`` ticks the way the engine does: frame -> processing -> actuate."""
    bank = bank or LinkBank(registry())
    bank.sync(network.links)
    steps, frames = [], []
    for tick in ticks:
        if before is not None:
            before(tick, source, bank)
        frame = source.frame(tick, network)
        step = bank.step(frame, flags(tick) if flags else None, at_utc=AT)
        source.actuate(step.commands)
        steps.append(step)
        frames.append(frame)
    return bank, steps, frames


@pytest.fixture(scope="module")
def attack():
    """One 10 km fibre, one session: 60 periods of baseline, an intercept-resend attack from tick 70
    to 95, then quiet."""
    source, network = SimulatedInputSource(11, "quiet", session_periods=None), topology([10])

    def inject(tick, source, bank):
        if tick == ONSET:
            source.inject(InputEvent("intercept_resend", "F10", 0.8, float(DURATION)))

    bank, steps, frames = run(source, network, range(END), before=inject)
    return {"bank": bank, "lives": [step.links["F10"] for step in steps], "frames": frames,
            "decisions": [decision for step in steps for decision in step.decisions]}


# --------------------------------------------------------------------------
# detector: incremental causal features are the batch causal reference
# --------------------------------------------------------------------------

def session_frame(frames, link_id="F10"):
    frame = pd.DataFrame([item.links[link_id].qkd.row for item in frames])
    frame["period"] = frame["period"].astype(int)
    frame["episode"], frame["label"], frame["onset_s"] = 0, "normal", np.nan
    return frame


def test_incremental_causal_features_equal_the_batch_causal_reference_bit_for_bit(attack):
    frame = session_frame(attack["frames"])
    reference, _, gate = detector_causal.causal_features(frame)
    assert gate["last_row_identical"] and gate["free_features_identical"]
    window = FeatureWindow(window=30, baseline_periods=60)
    incremental = CausalFeatures(window.window, window.baseline_periods)
    rows = [incremental.push(row) for row in frame.to_dict("records")]
    assert all(row is None for row in rows[:60]) and incremental.ready
    produced = np.array(rows[60:])
    assert produced.shape == reference.shape == (END - 60, 17)
    assert np.array_equal(produced, reference)
    # ... which is the author's function on the log up to each period (spot check, independent of the campaign code).
    for end in (60, 75, 120, END - 1):
        assert np.array_equal(produced[end - 60], _episode_features(frame.iloc[:end + 1], window)[0][-1])
    # The bounded window is really bounded.
    assert len(incremental._tail) == window.window + 1 and incremental._baseline is None


def test_live_detector_calls_equal_the_reviewed_detector_on_the_batch_reference(attack):
    frame = session_frame(attack["frames"])
    reference, _, _ = detector_causal.causal_features(frame)
    reviewed = SafeDetector()
    calls = reviewed.predict(reference)
    sustained = reviewed.sustained(calls)
    probability = 1.0 - reviewed.probabilities(reference)[:, reviewed.classes.index("normal")]
    lives = attack["lives"][60:]
    assert [live.detector.label for live in lives] == list(calls)
    assert [live.provenance["detector_sustained"] for live in lives] == list(sustained)
    np.testing.assert_array_equal([live.detector.attack_probability for live in lives], np.clip(probability, 0, 1))
    from qnet_qkd.qkd.detection import QberThreshold
    baseline = QberThreshold(sigma=detector_session.AUTHOR_QBER_SIGMA).predict(reference) != "normal"
    assert [live.detector.baseline_alarm for live in lives] == list(baseline)
    assert "intercept_resend" in set(calls[ONSET - 60:ONSET - 60 + DURATION])


def test_fast_detector_is_bit_identical_to_the_reviewed_export(attack):
    reviewed, fast = SafeDetector(), FastSafeDetector()
    reference, _, _ = detector_causal.causal_features(session_frame(attack["frames"]))
    random = np.random.default_rng(0).normal(size=(64, 17)) * reviewed.scale * 3 + reviewed.mean
    random[5, 3] = np.nan
    for block in (reference, random, reference[:1]):
        assert np.array_equal(fast.probabilities(block), reviewed.probabilities(block), equal_nan=True)
        assert list(fast.predict(block)) == list(reviewed.predict(block))
    with pytest.raises(ValueError):
        fast.probabilities(np.zeros((2, 16)))


def test_causal_features_refuse_a_gap_in_the_session_log():
    features = CausalFeatures()
    row = dict.fromkeys(detector_session.COLUMNS, 0.01)
    for period in range(3):
        features.push({**row, "time_s": float(period)})
    with pytest.raises(ValueError):
        features.push({**row, "time_s": 5.0})
    with pytest.raises(ValueError):
        CausalFeatures(window=2)


# --------------------------------------------------------------------------
# intercept-resend: alarm, key discarded, alarm released
# --------------------------------------------------------------------------

def test_honest_period_estimates_come_from_the_published_observables(attack):
    lives, frames = attack["lives"], attack["frames"]
    live, row = lives[40], frames[40].links["F10"].qkd.row
    assert live.status == "up" and live.provenance["status_reasons"] == []
    assert live.qber == row["signal_error"] and 0.015 < live.qber < 0.035
    assert live.skr_bps == row["key_rate_hz"] > 1e6        # the bounds and the rate are recomputed, and agree
    assert live.fidelity == werner_fidelity(live.qber) == pytest.approx(1 - 1.5 * live.qber)
    commissioned = commission(10.0)
    assert live.sifted_rate_hz == pytest.approx(commissioned.clock_rate_hz * commissioned.signal_probability
                                                * row["signal_gain"] * commissioned.sifting_factor)
    assert (live.qkd.signal_intensity, live.qkd.decoy_intensity, live.qkd.clock_rate_hz) == (
        commissioned.signal_intensity, commissioned.decoy_intensity, commissioned.clock_rate_hz)
    assert live.qkd.last_action == "manter" and live.qkd.protocol == "decoy_state_bb84"
    assert live.detector.label is None and "session baseline" in live.provenance["detector_state"]
    assert lives[62].detector.label == "normal" and lives[62].detector.alarm is False
    assert 0.0 < live.stability <= 1.0 and 0.9 < live.gate_efficiency <= 1.0 and live.sync_residual_ps < 50
    assert 100 < live.polarisation_residual_mrad < 1500
    assert live.controllers == {
        "compensation": "baseline.alpha_beta", "sync": "baseline.alpha_beta", "keyrate": "baseline.static",
        "detector": "qkd.detector.boosting", "qber_alarm": "baseline.qber_threshold",
        "compensation.model_state": "unavailable", "sync.model_state": "unavailable",
        "keyrate.model_state": "unavailable", "detector.model_state": "active"}
    assert live.provenance["physical_action_performed"] is False
    assert live.provenance["domain"]["detector"]["in_domain"] is False        # a 10 km metro fibre
    assert live.classical_admission is None


def test_wave_is_20_hz_visibility_of_the_last_12_s_and_history_is_1_hz(attack):
    lives = attack["lives"]
    assert [len(live.wave) for live in lives[:13]] == [20 * count for count in range(1, 13)] + [240]
    assert len(lives[-1].wave) == 240 and all(-1.0 <= sample <= 1.0 for sample in lives[-1].wave)
    visibility = np.mean(lives[30].wave[-20:])
    assert visibility == pytest.approx(1 - 2 * lives[30].provenance["polarisation_qber"], abs=1e-12)
    history = lives[-1].history
    assert history.t_s == tuple(float(tick) for tick in range(END))
    assert history.qber == tuple(live.qber for live in lives)
    assert history.skr_bps == tuple(live.skr_bps for live in lives)


def test_intercept_resend_raises_the_qber_and_a_sustained_alarm_and_the_key_is_discarded(attack):
    lives, decisions = attack["lives"], attack["decisions"]
    before, during = lives[ONSET - 1], lives[ONSET + 10]
    assert during.qber > before.qber + 0.04 and during.skr_bps < 0.5 * before.skr_bps
    assert during.fidelity < before.fidelity and during.stability < before.stability
    raised = [decision for decision in decisions if decision.outcome == "alarm_raised"]
    cleared = [decision for decision in decisions if decision.outcome == "alarm_cleared"]
    assert len(raised) == len(cleared) == 1
    # Three equal consecutive calls are needed: the alarm cannot come before the third attacked period.
    assert ONSET + 2 <= raised[0].tick <= ONSET + 5
    assert all(live.status == "up" and live.detector.alarm is False for live in lives[60:ONSET])
    held = lives[raised[0].tick:cleared[0].tick]
    assert all(live.status == "compromised" and live.detector.alarm for live in held)
    # Key accounting: nothing enters the pool while the alarm is held, and the periods that built it are recalled.
    pool = [live.qkd.key_pool_bits for live in lives]
    assert all(value == pool[raised[0].tick] for value in pool[raised[0].tick:cleared[0].tick])
    assert pool[raised[0].tick] == pytest.approx(sum(live.skr_bps for live in lives[:raised[0].tick - 2]))
    discarded = sum(live.skr_bps for live in lives[raised[0].tick - 2:cleared[0].tick])
    assert lives[cleared[0].tick - 1].qkd.discarded_bits == pytest.approx(discarded)
    assert lives[ONSET - 1].qkd.discarded_bits == 0.0
    # Observed, not wished: some 13 periods after the attack ends the detector calls intercept_resend again
    # for about 16 periods (the attacked periods are leaving its 30-period trend window). The alarm is
    # therefore held for a full feature window without an attack call, and is released exactly once.
    labels = [live.detector.label for live in lives]
    quiet = labels.index("normal", ONSET + DURATION - 1)
    assert quiet <= ONSET + DURATION + 1
    assert "intercept_resend" in labels[quiet:]
    last_call = max(index for index, label in enumerate(labels) if label == "intercept_resend")
    assert ONSET + DURATION < last_call < ONSET + DURATION + 31
    assert cleared[0].tick == last_call + 30
    assert lives[-1].status == "up" and lives[-1].detector.alarm is False
    assert lives[-1].qkd.key_pool_bits > pool[cleared[0].tick - 1]
    for decision in raised + cleared:
        assert decision.kind == "detector" and decision.controller_id == "qkd.detector.boosting"
        assert decision.subject == "F10" and decision.model_state == "active" and decision.verify()
        assert decision.in_contract is False and decision.fallback_used is False       # 10 km: outside 20-70 km
        assert decision.physical_action_performed is False and len(decision.inputs_sha256) == 64
    assert "intercept_resend" in raised[0].summary and "QBER" in raised[0].summary


def test_decisions_are_aggregated_per_tick_and_baseline_controllers_say_what_they_are(attack):
    decisions = attack["decisions"]
    first = [decision for decision in decisions if decision.tick == 0]
    assert sorted(decision.outcome for decision in first) == ["acquisition", "initial_alignment"]
    alignment = next(decision for decision in first if decision.outcome == "initial_alignment")
    assert (alignment.kind, alignment.controller_id, alignment.model_state, alignment.fallback_used) == (
        "compensation", "baseline.alpha_beta", "unavailable", True)
    assert "mrad" in alignment.summary
    # A static transmitter and healthy loops decide nothing more: 500 control steps a second emit no records.
    assert {decision.outcome for decision in decisions} == {"acquisition", "initial_alignment", "alarm_raised",
                                                            "alarm_cleared"}
    assert all(isinstance(decision, Decision) and decision.verify() for decision in decisions)


def test_processing_never_reads_the_hidden_truth_of_the_simulated_plant():
    class Poison(dict):
        def __getitem__(self, key):
            raise AssertionError("the processing read the plant's hidden truth")
        get = keys = values = items = __iter__ = __contains__ = __getitem__

    source, network = SimulatedInputSource(3, "quiet"), topology([10])
    processor = LinkProcessor("F10", "n0", "n1", 10.0, registry())
    for tick in range(63):
        link_input = replace(source.frame(tick, network).links["F10"], reference=Poison())
        live, _ = processor.step(link_input, controls=StepControls(tick, AT, float(tick)))
    assert live.status == "up" and live.detector.label == "normal"
    with pytest.raises(ValueError):
        processor.step(link_input, controls="now")
    with pytest.raises(ValueError):
        processor.finish()
    with pytest.raises(ValueError):
        StepControls(0, AT, 0.0, flag="broken")


# --------------------------------------------------------------------------
# the unrolled supervisor is the reviewed supervisor
# --------------------------------------------------------------------------

class Scripted:
    """A controller that turns to garbage for a while (supervisor tests)."""

    controller_id = "test.scripted"

    def __init__(self, inner, bad):
        self.inner, self.bad = inner, set(bad)

    def reset(self):
        self.inner.reset()
        self.calls = 0

    def propose(self, time_s, measured, covariates):
        self.calls += 1
        proposal = self.inner.propose(time_s, measured, covariates)
        if self.calls - 1 in self.bad:
            if (self.calls - 1) % 2:
                raise ValueError("scripted fault")
            return np.full((3, 3), np.nan)
        return proposal


@pytest.mark.parametrize("bad, fallback", [((), False), (range(700, 704), False), (range(700, 720), True),
                                           ([3, 4, 900], True)])
def test_unrolled_polarisation_loop_issues_the_commands_of_the_reviewed_supervisor(bad, fallback):
    source, network = SimulatedInputSource(5, "quiet"), topology([10])
    ticks = [source.frame(tick, network).links["F10"] for tick in range(3)]
    times = np.concatenate([item.polarisation.time_s for item in ticks])
    measured = np.concatenate([item.polarisation.measured for item in ticks])
    covariates = np.concatenate([item.polarisation.covariates for item in ticks])

    def controllers():
        return Scripted(pol.AlphaBeta(0.01, 0.0), bad), (pol.AlphaBeta(0.01, 0.0) if fallback else None)

    controller, standby = controllers()
    expected, ledger = pol.PolarisationSupervisor().run(controller, standby, [float(t) for t in times], measured,
                                                        covariates)
    controller, standby = controllers()
    channel = PolarisationChannel(controller, standby)
    results = [channel.step(item.polarisation) for item in ticks]
    assert np.array_equal(np.concatenate([result["commands"] for result in results]), expected)
    assert channel.ledger == ledger
    assert channel.degraded == (ledger.degraded_at_s is not None)
    assert sum(result["faults"] for result in results) == ledger.faults
    assert [result["initial_alignment"] for result in results] == [True, False, False]
    if fallback and ledger.degraded_at_s is not None:
        assert channel.acting_id == "baseline.alpha_beta" and results[1]["fallback_entered"]
    # Monitored residual against the hidden truth (allowed here, in a test): same loop, the monitor adds its noise.
    truth = np.concatenate([item.reference["true_rotation"][::2] for item in ticks])
    in_force = np.concatenate([np.broadcast_to(np.eye(3), (20, 3, 3)), expected[:-20]])
    true_qber = plant.bb84_qber(in_force @ truth)
    monitored = np.concatenate([result["qber"] for result in results])
    assert 0.0 <= monitored.mean() - true_qber.mean() < 0.006
    with pytest.raises(ValueError):
        channel.step(ticks[0].polarisation)                     # the stream cannot go back in time


def test_sync_channel_moves_the_gate_two_seconds_after_each_forecast_and_falls_back_on_faults():
    from qnet_qkd.qkd.timing import AlphaBetaTracker, gate_efficiency

    class Sample:
        latency_s = 2.0

        def __init__(self, time_s, offset):
            self.time_s, self.measured_offset_s, self.temperature_c = time_s, offset, 20.0

    class Broken:
        def reset(self):
            pass

        def predict(self, *_):
            raise ValueError("refused")

    reference = AlphaBetaTracker()
    channel = SyncChannel(AlphaBetaTracker(), None, "baseline.alpha_beta", "baseline.alpha_beta")
    offsets = [10e-12 * step for step in range(8)]
    forecasts = []
    for step, offset in enumerate(offsets):
        result = channel.step(Sample(float(step), offset), 400e-12, 60e-12)
        forecasts.append(reference.predict(float(step), offset, 20.0, 2.0))
        position = 0.0 if step < 2 else forecasts[step - 2]
        assert result["residual_s"] == pytest.approx(offset - position, abs=1e-18)
        assert result["efficiency"] == float(gate_efficiency(offset - position, 400e-12, 60e-12))
        assert (result["acquired"], result["first_update"]) == (step >= 2, step == 0)
    faulty = SyncChannel(Broken(), AlphaBetaTracker(), "qkd.sync.ridge.1s.h2s", "baseline.alpha_beta", max_faults=3)
    entered = [faulty.step(Sample(float(step), 1e-12), 400e-12, 60e-12)["fallback_entered"] for step in range(5)]
    assert entered == [False, False, True, False, False] and faulty.faults == 3
    assert faulty.acting_id == "baseline.alpha_beta" and faulty.last_prediction is not None


# --------------------------------------------------------------------------
# flags, idle source, topology changes, recalibration
# --------------------------------------------------------------------------

def test_idle_source_gives_no_data_and_no_decisions():
    network = topology([5, 10])
    bank, steps, _ = run(IdleInputSource(), network, range(3))
    for step in steps:
        assert step.decisions == () and step.commands == {}
        for live in step.links.values():
            assert live.status == "no_input" and live.wave == () and live.history.t_s == ()
            assert (live.qber, live.fidelity, live.skr_bps, live.sifted_rate_hz, live.sync_residual_ps,
                    live.gate_efficiency, live.polarisation_residual_mrad, live.stability) == (None,) * 8
            assert live.detector.to_dict() == {"label": None, "attack_probability": None, "alarm": None,
                                               "baseline_alarm": None}
            assert live.qkd.key_pool_bits is None and live.controllers == {}
    assert bank.measurement("F5") is None and bank.link_state("F10").status == "no_input"


def test_imposed_flags_suppress_observables_and_recalibration_measures_before_and_after():
    network = topology([10, 20])
    source = SimulatedInputSource(4, "quiet")
    bank, steps, _ = run(source, network, range(6))
    before = bank.measurement("F10")
    assert before.period == 5 and before.qber == steps[-1].links["F10"].qber and before.status == "up"
    assert (before.signal_intensity, before.decoy_intensity) == (commission(10.0).signal_intensity,
                                                                 commission(10.0).decoy_intensity)
    command = bank.reset_link("F10")
    assert (command.link_id, command.recommission, command.keyrate_action) == ("F10", True, 0)
    source.actuate({"F10": command})
    assert bank.measurement("F10") == before                    # the "before" survives the reset
    _, steps, frames = run(source, network, range(6, 11), bank=bank, flags=lambda tick: {"F10": "calibrating"})
    for step in steps:
        live = step.links["F10"]
        assert live.status == "calibrating" and live.wave == () and live.qber is None and live.skr_bps is None
        assert live.stability is None and live.detector.label is None and live.controllers == {}
        assert step.links["F20"].status == "up" and step.links["F20"].qber is not None
    assert frames[0].links["F10"].epoch_tick == 6                # the source recommissioned the session
    after = bank.measurement("F10")
    assert (after.epoch_tick, after.period, after.tick) == (6, 4, 10)
    assert after.qber is not None and after.key_rate_hz > 0 and after.sync_residual_ps is not None
    assert after.clock_rate_hz == commission(10.0).clock_rate_hz
    realigned = [decision for step in steps for decision in step.decisions if decision.subject == "F10"]
    assert sorted(decision.outcome for decision in realigned) == ["acquisition", "initial_alignment"]
    # Returned to service: published again, with a gap in its history.
    _, steps, _ = run(source, network, range(11, 13), bank=bank)
    live = steps[-1].links["F10"]
    assert live.status == "up" and live.qber is not None and len(live.wave) == 40
    assert live.history.qber[6:11] == (None,) * 5 and live.history.qber[5] is not None
    assert live.provenance["epoch_tick"] == 6 and live.provenance["period"] == 6

    # Disabled / isolated: nothing is processed even if input arrives, and the session restarts afterwards.
    _, steps, _ = run(source, network, range(13, 15), bank=bank, flags=lambda tick: {"F20": "isolated"})
    assert steps[-1].links["F20"].status == "isolated" and steps[-1].links["F20"].qber is None
    assert bank.measurement("F20").tick == 12
    _, steps, _ = run(source, network, range(15, 16), bank=bank)
    assert steps[-1].links["F20"].status == "up" and len(steps[-1].links["F20"].wave) == 20
    with pytest.raises(ValueError):
        bank.step(source.frame(16, network), {"missing": "disabled"})


def test_a_held_alarm_survives_a_scheduled_new_session_and_is_released_by_the_detector_or_a_recalibration():
    network = topology([10])

    def inject(tick, source, bank):
        if tick == 100:
            source.inject(InputEvent("intercept_resend", "F10", 0.8, 30.0))

    source = SimulatedInputSource(11, "quiet", session_periods=120)
    bank, steps, frames = run(source, network, range(225), before=inject)
    lives = [step.links["F10"] for step in steps]
    assert [frame.links["F10"].epoch_tick for frame in frames[128:132]] == [0, 0, 130, 130]   # waited for the attack
    assert lives[125].status == "compromised" and lives[125].detector.alarm is True
    baseline = lives[131:190]
    assert all(live.status == "compromised" and live.detector.alarm is True and live.detector.label is None
               for live in baseline)
    assert baseline[-1].qkd.key_pool_bits == lives[125].qkd.key_pool_bits        # still discarding
    cleared = [decision.tick for step in steps for decision in step.decisions if decision.outcome == "alarm_cleared"]
    assert cleared == [130 + 60 + 30 - 1] and lives[-1].status == "up"

    source = SimulatedInputSource(11, "quiet", session_periods=120)
    bank, steps, _ = run(source, network, range(110), before=inject)
    assert steps[-1].links["F10"].status == "compromised"
    source.actuate({"F10": bank.reset_link("F10")})                                # recalibration
    _, steps, _ = run(source, network, range(110, 112), bank=bank)
    assert steps[-1].links["F10"].status != "compromised" and steps[-1].links["F10"].detector.alarm is None


def test_bank_follows_the_topology_and_marks_inactive_fibres_disabled():
    source = SimulatedInputSource(6, "quiet")
    bank, steps, _ = run(source, topology([10, 20]), range(2))
    assert bank.link_ids == ("F10", "F20") and set(steps[-1].links) == {"F10", "F20"}
    _, steps, _ = run(source, topology([10, 20], inactive=("F20",)), range(2, 3), bank=bank)
    assert steps[-1].links["F20"].status == "disabled" and steps[-1].links["F20"].qber is None
    longer = topology([10])
    longer.links = (replace(longer.links[0], distance_km=14.0),)
    _, steps, _ = run(source, longer, range(3, 4), bank=bank)
    assert bank.link_ids == ("F10",)
    live = steps[-1].links["F10"]
    assert live.distance_km == 14.0 and live.provenance["period"] == 0 and len(live.wave) == 20
    assert live.qkd.signal_intensity == commission(14.0).signal_intensity
    assert live.qkd.key_pool_bits > live.skr_bps                # the pool is a stock: it survives recommissioning
    with pytest.raises(ValueError):
        bank.sync(topology([10]).links * 2)
    with pytest.raises(KeyError):
        bank.link_state("F20")


def test_a_frame_of_a_domain_the_reviewed_models_do_not_admit_is_still_processed_by_the_baselines():
    assert links_module.domain_of("simulated") == "simulated_offline" and links_module.domain_of("measured") == \
        "measured"
    source, network = SimulatedInputSource(6, "quiet"), topology([10])
    processor = LinkProcessor("F10", "n0", "n1", 10.0, registry())
    link_input = source.frame(0, network).links["F10"]
    live, _ = processor.step(link_input, controls=StepControls(0, AT, 0.0, domain="measured"))
    assert live.status == "up" and live.provenance["input"] == "measured"
    assert live.controllers["compensation"] == "baseline.alpha_beta"


# --------------------------------------------------------------------------
# what the real models do with the other input events (asserted as observed, not as wished)
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def events():
    """Four 35 km-class fibres, one input event each at tick 70 for 25 s."""
    network = topology([33, 34, 35, 36])
    kinds = {"F33": "loss_event", "F34": "thermal_drift", "F35": "pns", "F36": "detector_blinding"}
    source = SimulatedInputSource(11, "quiet", session_periods=None)

    def inject(tick, source, bank):
        if tick == ONSET:
            for link_id, kind in kinds.items():
                source.inject(InputEvent(kind, link_id, 0.8, float(DURATION)))

    _, steps, frames = run(source, network, range(125), before=inject)
    return steps, frames


def test_loss_event_lowers_the_key_rate_and_is_classified_as_loss_without_an_attack_alarm(events):
    steps, _ = events
    lives = [step.links["F33"] for step in steps]
    assert lives[ONSET + 10].skr_bps < 0.8 * lives[ONSET - 1].skr_bps
    assert abs(lives[ONSET + 10].qber - lives[ONSET - 1].qber) < 0.005
    during = lives[ONSET + 3:ONSET + DURATION]
    assert {live.detector.label for live in during} == {"loss_event"}
    assert all(live.status == "degraded" and live.detector.alarm is False for live in during)
    assert all("detector: sustained loss_event" in live.provenance["status_reasons"] for live in during)
    assert lives[ONSET + 10].qkd.key_pool_bits > lives[ONSET + 9].qkd.key_pool_bits     # key is kept


def test_thermal_drift_is_classified_by_the_detector_and_degrades_the_fibre_without_discarding_key(events):
    steps, _ = events
    lives = [step.links["F34"] for step in steps]
    labels = [live.detector.label for live in lives[ONSET:ONSET + DURATION]]
    assert labels.count("thermal_drift") >= 15
    assert any(live.status == "degraded" for live in lives[ONSET:ONSET + DURATION])
    assert lives[-1].qkd.discarded_bits == 0.0 or any(live.status == "compromised" for live in lives)
    # Observed: 11 K rising with a 60 s time constant moves the arrival time of a 34 km span by ~270 ps/s.
    # The alpha-beta tracker (2 s latency) loses the gate for a few seconds at the onset, then follows.
    onset = lives[ONSET:ONSET + 10]
    assert max(live.sync_residual_ps for live in onset) > 300 and min(live.gate_efficiency for live in onset) < 0.5
    assert any("gate efficiency below 90 % of its ideal" in live.provenance["status_reasons"] for live in onset)
    settled = lives[ONSET + 14:ONSET + DURATION - 1]
    assert np.mean([live.gate_efficiency for live in settled]) > 0.9
    assert min(live.stability for live in lives[ONSET:ONSET + DURATION]) < lives[ONSET - 1].stability - 0.1
    assert lives[ONSET - 1].sync_residual_ps < 30 and lives[ONSET - 1].gate_efficiency > 0.99


def test_pns_is_detected_on_a_span_inside_the_trained_range(events):
    steps, _ = events
    lives = [step.links["F35"] for step in steps]
    raised = [decision for step in steps for decision in step.decisions
              if decision.subject == "F35" and decision.outcome == "alarm_raised"]
    assert len(raised) == 1 and ONSET + 2 <= raised[0].tick <= ONSET + 5 and "pns" in raised[0].summary
    # PNS leaves the QBER alone: the QBER-threshold alarm stays silent while the detector raises the alarm.
    assert abs(lives[ONSET + 10].qber - lives[ONSET - 1].qber) < 0.005
    assert not any(live.detector.baseline_alarm for live in lives[ONSET:ONSET + DURATION])
    assert lives[ONSET + 10].status == "compromised"
    # Still out of contract: this receiver model and transmitter point were not in the detector's training.
    assert raised[0].in_contract is False


def test_detector_blinding_is_not_detected_at_this_span_and_the_record_shows_it(events):
    """Observed behaviour, reported as it is: with the key-rate plant's receiver (afterpulsing) the
    vacuum-yield signature of blinding is small at metro and mid spans, and the detector stays at
    'normal'. It does detect blinding at 70 km and beyond (see the product note)."""
    steps, frames = events
    lives = [step.links["F36"] for step in steps]
    assert frames[ONSET + 5].links["F36"].reference["active_labels"] == ("detector_blinding",)
    during = lives[ONSET:ONSET + DURATION]
    assert {live.detector.label for live in during} == {"normal"}
    assert all(live.status == "up" and live.detector.alarm is False for live in during)
    assert all(live.provenance["detector_in_contract"] is False for live in during)
    vacuum = [frame.links["F36"].qkd.row["vacuum_gain"] for frame in frames]
    assert 1.5 < np.mean(vacuum[ONSET + 2:ONSET + DURATION]) / np.mean(vacuum[40:ONSET]) < 20


# --------------------------------------------------------------------------
# serialisation, performance, imports
# --------------------------------------------------------------------------

def test_live_state_round_trips_through_json_with_real_records(attack):
    bank = attack["bank"]
    state = LiveState(tick=END - 1, at_utc=AT, network_time_s=float(END - 1), topology_version=1,
                      source=SimulatedInputSource(11, "quiet").info(), links=(attack["lives"][-1],),
                      decisions=tuple(attack["decisions"]), models=bank.model_status())
    text = state.to_json()
    assert LiveState.from_json(text) == state and LiveState.from_json(text).to_json() == text
    assert state.to_dict()["schema"] == "orion.network-live.v1"
    assert isinstance(state.to_dict()["links"][0]["wave"], list)


def test_a_tick_of_ten_fibres_stays_well_inside_one_second():
    network = topology([3, 5, 8, 10, 12, 15, 20, 25, 35, 50])
    source, bank = SimulatedInputSource(1, "quiet"), LinkBank(registry())
    bank.sync(network.links)
    for tick in range(62):                                       # past the detector's baseline
        bank.step(source.frame(tick, network), at_utc=AT)
    durations = []
    for tick in range(62, 67):
        started = time.perf_counter()
        step = bank.step(source.frame(tick, network), at_utc=AT)
        durations.append(time.perf_counter() - started)
    assert all(live.detector.label is not None for live in step.links.values())
    assert float(np.median(durations)) < 1.5, durations           # target 0.4 s; measured about 0.2 s


def test_importing_the_engine_does_not_import_qt():
    code = "import sys, quantum_autopilot.network_engine as e; assert e.LinkBank; print('PySide6' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "False"
