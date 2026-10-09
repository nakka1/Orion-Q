# Product and Experimental Roadmap

This roadmap separates product integration from scientific validation. A
completed software milestone never changes the evidence status of the central
WDM hypothesis by itself.

Console performance (5.51, 2026-10-07): [operating and measurement contracts](orion_q_performance.md) separate scientific processing from GUI presentation, keep hidden-view state current, bound inspection requests, and reuse map/globe geometry. [Reproducible evidence](../results/orion_q_performance_20261007.md) records full-topology profiling, alternatives, regression checks, and before/after measurements. This software increment preserves security and scientific acceptance boundaries; it does not qualify hardware or institutional deployment.

Security integration (5.50, 2026-10-07): [common security contracts](orion_q_security.md)
bind the native console, API, laboratory, acquisition adapters and key services
to backend identities, roles and resource scopes. Loopback API requests require
authentication. Revocable TLS 1.3 certificate policies, DPAPI-protected secrets,
authenticated audit checkpoints and key-consumption journals add software
acceptance for unauthorized access, tampering, replay and database restoration.
Institutional login/PKI deployment, external anti-rollback anchors, wrapping-key
migration of existing ledgers and physical/QKD qualification remain explicit
acceptance dependencies; these software tests do not close those milestones.

IBM Quantum integration was **discontinued on 2026-10-07** at the project
owner's request. IBM account/backend adapters, operator commands, diagnostics
and the optional Runtime dependency are outside the active product. IBM-2C,
IBM-3 and IBM-4 are cancelled milestones rather than outstanding acceptance
gates. Qiskit/Aer remains the local simulation engine; physical actuation remains
blocked. The [retired source archive](../../archives/retired_ibm_quantum_20261007/ibm_quantum_source.zip) preserves historical evidence
without implying current IBM support.

Globe zoom tolerance (5.48, 2026-10-07): [GEO operation](orion_q_geo_modes.md)
keeps the globe active up to zoom 5.8 and extends its camera range to 6.0.
Nine ordinary zoom-in scroll steps from zoom 1 retain the globe; the tenth
opens the world map with the same geographic focus. Map-entry hysteresis,
satellite selection and exclusive renderer lifecycle remain in place.

Laboratory hardening (5.47, 2026-10-07): [configuration and operation](orion_q_laboratory.md)
validate all parameters before application and determine availability per experiment.
Swabian configuration failure leaves acquisition blocked until verified readback
and recalibration. Registered adapters, observation/clock contracts and durable
continuous capture feed the same processing path. The laboratory journal uses
interprocess exclusion, transactional receipt publication and explicit interrupted
states after restart. Two KME and two application processes use separate stores,
real local mTLS and irreversible key-delivery reservations. BB84 estimation is
conditional on the declared emulated source and independently checked finite-block
assumptions; certified secret bits remain zero. [Public ephemerides](orion_q_public_ephemeris.md)
add asynchronous OMM acquisition and covered SGP4 predictions; they do not qualify
satellite housekeeping, quantum payload or physical links. The
[validation matrix](../results/orion_q_lab_hardening_20261007.md) distinguishes
local completion from SDK/license/FileWriter, proprietary-device and experimental
dependencies. Optional models remain off and physical actuation remains blocked.

Global network operations (5.46, 2026-10-07): [operation and assumptions](orion_q_global_network.md)
configure institutional endpoints with combinable protocol capabilities, durable
classical demands and locally admitted quantum services. Four explicitly simulated
GEO devices and four registered ISL undergo received engineering/TDC, geometry,
optical-budget and paired-channel validation. [ISL contracts](orion_q_inter_satellite_contract.md)
preserve the distinction between measured photons and stored relay resources.
The globe prioritizes satellite selection while ground infrastructure remains
accessible. A complete component/link matrix, refusal causes and failure/recovery
evidence are exported by the global-network diagnostic. Coherent orbital relay,
hardware qualification and certified global key delivery remain unimplemented;
no software milestone marks those capabilities complete.

Satellite bridge and GEO maintenance (5.45, 2026-10-07): [GEO operation](orion_q_geo_modes.md)
widens scroll hysteresis, preserves the cursor's geographic focus and clears the
transition raster with a bounded timer. The wrapping bottom legend identifies
backend component symbols, terrestrial link states, configured satellite
associations and received emission blocks. Floating satellite inspection uses
live engineering and joint-protocol observations. An additive, explicitly
hypothetical Atlantic MEO dual-downlink scenario exercises the existing OGS
pipeline between UFRJ and BSC; the unavailable long-distance LEO comparison
remains. [Bridge assumptions and contracts](orion_q_satellite_bridges.md) separate
software reception from field qualification and certified key material. The
[validation report](../results/orion_q_geo_satellite_bridge_20261007.md) records
operations, failure/recovery and native packaging. Optional models remain off
by default and physical actuation remains blocked.

Topology symbol maintenance (5.44, 2026-10-06): [GEO operation](orion_q_geo_modes.md)
uses a prominent bidirectional endpoint port, node/core hubs and compact repeater
marks from backend classifications. Fibres render as selectable lines without
midpoint squares; shared symbols extend to the globe, graph, matrix and node
inspection. [Visual validation](../results/orion_q_topology_symbols_20261006.md)
records the appearance and interaction checks. Physics and model gates are unchanged.

