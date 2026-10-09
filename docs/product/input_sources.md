# ORION-Q input plug - v5.38

## One interface, interchangeable inputs

F6 or **DATA SOURCE** replaces the input of the existing window. No second
dashboard or demonstration window is created. The six screen instances,
selected tab, window size and zoom remain in place. On replacement, every
screen is cleared before accepting data from the new input. The previous
producer is stopped and disconnected; generation tokens ignore delayed
callbacks. Switching is refused while an operation is busy; finish or cancel
it first. Data provenance and persistent journals are not merged.

The current plug is `DataProvider -> SwitchableProvider -> Snapshot -> screens`.
Each adapter implements `start()`, `stop()`, `snapshot_changed` and, when
appropriate, `dispatch()`. `Snapshot.input_kind` identifies service, demo,
synthetic, measured or unavailable input independently of campaign names. An adapter is
trusted application code, not an automatically downloaded hardware plugin.

| Selection (login screen) | `input_kind` | Actual behavior |
| --- | --- | --- |
| Demonstration | `demo` | The local service and network engine on `<state-root>/demo/brazil-institutions-v1/sandbox_journal.sqlite`: seven institutional locations in Brazil, eight hypothetical direct fibres and backend `SimulatedInputSource` observations. ML control starts OFF in a new workspace. Operator label `DEMONSTRATION`, adapter `assurance-demo-service`. The legacy Salvador journal and the API preset `salvador_metro` remain available. |
| Measured telemetry JSON | `measured` | A local acquisition bridge supplies declared measurements. Read-only; no model invocation, device command or physical executor. |
| Local service | `service` | The operator's journal and topology with the same engine and no input feed unless one is configured: configuration and backend routing are shown, measured fields are absent. Also the existing model replay, simulated controller and published QKD evidence; mixed provenance, not an entirely live physical source. Operator label `LOCAL SANDBOX`, adapter `assurance-local-service`. |

The seeded reference/load/faults stream (`--synthetic`, `input_kind`
`synthetic`) is a developer UI stress fixture whose values are generated on
the UI side. It is not the demonstration, and it is not selectable from the
login screen or the command palette.

### Demonstration and normal operation share one pipeline

`ServiceProvider` is the adapter for both. It maps
`service.network_live_state()` onto `Snapshot.live` and formats the topology
strings from it; it computes no physics and decides nothing. A dedicated
thread calls `service.network_step()` once per second; snapshots refresh once
per second. Commands are the backend's verbs: the topology verbs
(`add_network_node`, `set_node_location`, `set_node_isolation`,
`set_link_active`, `recalibrate_node`) run through the tracked command journal
with readback, and the engine verbs (`define_pair`, `remove_pair`,
`request_pair`, `purify_pair`, `consume_pair`, `set_model_archive`, `set_ml_control`) run through
`service.network_command`. `inject_input_event` disturbs the simulated INPUT
of one fibre and exists only in the demonstration; the processing then reacts
to it as it would to the same disturbance on a real feed. A measured input
source for the engine is not implemented in this build.

The Topology ML switch requires an explicit confirmation in either direction.
It changes permission for compensation, synchronization, key-rate, routing and
allocation controllers. Baselines take over when OFF; diagnostic detectors and
safety admission remain enabled. ON still requires verified weights and an
admitted plant contract. The backend persists a revision and a hashed receipt;
stale confirmations are refused. Measured and idle input feeds cannot use this
simulation-only operation. See the [current demonstration guide](orion_q_demo_guide.md).

Receiving physical measurements is not controlling physical equipment. This
version provides a transport-independent UI seam and one concrete local-file
adapter. A device-specific collector must still read the actual instrument and
publish the agreed envelope. It is not a universal USB, FPGA, IBM or network
device driver. The software validates declarations and numeric structure, not
the physical authenticity of a sensor reading. No new scientific claim is made.

## Local acquisition bridge

Launch with `ORION-Q.exe --telemetry-file C:\acquisition\telemetry.json`, or
choose **Measured telemetry JSON** from DATA SOURCE. The file is read once per
second, up to 2 MB. Use a local regular file, not a network share or a named
pipe. Publish by writing a temporary sibling and atomically replacing the
destination; do not expose half-written frames.

The strict JSON envelope uses exactly these fields:

- `schema`: `orion.telemetry.v1`.
- `source_id`, `campaign_id`: nonempty plain-text identifiers.
- `evidence_kind`: `measured` for this adapter. Artificial demonstration must
  use the synthetic adapter; do not relabel generated values as measured.
