"""Bundle only the reviewed laboratory evidence, configuration and changed-source hashes."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

from quantum_autopilot.lab.photon import PhotonConfig
from quantum_autopilot.lab.qkd import QkdConfig


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "outputs/orion_lab_20261007"
    config_dir = root / "configs/lab"
    config_dir.mkdir(parents=True, exist_ok=True)
    configuration = {"schema": "orion.lab.configuration.v1", "mode": "emulation", "photon": asdict(PhotonConfig()),
                     "qkd": asdict(QkdConfig()), "hardware_action_performed": False,
                     "native_replay": {"status": "blocked_missing_event_chunks_sdk_license_and_metadata_normalizer"},
                     "references_only": ["Clavis XGR", "Toshiba MU", "SPDC810N", "Time Tagger Ultra", "Single Quantum SNSPD"]}
    config = config_dir / "bench_v1.json"
    config.write_text(json.dumps(configuration, ensure_ascii=False, indent=2), encoding="utf8")
    integration = output / "integration_complete"
    run = json.loads((integration / "integration_report.json").read_text(encoding="utf8"))
    receipt = json.loads((integration / "laboratory" / run["http_run_id"] / "receipt.json").read_text(encoding="utf8"))
    photon, qkd = receipt["results"]["photon"], receipt["results"]["qkd"]
    suites = ET.parse(output / "final_lab.xml").getroot()
    test_suites = list(suites.iter("testsuite"))
    metrics = {"schema": "orion.lab.metrics.v1", "receipt_run_id": receipt["run_id"],
               "receipt_sha256": receipt["receipt_sha256"], "photon": photon["nominal"],
               "qkd_session": qkd["qkd_session"], "delivery": qkd["delivery"], "delivery_checks": qkd["checks"],
               "photon_faults": [{k: fault[k] for k in ("fault", "detected", "recovery_complete", "recovery_manifest")}
                                 for fault in photon["faults"]],
               "experimental_aggregate_replay_validated": photon["experimental_aggregate_replay_validated"],
               "tests": {name: sum(int(suite.get(name, "0")) for suite in test_suites)
                         for name in ("tests", "failures", "errors", "skipped")},
               "physical_hardware_validated": False, "native_raw_replay_validated": False}
    metrics_path = output / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")
    folders = [integration, output / "satellite_ibm", output / "qkd", output / "cli_reproduction"]
    files = [metrics_path, config, output / "inventory.json", output / "final_lab.xml", output / "baseline_2.xml",
             output / "probe_qt_startup.json",
             output / "panel_final.xml",
             output / "duplicate_before.txt", output / "duplicate_after.txt", output / "duplicate_before.xml",
             output / "duplicate_after.xml"]
    for folder in folders:
        files.extend(file for file in folder.rglob("*") if file.is_file())
    files += list((root / "docs/research").glob("orion_lab_*_20261007.md"))
    files += [root / "docs/product/orion_q_laboratory.md", root / "docs/results/orion_q_laboratory_validation_20261007.md"]
    sources = list((root / "src/quantum_autopilot/lab").glob("*.py"))
    sources += [root / "src/quantum_autopilot" / p for p in (
        "qkd/dashboard_api.py", "console/contract.py", "console/capabilities.py", "console/service_provider.py",
        "console/screens/keys.py", "console/widgets/laboratory.py", "quantum_protocols/satellite_processing.py",
        "quantum_protocols/satellite_simulation.py", "quantum_protocols/satellite_validation.py", "network_engine/engine.py")]
    sources += [root / "tools" / p for p in ("inventory_lab.py", "run_lab_validation.py", "render_lab_console.py", "build_lab_evidence.py")]
    sources += list((root / "tests").glob("test_lab_*.py"))
    sources += [root / "tests/test_satellite_pipeline_v2.py",
                root / "tests/test_qkd_dashboard_api.py", root / "pyproject.toml", root / "README.md"]
    files = sorted(set(file for file in files if file.is_file()))
    for file in files:
        if file.suffix in {".key", ".pem"} or b"BEGIN PRIVATE KEY" in file.read_bytes():
            raise ValueError("private PKI artifact cannot enter evidence bundle")
    def record(file):
        return {"path": file.relative_to(root).as_posix(), "bytes": file.stat().st_size,
                "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
    manifest = {"schema": "orion.lab.evidence-manifest.v1", "artifacts": [record(file) for file in files],
                "source_files": [record(file) for file in sources],
                "hash_scope": "integrity only, not authorship authentication or scientific validation",
                "physical_hardware_validated": False, "certified_secret_bits": 0}
    manifest_path = output / "evidence_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf8")
    bundle = output / "orion_lab_evidence.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in files + [manifest_path]:
            archive.write(file, file.relative_to(root).as_posix())
    with zipfile.ZipFile(bundle) as archive:
        assert archive.testzip() is None
        for item in manifest["artifacts"]:
            assert hashlib.sha256(archive.read(item["path"])).hexdigest() == item["sha256"]
    print(json.dumps({"bundle": str(bundle), "artifacts": len(files), "source_hashes": len(sources),
                      "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(), "bytes": bundle.stat().st_size,
                      "tests": metrics["tests"]}, indent=2))


if __name__ == "__main__":
    main()
