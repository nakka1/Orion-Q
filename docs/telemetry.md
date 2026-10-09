# Telemetry and data provenance

## One source contract

Every source derives from `quantum_twin.optical.interface.TelemetrySource`
and exposes:

- `read()` for canonical records;
- `schema()` for units, ranges, roles and provenance;
- `validate()` for columns, numeric types, ranges and timestamps;
- `generate_dataset()` as a compatibility alias for older experiments;
- `preprocess()` only when an explicit `F_t` target exists.

`quantum_twin.optical.sources` no longer defines a competing abstract base
class. Package-level and submodule imports resolve to the same contract.

## Provenance boundaries

Fields must be classified as one of:

- `measured`: directly reported by hardware;
- `derived`: deterministically calculated from documented inputs;
- `static`: topology or configuration metadata;
- `simulated`: produced by a physical/statistical model;
- `label`: supervised target;
- `identifier`: dataset, campaign, link, device or channel identity.

Recorded WDM data must not be padded with nominal phase, polarization,
temperature, T1/T2, depolarization or fidelity values. Missing sensors remain
`NaN` and receive `*_is_missing` flags. A model trained on a simulated
fidelity target must label it `simulated_quantum`; it is not hardware
validation.

`channel_available` in historical synthetic datasets is a quantum erasure
event. New recorded schemas use `wdm_failure`/`wdm_carrier_available`; future
quantum datasets should use `entanglement_success` separately.

## Sant'Anna optical-failure adapter

`SantAnnaOpticalFailureSource` supports the public hard/soft failure CSVs:

```python
from quantum_twin.optical import SantAnnaOpticalFailureSource

source = SantAnnaOpticalFailureSource(
    "HardFailure_dataset.csv",
    blank_failure_is_normal=False,
)
telemetry = source.read()
print(source.validate(telemetry).summary())
print(source.quality_report(telemetry))
```

The source data is long format. Transponders report BER/OSNR and amplifiers
report input/output power, so missing values in the other fields are
structural and valid. The canonical entity key is
`timestamp_utc + device_id`, not timestamp alone.

Blank values in `Failure` remain unknown by default. Set
`blank_failure_is_normal=True` only when explicitly adopting the authors'
indicator convention.

`build_device_panel()` aligns descriptive snapshots to the nearest sample
with a bounded tolerance; it never averages equipment at the same timestamp.
It must not be used for forecasting because a nearest sample may be later
than the anchor. `build_causal_device_panel()` is the forecasting API: it
uses a backward-only join and records both the selected sensor timestamp and
its age, making future-sample exclusion auditable.
`build_optical_forecast_frame()` shifts labels independently within each
device and therefore cannot leak a future label across equipment.

`build_causal_forecast_panel()` combines these guarantees: each transponder
is an independent anchor, other transponders and all four amplifiers are
joined only from observations available at issue time, and future labels are
shifted within that anchor.

## Leakage-safe optical benchmark

Run the recorded-data benchmark with:

```bash
python experiments/run_santanna_optical_baseline.py \
  --csv /path/to/HardFailure_dataset.csv \
  --target osnr_db \
  --horizon-steps 1 \
  --purge-steps 20 \
  --output outputs/santanna_osnr/report.json
```

The script performs a chronological split within each device, purges a gap
between train and test, fits imputation/scaling only on train, and compares a
histogram gradient booster against persistence. Its report includes dataset
SHA-256, split timestamps, package versions, seed, Git commit/dirty state and
an explicit claim scope: recorded optical forecasting with no measured
quantum target.

For thesis results, use the stronger multi-horizon campaign:

```bash
python experiments/run_santanna_optical_campaign.py \
  --csv /path/to/HardFailure_dataset.csv \
  --output-dir outputs/santanna_hard_failure \
  --targets osnr_db pre_fec_ber \
  --horizons 1 3 10 30 \
  --seeds 13 42 97
```

The campaign adds device-local lags, differences and trailing statistics. All
features at issue time `t` use only measurements at or before `t`; rolling
windows are never centered and values are never backfilled. The effective
embargo is `max(requested_purge, max_lag + horizon)`. Imputation, scaling and
model fitting occur separately inside each expanding-window fold.

