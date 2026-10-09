# Data card: Sant'Anna Optical Failure Dataset

## Source and intended use

- Source: `Network-And-Services/optical-failure-dataset` on GitHub.
- Origin: ARNO optical testbed, Scuola Superiore Sant'Anna.
- Intended project use: external validation of optical OSNR/BER/failure
  forecasting and calibration of the optical layer.
- Not suitable as a measured label source for Bell fidelity, quantum-memory
  T1/T2, entanglement arrival or purification output.

Confirm licensing with the dataset authors before redistributing raw files.
The repository is fetched separately and raw CSVs are not vendored here.

## Raw schema

`Timestamp`, `Type`, `ID`, `BER`, `OSNR`, `InputPower`, `OutputPower`,
`Failure`.

The table is long format:

- `Devices` rows contain BER/OSNR;
- `Infrastructure` rows contain amplifier powers;
- multiple devices share or nearly share one timestamp;
- the documented acquisition cadence is approximately 3.5 seconds.

## Canonicalization decisions

- Unix seconds become timezone-aware `timestamp_utc`.
- `ID` is preserved as `device_id`.
- the entity key is `timestamp_utc + device_id`.
- structural missingness is preserved and masked.
- BER is never imputed to zero; values at/below a configurable measurement
  floor are marked as censored.
- blank `Failure` is unknown by default. Conversion to normal/zero requires
  `blank_failure_is_normal=True` and is recorded in provenance metadata.
- direction remains `unknown`; it is not inferred from device names.
- amplifier input/output powers remain distinct and are not relabeled as
  span/fiber loss without topology evidence.
- forecasting panels use backward-only device alignment. The source timestamp
  and age of every aligned sensor value are retained; nearest/future samples
  are prohibited.

## Known limitations

- Failure events are testbed interventions, not natural production failures.
- Only two transponder directions and four amplifiers are present.
- observations within a link are strongly autocorrelated;
- missingness is structural and device-dependent;
- no paired quantum measurements are provided;
- generalization must be evaluated on a separate campaign, device, link or
  external dataset.

## Required experiment reporting

Record raw file SHA-256, adapter version/Git commit, blank-failure policy,
BER floor, device IDs, chronological split timestamps, purge gap, random
seeds, dependency versions and whether the worktree was dirty.

The primary TCC campaign must additionally report:

- every forecast horizon, lag and trailing-window length;
- expanding-origin fold boundaries and the effective embargo;
- temporal and leave-one-device-out results as different evaluations;
- persistence beside every learned/filtering method;
- paired moving-block-bootstrap interval and block length;
- multiple-comparison correction and the predeclared winner rule;
- transformed BER units (`log10(BER)`) rather than presenting that error as
  raw BER;
- all negative comparisons, not only the best candidate.

Repeated model seeds are computational repetitions of the same physical
observations. Their losses are averaged per observation before the block
bootstrap; they do not increase the recorded-data sample size.

To test whether WDM features carry incremental information, target history
must be an explicit baseline. A combined target-history + WDM model must beat
both that autoregressive baseline and a control with WDM windows permuted
within device/split. Generic superiority over persistence is insufficient for
this claim.

For cross-campaign confirmation, use the checked-in frozen protocol rather
than selecting another model after viewing SoftFailure. HardFailure uses random
attenuation plus periodic 25 dB hard events; SoftFailure uses a sinusoidal
attenuation path plus periodic 10 dB soft events. They are separate regimes on
the same testbed, not different links or vendors.
