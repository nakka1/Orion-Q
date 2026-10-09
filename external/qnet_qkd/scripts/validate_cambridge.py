#!/usr/bin/env python3
"""Check the phase-5 physics against the Cambridge Quantum Network's own data.

    python scripts/validate_cambridge.py --data dados_cambridge --out runs/cambridge

The data are Dynes et al. (2019), CC-BY 4.0, doi:10.17863/CAM.44524 — download
the item's files into ``--data``.  Only ``2b_1.txt`` and ``4b.txt`` are used;
the rest are the network's key-management traffic and two analytic curves,
which no model here makes a prediction about.

Three results, written as CSVs and one figure:

1. the QBER threshold, calibrated at commissioning, against 515 benign days;
2. how much key one point of QBER costs — data three ways, model one;
3. whether the coexistence penalty has the Raman form, and how large it is.
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

from qnet_qkd.validation import cambridge as cb  # noqa: E402

SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#7a5cd6")


def _style(axes) -> None:
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    axes.grid(True, color="#dedcd5", linewidth=0.7, alpha=0.8)
    axes.set_axisbelow(True)


def figure(series, threshold, sensitivity, days, sweep, fit, path: Path,
           figsize: tuple[float, float] = (12.0, 3.4)) -> None:
    """Three panels: the threshold on real days, the rate–QBER slope, coexistence.

    ``figsize`` is in inches at the size the figure will be *printed*: text sizes
    are in points, so a figure drawn wide and shrunk onto a page shrinks its
    labels with it.  The article passes its column width.
    """
    fig, axes = plt.subplots(1, 3, figsize=figsize, dpi=200)

    # (a) the QBER series and the commissioning threshold
    ax = axes[0]
    ax.plot(series["time"], series["qber"] * 100, color=SERIES[0], linewidth=1.0)
    cut = series["time"].iloc[threshold.baseline_days]
    ax.axvspan(series["time"].iloc[0], cut, color="#dedcd5", alpha=0.6, linewidth=0)
    ax.axhline(threshold.threshold * 100, color=SERIES[1], linewidth=1.2, linestyle="--")
    over = series.iloc[threshold.baseline_days:]
    over = over[over["qber"] > threshold.threshold]
    ax.scatter(over["time"], over["qber"] * 100, s=6, color=SERIES[1], zorder=3)
    ax.set_ylabel("QBER (%)", fontsize=9)
    ax.set_title(f"(a) limiar de 1%: {threshold.alarm_rate:.1%} dos dias",
                 fontsize=9, loc="left")
    ax.tick_params(labelsize=7)
    import matplotlib.dates as mdates
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 7)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%y"))
    _style(ax)

    # (b) ln rate against QBER
    ax = axes[1]
    colours = {"normal": "#b8b6ae", "erro": SERIES[3], "perda": SERIES[4]}
    for kind, group in days.groupby("kind"):
        ax.scatter(group["qber"] * 100, group["key_rate_bps"] / 1e6, s=6,
                   color=colours[kind], label=f"{kind} ({len(group)})", zorder=2)
    episode = series[(series["time"] >= "2017-08-22") & (series["time"] <= "2017-09-13")]
    ax.scatter(episode["qber"] * 100, episode["key_rate_bps"] / 1e6, s=10,
               facecolor="none", edgecolor=SERIES[0], linewidth=0.9,
               label="rampa ago–set/17", zorder=3)
    grid = np.linspace(series["qber"].min(), series["qber"].max(), 50)
    anchor_q = float(episode["qber"].mean())
    anchor_r = float(np.log(episode["key_rate_bps"]).mean())
    for slope, colour, name in ((sensitivity.episode.value, SERIES[0], "dados"),
                                (sensitivity.model, SERIES[1], "modelo, f=1,16")):
        ax.plot(grid * 100, np.exp(anchor_r + slope * (grid - anchor_q)) / 1e6,
                color=colour, linewidth=1.2, label=f"{name}: {slope:.1f}")
    ax.set_xlabel("QBER (%)", fontsize=9)
    ax.set_ylabel("taxa de chave (Mbit/s)", fontsize=9)
    ax.set_title("(b) custo de 1 p.p. de QBER", fontsize=9, loc="left")
    ax.legend(frameon=False, fontsize=6.5)
    ax.tick_params(labelsize=7)
    _style(ax)

    # (c) coexistence
    ax = axes[2]
    ax.errorbar(sweep["received_mw"], sweep["qber"] * 100,
                yerr=sweep["qber_error"] * 100, fmt="o", markersize=4,
                color=SERIES[0], capsize=2, linewidth=0.9, label="medido")
    grid = np.linspace(0, sweep["received_mw"].max() * 1.05, 50)
    ax.plot(grid, (fit.baseline + fit.slope_per_mw * grid) * 100, color=SERIES[2],
            linewidth=1.2, label=f"linear em mW (χ²={fit.chi2_linear_mw:.1f})")
    constant = np.average(sweep["qber"], weights=1 / sweep["qber_error"] ** 2)
    ax.axhline(constant * 100, color="#8a8880", linewidth=1.0, linestyle=":",
               label=f"constante (χ²={fit.chi2_constant:.1f})")
    ax.set_xlabel("potência clássica recebida (mW)", fontsize=9)
    ax.set_ylabel("QBER (%)", fontsize=9)
    ax.set_title("(c) coexistência com canal 100G", fontsize=9, loc="left")
    ax.legend(frameon=False, fontsize=6.5, loc="upper left")
    ax.tick_params(labelsize=7)
    _style(ax)

    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=Path("dados_cambridge"))
    parser.add_argument("--out", type=Path, default=Path("runs/cambridge"))
    parser.add_argument("--baseline-days", type=int, default=60,
                        help="commissioning period for the QBER threshold; 60 "
                             "matches the simulator's baseline, in days here "
                             "rather than seconds")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    series = cb.load_key_rate_series(args.data / "2b_1.txt")
    sweep = cb.load_coexistence_sweep(args.data / "4b.txt")
    print(f"[cambridge] {len(series)} dias, {series['time'].min():%Y-%m-%d} a "
          f"{series['time'].max():%Y-%m-%d}; taxa média "
          f"{series['key_rate_bps'].mean() / 1e6:.2f} Mbit/s")

    # 1 -------------------------------------------------------------------
    rows = []
    for baseline in (30, 60, 90):
        for target in (0.01, 0.05):
            result = cb.threshold_false_alarms(series, baseline, target)
            rows.append({"baseline_days": baseline, "target": target,
                         "threshold_pct": result.threshold * 100,
                         "alarm_rate": result.alarm_rate, "episodes": result.episodes,
                         "days": result.days, "excess": result.excess})
    thresholds = pd.DataFrame(rows)
    thresholds.to_csv(args.out / "threshold.csv", index=False)
    print("\n1. limiar de QBER calibrado no comissionamento, em dias benignos")
    print(thresholds.to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    # 2 -------------------------------------------------------------------
    sensitivity = cb.rate_sensitivity(series)
    slopes = pd.DataFrame([
        {"estimativa": s.label, "slope": s.value, "ic95": s.half_width,
         "r2": s.r_squared, "dias": s.rows, "custo_por_pp": s.cost_per_point}
        for s in (sensitivity.whole, sensitivity.within_month, sensitivity.episode)]
        + [{"estimativa": f"modelo, f={f:.2f}", "slope": v, "ic95": np.nan,
            "r2": np.nan, "dias": np.nan, "custo_por_pp": 1 - np.exp(v / 100)}
           for f, v in sensitivity.model_by_inefficiency.items()])
    slopes.to_csv(args.out / "rate_sensitivity.csv", index=False)
    print("\n2. d ln R / d QBER")
    print(slopes.to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    days = cb.degradation_days(series, sensitivity.within_month.value)
    days.to_csv(args.out / "degradation_days.csv", index=False)
    print("\n   dias por tipo de degradação:",
          days["kind"].value_counts().to_dict())

    # 3 -------------------------------------------------------------------
    fit = cb.coexistence_fit(sweep)
    coexistence = pd.DataFrame([{
        "forma": "constante", "chi2": fit.chi2_constant, "p": fit.p_constant},
        {"forma": "linear em mW (Raman)", "chi2": fit.chi2_linear_mw, "p": fit.p_linear_mw},
        {"forma": "linear em dBm", "chi2": fit.chi2_linear_db, "p": fit.p_linear_db}])
    coexistence.to_csv(args.out / "coexistence.csv", index=False)
    print("\n3. coexistência")
    print(coexistence.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print(f"   inclinação medida {fit.slope_per_mw * 100:.4f} ± "
          f"{fit.slope_error * 100:.4f} p.p./mW; modelo padrão "
          f"{fit.model_slope * 100:.3f} p.p./mW")
    print(f"   acoplamento rho·filtro·janela: implícito {fit.implied_coupling:.2e}, "
          f"padrão {fit.default_coupling:.2e} "
          f"({fit.default_coupling / fit.implied_coupling:.0f}× pessimista)")

    figure(series, cb.threshold_false_alarms(series, args.baseline_days, 0.01),
           sensitivity, days, sweep, fit, args.out / "cambridge.pdf")
    figure(series, cb.threshold_false_alarms(series, args.baseline_days, 0.01),
           sensitivity, days, sweep, fit, args.out / "cambridge.png")
    print(f"\n[cambridge] salvo em {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
