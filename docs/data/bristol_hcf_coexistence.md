# Bristol HCF measured-QKD coexistence data

## Why this source is included

This public University of Bristol release provides real quantum-network
measurements without requiring access to a QKD laboratory. It accompanies the
paper *Coexistence of Entanglement-based Quantum Channels with DWDM Classical
Channels over Hollow Core Fiber in a Four Node Quantum Communication Network*.

- Dataset DOI: [10.5523/bris.61193flfovbn2puknndsr3qm5](https://doi.org/10.5523/bris.61193flfovbn2puknndsr3qm5)
- Paper: [npj Quantum Information 11, 164 (2025)](https://www.nature.com/articles/s41534-025-01125-7)
- Declared dataset licence: Non-Commercial Government Licence. Review the
  licence before redistribution or product use.

The downloaded archive remains under `data/raw/`, which is ignored by Git.
The repository stores code, protocol and checksums, not a redistributed copy.

## What is actually measured

`4UserNetworkData/4U_OnOff.csv` contains an ordered four-user experiment with
six entanglement links (`AB`, `AC`, `AD`, `BC`, `BD`, `CD`). For each published
chunk it reports:

- measured QBER and its reported error;
- calculated secure key rate in bit/s;
- endpoint single-photon count rates;
- whether classical coexistence was on or off.

The paper describes four 200-Gbit/s 16-QAM classical channels and total
coexistence power of -3 dBm. The classical light coexists on the three Alice
links (`AB`, `AC`, `AD`); `BC`, `BD` and `CD` are contemporaneous unexposed
controls.

## What is deliberately not reconstructed

The release does not provide per-chunk classical BER, OSNR or power telemetry,
absolute timestamps, or a Bell-fidelity time series. `QBER` is not renamed to
or converted into Bell fidelity. The source README and article describe the
aggregation duration differently, so `relative_chunk_index` is retained only
as an order index; no duration or UTC timestamp is invented.

Consequently this source does not satisfy `PairedQuantumTelemetrySource`. It
supports a controlled, contemporaneous coexistence test, not the strict future
prediction campaign.

## Canonical adapter

`BristolHCFCoexistenceSource` converts the wide CSV to one row per chunk/link,
adds only documented or deterministic metadata, and validates:

- exactly six links per observed chunk;
- unique `(relative_chunk_index, link_id)` keys;
- a single on/off state per chunk;
- consistency of the Alice exposure group and its derived interaction;
- physical ranges and missingness declared by the schema.

The real file yields 228 rows across 38 observed chunks, three separated
sequence segments and one missing QBER value. The missing value is preserved;
it is not imputed.

## Reproduction

After downloading and extracting the public dataset:

```bash
python experiments/run_bristol_hcf_coexistence_campaign.py \
  --dataset-dir /path/to/61193flfovbn2puknndsr3qm5 \
  --output-dir outputs/bristol_hcf_coexistence_measured
```

The reference configuration is stored in
`experiments/configs/bristol_hcf_coexistence_reference.json`.

The separate robustness campaign tests segment, link and pair influence plus
all spatial three-versus-three assignments:

```bash
python experiments/run_bristol_hcf_robustness_campaign.py \
  --dataset-dir /path/to/61193flfovbn2puknndsr3qm5 \
  --output-dir outputs/bristol-hcf-robustness
```

Its sensitivities are descriptive because they were specified after examining
the public data. See [`../results/qc_peb_phase1.md`](../results/qc_peb_phase1.md).
