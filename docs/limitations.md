# Limitations

Stated explicitly, per this project's own established discipline of
reporting negative results and honest caveats rather than hiding them.

## Discontinued IBM integration

IBM Quantum integration was removed from the active product on 2026-10-07.
Earlier IBM circuit-preparation, execution-boundary, operator, recovery and
readiness results used local doubles and never established measured hardware
performance. Their records remain historical; their adapters, runbooks and
Runtime dependency are no longer available as current integrations. Qiskit/Aer
continues to support local simulation without IBM account or network access.
The [retired source archive](../archives/retired_ibm_quantum_20261007/ibm_quantum_source.zip)
preserves the previous implementation and its historical records.

## Statistical rigor

- Most headline experiment comparisons in this project use a SINGLE
  seed by default. The WDM-vs-privileged central finding is the one
  exception, validated across 10 independent seeds specifically because
  it was flagged as needing that rigor (`run_wdm_vs_privileged_single_seed.py`).
  Other comparisons (Models B/D in the WDM-vs-privileged experiment,
  most controller comparisons beyond the 3-seed DualHead result) have
  NOT been validated at this level and should be treated as suggestive.
- No correction for multiple comparisons has been systematically applied
  across this project's many statistical tests.

## Energy and cost models

- Every constant in `energy_model.EnergyConfig` and
  `risk_aware_controller.RiskCostConfig` is an explicitly-labeled
  order-of-magnitude ESTIMATE, not a hardware measurement. Cryogenic
  cooling overhead (which typically dominates real superconducting-qubit
  system power) is deliberately excluded from the QPU energy estimate.
- Under this project's own default estimates, predictive control's
  classical inference overhead is NOT quite energy-justified even at
  DualHead's real ~68-85% halt rate (break-even needs ~6-8x more
  expensive QPU operations than the documented default) — reported
  honestly rather than reframed as a win.

## Architecture

- `src/quantum_twin/` now contains the production implementation. Some
  historical narratives and compatibility re-exports remain and should not
  be interpreted as separate production paths.
- `repeater_chain.py`'s legacy multi-hop model was NOT moved to
  `legacy/` alongside `dataset.py`, despite being conceptually
  superseded by `causal_chain.py` — flagged as a candidate for a future
  migration pass, not completed.

## Physical simulation

- The IrelandQCI/Toshiba release provides measured hardware QBER and secure
  key rate under controlled classical optical-signal injection. It does not
  provide Bell-state fidelity, entangled-pair yield, quantum-memory or
  purification labels, and it has no timestamps. It supports a controlled QKD
  coexistence effect, not temporal Bell-fidelity forecasting.
- QKD conditions were acquired as separate sweeps/sessions; the methodology
  mentions frequent alignment adjustments and does not document randomized
  order. Fibre/VOA matching cannot exclude residual session drift, so the
  result is exploratory controlled dependence rather than a randomized trial.
- DualHead/TriHead transfer on these sweeps is necessarily tabular and
  leave-one-fibre/VOA-stratum-out. It must not be described as temporal
  prediction: no LSTM axis or timestamp is invented. DualHead factorizes
  secure-key availability and conditional key rate; TriHead adds measured
  QBER, which still is not Bell-state fidelity.
- The publisher's QBER dictionary says `ratio (or %)`. The adapter preserves
  the published scalar as `qber_reported` and does not compute `1-QBER` or
  report percentage-point magnitudes until that scale ambiguity is resolved.
- The separate Bristol HCF release provides ordered on/off coexistence chunks,
  measured QBER/SKR and simultaneous unexposed control links. It does not
  provide absolute timestamps, per-chunk classical BER/OSNR/power or measured
  Bell fidelity. The README and paper describe aggregation duration
  differently, so the adapter preserves only relative chunk order.
- Bristol HCF switching alternated deterministically. The adjacent-pair
  difference-in-differences removes shared changes seen in `BC/BD/CD`, but
  cannot rule out every link-specific time-varying confounder. Its corrected
  positive QBER/SKR result is exploratory contemporaneous evidence, not a
  randomized trial or prospective forecast.
- The Bristol robustness families are post-inspection descriptive
  sensitivities. Directional stability and a 1/20 spatial rank strengthen the
  result, but the spatial tail fraction is 0.10 and exposure assignment was not
  randomized. Perfect period-2 On/Off alternation makes ordinary shifted-label
  temporal placebos non-identifiable.
