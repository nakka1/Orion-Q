"""Optimized observation copies preserve every value and independent ownership."""
from dataclasses import asdict
from quantum_autopilot.quantum_protocols.contracts import Acquisition, canonical


def acquisition(raw):
    return Acquisition(command_id="c", session_id="s", operation="satellite_block",
        source_id="fixture", origin="simulated", sequence=1, observed_at_s=1.,
        clock_offset_s=0., quantum_channel_ids=("q",), classical_channel_ids=("c",), raw=raw)


def test_copy_matches_dataclass_contract_and_preserves_ps_precision_and_containers():
    raw = {"timestamp_ps": list(range(10**18, 10**18 + 100)),
           "nested": [{"basis": "Z", "weights": (.1, None, True)}]}
    record = acquisition(raw)
    actual = record.to_dict()
    assert actual == asdict(record)
    assert canonical(actual) == canonical(asdict(record))
    actual["raw"]["timestamp_ps"].append(0)
    actual["raw"]["nested"][0]["basis"] = "X"
    assert raw["nested"][0]["basis"] == "Z"
    assert len(raw["timestamp_ps"]) == 100
    assert isinstance(actual["raw"]["nested"][0]["weights"], tuple)
