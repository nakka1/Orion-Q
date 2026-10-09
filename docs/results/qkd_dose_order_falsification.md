# QKD monotonic dose-order falsification

## Question and evidence boundary

The earlier IrelandQCI audit showed that the frozen Pareto-aware TriHead orders
joint QBER/conditional-SKR dominance above a trained permuted-condition control
and a joint-label randomization null. This post-discovery audit asks the harder
question: does the network beat the simple physical rule that more injected WDM
power should increase QBER and reduce SKR?

This is a falsification/sensitivity analysis designed after inspecting the
earlier result. It is not an independent confirmation or a preregistered test.

```bash
python experiments/run_qkd_dose_order_falsification_campaign.py \
  --qkd-multihead-dir outputs/qkd_multihead_measured \
  --output-dir outputs/qkd-dose-order-falsification
```

## Leakage-safe protocol

The neural predictions remain frozen from the original
leave-one-fibre/VOA-stratum-out campaign. Four simple baselines are evaluated
on the same 14 positive-key strata:

1. fixed adverse ordering using only injected optical power;
2. QBER-increasing and conditional-SKR-decreasing isotonic fits using power;
3. a train-only Ridge path component plus monotonic isotonic dose residual;
4. a train-only Ridge path component plus sign-constrained linear dose residual.

No baseline receives a target from its held-out stratum. Absence of an injected
signal is represented as `0 mW`; injected dBm is converted by
`10**(dBm/10)`. The fixed baseline uses no measured target at all. A regression
test mutates both targets in a held-out stratum and verifies that every baseline
prediction for that stratum remains unchanged.

Comparisons average the three neural seeds within each stratum and bootstrap
paired stratum differences. Bonferroni simultaneous intervals control one
family of 16 candidate/baseline/metric comparisons.

## Result

| Candidate | Monotonic reference | Reference dominance | Candidate dominance | Candidate − reference | Corrected interval | Result |
|---|---|---:|---:|---:|---:|---|
| Conventional TriHead | Fixed power order | 0.6784 | 0.4283 | -0.2501 | [-0.3975, -0.0874] | reference better |
| Conventional TriHead | Power-only isotonic | 0.6223 | 0.4283 | -0.1940 | [-0.3101, -0.0631] | reference better |
| Conventional TriHead | Path + isotonic power | 0.6223 | 0.4283 | -0.1940 | [-0.3083, -0.0612] | reference better |
| Conventional TriHead | Path + monotonic linear | 0.6784 | 0.4283 | -0.2501 | [-0.3919, -0.0938] | reference better |
| Pareto-aware TriHead | Fixed power order | 0.6784 | 0.5625 | -0.1159 | [-0.1966, -0.0329] | reference better |
| Pareto-aware TriHead | Power-only isotonic | 0.6223 | 0.5625 | -0.0598 | [-0.1449, 0.0297] | inconclusive |
| Pareto-aware TriHead | Path + isotonic power | 0.6223 | 0.5625 | -0.0598 | [-0.1441, 0.0315] | inconclusive |
| Pareto-aware TriHead | Path + monotonic linear | 0.6784 | 0.5625 | -0.1159 | [-0.1976, -0.0298] | reference better |

The fixed and path-adjusted linear rules have identical dominance accuracy
because the path component is constant within each held-out fibre/VOA stratum;
their joint ordering is therefore determined entirely by monotonic dose. The
isotonic models flatten some adjacent levels and obtain a slightly lower
dominance score.

For Pareto-set recovery, every monotonic reference has mean Jaccard `0.2381`,
versus `0.2298` for the Pareto-aware TriHead; none of the network comparisons
shows corrected-positive improvement.

## Scientific conclusion

The earlier alignment result remains numerically true: the Pareto-aware
TriHead beats its trained WDM-permutation control and a joint-label null. The
new control changes its interpretation. A target-free fixed power ordering is
significantly better than that TriHead on the same dominance task. Therefore,
the current result does not demonstrate learned QBER/SKR ordering structure
beyond the simple WDM dose-response rule.

This does not invalidate the measured coexistence effect. Instead, it separates
three claims that must no longer be conflated:

- classical coexistence is associated with measured QKD degradation: supported,
  exploratory and directionally replicated;
- injected power supplies a useful within-grid ordering prior: supported on the
  IrelandQCI cells;
- the evaluated TriHead learns residual ordering beyond that prior: refuted for
  the evaluated models.

The result remains limited to controlled contemporaneous conditions. It does
not test timestamped WDM telemetry, future QKD prediction, causality or physical
Bell-state fidelity.
