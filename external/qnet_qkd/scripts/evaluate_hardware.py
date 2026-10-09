#!/usr/bin/env python3
"""What the phase-5 estimators lose when the arithmetic becomes an FPGA's.

    python scripts/evaluate_hardware.py --part sync
    python scripts/evaluate_hardware.py --part detector --detector runs/detector

Two parts, one method.  The floating-point model is fitted exactly as it always
was, then its inference is replaced by a bit-accurate fixed-point version at a
range of word lengths, and the *unmodified* evaluation is re-run on the same
episodes and the same seeds.  Every difference in the table is therefore
attributable to the word length.

``sync``
    The ridge predictor inside :func:`~qnet_qkd.qkd.timing.run_sync_loop`,
    scored the way the phase-5 report scores it: residual, gate efficiency, and
    the key rate that survives.  This is the estimator with a real latency
    argument for being in the fabric, so it is the one that has to hold up.

``detector``
    The boosting classifier, scored with
    :func:`~qnet_qkd.qkd.detection.evaluate_detector` on the held-out
    episodes — macro F1, false alarms per hour, precision at a 1% prior.  A
    tree ensemble's error is not smooth in the word length: a sample flips
    branch only when it lands within one LSB of a threshold, so the curve has
    to be measured.

Both parts sweep the rounding mode as well as the width, because truncation is
one adder cheaper and biased, and whether that bias matters is exactly the sort
of thing that is cheap to answer here and expensive to answer in silicon.
"""
from __future__ import annotations

import argparse
import pickle
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from task5 import (BUDGET, DEAD_TIME_S, DETECTOR_SEED, FEATURE_WINDOW,  # noqa: E402
                   LINK, SYNC_TEST_SEEDS, SYNC_TRAIN_SEEDS, UNTRAINABLE_LABELS,
                   make_commissioned, make_gate, make_timing_trajectories)
from qnet_qkd.hardware.fixed_point import (DEFAULT_WORD_BITS,  # noqa: E402
                                                 quantise_boosting, quantise_ridge)
from qnet_qkd.physics.decoy import decoy_key_rate_hz, observe  # noqa: E402
from qnet_qkd.qkd.detection import (build_detection_features,  # noqa: E402
                                          episode_split, evaluate_detector)
from qnet_qkd.qkd.keyrate import receiver_response  # noqa: E402
from qnet_qkd.qkd.timing import (AlphaBetaTracker, ClockModel,  # noqa: E402
                                       LearnedSync, build_sync_dataset,
                                       fit_sync_predictor, gate_efficiency,
                                       run_sync_loop, timing_spread)


# ---------------------------------------------------------------------------
# the synchroniser
# ---------------------------------------------------------------------------

