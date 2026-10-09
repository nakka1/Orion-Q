"""Independent feature, action-box, optionality and malformed-input tests."""
from copy import deepcopy
from dataclasses import asdict, replace
import json

import numpy as np
import pytest

from quantum_autopilot.model_intake import artifacts, catalog
from quantum_autopilot.model_intake.keyrate import (
    ACTIONS, BOX, FEATURE_NAMES, KeyRateReplayModel, LinkCalibration,
    PeriodObservables, TransmitterSettings, build_observation,
)
from quantum_autopilot.model_intake.keyrate_evaluation import evaluate_keyrate

pytestmark = pytest.mark.unit

SETTINGS = TransmitterSettings(.5, .1, .1, .7, 1e9)
CALIBRATION = LinkCalibration(.01, 1e6, 1e-8, .02, 50e-9)
OBSERVABLES = PeriodObservables(.01, .002, 1e-8, .02, .03, .01, .04, 1e6)


class SyntheticQKD:
    kind = "qkd"
    sha256 = "synthetic-for-tests-only"

    def __init__(self):
        self.meta = {"temperature": .25, "actions": list(ACTIONS), "observation": list(FEATURE_NAMES),
                     "box": deepcopy(BOX), "dead_time_s": 50e-9}
        self.data = {"normaliser_count": np.array(20.), "normaliser_mean": np.zeros(16),
                     "normaliser_m2": np.full(16, 20.)}
        for role, sizes in (("actor", (16, 64, 64, 9)), ("critic", (16, 64, 64, 1))):
            for i, (a, b) in enumerate(zip(sizes[:-1], sizes[1:])):
                self.data[f"{role}_{2*i}"] = np.zeros((a, b))
                self.data[f"{role}_{2*i+1}"] = np.zeros(b)

    def json(self, _name):
        return deepcopy(self.meta)

    def arrays(self, _name):
        return {k: v.copy() for k, v in self.data.items()}


def recommend(model, **overrides):
    args = {"previous": OBSERVABLES, "settings": SETTINGS, "calibration": CALIBRATION,
            "domain": "simulated_offline", "link_span_km": 50., "protocol": "decoy_state_bb84",
            "rng": np.random.default_rng(123)}
    args.update(overrides)
    return model.recommend(**args)


def test_initial_features_match_hand_calculation():
    got = build_observation(None, SETTINGS, CALIBRATION)
    expected = [0., 0., 0., 2., 2., 0., 2., 0., .5, .2, 0., .7, np.log(.5), 0., np.log(.01), 0.]
    np.testing.assert_allclose(got, expected, rtol=0, atol=1e-14)


def test_observed_features_match_hand_calculation():
    got = build_observation(OBSERVABLES, SETTINGS, CALIBRATION)
    expected = [0., np.log(.2), 0., 2., 3., 0., 4., 0., .5, .2, 0., .7, np.log(.5), 0., np.log(.01), 0.]
    np.testing.assert_allclose(got, expected, rtol=0, atol=1e-14)


def test_zero_rate_preserves_distinct_original_floors():
    got = build_observation(replace(OBSERVABLES, key_rate_hz=0.), SETTINGS, CALIBRATION)
    assert got[7] == pytest.approx(np.log(1e-12))
    assert got[13] == pytest.approx(np.log(1e-18))
    assert got[7] != got[13]
    assert np.isfinite(got).all()


def test_normalisation_network_path_and_temperature():
    archive = SyntheticQKD()
    archive.data["normaliser_mean"][8] = .1
    archive.data["normaliser_m2"][8] = 80.  # std = sqrt(80/20) = 2
    archive.data["actor_0"][8, 0] = 1.
    archive.data["actor_2"][0, 0] = 1.
    archive.data["actor_4"][0, 1] = 1.
    result = recommend(KeyRateReplayModel(archive))
    assert result.logits[1] == pytest.approx(.2)
    weights = np.ones(9)
    weights[1] = np.exp(.2/.25)
    np.testing.assert_allclose(result.probabilities, weights/weights.sum(), rtol=0, atol=1e-15)
    assert result.greedy_index == 1


