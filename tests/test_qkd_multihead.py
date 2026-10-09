import numpy as np
import pandas as pd
import torch

from quantum_twin.evaluation.qkd_multihead import (
    QKDMultiHeadConfig,
    run_qkd_multihead_campaign,
)
from quantum_twin.ml.qkd_multihead import (
    QKDDualHead,
    QKDMultiHeadLoss,
    QKDTriHead,
    build_qkd_dominance_pairs,
    predict_qkd_multihead,
    train_qkd_multihead,
)


def test_dominance_pairs_are_positive_key_and_within_stratum_only():
    pairs = build_qkd_dominance_pairs(
        available=np.asarray([1, 1, 1, 0, 1]),
        log_skr_scaled=np.asarray([3.0, 2.0, 1.0, 4.0, 0.0]),
        qber_scaled=np.asarray([0.1, 0.2, 0.3, 0.01, 0.9]),
        strata=np.asarray(["a", "a", "b", "a", "b"]),
    )
    np.testing.assert_array_equal(
        pairs,
        np.asarray([[0, 1, 1, 1], [2, 4, 1, 1]], dtype=np.int64),
    )


def test_pareto_loss_penalizes_a_joint_dominance_inversion():
    criterion = QKDMultiHeadLoss(dominance_weight=0.25, dominance_margin=0.1)
    common = {
        "available_true": torch.ones((2, 1)),
        "log_skr_true": torch.tensor([[1.0], [0.0]]),
        "qber_true": torch.tensor([[0.0], [1.0]]),
        "sample_weight": torch.ones((2, 1)),
        "dominance_pairs": torch.tensor([[0, 1, 1, 1]]),
    }
    _, ordered = criterion(
        (torch.full((2, 1), 0.5), torch.tensor([[1.0], [0.0]]), torch.tensor([[0.0], [1.0]])),
        **common,
    )
    _, reversed_order = criterion(
        (torch.full((2, 1), 0.5), torch.tensor([[0.0], [1.0]]), torch.tensor([[1.0], [0.0]])),
        **common,
    )
    assert ordered["pareto_dominance_loss"] == 0.0
    assert reversed_order["pareto_dominance_loss"] > 0.0


def _write_qkd_sweeps(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    rows = []
    for distance in (0.0, 50.0):
        for voa in (2.0, 8.0):
            for power in (None, 3.0, 7.0, 9.0, 12.0):
                condition = 0.0 if power is None else power / 12.0
                for repeat in range(2):
                    qber = 0.02 + distance * 2e-4 + voa * 1e-3 + 0.03 * condition
                    skr = 180_000.0 - distance * 1_000.0 - voa * 5_000.0
                    skr = max(skr * (1.0 - condition) - repeat * 5.0, 0.0)
                    rows.append({
                        "Noise_dBm": "None" if power is None else power,
                        "Sweep_km": distance,
                        "VOA_dB": voa,
                        "QBER": qber,
                        "SecureKeyRate_bps": skr,
                    })
    pd.DataFrame(rows).to_csv(tmp_path / "qkd.csv", index=False)
    return tmp_path


def test_dual_and_trihead_shapes_and_masked_training_are_deterministic():
    rng = np.random.default_rng(4)
    X = rng.normal(size=(12, 5))
    available = np.asarray([0, 1] * 6, dtype=float)
    log_skr = rng.normal(size=12)
    qber = rng.normal(size=12)

    first = QKDTriHead(5, hidden_size=4)
    second = QKDTriHead(5, hidden_size=4)
    first, summary = train_qkd_multihead(
        first, X, available, log_skr, qber_scaled=qber, seed=9, epochs=3
    )
    second, _ = train_qkd_multihead(
        second, X, available, log_skr, qber_scaled=qber, seed=9, epochs=3
    )
    first_outputs = predict_qkd_multihead(first, X)
    second_outputs = predict_qkd_multihead(second, X)
    assert len(first_outputs) == 3
    assert all(values.shape == (12,) for values in first_outputs)
    for a, b in zip(first_outputs, second_outputs):
        np.testing.assert_allclose(a, b)
    assert np.isfinite(summary.final_loss)
    assert len(predict_qkd_multihead(QKDDualHead(5, 4), X)) == 2


def test_qkd_multihead_campaign_holds_complete_strata_out(tmp_path):
    result = run_qkd_multihead_campaign(
        _write_qkd_sweeps(tmp_path / "dataset"),
        config=QKDMultiHeadConfig(
            seeds=(7,), hidden_size=4, epochs=2, bootstrap_replicates=20
        ),
    )
    assert set(result.predictions["model"]) == {
        "independent_linear", "independent_mlp", "dualhead_qkd", "trihead_qkd",
        "trihead_permuted_condition", "trihead_pareto_qkd",
        "trihead_pareto_permuted_condition",
    }
    assert "qber_reported" not in set(
        result.predictions.loc[
            result.predictions["model"].eq("dualhead_qkd"), "target"
        ]
    )
    assert result.report["validation"].startswith("leave-one")
    assert result.report["not_claimed"][0].startswith("The curated sweeps")
    assert set(result.comparisons["multiple_comparison_correction"]) == {"Bonferroni"}
    assert set(result.comparisons["hypothesis_family_size"]) == {21}
    pareto_rows = result.predictions["model"].str.startswith("trihead_pareto")
    assert result.predictions.loc[pareto_rows, "training_dominance_pairs"].gt(0).all()
