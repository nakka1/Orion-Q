"""Bounded SO(3) session, checkpoint isolation, warm-up and non-leaking references."""
import hashlib
import io
import json
import zipfile

import numpy as np
import pytest

from quantum_autopilot.model_intake import artifacts, catalog
from quantum_autopilot.model_intake.compensation_session import (
    MODEL_IDS, demo_compensation_input, evaluate_compensation_session,
    export_compensation_evaluation, load_compensation_input, parse_compensation_input,
)
from tests import test_optional_compensation as helpers

pytestmark = pytest.mark.unit
DEFAULT = MODEL_IDS[0]


@pytest.fixture
def compensation_archive(tmp_path, monkeypatch):
    path = tmp_path / "compensation.zip"
    with zipfile.ZipFile(path, "w") as target:
        for model_id in MODEL_IDS:
            _, _, point, kind = model_id.split(".")
            fixture = helpers.SyntheticArchive(kind, point)
            prefix = artifacts.COMPENSATION_ROOT + point + "/model/" + kind
            buffer = io.BytesIO()
            np.savez(buffer, **fixture.data)
            target.writestr(prefix + ".npz", buffer.getvalue())
            target.writestr(prefix + ".json", json.dumps(fixture.meta))
    monkeypatch.setitem(artifacts.PINS, "qnet", hashlib.sha256(path.read_bytes()).hexdigest())
    return path


@pytest.mark.parametrize("model_id", MODEL_IDS)
def test_four_checkpoints_preserve_warmup_and_final_forecast(compensation_archive, model_id):
    text = demo_compensation_input(model_id)
    result = evaluate_compensation_session(model_id, compensation_archive, text)
    assert [step["status"] for step in result.steps] == ["warmup_persistence"] * 15 + ["model"]
    assert all(step["predicted"] == step["persistence"] for step in result.steps[:15])
    assert len(result.steps[-1]["features"]) == 48
    assert result.model_angular_error_rad == pytest.approx(0., abs=1e-7)
    assert result.persistence_angular_error_rad == pytest.approx(.02, abs=1e-12)
    assert result.archive_sha256 == artifacts.PINS["qnet"]
    assert result.input_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert not result.physical_action_performed and result.evidence == "simulated_offline"
    assert "NOT learned forecasts" in result.describe()


@pytest.mark.parametrize("count", [1, 8, 15])
def test_short_series_is_only_persistence(compensation_archive, count):
    request = json.loads(demo_compensation_input(DEFAULT))
    request["samples"] = request["samples"][:count]
    request["reference"]["target_timestamp_s"] = request["samples"][-1]["timestamp_s"] + .04
    result = evaluate_compensation_session(DEFAULT, compensation_archive, json.dumps(request))
    assert all(step["status"] == "warmup_persistence" for step in result.steps)
    assert result.model_angular_error_rad == result.persistence_angular_error_rad
    assert "learned outputs: 0" in result.describe()


def test_future_reference_never_changes_prediction(compensation_archive):
    request = json.loads(demo_compensation_input(DEFAULT))
    original = evaluate_compensation_session(DEFAULT, compensation_archive, json.dumps(request))
    request["reference"]["rotation"] = np.eye(3).tolist()
    changed = evaluate_compensation_session(DEFAULT, compensation_archive, json.dumps(request))
    request["reference"] = None
    missing = evaluate_compensation_session(DEFAULT, compensation_archive, json.dumps(request))
    assert original.steps == changed.steps == missing.steps
    assert original.model_angular_error_rad != changed.model_angular_error_rad
    assert missing.model_angular_error_rad is None and missing.persistence_angular_error_rad is None
    assert "NOT PROVIDED" in missing.describe()


@pytest.mark.parametrize("change", ["measured", "checkpoint", "stream", "period", "horizon", "photons", "photon_bool",
                                   "empty", "too_many", "gap", "reversed", "negative_time", "huge_time", "time_bool",
                                   "rotation_shape", "rotation_bool", "rotation_nan", "reflection", "nonorthogonal",
                                   "covariate_extra", "covariate_bool", "temperature", "vibration",
                                   "reference_time", "reference_shape", "extra", "missing"])
