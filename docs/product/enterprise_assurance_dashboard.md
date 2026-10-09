# WDM·QKD Assurance: local enterprise workbench

## Product position

WDM·QKD Assurance packages the repository's strongest defensible result as a
local, read-only decision-support product. It answers a narrower and more useful
operational question than the still-open thesis hypothesis:

> Is this WDM/QKD condition an exact member of a reviewed experimental envelope,
> and may the available evidence be shown or used for technical decision support?

The answer is emitted as a self-contained JSON decision record. A company gets
one place to inspect scientific status, reproduce a published cell, see why a
condition was blocked, and retain the evidence/configuration hashes used in the
decision. The product never claims that a grid match proves temporal prediction,
causality, device transfer, Bell-state fidelity, or safe autonomous control.

## Why an organization would use it

The immediate value is evidence governance for teams evaluating quantum/classical
coexistence:

- **Faster technical due diligence:** the reviewed evidence, limitations and
  allowed language are visible without reconstructing months of experiments.
- **Auditable decisions:** every result preserves the point, exact cell, policy,
  violations and SHA-256 links to the evidence package.
- **Fail-closed scope control:** unseen campaigns, powers, VOA values, devices,
  channels and datasets cannot silently inherit an experimental result.
- **Data sovereignty:** the UI and API run on loopback; imported operating points
  do not leave the workstation.
- **Vendor-neutral integration boundary:** a typed JSON operating point can be
  produced by an OSS/NMS adapter later without changing the scientific gate.
- **Honest innovation pipeline:** supported, refuted, simulation-only and open
  hypotheses coexist in the same portfolio instead of negative results being
  hidden from product users.

This is suitable for a TCC demonstrator, research lab, network architecture
review, pre-deployment evidence review, or internal proof of concept. It is not
yet a production QKD controller, a monitoring substitute, or a quantified
commercial savings claim.

## Demonstrable workflow

1. The dashboard loads a summary only after validating the formal claim registry,
   all registered evidence hashes and the pinned IrelandQCI grid.
2. The user selects one of ten published campaign conditions and one of its eight
   exact VOA settings.
3. The browser sends only those condition coordinates. QBER and SKR are loaded by
   the Python API from the hash-verified 80-cell CSV; the browser cannot assign
   invented outcomes to that artifact.
4. `QKDOperatingPoint` validates measured provenance and the classifier performs
   exact matching without interpolation.
5. `ClaimRegistryAdapter` applies the formal evidence policy. A valid
   `C-MEASURED` cell yields `PROVIDE_DECISION_SUPPORT`; all unsupported uses yield
   `HALT` with machine-readable violations.
6. The complete result can be downloaded as JSON for an audit, appendix, review
   ticket or future systems integration.

An external `QKDOperatingPoint` JSON can also be imported. It is revalidated by
the current backend; a caller-supplied `condition_scope` is never trusted.

## Local architecture

```text
Browser dashboard (localhost:3000)
          |
          | condition coordinates or typed JSON
          v
Loopback assurance API (127.0.0.1:8765)
          |
          +-- formal claim registry + evidence hash validation
          +-- exact IrelandQCI operating-grid classifier
          +-- read-only ClaimRegistryAdapter
          +-- three-lane WDM/QKD/Bell evidence boundary
          |
          v
Audited DecisionRecord JSON (support or HALT)
```

The API refuses non-loopback binding, accepts CORS only from local development
ports, limits request bodies to 256 KiB, sends no-store and anti-sniff/frame
headers, and contains no physical executor.

## Run locally

Install the Python project and dashboard dependencies once:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
cd dashboard
pnpm install --frozen-lockfile
cd ..
```

Start the read-only API in one terminal:

```powershell
$env:PYTHONPATH = "$PWD\src"
.venv\Scripts\python.exe experiments/run_qkd_assurance_api.py
```

Start the dashboard in another terminal:

```powershell
cd dashboard
pnpm dev
```

Open `http://localhost:3000`. The evaluator stays disabled if the API is offline
or if the evidence integrity check fails.

## API boundary

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/health` | Read-only liveness and safety mode |
| `GET` | `/api/v1/summary` | Validated claims, integrity and grid metadata |
| `GET` | `/api/v1/example-decision` | Shipped hash-bound example |
| `GET` | `/api/v1/evidence-lanes` | Non-interchangeable evidence contracts |
| `GET` | `/api/v1/evidence-lanes/optical-health` | Optional hash-bound measured WDM snapshot |
| `GET` | `/api/v1/control-plane/model` | Validated local model-bundle status |
| `POST` | `/api/v1/assess-cell` | Load and assess an exact published cell |
| `POST` | `/api/v1/assess` | Revalidate a typed external operating point |
| `POST` | `/api/v1/control-plane/model-replay` | Bounded server-side WDM shadow replay |

## Commercialization path after the TCC

The current artifact is the evidence-assurance core of a future product. The
lowest-risk expansion sequence is:

1. add signed report bundles, user authentication and organization-level audit
   retention;
2. implement read-only adapters for selected NMS/OSS telemetry exports;
3. collect prospective, timestamped paired WDM/QKD telemetry under the existing
   `PairedQuantumTelemetrySource` acquisition protocol;
4. validate temporal prediction and cross-device transport before exposing any
   predictive alert;
5. only after prospective safety validation, consider recommendation workflows;
   closed-loop actuation remains a separate authorization and engineering phase.

Until step 3 succeeds, the company-facing promise is traceable evidence and
scope-aware decision support—not forecasting.

## Quantum control-plane sandbox (v5.22)

The same local application now includes a
[`Quantum Network Control Plane Sandbox`](quantum_network_control_plane_sandbox.md).
It reuses the original repeater policy and Qiskit-backed simulation path in
simulation, shadow and assurance modes, behind an independent fail-closed safety
supervisor. Every cycle is hash-addressed and journaled locally. Physical mode
and physical execution remain absent by construction. A persistent simulated
Alice–repeater–Bob topology now survives API restarts, and the versioned
resilience scenario demonstrates nominal SWAP, an uncertainty-triggered HALT
and recovery with an exportable audit trail.

Version 5.24 adds the
[`three-lane evidence architecture`](evidence_lanes.md). Real Sant'Anna WDM is
now usable as an optical-health replay, while measured QBER/SKR stays in the
assurance lane and Bell-fidelity control stays simulated. The dashboard shows
those boundaries and the registered negative WDM forecasting result directly.

Version 5.27 adds the
[`safe frozen-model replay`](wdm_model_bundle_and_replay.md). A server-side NPZ
bundle is loaded without pickle, fully hash/shape validated and replayed only
in shadow mode through the frozen v5.26 task policy. The chart exposes
prediction, uncertainty, OOD and intended/effective actions together with the
negative `0/3` registered results. The browser cannot upload weights or choose
a filesystem path, and the panel cannot approve a controller.
