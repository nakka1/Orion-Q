"""Fixed-plant allocator, optional selection and queue semantics without external files."""
from copy import deepcopy
from dataclasses import replace
import io
import json

import numpy as np
import pytest

from quantum_autopilot.model_intake import artifacts, catalog
from quantum_autopilot.model_intake.allocation import (
    AllocationObservation, AllocationReplayModel, COEXISTENCE, LEVELS,
)
from quantum_autopilot.model_intake.artifacts import ReviewedArchive

pytestmark = pytest.mark.unit


class SyntheticPlant:
    kind = "qnet"
    sha256 = "synthetic-unit-fixture"

    def __init__(self, variant="persistence"):
        self.plant = {
            "config": {"control_period_s": 5., "reconfiguration_latency_s": 30., "latency_steps": 6,
                       "admission_levels": list(LEVELS), "estimation_fraction": .05, "coexistence_explicit": False},
            "coexistence": deepcopy(COEXISTENCE),
            "links": [{"index": i, "name": f"link-{i}", "length_km": 50., "sifted_rate_hz": 1000.,
                       "signal_counts_per_gate": .01, "classical_power_full_w": .0003} for i in range(11)],
        }
        self.meta = {"coverage_target": 1., "idle_level": 1., "horizon_cap_s": 150.,
                     "forecaster": {"kind": variant, "horizon": 30, "window": 6, "coefficients_shape": [7, 30]}}
        self.coefficients = np.zeros((7, 30))
        self.coefficients[-1] = .02

    def json(self, name):
        return deepcopy(self.plant if name.endswith("plant.json") else self.meta)

    def read(self, name):
        return json.dumps(self.json(name)).encode()

    def arrays(self, name):
        assert self.meta["forecaster"]["kind"] == "ridge", "persistence must not load learned weights"
        return {"coefficients": self.coefficients.copy()}


def observation(time=0., **changes):
    values = {"time_s": time, "measured_qber": (.01,) * 11, "raman_qber_estimate": (0.,)*11,
              "pending_admission": tuple((1.,)*11 for _ in range(6)), "key_pool_bits": (0.,)*11,
              "backlog_bits": (0.,)*11, "urgency_s": (None,)*11}
    values.update(changes)
    return AllocationObservation(**values)


def recommend(model, obs=None, **changes):
    kwargs = {"domain": "simulated_offline", "protocol": "single_photon_bb84",
              "link_names": model.link_names, "stream_id": "campaign/plant"}
    kwargs.update(changes)
    return model.recommend(observation() if obs is None else obs, **kwargs)


def simple_budget(_link, drift, level):
    # Independent hand-checkable plant: 10 bits/s at no admission, zero at full.
    return float(np.sum(np.ones(len(drift)) * (1. - np.asarray(level)) * 10.))


def test_persistence_idle_and_provenance():
    model = AllocationReplayModel(SyntheticPlant(), "persistence")
    result = recommend(model)
    assert result.admission == (1.,)*11
    assert result.forecast_key_bits == (None,)*11
    assert np.allclose(result.forecast, .005)
    assert result.status == "persistence" and result.effective_at_s == 30.
    assert result.key_accounting == "asymptotic_forecast_not_certified"
    assert not result.physical_action_performed and len(result.plant_sha256) == 64


def test_ridge_five_steps_warmup_then_model_and_reset():
    model = AllocationReplayModel(SyntheticPlant("ridge"), "ridge")
    for i in range(5):
        result = recommend(model, observation(5.*i))
        assert result.status == "warmup_persistence" and np.allclose(result.forecast, .005)
    result = recommend(model, observation(25.))
    assert result.status == "ridge" and np.allclose(result.forecast, .02)
    model.reset()
    assert recommend(model, observation(0.), stream_id="new").status == "warmup_persistence"


def test_history_order_and_ridge_nonnegative_clipping():
    archive = SyntheticPlant("ridge")
    archive.coefficients[:] = 0.
    archive.coefficients[0, 0] = 1.
    archive.coefficients[5, 1] = 1.
    archive.coefficients[-1, 2] = -1.
    model = AllocationReplayModel(archive, "ridge")
    for i in range(6):
        result = recommend(model, observation(i*5., measured_qber=(.01+i*.01,)*11))
    assert result.forecast[0][:3] == pytest.approx((.005, .055, 0.))


