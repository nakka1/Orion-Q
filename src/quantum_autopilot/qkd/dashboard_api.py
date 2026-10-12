"""Authenticated assurance/control API; remote service requires operational mTLS."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import mimetypes
from contextlib import nullcontext
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import TYPE_CHECKING, Any, Mapping
from urllib.parse import parse_qs, unquote, urlparse

from quantum_autopilot import __version__ as autopilot_version
from quantum_autopilot.control_plane import (
    DEFAULT_SCENARIO_PRESET,
    DEFAULT_TOPOLOGY_ID,
    ControllerRuntime,
    ScenarioDefinition,
    SimulatedTopologyStore,
    TopologyScenarioRunner,
    resilience_demo_scenario,
)
if TYPE_CHECKING:
    from quantum_autopilot.control_plane import LoadedWDMModelBundle, WDMModelDrivenController
from quantum_autopilot.core import EvidenceKind, Provenance
from quantum_autopilot.evidence_lanes import (
    EvidenceLaneService,
    MeasuredOpticalHealthAdapter,
)
from quantum_autopilot.qkd.claim_registry_adapter import (
    REQUESTED_USES,
    ClaimRegistryAdapter,
    build_assurance_workload,
)
from quantum_autopilot.qkd.operating_point import QKDOperatingPoint
from quantum_autopilot.qkd.api_contract import API_VERSION, DEFAULT_HOST, DEFAULT_PORT, MAX_REQUEST_BYTES
from quantum_autopilot.security.service_boundary import (CONSOLE_SERVICE_METHODS, secure_service,
                                                        DurableResultAuditPending, admit_service_call)
from quantum_autopilot.security.equipment import EquipmentDenied
from quantum_twin.evaluation.thesis_claims import (
    load_claim_registry,
    validate_claim_registry,
)


EXAMPLE_POINT_PATH = Path(
    "configs/qkd_assurance/irelandqci_example_operating_point.json"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_object(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return {str(key): item for key, item in value.items()}


@secure_service
class AssuranceDashboardService:
    """Read-only application service shared by HTTP and unit tests."""

    # Explicit adapter feature: old/fake services retain their no-argument API.
    network_metadata_supported = True

    def __init__(
        self,
        repository_root: str | Path | None = None,
        *,
        control_journal_path: str | Path | None = None,
        optical_wdm_csv: str | Path | None = None,
        optical_evidence_report_path: str | Path | None = None,
        expected_optical_dataset_sha256: str | None = None,
        wdm_model_bundle_path: str | Path | None = None,
        expected_wdm_bundle_sha256: str | None = None,
        wdm_replay_max_cycles: int = 100,
        network_seed: int = 0,
        security=None,
        security_root: str | Path | None = None,
    ) -> None:
        default_root = Path(__file__).resolve().parents[3]
        self.repository_root = Path(repository_root or default_root).resolve()
        self.registry_path = self.repository_root / "docs/thesis/tcc_claim_registry.json"
        self.adapter = ClaimRegistryAdapter(repository_root=self.repository_root)
        self.control_journal_path = Path(
            control_journal_path
            or self.repository_root / "outputs/control_plane/sandbox_journal.sqlite"
        ).resolve()
        from quantum_autopilot.security import SecurityRuntime
        self.security = security or SecurityRuntime(
            security_root or self.control_journal_path.parent / ".security")
        self._controller_runtime: ControllerRuntime | None = None
        self._policy_preparation = None
        self._policy_compatibility = None
        self._policy_preparation_generation = 0
        self._topology_store: SimulatedTopologyStore | None = None
        # Live network engine (design §2.5): created on first use, bound to this journal.
        self._network_engine = None
        self._network_lock = RLock()
        self._mixed_controller = None
        self._mixed_resource_authority = None
        self._mixed_controller_lock = RLock()
        self.network_seed = network_seed
        self.is_demo = False
        self._laboratory = None
        self._laboratory_lock = RLock()
        self._scenario_runner: TopologyScenarioRunner | None = None
        self.wdm_model_bundle_path = (
            Path(wdm_model_bundle_path).resolve()
            if wdm_model_bundle_path is not None
            else None
        )
        self.expected_wdm_bundle_sha256 = expected_wdm_bundle_sha256
        if (
            isinstance(wdm_replay_max_cycles, bool)
            or not isinstance(wdm_replay_max_cycles, int)
            or not 1 <= wdm_replay_max_cycles <= 250
        ):
            raise ValueError("wdm_replay_max_cycles must be an integer in [1, 250]")
        self.wdm_replay_max_cycles = wdm_replay_max_cycles
        self._wdm_model_bundle: LoadedWDMModelBundle | None = None
        self._wdm_model_controller: WDMModelDrivenController | None = None
        self._controller_lock = RLock()
        optical_adapter = (
            MeasuredOpticalHealthAdapter(
                optical_wdm_csv,
                forecast_report_path=optical_evidence_report_path,
                expected_dataset_sha256=expected_optical_dataset_sha256,
            )
            if optical_wdm_csv is not None
            else None
        )
        self.evidence_lane_service = EvidenceLaneService(optical_adapter)

    def security_status(self):
        return self.security.snapshot()

    def console_snapshot(self, *, include_static: bool = True):
        """Transport records for a remote UI, without importing any GUI package.

        Reads never step the engine or run a replay. Presentation selection and
        formatting belong to the client; all protocol state remains here.
        A version handshake prevents mixing a new topology into an old frame.
        """
        if type(include_static) is not bool:
            raise ValueError("include_static must be a boolean")
        identity = self.security.current_identity()
        from quantum_autopilot.control_plane.console_commands import read_commands
        with self.security.audit.batch():
            for _attempt in range(3):
                live = self.network_live_state()
                topology = self.control_plane_topology()
                metadata = self.network_live_state(metadata_only=True)
                version = (topology.get("topology") or {}).get("topology_version")
                if (live.get("publication_revision") == metadata.get("publication_revision")
                        and live.get("engine_session_id") == metadata.get("engine_session_id")
                        and live.get("topology_version") == version):
                    break
            else:
                raise ValueError("network state changed during observation; request a fresh snapshot")
            result = {"schema": "orion.console.remote.v1", "health": self.health(),
                      "topology": topology, "events": self.control_plane_events(limit=100)["events"],
                      "live": live, "laboratory": self.laboratory_state(),
                      "workspace_path": self.workspace_path_result(),
                      "safety_events": self.controller_runtime.journal.list_recent_safety_events(limit=50),
                      "commands": read_commands(self.control_journal_path),
                      "replay": {"available": False, "reason": "explicit replay required"},
                      "security": {"subject": identity.subject, "display_name": identity.display_name,
                                   "roles": sorted(identity.roles), "scopes": list(identity.scopes),
                                   "source": identity.source, "expires_at": identity.expires_at,
                                   "profile": self.security.profile, "identity_profile": self.security.identity_profile,
                                   "vault_mode": self.security.vault.mode, "physical_qualification": False}}
            if include_static:
                result["model"] = self.wdm_model_status()
                result["evidence_lanes"] = self.evidence_lanes()
                try:
                    result["summary"] = self.summary()
                except ValueError:
                    result["summary"] = {"available": False, "reason": "published evidence failed validation"}
        return result

    def console_command(self, verb: str, payload: dict):
        """Explicit service operations, protected again at the target boundary."""
        if not isinstance(verb, str) or not isinstance(payload, dict) or verb not in CONSOLE_SERVICE_METHODS:
            raise ValueError("unsupported remote console command")
        tracked = {"add_network_node", "set_node_location", "set_node_isolation", "set_link_active", "recalibrate_node"}
        if verb in tracked:
            from quantum_autopilot.control_plane.console_commands import ConsoleCommandJournal, validated_request
            from quantum_autopilot.control_plane.topology import _sha256
            from quantum_autopilot.security.service_boundary import METHOD_ACTIONS, service_resource
            request = validated_request(verb, payload)
            journal = ConsoleCommandJournal(self.controller_runtime.journal.path)
            command_id = journal.begin(verb, request)
            try:
                self.security.authorize(METHOD_ACTIONS[verb], service_resource(self, (request,), operation=verb))
            except PermissionError:
                journal.transition(command_id, "REJECTED", {"reason": "capability_revoked_before_dispatch"})
                raise
            journal.transition(command_id, "RUNNING")
            try:
                result = getattr(self, CONSOLE_SERVICE_METHODS[verb])(request)
                persisted = self.topology_store.get(request["topology_id"])
                if (result.get("status") != "persisted" or result.get("physical_action_performed") is not False
                        or persisted is None or persisted.topology_version != request["expected_version"] + 1
                        or persisted.to_dict() != result.get("topology")):
                    raise ValueError("inventory readback did not confirm this response")
            except Exception as error:
                journal.transition(command_id, "UNKNOWN", {"reason": "execution_or_readback_failed",
                                                          "error_type": type(error).__name__})
                raise RuntimeError("inventory outcome uncertain; inspect durable command receipts") from None
            record = journal.transition(command_id, "CONFIRMED", {"topology_version": persisted.topology_version,
                                        "topology_sha256": _sha256(persisted.to_dict())})
            return {**result, "command": {"record": record, "record_sha256": _sha256(record)}}
        return getattr(self, CONSOLE_SERVICE_METHODS[verb])(payload)

    def console_evidence(self, scope: str):
        """Audited download; a remote client cannot choose server file paths."""
        if not isinstance(scope, str) or not scope.strip() or len(scope) > 128:
            raise ValueError("bounded evidence scope required")
        evidence = self.console_snapshot()
        encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return {"schema": "orion.console.remote-evidence.v1", "scope": scope,
                "evidence": evidence, "record_sha256": hashlib.sha256(encoded).hexdigest(),
                "physical_action_performed": False}

    def _release_controller_io_resources(self):
        """Release initialized idle journal pagers without creating a runtime.

        This is resource cleanup, including after identity revocation. It does
        not reset policy, network state, authorizations or stored records.
        """
        with self._controller_lock:
            runtimes = [self._controller_runtime]
            if self._wdm_model_controller is not None:
                runtimes.append(self._wdm_model_controller.runtime)
            journals = {id(runtime.journal): runtime.journal
                        for runtime in runtimes if runtime is not None}
            for journal in journals.values():
                journal.release_io_resources()
        with self._mixed_controller_lock:
            if self._mixed_controller is not None:
                self._mixed_controller.close()
                self._mixed_controller = None

    @property
    def controller_runtime(self) -> ControllerRuntime:
        """Create the journal-backed runtime only when the sandbox is used."""

        with self._controller_lock:
            if self._controller_runtime is None:
                self._controller_runtime = ControllerRuntime(self.control_journal_path)
            return self._controller_runtime

    def _policy_preparation_session(self):
        """Bind preparation to the already initialized local runtime, never create it."""
        from quantum_autopilot.control_plane.policy_preparation import PolicyPreparationSession
        if self._controller_runtime is None:
            raise ValueError("initialize the simulation independently before policy preparation")
        if self._policy_preparation is not None and self._policy_preparation.runtime is not self._controller_runtime:
            self._policy_preparation.invalidate()
            self._policy_preparation = None
        if self._policy_preparation is None:
            self._policy_preparation = PolicyPreparationSession(self._controller_runtime)
        return self._policy_preparation

    def policy_preparation_context(self):
        with self._controller_lock:
            return self._policy_preparation_session().inspect()

    def prepare_policy_change(self, payload):
        with self._controller_lock:
            return self._policy_preparation_session().prepare(payload)

    def review_policy_change(self, proposal_id, expected_sha256):
        with self._controller_lock:
            return self._policy_preparation_session().review(proposal_id, expected_sha256)

    def export_policy_review(self, proposal_id, expected_sha256, destination):
        with self._controller_lock:
            return self._policy_preparation_session().export_review(proposal_id, expected_sha256, destination)

    def approve_policy_change(self, payload):
        with self._controller_lock:
            return self._policy_preparation_session().approve(payload)

    def export_policy_approval(self, approval_id, expected_sha256, destination):
        with self._controller_lock:
            return self._policy_preparation_session().export_approval(approval_id, expected_sha256, destination)

    def activate_policy_change(self, payload):
        from quantum_autopilot.control_plane.policy_activation import activate
        with self._controller_lock:
            admit_service_call(self, 'activate_policy_change')
            return activate(self._policy_preparation_session(), payload,
                            validate_source=lambda: admit_service_call(self, 'activate_policy_change'))

    def policy_activation_status(self):
        from quantum_autopilot.control_plane.policy_activation import activation_status
        with self._controller_lock:
            if self._controller_runtime is None:
                raise ValueError('initialize the simulation independently before policy readback')
            return activation_status(self._controller_runtime)

    def export_policy_activation(self, activation_id, expected_sha256, destination):
        from quantum_autopilot.control_plane.policy_activation import export_activation
        with self._controller_lock:
            return export_activation(self.control_journal_path, activation_id, expected_sha256, destination)

    def policy_compatibility_catalog(self):
        from quantum_autopilot.model_intake.policy_compatibility import policy_compatibility_catalog
        return policy_compatibility_catalog()

    def inspect_policy_compatibility(self, candidate_id):
        from quantum_autopilot.model_intake.policy_compatibility import inspect_policy_compatibility
        return inspect_policy_compatibility(candidate_id)

    def _policy_compatibility_session(self):
        from quantum_autopilot.control_plane.policy_context import PolicyCompatibilitySession
        runtime = self._controller_runtime
        if runtime is None:
            raise ValueError('existing simulation runtime required; observation does not initialize it')
        if self._policy_compatibility is not None and self._policy_compatibility.runtime is not runtime:
            self._policy_compatibility.invalidate()
            self._policy_compatibility = None
        if self._policy_compatibility is None:
            self._policy_compatibility = PolicyCompatibilitySession(runtime)
        return self._policy_compatibility

    def policy_compatibility_context(self):
        with self._controller_lock:
            return self._policy_compatibility_session().inspect()

    def observe_policy_compatibility(self, payload):
        with self._controller_lock:
            return self._policy_compatibility_session().observe(payload)

    def policy_decision_history(self, limit=20):
        from quantum_autopilot.control_plane.policy_decisions import decision_history
        return decision_history(self.control_journal_path, limit)

    def explain_policy_decision(self, request_id, expected_sha256=None):
        from quantum_autopilot.control_plane.policy_decisions import explain_decision
        return explain_decision(self.control_journal_path, request_id, expected_sha256)

    def prepare_policy_rollback(self, activation_id, expected_sha256):
        from quantum_autopilot.control_plane.policy_recovery import prepare_rollback
        with self._controller_lock:
            return prepare_rollback(self._policy_preparation_session(), activation_id, expected_sha256)

    def policy_recovery_context(self):
        from quantum_autopilot.control_plane.policy_recovery import inspect_recovery
        with self._controller_lock:
            if self._controller_runtime is None:
                raise ValueError('existing runtime required for recovery')
            return inspect_recovery(self._controller_runtime)

    def recover_policy_change(self, payload):
        from quantum_autopilot.control_plane.policy_recovery import recover
        with self._controller_lock:
            admit_service_call(self, 'recover_policy_change')
            if self._controller_runtime is None:
                raise ValueError('existing runtime required for recovery')
            result = recover(self._controller_runtime, payload,
                             validate_source=lambda: admit_service_call(self, 'recover_policy_change'))
            self._invalidate_policy_preparation()
            return result

    def invalidate_policy_preparation(self):
        self._invalidate_policy_preparation()

    def _invalidate_policy_preparation(self):
        """Retire sessions internally, including after an admitted durable commit.

        Public callers still enter the protected method above. Post-commit
        housekeeping must not depend on the operator remaining logged in.
        """
        with self._controller_lock:
            self._policy_preparation_generation += 1
            if self._policy_compatibility is not None:
                self._policy_compatibility.invalidate()
                self._policy_compatibility = None
            if self._controller_runtime is not None:
                with self._controller_runtime._policy_recovery_lock:
                    self._controller_runtime._policy_recovery_selection = None
            if self._policy_preparation is not None:
                self._policy_preparation.invalidate()
                self._policy_preparation = None

    @property
    def topology_store(self) -> SimulatedTopologyStore:
        """Share the sandbox database without exposing its path over HTTP."""

        with self._controller_lock:
            if self._topology_store is None:
                self._topology_store = SimulatedTopologyStore(self.control_journal_path)
            return self._topology_store

    @property
    def scenario_runner(self) -> TopologyScenarioRunner:
        with self._controller_lock:
            if self._scenario_runner is None:
                self._scenario_runner = TopologyScenarioRunner(
                    self.controller_runtime,
                    self.topology_store,
                )
            return self._scenario_runner

    @property
    def wdm_model_bundle(self) -> LoadedWDMModelBundle:
        """Load the server-configured, non-pickle bundle once after validation."""

        if self.wdm_model_bundle_path is None:
            raise ValueError("no WDM model bundle is configured on the local server")
        with self._controller_lock:
            if self._wdm_model_bundle is None:
                from quantum_autopilot.control_plane import load_wdm_model_bundle
                self._wdm_model_bundle = load_wdm_model_bundle(
                    self.wdm_model_bundle_path,
                    expected_bundle_sha256=self.expected_wdm_bundle_sha256,
                )
            return self._wdm_model_bundle

    @property
    def wdm_model_controller(self) -> WDMModelDrivenController:
        """Use the bundle's frozen task policy and calibrated safety ceiling."""

        with self._controller_lock:
            if self._wdm_model_controller is None:
                from quantum_autopilot.control_plane import WDMModelDrivenController
                bundle = self.wdm_model_bundle
                runtime = ControllerRuntime(
                    self.control_journal_path,
                    policy=bundle.policy(),
                    supervisor=bundle.supervisor(),
                )
                self._wdm_model_controller = WDMModelDrivenController(
                    runtime,
                    bundle.inference_adapter(),
                )
            return self._wdm_model_controller

    def health(self) -> dict[str, Any]:
        """Return a minimal, non-mutating liveness and safety statement."""

        clock = getattr(self, "_headless_clock", None)
        return {
            "status": "ok",
            "api_version": API_VERSION,
            "autopilot_version": autopilot_version,
            "read_only": True,
            "physical_read_only": True,
            "local_mutation_scope": "sandbox_journal_simulated_topology_and_laboratory_test_evidence",
            "automation_allowed": False,
            "control_plane_sandbox_available": True,
            "policy_preparation_available": self._controller_runtime is not None,
            "simulated_inventory_editor_available": True,
            "simulated_link_editor_available": True,
            "workspace_path_available": True,
            "workspace_temporal_path_available": True,
            "workspace_temporal_campaign_available": True,
            "laboratory_emulation_available": self.laboratory.capabilities()["available"],
            "wdm_model_bundle_configured": self.wdm_model_bundle_path is not None,
            "processing_clock": clock.status() if clock is not None else {
                "state": "manual", "active": False, "owner": "service-api"},
            "generated_at": _utc_now(),
        }

    @property
    def laboratory(self):
        from quantum_autopilot.lab.service import LaboratoryService
        with self._laboratory_lock:
            if self._laboratory is None:
                self._laboratory = LaboratoryService(self.control_journal_path.parent / "laboratory", security=self.security)
            return self._laboratory

    def laboratory_state(self):
        from quantum_autopilot.lab.configuration import LaboratoryConfigurationStore
        history = self.laboratory.history()
        configuration = LaboratoryConfigurationStore(self.laboratory.root).state()
        configuration["replay_candidates"] = [
            {"run_id": run["run_id"], "parameters": run["results"]["photon"]["configuration"]}
            for run in history["runs"] if run["status"] == "completed"
            and run.get("results", {}).get("photon", {}).get("configuration")
            and (self.laboratory.root / run["run_id"] / "photon" / "nominal.manifest.json").is_file()]
        return {**history, "capabilities": self.laboratory.capabilities(), "configuration": configuration}

    def run_lab_experiment(self, request):
        return self.laboratory.run(request)

    def _mixed(self):
        from quantum_autopilot.control_plane.mixed_controller import MixedController
        with self._mixed_controller_lock:
            if self._mixed_controller is None:
                self._mixed_controller = MixedController(self.control_journal_path.parent / "mixed-controller.sqlite",
                    resource_authority=getattr(self, "_mixed_resource_authority", None))
            return self._mixed_controller

    def mixed_controller_status(self):
        from quantum_autopilot.security.service_boundary import admit_service_call
        return self._mixed().status(admit=lambda: admit_service_call(self, "mixed_controller_status"))

    def configure_mixed_controller(self, request):
        from quantum_autopilot.security.service_boundary import admit_service_call
        return self._mixed().configure(request, admit=lambda: admit_service_call(self, "configure_mixed_controller", (request,)))

    def ingest_mixed_observations(self, request):
        # A registered source only gains ingestion authority, never routing or
        # equipment-control privileges. Every reported node is checked separately.
        from quantum_autopilot.control_plane.mixed_contracts import exact, validate_record
        from quantum_autopilot.security.service_boundary import service_resource
        exact(request, {"records"})
        if not isinstance(request["records"], list) or not 1 <= len(request["records"]) <= 256:
            raise ValueError("invalid mixed observation batch")
        observations = [validate_record(record)[0] for record in request["records"]]
        workspace = service_resource(self)
        def admit():
            from quantum_autopilot.security.service_boundary import admit_service_call
            admit_service_call(self, "ingest_mixed_observations", (request,))
            for node_id in {observation.node_id for observation in observations}:
                self.security.authorize("ingestion.write", workspace + "/nodes/" + node_id)
        return self._mixed().ingest(request, admit=admit)

    def reserve_mixed_route(self, request):
        from quantum_autopilot.security.service_boundary import admit_service_call
        return self._mixed().demand(request, admit=lambda: admit_service_call(self, "reserve_mixed_route", (request,)))

    def release_mixed_reservation(self, request):
        from quantum_autopilot.security.service_boundary import admit_service_call
        return self._mixed().release(request, admit=lambda: admit_service_call(self, "release_mixed_reservation", (request,)))

    def _complete_mixed_audit(self, result, operation):
        from quantum_autopilot.security.service_boundary import service_resource
        core = self._mixed()
        for pending in core.pending_audit_for_request(result["request_id"]):
            if (pending["receipt_sha256"], pending["operation"]) != (result["receipt_sha256"], operation):
                # The receipt may have expired or been invalidated on restart.
                # Recover the original hash explicitly; never silently erase a
                # pending outcome merely because the current state changed.
                identity = self.security.current_identity()
                self.security.authorize("network.operate", service_resource(self))
                self.security.audit.record(identity, "network.operate", service_resource(self), "reconciled",
                    {"request_id": pending["request_id"], "operation": pending["operation"],
                     "artifact_sha256": pending["receipt_sha256"]})
            core.audit_completed(pending["request_id"], pending["receipt_sha256"], pending["operation"])

    def configure_laboratory(self, request):
        return self.laboratory.configure(request)

    def summary(self) -> dict[str, Any]:
        """Build a company-facing summary from validated source artifacts."""

        registry = load_claim_registry(self.registry_path)
        validation = validate_claim_registry(
            self.registry_path,
            repository_root=self.repository_root,
        ).report
        grid_status = self.adapter.grid_classifier.classify_optional(None)
        if not grid_status.grid_valid:
            raise ValueError("operating grid failed integrity validation")
        grid = json.loads(
            self.adapter.grid_classifier.grid_path.read_text(encoding="utf-8")
        )
        claims = []
        for claim in registry["claims"]:
            claims.append(
                {
                    "id": claim["id"],
                    "role": claim["role"],
                    "statement": claim["statement"],
                    "status": claim["status"],
                    "boundaries": list(claim.get("boundaries", [])),
                    "permitted_language": list(claim.get("permitted_language", [])),
                    "forbidden_language": list(claim.get("forbidden_language", [])),
                    "product_behavior": dict(claim["product_behavior"]),
                    "evidence_item_count": len(claim.get("evidence", [])),
                }
            )
        campaigns = []
        for campaign in grid["campaigns"]:
            fiber_key = f"{float(campaign['fiber_length_km']):g}"
            campaigns.append(
                {
                    "campaign_id": campaign["campaign_id"],
                    "fiber_length_km": float(campaign["fiber_length_km"]),
                    "external_signal_present": campaign["external_signal_present"],
                    "external_signal_power_dbm": campaign["external_signal_power_dbm"],
                    "voa_attenuation_db": list(grid["voa_by_fiber_length_km"][fiber_key]),
                    "source_file_sha256": campaign["source_file_sha256"],
                }
            )
        return {
            "product": {
                "name": "WDM·QKD Assurance",
                "description": (
                    "Local scientific assurance and decision-support workbench for "
                    "WDM/QKD coexistence"
                ),
                "autopilot_version": autopilot_version,
                "read_only": True,
                "physical_read_only": True,
                "local_mutation_scope": "sandbox_journal_and_simulated_topology_only",
                "automation_allowed": False,
                "control_plane_sandbox_available": True,
            },
            "integrity": {
                "valid": bool(validation["valid"] and grid_status.grid_valid),
                "registry_sha256": validation["registry_sha256"],
                "grid_config_sha256": grid_status.grid_config_sha256,
                "grid_source_artifact_verified": grid_status.source_artifact_verified,
                "check_count": validation["check_count"],
                "failed_check_count": validation["failed_check_count"],
                "evidence_item_count": validation["evidence_item_count"],
            },
            "headline_claim": dict(registry["headline_claim"]),
            "claims": claims,
            "operating_grid": {
                "grid_id": grid["grid_id"],
                "dataset_id": grid["dataset_id"],
                "doi": grid["doi"],
                "license": grid["license"],
                "expected_cell_count": grid["expected_cell_count"],
                "interpolation_allowed": grid["interpolation_allowed"],
                "qber_unit_status": grid["qber_unit_status"],
                "source_artifact": dict(grid["source_artifact"]),
                "campaigns": campaigns,
                "limitations": list(grid["limitations"]),
            },
            "generated_at": _utc_now(),
        }

    def control_plane_status(self) -> dict[str, Any]:
        """Return non-physical sandbox capabilities and recent-cycle count."""

        status = self.controller_runtime.status()
        status["recent_events"] = self.controller_runtime.journal.list_recent(limit=100)
        status["persistent_topology_available"] = True
        status["scenario_runner_available"] = True
        status["generated_at"] = _utc_now()
        return status

    def wdm_model_status(self) -> dict[str, Any]:
        """Describe the locally configured model without exposing its path or weights."""

        if self.wdm_model_bundle_path is None:
            return {
                "api_version": API_VERSION,
                "configured": False,
                "valid": False,
                "replay_available": False,
                "reason": "no_server_configured_bundle",
                "default_mode": "shadow",
                "physical_action_available": False,
                "generated_at": _utc_now(),
            }
        try:
            status = self.wdm_model_bundle.status()
        except ValueError as exc:
            return {
                "api_version": API_VERSION,
                "configured": True,
                "valid": False,
                "replay_available": False,
                "reason": "bundle_validation_failed",
                "detail": str(exc),
                "default_mode": "shadow",
                "physical_action_available": False,
                "generated_at": _utc_now(),
            }
        return {
            "api_version": API_VERSION,
            **status,
            "replay_available": True,
            "max_replay_cycles_per_request": self.wdm_replay_max_cycles,
            "generated_at": _utc_now(),
        }

    @staticmethod
    def _replay_outcome(record: Mapping[str, Any]) -> str:
        safety = record["safety"]
        codes = {item["reason_code"] for item in safety["violations"]}
        if "input_out_of_distribution" in codes:
            return "safety_ood_abstention"
        if "prediction_uncertainty_too_high" in codes:
            return "safety_high_uncertainty_abstention"
        effective = safety["effective_action"]
        if effective == "WAIT":
            return "policy_probability_abstention"
        if effective == "PURIFY":
            return "purification_intent_shadow"
        if record["intent"]["policy_details"].get("reason_code") == "no_pair_available":
            return "no_pair"
        return "ordinary_halt"

    def run_wdm_model_replay(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Replay a bounded server-side trace through the frozen model in shadow mode."""

        payload = _json_object(request, name="request")
        allowed = {"start_index", "limit"}
        unknown = sorted(set(payload).difference(allowed))
        if unknown:
            raise ValueError(f"unsupported request fields: {unknown}")
        start = payload.get("start_index", 0)
        limit = payload.get("limit", min(25, self.wdm_replay_max_cycles))
        for name, value in (("start_index", start), ("limit", limit)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
        if start < 0:
            raise ValueError("start_index must be >= 0")
        if not 1 <= limit <= self.wdm_replay_max_cycles:
            raise ValueError(
                f"limit must be in [1, {self.wdm_replay_max_cycles}]"
            )
        bundle = self.wdm_model_bundle
        if start >= len(bundle.replay_records):
            raise ValueError("start_index is outside the configured replay trace")
        selected = bundle.replay_records[start : start + limit]
        rows: list[dict[str, Any]] = []
        action_counts: dict[str, int] = {}
        outcome_counts: dict[str, int] = {}
        for offset, replay_record in enumerate(selected):
            index = start + offset
            request_payload = replay_record.request.to_dict()
            provenance = dict(request_payload["provenance"])
            artifact_hashes = dict(provenance.get("artifact_hashes", {}))
            artifact_hashes["wdm_model_bundle"] = bundle.bundle_sha256
            provenance["artifact_hashes"] = artifact_hashes
            provenance_metadata = dict(provenance.get("metadata", {}))
            provenance_metadata.update(
                {
                    "model_bundle_sha256": bundle.bundle_sha256,
                    "bundle_replay_index": index,
                    "physical_calibration": False,
                    "measured_quantum_target": False,
                }
            )
            provenance["metadata"] = provenance_metadata
            request_payload["provenance"] = provenance
            metadata = dict(request_payload.get("metadata", {}))
            metadata.update(
                {
                    "model_bundle_sha256": bundle.bundle_sha256,
                    "bundle_replay_index": index,
                }
            )
            request_payload["metadata"] = metadata
            result = self.wdm_model_controller.cycle(request_payload, mode="shadow")
            cycle = result["record"]
            inference = result["inference"]
            if cycle["execution"]["physical_action_performed"]:
                raise RuntimeError("shadow replay violated the physical-action invariant")
            intended = cycle["intent"]["action"]
            effective = cycle["safety"]["effective_action"]
            outcome = self._replay_outcome(cycle)
            action_counts[effective] = action_counts.get(effective, 0) + 1
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
            mean = float(inference["mean"])
            uncertainty = float(inference["uncertainty"])
            rows.append(
                {
                    "index": index,
                    "timestamp_utc": replay_record.request.evaluated_at_utc,
                    "predicted_fidelity": mean,
                    "uncertainty": uncertainty,
                    "interval_lower": max(0.0, mean - uncertainty),
                    "interval_upper": min(1.0, mean + uncertainty),
                    "simulated_actual_fidelity": replay_record.simulated_actual_fidelity,
                    "out_of_distribution": inference["out_of_distribution"],
                    "ood_fraction": inference["ood_fraction"],
                    "availability_probability": inference["availability_probability"],
                    "conditional_fidelity": inference["conditional_fidelity"],
                    "intended_action": intended,
                    "effective_action": effective,
                    "outcome_category": outcome,
                    "safety_reason_codes": [
                        item["reason_code"] for item in cycle["safety"]["violations"]
                    ],
                    "record_sha256": result["record_sha256"],
                    "idempotent_replay": result["idempotent_replay"],
                    "physical_action_performed": False,
                }
            )
        return {
            "api_version": API_VERSION,
            "mode": "shadow",
            "bundle_sha256": bundle.bundle_sha256,
            "model_sha256": bundle.model_spec.model_sha256,
            "policy_spec_sha256": bundle.policy_spec.sha256,
            "start_index": start,
            "returned_count": len(rows),
            "trace_record_count": len(bundle.replay_records),
            "action_counts": dict(sorted(action_counts.items())),
            "abstention_counts": dict(sorted(outcome_counts.items())),
            "rows": rows,
            "scientific_status": dict(bundle.scientific_status),
            "manual_input_path": "separate_diagnostic_only",
            "physical_action_performed": False,
            "generated_at": _utc_now(),
        }

    def evidence_lanes(self) -> dict[str, Any]:
        """Expose the three non-interchangeable scientific/product lanes."""

        status = self.evidence_lane_service.status()
        status.update(
            {
                "api_version": API_VERSION,
                "generated_at": _utc_now(),
                "qkd_grid_integrity_required": True,
            }
        )
        return status

    def optical_health(self) -> dict[str, Any]:
        """Return a measured, hash-bound optical snapshot when configured."""

        return {
            "api_version": API_VERSION,
            "generated_at": _utc_now(),
            "snapshot": self.evidence_lane_service.optical_health().to_dict(),
        }

    def control_plane_topology(self) -> dict[str, Any]:
        """Return persisted simulated topology or its unpersisted default blueprint."""

        return {
            "api_version": API_VERSION,
            "generated_at": _utc_now(),
            **self.scenario_runner.describe(DEFAULT_TOPOLOGY_ID),
        }

    def run_control_cycle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Run one guarded simulation/shadow/assurance cycle."""

        payload = _json_object(request, name="request")
        allowed = {"mode", "snapshot"}
        unknown = sorted(set(payload).difference(allowed))
        if unknown:
            raise ValueError(f"unsupported request fields: {unknown}")
        if "snapshot" not in payload:
            raise ValueError("snapshot is required")
        try:
            result = self.controller_runtime.cycle(
                _json_object(payload["snapshot"], name="snapshot"),
                mode=payload.get("mode", "simulation"),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"invalid control-plane snapshot: {exc}") from exc
        return {
            "api_version": API_VERSION,
            "generated_at": _utc_now(),
            **result,
        }

    def add_simulated_node(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Native-local inventory increment; no new HTTP route or device operation."""
        from quantum_autopilot.control_plane.inventory import AddSimulatedNode
        command = AddSimulatedNode.from_dict(request)
        topology = self.topology_store.add_inventory_node(command)
        return {"status": "persisted", "physical_action_performed": False,
                "topology": topology.to_dict()}

    def configure_simulated_link(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Native-local configuration only; no new HTTP route or physical action."""
        from quantum_autopilot.control_plane.inventory import ConfigureSimulatedLink
        topology = self.topology_store.configure_inventory_link(ConfigureSimulatedLink.from_dict(request))
        return {"status": "persisted", "physical_action_performed": False, "topology": topology.to_dict()}

    def apply_simulated_node_action(self, request: Mapping[str, Any]) -> dict[str, Any]:
        from quantum_autopilot.control_plane.inventory import SimulatedNodeAction
        topology = self.topology_store.apply_inventory_node_action(SimulatedNodeAction.from_dict(request))
        return {'status': 'persisted', 'physical_action_performed': False, 'topology': topology.to_dict()}

    # Live-network inventory verbs (design §1): native-local pass-throughs, no HTTP route.
    def add_network_node(self, request: Mapping[str, Any]) -> dict[str, Any]:
        from quantum_autopilot.control_plane.inventory import AddNetworkNode
        command = AddNetworkNode.from_dict(request)
        self._network_hold("add_network_node")
        topology = self.topology_store.add_network_node(command)
        self._network_topology_changed(f"node {command.node_id} added")
        return {"status": "persisted", "physical_action_performed": False, "topology": topology.to_dict()}

    def set_node_location(self, request: Mapping[str, Any]) -> dict[str, Any]:
        from quantum_autopilot.control_plane.inventory import SetNodeLocation
        command = SetNodeLocation.from_dict(request)
        self._network_hold("set_node_location")
        topology = self.topology_store.set_node_location(command)
        self._network_topology_changed(f"node {command.node_id} location changed")
        return {"status": "persisted", "physical_action_performed": False, "topology": topology.to_dict()}

    def set_node_isolation(self, request: Mapping[str, Any]) -> dict[str, Any]:
        from quantum_autopilot.control_plane.inventory import SetNodeIsolation
        command = SetNodeIsolation.from_dict(request)
        self._network_hold("set_node_isolation")
        topology = self.topology_store.set_node_isolation(command)
        self._network_topology_changed(f"node {command.node_id} " + ("isolated" if command.isolated else "restored"))
        return {"status": "persisted", "physical_action_performed": False, "topology": topology.to_dict()}

    def set_link_active(self, request: Mapping[str, Any]) -> dict[str, Any]:
        from quantum_autopilot.control_plane.inventory import SetLinkActive
        command = SetLinkActive.from_dict(request)
        self._network_hold("set_link_active")
        topology = self.topology_store.set_link_active(command)
        self._network_topology_changed(f"fibre {command.link_id} " + ("enabled" if command.active else "disabled"))
        return {"status": "persisted", "physical_action_performed": False, "topology": topology.to_dict()}

    def record_node_calibration(self, payload: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
        """Persist the engine's finished recalibration report; this method calibrates nothing."""
        from quantum_autopilot.control_plane.inventory import RecalibrateNode
        topology = self.topology_store.record_node_calibration(RecalibrateNode.from_dict(payload), report)
        return {"status": "persisted", "physical_action_performed": False, "topology": topology.to_dict()}

    # ------------------------------------------------------------------
    # Live network engine shared by desktop and authenticated headless API.
    # ------------------------------------------------------------------

    @property
    def network_engine(self):
        """The ``NetworkEngine`` bound to this service's journal and topology store.

        Created on first use with ``IdleInputSource`` (normal operation shows
        configuration and routing only until an input source is configured). The
        demonstration uses the SAME class on its own journal (``demo_service``).
        """

        with self._network_lock:
            if self._network_engine is None:
                from quantum_autopilot.network_engine import IdleInputSource, ModelRegistry, NetworkEngine
                self._network_engine = NetworkEngine(
                    self.topology_store, self.control_journal_path, source=IdleInputSource(),
                    registry=ModelRegistry(), seed=self.network_seed, security=self.security)
            return self._network_engine

    def _network_hold(self, verb: str) -> None:
        """Refuse a topology change while a recalibration holds the topology version."""
        engine = self._network_engine
        hold = None if engine is None else engine.calibration.hold_reason()
        if hold is not None:
            raise ValueError(f"{verb} refused: {hold}")

    def _network_topology_changed(self, trigger: str) -> None:
        """Let a running engine reallocate now instead of at its next network second."""
        engine = self._network_engine
        if engine is not None:
            engine.notify_topology_changed(trigger, replan_all=True)

    def configure_network_input(self, kind: str, *, seed: int | None = None, scenario: str | None = None,
                                path: str | Path | None = None) -> dict[str, Any]:
        """Select where the engine's INPUT comes from: ``simulated`` | ``none`` | ``measured``."""
        payload = {"kind": kind, "seed": seed, "scenario": scenario,
                   "path": None if path is None else str(path)}
        return self.network_engine.command("configure_network_input", payload)

    def network_live_state(self, *, metadata_only: bool = False) -> dict[str, Any]:
        """Last tick as full records (default) or an explicitly requested descriptor.

        Neither read advances the network. Scientific events remain in the
        complete state and journal when a caller requests only metadata.
        """
        if type(metadata_only) is not bool:
            raise ValueError("metadata_only must be a boolean")
        state = self.network_engine.live_state()
        return self._network_metadata(state) if metadata_only else state.to_dict()

    def network_step(self, *, metadata_only: bool = False) -> dict[str, Any]:
        """Advance one network second; full records remain the default return."""
        if type(metadata_only) is not bool:
            raise ValueError("metadata_only must be a boolean")
        state = self.network_engine.step()
        return self._network_metadata(state) if metadata_only else state.to_dict()

    def _network_metadata(self, state) -> dict[str, Any]:
        """Small publication descriptor; no scientific event/resource is discarded.

        The full state remains available through the default API. Console
        stepping discards the full return value, while capabilities only need
        source.kind. Both paths retain their existing authorization and audit.
        """
        technology = state.technologies
        rate = technology.get("clock_rate", 1.0)
        clock = technology.get("clock_s", 0.0)
        return {"schema": "orion.network-metadata.v1", "tick": state.tick,
                "engine_session_id": state.engine_session_id, "publication_revision": state.publication_revision,
                "at_utc": state.at_utc, "network_time_s": state.network_time_s,
                "topology_version": state.topology_version, "source": state.source.to_dict(),
                "nodes": len(state.nodes), "links": len(state.links), "satellites": len(state.satellites),
                "last_step_ms": self.network_engine.timings.get("last_step_ms"),
                "protocol_pending_events": technology.get("pending_events", 0),
                "protocol_clock_s": clock,
                "protocol_backlog_s": max(0.0, state.network_time_s * rate - clock),
                "pending_classical_hops": technology.get("global_operations", {}).get("pending_classical_hops", 0)}

    def network_command(self, verb: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """One engine command (topology verbs, pairs, input events, model archive, input source)."""
        if verb == "export_technology_events" and self.security.identity_profile == "service":
            raise ValueError("service clients must download evidence; server file destinations are not accepted")
        return self.network_engine.command(verb, payload)

    def fpga_device_state(self) -> dict[str, Any]:
        """Published FPGA lifecycle and hash-bound software acquisition feedback."""
        state = self.network_engine.live_state()
        return state.technologies.get("fpga_devices", {})

    def network_decisions(self, limit: int = 50) -> list[dict[str, Any]]:
        """Newest rows of the ``network_decisions`` journal table, hash-verified."""
        return self.network_engine.recent_decisions(limit)

    def recalibrate_node(self, request: Mapping[str, Any], *, tick_interval_s: float = 0.0) -> dict[str, Any]:
        """Full recalibration, executed by the engine and awaited here.

        Blocking: the engine is stepped until the procedure has recorded its report (a few
        network seconds), so the caller's tracked-command readback sees the persisted
        topology at ``expected_version + 1``. Call it off the UI thread; the phases are
        visible meanwhile through ``network_live_state()``. ``tick_interval_s`` paces those
        network seconds on the wall clock (the console passes its stepping period, so the
        network does not run ahead while a node is recalibrated).
        """

        clock = getattr(self, "_headless_clock", None)
        handoff = clock.hold("recalibration") if clock is not None else nullcontext()
        with handoff:
            from quantum_autopilot.security.service_boundary import admit_service_call
            admit_service_call(self, "recalibrate_node", (request,))
            result = self.network_engine.run_calibration(request, tick_interval_s=tick_interval_s)
        return {"status": "persisted", "physical_action_performed": False, "topology": result["topology"],
                "calibration": result["calibration"]}

    def run_workspace_path(self, request: Mapping[str, Any]) -> dict[str, Any]:
        from quantum_autopilot.control_plane.workspace_path import WorkspacePathRequest, run_workspace_path
        from quantum_autopilot.control_plane.temporal_path import TemporalPathRequest
        contract = TemporalPathRequest if isinstance(request, Mapping) and request.get("protocol") == "memory_single_swap_v1" else WorkspacePathRequest
        return run_workspace_path(self.topology_store, contract.from_dict(request))

    def workspace_path_result(self, run_id=None):
        from quantum_autopilot.control_plane.workspace_path import read_workspace_path
        return read_workspace_path(self.topology_store, run_id)

    def control_plane_events(self, limit: int = 20) -> dict[str, Any]:
        """Return recent immutable journal summaries, newest first."""

        return {
            "api_version": API_VERSION,
            "events": self.controller_runtime.journal.list_recent(limit=limit),
            "generated_at": _utc_now(),
        }

    def run_control_scenario(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Run a versioned scenario against the persistent simulated topology."""

        payload = _json_object(request, name="request")
        allowed = {
            "scenario_id",
            "topology_id",
            "preset",
            "reset_topology",
            "steps",
        }
        unknown = sorted(set(payload).difference(allowed))
        if unknown:
            raise ValueError(f"unsupported request fields: {unknown}")
        scenario_id = payload.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            raise ValueError("scenario_id is required for idempotency")
        topology_id = payload.get("topology_id", DEFAULT_TOPOLOGY_ID)
        if not isinstance(topology_id, str) or not topology_id.strip():
            raise ValueError("topology_id cannot be empty")
        preset = payload.get("preset", DEFAULT_SCENARIO_PRESET)
        try:
            if preset == DEFAULT_SCENARIO_PRESET:
                if "steps" in payload:
                    raise ValueError("preset scenarios do not accept custom steps")
                scenario = resilience_demo_scenario(
                    scenario_id.strip(),
                    topology_id=topology_id.strip(),
                    reset_topology=payload.get("reset_topology", True),
                )
            elif preset == "custom":
                scenario = ScenarioDefinition.from_dict(
                    {
                        "scenario_id": scenario_id.strip(),
                        "topology_id": topology_id.strip(),
                        "preset": "custom",
                        "reset_topology": payload.get("reset_topology", False),
                        "steps": payload.get("steps", ()),
                    }
                )
            else:
                raise ValueError(
                    f"preset must be one of {[DEFAULT_SCENARIO_PRESET, 'custom']}"
                )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"invalid control-plane scenario: {exc}") from exc
        result = self.scenario_runner.run(scenario)
        return {
            "api_version": API_VERSION,
            "generated_at": _utc_now(),
            **result,
        }

    def assess(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Validate an API request and return the complete audited record."""

        payload = _json_object(request, name="request")
        allowed = {"claim_id", "requested_use", "operating_point"}
        unknown = sorted(set(payload).difference(allowed))
        if unknown:
            raise ValueError(f"unsupported request fields: {unknown}")
        claim_id = payload.get("claim_id", "C-MEASURED")
        requested_use = payload.get("requested_use", "decision_support")
        if requested_use not in REQUESTED_USES:
            raise ValueError(f"requested_use must be one of {sorted(REQUESTED_USES)}")
        point_payload = payload.get("operating_point")
        point = (
            QKDOperatingPoint.from_dict(
                _json_object(point_payload, name="operating_point")
            )
            if point_payload is not None
            else None
        )
        workload = build_assurance_workload(
            str(claim_id),
            requested_use=str(requested_use),
            operating_point=point,
        )
        record = self.adapter.assess(workload)
        return {
            "api_version": API_VERSION,
            "generated_at": _utc_now(),
            "record": record.to_dict(),
        }

    def assess_published_cell(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Create a point from one hash-verified published cell and assess it.

        The browser sends condition coordinates only.  QBER, SKR and provenance
        are read here from the reviewed source artifact so the UI cannot attach
        a published hash to user-invented outcomes.
        """

        payload = _json_object(request, name="request")
        allowed = {"campaign_id", "voa_attenuation_db"}
        unknown = sorted(set(payload).difference(allowed))
        if unknown:
            raise ValueError(f"unsupported request fields: {unknown}")
        campaign_id = payload.get("campaign_id")
        if not isinstance(campaign_id, str) or not campaign_id.strip():
            raise ValueError("campaign_id cannot be empty")
        voa = payload.get("voa_attenuation_db")
        if isinstance(voa, bool) or not isinstance(voa, (int, float)):
            raise ValueError("voa_attenuation_db must be numeric")
        voa = float(voa)
        if not math.isfinite(voa) or voa < 0:
            raise ValueError("voa_attenuation_db must be finite and >= 0")

        summary = self.summary()
        grid = summary["operating_grid"]
        campaign = next(
            (
                item
                for item in grid["campaigns"]
                if item["campaign_id"] == campaign_id.strip()
            ),
            None,
        )
        if campaign is None:
            raise ValueError("campaign_id is outside the reviewed operating grid")
        if not any(math.isclose(voa, float(item), abs_tol=1e-9) for item in campaign["voa_attenuation_db"]):
            raise ValueError("voa_attenuation_db is outside the selected campaign")

        source = grid["source_artifact"]
        source_path = (self.repository_root / source["path"]).resolve()
        try:
            source_path.relative_to(self.repository_root)
        except ValueError as exc:
            raise ValueError("source artifact must remain inside repository_root") from exc

        selected_row: dict[str, str] | None = None
        with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                signal_present = row["external_signal_present"] == "1"
                row_power = (
                    float(row["external_signal_power_dbm"])
                    if row["external_signal_power_dbm"]
                    else None
                )
                if (
                    math.isclose(float(row["fiber_length_km"]), campaign["fiber_length_km"], abs_tol=1e-9)
                    and math.isclose(float(row["voa_attenuation_db"]), voa, abs_tol=1e-9)
                    and signal_present is campaign["external_signal_present"]
                    and (
                        row_power is None
                        if campaign["external_signal_power_dbm"] is None
                        else row_power is not None
                        and math.isclose(
                            row_power,
                            campaign["external_signal_power_dbm"],
                            abs_tol=1e-9,
                        )
                    )
                ):
                    selected_row = row
                    break
        if selected_row is None:
            raise ValueError("published source cell was not found")

        provenance = Provenance(
            evidence_kind=EvidenceKind.MEASURED,
            source=f"{source['path']}; doi:{grid['doi']}",
            dataset_hash=source["sha256"],
            artifact_hashes={
                "campaign_source": campaign["source_file_sha256"],
            },
            metadata={
                "doi": grid["doi"],
                "license": grid["license"],
                "aggregation": (
                    "mean of the published campaign/fibre/VOA/condition cell"
                ),
                "n_measurements": int(selected_row["n_measurements"]),
                "qber_reported_sd": float(selected_row["qber_reported_sd"]),
                "secure_key_rate_sd": float(selected_row["secure_key_rate_sd"]),
                "measurement_timestamp_status": "not_published",
            },
        )
        point = QKDOperatingPoint(
            dataset_id=grid["dataset_id"],
            campaign_id=campaign["campaign_id"],
            device_id="not_published",
            channel_id="not_published",
            external_signal_present=campaign["external_signal_present"],
            external_signal_power_dbm=campaign["external_signal_power_dbm"],
            fiber_length_km=campaign["fiber_length_km"],
            voa_attenuation_db=voa,
            qber_reported=float(selected_row["qber_reported"]),
            qber_unit_status=grid["qber_unit_status"],
            secure_key_rate_bps=float(selected_row["secure_key_rate_bps"]),
            observation_level="published_cell_mean",
            provenance=provenance,
            observation_time_utc=None,
            time_basis="not_published",
        )
        result = self.assess(
            {
                "claim_id": "C-MEASURED",
                "requested_use": "decision_support",
                "operating_point": point.to_dict(),
            }
        )
        result["source_cell"] = {
            "qber_reported": point.qber_reported,
            "qber_unit_status": point.qber_unit_status,
            "secure_key_rate_bps": point.secure_key_rate_bps,
            "n_measurements": int(selected_row["n_measurements"]),
            "qber_reported_sd": float(selected_row["qber_reported_sd"]),
            "secure_key_rate_sd": float(selected_row["secure_key_rate_sd"]),
        }
        return result

    def example_decision(self) -> dict[str, Any]:
        """Assess the shipped hash-bound IrelandQCI example point."""

        path = (self.repository_root / EXAMPLE_POINT_PATH).resolve()
        try:
            path.relative_to(self.repository_root)
        except ValueError as exc:
            raise ValueError("example point must remain inside repository_root") from exc
        point = json.loads(path.read_text(encoding="utf-8"))
        return self.assess(
            {
                "claim_id": "C-MEASURED",
                "requested_use": "decision_support",
                "operating_point": point,
            }
        )


def demo_journal_path(state_root: str | Path, *, preset: str = "salvador_metro") -> Path:
    """Journal of the demonstration: its own state, never the operator's."""

    if preset not in ("salvador_metro", "brazil-institutions-v1"):
        raise ValueError("unknown demonstration preset")
    folder = Path(state_root).resolve() / "demo"
    if preset != "salvador_metro":
        folder = folder / preset
    return folder / "sandbox_journal.sqlite"


def demo_service(repository_root: str | Path | None = None, state_root: str | Path | None = None, *,
                 seed: int = 20261004, scenario: str = "demo", preset: str = "salvador_metro", **kwargs: Any) -> AssuranceDashboardService:
    """An ``AssuranceDashboardService`` for DEMONSTRATION mode.

    Same service class and same ``NetworkEngine`` as normal operation; only two things
    differ: the state lives in ``<state_root>/demo/sandbox_journal.sqlite`` (initialised
    with the "Salvador metro" reference topology and demands P1-P4 when that journal is
    new) and the input comes from ``SimulatedInputSource``.

    ``preset="brazil-institutions-v1"`` selects the current console demonstration:
    seven campus references, hypothetical repeater alternatives and direct geodesic
    spans, ML permission initially off, and its
    own versioned journal. The default argument preserves the legacy API fixture.
    """

    from quantum_autopilot.network_engine import ensure_demo_workspace

    if "control_journal_path" in kwargs:
        raise ValueError("the demonstration journal path is derived from state_root")
    root = Path(repository_root).resolve() if repository_root is not None else Path(__file__).resolve().parents[3]
    state = Path(state_root) if state_root is not None else root / "outputs" / "control_plane"
    service = AssuranceDashboardService(repository_root, control_journal_path=demo_journal_path(state, preset=preset),
                                        network_seed=seed, **kwargs)
    service.is_demo = True
    if preset == "salvador_metro":
        ensure_demo_workspace(service.topology_store)
    else:
        from quantum_autopilot.network_engine.institutional import ensure_institutional_workspace
        from quantum_autopilot.network_engine.control_mode import read_mode
        from quantum_autopilot.control_plane.topology import DEFAULT_TOPOLOGY_ID
        ensure_institutional_workspace(service.topology_store)
        read_mode(service.control_journal_path, DEFAULT_TOPOLOGY_ID, default=False)
    service.configure_network_input("simulated", seed=seed, scenario=scenario)
    if preset == "brazil-institutions-v1":
        service.network_engine.seed_demo_fpga()
    return service


def _allowed_origin(origin: str | None) -> str | None:
    if not origin:
        return None
    parsed = urlparse(origin)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        return None
    if parsed.port not in {3000, 3001, 3002}:
        return None
    return origin


def make_handler(
    service: AssuranceDashboardService,
    *,
    dashboard_root: str | Path | None = None,
    http_policy=None,
    peer_authorizer=None,
) -> type[BaseHTTPRequestHandler]:
    """Create a request handler bound to one validated service instance.

    When ``dashboard_root`` is provided, the same loopback server also serves a
    pre-built static dashboard.  Keeping UI and API on one origin makes the
    Windows bundle self-contained without broadening the network boundary.
    """

    static_root = Path(dashboard_root).resolve() if dashboard_root is not None else None
    if static_root is not None and not static_root.is_dir():
        raise ValueError("dashboard_root must be an existing directory")

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "WDMQKDAssurance/1.3"

        def _headers(self) -> dict[str, str]:
            headers = {
                "Cache-Control": "no-store",
                "Content-Type": "application/json; charset=utf-8",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            }
            origin = _allowed_origin(self.headers.get("Origin"))
            if origin:
                headers["Access-Control-Allow-Origin"] = origin
                headers["Vary"] = "Origin"
            return headers

        def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            for name, value in self._headers().items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, request_path: str) -> bool:
            if static_root is None:
                return False
            decoded = unquote(request_path)
            if "\x00" in decoded:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_path"})
                return True
            relative = PurePosixPath(decoded.lstrip("/") or "index.html")
            if any(part in {"", ".", ".."} or part.startswith(".") for part in relative.parts):
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return True
            candidate = static_root.joinpath(*relative.parts).resolve()
            try:
                candidate.relative_to(static_root)
            except ValueError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return True
            if candidate.is_dir():
                candidate = (candidate / "index.html").resolve()
                try:
                    candidate.relative_to(static_root)
                except ValueError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return True
            if not candidate.is_file():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return True

            body = candidate.read_bytes()
            content_type, _ = mimetypes.guess_type(candidate.name)
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type",
                (content_type or "application/octet-stream")
                + ("; charset=utf-8" if candidate.suffix in {".html", ".css", ".js"} else ""),
            )
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Cache-Control",
                "no-cache" if candidate.suffix == ".html" else "public, max-age=31536000, immutable",
            )
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "font-src 'self' data:; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)
            return True

        def do_OPTIONS(self) -> None:  # noqa: N802
            origin = _allowed_origin(self.headers.get("Origin"))
            if not origin:
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin_not_allowed"})
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                if parsed.path == "/api/v1/health":
                    self._send_json(HTTPStatus.OK, service.health())
                elif parsed.path == "/api/v1/summary":
                    self._send_json(HTTPStatus.OK, service.summary())
                elif parsed.path == "/api/v1/example-decision":
                    self._send_json(HTTPStatus.OK, service.example_decision())
                elif parsed.path == "/api/v1/control-plane/status":
                    self._send_json(HTTPStatus.OK, service.control_plane_status())
                elif parsed.path == "/api/v1/control-plane/model":
                    self._send_json(HTTPStatus.OK, service.wdm_model_status())
                elif parsed.path == "/api/v1/evidence-lanes":
                    self._send_json(HTTPStatus.OK, service.evidence_lanes())
                elif parsed.path == "/api/v1/evidence-lanes/optical-health":
                    self._send_json(HTTPStatus.OK, service.optical_health())
                elif parsed.path == "/api/v1/control-plane/events":
                    query = parse_qs(parsed.query)
                    limit_text = query.get("limit", ["20"])[0]
                    try:
                        limit = int(limit_text)
                    except ValueError as exc:
                        raise ValueError("limit must be an integer") from exc
                    self._send_json(
                        HTTPStatus.OK,
                        service.control_plane_events(limit=limit),
                    )
                elif parsed.path == "/api/v1/control-plane/topology":
                    self._send_json(HTTPStatus.OK, service.control_plane_topology())
                elif parsed.path == "/api/v1/network/state":
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    if set(query) - {"metadata_only"} or any(len(rows) != 1 for rows in query.values()):
                        raise ValueError("invalid network state query")
                    choice = query.get("metadata_only", ["false"])[0]
                    if choice not in {"true", "false"}:
                        raise ValueError("metadata_only must be true or false")
                    self._send_json(HTTPStatus.OK, service.network_live_state(metadata_only=choice == "true"))
                elif parsed.path == "/api/v1/console/snapshot":
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    if set(query) - {"include_static"} or any(len(rows) != 1 for rows in query.values()):
                        raise ValueError("invalid console snapshot query")
                    choice = query.get("include_static", ["true"])[0]
                    if choice not in {"true", "false"}:
                        raise ValueError("include_static must be true or false")
                    self._send_json(HTTPStatus.OK, service.console_snapshot(include_static=choice == "true"))
                elif parsed.path == "/api/v1/network/decisions":
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    if set(query) - {"limit"} or any(len(rows) != 1 for rows in query.values()):
                        raise ValueError("invalid decisions query")
                    limit = int(query.get("limit", ["50"])[0])
                    if not 1 <= limit <= 200:
                        raise ValueError("invalid decisions limit")
                    self._send_json(HTTPStatus.OK, {"decisions": service.network_decisions(limit)})
                elif parsed.path == "/api/v1/laboratory":
                    self._send_json(HTTPStatus.OK, service.laboratory_state())
                elif parsed.path == "/api/v1/mixed-controller/status":
                    if parsed.query:
                        raise ValueError("mixed status has no query parameters")
                    self._send_json(HTTPStatus.OK, service.mixed_controller_status())
                elif self._send_static(parsed.path):
                    return
                else:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            except (PermissionError, TimeoutError):
                raise
            except EquipmentDenied:
                self._send_json(HTTPStatus.CONFLICT, {"error": "resource_authority_conflict_or_unavailable"})
            except ValueError:
                self._send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": "assurance_validation_failed"},
                )
            except Exception:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "internal_error"},
                )

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in {
                "/api/v1/assess",
                "/api/v1/assess-cell",
                "/api/v1/control-plane/cycles",
                "/api/v1/control-plane/scenarios",
                "/api/v1/control-plane/model-replay",
                "/api/v1/laboratory/runs",
                "/api/v1/laboratory/configuration",
                "/api/v1/network/step",
                "/api/v1/network/commands",
                "/api/v1/console/commands",
                "/api/v1/console/evidence",
                "/api/v1/mixed-controller/configure",
                "/api/v1/mixed-controller/observations",
                "/api/v1/mixed-controller/demands",
                "/api/v1/mixed-controller/release",
            }:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_content_length"})
                return
            if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": "request_size_out_of_bounds"},
                )
                return
            try:
                from quantum_autopilot.lab.service import unique_fields
                request = json.loads(self.rfile.read(content_length).decode("utf-8"), object_pairs_hook=unique_fields)
                if self.path == "/api/v1/assess-cell":
                    response = service.assess_published_cell(request)
                elif self.path == "/api/v1/mixed-controller/configure":
                    response = service.configure_mixed_controller(request)
                elif self.path == "/api/v1/mixed-controller/observations":
                    response = service.ingest_mixed_observations(request)
                elif self.path == "/api/v1/mixed-controller/demands":
                    response = service.reserve_mixed_route(request)
                elif self.path == "/api/v1/mixed-controller/release":
                    response = service.release_mixed_reservation(request)
                elif self.path == "/api/v1/control-plane/cycles":
                    response = service.run_control_cycle(request)
                elif self.path == "/api/v1/control-plane/scenarios":
                    response = service.run_control_scenario(request)
                elif self.path == "/api/v1/control-plane/model-replay":
                    response = service.run_wdm_model_replay(request)
                elif self.path == "/api/v1/laboratory/runs":
                    response = service.run_lab_experiment(request)
                elif self.path == "/api/v1/laboratory/configuration":
                    response = service.configure_laboratory(request)
                elif self.path == "/api/v1/network/step":
                    if not isinstance(request, dict) or set(request) - {"metadata_only"}:
                        raise ValueError("invalid step request")
                    response = service.network_step(metadata_only=request.get("metadata_only", False))
                elif self.path == "/api/v1/network/commands":
                    if (not isinstance(request, dict) or set(request) != {"verb", "payload"}
                            or not isinstance(request["verb"], str) or not isinstance(request["payload"], dict)):
                        raise ValueError("invalid network command")
                    if request["verb"] == "export_technology_events":
                        raise ValueError("HTTP clients cannot choose server file destinations")
                    response = service.network_command(request["verb"], request["payload"])
                elif self.path == "/api/v1/console/commands":
                    if (set(request) != {"verb", "payload"} or not isinstance(request["verb"], str)
                            or not isinstance(request["payload"], dict)):
                        raise ValueError("invalid console command")
                    response = service.console_command(request["verb"], request["payload"])
                elif self.path == "/api/v1/console/evidence":
                    if set(request) != {"scope"}:
                        raise ValueError("invalid evidence request")
                    response = service.console_evidence(request["scope"])
                else:
                    response = service.assess(request)
                self._send_json(HTTPStatus.OK, response)
            except DurableResultAuditPending as error:
                # The transaction already committed. Do not report an unknown
                # internal failure that could encourage a new reservation ID.
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, error.recovery())
            except EquipmentDenied:
                self._send_json(HTTPStatus.CONFLICT, {"error": "resource_authority_conflict_or_unavailable"})
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            except (PermissionError, TimeoutError):
                raise
            except ValueError:
                self._send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": "assurance_validation_failed"},
                )
            except Exception:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "internal_error"},
                )

        def log_message(self, format: str, *args: Any) -> None:
            return

    from quantum_autopilot.security.http_boundary import secure_handler
    return secure_handler(DashboardHandler, service, policy=http_policy,
                          peer_authorizer=peer_authorizer, max_request_bytes=MAX_REQUEST_BYTES)


