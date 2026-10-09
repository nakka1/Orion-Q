"""Declared Q05 compatibility only; no artifacts, runtime, inference or authority.

Keep this module standard-library only: the native catalog must remain usable
without NumPy, Torch, reviewed archives or an initialized controller.
"""

from datetime import datetime, timezone
import hashlib
import json

from .catalog import model_options

TARGET_POLICY_ID = "repeater.utility.v1"
TARGET_ACTIONS = ("HALT", "WAIT", "PURIFY", "SWAP")
RULESET = "orion.policy-compatibility-rules.v1"


def _descriptor(candidate_id):
    if candidate_id == TARGET_POLICY_ID:
        return dict(
            kind="existing_controller",
            input_contract="NetworkStateSnapshot/1.1.0",
            input_domain="Simulated repeater state with declared fidelity, uncertainty and resources.",
            output_kind="control_action",
            declared_actions=list(TARGET_ACTIONS),
            action_semantics="Repeater control intent, subject to the independent safety supervisor.",
            adapter_status="threshold_workflow_only",
            archive_kind=None,
        )
    option = next((o for o in model_options() if o.model_id == candidate_id), None)
    if option is None:
        raise ValueError("unknown candidate; no inferred compatibility")
    specs = {
        "qkd.detector.boosting": (
            "orion.optional-detector-input.v1",
            "Simulated decoy-state session log (17 public columns), 1 s periods, 20-70 km, trained transmitter "
            "settings; reviewed safe export, the pickle is hashed and never loaded.",
            "attack_classification",
            [],
            "Classification is not a repeater control intent.",
        ),
        "qkd.keyrate.actor": (
            "orion.optional-keyrate-input.v1",
            "Simulated decoy-state BB84 transmitter; declared 25-80 km domain, explicit seed.",
            "transmitter_parameter_recommendation",
            [],
            "Nine bounded transmitter parameter changes; no repeater action mapping.",
        ),
        "qkd.sync.ridge.1s.h2s": (
            "orion.optional-sync-input.v1",
            "Simulated arrival offset; eight samples, 1 s cadence, 2 s horizon.",
            "arrival_offset_forecast",
            [],
            "Offset forecast is neither Bell fidelity nor a control intent.",
        ),
    }
    for variant in ("nominal", "prazo_longo"):
        specs[f"qnet.routing.{variant}"] = (
            "orion.optional-routing-input.v1",
            "Declared simulator features: state11/action23, explicit seed and instantaneous resources.",
            "candidate_action_scoring",
            ["WAIT", "GENERATE", "PURIFY", "SWAP", "DELIVER", "DISCARD"],
            "Named candidate resources and action scores are not executable repeater intents; "
            "even shared action names need an explicit translation.",
        )
    for point in ("calibrado", "desenvolvimento"):
        for architecture in ("ridge", "mlp"):
            specs[f"qnet.compensation.{point}.{architecture}"] = (
                "orion.optional-compensation-input.v1",
                f"Declared {point} SO(3) regime; 2 ms cadence, 40 ms horizon, 15 warm-up steps.",
                "rotation_forecast",
                [],
                "Rotation prediction does not command a polarisation controller.",
            )
    for variant in ("persistence", "ridge"):
        specs[f"qnet.allocator.{variant}"] = (
            "orion.optional-allocation-input.v1",
            "Fixed synthetic 11-link single-photon BB84 plant; 5 s steps, 30 s queue, 150 s horizon.",
            "admission_recommendation",
            [],
            "Admission fractions and key-budget forecasts do not update the queue or map to repeater intents.",
        )
    if candidate_id not in specs:
        raise ValueError("candidate has no reviewed compatibility declaration")
    contract, domain, output, actions, semantics = specs[candidate_id]
    return dict(
        kind="optional_component",
        input_contract=contract,
        input_domain=domain,
        output_kind=output,
        declared_actions=actions,
        action_semantics=semantics,
        adapter_status=option.status,
        archive_kind=option.archive_kind,
    )


def _receipt(record):
    raw = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return dict(record=json.loads(raw), record_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest())


