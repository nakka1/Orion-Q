"""RTL ABI/fixture tests; tools absent means not executed, never a board claim."""
from dataclasses import replace
import importlib.util
from pathlib import Path
import shutil

import pytest

from quantum_autopilot.lab.pl_accelerator import decode_histogram, encode_request
from quantum_autopilot.lab.pspl import CorrelationRequest, PSPLStateError, TimestampBlock, cpu_correlation


ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("orionq_rtl_validation", ROOT/"tools"/"run_pspl_rtl_validation.py")
RUNNER=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def test_rtl_fixture_reference_has_integer_edges_seeds_and_negatives():
    cases=RUNNER.fixture_cases()
    names={case["name"] for case in cases}
    assert {"inclusive_boundaries","ties_all_pairs","integer_above_float_precision",
        "positive_extended_arithmetic","negative_extended_arithmetic","pair_budget_atomic",
        "missing_last","early_last","stalled_input_watchdog","processing_watchdog"} <= names
    assert {f"seed_{seed}_events_256" for seed in (3,17,101)} <= names
    boundaries=next(case for case in cases if case["name"]=="inclusive_boundaries")
    assert boundaries["expected_counts"]==[1,1,1] and boundaries["expected_pairs"]==3
    ties=next(case for case in cases if case["name"]=="ties_all_pairs")
    assert ties["expected_pairs"]==4
    assert all(case["expected_counts"]==[0] and case["expected_pairs"]==0 for case in cases if case["expected_status"])


def test_rtl_fixture_binary_agrees_with_ps_adapter_and_cpu_result():
    for case in RUNNER.fixture_cases():
        if case["expected_status"]:
            continue
        wire=bytes.fromhex(case["block_wire_hex"])
        request=CorrelationRequest(**case["cpu_result"]["request"])
        values=RUNNER.CONFIG.unpack(bytes.fromhex(case["configuration_hex"]))
        header,events=encode_request(wire,request,request_id=values[3],cycle_budget=values[7])
        assert header.hex()==case["configuration_hex"]
        assert events==b"".join(RUNNER.EVENT.pack(*event) for event in case["events"])
        payload=b"".join(RUNNER.BIN.pack(count,index,0) for index,count in enumerate(case["expected_counts"]))
        result=decode_histogram(payload,returned_request_id=values[3],expected_request_id=values[3],block_wire=wire,request=request)
        assert result==case["cpu_result"]==cpu_correlation(TimestampBlock.from_wire(wire),request)


def test_rtl_origin_is_provenance_and_not_an_algorithm_switch():
    case=RUNNER.fixture_cases()[1]
    block=TimestampBlock.from_wire(bytes.fromhex(case["block_wire_hex"]))
    request=CorrelationRequest(**case["cpu_result"]["request"])
    physical_fixture=replace(block,origin="physical")
    assert encode_request(block.to_wire(),request,request_id=1)==encode_request(physical_fixture.to_wire(),request,request_id=1)
    assert cpu_correlation(block,request)["counts"]==cpu_correlation(physical_fixture,request)["counts"]
    assert block.to_wire()!=physical_fixture.to_wire()


@pytest.mark.parametrize("status", [1,2,4,8,16,32,64,128])
def test_rtl_terminal_flags_never_publish_partial_metrics(status):
    case=RUNNER.fixture_cases()[1]
    wire=bytes.fromhex(case["block_wire_hex"])
    request=CorrelationRequest(**case["cpu_result"]["request"])
    with pytest.raises(PSPLStateError):
        decode_histogram(RUNNER.BIN.pack(0,0xffffffff,status),returned_request_id=1,expected_request_id=1,
                         block_wire=wire,request=request)


def test_rtl_vectors_reproducible(tmp_path):
    first,second=tmp_path/"a.txt",tmp_path/"b.txt"
    RUNNER.write_vectors(first,RUNNER.fixture_cases())
    RUNNER.write_vectors(second,RUNNER.fixture_cases())
    assert first.read_bytes()==second.read_bytes()
    assert RUNNER.CONFIG.size==64 and RUNNER.EVENT.size==16 and RUNNER.BIN.size==16


@pytest.mark.skipif(not shutil.which("iverilog") or not shutil.which("vvp"),reason="Icarus RTL tools unavailable in PATH")
def test_rtl_simulation_when_tools_available(tmp_path):
    report=RUNNER.validate(tmp_path,iverilog=shutil.which("iverilog"),vvp=shutil.which("vvp"))
    assert report["rtl"]["status"]=="passed"
    assert report["physical_action_performed"] is False
