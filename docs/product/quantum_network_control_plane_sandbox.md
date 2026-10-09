# Quantum Network Control Plane Sandbox

## Purpose

The sandbox is a non-physical control-plane demonstrator for quantum repeater
decisions. It closes the software loop:

```text
typed network state -> original repeater policy -> independent safety supervisor
                    -> shadow or simulated executor -> hashed SQLite journal
```

Version 5.23 also provides the guarded model-driven path:

```text
timestamped WDM window -> frozen feature/scaler contract -> existing WDM model
                       -> fidelity + calibrated uncertainty + OOD
                       -> typed network state -> unchanged guarded control loop
```

It is a product and architecture demonstrator, not a hardware controller. The
public mode enum contains only `assurance`, `shadow`, and `simulation`; there is
no physical mode or physical executor. Every execution record also carries the
invariant `physical_action_performed=false`.

## Reused scientific components

`RepeaterSandboxPolicy` adapts the existing `quantum_twin.control.PolicyEngine`
without changing its HALT/WAIT/PURIFY/SWAP utility calculation. `SWAP` uses the
existing density-matrix Werner-state implementation. `PURIFY` uses the existing
Qiskit-backed `QuantumRepeaterNode` event path. The sandbox does not introduce a
new fidelity model or upgrade any scientific claim.

Manual `predicted_fidelity` and `prediction_uncertainty` remain available in the
current dashboard as diagnostic simulated inputs. The v5.23 Python adapter and
campaign can now generate them from an existing WDM model, but the dashboard
does not yet load a model bundle. Neither path is an output of a measured,
paired WDM/quantum predictor. The original temporal WDM hypothesis therefore
remains open.

## Guarded WDM model inference (v5.23)

`WDMModelInferenceAdapter` binds one in-memory model to an immutable spec that
contains its feature order, temporal window size, training min/max values,
output signature and SHA-256 hashes for model state, configuration and dataset.
Only columns from `QuantumNetworkDatasetV3.WDM_FEATURE_COLUMNS` are accepted;
`F_t`, T1, T2 and depolarization cannot cross the adapter as input features.

Every telemetry point requires a timezone-aware timestamp. Windows must have
the exact declared length and strictly increasing timestamps. Missing,
non-numeric and non-finite features are rejected. Values outside any training
bound set `out_of_distribution=true`; they are not silently clipped to hide the
shift, and the independent safety supervisor consequently forces `HALT`.

The output adapters cover the existing model families:

| Output contract | Existing model role | Uncertainty rule |
|---|---|---|
| `point` | EdgeLSTM, EdgeGRU, EdgeTCN | held-out temporal residual RMS is mandatory as a global sigma |
| `probabilistic` | EdgeLSTMProbabilistic or calibrated ensemble | model returns mean and uncertainty |
| `dual_head` | fidelity EdgeLSTM DualHead | availability is a gate; fidelity is never multiplied by it |

The QKD `QKDDualHead` and `QKDTriHead` predict key availability, SKR and QBER
from controlled measured cells. They do not output Bell fidelity and are not
connected to this repeater-fidelity decision boundary. Doing so would require a
new measured target and a scientifically justified policy.

The model-driven request preserves only the latest raw WDM values in the
network snapshot while hashing the entire ordered window. Model, window,
configuration and dataset hashes travel into provenance and the append-only
journal. The policy record identifies `wdm_model_inference_adapter` as the
prediction source.

Run the bounded simulated shadow campaign with:

```bash
python experiments/run_wdm_control_plane_shadow_campaign.py \
  --output-dir outputs/wdm_control_plane_shadow \
  --model edge_lstm
```

`edge_gru`, `edge_tcn` and `edge_lstm_dual_head` are alternative values. The
script trains only on a temporal prefix, reserves a later calibration tail for
point/dual-head uncertainty and replays held-out windows through shadow mode. It writes prediction rows,
complete control records, the SQLite journal, a summary and an artifact-hash
manifest. Actual held-out simulated fidelity is used only for retrospective
evaluation, never as controller input.

## Modes

| Mode | Policy | Safety gates | Execution in quantum twin | Physical action |
|---|---:|---:|---:|---:|
| `assurance` | yes | yes | no | impossible |
| `shadow` | yes | yes | no | impossible |
| `simulation` | yes | yes | yes | impossible |

Assurance and shadow use `DryRunExecutor`. Simulation uses
`SimulatedQuantumExecutor`. A caller cannot select an executor through the HTTP
request.

## Typed state contract

`NetworkStateSnapshot` requires:

- request and node identifiers;
- timezone-aware timestamp and non-negative state version;
- fidelity estimate in `[0,1]` and finite non-negative uncertainty;
- explicit telemetry age;
- quantum-memory state;
- aligned pair-fidelity and memory-age arrays;
- OOD, integrity and operator-interlock states;
- explicit evidence provenance.

Malformed numerical values, naive timestamps, misaligned arrays and unknown
memory states are rejected before policy evaluation.

## Independent safety supervisor

The default `SafetySupervisor` allows a non-HALT simulated action only when:

- intent and snapshot identities and state versions match;
- state integrity is verified;
- the operator interlock is closed;
- the state is not OOD;
- telemetry age is at most 5 seconds;
- prediction uncertainty is at most 0.20;
- simulation inputs carry simulated provenance;
- PURIFY/SWAP inputs explicitly declare the `bell_fidelity_control` evidence
  lane and are not measured optical/QKD evidence;
