"""Frozen quantisation campaign v1: the compensator in the author's fixed point, in ORION-Q's closed loop.

The author ported the compensator to fixed point (``qnet_network.deploy``: per-tensor Q
formats, one rounding per layer, SO(3) logarithm and exponential by CORDIC) and
measured, at the development point only, that 16 bits are enough and 14 are a cliff.
This campaign repeats that in ORION-Q at both operating points, on new seeds.

Gates, in order:

1. The author's float compensators are rebuilt from the reviewed export arrays (no
   pickle: ``LearnedCompensator`` around the Ridge coefficients or the MLP layers) and,
   in the author's own loop, reproduce the author's word-length sweep
   (``external/qnet_network/runs/quantisation/width.csv``: TEST_SEEDS[:6], calibration on
   TRAIN_SEEDS[:3]) row by row and column by column, and the 8-stage CORDIC row of
   ``cordic.csv`` (the configuration of the author's RTL).
2. In ORION-Q's loop, the rebuilt float compensator equals ORION-Q's own replay of the
   checkpoint on an evaluation seed, and the replay's mean QBER over the 20 seeds equals
   the closed-loop campaigns' published value.

Evaluation: the fixed-point ranges are calibrated on the tuning seeds through ORION-Q's
loop (the author calibrated on training seeds, never on the scored ones); then every
configuration runs on the 20 evaluation seeds, supervised, with the alpha-beta the
closed-loop campaigns tuned. Primary hypothesis, per model and point: at 16 bits with
8 CORDIC stages (the RTL), the per-seed mean QBER is within 5% of the float model
(exact one-sided sign-flip test on ``q_fixed - 1.05 q_float``, Holm over four).
"""
from __future__ import annotations

import csv
from functools import lru_cache
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import types

import numpy as np

from . import polarisation_loop as loop
from . import polarisation_plant as plant
from .comparative_evaluation import holm_adjust, paired_sign_flip_test
from ..model_intake.artifacts import COMPENSATION_ROOT, PINS, ReviewedArchive
from ..model_intake.sync_session import _constant, _unique

ROOT = Path(__file__).resolve().parents[3]
REFERENCES = {"width.csv": "external/qnet_network/runs/quantisation/width.csv",
              "cordic.csv": "external/qnet_network/runs/quantisation/cordic.csv"}
CONFIGURATIONS = [{"width": 16, "cordic": 8}, {"width": 16, "cordic": 16}, {"width": 14, "cordic": 16},
                  {"width": 12, "cordic": 16}]
PROTOCOL = {
    "schema": "orion.polarisation-quantisation-protocol.v1", "target_id": loop.TARGET_ID,
    "evidence": "simulated", "mode": "simulation",
    "fixed_point": "qnet_network.deploy (author): collect_calibration ranges, build_quantised, QuantisedCompensator "
                   "(CORDIC datapath 32 bits, 20 fraction bits)",
    "models": ["ridge", "mlp"],
    "reproduction": {
        "references": {name: {"path": path} for name, path in REFERENCES.items()},
        "reference_sha256": {"width.csv": "0ee9be41ff3ff72aa81079a5872d5f59e226196b497f250906d158a3e5b21bc0",
                             "cordic.csv": "6ce288930e6d676644740263730dc3eb055b07dbc241ae91b504c03d50eb6b44"},
        "procedure": "scripts/evaluate_quantisation.py study(): task3 TEST_SEEDS[:6], rng 7000+i, calibration on "
                     "TRAIN_SEEDS[:3]; widths 8-24 at CORDIC 16, and 16 bits at CORDIC 8",
        "columns": ["mean_qber", "median_qber", "p95_qber", "uptime", "mean_key_rate_hz", "mean_residual_mrad",
                    "p95_residual_mrad", "mean_prediction_error_mrad", "qber_stderr", "saturations"],
        "tolerance": "relative 1e-12 (absolute below 1): |ours - author| <= 1e-12 * max(1, |author|)"},
    "operating_points": {
        "calibrado": {"alpha_beta": {"alpha": 0.01, "beta": 0.0},
                      "float_mean_qber": {"ridge": 0.013079433498572032, "mlp": 0.013422886566343249},
                      "source": "docs/results/orion_q_polarisation_closed_loop_20260929_summary.json"},
        "desenvolvimento": {"alpha_beta": {"alpha": 0.03, "beta": 0.005},
                            "float_mean_qber": {"ridge": 0.007612920696349887, "mlp": 0.007220517693620397},
                            "source": "docs/results/orion_q_polarisation_closed_loop_dev_20260929_summary.json"}},
    "configurations": CONFIGURATIONS,
    "calibration": {"plant_seeds": [100, 101, 102, 103], "monitoring_seeds": [7, 8, 9, 10], "duration_s": 20.0},
    "duration_s": 20.0, "evaluation_plant_seeds": list(range(1000, 1020)), "monitoring_seed_offset": 6000,
    "supervisor": {"max_command_step_rad": 1.5707963267948966, "max_consecutive_faults": 8,
                   "rotation_tolerance": 1e-7},
    "fixed_point_output": "projected onto SO(3) (nearest rotation by SVD) before the supervisor, as an actuator "
                          "driver would; the largest Frobenius distance moved is recorded",
    "primary": {"configuration": {"width": 16, "cordic": 8}, "margin": 0.05,
                "endpoint": "per-seed mean QBER, fixed point against the float model"},
    "test": "exact one-sided paired sign-flip test, H1: mean(q_fixed - (1 + margin) q_float) < 0",
    "multiplicity": "Holm over the four model x point hypotheses", "alpha": 0.05,
    "bootstrap": {"resamples": 10000, "seed": 20261022, "confidence": 0.95},
    "interpretation": "simulated closed loop with an ideal actuator; the fixed-point model is the author's reference, "
                      "whose RTL tools/verify_author_rtl.py checks bit for bit",
}
SOURCES = tuple(f"quantum_autopilot/{path}" for path in (
    "control_plane/polarisation_quantisation.py", "control_plane/polarisation_loop.py",
    "control_plane/polarisation_plant.py", "model_intake/compensation.py")) + (
    "qnet_network/deploy/quantise.py", "qnet_network/deploy/fixed_point.py", "qnet_network/compensation/closed_loop.py",
    "qnet_network/compensation/predictors.py")


