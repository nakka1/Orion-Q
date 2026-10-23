```text
       #####                      ####                                           =   ==== ===
   #############                 #####                                        == =    ==   =====
 ######     #####                                                            ===     ==    =    =
## ##         ####    #### ####   ###       ########      #### ######       ==      ==    = = = = =
# ##           # ##   #  ######   # #     ## ###### ##    #  ####### ##    ==     ===== == =     ==
# #            #  #   #  ##       # #    ####      # ##   #  #      # #    == = =  =   ==  =   ====
# ##           #  #   #  #        # #   ## #       ## #   #  #      # ##   = =     =      ====   ==
## #           # ##   #  #        # #   #  #        # #   #  #      #  #   ===== == =   = =      ==
 ####         ####    #  #        # #   ## #       ####   #  #      #  #    ===   ==     =     ===
  ## #### #### ##     #  #        # #    ## ##   #####    #  #      #  #      = =  =   ==    ====    ===
    ###########       ####        ###      #### #####     ####      ####        =======  =  ========== ==
                                                                                    ======       =   ===
                                                                                               ===  ==
                                                                                              ======
                                                                                                ==
```

# OrionQ — Quantum Network Control and Digital Twin

**Version 5.66.2 · Windows and Linux · Apache License 2.0**

OrionQ is a research platform for operating and studying quantum-network control. It combines a persistent topology, causal protocol simulation, optional machine-learning controllers, authenticated services, a native desktop interface and a live terminal dashboard.

The backend distinguishes classical control communication from the quantum payload. Simulated and recorded inputs use shared contracts for validation, processing, resource management, metrics and evidence. Source provenance remains visible: a simulated observation is never presented as a physical measurement.

**Machine-learning models are optional. Physical actuation remains locked. This release does not establish hardware qualification or TRL 5.**

## Current release

Version 5.66.2 includes:

- Asynchronous worker shutdown and source switching that retain resources until draining finishes.
- Atomic pair consumption through durable reservations, with accurate published state and resource admission checks against duplicate ownership and capacity overruns.
- Reproducible route planning with a fixed budget of 24 episodes per policy and an explicit seed reset, replacing the former 0.12-second wall-clock cutoff.
- A larger commissioning cache, from 16 to 512 entries, without changing the physics formulas or random-number generator.
- Per-link identification of processing, input provenance and the physics model in use.
- Portable public test fixtures, corrected OpenStreetMap attribution and removal of the unused `Quantum.otf` font from source and packaging.

See the [release report](docs/results/orionq_release_5_66_2_20261012.md) and the [concurrency and routing results](docs/results/orionq_concurrency_and_routing_20261012.md) for the exact scope, earlier failures and remaining limitations. These detailed reports are currently in Portuguese.

## Capabilities

| Area | Implemented behavior | Qualification boundary |
|---|---|---|
| Network control | Topology, demand handling, route planning, resource reservation, retries and traceable outcomes | Centralized durable control; no claim of distributed consensus or universal device-side fencing |
| Classical and quantum channels | Explicit channel association, validated observations, control receipts and protocol processing | Physical source identity must be verified by the authorized adapter or service |
| Quantum protocols | Entanglement generation and swapping, memory storage/decoherence, BBPSSW purification, TF-QKD and satellite-link models | Simulation and conditional estimates; no certified secret keys from simulated material |
| Satellite communication | Orbital propagation, visibility, pointing, Doppler, link budgets and separate engineering/science inputs | Public-data and local contract validation; no connected SDR, OGS or qualified optical payload in this campaign |
| Optional ML | Reviewed model intake, bounded recommendations, baselines, approval and rollback workflows | Models are off unless explicitly selected; package integrity alone does not authorize activation |
| Desktop | Windows/Linux Qt console, GEO Map/Globe, details, charts, telemetry and evidence inspection | Native automated checks passed; some pauses and performance limits remain |
| Terminal | Live overview, links, nodes, satellites, reservations, audit and mixed-network views through the authenticated API | Linux x86_64/WSL2 validated; not an ARM/PetaLinux hardware qualification |
| PS/PL integration | Replaceable acquisition workers, binary contracts and an emulated PL gateway | DMA, FPGA timing closure and physical fast-gate control remain unqualified |
| Security and KME | Backend identity/authorization, TLS 1.3 mutual authentication, AES-GCM storage, authenticated audit and conservative key-delivery recovery | Operational PKI, equipment safety and experimental QKD security require deployment-specific qualification |

IBM Quantum service integrations were discontinued. Qiskit/Aer remains available for local simulation; it does not submit IBM hardware jobs.

## Quick start: desktop from source

Python 3.12 is the validated release environment. The package metadata currently permits Python 3.10 or newer; that range is not a claim that every interpreter/platform combination has been qualified.

Create an isolated environment from the repository root:

```bash
python -m venv .venv
```

Activate it on Linux:

```bash
source .venv/bin/activate
```

Or activate it in Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install the desktop, satellite and development dependencies:

```bash
python -m pip install -e ".[console,satellite,dev]"
orionq --version
orionq
```

Select the demonstration input to explore the simulated network. Recorded telemetry and remote service modes require their own configuration and authorized access. See the [branding and version guide](docs/product/orionq_branding.md) and [local workspace guide](docs/product/local_workspace.md).

## Linux service and live terminal dashboard

The controller can run independently of the desktop or an operator session. Login identifies and authorizes the operator's actions; logging out does not stop an independently running core service. Routing, resource management and KME use the provisioned service identity rather than the Windows desktop session.

The headless packaging profile separates Qt and optional ML dependencies from the service. The [Linux service guide](docs/product/orionq_linux_service.md) describes source preparation, provisioning, storage permissions and certificate handling. The [terminal guide](docs/product/orionq_terminal_operator.md) describes the dashboard and keyboard interaction.

Inspect the available commands:

```bash
orion-core --help
orion-operator --help
orion-pl-gateway --help
```

After provisioning a private service configuration and authorized client certificates, start the core and connect a terminal client:

```bash
orion-core --config /etc/orionq/core.json serve

orion-operator \
  --url https://orion-core:8765 \
  --ca /etc/orionq/client/ca.pem \
  --cert /etc/orionq/client/operator.pem \
  --key /etc/orionq/client/operator-key.pem \
  watch --layout dashboard --view overview --interval-s 1
```

The hostname, port and file paths above are deployment examples. They must match your provisioned configuration and certificate names. Keep private keys and credentials outside Git; use a secure credential provider when encrypted client keys require one. The service starts without an active data source; starting it does not manufacture telemetry.

The dashboard presents backend observations. A one-second display interval does not guarantee one-second scientific processing under load. For embedded integration, see the [PS/Linux–PL gateway](docs/product/orionq_pspl_linux.md), [PetaLinux layer](docs/product/orionq_petalinux.md) and [mixed acquisition/SeQUeNCe controller](docs/product/orionq_mixed_controller.md).

## Native packages and builds

The qualified binaries are separate release assets and are not included in this source repository.

| Platform | Package |
|---|---|
| Windows x64 desktop | `OrionQ-5.66.2-native-windows-x64.zip` |
| Linux x86_64 desktop | `OrionQ-5.66.2-linux-x86_64-gui.tar.gz` |
| Linux x86_64 headless | `OrionQ-5.66.2-linux-x86_64-headless.tar.gz` |

Extract the complete Windows package and run `OrionQ/OrionQ.exe`, keeping its `_internal` directory. On Linux, extract with `tar -xzf` and keep the internal directories. The desktop entry point is `./OrionQ/OrionQ`; headless entry points are `./OrionQ-headless/orion-core`, `orion-operator` and `orion-pl-gateway`.