- Cross-platform partial-conjunction analysis replicates the positive direction
  for QBER and `log1p(SKR)`, but it contains only two non-randomized studies.
  Heterogeneity is high (`I²=95.4%` and `79.9%`), both exploratory
  random-effects intervals cross zero, and no common or transferable effect
  magnitude is claimed.
- The structured public-data search found a third physical DWDM/QKD platform
  with matching QBER/SKR direction and paired condition-level BER/OSNR means.
  Those Gavignet values are approximate published aggregates, not public raw
  measurements. They carry no independent-unit uncertainty and are excluded
  from p-values, bootstrap intervals, the formal `k >= 3` claim and temporal
  model training. The search is an auditable rapid search, not a registered
  systematic review, and its absence conclusion is bounded by its frozen
  sources and 2026-09-02 snapshot.
- The Pareto campaign reuses predictions and sources that had already been
  inspected, so its frozen hypotheses remain exploratory. A fixed Pareto-aware
  loss raises dominance-pair accuracy to `0.5625` and beats its permuted-WDM
  control after six-comparison Bonferroni correction, but joint-front recovery
  remains low (`Jaccard=0.2298`) and superiority over the conventional TriHead
  is not corrected-positive. This is condition-ordering evidence, not temporal
  prediction or external validation. Gavignet's three points all lie on the four-objective front, but
  they are approximate condition means; normalized utilities and weight
  sensitivity are geometry/decision demonstrations, not calibrated operator
  preferences or population estimates.
- A post-discovery 20,000-replicate joint-label randomization keeps the
  Pareto-aware dominance result after Bonferroni (`p_adj=0.0118`), but not
  Pareto-set Jaccard. This is a falsification sensitivity, not independent
  confirmation. In leave-one-condition-out evaluation, neither TriHead beats
  the within-stratum target-mean baseline and both are corrected-worse for
  conditional log-SKR. Current models must not claim unseen-power transport.
- A further post-discovery dose-order audit shows that a target-free monotonic
  power rule reaches `0.6784` dominance accuracy, above the Pareto-aware
  TriHead's `0.5625`; its corrected model-minus-baseline interval is
  `[-0.1966, -0.0329]`. Thus the alignment/permutation result does not establish
  learned joint structure beyond the simple dose direction. This refutes the
  residual-ordering claim for the evaluated models, not the measured physical
  coexistence effect.
- A raw-reading IrelandQCI audit resamples VOA strata and within-cell circular
  blocks of 1/5/10 observations. Both primary degradation intervals remain
  positive, and deletion of any stratum or either fibre configuration preserves
  direction. This robustness is conditional on the ten published sweep files:
  each distance/condition has exactly one session, so condition and session
  effects are not separable and campaign-clustered confirmatory inference is
  unavailable.
- The Dryad STIKE release is a 168-hour quasi-QSDC prototype sequence, not a
  conventional QKD/WDM campaign. It contains measured QBER and communication
  rate but no SKR, WDM telemetry, fidelity or absolute timestamps. Its public
  download endpoint rejected anonymous access during the reference phase; no
  synthetic values are substituted for a missing real run.
- The Bristol dataset provides real, timestamped, multivendor WDM telemetry
  but no quantum label. Its replay fidelity comes from the original Aer model
  after inserting recorded BER into the phenomenological
  `DEPOLARIZATION_P + KAPPA_DEPOL_FROM_BER * BER` equation. The direct
  BER/fidelity correlation is imposed, and `KAPPA_DEPOL_FROM_BER` is not
  hardware-calibrated.
- In the Bristol non-circular test, 0/8 model/horizon combinations passed both
  the autoregressive and temporal-permutation controls. EdgeTCN beat target
  history alone at both horizons, but it was worse than the permuted-WDM
  control, so those gains are not evidence that aligned cross-channel WDM
  carries predictive information about future simulated fidelity.
- The Sant'Anna campaign contains only two transponder directions on one
  documented testbed/link. Expanding-origin folds measure temporal
  generalization inside that campaign, while leave-one-device-out measures a
  narrow direction/device transfer; neither establishes generalization to a
  different link, vendor, topology or operational network. Strong temporal
  autocorrelation is handled with purging and moving-block bootstrap but is not
  eliminated.
