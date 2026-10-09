"""Q05 lifecycle for the polarisation control target: prepare, review, approve, activate, roll back.

The repeater threshold has its Q05 flow bound to the console runtime. This is the
same discipline applied to ``polarisation.compensation.v1``, so that a learned
compensator enters the simulated loop only through an explicit, audited and
reversible activation:

* **prepare** binds a proposal to the slot's current configuration, the
  checkpoint's contract decision and weight hashes, and a closed-loop campaign
  summary whose own hash the operator supplies; it expires after 300 s;
* **review** re-reads all of that and confirms nothing changed;
* **approve** records an explicit operator declaration (not authentication);
* **activate** re-validates under the slot lock inside a SQLite ``BEGIN
  IMMEDIATE`` transaction, builds the controller, checks it reproduces the
  approved configuration, writes the record and only then swaps it in; any
  failure restores the previous controller;
* **status** distinguishes the committed record from the live configuration;
* **rollback** prepares the previous configuration, which again needs review,
  approval and activation.

Every loop run is journalled with the activation it ran under. Models are OFF
until activated; the default is the classical alpha-beta baseline. Simulation
only: no record here can authorise physical actuation.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock
import time
from uuid import UUID, uuid4

from . import polarisation_loop as loop
from .policy_approval import APPROVAL_CONFIRMATION, PolicyApprovalRequest
from .topology import _sha256

POLICY_ID = loop.TARGET_ID
ACTIVATION_CONFIRMATION = "ACTIVATE_APPROVED_SIMULATION_POLICY"  # same declaration as the repeater Q05 flow
MAX_PROPOSAL_AGE_S = 300
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_RECORD_BYTES = 256 * 1024
EVIDENCE_SCHEMA = "orion.polarisation-closed-loop-results.v1"
DEFAULT_ALPHA_BETA = {"alpha": 0.01, "beta": 0.0}


def _receipt(record):
    detached = json.loads(json.dumps(record, allow_nan=False))
    return {"record": detached, "record_sha256": _sha256(detached)}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(value, name):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise ValueError(f"exact {name} SHA-256 required")
    return value


def _uuid(value, name):
    try:
        canonical = str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f"canonical {name} UUID required") from None
    if canonical != value:
        raise ValueError(f"canonical {name} UUID required")
    return value


def _strict(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("non-finite JSON number")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=constant)


def read_evidence(path, expected_sha256):
    """The campaign summary, exactly as the operator selected it (hash of the file bytes)."""
    _digest(expected_sha256, "evidence")
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_EVIDENCE_BYTES + 1)
    if len(raw) > MAX_EVIDENCE_BYTES:
        raise ValueError("evidence exceeds 1 MiB")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("evidence file differs from the selected hash")
    return _strict(raw.decode("utf-8"))


def evidence_supports(summary, model_id, checkpoint_sha256):
    """Accept only a complete v1 campaign in which *these* weights beat every baseline after Holm."""
    from .polarisation_campaign import PROTOCOLS

    expected = PROTOCOLS.get(model_id.split(".")[2]) if model_id.count(".") == 3 else None
    if not isinstance(summary, dict) or summary.get("schema") != EVIDENCE_SCHEMA or summary.get("status") != "complete":
        raise ValueError("evidence is not a complete closed-loop campaign summary")
    if summary.get("physical_action_performed") is not False or summary.get("policy_activated") is not False:
        raise ValueError("evidence must come from a simulation-only campaign")
    if expected is None or (json.dumps(summary.get("manifest", {}).get("protocol"), sort_keys=True)
                            != json.dumps(expected, sort_keys=True)):
        raise ValueError("evidence protocol differs from frozen v1 for this operating point")
    contract = [row for row in summary.get("contracts", []) if row.get("model_id") == model_id]
    if len(contract) != 1 or contract[0].get("status") != "ADMITTED":
        raise ValueError("evidence does not admit this checkpoint")
    if contract[0].get("checkpoint_sha256") != checkpoint_sha256:
        raise ValueError("evidence was produced with different weights")
    if not any(row.get("model_id") == model_id and row.get("passed") is True for row in summary.get("reproduction", [])):
        raise ValueError("evidence lacks a passed reproduction gate for this checkpoint")
    rows = [row for row in summary.get("comparisons", []) if row.get("model_id") == model_id]
    if ({row.get("baseline") for row in rows} != set(expected["baselines"]) or len(rows) != len(expected["baselines"])
            or not all(row.get("significant") is True and row.get("baseline_to_model_qber_ratio", 0) > 1 for row in rows)):
        raise ValueError("evidence does not show this checkpoint beating every baseline")
    tuned = summary.get("alpha_beta", {})
    gains = {"alpha": tuned.get("alpha"), "beta": tuned.get("beta")}
    loop.AlphaBeta(gains["alpha"], gains["beta"])
    return gains, [{key: row[key] for key in ("baseline", "baseline_to_model_qber_ratio", "wins", "holm_p_value")}
                   for row in rows]


class PolarisationControlSlot:
    """The one place the simulated polarisation loop takes its controller from.

    Process-bound: a restart starts from the classical baseline and does not
    restore historical activations. Journal rows are evidence, not state.
    """

    def __init__(self, journal_path, *, archive_path=None, operating_point="calibrado"):
        self.instance_id = str(uuid4())
        self.journal_path = Path(journal_path)
        self.archive_path = archive_path
        self.operating_point = operating_point
        self._lock = RLock()
        self._archive = None
        self.policy = self._baseline_policy(DEFAULT_ALPHA_BETA)
        self.controller, self.fallback = self._build(self.policy)
        self._marker = None
        self._proposal = self._review = self._approval = None
        self._started = None
        with closing(self._connect()) as connection:
            self._schema(connection)
            connection.commit()

    # -- configuration ------------------------------------------------------

    def select_archive(self, archive_path):
        """Point the slot at another reviewed package; only while the classical baseline is live."""
        with self._lock:
            if self.policy["controller_id"] != "baseline.alpha_beta":
                raise ValueError("roll back to the alpha-beta baseline before changing the weights package")
            self.archive_path, self._archive = archive_path, None
            self._proposal = self._review = self._approval = None

    def discard_selection(self):
        """Forget the captured proposal, review and approval (a closed or re-pointed panel)."""
        with self._lock:
            self._proposal = self._review = self._approval = None

    def _baseline_policy(self, gains):
        return {"policy_id": POLICY_ID, "operating_point": self.operating_point, "controller_id": "baseline.alpha_beta",
                "alpha_beta": dict(gains), "supervisor": asdict(loop.PolarisationSupervisorConfig()),
                "checkpoint_sha256": None, "evidence_sha256": None}

    def _reviewed_archive(self):
        from quantum_autopilot.model_intake.artifacts import ReviewedArchive

        if self.archive_path is None:
            raise ValueError("explicit reviewed qnet archive required for a learned controller")
        if self._archive is None:
            self._archive = ReviewedArchive(self.archive_path, "qnet")
        return self._archive

    def _build(self, policy):
        gains = policy["alpha_beta"]
        fallback = loop.AlphaBeta(gains["alpha"], gains["beta"])
        if policy["controller_id"] == "baseline.alpha_beta":
            return loop.AlphaBeta(gains["alpha"], gains["beta"]), None
        archive = self._reviewed_archive()
        decision = loop.check_checkpoint(policy["controller_id"], archive, policy["operating_point"])
        if decision.status != "ADMITTED" or decision.checkpoint_sha256 != policy["checkpoint_sha256"]:
            raise ValueError("checkpoint no longer matches the approved contract and weights")
        return loop.LearnedCheckpoint(policy["controller_id"], archive), fallback

    def _describe(self, controller, fallback):
        """Configuration read back from the live objects, not from the record."""
        policy = dict(self.policy)
        if isinstance(controller, loop.AlphaBeta) and fallback is None:
            policy.update(controller_id="baseline.alpha_beta",
                          alpha_beta={"alpha": controller.alpha, "beta": controller.beta})
        elif isinstance(controller, loop.LearnedCheckpoint) and isinstance(fallback, loop.AlphaBeta):
            policy.update(controller_id=controller.controller_id,
                          alpha_beta={"alpha": fallback.alpha, "beta": fallback.beta})
        else:
            raise ValueError("unsupported live controller")
        return policy

    # -- journal ------------------------------------------------------------

    def _connect(self):
        connection = sqlite3.connect(str(self.journal_path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _schema(connection):
        connection.execute("""CREATE TABLE IF NOT EXISTS polarisation_activations (
            revision INTEGER PRIMARY KEY, activation_id TEXT NOT NULL UNIQUE, approval_id TEXT NOT NULL UNIQUE,
            instance_id TEXT NOT NULL, record_sha256 TEXT NOT NULL, record_json TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS polarisation_cycles (
            cycle INTEGER PRIMARY KEY, instance_id TEXT NOT NULL, activation_id TEXT,
            record_sha256 TEXT NOT NULL, record_json TEXT NOT NULL)""")

    # -- lifecycle ----------------------------------------------------------

    def _unexpired(self):
        if self._started is None or not 0 <= time.monotonic() - self._started < MAX_PROPOSAL_AGE_S:
            raise ValueError("proposal expired; prepare again")

    def _context(self, target, evidence_path, evidence_sha256):
        """Everything a proposal depends on, recomputed from sources each time it is checked."""
        current = self._describe(self.controller, self.fallback)
        if current != self.policy:
            raise ValueError("live controller differs from the recorded configuration")
        if target == "baseline.alpha_beta":
            return current, None
        if not isinstance(target, str) or not target.startswith("qnet.compensation."):
            raise ValueError("only the alpha-beta baseline or a reviewed compensation checkpoint can be proposed")
        decision = loop.check_checkpoint(target, self._reviewed_archive(), self.operating_point)
        if decision.status != "ADMITTED":
            raise ValueError("contract refuses this checkpoint on this plant: " + ", ".join(decision.reasons))
        if evidence_path is None:
            raise ValueError("a learned controller needs an explicit closed-loop evidence file")
        summary = read_evidence(evidence_path, evidence_sha256)
        gains, comparisons = evidence_supports(summary, target, decision.checkpoint_sha256)
        return current, {"decision": asdict(decision), "alpha_beta": gains, "comparisons": comparisons,
                         "evidence_sha256": evidence_sha256}

    def prepare(self, controller_id, *, evidence_path=None, evidence_sha256=None, rollback_of=None):
        with self._lock:
            self._proposal = self._review = self._approval = None
            current, support = self._context(controller_id, evidence_path, evidence_sha256)
            if support is None:
                gains = (rollback_of or {}).get("alpha_beta", current["alpha_beta"])
                proposed = self._baseline_policy(gains)
            else:
                proposed = dict(current, controller_id=controller_id, alpha_beta=support["alpha_beta"],
                                checkpoint_sha256=support["decision"]["checkpoint_sha256"],
                                evidence_sha256=evidence_sha256)
            if proposed == current:
                raise ValueError("proposal equals the active configuration")
            record = {"schema": "orion.polarisation-proposal.v1", "proposal_id": str(uuid4()), "mode": "simulation",
                      "prepared_at_utc": _now(), "instance_id": self.instance_id, "current_policy": current,
                      "current_policy_sha256": _sha256(current), "proposed_policy": proposed,
                      "proposed_policy_sha256": _sha256(proposed), "support": support, "rollback_of": rollback_of,
                      "evidence_path": None if evidence_path is None else str(evidence_path),
                      "expires_after_s": MAX_PROPOSAL_AGE_S, "physical_action_performed": False}
            self._proposal, self._started = _receipt(record), time.monotonic()
            return self._proposal

    def review(self, proposal_id, expected_proposal_sha256):
        with self._lock:
            proposal = self._selected_proposal(proposal_id, expected_proposal_sha256)
            self._recheck(proposal["record"])
            record = {"schema": "orion.polarisation-review.v1", "proposal": proposal, "reviewed_at_utc": _now(),
                      "status": "CURRENT", "interpretation": "Context unchanged at review time; not an approval."}
            self._review = _receipt(record)
            return self._review

    def approve(self, payload):
        with self._lock:
            request = PolicyApprovalRequest.from_dict(payload)
            proposal = self._selected_proposal(request.proposal_id, request.expected_proposal_sha256)
            if self._review is None or self._review["record_sha256"] != request.expected_review_sha256:
                raise ValueError("approval must cite the current review")
            self._recheck(proposal["record"])
            record = {"schema": "orion.polarisation-approval.v1", "approval_id": str(uuid4()),
                      "approved_at_utc": _now(), "operator_label": request.operator_label,
                      "operator_authenticated": False, "review": self._review,
                      "interpretation": "Declared approval of a simulation proposal; not an activation."}
            self._approval = _receipt(record)
            return self._approval

    def _selected_proposal(self, proposal_id, expected_sha256):
        _uuid(proposal_id, "proposal")
        _digest(expected_sha256, "proposal")
        proposal = self._proposal
        if proposal is None or proposal["record"]["proposal_id"] != proposal_id or proposal["record_sha256"] != expected_sha256:
            raise ValueError("proposal is not the current selection")
        self._unexpired()
        return proposal

    def _recheck(self, record):
        target = record["proposed_policy"]["controller_id"]
        evidence = record["support"]["evidence_sha256"] if record["support"] else None
        current, support = self._context(target, record["evidence_path"], evidence)
        if (_sha256(current) != record["current_policy_sha256"]
                or json.loads(json.dumps(support, allow_nan=False)) != record["support"]):
            raise ValueError("context changed since preparation; prepare again")

    def activate(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"mode", "approval_id", "expected_approval_sha256",
                                                             "confirmation"}:
            raise ValueError("invalid activation fields")
        if payload["mode"] != "simulation" or payload["confirmation"] != ACTIVATION_CONFIRMATION:
            raise ValueError("explicit simulation activation confirmation required")
        _uuid(payload["approval_id"], "approval")
        _digest(payload["expected_approval_sha256"], "approval")
        with self._lock:
            approval = self._approval
            if (approval is None or approval["record"]["approval_id"] != payload["approval_id"]
                    or approval["record_sha256"] != payload["expected_approval_sha256"]):
                raise ValueError("approval is not the current local selection")
            proposal = approval["record"]["review"]["record"]["proposal"]["record"]
            old = (self.policy, self.controller, self.fallback, self._marker)
            try:
                self._unexpired()
                with closing(self._connect()) as connection:
                    try:
                        connection.execute("BEGIN IMMEDIATE")
                        self._recheck(proposal)
                        if self._marker is not None:
                            self._verify_marker(connection)
                        proposed = proposal["proposed_policy"]
                        controller, fallback = self._build(proposed)
                        revision = connection.execute(
                            "SELECT COALESCE(MAX(revision),0)+1 FROM polarisation_activations").fetchone()[0]
                        record = {"schema": "orion.polarisation-activation.v1", "activation_id": str(uuid4()),
                                  "revision": revision, "status": "COMMITTED", "mode": "simulation",
                                  "transition_kind": "rollback" if proposal["rollback_of"] else "activation",
                                  "rollback_of": proposal["rollback_of"], "instance_id": self.instance_id,
                                  "recorded_at_utc": _now(), "approval": approval,
                                  "previous_policy": proposal["current_policy"], "active_policy": proposed,
                                  "active_policy_sha256": _sha256(proposed),
                                  "previous_activation_id": None if self._marker is None else self._marker["activation_id"],
                                  "scope": "this_process_only_no_restart_restore",
                                  "physical_action_performed": False}
                        receipt = _receipt(record)
                        raw = json.dumps(record, ensure_ascii=False, allow_nan=False)
                        if len(raw.encode("utf-8")) > MAX_RECORD_BYTES:
                            raise ValueError("activation record exceeds 256 KiB")
                        connection.execute("INSERT INTO polarisation_activations VALUES (?,?,?,?,?,?)",
                                           (revision, record["activation_id"], payload["approval_id"],
                                            self.instance_id, receipt["record_sha256"], raw))
                        self._unexpired()
                        self.policy, self.controller, self.fallback = proposed, controller, fallback
                        if _sha256(self._describe(controller, fallback)) != record["active_policy_sha256"]:
                            raise ValueError("live controller does not read back as the approved configuration")
                        self._marker = {"activation_id": record["activation_id"],
                                        "record_sha256": receipt["record_sha256"], "revision": revision}
                        connection.commit()
                    except BaseException:
                        self.policy, self.controller, self.fallback, self._marker = old
                        connection.rollback()
                        raise
                return receipt
            finally:
                self._proposal = self._review = self._approval = None

    def _load(self, connection, activation_id, expected_sha256):
        row = connection.execute("SELECT * FROM polarisation_activations WHERE activation_id=?",
                                 (activation_id,)).fetchone()
        if row is None or len(row["record_json"].encode("utf-8")) > MAX_RECORD_BYTES:
            raise ValueError("activation missing or oversized")
        record = _strict(row["record_json"])
        if (_sha256(record) != expected_sha256 or row["record_sha256"] != expected_sha256
                or record.get("activation_id") != activation_id or record.get("status") != "COMMITTED"
                or record.get("instance_id") != row["instance_id"]
                or _sha256(record["active_policy"]) != record["active_policy_sha256"]):
            raise ValueError("activation receipt/index/hash mismatch")
        return record

    def _verify_marker(self, connection):
        marker = self._marker
        record = self._load(connection, marker["activation_id"], marker["record_sha256"])
        if (record["instance_id"] != self.instance_id or record["revision"] != marker["revision"]
                or _sha256(self._describe(self.controller, self.fallback)) != record["active_policy_sha256"]):
            raise ValueError("live controller does not match this activation")
        return record

    def status(self):
        with self._lock:
            base = {"schema": "orion.polarisation-activation-readback.v1", "observed_at_utc": _now(),
                    "instance_id": self.instance_id, "mode": "simulation", "physical_action_performed": False,
                    "live_policy": self._describe(self.controller, self.fallback)}
            if self._marker is None:
                return _receipt(dict(base, status="NO_LOCAL_ACTIVATION",
                                     reason="Classical alpha-beta baseline; no learned model is active."))
            try:
                with closing(self._connect()) as connection:
                    record = self._verify_marker(connection)
            except (ValueError, sqlite3.Error) as exc:
                return _receipt(dict(base, status="UNVERIFIED", activation_id=self._marker["activation_id"],
                                     reason=type(exc).__name__))
            return _receipt(dict(base, status="ACTIVE", activation_id=record["activation_id"],
                                 activation_sha256=self._marker["record_sha256"],
                                 interpretation="Live configuration matches the committed record at readback time."))

    def prepare_rollback(self):
        with self._lock:
            if self._marker is None:
                raise ValueError("nothing to roll back in this process")
            with closing(self._connect()) as connection:
                record = self._verify_marker(connection)
            target = record["previous_policy"]
            rollback = {"activation_id": record["activation_id"], "record_sha256": self._marker["record_sha256"],
                        "target_policy_sha256": _sha256(target), "alpha_beta": target["alpha_beta"]}
            if target["controller_id"] != "baseline.alpha_beta":
                raise ValueError("rollback to a learned controller needs a fresh evidence-bound proposal")
            return self.prepare("baseline.alpha_beta", rollback_of=rollback)

    # -- operation ----------------------------------------------------------

    def run(self, trajectory, *, plant_seed, monitoring_seed, fault_steps=()):
        """One supervised loop run with the live controller, journalled with its activation."""
        with self._lock:
            state = self.status()["record"]
            if state["status"] not in {"ACTIVE", "NO_LOCAL_ACTIVATION"}:
                raise ValueError("controller state unverified; the loop does not run")
            supervisor = loop.PolarisationSupervisor(loop.PolarisationSupervisorConfig(**self.policy["supervisor"]))
            archive_sha = None if self.fallback is None else self._reviewed_archive().sha256
            result = loop.run_loop(trajectory, self.controller, monitoring_seed=monitoring_seed,
                                   operating_point=self.operating_point, plant_seed=plant_seed, fallback=self.fallback,
                                   supervisor=supervisor, fault_steps=fault_steps, archive_sha256=archive_sha)
            record = {"schema": "orion.polarisation-cycle.v1", "recorded_at_utc": _now(),
                      "activation_status": state["status"], "activation_id": state.get("activation_id"),
                      "activation_sha256": state.get("activation_sha256"),
                      "policy_sha256": _sha256(self.policy), "loop": json.loads(result.serialized())}
            receipt = _receipt(record)
            with closing(self._connect()) as connection:
                connection.execute("INSERT INTO polarisation_cycles (instance_id, activation_id, record_sha256, "
                                   "record_json) VALUES (?,?,?,?)", (self.instance_id, state.get("activation_id"),
                                                                     receipt["record_sha256"],
                                                                     json.dumps(record, allow_nan=False)))
                connection.commit()
            return result, receipt


def approval_payload(proposal, review, operator_label):
    """Convenience for scripts: the exact approval declaration for the current selection."""
    return {"mode": "simulation", "proposal_id": proposal["record"]["proposal_id"],
            "expected_proposal_sha256": proposal["record_sha256"], "expected_review_sha256": review["record_sha256"],
            "operator_label": operator_label, "confirmation": APPROVAL_CONFIRMATION}


def activation_payload(approval):
    return {"mode": "simulation", "approval_id": approval["record"]["approval_id"],
            "expected_approval_sha256": approval["record_sha256"], "confirmation": ACTIVATION_CONFIRMATION}


__all__ = ["ACTIVATION_CONFIRMATION", "DEFAULT_ALPHA_BETA", "POLICY_ID", "PolarisationControlSlot",
           "activation_payload", "approval_payload", "evidence_supports", "read_evidence"]