Communicator form correction (5.43, 2026-10-06): [operation and contracts](orion_q_communicators_geo.md)
separate automatic type dependencies from operator-selected technologies, keep
orbital fields exclusive to satellites, associate protocol-role capabilities and
validate terrestrial coordinates as a pair. New-session resets, backend revision
readback and pending drafts across theme rebuilds are covered by the
[validation report](../results/orion_q_communicator_forms_20261006.md).
Physical actuation and optional-model boundaries remain unchanged.

GEO maintenance (5.42, 2026-10-06): [Map/Globe operation](orion_q_geo_modes.md)
displays all published satellites and trajectories, adds fixed screen-size
selection handles, hover/details and button/scroll mode switching with hysteresis.
A shared controller retains observations and interface state while retiring each
renderer and its graphical resources before creating the next. Communicators is
immediately below Nodes with its own icon and shortcut. The
[architecture decision](../architecture/adr_geo_004_exclusive_renderers.md) and
[validation table](../results/orion_q_geo_modes_20261006.md) describe the lifecycle,
repeat swaps and native package checks. This does not change hardware or
scientific qualification gates.

Satellite audit follow-up (5.41, 2026-10-06): [OGS v2 integration](orion_q_satellite_ogs_v2.md)
adds separate engineering/APT/TDC streams, authenticated read-only local WebSocket
reception, raw-tag persistence and source-independent detector processing. Extra
loss, per-detector noise, jitter/dead time and pass windows now affect detections.
SGP4/TLE and covered ECEF state vectors complement the hypothetical orbit models.
Housekeeping and Science inspection exposes units, freshness and finite-block
aborts. Parity reconciliation, verification and Toeplitz hashing are executed as
conditional demonstrations; certified output stays zero. This closes the audited
software gaps and leaves mission ICD/gateway calibration, measured benchmarking,
precise tracking/EOP, distributed authentication and reviewed composable QKD
security as external qualification gates. See the [implementation report](../results/orion_q_satellite_pipeline_20261006.md).

Startup correction (2026-10-06): the 3440 Ã— 1440 opening freeze was reproduced
in the previous Windows executable and traced to a fractional zoom/scrollbar
layout loop. Automatic scale and scrollbar decisions now use stable client
bounds. The package gate exercises ordinary native startup on an isolated
journal at four DPI scales, plus the same backend demonstration at two scales.
Frameless controls, optional models and the physical-actuation boundary are
retained. [Startup investigation and use cases](../results/orion_q_startup_ultrawide_20261006.md)
records the diagnostic evidence and replacement distribution. This maintenance
fix does not promote scientific evidence or independent-machine acceptance.

Interface and intake update (2026-10-06): received wave history remains visible
until it scrolls off; presentation uses a precise 30 FPS timer and cached artwork.
Startup is restored and resizable; runtime theme styles are scoped to the active
window. Residual transition pauses remain measured and documented. Link
configuration exposes distinct associated classical and quantum statistical
ports, with shared admission for simulated acquisitions and raw file replay.
Missing or rejected communication produces no node measurements or operational
route. [Paired-channel scope and format](orion_q_paired_channels.md) describes the
implemented software flow and the pending physical quantum transport. This is
an interface/intake increment, not new scientific evidence or field acceptance.
The [use-case table and timings](../results/orion_q_interface_fluency_20261006.md)
record the regression scope and residual pauses.

Demonstration update (2026-10-05): the native console now uses seven institutional
campus references in Brazil, including **IME-USP**, with a national offline map,
campus focus and eight hypothetical geodesic spans. A confirmed ML permission
switch starts OFF in a new demonstration workspace and controls compensation,
synchronization, key-rate, routing and allocation. Baselines and diagnostic
detectors remain available; permission, revision and hashed receipts persist.
The legacy Salvador and operator journals are retained. Direct long spans can
be physically unusable in the reference plant and are not shortened to fit ML
training domains. The [demonstration report and use-case table](../results/orion_q_institutional_demo_20261005.md)
record the implementation, geographic sources, validation and Windows package.
This is a simulation/product increment; surveyed cable routes, institutional
equipment data, field calibration and independent-machine acceptance remain
separate work. See the [current demonstration guide](orion_q_demo_guide.md).

Implementation update (2026-10-02): the [roadmap for the five high-value advances](roadmap_alto_valor_20261002.md)
now includes exact-profile controller recommendations, comparative reports,
isolated runtime diagnostics and a LabOne Q emulation bridge, all validated
locally. Independent Windows acceptance was deferred by the user. The
[usage guide](orion_q_roadmap_usage.md) documents these features and complements the
dated implementation notes below and the [pre-defense plan](roadmap_prebanca_20261022.md).
The [implementation report and local artifacts](../results/orion_q_roadmap_20261002.md)
locate the source, tests, documentation, Windows package and evidence under the
original project root.

## ORION-Q functional backlog â€” Q01 first increment (2026-09-10)

