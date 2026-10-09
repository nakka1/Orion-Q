# Sant'Anna incremental WDM-information study

## Question and claim boundary

This experiment asks whether past, causally aligned WDM telemetry contains
predictive information about future recorded OSNR or BER **after conditioning
on the target's own history**.  It operationalizes the optical prerequisite

`I(WDM_past; optical_target_future | optical_target_history) > 0`

as a paired improvement in MAE.  The dataset has no measured Bell fidelity,
memory state or entanglement outcome, so this is not direct validation of the
quantum-fidelity link or of the controller.

The run is exploratory.  HardFailure and SoftFailure labels had already been
used by earlier development/transfer experiments before this protocol was
created.  A third untouched campaign or a new link is required for a
confirmatory claim.

## Protocol

- Data: 65,733 recorded rows, six devices, 13,949 timestamps.
- Targets: OSNR in dB and `log10(BER)`.
- Horizons: 3 and 10 acquisition steps.
- Input window: 20 device-local observations.
- Alignment: backward-only `merge_asof`, five-second tolerance; every joined
  source timestamp and sensor age is retained for audit.
- Validation: two expanding-origin folds per transponder with an embargo of
  `window_size + horizon`.
- Models: Ridge and optical-regression adaptations of the repository's
  original EdgeLSTM, EdgeGRU and strictly causal EdgeTCN backbones.
- Feature sets: persistence; target history only; WDM only; target history +
  aligned WDM; and target history + WDM windows permuted within device/fold.
- Primary effect: `MAE(autoregressive) - MAE(combined)`, positive when WDM
  contributes incremental predictive value.
- Required control: `MAE(permuted WDM) - MAE(combined)`, positive when temporal
  alignment matters rather than feature count or marginal distributions.
- Inference: paired moving-block bootstrap within fold/device, 20,000
  replicates, block length 30, Bonferroni correction over 32 contrasts
  (`alpha_per_comparison = 0.0015625`).
- Evidence rule: the corrected lower confidence bound must exceed zero for
  **both** contrasts for the same target, horizon and model.

The machine-readable protocol is
[`experiments/configs/santanna_wdm_information_reference.json`](../../experiments/configs/santanna_wdm_information_reference.json).

## Results

`Delta AR` is the combined model's improvement over target history alone;
`Delta control` is its improvement over temporally permuted WDM.  Positive is
favorable to aligned WDM.  Values are MAE units of the corresponding target.

| Target | h | Model | Delta AR [corrected CI] | Delta control [corrected CI] | Both pass |
|---|---:|---|---:|---:|:---:|
| OSNR | 3 | EdgeGRU | -0.795 [-0.997, -0.618] | -0.129 [-0.322, +0.043] | no |
| OSNR | 3 | EdgeLSTM | -2.183 [-2.476, -1.913] | -1.011 [-1.285, -0.751] | no |
| OSNR | 3 | EdgeTCN | -20.458 [-26.744, -14.264] | -7.051 [-13.583, -0.777] | no |
| OSNR | 3 | Ridge | -0.379 [-0.486, -0.274] | -0.100 [-0.203, -0.002] | no |
| OSNR | 10 | EdgeGRU | -0.868 [-1.073, -0.680] | -0.185 [-0.395, +0.003] | no |
| OSNR | 10 | EdgeLSTM | -3.891 [-4.153, -3.635] | -1.020 [-1.291, -0.737] | no |
| OSNR | 10 | EdgeTCN | -15.419 [-20.295, -10.823] | -2.563 [-7.492, +2.571] | no |
| OSNR | 10 | Ridge | -0.461 [-0.582, -0.341] | -0.179 [-0.292, -0.064] | no |
| log10(BER) | 3 | EdgeGRU | -0.028 [-0.057, -0.001] | +0.005 [-0.016, +0.024] | no |
| log10(BER) | 3 | EdgeLSTM | -0.282 [-0.317, -0.248] | -0.095 [-0.123, -0.069] | no |
| log10(BER) | 3 | EdgeTCN | -1.016 [-1.347, -0.690] | -0.004 [-0.339, +0.332] | no |
| log10(BER) | 3 | Ridge | -0.154 [-0.197, -0.107] | -0.113 [-0.156, -0.067] | no |
| log10(BER) | 10 | EdgeGRU | -0.040 [-0.069, -0.013] | -0.002 [-0.023, +0.017] | no |
| log10(BER) | 10 | EdgeLSTM | -0.308 [-0.340, -0.277] | -0.093 [-0.121, -0.067] | no |
| log10(BER) | 10 | EdgeTCN | -1.076 [-1.410, -0.739] | -0.122 [-0.457, +0.232] | no |
| log10(BER) | 10 | Ridge | -0.171 [-0.217, -0.125] | -0.132 [-0.175, -0.087] | no |

No model/target/horizon combination met the joint rule: **0/16 positive
tests**.  More strongly, aligned WDM failed even the first, incremental-over-
autoregressive criterion in all 16 cases.  The small positive point estimate
for the EdgeGRU BER alignment control at horizon 3 included zero after
correction and its combined forecast was still worse than autoregression.

The EdgeTCN's large negative errors, particularly on the first OSNR fold, show
unstable extrapolation under campaign drift rather than evidence for WDM
value.  They are retained rather than clipped or excluded post hoc.

## Interpretation

The result does **not** prove that WDM telemetry is generally uninformative.
It says that, for these two transponder directions on this intervention-driven
testbed campaign, these aligned variables, windows, models and horizons did
not add out-of-sample information beyond the very strong history of OSNR/BER
itself.  Consequently, this dataset cannot currently be cited as empirical
support for the thesis's stronger WDM-to-quantum predictive claim.

It does strengthen the dissertation methodologically: the central assumption
is now explicit and falsifiable; the original architectures are evaluated;
autocorrelation is controlled; a temporal negative control is required; and a
negative result is preserved.  The next confirmatory step is new, untouched
telemetry from another campaign/link—ideally paired with a quantum-relevant
measurement—using this protocol without changing models after labels are
seen.

## Reproduction

```bash
python experiments/run_santanna_wdm_information_campaign.py \
  --csv /path/to/HardFailure_dataset.csv \
  --output-dir outputs/santanna_wdm_information_reference \
  --targets osnr_db pre_fec_ber --horizons 3 10 \
  --models ridge edge_lstm edge_gru edge_tcn \
  --window-size 20 --folds 2 --bootstrap-replicates 20000 \
  --bootstrap-block-length 30 --hidden-size 8 --max-epochs 6 \
  --batch-size 512 --patience 3
```

The output directory contains fold/device metrics, all paired contrasts,
evidence decisions, out-of-fold predictions, figures, raw-data hash, Git
state and a checksummed artifact manifest.
