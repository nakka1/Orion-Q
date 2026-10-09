# Quantum Autopilot core — selective integration gate

## Status

Phase 1 introduces only a domain-neutral decision and audit layer. It does not
import the attached photonic adapter, IBM execution path, FPGA model, old
documentation, or generated results.

The existing `quantum_twin` package remains the scientific source of truth for
WDM/QKD ingestion, physical simulation, predictive models, statistical
validation, and the TCC claim registry.

## Boundary

```text
quantum_twin scientific evidence
             |
             v
future domain adapter
             |
             v
quantum_autopilot.core
  contracts -> fail-closed gates -> policy -> audited decision
```

The core cannot convert simulated fidelity into measured evidence and does not
alter the status of any TCC claim. In particular, `H-ORIGINAL` remains open and
untested until a synchronized physical WDM/quantum time series exists.

## Deliberate corrections relative to the audited prototype

1. Configured SLO metrics are violations when missing or invalid.
2. Configured budgets require explicit, non-negative usage estimates.
3. A policy receives only candidates that passed both gates.
   Domain adapters may add an explicit third gate for evidence-specific
   constraints; its violations are stored in the same decision record.
4. An unknown or rejected action returned by a policy becomes `HALT`.
5. The uncertainty wrapper evaluates the plan actually selected.
6. OOD feature gates reject missing required features.
7. Evidence provenance is mandatory and timezones are explicit.
8. The complete immutable input snapshot is serialized with the decision.

## Candidate contract

An adapter must populate:

- `prediction` for a fidelity SLO;
- `expected_metrics.latency_s` for a latency SLO;
- `expected_metrics.success_probability` for a success-probability SLO;
- `expected_usage.attempts`, `time_s`, or `shots` for each configured budget;
- `Provenance.evidence_kind` using `measured`, `simulated`, or `synthesized`.

The absence of required information blocks recommendation rather than being
interpreted as a zero-cost or successful candidate.

## Subsequent integration status

The read-only WDM/QKD adapter, assurance grid, local dashboard and non-physical
control-plane sandbox were added in subsequent guarded increments. The sandbox
reuses the original repeater policy and simulator behind an independent safety
supervisor; `assurance` and `shadow` remain dry-run modes, and the public
contract exposes no physical mode or executor.

This evolution does not change the scientific boundary above. The current
dashboard accepts manually supplied simulated fidelity and uncertainty rather
than loading a measured WDM predictor, and `H-ORIGINAL` therefore remains open.
See [`Quantum Network Control Plane Sandbox`](../product/quantum_network_control_plane_sandbox.md)
for the current execution contract and limitations.

## Next integration gate

The persistent simulated topology and scenario runner are now implemented with
optimistic versions, restart-safe SQLite state and hash-addressed transitions.
The next defensible phase is a read-only model-inference adapter with explicit
simulated provenance. A genuine WDM/QKD feed may later be connected to shadow
mode. Physical device control, IBM execution and FPGA deployment remain
separately authorized future gates.
