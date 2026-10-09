"""Explicit in-memory campaign draft, no auto-run, file write or hidden defaults."""
import json
from dataclasses import asdict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from ..theme import C, mono
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import ScrollableTextEdit
from .prims import Btn, Prose, Section, hbox, vbox


class CampaignManifestEditor(AdaptiveDialog):
    def __init__(self, job, parent=None):
        super().__init__(parent)
        if job.active or not job.allowed or job.request is None:
            raise ValueError('load a manifest on an idle campaign source first')
        self.job, self.original, self.context = job, job.request, job._context()
        self.setWindowTitle('OrionQ · Edit campaign draft')
        self.resize(820, 620)
        self.setMinimumSize(560, 440)
        self.setStyleSheet(f'QDialog{{background:{C.raised};border:1px solid {C.line};}} QPlainTextEdit{{background:{C.void};color:{C.ink};border:1px solid {C.line};}}')
        layout = vbox(self, (18, 16, 18, 16), 10)
        layout.addWidget(Section('EDIT MANIFEST · SIMULATION ONLY', C.amber))
        layout.addWidget(Prose('Complete JSON draft · maximum 64 KiB. Apply updates the in-memory review only; it does not save a file or run trials.', 11))
        self.text = ScrollableTextEdit()
        self.text.setFont(mono(11))
        self.text.setAccessibleName('Complete campaign JSON draft')
        self.text.setPlainText(json.dumps(asdict(self.original), indent=2, ensure_ascii=False))
        layout.addWidget(self.text, 1)
        layout.addWidget(Prose('Choose a new campaign_id when changing a completed experiment. IDs, seeds and topology/version are never adjusted automatically.', 10))
        self.note = Prose('Cancel discards this draft. Applying requires a fresh review before RUN CAMPAIGN.', 10)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.setMaximumHeight(60)
        layout.addWidget(self.note)
        row = QWidget()
        buttons = hbox(row, (0, 0, 0, 0), 10)
        self.cancel_button = Btn('CANCEL DRAFT', C.muted)
        self.apply_button = Btn('VALIDATE / APPLY TO REVIEW', C.amber, filled=True)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.apply_button)
        layout.addWidget(row)
        self.cancel_button.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self.apply)
        job.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        available = (not self.job.active and self.job.allowed and self.job.request is self.original
                     and self.job._context() == self.context)
        self.apply_button.setEnabled(available)
        if not available:
            self.note.setText('Draft cannot be applied: source, loaded manifest or availability changed. Close and reopen after checking the source.')
            self.note.setToolTip(self.note.text())

    def apply(self):
        try:
            self.job.apply_manifest_draft(self.text.toPlainText(), expected_request=self.original,
                                          expected_context=self.context)
        except (ValueError, OSError, RuntimeError) as exc:
            self.note.setText(f'DRAFT REFUSED · {exc} · no draft applied.')
            self.note.setToolTip(self.note.text())
            return
        self.accept()
