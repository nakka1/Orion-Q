#!/usr/bin/env python3
"""Check the detector's RTL against the fixed-point ensemble, score by score.

    python scripts/cosimulate_detector.py --rounds 5 --bits 14 --vectors 256

The pruning sweep says how many boosting rounds survive being cut; this script
takes that ensemble, flattens it into the node and tree memories a block RAM
would hold, runs ``rtl/tree_classifier.v`` under Icarus Verilog over real
held-out rows, and compares **every class score** against
:meth:`QuantisedBoosting.raw_scores` — not just the predicted class.  A
classifier can agree on the argmax while getting the scores wrong, and the
scores are what a calibrated decision threshold reads.

The memory layout is reported because it is the number that decides whether the
ensemble fits: nodes × the node word, in bits.
"""
from __future__ import annotations

import argparse
import pickle
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from task5 import (DETECTOR_SEED, FEATURE_WINDOW,  # noqa: E402
                   UNTRAINABLE_LABELS)
from qnet_qkd.hardware.fixed_point import quantise_boosting  # noqa: E402
from qnet_qkd.qkd.detection import (build_detection_features,  # noqa: E402
                                          episode_split)


def _bits(count: int) -> int:
    """Address bits for ``count`` entries, never fewer than one."""
    return max(int(np.ceil(np.log2(max(count, 2)))), 1)


def _hex(value: int, width: int) -> str:
    """``value`` as a fixed-width two's-complement hex literal."""
    return format(int(value) & ((1 << width) - 1), f"0{(width + 3) // 4}x")


def flatten(model) -> dict:
    """Lay the ensemble out as one node array and one tree table.

    Every node becomes the same word — ``[is_leaf | feature | payload | left |
    right]`` — because that is what a block RAM holds, and the payload field is
    shared between a threshold and a leaf value since no node carries both.
    Children are rewritten from per-tree indices into absolute addresses.
    """
    word = model.input_format.word_bits
    leaf = model.leaf_format.word_bits
    payload = max(word, leaf)

    nodes: list[tuple[int, int, int, int, int]] = []
    trees: list[tuple[int, int]] = []

    for row in model.trees:
        for index, tree in enumerate(row):
            base = len(nodes)
            trees.append((index, base))
            count = len(tree["is_leaf"])
            for node in range(count):
                if tree["is_leaf"][node]:
                    nodes.append((
                        1, 0,
                        int(model.leaf_format.to_integer(tree["value"][node])[()]),
                        0, 0))
                else:
                    nodes.append((
                        0, int(tree["feature"][node]),
                        int(model.input_format.to_integer(
                            tree["threshold"][node])[()]),
                        base + int(tree["left"][node]),
                        base + int(tree["right"][node])))

    feature_bits = _bits(model.trees[0][0]["feature"].max() + 1
                         if len(model.trees) else 2)
    return {"nodes": nodes, "trees": trees, "word": word, "leaf": leaf,
            "payload": payload, "feature_bits": feature_bits}


