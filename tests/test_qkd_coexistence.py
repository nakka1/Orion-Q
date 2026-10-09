import json

import numpy as np
import pandas as pd

from quantum_twin.evaluation.qkd_coexistence import (
    QKDCoexistenceConfig,
    aggregate_experimental_cells,
    compute_controlled_contrasts,
    run_qkd_coexistence_campaign,
    write_qkd_coexistence_artifacts,
)
from quantum_twin.quantum.qkd_coexistence import QKDCoexistenceSource


def _write_qkd_sweeps(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    rows = []
    for distance in (0.0, 50.0):
        for voa in (2.0, 5.0, 8.0):
            for power in (None, 3.0, 7.0, 9.0, 12.0):
                injected = 0.0 if power is None else (power / 12.0) ** 2
                for repeat in range(3):
                    qber = 0.02 + distance * 0.00015 + voa * 0.0008
                    qber += injected * (0.012 + distance * 0.0003) + repeat * 1e-5
                    key_rate = 250_000.0 - 1_500.0 * voa - 800.0 * distance
                    key_rate *= 1.0 - 0.55 * injected
                    rows.append({
                        "Noise_dBm": "None" if power is None else power,
                        "Sweep_km": distance,
                        "VOA_dB": voa,
                        "QBER": qber,
                        "SecureKeyRate_bps": max(key_rate - repeat * 10.0, 0.0),
                    })
    path = tmp_path / "controlled_qkd_sweep.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return tmp_path


def test_qkd_adapter_preserves_measured_targets_and_explicit_limits(tmp_path):
    source = QKDCoexistenceSource(_write_qkd_sweeps(tmp_path))
    frame = source.read()

    assert len(frame) == 90
    assert source.validate(frame).is_valid
    assert "qkd_agreement_probability" not in frame
    assert frame["qber_reported"].between(0.0, 100.0).all()
    provenance = frame.attrs["telemetry_provenance"]
    assert provenance["source_type"] == "recorded_hardware"
    assert provenance["quantum_target_present"] is True
    assert provenance["bell_fidelity_present"] is False
    assert provenance["timestamp_present"] is False
    assert provenance["qber_unit_status"].startswith("ambiguous")


def test_qkd_analysis_uses_cells_as_units_and_exact_matched_contrasts(tmp_path):
    frame = QKDCoexistenceSource(_write_qkd_sweeps(tmp_path)).read()
    cells = aggregate_experimental_cells(frame)
    assert len(cells) == 30
    assert set(cells["n_measurements"]) == {3}

    config = QKDCoexistenceConfig(bootstrap_replicates=200, seed=7)
    first_primary, first_dose = compute_controlled_contrasts(cells, config=config)
    second_primary, second_dose = compute_controlled_contrasts(cells, config=config)
    pd.testing.assert_frame_equal(first_primary, second_primary)
    pd.testing.assert_frame_equal(first_dose, second_dose)
    assert (first_primary["matched_strata"] == 6).all()
    assert (first_primary["mean_degradation"] > 0.0).all()


def test_small_measured_qkd_campaign_writes_auditable_artifacts(tmp_path):
    dataset = _write_qkd_sweeps(tmp_path / "dataset")
    result = run_qkd_coexistence_campaign(
        dataset,
        config=QKDCoexistenceConfig(bootstrap_replicates=100, seed=11),
    )
    assert result.report["status"].startswith("exploratory")
    assert "Bell-pair fidelity was not measured." in result.report["not_claimed"]
    assert set(result.predictive_comparisons["target"]) == {
        "qber_reported", "log1p_secure_key_rate",
    }

    output = tmp_path / "outputs"
    manifest = write_qkd_coexistence_artifacts(result, output, make_plots=False)
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["dataset"]["provenance"]["quantum_target_kind"] == (
        "measured_qkd_qber_and_secure_key_rate"
    )
    assert (output / "primary_matched_contrasts.csv").exists()
    assert len(manifest["artifacts"]) == 6
