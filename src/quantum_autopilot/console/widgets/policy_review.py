"""Native Q05 preparation, approval, explicit simulation activation and observed readback."""
import json
import math
import time

from PySide6.QtCore import Qt, QTimer, QSignalBlocker
from PySide6.QtWidgets import QLineEdit, QTabWidget, QWidget, QFileDialog

from quantum_autopilot.control_plane.policy_preparation import POLICY_ID, MAX_PROPOSAL_AGE_S
from ..model_archive_check import LocalModelJob
from ..policy_review_binding import PolicyReviewBinding
from ..theme import C, mono
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText
from .prims import Btn, Prose, Section, vbox, hbox
from .command_history import _export_captured
from .policy_approval_confirmation import PolicyApprovalConfirmation
from .policy_activation_confirmation import PolicyActivationConfirmation
from .policy_recovery_confirmation import PolicyRecoveryConfirmation


class PolicyReview(AdaptiveDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.provider = self.binding = self._source = None
        self._export_capability = False
        self.confirmation = None
        self.activation = self.active_observation = self.recovery_review = None
        self.context = self.proposal = self.review = self.approval = None
        self.deadline = self._prepare_started = None
        self._epoch = 0
        self._handled = None
        self.message = 'Load the baseline or read the active policy explicitly.'
        self.job = LocalModelJob(self)
        self.export_job = LocalModelJob(self)
        self.setWindowTitle('OrionQ · Policy review and activation')
        self.resize(960, 680)
        self.setMinimumSize(640, 500)
        self.setStyleSheet(f'QDialog{{background:{C.raised};}} QPlainTextEdit,QLineEdit{{background:{C.void};color:{C.ink};border:1px solid {C.line};}}')
        layout = vbox(self, (18, 16, 18, 16), 10)
        layout.addWidget(Section('POLICY REVIEW · LOCAL SIMULATION', C.amber))
        layout.addWidget(Prose('Existing repeater utility policy. Optional models remain OFF. Preparation and review do not approve or activate a change.', 11))
        field = QWidget()
        row = hbox(field, (0, 0, 0, 0), 10)
        row.addWidget(Prose('Proposed fidelity threshold [0..1]', 11))
        self.threshold = QLineEdit()
        self.threshold.setMaxLength(32)
        self.threshold.setPlaceholderText('Decimal point; explicit value')
        row.addWidget(self.threshold, 1)
        layout.addWidget(field)
        self.tabs = QTabWidget()
        self.comparison = EvidenceText()
        self.receipt = EvidenceText()
        for edit, label in ((self.comparison, 'BASELINE / PROPOSAL'), (self.receipt, 'CAPTURED RECEIPT')):
            edit.setReadOnly(True)
            edit.setFont(mono(10))
            self.tabs.addTab(edit, label)
        active_tab = QWidget()
        active_layout = vbox(active_tab, (4, 4, 4, 4), 8)
        controls = QWidget()
        controls_row = hbox(controls, (0, 0, 0, 0), 8)
        self.read_active_button = Btn('READ ACTIVE POLICY', C.violet)
        self.export_state_button = Btn('EXPORT CAPTURED STATE', C.muted)
        controls_row.addWidget(self.read_active_button)
        controls_row.addWidget(self.export_state_button)
        active_layout.addWidget(controls)
        recovery_controls = QWidget()
        recovery_row = hbox(recovery_controls, (0, 0, 0, 0), 8)
        self.rollback_button = Btn('PREPARE ROLLBACK', C.amber)
        self.inspect_recovery_button = Btn('INSPECT RECOVERY', C.violet)
        self.recover_button = Btn('CONFIRM RECOVERY…', C.amber)
        for button in (self.rollback_button, self.inspect_recovery_button, self.recover_button):
            recovery_row.addWidget(button)
        active_layout.addWidget(recovery_controls)
        self.active_details = EvidenceText()
        self.active_details.setReadOnly(True)
        self.active_details.setFont(mono(10))
        active_layout.addWidget(self.active_details, 1)
        self.tabs.addTab(active_tab, 'ACTIVE POLICY')
        layout.addWidget(self.tabs, 1)
        buttons = QWidget()
        row = hbox(buttons, (0, 0, 0, 0), 8)
        self.load_button = Btn('LOAD BASELINE', C.amber)
        self.prepare_button = Btn('PREPARE', C.amber)
        self.review_button = Btn('REVIEW', C.violet)
        self.export_button = Btn('EXPORT REVIEW', C.muted)
        for button in (self.load_button, self.prepare_button, self.review_button, self.export_button):
            row.addWidget(button)
        layout.addWidget(buttons)
        approvals = QWidget()
        row = hbox(approvals, (0, 0, 0, 0), 8)
        self.approve_button = Btn('APPROVE PROPOSAL', C.amber)
        self.export_approval_button = Btn('EXPORT APPROVAL', C.muted)
        row.addWidget(self.approve_button)
        row.addWidget(self.export_approval_button)
        self.activate_button = Btn('ACTIVATE…', C.amber)
        row.addWidget(self.activate_button)
        layout.addWidget(approvals)
        self.status = Prose(self.message, 11)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setMaximumHeight(72)
        layout.addWidget(self.status)
        scope_note = Prose('Approval and activation require separate confirmations. READ ACTIVE POLICY observes current state. Optional models stay OFF; no physical control.', 10)
        layout.addWidget(scope_note)
        self.fixed_footer = (buttons, approvals, self.status, scope_note)
        self.threshold.textChanged.connect(self._draft_changed)
        self.load_button.clicked.connect(self.load_baseline)
        self.prepare_button.clicked.connect(self.prepare)
        self.review_button.clicked.connect(self.review_proposal)
        self.export_button.clicked.connect(self.export_review)
        self.approve_button.clicked.connect(self.confirm_approval)
        self.export_approval_button.clicked.connect(self.export_approval)
        self.activate_button.clicked.connect(self.confirm_activation)
        self.read_active_button.clicked.connect(self.read_active)
        self.export_state_button.clicked.connect(self.export_state)
        self.rollback_button.clicked.connect(self.prepare_rollback)
        self.inspect_recovery_button.clicked.connect(self.inspect_recovery)
        self.recover_button.clicked.connect(self.confirm_recovery)
        self.job.changed.connect(self._changed)
        self.export_job.changed.connect(self._changed)
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(250)
        self.watchdog.timeout.connect(self._tick)
        self._render()

    def attach(self, provider):
        if self.provider is not None:
            raise ValueError('policy review already attached')
        self.provider = provider
        provider.source_changed.connect(self._tick)
        provider.snapshot_changed.connect(self._snapshot)
        self._sync_source()

    def _scope(self, capabilities=None):
        if self.provider is None:
            self._export_capability = False
            return None
        service = getattr(self.provider.active, 'service', None)
        if capabilities is None:
            capabilities = self.provider.capabilities
        self._export_capability = capabilities.allows('export_record')
        return (id(self.provider.active), self.provider._generation,
                capabilities.allows('review_policy_preparation'),
                id(getattr(service, '_controller_runtime', None)),
                getattr(service, '_policy_preparation_generation', None),
                capabilities.allows('approve_policy_proposal'),
                capabilities.allows('activate_simulation_policy'),
                capabilities.allows('read_active_policy'),
                capabilities.allows('prepare_policy_rollback'),
                capabilities.allows('recover_simulation_policy'))

    def _sync_source(self, capabilities=None):
        scope = self._scope(capabilities)
        if scope != self._source:
            # A completed recovery invalidates every older approval. Adopt only
            # the generation announced by this binding, never a source/runtime change.
            own_generation = None if self.binding is None else self.binding.completed_recovery_generation
            if scope is not None and self._source is not None and own_generation is not None and not self.binding.cancelled.is_set():
                expected = list(self._source)
                expected[4] = own_generation
                if scope == tuple(expected):
                    self._source = scope
                    return scope[2]
            self._source = scope
            self._reset('Source changed. Load a fresh baseline.', clear_draft=True)
        return scope is not None and scope[2]

    def _dismiss_confirmation(self):
        if self.confirmation is not None:
            self.confirmation.reject()

    def _reset(self, message, clear_draft=False):
        self._epoch += 1
        self._dismiss_confirmation()
        if self.binding is not None:
            self.binding.cancel()
        self.binding = None
        self.activation = self.active_observation = self.recovery_review = None
        self.context = self.proposal = self.review = self.approval = None
        self.deadline = self._prepare_started = None
        self.message = message
        # A reset must not recursively query the backend from job.clear's
        # synchronous changed signal. Real job completions keep their checks.
        with QSignalBlocker(self.job), QSignalBlocker(self.export_job):
            self.job.clear()
            self.export_job.clear()
        if clear_draft:
            with QSignalBlocker(self.threshold):
                self.threshold.clear()
        self._render()

    def _draft_changed(self, *_):
        if self.job.active:
            self._reset('Draft changed during work. Load the baseline again.')
            return
        self._epoch += 1
        self._dismiss_confirmation()
        self.proposal = self.review = self.approval = None
        self.deadline = None
        self.job.clear()
        self.export_job.clear()
        self.recovery_review = None
        if self.binding is not None:
            self.binding.discard_recovery()
        self.message = 'Draft changed. PREPARE creates a new proposal; nothing is activated.'
        self._render()

    def _snapshot(self, snap):
        self._sync_source(snap.capabilities)
        if self.context is not None:
            context = self.context['record']['context']
            if not snap.topology.persisted or snap.topology.version != context['topology_version']:
                self._reset('Topology changed or unavailable. Load the baseline again.')
        if self.recovery_review is not None:
            version = self.recovery_review['record']['context']['topology_version']
            if not snap.topology.persisted or snap.topology.version != version:
                self._reset('Recovery topology changed. Read active policy and inspect again.')
        self._tick(capabilities=snap.capabilities)

    def _tick(self, *_, capabilities=None):
        self._sync_source(capabilities)
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self._reset('Recovery review expired. Read active policy and inspect again.' if self.recovery_review else 'Proposal expired. Load the baseline and prepare again.')
        self._render()

    def _changed(self):
        self._sync_source()
        if (not self.job.active and self._handled != self.job.generation
                and (self.job.result is not None or self.job.error)):
            self._handled = self.job.generation
            if self.job.error:
                error = self.job.error
                self._reset('REFUSED / OUTCOME NOT CONFIRMED · ' + error + '\nREAD ACTIVE POLICY before deciding what to do next.')
                return
            if self.job.result is not None:
                operation, receipt = self.job.result['operation'], self.job.result['receipt']
                if operation == 'inspect':
                    self.context = receipt
                    self.message = 'Baseline observed. Enter a different threshold and select PREPARE.'
                elif operation == 'prepare':
                    self.proposal = receipt
                    self.deadline = self._prepare_started + MAX_PROPOSAL_AGE_S
                    self.message = 'PREPARED · select REVIEW to check the current context. Not approved.'
                elif operation in {'activate', 'recover'}:
                    self._epoch += 1
                    self.activation, self.active_observation = receipt, None
                    self.recovery_review = None
                    self.context = self.proposal = self.review = self.approval = None
                    self.deadline = self._prepare_started = None
                    self.message = 'COMMITTED · historical receipt. READ ACTIVE POLICY to observe current state.'
                    self.tabs.setCurrentIndex(2)
                elif operation == 'prepare_rollback':
                    self.context = self.job.result['context']
                    self.proposal = receipt
                    self.review = self.approval = self.activation = self.active_observation = None
                    with QSignalBlocker(self.threshold):
                        self.threshold.setText(str(receipt['record']['proposed_policy']['threshold']))
                    self.deadline = self._prepare_started + MAX_PROPOSAL_AGE_S
                    self.message = 'ROLLBACK PREPARED · recorded previous policy. REVIEW, approve and activate separately.'
                    self.tabs.setCurrentIndex(0)
                elif operation == 'inspect_recovery':
                    self.recovery_review = receipt
                    self.deadline = self._prepare_started + MAX_PROPOSAL_AGE_S
                    self.message = 'RECOVERY OBSERVED · cycles remain blocked until explicit recovery commits.'
                    self.tabs.setCurrentIndex(2)
                elif operation == 'read_active':
                    self.active_observation = receipt
                    self.message = receipt['record']['status'] + ' AT READBACK · refresh explicitly for a new observation.'
                    self.tabs.setCurrentIndex(2)
                elif operation == 'approve':
                    self.approval = receipt
                    self.message = 'APPROVAL RECORDED · baseline unchanged; no activation.'
                else:
                    self.review = receipt
                    self.message = receipt['record']['status'] + ' AT REVIEW · not approved, not activated.'
        self._tick()

    def _value(self):
        try:
            value = float(self.threshold.text())
        except ValueError:
            return None
        return value if math.isfinite(value) and 0 <= value <= 1 else None

    def _render(self):
        allowed = self._source is not None and self._source[2] and self.isVisible()
        writer = getattr(self.provider.active, 'export_captured_evidence', None) if self.provider else None
        export_allowed = bool(allowed and self._export_capability and callable(writer))
        busy = self.job.active or self.export_job.active or self.confirmation is not None
        value = self._value()
        baseline = None if self.context is None else self.context['record']['context']['baseline']
        self.load_button.setEnabled(bool(allowed and not busy))
        self.threshold.setEnabled(bool(allowed and not busy))
        rollback = bool(self.proposal and self.proposal['record'].get('rollback_of'))
        self.threshold.setReadOnly(rollback)
        self.prepare_button.setEnabled(bool(allowed and not busy and not rollback and baseline is not None and value is not None and value != baseline['threshold']))
        self.review_button.setEnabled(bool(allowed and not busy and self.proposal is not None))
        self.export_button.setEnabled(bool(export_allowed and not busy and self.review is not None))
        can_approve = allowed and self._source[5] and not busy
        self.approve_button.setEnabled(bool(can_approve and self.review is not None
                                           and self.review['record']['status'] == 'CURRENT' and self.approval is None))
        self.export_approval_button.setEnabled(bool(export_allowed and can_approve and self.approval is not None))
        self.activate_button.setEnabled(bool(allowed and self._source[6] and not busy and self.approval is not None))
        self.read_active_button.setEnabled(bool(allowed and self._source[7] and not busy))
        active_record = None if self.active_observation is None else self.active_observation['record']
        current = None if active_record is None else active_record.get('activation')
        target = None if current is None else current['record'].get('rollback_policy', current['record']['previous_policy'])
        self.rollback_button.setEnabled(bool(allowed and self._source[8] and not busy and active_record
                                             and active_record['status'] == 'ACTIVE' and target is not None))
        self.inspect_recovery_button.setEnabled(bool(allowed and self._source[9] and not busy
            and (self.recovery_review is not None or (active_record and active_record['status'] == 'UNRESOLVED'))))
        self.recover_button.setEnabled(bool(allowed and self._source[9] and not busy and self.recovery_review is not None))
        selected_state = self.recovery_review or self.active_observation or self.activation
        self.export_state_button.setEnabled(bool(export_allowed and self._source[7] and not busy and selected_state is not None))
        state_text = 'No active-policy observation. Select READ ACTIVE POLICY explicitly.'
        if self.activation:
            record = self.activation['record']
            state_text = (f"COMMITTED · HISTORICAL RECEIPT\n{record['activation_id']}\n"
                          f"Revision: {record['revision']} · threshold: {record['active_policy']['threshold']:.12g}\n"
                          f"Recorded at: {record['recorded_at_utc']}\nCurrent activity has not been observed.\n")
        if self.active_observation:
            record = self.active_observation['record']
            state_text = (f"{record['status']} AT READBACK\nObserved at: {record['observed_at_utc']}\n"
                          f"Runtime: {record['runtime_instance_id']}\n")
            if record.get('activation'):
                active = record['activation']['record']
                state_text += (f"Policy: {active['active_policy']['policy_name']}\n"
                               f"Threshold: {active['active_policy']['threshold']:.12g}\n"
                               f"Activation/checkpoint: {active['activation_id']}\nRevision: {active['revision']}\n"
                               f"SHA-256: {record['activation']['record_sha256']}\n")
            else:
                state_text += record.get('reason', '') + '\n'
            if record['status'] == 'UNRESOLVED':
                state_text += 'Cycles blocked. Select INSPECT RECOVERY to review the retained policy.\n'
            state_text += 'Captured observation; select READ ACTIVE POLICY to refresh.\n'
        if self.recovery_review:
            record = self.recovery_review['record']
            context = record['context']
            state_text = (f"RECOVERY OBSERVED · NOT YET RECOVERED\nObserved at: {record['observed_at_utc']}\n"
                          f"Retained threshold: {context['retained_policy']['threshold']:.12g}\n"
                          f"Fault: {context['fault_id']}\nFailed record: {context['failed_record_observation']}\n"
                          f"Review SHA-256: {self.recovery_review['record_sha256']}\n"
                          'Absence does not prove the commit never occurred.\nCONFIRM RECOVERY keeps this policy; no retry.')
        if self.active_details.toPlainText() != state_text:
            self.active_details.set_evidence(state_text)
        text = 'No baseline loaded. Select LOAD BASELINE explicitly.'
        if baseline is not None:
            context = self.context['record']['context']
            text = (f"BASELINE OBSERVED\n{baseline['policy_name']}\nThreshold: {baseline['threshold']:.12g}\n"
                    f"Topology: {context['topology_id']} · version {context['topology_version']}\n"
                    f"Policy observation revision: {context['policy_observation_revision']} (session only)\n"
                    f"Observed at: {self.context['record']['observed_at_utc']}\n\n")
            if self.proposal:
                proposal = self.proposal['record']
                text += (f"PROPOSED THRESHOLD: {proposal['proposed_policy']['threshold']:.12g}\n"
                         f"Proposal: {proposal['proposal_id']}\nNo policy change performed.\n\n")
            if self.proposal and self.proposal['record'].get('rollback_of'):
                text += 'ROLLBACK · recorded previous policy; fresh approval required.\n'
            if self.review:
                review = self.review['record']
                text += f"REVIEW: {review['status']}\nAt: {review['reviewed_at_utc']}\n"
                text += '\n'.join(review['reasons']) + '\nCompatibility observation only; not operator approval.'
        if self.approval:
            approval = self.approval['record']
            text += (f"\n\nAPPROVAL RECORDED: {approval['approval_id']}\n"
                     f"Operator (declared): {approval['request']['operator_label']}\n"
                     f"At: {approval['recorded_at_utc']}\nBaseline unchanged; no activation authority.")
        if self.comparison.toPlainText() != text:
            self.comparison.set_evidence(text)
        selected = self.recovery_review or self.active_observation or self.activation or self.approval or self.review or self.proposal or self.context
        serialized = json.dumps(selected, indent=2, ensure_ascii=False) if selected else ''
        if self.receipt.toPlainText() != serialized:
            self.receipt.set_evidence(serialized)
        message = self.message
        if self.job.active or self.export_job.active:
            message = 'WORKING / FINISHING · one operation at a time; no queue.'
        elif self.export_job.error:
            message = 'EXPORT REFUSED · ' + self.export_job.error
        elif self.export_job.result:
            message = 'EXPORTED captured receipt · ' + self.export_job.result
        if self.deadline is not None:
            message += f'\nLocal validity remaining: {max(0, math.ceil(self.deadline - time.monotonic()))} s. Review again for current context.'
        self.status.setText(message)
        self.status.setToolTip(message)

    def load_baseline(self):
        self._tick()
        if not self.load_button.isEnabled():
            return
        self._reset('Reading baseline…')
        try:
            service = self.provider.active.policy_review_source()
            self.binding = PolicyReviewBinding(service)
            self.job.submit(self.binding.run, 'inspect')
        except (ValueError, AttributeError) as exc:
            self._reset('REFUSED · ' + str(exc))

    def prepare(self):
        self._tick()
        if not self.prepare_button.isEnabled():
            return
        self._epoch += 1
        self._dismiss_confirmation()
        self.proposal = self.review = self.approval = None
        self.deadline = None
        self.export_job.clear()
        request = dict(mode='simulation', policy_id=POLICY_ID, threshold=self._value(), optional_model_id=None,
                       expected_context_sha256=self.context['record']['context_sha256'])
        self._prepare_started = time.monotonic()
        self.job.submit(self.binding.run, 'prepare', request)

    def review_proposal(self):
        self._tick()
        if not self.review_button.isEnabled():
            return
        self._epoch += 1
        self._dismiss_confirmation()
        self.review = self.approval = None
        self.export_job.clear()
        self.job.submit(self.binding.run, 'review', self.proposal['record']['proposal_id'], self.proposal['record_sha256'])

    def confirm_approval(self):
        self._tick()
        if not self.approve_button.isEnabled():
            return
        epoch = self._epoch
        dialog = PolicyApprovalConfirmation(self.review, self)
        self.confirmation = dialog

        def accepted():
            self.confirmation = None
            self._tick()
            if epoch != self._epoch or not self.approve_button.isEnabled():
                return
            payload = dialog.request()
            if self.review['record_sha256'] != payload['expected_review_sha256']:
                return
            self._epoch += 1
            self.export_job.clear()
            self.job.submit(self.binding.run, 'approve', payload)

        def finished(*_):
            if self.confirmation is dialog:
                self.confirmation = None
            self._render()
            # Release closures before deferred Qt destruction (also on parent close).
            dialog.accepted.disconnect(accepted)
            dialog.finished.disconnect(finished)
            dialog.deleteLater()

        dialog.accepted.connect(accepted)
        dialog.finished.connect(finished)
        dialog.open()
        self._render()

    def confirm_activation(self):
        self._tick()
        if not self.activate_button.isEnabled():
            return
        epoch = self._epoch
        dialog = PolicyActivationConfirmation(self.approval, self)
        self.confirmation = dialog

        def accepted():
            self.confirmation = None
            self._tick()
            if epoch != self._epoch or not self.activate_button.isEnabled():
                return
            payload = dialog.request()
            if self.approval['record_sha256'] != payload['expected_approval_sha256']:
                return
            self._epoch += 1
            self.export_job.clear()
            self.active_observation = None
            self.message = 'Activation submitted. Once commit begins, read active state to confirm the outcome.'
            self.job.submit(self.binding.run, 'activate', payload)

        def finished(*_):
            if self.confirmation is dialog:
                self.confirmation = None
            self._render()
            dialog.accepted.disconnect(accepted)
            dialog.finished.disconnect(finished)
            dialog.deleteLater()

        dialog.accepted.connect(accepted)
        dialog.finished.connect(finished)
        dialog.open()
        self._render()

    def read_active(self):
        self._tick()
        if not self.read_active_button.isEnabled():
            return
        self._epoch += 1
        if self.recovery_review is not None:
            self.recovery_review = None
            self.deadline = None
            if self.binding is not None:
                self.binding.discard_recovery()
        self.active_observation = None
        self.export_job.clear()
        try:
            if self.binding is None:
                self.binding = PolicyReviewBinding(self.provider.active.policy_review_source())
            self.job.submit(self.binding.run, 'read_active')
        except (ValueError, AttributeError) as exc:
            self._reset('READBACK REFUSED · ' + str(exc))

    def prepare_rollback(self):
        self._tick()
        if not self.rollback_button.isEnabled():
            return
        current = self.active_observation['record']['activation']
        self._reset('Preparing the recorded previous policy…')
        try:
            self.binding = PolicyReviewBinding(self.provider.active.policy_review_source())
            self._prepare_started = time.monotonic()
            self.job.submit(self.binding.run, 'prepare_rollback', current['record']['activation_id'], current['record_sha256'])
        except (ValueError, AttributeError) as exc:
            self._reset('ROLLBACK REFUSED · ' + str(exc))

    def inspect_recovery(self):
        self._tick()
        if not self.inspect_recovery_button.isEnabled():
            return
        self._reset('Inspecting recovery; cycles remain blocked…')
        try:
            self.binding = PolicyReviewBinding(self.provider.active.policy_review_source())
            self._prepare_started = time.monotonic()
            self.job.submit(self.binding.run, 'inspect_recovery')
        except (ValueError, AttributeError) as exc:
            self._reset('RECOVERY INSPECTION REFUSED · ' + str(exc))

    def confirm_recovery(self):
        self._tick()
        if not self.recover_button.isEnabled():
            return
        epoch = self._epoch
        dialog = PolicyRecoveryConfirmation(self.recovery_review, self)
        self.confirmation = dialog

        def accepted():
            self.confirmation = None
            self._tick()
            if epoch != self._epoch or not self.recover_button.isEnabled():
                return
            payload = dialog.request()
            if self.recovery_review['record_sha256'] != payload['expected_recovery_sha256']:
                return
            self._epoch += 1
            self.export_job.clear()
            self.job.submit(self.binding.run, 'recover', payload)

        def finished(*_):
            if self.confirmation is dialog:
                self.confirmation = None
            self._render()
            dialog.accepted.disconnect(accepted)
            dialog.finished.disconnect(finished)
            dialog.deleteLater()

        dialog.accepted.connect(accepted)
        dialog.finished.connect(finished)
        dialog.open()
        self._render()

    def export_state(self):
        self._tick()
        if not self.export_state_button.isEnabled():
            return
        captured = json.dumps(self.recovery_review or self.active_observation or self.activation, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
        epoch = self._epoch
        writer = self.provider.active.export_captured_evidence
        path, _ = QFileDialog.getSaveFileName(self, 'Export captured policy state (new file only)',
                                             'policy-state.json', 'JSON (*.json)')
        self._tick()
        if not path or epoch != self._epoch or not self.export_state_button.isEnabled():
            return
        self.export_job.submit(_export_captured, captured, path, writer)

    def export_approval(self):
        self._tick()
        if not self.export_approval_button.isEnabled():
            return
        captured = json.dumps(self.approval, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
        epoch = self._epoch
        writer = self.provider.active.export_captured_evidence
        path, _ = QFileDialog.getSaveFileName(self, 'Export captured approval (new file only)',
                                             'policy-approval.json', 'JSON (*.json)')
        self._tick()
        if not path or epoch != self._epoch or not self.export_approval_button.isEnabled():
            return
        self.export_job.submit(_export_captured, captured, path, writer)

    def export_review(self):
        self._tick()
        if not self.export_button.isEnabled():
            return
        captured = json.dumps(self.review, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
        epoch = self._epoch
        writer = self.provider.active.export_captured_evidence
        path, _ = QFileDialog.getSaveFileName(self, 'Export captured policy review (new file only)',
                                             'policy-review.json', 'JSON (*.json)')
        self._tick()
        if not path or epoch != self._epoch or not self.export_button.isEnabled():
            return
        self.export_job.submit(_export_captured, captured, path, writer)

    def showEvent(self, event):
        self._tick()
        self.watchdog.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.watchdog.stop()
        self._reset('Panel closed. Load a fresh baseline.', clear_draft=True)
        super().hideEvent(event)