- A blank Sant'Anna `Failure` cell is unknown by default. Failure forecasting
  requires an explicit blank-as-normal convention; conclusions under that
  convention must not be described as independently verified event labels.
- HardFailure-selected models did not beat persistence on the independent
  SoftFailure campaign at any of the six preregistered nontrivial horizons.
  The within-HardFailure gains therefore do not establish cross-regime
  generalization. Post-hoc SoftFailure rankings are exploratory and cannot be
  substituted for the failed frozen hypotheses.
- In the dedicated incremental-information analysis, aligned WDM features did
  not improve on the target's own history for any of 16 exploratory
  model-target-horizon combinations, including optical adaptations of the
  original EdgeLSTM, EdgeGRU and EdgeTCN. These Sant'Anna results therefore do
  not validate the assumption that WDM telemetry adds out-of-sample
  predictive information, much less that it predicts a quantum target.
- HardFailure-calibrated conformal intervals covered nearly every SoftFailure
  point only because they were very wide (for example, roughly 34 dB total
  width for the horizon-10 OSNR model). Under this campaign shift,
  exchangeability is not assumed and nominal coverage is only a diagnostic.
- BBPSSW controller experiments assume two Werner pairs. When a second
  fidelity is not supplied, two identical-fidelity inputs are used and that
  assumption is logged per event.
- The NISQ output correction applies an effective two-CNOT depolarization and
  thermal gate-age map. It is not a device-calibrated gate/readout model.
- The WDM-to-quantum causal coefficients remain phenomenological until
  calibrated against an optical propagation model and paired quantum data.
- The strict paired-QKD campaign is implemented but has no qualifying public
  input in this repository. The Bristol HCF release improves the ordered
  measured-QKD evidence but still lacks its required timestamps and WDM
  telemetry vector; the Gavignet aggregate also lacks measurement-level time.
  The campaign will abort rather than join unrelated
  optical/quantum datasets or accept simulated fidelity as experimental
  evidence.
- The closed-loop multi-hop environment's 0% success rate beyond 1 hop
  is a property of its simple sequential-swap design (no retry/gating
  logic) — NOT a claim that multi-hop quantum repeaters are
  fundamentally infeasible; `causal_chain.MLGatedCausalSwappingChain`
  demonstrates the opposite given appropriate per-hop retry logic.
- `ThreeStateController`'s WAIT action (kept unchanged for backward
  compatibility) still uses a single-shot decoherence ESTIMATE, not the
  genuine multi-tick loop `environment.py` now supports separately.
- The control-plane topology is a local three-node SQLite-backed simulation,
  not a discovery of physical nodes or links. Scenario pair injection is
  synthetic, topology transactions are process-local, and a completed scenario
  is not evidence of WDM prediction, hardware safety or autonomous operation.
- The v5.24 measured-WDM dashboard lane is observational. Its registered
  Sant'Anna temporal result is negative (0/16 joint tests), it contains no
  quantum target, and it cannot authorize PURIFY/SWAP. The QKD lane contains
  measured QBER/SKR but no synchronized temporal WDM vector or Bell fidelity.
  The Bell control lane remains simulated; no conversion among these metrics
  is scientifically or programmatically permitted.
- The v5.25 paired controller campaign is also simulated. Its registered
  operational comparison was negative (0/3 candidates beat persistence after
  Holm correction): calibrated mean sigma exceeded the 0.20 demonstration
  safety limit for persistence and every neural controller, so all four made
  zero purification attempts. The DualHead's lower descriptive MAE is not an
  operational-gain result. Independently, the four-action `PolicyEngine`
  selected only `SWAP` in all 60/60 in-distribution fixed-state audits. Until
  action feasibility is made task-dependent and evaluated on fresh seeds, the
  policy cannot demonstrate sensitivity to predictor quality. Model-based
  latency/energy totals are assumptions, not hardware measurements.
- The v5.26 remediation fixes task semantics but does not rescue the scientific
  WDM claim. On ten fresh simulated seeds, 0/3 neural controllers significantly
  beat persistence after Holm correction; all three mean normalized-utility
  differences favored persistence. The normalized weights are research
  choices, not physical costs.
  Twenty-three of 60 ID controller-seed rows still used only one held-out
  action, now due to model/distribution behavior rather than unconditional SWAP
  competition. All neural controllers abstained completely under doubled
  distance. The calibration-quantile uncertainty ceiling enforces a relative
  budget on calibration data; it is not a hardware safety limit.
