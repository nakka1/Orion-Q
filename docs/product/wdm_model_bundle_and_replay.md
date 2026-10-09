# Safe WDM Model Bundle and Dashboard Replay (v5.27)

## Purpose

The v5.27 lane turns an existing simulated WDM fidelity predictor into a
reproducible product artifact without presenting it as an approved controller.
The local dashboard replays a bounded, server-configured held-out trace and
shows prediction, uncertainty, OOD state, intended action, effective action and
the independent safety outcome. Physical execution remains impossible.

## Safe serialization boundary

The `.wdmbundle.npz` artifact contains only:

- a UTF-8 JSON manifest encoded as a one-dimensional `uint8` array;
- a UTF-8 JSON replay trace encoded as `uint8`;
- finite `float32` state tensors.

The loader uses `numpy.load(..., allow_pickle=False)`. It never accepts a Python
object, class path, callback or arbitrary import. It reconstructs exactly one
local architecture from this allowlist: `EdgeLSTM`, `EdgeGRU`, `EdgeTCN`,
`EdgeLSTMProbabilistic` or `EdgeLSTMDualHead`. The output signature must match
the architecture.

Before inference, the loader verifies:

1. compressed and uncompressed size limits, duplicate entries and unsafe ZIP
   names;
2. optional externally pinned SHA-256 of the complete bundle;
3. exact manifest schema and the invariant `physical_action_available=false`;
4. architecture parameters and WDM input size;
5. exact state keys, shapes, `float32` dtypes, finite values and per-tensor
   SHA-256 hashes;
6. the semantic hash of the reconstructed PyTorch state;
7. ordered feature names, training-only bounds, dataset/config hashes and
   target semantics in `WDMModelSpec`;
8. replay hash, record count, unique request IDs and temporal-window length;
9. the frozen v5.26 link-purification policy and its derived SHA-256;
10. a scientific status that cannot claim physical validation, paired measured
    WDM/quantum validation or approval for control.

## Reproducible local artifact

From the repository root:

```powershell
$env:PYTHONPATH = "$PWD\src"
.venv\Scripts\python.exe experiments/build_wdm_model_bundle.py
$bundle = "outputs\wdm_model_bundle_v527\edge_lstm_dual_head.wdmbundle.npz"
$bundleHash = (Get-FileHash -LiteralPath $bundle -Algorithm SHA256).Hash.ToLower()
```

The builder uses predeclared v5.26 seed `809`, trains the three existing LSTM
candidates under the same fit/calibration split, freezes the DualHead model and
its calibration-only task policy, and embeds up to 50 uniformly spaced windows
from the held-out suffix. Uniform spacing is chosen without inspecting target,
prediction or OOD outcome. The retrospective simulated fidelity is stored
outside the inference request.

Start the loopback API with a server-side path and, preferably, an external
hash pin:

```powershell
.venv\Scripts\python.exe experiments/run_qkd_assurance_api.py `
  --wdm-model-bundle $bundle `
  --expected-wdm-bundle-sha256 $bundleHash `
  --wdm-replay-max-cycles 100
```

## API

`GET /api/v1/control-plane/model` reports validation state, bundle/model/policy
hashes, architecture, feature order, replay size and scientific restrictions.
It never returns the configured filesystem path or weights.

`POST /api/v1/control-plane/model-replay` accepts only:

```json
{"start_index": 0, "limit": 25}
```

Both integers are bounded by the server configuration. The trace is loaded
server-side; an HTTP caller cannot submit a model or local path. Each cycle runs
in `shadow`, uses the v5.26 `link_purification` policy and calibrated uncertainty
ceiling, records bundle/model/window/policy hashes and enforces
`physical_action_performed=false`.

## Scientific interpretation

The panel deliberately exposes the negative registered result: none of the
three neural candidates beat persistence after multiplicity correction in
v5.25 or v5.26 (`0/3` in both campaigns). The bundle is therefore useful for
reproducibility, explainability, safety demonstrations and product evaluation;
it is not evidence that WDM predicts a measured future quantum outcome and is
not approved for network control.
