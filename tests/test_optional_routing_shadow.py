"""Declared routing admission and read-only bridge tests, using pinned synthetic weights."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
import hashlib
import json

import pytest

from quantum_autopilot.control_plane.optional_routing import (
    MODEL_IDS,
    TELEMETRY_KEY,
    RoutingShadowWindow,
    evaluate_optional_routing,
    export_routing_shadow,
    main,
)
from quantum_autopilot.control_plane.runtime import ControllerRuntime
from quantum_autopilot.model_intake import catalog
from quantum_autopilot.model_intake.routing_session import evaluate_routing_session
from tests import test_optional_routing_session as fixtures
from tools.validate_routing_shadow import demo_snapshot

make_archive = fixtures.make_archive
routing_archive = fixtures.routing_archive
routing_package = fixtures.routing_package
pytestmark = pytest.mark.unit
MODEL = MODEL_IDS[0]


def run(state, path=None, model=MODEL):
    return evaluate_optional_routing(state, model_id=model, archive_path=path)


def bind_resources(state):
    raw = json.dumps(state.telemetry[TELEMETRY_KEY]["resources"], sort_keys=True, separators=(",", ":"))
    state.metadata["routing_resource_sha256"] = hashlib.sha256(raw.encode()).hexdigest()


def forbid_loading(monkeypatch):
    monkeypatch.setattr(catalog, "open_optional_model", lambda *a, **k: pytest.fail("weights opened"))


def test_off_missing_and_wrong_model(monkeypatch):
    forbid_loading(monkeypatch)
    assert evaluate_optional_routing(object(), archive_path="absent").status == "off"
    state = replace(demo_snapshot(), telemetry={"qber": 0.01, "optional_allocation_window": {}})
    assert run(state).reason == "missing_routing_window"
    assert run(demo_snapshot()).reason == "missing_archive"
    with pytest.raises(ValueError):
        run(demo_snapshot(), model="qnet.allocator.ridge")
    with pytest.raises(ValueError):
        run({})


@pytest.mark.parametrize("model", MODEL_IDS)
@pytest.mark.parametrize("seed", [0, 7, 1234, 2**32 - 1])
def test_full_offline_parity_and_immutability(routing_package, model, seed):
    state = demo_snapshot(seed)
    before = state.to_json()
    result = run(state, routing_package, model)
    assert result.status == "evaluated", result.detail
    actual = json.loads(result.evaluation_json)
    expected = json.loads(evaluate_routing_session(model, routing_package, actual["input_json"]).serialized())
    actual.pop("evaluated_at_utc")
    expected.pop("evaluated_at_utc")
    assert actual == expected
    assert [c["action_id"] for c in actual["candidates"]] == [
        "wait",
        "generate",
        "purify",
        "swap",
        "deliver",
        "discard",
    ]
    assert state.to_json() == before
    assert not any(
        (
            result.policy_activated,
            result.physical_action_performed,
            result.simulator_action_performed,
            result.resources_mutated,
        )
    )
    assert result.snapshot_sha256 == hashlib.sha256(result.snapshot_json.encode()).hexdigest()
    state.telemetry.clear()
    assert TELEMETRY_KEY in json.loads(result.snapshot_json)["telemetry"]
    with pytest.raises(FrozenInstanceError):
        result.status = "off"


BAD_FIELDS = [
    ("schema", "bad"),
    ("evidence", "measured"),
    ("feature_contract", "bad"),
    ("source_id", "other"),
    ("campaign_id", "other"),
    ("stream_id", "other"),
    ("clock_id", "other"),
    ("node_id", "other"),
    ("seed", True),
    ("seed", -1),
    ("seed", 2**32),
    ("state_features", {}),
    ("candidates", []),
    ("candidates", {}),
    ("reference_action_id", "absent"),
    ("task", []),
    ("resources", []),
    ("origin_timestamp_utc", "invalid"),
    ("features_timestamp_utc", "2026-09-24"),
    ("decision_timestamp_utc", "2026-09-24T00:00:11+00:00"),
    ("extra", 1),
]


@pytest.mark.parametrize("key,value", BAD_FIELDS)
def test_bad_window_before_weights(key, value, monkeypatch):
    forbid_loading(monkeypatch)
    state = demo_snapshot()
    state.telemetry[TELEMETRY_KEY][key] = value
    assert run(state, "unused").reason == "incompatible_routing_window"


RESOURCE_CASES = [
    "nodes_type",
    "node_duplicate",
    "node_extra",
    "capacity_bool",
    "capacity_negative",
    "reserved_excess",
    "node_unavailable",
    "node_flag",
    "link_duplicate_reverse",
    "link_missing",
    "link_unknown",
    "link_unavailable",
    "link_flag",
    "pairs_type",
    "pair_duplicate",
    "pair_unknown_node",
    "pair_id_bool",
    "pair_fidelity",
    "pair_reserved",
    "pair_future",
    "pair_lifetime",
    "pair_expired",
    "pair_missing",
    "memory_full",
    "memory_overflow",
    "no_purify",
    "purify_endpoints",
    "swap_node",
    "swap_parallel",
    "no_swap",
    "deliver_endpoints",
    "deliver_fidelity",
    "task_fraction",
    "disabled",
    "kind_duplicate",
    "kind_unknown",
    "kind_type",
    "ruleset",
    "resource_time",
    "version_bool",
    "version",
    "resource_extra",
]


@pytest.mark.parametrize("case", RESOURCE_CASES)
def test_resource_refusal_before_weights(case, monkeypatch):
    forbid_loading(monkeypatch)
    state = demo_snapshot()
    w = state.telemetry[TELEMETRY_KEY]
    r = w["resources"]
    if case == "nodes_type":
        r["nodes"] = {}
    elif case == "node_duplicate":
        r["nodes"].append(dict(r["nodes"][0]))
    elif case == "node_extra":
        r["nodes"][0]["extra"] = 0
    elif case == "capacity_bool":
        r["nodes"][0]["memory_capacity"] = True
    elif case == "capacity_negative":
        r["nodes"][0]["memory_capacity"] = -1
    elif case == "reserved_excess":
        r["nodes"][0]["reserved_slots"] = 9
    elif case == "node_unavailable":
        r["nodes"][0]["available"] = False
    elif case == "node_flag":
        r["nodes"][0]["available"] = 1
    elif case == "link_duplicate_reverse":
        r["links"].append(dict(endpoints=["B", "A"], available=True))
    elif case == "link_missing":
        r["links"] = []
    elif case == "link_unknown":
        r["links"][0]["endpoints"] = ["A", "D"]
    elif case == "link_unavailable":
        r["links"][0]["available"] = False
    elif case == "link_flag":
        r["links"][0]["available"] = "yes"
    elif case == "pairs_type":
        r["pairs"] = {}
    elif case == "pair_duplicate":
        r["pairs"].append(dict(r["pairs"][0]))
    elif case == "pair_unknown_node":
        r["pairs"][0]["endpoints"] = ["A", "D"]
    elif case == "pair_id_bool":
        r["pairs"][0]["pair_id"] = True
    elif case == "pair_fidelity":
        r["pairs"][0]["fidelity"] = 1.01
    elif case == "pair_reserved":
        r["pairs"][0]["reserved"] = True
    elif case == "pair_future":
        r["pairs"][0]["created_timestamp_utc"] = "2026-09-24T00:00:11+00:00"
    elif case == "pair_lifetime":
        r["pairs"][0]["expires_timestamp_utc"] = r["pairs"][0]["created_timestamp_utc"]
    elif case == "pair_expired":
        r["pairs"][0]["expires_timestamp_utc"] = w["decision_timestamp_utc"]
    elif case == "pair_missing":
        r["pairs"].pop(0)
    elif case == "memory_full":
        r["nodes"][0]["memory_capacity"] = 3
    elif case == "memory_overflow":
        r["nodes"][0]["memory_capacity"] = 2
    elif case == "no_purify":
        r["nodes"][0]["can_purify"] = False
    elif case == "purify_endpoints":
        r["pairs"][1]["endpoints"] = ["B", "C"]
    elif case == "swap_node":
        w["candidates"][3]["action"]["node"] = "A"
    elif case == "swap_parallel":
        w["candidates"][3]["action"]["pair_ids"] = [1, 2]
    elif case == "no_swap":
        r["nodes"][1]["can_swap"] = False
    elif case == "deliver_endpoints":
        w["task"]["endpoints"] = ["A", "B"]
    elif case == "deliver_fidelity":
        r["pairs"][3]["fidelity"] = 0.7
    elif case == "task_fraction":
        w["task"]["minimum_fidelity"] = True
    elif case == "disabled":
        r["enabled_kinds"].remove("SWAP")
    elif case == "kind_duplicate":
        r["enabled_kinds"] = ["WAIT", "WAIT"]
    elif case == "kind_unknown":
        r["enabled_kinds"] = ["INVALID"]
    elif case == "kind_type":
        r["enabled_kinds"] = [{}]
    elif case == "ruleset":
        r["ruleset"] = "other"
    elif case == "resource_time":
        r["timestamp_utc"] = w["origin_timestamp_utc"]
    elif case == "version_bool":
        r["state_version"] = True
    elif case == "version":
        r["state_version"] = 2
    elif case == "resource_extra":
        r["actuator"] = "device"
    bind_resources(state)  # Reach resource admission, rather than failing only the hash binding.
    result = run(state, "unused")
    assert result.status == "refused", (case, result.detail)
    assert result.evaluation_json is None


@pytest.mark.parametrize(
    "case",
    [
        "measured",
        "integrity",
        "ood",
        "hash",
        "ruleset",
        "origin",
        "stream",
        "campaign",
        "clock",
        "version",
        "future",
        "stale",
        "before_origin",
        "duplicate",
        "onehot",
    ],
)
def test_context_and_candidate_refusal(case, monkeypatch):
    forbid_loading(monkeypatch)
    state = demo_snapshot()
    w = state.telemetry[TELEMETRY_KEY]
    if case == "measured":
        state.provenance.evidence_kind = "measured"
    elif case == "integrity":
        state = replace(state, integrity_verified=False)
    elif case == "ood":
        state = replace(state, out_of_distribution=True)
    elif case in {"hash", "ruleset", "origin", "stream", "campaign", "clock"}:
        key = {
            "hash": "routing_resource_sha256",
            "ruleset": "routing_ruleset",
            "origin": "routing_origin_timestamp_utc",
            "stream": "routing_stream_id",
            "campaign": "campaign_id",
            "clock": "clock_id",
        }[case]
        state.metadata[key] = "other"
    elif case == "version":
        state = replace(state, state_version=2)
    elif case in {"future", "stale", "before_origin"}:
        seconds = {"future": 1, "stale": -1.000001, "before_origin": -11}[case]
        stamp = (datetime.fromisoformat(w["decision_timestamp_utc"]) + timedelta(seconds=seconds)).isoformat()
        w["features_timestamp_utc"] = w["resources"]["timestamp_utc"] = stamp
        bind_resources(state)
    elif case == "duplicate":
        w["candidates"].append(w["candidates"][0])
    elif case == "onehot":
        w["candidates"][1]["features"]["IS_WAIT"] = 1.0
    assert run(state, "unused").status == "refused"


def test_discard_expired_pair_and_age_boundary(routing_package):
    state = demo_snapshot()
    w = state.telemetry[TELEMETRY_KEY]
    w["candidates"] = [w["candidates"][-1]]
    w["reference_action_id"] = None
    w["resources"]["pairs"][1]["expires_timestamp_utc"] = w["decision_timestamp_utc"]
    w["features_timestamp_utc"] = w["resources"]["timestamp_utc"] = "2026-09-24T00:00:09+00:00"
    bind_resources(state)
    result = run(state, routing_package)
    assert result.status == "evaluated", result.detail
    assert result.feature_age_s == 1.0
    assert json.loads(result.evaluation_json)["agrees_with_reference"] is None


def test_bad_archive_and_immutable_window(tmp_path):
    state = demo_snapshot()
    window = RoutingShadowWindow.from_dict(state.telemetry[TELEMETRY_KEY])
    detached = window.to_dict()
    detached["resources"]["pairs"].clear()
    assert len(window.to_dict()["resources"]["pairs"]) == 4
    assert run(state, tmp_path / "missing.zip").reason == "model_evaluation_failed"
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not reviewed")
    assert run(state, bad).reason == "model_evaluation_failed"


def test_no_runtime_or_journal_mutation(routing_package, tmp_path, monkeypatch):
    runtime = ControllerRuntime(tmp_path / "commands.sqlite")
    before = (tmp_path / "commands.sqlite").read_bytes()
    for obj, method in [
        (runtime, "cycle"),
        (runtime.policy, "decide"),
        (runtime.journal, "append"),
        (runtime.simulated_executor, "execute"),
        (runtime.physical_executor, "execute"),
        (runtime.dry_run_executor, "execute"),
    ]:
        monkeypatch.setattr(obj, method, lambda *a, **k: pytest.fail("control invoked"))
    assert runtime.observe_optional_routing(object()).status == "off"
    assert (
        runtime.observe_optional_routing(demo_snapshot(), model_id=MODEL, archive_path=routing_package).status
        == "evaluated"
    )
    assert (tmp_path / "commands.sqlite").read_bytes() == before


def test_cli_and_export(routing_package, tmp_path):
    source, output = tmp_path / "input.json", tmp_path / "output.json"
    source.write_text(demo_snapshot().to_json(), encoding="utf-8")
    assert (
        main(
            [
                "--input",
                str(source),
                "--output",
                str(output),
                "--model",
                MODEL,
                "--qnet-zip",
                str(routing_package),
            ]
        )
        == 0
    )
    before = output.read_bytes()
    with pytest.raises(SystemExit) as exc:
        main(["--input", "missing", "--output", str(output)])
    assert exc.value.code == 2 and output.read_bytes() == before
    with pytest.raises(FileExistsError):
        export_routing_shadow(evaluate_optional_routing(None), output)
    assert main(["--input", "missing", "--output", str(tmp_path / "off.json")]) == 0


@pytest.mark.parametrize(
    "raw",
    ['{"x":1,"x":2}', "[]", '{"x":NaN}', '{"x":1e999}', " " * 131073],
    ids=["duplicate", "array", "nan", "overflow", "size"],
)
def test_strict_loader(raw, tmp_path, monkeypatch):
    forbid_loading(monkeypatch)
    source = tmp_path / "bad.json"
    source.write_text(raw, encoding="utf-8")
    output = tmp_path / "out.json"
    with pytest.raises(SystemExit) as exc:
        main(["--input", str(source), "--output", str(output), "--model", MODEL])
    assert exc.value.code == 2 and not output.exists()
