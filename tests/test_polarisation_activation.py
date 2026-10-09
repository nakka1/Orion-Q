"""Q05 lifecycle of the polarisation control target: evidence-bound, reversible, fail-closed."""
from dataclasses import asdict
import hashlib
import json
import sqlite3

import numpy as np
import pytest

from quantum_autopilot.control_plane import polarisation_activation as activation
from quantum_autopilot.control_plane import polarisation_campaign as campaign
from quantum_autopilot.control_plane import polarisation_loop as loop
from quantum_autopilot.control_plane import polarisation_plant as plant
from tests import test_polarisation_loop as loop_tests

pytestmark = pytest.mark.unit
RIDGE = "qnet.compensation.calibrado.ridge"


@pytest.fixture(scope="module")
def trajectory():
    return plant.simulate(plant.OPERATING_POINTS["calibrado"], 0.5, 1000)


@pytest.fixture
def archive(tmp_path, monkeypatch):
    return loop_tests._archive(tmp_path, monkeypatch)


def evidence(tmp_path, archive, model_id=RIDGE, name="summary.json", **changes):
    decision = loop.check_checkpoint(model_id, archive, "calibrado")
    summary = {"schema": activation.EVIDENCE_SCHEMA, "status": "complete",
               "manifest": {"protocol": campaign.PROTOCOL}, "contracts": [asdict(decision)],
               "reproduction": [{"model_id": model_id, "passed": True}],
               "comparisons": [{"model_id": model_id, "baseline": baseline, "baseline_to_model_qber_ratio": 3.3,
                                "wins": 20, "holm_p_value": 5.7e-6, "significant": True}
                               for baseline in campaign.PROTOCOL["baselines"]],
               "alpha_beta": {"alpha": 0.01, "beta": 0.0}, "policy_activated": False,
               "physical_action_performed": False}
    summary.update(changes)
    raw = json.dumps(summary).encode()
    path = tmp_path / name
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def slot_for(tmp_path, archive=None):
    return activation.PolarisationControlSlot(tmp_path / "journal.sqlite",
                                              archive_path=None if archive is None else tmp_path / "qnet.zip")


def through_activation(slot, proposal, label="operador-teste"):
    review = slot.review(proposal["record"]["proposal_id"], proposal["record_sha256"])
    approval = slot.approve(activation.approval_payload(proposal, review, label))
    return slot.activate(activation.activation_payload(approval))


def rows(tmp_path, table):
    with sqlite3.connect(tmp_path / "journal.sqlite") as connection:
        return connection.execute(f"SELECT * FROM {table}").fetchall()


def test_default_is_the_classical_baseline(tmp_path, trajectory):
    slot = slot_for(tmp_path)
    assert slot.status()["record"]["status"] == "NO_LOCAL_ACTIVATION"
    result, receipt = slot.run(trajectory, plant_seed=1000, monitoring_seed=7000)
    assert result.controller_id == "baseline.alpha_beta" and result.fallback_id is None
    assert receipt["record"]["activation_id"] is None and len(rows(tmp_path, "polarisation_cycles")) == 1
    with pytest.raises(ValueError, match="equals the active"):
        slot.prepare("baseline.alpha_beta")


def test_activation_then_rollback(tmp_path, archive, trajectory):
    slot = slot_for(tmp_path, archive)
    path, digest = evidence(tmp_path, archive)
    receipt = through_activation(slot, slot.prepare(RIDGE, evidence_path=path, evidence_sha256=digest))
    record = receipt["record"]
    assert record["active_policy"]["controller_id"] == RIDGE and record["transition_kind"] == "activation"
    assert record["physical_action_performed"] is False
    assert record["approval"]["record"]["operator_authenticated"] is False
    status = slot.status()["record"]
    assert status["status"] == "ACTIVE" and status["activation_id"] == record["activation_id"]
    result, cycle = slot.run(trajectory, plant_seed=1000, monitoring_seed=7000)
    assert result.controller_id == RIDGE and result.fallback_id == "baseline.alpha_beta"
    assert cycle["record"]["activation_id"] == record["activation_id"]

    back = through_activation(slot, slot.prepare_rollback())["record"]
    assert back["transition_kind"] == "rollback" and back["rollback_of"]["activation_id"] == record["activation_id"]
    assert back["active_policy"]["controller_id"] == "baseline.alpha_beta"
    assert slot.status()["record"]["status"] == "ACTIVE"
    result, _ = slot.run(trajectory, plant_seed=1000, monitoring_seed=7000)
    assert result.controller_id == "baseline.alpha_beta"
    assert len(rows(tmp_path, "polarisation_activations")) == 2 and len(rows(tmp_path, "polarisation_cycles")) == 2


