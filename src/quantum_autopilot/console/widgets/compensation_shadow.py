"""Explicit polarisation shadow view, separate from timing and every control policy."""
import json

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFileDialog, QWidget

from ...control_plane.contracts import NetworkStateSnapshot
from ...control_plane.compensation_temporal import TemporalCompensationEvaluation, export_compensation_temporal_report
from ...control_plane.optional_compensation import MODEL_IDS, evaluate_optional_compensation, export_compensation_shadow
from ..model_archive_check import LocalModelJob
from ..theme import C, mono
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText
from .prims import Btn, Prose, Section, hbox, vbox


def _evaluate(model_id, path, snapshot):
    return evaluate_optional_compensation(snapshot, model_id=model_id, archive_path=path)


class CompensationShadowSession(AdaptiveDialog):
    def __init__(self, model_id, parent=None):
        if model_id not in MODEL_IDS:
            raise ValueError("unsupported compensation checkpoint")
        super().__init__(parent)
        self.model_id = model_id
        self.package = self.latest = None
        self.context = self.window_context = self._last_json = None
        self.running = False
        self._export_epoch = 0
        self.temporal = TemporalCompensationEvaluation()
        self.temporal_error = ""
        self._registered_result = None
        self.job = LocalModelJob(self)
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(3500)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(self._expired)
        self.setWindowTitle("OrionQ · Polarisation · Artificial shadow batches")
        self.resize(1000, 700)
        self.setMinimumSize(640, 480)
        self.setStyleSheet(f"QDialog {{background:{C.raised};}} QPlainTextEdit {{background:{C.void};color:{C.ink};border:1px solid {C.line};}}")
        layout = vbox(self, (18, 16, 18, 16), 10)
        layout.addWidget(Section("POLARISATION · ARTIFICIAL SHADOW", C.amber))
        self.note = Prose("", 12, C.ink)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.note)
        self.metrics = Prose("", 11, C.green)
        self.metrics.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.metrics)
        self.output = EvidenceText()
        self.output.setReadOnly(True)
        self.output.setFont(mono(12))
        self.output.setAccessibleName("SO(3) predicted rotation and persistence; no actuator")
        layout.addWidget(self.output, 1)
        layout.addWidget(Prose("SIMULATED · 16 samples/batch, 2 ms virtual spacing, +40 ms horizon. "
                               "Independent clock: 32 ms per display batch, NOT 500 Hz real-time control. "
                               "References arrive later for descriptive angular scoring, never inference. "
                               "No Bell-fidelity or control-gain claim. STOP clears results.", 11, C.amber))
        actions = QWidget()
        buttons = hbox(actions, (0, 0, 0, 0), 8)
        self.start_button = Btn("START SHADOW", C.amber)
        self.stop_button = Btn("STOP / CLEAR", C.muted)
        self.export_button = Btn("EXPORT BATCH", C.violet)
        self.close_button = Btn("CLOSE", C.muted)
        for button in (self.start_button, self.stop_button, self.export_button, self.close_button):
            buttons.addWidget(button, 1)
        layout.addWidget(actions)
        self.temporal_export_button = Btn("EXPORT TEMPORAL REPORT", C.violet)
        layout.addWidget(self.temporal_export_button)
        self.fixed_footer = (actions, self.temporal_export_button)
        self.temporal_export_button.clicked.connect(self._export_temporal)
        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.stop)
        self.export_button.clicked.connect(self._export)
        self.close_button.clicked.connect(self.reject)
        self.job.changed.connect(self._refresh)
        self._refresh()

    def set_package(self, package):
        if package.model_id != self.model_id or package.adapter_status != "offline_only" or self.job.active:
            raise ValueError("matching verified checkpoint and idle worker required")
        self.stop()
        self.latest = self.context = self.window_context = self._last_json = None
        self.watchdog.stop()
        self.package = package
        self._refresh()

    def invalidate_package(self):
        self.package = self.latest = None
        self.watchdog.stop()
        self.stop()
        self.hide()

    def accept_snapshot(self, snapshot):
        context = (snapshot.input_kind, snapshot.campaign, snapshot.capabilities.adapter_id)
        if self.context is not None and context != self.context:
            self.invalidate_package()
        self.context = context
        candidates = [item for item in snapshot.optional_compensation_states
                      if isinstance(item, NetworkStateSnapshot)
                      and item.metadata.get("compensation_operating_point") == self.model_id.split(".")[2]
                      ] if snapshot.input_kind == "synthetic" else []
        if len(candidates) != 1:
            self.latest = None
            self.watchdog.stop()
            self.stop()
            return
        incoming = NetworkStateSnapshot.from_dict(json.loads(candidates[0].to_json()))
        identity = (incoming.node_id, incoming.provenance.source,
                    *(incoming.metadata.get(key) for key in ("campaign_id", "clock_id", "rotation_frame_id",
                                                            "rotation_convention", "compensation_operating_point")))
        if self.window_context is not None and identity != self.window_context:
            self.invalidate_package()
        self.window_context = identity
        text = incoming.to_json()
        if text == self._last_json:
            return
        self._last_json = text
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
            self.job.submit(_evaluate, self.model_id, self.package.path, incoming)
        self._refresh()

    def start(self):
        if not self.start_button.isEnabled():
            return
        self._export_epoch += 1
        self.temporal = TemporalCompensationEvaluation()
        self.temporal_error = ""
        self._registered_result = None
        try:
            self.temporal.observe(self.latest)
        except ValueError as exc:
            self.temporal_error = str(exc)
            self._refresh()
            return
        self.running = True
        self.job.submit(_evaluate, self.model_id, self.package.path, self.latest)

    def stop(self):
        self.running = False
        self._export_epoch += 1
        self.temporal = TemporalCompensationEvaluation()
        self.temporal_error = ""
        self._registered_result = None
        self.job.clear()

    def _expired(self):
        self.latest = None
        self.stop()

    def hideEvent(self, event):
        self.package = self.latest = self._last_json = None
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
        report = self.temporal.report(include_records=False)
        metrics = (f"Paired: {report['paired']} · Pending: {report['pending']} · Missed: {report['missed_target']}\n"
                   f"Batches: {report['observed_batches']} · Committed: {report['registered_predictions']} / 256\n")
        metrics += (f"Mean angle: model {report['model_mean_angle_rad'] * 1000:.5g} / persistence "
                    f"{report['persistence_mean_angle_rad'] * 1000:.5g} mrad · descriptive only" if report['paired'] else
                    "Angular errors unavailable until a later exact-target sample arrives.")
        self.metrics.setText(metrics + ("\nTEMPORAL REFUSED · " + self.temporal_error if self.temporal_error else ""))
        ready = self.package is not None and self.latest is not None
        self.temporal_export_button.setEnabled(self.package is not None and report["registered_predictions"] > 0)
        self.start_button.setEnabled(ready and not self.running and not self.job.active)
        self.stop_button.setEnabled(self.running or self.job.active or result is not None or report["registered_predictions"] > 0)
        self.export_button.setEnabled(ready and result is not None and not self.job.active)
        self.note.setText("Package not selected; reopen through the verified catalog." if self.package is None else
                          f"{self.model_id} · {'RUNNING' if self.running else 'OFF'}\nPackage SHA-256: {self.package.sha256}\n"
                          + (f"Input: {self.latest.request_id}" if self.latest else
                             "INPUT UNAVAILABLE · A unique compatible artificial polarisation batch is required."))
        if result is not None:
            summary = ""
            if result.evaluation_json:
                evaluation = json.loads(result.evaluation_json)
                last = evaluation["steps"][-1]
                def matrix(rows):
                    return "\n".join("  " + " ".join(f"{value: .6f}" for value in row) for row in rows)
                summary = (f"Final status: {result.final_output_status} · Target: {result.target_timestamp_utc}\n"
                           f"Predicted SO(3):\n{matrix(last['predicted'])}\nPersistence:\n{matrix(last['persistence'])}\n"
                           "This forecast has no future reference. See temporal metrics for settled older forecasts.\n"
                           "First 15 steps are persistence, not learned forecasts.\n\n")
            self.output.set_evidence(summary + result.serialized())
        else:
            self.output.set_evidence(f"REFUSED · {self.job.error}" if self.job.error else
                                     "RUNNING / FINISHING · no queued batches" if self.job.active else
                                     "No result. Select START SHADOW explicitly; no policy or actuator is enabled.")

    def _export(self):
        if not self.export_button.isEnabled():
            return
        result, package, epoch = self.job.result, self.package, self._export_epoch
        path, _ = QFileDialog.getSaveFileName(self, "Export selected completed batch (new file only)",
                                              "compensation-shadow.json", "JSON (*.json)")
        if not path or package is not self.package or epoch != self._export_epoch or not self.isVisible():
            return
        try:
            export_compensation_shadow(result, path)
            self.output.appendPlainText("\nEXPORTED selected batch · " + result.snapshot_sha256 + " · " + path)
        except (ValueError, OSError) as exc:
            self.output.appendPlainText("\nEXPORT REFUSED · " + str(exc))

    def _export_temporal(self):
        if not self.temporal_export_button.isEnabled():
            return
        text, epoch, package = self.temporal.serialized(), self._export_epoch, self.package
        path, _ = QFileDialog.getSaveFileName(self, "Export captured temporal report (new file only)",
                                              "compensation-temporal.json", "JSON (*.json)")
        if not path or epoch != self._export_epoch or package is not self.package or not self.isVisible():
            return
        try:
            export_compensation_temporal_report(text, path)
            self.output.appendPlainText("\nEXPORTED captured temporal report · " + path)
        except (ValueError, OSError) as exc:
            self.output.appendPlainText("\nEXPORT REFUSED · " + str(exc))
