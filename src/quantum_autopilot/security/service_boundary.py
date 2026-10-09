"""Shared authorization for in-process and HTTP application-service calls.

Python extension code running as the service account is trusted; this is a
service authorization boundary, not a sandbox for arbitrary Python code.
"""
from __future__ import annotations

from functools import wraps
from contextvars import ContextVar
import hashlib
import inspect
import json
from pathlib import Path
import re
import time


request_deadline = ContextVar("orion_request_deadline", default=None)


READ_METHODS = frozenset({
    "health", "summary", "control_plane_status", "wdm_model_status", "evidence_lanes", "optical_health",
    "control_plane_topology", "control_plane_events", "network_live_state", "network_decisions",
    "laboratory_state", "workspace_path_result", "policy_preparation_context", "review_policy_change",
    "policy_activation_status", "policy_compatibility_catalog", "inspect_policy_compatibility",
    "policy_compatibility_context", "policy_decision_history", "explain_policy_decision", "policy_recovery_context",
    "assess", "assess_published_cell", "example_decision", "security_status", "history", "capabilities",
})
METHOD_ACTIONS = {
    **{name: "network.read" for name in READ_METHODS},
    "security_status": "security.read",
    "fpga_device_state": "adapters.read",
    "prepare_policy_change": "policy.prepare", "approve_policy_change": "policy.approve",
    "activate_policy_change": "policy.activate", "recover_policy_change": "policy.activate",
    "prepare_policy_rollback": "policy.prepare", "observe_policy_compatibility": "simulation.run",
    "invalidate_policy_preparation": "policy.prepare",
    "export_policy_review": "evidence.export", "export_policy_approval": "evidence.export",
    "export_policy_activation": "evidence.export",
    "run_control_cycle": "simulation.run", "run_control_scenario": "simulation.run",
    "run_wdm_model_replay": "simulation.run", "run_workspace_path": "simulation.run",
    "add_simulated_node": "network.configure", "configure_simulated_link": "network.configure",
    "apply_simulated_node_action": "network.operate", "add_network_node": "network.configure",
    "set_node_location": "network.configure", "set_node_isolation": "network.operate",
    "set_link_active": "network.operate", "record_node_calibration": "adapters.configure",
    "configure_network_input": "ingestion.write", "network_step": "network.operate",
    "recalibrate_node": "network.operate", "run_lab_experiment": "experiments.run",
    "configure_laboratory": "adapters.configure", "configure": "adapters.configure",
    "run": "experiments.run", "recover": "state.restore", "exclusive_operation": "experiments.run",
}
CONFIGURE_VERBS = frozenset({"set_model_archive", "set_ml_control", "configure_network_input",
    "configure_technology", "configure_technology_source", "save_communicator",
    "configure_communicator_connection", "define_pair", "remove_pair",
    "register_fpga_device", "configure_fpga_device", "set_fpga_connection"})


def service_resource(service, args=(), kwargs=None):
    location = getattr(service, "control_journal_path", None) or getattr(service, "root", None)
    location = Path(location).resolve()
    if getattr(service, "control_journal_path", None) is not None:
        location = location.parent
    resource = "workspace:" + location.as_posix()
    payload = next((value for value in args if isinstance(value, dict)), None)
    payload = payload or (kwargs or {}).get("request") or (kwargs or {}).get("payload") or {}
    for key, kind in (("node_id", "nodes"), ("link_id", "links")):
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
            return resource + "/" + kind + "/" + value
    return resource


def protected(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        deadline = request_deadline.get()
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("operation deadline exceeded; inspect durable receipts")
        runtime = self.security
        action = METHOD_ACTIONS.get(method.__name__, "service.manage")
        if method.__name__ == "network_command":
            verb = args[0] if args else kwargs.get("verb")
            action = ("security.manage" if verb == "provision_satellite_session" else
                      "evidence.export" if verb == "export_technology_events" else
                      "network.configure" if verb in CONFIGURE_VERBS else "network.operate")
        resource = service_resource(self, args, kwargs)
        identity = runtime.authorize(action, resource)
        try:
            result = method(self, *args, **kwargs)
        except Exception as error:
            runtime.audit.record(identity, action, resource, "failed",
                                 {"error_type": type(error).__name__})
            raise
        # Runtime audits every authorization. Mutation/export outcomes add an
        # explicit result; reads do not duplicate the authorization record.
        if not action.endswith(".read") and method.__name__ != "exclusive_operation":
            details = {}
            payload = next((value for value in args if isinstance(value, dict)), None)
            if payload is not None:
                try:
                    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    details["configuration_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
                except (TypeError, ValueError):
                    pass
            if isinstance(result, dict):
                receipt_hash = result.get("receipt_sha256")
                if isinstance(receipt_hash, str) and re.fullmatch(r"[a-f0-9]{64}", receipt_hash):
                    details["artifact_sha256"] = receipt_hash
                else:
                    try:
                        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
                        details["artifact_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
                    except (TypeError, ValueError):
                        pass
            runtime.audit.record(identity, action, resource, "completed", details)
        return result
    return call


def secure_service(cls):
    """Protect all public callables, with unknown future actions denied to non-admins."""
    for name, method in list(vars(cls).items()):
        if not name.startswith("_") and inspect.isfunction(method):
            setattr(cls, name, protected(method))
    return cls