@pytest.mark.parametrize("changes, match", [
    (dict(status="partial"), "not a complete"),
    (dict(manifest={"protocol": {**campaign.PROTOCOL, "alpha": 0.1}}), "protocol differs"),
    (dict(reproduction=[{"model_id": RIDGE, "passed": False}]), "reproduction gate"),
    (dict(alpha_beta={"alpha": 2.0, "beta": 0.0}), "alpha"),
    (dict(physical_action_performed=True), "simulation-only"),
])
def test_evidence_must_support_this_checkpoint(tmp_path, archive, changes, match):
    slot = slot_for(tmp_path, archive)
    path, digest = evidence(tmp_path, archive, **changes)
    with pytest.raises(ValueError, match=match):
        slot.prepare(RIDGE, evidence_path=path, evidence_sha256=digest)
    assert slot.status()["record"]["status"] == "NO_LOCAL_ACTIVATION"


def test_evidence_for_other_weights_or_weak_results_refused(tmp_path, archive):
    slot = slot_for(tmp_path, archive)
    decision = asdict(loop.check_checkpoint(RIDGE, archive, "calibrado"))
    decision["checkpoint_sha256"] = {"json": "0" * 64, "npz": "1" * 64}
    path, digest = evidence(tmp_path, archive, contracts=[decision])
    with pytest.raises(ValueError, match="different weights"):
        slot.prepare(RIDGE, evidence_path=path, evidence_sha256=digest)
    weak = [{"model_id": RIDGE, "baseline": baseline, "baseline_to_model_qber_ratio": 1.02, "wins": 12,
             "holm_p_value": 0.2, "significant": baseline != "baseline.alpha_beta"}
            for baseline in campaign.PROTOCOL["baselines"]]
    path, digest = evidence(tmp_path, archive, name="weak.json", comparisons=weak)
    with pytest.raises(ValueError, match="beating every baseline"):
        slot.prepare(RIDGE, evidence_path=path, evidence_sha256=digest)
    with pytest.raises(ValueError, match="differs from the selected hash"):
        slot.prepare(RIDGE, evidence_path=path, evidence_sha256="f" * 64)
    with pytest.raises(ValueError, match="evidence file"):
        slot.prepare(RIDGE, evidence_sha256=digest)


def test_contract_and_archive_gate_learned_controllers(tmp_path, archive):
    path, digest = evidence(tmp_path, archive)
    with pytest.raises(ValueError, match="archive required"):
        slot_for(tmp_path).prepare(RIDGE, evidence_path=path, evidence_sha256=digest)
    slot = slot_for(tmp_path, archive)
    with pytest.raises(ValueError, match="contract refuses"):
        slot.prepare("qnet.compensation.desenvolvimento.ridge", evidence_path=path, evidence_sha256=digest)
    with pytest.raises(ValueError, match="only the alpha-beta baseline"):
        slot.prepare("baseline.persistence")


def test_changed_evidence_invalidates_review(tmp_path, archive):
    slot = slot_for(tmp_path, archive)
    path, digest = evidence(tmp_path, archive)
    proposal = slot.prepare(RIDGE, evidence_path=path, evidence_sha256=digest)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="differs from the selected hash"):
        slot.review(proposal["record"]["proposal_id"], proposal["record_sha256"])


