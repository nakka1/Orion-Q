# Structured search for public paired QKD/coexistence data

## Scope and frozen criteria

This is an auditable rapid search, not a registered systematic review or a
PRISMA claim. The snapshot date is 2026-09-02. The search covered Zenodo,
Dryad, Figshare, Mendeley Data, GitHub, arXiv source archives, Europe PMC/PMC
and publisher supplements using combinations of `QKD`, `QBER`, `SKR`, `WDM`,
`coexistence`, `BER`, `OSNR`, `telemetry`, `time series`, `dataset` and
`source data`.

A new source is eligible for formal third-platform effect replication only if
it provides all of the following:

1. measured QBER and measured secure key rate;
2. a documented classical optical baseline, exposure or dose;
3. public machine-readable measurement-level values;
4. documented independent experimental units;
5. reusable data terms and primary-source provenance;
6. hardware independent of the IrelandQCI and Bristol HCF studies.

The stronger temporal claim additionally requires ordered multivariate WDM
telemetry synchronized to a future measured quantum target. Figure pixels,
repeated points from one condition session, expected/simulated SKR and data
available only by contacting authors are excluded from formal inference.

The executable version of these rules is in
`public_qkd_evidence.validate_public_qkd_candidate_registry`. The frozen
protocol and decisions are emitted as `search_protocol.json` and
`candidate_registry.csv`.

## Candidate audit

| Candidate | Public resolution | Decision | Principal reason |
|---|---|---|---|
| IrelandQCI/Toshiba | measurement-level CSV | already integrated | no timestamps/WDM vector |
| Bristol HCF | ordered measurement chunks | already integrated | no per-chunk WDM vector |
| Gavignet 2024 DWDM | condition means in prose/figures | aggregate only | no numeric source measurements or independent-unit metadata |
| Stathis 2026 last-mile | five values in Table 1 | aggregate only | underlying data request-only |
| Wu 2025 field MCF | published curves | excluded from numeric integration | data/code request-only |
| Maragkopoulos 2024 diagnostics | reported QBER/SKR time series | excluded | data/programs request-only and no WDM vector |
| Wang 2023 time-interleaved C-band | figure PDFs | excluded | arXiv source has no numeric files |
| Dryad STIKE | hourly QBER/rate files | excluded from WDM test | no WDM and communication rate is not SKR |

Primary records:

- [IrelandQCI Zenodo dataset](https://zenodo.org/records/21132088)
- [Bristol HCF data collection](https://doi.org/10.5523/bris.61193flfovbn2puknndsr3qm5)
- [Gavignet et al. DWDM/QKD preprint](https://arxiv.org/abs/2312.11924)
- [Stathis et al. last-mile QKD article](https://doi.org/10.1038/s41598-025-34184-z)
- [Wu et al. field-deployed multicore-fibre article](https://doi.org/10.1038/s41377-025-01982-z)
- [Maragkopoulos et al. diagnostics article](https://doi.org/10.3390/e26110922)
- [Wang et al. time-interleaved C-band preprint](https://arxiv.org/abs/2304.13828)
- [Dryad STIKE release](https://doi.org/10.5061/dryad.n2z34tn70)

## Result of the search

Six new candidates were audited. None satisfies the formal third-platform
criteria, and none satisfies the strict temporal prediction contract. Two
provide usable public condition-level aggregates, so they remain external
corroboration rather than being discarded.

The absence result is bounded by the sources and snapshot above. It means “no
qualifying source located under this protocol,” not “no such data exist.”

Rebuild the registry with:

```bash
python experiments/build_public_qkd_evidence_search.py \
  --output-dir outputs/public-qkd-evidence-search
```

The tracked Gavignet transcription and its restrictions are documented in
[`../../data/reference/README.md`](../../data/reference/README.md).
