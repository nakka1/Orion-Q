# Product path: QKD Predictive Assurance

## Product claim today

The defensible first product is an offline/near-line QKD coexistence assurance
layer: ingest classical optical telemetry and measured QKD KPIs, validate their
provenance and synchronization, detect operating regimes in which classical
channels degrade QBER/SKR, and estimate whether WDM adds forecast value beyond
the QKD KPI's own history.

It is not yet a Bell-fidelity predictor or autonomous quantum-repeater
controller. Those become product claims only after paired physical
Bell-fidelity or repeater measurements pass the same external validation.

## Scientific asset already present

The project now contains five deliberately separated evidence tracks:

| Track | Input / target | Current scientific value |
|---|---|---|
| Synthetic twin | simulated WDM / simulated fidelity | Mechanism and controller prototyping |
| Recorded optical | real WDM / real optical KPI | Leakage-safe external optical tests; current incremental result negative |
| Controlled QKD coexistence | real injected optical condition / measured QBER-SKR | Corrected-significant physical coexistence degradation |
| Ordered HCF coexistence | real on/off optical condition / measured QBER-SKR | Corrected-positive exposed-vs-control switch-pair effect |
| Strict paired campaign | synchronized real WDM / future measured QBER-SKR | Implemented protocol; awaits a qualifying dataset |

The negative controls are product assets: they prevent a dashboard from
reporting autocorrelation, temporal misalignment or a hard-coded BER equation
as predictive quantum intelligence.

Version 5.24 makes the product-level separation executable through three
evidence lanes: recorded optical WDM, measured QKD assurance and simulated
Bell-fidelity control. See [`evidence_lanes.md`](evidence_lanes.md). A real WDM
recording can now be displayed without being silently promoted into either of
the two quantum target spaces.

The IrelandQCI design audit adds a required provenance state: `session
confounded`. Its hierarchical intervals are stable conditional on the ten
published sweeps, but every distance/condition has only one session. The API
and UI must therefore distinguish `measurement robust` from `independently
session replicated`; the former must never unlock causal wording or automated
actuation.

## MVP

1. **Collector/validator** — CSV/Parquet initially, then adapters for gNMI/
   OpenConfig and vendor QKD managers. Reject unit, time, provenance and raw-key
   violations before storage.
2. **Evidence engine** — temporal feature generation, purged rolling folds,
   campaign/device transfer, persistence/autoregression/permutation controls,
   corrected uncertainty and signed artifact manifests.
3. **Assurance API** — per-channel QBER/SKR risk vectors, forecast horizon,
   confidence/status, telemetry age, model/evidence version and Pareto status.
   No raw keys and no hidden collapse into a single health scalar.
4. **Operator UI** — coexistence heatmaps, telemetry alignment health, regime
   drift, threshold alerts and an evidence badge distinguishing measured,
   replayed and simulated outputs.
5. **Digital-twin sandbox** — keep the existing Qiskit controller experiments
   as a separate what-if module. Never merge its simulated fidelity with a
   measured-QKD product metric without an explicit provenance label.

## Pareto Guard decision layer

The Gavignet hardware aggregate exposes a product requirement that was not
visible in the original scalar-fidelity controller: increasing WDM launch
power can improve classical BER/OSNR while degrading quantum QBER/SKR.

The new Pareto audit therefore defines the decision layer around four separate
utilities:

- maximize classical OSNR;
- minimize classical pre-FEC BER;
- minimize measured QBER;
- maximize measured SKR or key availability.

All three published Gavignet multispan conditions are non-dominated. A simple
weighted sum selects only the low- or high-power endpoint and never selects the
valid middle compromise. The MVP should therefore return the complete
non-dominated set, apply explicit QBER/SKR safety constraints, and only then
rank feasible actions using visible operator policy. Weight changes must be
versioned and auditable.

Current model quality is not sufficient for automated operation. The
Pareto-aware TriHead improves dominance-pair accuracy from `0.4283` to
`0.5625`; against its loss-matched permuted-condition control the gain is
`+0.1750` with a familywise-corrected interval `[0.0168, 0.3111]`. A stricter
monotonic-dose audit shows that fixed power ordering reaches `0.6784` and is
corrected-better than the Pareto-aware TriHead (model-minus-baseline interval
`[-0.1966, -0.0329]`). The network therefore has not demonstrated ranking
value beyond the transparent physical prior. Pareto-set Jaccard also remains
only `0.2298`. Pareto Guard must begin as an evidence and decision-support
surface that displays the dose baseline as its primary reference, not as an
autonomous neural optimizer.

A subsequent unseen-condition gate reinforces that restriction. With each of
baseline/3/7/9/12 dBm removed in turn, neither TriHead beats a simple
within-stratum target-mean baseline after correction; both are materially
worse for conditional log-SKR. Product claims must therefore be restricted to
assurance within a calibrated operating grid. Extrapolation to unseen launch
power requires a new validation campaign and an explicit out-of-distribution
status in the API.

## Commercial moat

The Bristol HCF result strengthens a product discovery story—classical
coexistence state materially changes real QKD performance—but is insufficient
training data for a forecasting product. Its immediate product use is a
benchmark and an assurance-rule prototype, not a deployable predictive model.

The moat is not the LSTM architecture. It is the defensible paired dataset,
instrument adapters, traceable evidence protocol, cross-device/campaign
benchmark history and operational feedback loop. The acquisition contract is
therefore the highest-value next investment.

## Milestones and gates

| Milestone | Exit criterion |
|---|---|
| Data readiness | Two devices or campaigns; stable UTC sync; BER/OSNR/power + measured QBER/SKR; license permits intended use |
| Session identification | Repeated or randomized sessions for every key condition, allowing condition and session effects to be separated |
| Predictive validation | Corrected positive aligned-WDM gain over own-history and permuted controls on a held-out campaign/device |
| Pareto validation | Joint QBER/SKR frontier recovery exceeds frozen thresholds and both alignment and loss-ablation controls pass on a held-out campaign |
| Condition transport | Beat within-stratum target baselines when complete power levels are held out; reject or flag powers outside the validated grid |
| Pilot | Shadow-mode forecasts with telemetry-age and drift monitoring; no actuation |
| Decision support | Operator-accepted alert thresholds and measured false-alert/missed-event costs |
| Closed loop | Separate safety review and physical experiment showing an action improves a measured quantum KPI |
| Bell/repeater expansion | Measured synchronized fidelity/yield/memory outcomes; kappa calibrated train-only and externally replicated |

## Business model

A practical route is B2B software plus integration: paid testbed assessment,
on-premise assurance service, instrument/vendor adapters, and recurring model
monitoring. Keep data ingestion deployable on customer premises because QKD
performance telemetry and topology may be sensitive even when no key material
is stored.

## Licensing constraint

The IrelandQCI/Toshiba release is usable with attribution under its published
license; the Bristol release has academic/noncommercial constraints. Do not
bundle restricted raw data or derivative weights into a commercial image.
Ship code and schemas, require customers to connect data they are authorized
to use, and obtain a separate license for any redistributed benchmark corpus.