def write_memories(directory: Path, model, layout: dict,
                   features: np.ndarray, feature_count: int,
                   sync_memory: bool = False) -> dict:
    """Emit the node array, the tree table, the baseline, the rows and the header."""
    word, leaf, payload = layout["word"], layout["leaf"], layout["payload"]
    accumulator = model.accumulator_format.word_bits
    shift = (model.accumulator_format.fraction_bits
             - model.leaf_format.fraction_bits)
    if shift < 0:
        raise ValueError(
            f"the leaf format carries more fraction bits ({model.leaf_format.fraction_bits}) "
            f"than the accumulator ({model.accumulator_format.fraction_bits}); "
            "the RTL only shifts left")

    node_addr = _bits(len(layout["nodes"]))
    tree_addr = _bits(len(layout["trees"]))
    class_bits = _bits(len(model.classes))
    feature_bits = _bits(feature_count)
    node_width = 1 + feature_bits + payload + 2 * node_addr

    node_lines = []
    for is_leaf, feature, value, left, right in layout["nodes"]:
        packed = ((is_leaf << (feature_bits + payload + 2 * node_addr))
                  | ((feature & ((1 << feature_bits) - 1))
                     << (payload + 2 * node_addr))
                  | ((value & ((1 << payload) - 1)) << (2 * node_addr))
                  | ((left & ((1 << node_addr) - 1)) << node_addr)
                  | (right & ((1 << node_addr) - 1)))
        node_lines.append(_hex(packed, node_width))
    (directory / "nodes.hex").write_text("\n".join(node_lines) + "\n")

    (directory / "trees.hex").write_text("\n".join(
        _hex((klass << node_addr) | root, class_bits + node_addr)
        for klass, root in layout["trees"]) + "\n")

    baseline = model.accumulator_format.to_integer(model.baseline)
    (directory / "baseline.hex").write_text("\n".join(
        _hex(int(value), accumulator) for value in baseline) + "\n")

    codes = model.input_format.to_integer(features)
    (directory / "features.hex").write_text("\n".join(
        _hex(int(code), word) for code in codes.reshape(-1)) + "\n")

    (directory / "tree_params.vh").write_text(
        "// Generated by scripts/cosimulate_detector.py — do not edit.\n"
        "`ifndef TREE_PARAMS_VH\n`define TREE_PARAMS_VH\n"
        f"`define TC_WORD    {word}\n"
        f"`define TC_LEAF    {leaf}\n"
        f"`define TC_ACC_W   {accumulator}\n"
        f"`define TC_FEATS   {feature_count}\n"
        f"`define TC_CLASSES {len(model.classes)}\n"
        f"`define TC_NODES   {len(layout['nodes'])}\n"
        f"`define TC_TREES   {len(layout['trees'])}\n"
        f"`define TC_LSHIFT  {shift}\n"
        f"`define TC_FIDX    {feature_bits}\n"
        f"`define TC_NADDR   {node_addr}\n"
        f"`define TC_TADDR   {tree_addr}\n"
        f"`define TC_CIDX    {class_bits}\n"
        f"`define TC_VECTORS {len(codes)}\n"
        f"`define TC_SYNC_MEM {1 if sync_memory else 0}\n"
        "`endif\n")

    return {"node_width": node_width, "node_addr": node_addr,
            "tree_addr": tree_addr, "class_bits": class_bits,
            "feature_bits": feature_bits, "shift": shift,
            "nodes": len(layout["nodes"]), "trees": len(layout["trees"]),
            "vectors": len(codes)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rounds", type=int, default=5,
                        help="boosting rounds kept, from the pruning sweep")
    parser.add_argument("--bits", type=int, default=14)
    parser.add_argument("--vectors", type=int, default=256)
    parser.add_argument("--input-quantile", type=float, default=0.999)
    parser.add_argument("--detector", type=Path, default=Path("runs/detector"))
    parser.add_argument("--build", type=Path, default=Path("runs/hardware/rtl_tree"))
    parser.add_argument("--rtl", type=Path, default=Path("rtl"))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--sync-memory", action="store_true",
                        help="drive the node and tree memories as a block RAM "
                             "does — one cycle late — which is what the "
                             "synthesised top level uses")
    args = parser.parse_args()

    build = (REPO_ROOT / args.build).resolve()
    build.mkdir(parents=True, exist_ok=True)

    frame = pd.read_csv((REPO_ROOT / args.detector / "sessions.csv").resolve())
    with open((REPO_ROOT / args.detector / "detectors.pkl").resolve(), "rb") as handle:
        detector = pickle.load(handle)["boosted"]

    features, _, index = build_detection_features(frame, FEATURE_WINDOW)
    train, test = episode_split(index, 0.7, seed=DETECTOR_SEED)
    scored = test & ~np.isin(index["episode_label"].to_numpy(), UNTRAINABLE_LABELS)
    trainable = train & ~np.isin(index["episode_label"].to_numpy(), UNTRAINABLE_LABELS)

    model = quantise_boosting(detector, features[trainable], word_bits=args.bits,
                              rounds=args.rounds,
                              input_quantile=args.input_quantile)
    print(f"[cosim] boosting {args.bits}b x{model.rounds} — "
          f"entrada {model.input_format.describe()}, "
          f"folha {model.leaf_format.describe()}, "
          f"acumulador {model.accumulator_format.describe()}")

    generator = np.random.default_rng(args.seed)
    rows = np.flatnonzero(scored)
    chosen = np.sort(generator.choice(rows, size=min(args.vectors, len(rows)),
                                      replace=False))
    sample = detector.standardiser.transform(features[chosen])

    layout = flatten(model)
    shape = write_memories(build, model, layout, sample, features.shape[1],
                           sync_memory=args.sync_memory)
    memory_bits = shape["nodes"] * shape["node_width"]
    print(f"[cosim] {shape['nodes']} nós × {shape['node_width']} bits = "
          f"{memory_bits / 8192:.1f} kB de BRAM; {shape['trees']} árvores; "
          f"deslocamento +{shape['shift']}")

    sources = [str((REPO_ROOT / args.rtl / name).resolve())
               for name in ("tree_classifier.v", "tb_tree_classifier.v")]
    compiled = subprocess.run(
        ["iverilog", "-g2012", "-I", str(build), "-o", "simulation", *sources],
        cwd=build, capture_output=True, text=True)
    if compiled.returncode != 0:
        print(compiled.stdout)
        print(compiled.stderr)
        raise SystemExit("iverilog falhou")

    run = subprocess.run(["vvp", "simulation"], cwd=build,
                         capture_output=True, text=True)
    print(run.stdout.strip())
    if run.returncode != 0:
        print(run.stderr)
        raise SystemExit("vvp falhou")

    emitted = np.loadtxt(build / "rtl_scores.txt", dtype=np.int64, ndmin=2)
    expected = model.accumulator_format.to_integer(model.raw_scores(sample))
    classes = np.asarray(model.classes, dtype=object)

    if emitted.shape != (len(sample), len(model.classes) + 1):
        raise SystemExit(f"o RTL devolveu {emitted.shape}, "
                         f"esperado {(len(sample), len(model.classes) + 1)}")

    score_errors = int(np.count_nonzero(emitted[:, 1:] != expected))
    predicted = classes[emitted[:, 0]]
    reference = classes[np.argmax(expected, axis=1)]
    label_errors = int(np.count_nonzero(predicted != reference))

    print(f"[cosim] {len(sample)} vetores × {len(model.classes)} classes: "
          f"{score_errors} escores divergentes, {label_errors} classes divergentes")

    if score_errors or label_errors:
        row = int(np.argmax((emitted[:, 1:] != expected).any(axis=1)))
        print(f"  primeiro exemplo: linha {row}")
        print(f"    RTL    {emitted[row, 1:].tolist()} -> {predicted[row]}")
        print(f"    modelo {expected[row].tolist()} -> {reference[row]}")
        return 1

    print("[cosim] equivalência bit a bit em todos os escores de classe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