def test_subtract_own_raman_and_optical_error():
    model = AllocationReplayModel(SyntheticPlant(), "persistence")
    result = recommend(model, observation(measured_qber=(.02,)*11, raman_qber_estimate=(.007,)*11))
    assert result.drift == pytest.approx((.008,)*11)
    result = recommend(model, observation(5., measured_qber=(.001,)*11, raman_qber_estimate=(.02,)*11))
    assert result.drift == (0.,)*11


@pytest.mark.parametrize("backlog,pool,urgency,expected", [
    (500., 0., None, .5), (1000., 500., None, .5), (1000., 0., 300., .5),
    (1500., 0., None, 0.), (500., 500., None, 1.), (100., 500., None, 1.),
])
def test_admission_law_debt_and_pacing(monkeypatch, backlog, pool, urgency, expected):
    model = AllocationReplayModel(SyntheticPlant(), "persistence")
    monkeypatch.setattr(model, "_key_over", simple_budget)
    result = recommend(model, observation(backlog_bits=(backlog,)*11, key_pool_bits=(pool,)*11,
                                          urgency_s=(urgency,)*11))
    assert result.admission == (expected,)*11
    if backlog == 1500.:
        assert result.model_coverage_met == (False,)*11
        assert result.forecast_key_bits == (1200.,)*11


def test_deadline_inside_latency_reads_oldest_committed_slot(monkeypatch):
    model = AllocationReplayModel(SyntheticPlant(), "persistence")
    monkeypatch.setattr(model, "_key_over", simple_budget)
    pending = np.ones((6, 11))
    pending[0] = 0.
    obs = observation(pending_admission=pending, backlog_bits=(25.,)*11, urgency_s=(5.,)*11)
    result = recommend(model, obs)
    assert result.admission == (1.,)*11 and result.forecast_key_bits == (50.,)*11
    assert np.array_equal(pending[0], np.zeros(11))  # Queue is read, never rewritten.
    model.reset()
    result = recommend(model, replace(obs, pending_admission=pending[::-1]))
    assert result.admission == (0.,)*11 and not any(result.model_coverage_met)


def test_internal_key_formula_and_raman_monotonicity():
    model = AllocationReplayModel(SyntheticPlant(), "persistence")
    qber = (.005*.01 + .5e-8)/(.01+1e-8)
    entropy = -qber*np.log2(qber)-(1-qber)*np.log2(1-qber)
    expected = 1000.*.95*(1.-1.16*entropy-entropy)
    assert model._key_over(0, np.array([0.]), 0.) == pytest.approx(expected, abs=1e-12)
    rates = [model._key_over(0, np.zeros(2), level) for level in LEVELS]
    assert all(a >= b for a, b in zip(rates, rates[1:]))
    assert model._key_over(0, np.zeros(0), 1.) == 0.


@pytest.mark.parametrize("field,value", [
    ("measured_qber", (np.nan,)*11), ("measured_qber", (1.01,)*11), ("measured_qber", (-.1,)*11),
    ("raman_qber_estimate", (np.inf,)*11), ("backlog_bits", (-1.,)*11),
    ("key_pool_bits", (np.inf,)*11), ("key_pool_bits", (1.,)*10),
    ("pending_admission", np.ones((5, 11))), ("pending_admission", np.full((6, 11), .1)),
    ("urgency_s", (0.,)*11), ("urgency_s", (-1.,)*11), ("urgency_s", (np.nan,)*11),
    ("urgency_s", (-np.inf,)*11), ("urgency_s", (True,)*11), ("urgency_s", ("100",)*11),
    ("urgency_s", np.array(5.)), ("time_s", np.inf), ("time_s", -5.),
])
def test_invalid_observation_does_not_fill_warmup(field, value):
    model = AllocationReplayModel(SyntheticPlant("ridge"), "ridge")
    with pytest.raises(ValueError):
        recommend(model, observation(**{field: value}))
    for i in range(5):
        assert recommend(model, observation(i*5.)).status == "warmup_persistence"


@pytest.mark.parametrize("override", [
    {"domain": "measured"}, {"protocol": "decoy_state_bb84"}, {"stream_id": ""},
    {"stream_id": "x"*257}, {"link_names": ()}, {"link_names": tuple(f"link-{i}" for i in reversed(range(11)))},
])
def test_wrong_domain_identity_protocol(override):
    with pytest.raises(ValueError):
        recommend(AllocationReplayModel(SyntheticPlant(), "persistence"), **override)