Current position (2026-09-21): Q03 includes persistent nodes/links and scoped
path execution; Q08/Q09 have a limited temporal path, not a general scheduler.
Q14 has the packaged campaign runner, history, manifest editing and saving.
[Q04 first increment](orion_q_commands_q04.md) now tracks native inventory
commands with persistent IDs, readback confirmation and unknown outcomes.
Stage A remains incomplete. Q05 runtime policy binding is still pending.

Update (2026-09-22): both papers and QKD/Quantum Network exports were received.
The [first Q16 intake increment](orion_q_author_model_intake.md) adds pinned,
data-only archive admission and offline parity for QKD synchronization and two
routing checkpoints, followed by four polarisation compensation checkpoints.
The [optional-model contract](orion_q_optional_models.md) requires OFF by default,
explicit per-function selection and a usable base product without weights. The
lazy catalog/factory is implemented for offline use. The source now also exposes
a read-only native catalog under EDGE CONTROL; inspecting a row never activates a model.
The [QKD key-rate actor](orion_q_optional_keyrate.md) is now also admitted offline:
features, probabilities and recorded-action consequences match all 40 supplied
periods. Original stochastic draws and key-rate improvements were not reproduced.
The [hybrid allocator](orion_q_optional_allocator.md) now reproduces persistence
and Ridge exports over 1,440 steps plus 20 snapshots. This does not regenerate
throughput or finite-key results. Detector loading remains blocked (pickle).
The [native OFF/offline/blocked catalog](../results/orion_q_model_catalog_ui_20260922.md)
was first implemented and tested in source; the Windows increment is recorded below.
The catalog now also supports explicit local ZIP identity/integrity checks, with
stale-result invalidation and no installation or model construction. See the
[verification increment](../results/orion_q_model_archive_ui_20260922.md).
The first [graphical offline session](../results/orion_q_sync_session_ui_20260922.md)
now evaluates the QKD synchronizer on one explicit simulated window against
persistence, with optional future reference isolated from inference and exclusive
JSON export. No automatic execution, training, runtime activation or claim of gain.
The [QKD actor session](../results/orion_q_keyrate_session_ui_20260922.md) now also
compares recommended transmitter parameters with hold-current settings, using a
distinct causal input schema and explicit PCG64 seed. No next-period rate or
counterfactual performance is fabricated.
The [polarisation session](../results/orion_q_compensation_session_ui_20260922.md)
now exposes four independent Ridge/MLP checkpoints across calibrated/development
regimes, strict SO(3) inputs, explicit persistence warm-up and optional final-reference
angular errors. The [routing sessions](../results/orion_q_routing_session_ui_20260922.md)
now expose nominal stochastic and long-deadline greedy selection from named
simulator features, with explicit seed and declared decision reference. No
heuristic performance or counterfactual outcome is inferred from action agreement.
Update (2026-09-23): [hybrid allocation sessions](../results/orion_q_allocation_session_ui_20260923.md)
now compare persistence and Ridge on identical fixed-plant snapshots, with explicit
warm-up, committed-queue semantics and asymptotic-only key budgets. All ten
admitted graphical configurations remain optional and offline; the detector is blocked.
The [Windows increment](../results/orion_q_optional_models_windows_20260923.md)
now packages all ten sessions, with successful local frozen checks both without
packages and with the explicitly supplied weights. Author weights remain external;
do not bundle them before redistribution terms are clarified. Independent-machine
manual acceptance is pending. Next integration increment: define and validate the
typed feature bridge for sandbox shadow evaluation before Q05 activation.
The first [synchronization shadow bridge](orion_q_sync_shadow_bridge.md) is now
implemented in source: explicit typed simulated windows, causal/context checks,
read-only runtime sidecar and report export. Generic WDM snapshots return
unavailable, never fabricated timing features. The subsequent
[native continuous shadow session](../results/orion_q_sync_shadow_ui_20260923.md)
now consumes explicit timing windows from the artificial provider through the same
snapshot plug, with manual START, freshness/invalidation and frame export. Physical
timing and other bridges remain pending; the previous Windows package is unchanged.
The source now also includes [causal temporal scoring](../results/orion_q_sync_temporal_20260923.md):
committed forecasts are paired with later exact-target samples, with coverage,
MAE/RMSE versus persistence and explicit report export. A fixed single-seed artificial
replay verifies the pipeline, not generalization or WDM-to-fidelity prediction.
The subsequent [frozen six-scenario, ten-seed campaign](../results/orion_q_sync_campaign_20260923.md)
is complete in source. Only the smooth scenario met all predefined descriptive
engineering filters against persistence and a past-window-mean baseline. Three
scenarios were worse than persistence in all ten seeds; packet-loss coverage was
80.7%. Do not generalize the initial diagnostic improvement or activate Q05 from
these results. The synchronizer remains optional/offline/shadow, not a universal
replacement for the baseline. Other typed model bridges remain the next integration work.
The [polarisation shadow bridge](orion_q_compensation_shadow_bridge.md) is now
implemented in source for all four admitted checkpoints, with explicit SO(3),
reference-frame and regime contracts, 2 ms cadence and warm-up labeling. It is
read-only and does not reuse timing/WDM fields. An artificial producer and native
opt-in polarisation shadow view now use the common Snapshot plug: 16 samples at
2 ms spacing per publication, on independent virtual clocks (not 500 Hz GUI/control).
Four external checkpoints are selectable; source loss, stop and context changes
invalidate results. [Delayed-reference angular evaluation](orion_q_compensation_temporal.md)
now scores committed forecasts against exact samples inside later disjoint batches.
The [completed frozen SO(3) campaign](../results/orion_q_compensation_campaign_20260923.md)
now covers 240 runs and all 24 scenario/checkpoint groups. Six groups met the
descriptive filters: Ridge in both regimes under smooth, thermal-reversal and
rotation-step inputs. Noise failed; packet loss and late results reduced coverage
to 73.333%. MLP lost to both baselines in all seeds. The historical selected
regression passed 500 tests, not the entire repository suite. Consolidation on
2026-09-24 verified 240/240 report hashes, all 15 frozen source hashes and all
24 aggregates. Q05 and the previous Windows package are unchanged. See the
[native shadow checks](../results/orion_q_compensation_shadow_ui_20260923.md).
Q16 as a whole, closed-loop evaluation and runtime binding remain incomplete.
Q05 activation and the physical feature bridge are still pending: no new policy is active in the UI.
Follow the [integration plan](orion_q_two_models_integration_plan.md), keep domains
separate, and do not infer operational advantage or experimental validation from
parity with supplied examples. Older dated notes describe their original milestones.

