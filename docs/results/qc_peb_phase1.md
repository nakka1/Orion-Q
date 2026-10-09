# QC-PEB phase 1: evidence matrix and robustness audit

## Outcome

The first Quantum-Classical Predictive Evidence Base (QC-PEB) pass now keeps
seven evidence profiles in one machine-readable capability matrix without
joining their rows or assigning an arbitrary validity score. The decisive
gap is explicit: none of the currently usable public datasets jointly contains
ordered real WDM telemetry and a measured future quantum target.

Build the matrix and evidence ledger with:

```bash
python experiments/build_qc_peb.py --output-dir outputs/qc-peb
```

`dataset_capability_matrix.csv` distinguishes recorded versus simulated data,
WDM time series versus binary conditions, QBER/SKR/communication-rate/fidelity
targets, time information and the exact claim each source can support. Runtime
validation rejects internally inconsistent capability claims.

## Bristol HCF robustness result

The original primary effect was re-estimated under six descriptive sensitivity
families: per sequence segment, leave-one-segment-out, leave-one-link-out,
leave-one-pair-out, all 20 three-versus-three spatial assignments and an audit
of the treatment sequence's periodicity.

| Target | Primary DiD degradation | Segment range | Leave-link range | Leave-pair range | Spatial rank | Stable |
|---|---:|---:|---:|---:|---:|:---:|
| QBER proportion | 0.015266 | [0.011825, 0.016804] | [0.014731, 0.015926] | [0.014945, 0.015718] | 1/20 | yes |
| `log1p(SKR)` | 0.106588 | [0.097187, 0.148006] | [0.071972, 0.138747] | [0.100398, 0.115354] | 1/20 | yes |

Both directions also stayed positive after removing any complete sequence
segment. The documented Alice group is the strongest positive assignment,
but its two-sided spatial tail fraction is 0.10 because the complementary
group produces the sign-reversed effect. Exposure topology was not randomized;
this is a spatial specificity diagnostic, not a randomization-test p-value.

The On/Off labels alternate perfectly inside all three contiguous segments:
lag 1 flips every label and lag 2 reproduces every label. A shifted-label
"temporal placebo" is therefore not identifiable and is rejected by the
campaign instead of being reported as independent evidence.

Reproduce with:

```bash
python experiments/run_bristol_hcf_robustness_campaign.py \
  --dataset-dir data/raw/bristol-hcf-coex/61193flfovbn2puknndsr3qm5 \
  --output-dir outputs/bristol-hcf-robustness \
  --bootstrap-replicates 50000 --seed 42
```

## Dryad status

The STIKE source and purged temporal campaign are implemented and tested, but
the actual campaign has not been executed: Dryad's public file endpoint denied
anonymous downloads during this phase. This remains a documented unavailable
input, not a synthetic substitution. Even after execution it will validate
only real-quantum own-history methodology because the release contains no WDM
telemetry, SKR or fidelity.

## Scientific conclusion

Phase 1 strengthens the narrow real-hardware result: classical coexistence is
associated with localized simultaneous degradation of measured QBER and SKR,
and the Bristol direction is not explained by one obvious segment, link or
switch pair. It still does not validate the central prospective statement that
time-varying WDM telemetry adds information about future measured quantum
performance. That remains the high-impact open claim.

## Phase-2 continuation

The subsequent cross-platform campaign formally tested directional replication
between IrelandQCI and Bristol. Both QBER and `log1p(SKR)` replicated after
Holm correction, while magnitude heterogeneity was high. See
[`cross_platform_qkd_replication.md`](cross_platform_qkd_replication.md).

Phase 3 then audited six additional public candidates and incorporated
Gavignet et al. as a third-platform **published aggregate**, without treating
condition means as raw samples. See [`qc_peb_phase3.md`](qc_peb_phase3.md).