def test_stream_switch_and_gap_need_explicit_reset():
    model = AllocationReplayModel(SyntheticPlant("ridge"), "ridge")
    recommend(model)
    for time in (0., 10., -5.):
        with pytest.raises(ValueError):
            recommend(model, observation(time))
    with pytest.raises(ValueError):
        recommend(model, observation(5.), stream_id="another")
    assert recommend(model, observation(5.)).status == "warmup_persistence"
    model.reset()
    assert recommend(model, observation(100.), stream_id="another").status == "warmup_persistence"


def test_stateless_snapshot_preserves_stream_and_checks_cadence():
    model = AllocationReplayModel(SyntheticPlant("ridge"), "ridge")
    kwargs = {"drift_history": np.full((5, 11), .01), "history_times_s": np.arange(5)*5.,
              "domain": "simulated_offline", "protocol": "single_photon_bb84", "link_names": model.link_names}
    assert model.recommend_snapshot(observation(25.), **kwargs).status == "ridge"
    assert recommend(model).status == "warmup_persistence"
    kwargs["history_times_s"][-1] = 19.
    with pytest.raises(ValueError):
        model.recommend_snapshot(observation(25.), **kwargs)


def test_failed_calculation_does_not_advance_state(monkeypatch):
    model = AllocationReplayModel(SyntheticPlant("ridge"), "ridge")
    for i in range(5):
        recommend(model, observation(i*5.))
    with monkeypatch.context() as patch:
        patch.setattr(model, "_coefficients", np.full((7, 30), np.inf))
        with pytest.raises((ValueError, FloatingPointError)):
            recommend(model, observation(25.))
    assert recommend(model, observation(25.)).status == "ridge"


@pytest.mark.parametrize("change", ["latency", "levels", "physics", "order", "duplicate", "profile", "coverage", "coefficients"])
def test_metadata_and_weights_contract(change):
    archive = SyntheticPlant("ridge")
    if change == "latency":
        archive.plant["config"]["latency_steps"] = 0
    elif change == "levels":
        archive.plant["config"]["admission_levels"] = [0., 1.]
    elif change == "physics":
        archive.plant["coexistence"]["optical_error_rate"] = .01
    elif change == "order":
        archive.plant["links"].reverse()
    elif change == "duplicate":
        archive.plant["links"][1]["name"] = "link-0"
    elif change == "profile":
        archive.plant["links"][0]["length_km"] = np.nan
    elif change == "coverage":
        archive.meta["coverage_target"] = .5
    else:
        archive.coefficients = np.zeros((6, 30))
    with pytest.raises(ValueError):
        AllocationReplayModel(archive, "ridge")


def test_infinity_requires_explicit_field_allowlist(monkeypatch):
    def content(value):
        stream = io.BytesIO()
        np.savez(stream, urgency_s=np.array(value))
        return stream.getvalue()
    archive = object.__new__(ReviewedArchive)
    monkeypatch.setattr(archive, "read", lambda _: content([5., np.inf]))
    with pytest.raises(ValueError):
        archive.arrays("stream.npz")
    assert np.isposinf(archive.arrays("stream.npz", positive_infinity_keys=frozenset({"urgency_s"}))["urgency_s"][1])
    for invalid in ([np.nan], [-np.inf], [True], ["inf"]):
        monkeypatch.setattr(archive, "read", lambda _, v=invalid: content(v))
        with pytest.raises(ValueError):
            archive.arrays("stream.npz", positive_infinity_keys=frozenset({"urgency_s"}))


@pytest.mark.parametrize("variant", ["persistence", "ridge"])
def test_optional_selection_is_off_and_explicit(monkeypatch, variant):
    calls = []
    def load(path, kind):
        calls.append((path, kind))
        return SyntheticPlant(variant)
    monkeypatch.setattr(artifacts, "ReviewedArchive", load)
    assert catalog.open_optional_model() is None and not calls
    model = catalog.open_optional_model(f"qnet.allocator.{variant}", archive_path="chosen.zip")
    assert isinstance(model, AllocationReplayModel) and calls == [("chosen.zip", "qnet")]
    assert not any(o.default_enabled or o.runtime_available for o in catalog.model_options())
