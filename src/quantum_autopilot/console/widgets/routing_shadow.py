"""Opt-in routing decisions on the author's repeater environment, never feedback."""
import json

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFileDialog, QWidget

from ...control_plane.contracts import NetworkStateSnapshot
from ...control_plane.optional_routing import (
    MODEL_IDS, TELEMETRY_KEY, RoutingShadowWindow, evaluate_optional_routing, export_routing_shadow,
)
from ...control_plane.optional_sync import MAX_SNAPSHOT_BYTES, _json, _time
from ..model_archive_check import LocalModelJob
from ..theme import C, mono
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText
from .prims import Btn, Prose, Section, hbox, vbox

FRESHNESS_MS = 3500


def routing_frame(snapshot):
    """Validate and detach a frame before it can enable START or refresh its age."""
    candidate = snapshot.optional_routing_state if snapshot.input_kind == "synthetic" else None
    if not isinstance(candidate, NetworkStateSnapshot):
        raise ValueError("A typed artificial routing observation is required.")
    text = _json(candidate.to_dict())
    if len(text.encode()) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Routing snapshot exceeds 128 KiB.")
    incoming = NetworkStateSnapshot.from_dict(json.loads(text))
    window = RoutingShadowWindow.from_dict(incoming.telemetry.get(TELEMETRY_KEY))
    window.validate_context(incoming)
    value = window.to_dict()
    if value["campaign_id"] != snapshot.campaign:
        raise ValueError("Routing campaign differs from the selected source.")
    identity = (snapshot.input_kind, snapshot.capabilities.adapter_id,
                *(value[key] for key in ("source_id", "node_id", "campaign_id", "clock_id", "stream_id")),
                _time(value["origin_timestamp_utc"]), value["resources"]["ruleset"], _json(value["task"]),
                tuple(sorted((n["node_id"], n["memory_capacity"], n["can_swap"], n["can_purify"])
                             for n in value["resources"]["nodes"])),
                tuple(sorted(tuple(sorted(link["endpoints"])) for link in value["resources"]["links"])))
    return incoming, identity, _time(value["decision_timestamp_utc"]), text


def _evaluate(model_id, path, snapshot):
    return evaluate_optional_routing(snapshot, model_id=model_id, archive_path=path)


class RoutingShadowSession(AdaptiveDialog):
    def __init__(self, model_id, parent=None):
        if model_id not in MODEL_IDS:
            raise ValueError("unsupported routing checkpoint")
        super().__init__(parent)
        self.model_id = model_id
        self.package = self.latest = None
        self.context = self.window_context = self._last_stamp = self._last_json = None
        self._last_version = None
        self.running = False
        self.input_error = ""
        self._export_epoch = 0
        self.job = LocalModelJob(self)
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(FRESHNESS_MS)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(self._expired)
        self.setWindowTitle("OrionQ · Routing · Artificial shadow")
        self.resize(1000, 700)
        self.setMinimumSize(640, 480)
        self.setStyleSheet(f"QDialog {{background:{C.raised};}} QPlainTextEdit {{background:{C.void};color:{C.ink};border:1px solid {C.line};}}")
        layout = vbox(self, (18, 16, 18, 16), 10)
        layout.addWidget(Section("ROUTING · ARTIFICIAL SHADOW", C.amber))
        self.note = Prose("", 12, C.ink)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.note)
        self.output = EvidenceText()
        self.output.setReadOnly(True)
        self.output.setFont(mono(12))
        self.output.setAccessibleName("Routing candidates and policy probabilities; no actions executed")
        layout.addWidget(self.output, 1)
        layout.addWidget(Prose("SIMULATED · Author's repeater environment (qnet_network QuantumNetworkEnv, exported "
                               "nominal configuration) driven by the greedy-fidelity incumbent; the environment's own "
                               "features. Each update is one decision, 1 s of virtual time. Resources follow the "
                               "incumbent, never recommendations. Policy probabilities are not success rates; reference "
                               "agreement is not performance. STOP clears results.", 11, C.amber))
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
            raise ValueError("matching verified routing checkpoint and idle worker required")
        self.stop()
        self.latest = self.context = self.window_context = self._last_stamp = self._last_json = None
        self.input_error = ""
        self._last_version = None
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
            incoming, identity, stamp, text = routing_frame(snapshot)
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
        if self._last_version is not None and incoming.state_version <= self._last_version:
            self._unavailable("Resource version did not advance; wait for a new sample.")
            return
        self._last_version = incoming.state_version
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
        self._last_version = None
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
            rows = "\n".join(f"{row['action_id']}: {row['action']['kind']} | p={row['probability']:.4f}"
                             for row in evaluation["candidates"])
            self.output.set_evidence(
                f"Selection: {evaluation['selection_mode']}\n"
                f"PCG64 seed: {evaluation['seed']} · random draw: {evaluation['rng_used']}\n"
                f"Recommended: {evaluation['recommendation_action_id']}\n"
                f"Greedy candidate: {evaluation['greedy_action_id']}\n"
                f"Declared reference: {evaluation['reference_action_id']}\n"
                f"Agreement: {evaluation['agrees_with_reference']} · not a success metric\n\n"
                "ORDERED CANDIDATES · POLICY PROBABILITIES\n" + rows + "\n\n"
                "Declared instantaneous resource checks passed. No action executed or scheduled.\n"
                "Features come from the author's simulator; not a deployed network.\n"
                "Export includes candidates, resources, seed, logits, critic return and hashes.")
        elif result is not None:
            self.output.set_evidence(f"REFUSED · {result.reason}\n{result.detail}")
        else:
            self.output.set_evidence(f"REFUSED · {self.job.error}" if self.job.error else self.input_error or
                                     ("RUNNING / FINISHING · no queued frames" if self.job.active else
                                      "No result. Select START SHADOW explicitly; no policy or resources are changed."))

    def _export(self):
        if not self.export_button.isEnabled():
            return
        result, package, epoch = self.job.result, self.package, self._export_epoch
        path, _ = QFileDialog.getSaveFileName(self, "Export selected frame (new file only)",
                                              "routing-shadow.json", "JSON (*.json)")
        if not path or package is not self.package or epoch != self._export_epoch or not self.isVisible():
            return
        try:
            export_routing_shadow(result, path)
            self.output.appendPlainText("\nEXPORTED selected frame · " + path)
        except (ValueError, OSError) as exc:
            self.output.appendPlainText("\nEXPORT REFUSED · " + str(exc))