def evaluate_sync(out: Path, word_bits, length_km: float = LINK.length_km,
                  gate_width_s: float = LINK.gate_width_s,
                  dark_count_rate_hz: float = 10.0,
                  roundings=("round", "truncate")) -> None:
    """The sync predictor in float and in fixed point, through the same loop."""
    gate = make_gate(width_s=gate_width_s)
    trajectories = make_timing_trajectories(SYNC_TRAIN_SEEDS, length_km)
    features, targets, _, _ = build_sync_dataset(trajectories, gate)
    learned = fit_sync_predictor(features, targets, name="ridge float64")
    print(f"[hw] ridge ajustado em {len(features)} janelas, "
          f"{features.shape[1]} atributos")

    predictors = [("float64", 64, "—", learned)]
    for rounding in roundings:
        for bits in word_bits:
            quantised = quantise_ridge(learned.model, features, word_bits=bits,
                                       rounding=rounding)
            predictors.append((
                rounding, bits, quantised.input_format.describe(),
                LearnedSync(model=quantised, window=learned.window,
                            scale=learned.scale,
                            name=f"ridge {bits}b {rounding}")))

    rows = []
    for rounding, bits, fmt, predictor in predictors:
        for seed in SYNC_TEST_SEEDS:
            trajectory = ClockModel(length_km=length_km).simulate(
                3600.0, 1.0, rng=int(seed) + 10_000)
            result = run_sync_loop(trajectory, predictor, gate, rng=int(seed))
            summary = result.summary()
            rows.append({
                "rounding": rounding, "word_bits": bits, "format": fmt,
                "seed": seed, "synchroniser": summary["sincronizador"],
                "mean_efficiency": summary["mean_efficiency"],
                "p05_efficiency": summary["p05_efficiency"],
                "mean_residual_ps": summary["mean_residual_ps"],
                "key_rate_hz": _key_rate_with(length_km, gate_width_s,
                                              summary["mean_efficiency"],
                                              dark_count_rate_hz),
            })
        saturations = getattr(predictor.model, "saturations", 0)
        if saturations:
            print(f"  {bits}b {rounding}: {saturations} saturações — "
                  f"a faixa inteira está errada")

    # The alpha-beta tracker is the thing the FPGA would run if the ridge did
    # not fit, so it is the floor the quantised model has to stay above.
    for seed in SYNC_TEST_SEEDS:
        trajectory = ClockModel(length_km=length_km).simulate(
            3600.0, 1.0, rng=int(seed) + 10_000)
        result = run_sync_loop(trajectory, AlphaBetaTracker(), gate, rng=int(seed))
        summary = result.summary()
        rows.append({
            "rounding": "—", "word_bits": 0, "format": "alfa-beta",
            "seed": seed, "synchroniser": summary["sincronizador"],
            "mean_efficiency": summary["mean_efficiency"],
            "p05_efficiency": summary["p05_efficiency"],
            "mean_residual_ps": summary["mean_residual_ps"],
            "key_rate_hz": _key_rate_with(length_km, gate_width_s,
                                          summary["mean_efficiency"],
                                          dark_count_rate_hz),
        })

    frame = pd.DataFrame(rows)
    frame.to_csv(out / "sync_episodes.csv", index=False)
    summary = (frame.groupby(["rounding", "word_bits", "format", "synchroniser"])
               .agg(mean_efficiency=("mean_efficiency", "mean"),
                    p05_efficiency=("p05_efficiency", "mean"),
                    mean_residual_ps=("mean_residual_ps", "mean"),
                    key_rate_hz=("key_rate_hz", "mean"),
                    key_rate_std=("key_rate_hz", "std"))
               .reset_index().sort_values(["rounding", "word_bits"]))

    reference = float(summary.loc[summary["word_bits"] == 64, "key_rate_hz"].iloc[0])
    summary["key_rate_loss"] = 1.0 - summary["key_rate_hz"] / reference
    summary.to_csv(out / "sync_summary.csv", index=False)

    print("\nsincronizador quantizado "
          f"({length_km:.0f} km, gate {gate_width_s * 1e12:.0f} ps, "
          f"escuro {dark_count_rate_hz:.0f} Hz)")
    print(summary[["rounding", "word_bits", "format", "mean_residual_ps",
                   "key_rate_hz", "key_rate_loss"]].to_string(
        index=False, float_format=lambda value: f"{value:10.4f}"))


def _key_rate_with(length_km: float, gate_width_s: float, efficiency: float,
                   dark_count_rate_hz: float = 10.0) -> float:
    """Key rate at a span, given what the synchroniser left inside the gate.

    The same accounting as ``evaluate_phase5._key_rate_with``, reproduced here
    rather than imported so the two scripts can be run independently — and kept
    identical so their numbers are comparable.
    """
    link = replace(LINK, length_km=length_km, gate_width_s=gate_width_s,
                   dark_count_rate_hz=dark_count_rate_hz)
    settings = make_commissioned(length_km)
    perfect = float(gate_efficiency(0.0, gate_width_s, timing_spread(BUDGET, length_km)))
    achieved = efficiency / max(perfect, 1e-12)
    degraded = replace(link, coupling_efficiency=link.coupling_efficiency * achieved)
    response = receiver_response(degraded, settings, BUDGET, DEAD_TIME_S)
    return decoy_key_rate_hz(observe(response, settings))


# ---------------------------------------------------------------------------
# the detector
# ---------------------------------------------------------------------------

def _calibration_mask(index, trainable: np.ndarray) -> np.ndarray:
    """The held-out fifth of the training episodes, as ``train_detector`` cuts it.

    Reproduced rather than imported because the split is what makes a
    recalibrated threshold comparable to the original one: a threshold set on
    rows the model was fitted on is set on rows it has memorised.
    """
    episodes = index["episode"].to_numpy()
    train_episodes = np.unique(episodes[trainable])
    shuffled = np.array(train_episodes, copy=True)
    np.random.default_rng(DETECTOR_SEED).shuffle(shuffled)
    cut = max(int(round(0.8 * len(shuffled))), 1)
    return trainable & np.isin(episodes, shuffled[cut:])


