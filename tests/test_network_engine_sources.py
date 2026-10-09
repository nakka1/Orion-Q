"""Input sources of the network engine: the only simulated part, and its determinism."""
from dataclasses import replace
import types

import numpy as np
import pytest

from qnet_qkd.qkd import session as author_session

from quantum_autopilot.control_plane import keyrate_loop as kr
from quantum_autopilot.control_plane import polarisation_plant as plant
from quantum_autopilot.control_plane.topology import SimulatedLink
from quantum_autopilot.model_intake import detector_session
from quantum_autopilot.network_engine import sources
from quantum_autopilot.network_engine.sources import (IdleInputSource, InputEvent, InputSource, LinkCommand,
                                                      SimulatedInputSource, TimelineEvent, commission, stream_seed)

pytestmark = pytest.mark.unit


def topology(lengths, *, inactive=()):
    """A minimal topology: fibres ``F<km>`` of the given lengths."""
    links = tuple(SimulatedLink(f"F{length:g}", f"n{index}", f"n{index + 1}", float(length), 0.95,
                                active=f"F{length:g}" not in inactive) for index, length in enumerate(lengths))
    return types.SimpleNamespace(links=links, version=1)


def same_input(first, second):
    return (first.qkd.row == second.qkd.row and first.sync == second.sync
            and np.array_equal(first.polarisation.measured, second.polarisation.measured)
            and np.array_equal(first.polarisation.covariates, second.polarisation.covariates)
            and first.epoch_tick == second.epoch_tick and first.period == second.period)


def test_sources_follow_the_protocol_and_idle_delivers_nothing():
    assert isinstance(SimulatedInputSource(1), InputSource) and isinstance(IdleInputSource(), InputSource)
    idle = IdleInputSource()
    frame = idle.frame(3, topology([10]))
    assert (frame.kind, frame.links, frame.events) == ("none", {}, ())
    assert idle.info().kind == "none"
    with pytest.raises(ValueError):
        idle.inject(InputEvent("loss_event", "F10"))
    assert idle.actuate({"F10": LinkCommand("F10", 1)}) is None


@pytest.mark.parametrize("change", [{"seed": -1}, {"seed": 1.5}, {"scenario": "unknown"}, {"operating_point": "x"},
                                    {"scenario": (TimelineEvent(400, "pns", 0, 0.5, 30.0),)}])
def test_simulated_source_rejects_bad_arguments(change):
    arguments = {"seed": 1, **change}
    with pytest.raises(ValueError):
        SimulatedInputSource(**arguments)


@pytest.mark.parametrize("change", [{"kind": "earthquake"}, {"link_id": ""}, {"strength": 0.0}, {"strength": 1.5},
                                    {"duration_s": 0.5}, {"profile": "square"}, {"strength": float("nan")}])
def test_input_event_validation(change):
    arguments = {"kind": "pns", "link_id": "F10", **change}
    with pytest.raises(ValueError):
        InputEvent(**arguments)


def test_frames_are_deterministic_per_seed_link_and_tick_and_independent_of_other_links():
    together, alone, again = (SimulatedInputSource(5, "demo") for _ in range(3))
    full, single = topology([4, 10, 35]), topology([10])
    reverse = types.SimpleNamespace(links=tuple(reversed(full.links)), version=1)
    other = SimulatedInputSource(6, "demo")
    for tick in range(4):
        a, b, c = together.frame(tick, full), alone.frame(tick, single), again.frame(tick, reverse)
        assert same_input(a.links["F10"], b.links["F10"]) and same_input(a.links["F10"], c.links["F10"])
        assert not same_input(a.links["F10"], other.frame(tick, full).links["F10"])
        assert not same_input(a.links["F10"], a.links["F4"])
    assert together.frame(3, full) is together.frame(3, full)        # the same tick is the same frame


def test_row_is_the_17_public_columns_and_the_monitor_runs_at_the_plant_cadence():
    link = SimulatedInputSource(2, "quiet").frame(0, topology([10])).links["F10"]
    assert tuple(link.qkd.row) == detector_session.COLUMNS
    assert all(np.isfinite(value) for value in link.qkd.row.values())
    assert link.qkd.row["length_km"] == 10.0 and link.distance_km == 10.0
    monitor = link.polarisation
    assert monitor.measured.shape == (500, 3, 3) and monitor.covariates.shape == (500, 3)
    np.testing.assert_allclose(np.diff(monitor.time_s), plant.CONTROL_PERIOD_S, rtol=0, atol=1e-12)
    assert (monitor.control_period_s, monitor.latency_s) == (plant.CONTROL_PERIOD_S, plant.LATENCY_S)
    assert np.allclose(monitor.measured @ np.transpose(monitor.measured, (0, 2, 1)), np.eye(3), atol=1e-9)
    assert (link.sync.update_period_s, link.sync.latency_s) == (1.0, 2.0)


