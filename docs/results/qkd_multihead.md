# Exploratory QKD DualHead/TriHead/Pareto-aware transfer

## Question

Does the multihead factorization that helped in simulation transfer to the
measured IrelandQCI/Toshiba coexistence outcomes?

This is not a temporal forecast. The release has no timestamps, so the
experiment aggregates 3,893 readings into 80 controlled cells and uses a
small shared MLP instead of fabricating an LSTM sequence.

## Protocol

```bash
python experiments/run_qkd_multihead_campaign.py \
  --dataset-dir /path/to/zenodo-21132088 \
  --output-dir outputs/qkd_multihead_measured
```

- 16 fibre-length/VOA strata; all five signal conditions in one stratum are
  held out together.
- 80 cells, including 12 cells with zero secure key rate.
- DualHead: `P(SKR > 0)` plus conditional `log1p(SKR)`.
- TriHead: the DualHead targets plus measured publisher-reported QBER.
- Pareto-aware TriHead: the same architecture plus a fixed loss term
  (`weight=0.25`, scaled margin `0.05`) for measured QBER/SKR dominance pairs.
  Pairs are constructed only among positive-key cells in the training strata;
  no held-out target is used to build them.
- Controls: logistic/Ridge heads, three capacity-matched MLPs fitted
  independently, and a TriHead with coexistence conditions permuted within
  each stratum. The Pareto-aware model has its own identically trained
  permuted-condition control.
- Seeds 13, 42 and 97; repeated-seed losses are averaged per physical cell.
- Stratum bootstrap with Bonferroni correction over all 21 reported
  target/model contrasts (`alpha/21`).
- Exploratory status: the dataset was inspected before this protocol was
  defined.

## Result

Three of 21 marginal model contrasts were corrected-positive. Neither the
conventional nor Pareto-aware TriHead passed an aligned-condition contrast on
the three marginal losses; the joint ordering result is reported separately
below.

| Target and contrast | Reference loss | Candidate loss | Improvement | Corrected CI | Result |
|---|---:|---:|---:|---:|---|
| QBER: TriHead vs linear independent | 0.032176 | 0.014162 | +0.018014 | [0.007478, 0.029980] | positive |
| SKR availability: DualHead vs independent MLP | 0.169749 | 0.142497 | +0.027252 | [0.000268, 0.082358] | positive |
| SKR availability: TriHead vs independent MLP | 0.169749 | 0.138222 | +0.031527 | [0.000279, 0.090764] | positive |
| Conditional log-SKR: Pareto vs conventional TriHead | 0.575112 | 0.561215 | +0.013896 | [-0.021290, 0.054572] | not confirmed |
| QBER: Pareto vs conventional TriHead | 0.014162 | 0.014571 | -0.000409 | [-0.001026, 0.000207] | not confirmed |
| Conditional log-SKR: aligned Pareto vs permuted Pareto | 0.594908 | 0.561215 | +0.033693 | [-0.028472, 0.098273] | not confirmed |

Loss is Brier score for availability and absolute error for QBER/log-SKR.
QBER magnitudes remain on the publisher's ambiguous `ratio (or %)` scalar and
must not be relabelled as percentage points.

## Interpretation

The measured cells provide exploratory evidence that sharing representations
can help the key-availability task compared with separately fitted MLP heads,
and that the nonlinear TriHead predicts QBER better than a linear baseline.
The marginal losses alone do not show that correctly aligned coexistence
conditions outperform the permuted-condition control. The joint audit does,
however, find one narrower result: Pareto-aware aligned inputs improve
dominance-pair accuracy over the Pareto-aware permuted control by `+0.1750`,
with a six-comparison Bonferroni bootstrap interval `[0.0168, 0.3111]`.
That is exploratory evidence that coexistence condition alignment helps order
measured QBER/SKR outcomes. It is not evidence that WDM *history* predicts a
future quantum target because these cells have no timestamps.

The controlled matched-contrast analysis remains a different and stronger
finding for the physical coexistence effect: injected classical signal
significantly degraded measured QBER and SKR. The missing next step is still a
timestamped paired dataset evaluated with the strict temporal campaign.

A subsequent joint-objective audit asks whether the same out-of-stratum
predictions recover the measured QBER/SKR Pareto set. The Pareto-aware loss
raises dominance accuracy from `0.4283` to `0.5625`, but Pareto Jaccard remains
low (`0.2298`) and improvement over the conventional TriHead crosses the
corrected interval. Full-front recovery therefore remains unvalidated. See
[`classical_quantum_pareto.md`](classical_quantum_pareto.md).

The post-discovery falsification audit later confirms above-chance dominance
alignment under 20,000 joint relabelings, but rejects a stronger transport
interpretation: neither TriHead beats a within-stratum target-mean baseline
when an entire coexistence condition is unseen. See
[`qkd_pareto_falsification.md`](qkd_pareto_falsification.md).

## Reproducibility

The run used protocol configuration
`experiments/configs/qkd_multihead_default.json`. Generated artifacts include
cell data, every out-of-stratum prediction, seed/model metrics, all corrected
comparisons, Git state and SHA-256 checksums. The reference artifact hashes
were:

- `experimental_cells.csv`: `1cb01018289b78eb0de2b6f5d00114fa954c75c942ca65a7fabfee614c154ed8`
- `model_metrics.csv`: `725a5b9c182c439cef63ef4c564e38012749a36665270ef247b0c54d333110f8`
- `corrected_comparisons.csv`: `1bfb8d64e4f61c8cf7e72e1103d55b955c5c61ff8b87f29ead253113368bb21a`
- `out_of_stratum_predictions.csv`: `4d1c2eb33ec0ce3325e3cea2ed482d997e0301fea8cc2511fd2b27a7f904ff18`
- `report.json`: `1041d7185a760d9895c6e2274980f547166290ad155f9b6b792dc4a97567a3c6`
