"""Q05 preparation, review and local approval; never switches policy or runs a cycle."""
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from threading import RLock
import time
from uuid import uuid4

from .campaign_read_store import ReadOnlyCampaignStore
from .command_reconciliation import _read_topology
from .policy import RepeaterSandboxPolicy
from .policy_approval import PolicyApprovalRequest
from .safety import SafetySupervisor, SafetyPolicyConfig
from .topology import DEFAULT_TOPOLOGY_ID, _sha256

POLICY_ID = 'repeater.utility.v1'
MAX_PROPOSAL_AGE_S = 300


def _number(value, name, maximum=None):
    if type(value) not in (int, float):
        raise ValueError(f'{name} must be numeric, not boolean')
    if value < 0 or (maximum is not None and value > maximum):
        raise ValueError(f'{name} outside supported range')
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f'{name} must be finite')
    return value


@dataclass(frozen=True)
class PolicyPreparationRequest:
    mode: str
    policy_id: str
    threshold: float
    optional_model_id: None
    expected_context_sha256: str

    def __post_init__(self):
        import re
        if self.mode != 'simulation' or self.policy_id != POLICY_ID:
            raise ValueError('only the existing repeater utility policy in simulation is supported')
        if self.optional_model_id is not None:
            raise ValueError('optional models have no Q05 activation adapter; keep OFF')
        _number(self.threshold, 'threshold', 1)
        if not isinstance(self.expected_context_sha256, str) or not re.fullmatch('[0-9a-f]{64}', self.expected_context_sha256):
            raise ValueError('exact observed context SHA-256 required')

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict) or set(payload) != {
                'mode', 'policy_id', 'threshold', 'optional_model_id', 'expected_context_sha256'}:
            raise ValueError('invalid policy preparation fields')
        return cls(**payload)


def _describe(runtime):
    from quantum_twin.control.policy import PolicyEngine
    from quantum_twin.control.risk_aware import RiskAwareController, RiskCostConfig
    from quantum_twin.evaluation.energy import EnergyConfig
    from quantum_twin.quantum.swapping import WernerStateSwapping
    policy, safety = runtime.policy, runtime.supervisor
    if type(policy) is not RepeaterSandboxPolicy or type(safety) is not SafetySupervisor:
        raise ValueError('unsupported active policy or supervisor')
    engine = policy.engine
    if type(engine) is not PolicyEngine or type(engine._risk_controller) is not RiskAwareController:
        raise ValueError('unsupported policy engine')
    risk = engine._risk_controller
    if (type(engine.energy_cfg) is not EnergyConfig or type(engine.risk_cfg) is not RiskCostConfig
            or type(risk.energy_cfg) is not EnergyConfig or type(risk.risk_cfg) is not RiskCostConfig
            or type(safety.config) is not SafetyPolicyConfig or type(engine._swapper) is not WernerStateSwapping
            or policy.name != RepeaterSandboxPolicy.name):
        raise ValueError('unsupported policy configuration')
    threshold = _number(engine.threshold, 'active threshold', 1)
    if (type(risk.threshold) not in (float, int) or threshold != risk.threshold
            or _sha256(asdict(engine.energy_cfg)) != _sha256(asdict(risk.energy_cfg))
            or _sha256(asdict(engine.risk_cfg)) != _sha256(asdict(risk.risk_cfg))):
        raise ValueError('inconsistent policy engine configuration')
    energy, costs, supervisor = asdict(engine.energy_cfg), asdict(engine.risk_cfg), asdict(safety.config)
    for name, value in (energy | costs | supervisor).items():
        _number(value, name)
    if type(costs['N_GATES_PURIFY']) is not int:
        raise ValueError('gate count must be an integer')
    descriptor = dict(policy_id=POLICY_ID, policy_name=policy.name, threshold=threshold,
                      energy_costs=energy, risk_costs=costs,
                      inference_latency_s=_number(risk.inference_latency_s, 'inference_latency_s'),
                      wait_time_s=_number(risk.wait_time_s, 'wait_time_s'), optional_model_id=None)
    identities = (policy, engine, risk, safety, engine._swapper, engine.energy_cfg,
                  engine.risk_cfg, risk.energy_cfg, risk.risk_cfg, safety.config)
    return descriptor, supervisor, identities


def _receipt(record):
    # Detach mutable caller data. These hashes are local integrity checks, not signatures.
    detached = json.loads(json.dumps(record, allow_nan=False))
    return {'record': detached, 'record_sha256': _sha256(detached)}


