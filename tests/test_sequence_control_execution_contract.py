"""The independent native executor only admits bounded live classical receipts."""
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("independent_mixed_native_contract", Path(__file__).resolve().parents[1] / "tools" / "run_mixed_sequence_nodes.py")
NATIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NATIVE)


def inputs():
    nodes = {name: SimpleNamespace(operational=True, cchannels={other: object()})
             for name, other in (("A", "B"), ("B", "A"))}
    links = [{"link_id": "L-A-B", "a": "A", "b": "B"}]
    receipt = {"status": "reserved", "request_id": "request-1", "route": ["A", "B"], "links": ["L-A-B"],
        "created_ns": 10, "expires_ns": 1000, "physical_action_performed": False, "quantum_result": None,
        "request": {"request_id": "request-1", "source": "A", "target": "B", "operation": "classical_control", "ttl_s": 1}}
    receipt["receipt_sha256"] = NATIVE._receipt_hash(receipt)
    return receipt, nodes, links


def test_fresh_classical_receipt_preserves_exact_native_route():
    receipt, nodes, links = inputs()
    parsed = NATIVE._validate_control_receipt(receipt, nodes, links, set(), now_ns=100)
    assert parsed["route"] == ["A", "B"] and parsed["links"] == ["L-A-B"]
    assert parsed["receipt_sha256"] == receipt["receipt_sha256"]


@pytest.mark.parametrize("fault", ["tamper", "expired", "replay", "released", "physical", "quantum", "wrong_link", "offline"])
def test_invalid_or_quantum_receipt_cannot_schedule_native_control(fault):
    receipt, nodes, links = inputs()
    changed, used = deepcopy(receipt), set()
    if fault == "tamper":
        changed["request_id"] = "tampered"
    elif fault == "expired":
        changed["expires_ns"] = 100
    elif fault == "replay":
        used.add(receipt["request_id"])
    elif fault == "released":
        changed["status"] = "released"
    elif fault == "physical":
        changed["physical_action_performed"] = True
    elif fault == "quantum":
        changed["request"]["operation"] = "entanglement"
    elif fault == "wrong_link":
        changed["links"] = ["unconfigured-link"]
    elif fault == "offline":
        nodes["B"].operational = False
    if fault != "tamper":
        changed.pop("receipt_sha256")
        changed["receipt_sha256"] = NATIVE._receipt_hash(changed)
    with pytest.raises(ValueError):
        NATIVE._validate_control_receipt(changed, nodes, links, used, now_ns=100)


def test_duplicate_command_fields_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        NATIVE._unique_object([("type", "execute_control"), ("type", "stop")])
