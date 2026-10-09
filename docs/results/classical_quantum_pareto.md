# Classical-quantum Pareto and joint-head audit

## Question and evidence boundary

Can the measured-data TriHead recover the joint QBER/SKR operating frontier,
does an explicit dominance loss improve that recovery, and does a third
physical platform support a genuinely multi-objective classical/quantum
trade-off?

This is a frozen **exploratory** protocol, not a retrospective
“preregistration”: all source datasets had already been inspected. No model is
fitted here. IrelandQCI predictions come from the version-2 leave-one-stratum-
out MultiHead campaign. Its Pareto-aware variant uses a fixed loss weight
`0.25` and scaled margin `0.05`; dominance labels come only from positive-key
cells inside each training fold. The Gavignet aggregates are hash-frozen and loaded
only after the internal audit; they are never training rows.

```bash
python experiments/run_classical_quantum_pareto_campaign.py \
  --qkd-multihead-dir outputs/qkd_multihead_measured \
  --aggregate-csv data/reference/gavignet_2024_published_aggregate.csv \
  --output-dir outputs/classical-quantum-pareto
```

## Internal IrelandQCI result

For each fibre/VOA stratum, a condition is Pareto-efficient when no other
condition has both lower QBER and higher SKR. The audit finds 22 efficient
cells among 80; the 12 dBm condition is dominated in 14 of 16 strata.

Joint model evaluation is possible in 14 strata with at least two positive-key
conditions. The primary recovery metrics are Pareto-set Jaccard and accuracy
on condition pairs with a true dominance relation.

| Metric | Aligned TriHead | Permuted-condition TriHead | Difference | Bonferroni bootstrap CI |
|---|---:|---:|---:|---:|
| Pareto Jaccard | 0.2123 | 0.2159 | -0.0036 | [-0.1217, 0.1175] |
| Dominance-pair accuracy | 0.4283 | 0.3164 | +0.1119 | [-0.0588, 0.3069] |

The Pareto-aware model changes the joint result:

| Metric | Pareto-aware aligned | Pareto-aware permuted | Difference | Bonferroni bootstrap CI |
|---|---:|---:|---:|---:|
| Pareto Jaccard | 0.2298 | 0.1508 | +0.0790 | [-0.1115, 0.3214] |
| Dominance-pair accuracy | 0.5625 | 0.3874 | +0.1750 | **[0.0168, 0.3111]** |

Bonferroni controls the family of six joint model/metric contrasts; each
simultaneous interval has confidence `99.17%`, yielding familywise `95%`.
The dominance-order result survives that correction. It is not just a lower
marginal loss: correctly aligned coexistence conditions help the constrained
model order which measured condition dominates another.

The full-front criterion nevertheless fails. Pareto Jaccard remains `0.2298`,
well below the frozen `0.5` threshold. Relative to the conventional TriHead,
the Pareto-aware dominance gain is `+0.1342`, but its corrected interval is
`[-0.0004, 0.2987]`; Jaccard improvement is also inconclusive. Thus the result
supports an alignment-specific ordering signal, not reliable Pareto-set
recovery or general superiority of the new loss.

This joint negative result is stricter than evaluating QBER and SKR losses
separately: a model can have tolerable marginal error while choosing the wrong
operating point.

## External Gavignet structure check

The three multispan condition means form a real antagonistic frontier:

| WDM power | Classical utility | Quantum utility | Four-objective Pareto |
|---:|---:|---:|:---:|
| 13.5 dBm | 0.000 | 1.000 | yes |
| 15.5 dBm | 0.463 | 0.519 | yes |
| 17.5 dBm | 1.000 | 0.000 | yes |

Classical utility averages normalized OSNR and negative `log10(BER)`;
quantum utility averages normalized negative QBER and `log1p(SKR)`. These
normalizations are for geometry only, not effect-size transport.

All three conditions are non-dominated, but a simple weighted sum selects only
the endpoints: quantum weight above 0.5 selects 13.5 dBm, classical weight
above 0.5 selects 17.5 dBm, and 0.5 ties the endpoints. The valid intermediate
15.5 dBm compromise is never selected by the scalar score.

That is the product-relevant result: a single “network health” score can hide
valid operating points and silently encode an uncalibrated business policy.
The system should expose the Pareto front, explicit safety constraints and the
selected policy weights separately.

## Scientific conclusion

The external hardware evidence supports the existence of a real
classical/quantum multi-objective trade-off. Internally, the new loss adds a
specific exploratory finding: aligned coexistence conditions improve
dominance ordering over a capacity- and loss-matched permutation control.

A later [monotonic dose-order falsification](qkd_dose_order_falsification.md)
shows that a target-free fixed power rule reaches `0.6784` dominance accuracy,
above the Pareto-aware TriHead's `0.5625`, with a corrected negative
model-minus-baseline interval. The alignment result below remains valid, but it
must not be interpreted as learned ordering structure beyond the dose prior.
The defensible claim is therefore about contemporaneous condition information
and decision geometry, not temporal forecasting:

> Classical coexistence conditions contain useful information for ordering
> joint measured QBER/SKR outcomes in this controlled dataset, while QKD
> assurance must still expose QBER, SKR and optical quality as separate
> objectives because they can respond antagonistically to the same WDM control.

This does not establish causality, future prediction, Bell fidelity, external
model transport, independent-unit inference from Gavignet or calibrated
operator utility weights. Those remain out of scope.

## Post-discovery falsification

A subsequent frozen-model audit used 20,000 joint condition-label
randomizations. Pareto-aware dominance accuracy remained above its null after
Bonferroni (`p_adj=0.0118`), but Pareto Jaccard did not. When each WDM
condition was completely held out, neither TriHead beat a within-stratum
target-mean baseline; both were corrected-worse for conditional log-SKR.
Accordingly, the positive result is confined to dominance ordering inside the
observed condition grid. See
[`qkd_pareto_falsification.md`](qkd_pareto_falsification.md).
