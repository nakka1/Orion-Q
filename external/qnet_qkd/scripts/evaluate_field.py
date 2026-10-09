#!/usr/bin/env python3
"""Re-run phase 3 on the fibre the field measurements describe.

Phase 3 chose its operating point from the literature and swept around it. Two
published measurements narrow it, and neither agrees with what was chosen:

* The only 4 kHz polarimeter recording on aerial fibre (NIST, 220 km of
  optical-ground-wire) finds a **60 Hz tone with 120 and 180 Hz harmonics** —
  power-line current — where phase 3 put a 40 Hz acoustic band.
* ORNL's 11-month characterisation of a 15 km aerial/in-ground link reports a
  drift spectrum going as :math:`f^{-1.0 \\pm 0.2}`. In this model that exponent
  is a sharp function of the drift rate, and matching it pins the rate near
  **0.02 rad/s** against the 0.05 phase 3 used.

So this script does two things. It shows the calibration — exponent against
drift rate, so the reader can see how tightly the measurement pins it — and then
re-runs the compensator comparison at the calibrated point, with the predictors
retrained there rather than transplanted. The transplant is reported too: what
the phase-3-trained model does on the calibrated fibre is a different and also
useful number.

    python scripts/evaluate_field.py --out runs/field
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

from task3 import (DURATION_S, FEATURE_SPEC, SAMPLE_INTERVAL_S,  # noqa: E402
                   TEST_SEEDS, TRAIN_SEEDS, VALIDATION_SEEDS, make_control,
                   make_model)
from qnet_qkd.compensation import (AlphaBetaFilter, Persistence,  # noqa: E402
                                         run_closed_loop)
from qnet_qkd.compensation.train import (build_dataset,  # noqa: E402
                                               chronological_split, fit_mlp,
                                               fit_ridge)
from qnet_qkd.physics.field_calibration import (  # noqa: E402
    CALIBRATED_DRIFT_RATE_RAD_PER_S, FLICKER_EXPONENT, SPECTRAL_FIT_BAND_HZ,
    spectral_exponent)

#: Drift rates the exponent is measured at. Logarithmic and wide enough to show
#: the exponent crossing the measured value rather than merely touching it.
DRIFT_RATES = (0.005, 0.007, 0.01, 0.015, 0.02, 0.03, 0.05, 0.08)

#: Long enough to resolve the bottom of ORNL's fitting band, sampled coarsely
#: because nothing above a hundredth of a hertz matters to this measurement.
SPECTRUM_DURATION_S = 40_000.0
SPECTRUM_DT_S = 1.0


def operating_points() -> dict[str, dict]:
    """The three fibres being compared, as overrides to the phase-3 model.

    Named rather than parameterised: each one is a claim about a real link, and
    a reader should be able to see which claim without reading a sweep.
    """
    return {
        "fase 3 (40 Hz, 0,05 rad/s)": dict(),
        "aérea calibrada (60/120/180 Hz, 0,02 rad/s)": dict(
            thermal_drift_rate_rad_per_s=CALIBRATED_DRIFT_RATE_RAD_PER_S,
            vibration_frequency_hz=60.0,
            vibration_harmonics=(1.0, 0.5, 0.25),
        ),
        "só a frequência corrigida (60 Hz, 0,05 rad/s)": dict(
            vibration_frequency_hz=60.0,
            vibration_harmonics=(1.0, 0.5, 0.25),
        ),
        "só a taxa corrigida (40 Hz, 0,02 rad/s)": dict(
            thermal_drift_rate_rad_per_s=CALIBRATED_DRIFT_RATE_RAD_PER_S,
        ),
    }


def trajectories(overrides, seeds, duration_s=DURATION_S):
    model = make_model(**overrides)
    return [model.simulate(duration_s, SAMPLE_INTERVAL_S, rng=int(seed),
                           track_rotation=True) for seed in seeds]


# ---------------------------------------------------------------------------
# 1. the calibration itself
# ---------------------------------------------------------------------------

def study_exponent(seeds: int = 3) -> pd.DataFrame:
    """Spectral exponent against drift rate, with and without the band."""
    rows = []
    for rate in DRIFT_RATES:
        for label, extra in (("sem banda", dict(vibration_amplitude_rad=0.0)),
                             ("com banda de 60 Hz",
                              dict(vibration_frequency_hz=60.0,
                                   vibration_harmonics=(1.0, 0.5, 0.25)))):
            model = make_model(thermal_drift_rate_rad_per_s=rate, **extra)
            exponents = []
            for seed in range(seeds):
                trajectory = model.simulate(SPECTRUM_DURATION_S, SPECTRUM_DT_S,
                                            rng=seed)
                exponents.append(spectral_exponent(trajectory.stokes[:, 0],
                                                   SPECTRUM_DT_S,
                                                   *SPECTRUM_BAND))
            rows.append({"drift_rate_rad_per_s": rate, "band": label,
                         "exponent": float(np.mean(exponents)),
                         "exponent_std": float(np.std(exponents))})
    return pd.DataFrame(rows)


SPECTRUM_BAND = SPECTRAL_FIT_BAND_HZ


# ---------------------------------------------------------------------------
# 2. the comparison, retrained at each point
# ---------------------------------------------------------------------------

def train_at(overrides, epochs: int, seed: int = 0):
    """Fit ridge and MLP on trajectories from one operating point."""
    control = make_control()
    dataset = build_dataset(trajectories(overrides, TRAIN_SEEDS), control,
                            FEATURE_SPEC, target="measured", rng=seed)
    train, validation = chronological_split(dataset, 0.75, 0.05)
    ridge = fit_ridge(dataset, train)
    mlp, _ = fit_mlp(dataset, train, validation, hidden=(192, 192),
                     epochs=epochs, seed=seed)
    return {"ridge": ridge, "mlp": mlp}


def score(test, compensator, control, seed=7000) -> dict:
    rows = [run_closed_loop(trajectory, compensator, control, rng=seed + index).summary()
            for index, trajectory in enumerate(test)]
    aggregate = {key: float(np.mean([row[key] for row in rows]))
                 for key in rows[0] if key != "compensator"}
    aggregate["compensator"] = compensator.name
    aggregate["qber_stderr"] = float(
        np.std([row["mean_qber"] for row in rows], ddof=1) / np.sqrt(len(rows)))
    return aggregate


def study_comparison(transplanted, epochs: int, test_seeds: int) -> pd.DataFrame:
    control = make_control()
    rows = []
    for name, overrides in operating_points().items():
        test = trajectories(overrides, TEST_SEEDS[:test_seeds])
        fitted = train_at(overrides, epochs)

        candidates = [
            ("persistência", Persistence(), "—"),
            ("filtro alfa-beta",
             AlphaBetaFilter(alpha=0.05, beta=0.005, name="filtro alfa-beta"), "—"),
            ("ridge (Wiener)", fitted["ridge"], "no ponto"),
            ("MLP", fitted["mlp"], "no ponto"),
        ]
        if transplanted is not None:
            candidates += [
                ("ridge (Wiener)", transplanted["ridge"], "da fase 3"),
                ("MLP", transplanted["mlp"], "da fase 3"),
            ]

        for label, compensator, training in candidates:
            record = score(test, compensator, control)
            record.update(operating_point=name, compensator=label,
                          training=training)
            rows.append(record)
        print(f"  {name}: done")
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 3. the one parameter nobody measured
# ---------------------------------------------------------------------------

#: Amplitudes of the power-line tone, in radians RMS. The range is set by what
#: the two ends would mean rather than by taste: at 0.01 rad the band is below
#: the measurement noise of the monitor and compensation is a non-problem, and
#: at 0.5 rad the polarisation is most of the way around the sphere within one
#: control period and nothing can help. If the conclusion holds across that, it
#: holds wherever the real fibre happens to sit.
AMPLITUDES_RAD = (0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5)


def study_amplitude(epochs: int, test_seeds: int) -> pd.DataFrame:
    """Sweep the tone amplitude, retraining the predictors at each one.

    The amplitude is the only parameter in the phase-3 operating point with no
    measurement behind it — the NIST report shows the tone and never states its
    size — and it is also the one that governs every number phase 3 reports. A
    sweep does not measure it, but it does something almost as good: it turns
    "this rests on a guess" into "this holds for any amplitude in this range",
    which is a claim that survives learning what the real value is.
    """
    control = make_control()
    base = dict(vibration_frequency_hz=60.0, vibration_harmonics=(1.0, 0.5, 0.25),
                thermal_drift_rate_rad_per_s=CALIBRATED_DRIFT_RATE_RAD_PER_S)
    rows = []

    for amplitude in AMPLITUDES_RAD:
        overrides = dict(base, vibration_amplitude_rad=amplitude)
        test = trajectories(overrides, TEST_SEEDS[:test_seeds])
        fitted = train_at(overrides, epochs)
        for label, compensator in (
                ("persistência", Persistence()),
                ("filtro alfa-beta",
                 AlphaBetaFilter(alpha=0.05, beta=0.005, name="filtro alfa-beta")),
                ("ridge (Wiener)", fitted["ridge"]),
                ("MLP", fitted["mlp"])):
            record = score(test, compensator, control)
            record.update(amplitude_rad=amplitude, compensator=label)
            rows.append(record)
        print(f"  {amplitude:g} rad done")
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 4. does it have to be retrained per link?
# ---------------------------------------------------------------------------

#: Frequencies the robustness study trains and tests on. 50 and 60 are the two
#: grid frequencies; 45 and 70 bracket them so a model that merely interpolates
#: between the two real ones is distinguishable from one that generalises.
FREQUENCIES_HZ = (45.0, 50.0, 60.0, 70.0)


def _frequency_overrides(frequency_hz: float) -> dict:
    return dict(vibration_frequency_hz=frequency_hz,
                vibration_harmonics=(1.0, 0.5, 0.25),
                thermal_drift_rate_rad_per_s=CALIBRATED_DRIFT_RATE_RAD_PER_S)


def train_generalist(epochs: int, seed: int = 0):
    """Fit on trajectories whose tone frequency varies from seed to seed.

    Domain randomisation, and the cheapest possible test of whether the
    brittleness found in the field comparison is intrinsic. If one model trained
    across the band keeps most of the specialist's advantage, a deployment needs
    one compensator; if it does not, it needs a polarimeter and a training run
    per link, and that is a very different product.
    """
    control = make_control()
    mixed = []
    for index, train_seed in enumerate(TRAIN_SEEDS):
        frequency = FREQUENCIES_HZ[index % len(FREQUENCIES_HZ)]
        model = make_model(**_frequency_overrides(frequency))
        mixed.append(model.simulate(DURATION_S, SAMPLE_INTERVAL_S,
                                    rng=int(train_seed), track_rotation=True))

    dataset = build_dataset(mixed, control, FEATURE_SPEC, target="measured",
                            rng=seed)
    train, validation = chronological_split(dataset, 0.75, 0.05)
    ridge = fit_ridge(dataset, train)
    mlp, _ = fit_mlp(dataset, train, validation, hidden=(192, 192),
                     epochs=epochs, seed=seed)
    return {"ridge": ridge, "mlp": mlp}


def study_robustness(epochs: int, test_seeds: int) -> pd.DataFrame:
    """Specialists against a generalist, every combination of train and test."""
    control = make_control()
    specialists = {frequency: train_at(_frequency_overrides(frequency), epochs)
                   for frequency in FREQUENCIES_HZ}
    print("  specialists trained")
    generalist = train_generalist(epochs)
    print("  generalist trained")

    rows = []
    for frequency in FREQUENCIES_HZ:
        test = trajectories(_frequency_overrides(frequency),
                            TEST_SEEDS[:test_seeds])
        candidates = [
            ("filtro alfa-beta", "—",
             AlphaBetaFilter(alpha=0.05, beta=0.005, name="filtro alfa-beta")),
            ("MLP", "generalista", generalist["mlp"]),
            ("ridge (Wiener)", "generalista", generalist["ridge"]),
        ]
        for trained_at, fitted in specialists.items():
            label = ("especialista, mesma frequência" if trained_at == frequency
                     else f"especialista de {trained_at:g} Hz")
            candidates.append(("MLP", label, fitted["mlp"]))

        for compensator_name, training, compensator in candidates:
            record = score(test, compensator, control)
            record.update(test_frequency_hz=frequency, training=training,
                          compensator=compensator_name)
            rows.append(record)
        print(f"  tested at {frequency:g} Hz")
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 5. how far does the generalisation reach?
# ---------------------------------------------------------------------------

#: Tones the wide generalist trains on. Reaching 180 Hz matters because that is
#: the third harmonic of a 60 Hz grid, and a link whose third harmonic dominates
#: is a link the narrow 45-70 Hz generalist has never seen.
WIDE_TRAINING_HZ = (45.0, 60.0, 90.0, 120.0, 150.0, 180.0)

#: Held-out tones. The first four fall between training points; 240 Hz is
#: outside the trained range entirely, which is the only honest way to ask
#: whether the model extrapolates or merely interpolates.
WIDE_TEST_HZ = (50.0, 75.0, 105.0, 165.0, 240.0)

#: Amplitudes the amplitude-generalist trains on, and the held-out ones. 0.5 rad
#: is above everything trained on, for the same reason 240 Hz is.
AMPLITUDE_TRAINING_RAD = (0.05, 0.15, 0.30)
AMPLITUDE_TEST_RAD = (0.02, 0.10, 0.20, 0.50)


def _point(frequency_hz: float, amplitude_rad: float) -> dict:
    return dict(vibration_frequency_hz=frequency_hz,
                vibration_harmonics=(1.0, 0.5, 0.25),
                vibration_amplitude_rad=amplitude_rad,
                thermal_drift_rate_rad_per_s=CALIBRATED_DRIFT_RATE_RAD_PER_S)


def train_over(points, epochs: int, seed: int = 0, hidden=(192, 192),
               repeats: int = 2):
    """Fit on trajectories drawn from several operating points at once.

    One trajectory per point per repetition, with the seed advancing across the
    whole set so no two trajectories share a realisation. Mixing at the
    trajectory level rather than within one keeps every training example
    internally consistent — a window that straddled two different tones would
    be a physical impossibility the model could learn to exploit.
    """
    control = make_control()
    mixed = []
    for index, overrides in enumerate(points):
        model = make_model(**overrides)
        for repeat in range(repeats):
            mixed.append(model.simulate(
                DURATION_S, SAMPLE_INTERVAL_S,
                rng=int(1000 + 17 * index + repeat), track_rotation=True))

    dataset = build_dataset(mixed, control, FEATURE_SPEC, target="measured",
                            rng=seed)
    train, validation = chronological_split(dataset, 0.75, 0.05)
    ridge = fit_ridge(dataset, train)
    mlp, _ = fit_mlp(dataset, train, validation, hidden=tuple(hidden),
                     epochs=epochs, seed=seed)
    return {"ridge": ridge, "mlp": mlp}


def study_capacity(epochs: int, test_seeds: int) -> pd.DataFrame:
    """Is the wide generalist's failure capacity, or is it the task?

    Spreading one model over 45-180 Hz makes it worse than the classical filter
    everywhere, including at frequencies it trained on. That has two possible
    causes and they call for opposite conclusions: too little capacity for the
    band, in which case a bigger network fixes it; or a task that cannot be
    solved from this observation window, in which case no network will. The
    only way to tell is to spend the capacity and look.
    """
    control = make_control()
    points = [_point(frequency, 0.30) for frequency in WIDE_TRAINING_HZ]
    variants = {
        "192x192, 2 traj/ponto": dict(hidden=(192, 192), repeats=2,
                                      epochs=epochs),
        "384x384, 4 traj/ponto": dict(hidden=(384, 384), repeats=4,
                                      epochs=epochs * 2),
    }

    rows = []
    for label, settings in variants.items():
        fitted = train_over(points, settings["epochs"], hidden=settings["hidden"],
                            repeats=settings["repeats"])
        print(f"  {label} trained")
        for frequency in WIDE_TEST_HZ:
            test = trajectories(_point(frequency, 0.30), TEST_SEEDS[:test_seeds])
            for name, compensator in (("MLP", fitted["mlp"]),
                                      ("ridge (Wiener)", fitted["ridge"])):
                record = score(test, compensator, control)
                record.update(variant=label, test_point=frequency,
                              compensator=name)
                rows.append(record)
            print(f"    {frequency:g} Hz tested")
    return pd.DataFrame(rows)


def study_reach(epochs: int, test_seeds: int) -> pd.DataFrame:
    """Does the generalist interpolate, extrapolate, or neither?

    Three models, each trained across one axis, all tested on points none of
    them saw. The narrow generalist from the previous study is included as the
    control: it is the one whose reach the limits section flagged as unmeasured.
    """
    control = make_control()

    narrow = train_generalist(epochs)
    print("  narrow generalist (45-70 Hz) trained")
    wide = train_over([_point(frequency, 0.30) for frequency in WIDE_TRAINING_HZ],
                      epochs)
    print("  wide generalist (45-180 Hz) trained")
    across_amplitude = train_over(
        [_point(60.0, amplitude) for amplitude in AMPLITUDE_TRAINING_RAD], epochs)
    print("  amplitude generalist trained")

    rows = []
    for frequency in WIDE_TEST_HZ:
        overrides = _point(frequency, 0.30)
        test = trajectories(overrides, TEST_SEEDS[:test_seeds])
        specialist = train_at(overrides, epochs)
        candidates = [
            ("filtro alfa-beta", "—",
             AlphaBetaFilter(alpha=0.05, beta=0.005, name="filtro alfa-beta")),
            ("MLP", "especialista, mesma frequência", specialist["mlp"]),
            ("MLP", "generalista estreito (45-70 Hz)", narrow["mlp"]),
            ("MLP", "generalista largo (45-180 Hz)", wide["mlp"]),
            ("ridge (Wiener)", "generalista largo (45-180 Hz)", wide["ridge"]),
        ]
        for compensator_name, training, compensator in candidates:
            record = score(test, compensator, control)
            record.update(axis="frequência", test_point=frequency,
                          training=training, compensator=compensator_name)
            rows.append(record)
        print(f"  frequency {frequency:g} Hz tested")

    for amplitude in AMPLITUDE_TEST_RAD:
        overrides = _point(60.0, amplitude)
        test = trajectories(overrides, TEST_SEEDS[:test_seeds])
        specialist = train_at(overrides, epochs)
        candidates = [
            ("filtro alfa-beta", "—",
             AlphaBetaFilter(alpha=0.05, beta=0.005, name="filtro alfa-beta")),
            ("MLP", "especialista, mesma amplitude", specialist["mlp"]),
            ("MLP", "generalista em amplitude", across_amplitude["mlp"]),
            ("ridge (Wiener)", "generalista em amplitude",
             across_amplitude["ridge"]),
        ]
        for compensator_name, training, compensator in candidates:
            record = score(test, compensator, control)
            record.update(axis="amplitude", test_point=amplitude,
                          training=training, compensator=compensator_name)
            rows.append(record)
        print(f"  amplitude {amplitude:g} rad tested")

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("runs/field"))
    parser.add_argument("--models", type=Path,
                        default=Path("runs/compensator/main.pkl"))
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--test-seeds", type=int, default=6)
    parser.add_argument("--spectrum-seeds", type=int, default=3)
    parser.add_argument("--only", nargs="+",
                        default=["exponent", "comparison", "amplitude",
                                 "robustness", "reach", "capacity"],
                        choices=["exponent", "comparison", "amplitude",
                                 "robustness", "reach", "capacity"])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    if "exponent" in args.only:
        print("=== what the measured spectrum pins the drift rate to ===")
        exponent = study_exponent(args.spectrum_seeds)
        exponent.to_csv(args.out / "exponent_scan.csv", index=False)
        print(exponent.pivot(index="drift_rate_rad_per_s", columns="band",
                             values="exponent").round(2).to_string())
        centre, tolerance = FLICKER_EXPONENT
        inside = exponent[(exponent["band"] == "sem banda")
                          & (exponent["exponent"] - centre).abs().le(tolerance)]
        print(f"  measured {centre} ± {tolerance}; "
              f"rates inside it: {sorted(inside['drift_rate_rad_per_s'])}")

    if "comparison" in args.only:
        print("\n=== the phase-3 comparison, re-run at each operating point ===")
        transplanted = None
        if args.models.is_file():
            with open(args.models, "rb") as handle:
                transplanted = pickle.load(handle)
        else:
            print(f"  ({args.models} not found; skipping the transplant column)")

        comparison = study_comparison(transplanted, args.epochs, args.test_seeds)
        comparison.to_csv(args.out / "field_comparison.csv", index=False)
        table = comparison.assign(
            who=comparison["compensator"] + " (" + comparison["training"] + ")")
        print(table.pivot(index="who", columns="operating_point",
                          values="mean_qber").round(4).to_string())

    if "amplitude" in args.only:
        print("\n=== the one parameter nobody measured, swept ===")
        amplitude = study_amplitude(args.epochs, args.test_seeds)
        amplitude.to_csv(args.out / "amplitude_sweep.csv", index=False)
        print(amplitude.pivot(index="amplitude_rad", columns="compensator",
                              values="mean_qber").round(4).to_string())

    if "robustness" in args.only:
        print("\n=== does it have to be retrained per link? ===")
        robustness = study_robustness(args.epochs, args.test_seeds)
        robustness.to_csv(args.out / "robustness.csv", index=False)
        labelled = robustness.assign(
            who=robustness["compensator"] + " · " + robustness["training"])
        print(labelled.pivot(index="who", columns="test_frequency_hz",
                             values="mean_qber").round(4).to_string())

    if "reach" in args.only:
        print("\n=== how far does the generalisation reach? ===")
        reach = study_reach(args.epochs, args.test_seeds)
        reach.to_csv(args.out / "reach.csv", index=False)
        labelled = reach.assign(
            who=reach["compensator"] + " · " + reach["training"])
        for axis, group in labelled.groupby("axis"):
            print(f"\n-- {axis}")
            print(group.pivot(index="who", columns="test_point",
                              values="mean_qber").round(4).to_string())

    if "capacity" in args.only:
        print("\n=== is the wide generalist short of capacity? ===")
        capacity = study_capacity(args.epochs, args.test_seeds)
        capacity.to_csv(args.out / "capacity.csv", index=False)
        labelled = capacity.assign(
            who=capacity["compensator"] + " · " + capacity["variant"])
        print(labelled.pivot(index="who", columns="test_point",
                             values="mean_qber").round(4).to_string())

    print(f"\nwrote CSVs to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
