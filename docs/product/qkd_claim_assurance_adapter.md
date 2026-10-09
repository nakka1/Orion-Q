# Read-only WDM/QKD claim-assurance adapter

## Purpose

`quantum_autopilot.qkd.ClaimRegistryAdapter` converts the formal TCC claim
registry into self-contained decision records for a future local dashboard.
It reads the registry and its evidence artifacts, validates every registered
SHA-256 and assertion, and never executes a photonic, QKD, IBM, or FPGA action.

The generated decision has synthesized provenance because it is a derived
assurance artifact. The upstream evidence kind remains separately recorded as
measured, simulated, or absent; these labels are never collapsed.

## Supported uses

| Requested use | Required state | Current result |
|---|---|---|
| `display` | Valid registry and registered claim | All seven claims are visible |
| `decision_support` | Measured, `supported_exploratory`, exact verified cell | Only `C-MEASURED` can pass |
| `automation` | All support gates plus explicit registry authorization | Blocked for every claim |

Version 5.19 removes the caller-supplied grid flag. A caller cannot assert
`inside_validated_grid`: it must provide a validated `QKDOperatingPoint`, and
`IrelandQCIGridClassifier` computes the scope from the reviewed grid artifact.
Both outside and unknown fail closed for decision support.

## Exact operating-grid contract

The reviewed IrelandQCI grid contains 80 cell means: ten published campaigns,
each with eight VOA cells. The exact axes are dataset, campaign, fibre length,
VOA attenuation, injected-signal presence/power and the publisher's explicitly
ambiguous QBER scale. Classification also requires the SHA-256 of the campaign
source file. Interpolation and nearest-neighbour matching are prohibited.

`QKDOperatingPoint` validates measured provenance, QBER/SKR, baseline power
semantics and observation level. Simulated provenance is rejected before a
decision can be built. QBER and SKR are outcomes: they are preserved in the
snapshot but never used to decide whether their own input condition belongs to
the grid, avoiding a circular acceptance rule.

The source does not publish measurement timestamps, stable channel identifiers
or stable device identifiers. Historical cells therefore use explicit
`not_published` sentinels and `published_cell_mean`. These fields do not become
new scientific axes. Any real new channel, device, campaign or platform is
classified outside this grid until transport evidence exists.

## Examples

Display the defensible measured claim:

```bash
python experiments/inspect_qkd_assurance.py C-MEASURED
```

Request decision support for the shipped, hash-bound example cell:

```bash
python experiments/inspect_qkd_assurance.py C-MEASURED \
  --use decision_support \
  --operating-point-json configs/qkd_assurance/irelandqci_example_operating_point.json
```

The following requests return an audited `HALT` with explicit violations:

```bash
python experiments/inspect_qkd_assurance.py C-MEASURED \
  --use automation \
  --operating-point-json configs/qkd_assurance/irelandqci_example_operating_point.json

python experiments/inspect_qkd_assurance.py H-ORIGINAL \
  --use decision_support \
  --operating-point-json configs/qkd_assurance/irelandqci_example_operating_point.json

python experiments/inspect_qkd_assurance.py B-FIDELITY \
  --use decision_support \
  --operating-point-json configs/qkd_assurance/irelandqci_example_operating_point.json
```

A decision-support request without `--operating-point-json` also halts with
`operating_point_missing`. Legacy or hand-crafted payloads containing only
`"condition_scope": "inside_validated_grid"` cannot bypass the classifier.

## Dashboard contract

The serialized record contains:

- the full registry-derived input snapshot;
- registry validation status and SHA-256;
- claim status, boundaries, permitted and forbidden language;
- product badge and product message;
- requested use and grid scope;
- the complete typed operating point and exact matched cell, when present;
- pinned grid/config/source hashes and detailed mismatch reasons;
- upstream evidence kind;
- all domain-gate violations;
- the final action and a machine-readable rationale.

This remains an assurance interface, not evidence of temporal prediction.
`PairedQuantumTelemetrySource` is the separate, stricter timestamped contract
for testing incremental WDM information about future measured QBER/SKR or Bell
fidelity. An IrelandQCI grid match must never be presented as satisfying it.

## Local product interface (v5.20)

The [enterprise assurance dashboard](enterprise_assurance_dashboard.md) exposes
this contract through a local UI and a loopback-only JSON API. Its published-cell
endpoint accepts only campaign and VOA coordinates; QBER, SKR and provenance are
recovered server-side from the verified CSV. Imported points pass through the
same typed validation and exact-cell classifier. Every result can be exported as
a complete `DecisionRecord`; no endpoint or UI control can execute a device
action.
