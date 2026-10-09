# Paired WDM/quantum telemetry acquisition protocol

## Purpose and evidence boundary

This contract is the missing dataset needed to test the thesis directly:

```
I(WDM past; measured quantum target future | quantum-target history) > 0
```

The existing IrelandQCI/Toshiba sweeps contain measured QBER and secure key
rate, but no timestamps. The Bristol and Sant'Anna releases contain temporal
WDM telemetry, but no measured quantum target. They cannot be joined, and the
code never fabricates timestamps or treats simulated fidelity as experimental.

`PairedQuantumTelemetrySource` accepts only CSV/Parquet data accompanied by a
JSON manifest. An experimental run is rejected unless the input is declared
`recorded_hardware` or `live_hardware`, QBER/SKR provenance is
`measured_quantum`, timestamps have an explicit UTC zone, and the optical and
quantum samples are verified as coming from the same link and clock domain.

## Generate a collection bundle

```bash
python experiments/create_paired_quantum_acquisition_template.py \
  --output-dir acquisition/paired-qkd-001 \
  --formats csv parquet \
  --dataset-id paired-qkd-001
```

The command creates an empty table, manifest and field dictionary. Edit the
manifest before collection; placeholder values deliberately fail validation.
Parquet generation requires the optional `pyarrow` dependency.

## One row, one synchronized observation

| Field | Unit | Required | Meaning |
|---|---:|:---:|---|
| `dataset_id` | identifier | yes | Immutable release identifier |
| `campaign_id` | identifier | yes | Acquisition session/regime |
| `device_id` | identifier | yes | QKD/quantum endpoint or apparatus |
| `channel_id` | identifier | yes | Stable wavelength/link channel |
| `timestamp_utc` | ISO 8601 UTC | yes | Issue timestamp with `Z` or offset |
| `pre_fec_ber` | ratio | yes | Measured classical pre-FEC BER |
| `osnr_db` | dB | yes | Measured optical signal-to-noise ratio |
| `rx_power_dbm` | dBm | yes | Measured received power |
| `tx_power_dbm` | dBm | nullable | Measured launch power if exported |
| `qber_ratio` | ratio | yes | Measured QBER in `[0,1]`, never percent |
| `secure_key_rate_bps` | bit/s | yes | Measured SKR; zero is a valid outcome |
| `bell_fidelity_measured` | ratio | profile-dependent | Tomography/witness-derived measured fidelity |
| `fidelity_standard_error` | ratio | with fidelity | Measurement uncertainty |
| `fidelity_shots` | count | with fidelity | Number of trials/shots |
| `fidelity_measurement_method` | category | with fidelity | Tomography, witness, visibility mapping, etc. |
| `alignment_error_ms` | ms | yes | Absolute optical/quantum alignment error |

Performance metrics may be collected; raw key bits, generated keys and secret
material are prohibited. `contains_key_material` must remain `false`.

## Synchronization procedure

1. Put optical and quantum instruments in a documented common clock domain
   (PTP, GPS, NTP with measured error, or a shared instrument clock).
2. Preserve native timestamps and record clock source and maximum alignment
   error in the manifest.
3. Join only observations from the same physical link/channel. Record the
   measured absolute join error in `alignment_error_ms`; never forward-align
   from a future optical observation.
4. Keep stable `campaign_id`, `device_id` and `channel_id` values. Start a new
   campaign when firmware, topology, wavelength plan, calibration or operating
   protocol changes materially.
5. Prefer a cadence fast enough to resolve the degradation horizon of interest.
   Do not upsample sparse WDM telemetry to quantum-shot cadence and present the
   interpolated values as measured.
6. Retain zero SKR and failed operating intervals. Removing them destroys the
   availability process that DualHead is designed to model.

For a Bell-fidelity campaign, record the measurement method, standard error
and shots on every row. A QBER-to-fidelity transform is a derived model, not a
measurement, and is rejected by the experimental validator.

## Temporal prediction protocol

```bash
python experiments/run_measured_quantum_prediction_campaign.py \
  --data acquisition/paired-qkd-001/paired_quantum_telemetry.csv \
  --manifest acquisition/paired-qkd-001/paired_quantum_telemetry.manifest.json \
  --output-dir outputs/paired-qkd-001 \
  --models edge_lstm edge_gru edge_tcn
```

The campaign predicts future QBER and `log1p(SKR)` with the original EdgeLSTM,
EdgeGRU and causal EdgeTCN backbones. For each model it compares persistence,
the target's own history, aligned WDM plus history, WDM-only and a control in
which complete WDM windows are temporally permuted within each series. Folds
use `max(requested purge, window + horizon)` and preprocessing fitted only on
training data. Campaign and device holdouts are added only when at least two
groups and enough time-safe training observations exist; skipped folds remain
explicit in `report.json`.

Evidence requires improvement over both own-target history and permuted WDM
after family-wise multiplicity correction across targets, horizons, models and
evaluation regimes. This supports incremental predictive information, not by
itself a causal mechanism.

## Guarded `KAPPA_DEPOL_FROM_BER` calibration

```bash
python experiments/calibrate_kappa_from_measured_fidelity.py \
  --data acquisition/paired-bell-001/paired_quantum_telemetry.csv \
  --output-dir outputs/kappa-paired-bell-001
```

This command requires synchronized measured BER and Bell fidelity on every
row. It selects kappa on a temporal training prefix and reports untouched test
error. It does not mutate the default physics configuration. Until such a run
exists, the default `KAPPA_DEPOL_FROM_BER=15` remains a phenomenological
sensitivity parameter, not a hardware calibration.

## Interoperability references

The field names are project-canonical rather than claims of conformance. A
collector can map OpenConfig terminal-device/gNMI optical telemetry and a QKD
manager interface into this table. Relevant references include ITU-T Y.3800
for QKD-network concepts and management, OpenConfig terminal-device and gNMI
for optical telemetry, and ETSI GS QKD 015/018 for QKD control/orchestration
interfaces. Record the exact vendor path, transform, firmware and units in the
campaign's auxiliary metadata.

## Licensing and publication

Publish the manifest, schema version, acquisition code, anonymization rules and
data license with the dataset. Device and link identifiers may be pseudonyms,
but the mapping must remain stable within a campaign. Commercial products must
not redistribute third-party research datasets or trained weights unless their
licenses explicitly permit it.
