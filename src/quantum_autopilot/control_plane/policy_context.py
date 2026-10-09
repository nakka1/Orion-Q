"""Read-only, session-bound Q05 input observations; never a selection gate."""

from contextlib import closing
from dataclasses import fields
from datetime import datetime, timezone
import json
import time

from quantum_autopilot.model_intake.policy_compatibility import inspect_policy_compatibility, TARGET_POLICY_ID
from quantum_autopilot.core.contracts import SCHEMA_VERSION as PROVENANCE_SCHEMA_VERSION
from .campaign_read_store import ReadOnlyCampaignStore
from .command_reconciliation import _read_topology
from .contracts import NetworkStateSnapshot
from .policy_decisions import _digest
from .policy_preparation import PolicyPreparationSession, _receipt, MAX_PROPOSAL_AGE_S
from .topology import DEFAULT_TOPOLOGY_ID, _sha256

MAX_INPUT_BYTES = 64 * 1024


def _now():
    return datetime.now(timezone.utc)


def _snapshot(payload):
    if type(payload) is not dict or set(payload) != {f.name for f in fields(NetworkStateSnapshot)}:
        raise ValueError("complete explicit NetworkStateSnapshot fields required")
    try:
        raw = json.dumps(payload, allow_nan=False, ensure_ascii=False)
        if len(raw.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError("snapshot exceeds 64 KiB")
        detached = json.loads(raw)
        provenance = detached.get("provenance")
        if (
            type(provenance) is not dict
            or type(provenance.get("metadata")) is not dict
            or type(provenance.get("artifact_hashes")) is not dict
            or type(provenance.get("source")) is not str
            or not 1 <= len(provenance["source"]) <= 200
            or not provenance["source"].isprintable()
            or not provenance["source"].strip()
            or provenance.get("schema_version") != PROVENANCE_SCHEMA_VERSION
        ):
            raise ValueError("explicit source, supported provenance schema and object metadata required")
        state = NetworkStateSnapshot.from_dict(detached)
    except (TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("bounded JSON snapshot required") from exc
    # Reject coercion/defaults inside the provenance as well as at the top level.
    if state.to_dict() != detached:
        raise ValueError("snapshot/provenance must be explicit and canonical")
    return state


class PolicyCompatibilitySession:
    """Independent observation session; does not change preparation or approvals."""

    def __init__(self, runtime):
        self.runtime = runtime
        self._context = PolicyPreparationSession(runtime)
        self._expected = None
        self._started = None

    def invalidate(self):
        with self._context._lock:
            self._expected = self._started = None
            self._context.invalidate()

    def inspect(self):
        with self._context._lock:
            started = time.monotonic()
            context = self._context._capture()
            context["runtime_instance_id"] = self.runtime.instance_id
            self._expected, self._started = _sha256(context), started
            return _receipt(
                dict(
                    schema="orion.policy_compatibility_context.v1",
                    observed_at_utc=_now().isoformat(),
                    context=context,
                    context_sha256=self._expected,
                    max_age_s=MAX_PROPOSAL_AGE_S,
                    selection_available=False,
                    activation_available=False,
                    commands_emitted=0,
                    physical_action_performed=False,
                )
            )

    def _check_reference(self, expected):
        if (
            self._expected is None
            or expected != self._expected
            or self._started is None
            or time.monotonic() - self._started >= MAX_PROPOSAL_AGE_S
        ):
            raise ValueError("context selection absent, changed or expired; inspect again")

    def observe(self, payload):
        if type(payload) is not dict or set(payload) != {
            "mode",
            "candidate_id",
            "expected_context_sha256",
            "snapshot",
        }:
            raise ValueError("exact contextual observation fields required")
        if payload["mode"] != "simulation":
            raise ValueError("only simulation observations supported")
        expected = _digest(payload["expected_context_sha256"])
        declaration = inspect_policy_compatibility(payload["candidate_id"])
        state = _snapshot(payload["snapshot"])
        with self._context._lock, self.runtime._cycle_lock:
            self._check_reference(expected)
            with closing(ReadOnlyCampaignStore(self._context._journal_path)._connect()) as reader:
                reader.execute("BEGIN")
                context = self._context._capture(reader)
                context["runtime_instance_id"] = self.runtime.instance_id
                if _sha256(context) != expected:
                    self._expected = None
                    raise ValueError("context changed; inspect again")
                topology = _read_topology(reader, DEFAULT_TOPOLOGY_ID)
                if _sha256(topology) != context["topology_sha256"]:
                    raise ValueError("inconsistent topology read snapshot")
            # A second read notices writes committed while the first SQLite snapshot was open.
            after = self._context._capture()
            after["runtime_instance_id"] = self.runtime.instance_id
            self._check_reference(expected)
            if after != context:
                self._expected = None
                raise ValueError("context changed during observation; inspect again")
            return _receipt(self._assess(state, context, topology, declaration))

    def _assess(self, state, context, topology, declaration):
        observed = _now()
        reasons = []

        def require(ok, code, detail):
            if not ok:
                reasons.append(dict(code=code, detail=detail))

        provenance = state.provenance.to_dict()
        meta = provenance["metadata"]
        require(
            provenance["evidence_kind"] == "simulated",
            "NON_SIMULATED_INPUT",
            "Only simulated provenance is admitted.",
        )
        require(
            meta.get("topology_id") == topology["topology_id"],
            "TOPOLOGY_ID_MISMATCH",
            "Provenance must name the observed topology.",
        )
        require(
            meta.get("evidence_lane") == "bell_fidelity_control"
            and meta.get("target_semantics") == "simulated_future_bell_fidelity"
            and meta.get("physical_calibration") is False,
            "INPUT_DOMAIN_UNSUPPORTED",
            "Require declared simulated Bell-fidelity semantics without physical calibration.",
        )
        stamp = datetime.fromisoformat(state.timestamp_utc.replace("Z", "+00:00"))
        provenance_stamp = datetime.fromisoformat(provenance["timestamp"].replace("Z", "+00:00"))
        age = (observed - stamp).total_seconds()
        require(
            stamp == provenance_stamp,
            "INPUT_CLOCK_MISMATCH",
            "Snapshot and provenance timestamps must agree.",
        )
        require(age >= 0, "FUTURE_INPUT", "Future timestamps are refused.")
        effective_age = max(age, 0.0) + state.telemetry_age_s
        require(
            effective_age <= context["supervisor"]["max_telemetry_age_s"],
            "STALE_INPUT",
            "Elapsed age plus declared telemetry age exceeds the supervisor limit.",
        )
        require(
            state.prediction_uncertainty <= context["supervisor"]["max_prediction_uncertainty"],
            "UNCERTAINTY_LIMIT",
            "Prediction uncertainty exceeds the current supervisor limit.",
        )
        require(not state.out_of_distribution, "DECLARED_OOD", "Caller declares out-of-distribution input.")
        require(
            state.integrity_verified,
            "DECLARED_INTEGRITY_MISSING",
            "Caller did not declare input integrity; this observation does not authenticate it.",
        )
        node = next((n for n in topology["nodes"] if n["node_id"] == state.node_id), None)
        require(
            node is not None and node["role"] == "repeater",
            "REPEATER_REQUIRED",
            "Input must target a persisted repeater node.",
        )
        if node is not None:
            for name in ("state_version", "memory_state", "available_pair_fidelities", "memory_ages_s"):
                require(
                    state.to_dict()[name] == node[name],
                    "NODE_" + name.upper() + "_MISMATCH",
                    "Input differs from persisted " + name + ".",
                )
            require(
                all(
                    k in state.telemetry and _sha256({"v": state.telemetry[k]}) == _sha256({"v": v})
                    for k, v in node["telemetry"].items()
                ),
                "PERSISTED_TELEMETRY_MISMATCH",
                "Input omits or changes persisted node telemetry.",
            )
        require(
            self.runtime._policy_activation_fault is None,
            "UNRESOLVED_ACTIVATION",
            "Resolve activation uncertainty before a supported-input observation.",
        )
        input_ok = not reasons
        optional = declaration["record"]["candidate_id"] != TARGET_POLICY_ID
        if optional:
            for criterion in declaration["record"]["blocking_criteria"]:
                reasons.append(
                    dict(
                        code="CANDIDATE_" + criterion.upper() + "_BLOCKED",
                        detail="Declared candidate blocker remains; no component input/action translation is implemented.",
                    )
                )
        return dict(
            schema="orion.policy_compatibility_observation.v1",
            observed_at_utc=observed.isoformat(),
            status="BLOCKED" if reasons else "OBSERVED_SUPPORTED_INPUT",
            assessment_scope="CALLER_SIMULATED_INPUT_AGAINST_OBSERVED_CONTEXT",
            context=context,
            context_sha256=_sha256(context),
            declaration=declaration,
            input=state.to_dict(),
            input_sha256=_sha256(state.to_dict()),
            input_origin="caller_supplied",
            input_source_authenticated=False,
            target_input_checks_passed=input_ok,
            candidate_domain_validated=not optional and input_ok,
            effective_telemetry_age_s=effective_age,
            blocking_reasons=reasons,
            action_semantics=declaration["record"]["declared"]["action_semantics"],
            per_action_safety_assessed=False,
            model_identity_observed=False,
            selection_available=False,
            activation_available=False,
            preparation_available=False,
            operator_approval_recorded=False,
            decisions_executed=0,
            commands_emitted=0,
            physical_action_performed=False,
        )
