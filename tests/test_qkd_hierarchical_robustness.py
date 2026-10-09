import numpy as np
import pandas as pd

from quantum_twin.evaluation.qkd_hierarchical_robustness import (
    QKDHierarchicalRobustnessConfig,
    audit_campaign_design,
    compute_stratum_effects,
    prepare_raw_readings,
    run_qkd_hierarchical_robustness_campaign,
    summarize_raw_cells,
    write_qkd_hierarchical_robustness_artifacts,
)
from quantum_twin.quantum.qkd_coexistence import QKDCoexistenceSource


def _write_session_aliased_sweeps(directory):
    directory.mkdir(parents=True)
    for distance in (0.0, 50.0):
        for power in (None, 3.0, 7.0, 9.0, 12.0):
            rows = []
            for voa in (2.0, 5.0, 8.0):
                dose = 0.0 if power is None else power / 12.0
                for repeat in range(12):
                    rows.append({
                        "Noise_dBm": "None" if power is None else power,
                        "Sweep_km": distance,
                        "VOA_dB": voa,
                        "QBER": (
                            0.02 + 0.0001 * distance + 0.0005 * voa
                            + 0.02 * dose + repeat * 1e-6
                        ),
                        "SecureKeyRate_bps": max(
                            250_000.0 - 500.0 * distance - 2_000.0 * voa
                            - 80_000.0 * dose - repeat,
                            1.0,
                        ),
                    })
            condition = "baseline" if power is None else f"{power:g}dbm"
            pd.DataFrame(rows).to_csv(
                directory / f"{distance:g}km_{condition}.csv", index=False
            )
    return directory


def test_design_audit_detects_condition_session_aliasing(tmp_path):
    dataset = _write_session_aliased_sweeps(tmp_path / "dataset")
    raw = QKDCoexistenceSource(dataset).read()
    data = prepare_raw_readings(raw)
    design = audit_campaign_design(data).iloc[0]
    cells = summarize_raw_cells(data)
    effects = compute_stratum_effects(cells)

    assert design["raw_readings"] == 360
    assert design["campaigns"] == 10
    assert bool(design["condition_session_aliased"])
    assert not bool(design["session_effect_separable"])
    assert not bool(design["campaign_clustered_confirmatory_inference_available"])
    assert cells["campaign_count"].eq(1).all()
    primary = effects.loc[
        effects["contrast"].eq("average_injected_signal_vs_baseline")
    ]
    assert primary["degradation"].gt(0.0).all()


def test_hierarchical_campaign_is_deterministic_and_positive(tmp_path):
    dataset = _write_session_aliased_sweeps(tmp_path / "dataset")
    config = QKDHierarchicalRobustnessConfig(
        bootstrap_replicates=200, block_lengths=(1, 3), seed=7
    )
    first = run_qkd_hierarchical_robustness_campaign(dataset, config=config)
    second = run_qkd_hierarchical_robustness_campaign(dataset, config=config)
    pd.testing.assert_frame_equal(first.primary_intervals, second.primary_intervals)
    assert first.primary_intervals["positive_conditional_interval"].all()
    assert first.report["primary_result"]["conditional_measurement_robustness"]
    assert first.report["primary_result"]["campaign_clustered_confirmation"] is False
    assert set(first.primary_intervals["block_length"]) == {1, 3}


def test_hierarchical_campaign_writes_auditable_artifacts(tmp_path):
    dataset = _write_session_aliased_sweeps(tmp_path / "dataset")
    result = run_qkd_hierarchical_robustness_campaign(
        dataset,
        config=QKDHierarchicalRobustnessConfig(
            bootstrap_replicates=100, block_lengths=(1, 3), seed=11
        ),
    )
    output = tmp_path / "output"
    manifest = write_qkd_hierarchical_robustness_artifacts(result, output)
    assert len(manifest["artifacts"]) == 8
    assert (output / "design_audit.csv").exists()
    assert (output / "hierarchical_primary_intervals.csv").exists()
    assert (output / "input_integrity.json").exists()
    assert not result.input_integrity["synthetic_values_substituted"]
