"""Opt-in allocation recommendations from artificial snapshots, never feedback."""
import json

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFileDialog, QWidget

from ...control_plane.contracts import NetworkStateSnapshot
from ...control_plane.optional_allocation import (
    MODEL_IDS, TELEMETRY_KEY, AllocationShadowWindow, evaluate_optional_allocation, export_allocation_shadow,
)
from ...control_plane.optional_sync import MAX_SNAPSHOT_BYTES, _json, _time
from ..model_archive_check import LocalModelJob
from ..theme import C, mono
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText
from .prims import Btn, Prose, Section, hbox, vbox

FRESHNESS_MS = 3500


def allocation_frame(snapshot):
    """Validate and detach a frame before it can enable START or refresh its age."""
    candidate = snapshot.optional_allocation_state if snapshot.input_kind == "synthetic" else None
    if not isinstance(candidate, NetworkStateSnapshot):
        raise ValueError("A typed artificial allocation observation is required.")
    text = _json(candidate.to_dict())
    if len(text.encode()) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Allocation snapshot exceeds 128 KiB.")
    incoming = NetworkStateSnapshot.from_dict(json.loads(text))
    window = AllocationShadowWindow.from_dict(incoming.telemetry.get(TELEMETRY_KEY))
    window.validate_context(incoming)
    if window.campaign_id != snapshot.campaign:
        raise ValueError("Allocation campaign differs from the selected source.")
    identity = (snapshot.input_kind, snapshot.capabilities.adapter_id, window.source_id, window.node_id,
                window.campaign_id, window.clock_id, window.stream_id, window.plant_sha256,
                _time(window.origin_timestamp_utc))
    return incoming, identity, _time(window.observation.timestamp_utc), text


def _evaluate(model_id, path, snapshot):
    return evaluate_optional_allocation(snapshot, model_id=model_id, archive_path=path)


