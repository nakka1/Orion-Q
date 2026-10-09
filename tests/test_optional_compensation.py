"""Optional model admission and stateful compensation, with synthetic weights."""
from copy import deepcopy
import io
import json
import subprocess
import sys

import numpy as np
import pytest

from quantum_autopilot.model_intake import catalog
from quantum_autopilot.model_intake.artifacts import ReviewedArchive
from quantum_autopilot.model_intake.compensation import (
    CompensationReplayModel, _exp_map, _log_map, _rotation,
)

pytestmark = pytest.mark.unit


class SyntheticArchive:
    kind = "qnet"
    sha256 = "synthetic-not-a-reviewed-production-export"

    def __init__(self, architecture="ridge", point="calibrado"):
        self.meta = {
            "kind": architecture,
            "spec": {"window": 16, "use_covariates": True, "horizon_scale": 0.002,
                     "lags": None, "tone_lags": 0},
            "operating_point": {
                "control_period_s": 0.002, "horizon_s": 0.04, "monitoring_photons_per_basis": 400,
                "fibre": {"vibration_frequency_hz": 60 if point == "calibrado" else 40,
                          "thermal_drift_rate_rad_per_s": 0.02 if point == "calibrado" else 0.05}},
            "layers": 3, "sizes": [48, 192, 192, 3],
        }
        self.data = {"standardiser_mean": np.zeros(48), "standardiser_scale": np.ones(48)}
        if architecture == "ridge":
            self.data.update(coef=np.zeros((3, 48)), intercept=np.array([0., 0., 10.]))
        else:
            for i, (a, b) in enumerate(zip((48, 192, 192), (192, 192, 3))):
                self.data[f"weight_{i}"] = np.zeros((a, b))
                self.data[f"bias_{i}"] = np.zeros(b)
            self.data["bias_2"][-1] = 10.

    def json(self, _name):
        return deepcopy(self.meta)

    def arrays(self, _name):
        return {k: v.copy() for k, v in self.data.items()}


def predict(model, i, **kwargs):
    options = {"domain": "simulated_offline", "operating_point": "calibrado", "stream_id": "campaign/link"}
    options.update(kwargs)
    return model.predict(i * 0.002, np.eye(3), [20., 0., 0.3], **options)


@pytest.mark.parametrize("architecture", ["ridge", "mlp"])
def test_warmup_full_window_and_reset(architecture):
    model = CompensationReplayModel(SyntheticArchive(architecture), "calibrado", architecture)
    for i in range(15):
        result = predict(model, i)
        assert result.status == "warmup_persistence" and result.features is None
        assert result.predicted == result.persistence
    result = predict(model, 15)
    assert result.status == "model" and len(result.features) == 48
    assert result.increment == (0., 0., 10.)
    assert np.allclose(result.predicted, _exp_map(np.array([0., 0., 0.02])), rtol=0, atol=1e-14)
    assert result.target_timestamp_s == pytest.approx(0.07)
    assert result.physical_action_performed is False
    model.reset()
    assert predict(model, 200, stream_id="another/channel").status == "warmup_persistence"


@pytest.mark.parametrize("options", [
    {"domain": "measured"}, {"operating_point": "desenvolvimento"}, {"horizon_s": 0.02},
    {"horizon_s": float("nan")}, {"monitoring_photons_per_basis": 200}, {"stream_id": ""},
    {"stream_id": "x" * 257}, {"stream_id": "another/channel"},
])
def test_rejected_sample_does_not_mutate_history(options):
    model = CompensationReplayModel(SyntheticArchive(), "calibrado", "ridge")
    predict(model, 0)
    with pytest.raises(ValueError):
        predict(model, 1, **options)
    for i in range(1, 15):
        assert predict(model, i).status == "warmup_persistence"
    assert predict(model, 15).status == "model"


