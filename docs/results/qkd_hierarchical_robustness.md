# IrelandQCI raw-reading hierarchical robustness

## Why this audit was needed

The primary IrelandQCI analysis correctly averages repeated hardware readings
into 80 controlled cells before treating the 16 fibre/VOA strata as inference
units. That prevents raw-row pseudoreplication, but it does not show how much
within-cell serial variation or sweep-session structure affects the result.

This post-discovery sensitivity audit returns to all 3,893 readings without
promoting them to independent experimental units.

```bash
python experiments/run_qkd_hierarchical_robustness_campaign.py \
  --dataset-dir /path/to/zenodo-21132088 \
  --output-dir outputs/qkd-hierarchical-robustness
```

## Experimental-structure audit

The ten files have a scientifically important layout:

- every file contains exactly one fibre distance and one coexistence condition;
- every distance/condition combination appears in exactly one file;
- each controlled VOA cell contains 24–238 contiguous readings;
- there are no repeated sweep sessions for the same distance/condition.

Consequently, coexistence condition and sweep session are aliased within each
distance. The data can quantify measurement robustness conditional on the ten
published sessions, but cannot estimate or remove a session-wide offset. A
mixed/random campaign effect would not solve this identification failure.

## Conditional hierarchical bootstrap

The outer level resamples the eight VOA strata within each fibre configuration,
preserving the 0/50 km design. The inner level applies a circular moving-block
bootstrap inside every campaign/VOA/condition cell. Block lengths 1, 5 and 10
test sensitivity to progressively stronger local dependence. Repeated neural
seeds and individual raw rows are never counted as independent experiments.

Bonferroni controls the two-target primary family. The interval scope is always
explicitly conditional on the observed sweep sessions.

| Target | Mean degradation | Block 1 interval | Block 5 interval | Block 10 interval |
|---|---:|---:|---:|---:|
| QBER reported | +0.013204 | [0.001010, 0.037176] | [0.000996, 0.037117] | [0.000966, 0.036818] |
| `log1p(SKR)` | +0.360128 | [0.073700, 0.732014] | [0.071331, 0.725805] | [0.075857, 0.733341] |

Both directions remain positive at every block length. They also remain
positive after removing any one of the 16 strata and after retaining either
fibre configuration alone.

## Dose-specific sensitivity

The hierarchical result preserves the earlier dose pattern across every block
length:

- QBER: only 12 dBm has a corrected-positive interval;
- conditional `log1p(SKR)`: 9 and 12 dBm are corrected-positive;
- 3 and 7 dBm remain inconclusive, and the 7 dBm SKR point estimate is slightly
  negative.

The average injected-condition result is therefore not evidence of a smooth
effect across the full grid. It is concentrated in the highest-power regimes.

## Scientific conclusion

Conditional on the ten published sweep sessions, the measured QBER/SKR
degradation is robust to within-cell dependence, unequal cell sizes,
between-VOA resampling and deletion sensitivity. This strengthens the claim
that the recorded measurements contain a stable adverse coexistence pattern.

It does not provide session-clustered confirmation. Because there is only one
session per distance/condition, an unmeasured session offset remains
inseparable from the WDM condition. The result must therefore be described as
a robust conditional association, supported by directional corroboration on
the separate Bristol platform—not as a randomized or session-replicated causal
effect.