class AllocationShadowSession(AdaptiveDialog):
    def __init__(self, model_id, parent=None):
        if model_id not in MODEL_IDS:
            raise ValueError("unsupported allocation checkpoint")
        super().__init__(parent)
        self.model_id = model_id
        self.package = self.latest = None
        self.context = self.window_context = self._last_stamp = self._last_json = None
        self.running = False
        self.input_error = ""
        self._export_epoch = 0
        self.job = LocalModelJob(self)
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(FRESHNESS_MS)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(self._expired)
        self.setWindowTitle("OrionQ · Allocation · Artificial shadow")
        self.resize(1000, 700)
        self.setMinimumSize(640, 480)
        self.setStyleSheet(f"QDialog {{background:{C.raised};}} QPlainTextEdit {{background:{C.void};color:{C.ink};border:1px solid {C.line};}}")
        layout = vbox(self, (18, 16, 18, 16), 10)
        layout.addWidget(Section("ALLOCATION · ARTIFICIAL SHADOW", C.amber))
        self.note = Prose("", 12, C.ink)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.note)
        self.output = EvidenceText()
        self.output.setReadOnly(True)
        self.output.setFont(mono(12))
        self.output.setAccessibleName("Classical admission recommendations versus persistence; no queue changes")
        layout.addWidget(self.output, 1)
        layout.addWidget(Prose("SIMULATED · Author's 11-link metro plant (HybridAllocationEnv, exported finite-key "
                               "plant) driven by quantum-first. Each update advances 5 s of virtual time. "
                               "The queue follows the incumbent, never recommendations. "
                               "Key pools follow the author's finite-key accounting; not delivered keys. "
                               "No throughput or control-gain claim. STOP clears results.", 11, C.amber))
        actions = QWidget()
        buttons = hbox(actions, (0, 0, 0, 0), 8)
        self.start_button = Btn("START SHADOW", C.amber)
        self.stop_button = Btn("STOP / CLEAR", C.muted)
        self.export_button = Btn("EXPORT FRAME", C.violet)
        self.close_button = Btn("CLOSE", C.muted)
        for button in (self.start_button, self.stop_button, self.export_button, self.close_button):
            buttons.addWidget(button, 1)
        layout.addWidget(actions)
        self.fixed_footer = (actions,)
        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.stop)
        self.export_button.clicked.connect(self._export)
        self.close_button.clicked.connect(self.reject)
        self.job.changed.connect(self._refresh)
        self._refresh()

    def set_package(self, package):
        if package.model_id != self.model_id or package.adapter_status != "offline_only" or self.job.active:
            raise ValueError("matching verified allocation checkpoint and idle worker required")
        self.stop()
        self.latest = self.context = self.window_context = self._last_stamp = self._last_json = None
        self.input_error = ""
        self.watchdog.stop()
        self.package = package
        self._refresh()

    def invalidate_package(self):
        self.package = self.latest = None
        self.watchdog.stop()
        self.stop()
        self.hide()

    def _unavailable(self, message):
        self.input_error = message
        self.latest = None
        self.watchdog.stop()
        self.stop()

    def accept_snapshot(self, snapshot):
        context = (snapshot.input_kind, snapshot.campaign, snapshot.capabilities.adapter_id)
        if self.context is not None and context != self.context:
            self.invalidate_package()
        self.context = context
        try:
            incoming, identity, stamp, text = allocation_frame(snapshot)
        except (ValueError, TypeError, OverflowError) as exc:
            self._unavailable(str(exc))
            return
        if self.window_context is not None and identity != self.window_context:
            self.invalidate_package()
        self.window_context = identity
        if self._last_stamp is not None and stamp <= self._last_stamp:
            if stamp == self._last_stamp and text == self._last_json:
                return  # A duplicate cannot renew freshness, even after timeout/dropout.
            self._unavailable("Reordered or altered duplicate observation; wait for a new sample.")
            return
        self._last_stamp, self._last_json = stamp, text
        self.latest = incoming
        self.input_error = ""
        self.watchdog.start()
        # Clearing a busy job invalidates its result; never enqueue a second job.
        self.job.clear()
        if self.running and self.package is not None and not self.job.active:
            self.job.submit(_evaluate, self.model_id, self.package.path, incoming)
        self._refresh()

    def start(self):
        if not self.start_button.isEnabled():
            return
        self._export_epoch += 1
        self.running = True
        self.job.submit(_evaluate, self.model_id, self.package.path, self.latest)

    def stop(self):
        self.running = False
        self._export_epoch += 1
        self.job.clear()

    def _expired(self):
        self._unavailable("No new sample for 3.5 s. Wait for fresh input and select START again.")

    def hideEvent(self, event):
        self.package = self.latest = self._last_stamp = self._last_json = self.window_context = None
        self.watchdog.stop()
        self.stop()
        super().hideEvent(event)

    def _refresh(self):
        result = self.job.result
        if self.running and (self.job.error or (result is not None and result.status != "evaluated")):
            self.running = False
            self._export_epoch += 1
        ready = self.package is not None and self.latest is not None
        self.start_button.setEnabled(ready and not self.running and not self.job.active)
        self.stop_button.setEnabled(self.running or self.job.active or result is not None)
        self.export_button.setEnabled(ready and result is not None and result.status == "evaluated" and not self.job.active)
        self.note.setText("Package not selected; reopen through the verified catalog." if self.package is None else
                          f"{self.model_id} · {'RUNNING' if self.running else 'OFF'}\n" +
                          (f"Observation: {self.latest.timestamp_utc}" if self.latest else "INPUT UNAVAILABLE"))
        if result is not None and result.evaluation_json:
            evaluation = json.loads(result.evaluation_json)
            selected, baseline = evaluation["recommendation"], evaluation["persistence"]
            rows = "\n".join(f"{name}: {a:.2f} / {b:.2f}" for name, a, b in
                             zip(selected["link_names"], selected["admission"], baseline["admission"]))
            self.output.set_evidence(
                f"Forecast status: {result.final_output_status}\n"
                f"Past drift samples: {evaluation['history_samples']}/5 + current observation\n"
                f"Estimated effect after 30 s: {result.effective_timestamp_utc}\n"
                "Forecast horizon: 150 s. No action is scheduled.\n\n"
                "CLASSICAL ADMISSION FRACTION · SELECTED / PERSISTENCE\n" + rows + "\n\n"
                f"Different recommendations: {len(evaluation['differing_admission_links'])}/11 links.\n"
                "Differences are not performance gains. Persistence and warm-up are not ML.\n"
                "Export includes input, supplied queue, per-link forecasts and artifact hashes.")
        elif result is not None:
            self.output.set_evidence(f"REFUSED · {result.reason}\n{result.detail}")
        else:
            self.output.set_evidence(f"REFUSED · {self.job.error}" if self.job.error else self.input_error or
                                     ("RUNNING / FINISHING · no queued frames" if self.job.active else
                                      "No result. Select START SHADOW explicitly; no policy or queue is changed."))

    def _export(self):
        if not self.export_button.isEnabled():
            return
        result, package, epoch = self.job.result, self.package, self._export_epoch
        path, _ = QFileDialog.getSaveFileName(self, "Export selected frame (new file only)",
                                              "allocation-shadow.json", "JSON (*.json)")
        if not path or package is not self.package or epoch != self._export_epoch or not self.isVisible():
            return
        try:
            export_allocation_shadow(result, path)
            self.output.appendPlainText("\nEXPORTED selected frame · " + path)
        except (ValueError, OSError) as exc:
            self.output.appendPlainText("\nEXPORT REFUSED · " + str(exc))
