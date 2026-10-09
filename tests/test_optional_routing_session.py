"""Offline routing session guards, with synthetic pinned weights and no external data."""

from dataclasses import replace
import json

import numpy as np
import pytest

from quantum_autopilot.model_intake import catalog
from quantum_autopilot.model_intake.routing_session import (
    ACTION_NAMES,
    MODEL_IDS,
    STATE_NAMES,
    demo_routing_input,
    evaluate_routing_session,
    export_routing_evaluation,
    load_routing_input,
    parse_routing_input,
)
from tests import test_author_model_intake as helpers

pytestmark = pytest.mark.unit
make_archive = helpers.make_archive
routing_archive = helpers.routing_archive


@pytest.fixture
def routing_package(routing_archive, tmp_path):
    path = tmp_path / "routing.zip"
    path.write_bytes(routing_archive._raw)
    return path


@pytest.mark.parametrize("model_id", MODEL_IDS)
def test_selection_rng_and_explicit_reference(routing_package, model_id):
    text = demo_routing_input(model_id)
    result = evaluate_routing_session(model_id, routing_package, text)
    again = evaluate_routing_session(model_id, routing_package, text)
    assert replace(again, evaluated_at_utc=result.evaluated_at_utc) == result
    assert result.greedy_action_id == "candidate-0"
    stochastic = model_id == MODEL_IDS[0]
    expected = np.random.Generator(np.random.PCG64(1234)).choice(4, p=[0.25] * 4) if stochastic else 0
    assert result.recommendation_action_id == f"candidate-{expected}"
    assert result.rng_used == stochastic
    assert [c["probability"] for c in result.candidates] == (
        [0.25] * 4 if stochastic else [1.0, 0.0, 0.0, 0.0]
    )
    assert result.agrees_with_reference == (expected == 0)
    assert not result.physical_action_performed
    assert "NOT an independently evaluated heuristic" in result.describe()
    assert "not success" in result.describe()
    raw = json.loads(result.serialized())
    assert raw["input_json"] == text and raw["rng_algorithm"] == "PCG64"


def test_reference_not_fed_to_predictor_and_named_feature_order(routing_package):
    model_id = MODEL_IDS[0]
    request = json.loads(demo_routing_input(model_id))
    first = evaluate_routing_session(model_id, routing_package, json.dumps(request))
    request["reference_action_id"] = None
    request["state_features"] = dict(reversed(list(request["state_features"].items())))
    for candidate in request["candidates"]:
        candidate["features"] = dict(reversed(list(candidate["features"].items())))
    result = evaluate_routing_session(model_id, routing_package, json.dumps(request))
    assert result.candidates == first.candidates
    assert result.recommendation_action_id == first.recommendation_action_id
    assert result.agrees_with_reference is None
    assert "comparison unavailable" in result.describe()


@pytest.mark.parametrize(
    "case",
    [
        "domain",
        "checkpoint",
        "schema",
        "contract",
        "extra",
        "stream",
        "timestamp",
        "future",
        "seed_bool",
        "seed_negative",
        "seed_large",
        "state_missing",
        "state_bool",
        "state_nonfinite",
        "empty",
        "too_many",
        "candidate_extra",
        "duplicate_id",
        "duplicate_action",
        "kind",
        "one_hot",
        "feature_missing",
        "feature_bool",
        "action_extra",
        "pair_bool",
        "pair_count",
        "self_link",
        "link_missing",
        "wait_node",
        "reference",
        "nested",
    ],
)
def test_invalid_inputs_rejected_before_model_load(monkeypatch, case):
    request = json.loads(demo_routing_input(MODEL_IDS[0]))
    c = request["candidates"]
    if case == "domain":
        request["evidence"] = "measured"
    elif case == "checkpoint":
        request["model_id"] = MODEL_IDS[1]
    elif case == "schema":
        request["schema"] = "unknown"
    elif case == "contract":
        request["feature_contract"] = "raw_wdm"
    elif case == "extra":
        request["outcome"] = 1
    elif case == "stream":
        request["stream_id"] = "\n"
    elif case == "timestamp":
        request["decision_timestamp_s"] = -1
    elif case == "future":
        request["features_timestamp_s"] = 1
    elif case == "seed_bool":
        request["seed"] = True
    elif case == "seed_negative":
        request["seed"] = -1
    elif case == "seed_large":
        request["seed"] = 2**32
    elif case == "state_missing":
        request["state_features"].pop(STATE_NAMES[0])
    elif case == "state_bool":
        request["state_features"][STATE_NAMES[0]] = True
    elif case == "state_nonfinite":
        request["state_features"][STATE_NAMES[0]] = float("inf")
    elif case == "empty":
        request["candidates"] = []
    elif case == "too_many":
        request["candidates"] = c * 17
    elif case == "candidate_extra":
        c[0]["command"] = "execute"
    elif case == "duplicate_id":
        c[1]["action_id"] = c[0]["action_id"]
    elif case == "duplicate_action":
        c[2]["action"] = c[1]["action"]
    elif case == "kind":
        c[0]["action"]["kind"] = "EXECUTE"
    elif case == "one_hot":
        c[0]["features"]["IS_GENERATE"] = 1
    elif case == "feature_missing":
        c[0]["features"].pop(ACTION_NAMES[-1])
    elif case == "feature_bool":
        c[0]["features"][ACTION_NAMES[-1]] = False
    elif case == "action_extra":
        c[0]["action"]["target"] = "device"
    elif case == "pair_bool":
        c[0]["action"]["pair_ids"] = [True]
    elif case == "pair_count":
        c[1]["action"]["pair_ids"] = [0]
    elif case == "self_link":
        c[1]["action"]["link"] = ["0", "0"]
    elif case == "link_missing":
        c[1]["action"]["link"] = None
    elif case == "wait_node":
        c[0]["action"]["node"] = "0"
    elif case == "reference":
        request["reference_action_id"] = "absent"
    elif case == "nested":
        c[0]["features"][ACTION_NAMES[-1]] = [1]
    monkeypatch.setattr(
        catalog, "open_optional_model", lambda *a, **k: pytest.fail("model opened before validation")
    )
    with pytest.raises(ValueError):
        evaluate_routing_session(MODEL_IDS[0], "unused.zip", json.dumps(request))