def create_server(
    repository_root: str | Path | None = None,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    control_journal_path: str | Path | None = None,
    optical_wdm_csv: str | Path | None = None,
    optical_evidence_report_path: str | Path | None = None,
    expected_optical_dataset_sha256: str | None = None,
    wdm_model_bundle_path: str | Path | None = None,
    expected_wdm_bundle_sha256: str | None = None,
    wdm_replay_max_cycles: int = 100,
    dashboard_root: str | Path | None = None,
    security=None,
    security_root: str | Path | None = None,
    http_policy=None,
    ssl_context=None,
    peer_authorizer=None,
) -> ThreadingHTTPServer:
    """Create the loopback API/static-dashboard server without starting it."""

    from quantum_autopilot.security.http_boundary import BoundedHTTPServer, HTTPPolicy
    import ssl
    policy = http_policy or HTTPPolicy()
    if host not in {"127.0.0.1", "localhost"}:
        if (security is None or security.profile != "operational" or ssl_context is None
                or peer_authorizer is None or host not in policy.allowed_hosts):
            raise ValueError("non-loopback API requires operational mTLS and an explicit host allowlist")
    if ssl_context is not None:
        if (ssl_context.minimum_version != ssl.TLSVersion.TLSv1_3
                or ssl_context.maximum_version != ssl.TLSVersion.TLSv1_3
                or ssl_context.verify_mode != ssl.CERT_REQUIRED or peer_authorizer is None):
            raise ValueError("API mTLS requires TLS 1.3 and per-request certificate authorization")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer in [0, 65535]")
    service = AssuranceDashboardService(
        repository_root=repository_root,
        control_journal_path=control_journal_path,
        optical_wdm_csv=optical_wdm_csv,
        optical_evidence_report_path=optical_evidence_report_path,
        expected_optical_dataset_sha256=expected_optical_dataset_sha256,
        wdm_model_bundle_path=wdm_model_bundle_path,
        expected_wdm_bundle_sha256=expected_wdm_bundle_sha256,
        wdm_replay_max_cycles=wdm_replay_max_cycles,
        security=security,
        security_root=security_root,
    )
    server = BoundedHTTPServer(
        (host, port),
        make_handler(service, dashboard_root=dashboard_root, http_policy=policy, peer_authorizer=peer_authorizer),
        policy=policy,
    )
    server.service = service
    if ssl_context is not None:
        server.ssl_context = ssl_context
    return server


__all__ = [
    "API_VERSION",
    "AssuranceDashboardService",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "MAX_REQUEST_BYTES",
    "create_server",
    "demo_journal_path",
    "demo_service",
    "make_handler",
]