def test_refuse_before_model_loading(change, monkeypatch):
    request = json.loads(demo_compensation_input(DEFAULT))
    sample = request["samples"][0]
    if change == "measured":
        request["evidence"] = "measured"
    elif change == "checkpoint":
        request["model_id"] = MODEL_IDS[-1]
    elif change == "stream":
        request["stream_id"] = ""
    elif change in {"period", "horizon"}:
        request[change + "_s"] = .001
    elif change in {"photons", "photon_bool"}:
        request["monitoring_photons_per_basis"] = 200 if change == "photons" else True
    elif change == "empty":
        request["samples"] = []
    elif change == "too_many":
        request["samples"].append(sample)
    elif change == "gap":
        sample["timestamp_s"] = .001
    elif change == "reversed":
        request["samples"].reverse()
    elif change == "negative_time":
        sample["timestamp_s"] = -.002
    elif change == "huge_time":
        request["samples"] = [sample]
        sample["timestamp_s"] = 1e300
    elif change == "time_bool":
        sample["timestamp_s"] = False
    elif change == "rotation_shape":
        sample["rotation"] = [1, 0, 0]
    elif change == "rotation_bool":
        sample["rotation"][2][2] = True
    elif change == "rotation_nan":
        sample["rotation"][2][2] = float("nan")
    elif change == "reflection":
        sample["rotation"][2][2] = -1
    elif change == "nonorthogonal":
        sample["rotation"][0][0] = 2
    elif change == "covariate_extra":
        sample["covariates"]["future"] = 0
    elif change == "covariate_bool":
        sample["covariates"]["temperature_rate_k_s"] = False
    elif change == "temperature":
        sample["covariates"]["temperature_c"] = -300
    elif change == "vibration":
        sample["covariates"]["vibration_rms_rad"] = -1
    elif change == "reference_time":
        request["reference"]["target_timestamp_s"] = .06
    elif change == "reference_shape":
        request["reference"]["rotation"] = False
    elif change == "extra":
        request["command"] = "apply"
    else:
        del request["horizon_s"]
    monkeypatch.setattr(catalog, "open_optional_model", lambda *a, **kw: pytest.fail("opened weights"))
    with pytest.raises(ValueError):
        evaluate_compensation_session(DEFAULT, "never-read.zip", json.dumps(request))


@pytest.mark.parametrize("text", ['{"schema":1,"schema":2}', '[]', '"text"', " " * 65537],
                         ids=["duplicate", "array", "string", "oversize"])
def test_bad_json_refused(text):
    with pytest.raises(ValueError):
        parse_compensation_input(DEFAULT, text)


def test_fresh_state_export_replay_and_rechecked_archive(compensation_archive, tmp_path):
    text = demo_compensation_input(DEFAULT)
    result = evaluate_compensation_session(DEFAULT, compensation_archive, text)
    repeated = evaluate_compensation_session(DEFAULT, compensation_archive, text)
    assert repeated.steps == result.steps
    target = tmp_path / "result.json"
    export_compensation_evaluation(result, target)
    report = json.loads(target.read_bytes())
    replay = evaluate_compensation_session(report["model_id"], compensation_archive, report["input_json"])
    assert replay.steps == result.steps
    with pytest.raises(FileExistsError):
        export_compensation_evaluation(result, target)
    compensation_archive.write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash differs"):
        evaluate_compensation_session(DEFAULT, compensation_archive, text)


def test_load_bounds_and_checkpoint(tmp_path):
    path = tmp_path / "input.json"
    path.write_bytes(demo_compensation_input(DEFAULT).encode())
    assert load_compensation_input(DEFAULT, path) == demo_compensation_input(DEFAULT)
    with pytest.raises(ValueError, match="checkpoint"):
        load_compensation_input(MODEL_IDS[1], path)
    path.write_bytes(b" " * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        load_compensation_input(DEFAULT, path)
