# ORION-Q: institutional console and interchangeable input (v5.38)

**Live network update (2026-10-05, implemented):** the console now shows a live
network computed by the backend engine (`quantum_autopilot.network_engine`):
Topology opens on the **GEO** map of Salvador, nodes without location wait in a
queue under it, a click on a node opens a floating window with the stability
wave of each connection, and fibres, isolation, recalibration, pairs and
locations are backend verbs read back from the next snapshot. The login screen
offers **Demonstration** — the same service, engine and provider as normal
operation on its own journal, fed by the backend's simulated INPUT source; only
the input is simulated. Contract: [live network design](orion_q_live_network_design.md);
engine: [network engine](orion_q_network_engine.md); screens:
[native console](native_console.md); operator walk-through in Portuguese:
[guia de demonstração](orion_q_demo_guide.md). Where the notes below disagree
with those documents (GEO needing every node located, in-memory shadow
provisioning as the only way to add a node, the single node popover), the newer
documents hold.

**Workspace design update (2026-10):** the console now reproduces the approved
"ORION-Q Workspace" prototype. The chrome is shared by the whole application:
a 52 px top bar, a 60 px icon rail with the six screens, a collapsible
journal/terminal and a 30 px status bar. Dark (default) and light themes use the
prototype's tokens; type is Inter and JetBrains Mono only. A command palette
(Ctrl+K) and the login data-source selection are described in
[the native console guide](native_console.md). The v5.38 and earlier notes below
are kept as history; where they describe the 150 px rail, Chakra Petch / Oswald /
Noto Sans JP, the old amber palette or the profile overlay, they are superseded.

**v5.38 update (superseded visual contract):** the native console implemented the supplied Pattern B
structure: a 150 px six-route rail, Chakra Petch headings, the muted amber /
green / red / violet palette, click-only profile and node overlays, GRAPH / GEO /
MATRIX topology modes, shadow node provisioning and aggregate-only Edge
Control. These are UI capabilities, not evidence of physical hardware.

**v5.37 update:** F6 / DATA SOURCE now replaces the input in the same window.
The old separate-laboratory-window workflow described in this historical
v5.36 guide is superseded by the [input plug guide](input_sources.md).

ORION-Q is the product name of the native local console. The research packages
`quantum_twin` and `quantum_autopilot` retain their existing names and APIs.
The scientific claims, frozen WDM model and measured QKD evidence are unchanged.
See [the service integration boundary](native_console.md).

## Visual contract

The approved workspace prototype is a visual reference, not a source of
hardware capabilities; its fixture values (operator ids, campaign ids, HSM,
terminal and build numbers) are not reproduced. The console uses the prototype's
tokens in two themes. Dark (default): ground `#141413`, raised `#1a1a18`, ink
`#faf9f5`, accent `#d97757`, nominal `#7fb79a`, fault `#c05a6c`, quantum
`#8b81c4`. Light: ground `#faf9f5`, raised `#f1efe8`, ink `#141413`, accent
`#c15f3c`, nominal `#4f8a6b`, fault `#b04459`, quantum `#6a5fb0`. Corners are
square and borders are 1 px. The full token table is `console/theme.py`.
Bundled Inter (text, 13.5 px base) and JetBrains Mono (identifiers, labels,
buttons, tables) remove dependence on installed fonts. Chakra Petch, Oswald and
Noto Sans JP are no longer loaded by the interface; their files and licences
remain under `src/quantum_autopilot/console/fonts` with the font manifest.

Live content follows the existing service contracts. The reference's fictional
authentication, HSM, signatures, hardware quorum and measured fidelity claims
are intentionally not represented as working capabilities. Local entry is not
login. This is a responsive adaptation, not a pixel-identical screenshot at
every window size.

The logical canvas follows the viewport aspect ratio. Default text is never
scaled below its design size. At the 1440 x 900 reference frame the interface
renders at the prototype's own sizes; larger windows enlarge it uniformly (up
to 1.75x); below 1280 x 800 the window scrolls rather than making text smaller.
A screen body that cannot fit between the top bar and the journal scrolls
inside the workspace. Drag the terminal's upper divider to resize it; close it
with `×` and reopen it with **Terminal** in the status bar or **Ctrl+J**. Maps,
charts and tables follow the space available, and terminal records, command
history and unfinished input are kept. The header also collapses the terminal.
Topology LINKS uses a virtualized
table with accessible final rows, tooltips and independent scrolling. Key,
audit and node logs also scroll. The graph validates identifiers and coordinates.

## Running

Extract the entire portable ZIP and launch `ORION-Q/ORION-Q.exe`; keep
`_internal` adjacent. No Python or Node installation is needed. The portable
binary is unsigned. From source, install `.[console]` and run `orion-q`.
Existing `quantum-twin-console` aliases remain available.

- F11: toggle fullscreen; Esc: return to a normal window.
- Ctrl++ / Ctrl+-: change zoom; Ctrl+0: automatic zoom.
- 1–6: screens; Ctrl+K: command palette.
- Ctrl+J: close or reopen the terminal. Focus its divider and use Up/Down to
  resize it; Shift increases the step, Home or double-click resets its height.
- F6, the source button or Sign out: return to the login screen to choose the data source.
- Pause/resume, inject/clear dropout and run/cancel controller tests are palette
  entries and terminal verbs (`pause`, `dropout`, `run_scenario`, `cancel`),
  listed only when the active source declares them.

