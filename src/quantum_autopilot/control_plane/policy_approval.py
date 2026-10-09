"""Strict local approval declaration; never an activation or authentication token."""
from dataclasses import dataclass
import re
from uuid import UUID

APPROVAL_CONFIRMATION = 'APPROVE_SIMULATION_PROPOSAL_ONLY'


@dataclass(frozen=True)
class PolicyApprovalRequest:
    mode: str
    proposal_id: str
    expected_proposal_sha256: str
    expected_review_sha256: str
    operator_label: str
    confirmation: str

    def __post_init__(self):
        if self.mode != 'simulation' or self.confirmation != APPROVAL_CONFIRMATION:
            raise ValueError('explicit confirmation of simulation proposal only required')
        try:
            canonical = str(UUID(self.proposal_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('canonical proposal UUID required') from None
        if canonical != self.proposal_id:
            raise ValueError('canonical proposal UUID required')
        for digest in (self.expected_proposal_sha256, self.expected_review_sha256):
            if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
                raise ValueError('exact proposal and reviewed receipt SHA-256 required')
        label = self.operator_label
        if (not isinstance(label, str) or not 1 <= len(label) <= 80
                or label != label.strip() or not label.isprintable()):
            raise ValueError('operator label must be 1..80 printable characters without outer whitespace')

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict) or set(payload) != {
                'mode', 'proposal_id', 'expected_proposal_sha256', 'expected_review_sha256',
                'operator_label', 'confirmation'}:
            raise ValueError('invalid policy approval fields')
        return cls(**payload)
