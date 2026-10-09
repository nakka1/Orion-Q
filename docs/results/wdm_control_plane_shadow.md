# WDM Model-Driven Control-Plane Shadow Replay

## Status

This is an exploratory product-integration result on one simulated seed. It is
not a confirmatory model comparison and does not validate prediction of a
measured quantum outcome.

Both runs used the same `QuantumNetworkDatasetV3` trace (`seed=42`), WDM-only
features, a 20-sample causal window, a chronological 75/25 development/test
split and 25 held-out shadow cycles. Within the development prefix, 15% of the
windows were reserved after model fitting to estimate a global Gaussian sigma
as residual RMS. The held-out true simulated fidelity was never passed to the
controller.

## Exploratory result

| Model | Held-out MAE (25 replay rows) | Calibration sigma | OOD cycles | Authorized cycles | Effective action |
|---|---:|---:|---:|---:|---|
| EdgeLSTM | 0.3802 | 0.4449 | 0 | 0/25 | HALT 25/25 |
| Fidelity DualHead | 0.2755 | 0.3745 | 0 | 0/25 | HALT 25/25 |

The DualHead's descriptive MAE is 27.5% lower in this single run, consistent
with the motivation for separating pair availability from conditional
fidelity. This is not yet evidence of general superiority: there is one seed,
one short replay prefix and no multiplicity-controlled comparison.

Both calibrated uncertainties remain above the sandbox supervisor's fixed
demonstration limit of 0.20. The correct outcome is therefore a fail-closed
HALT for every cycle even though the policy's intended action can be SWAP. The
integration is working; the current models are not sufficiently certain under
this calibration and safety policy to authorize a non-HALT action.

## Reproducible artifacts

- EdgeLSTM: `outputs/wdm_control_plane_shadow_v523_rms/`
- Fidelity DualHead: `outputs/wdm_control_plane_dual_head_v523_rms/`

Each directory contains `summary.json`, row-level predictions, complete cycle
records, the SQLite journal and `manifest.json` with SHA-256 artifact hashes.
Every cycle records `physical_action_performed=false`.

## Interpretation and next test

This result supports only the software claim that existing WDM models can drive
the guarded sandbox with complete lineage. It identifies uncertainty and
availability discrimination as the next bottlenecks. The predeclared follow-up
should replay identical traces across seeds through persistence, point,
probabilistic and DualHead controllers, report calibration and resource metrics,
and retain HALT as the outcome when uncertainty exceeds the safety threshold.

