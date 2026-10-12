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
from uuid import UUID


request_deadline = ContextVar("orion_request_deadline", default=None)


class DurableResultAuditPending(RuntimeError):
    """A durable receipt exists, but its authenticated outcome audit is pending."""
    def __init__(self, operation, result):
        if operation in {'activate_policy_change', 'recover_policy_change'}:
            receipt = result.get('receipt', result) if isinstance(result, dict) else {}
            record = receipt.get('record', {}) if isinstance(receipt, dict) else {}
            try:
                canonical_id = str(UUID(record.get('activation_id', '')))
                digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(',', ':'),
                                                  ensure_ascii=False, allow_nan=False).encode()).hexdigest()
            except (AttributeError, ValueError, TypeError):
                raise ValueError('committed policy receipt required for audit recovery') from None
            expected_schema = ('orion.policy_activation.v1' if operation == 'activate_policy_change'
                               else 'orion.policy_recovery.v1')
            if (record.get('schema') != expected_schema
                    or record.get('status') != 'COMMITTED'
                    or record.get('activation_id') != canonical_id
                    or receipt.get('record_sha256') != digest):
                raise ValueError('committed policy receipt required for audit recovery')
            super().__init__('durable policy transition committed; authenticated audit pending; inspect active status without resubmission')
            self.operation, self.receipt = operation, receipt
            self.activation_id = record['activation_id']
            self.record_sha256 = receipt['record_sha256']
            return
        super().__init__("durable result committed; authenticated audit pending; retry the same request id")
        self.operation = operation
        self.request_id = result["request_id"]
        self.receipt_sha256 = result["receipt_sha256"]

    def recovery(self):
        if self.operation in {'activate_policy_change', 'recover_policy_change'}:
            return {'error': 'durable_result_audit_pending', 'committed': True,
                    'operation': self.operation, 'activation_id': self.activation_id,
                    'record_sha256': self.record_sha256,
                    'recovery': 'inspect_activation_or_recovery_status_without_resubmission'}
        return {"error": "durable_result_audit_pending", "committed": True,
                "operation": self.operation, "request_id": self.request_id,
                "receipt_sha256": self.receipt_sha256, "recovery": "retry_same_request_id_or_inspect_status"}


READ_METHODS = frozenset({
    "health", "summary", "control_plane_status", "wdm_model_status", "evidence_lanes", "optical_health",
    "control_plane_topology", "control_plane_events", "network_live_state", "network_decisions",
    "laboratory_state", "workspace_path_result", "policy_preparation_context", "review_policy_change",
    "policy_activation_status", "policy_compatibility_catalog", "inspect_policy_compatibility",
    "policy_compatibility_context", "policy_decision_history", "explain_policy_decision", "policy_recovery_context",
    "assess", "assess_published_cell", "example_decision", "security_status", "history", "capabilities",
    "console_snapshot",
    "mixed_controller_status",
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
    "console_evidence": "evidence.export",
    "configure_mixed_controller": "network.configure",
    "ingest_mixed_observations": "ingestion.write",
    "reserve_mixed_route": "network.operate",
    "release_mixed_reservation": "network.operate",
    "run": "experiments.run", "recover": "state.restore", "exclusive_operation": "experiments.run",
}
CONSOLE_SERVICE_METHODS = {
    "add_network_node": "add_network_node", "set_node_location": "set_node_location",
    "set_node_isolation": "set_node_isolation", "set_link_active": "set_link_active",
    "recalibrate_node": "recalibrate_node", "run_workspace_path": "run_workspace_path",
    "run_lab_experiment": "run_lab_experiment", "configure_laboratory": "configure_laboratory",
    "replay_model": "run_wdm_model_replay", "run_scenario": "run_control_scenario",
    "run_control_cycle": "run_control_cycle",
}
CONFIGURE_VERBS = frozenset({"set_model_archive", "set_ml_control", "configure_network_input",
    "configure_technology", "configure_technology_source", "save_communicator",
    "configure_communicator_connection", "define_pair", "remove_pair",
    "register_fpga_device", "configure_fpga_device", "set_fpga_connection"})


def service_resource(service, args=(), kwargs=None, *, operation=None):
    location = getattr(service, "control_journal_path", None) or getattr(service, "root", None)
    location = Path(location).resolve()
    if getattr(service, "control_journal_path", None) is not None:
        location = location.parent
    resource = "workspace:" + location.as_posix()
    payload = next((value for value in args if isinstance(value, dict)), None)
    payload = payload or (kwargs or {}).get("request") or (kwargs or {}).get("payload") or {}
    if operation == "ingest_mixed_observations" and isinstance(payload, dict):
        records = payload.get("records")
        if isinstance(records, list) and 1 <= len(records) <= 256:
            reported = [record.get("observation", {}).get("node_id") for record in records
                        if isinstance(record, dict) and isinstance(record.get("observation"), dict)]
            if (len(reported) == len(records) and all(isinstance(value, str) and
                    re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) for value in reported)
                    and len(set(reported)) == 1):
                return resource + "/nodes/" + reported[0]
    # A decorative node_id must never turn a global source/configuration
    # operation into a node-scoped authorization. Only known target operations
    # may narrow the resource, using their actual target field.
    targets = {
        "set_node_location": ("node_id", "nodes"), "set_node_isolation": ("node_id", "nodes"),
        "recalibrate_node": ("node_id", "nodes"), "record_node_calibration": ("node_id", "nodes"),
        "apply_simulated_node_action": ("node_id", "nodes"),
        "set_link_active": ("link_id", "links"), "configure_simulated_link": ("link_id", "links"),
    }
    for key, kind in (targets[operation],) if operation in targets else ():
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
            return resource + "/" + kind + "/" + value
    return resource


def admit_service_call(service, method_name, args=(), kwargs=None):
    """Check current authority/deadline, also after a bounded resource wait."""
    kwargs = kwargs or {}
    deadline = request_deadline.get()
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("operation deadline exceeded; inspect durable receipts")
    action = METHOD_ACTIONS.get(method_name, "service.manage")
    operation = method_name
    if method_name == "network_command":
        verb = args[0] if args else kwargs.get("verb")
        operation = verb
        action = ("security.manage" if verb == "provision_satellite_session" else
                  "evidence.export" if verb == "export_technology_events" else
                  "network.configure" if verb in CONFIGURE_VERBS else "network.operate")
    elif method_name == "console_command":
        verb = args[0] if args else kwargs.get("verb")
        operation = CONSOLE_SERVICE_METHODS.get(verb)
        action = METHOD_ACTIONS.get(operation, "service.manage")
    resource = service_resource(service, args, kwargs, operation=operation)
    return service.security.authorize(action, resource), action, resource


def protected(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        runtime = self.security
        identity, action, resource = admit_service_call(self, method.__name__, args, kwargs)
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
            try:
                runtime.audit.record(identity, action, resource, "completed", details)
                if method.__name__ in {"reserve_mixed_route", "release_mixed_reservation"}:
                    self._complete_mixed_audit(result, method.__name__)
            except Exception as error:
                if method.__name__ in {"reserve_mixed_route", "release_mixed_reservation",
                                        "activate_policy_change", "recover_policy_change"}:
                    raise DurableResultAuditPending(method.__name__, result) from error
                raise
        return result
    return call


def secure_service(cls):
    """Protect all public callables, with unknown future actions denied to non-admins."""
    for name, method in list(vars(cls).items()):
        if not name.startswith("_") and inspect.isfunction(method):
            setattr(cls, name, protected(method))
    return cls