@pytest.mark.parametrize("index,field,expected", [
    (0, "signal_intensity", .5), (1, "signal_intensity", .625), (2, "signal_intensity", .4),
    (3, "decoy_intensity", .125), (4, "decoy_intensity", .08), (5, "clock_rate_hz", 1.5e9),
    (6, "clock_rate_hz", .67e9), (7, "signal_probability", .75), (8, "signal_probability", .65),
])
def test_all_nine_action_previews(index, field, expected):
    result = KeyRateReplayModel(SyntheticQKD()).preview_action(SETTINGS, index, domain="simulated_offline")
    assert getattr(result, field) == pytest.approx(expected)
    assert result.vacuum_probability == SETTINGS.vacuum_probability
    assert SETTINGS.signal_intensity == .5  # Input has not been modified.


@pytest.mark.parametrize("settings,index,field,expected", [
    (replace(SETTINGS, signal_intensity=1.), 1, "signal_intensity", 1.),
    (replace(SETTINGS, signal_intensity=.05, decoy_intensity=.01), 2, "signal_intensity", .05),
    (replace(SETTINGS, signal_intensity=.05, decoy_intensity=.03), 3, "decoy_intensity", .03),
    (replace(SETTINGS, decoy_intensity=.01), 4, "decoy_intensity", .01),
    (replace(SETTINGS, signal_intensity=1., decoy_intensity=.4), 3, "decoy_intensity", .4),
    (replace(SETTINGS, clock_rate_hz=5e9), 5, "clock_rate_hz", 5e9),
    (replace(SETTINGS, clock_rate_hz=1e8), 6, "clock_rate_hz", 1e8),
    (replace(SETTINGS, signal_probability=.85), 7, "signal_probability", .85),
    (replace(SETTINGS, signal_probability=.4), 8, "signal_probability", .4),
    (replace(SETTINGS, vacuum_probability=.2, signal_probability=.75), 7, "signal_probability", .75),
    (replace(SETTINGS, signal_intensity=.5, decoy_intensity=.3), 2, "decoy_intensity", .24),
])
def test_coupled_action_limits(settings, index, field, expected):
    result = KeyRateReplayModel(SyntheticQKD()).preview_action(settings, index, domain="simulated_offline")
    assert getattr(result, field) == pytest.approx(expected)


@pytest.mark.parametrize("index", [-1, 9, True, 1.0, "1", np.nan])
def test_invalid_action_indices(index):
    with pytest.raises(ValueError):
        KeyRateReplayModel(SyntheticQKD()).preview_action(SETTINGS, index, domain="simulated_offline")


@pytest.mark.parametrize("overrides", [
    {"domain": "measured"}, {"link_span_km": 24.}, {"link_span_km": 81.}, {"link_span_km": np.nan},
    {"protocol": "single_photon_bb84"}, {"rng": None},
    {"settings": replace(SETTINGS, clock_rate_hz=np.inf)},
    {"settings": replace(SETTINGS, signal_intensity=True)},
    {"settings": replace(SETTINGS, signal_probability=.9)},
    {"settings": replace(SETTINGS, vacuum_probability=.4)},
    {"settings": replace(SETTINGS, decoy_intensity=.4)},
    {"settings": replace(SETTINGS, signal_intensity="0.5")},
    {"calibration": replace(CALIBRATION, calibration_gain=0)},
    {"calibration": replace(CALIBRATION, calibration_rate_hz=0)},
    {"calibration": replace(CALIBRATION, dark_count_probability=0)},
    {"calibration": replace(CALIBRATION, dead_time_s=1e-8)},
    {"previous": replace(OBSERVABLES, signal_error=2.)},
    {"previous": replace(OBSERVABLES, decoy_gain=-.1)},
    {"previous": replace(OBSERVABLES, key_rate_hz=-1.)},
    {"previous": replace(OBSERVABLES, vacuum_gain=np.nan)},
    {"previous": {}}, {"settings": {}}, {"calibration": {}},
])
def test_incompatible_inputs_fail_without_rng_consumption(overrides):
    rng = np.random.default_rng(12)
    before = json.dumps(rng.bit_generator.state)
    options = {"rng": rng, **overrides}
    with pytest.raises(ValueError):
        recommend(KeyRateReplayModel(SyntheticQKD()), **options)
    assert json.dumps(rng.bit_generator.state) == before


def test_sampling_explicit_seed_and_readonly_recommendation():
    model = KeyRateReplayModel(SyntheticQKD())
    rng, expected = np.random.default_rng(22), np.random.default_rng(22)
    for _ in range(12):
        result = recommend(model, rng=rng)
        index = int(expected.choice(9, p=np.full(9, 1/9)))
        assert result.sampled_index == index and result.action_name == ACTIONS[index]
        assert result.greedy_index == 0 and result.temperature == .25
        assert result.proposed_settings == model.preview_action(SETTINGS, index, domain="simulated_offline")
        assert not result.physical_action_performed


