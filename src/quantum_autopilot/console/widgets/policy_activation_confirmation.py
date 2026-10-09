"""Explicit confirmation of an approved threshold change in this simulation instance."""
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QWidget

from quantum_autopilot.control_plane.policy_activation import ACTIVATION_CONFIRMATION, PolicyActivationRequest
from ..theme import C, mono
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText
from .prims import Btn, Prose, Section, vbox, hbox


class PolicyActivationConfirmation(AdaptiveDialog):
    def __init__(self, approval, parent=None):
        super().__init__(parent)
        self._approval = json.loads(json.dumps(approval, allow_nan=False))
        self.setWindowTitle('OrionQ · Confirm simulation activation')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(680, 500)
        self.setMinimumSize(600, 450)
        self.setStyleSheet(f'QDialog{{background:{C.raised};}} QCheckBox{{color:{C.ink};}} QPlainTextEdit{{background:{C.void};color:{C.ink};border:1px solid {C.line};}}')
        layout = vbox(self, (18, 16, 18, 16), 10)
        layout.addWidget(Section('ACTIVATE APPROVED POLICY · SIMULATION', C.amber))
        layout.addWidget(Prose('This changes the existing policy threshold in this running simulator. Context and expiry will be checked again.', 11))
        self.details = EvidenceText()
        self.details.setReadOnly(True)
        self.details.setFont(mono(10))
        record = self._approval['record']
        proposal = record['approved_review']['record']['proposal']['record']
        self.details.set_evidence(
            f"Policy: {proposal['context']['baseline']['policy_name']}\n"
            f"Previous threshold: {proposal['context']['baseline']['threshold']:.12g}\n"
            f"Approved threshold: {proposal['proposed_policy']['threshold']:.12g}\n"
            f"Approval: {record['approval_id']}\nApproval SHA-256: {self._approval['record_sha256']}\n"
            f"Operator (declared): {record['request']['operator_label']}\n"
            f"Approved at: {record['recorded_at_utc']}\n"
            f"Topology: {proposal['context']['topology_id']} · version {proposal['context']['topology_version']}")
        if proposal.get('rollback_of'):
            self.details.set_evidence('ROLLBACK · recorded previous policy\n' + self.details.toPlainText()
                + '\nRollback of: ' + proposal['rollback_of']['activation_id'])
        layout.addWidget(self.details, 1)
        self.confirmed = QCheckBox('I activate this approved policy in this simulator.')
        layout.addWidget(self.confirmed)
        layout.addWidget(Prose('Optional models remain OFF; no physical action. Once commit starts, closing or switching sources cannot undo it. Read the active policy afterwards.', 11))
        buttons = QWidget()
        row = hbox(buttons, (0, 0, 0, 0), 10)
        self.cancel_button = Btn('CANCEL', C.muted)
        self.activate_button = Btn('ACTIVATE IN SIMULATION', C.amber)
        row.addWidget(self.cancel_button)
        row.addWidget(self.activate_button)
        layout.addWidget(buttons)
        self.cancel_button.clicked.connect(self.reject)
        self.activate_button.clicked.connect(self.accept)
        self.confirmed.toggled.connect(self._validate)
        self._validate()

    def request(self):
        if not self.confirmed.isChecked():
            raise ValueError('explicit activation checkbox required')
        payload = dict(mode='simulation', approval_id=self._approval['record']['approval_id'],
            expected_approval_sha256=self._approval['record_sha256'], confirmation=ACTIVATION_CONFIRMATION)
        PolicyActivationRequest.from_dict(payload)
        return payload

    def _validate(self, *_):
        self.activate_button.setEnabled(self.confirmed.isChecked())

    def accept(self):
        try:
            self.request()
        except ValueError:
            return
        super().accept()
