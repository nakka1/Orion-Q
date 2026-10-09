# Task-constrained policy and abstention remediation (v5.26)

## Result in one sentence

The policy-semantics defect found in v5.25 is fixed and the remediated policy is
responsive in all registered sensitivity probes, but **none of the three WDM
neural controllers significantly beats persistence** on the fresh held-out
simulated seeds.

This remains simulated WDM-to-simulated-Bell-fidelity evidence. It is not a
test of synchronized measured WDM and a measured quantum target, and no
physical action is possible.

## What changed

The legacy `PolicyEngine` remains available as a compatibility control. The new
`TaskConstrainedRepeaterPolicy` requires one of two mutually exclusive tasks:

| Task | Permitted action set | Decision input |
|---|---|---|
| `link_purification` | HALT, WAIT, PURIFY | forecast distribution for the future raw same-link pair; stored pair values establish resource count only |
| `path_extension` | HALT, WAIT, SWAP | exactly two explicit stored link-pair fidelities; the candidate WDM prediction is ignored |

`PURIFY` and `SWAP` can therefore never compete in the same decision. More than
two unselected pairs fail closed as ambiguous; one pair produces `WAIT`, and no
pair produces `HALT`.

The campaign also corrected the operational target. A desired purified output
fidelity of 0.65 does not require a raw input fidelity of 0.65: analytical
BBPSSW reaches 0.65 output at **0.625 input**. The decision policy now computes
this inverse boundary from the existing BBPSSW implementation. This restores
the future-fidelity oracle as a genuine operational upper-bound diagnostic:
on the held-out ID trace it has no false purification and misses no pair capable
of producing a useful output.

## Predeclared calibration and fresh test

The registered configuration is
[`experiments/configs/wdm_policy_remediation_v526.json`](../../experiments/configs/wdm_policy_remediation_v526.json),
with canonical SHA-256
`25cf70682929d7f51dc194a4a54978de589dbb79fb3b289847cba78281fbea7c`.

Its ten confirmatory evaluation seeds (`809, 811, 821, 823, 827, 829, 839,
853, 857, 859`) are disjoint from every v5.25 seed and from the ten v5.26
methodology-debug seeds. The debug run exposed the raw-input versus purified-
output target mismatch; after correcting that semantic defect, those observed
seeds were prohibited from the final evaluation rather than reused. For each
confirmatory seed and controller:

1. the model is fitted on the temporal fit prefix;
2. prediction uncertainty is calibrated on a later calibration prefix;
3. the safety-uncertainty limit is the calibration-only quantile implied by a
   maximum 20% high-uncertainty abstention budget;
4. the probability margin is selected from a fixed grid by normalized utility,
   subject to a 35% policy-WAIT budget;
5. the complete policy spec and calibration inputs are hash-bound;
6. only then is the held-out suffix evaluated.

All 60 controller-seed policy specs respected both calibration budgets. The
utility weights are explicit normalized research choices, not Joules, money or
hardware-calibrated costs: useful output `+1.00`, false purification `-0.25`,
missed useful output `-0.50`, WAIT `-0.01`, and purification attempt `-0.01`.
No weight, threshold or winner was selected from the held-out results.

## Registered primary result

The primary metric is normalized net utility per available-pair arrival on the
ID held-out suffix. Tests are exact paired seed-level sign-flip tests against
persistence, with Holm correction over the three neural candidates.

| Candidate versus persistence | Mean difference | Raw p | Holm p | Positive and family-wise significant |
|---|---:|---:|---:|---|
| EdgeLSTM point | -0.01984 | 0.6494 | 1.000 | No |
| EdgeLSTM probabilistic | -0.14041 | 0.9961 | 1.000 | No |
| EdgeLSTM DualHead | -0.20094 | 0.9873 | 1.000 | No |

Result: **0/3 registered comparisons were positive and significant**. All
three confirmatory mean differences favor persistence.

## Held-out ID scorecard

