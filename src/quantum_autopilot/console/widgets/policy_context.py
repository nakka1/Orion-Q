"""Explicit native observation of caller-supplied simulated input; no policy selection."""

import json
import time

from PySide6.QtCore import Qt, QTimer, QSignalBlocker
from PySide6.QtWidgets import QComboBox, QFileDialog, QTabWidget, QWidget

from quantum_autopilot.control_plane.command_reconciliation import _strict_json
from quantum_autopilot.control_plane.policy_context import MAX_INPUT_BYTES, MAX_PROPOSAL_AGE_S
from quantum_autopilot.model_intake.catalog import model_options
from quantum_autopilot.model_intake.policy_compatibility import TARGET_POLICY_ID
from ..model_archive_check import LocalModelJob
from ..policy_context_binding import PolicyContextBinding
from ..theme import C, mono
from .command_history import _export_captured
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText, ScrollableTextEdit
from .prims import Btn, Prose, Section, vbox, hbox


def _observe_text(binding, candidate, expected, text):
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("input JSON exceeds 64 KiB")
    snapshot = _strict_json(text)
    return binding.run(
        "observe",
        dict(mode="simulation", candidate_id=candidate, expected_context_sha256=expected, snapshot=snapshot),
    )


class PolicyContextInspection(AdaptiveDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.provider = self._source = self.binding = None
        self._export_capability = False
        self.context = self.observation = self.deadline = None
        self._epoch = 0
        self._handled = None
        self.job = LocalModelJob(self)
        self.export_job = LocalModelJob(self)
        self.setWindowTitle("OrionQ · Simulated context inspection")
        self.resize(960, 680)
        self.setMinimumSize(640, 480)
        self.setStyleSheet(
            f"QDialog{{background:{C.raised};}} QPlainTextEdit,QComboBox{{background:{C.void};color:{C.ink};border:1px solid {C.line};}}"
        )
        layout = vbox(self, (18, 16, 18, 16), 10)
        layout.addWidget(Section("CONTEXT INSPECTION · SIMULATION ONLY", C.amber))
        layout.addWidget(
            Prose(
                "Observe an explicit input against the existing runtime. Caller provenance is not authenticated; this never authorizes selection or an action.",
                11,
            )
        )
        self.candidate = QComboBox()
        self.candidate.addItems([TARGET_POLICY_ID, *(o.model_id for o in model_options())])
        self.candidate.setAccessibleName("Candidate to inspect; never activates a model")
        layout.addWidget(self.candidate)
        self.tabs = QTabWidget()
        self.input = ScrollableTextEdit()
        self.input.setPlaceholderText(
            "Paste a complete simulated NetworkStateSnapshot JSON (max 64 KiB). Timestamps and predictions are never filled automatically."
        )
        self.input.setAccessibleName("Explicit simulated input JSON")
        self.details = EvidenceText()
        self.receipt = EvidenceText()
        for widget, label in (
            (self.input, "INPUT JSON"),
            (self.details, "CONTEXT / RESULT"),
            (self.receipt, "CAPTURED RECEIPT"),
        ):
            widget.setFont(mono(10))
            widget.setReadOnly(widget is not self.input)
            self.tabs.addTab(widget, label)
        layout.addWidget(self.tabs, 1)
        controls = QWidget()
        row = hbox(controls, (0, 0, 0, 0), 8)
        self.load_button = Btn("LOAD CONTEXT", C.amber)
        self.observe_button = Btn("OBSERVE INPUT", C.violet)
        self.export_button = Btn("EXPORT CAPTURED", C.muted)
        for button in (self.load_button, self.observe_button, self.export_button):
            row.addWidget(button)
        layout.addWidget(controls)
        self.message = "Load context explicitly, then paste an input. No runtime is initialized here."
        self.status = Prose(self.message, 11)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.candidate.currentIndexChanged.connect(self._draft_changed)
        self.input.textChanged.connect(self._draft_changed)
        self.load_button.clicked.connect(self.load)
        self.observe_button.clicked.connect(self.observe)
        self.export_button.clicked.connect(self.export)
        self.job.changed.connect(self._changed)
        self.export_job.changed.connect(self._changed)
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(250)
        self.watchdog.timeout.connect(self._tick)
        self._render()

    def attach(self, provider):
        if self.provider is not None:
            raise ValueError("context inspection already attached")
        self.provider = provider
        provider.source_changed.connect(self._tick)
        provider.snapshot_changed.connect(self._snapshot)
        self._tick()

    def _scope(self, capabilities=None):
        if self.provider is None:
            self._export_capability = False
            return None
        service = getattr(self.provider.active, "service", None)
        runtime = getattr(service, "_controller_runtime", None)
        if capabilities is None:
            capabilities = self.provider.capabilities
        self._export_capability = capabilities.allows("export_record")
        return (
            id(self.provider.active),
            self.provider._generation,
            capabilities.allows("observe_policy_context"),
            id(service),
            id(runtime),
            getattr(service, "_policy_preparation_generation", None),
            str(getattr(getattr(runtime, "journal", None), "path", None)),
            str(getattr(service, "control_journal_path", None)),
        )

    def _reset(self, message, clear_input=False):
        self._epoch += 1
        if self.binding is not None:
            self.binding.cancel()
        self.binding = self.context = self.observation = self.deadline = None
        self.message = message
        if clear_input:
            with QSignalBlocker(self.input):
                self.input.clear()
        # The caller applies the complete reset and renders afterwards. Avoid
        # changed reentering _tick with a new backend read on the GUI thread.
        with QSignalBlocker(self.job), QSignalBlocker(self.export_job):
            self.job.clear()
            self.export_job.clear()

    def _tick(self, *_, capabilities=None):
        scope = self._scope(capabilities)
        if scope != self._source:
            self._source = scope
            self._reset("Source changed. Load a new context and explicit input.", True)
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self._reset("Context expired. Load a new context; input timestamps are not renewed.")
        self._render()

    def _snapshot(self, snapshot):
        self._tick(capabilities=snapshot.capabilities)
        if self.context is not None:
            if (
                not snapshot.topology.persisted
                or snapshot.topology.version != self.context["record"]["context"]["topology_version"]
            ):
                self._reset("Topology changed or unavailable. Load context again.")
        self._render()

    def _draft_changed(self, *_):
        if self.job.active or self.export_job.active:
            self._reset("Selection edited during work. Load context again.")
        else:
            self._epoch += 1
            self.observation = None
            self.job.clear()
            self.export_job.clear()
            self.message = "Input/candidate changed. Observe explicitly; nothing is selected for execution."
        self._render()

    def _changed(self):
        self._tick()
        if (
            not self.job.active
            and self._handled != self.job.generation
            and (self.job.result is not None or self.job.error)
        ):
            self._handled = self.job.generation
            if self.job.error:
                error = self.job.error
                self._reset("OBSERVATION REFUSED · " + error + " · Load context again.")
            else:
                receipt = self.job.result
                if receipt["record"]["schema"] == "orion.policy_compatibility_context.v1":
                    self.context = receipt
                    self.message = "Context captured. Paste explicit input and select OBSERVE INPUT."
                else:
                    self.observation = receipt
                    self.message = (
                        receipt["record"]["status"] + " · captured observation, never authorization."
                    )
                    self.tabs.setCurrentWidget(self.details)
        self._render()

    def _render(self):
        allowed = self.isVisible() and self._source is not None and self._source[2]
        busy = self.job.active or self.export_job.active
        self.load_button.setEnabled(bool(allowed and not busy))
        self.observe_button.setEnabled(
            bool(allowed and not busy and self.context and self.input.toPlainText().strip())
        )
        writer = getattr(self.provider.active, 'export_captured_evidence', None) if self.provider else None
        self.export_button.setEnabled(bool(allowed and self._export_capability and callable(writer)
                                           and not busy and self.observation))
        # Edits stay possible during a worker so they can immediately cancel its selection.
        self.input.setEnabled(bool(allowed))
        self.candidate.setEnabled(bool(allowed))
        text = "No context captured. LOAD CONTEXT performs a read only."
        if self.context:
            c = self.context["record"]["context"]
            text = (
                f"CONTEXT CAPTURED · {self.context['record']['observed_at_utc']}\n"
                f"Policy: {c['baseline']['policy_id']} · threshold {c['baseline']['threshold']}\n"
                f"Topology: {c['topology_id']} · version {c['topology_version']}\n"
                f"Context SHA-256: {self.context['record']['context_sha256']}\n"
            )
        if self.observation:
            r = self.observation["record"]
            text = (
                f"{r['status']} · SIMULATED OBSERVATION\n"
                f"Candidate: {r['declaration']['record']['candidate_id']}\n"
                f"Observed: {r['observed_at_utc']}\n"
                f"Target input checks passed: {r['target_input_checks_passed']}\n"
                f"Candidate domain validated: {r['candidate_domain_validated']}\n"
                f"Effective input age: {r['effective_telemetry_age_s']:.3f} s\n"
                "Selection / activation: UNAVAILABLE. No per-action authorization.\n"
                "Input origin: caller supplied, unauthenticated. Optional models remain OFF.\n\n"
                + "BLOCKING REASONS\n"
                + json.dumps(r["blocking_reasons"], indent=2, ensure_ascii=False)
                + "\n\n"
                + text
            )
        if self.details.toPlainText() != text:
            self.details.set_evidence(text)
        serialized = (
            json.dumps(self.observation or self.context, indent=2, ensure_ascii=False)
            if self.observation or self.context
            else ""
        )
        if self.receipt.toPlainText() != serialized:
            self.receipt.set_evidence(serialized)
        message = self.message
        if busy:
            message = "READING / FINISHING · no queued work."
        elif self.export_job.error:
            message = "EXPORT REFUSED · " + self.export_job.error
        elif self.export_job.result:
            message = "EXPORTED captured observation · " + self.export_job.result
        self.status.setText(message)

    def load(self):
        self._tick()
        if not self.load_button.isEnabled():
            return
        self._reset("Reading context…")
        self.deadline = time.monotonic() + MAX_PROPOSAL_AGE_S
        try:
            self.binding = PolicyContextBinding(self.provider.active.policy_context_source())
            self.job.submit(self.binding.run, "inspect")
        except (ValueError, AttributeError) as exc:
            self._reset("CONTEXT REFUSED · " + str(exc))
        self._render()

    def observe(self):
        self._tick()
        if not self.observe_button.isEnabled():
            return
        self._epoch += 1
        self.observation = None
        self.export_job.clear()
        self.job.submit(
            _observe_text,
            self.binding,
            self.candidate.currentText(),
            self.context["record"]["context_sha256"],
            self.input.toPlainText(),
        )

    def export(self):
        self._tick()
        if not self.export_button.isEnabled():
            return
        captured = json.dumps(self.observation, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        epoch = self._epoch
        writer = self.provider.active.export_captured_evidence
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export captured observation (new file only)",
            "policy-context-observation.json",
            "JSON (*.json)",
        )
        self._tick()
        if not path or epoch != self._epoch or not self.export_button.isEnabled():
            return
        self.export_job.submit(_export_captured, captured, path, writer)

    def showEvent(self, event):
        self._tick()
        self.watchdog.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.watchdog.stop()
        self._reset("Panel closed. Load a new context and explicit input.", True)
        super().hideEvent(event)
