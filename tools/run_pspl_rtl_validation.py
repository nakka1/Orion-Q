#!/usr/bin/env python3
"""Compare original OrionQ RTL with CPU fixtures; never load a board or bitstream."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import re
import shutil
import struct
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
RTL = ROOT / "fpga" / "orionq_pl"
CONFIG = struct.Struct("<4sHHQIiiIQQqQ")
EVENT = struct.Struct("<qiI")
BIN = struct.Struct("<QII")
MAX_EVENTS = 256
MAX_BINS = 257


def configuration(*, request_id, event_count, channel_a=1, channel_b=2, cycle_budget=100_000,
                  bin_width_ps=1, radius_ps=10, delay_ps=0, max_pairs=10_000_000, magic=b"QCOR", major=1, minor=0):
    return CONFIG.pack(magic, major, minor, request_id, event_count, channel_a, channel_b,
                       cycle_budget, bin_width_ps, radius_ps, delay_ps, max_pairs)


def fixture_cases():
    """Known boundaries plus independent CPU goldens for three fixed seeds."""
    from quantum_autopilot.lab.pspl import CorrelationRequest, TimestampBlock, TimestampEvent, cpu_correlation
    cases = []

    def append(name, events, *, status=0, send_count=None, last_override=None, **options):
        request_id = len(cases) + 1
        options = dict(options)
        count = options.pop("event_count", len(events))
        wire = configuration(request_id=request_id, event_count=count, **options)
        bins, pairs, cpu, block_wire_hex = [0], 0, None, None
        if not status:
            parameters = {key: value for key, value in options.items()
                          if key in {"channel_a", "channel_b", "bin_width_ps", "radius_ps", "delay_ps", "max_pairs"}}
            defaults = {"channel_a":1, "channel_b":2, "bin_width_ps":1, "radius_ps":10}
            defaults.update(parameters)
            request = CorrelationRequest(**defaults)
            start = min((event[0] for event in events), default=0)
            end = max((event[0] for event in events), default=start) + 1
            block = TimestampBlock("rtl-fixture-session", request_id, "rtl-fixture-clock", "rtl-fixture-config",
                start, end-start, tuple(TimestampEvent(*event) for event in events), "simulated", "rtl-fixture", "fixture")
            cpu = cpu_correlation(block, request)
            bins, pairs = cpu["counts"], cpu["pairs"]
            block_wire_hex = block.to_wire().hex()
        send_count = len(events) if send_count is None else send_count
        lasts = [int(index + 1 == count) for index in range(send_count)]
        if last_override is not None:
            lasts = list(last_override)
        cases.append({"name": name, "configuration_hex": wire.hex(), "events": [list(e) for e in events[:send_count]],
            "last": lasts, "expected_status": status, "expected_counts": bins, "expected_pairs": pairs,
            "cpu_result": cpu, "block_wire_hex": block_wire_hex,
            "parameters": {key:value.hex() if isinstance(value,bytes) else value for key,value in options.items()}})

    append("empty", [])
    append("inclusive_boundaries", [(90,2,0),(100,1,0),(100,2,0),(110,2,0)], radius_ps=10, bin_width_ps=10)
    append("ties_all_pairs", [(100,1,0),(100,1,0),(100,2,0),(100,2,0)])
    append("negative_channel_and_delay", [(10,-2,0),(12,-1,0),(15,-2,0)], channel_a=-1, channel_b=-2, delay_ps=-2)
    append("integer_above_float_precision", [(2**53+99,1,0),(2**53+100,2,0),(2**53+103,2,0)], radius_ps=2)
    append("positive_extended_arithmetic", [(0,1,0),(2**63-2,2,0)], delay_ps=-(2**63)+1,
           radius_ps=2**63-1, bin_width_ps=2**63-1)
    append("negative_extended_arithmetic", [(0,2,0),(2**63-2,1,0)], delay_ps=2**63-1,
           radius_ps=2**63-1, bin_width_ps=2**63-1)
    for seed in (3,17,101):
        rng = random.Random(seed)
        for size in (8,32,64,256):
            events = sorted((rng.randrange(1000,1150), rng.choice((1,2,9)), 0) for _ in range(size))
            append(f"seed_{seed}_events_{size}", events, radius_ps=30, bin_width_ps=5, delay_ps=seed%7-3)
    append("pair_budget_atomic", [(100,1,0),(100,1,0),(100,2,0),(100,2,0)], max_pairs=3, status=16)
    append("same_channels", [], channel_a=1, channel_b=1, status=4)
    append("zero_channel_request", [], channel_a=0, status=4)
    append("event_count_cap", [], event_count=257, status=4)
    append("bin_cap", [], radius_ps=129, status=4)
    append("nondivisible_window", [], radius_ps=3, bin_width_ps=2, status=4)
    append("zero_bin_width", [], bin_width_ps=0, status=4)
    append("unsupported_magic", [], magic=b"FAIL", status=4)
    append("unsupported_version", [], major=2, status=4)
    append("loss_flag", [(100,1,1),(101,2,0)], status=1)
    append("error_flag", [(100,1,2),(101,2,0)], status=2)
    append("unknown_event_flags", [(100,1,4)], status=32)
    append("negative_timestamp", [(-1,1,0),(1,2,0)], status=32)
    append("reserved_zero_channel", [(100,0,0)], status=32)
    append("clock_regression", [(100,1,0),(99,2,0)], status=64)
    append("missing_last", [(100,1,0),(101,2,0)], last_override=(0,0), status=8)
    append("early_last", [(100,1,0)], event_count=2, last_override=(1,), status=8)
    append("stalled_input_watchdog", [], event_count=1, cycle_budget=60, status=128)
    append("processing_watchdog", [(100,1,0),(101,2,0)], radius_ps=1, cycle_budget=7, status=128)
    append("zero_watchdog_invalid", [], cycle_budget=0, status=4)
    return cases


def write_vectors(path, cases):
    lines = [str(len(cases))]
    for case in cases:
        config = bytes.fromhex(case["configuration_hex"])
        lines.append(f"{int.from_bytes(config,'little'):0128x} {len(case['events'])} "
                     f"{case['expected_status']:08x} {len(case['expected_counts'])} {case['expected_pairs']:016x}")
        for event, last in zip(case["events"], case["last"], strict=True):
            lines.append(f"{int.from_bytes(EVENT.pack(*event),'little'):032x} {last}")
        lines.extend(f"{count:016x}" for count in case["expected_counts"])
    Path(path).write_text("\n".join(lines)+"\n", encoding="ascii")


def _run(command, *, timeout_s=120):
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s, check=False)
    if completed.returncode:
        raise RuntimeError(f"RTL tool exit {completed.returncode}: {(completed.stdout+completed.stderr)[-12000:]}")
    return completed.stdout+completed.stderr


def validate(output, *, iverilog=None, vvp=None, yosys=None, ivl_base=None, generic_synthesis=False):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases = fixture_cases()
    write_vectors(output/"vectors.txt", cases)
    (output/"fixtures.json").write_text(json.dumps(cases, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    compiler = iverilog or shutil.which("iverilog")
    simulator = vvp or (str(Path(compiler).with_name("vvp")) if compiler else shutil.which("vvp"))
    synthesizer = yosys or shutil.which("yosys")
    report = {"schema": "orion.pspl.rtl-validation.v1", "abi": "orion.pspl.rtl.v1",
        "fixture_count": len(cases), "seeds": [3,17,101], "max_events": MAX_EVENTS, "max_bins": MAX_BINS,
        "rtl": {"status": "not_executed", "reason": "iverilog/vvp unavailable"},
        "synthesis_frontend": {"status": "not_executed", "reason": "yosys unavailable"},
        "generic_synthesis": {"status": "not_executed", "reason": "optional bounded-resource qualification; use --generic-synthesis"},
        "physical_action_performed": False, "physical_actuation_locked": True,
        "limits": ["original reference logic only; no board, BSP, XSA, bitstream or DMA driver",
            "no place/route, FPGA frequency, latency, optical/electrical or hardware safety qualification",
            "cycle budget uses local RTL clock cycles; timestamps use integer ps without a resolution claim",
            "all-pairs block profile; no cross-block coincidences, fidelity or QKD inference"],
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(RTL.glob("*")) if path.is_file()}}
    if compiler and simulator:
        started = time.perf_counter()
        outputs = {}
        for name in ("correlation", "fast_gate"):
            image = output/f"{name}.vvp"
            command = [compiler, "-g2012"] + (["-B", ivl_base] if ivl_base else [])
            command += ["-s", f"tb_{name}", "-o", str(image), str(RTL/f"orionq_{name}.v"), str(RTL/f"tb_{name}.sv")]
            compile_log = _run(command)
            log = _run([simulator,str(image)]+([f"+vectors={output/'vectors.txt'}"] if name=="correlation" else []))
            if f"RTL_{name.upper()}_PASS" not in log:
                raise RuntimeError("RTL simulation did not publish its acceptance marker")
            (output/f"{name}.log").write_text(compile_log+log, encoding="utf-8")
            outputs[name] = log.strip()
        report["rtl"] = {"status": "passed", "duration_s": time.perf_counter()-started,
            "compiler": _run([compiler,"-V"]+(["-B",ivl_base] if ivl_base else []))[0:200],
            "outputs": outputs, "fixture_equivalence": "full histogram, pairs, status, identity and TLAST",
            "backpressure": "seven held cycles per result; no duplicate/lost result frames"}
    if synthesizer:
        synth_logs = {}
        for name in ("correlation", "fast_gate"):
            script = f"read_verilog {RTL/f'orionq_{name}.v'}; hierarchy -check -top orionq_{name}; proc; opt; memory_collect; check; stat"
            log = _run([synthesizer,"-Q","-p",script])
            (output/f"{name}-yosys.log").write_text(log, encoding="utf-8")
            check = re.search(r"Found and reported (\d+) problems", log)
            if check is None or int(check[1]) != 0:
                raise RuntimeError("Yosys structural check failed")
            synth_logs[name] = {"structural_problems": int(check[1])}
        report["synthesis_frontend"] = {"status": "passed", "version": _run([synthesizer,"-V"]).strip(),
            "scope": "Verilog frontend, hierarchy, process lowering, optimized memory cells and structural checks",
            "modules": synth_logs, "technology_mapping": "not_executed"}
        if generic_synthesis:
            generic_logs = {}
            for name in ("correlation", "fast_gate"):
                logfile = output/f"{name}-generic-synthesis.log"
                script = f"read_verilog {RTL/f'orionq_{name}.v'}; synth -top orionq_{name} -noabc; check; stat"
                started = time.perf_counter()
                _run([synthesizer,"-Q","-q","-l",str(logfile),"-p",script], timeout_s=600)
                log = logfile.read_text(encoding="utf-8")
                checks = re.findall(r"Found and reported (\d+) problems", log)
                cells = re.findall(r"Number of cells:\s+(\d+)", log)
                if not checks or any(int(value) for value in checks) or not cells:
                    raise RuntimeError("Yosys generic synthesis check failed")
                generic_logs[name] = {"structural_problems": 0, "generic_cells": int(cells[-1]),
                    "duration_s": time.perf_counter()-started,
                    "command": script, "log_sha256": hashlib.sha256(logfile.read_bytes()).hexdigest()}
            report["generic_synthesis"] = {"status": "passed", "modules": generic_logs,
                "scope": "generic Boolean cells without ABC, FPGA primitives, place/route or timing closure",
                "board_technology_mapping": "not_executed"}
    (output/"report.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iverilog")
    parser.add_argument("--vvp")
    parser.add_argument("--yosys")
    parser.add_argument("--ivl-base")
    parser.add_argument("--generic-synthesis", action="store_true",
                        help="Run optional full generic synthesis (correlation may require several minutes and multiple GB RAM)")
    arguments=parser.parse_args(argv)
    report=validate(**vars(arguments))
    print(json.dumps({"rtl": report["rtl"]["status"], "synthesis_frontend": report["synthesis_frontend"]["status"],
                      "generic_synthesis": report["generic_synthesis"]["status"],
                      "fixture_count": report["fixture_count"], "physical_action_performed": False}))
    return 0 if report["rtl"]["status"]=="passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
