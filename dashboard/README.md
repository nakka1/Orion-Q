# NISQ Predictive Twin — Quantum Network Control Plane

Local dashboard for the NISQ Predictive Twin product. It controls the persistent
simulated topology, runs supervised control cycles and keeps physical actuation
locked.

## Windows executable

From the repository root, build the static UI and one-folder executable:

```powershell
.\tools\build_windows_exe.ps1
```

The script runs the desktop/API tests, embeds the validated evidence bundle,
frozen DualHead replay model, Qiskit/Aer simulator and Python runtime, then
writes an ignored release under `release/windows/`. Execute
`NISQPredictiveTwin.exe`; mutable journal data is stored under
`%LOCALAPPDATA%\NISQPredictiveTwin`. The server binds only to
`127.0.0.1:8765`.

The generated `.exe` is a local simulator/control-plane product. A physical
network adapter, vendor authentication, authorization policy and hardware
acceptance campaign are still required before live actuation can exist.

## Development mode

From the repository root, start the API:

```powershell
$env:PYTHONPATH = "$PWD\src"
.venv\Scripts\python.exe experiments/run_qkd_assurance_api.py
```

To expose the local Sant'Anna recording in the **WDM Optical Health** lane,
configure the path on the server. The browser cannot submit or guess a local
dataset path:

```powershell
.venv\Scripts\python.exe experiments/run_qkd_assurance_api.py `
  --optical-wdm-csv ..\optical-failure-dataset\HardFailure_dataset.csv `
  --optical-evidence-report outputs\santanna_wdm_information_reference\report.json `
  --expected-optical-dataset-sha256 3d55f31644e8387ab9092bba232dae99ba8a90788d4157a599e9e3f12375f12d
```

The three dashboard lanes are deliberately non-interchangeable: measured WDM
reports optical state, measured QKD reports QBER/SKR assurance, and only the
simulated Bell-fidelity lane can reach the non-physical control sandbox.

To enable the v5.27 frozen-model panel, first build the reproducible simulated
DualHead artifact. The bundle contains JSON and numeric NumPy tensors only;
the loader never unpickles a model object:

```powershell
.venv\Scripts\python.exe experiments/build_wdm_model_bundle.py
$bundle = "outputs\wdm_model_bundle_v527\edge_lstm_dual_head.wdmbundle.npz"
$bundleHash = (Get-FileHash -LiteralPath $bundle -Algorithm SHA256).Hash.ToLower()
.venv\Scripts\python.exe experiments/run_qkd_assurance_api.py `
  --wdm-model-bundle $bundle `
  --expected-wdm-bundle-sha256 $bundleHash
```

The server validates the external bundle hash, architecture allowlist, tensor
names/shapes/dtypes/hashes, WDM feature order, preprocessing bounds, frozen
task-policy hash and replay-trace hash. The browser can request only a bounded
server-side replay; it cannot upload weights or select a local path.

Then start the UI:

```powershell
cd dashboard
pnpm install --frozen-lockfile
pnpm dev
```

Open `http://localhost:3000`.

Useful checks:

```powershell
pnpm lint
pnpm build
```

The API URL defaults to `http://127.0.0.1:8765/api/v1`. A different loopback API
can be selected at build time with `NEXT_PUBLIC_ASSURANCE_API_URL`.

The **Frozen model replay** section plots prediction, uncertainty, retrospective
simulated target, OOD state, intended action and effective action. It also shows
the v5.25/v5.26 `0/3` registered neural results and labels the model as not
approved. Replay is always `shadow` and never executes the quantum simulator.

The separate **Control plane** section is an explicitly manual control path
that runs HALT/WAIT/PURIFY/SWAP decisions in
`simulation`, `shadow`, or `assurance` mode and stores the records in
`outputs/control_plane/sandbox_journal.sqlite`. It has no physical mode or
physical executor. The same database retains the versioned Alice–repeater–Bob
simulation topology, topology-event hashes and idempotent scenario records.
The bundled resilience scenario demonstrates nominal SWAP, fail-closed HALT
under excess uncertainty, and recovery without representing a live network.
