# Native local console — ORION-Q v5.38

See the [ORION-Q guide](orion_q_console.md) for the responsive visual contract,
bundled fonts, isolated artificial laboratory and fault-injection coverage.
For the current same-window DATA SOURCE workflow and measured-input contract,
see the [interchangeable input guide](input_sources.md).

Ultrawide startup correction (2026-10-06): automatic zoom now uses the whole
client area and resolves scrollbar space before resizing the canvas. Fractional
scales no longer alternate between viewport sizes on 3440 × 1440 monitors.
The frameless chrome is retained. Cyclic collection is confined to the UI thread
before constructing widgets, with cleanup on failed initialization as well.
`ORION-Q.exe --startup-test report.json` exercises the ordinary startup, source
selection, all six screens, resizing, maximize/restore and terminal visibility
on a new isolated journal. `--startup-size 3440x1440` sets an explicit diagnostic
client size in logical pixels; DPI is still applied once by Qt. `--demo` selects
the ordinary simulated-input provider for this check. The Windows release gate
requires the actual Windows platform at 100%, 125%, 150% and 200% scale, and
demonstration startup at 100% and 150%. A bounded external process timeout is
required because a blocked GUI cannot run its own diagnostic timer. These are
local software checks; physical pointer gestures and a second computer remain
outside their scope.

Resize update (2026-10-06): the frameless Windows window sizes directly from
its eight client-side edge/corner grips, with explicit mouse capture and release,
instead of entering the Windows system sizing loop. Width and height remain
independent. Deactivation, lost capture, hiding a grip or movement without a
pressed button cancel an unfinished resize. Deferred topology work is bound to
its widget lifetime. During repeated resizing, the base map uses faster outline
rasterization; its complete antialiased render returns after 100 ms of stable
size. Node geometry and simulation updates are retained. The
[resize correction report](../results/orion_q_resize_fix_20261006.md)
records regression cases and the replacement Windows package.

Terminal update (2026-10-06): resize, close and reopen the terminal in the shared
workspace; records, drafts and the preferred height are retained. The
[terminal workflow report](../results/orion_q_terminal_workspace_20261006.md)
records the use cases and replacement package.

## Purpose and evidence boundary

The PySide6 console integrates the revised interface supplied for the TCC with
the existing local assurance service and simulated network controller. It is
an offline demonstrator for inspecting evidence, replaying a frozen WDM model,
exercising safety policies and exporting traceable records. It does not control
a physical quantum network and does not validate the central WDM-to-measured-
Bell-fidelity hypothesis.

The native release contains Python, Qt, Torch, Qiskit Aer, the frozen model,
the published QKD grid and the portable claim evidence. No Python installation,
browser or development server is required on the target Windows machine.
The portable ZIP must be extracted in full; the executable depends on its
adjacent `_internal` directory. The executable is not code-signed.

## Application chrome (workspace design)

The console reproduces the approved workspace prototype. One chrome frames the
whole application; a screen renders only its body.

- **Top bar (52 px):** pixel mark, `orion-q`, breadcrumb of the current screen,
  the `Ctrl K · Run a command…` launcher, the Light / Dark toggle and the
  data-source button, which returns to the login screen.
- **Icon rail (60 px):** six screens in fixed order — `[1] Topology`,
  `[2] Fidelity & decoherence`, `[3] Key management`, `[4] Nodes`,
  `[5] Edge control`, `[6] Access & audit` — and `Sign out`. Keys 1–6 select a
  screen.
- **Journal · terminal (resizable and closable):** drag its upper divider to
  change its height; the current screen, map and charts use the remaining space.
  Close it with `×`; reopen it with **Terminal** in the status bar or **Ctrl+J**.
  Closing retains records, command history and unfinished input. The header
  still supports collapse/expand. Arrow keys on the divider resize it; Shift
  uses a larger step, and Home or a double-click restores the default height.
  A compact window keeps the command prompt and a usable screen area accessible;
  the navigation rail scrolls when necessary. The preferred terminal height and
  visibility survive navigation and theme changes for the current session.
  The list shows only records delivered
  by the data source — the console-command lifecycle (verb, state, time) and
  the ledger entries, newest last. The interface never writes a row of its own
  there. A typed line is parsed (`verb target`) and dispatched through the same
  path as the buttons, after the source's declared capabilities allow it.
  Unknown or refused input is answered in a separate strip marked `INPUT`;
  that strip is feedback about the input, not a record.
