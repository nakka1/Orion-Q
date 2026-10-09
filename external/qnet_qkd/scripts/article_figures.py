#!/usr/bin/env python3
"""Render the figures for the phase-5 article from the measured run artefacts.

    python scripts/article_figures.py --out artigo/figuras

Every figure is drawn from a CSV a phase-5 script wrote, except the first, which
is computed live from :mod:`qnet_qkd.physics.decoy` — what each attack
does to the observables is a property of the physics, and a figure of it should
change when the physics does.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from qnet_qkd.physics.decoy import DecoySettings  # noqa: E402
from qnet_qkd.qkd.attacks import (DetectorBlinding, InterceptResend,  # noqa: E402
                                        NoAttack, PhotonNumberSplitting)
from qnet_qkd.qkd.session import LinkConditions  # noqa: E402

LABELS = {
    "normal": "normal",
    "thermal_drift": "deriva térmica",
    "loss_event": "evento de perda",
    "intercept_resend": "intercept-and-resend",
    "pns": "PNS",
    "detector_blinding": "cegamento",
}
ORDER = list(LABELS)
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#7a5cd6")


def _style(axes) -> None:
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    axes.grid(True, color="#dedcd5", linewidth=0.7, alpha=0.8)
    axes.set_axisbelow(True)


def figure_signatures(out: Path) -> None:
    """Gain under each attack, divided by the honest gain."""
    link = LinkConditions(length_km=50.0)
    settings = DecoySettings()
    honest = link.response()
    attacks = (NoAttack(), InterceptResend(fraction=0.25),
               PhotonNumberSplitting(blocked_fraction=0.5),
               DetectorBlinding(click_probability=float(
                   honest.gain(settings.signal_intensity)) * 1.3))
    names = ("sem ataque", "intercept-and-resend", "PNS", "cegamento de detector")

    intensities = np.linspace(0.01, 1.0, 80)
    reference = np.array([float(honest.gain(value)) for value in intensities])

    figure, axes = plt.subplots(figsize=(6.0, 3.4), dpi=200)
    for index, (attack, name) in enumerate(zip(attacks, names)):
        response = attack.apply(honest, settings)
        gains = np.array([float(response.gain(value)) for value in intensities])
        axes.plot(intensities, gains / reference, color=SERIES[index], linewidth=2,
                  label=name)

    for value, label in ((settings.decoy_intensity, r"$\nu$"),
                         (settings.signal_intensity, r"$\mu$")):
        axes.axvline(value, color="#52514e", linewidth=0.9, linestyle=":")
        axes.annotate(label, (value, 1.30), color="#52514e", fontsize=11, ha="center")

    axes.set_xlabel("intensidade do pulso (número médio de fótons)", fontsize=9)
    axes.set_ylabel(r"$Q$ atacado $/\ Q$ honesto", fontsize=9)
    axes.tick_params(labelsize=8)
    axes.legend(frameon=False, fontsize=8, loc="lower right")
    _style(axes)
    figure.tight_layout()
    figure.savefig(out / "assinaturas.pdf")
    plt.close(figure)


def figure_recall(detector_dir: Path, out: Path) -> None:
    """Per-class recall for the QBER threshold and the three learned models."""
    scores = pd.read_csv(detector_dir / "scores.csv")
    scores = scores[scores["subset"] == "teste"]
    detectors = ["limiar de QBER", "random forest", "SVM (RBF)", "gradient boosting"]
    labels = [label for label in ORDER if label in set(scores["label"])]

    figure, axes = plt.subplots(figsize=(6.2, 3.2), dpi=200)
    positions = np.arange(len(labels))
    width = 0.2
    for index, detector in enumerate(detectors):
        subset = scores[scores["detector"] == detector].set_index("label")
        values = [float(subset["recall"].get(label, np.nan)) for label in labels]
        axes.bar(positions + (index - 1.5) * width, values, width * 0.9,
                 color=SERIES[index], label=detector)

    axes.set_xticks(positions)
    axes.set_xticklabels([LABELS[label] for label in labels], rotation=18,
                         ha="right", fontsize=8)
    axes.set_ylim(0, 1.32)
    axes.set_yticks([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    axes.set_ylabel("recall (fração dos períodos)", fontsize=9)
    axes.tick_params(labelsize=8)
    axes.legend(frameon=False, fontsize=8, loc="upper left", ncol=4,
                columnspacing=1.0, handlelength=1.2)
    _style(axes)
    figure.tight_layout()
    figure.savefig(out / "recall.pdf")
    plt.close(figure)


def figure_operating_point(fast_dir: Path, slow_dir: Path, out: Path) -> None:
    """False alarms per hour against precision under a 1% prior, at two periods."""
    figure, axes = plt.subplots(figsize=(6.0, 3.4), dpi=200)

    for index, (directory, marker, name) in enumerate(
            ((fast_dir, "o", "período de 1 s"), (slow_dir, "s", "período de 5 s"))):
        scores = pd.read_csv(directory / "scores.csv")
        scores = scores[(scores["subset"] == "teste")
                        & (~scores["detector"].str.contains(r"\(", regex=True))]
        points = scores.drop_duplicates("detector")
        axes.scatter(np.maximum(points["false_alarms_per_hour"], 0.02),
                     points["precision_at_prior"], s=46, marker=marker,
                     color=SERIES[index], label=name, zorder=4,
                     edgecolor="white", linewidth=0.8)
        for _, row in points.iterrows():
            axes.annotate(row["detector"].replace("gradient boosting", "GB")
                          .replace("random forest", "RF")
                          .replace("limiar de QBER", "limiar")
                          .replace(" + limiar + histerese", "+lim+hist")
                          .replace(" + limiar", "+lim"),
                          (max(row["false_alarms_per_hour"], 0.02),
                           row["precision_at_prior"]),
                          fontsize=6.5, color="#52514e",
                          xytext=(4, 3), textcoords="offset points")

    axes.set_xscale("log")
    axes.set_xlabel("alarmes falsos por hora de operação saudável", fontsize=9)
    axes.set_ylabel("precisão com prior de 1%", fontsize=9)
    axes.set_ylim(-0.03, 1.08)
    axes.tick_params(labelsize=8)
    axes.legend(frameon=False, fontsize=8, loc="center left")
    _style(axes)
    figure.tight_layout()
    figure.savefig(out / "ponto_operacao.pdf")
    plt.close(figure)


def figure_keyrate(phase5_dir: Path, out: Path) -> None:
    """Key rate by span for the three policies."""
    summary = pd.read_csv(phase5_dir / "keyrate_summary.csv")
    spans = sorted(summary["length_km"].unique())
    policies = ["estático", "agente", "oráculo"]

    figure, axes = plt.subplots(figsize=(6.0, 3.2), dpi=200)
    positions = np.arange(len(spans))
    width = 0.26
    for index, policy in enumerate(policies):
        subset = summary[summary["policy"] == policy].set_index("length_km")
        values = [float(subset["key_rate_hz"].get(span, np.nan)) / 1e6 for span in spans]
        errors = [float(subset["key_rate_std"].get(span, np.nan)) / 1e6 for span in spans]
        axes.bar(positions + (index - 1) * width, values, width * 0.9,
                 yerr=errors, capsize=3, error_kw={"elinewidth": 0.9},
                 color=SERIES[index], label=policy)

    axes.set_xticks(positions)
    axes.set_xticklabels([f"{span:.0f} km" for span in spans], fontsize=8)
    axes.set_ylabel("taxa de chave secreta (Mbit/s)", fontsize=9)
    axes.tick_params(labelsize=8)
    axes.legend(frameon=False, fontsize=8)
    _style(axes)
    figure.tight_layout()
    figure.savefig(out / "taxa_chave.pdf")
    plt.close(figure)


def figure_timing(phase5_dir: Path, out: Path) -> None:
    """Key rate against gate width, per synchroniser, at two dark count rates."""
    summary = pd.read_csv(phase5_dir / "timing_summary.csv")
    summary = summary[np.isclose(summary["length_km"], 50.0)]

    figure, axes = plt.subplots(1, 2, figsize=(6.4, 3.0), dpi=200, sharey=True)
    for panel, dark in zip(axes, (10.0, 1.0e6)):
        subset = summary[np.isclose(summary["dark_count_rate_hz"], dark)]
        for index, (name, group) in enumerate(subset.groupby("synchroniser", sort=True)):
            group = group.sort_values("gate_width_ps")
            panel.plot(group["gate_width_ps"], group["key_rate_hz"] / 1e6,
                       color=SERIES[index], linewidth=1.8, marker="o", markersize=4,
                       label=name)
        panel.set_title(f"escuro {dark:.0e} Hz".replace("e+0", "e"), fontsize=9)
        panel.set_xlabel("largura do gate (ps)", fontsize=9)
        panel.tick_params(labelsize=8)
        _style(panel)
    axes[0].set_ylabel("taxa de chave (Mbit/s)", fontsize=9)
    axes[1].legend(frameon=False, fontsize=7, loc="lower center")
    figure.tight_layout()
    figure.savefig(out / "sincronismo.pdf")
    plt.close(figure)


def figure_field(data_dir: Path, out: Path) -> bool:
    """The Cambridge field-data checks, drawn by the validation script itself.

    Skipped, with a message, when the data are absent: they are a third party's
    files, downloaded by hand from doi:10.17863/CAM.44524, and every other figure
    in the article can still be rebuilt without them.
    """
    if not (data_dir / "2b_1.txt").exists():
        print(f"  dados de Cambridge ausentes em {data_dir}; figura de campo omitida")
        return False

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from validate_cambridge import figure  # noqa: E402
    from qnet_qkd.validation import cambridge as cb  # noqa: E402

    series = cb.load_key_rate_series(data_dir / "2b_1.txt")
    sweep = cb.load_coexistence_sweep(data_dir / "4b.txt")
    sensitivity = cb.rate_sensitivity(series)
    days = cb.degradation_days(series, sensitivity.within_month.value)
    figure(series, cb.threshold_false_alarms(series, 60, 0.01), sensitivity, days,
           sweep, cb.coexistence_fit(sweep), out / "campo.pdf",
           figsize=(8.6, 3.3))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--detector", type=Path, default=Path("runs/detector"))
    parser.add_argument("--slow", type=Path, default=Path("runs/detector_slow"))
    parser.add_argument("--phase5", type=Path, default=Path("runs/phase5"))
    parser.add_argument("--out", type=Path, default=Path("artigo/figuras"))
    parser.add_argument("--field", type=Path, default=Path("dados_cambridge"),
                        help="the Cambridge Quantum Network files, for the "
                             "field-validation figure")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    figure_signatures(args.out)
    figure_recall(args.detector, args.out)
    figure_operating_point(args.detector, args.slow, args.out)
    figure_keyrate(args.phase5, args.out)
    figure_timing(args.phase5, args.out)
    figure_field(args.field, args.out)
    print(f"figuras em {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
