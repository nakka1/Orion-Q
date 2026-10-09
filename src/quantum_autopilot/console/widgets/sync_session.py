"""Explicit simulated synchronization session, separate from every runtime policy."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QSplitter, QWidget

from ...model_intake.sync_session import MODEL_ID, demo_sync_input, evaluate_sync, export_sync_evaluation, load_sync_input
from ..model_archive_check import LocalModelJob
from ..theme import C, mono
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText, ScrollableTextEdit
from .prims import Btn, Prose, Section, hbox, vbox


class SyncSession(AdaptiveDialog):
    # Reusable bounded session shell; subclasses must supply their own typed
    # evaluator and scientific wording, never reinterpret synchronization data.
    model_id = MODEL_ID
    window_title = "OrionQ · QKD synchronization · Offline evaluation"
    heading = "QKD SYNCHRONIZATION · OFFLINE SESSION"
    input_description = "Simulated synchronization input JSON"
    output_description = "Offline prediction and persistence comparison"
    scope = ("SIMULATION ONLY · 8 samples at 1 s; horizon +2 s. "
             "Reference is optional and never fed to the model. No Bell-fidelity or control claim.")
    export_filename = "sync-evaluation.json"
    input_limit = "64 KiB"

    @staticmethod
    def _demo_input():
        return demo_sync_input()

    @staticmethod
    def _evaluate(path, text):
        return evaluate_sync(path, text)

    @staticmethod
    def _load_input(path):
        return load_sync_input(path)

    @staticmethod
    def _export_result(result, path):
        return export_sync_evaluation(result, path)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.package = None
        self.job = LocalModelJob(self)
        self.setWindowTitle(self.window_title)
        self.resize(1060, 760)
        self.setMinimumSize(640, 480)
        self.setStyleSheet(f"QDialog {{background:{C.raised};}} QPlainTextEdit {{background:{C.void};color:{C.ink};border:1px solid {C.line};}}")
        layout = vbox(self, (18, 16, 18, 16), 10)
        layout.addWidget(Section(self.heading, C.amber))
        self.note = Prose("", 12, C.ink)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.note)
        split = QSplitter(Qt.Orientation.Vertical)
        split.setChildrenCollapsible(False)
        self.editor = ScrollableTextEdit()
        self.editor.setFont(mono(12))
        self.editor.setMinimumHeight(100)
        self.editor.setAccessibleName(self.input_description)
        self.editor.setPlaceholderText("LOAD JSON or DEMO, review all inputs, then RUN. Nothing runs automatically.")
        self.output = EvidenceText()
        self.output.setReadOnly(True)
        self.output.setFont(mono(12))
        self.output.setMinimumHeight(100)
        self.output.setAccessibleName(self.output_description)
        split.addWidget(self.editor)
        split.addWidget(self.output)
        layout.addWidget(split, 1)
        layout.addWidget(Prose(self.scope, 11, C.amber))
        actions = QWidget()
        buttons = hbox(actions, (0, 0, 0, 0), 8)
        self.load_button = Btn("LOAD JSON", C.violet)
        self.demo_button = Btn("DEMO", C.muted)
        self.run_button = Btn("RUN", C.amber)
        self.export_button = Btn("EXPORT", C.muted)
        self.close_button = Btn("CLOSE", C.muted)
        for button in (self.load_button, self.demo_button, self.run_button, self.export_button, self.close_button):
            buttons.addWidget(button, 1)
        layout.addWidget(actions)
        self.fixed_footer = (actions,)
        self.load_button.clicked.connect(self._load)
        self.demo_button.clicked.connect(lambda: self.editor.setPlainText(self._demo_input()))
        self.run_button.clicked.connect(self._run)
        self.export_button.clicked.connect(self._export)
        self.close_button.clicked.connect(self.reject)
        self.editor.textChanged.connect(self.job.clear)
        self.job.changed.connect(self._refresh)
        self._refresh()

    def set_package(self, package):
        if package.model_id != self.model_id or package.adapter_status != "offline_only" or self.job.active:
            raise ValueError("session requires its matching reviewed model package and an idle worker")
        self.package = package
        self.editor.clear()
        self.job.clear()

    def invalidate_package(self):
        self.package = None
        self.job.clear()
        self.editor.clear()
        self.hide()

    def hideEvent(self, event):
        self.package = None
        self.job.clear()
        super().hideEvent(event)

    def _refresh(self):
        ready = self.package is not None and not self.job.active
        self.load_button.setEnabled(ready)
        self.demo_button.setEnabled(ready)
        self.run_button.setEnabled(ready and bool(self.editor.toPlainText().strip()))
        self.export_button.setEnabled(ready and self.job.result is not None)
        self.note.setText("Package not selected; reopen through the verified catalog." if self.package is None else
                          f"{self.model_id}\nPackage SHA-256: {self.package.sha256}\nRechecked on every RUN; no installation or policy activation.")
        self.output.set_evidence("RUNNING / FINISHING · edited or invalidated input discards the result." if self.job.active else
                                 f"REFUSED · {self.job.error}" if self.job.error else
                                 self.job.result.describe() if self.job.result is not None else
                                 "No evaluation result. Review the simulated input and explicitly select RUN.")

    def _run(self):
        if self.package is not None and not self.job.active:
            self.job.submit(self._evaluate, self.package.path, self.editor.toPlainText())

    def _load(self):
        if self.package is None or self.job.active:
            return
        package, generation = self.package, self.job.generation
        path, _ = QFileDialog.getOpenFileName(self, f"{self.input_description} (max {self.input_limit})", "",
                                              "JSON (*.json)")
        if not path or self.package is not package or self.job.generation != generation or not self.isVisible():
            return
        self.job.clear()
        self.editor.clear()
        try:
            self.editor.setPlainText(self._load_input(path))
        except (ValueError, OSError, UnicodeError, RecursionError) as exc:
            self.job.error = str(exc)
            self._refresh()

    def _export(self):
        result = self.job.result
        if result is None or self.job.active or self.package is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save offline evaluation (new file only)", self.export_filename, "JSON (*.json)")
        if not path or self.job.result is not result or self.package is None or not self.isVisible():
            return
        try:
            self._export_result(result, path)
            self.output.set_evidence(result.describe() + "\n\nEXPORTED · " + path)
        except (ValueError, OSError) as exc:
            self.output.set_evidence(result.describe() + "\n\nEXPORT REFUSED · " + str(exc))