def test_first_segment_of_the_polarisation_stream_is_the_authors_plant_bit_for_bit():
    seed, link_id = 9, "F10"
    link = SimulatedInputSource(seed, "quiet").frame(0, topology([10])).links[link_id]
    reference = plant.simulate(plant.OPERATING_POINTS["calibrado"], 1.0,
                               stream_seed(seed, link_id, 0, "polarisation-plant"), plant.SAMPLE_INTERVAL_S)
    assert np.array_equal(link.reference["true_rotation"], reference.rotation)
    measured = plant.measure_rotation(reference.rotation[::2], np.random.default_rng(
        stream_seed(seed, link_id, 0, "polarisation-monitoring")))
    assert np.array_equal(link.polarisation.measured, measured)
    assert np.array_equal(link.polarisation.covariates, reference.covariates()[::2])


def test_first_episode_of_the_session_misalignment_is_the_authors_series():
    seed, link_id, periods = 4, "F35", int(author_session.SessionConfig().duration_s)
    assert periods == sources.SESSION_SEGMENT_PERIODS == 600
    source, network = SimulatedInputSource(seed, "quiet", session_periods=None), topology([35])
    frames = [source.frame(tick, network).links[link_id] for tick in range(periods)]
    temperature = np.array([item.qkd.row["temperature_c"] for item in frames[:periods]])
    config = replace(author_session.SessionConfig(), link=replace(kr.LINK, length_km=35.0))
    expected = author_session._misalignment_series(
        config, np.arange(periods, dtype=float), temperature,
        np.random.default_rng(stream_seed(seed, link_id, 0, "session-drift")))
    produced = np.array([item.reference["true_misalignment"] for item in frames])
    # The author's gradient is one-sided at the last sample of an episode; the stream knows the next one.
    np.testing.assert_allclose(produced[:periods - 1], expected[:periods - 1], rtol=1e-9, atol=1e-12)
    assert produced[periods - 1] > kr.LINK.misalignment_error          # the uncompensated drift has moved


def test_sessions_are_author_episodes_and_roll_over_with_a_continuous_cable_temperature():
    network = topology([10, 20, 35])
    source = SimulatedInputSource(13, "quiet", session_periods=150)
    first = {link_id: source._first_episode(link_id) for link_id in ("F10", "F20", "F35")}
    assert all(120 <= value <= 150 for value in first.values())
    source.inject(InputEvent("thermal_drift", "F20", 1.0, 20.0))
    source.inject(InputEvent("intercept_resend", "F35", 0.5, 300.0))
    epochs, temperature = {"F10": [], "F20": [], "F35": []}, []
    for tick in range(331):
        frame = source.frame(tick, network)
        for link_id, link in frame.links.items():
            if link.period == 0:
                epochs[link_id].append(tick)
        temperature.append(frame.links["F20"].qkd.row["temperature_c"])
    # Each fibre starts a new session when its (staggered) first episode ends, then every 150 periods.
    assert epochs["F10"] == [0, first["F10"], first["F10"] + 150]
    assert epochs["F20"] == [0, first["F20"], first["F20"] + 150]
    # A rollover waits for an attack in progress to end (injected at tick 0, 300 s long).
    assert epochs["F35"] == [0, 300]
    # The cable keeps its temperature through a rollover, relaxing excursion included.
    assert max(temperature) - temperature[0] > 3.0
    assert np.abs(np.diff(temperature)).max() < 0.5
    rolled = first["F20"]
    assert abs(temperature[rolled] - temperature[rolled - 1]) < 0.2 and temperature[rolled] - temperature[0] > 0.3
    with pytest.raises(ValueError):
        SimulatedInputSource(1, session_periods=100)
    unbounded = SimulatedInputSource(13, "quiet", session_periods=None)
    assert [unbounded.frame(tick, network).links["F10"].period for tick in range(3)] == [0, 1, 2]
    assert unbounded._first_episode("F10") is None


