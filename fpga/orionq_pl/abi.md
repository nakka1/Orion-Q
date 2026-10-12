# OrionQ PL reference ABI 1.0

This directory contains original project RTL and simulation testbenches. It is a
bounded digital reference, not a board design, a firmware image, a timing-closed
accelerator or a qualified physical interlock. No vendor IP is distributed.
The existing repository license applies. Physical gate outputs are disabled by
the default elaboration parameter, `PHYSICAL_ENABLE=0`.

## Composition and provenance

The Linux/PetaLinux PS validates the authenticated OQPL block and request using
`quantum_autopilot.lab.pspl` before encoding QCOR using
`quantum_autopilot.lab.pl_accelerator`. OQPL retains session, sequence, clock,
configuration, source, origin and block hash. QCOR is the fixed PL operand ABI;
it does not replace that provenance or authenticate a caller. The PS must bind
the response to the submitted request ID and block, validate all result frames,
and persist the resulting evidence. An IRQ, DMA completion or manifest alone
does not authorize publication of a result.

Synthetic and physical-origin input use the same ABI and algorithm. A physical
origin in a fixture is only a provenance test, not evidence of a measured signal.
No fidelity, QBER, secure key or entangled pair is inferred from timestamps alone.

## Correlation ports

`orionq_correlation` uses a single clock domain and synchronous active-low reset.
Transfers occur on a rising `clk` edge when both valid and ready are high.
The source must hold data and TLAST while backpressured. One request is accepted
at a time; `cfg_tready` is false until the last result has been consumed.

| Port | Width | Meaning |
|---|---:|---|
| `cfg_tdata`, `cfg_tvalid`, `cfg_tready` | 512 / 1 / 1 | One complete configuration header |
| `s_axis_tdata`, `s_axis_tvalid`, `s_axis_tready`, `s_axis_tlast` | 128 / 1 / 1 / 1 | Input event stream; TLAST on the declared final event |
| `m_axis_tdata`, `m_axis_tvalid`, `m_axis_tready`, `m_axis_tlast` | 128 / 1 / 1 / 1 | Ordered result bins or one rejected-request frame |
| `m_axis_request_id` | 64 | Response association, stable throughout output |
| `m_axis_pairs` | 64 | Sum of accepted coincidences; zero on rejection |
| `busy` | 1 | Configuration, ingestion, calculation or unconsumed output owns the engine |
| `clk`, `resetn` | 1 / 1 | Local clock and reset; no implied physical frequency |

These ports follow ready/valid streaming semantics; there is no packaged AXI
interconnect, TKEEP, AXI-Lite register map, CDC, DMA driver or IRQ wrapper here.
Those require a board-specific implementation and validation. The configuration
is a 512-bit transaction, not eight independently mutable register writes.

## Binary format

All byte serialization is little endian. Byte 0 occupies the least significant
eight bits of the RTL word. Header size is exactly 64 bytes:
Python `struct.Struct('<4sHHQIiiIQQqQ')`.

| Offset | Type | Field / constraint |
|---:|---|---|
| 0 | 4 bytes | `QCOR` |
| 4 | uint16 | Major version 1 |
| 6 | uint16 | Minor version 0 |
| 8 | uint64 | Request ID assigned and tracked by PS |
| 16 | uint32 | Event count, 0 through 256 |
| 20 | int32 | First channel, nonzero |
| 24 | int32 | Second channel, nonzero and different from first |
| 28 | uint32 | Local processing watchdog cycles, 1 through 2^32-1 |
| 32 | uint64 | Bin width in integer ps, 1 through 2^63-1 |
| 40 | uint64 | Radius in integer ps, 1 through 2^63-1, divisible by width |
| 48 | int64 | Delay in integer ps, -2^63+1 through 2^63-1 |
| 56 | uint64 | Coincidence budget, 1 through 10,000,000 |

`bin_count = 2 * radius / width + 1` must be at most 257. This is an explicit
subset of the CPU profile. Unsupported requests must be rejected, never silently
truncated to the RTL limits.

Each input event is exactly 16 bytes, `'<qiI'`: nonnegative signed int64 timestamp
in ps, signed int32 nonzero channel, uint32 flags. Timestamps are nondecreasing;
ties and signed channel identifiers are valid. Flag bit 0 reports loss and bit 1
reports event error. Other bits are invalid in this ABI. A block with an error,
loss, invalid event, ordering regression or malformed TLAST publishes no accepted
histogram. PS additionally checks OQPL clock lock, intervals, source and session;
QCOR cannot recover metadata that has not been supplied.

Each output bin is exactly 16 bytes, `'<QII'`: uint64 count, uint32 bin index and
uint32 status. Accepted bins appear in index order, status zero, TLAST only on the
last bin. Rejection emits exactly one frame with count zero, index `0xffffffff`,
nonzero status and TLAST. `m_axis_pairs` is zero on rejection; partial internal
counts are never published as accepted output.

| Status bit / value | Meaning |
|---|---|
| 0 / 1 | Loss flag |
| 1 / 2 | Event error flag |
| 2 / 4 | Invalid configuration or unsupported request |
| 3 / 8 | Missing or premature final event marker |
| 4 / 16 | Coincidence budget exceeded |
| 5 / 32 | Invalid event, flags or arithmetic/input range |
| 6 / 64 | Timestamp ordering regression |
| 7 / 128 | Processing/ingestion watchdog deadline |

Status bits may be combined. Unknown status bits are a PS decoding error.

## Correlation definition, ownership and timing

