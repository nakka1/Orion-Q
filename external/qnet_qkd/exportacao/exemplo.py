#!/usr/bin/env python3
"""Run the three models on the bundled examples and compare with the saved outputs.

    python exemplo.py

Needs only ``requirements.txt``.  Each block loads a model, feeds it an input
from ``exemplos/``, prints what came out, and checks it against the output file
that was written when the bundle was exported — so a run on a new machine is
also a check that the models loaded correctly.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from inferencia import (ACTIONS, AttackDetector, KeyRateAgent, PeriodObservables,
                        SyncPredictor, TransmitterSettings, keyrate_observation)

HERE = Path(__file__).resolve().parent
MODELS, EXAMPLES = HERE / "modelos", HERE / "exemplos"


def detector() -> None:
    print("=" * 72)
    print("1. DETECTOR DE ATAQUES — um registro de sessão por classe")
    print("=" * 72)
    model = AttackDetector.load(MODELS)
    print(f"   limiar calibrado P(ataque) >= {model.attack_threshold:.4f}, "
          f"histerese de {model.sustain} períodos\n")
    for entrada in sorted(EXAMPLES.glob("detector_*_entrada.csv")):
        stem = entrada.name[: -len("_entrada.csv")]
        log = pd.read_csv(entrada)
        truth = pd.read_csv(EXAMPLES / f"{stem}_gabarito.csv")
        result = model.classify_session(log)

        saved = pd.read_csv(EXAMPLES / f"{stem}_saida.csv")
        assert (result["alarm"].to_numpy() == saved["alarm"].to_numpy()).all(), stem
        assert np.allclose(result["attack_probability"], saved["attack_probability"],
                           rtol=1e-5, atol=1e-9), stem

        onset = truth["onset_s"].dropna()
        onset = float(onset.iloc[0]) if len(onset) else None
        alarms = result[result["alarm"] != "normal"]
        first = float(alarms["time_s"].min()) if len(alarms) else None
        after = result[result["time_s"] >= onset] if onset is not None else result
        called = after["alarm"][after["alarm"] != "normal"]
        verdict = called.value_counts().index[0] if len(called) else "normal"
        print(f"   {truth['label'].iloc[0]:18s} início "
              f"{'—' if onset is None else f'{onset:5.0f} s'}  "
              f"primeiro alarme {'—' if first is None else f'{first:5.0f} s'}  "
              f"-> {verdict}")


def agent() -> None:
    print("\n" + "=" * 72)
    print("2. AGENTE DE TAXA DE CHAVE — primeiros períodos de um episódio a 50 km")
    print("=" * 72)
    model = KeyRateAgent.load(MODELS)
    episode = json.loads((EXAMPLES / "agente_episodio_50km.json").read_text())
    calibration = episode["calibração_do_enlace"]
    for record in episode["periodos"][:6]:
        entrada = record["entrada"]
        previous = entrada["observaveis_do_periodo_anterior"]
        observables = None if previous is None else PeriodObservables(**previous)
        settings = TransmitterSettings(**entrada["parametros_atuais"])
        # As trained: the "previous rate" is the rate of the period just
        # observed, so feature 13 always equals feature 7 (see LEIAME.md).
        previous_rate = (calibration["calibration_rate_hz"] if observables is None
                         else observables.key_rate_hz)
        observation = keyrate_observation(
            observables, settings,
            calibration_gain=calibration["calibration_gain"],
            calibration_rate_hz=calibration["calibration_rate_hz"],
            dark_count_probability=calibration["dark_count_probability"],
            misalignment_error=calibration["misalignment_error"],
            previous_rate_hz=previous_rate, dead_time_s=calibration["dead_time_s"])
        assert np.allclose(observation, record["observacao_16"], atol=1e-8)

        probabilities = model.action_probabilities(observation)
        best = int(np.argmax(probabilities))
        saved = record["saida"]["probabilidades"]
        assert np.allclose(probabilities, [saved[a.name] for a in ACTIONS], atol=1e-6)
        # The episode advanced with the action *sampled* at T = 0.25, as in the
        # article; the greedy choice is shown beside it.
        taken_name = next(v for k, v in record["saida"].items() if k.startswith("acao_amostrada"))
        taken = [a.name for a in ACTIONS].index(taken_name)
        after = model.apply(settings, taken)
        print(f"   período {record['periodo']:2d}: mu={settings.signal_intensity:.3f} "
              f"nu={settings.decoy_intensity:.3f} p_sinal={settings.signal_probability:.2f} "
              f"| gulosa {ACTIONS[best].name:13s} (p={probabilities[best]:.2f}) "
              f"| amostrada {taken_name:13s} -> mu={after.signal_intensity:.3f} "
              f"nu={after.decoy_intensity:.3f} p_sinal={after.signal_probability:.2f}")


def sync() -> None:
    print("\n" + "=" * 72)
    print("3. SINCRONIZADOR — onde o pulso estará daqui a 2 s")
    print("=" * 72)
    model = SyncPredictor.load(MODELS)
    entrada = pd.read_csv(EXAMPLES / "sincronizador_entrada.csv")
    saved = pd.read_csv(EXAMPLES / "sincronizador_saida.csv")
    offsets, temps = entrada["offset_medido_s"].to_numpy(), entrada["temperatura_c"].to_numpy()
    predictions = [model.predict(offsets[i - model.window + 1: i + 1],
                                 temps[i - model.window + 1: i + 1])
                   for i in range(model.window - 1, model.window - 1 + len(saved))]
    assert np.allclose(predictions, saved["offset_previsto_em_t+2s_s"], rtol=0, atol=1e-15)
    for row in saved.iloc[[0, 100, 300, 500]].itertuples():
        print(f"   t={row.time_s:5.0f} s  medido {row.offset_medido_s * 1e9:8.3f} ns  "
              f"previsto(t+2) {getattr(row, '_4') * 1e9:8.3f} ns  "
              f"verdadeiro(t+2) {getattr(row, '_5') * 1e9:8.3f} ns")
    rms = np.sqrt(np.mean(saved["erro_previsto_ps"] ** 2))
    hold = np.sqrt(np.mean(saved["erro_ultima_medida_ps"] ** 2))
    print(f"   erro RMS: {rms:.2f} ps com o modelo, {hold:.2f} ps repetindo a última medida")
    print("   (o modelo não supera a última medida neste regime — ver LEIAME.md)")


if __name__ == "__main__":
    detector()
    agent()
    sync()
    print("\nTodas as saídas conferem com as gravadas em exemplos/.")
