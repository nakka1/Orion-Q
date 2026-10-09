"""QKD actor export parity; does not regenerate stochastic outcomes or keys."""
from dataclasses import asdict

import numpy as np

from .artifacts import QKD_ROOT, ReviewedArchive
from .keyrate import ACTIONS, KeyRateReplayModel, LinkCalibration, PeriodObservables, TransmitterSettings

FEATURE_ATOL = 1e-8  # Exported observations have nine decimal places.
PROBABILITY_ATOL = 1e-6  # Exported probabilities have six decimal places.
SETTINGS_ATOL = SETTINGS_RTOL = 1e-12


def evaluate_keyrate(archive: ReviewedArchive) -> dict:
    model = KeyRateReplayModel(archive)
    episode = archive.json(QKD_ROOT + "exemplos/agente_episodio_50km.json")
    calibration = LinkCalibration(**episode["calibração_do_enlace"])
    records = episode["periodos"]
    if (len(records) != 40 or [r["periodo"] for r in records] != list(range(40))
            or episode["temperatura"] != 0.25):
        raise ValueError("unexpected key-rate reference episode")
    feature_error = probability_error = 0.0
    settings_errors = {field: 0. for field in TransmitterSettings.__dataclass_fields__}
    greedy_mismatches = settings_mismatches = 0
    rng = np.random.default_rng(0)
    for row in records:
        inputs, expected = row["entrada"], row["saida"]
        previous = inputs["observaveis_do_periodo_anterior"]
        previous = None if previous is None else PeriodObservables(**previous)
        settings = TransmitterSettings(**inputs["parametros_atuais"])
        result = model.recommend(previous, settings, calibration, domain="simulated_offline",
                                 link_span_km=episode["vão_km"], protocol="decoy_state_bb84", rng=rng)
        reference_observation = np.asarray(row["observacao_16"], dtype=float)
        reference_probabilities = np.asarray([expected["probabilidades"][name] for name in ACTIONS])
        if reference_observation.shape != (16,) or not np.isfinite(reference_observation).all():
            raise ValueError("invalid reference features")
        feature_error = max(feature_error, float(np.max(np.abs(result.observation - reference_observation))))
        probability_error = max(probability_error, float(np.max(np.abs(result.probabilities - reference_probabilities))))
        greedy_mismatches += ACTIONS[result.greedy_index] != expected["acao_gulosa"]
        # The export records the sampled action, but does not declare a policy
        # RNG state/seed. Check its bounded consequence, NOT sampling parity.
        recorded_index = ACTIONS.index(expected["acao_amostrada_T=0.25"])
        proposed = asdict(model.preview_action(settings, recorded_index, domain="simulated_offline"))
        reference = asdict(TransmitterSettings(**expected["novos_parametros"]))
        for name in settings_errors:
            error = abs(proposed[name] - reference[name])
            settings_errors[name] = max(settings_errors[name], error)
            settings_mismatches += not np.isclose(proposed[name], reference[name],
                                                  rtol=SETTINGS_RTOL, atol=SETTINGS_ATOL)
    return {
        "model_id": "qkd.keyrate.actor", "periods": len(records), "link_span_km": episode["vão_km"],
        "temperature": 0.25, "max_feature_absolute_error": feature_error,
        "feature_absolute_tolerance": FEATURE_ATOL, "max_probability_absolute_error": probability_error,
        "probability_absolute_tolerance": PROBABILITY_ATOL,
        "greedy_mismatches": int(greedy_mismatches), "settings_field_mismatches": int(settings_mismatches),
        "max_settings_absolute_errors": settings_errors,
        "settings_absolute_tolerance": SETTINGS_ATOL, "settings_relative_tolerance": SETTINGS_RTOL,
        "sampled_action_parity_checked": False,
        "sampling_note": "episode seed is not a declared policy RNG state; recorded actions used only for box parity",
        "passed": bool(feature_error <= FEATURE_ATOL and probability_error <= PROBABILITY_ATOL
                       and greedy_mismatches == 0 and settings_mismatches == 0),
        "scope": "one supplied 50 km simulated episode; no regenerated key bits, new outcomes or operational gain",
    }
