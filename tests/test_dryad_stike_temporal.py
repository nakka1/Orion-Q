import json

import numpy as np

from quantum_twin.evaluation.dryad_stike_temporal import (
    DryadSTIKETemporalConfig,
    run_dryad_stike_temporal_campaign,
    write_dryad_stike_temporal_artifacts,
)


def _write_release(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    time = np.arange(168)
    qber = 3.6 + 0.25 * np.sin(time / 8.0) + 0.03 * np.sin(time / 2.0)
    rate = 2.38 - 0.12 * np.sin(time / 8.0) + 0.02 * np.cos(time / 3.0)
    (tmp_path / "QBER_168h.txt").write_text(
        "\n".join(map(str, qber)) + "\n", encoding="utf-8"
    )
    (tmp_path / "Rate_168h.txt").write_text(
        "\n".join(map(str, rate)) + "\n", encoding="utf-8"
    )
    return tmp_path


def test_temporal_campaign_uses_purged_training_only_design(tmp_path):
    result = run_dryad_stike_temporal_campaign(
        _write_release(tmp_path),
        config=DryadSTIKETemporalConfig(
            lookback_hours=12,
            horizons_hours=(1, 6),
            folds=2,
            test_hours=12,
            minimum_train_samples=48,
            seed=7,
        ),
        verify_publisher_hashes=False,
    )

    assert result.splits["purge_valid"].all()
    assert (
        result.splits["last_train_target_index"]
        < result.splits["first_test_origin_index"]
    ).all()
    assert (result.predictions["target_sample_index"] > result.predictions[
        "origin_sample_index"
    ]).all()
    assert set(result.predictions["model"]) == {
        "persistence",
        "historical_mean",
        "own_history_ridge",
        "bivariate_history_ridge",
        "bivariate_permuted_cross_ridge",
    }
    assert result.report["central_wdm_thesis_tested"] is False
    assert not result.comparisons["supports_wdm_claim"].any()


def test_temporal_campaign_writes_auditable_artifacts(tmp_path):
    result = run_dryad_stike_temporal_campaign(
        _write_release(tmp_path / "dataset"),
        config=DryadSTIKETemporalConfig(
            lookback_hours=12, horizons_hours=(1,), folds=2,
            test_hours=12, minimum_train_samples=48,
        ),
        verify_publisher_hashes=False,
    )
    output = tmp_path / "outputs"
    manifest = write_dryad_stike_temporal_artifacts(result, output)

    assert len(manifest["artifacts"]) == 7
    assert (output / "model_comparisons.csv").exists()
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["validation"]["purged_splits_all_valid"] is True
    assert "not a conventional QKD" in report["not_claimed"][0]