Follow-up (2026-09-12): the [Q02 numeric/temporal foundation](orion_q_observations_q02.md)
adds canonical observations to measured JSON and artificial inputs, per-link
acquisition-order checks and stable first-receipt timestamps. Service evidence
lanes retain their previous contracts; persistent topology integration (Q03)
is next. This does not implement general historical replay or validate measured
WDM-to-quantum prediction.

The [30-feature backlog](orion_q_functional_backlog_20260910.md) starts with
explicit local adapter capabilities. The [first implementation note](orion_q_capabilities_q01.md)
records deny-by-default command gating, input-mode/health declarations and
capability-aware native controls. Persistent UI-to-backend provisioning (Q03),
command lifecycle (Q04) and runtime policy switching (Q05) remain pending.
This is a source increment, not a new packaged Windows release or physical-control milestone.

## Next after SO(3) consolidation (2026-09-24)

The [local checkout record](local_workspace.md) registers the move to
`E:\Codex\2026-08-27\e\work\NISQ-Predictive-Twin`; existing work and original
campaign artifacts are preserved.

Completed on 2026-09-24: the [allocation shadow bridge](orion_q_allocation_shadow_bridge.md)
now admits explicit fixed-plant observations and past drift history for persistence
and Ridge, validates source/clock/plant/stream and compares identical committed
queues without mutation. A read-only runtime method, explicit replay CLI and
exclusive report export are available. Four original-package parity checks and
407 selected tests passed; see [validation](../results/orion_q_allocation_shadow_20260924.md).
This source increment does not add continuous allocation to the GUI or activate Q05.

The subsequent [native allocation shadow session](../results/orion_q_allocation_shadow_ui_20260924.md)
is now implemented in source: causal artificial history, explicit START, independent
5 s virtual cadence, freshness/invalidation and captured-frame export. The producer
never consumes recommendations, advances the supplied queue or infers delivered
keys. The original-package diagnostic passed 12 evaluations, including warm-up. The
console regression passed 581 tests; the final catalog/UI recheck passed 145
overlapping tests, including the cached-payload revalidation check.

The [routing shadow bridge](orion_q_routing_shadow_bridge.md) is also implemented
in source: explicit named features and ordered candidates, declared instantaneous
node/link/memory/pair checks, resource identity and reproducible offline selection.
Any invalid candidate refuses the entire window before weights are loaded. The
read-only runtime method and replay CLI do not execute or consume resources.
[Validation and limits](../results/orion_q_routing_shadow_20260924.md) distinguish
software parity from routing performance and complete simulator legality.

The [native routing shadow session](../results/orion_q_routing_shadow_ui_20260924.md)
is now implemented in source. A separate artificial three-node inventory evolves
independently of recommendations; START is explicit, expired/late results are
invalidated and captured-frame exports never overwrite. Task/topology changes
require reopening; resource versions must advance. Twelve original-weight UI
evaluations matched offline selection, with no commands or resource consumption.
Features remain illustrative and do not certify the original simulator domain.

The [QKD key-rate shadow bridge](orion_q_keyrate_shadow_bridge.md) is now implemented
in source: explicit decoy-state observables and units, link/settings/calibration
identity, causal periods and an explicit calibration bootstrap at clock origin.
The read-only runtime method and replay CLI preserve PCG64 selection, nine bounded
proposals and the hold-current baseline. Eight original-weight checks matched the
offline session; 564 selected tests passed, including 98 new bridge cases.
See [validation](../results/orion_q_keyrate_shadow_20260924.md). No settings are applied.