- **Status bar (30 px):** status text; data-source label (its tooltip and click
  show the declared capabilities); `Physical locked`; campaign and snapshot time.
- **Command palette (Ctrl+K):** entries are generated from the current
  snapshot and capabilities — the six screens, `Select node …` / `Select link …`
  for the nodes and links that exist, the operations the source declares, the
  theme switch and sign out. An operation the source does not declare is not
  listed. With a live network the list also follows the backend state:
  `Recalibrate <node> (full)`, `Isolate <node>` or `Restore <node>` (whichever
  applies to the node's persisted state), `Disable fibre <id>` or
  `Enable fibre <id>`, `Request pair <demand>` (and `Purify` / `Consume` once the
  backend holds a delivered pair) and, in the demonstration only,
  `Inject input event <kind> on <fibre>`.
- **Toast:** a transient notice (2.6 s). It records nothing.
- **Themes:** dark (default) and light, with the prototype's tokens. Changing
  the theme rebuilds the widgets and re-applies the current snapshot, screen,
  selection and view state. It is refused while an operation is running or a
  secondary window is open.

Terminal verbs: `help`, `go <screen>`, `theme [dark|light]`, `select_node <id>`,
`select_link <id>`, `run_scenario`, `replay_model`, `export [scope]`, `reload`,
`pause`, `dropout`, `cancel`, `capabilities`, `sign_out`, and the live-network
verbs below. Each operation verb is refused unless the active source declares
the matching operation.

| Typed line | Backend verb | Request built from the snapshot |
| --- | --- | --- |
| `isolate <node>` / `restore <node>` | `set_node_isolation` | `{node_id, isolated, expected_version, topology_id}` |
| `recalibrate <node>` | `recalibrate_node` (full recalibration by the engine) | `{node_id, expected_version, topology_id}` |
| `enable <link>` / `disable <link>` | `set_link_active` | `{link_id, active, expected_version, topology_id}` |
| `locate <node> <lat> <lon> [place]` / `unlocate <node>` | `set_node_location` | `{node_id, latitude, longitude, place, …}` (`null` coordinates return the node to the queue) |
| `request_pair <demand>` · `purify_pair <demand>` · `consume_pair <demand>` | same names | `{demand_id}` |
| `define_pair <id> <a> <b> [min_fidelity]` · `remove_pair <id>` | same names | `{demand_id, a, b[, min_fidelity]}` / `{demand_id}` |
| `model_archive <qnet\|qkd> <path\|none>` | `set_model_archive` | `{kind, path}` |
| `inject <kind> <link> [strength] [duration_s]` | `inject_input_event` (demonstration only) | `{kind, link_id[, strength][, duration_s]}` |

The interface only names the operation and its subject. Whether it happens, and
what it does to the fibres and routes, is the backend's answer, read back from
the next snapshots: the command lifecycle in the journal
(`REQUESTED → RUNNING → CONFIRMED | UNKNOWN`), the node and fibre states, the
re-planned demands and the engine's events. A refusal (`ValueError` in the
backend — for example a topology change while a node is being recalibrated, or
enabling a fibre whose end is isolated) is shown in the status bar with the
backend's own message. For a source that does not declare the live verbs (the
developer fixtures), `recalibrate` and `isolate` dispatch what that source does
declare.

## Login and data source

The login screen is not authentication. It shows `Local session, unsigned. No
hardware authentication.`, the operator label supplied by the source, the
initialisation checks and build label reported by the source, and the execution
boundary derived from the source's declared capabilities (Simulation and Shadow
are `ENABLED` only when an operation of that class is declared; Physical is
`LOCKED`; Hardware access is `DISABLED`).

