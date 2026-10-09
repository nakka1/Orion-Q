"""Explicit endpoint–repeater–endpoint conditional simulation form."""
import json
from dataclasses import asdict
from uuid import uuid4

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QLineEdit, QTabWidget, QWidget

from quantum_autopilot.control_plane.workspace_path import WorkspacePathRequest
from quantum_autopilot.control_plane.temporal_path import TemporalPathRequest
from quantum_autopilot.control_plane.topology import _sha256
from ..theme import C, mono
from .adaptive_dialog import AdaptiveDialog
from .adaptive_layout import AdaptiveRow
from .evidence_text import EvidenceText
from .prims import Btn, Prose, Section, vbox, scroll_panel


class PathRunner(AdaptiveDialog):
    command = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OrionQ · Frozen path simulation")
        self.resize(570, 550)
        self.setMinimumWidth(450)
        self.setFont(mono(10))
        self.setStyleSheet(f"QDialog{{background:{C.raised};}} QComboBox,QLineEdit{{background:{C.void};color:{C.ink};border:1px solid {C.line};padding:7px;}}")
        self.snap = self.context = self.pending = self.version = None
        self.busy = False
        self.run_id = ""
        layout = vbox(self, (20, 18, 20, 18), 10)
        layout.addWidget(Section("ONE REPEATER · FROZEN PATH SIMULATION", C.amber))
        self.scope = Prose("", 11)
        layout.addWidget(self.scope)
        self.form = QWidget()
        form = vbox(self.form, (0, 0, 0, 0), 7)
        self.fields = {}
        form.addWidget(Section("PROTOCOL"))
        self.protocol = QComboBox()
        self.protocol.addItems(("Ideal conditional SWAP", "Acquisition + T1/T2 memory"))
        self.protocol.setToolTip("Choose ideal available pairs or a temporal acquisition trial with explicit memory assumptions.")
        form.addWidget(self.protocol)
        for name in ("source", "repeater", "destination"):
            form.addWidget(Section(name.upper()))
            self.fields[name] = QComboBox()
            self.fields[name].setToolTip(f"Select the {name} node for this frozen simulation route.")
            form.addWidget(self.fields[name])
        form.addWidget(Section("TARGET FIDELITY · fraction (0–1)"))
        self.target = QLineEdit()
        self.target.setToolTip("Required conditional fidelity as a fraction from 0 to 1; 0.8 means 80%.")
        form.addWidget(self.target)
        self.temporal_form = QWidget()
        temporal = vbox(self.temporal_form, (0, 0, 0, 0), 7)
        self.temporal_fields = {}
        for key, label in (("success_probability_left", "LEFT HOP · probability per attempt [0–1]"),
            ("success_probability_right", "RIGHT HOP · probability per attempt [0–1]"),
            ("attempt_interval_s", "ATTEMPT INTERVAL · seconds"), ("max_attempts", "MAX TICKS · 1–1000"),
            ("memory_t1_s", "MEMORY T1 · seconds"), ("memory_t2_s", "MEMORY T2 · seconds (T2 ≤ 2T1)"),
            ("memory_ttl_steps", "RETENTION LIMIT · ticks (expire before acquisition)"), ("seed", "SEED · integer [0–4294967295]")):
            temporal.addWidget(Section(label))
            self.temporal_fields[key] = QLineEdit()
            self.temporal_fields[key].setToolTip(label + ". Explicit assumption for one simulated trial.")
            temporal.addWidget(self.temporal_fields[key])
        form.addWidget(self.temporal_form)
        self.form_scroll = scroll_panel(self.form, 150)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.form_scroll, "REQUEST")
        self.result_page = QWidget()
        result_layout = vbox(self.result_page, (8, 8, 8, 8), 8)
        self.note = Prose("No result loaded.", 11)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        result_layout.addWidget(self.note)
        self.results = EvidenceText()
        self.results.setFont(mono(10))
        result_layout.addWidget(self.results, 1)
        self.tabs.addTab(self.result_page, "RESULTS / RECEIPT")
        self.tabs.setTabToolTip(0, "Fill an explicit request on the pinned topology version.")
        self.tabs.setTabToolTip(1, "Read the persisted simulation result, assumptions and complete receipt hashes.")
        layout.addWidget(self.tabs, 1)
        self.identity = Prose("", 10)
        layout.addWidget(self.identity)
        self.reload = Btn("NEW REQUEST / RELOAD FORM", C.muted)
        self.run = Btn("RUN FROZEN PATH", C.amber, filled=True)
        layout.addWidget(AdaptiveRow((self.reload, self.run), gap=8))
        self.status = Prose("Fill the request explicitly, then run the simulation.", 11)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.reload.clicked.connect(self.load_form)
        self.run.clicked.connect(self.submit)
        self.protocol.currentIndexChanged.connect(self._protocol_changed)
        self._protocol_changed()

    def _protocol_changed(self, *_):
        temporal = self.protocol.currentIndex() == 1
        self.temporal_form.setVisible(temporal)
        self.scope.setText("Independent Bernoulli acquisition; assumed probabilities, one slot per link, expiry and T1/T2 noise on BOTH qubits of each stored pair. Ideal BSM; no WDM inference or physical delivery. Distance remains metadata."
                           if temporal else "Two nominal Werner pairs already available; ideal BSM/corrections, zero memory age. No loss, timing, WDM prediction or physical delivery. Distance is metadata only. Workspace memory is preserved.")
        self.controls()

    def allowed(self):
        return self.snap is not None and self.snap.capabilities.allows("run_workspace_path") and (
            self.protocol.currentIndex() == 0 or self.snap.capabilities.allows("run_temporal_path"))

    def controls(self):
        enabled = self.allowed() and not self.busy and self.pending is None
        for widget in (self.form, self.reload, self.run):
            widget.setEnabled(enabled)

    def set_busy(self, busy):
        self.busy = busy
        self.controls()

    def set_snapshot(self, snap):
        context = (snap.campaign, snap.input_kind, snap.capabilities.adapter_id, snap.topology.id)
        if context != self.context or not snap.capabilities.allows("run_workspace_path"):
            self.pending = None
            self.version = None
            self.hide()
            self.feedback("Source changed; no request resubmitted.")
            self.results.clear()
            self.identity.clear()
            for field in self.fields.values():
                field.clear()
            self.target.clear()
            for field in self.temporal_fields.values():
                field.clear()
        self.snap, self.context = snap, context
        self.protocol.model().item(1).setEnabled(snap.capabilities.allows("run_temporal_path"))
        if not snap.capabilities.allows("run_temporal_path"):
            self.protocol.setCurrentIndex(0)
        result = snap.workspace_path
        if self.pending and result and result.request_hash == _sha256(asdict(self.pending)):
            self.pending = None
            self.show_result(result, "CONFIRMED")
        self.controls()

    def feedback(self, message):
        self.note.setText(message)
        self.status.setText(message.splitlines()[0])

    def show_result(self, result, label, *, activate=True):
        if result.protocol == "memory_single_swap_v1":
            value = "F unavailable (no swap)" if result.fidelity is None else f"F = {result.fidelity:.10g} · target {'MET' if result.target_met else 'NOT MET'}"
            self.feedback(f"{label} · {' → '.join(result.route)} · v{result.version}\n"
                f"{result.outcome.upper()} · {value}\n{result.ticks} ticks · elapsed {result.elapsed_s:g} s · ages {result.memory_ages_s} s\n"
                f"Generated {result.generated_pairs} · expired {result.expired_pairs} pairs\n"
                f"Record {result.record_hash[:16]} · one simulated trial, not a rate estimate.")
        else:
            self.feedback(f"{label} · {' → '.join(result.route)} · version {result.version}\n"
                          f"Conditional F = {result.fidelity:.10g} · target {result.target:g} · {'MET' if result.target_met else 'NOT MET'} under stated assumptions only.\n"
                          f"Record {result.record_hash[:16]} · no physical delivery.")
        self.note.setToolTip(f"Run: {result.run_id}\nTopology SHA256: {result.topology_hash}\nRecord SHA256: {result.record_hash}")
        self.results.set_evidence(self.note.text() + "\n\nPERSISTED RESULT READBACK\n"
            + json.dumps(asdict(result), indent=2, ensure_ascii=False, allow_nan=False))
        if activate:
            self.tabs.setCurrentWidget(self.result_page)
            if self.content_scroll is not None:
                self.content_scroll.verticalScrollBar().setValue(0)

    def load_form(self):
        if not self.allowed() or self.pending or self.busy:
            return
        self.version = self.snap.topology.version
        self.run_id = uuid4().hex
        for name, field in self.fields.items():
            field.clear()
            field.addItems([node.id for node in self.snap.topology.nodes if node.kind == ("repeater" if name == "repeater" else "node")])
            field.setCurrentIndex(-1)
        self.target.clear()
        for field in self.temporal_fields.values():
            field.clear()  # No silent experimental defaults; every assumption is explicit.
        self.identity.setText(f"Pinned version {self.version} · request {self.run_id}")
        self.feedback("Select each node explicitly. Closing does not cancel an accepted run.")
        self.results.clear()
        self.tabs.setCurrentWidget(self.form_scroll)
        if self.snap.workspace_path:
            self.show_result(self.snap.workspace_path, "PREVIOUS RESULT (not this request)", activate=False)
        self.controls()

    def begin(self):
        if self.allowed():
            if not self.pending:
                self.load_form()
            self.show()
            self.raise_()

    def submit(self):
        if not self.allowed() or self.pending or self.busy:
            return
        try:
            request = WorkspacePathRequest(self.run_id, self.fields["source"].currentText(),
                self.fields["repeater"].currentText(), self.fields["destination"].currentText(),
                self.version, float(self.target.text()), self.snap.topology.id)
            if self.protocol.currentIndex() == 1:
                values = {}
                for key, field in self.temporal_fields.items():
                    text = field.text().strip()
                    integer = key in {"max_attempts", "memory_ttl_steps", "seed"}
                    if integer and (not text.isascii() or not text.isdigit()):
                        raise ValueError(f"{key} must be an integer")
                    values[key] = int(text) if integer else float(text)
                request = TemporalPathRequest(**dict(asdict(request), protocol="memory_single_swap_v1", **values))
            serialized = json.dumps(asdict(request), allow_nan=False)
        except (ValueError, OverflowError) as exc:
            self.feedback(f"REFUSED · {exc}")
            return
        self.pending = request
        self.feedback("PENDING · Waiting for persisted simulation result. Workspace is not modified.")
        self.results.set_evidence(self.note.text())
        self.tabs.setCurrentWidget(self.result_page)
        self.controls()
        self.command.emit("run_temporal_path" if self.protocol.currentIndex() == 1 else "run_workspace_path", serialized)

    def command_failed(self, message):
        if self.pending:
            self.pending = None
            self.feedback(f"UNCONFIRMED · {message}. Same request ID can be retried unchanged; reload for a new request.")
            self.results.set_evidence(self.note.text())
            self.tabs.setCurrentWidget(self.result_page)
            self.controls()