Completed on 2026-09-26: the [native QKD key-rate shadow session](../results/orion_q_keyrate_shadow_ui_20260926.md)
now has an independent artificial producer, explicit START, fixed transmitter
settings, causal 1 s virtual intervals and a labeled calibration bootstrap after
stream discontinuities. Freshness follows the observed period end; moving the
decision timestamp alone cannot renew it. Configuration/calibration changes
invalidate the session; export captures one frame without overwriting files.
Six original-weight UI evaluations matched the offline session, with no commands.
All ten admitted configurations now have offline and artificial shadow views in
source. Q16 remains partial: detector safe export, measured-domain validation,
weight redistribution terms and Q05 binding are still open.

Update (2026-09-26): Q04 now has an explicit read-only reconciliation API/CLI for
unresolved inventory commands. One SQLite snapshot binds the selected receipt hash
to current topology evidence; export preserves the original lifecycle and always
keeps the command outcome unresolved. Matching fields cannot establish causation.
[Scope and usage](orion_q_commands_q04.md) Â·
[Validation](../results/orion_q_command_reconciliation_20260926.md).

The native COMMAND CENTER now also offers CHECK CURRENT STATE and EXPORT
EVIDENCE, with background work, source/selection invalidation and captured export.
Original receipts and unresolved outcomes are preserved; there is no replay or
automatic retry. [Native validation](../results/orion_q_command_reconciliation_ui_20260926.md).
Q04 remains partial beyond these inventory verbs and observations.

Update (2026-09-27): the first Q05 preparation/review API is implemented for a
threshold proposal on the existing repeater utility policy. It binds the proposal
to the local runtime session, observed policy configuration and topology version/hash,
retains the baseline, expires after 300 monotonic seconds and invalidates stale
proposals. CURRENT means compatible with the observed context, not approved or active.
No optional model is loaded. [Contract and usage](orion_q_policy_preparation_q05.md) Â·
[Validation](../results/orion_q_policy_preparation_20260927.md).

The native EDGE CONTROL â†’ POLICY REVIEW panel now loads the baseline, prepares
an explicit threshold proposal, reviews its context and exports the captured
review. Work runs in the background; source/runtime changes, edits, expiry and
panel closure invalidate selection. CURRENT is a timestamped observation, not
approval. [Native validation](../results/orion_q_policy_review_ui_20260927.md).

Update (2026-09-28): the [local approval API](orion_q_policy_approval_q05.md) records
an explicit declaration bound to the current proposal/review hashes after fresh
context validation. It preserves the original expiry and records the caller-declared
operator label without claiming authentication. Approval receipts are historical,
not activation tokens. No policy, journal or optional model is changed.
[Validation](../results/orion_q_policy_approval_20260928.md).

Native approval confirmation and captured export are now implemented in POLICY
REVIEW. The modal shows the exact proposal/review, requires a declared operator
label and an initially unchecked approval checkbox, then revalidates in the worker. Context/selection/
expiry changes dismiss confirmation; approval never changes the baseline.
[Native validation](../results/orion_q_policy_approval_ui_20260928.md).

The [local simulation activation API](orion_q_policy_activation_q05.md) now performs
fresh validation under the cycle lock and a SQLite write transaction, records the
approval plus previous/new configurations, and exposes live policy readback. Cycles
check active-policy evidence; commit uncertainty restores the old object and blocks
further execution. Restart does not restore historical activation automatically.
[Architecture](../architecture/adr_q05_001_process_bound_activation.md) Â·
[Validation](../results/orion_q_policy_activation_20260928.md).

Update (2026-09-28): [approved rollback and explicit recovery](orion_q_policy_recovery_q05.md)
are implemented as local simulation APIs. Rollback prepares the recorded previous
threshold and requires fresh review, approval and activation. Recovery observes the
uncertain commit, retains the restored policy object, and writes an explicit checkpoint
before releasing the cycle block. A missing row is only absence at observation, not
proof that the commit never occurred. No automatic retry or restart restore is added.
[Validation](../results/orion_q_policy_recovery_20260928.md).

Native activation/readback are now implemented in POLICY REVIEW. ACTIVATE requires
its own unchecked confirmation; ACTIVE POLICY distinguishes a historical commit from
a timestamped live observation and exports the captured receipt without overwriting.
Cancellation is checked before commit; once commit begins, closing/switching cannot
promise to undo the accepted operation. Refresh readback to establish current state.
[Validation](../results/orion_q_policy_activation_ui_20260928.md).

Native rollback and recovery are now implemented in ACTIVE POLICY. PREPARE ROLLBACK
pins the recorded previous threshold and requires fresh review/approval/activation.
INSPECT RECOVERY observes the uncertain attempt; a separate confirmation retains the
restored policy and writes a checkpoint. Source cancellation, competing panels and
commit uncertainty are covered by the local integrated regression.
[Validation](../results/orion_q_policy_recovery_ui_20260928.md).

[Structured decision explanations](orion_q_policy_decisions_q05.md) now have a local
read-only API and native DECISION AUDIT panel. Cycle hashes, request/index consistency
and historical policy references are checked in one SQLite read snapshot. Intended
action, safety violations and executor outcome remain separate recorded facts.
New activated cycles include the complete policy receipt hash; legacy/missing links
are explicit and never filled with the current configuration.
[Validation](../results/orion_q_policy_decisions_20260928.md).