@pytest.mark.parametrize("index", [0, -1, 2, float("nan"), float("inf")])
def test_cadence_and_timestamp_rejection(index):
    model = CompensationReplayModel(SyntheticArchive(), "calibrado", "ridge")
    predict(model, 0)
    with pytest.raises(ValueError):
        predict(model, index)
    assert predict(model, 1).status == "warmup_persistence"


@pytest.mark.parametrize("matrix", [np.zeros((3, 3)), np.diag([1., 1., -1.]),
                                    np.eye(3)*2, np.full((3, 3), np.nan), np.eye(4)])
def test_invalid_rotations_are_not_projected(matrix):
    model = CompensationReplayModel(SyntheticArchive(), "calibrado", "ridge")
    with pytest.raises(ValueError):
        model.predict(0., matrix, [20., 0., .3], domain="simulated_offline",
                      operating_point="calibrado", stream_id="s")
    assert predict(model, 0).status == "warmup_persistence"


@pytest.mark.parametrize("covariates", [[-274., 0., .3], [20., 0., -.1], [20., 0.],
                                       [20., np.nan, .3], ["20", "0", ".3"]])
def test_invalid_covariates(covariates):
    model = CompensationReplayModel(SyntheticArchive(), "calibrado", "ridge")
    with pytest.raises(ValueError):
        model.predict(0., np.eye(3), covariates, domain="simulated_offline",
                      operating_point="calibrado", stream_id="s")


@pytest.mark.parametrize("angle", [0., 1e-10, .4, np.pi-1e-4, np.pi-1e-7, np.pi])
def test_rotation_roundtrip_small_regular_and_pi(angle):
    vector = np.array([0., 0., angle])
    matrix = _exp_map(vector)
    _rotation(matrix)
    assert np.allclose(_exp_map(_log_map(matrix)), matrix, rtol=0, atol=1e-7)


def test_window_frame_order_and_standardisation():
    archive = SyntheticArchive()
    archive.data["standardiser_mean"][:] = 1.
    archive.data["standardiser_scale"][:] = 2.
    model = CompensationReplayModel(archive, "calibrado", "ridge")
    history = np.array([_exp_map(np.array([0., 0., i*.01])) for i in range(16)])
    result = model.predict_window(np.arange(16)*.002, history, [20., .1, .3],
                                  domain="simulated_offline", operating_point="calibrado")
    features = np.asarray(result.features)
    assert np.allclose(features[:45].reshape(15, 3)[:, 2], -np.arange(1, 16)*5.)
    assert np.allclose(result.scaled, (features-1.)/2.)
    assert predict(model, 0).status == "warmup_persistence"  # Stateless call did not fill the stream.


def test_input_arrays_are_copied():
    model = CompensationReplayModel(SyntheticArchive(), "calibrado", "ridge")
    measured = np.eye(3)
    model.predict(0., measured, [20., 0., .3], domain="simulated_offline",
                  operating_point="calibrado", stream_id="campaign/link")
    measured[:] = 0
    for i in range(1, 16):
        result = predict(model, i)
    assert np.allclose(result.features[:45], 0.)


@pytest.mark.parametrize("change", ["gap", "reverse", "short", "history", "nonfinite"])
def test_window_validation(change):
    model = CompensationReplayModel(SyntheticArchive(), "calibrado", "ridge")
    times = np.arange(16)*.002
    history = np.repeat(np.eye(3)[None], 16, axis=0)
    if change == "gap":
        times[-1] += .002
    elif change == "reverse":
        times = times[::-1]
    elif change == "short":
        times = times[:-1]
    elif change == "history":
        history = history[:-1]
    else:
        times[-1] = np.nan
    with pytest.raises(ValueError):
        model.predict_window(times, history, [20., 0., .3], domain="simulated_offline",
                             operating_point="calibrado")


def test_failed_numerical_step_is_not_committed(monkeypatch):
    model = CompensationReplayModel(SyntheticArchive(), "calibrado", "ridge")
    for i in range(15):
        predict(model, i)
    with monkeypatch.context() as patch:
        patch.setattr(model, "_coef", np.full((3, 48), 1e308))
        with pytest.raises(FloatingPointError):
            predict(model, 15)
    assert predict(model, 15).status == "model"