def load_protocol(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(32769)
    if len(raw) > 32768:
        raise ValueError("protocol exceeds 32 KiB")
    parsed = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    if json.dumps(parsed, sort_keys=True) != json.dumps(PROTOCOL, sort_keys=True):
        raise ValueError("protocol differs from frozen v1; create a separately reviewed version")
    return raw, PROTOCOL


@lru_cache(maxsize=1)
def task3():
    """The author's phase-3 benchmark (``external/qnet_network/scripts/task3.py``), under a name of its own."""
    spec = importlib.util.spec_from_file_location("orion_qnet_network_task3",
                                                  ROOT / "external" / "qnet_network" / "scripts" / "task3.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# the author's compensator, rebuilt from the reviewed arrays
# ---------------------------------------------------------------------------

class _Ridge:
    def __init__(self, arrays):
        self.coef_, self.intercept_ = np.asarray(arrays["coef"]), np.asarray(arrays["intercept"])

    def predict(self, features):
        return features @ self.coef_.T + self.intercept_


class _MLP:
    """The numpy MLP's forward pass: ReLU on every layer but the last (``deploy.quantise._layers_of``)."""

    def __init__(self, arrays):
        self.network = types.SimpleNamespace(weights=[np.asarray(arrays[f"weight_{i}"]) for i in range(3)],
                                             biases=[np.asarray(arrays[f"bias_{i}"]) for i in range(3)])

    def predict(self, features):
        for index, (weight, bias) in enumerate(zip(self.network.weights, self.network.biases)):
            features = features @ weight + bias
            if index < len(self.network.weights) - 1:
                features = np.maximum(features, 0.0)
        return features


def author_compensator(archive, point, model):
    from qnet_network.compensation.predictors import FeatureSpec, LearnedCompensator, Standardiser

    prefix = COMPENSATION_ROOT + point + "/model/" + model
    meta, arrays = archive.json(prefix + ".json"), archive.arrays(prefix + ".npz")
    spec = meta["spec"]
    return LearnedCompensator(
        model=_Ridge(arrays) if model == "ridge" else _MLP(arrays),
        spec=FeatureSpec(window=spec["window"], use_covariates=spec["use_covariates"],
                         horizon_scale=spec["horizon_scale"], lags=spec["lags"], tone_lags=spec["tone_lags"]),
        standardiser=Standardiser(mean=np.asarray(arrays["standardiser_mean"]),
                                  scale=np.asarray(arrays["standardiser_scale"])),
        name=model)


def quantisation_config(width, cordic):
    from qnet_network.deploy import CordicConfig, QuantisationConfig

    return QuantisationConfig(width=width, cordic=CordicConfig(cordic, 20, 32))


# ---------------------------------------------------------------------------
# gate 1: the author's sweep (evaluate_quantisation.py score/study, transcribed)
# ---------------------------------------------------------------------------

def _score(trajectories, compensator, control):
    from qnet_network.compensation import run_closed_loop

    rows = [run_closed_loop(trajectory, compensator, control, rng=7000 + index).summary()
            for index, trajectory in enumerate(trajectories)]
    aggregate = {key: float(np.mean([row[key] for row in rows])) for key in rows[0] if key != "compensator"}
    aggregate["qber_stderr"] = float(np.std([row["mean_qber"] for row in rows], ddof=1) / np.sqrt(len(rows)))
    return aggregate


def _relative_error(ours, theirs):
    return abs(float(ours) - float(theirs)) / max(1.0, abs(float(theirs)))


def reproduce_author(archive, protocol=PROTOCOL, progress=None, models=None):
    from qnet_network.deploy import build_quantised, collect_calibration

    t3 = task3()
    control = t3.make_control()
    calibration_runs = t3.make_trajectories(t3.TRAIN_SEEDS[:3])
    trajectories = t3.make_trajectories(t3.TEST_SEEDS[:6])
    references = {}
    for name, path in REFERENCES.items():
        text = (ROOT / path).read_bytes()
        if hashlib.sha256(text).hexdigest() != protocol["reproduction"]["reference_sha256"][name]:
            raise ValueError(f"{path} differs from the frozen reference")
        references[name] = list(csv.DictReader(io.StringIO(text.decode("utf-8"))))
    wanted = [(row, "width.csv") for row in references["width.csv"]]
    wanted += [(row, "cordic.csv") for row in references["cordic.csv"] if row["cordic_iterations"] in ("8.0",)]
    rows, worst = [], 0.0
    for model in models or protocol["models"]:
        compensator = author_compensator(archive, "desenvolvimento", model)
        calibration = collect_calibration(compensator, calibration_runs, control)
        for reference, source in wanted:
            if reference["model"] != model:
                continue
            if reference["variant"] == "float64":
                ours = _score(trajectories, compensator, control)
                ours["saturations"] = 0
            else:
                config = quantisation_config(int(float(reference["width"])), int(float(reference["cordic_iterations"])))
                quantised = build_quantised(compensator, calibration, config)
                ours = _score(trajectories, quantised, control)
                ours["saturations"] = quantised.report()["saturations"]
            errors = {column: _relative_error(ours[column], reference[column])
                      for column in protocol["reproduction"]["columns"]}
            column = max(errors, key=errors.get)
            worst = max(worst, errors[column])
            rows.append({"reference": source, "model": model, "variant": reference["variant"],
                         "mean_qber": ours["mean_qber"], "author_mean_qber": float(reference["mean_qber"]),
                         "max_relative_error": errors[column], "worst_column": column,
                         "passed": errors[column] <= 1e-12})
            if progress:
                progress(f"  {model} {reference['variant']}: QBER {ours['mean_qber']:.6f} "
                         f"(author {float(reference['mean_qber']):.6f})")
    return rows, worst


# ---------------------------------------------------------------------------
# ORION-Q loop
# ---------------------------------------------------------------------------

class AuthorController:
    """The author's compensator (float or fixed point) behind ORION-Q's controller interface.

    A fixed-point prediction is a rotation only to the word's resolution, and the
    supervisor refuses anything outside SO(3); with ``project`` it is replaced by the
    nearest rotation (SVD), as an actuator driver would, and the largest distance moved
    is kept.
    """

    def __init__(self, compensator, controller_id, project=False):
        self.compensator, self.controller_id, self.project = compensator, controller_id, project
        self.max_projection = 0.0

    def reset(self):
        self.compensator.reset()

    def propose(self, time_s, measured, covariates):
        proposal = np.asarray(self.compensator.predict(time_s, measured, covariates, plant.LATENCY_S), dtype=float)
        if not self.project or not np.isfinite(proposal).all():
            return proposal
        left, _, right = np.linalg.svd(proposal)
        rotation = left @ np.diag([1.0, 1.0, np.sign(np.linalg.det(left @ right))]) @ right
        self.max_projection = max(self.max_projection, float(np.linalg.norm(rotation - proposal)))
        return rotation


def _supervisor(protocol):
    return loop.PolarisationSupervisor(loop.PolarisationSupervisorConfig(**protocol["supervisor"]))


def _run(trajectory, controller, point, seed, monitor, protocol, fallback):
    return loop.run_loop(trajectory, controller, monitoring_seed=int(monitor), operating_point=point,
                         plant_seed=int(seed), supervisor=_supervisor(protocol), fallback=fallback)


def calibrate(compensator, point, protocol=PROTOCOL):
    """``collect_calibration`` of the author, with the traces taken from ORION-Q's loop on the tuning seeds."""
    from qnet_network.deploy.quantise import _layers_of, _Recorder

    raw = []
    config = protocol["calibration"]
    for seed, monitor in zip(config["plant_seeds"], config["monitoring_seeds"]):
        recorder = _Recorder(compensator)
        trajectory = plant.simulate(plant.OPERATING_POINTS[point], config["duration_s"], seed)
        _run(trajectory, AuthorController(recorder, "calibration"), point, seed, monitor, protocol, None)
        raw.extend(recorder.features)
    raw = np.asarray(raw, dtype=float)
    scaled = compensator.standardiser.transform(raw)
    layers = _layers_of(compensator)
    activations, pre_activations = scaled, []
    for weight, bias, relu in layers:
        pre = activations @ weight + bias
        pre_activations.append(pre)
        activations = np.maximum(pre, 0.0) if relu else pre
    return {"raw": raw, "scaled": scaled, "pre_activations": pre_activations, "layers": layers}


def _label(config):
    return f"{config['width']}b/CORDIC{config['cordic']}"


def evaluate_point(archive_path, point, protocol=PROTOCOL):
    """Calibrate, then run float replay, author float, every fixed-point configuration and the alpha-beta."""
    from qnet_network.deploy import build_quantised

    archive = ReviewedArchive(archive_path, "qnet")
    tuned = protocol["operating_points"][point]["alpha_beta"]
    controllers, saturations = {}, {}
    for model in protocol["models"]:
        compensator = author_compensator(archive, point, model)
        calibration = calibrate(compensator, point, protocol)
        controllers[f"{model}|replay"] = loop.LearnedCheckpoint(f"qnet.compensation.{point}.{model}", archive)
        controllers[f"{model}|author-float"] = AuthorController(author_compensator(archive, point, model),
                                                                f"{model}|author-float")
        for config in protocol["configurations"]:
            quantised = build_quantised(compensator, calibration, quantisation_config(config["width"], config["cordic"]))
            controllers[f"{model}|{_label(config)}"] = AuthorController(quantised, f"{model}|{_label(config)}",
                                                                         project=True)
    controllers["alpha_beta"] = loop.AlphaBeta(tuned["alpha"], tuned["beta"])
    per_seed = {name: [] for name in controllers}
    faults = {name: 0 for name in controllers}
    for seed in protocol["evaluation_plant_seeds"]:
        trajectory = plant.simulate(plant.OPERATING_POINTS[point], protocol["duration_s"], seed)
        for name, controller in controllers.items():
            record = _run(trajectory, controller, point, seed, seed + protocol["monitoring_seed_offset"], protocol,
                          None if name == "alpha_beta" else loop.AlphaBeta(tuned["alpha"], tuned["beta"]))
            per_seed[name].append(record.summary["mean_qber"])
            faults[name] += int(record.supervisor["faults"])
    for name, controller in controllers.items():
        if getattr(controller, "project", False):
            saturations[name] = {"saturations": int(controller.compensator.report()["saturations"]),
                                 "max_projection_frobenius": controller.max_projection}
    return point, per_seed, faults, saturations


def _reproduce_model(args):
    archive_path, protocol, model = args
    return reproduce_author(ReviewedArchive(archive_path, "qnet"), protocol, None, [model])


def _evaluate_point(args):
    return evaluate_point(*args)


def statistics(results, protocol=PROTOCOL):
    bootstrap = protocol["bootstrap"]
    generator = np.random.default_rng(bootstrap["seed"])
    lower = (1 - bootstrap["confidence"]) / 2
    margin = protocol["primary"]["margin"]
    primary_label = _label(protocol["primary"]["configuration"])
    rows, primary = [], []
    for point, per_seed in results.items():
        baseline = np.asarray(per_seed["alpha_beta"])
        for model in protocol["models"]:
            reference = np.asarray(per_seed[f"{model}|replay"])
            for config in protocol["configurations"]:
                label = _label(config)
                fixed = np.asarray(per_seed[f"{model}|{label}"])
                draws = generator.integers(0, len(fixed), size=(bootstrap["resamples"], len(fixed)))
                ratios = fixed[draws].mean(axis=1) / reference[draws].mean(axis=1)
                row = {"operating_point": point, "model": model, "configuration": label,
                       "float_mean_qber": float(reference.mean()), "fixed_mean_qber": float(fixed.mean()),
                       "fixed_to_float_ratio": float(fixed.mean() / reference.mean()),
                       "ratio_ci95": [float(np.quantile(ratios, lower)), float(np.quantile(ratios, 1 - lower))],
                       "worst_seed_ratio": float(np.max(fixed / reference)),
                       "alpha_beta_mean_qber": float(baseline.mean()),
                       "beats_alpha_beta_seeds": int(np.sum(fixed < baseline)), "n_seeds": len(fixed)}
                if label == primary_label:
                    test = paired_sign_flip_test((1 + margin) * reference - fixed, alternative="greater")
                    row.update(p_value=test["p_value"], method=test["method"])
                    primary.append(row)
                rows.append(row)
    for row, value in zip(primary, holm_adjust([row["p_value"] for row in primary])):
        row["holm_p_value"] = float(value)
        row["non_inferior"] = bool(value < protocol["alpha"])
    return rows


def _export(text, path):
    with Path(path).open("xb") as stream:
        stream.write(text.encode("utf-8"))


def _hashes():
    return {path: hashlib.sha256((ROOT / "src" / path).read_bytes()).hexdigest() for path in SOURCES}


def run_campaign(archive_path, protocol_path, output, *, progress=None, workers=1):
    raw, protocol = load_protocol(protocol_path)
    archive = ReviewedArchive(archive_path, "qnet")
    manifest = {"protocol": protocol, "protocol_file_sha256": hashlib.sha256(raw).hexdigest(),
                "archive_sha256": archive.sha256, "expected_archive_sha256": PINS["qnet"], "source_sha256": _hashes()}
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    _export(json.dumps(manifest, indent=2, allow_nan=False), output / "frozen-protocol.json")
    say = progress or (lambda text: None)

    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=min(workers, len(protocol["models"]))) as pool:
            parts = list(pool.map(_reproduce_model, [(archive_path, protocol, model) for model in protocol["models"]]))
        reproduction = [row for rows, _ in parts for row in rows]
        worst = max(value for _, value in parts)
    else:
        reproduction, worst = reproduce_author(archive, protocol, say)
    say(f"Author's sweep: {sum(row['passed'] for row in reproduction)}/{len(reproduction)} rows reproduced "
        f"(max error {worst:.1e}).")
    if not all(row["passed"] for row in reproduction):
        raise ValueError("the author's quantisation sweep was not reproduced; no evaluation")

    points = list(protocol["operating_points"])
    tasks = [(archive_path, point, protocol) for point in points]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=min(workers, len(tasks))) as pool:
            outcomes = list(pool.map(_evaluate_point, tasks))
    else:
        outcomes = [evaluate_point(*task) for task in tasks]
    results = {point: per_seed for point, per_seed, _, _ in outcomes}
    consistency = []
    for point, per_seed, faults, saturations in outcomes:
        for model in protocol["models"]:
            replay = np.asarray(per_seed[f"{model}|replay"])
            author = np.asarray(per_seed[f"{model}|author-float"])
            published = protocol["operating_points"][point]["float_mean_qber"][model]
            consistency.append({"operating_point": point, "model": model,
                                "author_float_vs_replay_max_difference": float(np.max(np.abs(author - replay))),
                                "replay_mean_qber": float(replay.mean()), "closed_loop_campaign_mean_qber": published,
                                "passed": bool(np.max(np.abs(author - replay)) <= 1e-12
                                               and abs(float(replay.mean()) - published) <= 1e-12)})
        say(f"{point}: " + ", ".join(f"{name} {np.mean(values):.5f}" for name, values in per_seed.items()))
    if not all(row["passed"] for row in consistency):
        _export(json.dumps({"status": "consistency_failed", "consistency": consistency}, indent=2),
                output / "consistency-failed.json")
        raise ValueError("float compensator in ORION-Q's loop differs from the replay or the closed-loop campaign")
    if _hashes() != manifest["source_sha256"]:
        raise ValueError("source changed during campaign; no completion summary")
    rows = statistics(results, protocol)
    summary = {"schema": "orion.polarisation-quantisation-results.v1", "status": "complete", "manifest": manifest,
               "reproduction": reproduction, "consistency": consistency, "comparisons": rows,
               "per_seed_mean_qber": {point: values for point, values in results.items()},
               "supervisor_faults": {point: faults for point, _, faults, _ in outcomes},
               "fixed_point": {point: saturations for point, _, _, saturations in outcomes},
               "policy_activated": False, "physical_action_performed": False,
               "limitations": ["Ideal actuator; the fixed-point model is the author's bit-exact reference, not the "
                               "synthesised hardware.",
                               "Ranges calibrated on four tuning seeds of each point."]}
    _export(json.dumps(summary, indent=2, allow_nan=False), output / "summary.json")
    return summary


__all__ = ["PROTOCOL", "author_compensator", "calibrate", "load_protocol", "reproduce_author", "run_campaign"]