Update (2026-09-29): [declared compatibility criteria](orion_q_policy_compatibility_q05.md)
are exposed in the native catalog, local API and explicit JSON export. The existing
controller requires context; all eleven optional components remain blocked, with
input-domain, action-mapping and lifecycle reasons. A ZIP check or matching action
name cannot promote selection. These declarations do not inspect current telemetry
or runtime identity. [Validation](../results/orion_q_policy_compatibility_20260929.md).

[Contextual observation](orion_q_policy_context_q05.md) is now implemented in a separate
local API session: caller-supplied simulated input is checked against the observed
controller/supervisor and persisted topology. Input age, provenance semantics,
node resources, hashes and context expiry/change are checked without deciding or
preparing a switch. All optional candidates remain blocked. This is not source
authentication, measured-domain validation or per-action authorization.
[Validation](../results/orion_q_policy_context_20260929.md).

Native CONTEXT INSPECTION is now implemented with explicit input/context selection,
a private background session, invalidation and captured export. It never fills
predictions or renews input timestamps. BLOCKED observations remain inspectable;
no result grants selection or per-action authority.
[Validation](../results/orion_q_policy_context_ui_20260929.md).

Update (2026-09-29): the [supervised closed-loop polarisation target](../results/orion_q_polarisation_closed_loop_20260929.md)
is implemented in simulation mode. A plant transcribed from the author's package
(bit-identical trajectories and monitoring noise, CI fixtures) receives the approved
correction 40 ms later; an independent supervisor refuses non-SO(3) proposals, bounds
the step between commands and degrades to a hot-standby alpha-beta after eight
consecutive faults. A per-function contract admits a checkpoint only on the plant its
own metadata declares. Under frozen protocol v1 (20 new seeds, reproduction gate,
retuned baseline, exact sign-flip + Holm) both calibrated checkpoints beat no
compensation, persistence and the retuned alpha-beta in 20/20 seeds (Ridge 3.33x
below alpha-beta). No policy was activated by the campaign and no physical action exists.

The [Q05 activation of the polarisation target](orion_q_polarisation_activation_q05.md)
followed the same day: a learned checkpoint enters the simulated loop only after
prepare/review/approve/activate, bound to its contract decision, the hashes of its
weight files and a campaign summary showing Holm-significant wins over every baseline
for those weights. Activation re-validates inside a SQLite write transaction and
restores the previous controller on failure; readback distinguishes the committed
record from the live controller; rollback returns to alpha-beta through the same flow.
Process-bound and CLI-driven; console integration is open.

1. Show the polarisation activation in the native console (proposal, review,
   approval, activation, readback, rollback and the journalled loop runs), reusing
   the POLICY REVIEW patterns. Keep models OFF by default and simulation-only.
2. (Deferred until after the pre-banca) Define a bounded action-translation contract for optional routing components:
   explicitly map the supported repeater subset and bind resources/preconditions
   to persisted simulated state. Refuse unsupported actions or missing mappings;
   matching action names are insufficient. Keep models OFF and selection blocked
   until independent-supervisor and lifecycle integration meet acceptance.
   Q05 remains partial for additional candidate adapters and selection.
   Historical JSON alone cannot authorize activation; preserve optional models OFF
   by default, explicit confirmations and no physical authority.
3. Extend SO(3) evidence only under separately frozen protocols. The calibrated
   plant now exists (closed-loop v1); still open: the development point on its own
   plant, a bounded actuator (slew/range/reset) and longer traces. Preserve all
   checkpoints and baselines.
4. Keep Q05 partial until the compatibility/selection contract for additional
   controllers meets acceptance. Structured historical explanations are implemented. The tested threshold
   workflow does not enable optional model selection. Models stay optional and OFF by default;
   no physical actuation follows from these simulated results.
5. Retain independent-machine Windows acceptance and safe detector export as open
   deliverables. Weight redistribution terms are closed: the checkpoints are the
   TCC author's own work. The source shadow increments have
   not produced a replacement Windows package.

## Completed: v5.23 â€” WDM models connected to the sandbox

Status: implemented and locally testable.

- accept a timestamped, ordered WDM window through a typed contract;
- reject missing, non-finite, reordered and quantum-privileged inputs;
- apply training-only min/max preprocessing recorded in the model spec;
- run the existing EdgeLSTM, EdgeGRU or EdgeTCN fidelity predictor;
- require held-out temporal uncertainty for point models;
- understand the existing probabilistic and fidelity DualHead output signatures;
- propagate OOD, telemetry age, model hash, dataset hash, configuration hash and
  window hash into the controller record;
- feed the unmodified repeater policy and independent safety supervisor;
- default the reproducible campaign to shadow mode and simulated provenance;
- keep `physical_action_performed=false` as an enforced invariant.

Exit evidence: unit/adversarial tests plus
`experiments/run_wdm_control_plane_shadow_campaign.py` producing hashed local
records. Scientific meaning: product integration on simulated data only. It
does not validate prediction of measured quantum outcomes.

## Completed: v5.24 â€” Three-lane evidence architecture

Status: implemented and locally testable.

