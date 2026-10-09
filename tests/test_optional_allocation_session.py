"""Allocation snapshots and baseline use identical inputs; no real package required."""

from dataclasses import replace
import io
import json

import numpy as np
import pytest

from quantum_autopilot.model_intake import catalog
from quantum_autopilot.model_intake.allocation_session import (
    LINK_NAMES,
    MODEL_IDS,
    demo_allocation_input,
    evaluate_allocation_session,
    export_allocation_evaluation,
    load_allocation_input,
    parse_allocation_input,
)
from quantum_autopilot.model_intake.artifacts import ALLOCATION_ROOT
from tests import test_author_model_intake as archive_helpers
from tests.test_optional_allocation import SyntheticPlant, simple_budget

pytestmark = pytest.mark.unit
make_archive = archive_helpers.make_archive


@pytest.fixture
def allocation_package(make_archive):
    fixture = SyntheticPlant()
    for profile, name in zip(fixture.plant["links"], LINK_NAMES):
        profile["name"] = name
    files = {ALLOCATION_ROOT + "model/plant.json": json.dumps(fixture.plant)}
    for variant in ("persistence", "ridge"):
        files[ALLOCATION_ROOT + f"model/{variant}.json"] = json.dumps(SyntheticPlant(variant).meta)
    stream = io.BytesIO()
    np.savez(stream, coefficients=fixture.coefficients)
    files[ALLOCATION_ROOT + "model/ridge.npz"] = stream.getvalue()
    return make_archive(files, "qnet")


@pytest.mark.parametrize("model_id", MODEL_IDS)
@pytest.mark.parametrize("history_count", [0, 1, 4, 5])
def test_warmup_persistence_and_fresh_snapshot(allocation_package, model_id, history_count):
    request = json.loads(demo_allocation_input(model_id))
    request["history_times_s"] = request["history_times_s"][-history_count:] if history_count else []
    request["drift_history"] = request["drift_history"][-history_count:] if history_count else []
    text = json.dumps(request)
    result = evaluate_allocation_session(model_id, allocation_package, text)
    again = evaluate_allocation_session(model_id, allocation_package, text)
    assert replace(again, evaluated_at_utc=result.evaluated_at_utc) == result
    expected = (
        "persistence" if model_id == MODEL_IDS[0] else "ridge" if history_count == 5 else "warmup_persistence"
    )
    assert result.recommendation["status"] == expected
    assert result.persistence["status"] == "persistence"
    assert result.recommendation["effective_at_s"] == 55
    assert result.history_samples == history_count
    assert len(result.recommendation["forecast"]) == 11
    assert all(len(row) == 30 for row in result.recommendation["forecast"])
    if model_id == MODEL_IDS[0]:
        assert result.recommendation == result.persistence
    assert not result.physical_action_performed
    assert "NOT delivered keys" in result.describe()
    assert "null budget" in result.describe()
    assert json.loads(result.serialized())["input_json"] == text


def test_same_input_queue_and_history_reach_both_models(allocation_package, monkeypatch):
    original = catalog.open_optional_model
    calls = []

    class Spy:
        def __init__(self, model):
            self.model = model

        def recommend_snapshot(self, observation, **kwargs):
            calls.append((observation, kwargs))
            return self.model.recommend_snapshot(observation, **kwargs)

    monkeypatch.setattr(catalog, "open_optional_model", lambda *a, **kw: Spy(original(*a, **kw)))
    text = demo_allocation_input(MODEL_IDS[1])
    evaluate_allocation_session(MODEL_IDS[1], allocation_package, text)
    assert len(calls) == 2 and calls[0][0] is calls[1][0]
    assert calls[0][1] == calls[1][1]
    assert calls[0][0].pending_admission == json.loads(text)["observation"]["pending_admission"]


