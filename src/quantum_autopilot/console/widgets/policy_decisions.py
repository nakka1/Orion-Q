"""Explicit read-only audit of recorded simulation decisions, separate from live policy state."""
import json

from PySide6.QtCore import Qt, QTimer, QSignalBlocker
from PySide6.QtWidgets import QComboBox, QFileDialog, QTabWidget, QWidget

from quantum_autopilot.control_plane.policy_decisions import decision_history, explain_decision
from ..model_archive_check import LocalModelJob
from ..theme import C, mono
from .command_history import _export_captured
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText
from .prims import Btn, Prose, Section, vbox, hbox


class PolicyDecisionAudit(AdaptiveDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.provider = self._source = None
        self._export_capability = False
        self.history = self.explanation = None
        self._epoch = 0
        self._handled = None
        self.job = LocalModelJob(self)
        self.export_job = LocalModelJob(self)
        self.setWindowTitle('OrionQ · Recorded simulation decisions')
        self.resize(960, 650)
        self.setMinimumSize(640, 480)
        self.setStyleSheet(f'QDialog{{background:{C.raised};}} QPlainTextEdit,QComboBox{{background:{C.void};color:{C.ink};border:1px solid {C.line};}}')
        layout = vbox(self, (18,16,18,16), 10)
        layout.addWidget(Section('DECISION AUDIT · RECORDED SIMULATION', C.amber))
        layout.addWidget(Prose('Read recorded intent, safety assessment and executor result. No recomputation or current-policy inference; no physical control.', 11))
        self.selection = QComboBox()
        self.selection.setToolTip('Select a captured cycle, then verify its recorded decision with EXPLAIN SELECTED.')
        layout.addWidget(self.selection)
        tabs = QTabWidget()
        self.details = EvidenceText()
        self.receipt = EvidenceText()
        for widget, label in ((self.details,'STRUCTURED EXPLANATION'),(self.receipt,'CAPTURED RECEIPT')):
            widget.setReadOnly(True)
            widget.setFont(mono(10))
            tabs.addTab(widget,label)
            tabs.setTabToolTip(tabs.count() - 1, 'Read the captured explanation and evidence; scrolling does not run a new decision.')
        layout.addWidget(tabs,1)
        controls = QWidget()
        row = hbox(controls,(0,0,0,0),8)
        self.load_button = Btn('READ LATEST 20',C.amber)
        self.explain_button = Btn('EXPLAIN SELECTED',C.violet)
        self.export_button = Btn('EXPORT CAPTURED',C.muted)
        for button in (self.load_button,self.explain_button,self.export_button):
            row.addWidget(button)
        layout.addWidget(controls)
        self.message = 'Read the journal explicitly. No cycles will be executed.'
        self.status = Prose(self.message,11)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.selection.currentIndexChanged.connect(self._selection_changed)
        self.load_button.clicked.connect(self.load)
        self.explain_button.clicked.connect(self.explain)
        self.export_button.clicked.connect(self.export)
        self.job.changed.connect(self._changed)
        self.export_job.changed.connect(self._changed)
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(250)
        self.watchdog.timeout.connect(self._tick)
        self._render()

    def attach(self,provider):
        if self.provider is not None:
            raise ValueError('decision audit already attached')
        self.provider = provider
        provider.source_changed.connect(self._tick)
        self._tick()

    def _scope(self):
        if self.provider is None:
            self._export_capability = False
            return None
        service = getattr(self.provider.active,'service',None)
        capabilities = self.provider.capabilities
        self._export_capability = capabilities.allows('export_record')
        return (id(self.provider.active),self.provider._generation,
                capabilities.allows('read_policy_decisions'),id(service),
                str(getattr(service,'control_journal_path',None)))

    def _clear(self,message):
        self._epoch += 1
        self.history = self.explanation = None
        with QSignalBlocker(self.selection):
            self.selection.clear()
        self.message = message
        self.job.clear()
        self.export_job.clear()

    def _tick(self,*_):
        scope = self._scope()
        if scope != self._source:
            self._source = scope
            self._clear('Source changed. Read the journal again.')
        self._render()

    def _selection_changed(self,*_):
        self._epoch += 1
        self.explanation = None
        self.message = 'Select EXPLAIN SELECTED for the captured request ID/hash.'
        self.job.clear()
        self.export_job.clear()
        self._render()

    def _changed(self):
        self._tick()
        if (not self.job.active and self._handled != self.job.generation
                and (self.job.result is not None or self.job.error)):
            self._handled = self.job.generation
            if self.job.error:
                self.message = 'READ REFUSED · '+self.job.error
                self.explanation = None
            else:
                receipt = self.job.result
                if receipt['record']['schema']=='orion.policy_decision_history.v1':
                    self.history = receipt
                    with QSignalBlocker(self.selection):
                        self.selection.clear()
                        for item in receipt['record']['items']:
                            self.selection.addItem(f"{item['completed_at_utc']} · {item['request_id']} · {item['intended_action']} → {item['effective_action']}",item)
                    self.message = f"{len(receipt['record']['items'])} recorded simulation cycles. Select EXPLAIN SELECTED."
                else:
                    self.explanation = receipt
                    self.message = 'VERIFIED CYCLE · historical evidence; policy link: '+receipt['record']['policy_evidence']['status']
        self._render()

    def _render(self):
        allowed = self.isVisible() and self._source is not None and self._source[2]
        busy = self.job.active or self.export_job.active
        self.load_button.setEnabled(bool(allowed and not busy))
        self.selection.setEnabled(bool(allowed and not busy and self.history))
        self.explain_button.setEnabled(bool(allowed and not busy and self.selection.currentData()))
        writer = getattr(self.provider.active, 'export_captured_evidence', None) if self.provider else None
        self.export_button.setEnabled(bool(allowed and self._export_capability and callable(writer)
                                           and not busy and self.explanation))
        text = 'No explanation captured. Read the journal, then explain a selected cycle.'
        if self.explanation:
            r = self.explanation['record']
            link = r['policy_evidence']
            text = (f"HISTORICAL SIMULATION CYCLE\nRequest: {r['request_id']}\nCycle: {r['cycle_id']}\n"
                    f"Recorded at: {r['cycle']['record']['completed_at_utc']}\nObserved at: {r['observed_at_utc']}\n"
                    f"Policy: {r['policy_name']}\nPolicy evidence: {link['status']}\n{link['reason']}\n")
            if link['activation']:
                policy = link['activation']['record']
                text += f"Recorded threshold: {policy['active_policy']['threshold']:.12g} · revision {policy['revision']}\n"
            text += (f"\nINTENDED: {r['intended_action']}\nSAFETY AUTHORIZED: {r['authorized']}\n"
                     f"SAFETY EFFECTIVE: {r['effective_action']}\n"
                     f"EXECUTOR: {r['execution']['action']} · {r['execution']['status']}\n"
                     f"Performed in simulator: {r['execution']['performed_in_simulator']}\n"
                     '\nRECORDED SAFETY VIOLATIONS\n'+json.dumps(r['safety_violations'],indent=2,ensure_ascii=False)
                     +'\n\nRECORDED POLICY DETAILS\n'+json.dumps(r['recorded_policy_details'],indent=2,ensure_ascii=False)
                     +'\n\nRECORDED EXECUTOR DETAILS\n'+json.dumps(r['execution']['details'],indent=2,ensure_ascii=False))
        self.details.set_evidence(text)
        serialized = json.dumps(self.explanation,indent=2,ensure_ascii=False) if self.explanation else ''
        self.receipt.set_evidence(serialized)
        message = self.message
        if busy:
            message = 'READING / FINISHING · no queued work.'
        elif self.export_job.error:
            message = 'EXPORT REFUSED · '+self.export_job.error
        elif self.export_job.result:
            message = 'EXPORTED captured explanation · '+self.export_job.result
        self.status.setText(message)

    def load(self):
        self._tick()
        if not self.load_button.isEnabled():
            return
        self._clear('Reading recorded simulation cycles…')
        try:
            path = self.provider.active.policy_decision_store_path()
            self.job.submit(decision_history,path)
        except (ValueError,AttributeError) as exc:
            self.message = 'READ REFUSED · '+str(exc)
            self._render()

    def explain(self):
        self._tick()
        if not self.explain_button.isEnabled():
            return
        item = self.selection.currentData()
        self._epoch += 1
        self.explanation = None
        self.export_job.clear()
        try:
            path = self.provider.active.policy_decision_store_path()
            self.job.submit(explain_decision,path,item['request_id'],item['record_sha256'])
        except (ValueError,AttributeError) as exc:
            self.message = 'READ REFUSED · '+str(exc)
            self._render()

    def export(self):
        self._tick()
        if not self.export_button.isEnabled():
            return
        captured = json.dumps(self.explanation,indent=2,ensure_ascii=False,allow_nan=False)+'\n'
        epoch = self._epoch
        writer = self.provider.active.export_captured_evidence
        path,_ = QFileDialog.getSaveFileName(self,'Export captured simulation explanation (new file only)',
                                            'policy-decision.json','JSON (*.json)')
        self._tick()
        if not path or epoch!=self._epoch or not self.export_button.isEnabled():
            return
        self.export_job.submit(_export_captured,captured,path,writer)

    def showEvent(self,event):
        self._tick()
        self.watchdog.start()
        super().showEvent(event)

    def hideEvent(self,event):
        self.watchdog.stop()
        self._clear('Panel closed. Read the journal explicitly.')
        super().hideEvent(event)