- expose measured Sant'Anna WDM as historical optical-health observations;
- bind the raw CSV to the registered temporal result by SHA-256;
- preserve the negative `0/16` incremental-prediction result in the product;
- retain measured QBER/SKR inside the exact-grid QKD assurance lane;
- retain simulated Bell fidelity inside the non-physical control lane;
- prohibit BER/QBER/SKR-to-Bell-fidelity conversion;
- fail closed when optical, QKD, measured or undeclared evidence attempts a
  `PURIFY`/`SWAP` ingress;
- expose all three contracts and optional optical snapshot through API v1.2 and
  the dashboard.

Exit evidence: adversarial contract/API/safety tests, dashboard lint/build and
`experiments/export_measured_optical_health_lane.py` run on the 65,733-row
Sant'Anna recording. Scientific meaning: stronger construct validity and an
honest real-data product surface. It does not validate the central temporal
WDM-to-quantum hypothesis.

## Next control-plane increments

### Completed: v5.25 â€” Comparative controller evaluation

- replayed the same held-out simulated trace through persistence, reactive,
  point-model, probabilistic, DualHead and oracle controllers;
- compared prediction, expected BBPSSW yield, resources, calibration and
  fail-closed rates under a predeclared ten-seed scorecard;
- retained two zero-shot domain shifts and selected no winner on test data;
- found 0/3 positive registered comparisons against persistence because
  calibrated uncertainty made every candidate abstain;
- detected intended-policy saturation: all 60/60 in-distribution
  controller-seed audits selected only `SWAP` at the fixed two-pair state.

Exit evidence:
[`wdm_controller_comparison_v525.md`](../results/wdm_controller_comparison_v525.md),
unit/adversarial tests and the hash-manifested local campaign output.
Scientific meaning: a negative simulated result that identifies uncertainty
and action-space bottlenecks. It does not validate measured WDM-to-quantum
prediction.

### Completed: v5.26 â€” Policy semantics and abstention remediation

- replaced unconditional four-way competition with mutually exclusive
  link-purification and path-extension task contracts;
- distinguished the forecast raw-pair fidelity from the two explicit stored
  link-pair fidelities consumed by a swap;
- inverted the analytical BBPSSW mapping so a 0.65 output target correctly uses
  a 0.625 raw-input eligibility threshold;
- calibrated uncertainty and probability-abstention thresholds only on
  temporal calibration prefixes under predeclared budgets;
- hash-froze every policy before testing on ten confirmatory seeds disjoint
  from v5.25 and from the methodology-debug seeds;
- passed 60/60 task-sensitivity audits; 23/60 held-out controller-seed rows
  remained single-action, while the earlier fixed-state audit was 60/60 (the
  different audit inputs prevent treating that contrast as an effect size);
- found 0/3 significant neural improvements over persistence after Holm, while
  retaining separate OOD, uncertainty, WAIT and ordinary-HALT outcomes.

Exit evidence:
[`wdm_policy_remediation_v526.md`](../results/wdm_policy_remediation_v526.md),
unit/adversarial tests and the hash-manifested local fresh-seed campaign.
Scientific meaning: the action semantics are repaired, but present WDM models
still have no statistically supported operational advantage and no measured
validation.

### Completed: v5.27 â€” Frozen model bundle and dashboard replay

- defined an NPZ bundle containing JSON/`uint8` plus numeric `float32` state
  tensors and loading exclusively with `allow_pickle=False`;
- restricted reconstruction to five local architectures and validated exact
  tensor keys, shapes, dtypes, finite values, per-tensor hashes, semantic model
  hash, feature order, preprocessing bounds, trace hash and policy-spec hash;
- exposed server-configured `GET /api/v1/control-plane/model` without returning
  the local path or weights;
- exposed bounded `POST /api/v1/control-plane/model-replay`, fixed to shadow
  mode and the frozen v5.26 link-purification policy;
- added a dashboard replay chart for prediction, uncertainty, retrospective
  simulated target, OOD state, intended/effective action and abstention cause;
- retained manual sandbox input as an explicitly labelled diagnostic path;
- exposed the v5.25/v5.26 `0/3` results and the not-approved status beside the
  model rather than presenting it as an operational winner.

Exit evidence: safe-loader adversarial tests, API service/HTTP tests, dashboard
lint/build and a reproducible seed-809 DualHead bundle smoke replay. Scientific
meaning: reproducibility and product-safety integration on simulated data only;
the neural operational-gain and measured WDM-to-quantum hypotheses remain
unsupported/unvalidated.

### Historical: v5.28–v5.33 — Discontinued IBM validation branch

These releases implemented offline circuit preparation and Bell controls,
plan-bound submission contracts, a durable operator ledger, read-only recovery
and metadata diagnostics. All validation used fake backends or local doubles;
no real IBM job was submitted or retrieved. The integration was retired on
2026-10-07, including its adapters, commands and optional Runtime dependency.
The detailed development record remains in [history](../history.md). Local
Qiskit/Aer simulation and the existing network protocols are retained.

### Completed: v5.34 â€” Windows desktop control-plane product

- rethemed the dashboard as a compact network command center using the supplied
  Nocturne visual reference without executing its bundled scripts;
- moved topology control and WDM replay ahead of the evidence portfolio while
  retaining explicit simulated/measured provenance;
- served the static dashboard and JSON API from one loopback-only origin;
- added a Windows entry point with persistent local state and Edge app-window
  launch;
