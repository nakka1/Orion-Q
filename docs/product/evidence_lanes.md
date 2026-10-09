# Three-lane evidence architecture

## Why the separation is necessary

The repository contains useful evidence about three different targets. They
belong in one product interface, but they are not interchangeable:

| Lane | Evidence | Output | Current permitted use |
|---|---|---|---|
| `wdm_optical_health` | measured Sant'Anna BER, OSNR and optical power | observed optical state and data quality | monitoring and historical replay |
| `qkd_assurance` | measured published QBER and SKR | exact-grid evidence decision | technical decision support inside reviewed cells |
| `bell_fidelity_control` | simulated WDM, pair state and Bell fidelity | future simulated fidelity, uncertainty and action | shadow/simulation sandbox only |

Optical pre-FEC BER is not QBER. QBER and SKR are not Bell-state fidelity. The
application therefore performs no cross-metric conversion, and the first two
lanes cannot authorize `PURIFY` or `SWAP`.

```text
measured WDM --------> optical-health snapshot --------> observe / audit
                           X
measured QBER + SKR -> exact-grid assurance -----------> decision support
                           X
simulated WDM ------> simulated Bell predictor --------> safety supervisor
                                                        -> dry-run/simulator
                                                        -> never hardware
```

The `X` boundaries are executable gates, not documentation conventions.

## Measured WDM currently used

`MeasuredOpticalHealthAdapter` reads the public Sant'Anna long-format CSV via
the existing validated source adapter. It preserves individual transponders
and amplifiers, structural missingness, UTC timestamps and the original
optical failure label. The API returns the latest recorded state per device,
the complete quality report and SHA-256 provenance.

The registered HardFailure recording contains 65,733 rows, 13,949 timestamps
and six devices. Its source hash is
`3d55f31644e8387ab9092bba232dae99ba8a90788d4157a599e9e3f12375f12d`.
The associated causal forecasting study found zero positive joint tests out of
16 predeclared model/target/horizon combinations. The product displays that
negative result as `no_incremental_predictive_value_detected`; it does not hide
it or replace it with a model trained on simulated fidelity.

Generate the local, hash-bound optical artifact with:

```powershell
$env:PYTHONPATH = "$PWD\src"
.venv\Scripts\python.exe experiments/export_measured_optical_health_lane.py `
  --csv ..\optical-failure-dataset\HardFailure_dataset.csv `
  --forecast-report outputs\santanna_wdm_information_reference\report.json `
  --expected-dataset-sha256 3d55f31644e8387ab9092bba232dae99ba8a90788d4157a599e9e3f12375f12d
```

The generated `report.json` and `artifact_manifest.json` stay under ignored
`outputs/`; raw data is never bundled into the application.

## API and dashboard

API v1.2 adds:

| Method | Path | Meaning |
|---|---|---|
| `GET` | `/api/v1/evidence-lanes` | three lane contracts, configuration and central-hypothesis status |
| `GET` | `/api/v1/evidence-lanes/optical-health` | configured measured WDM snapshot with hashes |

The browser cannot supply a filesystem path. The operator must configure the
trusted source when starting the loopback server:

```powershell
.venv\Scripts\python.exe experiments/run_qkd_assurance_api.py `
  --optical-wdm-csv ..\optical-failure-dataset\HardFailure_dataset.csv `
  --optical-evidence-report outputs\santanna_wdm_information_reference\report.json `
  --expected-optical-dataset-sha256 3d55f31644e8387ab9092bba232dae99ba8a90788d4157a599e9e3f12375f12d
```

If the CSV or report hash differs, the optical snapshot is rejected. If no
source is configured, the dashboard shows that state instead of guessing a
path or falling back to synthetic data.

## Control gate

`EvidenceLaneRegistry.assess_control_ingress` is a semantic pre-gate. Optical
and QKD inputs return `sandbox_ingress_allowed=false`, effective action `HALT`
and reason `evidence_lane_not_bell_fidelity`. A simulated Bell input is only
eligible to proceed; it is not authorized. The existing independent
`SafetySupervisor` still decides whether the requested non-physical operation
must fail closed.

The supervisor now also requires every `PURIFY`/`SWAP` snapshot to declare the
`bell_fidelity_control` lane and rejects measured inputs because a measured
Bell control adapter has not been implemented. Every current execution still
enforces `physical_action_performed=false`.

## Scientific interpretation

This release improves construct validity and product auditability, not the
evidence for the central causal hypothesis. It proves that the application can
consume a real WDM recording without relabelling it as quantum data. The claim
that temporally aligned WDM telemetry predicts a future measured quantum KPI
remains open until a qualifying synchronized WDM/QBER-SKR or WDM/Bell-fidelity
dataset passes own-history, persistence, permutation and external-transfer
controls.
