# QKD Pareto falsification and unseen-condition audit

## Evidence boundary

This is a post-discovery falsification analysis of the fixed IrelandQCI
TriHeads. It was designed after observing the Pareto-aware ordering result, so
it is not an independent confirmation. No architecture, loss weight, margin,
seed or threshold is selected in this phase.

The later [monotonic dose-order audit](qkd_dose_order_falsification.md)
supersedes the model-specific interpretation of the positive ordering result:
a target-free fixed power rule is corrected-better than the Pareto-aware
TriHead on the same dominance task.

```bash
python experiments/run_qkd_pareto_falsification_campaign.py \
  --qkd-multihead-dir outputs/qkd_multihead_measured \
  --output-dir outputs/qkd-pareto-falsification
```

The audit asks two narrower questions:

1. Are the frozen out-of-stratum predictions aligned with the measured
   condition labels more strongly than expected under joint relabeling?
2. Does either frozen architecture generalize when an entire coexistence
   condition—baseline, 3, 7, 9 or 12 dBm—is absent from training?

## Joint-label randomization

For each of 20,000 replicates, the predicted QBER and SKR vectors are
permuted *together* within each physical fibre/VOA stratum. This preserves the
model's joint prediction geometry but breaks its association with the measured
condition. All three model seeds share the same permutation because they are
repeated fits of the same physical cells, not independent experiments.

Bonferroni controls a family of four model/metric tests.

| Model | Metric | Observed | Null mean | Difference | Adjusted p | Result |
|---|---|---:|---:|---:|---:|---|
| Conventional TriHead | Pareto Jaccard | 0.2123 | 0.2555 | -0.0432 | 1.0000 | no |
| Conventional TriHead | Dominance accuracy | 0.4283 | 0.3209 | +0.1074 | 0.0226 | positive |
| Pareto-aware TriHead | Pareto Jaccard | 0.2298 | 0.2362 | -0.0064 | 1.0000 | no |
| Pareto-aware TriHead | Dominance accuracy | 0.5625 | 0.4089 | +0.1535 | 0.0118 | positive |

Both networks therefore contain above-chance information about the direction
of measured QBER/SKR dominance inside the observed condition grid. Neither
recovers the exact Pareto set above the relabeling null.

This test complements rather than replaces the trained permuted-WDM control.
Randomization conditions on already fitted predictions and asks whether their
condition labels matter. The trained control asks whether the association
survives refitting after WDM features are broken. The Pareto-aware dominance
result passes both checks; the conventional model passes only this conditional
randomization check.

## Leave-one-condition-out transport

Each coexistence condition is then removed from training in turn. Evaluation
uses all 16 held-out cells for that condition. This keeps the same fibre/VOA
strata represented through their other conditions, so it tests interpolation
or extrapolation across the condition grid—not device or campaign transfer.

The strong baseline predicts each held-out cell from the measured mean of the
same target over the other conditions in that physical stratum.

| Target loss | Stratum-mean baseline | Conventional | Pareto-aware | Corrected conclusion |
|---|---:|---:|---:|---|
| Conditional log-SKR MAE | 0.2813 | 0.6628 | 0.6624 | both networks worse |
| QBER MAE | 0.02110 | 0.01659 | 0.01790 | intervals cross zero |
| Availability Brier | 0.03125 | 0.06365 | 0.06463 | intervals cross zero |

For conditional log-SKR, conventional-minus-baseline improvement is
`-0.3815`, with nine-comparison Bonferroni interval
`[-0.5979, -0.1649]`; Pareto-aware improvement is `-0.3811`, interval
`[-0.6241, -0.1714]`. Neither architecture is superior to the other for any
held-out target.

## Scientific conclusion

At this stage of the audit, the result was narrowed to:

> Within the measured IrelandQCI condition grid, aligned coexistence
> conditions carry information for ordering joint QBER/SKR dominance, and the
> Pareto-aware model survives both a trained feature-permutation control and a
> conditional joint-label randomization test.

The same models do not demonstrate transport to a completely unseen WDM
condition. This rules out describing the current result as a generally
interpolating power-response model. It remains contemporaneous controlled-cell
evidence, not temporal prediction, causality, Bell-fidelity validation or
external replication.

The subsequent dose-order comparison narrows this further: alignment above a
permutation/null control does not establish learned structure beyond the
monotonic power prior. The measured coexistence association remains supported,
but model-specific residual ordering does not.

The next validation gate is a protocol frozen before evaluation on a distinct
campaign or device with a denser power grid—or a genuinely timestamped paired
WDM/QKD trace. Further tuning on these 80 cells would weaken rather than
strengthen the thesis.