def evaluate_pruning(out: Path, detector_dir: Path, rounds, word_bits: int = 14,
                     period_s: float = 1.0, sustain: int = 3,
                     input_quantile: float = 1.0,
                     false_alarm_target: float = 0.005) -> None:
    """How short the ensemble can be cut before the score moves.

    Boosting is additive and ordered, so keeping a prefix of the rounds is the
    model early stopping would have produced — it is a modelling decision, not
    an approximation, and it is the decision that settles whether the detector
    fits a small fabric at all.  The node count is carried alongside the score
    because that is what the block RAM has to hold, and the depth because that
    is how many cycles one traversal costs.
    """
    from copy import deepcopy

    frame = pd.read_csv(detector_dir / "sessions.csv")
    with open(detector_dir / "detectors.pkl", "rb") as handle:
        fitted = pickle.load(handle)

    features, labels, index = build_detection_features(frame, FEATURE_WINDOW)
    train, test = episode_split(index, 0.7, seed=DETECTOR_SEED)
    scored = test & ~np.isin(index["episode_label"].to_numpy(), UNTRAINABLE_LABELS)
    trainable = train & ~np.isin(index["episode_label"].to_numpy(), UNTRAINABLE_LABELS)

    # The decision threshold was calibrated against the *full* ensemble's
    # probabilities.  A shorter ensemble is less confident, so the same
    # threshold suppresses every alarm — which is a stale calibration, not a
    # property of pruning.  The threshold is therefore re-set for each pruned
    # model, on the same held-out training episodes the original used.
    calibration = _calibration_mask(index, trainable)

    rows = []
    for name, reference in (("argmax", fitted["boosted"]), ("limiar", fitted["gated"])):
        baseline = np.asarray(reference.predict(features[scored]))
        rows.append(_detector_row(_Replay(baseline), features, labels, index,
                                  scored, "round", 64, "—", name, period_s,
                                  sustain, nodes=0, saturations=0,
                                  baseline=baseline)
                    | {"rounds": 0, "depth": 0,
                       "attack_threshold": float(reference.attack_threshold)})

        for kept in rounds:
            candidate = deepcopy(reference)
            quantised = quantise_boosting(reference, features[trainable],
                                          word_bits=word_bits, rounds=kept,
                                          input_quantile=input_quantile)
            candidate.model = quantised
            candidate.name = quantised.name
            if reference.attack_threshold > 0.0:
                candidate.attack_threshold = 0.0
                candidate.calibrate_threshold(features, labels, calibration,
                                              false_alarm_target)

            row = _detector_row(candidate, features, labels, index, scored,
                                "round", word_bits,
                                quantised.input_format.describe(), name,
                                period_s, sustain, nodes=quantised.nodes,
                                saturations=quantised.saturations,
                                baseline=baseline)
            rows.append(row | {"rounds": quantised.rounds,
                               "depth": quantised.depth,
                               "attack_threshold": float(candidate.attack_threshold)})
            print(f"  {name} x{kept}: F1 {row['macro_f1']:.3f}, "
                  f"{row['false_alarms_per_hour']:.1f} alarmes/h, "
                  f"{quantised.nodes} nós, profundidade {quantised.depth}")

    summary = pd.DataFrame(rows)
    summary.to_csv(out / "pruning_summary.csv", index=False)
    print("\npoda do conjunto "
          f"({word_bits} bits, arredondamento meio-para-longe-do-zero)")
    print(summary[["decision", "rounds", "nodes", "depth", "attack_threshold",
                   "macro_f1", "false_alarms_per_hour", "precision_at_prior",
                   "agreement"]].to_string(
        index=False, float_format=lambda value: f"{value:9.4f}"))


def evaluate_boosting(out: Path, detector_dir: Path, word_bits,
                      roundings=("round", "truncate"), period_s: float = 1.0,
                      sustain: int = 3, input_quantile: float = 1.0) -> None:
    """The boosting classifier in float and in fixed point, on the same episodes."""
    from copy import deepcopy

    frame = pd.read_csv(detector_dir / "sessions.csv")
    with open(detector_dir / "detectors.pkl", "rb") as handle:
        fitted = pickle.load(handle)
    boosted = fitted["boosted"]
    gated = fitted["gated"]

    features, labels, index = build_detection_features(frame, FEATURE_WINDOW)
    train, test = episode_split(index, 0.7, seed=DETECTOR_SEED)
    scored = test & ~np.isin(index["episode_label"].to_numpy(), UNTRAINABLE_LABELS)
    trainable = train & ~np.isin(index["episode_label"].to_numpy(), UNTRAINABLE_LABELS)
    print(f"[hw] {int(scored.sum())} períodos de teste, "
          f"{features.shape[1]} atributos")

    rows = []
    for name, reference in (("argmax", boosted), ("limiar", gated)):
        baseline = np.asarray(reference.predict(features[scored]))
        rows.append(_detector_row(_Replay(baseline), features, labels, index,
                                  scored, "float64", 64, "—", name, period_s,
                                  sustain, nodes=0, saturations=0,
                                  baseline=baseline))

        for rounding in roundings:
            for bits in word_bits:
                candidate = deepcopy(reference)
                quantised = quantise_boosting(reference, features[trainable],
                                              word_bits=bits, rounding=rounding,
                                              input_quantile=input_quantile)
                candidate.model = quantised
                candidate.name = f"{reference.name} {bits}b {rounding}"
                rows.append(_detector_row(
                    candidate, features, labels, index, scored, rounding, bits,
                    quantised.input_format.describe(), name, period_s, sustain,
                    nodes=quantised.nodes, saturations=quantised.saturations,
                    baseline=baseline))
                print(f"  {name} {bits}b {rounding} "
                      f"[{quantised.input_format.describe()}, "
                      f"{quantised.saturations} sat]: "
                      f"F1 {rows[-1]['macro_f1']:.3f}, "
                      f"{rows[-1]['false_alarms_per_hour']:.1f} alarmes/h, "
                      f"concordância {rows[-1]['agreement']:.4f}")

    summary = pd.DataFrame(rows)
    for decision in summary["decision"].unique():
        selector = summary["decision"] == decision
        reference = float(summary.loc[selector & (summary["word_bits"] == 64),
                                      "macro_f1"].iloc[0])
        summary.loc[selector, "macro_f1_loss"] = (
            1.0 - summary.loc[selector, "macro_f1"] / reference)
    summary.to_csv(out / "detector_summary.csv", index=False)

    print("\ndetector quantizado")
    print(summary[["decision", "rounding", "word_bits", "format", "macro_f1",
                   "macro_f1_loss", "false_alarms_per_hour", "precision_at_prior",
                   "agreement"]].to_string(
        index=False, float_format=lambda value: f"{value:9.4f}"))