The comparison family contains persistence, EWMA, a scalar local-level Kalman
filter, ridge/logistic regression and histogram gradient boosting. Filter
parameters are selected on the training fold only. A chronological
leave-one-device-out evaluation is reported separately from temporal
generalization.

Model selection uses the mean loss across model seeds for each unique physical
observation, followed by a paired moving-block bootstrap within fold/device.
Confidence intervals receive a Bonferroni family-wise correction over candidate
models. A candidate is declared the winner only when its loss is lower and the
corrected interval for improvement over persistence lies entirely above zero;
otherwise the result table explicitly retains persistence. This prevents
repeated seeds from being treated as new telemetry and makes a negative result
scientifically reportable rather than silently selecting the least-bad ML
model.

Failure classification is disabled unless
`--blank-failure-is-normal` is supplied. This makes the dataset-specific
interpretation of blank `Failure` cells explicit in the report.

## Frozen Hard-to-Soft confirmation

The independent regime-transfer command is:

```bash
python experiments/run_santanna_cross_campaign_validation.py \
  --hard-csv /path/to/HardFailure_dataset.csv \
  --soft-csv /path/to/SoftFailure_dataset.csv \
  --output-dir outputs/santanna_hard_to_soft_confirmation
```

`experiments/configs/santanna_hard_to_soft_frozen_selection.json` is the
machine-readable preregistration. It fixes both dataset hashes, feature
contract, three fit seeds, block-bootstrap settings and the model selected for
each target/horizon. A hash mismatch aborts before modeling.

Within HardFailure, an early-prefix model predicts a purged late suffix to
estimate holdout residual radii. After the protocol is frozen, the final point
models, preprocessors and EWMA/Kalman parameters are refit using all
HardFailure rows. Filter state is reset at the start of SoftFailure and a
device-local warmup is excluded. SoftFailure is then used only for external
predictions, paired losses, coverage and distribution-shift diagnostics. Since
the point model is refit after estimating the radius, and the campaign shifts,
coverage is explicitly diagnostic rather than a conformal guarantee.

The confirmatory family contains the six horizons whose frozen model was not
persistence. Paired moving-block-bootstrap intervals receive a Bonferroni
correction across those six hypotheses. Alternative-model rankings on
SoftFailure are written to a separate table marked `exploratory_only`; they
cannot replace a failed preregistered model.

## Incremental WDM-information campaign

Forecast accuracy alone cannot show that WDM telemetry contributes useful
information: an OSNR or BER model can succeed by exploiting only the target's
autocorrelation. The dedicated campaign makes the conditional question
explicit:

```bash
python experiments/run_santanna_wdm_information_campaign.py \
  --csv /path/to/HardFailure_dataset.csv \
  --output-dir outputs/santanna_wdm_information_reference \
  --targets osnr_db pre_fec_ber --horizons 3 10 \
  --models ridge edge_lstm edge_gru edge_tcn
```

For each model it compares target history, WDM-only, their combination, and a
control where complete WDM windows are permuted within device and split while
target histories remain unchanged. Evidence requires corrected paired-block
bootstrap improvement over both autoregression and the misalignment control.
The Edge models inherit the original LSTM/GRU/strictly-causal-TCN backbones;
only their `[0,1]` fidelity sigmoid is removed for OSNR/log-BER regression.

The current HardFailure run is exploratory because both public campaigns had
already been examined. It found no positive combination (0/16). See
[`results/santanna_wdm_information.md`](results/santanna_wdm_information.md).

## Measured QKD coexistence adapter

`QKDCoexistenceSource` reads the IrelandQCI/Toshiba Zenodo release and keeps
measured QBER and secure key rate as quantum targets. Its canonical
experimental keys are fibre length, VOA attenuation, injected-signal state
and injected power. Repeated readings are retained on ingestion and aggregated
only by the analysis at the controlled-cell level.

