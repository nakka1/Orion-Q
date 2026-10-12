"""Contracts and native network regressions; mock outputs are not evidence."""
from __future__ import annotations

import ast
from copy import deepcopy
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import sequence_network_backend as backend


def payload():
    return {"schema": backend.INPUT_SCHEMA, "scenario_id": "three-native-routers", "topology_sha256": "a"*64,
            "seed": 2026101001, "duration_s": .1,
            "nodes": [{"id": name, "memory_count": 4, "memory_frequency_hz": 10000, "memory_coherence_s": 1,
                       "memory_efficiency": 1, "raw_fidelity": .97} for name in ("A", "R", "B")],
            "links": [{"id": name, "a": a, "b": b, "length_km": 1, "attenuation_db_km": .2,
                       "classical_delay_s": 5e-6, "protocol": "entanglement", "enabled": True, "classical_enabled": True}
                      for name, a, b in (("AR", "A", "R"), ("RB", "R", "B"))],
            "demands": [{"id": "AB", "source": "A", "target": "B", "request_s": 0, "start_s": .01,
                         "duration_s": .08, "memory_count": 1, "target_fidelity": .8}],
            "detector": {"efficiency": 1, "dark_count_hz": 0, "time_resolution_ps": 100, "count_rate_hz": 5e7}}


def test_complete_contract_is_valid_without_loading_sdk():
    backend.validate_payload(payload())


@pytest.mark.parametrize("path,value", [
    (("schema",), "other"), (("topology_sha256",), "z"*64), (("seed",), True), (("seed",), -1),
    (("formalism",), "bell_diagonal"),
    (("duration_s",), 0), (("nodes", 0, "memory_count"), 1.5), (("nodes", 0, "memory_efficiency"), 1.1),
    (("nodes", 0, "memory_coherence_s"), 0), (("nodes", 0, "raw_fidelity"), float("nan")),
    (("links", 0, "a"), "missing"), (("links", 0, "b"), "A"), (("links", 0, "length_km"), -1),
    (("links", 0, "classical_delay_s"), 0), (("links", 0, "enabled"), 1),
    (("demands", 0, "request_s"), .01), (("demands", 0, "duration_s"), .1),
    (("demands", 0, "target_fidelity"), 0), (("demands", 0, "memory_count"), False),
    (("detector", "efficiency"), -1), (("detector", "time_resolution_ps"), 0),
])
def test_invalid_contract_rejected_before_sdk(path, value):
    data = payload()
    selected = data
    for key in path[:-1]:
        selected = selected[key]
    selected[path[-1]] = value
    with pytest.raises(ValueError):
        backend.validate_payload(data)


@pytest.mark.parametrize("collection", ["nodes", "links", "demands"])
def test_duplicate_component_ids_rejected(collection):
    data = payload()
    data[collection].append(deepcopy(data[collection][0]))
    with pytest.raises(ValueError, match="duplicate"):
        backend.validate_payload(data)


@pytest.mark.parametrize("modification,reason", [
    ({"enabled": False}, "disabled_quantum_connection"),
    ({"classical_enabled": False}, "paired_classical_channel_disabled"),
    ({"protocol": "tf_qkd"}, "unsupported_native_protocol"),
    ({"length_km": 0}, "zero_length_midpoint_not_supported"),
])
def test_excluded_links_remain_in_census(modification, reason):
    data = payload()
    data["links"][0].update(modification)
    selected, census = backend.select_links(data)
    assert len(census) == 2
    assert len(selected) == 1
    assert census[0]["reason"] == reason
    assert census[0]["status"] == "not_demonstrated"


def test_parallel_bank_selection_is_explicit_not_summed():
    data = payload()
    duplicate = {**data["links"][0], "id": "AR-2", "length_km": 2, "selected": True}
    data["links"].append(duplicate)
    selected, census = backend.select_links(data)
    assert duplicate in selected and data["links"][0] not in selected
    assert census[0]["reason"] == "parallel_device_bank_not_supported_in_single_RouterNetTopo"
    data["links"][0]["selected"] = True
    with pytest.raises(ValueError, match="multiple"):
        backend.select_links(data)


