# Desktop Quantum Network Control Plane

> This page documents the v5.34 browser-based package. Since v5.35 the default
> desktop entry point uses the [native PySide6 console](native_console.md).
> Use `--ui web` for the separately built browser interface; the native portable
> distribution does not include the static website.

## Product boundary

Version 5.34 packages the local dashboard and non-physical controller as a
Windows application. `NISQPredictiveTwin.exe` starts one HTTP server bound to
`127.0.0.1`, serves the static interface and API on the same origin, opens an
Edge application window when available and keeps mutable state under
`%LOCALAPPDATA%\NISQPredictiveTwin`.

This is a usable control-plane sandbox, not a physical network controller. It
can mutate only the simulated topology and its local SQLite journal. There is
no optical-device, QKD-appliance, FPGA or IBM QPU actuation adapter in this
binary.

## Included capabilities

- persistent Alice–repeater–Bob simulated topology;
- HALT, WAIT, PURIFY and SWAP intents;
- independent fail-closed safety supervisor;
- simulation, shadow and assurance execution modes;
- Qiskit/Aer-backed simulated purification and swapping;
- frozen, hash-validated WDM DualHead shadow replay;
- exact-grid assurance for measured IrelandQCI QBER/SKR cells;
- formal claim/evidence validation and downloadable audit records;
- local immutable-hash decision journal;
- Nocturne-inspired compact command-center interface.

The static interface and API share one origin. Static path traversal is
rejected, dotfiles are not served, responses use `nosniff`, frame denial,
no-referrer and a restrictive content-security policy. Server creation rejects
non-loopback hosts.

## Build

```powershell
.\tools\build_windows_exe.ps1
```

The build performs a Vinext static export with
`NEXT_PUBLIC_ASSURANCE_API_URL=/api/v1`, runs desktop/API tests, invokes the
versioned PyInstaller specification, writes an executable SHA-256 file and
creates a compressed Windows x64 package. Generated releases are ignored by
Git; source, specification and build script are tracked.

The current one-folder form is intentional. Torch, Qiskit Aer, NumPy, Pandas
and their native libraries are kept beside the executable, avoiding the slow
startup and opaque extraction behavior of a single-file binary.

## Runtime behavior

The default URL is `http://127.0.0.1:8765/`. The console reports the URL,
control boundary and journal path. Pressing Ctrl+C or closing the console stops
the service. `--no-browser` supports diagnostics, and `--state-root` can place
the journal in a controlled test directory.

The bundled WDM model is loaded only from the server-configured, non-pickle NPZ
artifact. Its replay remains shadow-only and labels the Bell-fidelity target as
simulated. The program does not convert measured BER, QBER or SKR into measured
Bell fidelity.

## Gate to physical control

Live network control requires a separate, reviewed implementation of all of
the following:

1. vendor-specific inventory and telemetry adapters;
2. mutually authenticated command transport and credential isolation;
3. typed physical action receipts with idempotency and reconciliation;
4. role-based operator authorization and emergency-stop ownership;
5. hardware-in-the-loop acceptance tests for every supported action;
6. synchronized WDM–quantum measurements to calibrate prediction and safety;
7. deployment-specific rollback, redundancy and incident procedures.

Until those gates pass, the physical interlock remains closed by construction.
Changing a UI label or adding credentials cannot enable a physical executor.