Three data sources can be marked: **Demonstration** (simulated input · real
pipeline), **Measured telemetry (local JSON plug)** (read only) and **Local
service** (operator workspace). Marking a row changes nothing; `Open workspace →`
applies the marked source and opens Topology. Returning to the login screen
(`Sign out`, the source button or F6) shows the active source as marked.

**Demonstration** is the same application service, the same network engine and
the same console provider as normal operation. Two things differ, both in the
backend: the state lives in its own journal
(`<state-root>/demo/sandbox_journal.sqlite`, initialised with the "Salvador
metro" reference topology and the demands P1–P4) and the engine's INPUT comes
from `SimulatedInputSource`. The only simulated thing is the input data; every
value, decision, command effect and journal record on screen is produced by the
backend processing. **Local service** opens the operator's journal and topology
with no input feed unless one is configured: fibres show their configuration,
routes are still planned by the backend, and every measured field is absent
("—"). The login checks include the journal in use, the engine tick, the input
source kind, how many model roles are `active` / `fallback` / baseline-only and
the size of the topology — all read from the backend.

The synthetic stream (`--synthetic`) and the mock fixture
(`python -m quantum_autopilot.console.app --provider mock`) are developer UI
fixtures with values generated on the UI side. They are not the demonstration,
are not offered on the login screen or in the palette, declare
`input_kind="synthetic"` and are labelled `SYNTHETIC · UI FIXTURE` by the
shell. No path leads from "Demonstration" to either of them.

## Live network view (`Snapshot.live`)

A service that carries a network engine publishes `Snapshot.live`, the UI-side
mirror of the engine's `orion.network-live.v1` state
([contract](orion_q_live_network_design.md), [engine](orion_q_network_engine.md)):
per fibre the status, QBER, Werner fidelity, key rate, sifted rate,
synchronisation residual, gate efficiency, polarisation residual, stability,
the 20 Hz stability wave of the last 12 s, the 1 Hz history of the last 300 s,
the attack detector, the decoy-BB84 block, the controllers in charge, the
classical admission and the provenance record (including whether each model
runs inside the domain it was validated in); per node the engine status,
location, incident fibres, demands routed through it and the running or last
recalibration; the demands with their route, policy and the backend's reason
when there is no route; the last 200 decisions with their hashes; the model
registry; the event feed. Numbers stay numbers and a value the backend did not
produce is `None` — the provider formats only the three link strings of the
topology table and never fills a gap.

Threads. One daemon thread (`console-network-step`) calls
`service.network_step()` once per second while the source is started; nothing
else in the console advances network time. The snapshot is rebuilt once per
second on the single service worker from cheap reads (the engine's last
published state is read without waiting for a tick). While a command occupies
that worker — a full recalibration drives its own network ticks, with the
stepping thread held and the same one-second pause between ticks, so it takes
about seven seconds and network time does not run ahead — the engine's state is
still published every 200 ms, so each calibration phase (`queued`,
`polarisation`, `sync`, `commissioning`, `memory`, `report`) appears on screen
as it happens. Selecting a node is view state: it is answered from the records
already read, without a backend command and without the busy state, so a click
on a node never disables the screens. Stopping or switching the source stops
the stepping thread and joins it. Automatic garbage collection is confined to the UI thread
(`console/ui_gc.py`): a collection on a worker thread that frees Qt wrappers is
a double delete in the Qt binding and used to end the process.

## Screens and operations

| Screen | Actual source and operation |
| --- | --- |
| Login | Local entry point and data-source selection; no authenticated identity or quorum claim. |
| Topology | The topology the source delivers (the operator's journal, or the demonstration's "Salvador metro"), on the map of Salvador, as a matrix and as a logical graph. Node and fibre operations are backend verbs on simulated state; see "Topology screen". |
| Fidelity & decoherence | With a live network: the per-fibre estimates of the last network second and the engine's 1 Hz history (fidelity, QBER, key rate), point for point; the "fibre down" line is the threshold the running engine publishes (`LiveState.policy`). Below it, the frozen WDM model's shadow replay (its fidelity target remains simulated). |
| Key management | With a live network: the decoy-BB84 session of the chosen fibre (clock, sifted rate, QBER, secret key rate, pool and discarded bits, the controller's last action). Below it, the published experimental QBER/SKR cells and exact-grid assurance. No key storage or KMS integration. |
| Nodes | The inventory of the source; the form sends `add_network_node` (location optional) and the detail reads the backend's node record, its fibres, recalibration report and log. No device is contacted. |
| Edge control | Model registry status, decision feed (newest first, by kind), demands and — in the demonstration only — the control that injects an event into the SIMULATED INPUT of one fibre. Plus the aggregate controller-journal actions and the model catalogue. No per-shot stream, FPGA flash or runtime binding is claimed. |
| Access & audit | Control cycles, safety events, topology-store events, engine decisions (with hashes) and engine events, console-command records, plus unsigned JSON export. No signatures, quorum or externally anchored hash-chain guarantee. |

### Topology screen

Topology shows one model of the snapshot in three views, in this order: **GEO**
(the main view; the workspace opens on it), **MATRIX** and **GRAPH**. A change
in the backend state — a fibre disabled, a node isolated, a location set — and
the selection appear in all three, because they are drawn from the same model
(`widgets/network_views.build_net_model`).

- **GEO** draws the offline vector map of Salvador (`console/geodata.py`: land,
  municipality outline, roads, place and water labels, the base map's
  attribution) and the nodes at their operator-declared coordinates. One scale
  maps metres to pixels on both axes, so nodes 10 km apart are drawn 10 km apart
  at the scale of the map; the scale bar is read from the same transform. Wheel
  zooms about the cursor, drag pans, `Reset view` returns to the default view
  (the placed nodes with a margin, or the municipality when nothing is placed).
  A fibre is selectable on its line or on its midpoint handle. The static base
  layer is cached per size, view and theme.
- **Queue (`Awaiting location · n`)**, under the view: nodes without location.
  No geographic line is drawn for an endpoint without declared coordinates.
  Click a queued node to inspect its retained connections; MATRIX and GRAPH
  also retain the logical inventory. `Set location…` (queue item, inspector, node
  window) opens a dialog with latitude, longitude and place; `Pick on map` fills
  the coordinates from a click on the map. The request is the backend verb
  `set_node_location`; the node moves onto the map when the next snapshot says
  it is located, with its data and connections.
- **MATRIX** lists the same nodes (located and unlocated) as rows and columns;
  a cell is the direct fibre of the pair: live fidelity estimate, length and
  status. A cell selects the fibre, a header selects the node. No swapped or
  multi-hop value is inferred.
- **GRAPH** keeps the logical layout (`x`, `y` of the source).
- **Header strip**: topology id and input kind, node counts by engine status,
  fibres in the effective routing graph, the layer (`Physical` /
  `Entanglement` — the selected demand's route with `END` / `BSM` marks) and
  the view. It stays on ONE line: when the row is too narrow the strip is
  compacted in steps — the two legacy actions (`Run simulated scenario`,
  `Replay WDM model`) fold behind a `⋯` button that opens them on a second
  line, `fibres in graph` becomes `fibres`, zero counts lose their word, then
  the `Layer` caption and the remaining words go (every word stays in the
  tooltips). At 1440 px the first step is enough.
- **Inspector (330 px)**: `Inspector` (node or fibre, with the operations the
  source declares), `Events n` (engine and topology events, newest first; n
  counts those of severity warning or critical), `Links` (table; the link
  editor, path runner and temporal campaigns open from here) and `Pairs` (the
  demands with status, route, plan numbers, policy, model state, the backend's
  reason when there is no route, the stored pair, the routing decisions, and
  `Request pair` / `Purify` / `Consume`). The footer shows the data source, the
  topology version and the live tick.
- **Node window**: a click on a node (map, queue, matrix, graph) selects it and
  opens a floating, movable, non-modal window; several can be open; each closes
  with its `✕` or Esc. It shows every field of the backend's node record, each
  incident fibre with its live values and controllers, the running or last
  recalibration (before / after per fibre), the demands through the node and a
  wave chart with one series per connection (`LiveLink.wave`, 20 Hz, last 12 s;
  repainted about 15 times per second).
  The chart draws only received samples: between two deliveries it reveals the
  newest second progressively (a presentation clock over real samples) and
  stops at the last sample when nothing new arrives. A fibre without data has
  no trace and its legend entry says why. Series differ by colour and dash
  pattern. Node windows are restored after a theme change.
- **Operations** (`set_node_isolation`, `recalibrate_node`, `set_link_active`,
  `set_node_location`, `request_pair`, `purify_pair`, `consume_pair`): a click
  dispatches the verb and shows `PENDING`; nothing on screen is changed by the
  click. The result is read back from the backend: the command lifecycle
  (`CONFIRMED` / `UNKNOWN` with the backend's message), the node and fibre
  states, the re-planned demands and the engine's events. A demand the backend
  cannot route (`no_route`, `blocked`) is shown with the backend's reason in
  the Pairs tab, in a banner on the map and in the node window. The reason is
  the backend's sentence, written to be read (`… 0 of 24 episodes delivered;
  episodes ended by: deadline × 21, max steps × 3`).
- **Labels on the map**: a node's label is drawn right of its marker; when
  that would cover another node or label it moves left, below or above
  (`figures.place_node_labels`). Only text moves, never a node.

A source without a network engine (`Snapshot.live is None`) still renders the
topology; live-only parts are absent or shown as "—".

Loading and commands run in a worker, with GUI-thread updates, busy state and
error reporting. There is no fictional progress timer. Empty service data
clears prior values, curves and events instead of restoring design fixtures.
Repeated scenario/model actions receive distinct request identifiers. Console
actions and exports are recorded in SQLite; exports use exclusive creation
and unique names.

## Scientific provenance

The bundled `experimental_cells.csv` is the existing derived published-grid
artifact. These are experimental condition aggregates, not newly collected
time-aligned WDM/quantum measurements. The bundled EdgeLSTM DualHead is the
existing simulation-trained model; packaging it does not retrain it on measured
fidelity or turn its predictions into measured outcomes.

`runtime_assets/native/v1/manifest.json` pins both distribution inputs to their
SHA-256 hashes and original runtime destinations. `tools/prepare_console_resources.py`
restores missing generated runtime files from these tracked assets and refuses
to overwrite a different existing file. This permits clean Git checkouts to
build even though general experiment outputs remain ignored.
Git attributes preserve exact bytes for hash-bound resources, including on
Windows checkouts configured for automatic line-ending conversion.

## Physical boundary

Only simulation, shadow and assurance are available execution modes. The
`physical` contract value is recognized so requests can be explicitly refused;
it is not advertised as an available executor. The imported physical module is
refusal scaffolding, not a hardware integration:

- named signers are strings, not authenticated people or cryptographic signatures;
- authorization timestamps are timezone-aware, future grants are inactive and
  revocation can be checked against the durable journal;
- the software lease uses monotonic time and is checked on access; it is not an
  independent watchdog and cannot stop hardware if the process freezes;
- no command is sent to a vendor adapter, including HALT; reaching the final
  unimplemented branch does not grant physical execution;
- device discovery, hardware timeouts, verified identities, safe-state feedback,
  emergency stop and independent safety qualification are future work.

The scaffold's proposed real-time/software split is a design hypothesis, not
a demonstrated timing result or a universally valid hardware architecture.
Its addition does not amend the formal scientific claims.

## Run from source

```powershell
python -m pip install -e ".[console]"
python tools/prepare_console_resources.py
quantum-twin-console
```

State defaults to `%LOCALAPPDATA%\ORION-Q`; use `--state-root PATH`
for an isolated session. Launcher flags of the native console:

| Flag | Effect |
| --- | --- |
| `--demo` | start directly in demonstration mode (mutually exclusive with `--synthetic` and `--telemetry-file`) |
| `--qnet-archive PATH`, `--qkd-archive PATH` | hand the author's model archives to the engine's model registry (`set_model_archive`), in both modes; the registry verifies them and a role without verified weights stays on its named baseline. `ORION_QNET_ARCHIVE` / `ORION_QKD_ARCHIVE` do the same |
| `--telemetry-file PATH` | read-only measured input |
| `--synthetic PROFILE` | developer UI stress fixture, not the demonstration |
 The older web interface remains available from source
with `--ui web` and a separately built dashboard. The native portable release
does not include that static website or advertise a web shortcut.

## Build and verification

```powershell
.\tools\build_windows_console.ps1 -SkipInstaller
# An already provisioned environment can skip dependency installation:
.\tools\build_windows_console.ps1 -Python .\.venv\Scripts\python.exe -SkipInstaller -SkipDependencyInstall
```

The script verifies resource hashes, runs the selected integration tests,
builds the one-folder executable and runs its packaged self-test before
creating a ZIP and SHA-256 sidecar. Omit `-SkipInstaller` only when Inno Setup
is installed. A portable executable and an installer are different artifacts.

```powershell
.\ORION-Q.exe --self-test .\self-test.json --state-root .\validation-state
```

When `--state-root` is omitted for self-test, a new diagnostic state directory
is created beside the report; the operator's default journal is not modified.

The packaged self-test checks evidence integrity, model loading, QKD cell
availability, metric labels and values, topology identifiers in the graph, the
matrix, the map and the queue, simulated scenario persistence, node selection,
export and audit persistence. It renders all six screens for inspection, runs
the responsive matrix, the functional node/fibre journey through the widgets
(`topology_validation.py`) and the demonstration check
(`validation.validate_demo_pipeline`): the real service pipeline on simulated
input for four network seconds, compared value for value with what the screens
show, and the idle service fabricating nothing. That last check also proves
that the network engine, the author's vendored packages and the Salvador base
map are inside the bundle. These are product integration checks, not new
scientific experiments or hardware qualification. Automated offscreen checks
also do not replace a manual acceptance session on the target Windows machine.
# Rendering and paired intake update — 2026-10-06

The rolling connection chart retains received samples until they leave the
visible window, even when the next backend delivery removes them from its
buffer. Its precise presentation timer targets 30 FPS and stops at the last
received sample. Grid and map artwork are cached independently of widget
children. The source cadence and scientific samples are unchanged.

Startup uses a restored window sized to the monitor; fullscreen remains an
explicit option. Windows dragging and resizing stay in the Qt event loop.
Theme transitions apply styles to the active root and dispose of outgoing
widgets after the initiating event, preserving page, geometry and terminal draft.
Measured theme transitions still take about two seconds; this is not a claim of
zero latency. See the [dated rendering report](../results/orion_q_interface_fluency_20261006.md)
for timings and regression evidence.

Links now expose mandatory paired software port configuration. Raw file replay
uses the same intake and receiver processing as the simulator. Quantum ports
carry statistical optical records; physical quantum information transport is
unsupported. See [paired-channel configuration and limitations](orion_q_paired_channels.md).

The institutional demonstration adds 24 hypothetical repeaters and 28 independently
acquired spans (at most 150 km each) alongside the eight original direct fibres.
Opening an older institutional demo once configures its missing local channel pairs
and appends the alternatives, with a versioned topology event containing the
previous state. Existing links, disabled flags, user nodes and configured channels
are preserved; reopening does not recreate operator removals. Operator workspaces
and the legacy Salvador demo are outside this upgrade.

The 150 km ceiling is a demonstration design choice evaluated by the existing
optical plant, not a guarantee about deployed equipment. Repeaters and their
interpolated coordinates, adjusted to the bundled IBGE land polygon, are
hypothetical. Positive per-span QKD requires a
positive commissioned reference and QBER below the published BB84 threshold.
Measured down spans remain visible but leave the effective routing graph.
Local acquisition and per-span key production do not certify national end-to-end
entanglement: the routing plant must deliver a pair above the demand threshold.
Requests that fail memory, generation or fidelity constraints remain blocked.
