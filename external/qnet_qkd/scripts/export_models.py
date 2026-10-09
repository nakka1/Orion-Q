#!/usr/bin/env python3
"""Export the three phase-5 models as a standalone bundle, with worked examples.

    python scripts/export_models.py --out modelo_qkd_ml

Writes:

``modelos/``
    The attack detector (the scikit-learn ensemble plus a JSON with its
    standardiser, class order, calibrated threshold and hysteresis), the
    key-rate agent's weights and action box, and the synchroniser's ridge
    coefficients.  ``modelos/fpga/`` carries the pruned 14-bit ensemble and the
    12-bit synchroniser ROM exactly as the RTL cosimulation verified them.

``exemplos/``
    Inputs and outputs for each model, produced by running
    ``exportacao/inferencia.py`` — the standalone code the bundle ships — on
    held-out data.

Every example is also run through ``qnet_qkd`` itself, and the export
**fails** if the standalone code disagrees with the package on any of them: a
class, a probability beyond 1e-9, an observation vector, a logit or an
offset.  The bundle is therefore a faithful copy, and the example outputs are
the package's outputs.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pickle
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from task5 import (BUDGET, DEAD_TIME_S, DETECTOR_SEED, FEATURE_WINDOW,  # noqa: E402
                   LINK, SYNC_TEST_SEEDS, SYNC_TRAIN_SEEDS, TEST_SEEDS,
                   UNTRAINABLE_LABELS, make_gate, make_keyrate_env,
                   make_timing_trajectories)
from qnet_qkd.qkd.detection import (FEATURE_NAMES,  # noqa: E402
                                          SustainedDetector,
                                          build_detection_features, episode_split)
from qnet_qkd.qkd.keyrate import ACTIONS, KeyRatePolicy  # noqa: E402
from qnet_qkd.qkd.timing import (ClockModel, MeasurementConfig,  # noqa: E402
                                       _sync_features, build_sync_dataset,
                                       fit_sync_predictor, measure_offset,
                                       timing_spread)
from qnet_qkd.physics.decoy import (decoy_key_rate_hz,  # noqa: E402
                                          single_photon_bounds)

#: What the session log carries that a receiver does not publish.  Stripped from
#: the example inputs and shipped separately as the answer key.
ANSWER_COLUMNS = ("label", "onset_s", "active", "strength", "true_misalignment",
                  "true_transmittance", "true_single_photon_yield", "severity",
                  "profile", "thermal_cooccurrence")


def _load_standalone(path: Path):
    spec = importlib.util.spec_from_file_location("inferencia", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["inferencia"] = module
    spec.loader.exec_module(module)
    return module


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"[export] DIVERGÊNCIA: {message}")


# ---------------------------------------------------------------------------
# detector
# ---------------------------------------------------------------------------

def export_detector(out: Path, examples: Path, detector_dir: Path, standalone) -> dict:
    with open(detector_dir / "detectors.pkl", "rb") as handle:
        fitted = pickle.load(handle)
    gated = fitted["gated"]
    settings = json.loads((detector_dir / "settings.json").read_text())

    with open(out / "detector_boosting.pkl", "wb") as handle:
        pickle.dump(gated.model, handle)
    config = {
        "feature_names": list(FEATURE_NAMES),
        "standardiser_mean": gated.standardiser.mean.tolist(),
        "standardiser_scale": gated.standardiser.scale.tolist(),
        "classes": list(gated.classes),
        "attack_threshold": float(gated.attack_threshold),
        "sustain_periods": int(settings.get("sustain", 3)),
        "window": FEATURE_WINDOW.window,
        "baseline_periods": FEATURE_WINDOW.baseline_periods,
        "period_s": float(settings.get("period_s", 1.0)),
        "trained_on": "sessões simuladas adversas, 20–70 km, episódios de treino "
                      "(runs/detector); ver o artigo, Seções 6 e 8",
        "sklearn_version": __import__("sklearn").__version__,
    }
    (out / "detector_config.json").write_text(json.dumps(config, indent=2))

    frame = pd.read_csv(detector_dir / "sessions.csv")
    _, _, index = build_detection_features(frame, FEATURE_WINDOW)
    _, test = episode_split(index, 0.7, seed=DETECTOR_SEED)
    test_episodes = set(index.loc[test, "episode"].unique())

    labels = frame.groupby("episode")["label"].first()
    wanted = ["normal", "thermal_drift", "loss_event", "intercept_resend", "pns",
              "detector_blinding", *UNTRAINABLE_LABELS]
    chosen = {}
    for label in wanted:
        candidates = sorted(e for e in test_episodes if labels.get(e) == label)
        if candidates:
            chosen[label] = candidates[0]

    package = SustainedDetector(base=gated, periods=config["sustain_periods"])
    detector = standalone.AttackDetector.load(out)
    summary = []
    for label, episode in chosen.items():
        log = frame[frame["episode"] == episode].sort_values("period")
        public = log.drop(columns=[c for c in ANSWER_COLUMNS if c in log.columns])
        stem = f"detector_{label}"
        public.drop(columns=["episode"]).to_csv(examples / f"{stem}_entrada.csv", index=False)
        log[["period", "time_s", "label", "onset_s", "strength", "profile",
             "thermal_cooccurrence"]].to_csv(examples / f"{stem}_gabarito.csv", index=False)

        result = detector.classify_session(public)
        result.to_csv(examples / f"{stem}_saida.csv", index=False,
                      float_format="%.6g")

        # -- parity against the package ------------------------------------
        features, _, pindex = build_detection_features(log, FEATURE_WINDOW)
        mine, _ = standalone.detection_features(public, FEATURE_WINDOW.window,
                                                FEATURE_WINDOW.baseline_periods)
        _check(np.allclose(features, mine, rtol=0, atol=1e-12),
               f"atributos do episódio {episode}")
        _check(np.array_equal(np.asarray(gated.predict(features)), result["per_period"].to_numpy()),
               f"decisão por período no episódio {episode}")
        _check(np.array_equal(np.asarray(package.predict_sequence(features, pindex)),
                              result["alarm"].to_numpy()),
               f"alarme com histerese no episódio {episode}")
        _check(np.allclose(1.0 - gated.model.predict_proba(gated.standardiser.transform(features))
                           [:, list(gated.classes).index("normal")],
                           result["attack_probability"].to_numpy(), atol=1e-9),
               f"probabilidade de ataque no episódio {episode}")

        onset = float(log["onset_s"].dropna().iloc[0]) if log["onset_s"].notna().any() else None
        after = result[result["time_s"] >= onset] if onset is not None else result
        alarms = result[result["alarm"] != "normal"]
        first = alarms["time_s"].min() if len(alarms) else None
        called = after["alarm"][after["alarm"] != "normal"]
        summary.append({
            "exemplo": stem, "classe_verdadeira": label, "episódio": int(episode),
            "vão_km": float(log["length_km"].iloc[0]),
            "perfil": str(log["profile"].iloc[0]) if "profile" in log else None,
            "início_do_evento_s": onset,
            "primeiro_alarme_s": None if first is None or pd.isna(first) else float(first),
            "atraso_s": (None if onset is None or first is None or pd.isna(first)
                         else float(first - onset)),
            "classe_mais_alarmada_após_início": (called.value_counts().index[0]
                                                 if len(called) else "normal"),
            "fração_de_períodos_alarmados_após_início": float((after["alarm"] != "normal").mean()),
            "alarmes_antes_do_início": int(((result["time_s"] < onset) & (result["alarm"] != "normal")).sum())
                                       if onset is not None else int((result["alarm"] != "normal").sum()),
        })
    (examples / "detector_resumo.json").write_text(json.dumps(summary, indent=2,
                                                              ensure_ascii=False))
    return {"episodes": len(chosen), "summary": summary}


# ---------------------------------------------------------------------------
# key-rate agent
# ---------------------------------------------------------------------------

def export_agent(out: Path, examples: Path, policy_path: Path, temperature: float,
                 standalone, length_km: float = 50.0, periods: int = 40) -> dict:
    shutil.copy(policy_path, out / "agente_taxa_chave.npz")
    env = make_keyrate_env(length_km, rng=0)
    box = {"signal_intensity_range": list(env.config.signal_intensity_range),
           "decoy_intensity_range": list(env.config.decoy_intensity_range),
           "clock_range": list(env.config.clock_range),
           "signal_probability_range": list(env.config.signal_probability_range),
           "minimum_decoy_probability": env.config.minimum_decoy_probability,
           "decoy_ratio_max": env.config.decoy_ratio_max}
    (out / "agente_config.json").write_text(json.dumps({
        "temperature": temperature,
        "actions": [action.name for action in ACTIONS],
        "observation": [
            "log ganho de sinal / calibração", "log ganho de decoy / calibração",
            "log ganho de vácuo / prob. de escuro", "erro de sinal (%)",
            "erro de decoy (%)", "log Y1 inferior / calibração", "e1 superior (%)",
            "log taxa provável / calibração", "mu", "nu/mu", "log clock / 1 GHz",
            "prob. de sinal", "log (cliques/s x tempo morto)",
            "log taxa do período anterior / calibração", "log ganho de sinal absoluto",
            "log taxa de calibração / 1 Mbit/s"],
        "box": box, "dead_time_s": env.config.dead_time_s,
        "trained_on": "generalista, vão sorteado em 25–80 km (runs/keyrate/generalist2)",
    }, indent=2, ensure_ascii=False))

    package = KeyRatePolicy(rng=0).load(policy_path)
    package.freeze_normalisers()
    agent = standalone.KeyRateAgent.load(out)

    seed = int(TEST_SEEDS[0])
    state = env.reset(rng=seed)
    generator = np.random.default_rng(seed)
    rows = []
    for step in range(periods):
        previous = env._last_observables
        settings = env.settings
        public = None
        if previous is not None:
            y1, e1 = single_photon_bounds(previous)
            public = standalone.PeriodObservables(
                signal_gain=previous.signal_gain, decoy_gain=previous.decoy_gain,
                vacuum_gain=previous.vacuum_gain, signal_error=previous.signal_error,
                decoy_error=previous.decoy_error, single_photon_yield_lower=y1,
                single_photon_error_upper=e1, key_rate_hz=decoy_key_rate_hz(previous))
        mine_settings = standalone.TransmitterSettings(
            signal_intensity=settings.signal_intensity, decoy_intensity=settings.decoy_intensity,
            vacuum_probability=settings.vacuum_probability,
            signal_probability=settings.signal_probability, clock_rate_hz=settings.clock_rate_hz)
        observation = standalone.keyrate_observation(
            public, mine_settings, calibration_gain=env._calibration_gain,
            calibration_rate_hz=env._calibration_rate,
            dark_count_probability=env.link.dark_count_probability,
            misalignment_error=env.link.misalignment_error,
            previous_rate_hz=env._previous_rate_hz, dead_time_s=env.config.dead_time_s)

        _check(np.allclose(observation, state, rtol=0, atol=1e-12), f"observação no passo {step}")
        _check(np.allclose(agent.logits(observation), package.logits(state), atol=1e-9),
               f"logits no passo {step}")

        probabilities = agent.action_probabilities(observation)
        action = agent.act(observation, generator)
        new_settings = agent.apply(mine_settings, action)
        package_settings = env.apply(action)
        _check(np.isclose(new_settings.signal_intensity, package_settings.signal_intensity)
               and np.isclose(new_settings.clock_rate_hz, package_settings.clock_rate_hz)
               and np.isclose(new_settings.signal_probability, package_settings.signal_probability),
               f"ajuste de parâmetros no passo {step}")

        state, _, done, info = env.step(action)
        rows.append({
            "periodo": step,
            "entrada": {"observaveis_do_periodo_anterior": None if public is None else public.__dict__,
                        "parametros_atuais": mine_settings.__dict__},
            "observacao_16": observation.round(9).tolist(),
            "saida": {"probabilidades": {a.name: round(float(p), 6)
                                         for a, p in zip(ACTIONS, probabilities)},
                      "acao_gulosa": ACTIONS[int(np.argmax(probabilities))].name,
                      "acao_amostrada_T=%.2f" % temperature: ACTIONS[action].name,
                      "novos_parametros": new_settings.__dict__},
            "resultado_no_simulador": {"bits_de_chave": float(info["key_bits"])},
        })
        if done:
            break

    calibration = {"calibration_gain": env._calibration_gain,
                   "calibration_rate_hz": env._calibration_rate,
                   "dark_count_probability": env.link.dark_count_probability,
                   "misalignment_error": env.link.misalignment_error,
                   "dead_time_s": env.config.dead_time_s}
    (examples / "agente_episodio_50km.json").write_text(json.dumps(
        {"vão_km": length_km, "semente": seed, "temperatura": temperature,
         "calibração_do_enlace": calibration, "periodos": rows},
        indent=2, ensure_ascii=False, default=float))
    return {"periods": len(rows)}


# ---------------------------------------------------------------------------
# synchroniser
# ---------------------------------------------------------------------------

def export_sync(out: Path, examples: Path, standalone,
                length_km: float = LINK.length_km, samples: int = 600) -> dict:
    gate = make_gate()
    trajectories = make_timing_trajectories(SYNC_TRAIN_SEEDS, length_km)
    features, targets, _, _ = build_sync_dataset(trajectories, gate)
    learned = fit_sync_predictor(features, targets)
    horizon = gate.latency_s
    (out / "sincronizador_ridge.json").write_text(json.dumps({
        "coefficients": np.asarray(learned.model.coef_).reshape(-1).tolist(),
        "intercept": float(np.asarray(learned.model.intercept_).reshape(-1)[0]),
        "window": learned.window, "scale": learned.scale,
        "update_period_s": gate.update_period_s, "horizon_s": horizon,
        "features": [f"offset[t-{lag}] - offset[t] (ns)" for lag in range(1, learned.window)]
                    + ["T[t] - T[t-7] (°C)", "dT/dt médio na janela (°C/s)"],
        "trained_on": f"{len(features)} janelas de {len(SYNC_TRAIN_SEEDS)} trajetórias "
                      f"de 1 h a {length_km:.0f} km",
    }, indent=2, ensure_ascii=False))

    predictor = standalone.SyncPredictor.load(out)
    seed = int(SYNC_TEST_SEEDS[0])
    trajectory = ClockModel(length_km=length_km).simulate(3600.0, 1.0, rng=seed + 10_000)
    measurement = MeasurementConfig(photons=gate.measurement.photons,
                                    spread_s=timing_spread(BUDGET, length_km))
    measured = measure_offset(trajectory.offset_s, measurement, rng=seed)
    temperature = trajectory.temperature_c

    steps = int(round(horizon / gate.update_period_s))
    rows = []
    for index in range(learned.window - 1, min(samples, len(measured) - steps)):
        history = measured[index - learned.window + 1: index + 1]
        temps = temperature[index - learned.window + 1: index + 1]
        mine = predictor.predict(history, temps)
        theirs = history[-1] + float(learned.model.predict(
            _sync_features(list(history), list(temps), learned.scale)[None, :])
            .reshape(-1)[0]) * learned.scale
        _check(abs(mine - theirs) < 1e-18, f"offset previsto na amostra {index}")
        rows.append({"time_s": float(trajectory.time_s[index]),
                     "offset_medido_s": float(measured[index]),
                     "temperatura_c": float(temperature[index]),
                     "offset_previsto_em_t+2s_s": mine,
                     "offset_verdadeiro_em_t+2s_s": float(trajectory.offset_s[index + steps]),
                     "offset_ultima_medida_s": float(measured[index])})
    frame = pd.DataFrame(rows)
    frame["erro_previsto_ps"] = (frame["offset_verdadeiro_em_t+2s_s"]
                                 - frame["offset_previsto_em_t+2s_s"]) * 1e12
    frame["erro_ultima_medida_ps"] = (frame["offset_verdadeiro_em_t+2s_s"]
                                      - frame["offset_ultima_medida_s"]) * 1e12
    pd.DataFrame({"time_s": trajectory.time_s[:samples],
                  "offset_medido_s": measured[:samples],
                  "temperatura_c": temperature[:samples]}).to_csv(
        examples / "sincronizador_entrada.csv", index=False, float_format="%.9e")
    frame.to_csv(examples / "sincronizador_saida.csv", index=False, float_format="%.9e")
    return {"samples": len(frame),
            "rms_predicted_ps": float(np.sqrt(np.mean(frame["erro_previsto_ps"] ** 2))),
            "rms_hold_ps": float(np.sqrt(np.mean(frame["erro_ultima_medida_ps"] ** 2)))}


# ---------------------------------------------------------------------------
# FPGA artefacts
# ---------------------------------------------------------------------------

def export_fpga(out: Path) -> None:
    fpga = out / "fpga"
    for command, build, name, files in (
            (["scripts/cosimulate_detector.py", "--rounds", "5", "--bits", "14",
              "--vectors", "64", "--sync-memory"], "runs/hardware/rtl_tree",
             "detector_5rodadas_14b", ("nodes.hex", "trees.hex", "baseline.hex",
                                       "tree_params.vh")),
            (["scripts/cosimulate_sync.py", "--bits", "12", "--vectors", "64"],
             "runs/hardware/rtl", "sincronizador_12b",
             ("coefficients.hex", "intercept.hex", "sync_params.vh"))):
        result = subprocess.run([sys.executable, *command], cwd=REPO_ROOT,
                                capture_output=True, text=True)
        if result.returncode != 0 or "0 escores divergentes" not in result.stdout \
                and "0 divergências" not in result.stdout:
            print(result.stdout[-1500:])
            raise SystemExit(f"[export] a cossimulação de {name} não passou")
        target = fpga / name
        target.mkdir(parents=True, exist_ok=True)
        for file in files:
            shutil.copy(REPO_ROOT / build / file, target / file)
    rtl = fpga / "rtl"
    rtl.mkdir(parents=True, exist_ok=True)
    for source in sorted((REPO_ROOT / "rtl").glob("*.v")):
        shutil.copy(source, rtl / source.name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("modelo_qkd_ml"))
    parser.add_argument("--detector", type=Path, default=Path("runs/detector"))
    parser.add_argument("--policy", type=Path,
                        default=Path("runs/keyrate/generalist2_policy.npz"))
    parser.add_argument("--temperature", type=float, default=0.25,
                        help="the sampling temperature selected on validation seeds")
    parser.add_argument("--no-fpga", action="store_true")
    args = parser.parse_args()

    out = args.out.resolve()
    models, examples = out / "modelos", out / "exemplos"
    for directory in (models, examples):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)

    source = REPO_ROOT / "exportacao"
    for name in ("inferencia.py", "exemplo.py", "LEIAME.md", "requirements.txt"):
        shutil.copy(source / name, out / name)
    standalone = _load_standalone(out / "inferencia.py")

    print("[export] detector")
    detector = export_detector(models, examples, REPO_ROOT / args.detector, standalone)
    for row in detector["summary"]:
        print(f"  {row['classe_verdadeira']:18s} alarme em {row['primeiro_alarme_s']}, "
              f"início {row['início_do_evento_s']}, chamado de "
              f"{row['classe_mais_alarmada_após_início']}")
    print("[export] agente de taxa de chave")
    agent = export_agent(models, examples, REPO_ROOT / args.policy, args.temperature,
                         standalone)
    print(f"  {agent['periods']} períodos conferidos")
    print("[export] sincronizador")
    sync = export_sync(models, examples, standalone)
    print(f"  {sync['samples']} predições; erro RMS {sync['rms_predicted_ps']:.1f} ps "
          f"contra {sync['rms_hold_ps']:.1f} ps repetindo a última medida")
    if not args.no_fpga:
        print("[export] artefatos de FPGA")
        export_fpga(models)

    print(f"\n[export] pacote em {out} — todos os exemplos conferem com qnet_qkd")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