def test_persistence_never_constructs_ridge(allocation_package, monkeypatch):
    original = catalog.open_optional_model
    calls = []

    def open_model(model_id, **kwargs):
        calls.append(model_id)
        return original(model_id, **kwargs)

    monkeypatch.setattr(catalog, "open_optional_model", open_model)
    evaluate_allocation_session(MODEL_IDS[0], allocation_package, demo_allocation_input(MODEL_IDS[0]))
    assert calls == [MODEL_IDS[0]]


def test_deadline_inside_committed_queue_cannot_be_fixed_by_new_admission(allocation_package, monkeypatch):
    from quantum_autopilot.model_intake.allocation import AllocationReplayModel

    monkeypatch.setattr(AllocationReplayModel, "_key_over", lambda self, *a: simple_budget(*a))
    request = json.loads(demo_allocation_input(MODEL_IDS[1]))
    obs = request["observation"]
    obs["urgency_s"] = [5.0] * 11
    obs["backlog_bits"] = [25.0] * 11
    obs["pending_admission"][0] = [0.0] * 11
    result = evaluate_allocation_session(MODEL_IDS[1], allocation_package, json.dumps(request))
    assert result.recommendation["admission"] == (1.0,) * 11
    assert result.recommendation["forecast_key_bits"] == (50.0,) * 11
    obs["pending_admission"].reverse()
    result = evaluate_allocation_session(MODEL_IDS[1], allocation_package, json.dumps(request))
    assert not any(result.recommendation["model_coverage_met"])
    assert not any(result.persistence["model_coverage_met"])
    assert result.recommendation["admission"] == (0.0,) * 11


def test_idle_budgets_remain_null_not_zero(allocation_package):
    request = json.loads(demo_allocation_input(MODEL_IDS[0]))
    request["observation"]["backlog_bits"] = [0.0] * 11
    result = evaluate_allocation_session(MODEL_IDS[0], allocation_package, json.dumps(request))
    assert result.recommendation["forecast_key_bits"] == (None,) * 11
    assert json.loads(result.serialized())["recommendation"]["forecast_key_bits"] == [None] * 11


def test_admission_differences_are_reported_without_claiming_gain(allocation_package, monkeypatch):
    from quantum_autopilot.model_intake.allocation import AllocationReplayModel

    def budget(self, link, drift, level):
        return float(np.sum((1. - np.asarray(level)) * (10. - 100. * drift)))

    monkeypatch.setattr(AllocationReplayModel, "_key_over", budget)
    request = json.loads(demo_allocation_input(MODEL_IDS[1]))
    request["observation"]["measured_qber"] = [.01] * 11
    request["observation"]["backlog_bits"] = [600.] * 11
    result = evaluate_allocation_session(MODEL_IDS[1], allocation_package, json.dumps(request))
    assert result.recommendation["admission"] == (0.,) * 11
    assert result.persistence["admission"] == (.25,) * 11
    assert result.differing_admission_links == LINK_NAMES
    assert "differences are not performance gains" in result.describe()