For every event from the first channel and every event from the second channel,
the engine computes `delta = second_ps - first_ps - delay_ps` using signed
66-bit intermediate arithmetic. It accepts the inclusive interval
`[-radius, +radius]` and increments `floor((delta + radius) / bin_width)`.
This counts all combinations, including tied events; it is not nearest-neighbor
matching. The radius boundary occupies the final bin. Integer arithmetic retains
timestamps above 2^53 without floating-point rounding. Correlation is block-local:
the producer must account for boundary coincidences explicitly when constructing
its acquisition protocol; the reference does not silently correlate separate
blocks.

The implementation clears bins, ingests the complete block, then scans all
ordered event pairs. Processing cost is quadratic in event count; configurable
integer division/modulo also have a significant combinational implementation
cost. The watchdog counts clear, input stalls and calculation cycles, but excludes
accepted output backpressure. The PS must provide its own response deadline and
bounded buffer policy. A watchdog rejection is terminal, not permission to
resubmit a physical block automatically.

Reset abandons the current transaction and returns ownership to idle; it does
not issue a result for the abandoned request. A transport cancellation must stop
the producer, synchronize/terminate DMA, drain or reset both endpoints, and mark
the abandoned request explicitly before admitting a new ID. Dropping a result or
changing an ID must not relabel an old DMA buffer as a new measurement.

## Independent digital gate

`orionq_fast_gate` is independent from the correlation engine. Parameters are
`PORTS=8` and `PHYSICAL_ENABLE=0`. With the default physical parameter, `gate_out`
is permanently zero and an arm attempt latches the physical-lock fault.
The testbench elaborates a second instance with `PHYSICAL_ENABLE=1` only inside
logic simulation to test the Boolean guard behavior; no hardware is armed.

| Inputs / outputs | Units and rules |
|---|---|
| `cfg_valid`, `cfg_ready`, `cfg_mask` | Atomic configuration; mask width PORTS; configuration accepted only while unarmed without fault |
| `cfg_period_cycles`, `cfg_width_cycles`, `cfg_watchdog_cycles` | uint32 local clock cycles; 1 <= width <= period, watchdog >= 1 |
| `arm`, `disarm`, `clear_fault` | Explicit pulse commands; no automatic rearm |
| `interlock_ok`, `configuration_verified`, `session_permit`, `clock_locked` | All mandatory guards; these are external evidence, not values invented by RTL |
| `heartbeat`, `external_fault` | Watchdog heartbeat and external fault input |
| `armed`, `fault_latched`, `fault_flags`, `physical_locked` | State/readback required by the PS wrapper |
| `gate_out` | Digital mask during the configured pulse; default zero; not a qualified voltage or board pin |

PS conversion from a duration in ps to gate cycles must be exact:
`duration_ps * clock_hz / 1,000,000,000,000`. Nonzero remainder, zero cycles,
overflow, unknown clock frequency or incompatible width/period must be rejected;
there is no silent rounding. The project `GateCycleConfig.from_picoseconds`
helper enforces this constraint. Picoseconds in a contract do not imply
picosecond hardware timing resolution.

Gate faults are interlock 1, configuration 2, session permit 4, clock 8, external
fault 16, heartbeat watchdog 32 and physical lock 64. Guard loss closes the
logical output immediately and latches on the next clock, even if disarm is also
asserted. Restoring guards does not rearm. Clearing a fault requires all guards,
no arm command and a subsequent valid configuration. Live reconfiguration while
armed faults. Disarm alone does not clear a fault.

This is digital fail-low logic, not a certified safety circuit. Asynchronous
guard signals need qualified CDC and an independent physical interlock. The PS
must verify readback and equipment/session/calibration authority before an
explicit physical arming procedure; software renewal must not renew equipment
authority silently. None is implemented as automatic hardware activation here.

## Reproduction and qualification boundary

Run `python tools/run_pspl_rtl_validation.py --output <directory>` with Icarus
`iverilog`/`vvp` in PATH; optional Yosys adds frontend/process/memory structural
checks. Explicit `--iverilog`, `--vvp`, `--ivl-base` and `--yosys` support an
extracted toolchain. Missing tools are reported as `not_executed`, not a pass.
Optional `--generic-synthesis` runs full generic Boolean synthesis without ABC;
the reference correlation profile needs several minutes and multiple GB of RAM.
This result is separate from FPGA technology mapping. The initial Yosys 0.33
campaign produced 235,397 generic cells and used about 3.5 GB peak memory for the
full correlation profile, demonstrating why board resource and timing validation
are necessary before adopting this reference as an accelerator.
The runner writes deterministic binary fixtures, commands' logs and source
hashes. The testbench checks the full histogram, request association, pair count,
status, TLAST, input stalls and stable results under output backpressure. It
checks default physical blocking and explicit simulated guard failures separately.

Passing CPU/RTL comparisons and structural synthesis checks establish only the
bounded reference logic. Board/BSP selection, signed bitstream distribution,
AXI/DMA register negotiation, memory ownership, interrupts, CDC, place-and-route,
timing closure, ILA traces, electrical polarity/voltage, latency/jitter,
throughput, FPGA-to-device synchronization, physical interlocks and optical
measurements remain necessary. Simulation clock periods are arbitrary test units.
No line-rate, real-time, ARM-board or TRL 5 qualification is inferred.

Primary references, consulted 2026-10-11:

- [AMD AXI4-Stream interface handshake](https://docs.amd.com/r/en-US/pg256-sdfec-integrated-block/AXI4-Stream-Interface).
- [AMD UG1483 2025.1, AXI4-Stream support](https://docs.amd.com/r/2025.1-English/ug1483-model-composer-sys-gen-user-guide/AXI4-Stream-Support-in-Model-Composer).
- [Linux DMAengine client ownership, termination and synchronization](https://docs.kernel.org/driver-api/dmaengine/client.html).

The repository PS/PL contract and public toolchain manifest identify the concrete
implementation assumptions and tool versions used in the validation campaign.
