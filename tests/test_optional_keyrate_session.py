"""Offline actor session: causal typed inputs and reproducible, non-actuating output."""
import hashlib
import io
import json
import zipfile

import numpy as np
import pytest

from quantum_autopilot.model_intake import artifacts, catalog
from quantum_autopilot.model_intake.keyrate import ACTIONS, BOX, FEATURE_NAMES
from quantum_autopilot.model_intake.keyrate_session import (
    demo_keyrate_input, evaluate_keyrate_session, export_keyrate_evaluation, load_keyrate_input, parse_keyrate_input,
)
from quantum_autopilot.model_intake.sync_session import demo_sync_input

pytestmark = pytest.mark.unit


@pytest.fixture
def actor_archive(tmp_path, monkeypatch):
    path = tmp_path / "actor.zip"
    arrays = {"normaliser_count": np.array(20.), "normaliser_mean": np.zeros(16),
              "normaliser_m2": np.full(16, 20.)}
    for role, sizes in (("actor", (16, 64, 64, 9)), ("critic", (16, 64, 64, 1))):
        for i, (a, b) in enumerate(zip(sizes[:-1], sizes[1:])):
            arrays[f"{role}_{2*i}"] = np.zeros((a, b))
            arrays[f"{role}_{2*i+1}"] = np.zeros(b)
    buffer = io.BytesIO()
    np.savez(buffer, **arrays)
    metadata = {"temperature": .25, "actions": list(ACTIONS), "observation": list(FEATURE_NAMES),
                "box": BOX, "dead_time_s": 50e-9}
    with zipfile.ZipFile(path, "w") as target:
        target.writestr(artifacts.QKD_ROOT + "modelos/agente_taxa_chave.npz", buffer.getvalue())
        target.writestr(artifacts.QKD_ROOT + "modelos/agente_config.json", json.dumps(metadata))
    monkeypatch.setitem(artifacts.PINS, "qkd", hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def test_sampling_is_reproducible_not_silently_greedy(actor_archive):
    text = demo_keyrate_input()
    first = evaluate_keyrate_session(actor_archive, text)
    second = evaluate_keyrate_session(actor_archive, text)
    assert first.recommendation == second.recommendation
    assert first.candidates == second.candidates
    assert first.recommendation["sampled_index"] == int(np.random.Generator(np.random.PCG64(1234)).choice(9))
    assert first.recommendation["sampled_index"] != first.recommendation["greedy_index"]
    assert first.baseline_settings == json.loads(text)["settings"]
    assert first.input_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert first.archive_sha256 == artifacts.PINS["qkd"]
    assert first.numpy_version == np.__version__ and first.rng_algorithm == "numpy.random.PCG64"
    assert first.physical_action_performed is False
    assert first.outcome_comparison.startswith("unavailable")
    assert "NOT performance differences" in first.describe()
    assert "not probabilities of success" in first.describe()


def test_all_candidates_are_bounded_and_baseline_unchanged(actor_archive):
    request = json.loads(demo_keyrate_input())
    request["settings"]["clock_rate_hz"] = 5e9
    result = evaluate_keyrate_session(actor_archive, json.dumps(request))
    assert len(result.candidates) == 9
    assert sum(c["probability"] for c in result.candidates) == pytest.approx(1.)
    assert result.candidates[5]["proposed_settings"]["clock_rate_hz"] == 5e9
    assert result.baseline_settings == request["settings"]
    assert result.recommendation["proposed_settings"] == result.candidates[result.recommendation["sampled_index"]]["proposed_settings"]


def test_missing_previous_is_explicit_calibration_bootstrap(actor_archive):
    request = json.loads(demo_keyrate_input())
    request["previous"] = request["previous_period_end_s"] = None
    result = evaluate_keyrate_session(actor_archive, json.dumps(request))
    assert result.observation_source == "calibration_bootstrap"
    assert "calibration_bootstrap" in result.describe()


@pytest.mark.parametrize("change", ["measured", "schema", "stream", "seed_missing", "seed_bool", "seed_negative",
                                   "seed_float", "seed_large", "protocol", "span", "future", "timestamp_bool",
                                   "timestamp_negative", "previous_missing_time", "initial_with_time", "future_target",
                                   "settings_extra", "settings_missing", "calibration_extra", "previous_extra",
                                   "signal_bool", "signal_string", "signal_nan", "signal_overflow", "signal_box",
                                   "dead_time", "decoy", "negative_rate"])
def test_refuse_invalid_input_before_opening_weights(change, monkeypatch):
    request = json.loads(demo_keyrate_input())
    if change == "measured":
        request["evidence"] = "measured"
    elif change == "schema":
        request["schema"] = "other"
    elif change == "stream":
        request["stream_id"] = "\n"
    elif change == "seed_missing":
        del request["seed"]
    elif change.startswith("seed_"):
        request["seed"] = {"seed_bool": True, "seed_negative": -1, "seed_float": 1., "seed_large": 2**32}[change]
    elif change == "protocol":
        request["protocol"] = "single_photon_bb84"
    elif change == "span":
        request["link_span_km"] = 100
    elif change == "future":
        request["previous_period_end_s"] = 2
    elif change.startswith("timestamp_"):
        request["decision_timestamp_s"] = True if change == "timestamp_bool" else -1
    elif change == "previous_missing_time":
        request["previous_period_end_s"] = None
    elif change == "initial_with_time":
        request["previous"] = None
    elif change == "future_target":
        request["next_rate_hz"] = 1e6
    elif change.endswith("_extra"):
        request[change.split("_")[0]]["unknown"] = 0
    elif change == "settings_missing":
        del request["settings"]["clock_rate_hz"]
    elif change.startswith("signal_"):
        request["settings"]["signal_intensity"] = {"signal_bool": True, "signal_string": ".5",
            "signal_nan": float("nan"), "signal_overflow": 10**400, "signal_box": 2.}[change]
    elif change == "dead_time":
        request["calibration"]["dead_time_s"] = 10e-9
    elif change == "decoy":
        request["settings"]["decoy_intensity"] = .4
    else:
        request["previous"]["key_rate_hz"] = -1
    monkeypatch.setattr(catalog, "open_optional_model", lambda *a, **kw: pytest.fail("weights opened"))
    with pytest.raises(ValueError):
        evaluate_keyrate_session("never-read.zip", json.dumps(request))


@pytest.mark.parametrize("text", ['{"schema":1,"schema":2}', '[]', " " * 65537, demo_sync_input()],
                         ids=["duplicate", "array", "oversize", "synchronizer-schema"])
def test_schema_and_size_are_distinct(text):
    with pytest.raises(ValueError):
        parse_keyrate_input(text)


def test_recheck_changed_archive(actor_archive):
    evaluate_keyrate_session(actor_archive, demo_keyrate_input())
    actor_archive.write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash differs"):
        evaluate_keyrate_session(actor_archive, demo_keyrate_input())


def test_load_and_export_are_bounded_reproducible_and_exclusive(actor_archive, tmp_path):
    source = tmp_path / "input.json"
    source.write_bytes(demo_keyrate_input().encode())
    result = evaluate_keyrate_session(actor_archive, load_keyrate_input(source))
    target = tmp_path / "result.json"
    export_keyrate_evaluation(result, target)
    record = json.loads(target.read_bytes())
    replay = evaluate_keyrate_session(actor_archive, record["input_json"])
    assert replay.recommendation == result.recommendation
    with pytest.raises(FileExistsError):
        export_keyrate_evaluation(result, target)
    source.write_bytes(b" " * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        load_keyrate_input(source)
