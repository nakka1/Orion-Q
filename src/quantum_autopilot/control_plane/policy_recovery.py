"""Explicit simulation rollback preparation and recovery of an uncertain commit."""

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from uuid import uuid4

from . import policy_activation as activation
from . import policy_preparation as preparation
from .command_reconciliation import _read_topology
from .policy_approval import PolicyApprovalRequest, APPROVAL_CONFIRMATION
from .policy_preparation import _describe, _receipt, MAX_PROPOSAL_AGE_S
from .topology import DEFAULT_TOPOLOGY_ID, _sha256

RECOVERY_CONFIRMATION = "KEEP_RESTORED_SIMULATION_POLICY"


@dataclass(frozen=True)
class PolicyRecoveryRequest:
    mode: str
    fault_id: str
    expected_recovery_sha256: str
    operator_label: str
    confirmation: str

    def __post_init__(self):
        if self.mode != "simulation" or self.confirmation != RECOVERY_CONFIRMATION:
            raise ValueError("explicit confirmation to retain the restored simulation policy required")
        # Reuse strict UUID/hash/operator label validation, without recording approval.
        PolicyApprovalRequest(
            "simulation",
            self.fault_id,
            self.expected_recovery_sha256,
            self.expected_recovery_sha256,
            self.operator_label,
            APPROVAL_CONFIRMATION,
        )

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict) or set(payload) != {
            "mode",
            "fault_id",
            "expected_recovery_sha256",
            "operator_label",
            "confirmation",
        }:
            raise ValueError("invalid policy recovery fields")
        return cls(**payload)


def mark_uncertain(runtime, failed_receipt, identities, path):
    runtime._policy_activation_fault = (
        "commit outcome/expiry unresolved; live policy restored; explicit recovery required"
    )
    with runtime._policy_recovery_lock:
        runtime._policy_recovery_selection = None
    runtime._policy_activation_recovery = dict(
        fault_id=str(uuid4()),
        failed_receipt=json.loads(json.dumps(failed_receipt)),
        identities=identities,
        journal_path=path,
    )


def prepare_rollback(session, activation_id, expected_sha256):
    runtime = session.runtime
    with session._lock, runtime._cycle_lock:
        if session._closed or runtime._policy_activation_fault is not None:
            raise ValueError("closed session or unresolved runtime; recovery required before rollback")
        marker = runtime._policy_activation_marker
        if (
            marker is None
            or marker["activation_id"] != activation_id
            or marker["record_sha256"] != expected_sha256
        ):
            raise ValueError("rollback target is not the current local activation")
        with closing(activation.ReadOnlyCampaignStore(runtime.journal.path)._connect()) as connection:
            receipt = activation._verify_marker(runtime, connection)
        record = receipt["record"]
        target = record.get("rollback_policy", record["previous_policy"])
        if target is None:
            raise ValueError("no previous activated policy is available for rollback")
        context = session.inspect()["record"]
        # This increment supports the existing threshold-only transition contract.
        if dict(context["context"]["baseline"], threshold=target["threshold"]) != target:
            raise ValueError("rollback would change unsupported parameters")
        proposal = session.prepare(
            dict(
                mode="simulation",
                policy_id=preparation.POLICY_ID,
                threshold=target["threshold"],
                optional_model_id=None,
                expected_context_sha256=context["context_sha256"],
            )
        )
        proposal["record"]["rollback_of"] = dict(
            activation_id=activation_id, record_sha256=expected_sha256, target_policy_sha256=_sha256(target)
        )
        proposal = _receipt(proposal["record"])
        session._proposal_json = json.dumps(proposal, allow_nan=False)
        return proposal


def _capture(runtime, connection):
    fault = runtime._policy_activation_recovery
    if runtime._policy_activation_fault is None or fault is None:
        raise ValueError("no recoverable local commit uncertainty")
    if runtime.journal.path.resolve(strict=True) != fault["journal_path"]:
        raise ValueError("recovery journal changed")
    policy, supervisor, identities = _describe(runtime)
    if any(a is not b for a, b in zip(identities, fault["identities"])):
        raise ValueError("restored runtime objects changed; refusing recovery")
    failed = fault["failed_receipt"]
    if policy != failed["record"]["previous_policy"] or supervisor != failed["record"]["supervisor"]:
        raise ValueError("restored configuration changed; refusing recovery")
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='policy_activations'"
    ).fetchone()
    exists = (
        table is not None
        and connection.execute(
            "SELECT 1 FROM policy_activations WHERE activation_id=?", (failed["record"]["activation_id"],)
        ).fetchone()
        is not None
    )
    if exists:
        activation._load(connection, failed["record"]["activation_id"], failed["record_sha256"])
    previous = (
        activation._verify_marker(runtime, connection)
        if runtime._policy_activation_marker is not None
        else None
    )
    topology = _read_topology(connection, DEFAULT_TOPOLOGY_ID)
    if topology is None:
        raise ValueError("persisted topology required for recovery review")
    after, after_supervisor, after_identities = _describe(runtime)
    if (
        after != policy
        or after_supervisor != supervisor
        or any(a is not b for a, b in zip(identities, after_identities))
    ):
        raise ValueError("restored configuration changed during recovery observation")
    return dict(
        runtime_instance_id=runtime.instance_id,
        fault_id=fault["fault_id"],
        failed_activation_id=failed["record"]["activation_id"],
        failed_record_sha256=failed["record_sha256"],
        failed_record_observation="present_and_verified" if exists else "absent_at_observation",
        retained_policy=policy,
        retained_policy_sha256=_sha256(policy),
        supervisor=supervisor,
        previous_activation=None
        if previous is None
        else dict(activation_id=previous["record"]["activation_id"], record_sha256=previous["record_sha256"]),
        topology_version=topology["topology_version"],
        topology_sha256=_sha256(topology),
        plan="retain_restored_policy_without_retrying_failed_candidate",
    )