The published QBER unit is ambiguous (`ratio (or %)`). The adapter therefore
uses `qber_reported`, preserves the scalar, and does not derive agreement
probability or Bell fidelity. No timestamp is fabricated. See
[`data/qkd_coexistence.md`](data/qkd_coexistence.md).

## Bristol HCF ordered coexistence adapter

`BristolHCFCoexistenceSource` reads the public four-user hollow-core-fibre
`4U_OnOff.csv` release. It preserves measured QBER, reported QBER error,
secure key rate and endpoint photon counts in a six-link long table. The
classical coexistence switch and the documented Alice exposure group are
separate columns, which permits `BC/BD/CD` to serve as simultaneous controls
for `AB/AC/AD`.

Only the publisher's relative chunk index is retained. No UTC timestamp or
duration is fabricated because source descriptions of the aggregation period
conflict. The release has a binary classical condition but no per-chunk BER,
OSNR or classical-power series and no Bell-fidelity series; it therefore does
not satisfy the strict paired contract below. See
[`data/bristol_hcf_coexistence.md`](data/bristol_hcf_coexistence.md).

## Strict paired measured-quantum contract

`PairedQuantumTelemetrySource` is the input boundary for a direct temporal
test of the thesis. It requires stable dataset/campaign/device/channel IDs,
explicit UTC timestamps, measured pre-FEC BER, OSNR, receive/transmit power,
measured QBER ratio and secure key rate, plus per-row alignment error. A strict
Bell profile additionally requires measured fidelity, method, uncertainty and
shot count on every row.

Every file has a JSON manifest declaring hardware and target provenance,
clock/link pairing, alignment bound, license and absence of raw key material.
Experimental validation rejects simulated/derived targets, simulated-fidelity
columns, naive timestamps, an unverified shared clock/link and alignment-bound
violations before feature construction or fitting. The acquisition template
and complete protocol are in
[`data/paired_quantum_telemetry.md`](data/paired_quantum_telemetry.md).

## Bristol field-WDM adapter and replay

`BristolNDFFTelemetrySource` canonicalizes the ADVA, Ciena and Voyager
15-minute aggregates into one channel-long table. It preserves structural
missingness for sensors a vendor does not export and keeps timestamps as
`timestamp_local_unzoned` because the source does not state a timezone.

For causal panels, every non-anchor channel is aligned backward only with a
bounded tolerance. The selected source timestamp and age are retained. During
quantum replay, other channels expose recorded BER/Q-factor/power only; their
simulated fidelity is never provided as a feature. See
[`data/bristol_ndff.md`](data/bristol_ndff.md).

The real result was negative: all six frozen superiority hypotheses failed on
SoftFailure. Hard-calibrated intervals reached approximately 100% coverage but
were extremely wide, so the coverage does not constitute useful calibration.
See [`results/santanna_hard_to_soft.md`](results/santanna_hard_to_soft.md) for
the numeric results and their permitted interpretation.

## Resampling

`resample_to_regular_grid()`:

- interprets numeric Unix timestamps as seconds and normalizes to UTC;
- can group by link/device/channel before resampling;
- bounds interpolation to short interior gaps;
- forward-fills binary/event fields rather than interpolating them;
- does not extrapolate continuous values before the first or after the last
  observation.

Do not upsample seconds-scale optical telemetry into microsecond quantum
rounds with linear interpolation. Use a multi-rate simulator: hold the last
observed optical state or sample a documented stochastic bridge, and increase
uncertainty with telemetry age.

## Valid scientific claims

| Data provenance | Target provenance | Integration | Valid description |
|---|---|---|---|
| simulated optical | simulated quantum | offline | synthetic simulation |
| recorded optical | simulated quantum | offline | real-telemetry replay / calibrated hybrid simulation |
| recorded optical | recorded optical | offline | external optical validation |
| live optical | simulated quantum | online | optical hardware-in-the-loop |
| live/recorded optical | measured quantum | physical loop | quantum-twin validation |

Recorded OSNR/BER/power alone does not validate Bell fidelity, memory T1/T2,
pair arrival or purification output.
