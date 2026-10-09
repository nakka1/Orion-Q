# Cross-platform replication of measured QKD coexistence degradation

## Question

Do two independent real-hardware public datasets show the same direction of
quantum degradation under classical optical coexistence?

This analysis uses:

- 16 IrelandQCI/Toshiba fibre/VOA strata, contrasting the average of four
  injected-signal powers against the matched no-signal baseline;
- 16 Bristol HCF adjacent On/Off difference-in-differences pairs for QBER and
  17 for `log1p(SKR)`, using three simultaneous unexposed links as controls.

Raw observations are never joined. Each source first produces effects at its
own valid experimental-unit level. QBER magnitudes are combined only after
within-study standardization because IrelandQCI's publisher scale is ambiguous.

## Replication test

For each study and outcome, the campaign recomputes a one-sided exact sign-flip
sensitivity p-value. With two studies, the partial-conjunction p-value for the
claim that both study effects are positive is the larger of the two p-values.
Holm correction is then applied across the QBER and `log1p(SKR)` families.

| Outcome | Ireland positive units | Bristol positive units | Partial-conjunction p | Holm p | Direction replicates |
|---|---:|---:|---:|---:|:---:|
| QBER | 13/16 | 16/16 | 0.0007477 | 0.0014954 | yes |
| `log1p(SKR)` | 12/16 | 15/17 | 0.0057373 | 0.0057373 | yes |

Both original study-specific primary results were already positive after their
own two-target corrections. The new result therefore supports directional
replication across two different hardware platforms and experimental designs.
Because exposure order was not randomized in either source, these p-values
remain sensitivity inference rather than randomized-treatment inference.

## Magnitude heterogeneity

Direction replicates; magnitude does not transport cleanly.

| Outcome | Ireland Hedges g | Bristol Hedges g | Exploratory pooled g | Random-effects 95% CI | I² |
|---|---:|---:|---:|---:|---:|
| QBER | 0.278 | 4.353 | 2.239 | [-1.752, 6.229] | 95.4% |
| `log1p(SKR)` | 0.490 | 1.482 | 0.957 | [-0.013, 1.928] | 79.9% |

Only two studies are available, making heterogeneity and random-effects
intervals unstable. Both pooled intervals cross zero, and the campaign sets
`common_magnitude_supported=false`. The scientifically defensible result is:

> A same-direction coexistence effect replicates, but its size is strongly
> platform- and design-dependent.

Leave-one-study-out tables make the evidence dependency explicit: after either
study is removed, one positive study remains, but cross-platform replication
is no longer identifiable.

## Claim boundary

This finding strengthens the physical middle link of the TCC thesis: real
classical optical coexistence conditions are repeatedly associated with real
QBER/SKR degradation. It does not establish:

- a common causal effect size;
- randomized causality;
- future prediction;
- incremental information in multivariate WDM telemetry;
- measured Bell-state fidelity.

## Reproduction

```bash
python experiments/run_cross_platform_qkd_replication_campaign.py \
  --ireland-campaign-dir outputs/qkd_coexistence_measured \
  --bristol-campaign-dir outputs/bristol_hcf_coexistence_measured \
  --output-dir outputs/cross-platform-qkd-replication
```

The command verifies the SHA-256 manifests of all four input artifacts before
analysis. Configuration is frozen in
`experiments/configs/cross_platform_qkd_replication_reference.json`.
