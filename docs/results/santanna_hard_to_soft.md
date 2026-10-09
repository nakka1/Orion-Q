# Sant'Anna HardFailure → SoftFailure confirmation

## Scope

This is a confirmatory test of recorded optical OSNR and BER forecasting. It
does not contain a measured quantum target, a different physical link or a
hardware-in-the-loop controller.

The discovery campaign was `HardFailure_dataset.csv` (SHA-256
`3d55f31644e8387ab9092bba232dae99ba8a90788d4157a599e9e3f12375f12d`).
The confirmation campaign was `SoftFailure_dataset.csv` (SHA-256
`2554e5548ff744d2416a80a0775c4320cf616d4cf9204420aaaf1c043a4096d2`).
Both files passed the canonical adapter validation.

## Frozen protocol

The versioned JSON protocol fixes:

- the two raw dataset hashes;
- horizons 1, 3, 10 and 30;
- device-local lags and trailing windows;
- the model selected by the HardFailure discovery campaign;
- fit seeds 13, 42 and 97;
- a 30-step moving-block bootstrap;
- family-wise alpha 0.05 across six non-persistence hypotheses;
- a HardFailure-only holdout-residual split, final refit on all HardFailure and
  a SoftFailure warmup.

SoftFailure never selects a model, hyperparameter, imputation value, scale,
filter parameter or interval radius.

## Confirmatory result

| Target | Horizon | Frozen model | Soft model MAE | Soft persistence MAE | Relative improvement | Result |
|---|---:|---|---:|---:|---:|---|
| OSNR (dB) | 1 | persistence | 0.1540 | 0.1540 | 0.0% | baseline predeclared |
| OSNR (dB) | 3 | histogram gradient boosting | 2.0510 | 0.2305 | -789.8% | not confirmed |
| OSNR (dB) | 10 | histogram gradient boosting | 2.2290 | 0.4970 | -348.5% | not confirmed |
| OSNR (dB) | 30 | histogram gradient boosting | 1.8644 | 0.8664 | -115.2% | not confirmed |
| log10(BER) | 1 | persistence | 0.0292 | 0.0292 | 0.0% | baseline predeclared |
| log10(BER) | 3 | ridge | 0.2130 | 0.0623 | -242.2% | not confirmed |
| log10(BER) | 10 | histogram gradient boosting | 0.5411 | 0.1750 | -209.2% | not confirmed |
| log10(BER) | 30 | histogram gradient boosting | 0.5433 | 0.3324 | -63.5% | not confirmed |

Every corrected confidence interval for a non-persistence hypothesis was
entirely below zero improvement. This is stronger than a nonsignificant result:
the frozen models were consistently worse than persistence on this campaign.

## Distribution shift and uncertainty

For the horizon-1 feature frame, median OSNR changed from 36.0 dB in the
all-HardFailure fit set to 26.3 dB in SoftFailure. Median log10(BER)
changed from -7.607 to -6.936. Population-stability-index values were 2.96 and
3.47 respectively; these values are descriptive diagnostics, not universal
physical thresholds.

HardFailure-calibrated 90% intervals covered 99.5-100% of SoftFailure points,
but their widths were not useful. The horizon-10 OSNR interval was roughly
34.1 dB wide and the horizon-10 BER interval roughly 14.0 log10 units wide.
Cross-campaign exchangeability is not assumed, so these are coverage
diagnostics rather than conformal guarantees.

## Interpretation

SoftFailure is smoother and substantially easier for persistence. Some frozen
models had lower absolute error on SoftFailure than during discovery, yet
persistence improved by much more. The original within-HardFailure gains must
therefore be described as campaign/regime-specific.

Post-hoc inspection found that a Kalman filter descriptively beat persistence
at horizon 30 for both targets, but only by 1.18% for OSNR and 0.95% for BER.
That observation is explicitly exploratory: it cannot replace the failed
preregistered histogram-gradient-boosting models and requires a new,
independent campaign before any confirmatory claim.