- `timestamp_utc`: timezone-aware acquisition-frame timestamp.
- `nodes`: 1..32 objects, each with `id`, `x`, `y`. These coordinates are
  logical layout fractions in [0,1], not measured device health. An adapter may
  additionally provide the pair `latitude`/`longitude` as finite WGS84 degrees;
  both are required together. GEO remains explicitly unavailable when they are absent.
- `links`: 1..256 objects using the fields below.

Each link requires `id`, `a`, `b`, `channel`, `device_id`, `measurement_id`,
`timestamp_utc`, and `evidence_kind: measured`. Endpoints must refer to distinct
declared nodes; link and node IDs must be unique. Identifiers have a maximum
of 160 characters, without markup or control characters.

At least one numeric measurement is required; omit values that were not measured:

| Field | Unit / constraint |
| --- | --- |
| `ber` | Fraction in [0,1] |
| `osnr_db` | Finite dB |
| `power_dbm` | Finite dBm |
| `qber` | Fraction in [0,1], not percent |
| `skr_bps` | Nonnegative bits per second |
| `fidelity` | Fraction in [0,1]; requires `fidelity_method` |
| `fidelity_method` | `state_tomography` or `measured_fidelity`; no BER/model/simulation conversion |

NaN, infinity, null measurements, unknown fields and duplicate JSON keys are
refused. Both frame and measurement timestamps must be no more than 30 seconds
old or 5 seconds ahead of the local clock. A measurement cannot be newer than
its envelope. Keep clocks synchronized; the software does not implement clock
synchronization or establish experimental temporal pairing by itself.

Frame times cannot move backward. A changed payload at the same frame time is
refused; JSON whitespace changes are harmless. SHA-256 addresses canonical
JSON, not raw file formatting, and is not a signature. A source/campaign change
requires explicitly reconnecting the input. File loss, expiry or invalid data
clears old values; recovery accepts a valid frame. Raw historical datasets are
not live input: do not refresh their timestamps to make them pass freshness.
Use the existing evidence/replay service for the available historical evidence.

## Screen behavior

The measured adapter shows up to three primary metrics, with every supplied
metric in the telemetry context. Clicking a measured link in Topology selects
that exact channel in Telemetry, including parallel channels between the same
endpoints. Clicking a node opens a local detail panel for its incident links. It displays latest samples,
not a synthesized history or forecast. No missing fidelity, QBER or SKR is
filled from optical data. Unreported endpoint health is unknown; logical links
do not assert entanglement or physical availability. Memory registers, key
material and calibration remain empty when not provided.

Measured-source simulation/model/control buttons are disabled and refused by
the adapter as well. Selection, refresh and unsigned export are supported.
Returning to the service or the demonstration restores only that source's supported
operations. This does not validate a model on the measured input or enable
automatic measured-input control.

## Verification and extension

`tests/test_console_input_sources.py` covers same-window switching, stale callback
rejection, busy gates, invalid schemas, units, provenance, freshness, duplicate
identifiers/JSON fields, absent metrics and recovery. The frozen `--self-test`
also switches the synthetic UI fixture -> a clearly named SOFTWARE TEST FIXTURE of the
measurement schema -> the synthetic UI fixture, without using a device.
`tests/test_console_live_provider.py` covers the demonstration and the idle
service against the real backend: located and unlocated nodes, live values and
the stability wave, the stepping thread's lifecycle across stop and source
switches, every tracked verb with its confirmation and readback, the phases of
a recalibration, backend refusals and the terminal and palette entries. Those fixtures
are software tests, not experimental data or demonstration measurements.

To support another acquisition transport, implement a `DataProvider` that
normalizes actual readings into `Snapshot`, preserves provenance and exposes
only read-only capabilities unless a separate control-authority design has
been approved. Register a factory with the source selector. The screen code
should not know whether readings came from a file, an API or an instrument.

Source validation on 2026-09-09: the complete final suite passed 1017 tests in
291.57 seconds, including the exact parallel-channel selection regression;
the Windows build's selected integration suite passed 78 tests. The only full-
suite warning was the existing joblib physical-core detection fallback.

The final Windows executable also passed 18 top-level self-test checks and
51 nested responsive/input checks. The packaged 20-round synthetic stress run
passed 535 checks across 140 controller cycles, without physical actuation.
Reports are in `outputs/software_validation_orion_v537`. Rendering checks use
an offscreen software surface; device compatibility and target-monitor human
acceptance remain unvalidated.
