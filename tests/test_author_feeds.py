"""Shadow producers are the author's plants: same simulation, same measurement, deterministic."""
import numpy as np
import pytest

from quantum_autopilot.control_plane import author_feeds as feeds
from quantum_autopilot.control_plane import keyrate_loop as kr
from quantum_autopilot.control_plane import polarisation_loop as loop
from quantum_autopilot.control_plane import polarisation_plant as plant

pytestmark = pytest.mark.unit


def test_polarisation_segment_is_the_plant_as_the_compensator_sees_it():
    segment = feeds.polarisation_segment("calibrado", 1337, 0)
    trajectory = plant.simulate(plant.OPERATING_POINTS["calibrado"], feeds.POLARISATION_SEGMENT_S, segment.plant_seed)
    indices = np.arange(0, len(trajectory.time_s), feeds.POLARISATION_STRIDE)
    expected = plant.measure_rotation(trajectory.rotation[indices], np.random.default_rng(segment.monitoring_seed))
    assert np.array_equal(segment.measured, expected) and segment.updates == 15000
    assert np.array_equal(segment.covariates, trajectory.covariates()[indices])
    assert all(loop.is_rotation(matrix) for matrix in segment.measured[:50])
    # Photon noise is present: estimates differ from the true rotation.
    assert not np.allclose(segment.measured[:100], trajectory.rotation[indices][:100])
    assert feeds.polarisation_segment("calibrado", 1337, 1).plant_seed != segment.plant_seed
    assert feeds.polarisation_segment("desenvolvimento", 1337, 0).plant_seed != segment.plant_seed


def test_clock_segment_uses_the_loop_measurement():
    from qnet_qkd.qkd.timing import ClockModel, MeasurementConfig, measure_offset, timing_spread

    segment = feeds.clock_segment(1337, 0)
    path = ClockModel(length_km=50.0).simulate(3600.0, 1.0, rng=segment.trajectory_seed)
    expected = measure_offset(path.offset_s, MeasurementConfig(photons=2000, spread_s=timing_spread(kr.BUDGET, 50.0)),
                              rng=segment.measurement_seed)
    assert np.array_equal(segment.measured_offset_s, expected) and len(expected) == feeds.CLOCK_SEGMENT_S
    assert np.array_equal(segment.temperature_c, path.temperature_c)


def test_keyrate_episode_holds_commissioned_settings():
    episode = feeds.KeyRateEpisode(1337, 1)
    observed, settings, calibration = episode.publish()
    assert observed is None and settings.signal_intensity == kr.commissioned(50.0).signal_intensity
    reference = kr.make_link(50.0)
    reference.reset(rng=episode.plant_seed)
    for _ in range(3):
        episode.advance()
        _, _, _, info = reference.step(0)
    observed, settings, _ = episode.publish()
    assert observed.key_rate_hz == info["key_rate_hz"] and episode.periods == 3 and not episode.finished
    assert settings == kr.transmitter(reference)


def test_allocation_episode_is_driven_by_quantum_first():
    episode = feeds.AllocationEpisode(1337, 1)
    first = episode.observation.pending_admission.copy()
    episode.advance()
    episode.advance()
    queue = episode.observation.pending_admission
    assert np.array_equal(queue[:-2], first[2:])
    assert set(np.unique(queue[-2:])) <= {0.0, 1.0} and episode.steps == 2 and not episode.done


def test_routing_windows_carry_the_environment_features_and_validate():
    from quantum_autopilot.console.synthetic import artificial_snapshot
    from quantum_autopilot.console.synthetic_routing import SyntheticRoutingStream
    from quantum_autopilot.control_plane.optional_routing import TELEMETRY_KEY, RoutingShadowWindow
    from quantum_autopilot.model_intake.routing_session import ACTION_NAMES, STATE_NAMES

    stream = SyntheticRoutingStream()
    kinds = set()
    for tick in range(80):  # several episodes, with pairs, swaps and deliveries
        episodes = stream.episodes
        state = stream.push(artificial_snapshot(tick=tick), 1337, tick)
        window = RoutingShadowWindow.from_dict(state.telemetry[TELEMETRY_KEY])  # validates the resources
        value = window.to_dict()
        observed_state, observed_features, actions = stream.episodes.observation
        assert [value["state_features"][name] for name in STATE_NAMES] == [float(item) for item in observed_state]
        assert [[candidate["features"][name] for name in ACTION_NAMES] for candidate in value["candidates"]] == \
            [[float(item) for item in row] for row in observed_features]
        kinds |= {candidate["action"]["kind"] for candidate in value["candidates"]}
        assert episodes is None or stream.episodes is episodes
    assert {"GENERATE", "SWAP", "WAIT"} <= kinds and stream.episodes.episode >= 1