- The v5.27 frozen bundle and dashboard replay improve reproducibility and
  product safety, not scientific validity. The bundled DualHead was trained on
  simulated WDM and predicts simulated Bell fidelity. Its holdout trace is also
  simulated, the displayed actual target is retrospective only, and the panel
  must retain the v5.25/v5.26 `0/3` registered neural results. A valid bundle
  hash proves artifact integrity, not predictive efficacy or hardware fitness.
- Historical v5.28–v5.33 IBM results were offline software checks only.
  Seeded fake calibration values and local backend/Sampler/job doubles do not
  measure processor or photonic-link fidelity. The IBM integration is now
  discontinued, so its former hardware run, recovery and readiness steps are
  not current product capabilities or outstanding roadmap gates. The earlier
  simulated Bell controls do not validate temporal WDM-to-QBER/SKR/Bell
  prediction, which still requires synchronized physical measurements.
- The v5.34 Windows executable is a local control-plane sandbox. Packaging the
  dashboard, model and Qiskit/Aer runtime does not create a physical device
  adapter, vendor authorization, hardware redundancy or synchronized
  WDM–quantum validation. It binds only to loopback and mutates only the
  simulated topology and local SQLite journal. The unsigned research binary
  may trigger Windows reputation warnings and is not a production installer.

## Common security qualification (v5.50)

Local Windows software tests exercise OS identity, explicit backend roles and
resources, access-token verification, certificate revocation, TLS 1.3, DPAPI,
authenticated audit and classical messages, equipment authority and conservative
key delivery. These results do not qualify an institutional IdP/CA, a remote OGS,
physical interlocks, measurement calibration or experimental QKD security.
Emulated material remains TEST and yields zero certified secret bits. Restoring
SQLite preserves consumption through an independent protected journal; restoring
the journal, vault and database together requires an external rollback anchor.
Versioned vault rotation does not automatically re-encrypt existing AES-GCM
ledgers. See [the security runbook](product/orion_q_security.md) for deployment
boundaries, explicit provisioning and evidence.

## Uncertainty calibration

- Once ensemble sigma is honestly calibrated (fixing 1-sigma coverage
  from 4% to ~68%), it becomes too wide for `ThreeStateController`'s
  confidence-interval rule to ever commit to PURIFY or HALT at practical
  thresholds — an unresolved tension between statistical honesty and
  decision usefulness, not swept under the rug (see `docs/uncertainty.md`).

## Never claim more than was measured

Nothing in this project's documentation should be read as asserting
"real-time", "production-ready", "hardware-validated", or
"energy-efficient" without the specific experiment backing that claim
being named. Every number in `README.md` and `docs/` traces back to a
specific script and a specific run; see `docs/history.md` for the full,
unabridged account of what was actually measured, including every
negative result.

## Physical generalization has NOT been established (domain shift)

`run_domain_shift_experiment.py` (zero-shot, no retraining/fine-tuning)
found the model's OOD behavior is regime-specific and in some cases
counter-intuitive: excluding T1/T2 from inputs made T1/T2-shift
generalization WORSE (the underlying F(t) distribution itself shifts,
which WDM-only features cannot fully anticipate), but made
distance-shift generalization BETTER (the full-feature model over-relies
on T1/T2 in a way that transfers poorly to a new distance/loss profile).
A methodological confound (MinMaxScaler producing out-of-[0,1]-range
inputs under a T1/T2 shift) was found and explicitly disentangled before
drawing conclusions — see `docs/history.md`'s forty-ninth addendum for
the full investigation. **No claim of physical generalization is made.**

## Formal validation/realism taxonomy

See [`docs/validation_levels.md`](validation_levels.md) for the formal
data-provenance, target-provenance and loop-integration axes, the legacy
`RealismLevel`/`ValidationLevel` taxonomy, and
`validation_taxonomy.py`'s callable `audit_text_for_banned_terms()` —
which found and fixed one real violation in this project's own README
("real-time" claimed without evidence) during the sixty-first addendum.
