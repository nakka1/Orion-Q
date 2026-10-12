"""Package only an accepted native build; preserve prior archives and files."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from quantum_autopilot.product import EXECUTABLE_NAME, PACKAGE_DIRECTORY, PRODUCT_NAME, product_version

ROOT = Path(__file__).resolve().parents[1]


def release_product(release):
    """Prefer the current layout; permit reading a preserved legacy release."""
    release = Path(release)
    for directory, executable in ((PACKAGE_DIRECTORY, EXECUTABLE_NAME), ("ORION-Q", "ORION-Q.exe")):
        product = release / directory
        if product.exists():
            exe = product / executable
            if not exe.is_file():
                raise FileNotFoundError(f"Release executable is missing: {exe}")
            return product, exe
    raise FileNotFoundError(f"Release product directory is missing: {release / PACKAGE_DIRECTORY}")


def digest(path):
    with path.open("rb") as stream:
        value = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            value.update(chunk)
        return value.hexdigest()


def put(path, data):
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"refusing to overwrite differing release file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def validate_optional(report, exe):
    from quantum_autopilot.model_intake.catalog import model_options
    expected = {option.model_id for option in model_options() if option.status == "offline_only"}
    entries = report.get("models")
    if (report.get("schema") != "orion.optional-models-acceptance.v1"
            or report.get("passed") is not True or report.get("frozen") is not True
            or report.get("mode") != "without_packages"
            or report.get("physical_action_performed") is not False
            or report.get("runtime_policy_activated") is not False
            or not isinstance(entries, list) or len(entries) != len(expected)
            or any(not isinstance(entry, dict) or entry.get("unloaded") is not True for entry in entries)
            or {entry.get("model_id") for entry in entries} != expected
            or report.get("executable_sha256") != digest(exe)):
        raise ValueError("optional no-package acceptance does not approve this executable")


def validate_roadmap(report, exe):
    expected = {"comparison_30", "comparison_120", "comparison_250", "isolated_runtime_20_cycles"}
    checks = report.get("checks")
    if (report.get("schema") != "orion.frozen-roadmap-check.v1" or report.get("passed") is not True
            or not isinstance(checks, dict) or set(checks) != expected
            or any(value is not True for value in checks.values())
            or report.get("executable_sha256") != digest(exe)):
        raise ValueError("roadmap checks do not approve this executable")


def validate_startup(report, exe):
    expected = {'service-100', 'service-125', 'service-150', 'service-200', 'demo-100', 'demo-150'}
    cases = report.get('cases')
    if (report.get('schema') != 'orion.frozen-startup-check.v1' or report.get('passed') is not True
            or not isinstance(cases, list) or len(cases) != len(expected)
            or any(not isinstance(case, dict) or case.get('passed') is not True for case in cases)
            or {case.get('case') for case in cases} != expected
            or report.get('executable_sha256') != digest(exe)):
        raise ValueError('ordinary startup checks do not approve this executable')


def validate_laboratory(report, exe):
    expected = {'completed_pipeline', 'photon_campaign', 'continuous_capture',
                'independent_qkd_campaign', 'four_endpoint_application_processes',
                'uncertain_delivery_recovery', 'registered_replay_adapter',
                'replay_preserves_source_and_metrics', 'configuration_binding',
                'reconciled_receipts_after_restart', 'certified_secret_bits_zero',
                'physical_action_blocked'}
    checks = report.get('checks')
    security = report.get('security', {})
    classical = security.get('authenticated_classical_protocol', {})
    audit = security.get('audit', {})
    if (report.get('schema') != 'orion.laboratory-validation.v2' or report.get('passed') is not True
            or report.get('frozen') is not True or report.get('full_campaign') is not True
            or not isinstance(checks, dict) or set(checks) != expected
            or any(value is not True for value in checks.values())
            or report.get('executable_sha256') != digest(exe)
            or type(report.get('certified_secret_bits')) is not int or report['certified_secret_bits'] != 0
            or report.get('physical_action_performed') is not False
            or report.get('hardware_validated') is not False
            or report.get('native_swabian_replay_validated') is not False
            or security.get('common_runtime') is not True
            or security.get('test_principals_explicitly_provisioned') is not True
            or security.get('tls13') is not True
            or classical.get('backup_rollback_protected') is not True
            or type(classical.get('accepted_messages')) is not int or classical['accepted_messages'] <= 0
            or audit.get('valid') is not True
            or type(audit.get('count')) is not int or audit['count'] <= 0
            or audit.get('checkpoint_count') != audit['count']
            or audit.get('uncheckpointed_records') != 0):
        raise ValueError('full frozen laboratory campaign does not approve this executable')


def validate_satellite_mission(report, exe, *, evidence_root):
    # Build scripts execute this file directly; qualification tools may load it
    # by absolute path from an unrelated cwd with no tools package on sys.path.
    spec = importlib.util.spec_from_file_location(
        'orion_satellite_mission_release_gate', Path(__file__).with_name('validate_frozen_satellite_mission.py'))
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    checker.verify_gate(report, exe, evidence_root=evidence_root)


def validate_desktop_behavior(report, exe, *, evidence_root):
    spec = importlib.util.spec_from_file_location(
        'orion_desktop_release_gate', Path(__file__).with_name('validate_frozen_desktop_behavior.py'))
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    checker.verify_gate(report, exe, evidence_root=evidence_root)


def validate_branding(report, exe, *, evidence_root):
    # This module's validation entry point is deliberately stdlib-only. The
    # frozen diagnostic owns screenshot, PE metadata and boot/source checks.
    from quantum_autopilot.console.branding_validation import validate_native_report
    validate_native_report(report, exe, evidence_root=evidence_root)


def package(release, worker_report):
    release = Path(release).resolve(strict=True)
    product, exe = release_product(release)
    if product.name != PACKAGE_DIRECTORY or exe.name != EXECUTABLE_NAME:
        raise ValueError("Legacy releases are read-only; packaging requires OrionQ/OrionQ.exe and current branding evidence")
    worker = json.loads(Path(worker_report).read_text(encoding="utf-8"))
    if worker.get("passed") is not True or worker.get("sha256") != digest(exe):
        raise ValueError("worker acceptance does not approve this executable")
    branding_root = release / 'branding-validation'
    branding = json.loads((branding_root / 'branding-report.json').read_text(encoding='utf-8'))
    validate_branding(branding, exe, evidence_root=branding_root)
    mission_root = release / 'satellite-mission-validation'
    mission_report = json.loads((mission_root / 'report.json').read_text(encoding='utf-8'))
    validate_satellite_mission(mission_report, exe, evidence_root=mission_root)
    desktop_root = release / 'desktop-behavior-validation'
    desktop_report = json.loads((desktop_root / 'report.json').read_text(encoding='utf-8'))
    validate_desktop_behavior(desktop_report, exe, evidence_root=desktop_root)
    optional = json.loads((release / "optional-models-validation/optional-models-report.json").read_text(encoding="utf-8"))
    validate_optional(optional, exe)
    roadmap = json.loads((release / "roadmap-validation/report.json").read_bytes())
    validate_roadmap(roadmap, exe)
    startup = json.loads((release / 'startup-validation/report.json').read_bytes())
    validate_startup(startup, exe)
    self_test = json.loads((release / "self-test.json").read_text(encoding="utf-8"))
    global_report=json.loads((release/'global-validation/report.json').read_text(encoding='utf-8'))
    laboratory_report=json.loads((release/'laboratory-validation/report.json').read_text(encoding='utf-8'))
    validate_laboratory(laboratory_report, exe)
    if (global_report.get('schema')!='orion.global-network-validation.v1' or global_report.get('passed') is not True
            or global_report.get('frozen') is not True or global_report.get('full_campaign') is not True
            or global_report.get('executable_sha256')!=digest(exe) or global_report.get('certified_secret_bits')!=0
            or global_report.get('physical_actuation') is not False):
        raise ValueError('full frozen global-network campaign does not approve this executable')
    stress = list((release / "stress-validation").rglob("report.json"))
    if self_test.get("passed") is not True or len(stress) != 1 or json.loads(stress[0].read_text(encoding="utf-8")).get("passed") is not True:
        raise ValueError("native self-test and controller stress must pass before packaging")
    version = product_version()
    archive = release / f"{PRODUCT_NAME}-{version}-native-windows-x64.zip"
    if archive.exists():
        raise ValueError("portable ZIP already exists")
    copies = {
        "README-WINDOWS.txt": ROOT / "packaging/README-WINDOWS.txt",
        "VALIDATE-OrionQ.cmd": ROOT / "packaging/VALIDATE-OrionQ.cmd",
        "VALIDATE-ORION-Q.cmd": ROOT / "packaging/VALIDATE-ORION-Q.cmd",
        "docs/orion_q_windows_acceptance.md": ROOT / "docs/product/orion_q_windows_acceptance.md",
        "examples/temporal_path_campaign_demo.json": ROOT / "experiments/configs/temporal_path_campaign_demo.json",
        "docs/orion_q_temporal_campaign_ui.md": ROOT / "docs/product/orion_q_temporal_campaign_ui.md",
        "docs/orion_q_temporal_campaign.md": ROOT / "docs/product/orion_q_temporal_campaign.md",
        "docs/orion_q_temporal_path.md": ROOT / "docs/product/orion_q_temporal_path.md",
        "docs/orion_q_frozen_temporal_worker.md": ROOT / "docs/product/orion_q_frozen_temporal_worker.md",
        "docs/orion_q_temporal_history.md": ROOT / "docs/product/orion_q_temporal_history.md",
        "docs/orion_q_campaign_attempts.md": ROOT / "docs/product/orion_q_campaign_attempts.md",
        "docs/orion_q_commands_q04.md": ROOT / "docs/product/orion_q_commands_q04.md",
        "docs/orion_q_two_models_integration_plan.md": ROOT / "docs/product/orion_q_two_models_integration_plan.md",
        "docs/orion_q_optional_models_windows.md": ROOT / "docs/product/orion_q_optional_models_windows.md",
        "docs/orion_q_roadmap_usage.md": ROOT / "docs/product/orion_q_roadmap_usage.md",
        "docs/orion_q_paired_channels.md": ROOT / "docs/product/orion_q_paired_channels.md",
        "docs/product/orion_q_modular_quantum_protocols.md": ROOT / "docs/product/orion_q_modular_quantum_protocols.md",
        "docs/product/orion_q_communicators_geo.md": ROOT / "docs/product/orion_q_communicators_geo.md",
        "docs/product/orion_q_satellite_ogs_v2.md": ROOT / "docs/product/orion_q_satellite_ogs_v2.md",
        "docs/product/orion_q_satellite_reception.md": ROOT / "docs/product/orion_q_satellite_reception.md",
        "docs/product/orion_q_desktop_identity.md": ROOT / "docs/product/orion_q_desktop_identity.md",
        "docs/product/orionq_branding.md": ROOT / "docs/product/orionq_branding.md",
        "docs/product/orionq_entry_ui.md": ROOT / "docs/product/orionq_entry_ui.md",
        "docs/product/orionq_release_5_61_0.md": ROOT / "docs/product/orionq_release_5_61_0.md",
        "docs/product/orionq_trl4_qualification.md": ROOT / "docs/product/orionq_trl4_qualification.md",
        "docs/product/orionq_trl4_design.md": ROOT / "docs/product/orionq_trl4_design.md",
        "docs/product/orionq_trl4_operator_manual.md": ROOT / "docs/product/orionq_trl4_operator_manual.md",
        "docs/product/orionq_trl5_qualification.md": ROOT / "docs/product/orionq_trl5_qualification.md",
        "docs/product/orionq_cross_simulators.md": ROOT / "docs/product/orionq_cross_simulators.md",
        "docs/product/orionq_sequence_network.md": ROOT / "docs/product/orionq_sequence_network.md",
        "docs/product/orionq_hybrid_qualification.md": ROOT / "docs/product/orionq_hybrid_qualification.md",
        "docs/product/orionq_hybrid_remediation.md": ROOT / "docs/product/orionq_hybrid_remediation.md",
        "docs/results/orionq_trl4_consolidation_20261009.md": ROOT / "docs/results/orionq_trl4_consolidation_20261009.md",
        "docs/results/orionq_trl5_initial_campaign_20261009.md": ROOT / "docs/results/orionq_trl5_initial_campaign_20261009.md",
        "docs/results/orionq_trl5_continuation_20261009.md": ROOT / "docs/results/orionq_trl5_continuation_20261009.md",
        "docs/results/orionq_cross_simulators_20261010.md": ROOT / "docs/results/orionq_cross_simulators_20261010.md",
        "docs/results/orionq_sequence_network_20261010.md": ROOT / "docs/results/orionq_sequence_network_20261010.md",
        "docs/results/orionq_hybrid_qualification_20261010.md": ROOT / "docs/results/orionq_hybrid_qualification_20261010.md",
        "docs/results/orionq_hybrid_remediation_20261010.md": ROOT / "docs/results/orionq_hybrid_remediation_20261010.md",
        "docs/results/orionq_space_optical_remediation_20261010.md": ROOT / "docs/results/orionq_space_optical_remediation_20261010.md",
        "docs/results/orionq_entry_ui_20261008.md": ROOT / "docs/results/orionq_entry_ui_20261008.md",
        "docs/results/orionq_unplug_ui_20261008.md": ROOT / "docs/results/orionq_unplug_ui_20261008.md",
        "docs/results/orionq_signal_audio_geo_20261008.md": ROOT / "docs/results/orionq_signal_audio_geo_20261008.md",
        "docs/results/orionq_geo_anchor_20261009.md": ROOT / "docs/results/orionq_geo_anchor_20261009.md",
        "docs/product/orion_q_geo_modes.md": ROOT / "docs/product/orion_q_geo_modes.md",
        "docs/product/orion_q_satellite_bridges.md": ROOT / "docs/product/orion_q_satellite_bridges.md",
        "docs/product/orion_q_global_network.md": ROOT / "docs/product/orion_q_global_network.md",
        "docs/product/orion_q_inter_satellite_contract.md": ROOT / "docs/product/orion_q_inter_satellite_contract.md",
        "docs/product/orion_q_laboratory.md": ROOT / "docs/product/orion_q_laboratory.md",
        "docs/product/orion_q_security.md": ROOT / "docs/product/orion_q_security.md",
        "docs/product/orion_q_performance.md": ROOT / "docs/product/orion_q_performance.md",
        "docs/product/orion_q_protocol_graphs_fpga.md": ROOT / "docs/product/orion_q_protocol_graphs_fpga.md",
        "docs/product/orion_q_protocol_time_series.md": ROOT / "docs/product/orion_q_protocol_time_series.md",
        "docs/results/orion_q_performance_20261007.md": ROOT / "docs/results/orion_q_performance_20261007.md",
        "docs/product/orion_q_security_pki_keys.md": ROOT / "docs/product/orion_q_security_pki_keys.md",
        "docs/product/orion_q_security_acquisition.md": ROOT / "docs/product/orion_q_security_acquisition.md",
        "docs/architecture/adr_security_001.md": ROOT / "docs/architecture/adr_security_001.md",
        "docs/results/orion_q_security_20261007.md": ROOT / "docs/results/orion_q_security_20261007.md",
        "docs/product/orion_lab_acquisition_contracts.md": ROOT / "docs/product/orion_lab_acquisition_contracts.md",
        "docs/product/orion_q_lab_key_delivery.md": ROOT / "docs/product/orion_q_lab_key_delivery.md",
        "docs/product/orion_q_public_ephemeris.md": ROOT / "docs/product/orion_q_public_ephemeris.md",
        "docs/results/orion_q_lab_keys_20261007.md": ROOT / "docs/results/orion_q_lab_keys_20261007.md",
        "docs/results/orion_lab_recovery_20261007.md": ROOT / "docs/results/orion_lab_recovery_20261007.md",
        "docs/results/orion_lab_console_startup_20261007.md": ROOT / "docs/results/orion_lab_console_startup_20261007.md",
        "docs/architecture/adr_geo_004_exclusive_renderers.md": ROOT / "docs/architecture/adr_geo_004_exclusive_renderers.md",
        "docs/architecture/adr_satellite_003_ogs_observations.md": ROOT / "docs/architecture/adr_satellite_003_ogs_observations.md",
        "docs/results/orion_q_communicators_geo_validation_20261006.md": ROOT / "docs/results/orion_q_communicators_geo_validation_20261006.md",
        "docs/results/orion_q_modular_connection_matrix_20261006.md": ROOT / "docs/results/orion_q_modular_connection_matrix_20261006.md",
        "docs/results/orion_q_modular_locations_20261006.md": ROOT / "docs/results/orion_q_modular_locations_20261006.md",
    }
    for relative, source in copies.items():
        put(product / relative, source.read_bytes())
    # Evidence stored in the release root is intentionally not executable data.
    files = sorted(p for p in product.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt")
    for path in files:
        path.resolve(strict=True).relative_to(product.resolve())
    sums = "".join(f"{digest(path)}  {path.relative_to(product).as_posix()}\n" for path in files)
    put(product / "SHA256SUMS.txt", sums.encode("utf-8"))
    files.append(product / "SHA256SUMS.txt")
    with ZipFile(archive, "x", ZIP_DEFLATED, compresslevel=6, allowZip64=True) as bundle:
        for path in files:
            bundle.write(path, path.relative_to(release).as_posix())
    with ZipFile(archive) as bundle:
        if bundle.testzip() is not None or len(bundle.infolist()) != len(files):
            raise ValueError("portable ZIP verification failed; do not distribute")
    sha = digest(archive)
    put(Path(str(archive)+".sha256"), f"{sha}  {archive.name}\n".encode("utf-8"))
    report = {"archive": str(archive), "sha256": sha, "bytes": archive.stat().st_size,
              "files": len(files), "executable_sha256": digest(exe), "zip_crc_verified": True}
    put(release / "portable-package.json", json.dumps(report, indent=2).encode("utf-8"))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--worker-report", required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.release_root, args.worker_report), indent=2))