def inspect_policy_compatibility(candidate_id, *, target_policy_id=TARGET_POLICY_ID, mode="simulation"):
    """Describe known contracts, never accept caller assertions as compatibility evidence."""
    if type(candidate_id) is not str or not candidate_id or len(candidate_id) > 160:
        raise ValueError("known candidate ID required")
    if target_policy_id != TARGET_POLICY_ID or mode != "simulation":
        raise ValueError("only the existing repeater target in simulation is described")
    declared = _descriptor(candidate_id)
    optional = declared["kind"] == "optional_component"
    checks = [
        dict(
            criterion="identity",
            status="NOT_OBSERVED",
            reason=(
                "Recheck pinned reviewed archive bytes and exact variant at future use; "
                "catalog presence or a prior ZIP check proves neither installation nor compatibility."
                if optional
                else "Observe exact controller, engine, supervisor and configuration in the current runtime."
            ),
        ),
        dict(
            criterion="input_domain",
            status="BLOCKED" if optional else "CONTEXT_REQUIRED",
            reason=(
                "Component-specific inputs and domain have no Q05 translation to this target. "
                "Offline or artificial shadow acceptance does not validate current telemetry."
                if optional
                else "Current topology, provenance, schema and parameters require fresh preparation/review."
            ),
        ),
        dict(
            criterion="action_mapping",
            status="BLOCKED" if optional else "DECLARED_SUPPORTED",
            reason=declared["action_semantics"],
        ),
        dict(
            criterion="lifecycle",
            status="BLOCKED" if optional else "THRESHOLD_ONLY",
            reason=(
                "No Q05 adapter for preparation, approval, activation/readback, rollback and recovery. "
                "The independent supervisor and audit must cover the translated actions."
                if optional
                else "Only threshold changes have the existing audited workflow; this report does not prepare one."
            ),
        ),
        dict(
            criterion="scientific_evidence",
            status="NOT_ASSESSED",
            reason="No ranking or scientific validation is inferred from contract shape, parity or the SO(3) campaign.",
        ),
        dict(
            criterion="operator_approval",
            status="NOT_REQUESTED",
            reason="Fresh context-bound review and explicit confirmation are required in a supported workflow; "
            "this receipt cannot authorize selection or activation.",
        ),
    ]
    if declared["adapter_status"] == "blocked_pickle":
        checks.insert(
            1,
            dict(
                criterion="safe_export",
                status="BLOCKED",
                reason="Reviewed safe detector export is missing; pickle deserialization remains blocked.",
            ),
        )
    record = dict(
        schema="orion.policy_compatibility.v1",
        ruleset=RULESET,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        assessment_scope="DECLARED_CONTRACT_ONLY",
        candidate_id=candidate_id,
        target=dict(
            policy_id=target_policy_id,
            mode=mode,
            input_contract="NetworkStateSnapshot/1.1.0",
            actions=list(TARGET_ACTIONS),
        ),
        declared=declared,
        criteria=checks,
        status="BLOCKED" if optional else "CONTEXT_REQUIRED",
        blocking_criteria=[c["criterion"] for c in checks if c["status"] == "BLOCKED"],
        runtime_observed=False,
        artifact_identity_observed=False,
        domain_validated=False,
        selection_available=False,
        activation_available=False,
        operator_approval_recorded=False,
        models_loaded=False,
        commands_emitted=0,
        physical_action_performed=False,
    )
    return _receipt(record)


def policy_compatibility_catalog():
    """Return detached declarations for the existing target and every catalog option."""
    return _receipt(
        dict(
            schema="orion.policy_compatibility_catalog.v1",
            ruleset=RULESET,
            assessment_scope="DECLARED_CONTRACT_ONLY",
            candidates=[
                inspect_policy_compatibility(candidate)
                for candidate in (TARGET_POLICY_ID, *(o.model_id for o in model_options()))
            ],
            selection_available=False,
            activation_available=False,
            commands_emitted=0,
            physical_action_performed=False,
        )
    )


def compatibility_notes(candidate_id):
    """Plain native-catalog explanation, independent of package checks and sources."""
    record = inspect_policy_compatibility(candidate_id)["record"]
    declared = record["declared"]
    checks = "\n".join(
        f"{item['criterion']}: {item['status']} — {item['reason']}" for item in record["criteria"]
    )
    return (
        f"Q05 COMPATIBILITY: {record['status']}\n"
        f"Target: {TARGET_POLICY_ID} / simulation\n"
        "Declared contracts only; runtime and artifacts NOT OBSERVED.\n"
        f"Input: {declared['input_contract'] or 'NO SAFE CONTRACT'}\n"
        f"Domain: {declared['input_domain']}\nOutput: {declared['output_kind']}\n"
        f"Declared action names: {', '.join(declared['declared_actions']) or 'none (component output)'}\n"
        f"{checks}\nSelection / activation: UNAVAILABLE. Optional models remain OFF."
    )
