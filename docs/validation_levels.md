# Validation and evidence provenance

The historical L0-L4 realism scale remains available for compatibility, but
it cannot describe recorded optical data driving a simulated quantum target
without ambiguity. New results must report three independent coordinates.

## 1. Input-data provenance

| Value | Meaning |
|---|---|
| `synthetic` | Generated entirely by the project |
| `calibrated_synthetic` | Generated after calibration to recorded data |
| `recorded_hardware` | Previously recorded physical telemetry |
| `live_hardware` | Hardware feeds the evaluated run live |

## 2. Target provenance

| Value | Meaning |
|---|---|
| `simulated_analytical` | Closed-form/model target |
| `simulated_circuit` | Quantum circuit/noise-simulator target |
| `recorded_optical` | OSNR, BER, power, failure or other optical label |
| `measured_quantum` | Quantum measurement such as QBER, visibility or tomography |

## 3. Loop integration

| Value | Meaning |
|---|---|
| `offline` | Ordinary offline training/evaluation |
| `offline_replay` | Recorded telemetry replays a decision/simulation loop |
| `hardware_in_the_loop` | A live component participates during evaluation |
| `physical_closed_loop` | The evaluated controller actuates a physical quantum system |

Examples in this repository:

| Experiment | Data | Target | Loop |
|---|---|---|---|
| Synthetic WDM fidelity forecast | synthetic | simulated circuit | offline |
| Sant'Anna OSNR/BER/failure benchmark | recorded hardware | recorded optical | offline |
| Bristol WDM driving quantum simulation | recorded hardware | simulated circuit | offline replay |
| IrelandQCI/Toshiba coexistence sweeps | recorded hardware | measured quantum (QBER/SKR) | offline |
| Bristol HCF coexistence switches | recorded hardware | measured quantum (QBER/SKR) | offline |
| Dryad STIKE 168-hour series | recorded hardware | measured quantum (QBER) and communication rate | offline |

The last row is a hybrid or real-telemetry-replay experiment. It is not
hardware-in-the-loop and does not validate quantum fidelity.

## Legacy validation checks

`ValidationLevel` still records whether a result was checked internally,
against an analytical model, Qiskit Aer, synthetic telemetry, real telemetry,
hardware-in-the-loop or a physical experiment. This is a check type, not a
maturity score.

The project currently has:

- simulated/analytical/Aer validation for the quantum layer;
- recorded optical validation through the Sant'Anna adapter and benchmark;
- measured QBER and secure-key-rate outcomes under controlled classical
  optical-signal injection through the IrelandQCI/Toshiba dataset;
- no measured Bell-state fidelity, entangled-pair yield, quantum-memory state,
  purification output or repeater closed loop;
- no live hardware-in-the-loop;
- no physical quantum-repeater experiment.

The primary Sant'Anna campaign uses purged expanding-origin folds, a separate
chronological device holdout and paired moving-block-bootstrap comparisons
against persistence. These strengthen the evidence *within* the
`recorded_hardware / recorded_optical / offline` cell; they do not move the
experiment into a quantum-target or hardware-in-the-loop cell.

The frozen HardFailure-to-SoftFailure confirmation stays in that same cell and
tests a different attenuation regime on the same testbed. None of its six
predeclared superiority hypotheses was confirmed. This is evidence against
cross-regime generalization of the discovered models, not evidence about a
different physical link and not quantum validation.

The incremental WDM-information campaign also stays in this cell. It is a
stronger test of the optical prerequisite because it conditions on target
history and includes a temporal misalignment control, but its exploratory
HardFailure result was negative (0/16 joint criteria). This prevents recorded
optical forecastability from being substituted for evidence that WDM carries
incremental or quantum-predictive information.

The QKD coexistence analysis occupies the
`recorded_hardware / measured_quantum / offline` cell. It establishes a
controlled dependence of real QKD outputs on a classical coexistence
condition, but the rows have no timestamps and the publisher does not include
Bell fidelity. It therefore supports the physical middle link of the thesis,
not the temporal Bell-fidelity forecasting claim. The Bristol replay occupies
the separate `recorded_hardware / simulated_circuit / offline_replay` cell;
combining the two cells in prose must not be described as one end-to-end
hardware validation.

The Bristol HCF switch analysis occupies the same
`recorded_hardware / measured_quantum / offline` cell but has a different
design: ordered on/off chunks, three exposed links and three simultaneous
unexposed links. Its switch-pair difference-in-differences strengthens the
localized real-QKD coexistence evidence. Missing timestamps, per-chunk
BER/OSNR/power and Bell fidelity still prevent a temporal WDM-to-future-
quantum prediction claim.

The cross-platform replication campaign remains in the
`recorded_hardware / measured_quantum / offline` cell. It raises confidence in
the direction of the coexistence association because both independent studies
must pass a partial-conjunction test. It does not raise the integration level,
create timestamps or supply WDM telemetry. High two-study heterogeneity also
prevents a common-magnitude claim.

The Gavignet DWDM audit remains in the same provenance/target/integration cell
but at a lower **published-aggregate** resolution. It adds a third platform to
the descriptive direction and exposes a classical/quantum power tradeoff. It
does not enter the two-study partial-conjunction statistic because the public
paper does not provide measurement-level independent units, uncertainty or a
timestamped telemetry stream.

The Dryad STIKE source is also `recorded_hardware / measured_quantum /
offline`, but its target is QBER plus information/communication rate in a
quasi-QSDC protocol, not QKD SKR or fidelity. Relative hourly order supports a
small own-history forecasting baseline. With no WDM telemetry, it cannot
occupy the incremental WDM-to-quantum prediction cell. The public file
endpoint denied anonymous download during the reference phase, so this row
currently denotes a validated adapter/protocol rather than an executed result.

The new measured-quantum prediction campaign also occupies
`recorded_hardware / measured_quantum / offline`, but only after the strict
paired source validates timestamps, same-link/same-clock synchronization and
measured target provenance. Its rolling-origin and campaign/device holdouts
increase predictive generalization evidence within that cell; they do not
turn an observational forecast into physical closed-loop or causal evidence.
The current public releases do not satisfy this source contract, so the cell
contains a protocol and executable validator rather than a completed result.

`validation_taxonomy.PROJECT_PROVENANCE_LEDGER` stores these coordinates.
Unqualified terms such as `real-time`, `hardware-ready`, `physically
validated`, `causal`, `energy-efficient` and `deployable` remain subject to
the documentation audit.

## Formal TCC claim registry

The final scientific wording is additionally controlled by
[`thesis/tcc_claim_registry.json`](thesis/tcc_claim_registry.json). It keeps
the original temporal hypothesis separate from the narrower measured claim,
records negative capability limits and maps every status to product behavior.
Run `python experiments/validate_tcc_claims.py` to verify artifact hashes,
row selectors and expected statistical values before publishing a report or
starting the dashboard.
