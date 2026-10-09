#!/usr/bin/env python3
"""Synthesise the two modules for an open FPGA target and report what they cost.

    python scripts/synthesise.py --target ecp5 --bits 14 --rounds 5

Everything before this script measured arithmetic, which is portable.  This
measures a *device*: Yosys maps the RTL to a real cell library and nextpnr
places, routes and times it, so the numbers that come out — LUTs, flip-flops,
DSP slices, block RAM, and the frequency the placed design actually closes at —
belong to that part and not to FPGAs in general.

The targets are the open ones, because those are the toolchains that exist here:

``ecp5``
    Lattice ECP5 (``LFE5U-25F``), the part a metropolitan-link controller would
    plausibly use — enough block RAM to hold the ensemble and enough DSP for the
    MAC without being a data-centre part.

``ice40``
    Lattice iCE40 ``hx8k``, deliberately small — a third of the ECP5's logic and
    a fraction of its block RAM.  Included because a design that fits here fits
    anywhere, and because it is where the ensemble's memory budget stops being
    an abstraction.  The smaller ``up5k`` was tried first and refused for a
    reason worth recording: its 48-pin package has fewer I/O than the
    classifier's boundary needs, which is a packaging problem, not a logic one.

A Xilinx or Intel part would need Vivado or Quartus, which are licensed and not
installed; the cell counts below do not transfer to them directly, and quoting
them as if they did would be inventing numbers.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

#: Device, package and the nextpnr binary for each open target.
TARGETS = {
    "ecp5": {"synth": "synth_ecp5", "binary": "nextpnr-ecp5",
             "arguments": ["--25k", "--package", "CABGA381", "--speed", "6"],
             "part": "LFE5U-25F"},
    "ice40": {"synth": "synth_ice40", "binary": "nextpnr-ice40",
              "arguments": ["--hx8k", "--package", "ct256"],
              "part": "iCE40 HX8K"},
}

#: Cell families worth reporting, and what to call them.  Yosys names them per
#: architecture, so the mapping is per target rather than universal.
CELLS = {
    "ecp5": {"LUT": ("LUT4", "TRELLIS_COMB"), "FF": ("TRELLIS_FF", "DFF"),
             "DSP": ("MULT18X18D", "TRELLIS_MULT18X18D"),
             "BRAM": ("DP16KD", "PDPW16KD")},
    "ice40": {"LUT": ("SB_LUT4",), "FF": ("SB_DFF", "SB_DFFE", "SB_DFFSR",
                                          "SB_DFFESR", "SB_DFFR", "SB_DFFER"),
              "DSP": ("SB_MAC16",), "BRAM": ("SB_RAM40_4K",)},
}


def _require(*binaries: str) -> None:
    """Fail early and by name, rather than inside a subprocess."""
    missing = [name for name in binaries if shutil.which(name) is None]
    if missing:
        raise SystemExit(
            f"faltam ferramentas de síntese: {', '.join(missing)}. "
            "Instale yosys e nextpnr, ou rode sem --place para só mapear.")


def synthesise(sources, top: str, include: Path, build: Path,
               target: str) -> dict:
    """Map one design to the target's cell library and count what came out.

    Yosys runs *in* the directory holding the generated memories, because the
    top levels initialise their ROM and block RAM with ``$readmemh`` and those
    paths resolve against the working directory.  Synthesising a design whose
    memories are empty would report the area of a machine that computes nothing.
    """
    settings = TARGETS[target]
    netlist = build / f"{top}.json"
    statistics = build / f"{top}_stat.json"

    script = "; ".join([
        *(f"read_verilog -sv -I {include} {source}" for source in sources),
        f"{settings['synth']} -top {top} -json {netlist}",
        f"tee -q -o {statistics} stat -json",
    ])
    result = subprocess.run(["yosys", "-p", script], cwd=include,
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout[-4000:])
        print(result.stderr[-2000:])
        raise SystemExit(f"yosys falhou em {top}")

    counts = json.loads(statistics.read_text())
    # ``stat`` reports per module; the top level's own count excludes the
    # submodule it instantiates, so the design's cells are the whole design's.
    cells = counts.get("design", counts["modules"][f"\\{top}"])
    by_type = cells["num_cells_by_type"]
    tally = {name: sum(by_type.get(kind, 0) for kind in kinds)
             for name, kinds in CELLS[target].items()}
    tally["células"] = int(cells["num_cells"])
    tally["netlist"] = netlist
    return tally


def place(netlist: Path, top: str, build: Path, target: str,
          frequency_mhz: float) -> dict:
    """Place and route, and read back the frequency the design actually closes at.

    The requested frequency matters: nextpnr stops optimising once it meets the
    constraint, so a design asked for 1 MHz reports 1 MHz and tells you nothing.
    Asking for more than the part can do makes it report its best, which is the
    figure worth quoting.
    """
    settings = TARGETS[target]
    report = build / f"{top}_route.json"
    command = [settings["binary"], *settings["arguments"],
               "--json", str(netlist), "--freq", str(frequency_mhz),
               "--report", str(report), "--placer", "heap"]
    result = subprocess.run(command, cwd=build, capture_output=True, text=True)

    # nextpnr exits non-zero when timing is not met, which is the expected
    # outcome of asking for more than the part can do — the frequency it did
    # reach is in the log either way.
    log = result.stdout + result.stderr
    achieved = re.findall(r"Max frequency for clock\s+'[^']*':\s+([\d.]+)\s+MHz", log)
    if not achieved:
        print(log[-3000:])
        raise SystemExit(f"nextpnr não reportou frequência para {top}")

    utilisation = {}
    if report.exists():
        payload = json.loads(report.read_text())
        for name, entry in payload.get("utilization", {}).items():
            if entry.get("used"):
                utilisation[name] = (entry["used"], entry.get("available", 0))

    return {"fmax_mhz": float(achieved[-1]), "utilisation": utilisation,
            "met": result.returncode == 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", default="ecp5", choices=sorted(TARGETS))
    parser.add_argument("--bits", type=int, default=14)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--sync-bits", type=int, default=12)
    parser.add_argument("--freq", type=float, default=400.0,
                        help="frequency to ask for; above the part's ceiling, "
                             "so nextpnr reports its best rather than stopping "
                             "at a constraint that was easy to meet")
    parser.add_argument("--build", type=Path, default=Path("runs/hardware/synth"))
    parser.add_argument("--out", type=Path, default=Path("runs/hardware"))
    parser.add_argument("--rtl", type=Path, default=Path("rtl"))
    parser.add_argument("--no-place", action="store_true",
                        help="map only; skip place-and-route and Fmax")
    args = parser.parse_args()

    _require("yosys", *([] if args.no_place else [TARGETS[args.target]["binary"]]))

    build = (REPO_ROOT / args.build).resolve()
    build.mkdir(parents=True, exist_ok=True)

    # The headers the two modules elaborate against are written by the
    # cosimulation drivers, so synthesis measures exactly the configuration that
    # was verified — not a plausible-looking one chosen here.
    print("[synth] gerando os cabeçalhos pelas cossimulações")
    for command in (
            [sys.executable, "scripts/cosimulate_sync.py",
             "--bits", str(args.sync_bits), "--vectors", "64"],
            [sys.executable, "scripts/cosimulate_detector.py",
             "--rounds", str(args.rounds), "--bits", str(args.bits),
             "--vectors", "32"]):
        result = subprocess.run(command, cwd=REPO_ROOT, capture_output=True,
                                text=True)
        if result.returncode != 0:
            print(result.stdout[-2000:])
            raise SystemExit(f"{command[1]} falhou; sintetizar uma configuração "
                             "não verificada não teria sentido")

    rtl = REPO_ROOT / args.rtl
    modules = (
        ("sync_top", [rtl / "sync_predictor.v", rtl / "sync_top.v"],
         REPO_ROOT / "runs/hardware/rtl", f"{args.sync_bits} bits, 9 taps"),
        ("detector_top", [rtl / "tree_classifier.v", rtl / "detector_top.v"],
         REPO_ROOT / "runs/hardware/rtl_tree",
         f"{args.bits} bits, {args.rounds} rodadas"),
    )

    rows = []
    for top, sources, include, description in modules:
        print(f"\n[synth] {top} ({description}) em {TARGETS[args.target]['part']}")
        tally = synthesise(sources, top, include, build, args.target)
        row = {"alvo": TARGETS[args.target]["part"], "módulo": top,
               "configuração": description, **{k: v for k, v in tally.items()
                                               if k != "netlist"}}

        if not args.no_place:
            routed = place(tally["netlist"], top, build, args.target, args.freq)
            row["fmax_mhz"] = routed["fmax_mhz"]
            for name, (used, available) in routed["utilisation"].items():
                row[f"uso_{name}"] = f"{used}/{available}"
            print(f"  {row['LUT']} LUT, {row['FF']} FF, {row['DSP']} DSP, "
                  f"{row['BRAM']} BRAM, {routed['fmax_mhz']:.1f} MHz")
        else:
            print(f"  {row['LUT']} LUT, {row['FF']} FF, {row['DSP']} DSP, "
                  f"{row['BRAM']} BRAM")
        rows.append(row)

    frame = pd.DataFrame(rows)
    destination = (REPO_ROOT / args.out).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    frame.to_csv(destination / f"synthesis_{args.target}.csv", index=False)

    print(f"\nsíntese em {TARGETS[args.target]['part']}")
    print(frame.to_string(index=False))
    print(f"\n[synth] salvo em {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