def test_links_appear_disappear_and_are_recommissioned_when_the_length_changes():
    source = SimulatedInputSource(3, "quiet")
    first = source.frame(0, topology([10, 20]))
    assert set(first.links) == {"F10", "F20"} and first.links["F10"].epoch_tick == 0
    assert set(source.frame(1, topology([10, 20], inactive=("F20",))).links) == {"F10"}
    back = source.frame(2, topology([10, 20]))
    assert (back.links["F20"].epoch_tick, back.links["F20"].period) == (2, 0)
    assert (back.links["F10"].epoch_tick, back.links["F10"].period) == (0, 2)
    longer = types.SimpleNamespace(links=(replace(topology([10]).links[0], distance_km=12.5),), version=2)
    changed = source.frame(3, longer).links["F10"]
    assert (changed.distance_km, changed.epoch_tick, changed.period) == (12.5, 3, 0)
    assert changed.qkd.commissioning == commission(12.5)
    # A break in the tick sequence recommissions everything.
    assert source.frame(9, longer).links["F10"].epoch_tick == 9


def test_domain_notes_say_where_each_model_was_validated_and_nothing_is_clamped():
    frame = SimulatedInputSource(1, "quiet").frame(0, topology([7, 50]))
    metro = {note.component: note for note in frame.links["F7"].domain_notes}
    long_haul = {note.component: note for note in frame.links["F50"].domain_notes}
    assert not metro["detector"].in_domain and "outside the trained 20-70 km" in metro["detector"].detail
    assert not metro["keyrate"].in_domain and not metro["sync"].in_domain
    assert long_haul["keyrate"].in_domain and long_haul["sync"].in_domain
    # The detector never saw this receiver model nor the commissioned transmitter point: said, not hidden.
    assert not long_haul["detector"].in_domain and "afterpulsing" in long_haul["detector"].detail
    assert frame.links["F7"].qkd.row["length_km"] == 7.0
    with pytest.raises(ValueError):
        commission(0.0)


def test_commissioning_is_the_authors_procedure_for_the_span():
    record = commission(35.0)
    settings = kr.commissioned(35.0)
    environment = kr.make_link(35.0)
    assert (record.signal_intensity, record.decoy_intensity, record.clock_rate_hz) == (
        settings.signal_intensity, settings.decoy_intensity, settings.clock_rate_hz)
    calibration = kr.calibration(environment)
    assert (record.calibration_gain, record.calibration_rate_hz) == (calibration.calibration_gain,
                                                                     calibration.calibration_rate_hz)
    assert record.to_dict()["length_km"] == 35.0


def test_honest_channel_is_plausible_at_10_km_and_worse_on_a_longer_span():
    frame = SimulatedInputSource(8, "quiet").frame(0, topology([10, 70]))
    near, far = frame.links["F10"].qkd.row, frame.links["F70"].qkd.row
    assert 0.01 < near["signal_error"] < 0.035 and near["key_rate_hz"] > 1e6
    assert far["key_rate_hz"] < 0.5 * near["key_rate_hz"]
    assert frame.links["F70"].qkd.commissioning.timing_spread_s > frame.links["F10"].qkd.commissioning.timing_spread_s


def test_injected_events_change_the_input_and_report_their_phases():
    network = topology([10, 20])
    source, quiet = SimulatedInputSource(12, "quiet"), SimulatedInputSource(12, "quiet")
    for tick in range(3):
        source.frame(tick, network)
        quiet.frame(tick, network)
    with pytest.raises(ValueError):
        source.inject(InputEvent("pns", "missing"))
    with pytest.raises(ValueError):
        source.inject("pns")
    source.inject(InputEvent("intercept_resend", "F10", 1.0, 3.0))
    source.inject(InputEvent("loss_event", "F20", 1.0, 3.0))
    phases = []
    for tick in range(3, 8):
        attacked, honest = source.frame(tick, network), quiet.frame(tick, network)
        phases.append(tuple(sorted((event.link_id, event.phase) for event in attacked.events)))
        active = tick < 6
        error = attacked.links["F10"].qkd.row["signal_error"] - honest.links["F10"].qkd.row["signal_error"]
        gain = attacked.links["F20"].qkd.row["signal_gain"] / honest.links["F20"].qkd.row["signal_gain"]
        # 5 dB of extra loss; the gain falls less than 5 dB because the detector is partly saturated.
        assert (error > 0.05) == active and (gain < 0.8) == active
        assert attacked.links["F10"].reference["active_labels"] == (("intercept_resend",) if active else ())
        assert attacked.links["F20"].reference["true_excess_loss_db"] == pytest.approx(6.0 if active else 1.0)
    assert phases[0] == (("F10", "started"), ("F20", "started"))
    assert phases[1] == phases[2] == (("F10", "active"), ("F20", "active"))
    assert phases[3] == (("F10", "ended"), ("F20", "ended")) and phases[4] == ()
    event = source.frame(3, network)
    assert event is not None and source.info().scenario == "quiet"