def test_unsupported_nodes_are_never_substituted():
    data = payload()
    data["nodes"][0] = {"id": "A", "native_eligible": False, "physical_type": "satellite"}
    backend.validate_payload(data)
    selected, census = backend.select_links(data)
    assert len(selected) == 1
    assert census[0]["reason"] == "endpoint_has_no_native_technology_mapping"


def test_duplicate_json_key_rejected():
    with pytest.raises(ValueError, match="duplicate JSON"):
        json.loads('{"seed":1,"seed":2}', object_pairs_hook=backend._unique_object)


def test_picosecond_conversion_uses_decimal_input():
    assert backend._ps(.000000000123) == 123
    assert backend._ps(1.25) == 1250000000000


def test_network_runner_has_no_orion_imports():
    source = Path(backend.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith("quantum_autopilot") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("quantum_autopilot")


def test_reference_refuses_product_already_loaded(monkeypatch):
    monkeypatch.setitem(sys.modules, "quantum_autopilot.fixture", object())
    with pytest.raises(RuntimeError, match="product implementation is loaded"):
        backend.load_native("missing-checkout")


def native_python():
    executable = os.environ.get("ORIONQ_SEQUENCE_PYTHON")
    if not executable:
        pytest.skip("native network checks require the isolated SeQUeNCe Python")
    return executable


def run_native(data, tmp_path, name):
    request, result = tmp_path / f"{name}-input.json", tmp_path / f"{name}-output.json"
    request.write_text(json.dumps(data), encoding="utf-8")
    completed = subprocess.run([native_python(), str(Path(backend.__file__)), "--input", str(request), "--output", str(result),
                                "--sequence-src", os.environ.get("SEQUENCE_SRC", "C:/SeQUeNCe")],
                               capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(result.read_text(encoding="utf-8")), result


def test_native_multihop_reservation_generation_swapping_and_receipts(tmp_path):
    result, result_path = run_native(payload(), tmp_path, "nominal")
    assert result["provenance"]["commit"] == backend.PINNED_COMMIT
    assert result["request_sha256"] == hashlib.sha256(json.dumps(payload(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    demand = result["demands"][0]
    assert demand["status"] == "approved"
    assert demand["path"] == ["A", "R", "B"]
    assert demand["delivered_pairs"] > 0
    assert all(pair["native_metadata_fidelity"] == pair["fidelity"] for pair in demand["delivery_observations"])
    assert any(pair["retained_density"]["status"] == "observed" for pair in demand["delivery_observations"])
    for pair in demand["delivery_observations"]:
        density = pair["retained_density"]
        if density["status"] == "observed":
            assert abs(density["trace"]-1) < 1e-12
            assert abs(sum(density["bell_fidelities"].values())-1) < 1e-12
    assert result["native_metrics"]["EG_SUCCESS"] > 0
    assert result["native_metrics"]["ES_SUCCESS"] > 0
    assert result["classical_receipts"]["max_delay_error_ps"] == 0
    assert result["complete_network_validation"] is False
    with gzip.open(result_path.with_name(result["event_log"]["path"]), "rt", encoding="utf-8") as stream:
        events = [json.loads(line) for line in stream]
    assert len(events) == result["event_log"]["count"]
    assert [e["time_ps"] for e in events] == sorted(e["time_ps"] for e in events)
    assert any(e["event"] == "photon_received" for e in events)
    assert any(e["event"] == "detector_click" for e in events)
    sent = {e["message_id"]: e for e in events if e["event"] == "classical_sent"}
    for receipt in [e for e in events if e["event"] == "classical_received"]:
        assert receipt["time_ps"] >= sent[receipt["message_id"]]["time_ps"]


def test_native_paired_classical_block_prevents_generation(tmp_path):
    data = payload()
    data["links"][0]["classical_enabled"] = False
    result, _ = run_native(data, tmp_path, "classical-block")
    assert result["demands"][0]["status"] == "blocked_no_native_route"
    assert result["demands"][0]["delivered_pairs"] == 0
    assert result["native_metrics"].get("EG_SUCCESS", 0) == 0


def test_native_all_classical_links_blocked_preserves_isolated_router_census(tmp_path):
    data = payload()
    for link in data["links"]:
        link["classical_enabled"] = False
    result, _ = run_native(data, tmp_path, "all-classical-blocked")
    assert result["demands"][0]["status"] == "blocked_no_native_route"
    assert result["demands"][0]["delivered_pairs"] == 0
    assert len(result["node_census"]) == 3
    assert all(node["status"] == "native_instantiated" and node["memory_count"] == 4
               for node in result["node_census"])
    assert all(link["reason"] == "paired_classical_channel_disabled" for link in result["link_census"])
    assert result["native_config"]["qconnections"] == []
    assert result["native_config"]["qchannels"] == []
    assert result["native_config"]["cchannels"] == []
    assert result["native_metrics"].get("EG_SUCCESS", 0) == 0
    assert result["event_counts"].get("photon_sent", 0) == 0


def test_native_no_eligible_nodes_has_explicit_unsupported_demands(tmp_path):
    data = payload()
    for node in data["nodes"]:
        node["native_eligible"] = False
    result, _ = run_native(data, tmp_path, "no-eligible-nodes")
    assert result["demands"][0]["status"] == "unsupported_source"
    assert result["demands"][0]["delivered_pairs"] == 0
    assert len(result["node_census"]) == 3
    assert all(node["status"] == "not_demonstrated" for node in result["node_census"])
    assert result["timeline"]["executed_events"] == 0
    assert result["event_log"]["count"] == 0


def test_native_zero_detection_has_no_successful_pairs(tmp_path):
    data = payload()
    data["detector"]["efficiency"] = 0
    result, _ = run_native(data, tmp_path, "zero-detection")
    assert result["demands"][0]["status"] == "approved"
    assert result["demands"][0]["delivered_pairs"] == 0
    assert result["event_counts"]["photon_sent"] > 0
    assert result["native_metrics"].get("EG_SUCCESS", 0) == 0


def test_native_probe_receipt_is_framework_event(tmp_path):
    native_python()
    request = {"command_id": "probe1", "sender": "satellite", "receiver": "OGS", "emitted_at_s": 1.25,
               "propagation_s": .004, "serialization_s": .002, "jitter_s": .000001, "payload": {"value": 3}}
    script = ("import json,sys;sys.path.insert(0,sys.argv[1]);import sequence_network_backend as b;"
              "api,metadata=b.load_native(sys.argv[2]);print(json.dumps(b.run_classical_probe(json.loads(sys.argv[3]),api)))")
    completed = subprocess.run([native_python(), "-c", script, str(Path(backend.__file__).parent),
                                os.environ.get("SEQUENCE_SRC", "C:/SeQUeNCe"), json.dumps(request)],
                               capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout.splitlines()[-1])
    assert result["received_at_ps"] - result["sent_at_ps"] == 6001000000
    assert result["events"][1]["event"] == "classical_received"
    assert result["payload"] == request["payload"]
    assert result["timeline_run_counter"] == 2


def test_native_resource_contention_and_release(tmp_path):
    data = payload()
    data["duration_s"] = .15
    for node in data["nodes"]:
        node["memory_count"] = 2 if node["id"] == "R" else 1
    first = {**data["demands"][0], "duration_s": .04}
    second = {**first, "id": "contender", "start_s": .011, "duration_s": .039}
    third = {**first, "id": "after-release", "request_s": .06, "start_s": .07}
    data["demands"] = [first, second, third]
    result, _ = run_native(data, tmp_path, "contention")
    assert [d["status"] for d in result["demands"]] == ["approved", "rejected", "approved"]
    assert result["demands"][1]["delivered_pairs"] == 0
    assert result["demands"][2]["delivered_pairs"] > 0
    assert result["native_metrics"]["RESERVATION_REJECTED"] >= 1


def test_native_node_unavailable_then_new_request_after_recovery(tmp_path):
    data = payload()
    data["duration_s"] = .15
    first = {**data["demands"][0], "request_s": .001, "duration_s": .03}
    second = {**first, "id": "after-recovery", "request_s": .06, "start_s": .07, "duration_s": .05}
    data["demands"] = [first, second]
    data["disruptions"] = [{"time_s": 0, "node_id": "A", "down": True}, {"time_s": .05, "node_id": "A", "down": False}]
    result, _ = run_native(data, tmp_path, "recovery")
    assert result["demands"][0]["status"] == "pending_no_response"
    assert result["demands"][0]["delivered_pairs"] == 0
    assert result["demands"][1]["status"] == "approved"
    assert result["demands"][1]["delivered_pairs"] > 0
    assert result["event_counts"]["node_status_changed"] == 2
    assert result["event_counts"]["classical_send_blocked_node_down"] > 0


def test_native_bbppsw_recurrence_is_executed_not_injected(tmp_path):
    data = payload()
    # Two endpoint route with two stored pairs enables native purification.
    data["nodes"] = [node for node in data["nodes"] if node["id"] != "R"]
    for node in data["nodes"]:
        node["raw_fidelity"] = .8
    data["links"] = [{**data["links"][0], "id": "AB", "b": "B"}]
    data["demands"][0].update(memory_count=2, target_fidelity=.83)
    result, _ = run_native(data, tmp_path, "purification")
    assert result["native_metrics"].get("EP_SUCCESS", 0) > 0
    assert result["demands"][0]["delivered_pairs"] > 0
    assert all(d["fidelity"] >= .83 for d in result["demands"][0]["delivery_observations"])
    assert result["purification_consumption_records"]
    assert all(p["input_memory_count"] == 2 and p["measured_memory"] != p["kept_memory"]
               for p in result["purification_consumption_records"])


def rio_long_request():
    """Measured exported Rio geometry, explicit alternate native experiment."""
    data = payload()
    names = ("PUC-RIO", "IME-RIO", "CBPF", "UFRJ")
    data["nodes"] = [{**data["nodes"][0], "id": name, "memory_count": 8, "memory_efficiency": .8, "raw_fidelity": .95}
                     for name in names]
    lengths = (8.827837920194833, .9574112530273069, 14.261212296194413)
    delays = (.001043227458600196, .0010046881757093571, .00106983317655981)
    data["links"] = [{**data["links"][0], "id": f"RIO-{index}", "a": names[index], "b": names[index+1],
                      "length_km": length, "classical_delay_s": delay}
                     for index, (length, delay) in enumerate(zip(lengths, delays))]
    data["detector"]["efficiency"] = .8
    data["formalism"] = "ket_vector"
    data["duration_s"] = 1.5
    demand = {"id": "reserve-first", "source": names[0], "target": names[-1], "request_s": 0,
              "start_s": .01, "duration_s": .4, "memory_count": 4, "target_fidelity": .7}
    data["demands"] = [demand, {**demand, "id": "reserve-overlap", "request_s": .001, "start_s": .02, "duration_s": .2},
                        {**demand, "id": "reserve-after-release", "request_s": .45, "start_s": .47, "duration_s": .8, "memory_count": 2}]
    return data


def test_native_ket_long_rio_contention_release_is_supported(tmp_path):
    result, _ = run_native(rio_long_request(), tmp_path, "ket-long-rio")
    assert result["state_formalism"] == "ket_vector"
    assert [d["status"] for d in result["demands"]] == ["approved", "rejected", "approved"]
    assert result["demands"][0]["delivered_pairs"] > 0
    assert result["demands"][2]["delivered_pairs"] > 0
    assert result["demands"][1]["delivered_pairs"] == 0
    assert all(pair["retained_density"].get("state_model") == "conditional_pure_trajectory"
               for demand in result["demands"] for pair in demand["delivery_observations"])
    assert result["classical_receipts"]["max_delay_error_ps"] == 0


def test_native_ket_long_rio_node_outage_then_new_request(tmp_path):
    data = rio_long_request()
    first = {**data["demands"][0], "id": "during-unavailability", "request_s": .001, "duration_s": .1}
    recovered = {**first, "id": "after-recovery", "request_s": .3, "start_s": .32, "duration_s": .8, "memory_count": 2}
    data["demands"] = [first, recovered]
    data["disruptions"] = [{"time_s": 0, "node_id": "PUC-RIO", "down": True}, {"time_s": .25, "node_id": "PUC-RIO", "down": False}]
    result, _ = run_native(data, tmp_path, "ket-long-rio-recovery")
    assert result["demands"][0]["delivered_pairs"] == 0
    assert result["demands"][1]["status"] == "approved"
    assert result["demands"][1]["delivered_pairs"] > 0
