"""Explicit confirmation of one captured simulation proposal; no activation."""
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLineEdit, QWidget

from quantum_autopilot.control_plane.policy_approval import APPROVAL_CONFIRMATION, PolicyApprovalRequest
from ..theme import C, mono
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText
from .prims import Btn, Prose, Section, vbox, hbox


class PolicyApprovalConfirmation(AdaptiveDialog):
    def __init__(self, review, parent=None):
        super().__init__(parent)
        self._review = json.loads(json.dumps(review, allow_nan=False))
        self.setWindowTitle('OrionQ · Confirm proposal approval')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(680, 540)
        self.setMinimumSize(600, 480)
        self.setStyleSheet(f'QDialog{{background:{C.raised};}} QCheckBox{{color:{C.ink};}} QPlainTextEdit,QLineEdit{{background:{C.void};color:{C.ink};border:1px solid {C.line};}}')
        layout = vbox(self, (18, 16, 18, 16), 10)
        layout.addWidget(Section('CONFIRM APPROVAL · SIMULATION ONLY', C.amber))
        layout.addWidget(Prose('This records your declaration for the proposal below. The baseline stays active. Context and expiry will be checked again.', 11))
        self.details = EvidenceText()
        self.details.setReadOnly(True)
        self.details.setFont(mono(10))
        record = self._review['record']
        proposal = record['proposal']['record']
        self.details.set_evidence(
            f"Policy: {proposal['context']['baseline']['policy_name']}\n"
            f"Baseline threshold: {proposal['context']['baseline']['threshold']:.12g}\n"
            f"Proposed threshold: {proposal['proposed_policy']['threshold']:.12g}\n"
            f"Proposal: {proposal['proposal_id']}\n"
            f"Proposal SHA-256: {record['proposal']['record_sha256']}\n"
            f"Review: {record['review_id']}\nReviewed at: {record['reviewed_at_utc']}\n"
            f"Review SHA-256: {self._review['record_sha256']}\n"
            f"Context SHA-256: {record['current_context_sha256']}\n"
            f"Topology: {proposal['context']['topology_id']} · version {proposal['context']['topology_version']}")
        if proposal.get('rollback_of'):
            self.details.set_evidence('ROLLBACK · recorded previous policy\n' + self.details.toPlainText()
                + '\nRollback of: ' + proposal['rollback_of']['activation_id'])
        layout.addWidget(self.details, 1)
        layout.addWidget(Prose('Operator label (declared locally; identity is not authenticated)', 11))
        self.operator_label = QLineEdit()
        self.operator_label.setMaxLength(80)
        self.operator_label.setPlaceholderText('Enter your operator label')
        layout.addWidget(self.operator_label)
        self.confirmed = QCheckBox('I approve this proposal for simulation only.')
        layout.addWidget(self.confirmed)
        layout.addWidget(Prose('No activation authority, physical action or optional model activation. Cancel leaves the review unchanged.', 11))
        buttons = QWidget()
        row = hbox(buttons, (0, 0, 0, 0), 10)
        self.cancel_button = Btn('CANCEL', C.muted)
        self.record_button = Btn('RECORD APPROVAL', C.amber)
        row.addWidget(self.cancel_button)
        row.addWidget(self.record_button)
        layout.addWidget(buttons)
        self.cancel_button.clicked.connect(self.reject)
        self.record_button.clicked.connect(self.accept)
        self.operator_label.textChanged.connect(self._validate)
        self.confirmed.toggled.connect(self._validate)
        self._validate()

    def request(self):
        if not self.confirmed.isChecked():
            raise ValueError('explicit approval checkbox required')
        proposal = self._review['record']['proposal']
        payload = dict(mode='simulation', proposal_id=proposal['record']['proposal_id'],
                       expected_proposal_sha256=proposal['record_sha256'],
                       expected_review_sha256=self._review['record_sha256'],
                       operator_label=self.operator_label.text(), confirmation=APPROVAL_CONFIRMATION)
        PolicyApprovalRequest.from_dict(payload)
        return payload

    def _validate(self, *_):
        try:
            self.request()
        except ValueError:
            self.record_button.setEnabled(False)
        else:
            self.record_button.setEnabled(True)

    def accept(self):
        try:
            self.request()
        except ValueError:
            return
        super().accept()
