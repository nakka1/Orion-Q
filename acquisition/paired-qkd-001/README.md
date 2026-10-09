# Campaign `paired-qkd-001`

Status: **acquisition scaffold created; hardware feed not connected**.

## Files

- `paired_quantum_telemetry_template.csv`: canonical collection header.
- `paired_quantum_telemetry_template.parquet`: equivalent Parquet template.
- `paired_quantum_telemetry.manifest.json`: provenance and synchronization contract.
- `paired_quantum_data_dictionary.md`: units and row semantics.

CSV/Parquet measurements are ignored by Git because operational telemetry may
expose sensitive topology and performance. The manifest and this runbook do
not contain raw keys or key bits and may be versioned after review.

## Blocking information before the first valid row

- [ ] Replace `synchronization.clock_source` with the actual PTP/NTP/GPS or
  shared instrument-clock source.
- [ ] Replace `license` with the dataset-use/redistribution terms.
- [ ] Confirm the maximum measured alignment error; do not retain the default
  `1000 ms` merely because it is present.
- [ ] Assign stable `campaign_id`, `device_id` and `channel_id` values.
- [ ] Map the optical exporter to pre-FEC BER, OSNR, Rx power and Tx power.
- [ ] Map the QKD manager to QBER as a ratio `[0,1]` and SKR in bit/s.
- [ ] Confirm that no raw key material enters the collector.
- [ ] Write timestamps with `Z` or an explicit UTC offset and compute
  `alignment_error_ms` per joined observation.

Bell fidelity is not required for the QKD profile. If it becomes available,
change its manifest provenance to `measured_quantum` and collect measurement
method, standard error and shots on every fidelity row. A QBER-derived value
must not be placed in `bell_fidelity_measured`.

## Preflight

After filling the manifest and writing data, run:

```powershell
@'
from quantum_twin.quantum.paired_telemetry import PairedQuantumTelemetrySource

source = PairedQuantumTelemetrySource(
    r"acquisition\paired-qkd-001\paired_quantum_telemetry.csv",
    manifest_path=r"acquisition\paired-qkd-001\paired_quantum_telemetry.manifest.json",
)
data = source.require_valid()
print(source.quality_report(data))
'@ | .audit-winvenv\Scripts\python.exe -
```

## First analysis

Only after preflight succeeds:

```powershell
.audit-winvenv\Scripts\python.exe `
  experiments\run_measured_quantum_prediction_campaign.py `
  --data acquisition\paired-qkd-001\paired_quantum_telemetry.csv `
  --manifest acquisition\paired-qkd-001\paired_quantum_telemetry.manifest.json `
  --output-dir outputs\paired-qkd-001 `
  --models edge_lstm edge_gru edge_tcn
```

For a useful transfer test, collect at least two campaigns or devices under a
documented protocol. The campaign code will report unavailable cross-group
folds instead of pretending that a single device establishes generalization.