State defaults to `%LOCALAPPDATA%/ORION-Q`. Old NISQPredictiveTwin state is
neither deleted nor automatically migrated. Labs use separate UUID directories;
each controller stress run creates another exclusive directory and journal.
An explicit `--state-root` remains available for controlled diagnostics.

```powershell
ORION-Q.exe --demo
ORION-Q.exe --demo --qnet-archive qnet_autopilot.zip --qkd-archive qkd_autopilot.zip
ORION-Q.exe --synthetic load --seed 1337
ORION-Q.exe --stress-test reports --stress-rounds 20 --seed 1337
ORION-Q.exe --self-test reports/self-test.json --state-root test-state
```

## Two distinct test layers

| Layer | What runs | What it does not establish |
| --- | --- | --- |
| Artificial UI stream | Seeded curves, warnings, node/link lists and unsigned records; reference: 8 nodes/12 links/32 records; load: 32/256/500; faults: 12/36/120 with dropout/recovery. | Photonic physics, measured fidelity, QKD keys, or control of a 32-node network. |
| Controller fault campaign | Existing controller, supervisor, SQLite journal, shadow cycles and separate three-node resilience simulator; safety-supervised probes of HALT/WAIT/PURIFY/SWAP. | Physical execution, quantum processors, FPGA, HSM, authentication or hardware timing. |

## Topology and operator overlays

- GEO is the main view. It draws the offline map of Salvador to scale and each
  node at its operator-declared WGS-84 coordinates; a node without coordinates
  is not placed by guesswork — it waits in the queue under the map with its
  connections. Logical layout coordinates are never reinterpreted as geography.
- MATRIX lists the same nodes and the direct fibre of each pair (the backend's
  live fidelity estimate, length and status). A dot means no direct fibre,
  `MULTI` parallel fibres. No end-to-end, swapped or multi-hop fidelity is
  calculated by the UI.
- GRAPH uses the logical coordinates from the snapshot.
- A click on a node selects it and opens its floating window (several can be
  open; Esc or `✕` closes the topmost). Hover shows a tooltip and never opens a
  window. Selecting does not put the provider in the busy state. There is no
  profile panel: the operator label and the unsigned-session statement are on
  the login screen.
- NODES sends `add_network_node` (location optional) to a source with a network
  engine; the node appears when the backend confirms it. A source without one
  keeps the older shadow record. Credentials are neither sent nor persisted.
- EDGE CONTROL shows the model registry, the decision feed and, in the
  demonstration only, the input-event control; below them the aggregate
  journal actions. The decision-source toggle is journalled, but remains
  unbound to the runtime. FPGA identity, per-shot telemetry, timing margin and
  bitstream flashing remain unavailable.

Artificial snapshots are explicitly marked SYNTHETIC/SIMULATED and are not
added to the scientific evidence registry. Display signals are not a calibrated
channel model. Exported hashes are identifiers, not digital signatures. The
lab's ADVANCE TEST FRAME is not WDM inference; normal-mode replay still invokes
the bundled model. Controller tests run in a worker and refuse overlapping runs.
Cancellation is cooperative between rounds; cancellation never reports PASS.

## Verified fault and coverage

The campaign exposed an idempotency bug: omitted provenance timestamps were
regenerated at parsing time, changing the hash of an identical request. The
snapshot contract now inherits its own stable timestamp when provenance omits
one, preserving explicit provenance timestamps. A regression test checks both
the parsed snapshot and the journal replay.

On 2026-09-09, seed 1337 and 20 rounds passed 535 checks over 140 shadow cycles
(20 SWAP, 120 HALT). Additional explicit executor probes exercised all four
actions in simulation; these are separate from policy-selected actions.
Checks include stale telemetry, uncertainty, OOD, integrity failure, interlock,
empty memory, invalid numbers, exact retries, conflicting retries, physical
refusal, scenario persistence and journal reopening.

The automated offscreen matrix tests six screens at 1280x800, 1920x1080,
2560x1440, 3440x1440 and 1024x768, plus the last of 256 links, loss/recovery
and 500-row scrolling. These are bounded software checks, not a claim that all
possible operating conditions or physical displays have been validated.
Human acceptance on the target computer, mixed-DPI/multiple monitors, prolonged
soak testing and accessibility review remain appropriate follow-up gates.

## Reproduction

The final source suite passed 996 tests in 295.05 seconds on 2026-09-09.
The existing joblib physical-core detection warning did not fail the run.

Run `tests/test_orion_console.py` together with the existing console, desktop
and control-plane tests. `--self-test` also runs the responsive matrix inside
the frozen executable. `tools/build_windows_console.ps1` includes these tests
before packaging. Generated diagnostic reports and screenshots belong in
isolated output directories and are not experimental evidence.

The final frozen package also passed all 18 top-level self-test checks (44
nested responsive checks) and the 20-round/535-check controller campaign.
PURIFY exposed missing Qiskit distribution metadata used for transpiler plugin
discovery. The specification now includes it, and the Windows build requires
the frozen all-actions stress test. For this local delivery, the eight metadata
files were assembled from the same build environment according to that rule
and byte-verified before rerunning both packaged diagnostics. No manual repair
is needed after extraction.

Unexpected frozen-entrypoint failures return code 2 and write `orion-error-*.log`
under `--state-root`, `--stress-test`, or `%TEMP%/ORION-Q-diagnostics`.
Interactive Windows acceptance was attempted, but the desktop inspection tool
could not locate the launched test window. Fullscreen and mixed-DPI visual
acceptance on the user's monitor therefore remain open; offscreen matrix
results must not be presented as completion of that human acceptance gate.