@pytest.mark.parametrize("change", ["scale_zero", "scale_negative", "weight_nan", "period", "frequency", "spec", "extra"])
def test_incompatible_weights_or_metadata(change):
    archive = SyntheticArchive()
    if change == "scale_zero":
        archive.data["standardiser_scale"][0] = 0
    elif change == "scale_negative":
        archive.data["standardiser_scale"][0] = -1
    elif change == "weight_nan":
        archive.data["coef"][0, 0] = np.nan
    elif change == "period":
        archive.meta["operating_point"]["control_period_s"] = .001
    elif change == "frequency":
        archive.meta["operating_point"]["fibre"]["vibration_frequency_hz"] = 40
    elif change == "spec":
        archive.meta["spec"]["lags"] = [1, 2]
    else:
        archive.data["extra"] = np.zeros(3)
    with pytest.raises(ValueError):
        CompensationReplayModel(archive, "calibrado", "ridge")


def test_boolean_masks_require_explicit_opt_in(monkeypatch):
    stream = io.BytesIO()
    np.savez(stream, truth_valid=np.array([True, False]), values=np.ones(2))
    archive = object.__new__(ReviewedArchive)
    monkeypatch.setattr(archive, "read", lambda name: stream.getvalue())
    with pytest.raises(ValueError):
        archive.arrays("data.npz")
    assert archive.arrays("data.npz", boolean_keys=frozenset({"truth_valid"}))["truth_valid"].dtype == bool
    with pytest.raises(ValueError):
        archive.arrays("data.npz", boolean_keys=frozenset({"truth_valid", "missing"}))
    with pytest.raises(ValueError):
        archive.arrays("data.npz", boolean_keys=frozenset({"values"}))


def test_off_and_catalog_do_not_open_archives(monkeypatch):
    monkeypatch.setattr(ReviewedArchive, "__init__", lambda *a, **k: pytest.fail("unexpected archive access"))
    assert catalog.open_optional_model() is None
    options = catalog.model_options()
    assert len(options) == 11
    assert len({o.model_id for o in options}) == len(options)
    assert not any(o.default_enabled or o.runtime_available for o in options)


@pytest.mark.parametrize("model_id,mode", [
    ("unknown", "simulated_offline"), ("qkd.detector.boosting", "simulated_offline"),
    ("qnet.allocator.ridge", "simulated_offline"), ("qnet.compensation.calibrado.ridge", "physical"),
    ("qnet.compensation.calibrado.ridge", "simulated_offline"),
])
def test_selector_fails_closed_before_read(model_id, mode, monkeypatch):
    monkeypatch.setattr(ReviewedArchive, "__init__", lambda *a, **k: pytest.fail("unexpected archive access"))
    with pytest.raises(ValueError):
        catalog.open_optional_model(model_id, mode=mode)


def test_explicit_selection_creates_fresh_state(monkeypatch):
    from quantum_autopilot.model_intake import artifacts
    calls = []
    def archive(path, kind):
        calls.append((path, kind))
        return SyntheticArchive()
    monkeypatch.setattr(artifacts, "ReviewedArchive", archive)
    name = "qnet.compensation.calibrado.ridge"
    first = catalog.open_optional_model(name, archive_path="chosen.zip")
    predict(first, 0)
    second = catalog.open_optional_model(name, archive_path="chosen.zip")
    assert predict(second, 100).status == "warmup_persistence"
    assert calls == [("chosen.zip", "qnet"), ("chosen.zip", "qnet")]


def test_catalog_does_not_import_numerical_backend():
    script = "from quantum_autopilot.model_intake.catalog import model_options; " \
             "import sys,json; print(json.dumps({'count':len(model_options()),'numpy':'numpy' in sys.modules}))"
    completed = subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout) == {"count": 11, "numpy": False}