class _Replay:
    """A detector that returns predictions already computed.

    :func:`evaluate_detector` calls ``predict`` itself, and so does the
    agreement figure below; a quantised ensemble over ninety thousand rows is
    seconds of work, and the gated detector evaluates it twice more for its
    probability.  Computing the predictions once and replaying them is the same
    arithmetic, four times cheaper, and — more to the point — guarantees that
    the score and the agreement are measured on *the same* decisions.
    """

    def __init__(self, predicted):
        self.predicted = np.asarray(predicted)

    def predict(self, features):
        if len(features) != len(self.predicted):
            raise ValueError(
                f"replaying {len(self.predicted)} decisions against "
                f"{len(features)} rows")
        return self.predicted


def _detector_row(detector, features, labels, index, scored, rounding, bits,
                  fmt, decision, period_s, sustain, nodes, saturations,
                  baseline) -> dict:
    """Score one detector and flatten the report into a row.

    ``baseline`` is the float model's own prediction on the same rows, so the
    row carries both answers a verification engineer wants: whether the score
    held up, and whether the decision is *the same decision*.  They are not the
    same question — an ensemble can lose a period here and gain one there and
    keep its F1 unchanged.
    """
    predicted = np.asarray(detector.predict(features[scored]))
    report = evaluate_detector(_Replay(predicted), features, labels, index, scored,
                               sustain=sustain, period_s=period_s)
    row = {
        "decision": decision, "rounding": rounding, "word_bits": bits,
        "format": fmt, "macro_f1": report.macro_f1,
        "false_alarm_rate": report.false_alarm_rate,
        "false_alarms_per_hour": report.false_alarms_per_hour,
        "precision_at_prior": report.precision_at_prior,
        "nodes": nodes, "saturations": saturations,
        "rows": report.rows,
    }
    for label, value in report.recall.items():
        row[f"recall_{label}"] = value

    row["agreement"] = float(np.mean(predicted == np.asarray(baseline)))
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--part", default="all",
                        choices=["all", "sync", "detector", "pruning"])
    parser.add_argument("--out", type=Path, default=Path("runs/hardware"))
    parser.add_argument("--detector", type=Path, default=Path("runs/detector"))
    parser.add_argument("--bits", type=int, nargs="+", default=list(DEFAULT_WORD_BITS))
    parser.add_argument("--length-km", type=float, default=LINK.length_km)
    parser.add_argument("--gate-width-ps", type=float,
                        default=LINK.gate_width_s * 1e12)
    parser.add_argument("--period", type=float, default=1.0)
    parser.add_argument("--rounds", type=int, nargs="+",
                        default=[10, 25, 50, 100, 200, 400],
                        help="boosting rounds to keep, for --part pruning")
    parser.add_argument("--prune-bits", type=int, default=14,
                        help="word length the pruning sweep is measured at")
    parser.add_argument("--input-quantile", type=float, default=1.0,
                        help="size the detector's feature bus on this quantile "
                             "of |z| and let the tail saturate")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    if args.part in ("all", "sync"):
        print("=== sincronizador ===")
        evaluate_sync(args.out, args.bits, args.length_km,
                      args.gate_width_ps * 1e-12)
    if args.part in ("all", "detector"):
        print("\n=== detector ===")
        evaluate_boosting(args.out, args.detector, args.bits,
                          period_s=args.period,
                          input_quantile=args.input_quantile)
    if args.part in ("all", "pruning"):
        print("\n=== poda ===")
        evaluate_pruning(args.out, args.detector, args.rounds,
                         word_bits=args.prune_bits, period_s=args.period,
                         input_quantile=args.input_quantile)
    print(f"\n[hw] salvo em {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