def test_expired_proposal_cannot_be_reviewed(tmp_path, archive, monkeypatch):
    slot = slot_for(tmp_path, archive)
    path, digest = evidence(tmp_path, archive)
    proposal = slot.prepare(RIDGE, evidence_path=path, evidence_sha256=digest)
    started = slot._started
    monkeypatch.setattr(activation.time, "monotonic", lambda: started + activation.MAX_PROPOSAL_AGE_S + 1)
    with pytest.raises(ValueError, match="expired"):
        slot.review(proposal["record"]["proposal_id"], proposal["record_sha256"])


def test_approval_and_activation_must_cite_the_current_selection(tmp_path, archive):
    slot = slot_for(tmp_path, archive)
    path, digest = evidence(tmp_path, archive)
    proposal = slot.prepare(RIDGE, evidence_path=path, evidence_sha256=digest)
    review = slot.review(proposal["record"]["proposal_id"], proposal["record_sha256"])
    payload = activation.approval_payload(proposal, review, "operador")
    for change in (dict(confirmation="yes"), dict(expected_review_sha256="a" * 64), dict(operator_label=" x")):
        with pytest.raises(ValueError):
            slot.approve({**payload, **change})
    approval = slot.approve(payload)
    request = activation.activation_payload(approval)
    for change in (dict(confirmation="ACTIVATE"), dict(expected_approval_sha256="b" * 64), dict(mode="physical")):
        with pytest.raises(ValueError):
            slot.activate({**request, **change})
    receipt = slot.activate(request)
    with pytest.raises(ValueError, match="current local selection"):
        slot.activate(request)  # an approval is consumed by its activation attempt
    assert slot.status()["record"]["activation_id"] == receipt["record"]["activation_id"]


class FailingCommit:
    def __init__(self, inner):
        self.inner = inner

    def execute(self, *args):
        return self.inner.execute(*args)

    def rollback(self):
        self.inner.rollback()

    def close(self):
        self.inner.close()

    def commit(self):
        raise sqlite3.OperationalError("disk I/O error")


def test_failed_commit_restores_the_previous_controller(tmp_path, archive, trajectory):
    slot = slot_for(tmp_path, archive)
    path, digest = evidence(tmp_path, archive)
    proposal = slot.prepare(RIDGE, evidence_path=path, evidence_sha256=digest)
    original = slot._connect
    slot._connect = lambda: FailingCommit(original())
    with pytest.raises(sqlite3.OperationalError):
        through_activation(slot, proposal)
    slot._connect = original
    assert slot.status()["record"]["status"] == "NO_LOCAL_ACTIVATION"
    assert slot.policy["controller_id"] == "baseline.alpha_beta" and not rows(tmp_path, "polarisation_activations")
    result, _ = slot.run(trajectory, plant_seed=1000, monitoring_seed=1)
    assert result.controller_id == "baseline.alpha_beta"


def test_tampered_record_blocks_the_loop(tmp_path, archive, trajectory):
    slot = slot_for(tmp_path, archive)
    path, digest = evidence(tmp_path, archive)
    through_activation(slot, slot.prepare(RIDGE, evidence_path=path, evidence_sha256=digest))
    with sqlite3.connect(tmp_path / "journal.sqlite") as connection:
        connection.execute("UPDATE polarisation_activations SET record_json = replace(record_json, 'COMMITTED', 'ACTIVE')")
    assert slot.status()["record"]["status"] == "UNVERIFIED"
    with pytest.raises(ValueError, match="unverified"):
        slot.run(trajectory, plant_seed=1000, monitoring_seed=1)


def test_supervisor_fallback_under_active_learned_controller(tmp_path, archive, trajectory):
    slot = slot_for(tmp_path, archive)
    path, digest = evidence(tmp_path, archive)
    through_activation(slot, slot.prepare(RIDGE, evidence_path=path, evidence_sha256=digest))
    result, _ = slot.run(trajectory, plant_seed=1000, monitoring_seed=1, fault_steps=range(20, 40))
    assert result.supervisor["faults"] == 8 and result.supervisor["degraded_at_s"] == pytest.approx(27 * 0.002)
    assert np.isfinite(result.summary["mean_qber"])
