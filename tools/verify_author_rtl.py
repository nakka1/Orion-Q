"""Simulate (Icarus Verilog) and synthesise (Yosys) the author's Verilog against the author's own fixed-point references.

The two qnet_autopilot versions ship Verilog that their author could not simulate
("Este testbench nunca foi executado": no HDL simulator in the author's container) or
co-simulated only at export time. This tool runs every piece of it, never trusting the
RTL to check itself:

* **Compensator MAC, Ridge** (``external/qnet_network/rtl/ridge``): the author's bench,
  unchanged, against the golden vectors of ``deploy/fixed_point.py``.
* **Compensator MAC, MLP** (``rtl/mlp``): the author's bench compares the first layer's
  output with the *network's* output (``vectors_out.hex`` holds 64 x 3 words, the bench
  reads 64 x 192), so it cannot pass. The three layers are rebuilt instead with the
  author's ``FixedLayer`` from the shipped memories and the formats of the shipped
  README; the one bias format per layer that makes the reference reproduce every golden
  output is found by exhaustive search (it must be unique), and a chained three-engine
  bench then checks every hidden and output word.
* **CORDIC** (``qnet_cordic.v``): vectoring and rotation against ``cordic_atan2`` and
  ``cordic_sincos``, on the log map's domain (``y >= 0``, where the sine is a norm) and,
  as a diagnostic, outside it.
* **QKD synchroniser** (``modelos/fpga`` of the pinned ``modelo_qkd_ml.zip``): the shipped
  12-bit coefficient ROM, rebuilt as the author's ``QuantisedRidge`` (codes must match),
  over rows of the author's timing pipeline.
* **QKD tree classifier**: the author's boosting detector is retrained with the author's
  pipeline (the model the safe export proved identical to the pinned pickle, which is
  never loaded), pruned and quantised as the export did (5 rounds, 14 bits); the node,
  tree and baseline memories must equal the shipped ones byte for byte before the bench
  runs, in both memory modes. Needs scikit-learn 1.8.0 (the author's); skipped otherwise.
* **Synthesis**: Yosys ``synth_xilinx`` (logic mapping only: no placement, routing or
  timing) of every top level, for resource counts.

    python tools/verify_author_rtl.py --qkd-zip modelo_qkd_ml.zip --output outputs/author-rtl-v1
"""
import argparse
import hashlib
import importlib.util
import io
import itertools
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import zipfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
RTL = ROOT / "external" / "qnet_network" / "rtl"
QKD_SCRIPTS = ROOT / "external" / "qnet_qkd" / "scripts"
FPGA = "modelo_qkd_ml/modelos/fpga/"


def tool_versions():
    iverilog = subprocess.run(["iverilog", "-V"], capture_output=True, text=True).stdout.splitlines()[0]
    yosys = subprocess.run(["yosys", "-V"], capture_output=True, text=True).stdout.strip()
    return {"iverilog": iverilog, "yosys": yosys, "python": platform.python_version(),
            "numpy": np.__version__}


def run(command, cwd, timeout=1800):
    done = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, command))} failed:\n{done.stdout[-2000:]}\n{done.stderr[-2000:]}")
    return done.stdout


def simulate(sources, cwd, include=None):
    command = ["iverilog", "-g2012", "-o", "simulation"]
    if include is not None:
        command += ["-I", str(include)]
    run(command + [str(source) for source in sources], cwd)
    return run(["vvp", "-n", "simulation"], cwd)


def words(path, width=16):
    values = [int(line, 16) for line in Path(path).read_text().split()]
    return np.array([v - (1 << width) if v >= 1 << (width - 1) else v for v in values], dtype=np.int64)


def hexwords(values, width=16):
    return "\n".join(format(int(v) & ((1 << width) - 1), f"0{(width + 3) // 4}x") for v in values) + "\n"


