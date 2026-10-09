# QC-PEB phase 3: third-platform public evidence audit

## Outcome

The search did not locate a third public dataset that can enter the formal
partial-conjunction analysis. It did locate a third physical DWDM/QKD platform
with sufficiently explicit published means for a guarded descriptive audit:
Gavignet et al.'s Toshiba systems co-propagating with up to 60 classical
channels.

This creates a new evidence tier:

> published hardware aggregate — external directional corroboration, without
> raw-sample inference or forecasting.

The six transcribed values remain labelled as condition means. The campaign
computes no p-values, confidence intervals or resampling from them.

## Single-span 50 km contrasts

At the same reported 16.8 dBm total WDM power for the exposed conditions:

| Contrast | QBER change | SKR change | Direction agrees |
|---|---:|---:|:---:|
| 30 channels vs no WDM | +2.0 percentage points | -62 kb/s (-36.69%) | yes |
| 60 channels vs no WDM | +2.0 percentage points | -63 kb/s (-37.28%) | yes |

The real-hardware direction therefore agrees with IrelandQCI and Bristol HCF:
classical coexistence is accompanied by higher QBER and lower SKR. Because the
paper reports means rather than independent acquisition units, “three
platforms agree descriptively” is valid; “three studies replicate
statistically” is not.

## Multispan paired aggregate

For the 50 km MU-QKD span, the paper reports three approximate 24-hour means:

| WDM power | Classical pre-FEC BER | OSNR | QBER | SKR |
|---:|---:|---:|---:|---:|
| 13.5 dBm | 1.0e-2 | 26.2 dB | 5.4% | 63 kb/s |
| 15.5 dBm | 5.5e-3 | 27.7 dB | 6.0% | 48 kb/s |
| 17.5 dBm | 3.0e-3 | 29.7 dB | 6.7% | 36.5 kb/s |

Every adjacent step is monotonic. Across the three condition means, WDM power
has Spearman `+1` with QBER and `-1` with SKR. More importantly, classical
performance improves while quantum performance degrades: OSNR rises and
pre-FEC BER falls, while QBER rises and SKR falls.

This is a useful design correction for the thesis and eventual product. A
single scalar notion of “optical link health” is insufficient. Higher launch
power can improve a classical coherent channel while simultaneously increasing
quantum-channel noise. Future DualHead/TriHead inputs should therefore retain
power, spectrum/channel occupancy, BER and OSNR separately and learn
regime-specific interactions rather than force all metrics onto one health
axis.

The correlations are descriptive identities of three ordered aggregates, not
estimated population effects. No uncertainty or temporal predictive lead can
be recovered from the paper.

## Scientific status after phase 3

- Formal same-direction replication remains **two studies** and retains its
  corrected QBER/SKR results.
- Descriptive physical corroboration now spans a **third DWDM/QKD platform**.
- A second new article-level aggregate (Stathis 2026) also reports a consistent
  optical-power dose response, but has not been pooled.
- Formal `k >= 3` and the central incremental temporal claim remain open
  because no new candidate exposes raw public independent units or synchronized
  WDM/quantum time series.

This is a stronger and more interesting thesis than merely saying “bad
classical BER means bad quantum performance”: classical and quantum objectives
can move in opposite directions under a shared optical control variable.

## Reproduction

```bash
python experiments/build_public_qkd_evidence_search.py \
  --aggregate-csv data/reference/gavignet_2024_published_aggregate.csv \
  --output-dir outputs/public-qkd-evidence-search
```

See the [search protocol](../data/public_qkd_dataset_search.md) and the
[cross-platform two-study inference](cross_platform_qkd_replication.md).

The follow-on [Pareto audit](classical_quantum_pareto.md) confirms that all
three multispan aggregates are non-dominated, while the existing TriHead does
not recover the IrelandQCI joint frontier better than its alignment control.
The later [falsification audit](qkd_pareto_falsification.md) finds
above-random dominance ordering but no full-front or unseen-condition
transport, preserving QC-PEB's separation between physical plausibility and
deployable predictive evidence.
