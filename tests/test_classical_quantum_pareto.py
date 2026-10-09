import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantum_twin.evaluation.classical_quantum_pareto import (
    ClassicalQuantumParetoConfig,
    pareto_front_mask,
    run_classical_quantum_pareto_campaign,
    write_classical_quantum_pareto_artifacts,
)
from quantum_twin.evaluation.optical_campaign import sha256_file


AGGREGATE = Path("data/reference/gavignet_2024_published_aggregate.csv")


def _write_multihead_fixture(directory: Path) -> None:
    directory.mkdir(parents=True)
    cell_records = []
    prediction_records = []
    conditions = [
        ("baseline", np.nan, 0.03, 100.0),
        ("3", 3.0, 0.04, 90.0),
        ("12", 12.0, 0.06, 50.0),
    ]
    for stratum_index, stratum in enumerate(("s1", "s2"), start=1):
        for condition_index, (noise, power, qber, skr) in enumerate(conditions):
            cell_records.append({
                "stratum_id": stratum,
                "noise_level": noise,
                "external_signal_power_dbm": power,
                "qber_reported": qber,
                "secure_key_rate_bps": skr,
                "log1p_secure_key_rate": np.log1p(skr),
            })
            for model in (
                "trihead_qkd", "trihead_permuted_condition",
                "trihead_pareto_qkd", "trihead_pareto_permuted_condition",
            ):
                if model in ("trihead_qkd", "trihead_pareto_qkd"):
                    predicted_qber = qber
                    predicted_skr = np.log1p(skr)
                else:
                    reverse = conditions[2 - condition_index]
                    predicted_qber = reverse[2]
                    predicted_skr = np.log1p(reverse[3])
                common = {
                    "model": model,
                    "seed": 7,
                    "stratum_id": stratum,
                    "fiber_length_km": 50.0,
                    "voa_attenuation_db": 10.0 + stratum_index,
                    "noise_level": noise,
                }
                prediction_records.extend([
                    {
                        **common,
                        "target": "qber_reported",
                        "y_true": qber,
                        "prediction": predicted_qber,
                    },
                    {
                        **common,
                        "target": "log1p_secure_key_rate_given_available",
                        "y_true": np.log1p(skr),
                        "prediction": predicted_skr,
                    },
                ])
    cells_path = directory / "experimental_cells.csv"
    predictions_path = directory / "out_of_stratum_predictions.csv"
    pd.DataFrame(cell_records).to_csv(cells_path, index=False)
    pd.DataFrame(prediction_records).to_csv(predictions_path, index=False)
    manifest = {
        "artifacts": [
            {"file": path.name, "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in (cells_path, predictions_path)
        ]
    }
    (directory / "artifact_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def test_pareto_front_preserves_antagonistic_tradeoffs():
    tradeoff = pd.DataFrame({
        "classical": [0.0, 0.5, 1.0],
        "quantum": [1.0, 0.5, 0.0],
    })
    assert pareto_front_mask(tradeoff, maximize=("classical", "quantum")).all()

    dominated = pd.DataFrame({"qber": [0.03, 0.04], "skr": [100.0, 90.0]})
    assert pareto_front_mask(
        dominated, minimize=("qber",), maximize=("skr",)
    ).tolist() == [True, False]


def test_campaign_separates_internal_recovery_from_external_aggregate(tmp_path):
    multihead = tmp_path / "multihead"
    _write_multihead_fixture(multihead)
    result = run_classical_quantum_pareto_campaign(
        multihead,
        AGGREGATE,
        config=ClassicalQuantumParetoConfig(
            bootstrap_replicates=500,
            bootstrap_seed=9,
        ),
    )

    aligned = result.pareto_recovery_summary.set_index("model")
    assert aligned.loc["trihead_qkd", "mean_pareto_jaccard"] == 1.0
    assert aligned.loc["trihead_permuted_condition", "mean_pareto_jaccard"] == 0.0
    alignment = result.joint_model_comparisons.loc[
        result.joint_model_comparisons["contrast"].str.contains("aligned_")
    ]
    assert alignment["positive_beyond_interval"].all()
    assert set(result.joint_model_comparisons["hypothesis_family_size"]) == {6}
    assert set(result.joint_model_comparisons["multiple_comparison_correction"]) == {
        "Bonferroni"
    }
    assert result.external_pareto_points["four_objective_pareto"].all()
    assert not result.external_pareto_points["external_used_for_training"].any()
    assert result.report["gavignet_external"]["external_used_for_training"] is False


def test_campaign_rejects_changed_published_aggregate(tmp_path):
    multihead = tmp_path / "multihead"
    _write_multihead_fixture(multihead)
    aggregate = tmp_path / "aggregate.csv"
    aggregate.write_bytes(AGGREGATE.read_bytes() + b"\n")
    try:
        run_classical_quantum_pareto_campaign(multihead, aggregate)
    except ValueError as error:
        assert "aggregate hash mismatch" in str(error)
    else:
        raise AssertionError("expected frozen external-aggregate integrity failure")


def test_pareto_writer_emits_nine_checksummed_artifacts(tmp_path):
    multihead = tmp_path / "multihead"
    _write_multihead_fixture(multihead)
    result = run_classical_quantum_pareto_campaign(
        multihead,
        AGGREGATE,
        config=ClassicalQuantumParetoConfig(bootstrap_replicates=100),
    )
    output = tmp_path / "output"
    manifest = write_classical_quantum_pareto_artifacts(result, output)
    assert len(manifest["artifacts"]) == 9
    assert (output / "joint_model_comparisons.csv").exists()
    assert (output / "input_integrity.json").exists()
