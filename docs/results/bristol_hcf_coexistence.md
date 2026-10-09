# Bristol HCF real-QKD coexistence result

## Question and estimand

Does switching documented classical DWDM coexistence on degrade simultaneous
real QKD performance on exposed Alice links more than it changes the three
unexposed links?

The unit of analysis is one complete adjacent On/Off switch pair, not an
individual link or repeated model seed. Within each pair:

- QBER degradation is `On - Off`;
- SKR degradation is `Off - On` on the `log1p(bit/s)` scale;
- the primary difference-in-differences (DiD) subtracts the mean degradation
  of `BC/BD/CD` from the mean degradation of `AB/AC/AD`.

Inference uses a two-sided exact paired sign-flip sensitivity test, a
switch-pair bootstrap interval and Bonferroni correction across the two
primary outcomes. The corrected simultaneous confidence level is 97.5% for
each outcome.

## Result

| Measured outcome | Complete pairs | Mean DiD degradation | Corrected 97.5% CI | Adjusted p | Positive pairs | Result |
|---|---:|---:|---:|---:|---:|---|
| QBER proportion | 16 | 0.015266 | [0.013462, 0.017049] | 0.0000610 | 16/16 | corrected-positive |
| `log1p(SKR bit/s)` | 17 | 0.106588 | [0.068682, 0.141179] | 0.0001526 | 15/17 | corrected-positive |

Both predeclared outcome families are positive after multiplicity correction.
The three unexposed links are important: they remove changes common to the
whole network at the same stage of the sequence instead of attributing every
on/off difference to classical coexistence.

## Robustness and falsification audit

The follow-up QC-PEB audit found both target directions positive within every
contiguous published segment, after removing any segment, after removing any
one physical link, and after removing any one complete switch pair. The QBER
segment means ranged from `0.011825` to `0.016804`; the `log1p(SKR)` segment
means ranged from `0.097187` to `0.148006`. The documented Alice exposure
group ranked first among all 20 possible three-link groups for both targets.

These are post-inspection descriptive sensitivities, not new confirmatory
tests. The spatial two-sided tail fraction is `0.10` and must not be called a
randomization p-value. A temporal shifted-label placebo is invalid here: lag 1
flips the perfectly alternating treatment labels and lag 2 recreates them.
The executable audit rejects that control explicitly. Full tables and command
are recorded in [`qc_peb_phase1.md`](qc_peb_phase1.md).

## Scientific interpretation

This is the strongest real-hardware bridge currently available in the
repository for the proposed physical mechanism. It supports the narrower
claim that a real, documented classical optical coexistence condition carries
information about simultaneous real QBER and SKR, and that the response is
localized to the links sharing the classical path.

It does not establish the stronger thesis claim that multivariate WDM
telemetry predicts *future* measured quantum fidelity. There is no per-chunk
BER/OSNR/power vector, no measured Bell-fidelity series and no absolute
timestamp. The on/off sequence alternated deterministically rather than being
randomized; control links reduce shared drift but cannot eliminate every
link-specific time-varying confounder. The analysis is exploratory because the
data were inspected before this protocol was frozen.

The defensible evidence ladder is therefore:

1. simulation: WDM-derived variables predict simulated future fidelity;
2. IrelandQCI/Toshiba: controlled optical injection changes real QBER/SKR;
3. Bristol HCF: an ordered coexistence switch changes exposed real QBER/SKR
   relative to simultaneous control links;
4. still missing: synchronized multivariate WDM telemetry and future measured
   quantum targets from an independent campaign.

## Reproducibility

Reference command:

```bash
python experiments/run_bristol_hcf_coexistence_campaign.py \
  --dataset-dir data/raw/bristol-hcf-coex/61193flfovbn2puknndsr3qm5 \
  --output-dir outputs/bristol_hcf_coexistence_measured \
  --bootstrap-replicates 50000

python experiments/run_bristol_hcf_robustness_campaign.py \
  --dataset-dir data/raw/bristol-hcf-coex/61193flfovbn2puknndsr3qm5 \
  --output-dir outputs/bristol-hcf-robustness \
  --bootstrap-replicates 50000
```

Key reference artifact hashes:

- canonical telemetry: `281d3e6e824226c059941284fd02214cc48ee1e9a943e484ea9069d7131691b7`
- link-pair effects: `00d78aaf4e0093891008290af965d9103c76a86e2085530133b7c00f692beb9d`
- switch-pair effects: `9f7e99636f551e3551444b6635a5cf23787385182cf0eb741e5b2b280339577f`
- primary results: `2473e1c0c959b8bc423db51c810fd7359b91f70689e715864732d725b381c882`
- report: `e31abe89193c81c4cac7a1575ceaa7fc3e4a21bb2a2942b391c4fbb034927a0e`
