"""Source/frozen acceptance of the local laboratory; no physical device access."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from .service import LaboratoryService


def provision_validation_identities(runtime, diagnostic_root):
    """Explicit admin fixture setup ONLY for an isolated acceptance directory.

    Never used by product boot/service defaults. The diagnostic entry point
    rejects pre-existing output; grants cannot alter a user's configured runtime.
    """
    root = Path(diagnostic_root).resolve()
    if not runtime.root.resolve().is_relative_to(root):
        raise ValueError("test_identity_provisioning_requires_isolated_diagnostic_runtime")
    runtime.authorize("security.manage", "security:diagnostic-fixtures")
    from quantum_autopilot.security.key_requirements import required_kme_identities
    for row in required_kme_identities():
        runtime.grant_identity(row["identity"], row["roles"], row["scopes"], source="service")


def validate_laboratory(output_directory):
    root = Path(output_directory).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("laboratory_diagnostic_requires_empty_directory")
    root.mkdir(parents=True, exist_ok=True)
    from quantum_autopilot.security import SecurityRuntime
    runtime = SecurityRuntime(root / "security")
    provision_validation_identities(runtime, root)
    service = LaboratoryService(root / "laboratory", security=runtime)
    first = service.run({"experiment": "all"})
    photon, qkd = first["results"]["photon"], first["results"]["qkd"]
    config = photon["configuration"]
    settings = service.configure({"expected_revision": 0,
        "photon": {"adapter_id": "orion-replay", "replay_run_id": first["run_id"], "parameters": config}})
    replay = service.run({"experiment": "photon"})
    state = LaboratoryService(service.root, security=runtime).history()
    classical = qkd["qkd_session"].get("classical_authentication", {})
    audit = runtime.audit.verify()
    checks = {
        "completed_pipeline": first["status"] == replay["status"] == "completed",
        "photon_campaign": photon["passed"],
        "continuous_capture": photon["continuous_capture"]["passed"],
        "independent_qkd_campaign": qkd["all_passed"] and classical.get("accepted_messages", 0) > 0 and
            classical.get("backup_rollback_protected") is True and qkd["delivery"]["tls_version"] == "TLSv1.3" and audit["valid"],
        "four_endpoint_application_processes": qkd["delivery"].get("independent_processes") == 4,
        "uncertain_delivery_recovery": qkd.get("fault_campaign", {}).get("all_passed") is True,
        "registered_replay_adapter": replay["results"]["photon"]["mode"] == "replay",
        "replay_preserves_source_and_metrics": all(photon["nominal"][name] ==
            replay["results"]["photon"]["nominal"][name] for name in ("counts", "coincidences", "histogram", "clock_domain")),
        "configuration_binding": replay["configuration_sha256"] == settings["sha256"],
        "reconciled_receipts_after_restart": len(state["runs"]) == 2 and not
            state["persistence"]["reconciliation"]["pending_receipts"],
        "certified_secret_bits_zero": qkd["qkd_session"]["certified_secret_bits"] == 0,
        "physical_action_blocked": not first["physical_action_performed"] and not replay["physical_action_performed"],
    }
    frozen = bool(getattr(sys, "frozen", False))
    report = {"schema": "orion.laboratory-validation.v2", "passed": all(checks.values()),
              "checks": checks, "frozen": frozen, "full_campaign": True,
              "executable_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest() if frozen else None,
              "run_ids": [first["run_id"], replay["run_id"]], "receipt_sha256": [first["receipt_sha256"], replay["receipt_sha256"]],
              "delivery": qkd["delivery"], "physical_action_performed": False,
              "hardware_validated": False, "native_swabian_replay_validated": False,
              "certified_secret_bits": 0,
              "security": {"fixture_scope": "isolated_empty_acceptance_directory",
                  "test_principals_explicitly_provisioned": True,
                  "common_runtime": True,
                  "authenticated_classical_protocol": classical,
                  "tls13": qkd["delivery"]["tls_version"] == "TLSv1.3",
                  "audit": audit},
              "scope": "local identified emulation, independent processes and replay; no hardware qualification"}
    (root / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report