@pytest.mark.parametrize(
    "case",
    [
        "checkpoint",
        "domain",
        "protocol",
        "schema",
        "extra",
        "stream",
        "link_order",
        "link_missing",
        "period_bool",
        "latency",
        "horizon",
        "obs_extra",
        "qber_negative",
        "qber_large",
        "qber_bool",
        "raman",
        "pool",
        "backlog",
        "vector",
        "queue_count",
        "queue_level",
        "queue_bool",
        "urgency_zero",
        "urgency_bool",
        "urgency_inf",
        "urgency_count",
        "time_negative",
        "time_precision",
        "history_count",
        "history_future",
        "history_gap",
        "history_negative",
        "history_bool",
        "history_large",
        "history_shape",
    ],
)
def test_invalid_input_before_archive_access(monkeypatch, case):
    request = json.loads(demo_allocation_input(MODEL_IDS[1]))
    obs = request["observation"]
    if case == "checkpoint":
        request["model_id"] = MODEL_IDS[0]
    elif case == "domain":
        request["evidence"] = "measured"
    elif case == "protocol":
        request["protocol"] = "decoy_state_bb84"
    elif case == "schema":
        request["schema"] = "unknown"
    elif case == "extra":
        request["outcome"] = 1
    elif case == "stream":
        request["stream_id"] = "\n"
    elif case == "link_order":
        request["link_names"].reverse()
    elif case == "link_missing":
        request["link_names"].pop()
    elif case == "period_bool":
        request["period_s"] = True
    elif case == "latency":
        request["latency_s"] = 5
    elif case == "horizon":
        request["horizon_s"] = 30
    elif case == "obs_extra":
        obs["unknown"] = 1
    elif case == "qber_negative":
        obs["measured_qber"][0] = -0.1
    elif case == "qber_large":
        obs["measured_qber"][0] = 1.1
    elif case == "qber_bool":
        obs["measured_qber"][0] = True
    elif case == "raman":
        obs["raman_qber_estimate"][0] = float("nan")
    elif case == "pool":
        obs["key_pool_bits"][0] = -1
    elif case == "backlog":
        obs["backlog_bits"][0] = -1
    elif case == "vector":
        obs["backlog_bits"].pop()
    elif case == "queue_count":
        obs["pending_admission"].pop()
    elif case == "queue_level":
        obs["pending_admission"][0][0] = 0.1
    elif case == "queue_bool":
        obs["pending_admission"][0][0] = True
    elif case == "urgency_zero":
        obs["urgency_s"][0] = 0
    elif case == "urgency_bool":
        obs["urgency_s"][0] = True
    elif case == "urgency_inf":
        obs["urgency_s"][0] = float("inf")
    elif case == "urgency_count":
        obs["urgency_s"].pop()
    elif case == "time_negative":
        obs["time_s"] = -1
    elif case == "time_precision":
        obs["time_s"] = 1e300
    elif case == "history_count":
        request["history_times_s"].pop()
    elif case == "history_future":
        request["history_times_s"][-1] = 30
    elif case == "history_gap":
        request["history_times_s"][1] = 4
    elif case == "history_negative":
        request["history_times_s"][0] = -5
    elif case == "history_bool":
        request["drift_history"][0][0] = True
    elif case == "history_large":
        request["drift_history"][0][0] = 1.1
    elif case == "history_shape":
        request["drift_history"][0].pop()
    monkeypatch.setattr(
        catalog, "open_optional_model", lambda *a, **kw: pytest.fail("opened before validation")
    )
    with pytest.raises(ValueError):
        evaluate_allocation_session(MODEL_IDS[1], "unused.zip", json.dumps(request))


@pytest.mark.parametrize(
    "text",
    ['{"schema":1,"schema":2}', "[]", '"text"', " " * 65537, '{"x":NaN}'],
    ids=["duplicate", "array", "string", "oversized", "nonfinite"],
)
def test_strict_json(text):
    with pytest.raises(ValueError):
        parse_allocation_input(MODEL_IDS[0], text)


def test_loader_export_and_recheck(allocation_package, tmp_path):
    text = demo_allocation_input(MODEL_IDS[0])
    path = tmp_path / "input.json"
    path.write_bytes(text.encode())
    assert load_allocation_input(MODEL_IDS[0], path) == text
    result = evaluate_allocation_session(MODEL_IDS[0], allocation_package, text)
    output = tmp_path / "result.json"
    export_allocation_evaluation(result, output)
    with pytest.raises(FileExistsError):
        export_allocation_evaluation(result, output)
    assert output.read_text(encoding="utf-8") == result.serialized()
    allocation_package.write_bytes(b"replaced")
    with pytest.raises(ValueError, match="hash"):
        evaluate_allocation_session(MODEL_IDS[0], allocation_package, text)
    path.write_bytes(b" " * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        load_allocation_input(MODEL_IDS[0], path)
    path.write_bytes(b"\xff")
    with pytest.raises(UnicodeError):
        load_allocation_input(MODEL_IDS[0], path)