def test_degenerate_distribution_does_not_consume_rng():
    archive = SyntheticQKD()
    archive.data["actor_5"][2] = 1e3
    rng = np.random.default_rng(12)
    before = json.dumps(rng.bit_generator.state)
    result = recommend(KeyRateReplayModel(archive), rng=rng)
    assert result.sampled_index == 2 and result.probabilities[2] == 1.
    assert json.dumps(rng.bit_generator.state) == before


def test_numerical_failure_does_not_consume_rng():
    archive = SyntheticQKD()
    archive.data["actor_0"][:] = 1e308
    rng = np.random.default_rng(12)
    before = json.dumps(rng.bit_generator.state)
    with pytest.raises(FloatingPointError):
        recommend(KeyRateReplayModel(archive), rng=rng)
    assert json.dumps(rng.bit_generator.state) == before


def test_normaliser_variance_floor_is_preserved():
    archive = SyntheticQKD()
    archive.data["normaliser_m2"][:] = 0.
    archive.data["actor_0"][8, 0] = 1.
    archive.data["actor_2"][0, 0] = 1.
    archive.data["actor_4"][0, 1] = 1.
    result = recommend(KeyRateReplayModel(archive))
    assert result.logits[1] == pytest.approx(.5/1e-4)


@pytest.mark.parametrize("change", ["actions", "features", "box", "temperature", "count", "m2", "weight", "missing"])
def test_checkpoint_contract(change):
    archive = SyntheticQKD()
    if change == "actions":
        archive.meta["actions"].reverse()
    elif change == "features":
        archive.meta["observation"].reverse()
    elif change == "box":
        archive.meta["box"]["clock_range"][1] = 1e10
    elif change == "temperature":
        archive.meta["temperature"] = 0
    elif change == "count":
        archive.data["normaliser_count"] = np.array(0.)
    elif change == "m2":
        archive.data["normaliser_m2"][0] = -1.
    elif change == "weight":
        archive.data["actor_0"] = np.zeros((17, 64))
    else:
        archive.data.pop("actor_5")
    with pytest.raises(ValueError):
        KeyRateReplayModel(archive)


def test_optional_factory_off_default_and_explicit_actor(monkeypatch):
    selected = []
    def open_archive(path, kind):
        selected.append((path, kind))
        return SyntheticQKD()
    monkeypatch.setattr(artifacts, "ReviewedArchive", open_archive)
    assert catalog.open_optional_model() is None and not selected
    model = catalog.open_optional_model("qkd.keyrate.actor", archive_path="chosen.zip")
    assert isinstance(model, KeyRateReplayModel)
    assert selected == [("chosen.zip", "qkd")]
    option = next(o for o in catalog.model_options() if o.model_id == "qkd.keyrate.actor")
    assert option.status == "offline_only" and not option.default_enabled and not option.runtime_available


@pytest.mark.parametrize("corrupt", [None, "probabilities", "settings"])
def test_evaluation_checks_all_periods_and_preserves_sampling_limitation(corrupt):
    archive = SyntheticQKD()
    original = archive.json
    expected_observation = [0., 0., 0., 2., 2., 0., 2., 0., .5, .2, 0., .7, np.log(.5), 0., np.log(.01), 0.]
    episode = {"vão_km": 50., "temperatura": .25, "calibração_do_enlace": asdict(CALIBRATION),
               "periodos": [{"periodo": i, "entrada": {"observaveis_do_periodo_anterior": None,
                                                        "parametros_atuais": asdict(SETTINGS)},
                             "observacao_16": expected_observation,
                             "saida": {"probabilidades": dict.fromkeys(ACTIONS, 1/9), "acao_gulosa": ACTIONS[0],
                                       "acao_amostrada_T=0.25": ACTIONS[0], "novos_parametros": asdict(SETTINGS)}}
                            for i in range(40)]}
    if corrupt == "probabilities":
        episode["periodos"][-1]["saida"]["probabilidades"][ACTIONS[0]] = .5
    elif corrupt == "settings":
        episode["periodos"][-1]["saida"]["novos_parametros"]["clock_rate_hz"] = 4e9
    archive.json = lambda name: episode if name.endswith("agente_episodio_50km.json") else original(name)
    report = evaluate_keyrate(archive)
    assert report["periods"] == 40 and report["passed"] == (corrupt is None)
    assert report["sampled_action_parity_checked"] is False
