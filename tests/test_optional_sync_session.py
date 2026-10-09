"""Single-window evaluation must preserve provenance, baseline and input isolation."""
import hashlib
import json
import zipfile

import pytest

from quantum_autopilot.model_intake import artifacts, catalog
from quantum_autopilot.model_intake.sync_session import (
    demo_sync_input, evaluate_sync, export_sync_evaluation, load_sync_input, parse_sync_input,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def archive(tmp_path, monkeypatch):
    path = tmp_path / "sync.zip"
    config = {"window": 8, "scale": 1e-9, "update_period_s": 1., "horizon_s": 2.,
              "coefficients": [0.] * 9, "intercept": .5}
    with zipfile.ZipFile(path, "w") as target:
        target.writestr(artifacts.QKD_ROOT + "modelos/sincronizador_ridge.json", json.dumps(config))
    monkeypatch.setitem(artifacts.PINS, "qkd", hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def test_comparison_retains_worse_model_without_claiming_gain(archive):
    text = demo_sync_input()
    result = evaluate_sync(archive, text)
    assert result.target_timestamp_s == 9.
    assert result.predicted_offset_s == pytest.approx(1.2e-9)
    assert result.persistence_offset_s == pytest.approx(.7e-9)
    assert result.model_absolute_error_s == pytest.approx(.3e-9)
    assert result.persistence_absolute_error_s == pytest.approx(.2e-9)
    assert result.model_absolute_error_s > result.persistence_absolute_error_s
    assert result.evidence == "simulated_offline" and result.physical_action_performed is False
    assert result.input_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert result.archive_sha256 == artifacts.PINS["qkd"]
    assert "does not establish predictive gain" in result.describe()


def test_future_reference_never_changes_prediction(archive):
    request = json.loads(demo_sync_input())
    original = evaluate_sync(archive, json.dumps(request))
    request["reference"]["offset_s"] = -1e-9
    altered = evaluate_sync(archive, json.dumps(request))
    request["reference"] = None
    missing = evaluate_sync(archive, json.dumps(request))
    assert original.predicted_offset_s == altered.predicted_offset_s == missing.predicted_offset_s
    assert original.model_absolute_error_s != altered.model_absolute_error_s
    assert missing.model_absolute_error_s is None and missing.persistence_absolute_error_s is None
    assert "NOT PROVIDED" in missing.describe()


@pytest.mark.parametrize("change", ["measured", "schema", "extra", "short", "cadence", "reversed",
                                   "bool", "string", "nan", "overflow", "wrong_horizon", "future_fields",
                                   "stream", "huge_int", "temp_shape"])
def test_invalid_inputs_refused_before_model_open(change, monkeypatch):
    request = json.loads(demo_sync_input())
    if change == "measured":
        request["evidence"] = "measured"
    elif change == "schema":
        request["schema"] = "unknown"
    elif change == "extra":
        request["policy"] = "activate"
    elif change == "short":
        request["timestamps_s"].pop()
    elif change == "cadence":
        request["timestamps_s"][4] += .5
    elif change == "reversed":
        request["timestamps_s"].reverse()
    elif change in {"bool", "string", "nan", "overflow", "huge_int"}:
        request["offsets_s"][0] = {"bool": True, "string": "0", "nan": float("nan"),
                                   "overflow": float("inf"), "huge_int": 10**400}[change]
    elif change == "wrong_horizon":
        request["reference"]["target_timestamp_s"] = 8
    elif change == "future_fields":
        request["reference"]["temperatures_c"] = 20
    elif change == "stream":
        request["stream_id"] = ""
    else:
        request["temperatures_c"] = [[20]] * 8
    monkeypatch.setattr(catalog, "open_optional_model", lambda *a, **kw: pytest.fail("opened for invalid input"))
    with pytest.raises(ValueError):
        evaluate_sync("never-read.zip", json.dumps(request))


@pytest.mark.parametrize("text", ['{"schema":1,"schema":2}', '[]', '"text"', '{"x":1e999}', " " * 65537],
                         ids=["duplicate", "array", "string", "overflow", "oversize"])
def test_bad_json_rejected(text):
    with pytest.raises(ValueError):
        parse_sync_input(text)


def test_archive_is_reverified_on_each_run(archive):
    evaluate_sync(archive, demo_sync_input())
    archive.write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash differs"):
        evaluate_sync(archive, demo_sync_input())


def test_report_is_reproducible_and_export_never_overwrites(archive, tmp_path):
    result = evaluate_sync(archive, demo_sync_input())
    target = tmp_path / "report.json"
    export_sync_evaluation(result, target)
    record = json.loads(target.read_text(encoding="utf-8"))
    replay = evaluate_sync(archive, record["input_json"])
    assert replay.predicted_offset_s == result.predicted_offset_s
    assert replay.input_sha256 == result.input_sha256
    with pytest.raises(FileExistsError):
        export_sync_evaluation(result, target)
    assert json.loads(target.read_text(encoding="utf-8")) == record


def test_file_load_is_bounded_and_strict(tmp_path):
    path = tmp_path / "input.json"
    path.write_bytes(demo_sync_input().encode("utf-8"))
    assert load_sync_input(path) == demo_sync_input()
    path.write_bytes(b" " * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        load_sync_input(path)
    path.write_bytes(b"\xff")
    with pytest.raises(UnicodeError):
        load_sync_input(path)
