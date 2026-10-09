# IrelandQCI/Toshiba measured QKD coexistence data

## Source and reuse

- Source: [Zenodo record 21132088](https://zenodo.org/records/21132088),
  DOI `10.5281/zenodo.21132088`.
- License: CC BY 4.0; attribution is required.
- Hardware: Toshiba QKD system under controlled laboratory sweeps.
- Downloaded release: ten measurement CSVs plus documentation/statistics.
- Integrity: the downloaded files used for the reference run were checked
  against every MD5 published by Zenodo; the adapter records local SHA-256
  hashes in each run report.

The data itself is not vendored in this repository. Download the Zenodo
release, preserve its directory structure, and pass that directory to the
campaign script.

## Experimental design

The release crosses two fibre configurations (0 and 50 km), eight VOA
attenuation levels per fibre, and five coexistence conditions: no injected
signal and external classical optical injection at 3, 7, 9 and 12 dBm. The
ten CSVs contain 3,893 repeated hardware readings. Aggregation by
fibre/VOA/signal condition yields 80 controlled cells and 16 matched
fibre/VOA strata.

The repository adapter is `QKDCoexistenceSource`. It emits:

| Canonical field | Meaning |
|---|---|
| `external_signal_present` | Baseline/injected condition |
| `external_signal_power_dbm` | 3/7/9/12 dBm; null for baseline |
| `fiber_length_km` | 0 or 50 km |
| `voa_attenuation_db` | Controlled optical attenuation |
| `qber_reported` | Measured QBER scalar exactly as published |
| `secure_key_rate_bps` | Measured secure key rate in bit/s |

The publisher's data dictionary labels QBER as `ratio (or %)`. Because that
does not identify one scale, the adapter does not silently convert the value,
does not compute `1-QBER`, and names the field `qber_reported`. Statistical
signs and randomization p-values are invariant to a positive scale conversion;
effect magnitudes must remain in “publisher-reported QBER scale” until the
curator resolves the unit.

These are genuine measured quantum-system outcomes, but neither QBER nor
secure key rate is Bell-state fidelity. The release also contains no
timestamps, so it cannot test temporal prediction. Each condition is a
separate sweep/session, the methodology mentions recurring system alignment,
and it does not document randomized acquisition order. Matching removes the
declared fibre/VOA differences but cannot exclude residual session drift.

## Reference analysis

```bash
python experiments/run_qkd_coexistence_campaign.py \
  --dataset-dir /path/to/zenodo-21132088 \
  --output-dir outputs/qkd_coexistence_measured \
  --bootstrap-replicates 50000 --seed 42
```

Repeated readings are averaged within each controlled cell before inference,
preventing pseudo-replication. The primary matched contrast compares the
average injected condition with baseline inside each of the 16 fibre/VOA
strata. Exact one-sided sign-flip tests and bootstrap intervals receive
Bonferroni correction. A separate leave-one-stratum-out Ridge analysis tests
cross-condition prediction and is not conflated with the controlled effect.

The raw-reading sensitivity can be reproduced separately:

```bash
python experiments/run_qkd_hierarchical_robustness_campaign.py \
  --dataset-dir /path/to/zenodo-21132088 \
  --output-dir outputs/qkd-hierarchical-robustness
```

It resamples VOA strata and circular blocks within each cell. This quantifies
measurement robustness conditional on the ten observed sweep files; it cannot
estimate session-level confounding because every distance/condition has only
one published session.

See [`../results/quantum_wdm_evidence.md`](../results/quantum_wdm_evidence.md).