def test_thermal_event_reaches_the_thermometer_the_clock_and_the_polarisation_monitor():
    network = topology([20])
    source, quiet = SimulatedInputSource(21, "quiet"), SimulatedInputSource(21, "quiet")
    source.frame(0, network)
    quiet.frame(0, network)
    source.inject(InputEvent("thermal_drift", "F20", 1.0, 30.0))
    for tick in range(1, 25):
        hot, cold = source.frame(tick, network).links["F20"], quiet.frame(tick, network).links["F20"]
    assert hot.qkd.row["temperature_c"] - cold.qkd.row["temperature_c"] > 3.0
    assert hot.polarisation.covariates[:, 0].mean() - cold.polarisation.covariates[:, 0].mean() > 3.0
    assert hot.polarisation.covariates[:, 1].mean() > cold.polarisation.covariates[:, 1].mean() + 0.05     # dT/dt
    # 14 K rising with a 60 s time constant over 20 km: tens of picoseconds of thermal delay.
    assert hot.reference["true_offset_s"] - cold.reference["true_offset_s"] > 20e-12


def test_demo_timeline_assigns_events_by_rank_for_any_topology_and_repeats():
    assert [entry.kind for entry in sources.DEMO_TIMELINE] == [
        "thermal_drift", "loss_event", "intercept_resend", "pns", "detector_blinding"]
    assert all(entry.at_s >= sources.BASELINE_PERIODS and entry.at_s + entry.duration_s <= sources.DEMO_LOOP_S
               for entry in sources.DEMO_TIMELINE)
    for lengths in ([10], [5, 10, 15]):
        source, network = SimulatedInputSource(7, "demo", session_periods=None), topology(lengths)
        ranked = sources.link_rank_order(7, [link.link_id for link in network.links])
        started = {}
        for tick in range(sources.DEMO_LOOP_S + 70):
            for event in source.frame(tick, network).events:
                if event.phase == "started":
                    started[tick] = (event.kind, event.link_id, event.origin)
        expected = {entry.at_s: (entry.kind, ranked[entry.rank % len(ranked)], "timeline")
                    for entry in sources.DEMO_TIMELINE}
        expected[sources.DEMO_LOOP_S + 65] = expected[65]             # the loop repeats
        assert started == expected
    # With session episodes an event that falls into a fibre's new baseline waits for it; none is lost.
    source, network = SimulatedInputSource(7, "demo", session_periods=130), topology([10])
    assert source._first_episode("F10") <= 130
    kinds = []
    for tick in range(sources.DEMO_LOOP_S):
        frame = source.frame(tick, network)
        kinds.extend(event.kind for event in frame.events if event.phase == "started")
        if frame.links["F10"].period < sources.BASELINE_PERIODS:
            assert frame.links["F10"].reference["active_labels"] == ()
    assert kinds == [entry.kind for entry in sources.DEMO_TIMELINE]


def test_transmitter_commands_take_effect_in_the_next_period_and_recommission_restarts_the_session():
    source, network = SimulatedInputSource(2, "quiet"), topology([50])
    first = source.frame(0, network).links["F50"]
    environment = kr.make_link(50.0)
    expected = environment.apply(1)                                   # mu +25 %
    source.actuate({"F50": LinkCommand("F50", keyrate_action=1), "gone": LinkCommand("gone", 2)})
    second = source.frame(1, network).links["F50"]
    assert first.qkd.row["signal_intensity"] == environment.settings.signal_intensity
    assert second.qkd.row["signal_intensity"] == expected.signal_intensity
    assert second.qkd.row["expected_gain"] > first.qkd.row["expected_gain"]
    source.actuate({"F50": LinkCommand("F50", recommission=True)})
    third = source.frame(2, network).links["F50"]
    assert (third.epoch_tick, third.period) == (2, 0)
    assert third.qkd.row["signal_intensity"] == first.qkd.row["signal_intensity"]