@pytest.mark.parametrize(
    "text",
    ['{"schema":1,"schema":2}', "[]", '"text"', " " * 65537, '{"x":NaN}'],
    ids=["duplicate", "array", "string", "oversized", "nonfinite"],
)
def test_strict_json(text):
    with pytest.raises(ValueError):
        parse_routing_input(MODEL_IDS[0], text)


@pytest.mark.parametrize(
    "kind,pairs,node",
    [("PURIFY", [1, 2], None), ("SWAP", [1, 2], "1"), ("DELIVER", [1], None), ("DISCARD", [1], None)],
)
def test_other_simulator_actions_are_structural_only(routing_package, kind, pairs, node):
    request = json.loads(demo_routing_input(MODEL_IDS[1]))
    candidate = request["candidates"][0]
    candidate["action"] = {"kind": kind, "pair_ids": pairs, "link": None, "node": node}
    candidate["features"]["IS_WAIT"] = 0
    candidate["features"]["IS_" + kind] = 1
    request["candidates"] = [candidate]
    result = evaluate_routing_session(MODEL_IDS[1], routing_package, json.dumps(request))
    assert result.candidates[0]["action"]["kind"] == kind
    assert "NOT resource availability" in result.describe()
    candidate["action"]["pair_ids"] = [1, 1]
    with pytest.raises(ValueError):
        parse_routing_input(MODEL_IDS[1], json.dumps(request))


def test_load_export_and_archive_recheck(routing_package, tmp_path):
    model_id = MODEL_IDS[0]
    path = tmp_path / "input.json"
    text = demo_routing_input(model_id)
    path.write_bytes(text.encode("utf-8"))
    assert load_routing_input(model_id, path) == text
    result = evaluate_routing_session(model_id, routing_package, text)
    output = tmp_path / "result.json"
    export_routing_evaluation(result, output)
    with pytest.raises(FileExistsError):
        export_routing_evaluation(result, output)
    assert output.read_text(encoding="utf-8") == result.serialized()
    routing_package.write_bytes(b"replaced")
    with pytest.raises(ValueError, match="hash"):
        evaluate_routing_session(model_id, routing_package, text)
    path.write_bytes(b" " * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        load_routing_input(model_id, path)
    path.write_bytes(b"\xff")
    with pytest.raises(UnicodeError):
        load_routing_input(model_id, path)


def test_feature_mapping_uses_reviewed_order(routing_package, monkeypatch):
    request = json.loads(demo_routing_input(MODEL_IDS[0]))
    request["state_features"] = dict(reversed([(name, i / 10) for i, name in enumerate(STATE_NAMES)]))
    for candidate in request["candidates"]:
        candidate["features"].update({name: i / 100 for i, name in enumerate(ACTION_NAMES) if i >= 6})
    original = catalog.open_optional_model
    calls = []

    class Spy:
        def evaluate(self, state, features, actions, **kwargs):
            calls.append((state, features))
            return model.evaluate(state, features, actions, **kwargs)

    model = original(MODEL_IDS[0], archive_path=routing_package)
    monkeypatch.setattr(catalog, "open_optional_model", lambda *a, **kw: Spy())
    evaluate_routing_session(MODEL_IDS[0], routing_package, json.dumps(request))
    assert calls[0][0] == [i / 10 for i in range(11)]
    assert calls[0][1][0] == [1.0, 0.0, 0.0, 0.0, 0.0, 0.0] + [i / 100 for i in range(6, 23)]