- embedded the validated evidence bundle, frozen WDM DualHead model, Python,
  Torch and Qiskit/Aer runtimes in a one-folder distribution;
- added a repeatable PyInstaller build, SHA-256 output and zipped release;
- smoke-tested UI, health, model loading, three-row shadow replay and the
  SWAPâ†’HALTâ†’SWAP resilience scenario from the generated executable.

Exit evidence: desktop/API tests, dashboard lint/static build and executable
smoke test. Scientific meaning: productization and reproducible simulated
control only. It does not create measured temporal WDM evidence or a physical
executor.

### Measured WDM/QKD gate

Temporal QBER/SKR models and QKD DualHead/TriHead outputs may enter shadow mode
only after a genuinely synchronized, timestamped dataset satisfies the
`PairedQuantumTelemetrySource` contract. Bell-fidelity control requires measured
Bell fidelity; QBER or SKR must not be silently converted into Bell fidelity.

## Retired IBM Quantum milestones

The first hardware Bell run (IBM-2C), processor BBPSSW purification (IBM-3)
and processor teleportation (IBM-4) were cancelled when the integration was
removed on 2026-10-07. They are not prerequisites for completing the active
roadmap. Earlier offline results remain historical software-validation evidence,
not measured optical-link or WDM/QKD evidence. Local BBPSSW, entanglement
swapping and other implemented network simulations retain their existing
contracts and scientific limits.

## Later product gates

Version 5.40 adds [Communicators and orbital GEO](orion_q_communicators_geo.md):
seven physical device categories, independent network roles, dynamic technology
forms, atomic inventory/topology persistence, paired-channel admission and
backend-derived satellite geometry. Endpoint/Core labels follow stored roles.
Satellite selection opens an animated globe; photon links require actual received
emission blocks and current visibility. Two-body scenario propagation is supported;
SGP4/TLE, measured ephemerides and validated physical drivers remain open.
The [validation report](../results/orion_q_communicators_geo_validation_20261006.md)
records functional UI, persistence, physical-limit and frozen-runtime checks.

Version 5.39 adds [modular quantum protocol sessions](orion_q_modular_quantum_protocols.md):
entanglement swapping, BBPSSW purification, quantum memories, reduced SNS-TF
and a satellite/Doppler model. Acquisition, compatibility, synchronization,
classical receipts, resource consumption and persistence run in the backend.
The institutional demonstration removes UNIJORGE, retains IME-USP and adds
military IME and the international reference topology (66 nodes / 71 links /
12 sessions). The [connection matrix](../results/orion_q_modular_connection_matrix_20261006.md)
and [use-case report](../results/orion_q_modular_quantum_validation_20261006.md)
record the implementation and its validation. Verified locations do not establish operational connections.
Sources and processing adapters can be replaced through shared contracts.

This completes the implemented simulation and architectural extension for those
five families. It does not close hardware-readiness or QKD-security gates:
validated instrument adapters and composable finite-key proofs remain absent.
The later 5.41 update adds authenticated observation ingress and conditional
bit processing; it does not qualify distributed QKD authentication. The displayed key budgets are
conditional and certified key output remains zero. Memories are session-local;
satellite ephemerides and optical terminals are hypothetical. Infeasible
intercontinental LEO scenarios remain unavailable rather than reporting success.

Version 5.38 implements the supplied Pattern B interface while preserving the
existing input contract. GRAPH/GEO/MATRIX share one snapshot, geographic data
is opt-in and validated, the matrix refuses multi-hop inference, overlays are
click-only, node provisioning is local shadow state and Edge Control exposes
only journal aggregates. This is a product-interface milestone, not a new
physical-control or scientific-evidence gate.

Version 5.37 replaces separate demonstration windows with an interchangeable
input plug in the existing native window. The [measured JSON adapter](input_sources.md)
is read-only and fail-closed on invalid/stale input. Vendor-specific acquisition,
source authentication and measured-input model validation remain separate gates.

Version 5.36 adds [ORION-Q](orion_q_console.md): the user-supplied PDF visual
language, bundled fonts, responsive native layout, scrollable large lists and
isolated artificial fault campaigns. This closes the fixed-canvas/letterbox
and LINKS clipping defects for the tested viewport matrix. Target-machine
visual acceptance, mixed-DPI testing and prolonged soak testing remain open.
Artificial UI data does not promote any scientific claim or hardware gate.

Version 5.35 integrates the revised native PySide6 console with the existing
service, explicit metric provenance, exact node selection, durable console
events, asynchronous loading and self-tested portable packaging. See the
[native console boundary](native_console.md). Physical contract recognition
remains refusal-only; imported lease/authorization scaffolding does not satisfy
any hardware-readiness gate below or upgrade the scientific claims.

- institutional login/role provisioning and operational PKI deployment;
- externally anchored checkpoints or publicly verifiable audit signatures;
- consumption recovery across deployment backups and separate operating-system accounts;
- vendor-neutral read-only inventory discovery;
- measured shadow operation on synchronized WDM/QKD telemetry;
- physical actuation only as a separately reviewed project with hardware safety
  limits, rollback, emergency stop and organizational authorization.
