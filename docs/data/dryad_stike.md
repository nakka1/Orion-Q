# Dryad STIKE 168-hour measured-quantum series

## Identity and scientific role

- Dataset DOI: [10.5061/dryad.n2z34tn70](https://doi.org/10.5061/dryad.n2z34tn70)
- Dryad version: 4, published 6 February 2025
- Licence: CC0 1.0
- Hardware: 104.8-km standard telecommunication fibre prototype
- Protocol: STIKE, a one-way quasi-QSDC protocol

This is not a conventional QKD telemetry release. It provides measured QBER
and information/communication rate, not secure key rate or Bell-state
fidelity. The 168 observations are consecutive one-hour averages. No absolute
timestamp, WDM BER, OSNR, optical power or wavelength-channel telemetry is
published.

Its role in QC-PEB is therefore limited but useful: it tests the temporal
evaluation machinery on a small real-quantum sequence and establishes how
much predictability is already available in the quantum target's own history.
It cannot test whether WDM adds incremental predictive information.

## Unit and integrity decisions

The accompanying paper reports an average QBER of 3.60%, an average real-time
information rate of 2.38 kbit/s and one point per hour. The adapter preserves
the published scalars as `qber_reported_percent` and
`communication_rate_reported_kbps`, then creates explicit derived SI fields:

- `qber_measured = qber_reported_percent / 100`;
- `communication_rate_bps = communication_rate_reported_kbps * 1000`.

It never renames communication rate to SKR. By default, it verifies the two
files against the SHA-256 digests exposed by the Dryad version-4 API:

- `QBER_168h.txt`: `0e13705f05285d848454d1f70625a16f3e61ea611e0838cd44eb37507eb6ddd5`
- `Rate_168h.txt`: `a71d900a37afaee6c54e3456d82a836c3e358d01c11018dfdd568340af49c47a`

## Access status

On 2 September 2026, the public landing page and metadata API were available,
but individual file endpoints returned HTTP 403 and API download endpoints
returned HTTP 401 to anonymous access. The public share link cited by the
paper resolved to the landing page but did not release a browser download.
Consequently the repository contains the strict adapter and tested campaign,
but no fabricated reference result. Raw files are not redistributed here.

When the two publisher files are available, run:

```bash
python experiments/run_dryad_stike_temporal_campaign.py \
  --dataset-dir /path/to/dryad-stike-168h \
  --output-dir outputs/dryad-stike-temporal
```

The campaign uses persistence, historical mean, own-target Ridge, bivariate
history Ridge and a bivariate control whose cross-target history is permuted
independently inside train and test. Rolling-origin folds are purged so every
training target precedes the first test input, and preprocessing is fit on
training rows only. Results remain exploratory because all folds come from
one 168-hour device campaign.