class PolicyPreparationSession:
    """One replaceable proposal tied to an existing local runtime and session.

    A process restart/source invalidation requires fresh preparation. Revision
    counts observations, not persistent policy changes or unobserved mutations.
    """
    def __init__(self, runtime):
        from .runtime import ControllerRuntime
        if type(runtime) is not ControllerRuntime:
            raise ValueError('existing local ControllerRuntime required')
        self.runtime = runtime
        self.source_id = str(uuid4())
        self._lock = RLock()
        self._closed = False
        self._proposal_json = None
        self._review_json = self._approval_json = None
        self._started = None
        self._stale = False
        self._revision = 0
        self._last_identity = self._last_policy_hash = None
        self._journal_path = runtime.journal.path.resolve(strict=True)

    def invalidate(self):
        with self._lock:
            self._closed = True
            self._stale = True
            self._review_json = self._approval_json = None

    def _capture(self, connection=None):
        if self._closed:
            raise ValueError('source session invalidated; prepare in the current source')
        if self.runtime.journal.path.resolve(strict=True) != self._journal_path:
            raise ValueError('runtime journal changed')
        with self.runtime._cycle_lock:
            baseline, supervisor, identities = _describe(self.runtime)
            policy_hash = _sha256(dict(baseline=baseline, supervisor=supervisor))
            if self._last_identity is not None and (
                    any(a is not b for a, b in zip(identities, self._last_identity))
                    or policy_hash != self._last_policy_hash):
                self._revision += 1
            self._last_identity, self._last_policy_hash = identities, policy_hash
            if connection is None:
                with closing(ReadOnlyCampaignStore(self._journal_path)._connect()) as reader:
                    reader.execute('BEGIN')
                    topology = _read_topology(reader, DEFAULT_TOPOLOGY_ID)
            else:
                topology = _read_topology(connection, DEFAULT_TOPOLOGY_ID)
            if topology is None:
                raise ValueError('persisted simulator topology required; preparation does not initialize it')
            after, after_supervisor, after_identities = _describe(self.runtime)
            if (baseline != after or supervisor != after_supervisor
                    or any(a is not b for a, b in zip(identities, after_identities))):
                raise ValueError('policy changed during observation')
        return dict(source=dict(kind='local_simulation_runtime', session_id=self.source_id),
                    policy_observation_revision=self._revision,
                    baseline=baseline, baseline_sha256=_sha256(baseline), supervisor=supervisor,
                    topology_id=topology['topology_id'], topology_version=topology['topology_version'],
                    topology_sha256=_sha256(topology))

    def inspect(self):
        with self._lock:
            context = self._capture()
            return _receipt(dict(schema='orion.policy_context.v1', observed_at_utc=datetime.now(timezone.utc).isoformat(),
                                 context=context, context_sha256=_sha256(context), **self._limits()))

    @staticmethod
    def _limits():
        return dict(mode='simulation', activation_available=False, operator_approval_recorded=False,
                    policy_activated=False, commands_emitted=0, physical_action_performed=False)

    def prepare(self, payload):
        request = PolicyPreparationRequest.from_dict(payload)
        with self._lock:
            context = self._capture()
            if _sha256(context) != request.expected_context_sha256:
                raise ValueError('context changed since inspection; inspect and prepare again')
            baseline = context['baseline']
            if request.threshold == baseline['threshold']:
                raise ValueError('proposal must change the threshold; baseline remains active')
            proposed = dict(baseline, threshold=request.threshold)
            now = datetime.now(timezone.utc)
            record = dict(schema='orion.policy_proposal.v1', proposal_id=str(uuid4()), status='PREPARED',
                          created_at_utc=now.isoformat(), expires_at_utc=(now + timedelta(seconds=MAX_PROPOSAL_AGE_S)).isoformat(),
                          max_age_s=MAX_PROPOSAL_AGE_S, request=asdict(request), context=context,
                          context_sha256=_sha256(context), proposed_policy=proposed, proposed_policy_sha256=_sha256(proposed),
                          changes={'threshold': {'before': baseline['threshold'], 'proposed': request.threshold}},
                          **self._limits())
            proposal = _receipt(record)
            self._proposal_json = json.dumps(proposal, allow_nan=False)
            self._review_json = self._approval_json = None
            self._started, self._stale = time.monotonic(), False
            return proposal

    def review(self, proposal_id, expected_sha256):
        with self._lock:
            if self._closed:
                raise ValueError('source session invalidated; prepare in the current source')
            proposal = json.loads(self._proposal_json) if self._proposal_json else None
            if (proposal is None or proposal['record']['proposal_id'] != proposal_id
                    or proposal['record_sha256'] != expected_sha256):
                raise ValueError('proposal or hash is not the current local selection')
            reasons, current = [], None
            if self._stale:
                reasons.append('previously_invalidated')
            age = time.monotonic() - self._started
            if not 0 <= age < MAX_PROPOSAL_AGE_S:
                reasons.append('proposal_expired')
            try:
                current = self._capture()
                prior = proposal['record']['context']
                if current['source'] != prior['source']:
                    reasons.append('source_changed')
                if current['baseline_sha256'] != prior['baseline_sha256'] or current['policy_observation_revision'] != prior['policy_observation_revision']:
                    reasons.append('policy_changed')
                if current['supervisor'] != prior['supervisor']:
                    reasons.append('supervisor_changed')
                if (current['topology_version'] != prior['topology_version']
                        or current['topology_sha256'] != prior['topology_sha256']):
                    reasons.append('topology_changed')
            except Exception as exc:
                reasons.append('context_unavailable:' + type(exc).__name__)
            # The read/lock wait can cross the deadline. Check again after capture.
            if not 0 <= time.monotonic() - self._started < MAX_PROPOSAL_AGE_S and 'proposal_expired' not in reasons:
                reasons.append('proposal_expired')
            self._stale = bool(reasons)
            result = _receipt(dict(schema='orion.policy_review.v1', review_id=str(uuid4()),
                                 reviewed_at_utc=datetime.now(timezone.utc).isoformat(),
                                 status='STALE' if reasons else 'CURRENT', reasons=reasons,
                                 interpretation='Compatibility observation only; no operator approval or activation authority.',
                                 proposal=proposal, current_context=current,
                                 current_context_sha256=None if current is None else _sha256(current), **self._limits()))
            self._review_json = json.dumps(result, allow_nan=False)
            self._approval_json = None
            return result

    def approve(self, payload):
        request = PolicyApprovalRequest.from_dict(payload)
        with self._lock:
            if self._closed:
                raise ValueError('source session invalidated; prepare in the current source')
            selected = json.loads(self._review_json) if self._review_json else None
            if (selected is None or selected['record_sha256'] != request.expected_review_sha256
                    or selected['record']['proposal']['record']['proposal_id'] != request.proposal_id
                    or selected['record']['proposal']['record_sha256'] != request.expected_proposal_sha256):
                raise ValueError('review or proposal hash is not the current local selection')
            if selected['record']['status'] != 'CURRENT':
                raise ValueError('only a CURRENT review can be submitted for approval')
            if self._approval_json is not None:
                raise ValueError('approval already recorded; explicit new review required')
            # Consume the reviewed selection and re-observe before recording. No
            # imported receipt, caller-supplied context, policy switch or DB write.
            fresh = self.review(request.proposal_id, request.expected_proposal_sha256)
            if fresh['record']['status'] != 'CURRENT':
                raise ValueError('approval refused: ' + ', '.join(fresh['record']['reasons']))
            if fresh['record']['current_context_sha256'] != selected['record']['current_context_sha256']:
                self._stale = True
                self._review_json = None
                raise ValueError('reviewed context changed; prepare again')
            record = dict(schema='orion.policy_approval.v1', approval_id=str(uuid4()), status='RECORDED',
                          recorded_at_utc=datetime.now(timezone.utc).isoformat(), request=asdict(request),
                          operator_identity='caller_declared_not_authenticated',
                          scope='simulation_proposal_only', activation_authority=False,
                          interpretation='Historical local approval declaration; not a valid activation token or proof of human identity.',
                          approved_review=selected, confirmation_review=fresh,
                          proposal_expires_at_utc=selected['record']['proposal']['record']['expires_at_utc'],
                          **(self._limits() | {'operator_approval_recorded': True}))
            result = _receipt(record)
            if not 0 <= time.monotonic() - self._started < MAX_PROPOSAL_AGE_S:
                self._stale = True
                self._review_json = None
                raise ValueError('proposal expired while recording approval')
            self._approval_json = json.dumps(result, allow_nan=False)
            return result

    def export_approval(self, approval_id, expected_sha256, destination):
        # Export the captured historical receipt; do not issue or renew approval.
        with self._lock:
            approval = json.loads(self._approval_json) if self._approval_json else None
            if (self._closed or approval is None or approval['record']['approval_id'] != approval_id
                    or approval['record_sha256'] != expected_sha256):
                raise ValueError('approval or hash is not the current local selection')
            raw = json.dumps(approval, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
            with Path(destination).open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(raw)
            return approval

    def export_review(self, proposal_id, expected_sha256, destination):
        review = self.review(proposal_id, expected_sha256)
        raw = json.dumps(review, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        with Path(destination).open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(raw)
        return review