def inspect_recovery(runtime):
    with runtime._cycle_lock:
        # A failed inspection invalidates any older review selection too.
        with runtime._policy_recovery_lock:
            runtime._policy_recovery_selection = None
        started = preparation.time.monotonic()
        with closing(activation.ReadOnlyCampaignStore(runtime.journal.path)._connect()) as connection:
            connection.execute("BEGIN")
            context = _capture(runtime, connection)
        receipt = _receipt(
            dict(
                schema="orion.policy_recovery_context.v1",
                review_id=str(uuid4()),
                context=context,
                observed_at_utc=datetime.now(timezone.utc).isoformat(),
                max_age_s=MAX_PROPOSAL_AGE_S,
                physical_action_performed=False,
                recovery_performed=False,
            )
        )
        _unexpired(started)
        selected = (json.dumps(receipt, allow_nan=False), started)
        with runtime._policy_recovery_lock:
            runtime._policy_recovery_selection = selected
        return receipt


def _unexpired(started):
    if not 0 <= preparation.time.monotonic() - started < MAX_PROPOSAL_AGE_S:
        raise ValueError("recovery review expired")


def recover(runtime, payload, *, validate_source=None):
    request = PolicyRecoveryRequest.from_dict(payload)
    with runtime._cycle_lock:
        if validate_source is not None:
            validate_source()
        with runtime._policy_recovery_lock:
            selection = runtime._policy_recovery_selection
        if selection is None:
            raise ValueError("inspect the current recovery context first")
        selected = json.loads(selection[0])
        if (
            selected["record_sha256"] != request.expected_recovery_sha256
            or selected["record"]["context"]["fault_id"] != request.fault_id
        ):
            raise ValueError("recovery review or fault is not the current local selection")
        with runtime._policy_recovery_lock:
            if runtime._policy_recovery_selection is not selection:
                raise ValueError("recovery selection invalidated before consumption")
            runtime._policy_recovery_selection = None
        fault = runtime._policy_activation_recovery
        old_marker = runtime._policy_activation_marker
        commit_attempted = False
        _unexpired(selection[1])
        with closing(activation._connect(runtime.journal.path.resolve(strict=True))) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                context = _capture(runtime, connection)
                if _sha256(context) != _sha256(selected["record"]["context"]):
                    raise ValueError("recovery context changed; inspect again")
                previous = activation._verify_marker(runtime, connection) if old_marker is not None else None
                rollback_policy = (
                    None
                    if previous is None
                    else previous["record"].get("rollback_policy", previous["record"]["previous_policy"])
                )
                activation._schema(connection)
                revision = connection.execute(
                    "SELECT COALESCE(MAX(revision),0)+1 FROM policy_activations"
                ).fetchone()[0]
                record = dict(
                    schema="orion.policy_recovery.v1",
                    activation_id=str(uuid4()),
                    revision=revision,
                    transition_kind="recovery_checkpoint",
                    status="COMMITTED",
                    runtime_instance_id=runtime.instance_id,
                    recorded_at_utc=datetime.now(timezone.utc).isoformat(),
                    mode="simulation",
                    scope="this_runtime_instance_only_no_restart_restore",
                    recovery_request=asdict(request),
                    operator_identity="caller_declared_not_authenticated",
                    reviewed_context=selected,
                    failed_attempt=fault["failed_receipt"],
                    validated_context=context,
                    previous_policy=context["retained_policy"],
                    active_policy=context["retained_policy"],
                    active_policy_sha256=context["retained_policy_sha256"],
                    supervisor=context["supervisor"],
                    previous_activation_id=None if old_marker is None else old_marker["activation_id"],
                    rollback_policy=rollback_policy,
                    commands_emitted=0,
                    optional_models_activated=False,
                    physical_action_performed=False,
                    policy_replaced=False,
                    interpretation="Explicit recovery checkpoint retaining the restored policy; absence does not prove the failed commit never happened.",
                )
                receipt = _receipt(record)
                raw = json.dumps(record, ensure_ascii=False, allow_nan=False)
                if len(raw.encode("utf-8")) > activation.MAX_RECORD_BYTES:
                    raise ValueError("recovery record exceeds 256 KiB")
                connection.execute(
                    "INSERT INTO policy_activations VALUES (?,?,?,?,?,?)",
                    (
                        revision,
                        record["activation_id"],
                        "recovery:" + request.fault_id,
                        runtime.instance_id,
                        receipt["record_sha256"],
                        raw,
                    ),
                )
                _, _, identities = _describe(runtime)
                _unexpired(selection[1])
                runtime._policy_activation_marker = dict(
                    activation_id=record["activation_id"],
                    record_sha256=receipt["record_sha256"],
                    revision=revision,
                    identities=identities,
                    journal_path=fault["journal_path"],
                )
                # Native cancellation is effective until commit begins.
                if validate_source is not None:
                    validate_source()
                commit_attempted = True
                activation._commit(connection)
                _unexpired(selection[1])
                runtime._policy_activation_fault = runtime._policy_activation_recovery = None
                return receipt
            except BaseException:
                runtime._policy_activation_marker = old_marker
                if commit_attempted:
                    mark_uncertain(runtime, receipt, fault["identities"], fault["journal_path"])
                connection.rollback()
                raise
