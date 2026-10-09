"""Opt-in live artificial timing display. No command signals or policy binding."""
import json

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFileDialog, QWidget

from ...control_plane.contracts import NetworkStateSnapshot
from ...control_plane.optional_sync import MODEL_ID, evaluate_optional_sync, export_sync_shadow
from ...control_plane.sync_temporal import TemporalSyncEvaluation, export_temporal_report
from ..model_archive_check import LocalModelJob
from ..theme import C, mono
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText
from .prims import Btn, Prose, Section, hbox, vbox


def _evaluate(path, snapshot):
    return evaluate_optional_sync(snapshot, model_id=MODEL_ID, archive_path=path)


class SyncShadowSession(AdaptiveDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.package = self.latest = None
        self.context = None
        self.running = False
        self._export_epoch = 0
        self._last_input_json = None
        self.temporal = TemporalSyncEvaluation()
        self.temporal_error = ""
        self._registered_result = None
        self.job = LocalModelJob(self)
        self.watchdog = QTimer(self)
        self.watchdog.setSingleShot(True)
        self.watchdog.setInterval(3500)
        self.watchdog.timeout.connect(self._expired)
        self.setWindowTitle("OrionQ · Synchronization · Artificial shadow stream")
        self.resize(1000, 700)
        self.setMinimumSize(640, 480)
        self.setStyleSheet(f"QDialog {{background:{C.raised};}} QPlainTextEdit {{background:{C.void};color:{C.ink};border:1px solid {C.line};}}")
        layout = vbox(self, (18, 16, 18, 16), 10)
        layout.addWidget(Section("SYNCHRONIZATION · ARTIFICIAL SHADOW", C.amber))
        self.note = Prose("", 12, C.ink)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.note)
        self.metrics = Prose("", 11, C.green)
        self.metrics.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.metrics)
        self.output = EvidenceText()
        self.output.setReadOnly(True)
        self.output.setFont(mono(12))
        layout.addWidget(self.output, 1)
        layout.addWidget(Prose("SIMULATED INPUT · 1 s virtual cadence / +2 s forecast. "
                               "Offset is not Bell fidelity. Future samples score only committed forecasts, never inference. "
                               "STOP or input loss discards results; export a completed frame explicitly.", 11, C.amber))
        actions = QWidget()
        buttons = hbox(actions, (0, 0, 0, 0), 8)
        self.start_button = Btn("START SHADOW", C.amber)
        self.stop_button = Btn("STOP / CLEAR", C.muted)
        self.export_button = Btn("EXPORT FRAME", C.violet)
        self.close_button = Btn("CLOSE", C.muted)
        for button in (self.start_button, self.stop_button, self.export_button, self.close_button):
            buttons.addWidget(button, 1)
        layout.addWidget(actions)
        self.temporal_export_button = Btn("EXPORT TEMPORAL REPORT", C.violet)
        self.temporal_export_button.clicked.connect(self._export_temporal)
        layout.addWidget(self.temporal_export_button)
        self.fixed_footer = (actions, self.temporal_export_button)
        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.stop)
        self.export_button.clicked.connect(self._export)
        self.close_button.clicked.connect(self.reject)
        self.job.changed.connect(self._refresh)
        self._refresh()

    def set_package(self, package):
        if package.model_id != MODEL_ID or package.adapter_status != "offline_only" or self.job.active:
            raise ValueError("matching verified synchronizer package and idle worker required")
        self.stop()
        self.latest = self.context = self._last_input_json = None
        self.watchdog.stop()
        self.package = package
        self._refresh()

    def invalidate_package(self):
        self.package = None
        self.latest = None
        self.watchdog.stop()
        self.stop()
        self.hide()

    def accept_snapshot(self, snapshot):
        context = (snapshot.input_kind, snapshot.campaign, snapshot.capabilities.adapter_id)
        if self.context is not None and context != self.context:
            self.invalidate_package()
            self._last_input_json = None
        self.context = context
        candidate = snapshot.optional_sync_state if snapshot.input_kind == "synthetic" else None
        if candidate is None:
            self.latest = None
            self.watchdog.stop()
            self.stop()
            return
        # Detach the producer's nested dictionaries; changed inputs invalidate workers.
        input_json = candidate.to_json()
        incoming = NetworkStateSnapshot.from_dict(json.loads(input_json))
        if input_json == self._last_input_json:
            return  # Repainting the same frame must not renew its freshness.
        self._last_input_json = input_json
        self.latest = incoming
        self.watchdog.start()
        if self.running:
            try:
                self.temporal.observe(incoming)
            except ValueError as exc:
                self.temporal_error = str(exc)
                self.running = False
                self._export_epoch += 1
        self.job.clear()
        if self.running and self.package is not None and not self.job.active:
            self.job.submit(_evaluate, self.package.path, self.latest)
        self._refresh()

    def start(self):
        if not self.start_button.isEnabled():
            return
        self._export_epoch += 1
        self.temporal = TemporalSyncEvaluation()
        self.temporal_error = ""
        self._registered_result = None
        try:
            self.temporal.observe(self.latest)
        except ValueError as exc:
            self.temporal_error = str(exc)
            self._refresh()
            return
        self.running = True
        self.job.submit(_evaluate, self.package.path, self.latest)

    def stop(self):
        self.running = False
        self._export_epoch += 1
        self.temporal = TemporalSyncEvaluation()
        self.temporal_error = ""
        self._registered_result = None
        self.job.clear()

    def _expired(self):
        self.latest = None
        self.stop()

    def hideEvent(self, event):
        self.package = self.latest = None
        self._last_input_json = None
        self.watchdog.stop()
        self.stop()
        super().hideEvent(event)

    def _refresh(self):
        result = self.job.result
        if self.running and result is not None and result is not self._registered_result:
            self._registered_result = result
            if result.status == "evaluated":
                try:
                    self.temporal.register(result)
                except ValueError as exc:
                    self.temporal_error = str(exc)
                    self.running = False
        if self.job.error or (result is not None and result.status != "evaluated"):
            self.running = False
        summary = self.temporal.report(include_records=False)
        paired = summary["paired"]
        metrics = (f"Paired: {paired} · Pending: {summary['pending']} · Missed target: {summary['missed_target']}\n"
                   f"Frames: {summary['observed_frames']} · Committed forecasts: {summary['registered_predictions']} / 256\n")
        metrics += (f"MAE: model {summary['model_mae_s'] * 1e9:.5g} ns / persistence "
                    f"{summary['persistence_mae_s'] * 1e9:.5g} ns · descriptive only" if paired else
                    "MAE unavailable until a later exact-target sample arrives.")
        self.metrics.setText(metrics + ("\nTEMPORAL REFUSED · " + self.temporal_error if self.temporal_error else ""))
        ready = self.package is not None and self.latest is not None
        self.temporal_export_button.setEnabled(self.package is not None and summary["registered_predictions"] > 0)
        self.start_button.setEnabled(ready and not self.running and not self.job.active)
        self.stop_button.setEnabled(self.running or self.job.active or result is not None
                                    or summary["registered_predictions"] > 0)
        self.export_button.setEnabled(ready and result is not None and not self.job.active)
        self.note.setText("Package not selected; reopen through the verified catalog." if self.package is None else
                          f"{MODEL_ID} · {'RUNNING' if self.running else 'OFF'}\n"
                          f"Package SHA-256: {self.package.sha256}\n"
                          + (f"Input: {self.latest.request_id}" if self.latest else
                             "INPUT UNAVAILABLE · Select artificial source; wait for eight consecutive frames."))
        if result is not None:
            evaluation = result.evaluation
            headline = (f"Predicted offset: {evaluation.predicted_offset_s * 1e9:.6g} ns\n"
                        f"Persistence: {evaluation.persistence_offset_s * 1e9:.6g} ns\n"
                        f"Target UTC: {result.target_timestamp_utc}\n" if evaluation else "")
            self.output.set_evidence(headline + "\n" + result.serialized())
        else:
            self.output.set_evidence(f"REFUSED · {self.job.error}" if self.job.error else
                                     "RUNNING / FINISHING · no queued frames" if self.job.active else
                                     "No result. Select START SHADOW explicitly; runtime policy remains unchanged.")

    def _export(self):
        result, package = self.job.result, self.package
        if not self.export_button.isEnabled():
            return
        epoch = self._export_epoch
        path, _ = QFileDialog.getSaveFileName(self, "Export selected completed frame (new file only)",
                                              "sync-shadow.json", "JSON (*.json)")
        # New frames may arrive while choosing a path. Export the immutable frame
        # selected on click, but invalidate on stop/loss/source/package changes.
        if (not path or epoch != self._export_epoch or package is not self.package
                or not self.isVisible()):
            return
        try:
            export_sync_shadow(result, path)
            self.output.appendPlainText("\nEXPORTED selected frame · " + result.snapshot_sha256 + " · " + path)
        except (ValueError, OSError) as exc:
            self.output.appendPlainText("\nEXPORT REFUSED · " + str(exc))

    def _export_temporal(self):
        if not self.temporal_export_button.isEnabled():
            return
        text, epoch, package = self.temporal.serialized(), self._export_epoch, self.package
        path, _ = QFileDialog.getSaveFileName(self, "Export captured temporal report (new file only)",
                                              "sync-temporal.json", "JSON (*.json)")
        if not path or epoch != self._export_epoch or package is not self.package or not self.isVisible():
            return
        try:
            export_temporal_report(text, path)
            self.output.appendPlainText("\nEXPORTED captured temporal report · " + path)
        except (ValueError, OSError) as exc:
            self.output.appendPlainText("\nEXPORT REFUSED · " + str(exc))
