"""Explicit local confirmation to retain the restored simulation policy after uncertainty."""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLineEdit, QWidget

from quantum_autopilot.control_plane.policy_recovery import RECOVERY_CONFIRMATION, PolicyRecoveryRequest
from ..theme import C, mono
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText
from .prims import Btn, Prose, Section, vbox, hbox


class PolicyRecoveryConfirmation(AdaptiveDialog):
    def __init__(self, review, parent=None):
        super().__init__(parent)
        self._review = json.loads(json.dumps(review, allow_nan=False))
        self.setWindowTitle("OrionQ · Confirm simulation recovery")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(700, 560)
        self.setMinimumSize(600, 500)
        self.setStyleSheet(
            f"QDialog{{background:{C.raised};}} QCheckBox{{color:{C.ink};}} QPlainTextEdit,QLineEdit{{background:{C.void};color:{C.ink};border:1px solid {C.line};}}"
        )
        layout = vbox(self, (18, 16, 18, 16), 10)
        layout.addWidget(Section("RECOVER · KEEP RESTORED SIMULATION POLICY", C.amber))
        layout.addWidget(
            Prose(
                "Keep the restored policy and record recovery before releasing blocked cycles. The failed candidate will not be retried.",
                11,
            )
        )
        self.details = EvidenceText()
        self.details.setReadOnly(True)
        self.details.setFont(mono(10))
        record = self._review["record"]
        context = record["context"]
        self.details.set_evidence(
            f"Retained policy: {context['retained_policy']['policy_name']}\n"
            f"Retained threshold: {context['retained_policy']['threshold']:.12g}\n"
            f"Fault: {context['fault_id']}\nFailed attempt: {context['failed_activation_id']}\n"
            f"Failed record: {context['failed_record_observation']}\n"
            f"Runtime: {context['runtime_instance_id']}\nObserved at: {record['observed_at_utc']}\n"
            f"Review SHA-256: {self._review['record_sha256']}\n"
            "An absent record does not prove the failed commit never occurred."
        )
        layout.addWidget(self.details, 1)
        layout.addWidget(Prose("Operator label (declared locally; identity is not authenticated)", 11))
        self.operator_label = QLineEdit()
        self.operator_label.setMaxLength(80)
        self.operator_label.setPlaceholderText("Enter your operator label")
        layout.addWidget(self.operator_label)
        self.confirmed = QCheckBox("I keep this restored policy and confirm simulation recovery.")
        layout.addWidget(self.confirmed)
        layout.addWidget(
            Prose(
                "Context and expiry will be checked again. Once commit begins, closing cannot undo it. Models remain OFF; no physical action.",
                11,
            )
        )
        buttons = QWidget()
        row = hbox(buttons, (0, 0, 0, 0), 10)
        self.cancel_button = Btn("CANCEL", C.muted)
        self.recover_button = Btn("KEEP POLICY AND RECOVER", C.amber)
        row.addWidget(self.cancel_button)
        row.addWidget(self.recover_button)
        layout.addWidget(buttons)
        self.cancel_button.clicked.connect(self.reject)
        self.recover_button.clicked.connect(self.accept)
        self.operator_label.textChanged.connect(self._validate)
        self.confirmed.toggled.connect(self._validate)
        self._validate()

    def request(self):
        if not self.confirmed.isChecked():
            raise ValueError("explicit recovery checkbox required")
        payload = dict(
            mode="simulation",
            fault_id=self._review["record"]["context"]["fault_id"],
            expected_recovery_sha256=self._review["record_sha256"],
            operator_label=self.operator_label.text(),
            confirmation=RECOVERY_CONFIRMATION,
        )
        PolicyRecoveryRequest.from_dict(payload)
        return payload

    def _validate(self, *_):
        try:
            self.request()
        except ValueError:
            self.recover_button.setEnabled(False)
        else:
            self.recover_button.setEnabled(True)

    def accept(self):
        try:
            self.request()
        except ValueError:
            return
        super().accept()