def load_script(name, directory=QKD_SCRIPTS):
    """An author script by path; its own ``sys.path`` insertions find task5 of the same tree."""
    spec = importlib.util.spec_from_file_location(f"orion_author_{name}", directory / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# compensator
# ---------------------------------------------------------------------------

def readme_layers(path):
    """The per-layer table of the shipped README: sizes, Q formats, shift, accumulator, ReLU."""
    layers = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 9 and cells[0].isdigit():
            fraction = [int(re.fullmatch(r"Q(\d+)\.(\d+)", cell).group(2)) for cell in cells[3:6]]
            layers.append({"inputs": int(cells[1]), "outputs": int(cells[2]), "input_fraction": fraction[0],
                           "weight_fraction": fraction[1], "output_fraction": fraction[2], "shift": int(cells[6]),
                           "accumulator": int(cells[7].split()[0]), "relu": cells[8] == "sim"})
    return layers


def bench_bias_shift(path):
    return int(re.search(r"\.BIAS_SHIFT\((\d+)\)", Path(path).read_text()).group(1))


def fixed_network(directory, bias_fractions):
    from qnet_network.deploy.fixed_point import FixedLayer, FixedNetwork, QFormat

    spec = readme_layers(directory / "README.md")
    layers = []
    for index, (layer, bias_fraction) in enumerate(zip(spec, bias_fractions)):
        weight = words(directory / f"qnet_layer{index}_w.hex").reshape(layer["outputs"], layer["inputs"]).T
        layers.append(FixedLayer(weight=weight.copy(), bias=words(directory / f"qnet_layer{index}_b.hex"),
                                 weight_format=QFormat(16, layer["weight_fraction"]),
                                 bias_format=QFormat(16, bias_fraction),
                                 output_format=QFormat(16, layer["output_fraction"]), relu=layer["relu"]))
    return FixedNetwork(layers=layers, input_format=QFormat(16, spec[0]["input_fraction"])), spec


def recover_bias_formats(directory):
    """Layer 0's bias format is in the author's bench; the others are the unique ones that reproduce the goldens."""
    spec = readme_layers(directory / "README.md")
    first = spec[0]["input_fraction"] + spec[0]["weight_fraction"] - bench_bias_shift(directory / "qnet_tb.v")
    stimulus = words(directory / "vectors_in.hex").reshape(-1, spec[0]["inputs"])
    golden = words(directory / "vectors_out.hex").reshape(-1, spec[-1]["outputs"])
    matches = []
    for rest in itertools.product(range(16), repeat=len(spec) - 1):
        network, _ = fixed_network(directory, (first, *rest))
        if np.array_equal(network.forward(stimulus), golden):
            matches.append((first, *rest))
    return matches, stimulus, golden


CHAIN_BENCH = """`timescale 1ns / 1ps
// ORION-Q chained bench: the author's qnet_mac, one engine per layer, every word checked.
module chain_tb;
{params}
    reg clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    integer vector, errors [0:{last}];
{memories}
    initial begin
{loads}
        repeat (4) @(posedge clk);
        rst_n = 1;
        for (vector = 0; vector < VECTORS; vector = vector + 1) begin
{steps}
        end
        $display("RESULT{report}", {counts});
        $finish;
    end
endmodule
"""


def chained_bench(spec, bias_shifts, vectors):
    last = len(spec) - 1
    params = [f"    localparam integer VECTORS = {vectors};"]
    memories, loads, steps = [], [f"        for (vector = 0; vector <= {last}; vector = vector + 1) "
                                  "errors[vector] = 0;"], []
    for k, layer in enumerate(spec):
        n_in, n_out = layer["inputs"], layer["outputs"]
        source = "stimulus[vector*%d + x%d]" % (n_in, k) if k == 0 else f"act{k - 1}[x{k}]"
        memories.append(f"""    reg signed [15:0] w{k} [0:{n_in * n_out - 1}];
    reg signed [15:0] b{k} [0:{n_out - 1}];
    reg signed [15:0] act{k} [0:{n_out - 1}];
    reg signed [15:0] expected{k} [0:{vectors * n_out - 1}];
    reg start{k} = 0;
    integer unit{k};
    wire [$clog2({n_in})-1:0] x{k};
    wire [$clog2({n_in * n_out})-1:0] wa{k};
    wire [$clog2({n_out})-1:0] ba{k};
    wire signed [15:0] y{k};
    wire valid{k}, done{k};
    qnet_mac #(.IN_WIDTH(16), .W_WIDTH(16), .OUT_WIDTH(16), .ACC_WIDTH({layer['accumulator']}),
               .INPUTS({n_in}), .OUTPUTS({n_out}), .SHIFT({layer['shift']}), .BIAS_SHIFT({bias_shifts[k]}),
               .RELU(1'b{1 if layer['relu'] else 0}))
    engine{k} (.clk(clk), .rst_n(rst_n), .start(start{k}), .x_data({source}), .x_addr(x{k}),
               .w_data(w{k}[wa{k}]), .w_addr(wa{k}), .b_data(b{k}[ba{k}]), .b_addr(ba{k}),
               .y_data(y{k}), .y_valid(valid{k}), .done(done{k}));""")
        loads.append(f'        $readmemh("qnet_layer{k}_w.hex", w{k}); $readmemh("qnet_layer{k}_b.hex", b{k}); '
                     f'$readmemh("expected{k}.hex", expected{k});')
        steps.append(f"""            @(posedge clk); start{k} = 1; @(posedge clk); start{k} = 0;
            unit{k} = 0;
            while (unit{k} < {n_out}) begin
                @(posedge clk);
                if (valid{k}) begin
                    act{k}[unit{k}] = y{k};
                    if (y{k} !== expected{k}[vector*{n_out} + unit{k}]) errors[{k}] = errors[{k}] + 1;
                    unit{k} = unit{k} + 1;
                end
            end""")
    loads.insert(0, '        $readmemh("vectors_in.hex", stimulus);')
    memories.insert(0, f"    reg signed [15:0] stimulus [0:{vectors * spec[0]['inputs'] - 1}];")
    return CHAIN_BENCH.format(params="\n".join(params), last=last, memories="\n".join(memories),
                              loads="\n".join(loads), steps="\n".join(steps),
                              report="".join(f" layer{k} %0d" for k in range(len(spec))),
                              counts=", ".join(f"errors[{k}]" for k in range(len(spec))))


def compensator(work):
    out = {}
    ridge = work / "ridge"
    shutil.copytree(RTL / "ridge", ridge)
    text = simulate(["qnet_tb.v", "qnet_mac.v"], ridge)
    out["ridge_author_bench"] = {"output": text.strip().splitlines()[0], "passed": "PASS" in text}

    mlp = work / "mlp_author"
    shutil.copytree(RTL / "mlp", mlp)
    text = simulate(["qnet_tb.v", "qnet_mac.v"], mlp)
    spec = readme_layers(mlp / "README.md")
    golden_words = len(words(mlp / "vectors_out.hex"))
    out["mlp_author_bench"] = {"output": text.strip().splitlines()[:3] + text.strip().splitlines()[-1:],
                               "passed": "PASS" in text, "golden_words": golden_words,
                               "bench_expects": 64 * spec[0]["outputs"],
                               "diagnosis": "vectors_out.hex holds the network output (64 x 3); the bench compares the "
                                            "first layer's 192 outputs per vector with it"}

    matches, stimulus, golden = recover_bias_formats(RTL / "mlp")
    if len(matches) != 1:
        raise RuntimeError(f"bias formats not identified uniquely: {matches}")
    network, spec = fixed_network(RTL / "mlp", matches[0])
    chain = work / "mlp_chain"
    shutil.copytree(RTL / "mlp", chain)
    activation, fraction = stimulus, spec[0]["input_fraction"]
    bias_shifts = []
    for k, layer in enumerate(network.layers):
        product = fraction + layer.weight_format.fraction
        bias_shifts.append(product - layer.bias_format.fraction)
        activation = np.vstack([layer.forward(row, network.input_format if k == 0 else network.layers[k - 1]
                                              .output_format) for row in activation])
        (chain / f"expected{k}.hex").write_text(hexwords(activation.reshape(-1)))
        fraction = layer.output_format.fraction
    if not np.array_equal(activation, golden):
        raise RuntimeError("layer-by-layer reference differs from the goldens")
    (chain / "chain_tb.v").write_text(chained_bench(spec, bias_shifts, len(stimulus)))
    text = simulate(["chain_tb.v", "qnet_mac.v"], chain)
    counts = [int(value) for value in re.search(r"RESULT(.*)", text).group(1).split()[1::2]]
    out["mlp_chain"] = {"bias_fractions": list(matches[0]), "bias_shifts": bias_shifts,
                        "candidates_tried": 16 ** (len(spec) - 1), "unique": True,
                        "words_checked": [len(stimulus) * layer["outputs"] for layer in spec],
                        "mismatches": counts, "passed": not any(counts)}
    out["cordic"] = cordic(work, RTL / "ridge" / "qnet_cordic.v")
    for name in ("ridge", "mlp"):
        out[f"{name}_cordic_identical"] = (RTL / name / "qnet_cordic.v").read_bytes() == \
            (RTL / "ridge" / "qnet_cordic.v").read_bytes()
    return out


CORDIC_BENCH = """`timescale 1ns / 1ps
module cordic_tb;
    localparam integer CASES = {cases};
    reg clk = 0, rst_n = 0, start = 0, vectoring = 0;
    always #5 clk = ~clk;
    reg signed [31:0] stimulus [0:3*CASES-1];
    reg signed [31:0] x_in, y_in, angle_in;
    wire signed [31:0] x_out, y_out, angle_out;
    wire valid;
    integer k, handle;
    qnet_cordic #(.WIDTH(32), .FRACTION(20), .ITERATIONS(8)) dut (
        .clk(clk), .rst_n(rst_n), .start(start), .vectoring(vectoring), .x_in(x_in), .y_in(y_in),
        .angle_in(angle_in), .x_out(x_out), .y_out(y_out), .angle_out(angle_out), .valid(valid));
    initial begin
        $readmemh("cordic_in.hex", stimulus);
        handle = $fopen("cordic_out.txt", "w");
        repeat (3) @(posedge clk);
        rst_n = 1;
        for (k = 0; k < CASES; k = k + 1) begin
            vectoring = stimulus[3*k] != 0;
            x_in = stimulus[3*k + 1]; y_in = stimulus[3*k + 2]; angle_in = stimulus[3*k + 2];
            @(posedge clk); start = 1; @(posedge clk); start = 0;
            while (!valid) @(posedge clk);
            $fwrite(handle, "%0d %0d %0d\\n", x_out, y_out, angle_out);
        end
        $fclose(handle);
        $finish;
    end
endmodule
"""


def cordic(work, source, count=3000, seed=20261022):
    from qnet_network.deploy.fixed_point import CordicConfig, cordic_atan2, cordic_sincos

    config = CordicConfig(8, 20, 32)
    rng = np.random.default_rng(seed)
    scale = 1 << config.fraction
    cases = []
    for _ in range(count):   # log-map domain: y is a norm (>= 0), x any sign
        angle, radius = rng.uniform(0, np.pi), rng.uniform(1e-3, 1.9)
        cases.append(("vector_domain", int(round(radius * np.cos(angle) * scale)),
                      int(round(radius * np.sin(angle) * scale))))
    for x, y in ((scale, 0), (-scale, 0), (0, scale), (0, 0), (-scale, 1), (-scale, 2)):
        cases.append(("vector_edge", x, y))
    for _ in range(count // 3):   # outside: third and fourth quadrants
        angle, radius = rng.uniform(-np.pi, 0), rng.uniform(1e-3, 1.9)
        x, y = int(round(radius * np.cos(angle) * scale)), int(round(radius * np.sin(angle) * scale))
        cases.append(("vector_outside_q3" if x < 0 else "vector_outside_q4", x, y))
    half_pi = int(np.floor(np.pi / 2 * scale + 0.5))
    for _ in range(count):
        cases.append(("rotation", 0, int(rng.integers(-2 * half_pi, 2 * half_pi + 1))))
    directory = work / "cordic"
    directory.mkdir()
    shutil.copy(source, directory / "qnet_cordic.v")
    (directory / "cordic_in.hex").write_text(hexwords(
        [value for kind, x, y in cases for value in (0 if kind == "rotation" else 1, x, y)], 32))
    (directory / "cordic_tb.v").write_text(CORDIC_BENCH.format(cases=len(cases)))
    simulate(["cordic_tb.v", "qnet_cordic.v"], directory)
    rtl = np.loadtxt(directory / "cordic_out.txt", dtype=np.int64, ndmin=2)
    tally = {}
    for (kind, x, y), (x_out, y_out, angle_out) in zip(cases, rtl):
        if kind == "rotation":
            same = (x_out, y_out) == cordic_sincos(y, config)
        else:
            angle, magnitude = cordic_atan2(y, x, config)
            same = (angle_out, x_out) == (angle, magnitude)
        entry = tally.setdefault(kind, {"cases": 0, "mismatches": 0})
        entry["cases"] += 1
        entry["mismatches"] += int(not same)
    return tally


# ---------------------------------------------------------------------------
# QKD FPGA
# ---------------------------------------------------------------------------

def extract_fpga(archive_path, work):
    raw = Path(archive_path).read_bytes()
    target = work / "fpga"
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for name in archive.namelist():
            if name.startswith(FPGA) and not name.endswith("/"):
                path = target / name[len(FPGA):]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(name))
    return target, hashlib.sha256(raw).hexdigest()


def qkd_sync(fpga, work, archive_path, vectors=512):
    cosim = load_script("cosimulate_sync")
    from qnet_qkd.hardware.fixed_point import quantise_ridge
    from qnet_qkd.qkd.timing import build_sync_dataset

    t5 = sys.modules["task5"]
    shipped = fpga / "sincronizador_12b"
    params = {k: int(v) for k, v in re.findall(r"`define (SP_\w+)\s+(-?\d+)", (shipped / "sync_params.vh").read_text())}
    features, _, _, _ = build_sync_dataset(t5.make_timing_trajectories(t5.SYNC_TRAIN_SEEDS, t5.LINK.length_km),
                                           t5.make_gate(), rng=20261022)
    codes = words(shipped / "coefficients.hex", params["SP_WORD"])
    intercept = words(shipped / "intercept.hex", params["SP_ACC_W"])[0]

    class Shipped:   # the ROM, read back as coefficients in the shipped formats
        coef_ = codes / 2.0 ** 10
        intercept_ = intercept / 2.0 ** 18

    model = quantise_ridge(Shipped, features, word_bits=params["SP_WORD"])
    identity = {"coefficient_codes_equal": np.array_equal(model.coefficient_codes, codes),
                "coefficient_format": model.coefficient_format.describe(),
                "accumulator_format": model.accumulator_format.describe()}
    rng = np.random.default_rng(20261022)
    sample = features[np.sort(rng.choice(len(features), size=vectors, replace=False))]
    build = work / "sync"
    build.mkdir()
    cosim.write_vectors(build, model, sample)
    for name in ("coefficients.hex", "intercept.hex"):
        identity[f"{name}_identical"] = (build / name).read_bytes() == (shipped / name).read_bytes()
    ours = (build / "sync_params.vh").read_text().replace(f"SP_VECTORS {vectors}", "SP_VECTORS 64")
    identity["sync_params_identical_but_vector_count"] = ours == (shipped / "sync_params.vh").read_text()
    simulate([fpga / "rtl" / "sync_predictor.v", fpga / "rtl" / "tb_sync_predictor.v"], build, include=build)
    emitted = np.loadtxt(build / "rtl_result.txt", dtype=np.int64)
    expected = model.accumulator_format.to_integer(model.predict(sample))

    # Traceability: the shipped ROM against the exported model (sincronizador_ridge.json).
    exported = json.loads(zipfile.ZipFile(archive_path).read("modelo_qkd_ml/modelos/sincronizador_ridge.json"))

    class Exported:
        coef_ = np.asarray(exported["coefficients"])
        intercept_ = exported["intercept"]

    exported_codes = quantise_ridge(Exported, features, word_bits=params["SP_WORD"]).coefficient_codes
    return {"vectors": int(len(sample)), "mismatches": int(np.count_nonzero(emitted != expected)),
            "passed": bool(np.array_equal(emitted, expected)), "identity": identity,
            "exported_model_vs_rom_max_lsb": int(np.max(np.abs(np.asarray(exported_codes) - codes)))}


def qkd_trees(fpga, work, vectors=1024):
    import sklearn

    if sklearn.__version__ != "1.8.0":
        return {"skipped": f"scikit-learn {sklearn.__version__}; the byte-identical retrain needs 1.8.0"}
    cosim = load_script("cosimulate_detector")
    exporter = load_script("export_detector_safe", ROOT / "tools")
    from qnet_qkd.hardware.fixed_point import quantise_boosting
    from qnet_qkd.qkd.detection import fit_boosted

    t5 = sys.modules["task5"]
    frame, digest = exporter.author_dataset(0)
    features, labels, index, train, test, fit_mask, _ = exporter.author_split(frame)
    boosted = fit_boosted(features, labels, fit_mask, seed=t5.DETECTOR_SEED)
    untrainable = np.isin(index["episode_label"].to_numpy(), t5.UNTRAINABLE_LABELS)
    trainable, scored = train & ~untrainable, test & ~untrainable
    model = quantise_boosting(boosted, features[trainable], word_bits=14, rounds=5, input_quantile=0.999)
    layout = cosim.flatten(model)
    rows = np.flatnonzero(scored)
    result = {"dataset_csv_sha256": digest, "rounds": model.rounds, "memories_identical": {}, "runs": []}
    shipped = fpga / "detector_5rodadas_14b"
    for count, sync_memory in ((64, True), (vectors, True), (vectors, False)):
        build = work / f"trees_{count}_{int(sync_memory)}"
        build.mkdir()
        chosen = np.sort(np.random.default_rng(0).choice(rows, size=min(count, len(rows)), replace=False))
        sample = boosted.standardiser.transform(features[chosen])
        shape = cosim.write_memories(build, model, layout, sample, features.shape[1], sync_memory=sync_memory)
        if count == 64 and sync_memory:   # exactly the export's call (--vectors 64 --sync-memory)
            for name in ("nodes.hex", "trees.hex", "baseline.hex", "tree_params.vh"):
                result["memories_identical"][name] = (build / name).read_bytes() == (shipped / name).read_bytes()
            if not all(result["memories_identical"].values()):
                raise RuntimeError(f"rebuilt memories differ from the shipped ones: {result['memories_identical']}")
        simulate([fpga / "rtl" / "tree_classifier.v", fpga / "rtl" / "tb_tree_classifier.v"], build, include=build)
        emitted = np.loadtxt(build / "rtl_scores.txt", dtype=np.int64, ndmin=2)
        expected = model.accumulator_format.to_integer(model.raw_scores(sample))
        result["runs"].append({"vectors": int(len(sample)), "block_ram_mode": sync_memory,
                               "score_mismatches": int(np.count_nonzero(emitted[:, 1:] != expected)),
                               "class_mismatches": int(np.count_nonzero(emitted[:, 0] != np.argmax(expected, axis=1))),
                               "nodes": shape["nodes"], "trees": shape["trees"], "node_width": shape["node_width"]})
    result["passed"] = all(run["score_mismatches"] == 0 and run["class_mismatches"] == 0 for run in result["runs"])
    return result


# ---------------------------------------------------------------------------
# synthesis
# ---------------------------------------------------------------------------

def synthesise(directory, sources, top, parameters=None):
    script = [f"read_verilog -sv -I{directory} " + " ".join(str(source) for source in sources)]
    script += [f"chparam -set {key} {value} {top}" for key, value in (parameters or {}).items()]
    script += [f"synth_xilinx -top {top} -flatten -noiopad", "stat"]
    text = run(["yosys", "-p", "; ".join(script)], directory)
    stat = text[text.rfind("Printing statistics"):]
    cells = {kind: int(count) for kind, count in re.findall(r"^\s{5}(\w+)\s+(\d+)\s*$", stat, re.M)}
    kinds = [kind for kind in cells if kind[0].isupper()]
    total = int(re.search(r"Number of cells:\s+(\d+)", stat).group(1))
    estimate = re.search(r"Estimated number of LCs:\s+(\d+)", stat)
    return {"top": top, "parameters": parameters or {}, "cells": total,
            "estimated_logic_cells": int(estimate.group(1)) if estimate else None,
            "luts": sum(cells[k] for k in kinds if k.startswith("LUT")),
            "flip_flops": sum(cells[k] for k in kinds if k.startswith("FD")),
            "dsp48": sum(cells[k] for k in kinds if k.startswith("DSP48")),
            "block_ram": sum(cells[k] for k in kinds if k.startswith("RAMB")),
            "carry": sum(cells[k] for k in kinds if k.startswith("CARRY")),
            "by_type": {k: cells[k] for k in kinds}}


def synthesis(work, fpga, spec_ridge, spec_mlp, bias_ridge, bias_mlp):
    rows = []
    directory = work / "synth"
    directory.mkdir()
    shutil.copy(RTL / "ridge" / "qnet_mac.v", directory)
    shutil.copy(RTL / "ridge" / "qnet_cordic.v", directory)
    for label, spec, shifts in (("ridge", spec_ridge, bias_ridge), ("mlp", spec_mlp, bias_mlp)):
        for k, layer in enumerate(spec):
            rows.append({"design": f"compensador {label}, camada {k}", **synthesise(
                directory, ["qnet_mac.v"], "qnet_mac",
                {"INPUTS": layer["inputs"], "OUTPUTS": layer["outputs"], "SHIFT": layer["shift"],
                 "BIAS_SHIFT": shifts[k], "ACC_WIDTH": layer["accumulator"], "RELU": int(layer["relu"])})})
    rows.append({"design": "CORDIC (32 bits, 8 estágios)",
                 **synthesise(directory, ["qnet_cordic.v"], "qnet_cordic")})
    for top, memory in (("sync_top", "sincronizador_12b"), ("detector_top", "detector_5rodadas_14b")):
        build = work / f"synth_{top}"
        shutil.copytree(fpga / memory, build)
        for source in (fpga / "rtl").glob("*.v"):
            shutil.copy(source, build)
        sources = ["sync_predictor.v", "sync_top.v"] if top == "sync_top" else ["tree_classifier.v", "detector_top.v"]
        rows.append({"design": top, **synthesise(build, sources, top)})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--qkd-zip", required=True, help="pinned modelo_qkd_ml.zip (for modelos/fpga)")
    parser.add_argument("--output", required=True, help="new directory")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    work = output / "work"
    work.mkdir()
    report = {"schema": "orion.author-rtl-verification.v1", "tools": tool_versions()}
    report["compensator"] = compensator(work)
    print(json.dumps(report["compensator"], indent=1), flush=True)
    fpga, digest = extract_fpga(args.qkd_zip, work)
    report["qkd_archive_sha256"] = digest
    report["qkd_sync"] = qkd_sync(fpga, work, args.qkd_zip)
    print(json.dumps(report["qkd_sync"], indent=1), flush=True)
    report["qkd_trees"] = qkd_trees(fpga, work)
    print(json.dumps(report["qkd_trees"], indent=1), flush=True)
    spec_ridge, spec_mlp = readme_layers(RTL / "ridge" / "README.md"), readme_layers(RTL / "mlp" / "README.md")
    bias_ridge = [bench_bias_shift(RTL / "ridge" / "qnet_tb.v")]
    report["synthesis"] = synthesis(work, fpga, spec_ridge, spec_mlp, bias_ridge,
                                    report["compensator"]["mlp_chain"]["bias_shifts"])
    for row in report["synthesis"]:
        print(f"{row['design']:<32} LUT {row['luts']:>6}  FF {row['flip_flops']:>5}  DSP {row['dsp48']:>3}  "
              f"BRAM {row['block_ram']:>3}", flush=True)
    report["rtl_sources_sha256"] = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                    for path in sorted(RTL.rglob("*")) if path.is_file()}
    (output / "summary.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
