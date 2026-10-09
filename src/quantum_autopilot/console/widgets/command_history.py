"""Read-only command inspection and explicitly requested reconciliation evidence."""
import json

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import QHeaderView, QTabWidget, QWidget, QFileDialog

from quantum_autopilot.control_plane.command_reconciliation import reconcile_command
from ..model_archive_check import LocalModelJob
from ..capabilities import SourceCapabilities
from ..theme import C, mono
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText
from .prims import Btn, Prose, Section, vbox, hbox
from .records import RecordTable


def _export_captured(text, path, writer):
    # Only immutable, already captured text crosses into the worker. The
    # captured source revalidates export authority immediately before writing.
    return writer(text, path)


def _read_reconciliation(source, command_id, receipt_sha256):
    # This source boundary checks current read permission and can perform
    # authenticated IO. Keep it with the reconciliation read on the worker.
    path = source.command_reconciliation_store_path()
    return reconcile_command(path, command_id, receipt_sha256)


class CommandHistory(AdaptiveDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.records = ()
        self.provider = None
        self._presented_capabilities = SourceCapabilities()
        self._source = self._selection = None
        self._epoch = 0
        self.evidence_json = ''
        self.job = LocalModelJob(self)
        self.export_job = LocalModelJob(self)
        self.setWindowTitle('OrionQ · Local command center')
        self.resize(1000, 680)
        self.setMinimumSize(600, 440)
        self.setStyleSheet(f'QDialog{{background:{C.raised};}} QPlainTextEdit{{background:{C.void};color:{C.ink};border:1px solid {C.line};}}')
        layout = vbox(self, (18, 16, 18, 16), 8)
        layout.addWidget(Section('COMMAND CENTER · LOCAL INVENTORY ONLY', C.amber))
        self.note = Prose('No source loaded.', 11)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.note)
        self.table = RecordTable(['REQUESTED UTC', 'OPERATION', 'STATE', 'COMMAND ID'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.tabs = QTabWidget()
        self.detail = EvidenceText()
        self.output = EvidenceText()
        for edit, title in ((self.detail, 'ORIGINAL RECEIPT'), (self.output, 'OBSERVED EVIDENCE')):
            edit.setReadOnly(True)
            edit.setFont(mono(10))
            self.tabs.addTab(edit, title)
            self.tabs.setTabToolTip(self.tabs.count() - 1, 'Read this captured command evidence without retrying the operation.')
        layout.addWidget(self.tabs, 2)
        buttons = QWidget()
        row = hbox(buttons, (0, 0, 0, 0), 8)
        self.check_button = Btn('CHECK CURRENT STATE', C.amber)
        self.export_button = Btn('EXPORT EVIDENCE', C.muted)
        row.addWidget(self.check_button)
        row.addWidget(self.export_button)
        row.addStretch(1)
        layout.addWidget(buttons)
        self.status = Prose('Select an unresolved command to check current state.', 10)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        layout.addWidget(Prose('Historical observation only. Matching state does not confirm execution. No retry, cancellation or physical control. Local hashes are not signatures.', 10))
        self.table.selectionModel().selectionChanged.connect(self.selected)
        self.check_button.clicked.connect(self.check_current)
        self.export_button.clicked.connect(self.export_evidence)
        self.job.changed.connect(self._job_changed)
        self.export_job.changed.connect(self._job_changed)
        self._render()

    def attach(self, provider):
        if self.provider is not None:
            raise ValueError('command center already attached')
        self.provider = provider
        provider.source_changed.connect(self._source_changed)
        provider.snapshot_changed.connect(self._snapshot)
        self._sync_source()

    def _context(self, capabilities=None):
        if self.provider is None:
            return None
        if capabilities is None:
            capabilities = self._presented_capabilities
        return (id(self.provider.active), self.provider._generation,
                capabilities.allows('read_console_commands'), capabilities.allows('export_record'),
                self.provider.running)

    def _sync_source(self, capabilities=None):
        context = self._context(capabilities)
        if context != self._source:
            self._source = context
            self._invalidate()
            self.set_records((), 'Source changed; select a fresh receipt.')
        return context is not None and context[2] and context[4]

    def _source_changed(self, *_):
        self._presented_capabilities = SourceCapabilities()
        self._sync_source()

    def _snapshot(self, snap):
        # Qt renders the capabilities already authorized with this snapshot.
        # Reading provider.capabilities here would repeat service IO on Qt.
        self._presented_capabilities = snap.capabilities
        allowed = self._sync_source(snap.capabilities) and snap.capabilities.allows('read_console_commands')
        self.set_records(snap.ledger.commands if allowed else (),
                         snap.ledger.commands_note if allowed else 'Command history unavailable for this source.')

    def _invalidate(self):
        self._epoch += 1
        self.evidence_json = ''
        # Clearing a job emits changed synchronously. This reset already owns
        # the source transition; reentering it would repeat guarded backend IO
        # while Qt is rendering the newly committed snapshot.
        with QSignalBlocker(self.job), QSignalBlocker(self.export_job):
            self.job.clear()
            self.export_job.clear()
        self.output.clear()

    def set_records(self, records, note):
        self.note.setText(note)
        if records == self.records:
            return
        current = self._selected_record()
        selected = current.command_id if current else None
        self.records = records
        rows = []
        for record in records:
            tone = C.green if record.state == 'CONFIRMED' else C.red if record.state in {'UNKNOWN', 'REJECTED'} else C.amber
            rows.append([(value, tone) for value in (record.requested_at, record.verb, record.state, record.command_id)])
        with QSignalBlocker(self.table.selectionModel()):
            self.table.set_rows(rows)
            if selected:
                for index, record in enumerate(records):
                    if record.command_id == selected:
                        self.table.selectRow(index)
                        break
        self.selected()

    def _selected_record(self):
        if not self.table.selectionModel().hasSelection():
            return None
        index = self.table.currentIndex().row()
        return self.records[index] if 0 <= index < len(self.records) else None

    def selected(self, *_):
        record = self._selected_record()
        selection = (record.command_id, record.serialized) if record else None
        if selection != self._selection:
            self._selection = selection
            self._invalidate()
        self.detail.set_evidence(record.serialized if record else '')
        self._render()

    def _job_changed(self):
        self._sync_source()
        self._render()

    def _render(self):
        record = self._selected_record()
        allowed = self._source is not None and self._source[2] and self._source[4] and self.isVisible()
        busy = self.job.active or self.export_job.active
        self.check_button.setEnabled(bool(allowed and record and record.state in {'REQUESTED', 'RUNNING', 'UNKNOWN'} and not busy))
        if self.job.result is not None:
            self.evidence_json = json.dumps(self.job.result, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
            self.output.set_evidence(self.evidence_json)
        exporter = getattr(self.provider.active, 'export_captured_evidence', None) if self.provider else None
        export_allowed = bool(allowed and self._source[3] and callable(exporter))
        self.export_button.setEnabled(bool(export_allowed and self.evidence_json and not busy))
        self.export_button.setToolTip('Export the captured observation to a new JSON file. '
                                     'The source revalidates export permission before writing.' if export_allowed else
                                     'Export unavailable: this source does not grant evidence export permission.')
        if self.job.error or self.export_job.error:
            self.status.setText('REFUSED · ' + (self.job.error or self.export_job.error))
        elif busy:
            self.status.setText('Reading / exporting / finishing one operation; no queued requests.')
        elif self.export_job.result:
            self.status.setText('EXPORTED captured evidence · ' + self.export_job.result)
        elif self.evidence_json:
            data = self.job.result['record']
            self.status.setText('UNRESOLVED · ' + data['comparison']['target_status'] + '\nObserved at ' + data['observed_at_utc'])
        else:
            self.status.setText('Select an unresolved command, then CHECK CURRENT STATE. The original receipt stays unchanged.')

    def check_current(self):
        self._sync_source()
        self._render()
        if not self.check_button.isEnabled():
            return
        record = self._selected_record()
        self._invalidate()
        try:
            selected = json.loads(record.serialized)
            if selected['record']['command_id'] != record.command_id:
                raise ValueError('selected receipt ID mismatch')
            self.job.submit(_read_reconciliation, self.provider.active,
                            record.command_id, selected['record_sha256'])
            self.tabs.setCurrentWidget(self.output)
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
            self.status.setText('REFUSED · ' + str(exc))

    def export_evidence(self):
        self._sync_source()
        self._render()
        if not self.export_button.isEnabled():
            return
        captured, epoch = self.evidence_json, self._epoch
        writer = self.provider.active.export_captured_evidence
        path, _ = QFileDialog.getSaveFileName(self, 'Export captured evidence (new file only)',
                                             'command-reconciliation.json', 'JSON (*.json)')
        self._sync_source()
        if not path or epoch != self._epoch or not self.isVisible():
            return
        self._render()
        if not self.export_button.isEnabled():
            return
        self.export_job.submit(_export_captured, captured, path, writer)

    def showEvent(self, event):
        self._sync_source()
        self._render()
        super().showEvent(event)

    def hideEvent(self, event):
        self._invalidate()
        self._render()
        super().hideEvent(event)