For a new Windows build:

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_windows_console.ps1
```

For the Linux build options:

```bash
python tools/build_linux_native.py --help
python tools/qualify_linux_native_release.py --help
```

The [release report](docs/results/orionq_release_5_66_2_20261012.md) records artifact hashes, build environments and validation profiles. Dependency ranges are not a reproducibility lock; retain each build's freeze and environment evidence.

## Validation evidence and remaining limits

The 5.66.2 campaign reported:

| Verification | Result | Scope |
|---|---:|---|
| Windows source regression | 4,848 distinct passes; 24 skips | 31 groups; optional dependencies/hardware account for skips |
| Windows executable self-test | 39/39 | Native console, topology, readers, technologies, ticks and shutdown |
| Linux source regression | 1,789 passes | 43 modules; private test state on a 4 GiB tmpfs |
| Linux build-tool regression | 19 passes | Version binding, resume isolation and watchdog validation |
| Linux native desktop | 39/39 plus startup checks | Qt offscreen on ext4/WSL VHD; 113 scientific-input checks |
| Linux native headless | Passed | Mutual TLS, PTY, color modes, revocation, core continuity and emulated gateway; fixtures in RAM |
| Frozen package contents | 80 modules and 27 resources checked | Code/resource identity, archive bytes, modes and entry points |

Windows 11 and Ubuntu 24.04/WSL2 x86_64 were tested on a Ryzen 7 5700X with 32 GiB RAM. The project and Linux VHD were on a mechanical HDD. WSLg/Wayland checks do not replace qualification on an independent Linux workstation.

**Performance remains conditional.** The full global topology's scientific 1 Hz target (G05) is still open. Source measurements reported a 7.19-second median step for the global topology and 0.400 seconds for the smaller metropolitan scenario; these are source measurements, not benchmarks of the new binaries. Linux offscreen startup recorded a 20.57 ms heartbeat p95 and an 841.97 ms maximum pause. Headless global configuration also exceeded a 15-second client deadline on the HDD. RAM-based functional passes do not demonstrate HDD performance or power-loss durability.

No Swabian bench, SDR/OGS or physical FPGA was connected for these release checks. ARM/PetaLinux, operational quantum-key generation, external hardware interoperability and TRL 5 remain unqualified. Optional proprietary SDKs, licenses and unavailable original model archives are not recreated or supplied by this repository.

Scientific comparisons are documented separately:

- [SeQUeNCe/optional NetSquid cross-validation](docs/product/orionq_cross_simulators.md): SeQUeNCe scenarios were run; NetSquid remains unavailable without an authorized SDK.
- [Complete-network SeQUeNCe campaign](docs/results/orionq_sequence_network_20261010.md): includes resource contention and classical callbacks; it does not externally qualify every satellite or TF-QKD model.
- [Hybrid qualification](docs/product/orionq_hybrid_qualification.md) and [model remediation](docs/product/orionq_hybrid_remediation.md): analytical references, conditional finite-key models and explicit assumptions.
- [TRL 4 acceptance protocol](docs/product/orionq_trl4_qualification.md) and [TRL 5 preparation](docs/product/orionq_trl5_qualification.md): evidence packages require independent review; software tests do not automatically assign a TRL.

## Testing and reproduction

Install analysis dependencies for the scientific workflows:

```bash
python -m pip install -e ".[analysis,dev]"
python -m pytest -m unit -q
python -m pytest tests/test_physics_regression.py -q
```

These are useful subsets, not the complete release qualification. The [release report](docs/results/orionq_release_5_66_2_20261012.md) and campaign tools identify the exact corpora and native checks used for acceptance. Hardware, proprietary SDKs and model-specific inputs remain explicit dependencies.

For the author-model campaigns, provide the original pinned archives and a compatible SeQUeNCe checkout where required. Completed campaigns can be resumed. See the [author-model evidence registry](docs/thesis/orion_q_platform_claim_registry.json) and [Windows campaign validation](docs/results/orion_q_windows_validation_20261001.md).

```powershell
powershell -ExecutionPolicy Bypass -File tools\run_official_campaigns.ps1 -QnetZip F:\qnet_network.zip -QkdZip "$env:USERPROFILE\Downloads\modelo_qkd_ml.zip" -AuthorZip "$env:USERPROFILE\Downloads\Qnet-Network ML.zip" -Workers 4
```

Do not equate simulation, recorded telemetry replay, an emulated instrument, a resource reservation or a hash match with a measured quantum result. Bell-fidelity/controller targets and many quantum outcomes remain simulated. See the [validation levels](docs/validation_levels.md) and [telemetry provenance](docs/telemetry.md).

## Repository layout

```text
src/              Controller, protocols, security, adapters and interfaces
packaging/core/   Headless service packaging and deployment examples
packaging/        Desktop and platform packaging resources
configs/          Reviewed scenarios and qualification configurations
tests/            Regression, integration, scientific and native UI checks
tools/            Campaign runners, benchmarks and packaging utilities
experiments/      Research workflows and result consolidation
external/         Vendored author packages and their provenance
fpga/orionq_pl/   PL contracts, RTL models and testbenches
docs/             Contracts, research, runbooks, results and thesis sources
evidence/         Selected portable release evidence
```

`release-source-manifest.json` records the selected files and hashes for the Git delivery. Private state, credentials, SQLite databases, virtual environments, proprietary SDKs and compiled application packages are excluded. `GIT-RELEASE.md` and `SOURCE_RELEASE.md` explain the source snapshot and separate binary delivery; these supplementary documents are currently in Portuguese.

This Git delivery is an independent snapshot. The original development repository, its branches, uncommitted work and full history remain preserved separately. No GitHub remote or upload is configured automatically.

## Documentation and contributing

Start with the [modular protocol contracts and research](docs/product/orion_q_modular_quantum_protocols.md), [security guide](docs/product/orion_q_security.md), [concurrency guarantees](docs/product/orionq_controller_concurrency.md) and [development history](docs/history.md).

The [previous README snapshot](docs/readme_snapshot_before_english_20261012.md) preserves the detailed release chronology and research narrative without changing its bytes. It contains historical and mixed-language content; relative links within that snapshot refer to the original repository root. Current behavior and qualification limits are summarized above and in the 5.66.2 report.

For contributions, preserve data provenance, event causality, optional-model defaults, authorization checks and physical safety boundaries. Include focused tests and reproducible evidence for behavioral or scientific changes. Never commit secrets or claim hardware validation from synthetic inputs.

## License and attribution

OrionQ is licensed under the [Apache License 2.0](LICENSE). Third-party code, models, datasets, fonts and map data retain their own licenses and attribution requirements. Review their bundled notices and provenance before redistribution. OpenStreetMap attribution must remain visible where its data is used.

**Sponsored by SENAI CIMATEC - LAQCC/QuIIN**.
