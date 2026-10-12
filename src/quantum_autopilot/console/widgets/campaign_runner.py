"""Institutional-style, scrollable campaign review and comparison dialog."""
import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QHeaderView, QLayout, QLineEdit, QProgressBar, QTabWidget, QWidget

from ..theme import C, mono
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText
from .prims import Btn, Prose, Section, vbox, hbox, scroll_panel
from .records import RecordTable
from quantum_autopilot.control_plane.campaign_attempts import TERMINAL
from quantum_autopilot.worker_runtime import worker_supported


class CampaignRunner(AdaptiveDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.job = None
        self._request = None
        self._receipt = None
        self._history_entries = None
        self._history_context = None
        self._history_prefix = ""
        self._attempts = None
        self._attempt_filter_context = None
        self.setWindowTitle("OrionQ · Temporal campaigns")
        self.resize(920, 650)
        self.setMinimumSize(600, 440)
        self.setFont(mono(10))
        self.setStyleSheet(f"QDialog,QTabWidget::pane{{background:{C.raised};border:1px solid {C.line};}} QTabBar::tab{{background:{C.raised};color:{C.muted};padding:8px 16px;}} QTabBar::tab:selected{{color:{C.amber};}} QCheckBox{{color:{C.ink};}} QPlainTextEdit{{background:{C.void};color:{C.ink};border:0;}} QProgressBar{{color:{C.ink};border:1px solid {C.line};text-align:center;}} QProgressBar::chunk{{background:{C.amber};}}")
        root = vbox(self, (18, 16, 18, 16), 10)
        root.addWidget(Section("TEMPORAL CAMPAIGNS · SIMULATION ONLY", C.amber))
        root.addWidget(Prose("One frozen path · paired seeds · timeouts included. Assumed acquisition/T1/T2; no physical control or measured WDM validation.", 11))
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        review = QWidget()
        layout = vbox(review, (8, 8, 8, 8), 8)
        self.edit_manifest_button = Btn("EDIT MANIFEST · IN-MEMORY DRAFT", C.amber)
        layout.addWidget(self.edit_manifest_button)
        self.edit_manifest_button.clicked.connect(self.edit_manifest)
        self.save_manifest_button = Btn("SAVE MANIFEST AS · NEW JSON", C.green)
        self.save_manifest_button.setToolTip("Save the applied configuration, not an unapplied draft or scientific result. Existing files are never overwritten.")
        layout.addWidget(self.save_manifest_button)
        self.save_manifest_button.clicked.connect(self.choose_manifest_save)
        self.manifest = EvidenceText()
        self.manifest.setReadOnly(True)
        self.manifest.setFont(mono(11))
        layout.addWidget(self.manifest, 1)
        self.reviewed = QCheckBox("I reviewed all parameters and the simulated evidence scope.")
        layout.addWidget(self.reviewed)
        self.tabs.addTab(review, "MANIFEST REVIEW")
        results = QWidget()
        layout = vbox(results, (8, 8, 8, 8), 8)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.results = RecordTable(["REGIME", "TARGET / N", "P [95% WILSON]", "TIMEOUTS", "F | SWAP", "PAIRS / TRIAL"])
        self.results.setMinimumHeight(180)
        layout.addWidget(self.results, 2)
        layout.addWidget(Prose("F | SWAP is conditional/descriptive; different subsets may swap. Intervals are pointwise Monte Carlo uncertainty, not physical-model uncertainty.", 10))
        layout.addWidget(Section("PRIMARY OUTCOME · VS DECLARED BASELINE · McNEMAR + HOLM"))
        self.comparisons = RecordTable(["REGIME", "DIFFERENCE", "p HOLM", "REJECT AT 0.05"])
        self.comparisons.setMinimumHeight(150)
        for table in (self.results, self.comparisons):
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.comparisons, 1)
        self.provenance = EvidenceText()
        self.provenance.setFont(mono(9))
        self.provenance.setReadOnly(True)
        self.provenance.setMaximumHeight(115)
        layout.addWidget(self.provenance)
        self.results_scroll = scroll_panel(results, 120)
        self.tabs.addTab(self.results_scroll, "CONFIRMED COMPARISON")
        history = QWidget()
        layout = vbox(history, (8, 8, 8, 8), 8)
        layout.addWidget(Prose("Completed campaigns in the current source's journal. Index metadata is not verification; opening a record never reruns it.", 11))
        self.history_filter = QLineEdit()
        self.history_filter.setMaxLength(128)
        self.history_filter.setPlaceholderText("Campaign ID starts with... · case-sensitive · Enter / REFRESH to apply")
        self.history_filter.setAccessibleName("Campaign ID prefix")
        self.history_filter.setFont(mono(11))
        self.history_filter.setStyleSheet(f"QLineEdit{{background:{C.void};color:{C.ink};border:1px solid {C.line};padding:6px;}}")
        self.history_filter.setToolTip("Literal prefix, case-sensitive. Empty means all IDs. Editing does not submit a search; press Enter or REFRESH.")
        layout.addWidget(self.history_filter)
        self.history_table = RecordTable(["SEQ", "CAMPAIGN", "RECORD SHA256", "STATE"])
        layout.addWidget(self.history_table, 1)
        self.history_note = Prose("Refresh to load the journal index.", 10)
        self.history_note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.history_note)
        controls = QWidget()
        bar = hbox(controls, (0, 0, 0, 0), 8)
        self.history_refresh = Btn("REFRESH", C.amber)
        self.history_newer = Btn("NEWER", C.muted)
        self.history_older = Btn("OLDER", C.muted)
        self.history_open = Btn("OPEN / VERIFY", C.green)
        for button in (self.history_refresh, self.history_newer, self.history_older, self.history_open):
            bar.addWidget(button)
        layout.addWidget(controls)
        self.tabs.addTab(history, "SAVED CAMPAIGNS")
        self.history_refresh.clicked.connect(self.search_history)
        self.history_filter.returnPressed.connect(self.search_history)
        self.history_filter.textChanged.connect(self.render_presented)
        self.history_newer.clicked.connect(lambda: self._action(lambda: self.job.browse_history("newer")))
        self.history_older.clicked.connect(lambda: self._action(lambda: self.job.browse_history("older")))
        self.history_open.clicked.connect(lambda: self._action(lambda: self.job.open_history(self.history_table.currentIndex().row())))
        self.history_table.doubleClicked.connect(lambda index: self._action(lambda: self.job.open_history(index.row())) if self.history_open.isEnabled() else None)
        self.history_table.selectionModel().selectionChanged.connect(self.render_presented)
        attempts = QWidget()
        layout = vbox(attempts, (8, 8, 8, 8), 8)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.addWidget(Prose("Operational attempts · NOT quantum results. OPEN means no terminal record, not a running-process indicator.", 11))
        filters = QWidget()
        bar = hbox(filters, (0, 0, 0, 0), 8)
        self.attempt_filter = QLineEdit()
        self.attempt_filter.setMaxLength(128)
        self.attempt_filter.setPlaceholderText("Campaign ID starts with... · Enter / REFRESH")
        self.attempt_filter.setAccessibleName("Operational campaign ID prefix")
        self.attempt_filter.setFont(mono(11))
        self.attempt_filter.setStyleSheet(self.history_filter.styleSheet())
        self.attempt_filter.setToolTip("Literal, case-sensitive prefix. Empty means all IDs. Press Enter or REFRESH to apply both filters.")
        self.attempt_status = QComboBox()
        self.attempt_status.setAccessibleName("Operational status filter")
        self.attempt_status.setFont(mono(10))
        self.attempt_status.setStyleSheet(f"QComboBox{{background:{C.void};color:{C.ink};border:1px solid {C.line};padding:6px;}} QComboBox QAbstractItemView{{background:{C.raised};color:{C.ink};selection-background-color:{C.line};}}")
        self.attempt_status.addItem("ALL STATES", "")
        for status in ("OPEN", *sorted(TERMINAL)):
            self.attempt_status.addItem(status, status)
        bar.addWidget(self.attempt_filter, 1)
        bar.addWidget(self.attempt_status)
        layout.addWidget(filters)
        period = QWidget()
        bar = hbox(period, (0, 0, 0, 0), 8)
        self.attempt_from, self.attempt_before_date = QLineEdit(), QLineEdit()
        for field, label, short in ((self.attempt_from, "Requested UTC from (inclusive)", "UTC FROM ≥"),
                                    (self.attempt_before_date, "Requested UTC before (exclusive)", "UTC BEFORE <")):
            field.setMaxLength(10)
            field.setFont(mono(10))
            field.setStyleSheet(self.history_filter.styleSheet())
            field.setPlaceholderText("YYYY-MM-DD")
            field.setAccessibleName(label)
            field.setToolTip(label + ". YYYY-MM-DD; empty = unbounded. Enter / REFRESH applies all filters. Not completion date or local time.")
            group = QWidget()
            group_bar = hbox(group, (0, 0, 0, 0), 6)
            caption = Prose(short, 10)
            caption.setTextFormat(Qt.TextFormat.PlainText)
            caption.setToolTip(label)
            group_bar.addWidget(caption)
            group_bar.addWidget(field, 1)
            bar.addWidget(group, 1)
        layout.addWidget(period)
        self.attempt_table = RecordTable(["REQUESTED UTC", "CAMPAIGN", "STATUS", "ATTEMPT ID"])
        self.attempt_table.setMinimumHeight(150)
        layout.addWidget(self.attempt_table, 2)
        self.attempt_detail = EvidenceText()
        self.attempt_detail.setReadOnly(True)
        self.attempt_detail.setFont(mono(10))
        self.attempt_detail.setMinimumHeight(100)
        layout.addWidget(self.attempt_detail, 1)
        self.attempt_note = Prose("Refresh to load operational history.", 10)
        self.attempt_note.setTextFormat(Qt.TextFormat.PlainText)
        self.attempt_note.setMaximumHeight(64)
        layout.addWidget(self.attempt_note)
        controls = QWidget()
        bar = hbox(controls, (0, 0, 0, 0), 8)
        self.attempt_refresh, self.attempt_newer, self.attempt_older = Btn("REFRESH", C.amber), Btn("NEWER", C.muted), Btn("OLDER", C.muted)
        self.attempt_export = Btn("EXPORT ATTEMPT", C.green)
        for button in (self.attempt_refresh, self.attempt_newer, self.attempt_older, self.attempt_export):
            bar.addWidget(button)
        layout.addWidget(controls)
        self.attempts_scroll = scroll_panel(attempts, 120)
        self.attempt_page_export = Btn("EXPORT PAGE ZIP · CURRENT PAGE ONLY", C.green)
        layout.addWidget(self.attempt_page_export)
        self.attempt_page_export.clicked.connect(self.choose_attempt_page_export)
        self.attempt_receipt_open = Btn("OPEN LINKED RECEIPT · SELECTED ATTEMPT", C.green)
        self.attempt_receipt_open.setToolTip("Read and verify the recorded receipt association. No trials, import or attempt reconciliation.")
        layout.addWidget(self.attempt_receipt_open)
        self.attempt_receipt_open.clicked.connect(lambda: self._action(
            lambda: self.job.open_attempt_receipt(self.attempt_table.currentIndex().row())))
        self.tabs.addTab(self.attempts_scroll, "ATTEMPTS")
        self.attempt_refresh.clicked.connect(self.search_attempts)
        self.attempt_filter.returnPressed.connect(self.search_attempts)
        self.attempt_filter.textChanged.connect(self.render_presented)
        self.attempt_status.currentIndexChanged.connect(self.render_presented)
        for field in (self.attempt_from, self.attempt_before_date):
            field.returnPressed.connect(self.search_attempts)
            field.textChanged.connect(self.render_presented)
        self.attempt_newer.clicked.connect(lambda: self._action(lambda: self.job.browse_attempts("newer")))
        self.attempt_older.clicked.connect(lambda: self._action(lambda: self.job.browse_attempts("older")))
        self.attempt_export.clicked.connect(self.choose_attempt_export)
        self.attempt_table.selectionModel().selectionChanged.connect(self.show_attempt)
        self.attempt_table.selectionModel().selectionChanged.connect(self.render_presented)
        verification = QWidget()
        layout = vbox(verification, (8, 8, 8, 8), 8)
        layout.addWidget(Prose("External operational JSON / page ZIP · integrity only. No import, authentication or scientific validation.", 11))
        self.attempt_file_button = Btn("CHOOSE / VERIFY FILE", C.green)
        layout.addWidget(self.attempt_file_button)
        self.attempt_zip_button = Btn("CHOOSE / VERIFY PAGE ZIP", C.green)
        layout.addWidget(self.attempt_zip_button)
        self.attempt_zip_button.clicked.connect(self.choose_attempt_archive)
        self.attempt_file_view = EvidenceText()
        self.attempt_file_view.setReadOnly(True)
        self.attempt_file_view.setFont(mono(10))
        layout.addWidget(self.attempt_file_view, 1)
        self.tabs.addTab(verification, "VERIFY FILE")
        self.tabs.setUsesScrollButtons(True)
        self.attempt_file_button.clicked.connect(self.choose_attempt_file)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        root.addWidget(self.progress)
        row = QWidget()
        buttons = hbox(row, (0, 0, 0, 0), 8)
        self.load_button, self.run_button = Btn("LOAD MANIFEST", C.amber), Btn("RUN CAMPAIGN", C.amber, filled=True)
        self.cancel_button, self.export_button = Btn("CANCEL", C.red), Btn("EXPORT JSON", C.green)
        for button in (self.load_button, self.run_button, self.cancel_button, self.export_button):
            buttons.addWidget(button)
        root.addWidget(row)
        self.note = Prose("Campaign adapter not attached.", 10)
        self.note.setMinimumHeight(32)
        self.note.setMaximumHeight(64)
        self.note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.note)
        # Keep cancellation/export reachable while the campaign tabs scroll.
        self.fixed_footer = (self.progress, row, self.note)
        self.load_button.clicked.connect(self.choose_manifest)
        self.run_button.clicked.connect(self.submit)
        self.cancel_button.clicked.connect(lambda: self.job.cancel())
        self.export_button.clicked.connect(self.choose_export)
        self.reviewed.toggled.connect(self.render_presented)
        self.render_presented()

    def attach(self, job):
        self.job = job
        job.changed.connect(self.render_presented)
        job.refresh_source()
        self.render_presented()

    def _action(self, callback):
        try:
            callback()
        except (ValueError, OSError, RuntimeError) as exc:
            self.note.setText(f"REFUSED · {exc}")

    def choose_manifest(self):
        path, _ = QFileDialog.getOpenFileName(self, "Review a complete temporal campaign manifest", "", "Campaign JSON (*.json)")
        if path:
            self._action(lambda: self.job.load(path))
            self.tabs.setCurrentIndex(0)

    def edit_manifest(self):
        from .campaign_manifest_editor import CampaignManifestEditor
        if not self.job or self.job.active or not self.job.allowed or self.job.request is None:
            return
        editor = CampaignManifestEditor(self.job, self)
        editor.exec()
        editor.deleteLater()
        self.tabs.setCurrentIndex(0)

    def choose_manifest_save(self):
        if not self.job or self.job.active or not self.job.allowed or self.job.request is None:
            return
        request, context = self.job.request, self.job._context()
        path, _ = QFileDialog.getSaveFileName(self, "Save applied configuration (new file only, not a result)",
            "campaign-manifest.json", "Campaign JSON (*.json)")
        if path:
            self._action(lambda: self.job.save_manifest(path, expected_request=request, expected_context=context))

    def search_history(self):
        if self.job:
            self._action(lambda: self.job.browse_history(campaign_prefix=self.history_filter.text()))

    def show_attempt(self, *_):
        index = self.attempt_table.currentIndex().row()
        value = self.job.attempts[index] if self.job and 0 <= index < len(self.job.attempts) else None
        self.attempt_detail.set_evidence(json.dumps(value, indent=2, ensure_ascii=False) if value else "")

    def choose_attempt_export(self):
        index = self.attempt_table.currentIndex().row()
        if not self.attempt_filters_applied() or not 0 <= index < len(self.job.attempts):
            return
        selected, context = self.job.attempts[index], self.job._context()
        path, _ = QFileDialog.getSaveFileName(self, "Export operational attempt (not a scientific receipt)",
            f"attempt-{selected['record']['attempt_id']}.json", "Operational JSON (*.json)")
        if path:
            def submit():
                if (not self.attempt_filters_applied() or self.job._context() != context or not 0 <= index < len(self.job.attempts)
                        or self.job.attempts[index] != selected):
                    raise ValueError("source or selected attempt changed while choosing a file; select again")
                self.job.export_attempt(index, path)
            self._action(submit)

    def choose_attempt_page_export(self):
        if not self.attempt_filters_applied() or not self.job.attempts:
            return
        page, context = self.job.attempts, self.job._context()
        filters = (self.job.attempt_prefix, self.job.attempt_status, self.job.attempt_from, self.job.attempt_before_date)
        path, _ = QFileDialog.getSaveFileName(self, f"Export this page ({len(page)} operational attempts, not scientific results)",
                                            "operational-attempt-page.zip", "Operational ZIP (*.zip)")
        if path:
            def submit():
                current_filters = (self.job.attempt_prefix, self.job.attempt_status, self.job.attempt_from, self.job.attempt_before_date)
                if (not self.attempt_filters_applied() or self.job._context() != context
                        or self.job.attempts != page or current_filters != filters):
                    raise ValueError("source, page or filters changed while choosing a file; refresh and choose again")
                self.job.export_attempt_page(path)
            self._action(submit)

    def attempt_filters_applied(self):
        return self.job is not None and (self.attempt_filter.text(), self.attempt_status.currentData(),
            self.attempt_from.text(), self.attempt_before_date.text()) == (
            self.job.attempt_prefix, self.job.attempt_status, self.job.attempt_from, self.job.attempt_before_date)

    def search_attempts(self):
        if self.job:
            self._action(lambda: self.job.browse_attempts(campaign_prefix=self.attempt_filter.text(),
                status=self.attempt_status.currentData(), requested_from=self.attempt_from.text(),
                requested_before=self.attempt_before_date.text()))

    def choose_attempt_file(self):
        if not self.job:
            return
        context = self.job._context()
        path, _ = QFileDialog.getOpenFileName(self, "Verify operational snapshot (read only, max 32 KiB)", "", "Operational JSON (*.json)")
        if path:
            def submit():
                if self.job._context() != context:
                    raise ValueError("source changed while choosing a file; choose again")
                self.job.verify_attempt_file(path)
            self._action(submit)

    def choose_attempt_archive(self):
        if not self.job:
            return
        context = self.job._context()
        path, _ = QFileDialog.getOpenFileName(self, "Verify operational page ZIP (no extraction, max 2 MiB)", "", "Operational ZIP (*.zip)")
        if path:
            def submit():
                if self.job._context() != context:
                    raise ValueError("source changed while choosing a ZIP; choose again")
                self.job.verify_attempt_archive(path)
            self._action(submit)

    def submit(self):
        if self.job is None or not self.reviewed.isChecked():
            self.note.setText("REFUSED · review and confirm the complete manifest first.")
            return
        self._action(self.job.start)

    def choose_export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export confirmed receipt (new file only)", "campaign-receipt.json", "JSON (*.json)")
        if path:
            self._action(lambda: self.job.export(path))

    def refresh(self):
        """Explicitly renew declarations; painting callbacks use render_presented."""
        if self.job is not None:
            try:
                self.job.refresh_source()
            except PermissionError:
                self.job.invalidate_presentation_capabilities()
                raise
        self.render_presented()

    def render_presented(self, *_):
        job = self.job
        # Rendering only consumes the source's committed presentation frame.
        # Every action still calls the job's live, guarded authority checks.
        capabilities = None if job is None else job.presentation_capabilities
        allowed = (capabilities is not None and capabilities.allows("run_temporal_campaign")
                   and worker_supported())
        active = job is not None and job.active
        history_allowed = capabilities is not None and capabilities.allows("read_temporal_campaign_history")
        export_allowed = capabilities is not None and capabilities.allows("export_record")
        request = job.request if job else None
        if request is not self._request:
            self._request = request
            self.reviewed.setChecked(False)
        self.load_button.setEnabled(allowed and not active)
        self.edit_manifest_button.setEnabled(allowed and not active and request is not None)
        self.save_manifest_button.setEnabled(allowed and not active and request is not None)
        self.run_button.setEnabled(allowed and not active and request is not None and self.reviewed.isChecked() and not job.provider.busy)
        self.cancel_button.setEnabled(active and job.process is not None and not job.cancel_requested)
        self.export_button.setEnabled((allowed or history_allowed) and not active and job.receipt is not None and export_allowed)
        self.reviewed.setEnabled(allowed and not active and request is not None)
        if job:
            filters = (job.context, job.attempt_prefix, job.attempt_status, job.attempt_from, job.attempt_before_date)
            if filters != self._attempt_filter_context:
                self._attempt_filter_context = filters
                self.attempt_filter.blockSignals(True)
                self.attempt_status.blockSignals(True)
                self.attempt_filter.setText(job.attempt_prefix)
                self.attempt_status.setCurrentIndex(self.attempt_status.findData(job.attempt_status))
                self.attempt_filter.blockSignals(False)
                self.attempt_status.blockSignals(False)
                for field, value in ((self.attempt_from, job.attempt_from), (self.attempt_before_date, job.attempt_before_date)):
                    field.blockSignals(True)
                    field.setText(value)
                    field.blockSignals(False)
            if self.attempt_file_view.toPlainText() != job.attempt_file_text:
                self.attempt_file_view.set_evidence(job.attempt_file_text)
            if job.attempts is not self._attempts:
                self._attempts = job.attempts
                self.attempt_detail.clear()
                self.attempt_table.set_rows([[(str(item["record"][key]), C.amber if key == "status" else C.ink)
                    for key in ("requested_at_utc", "campaign_id", "status", "attempt_id")] for item in job.attempts])
            attempt_note = job.attempt_note if self.attempt_filters_applied() else "FILTERS NOT APPLIED · press Enter / REFRESH. Displayed rows belong to the previous query."
            self.attempt_note.setText(attempt_note)
            self.attempt_note.setToolTip(attempt_note)
            if job.context != self._history_context or job.history_prefix != self._history_prefix:
                self._history_context, self._history_prefix = job.context, job.history_prefix
                self.history_filter.setText(job.history_prefix)
            if job.history_entries is not self._history_entries:
                self._history_entries = job.history_entries
                self.history_table.set_rows(job.history_rows)
            self.history_note.setText(job.history_note)
            if job.receipt is not self._receipt:
                self._receipt = job.receipt
                if self._receipt is not None:
                    self.tabs.setCurrentIndex(1)
            if self.manifest.toPlainText() != job.review_text:
                self.manifest.set_evidence(job.review_text)
            self.results.set_rows(job.result_rows)
            self.comparisons.set_rows(job.comparison_rows)
            self.provenance.set_evidence(job.provenance_text)
            self.progress.setRange(0, max(1, job.total))
            self.progress.setValue(job.completed)
            self.progress.setFormat("%v / %m trials · confirmed receipt" if job.receipt else "%v / %m trials · receipt required")
            self.note.setText(job.note)
            self.note.setToolTip(job.note)
        self.history_refresh.setEnabled(history_allowed and not active)
        self.history_filter.setEnabled(history_allowed and not active)
        applied = job is not None and self.history_filter.text() == job.history_prefix
        self.history_newer.setEnabled(history_allowed and not active and applied and bool(job.history_back))
        self.history_older.setEnabled(history_allowed and not active and applied and job.history_next is not None)
        self.history_open.setEnabled(history_allowed and not active and applied and self.history_table.currentIndex().isValid())
        self.attempt_refresh.setEnabled(history_allowed and not active)
        self.attempt_filter.setEnabled(history_allowed and not active)
        self.attempt_status.setEnabled(history_allowed and not active)
        self.attempt_from.setEnabled(history_allowed and not active)
        self.attempt_before_date.setEnabled(history_allowed and not active)
        self.attempt_file_button.setEnabled(history_allowed and not active)
        self.attempt_zip_button.setEnabled(history_allowed and not active)
        applied_attempt = self.attempt_filters_applied()
        self.attempt_page_export.setEnabled(history_allowed and not active and applied_attempt and bool(job.attempts)
                                           and export_allowed)
        self.attempt_newer.setEnabled(history_allowed and not active and applied_attempt and bool(job.attempt_back))
        self.attempt_older.setEnabled(history_allowed and not active and applied_attempt and job.attempt_next is not None)
        self.attempt_export.setEnabled(history_allowed and not active and applied_attempt and self.attempt_table.currentIndex().isValid()
                                       and export_allowed)
        selected_index = self.attempt_table.currentIndex().row()
        has_receipt = (job is not None and 0 <= selected_index < len(job.attempts)
                       and job.attempts[selected_index]['record']['receipt_sha256'] is not None)
        self.attempt_receipt_open.setEnabled(history_allowed and not active and applied_attempt and has_receipt)

    def begin(self):
        if self.job and (self.job.allowed or self.job.history_allowed):
            self.show()
            self.raise_()

    def reject(self):
        self.hide()  # Hiding/Esc is not cancellation; job belongs to the shell.