- WAIT has a pair, while PURIFY/SWAP have two stored pairs.

Any violation changes the effective action to `HALT` and remains visible in the
cycle record. `HALT` itself remains available when state quality is poor.

These thresholds are explicit demonstration defaults, not hardware-calibrated
safety limits.

## Idempotency and journal

Every request carries a caller-generated `request_id`. The runtime hashes the
complete mode and snapshot. Repeating the same identifier and payload returns
the original record without running the simulator again; reusing the identifier
with a different payload is rejected.

The application-level append-only SQLite journal stores the request hash,
record hash, action, mode, status and complete JSON record. This is useful for a
TCC demonstration and local audit, but it is not a signed or externally anchored
tamper-proof ledger. The idempotency lock is process-local; multi-instance or
physical control would require a durable pre-execution reservation protocol.

## Persistent simulated topology (v5.22)

The same SQLite database now retains a typed three-node line topology:
`alice -> repeater-a -> bob`. Nodes and the complete topology carry independent,
monotonic versions. Every reset, synthetic pair injection and applied control
cycle uses an optimistic version check and creates a hash-addressed topology
event. State therefore survives an API restart without becoming a claim about
physical inventory.

`resilience_demo` is the bundled deterministic scenario:

1. inject two nominal simulated pairs and execute the policy-selected SWAP;
2. inject a degraded state with excessive prediction uncertainty, preserving
   the intended SWAP but forcing an audited HALT;
3. inject a recovered state and execute SWAP again.

The scenario result contains every complete control-cycle record, topology
versions before and after each transition, each resulting node state and the
final topology. Completed scenario IDs are idempotent: exact replays return the
stored result and a changed payload under the same ID is rejected. All pair
injections carry simulated provenance and `physical_action_performed=false`.

## Local API

The existing loopback API adds:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/control-plane/status` | Modes, safety boundary and recent events |
| `GET` | `/api/v1/control-plane/events?limit=20` | Recent journal summaries |
| `GET` | `/api/v1/control-plane/topology` | Persisted topology or non-mutating default blueprint |
| `GET` | `/api/v1/control-plane/model` | Safe server-configured bundle status; never returns path or weights |
| `POST` | `/api/v1/control-plane/cycles` | Run one typed guarded cycle |
| `POST` | `/api/v1/control-plane/scenarios` | Run an idempotent persistent simulation scenario |
| `POST` | `/api/v1/control-plane/model-replay` | Run a bounded frozen trace in shadow mode only |

Request size, loopback binding, local CORS and no-store protections are shared
with the assurance API.

API v1.3 also exposes the separate measured optical and measured QKD contracts
through `/api/v1/evidence-lanes`. They are dashboard inputs, not alternate
routes into the repeater policy. See [`evidence_lanes.md`](evidence_lanes.md).
The model endpoints use the safe format described in
[`wdm_model_bundle_and_replay.md`](wdm_model_bundle_and_replay.md).

## Dashboard demonstration

Start the Python API and dashboard as described in
`enterprise_assurance_dashboard.md`, then open the **Control plane sandbox**
panel. The default two-pair, high-fidelity scenario normally selects SWAP.
Opening the operator interlock, disabling integrity, forcing OOD, increasing
telemetry age or increasing uncertainty demonstrates an audited fail-closed
HALT. Simulation, shadow and assurance records can be downloaded as JSON.
The topology panel shows node memory state and topology version. Its resilience
button resets the simulated line, runs the three guarded transitions and makes
the complete scenario record downloadable.

## Deliberate limitations and next gate

- no physical device adapter or command transport;
- the persisted topology is a fixed local simulation, not live discovery;
- no routing, scheduling or resource reservation;
- the dashboard/API loads only an operator-configured, non-pickle simulated
  model bundle; it does not accept uploaded models or measured-data claims;
- no claim that manual simulator inputs represent measured WDM telemetry;
- no distributed transaction, authentication, RBAC or signed audit log;
- completed scenarios are idempotent, but a process failure between sequential
  steps can leave an advanced topology without a completed scenario record;
- WAIT is a virtual state transition; hardware timing is not calibrated;
- operating safety thresholds are demonstration values.

The predeclared multi-seed comparison is now complete. It found no operational
gain over persistence (0/3 registered comparisons) because calibrated
uncertainty drove all neural controllers to fail-closed abstention. It also
found that the unchanged four-action policy selected only `SWAP` in all 60/60
in-distribution controller-seed audits at the declared fixed two-pair state.
See
[`wdm_controller_comparison_v525.md`](../results/wdm_controller_comparison_v525.md).

That remediation is now complete. The new task-constrained policy passed all
60/60 registered semantics probes and was frozen before evaluation on ten fresh
seeds, but no neural controller significantly beat persistence. See
[`wdm_policy_remediation_v526.md`](../results/wdm_policy_remediation_v526.md).

The hash-validated model bundle and read-only dashboard replay are now complete
and display the negative v5.25/v5.26 evidence without labelling the model as an
approved controller. Real measured shadow mode must wait for a genuine
synchronized telemetry source;
physical execution remains a later, separately authorized project. The former
IBM processor-validation branch was discontinued on 2026-10-07; its source is
retained in the [historical archive](../../archives/retired_ibm_quantum_20261007/ibm_quantum_source.zip).
Local Qiskit/Aer simulation and network protocols remain available and cannot
substitute for paired photonic measurements. See the current
[`roadmap.md`](roadmap.md).