Values below are means across ten seeds; action counts are per 100 evaluated
cycles.

| Controller | MAE | Net utility / arrival | Useful output rate / arrival | Attempts | False attempts | Missed useful | Total safety/policy abstention |
|---|---:|---:|---:|---:|---:|---:|---:|
| Persistence | 0.290 | 0.0668 | 20.67% | 22.8 | 2.1 | 16.2 | 0.00% |
| Reactive | 0.290 | 0.0668 | 20.67% | 22.8 | 2.1 | 16.2 | 0.00% |
| EdgeLSTM point | 0.316 | 0.0470 | 22.28% | 36.5 | 13.9 | 14.3 | 43.32% |
| EdgeLSTM probabilistic | 0.257 | -0.0736 | 14.10% | 22.6 | 8.2 | 22.5 | 61.41% |
| EdgeLSTM DualHead | **0.245** | -0.1341 | 9.83% | 14.9 | 4.7 | 26.7 | 43.32% |
| Oracle future fidelity | 0.000 | 0.3639 | 36.97% | 36.9 | 0.0 | 0.0 | 0.00% |

The DualHead again has the best neural MAE but worse operational utility than
persistence. The point model obtains slightly more useful expected output by
attempting much more often, but also incurs substantially more false attempts;
its net utility is below persistence. Persistence and reactive are behaviorally
identical here because the selected probability margin is zero and persistence
has constant calibration sigma; this equivalence is reported, not interpreted
as proof that forecasting is unnecessary in general.

## Semantics and saturation audits

All **60/60** controller-seed sensitivity audits established that:

- PURIFY and SWAP are mutually exclusive by task;
- link-purification actions change when the owned forecast inputs change;
- path-extension actions change when the two link-pair fidelities change;
- path-extension is invariant to the unrelated WDM candidate prediction.

On the actual ID held-out samples, 23/60 controller-seed rows still produced a
single intended action; the legacy fixed-state audit produced 60/60. Because
the input probes differ, this contrast diagnoses behavior but is not an effect
size attributable solely to the remediation. The
remaining saturation is concentrated in the neural models (point 9/10,
probabilistic 9/10, DualHead 5/10); it is now a model/distribution behavior,
not unconditional competition with SWAP. The baselines and oracle each used at
least two actions in all ten seeds.

Abstentions are now reported in non-overlapping categories: OOD safety,
high-uncertainty safety, policy probability-WAIT and ordinary low-fidelity
HALT. This prevents a 100% resource saving caused by universal HALT from being
presented as an efficiency gain.

## Domain-shift warning

Under doubled link distance, every neural controller abstained on 100% of
available-pair arrivals and produced zero useful output. The explicit-pair
baselines did not. Under fivefold depolarization, neural useful-output rates
remained between 0% and 1.21%. The remediation therefore fixes policy
semantics but does not establish physical or domain-shift generalization.

## Scientific conclusion

The v5.26 result supports three limited claims:

- the prior SWAP saturation was a correctable policy-design defect;
- using the correct post-purification target changes controller evaluation
  materially and is necessary for a valid oracle;
- among the current simulated WDM models, lower forecast MAE still does not
  imply statistically supported control utility.

It does not support a measured WDM-to-quantum claim, automated operation, or
selection of any neural model for deployment. The next product increment may
package a model for **read-only research replay**, but must show this negative
evidence and keep automatic control disabled.

## Reproduction and artifacts

```powershell
$env:PYTHONPATH = "$PWD\src"
python experiments/run_wdm_policy_remediation_campaign.py `
  --output-dir outputs/wdm_policy_remediation_v526
```

The local result directory includes per-cycle records, scorecards, exact tests,
frozen policy specs, sensitivity audits, model/dataset lineage, report and
artifact manifest. Generated outputs remain ignored by Git. The completed
report SHA-256 is
`683609f660f8e976658d8f06b10f7b549aefdfe3b39d8266f002a5519129ecb3`.
