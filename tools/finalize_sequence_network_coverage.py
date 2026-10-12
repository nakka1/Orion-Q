"""Add identified FPGA coverage without changing the frozen scientific run.

The frozen exporter read device_id at the snapshot root instead of inside
configuration. Its three unknown rows are retained. This stdlib-only derivation
binds their ordinal order to the frozen census and validates all other identities
and results before producing new, exclusive-create files.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

SCHEMA = "orionq.sequence-network-coverage-identification.v1"
EXPECTED_COUNTS = {"node/communicator": 71, "link": 131, "protocol_session": 36,
                   "operational_demand": 76, "fpga": 3}
RAW_INPUTS = ("census.json", "report.json", "coverage-matrix.json", "coverage-matrix.csv",
              "coverage-matrix.md", "freeze.json")
OUTPUTS = ("coverage-matrix-identified.json", "coverage-matrix-identified.csv",
           "coverage-matrix-identified.md", "coverage-identification-manifest.json")
SOURCE_FILES = ("tools/finalize_sequence_network_coverage.py", "tests/test_sequence_network_coverage.py")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load(path):
    def nonfinite(value):
        raise ValueError(f"nonfinite JSON value: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_pairs, parse_constant=nonfinite)


def _ids(rows, key, kind):
    if not isinstance(rows, list):
        raise ValueError(f"{kind}: list required")
    ids = [row.get(key) for row in rows if isinstance(row, dict)]
    if len(ids) != len(rows) or any(not isinstance(ident, str) or not ident for ident in ids):
        raise ValueError(f"{kind}: complete nonempty identities required")
    if len(set(ids)) != len(ids):
        raise ValueError(f"{kind}: duplicate identity")
    if len(ids) != EXPECTED_COUNTS[kind]:
        raise ValueError(f"{kind}: unexpected number of registrations")
    return set(ids)


def validate_census(census):
    """Validate actual nested FPGA identities and hashes, without product imports."""
    if not isinstance(census, dict) or census.get("schema") != "orionq.sequence-network-inventory.v1":
        raise ValueError("wrong census schema")
    expected = {
        "node/communicator": _ids(census.get("nodes"), "node_id", "node/communicator"),
        "link": _ids(census.get("links"), "link_id", "link"),
        "protocol_session": _ids(census.get("sessions"), "session_id", "protocol_session"),
        "operational_demand": _ids(census.get("global_demand_templates"), "demand_id", "operational_demand"),
    }
    raw_devices = census.get("fpga", {}).get("devices")
    devices = list(raw_devices.values()) if isinstance(raw_devices, dict) else raw_devices
    if not isinstance(devices, list) or len(devices) != EXPECTED_COUNTS["fpga"]:
        raise ValueError("fpga: exactly three registered snapshots required")
    configurations = []
    for device in devices:
        if not isinstance(device, dict) or not isinstance(device.get("configuration"), dict):
            raise ValueError("fpga: nested configuration required")
        config = device["configuration"]
        if config.get("schema") != "orion.fpga-devices.v1":
            raise ValueError("fpga: configuration schema mismatch")
        if config.get("node_id") not in expected["node/communicator"]:
            raise ValueError("fpga: associated node is absent from census")
        if digest(config) != device.get("configuration_sha256"):
            raise ValueError("fpga: configuration hash mismatch")
        configurations.append(config)
    expected["fpga"] = _ids(configurations, "device_id", "fpga")
    flattened = [ident for identities in expected.values() for ident in identities]
    if len(flattened) != 317 or len(set(flattened)) != 317:
        raise ValueError("cross-kind identity collision or incomplete census")
    return expected, devices


def derive_coverage(census, raw_rows, report):
    """Copy only FPGA identity/detail fields; preserve every scientific result."""
    expected, devices = validate_census(census)
    if not isinstance(raw_rows, list) or len(raw_rows) != 317 or any(not isinstance(row, dict) for row in raw_rows):
        raise ValueError("raw coverage must contain exactly 317 objects")
    if Counter(row.get("kind") for row in raw_rows) != Counter(EXPECTED_COUNTS):
        raise ValueError("raw coverage kind counts mismatch")
    if not isinstance(report, dict) or report.get("schema") != "orionq.sequence-network-campaign.v1":
        raise ValueError("final campaign report required")
    coverage = report.get("coverage", {})
    for key, count in (("rows", 317), ("physical_nodes", 71), ("physical_links", 131),
                       ("sessions", 36), ("global_demands", 76)):
        if type(coverage.get(key)) is not int or coverage[key] != count:
            raise ValueError(f"report coverage mismatch: {key}")
    for kind, identifiers in expected.items():
        if kind == "fpga":
            continue
        actual = [row.get("component") for row in raw_rows if row["kind"] == kind]
        if len(set(actual)) != len(actual) or set(actual) != identifiers:
            raise ValueError(f"raw coverage identity mismatch: {kind}")
    fpga_indices = [index for index, row in enumerate(raw_rows) if row["kind"] == "fpga"]
    if any(raw_rows[index].get("component") != "unknown" for index in fpga_indices):
        raise ValueError("known frozen exporter defect requires three unknown FPGA identities")
    identified = deepcopy(raw_rows)
    changes = []
    for ordinal, (index, device) in enumerate(zip(fpga_indices, devices)):
        row = identified[index]
        if not isinstance(row.get("detail"), dict):
            raise ValueError("fpga: raw detail object required")
        if row.get("native_quantum_scope") != "not_demonstrated" or row.get("physical_connection_validated") is not False or row.get("certified_secret_bits") != 0:
            raise ValueError("fpga: unchanged unqualified simulation scope required")
        config = deepcopy(device["configuration"])
        row["component"] = config["device_id"]
        row["detail"].update({
            "configuration": config, "configuration_sha256": device["configuration_sha256"],
            "identity_source": f"census.fpga.devices[{ordinal}].configuration.device_id",
            "identity_binding": "same ordinal enumeration as frozen export_coverage; raw snapshots were not identified at top level",
            "integration_mode": device.get("mode", "unreported"),
            "postprocessing_scope": "configuration identification only; no new experiment, FPGA timing, bitstream or physical qualification",
        })
        changes.append({"row_index": index, "raw_component": "unknown", "identified_component": config["device_id"],
                        "node_id": config["node_id"], "configuration_sha256": device["configuration_sha256"]})
    for index, (old, new) in enumerate(zip(raw_rows, identified)):
        if index not in fpga_indices and old != new:
            raise ValueError("non-FPGA scientific evidence was modified")
        if index in fpga_indices:
            if {key: value for key, value in old.items() if key not in ("component", "detail")} != {key: value for key, value in new.items() if key not in ("component", "detail")}:
                raise ValueError("FPGA result/scope was modified")
            if any(new["detail"].get(key) != value for key, value in old["detail"].items()):
                raise ValueError("original FPGA detail was overwritten")
    components = [row["component"] for row in identified]
    if len(set(components)) != 317 or set(components) != set.union(*expected.values()):
        raise ValueError("identified coverage is incomplete or contains duplicate identities")
    return identified, changes


def finalize(campaign):
    """Exclusive-create additional exports after the frozen report is final."""
    directory = Path(campaign).resolve()
    if not directory.is_dir():
        raise ValueError("campaign directory does not exist")
    if any(not (directory / name).is_file() for name in RAW_INPUTS):
        raise ValueError("final report and all original coverage exports must exist")
    if any((directory / name).exists() for name in OUTPUTS):
        raise ValueError("identified outputs already exist; overwrite refused")
    original_hashes = {name: sha(directory / name) for name in RAW_INPUTS}
    census, raw_rows, report = (load(directory / name) for name in ("census.json", "coverage-matrix.json", "report.json"))
    if report.get("coverage", {}).get("matrix_sha256") != original_hashes["coverage-matrix.json"]:
        raise ValueError("raw matrix hash differs from final campaign report")
    if report.get("census_sha256") != original_hashes["census.json"]:
        raise ValueError("census hash differs from final campaign report")
    if report.get("freeze_sha256") != original_hashes["freeze.json"]:
        raise ValueError("freeze hash differs from final campaign report")
    identified, changes = derive_coverage(census, raw_rows, report)
    root = Path(__file__).resolve().parents[1]
    source_hashes = {name: sha(root / name) for name in SOURCE_FILES}
    with (directory / OUTPUTS[0]).open("x", encoding="utf-8") as stream:
        json.dump(identified, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    fields = ["component", "kind", "native_quantum_scope", "native_classical_received_at_source",
              "physical_connection_validated", "certified_secret_bits", "detail"]
    with (directory / OUTPUTS[1]).open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows({**row, "detail": json.dumps(row["detail"], ensure_ascii=False, allow_nan=False)} for row in identified)
    lines = ["# Matriz identificada de cobertura externa", "",
             "Derivação administrativa: três linhas FPGA `unknown` do exportador congelado foram identificadas pela configuração cadastrada. Os resultados científicos originais permanecem preservados.", "",
             "Recepção clássica conta callbacks na fonte; consulte os recibos OrionQ para confirmar a admissão final.", "",
             "| Componente | Tipo | Escopo quântico externo | Callbacks clássicos na fonte |", "|---|---|---|---:|"]
    def escape(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines.extend("| " + " | ".join(escape(row[key]) for key in
                 ("component", "kind", "native_quantum_scope", "native_classical_received_at_source")) + " |" for row in identified)
    with (directory / OUTPUTS[2]).open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    if original_hashes != {name: sha(directory / name) for name in RAW_INPUTS}:
        raise ValueError("original scientific inputs changed during derivation")
    manifest = {
        "schema": SCHEMA, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "campaign": str(directory), "raw_input_sha256": original_hashes,
        "postprocessing_source_sha256": source_hashes,
        "identified_output_sha256": {name: sha(directory / name) for name in OUTPUTS[:3]},
        "raw_export_defect": {"unknown_fpga_rows": 3, "raw_unique_component_identifiers": len({row["component"] for row in raw_rows}),
                              "cause": "frozen exporter reads device_id/id at snapshot root; actual identity is configuration.device_id"},
        "corrections": changes, "rows": len(identified), "unique_component_identifiers": len({row["component"] for row in identified}),
        "kind_counts": dict(Counter(row["kind"] for row in identified)),
        "originals_unchanged": True, "non_fpga_rows_unchanged": True,
        "scientific_results_recomputed": False, "scientific_acceptance_changed": False,
        "raw_campaign_status": report.get("status"), "raw_covered_scope_passed": report.get("covered_scope_passed"),
        "full_hybrid_quantum_network_validated": report.get("full_hybrid_quantum_network_validated"),
        "physical_connection_validated": False, "certified_secret_bits": 0,
        "scope": "Additional identified inventory artifact, outside the original frozen scientific source inventory; hashes establish integrity, not authenticated authorship",
    }
    with (directory / OUTPUTS[3]).open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = finalize(args.campaign)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"coverage identification refused: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"rows": result["rows"], "unique_component_identifiers": result["unique_component_identifiers"],
                      "originals_unchanged": result["originals_unchanged"], "manifest": str(args.campaign / OUTPUTS[3])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
