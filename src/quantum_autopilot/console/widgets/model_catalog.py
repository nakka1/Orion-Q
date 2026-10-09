"""Read-only inventory and explicit package checks; no model load or policy changes."""
import time
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QFileDialog, QHeaderView, QSplitter, QWidget

from ...model_intake.catalog import ModelOption, model_options
from ...model_intake.policy_compatibility import compatibility_notes
from ..model_archive_check import ModelArchiveCheck
from ..theme import C, mono
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText
from .prims import Btn, Prose, Section, hbox, vbox
from .records import RecordTable

SESSION_MODELS = ("qkd.detector.boosting", "qkd.sync.ridge.1s.h2s", "qkd.keyrate.actor", "qnet.routing.nominal", "qnet.routing.prazo_longo",
                  "qnet.allocator.persistence", "qnet.allocator.ridge",
                  *(f"qnet.compensation.{point}.{kind}" for point in ("calibrado", "desenvolvimento")
                    for kind in ("ridge", "mlp")))


def model_notes(option: ModelOption) -> str:
    """Scientific boundaries, not performance rankings or installation claims."""
    model_id = option.model_id
    if model_id == "qkd.detector.boosting":
        return ("Attack classification of a simulated decoy-state session log (17 public columns, 1 s periods, "
                "20-70 km). Runs the reviewed safe export: the author's pipeline retrained it and its serialised "
                "bytes equal the archive's pickle, which is hashed, never deserialised. Alarms need 3 equal "
                "consecutive calls. Compared with the QBER alarm at the author's calibration. Alarms are labels "
                "for an operator, not security proofs or device commands. The pruned FPGA detector is a "
                "different model, not an interchangeable replacement.")
    if model_id == "qkd.keyrate.actor":
        return ("Decoy-state BB84 transmitter recommendations: nine bounded parameter changes, "
                "explicit random generator, declared 25-80 km domain. Recommendations are not "
                "measured key rates, newly generated keys, or authorized device commands. "
                "An opt-in artificial shadow session keeps parameters fixed and labels calibration bootstrap explicitly.")
    if model_id == "qkd.sync.ridge.1s.h2s":
        return ("Arrival-offset forecast: eight samples at 1 s cadence, 2 s prediction horizon. "
                "Compare with persistence; no superiority is established by inclusion here. "
                "Arrival offset is not Bell fidelity. An explicit artificial-source shadow session "
                "is available; this does not activate a control policy or accept physical timing data.")
    if model_id.startswith("qnet.routing."):
        return ("Scores simulator actions using 11 state and 23 per-action features. "
                "Nominal uses stochastic selection; prazo_longo uses greedy selection. "
                "An opt-in artificial shadow view checks declared instantaneous resources. "
                "It is fed by the author's repeater environment, not a network; no control policy is activated. "
                "An action score is not fidelity or evidence of superiority to a heuristic.")
    if model_id.startswith("qnet.compensation."):
        _, _, point, architecture = model_id.split(".")
        return (f"Declared regime: {point}; architecture: {architecture.upper()}. "
                "SO(3) polarisation-rotation prediction; 2 ms sampling and 40 ms horizon. "
                "First 15 steps use persistence. Domain validity is declared, not automatically "
                "detected. An explicit artificial-source shadow session is available, using "
                "independent virtual-clock batches. Predictions do not actuate a polarisation controller.")
    if model_id.startswith("qnet.allocator."):
        forecast = ("Persistence is a non-ML baseline." if model_id.endswith(".persistence") else
                    "Ridge uses six drift samples; first five steps are persistence warm-up.")
        return (f"{forecast} Fixed synthetic 11-link metro topology; 5 s steps, "
                "30 s queued latency and 150 s forecast horizon. Single-photon BB84, not decoy-state "
                "BB84. Admission and asymptotic key-budget forecasts are not finite-key security "
                "certificates or served keys. No automatic mapping to the current topology. "
                "An explicit artificial shadow view is available; recommendations never change the supplied queue.")
    return "No reviewed component notes available. Do not infer runtime compatibility."


class ModelCatalog(AdaptiveDialog):
    """Inspection is deliberately independent of source capabilities and ML policy toggles."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.options = model_options()
        self.checker = ModelArchiveCheck(self)
        self.offline_session = None
        self.shadow_session = None
        self.compensation_shadow = None
        self.allocation_shadow = None
        self.routing_shadow = None
        self.keyrate_shadow = None
        self._keyrate_input_key = None
        self._keyrate_received = 0.
        self._routing_input_key = None
        self._routing_received = 0.
        self._allocation_input_key = None
        self._allocation_received = 0.
        self._compensation_input_key = {}
        self._compensation_received = {}
        self._shadow_snapshot = None
        self._shadow_input_key = None
        self._shadow_received = 0.
        self.setWindowTitle("OrionQ · Optional model catalog")
        self.resize(1060, 760)
        self.setMinimumSize(640, 480)
        self.setStyleSheet(f"""
            QDialog {{ background:{C.raised}; }}
            QPlainTextEdit {{ background:{C.void}; color:{C.ink}; border:1px solid {C.line}; }}
            QSplitter::handle {{ background:{C.line}; }}
        """)
        layout = vbox(self, (18, 16, 18, 16), 10)
        layout.addWidget(Section("OPTIONAL MODELS · READ-ONLY CATALOG", C.amber))
        offline = sum(option.status == "offline_only" for option in self.options)
        self._summary_text = (
            f"{len(self.options)} options · {offline} offline adapters · "
            f"{len(self.options) - offline} blocked / unavailable. "
            "All optional models default OFF. This catalog does not report the active runtime policy."
        )
        self.summary = Prose(self._summary_text, 12, C.ink)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.table = RecordTable(["MODEL / VARIANT", "EVALUATION", "DEFAULT", "RUNTIME"])
        self.table.setFont(mono(12))
        self.table.horizontalHeader().setFont(mono(11, 0.08))
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # Keep status text readable even when the viewport requires horizontal scrolling.
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setAccessibleName("Optional models; selecting a row only displays details")
        self.table.set_rows([
            [(option.model_id, C.ink),
             ("OFFLINE ONLY" if option.status == "offline_only" else "BLOCKED",
              C.amber if option.status == "offline_only" else C.red),
             ("ON" if option.default_enabled else "OFF", C.muted),
             ("SEE POLICY GATE" if option.runtime_available else "NOT INTEGRATED", C.muted)]
            for option in self.options
        ])
        self.detail = EvidenceText()
        self.detail.setReadOnly(True)
        self.detail.setFont(mono(12))
        self.detail.setMinimumHeight(110)
        self.detail.setAccessibleName("Model scope and scientific limitations")
        self.detail.set_evidence("Select a model to inspect its scope and limitations.\n\n"
                                 "Weights: NOT CHECKED. No files are scanned or loaded.\n"
                                 "Selection here is inspection only, never activation.")
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(self.detail)
        self.splitter.setSizes([360, 230])
        layout.addWidget(self.splitter, 1)
        layout.addWidget(Prose(
            "OFFLINE SIMULATION ONLY · No experimental validation or physical actuation. "
            "The existing baseline and runtime policy remain unchanged.", 11, C.amber))
        actions = QWidget()
        buttons = hbox(actions, (0, 0, 0, 0), 8)
        self.verify_button = Btn("VERIFY LOCAL ZIP", C.violet)
        self.verify_button.setToolTip("Explicit read-only identity and inventory check. No installation or activation.")
        self.verify_button.clicked.connect(self._choose_archive)
        self.clear_button = Btn("FORGET CHECK", C.muted)
        self.clear_button.setToolTip("Clear this session's check only. No file is removed.")
        self.clear_button.clicked.connect(self.checker.clear)
        self.evaluate_button = Btn("OFFLINE SESSION", C.amber)
        self.evaluate_button.setToolTip("Verified synchronization, key-rate, polarisation, routing or allocation; offline only.")
        self.evaluate_button.clicked.connect(self._open_session)
        self.close_button = Btn("CLOSE", C.muted)
        self.close_button.clicked.connect(self.reject)
        for button in (self.verify_button, self.evaluate_button, self.clear_button, self.close_button):
            buttons.addWidget(button, 1)
        layout.addWidget(actions)
        self.shadow_button = Btn("SYNCHRONIZER · LIVE ARTIFICIAL SHADOW", C.violet)
        self.shadow_button.setToolTip("Explicit read-only timing forecasts from the artificial source; no control policy.")
        self.shadow_button.clicked.connect(self._open_shadow)
        layout.addWidget(self.shadow_button)
        self.table.selectionModel().selectionChanged.connect(self._selected)
        self.checker.changed.connect(self._refresh_check)
        self._refresh_check()

    def _selected(self, *_):
        self.checker.clear()

    def _choose_archive(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows or self.checker.active:
            return
        option = self.options[rows[0].row()]
        generation = self.checker.generation
        path, _ = QFileDialog.getOpenFileName(
            self, f"Verify reviewed {option.archive_kind.upper()} package (read only, max 32 MiB)",
            "", "Reviewed ZIP (*.zip)",
        )
        current = self.table.selectionModel().selectedRows()
        if (path and current and self.options[current[0].row()] == option and self.isVisible()
                and self.checker.generation == generation):
            self.checker.start(option.model_id, path)

    def _refresh_check(self):
        rows = self.table.selectionModel().selectedRows()
        selected = self.options[rows[0].row()].model_id if rows else ""
        compensation = selected.startswith("qnet.compensation.")
        allocation = selected.startswith("qnet.allocator.")
        routing = selected.startswith("qnet.routing.")
        keyrate = selected == "qkd.keyrate.actor"
        self.shadow_button.setText(("QKD KEY RATE" if keyrate else "ROUTING" if routing else "ALLOCATION" if allocation else "POLARISATION" if compensation else "SYNCHRONIZER")
                                   + " · LIVE ARTIFICIAL SHADOW")
        self.shadow_button.setToolTip("Explicit artificial-source predictions; no control policy or physical actuation.")
        if (self.keyrate_shadow is not None and self.keyrate_shadow.package is not None
                and self.keyrate_shadow.package is not self.checker.result):
            self.keyrate_shadow.invalidate_package()
        if (self.routing_shadow is not None and self.routing_shadow.package is not None
                and self.routing_shadow.package is not self.checker.result):
            self.routing_shadow.invalidate_package()
        if (self.allocation_shadow is not None and self.allocation_shadow.package is not None
                and self.allocation_shadow.package is not self.checker.result):
            self.allocation_shadow.invalidate_package()
        if (self.compensation_shadow is not None and self.compensation_shadow.package is not None
                and self.compensation_shadow.package is not self.checker.result):
            self.compensation_shadow.invalidate_package()
        if (self.shadow_session is not None and self.shadow_session.package is not None
                and self.shadow_session.package is not self.checker.result):
            self.shadow_session.invalidate_package()
        self.shadow_button.setEnabled(
            self.checker.result is not None and not self.checker.active
            and self.checker.result.model_id == selected
            and (selected == "qkd.sync.ridge.1s.h2s" or compensation or allocation or routing or keyrate)
            and not (self.shadow_session is not None and self.shadow_session.job.active)
            and not (self.compensation_shadow is not None and self.compensation_shadow.job.active)
            and not (self.allocation_shadow is not None and self.allocation_shadow.job.active)
            and not (self.routing_shadow is not None and self.routing_shadow.job.active)
            and not (self.keyrate_shadow is not None and self.keyrate_shadow.job.active))
        if (self.offline_session is not None and self.offline_session.package is not None
                and self.offline_session.package is not self.checker.result):
            self.offline_session.invalidate_package()
        self.evaluate_button.setEnabled(
            bool(rows) and not self.checker.active and self.checker.result is not None
            and self.options[rows[0].row()].model_id in SESSION_MODELS
            and self.checker.result.model_id == self.options[rows[0].row()].model_id
            and not (self.offline_session is not None and self.offline_session.job.active))
        self.verify_button.setEnabled(bool(rows) and not self.checker.active)
        self.clear_button.setEnabled(bool(self.checker.result or self.checker.error or self.checker.active))
        state = ("CHECKING / FINISHING" if self.checker.active else
                 "REFUSED" if self.checker.error else
                 "IDENTITY VERIFIED · NOT INSTALLED" if self.checker.result else "NOT CHECKED")
        self.summary.setText(f"{self._summary_text}\nPACKAGE CHECK: {state}")
        self._render_details()

    def _open_session(self):
        if not self.evaluate_button.isEnabled():
            return
        model_id = self.checker.result.model_id
        if self.offline_session is not None and self.offline_session.model_id != model_id:
            self.offline_session.job.changed.disconnect(self._refresh_check)
            self.offline_session.close()
            self.offline_session.deleteLater()
            self.offline_session = None
        if self.offline_session is None:
            if model_id.startswith("qnet.allocator."):
                from .allocation_session import AllocationSession
                self.offline_session = AllocationSession(model_id, self)
            elif model_id.startswith("qnet.routing."):
                from .routing_session import RoutingSession
                self.offline_session = RoutingSession(model_id, self)
            elif model_id.startswith("qnet.compensation."):
                from .compensation_session import CompensationSession
                self.offline_session = CompensationSession(model_id, self)
            elif model_id == "qkd.detector.boosting":
                from .detector_session import DetectorSession
                self.offline_session = DetectorSession(self)
            elif model_id == "qkd.keyrate.actor":
                from .keyrate_session import KeyRateSession
                self.offline_session = KeyRateSession(self)
            else:
                from .sync_session import SyncSession
                self.offline_session = SyncSession(self)
            self.offline_session.job.changed.connect(self._refresh_check)
        if self.offline_session.package is not self.checker.result:
            self.offline_session.set_package(self.checker.result)
        self.offline_session.show()
        self.offline_session.raise_()
        self.offline_session.activateWindow()

    def accept_shadow_snapshot(self, snapshot):
        candidate = snapshot.optional_sync_state
        key = (snapshot.input_kind, snapshot.campaign, snapshot.capabilities.adapter_id,
               candidate.to_json() if candidate is not None else None)
        if key != self._shadow_input_key:
            self._shadow_received = time.monotonic()
            self._shadow_input_key = key
        self._shadow_snapshot = snapshot
        for point in ("calibrado", "desenvolvimento"):
            compensation_key = (snapshot.input_kind, snapshot.campaign, snapshot.capabilities.adapter_id,
                                tuple(state.to_json() for state in snapshot.optional_compensation_states
                                      if state.metadata.get("compensation_operating_point") == point))
            if compensation_key != self._compensation_input_key.get(point):
                self._compensation_received[point] = time.monotonic()
                self._compensation_input_key[point] = compensation_key
        from .allocation_shadow import allocation_frame
        try:
            _, identity, stamp, text = allocation_frame(snapshot)
        except (ValueError, TypeError, OverflowError):
            self._allocation_received = 0.
        else:
            previous = self._allocation_input_key
            if previous is None or identity != previous[0] or stamp > previous[1]:
                self._allocation_input_key = (identity, stamp, text)
                self._allocation_received = time.monotonic()
            elif stamp < previous[1] or text != previous[2]:
                self._allocation_received = 0.
        if self.allocation_shadow is not None and self.allocation_shadow.isVisible():
            self.allocation_shadow.accept_snapshot(snapshot)
        from .keyrate_shadow import keyrate_frame
        try:
            _, identity, stamp, text = keyrate_frame(snapshot)
        except (ValueError, TypeError, OverflowError):
            self._keyrate_received = 0.
        else:
            previous = self._keyrate_input_key
            if previous is None or identity != previous[0] or stamp > previous[1]:
                self._keyrate_input_key = (identity, stamp, text)
                self._keyrate_received = time.monotonic()
            elif stamp < previous[1] or text != previous[2]:
                self._keyrate_received = 0.
        if self.keyrate_shadow is not None and self.keyrate_shadow.isVisible():
            self.keyrate_shadow.accept_snapshot(snapshot)
        from .routing_shadow import routing_frame
        try:
            incoming, identity, stamp, text = routing_frame(snapshot)
        except (ValueError, TypeError, OverflowError):
            self._routing_received = 0.
        else:
            previous = self._routing_input_key
            if (previous is None or identity != previous[0]
                    or stamp > previous[1] and incoming.state_version > previous[3]):
                self._routing_input_key = (identity, stamp, text, incoming.state_version)
                self._routing_received = time.monotonic()
            elif stamp < previous[1] or text != previous[2]:
                self._routing_received = 0.
        if self.routing_shadow is not None and self.routing_shadow.isVisible():
            self.routing_shadow.accept_snapshot(snapshot)
        if self.compensation_shadow is not None and self.compensation_shadow.isVisible():
            self.compensation_shadow.accept_snapshot(snapshot)
        if self.shadow_session is not None and self.shadow_session.isVisible():
            self.shadow_session.accept_snapshot(snapshot)

    def _open_shadow(self):
        if not self.shadow_button.isEnabled():
            return
        if self.checker.result.model_id == "qkd.keyrate.actor":
            self._open_keyrate_shadow()
            return
        if self.checker.result.model_id.startswith("qnet.routing."):
            self._open_routing_shadow()
            return
        if self.checker.result.model_id.startswith("qnet.allocator."):
            self._open_allocation_shadow()
            return
        if self.checker.result.model_id.startswith("qnet.compensation."):
            self._open_compensation_shadow()
            return
        if self.shadow_session is None:
            from .sync_shadow import SyncShadowSession
            self.shadow_session = SyncShadowSession(self)
            self.shadow_session.job.changed.connect(self._refresh_check)
        if self.shadow_session.package is not self.checker.result:
            self.shadow_session.set_package(self.checker.result)
        self.shadow_session.show()
        if self._shadow_snapshot is not None and time.monotonic() - self._shadow_received < 3.5:
            self.shadow_session.accept_snapshot(self._shadow_snapshot)
        self.shadow_session.raise_()
        self.shadow_session.activateWindow()

    def _open_compensation_shadow(self):
        model_id = self.checker.result.model_id
        if self.compensation_shadow is not None and self.compensation_shadow.model_id != model_id:
            self.compensation_shadow.job.changed.disconnect(self._refresh_check)
            self.compensation_shadow.close()
            self.compensation_shadow.deleteLater()
            self.compensation_shadow = None
        if self.compensation_shadow is None:
            from .compensation_shadow import CompensationShadowSession
            self.compensation_shadow = CompensationShadowSession(model_id, self)
            self.compensation_shadow.job.changed.connect(self._refresh_check)
        if self.compensation_shadow.package is not self.checker.result:
            self.compensation_shadow.set_package(self.checker.result)
        self.compensation_shadow.show()
        received = self._compensation_received.get(model_id.split(".")[2], 0.)
        if self._shadow_snapshot is not None and time.monotonic() - received < 3.5:
            self.compensation_shadow.accept_snapshot(self._shadow_snapshot)
        self.compensation_shadow.raise_()
        self.compensation_shadow.activateWindow()

    def _open_allocation_shadow(self):
        model_id = self.checker.result.model_id
        if self.allocation_shadow is not None and self.allocation_shadow.model_id != model_id:
            self.allocation_shadow.job.changed.disconnect(self._refresh_check)
            self.allocation_shadow.close()
            self.allocation_shadow.deleteLater()
            self.allocation_shadow = None
        if self.allocation_shadow is None:
            from .allocation_shadow import AllocationShadowSession
            self.allocation_shadow = AllocationShadowSession(model_id, self)
            self.allocation_shadow.job.changed.connect(self._refresh_check)
        if self.allocation_shadow.package is not self.checker.result:
            self.allocation_shadow.set_package(self.checker.result)
        self.allocation_shadow.show()
        if (self._shadow_snapshot is not None and self._allocation_received > 0
                and time.monotonic() - self._allocation_received < 3.5):
            from .allocation_shadow import allocation_frame
            try:
                _, identity, stamp, text = allocation_frame(self._shadow_snapshot)
            except (ValueError, TypeError, OverflowError):
                pass
            else:
                if (identity, stamp, text) == self._allocation_input_key:
                    self.allocation_shadow.accept_snapshot(self._shadow_snapshot)
        self.allocation_shadow.raise_()
        self.allocation_shadow.activateWindow()

    def _open_routing_shadow(self):
        model_id = self.checker.result.model_id
        if self.routing_shadow is not None and self.routing_shadow.model_id != model_id:
            self.routing_shadow.job.changed.disconnect(self._refresh_check)
            self.routing_shadow.close()
            self.routing_shadow.deleteLater()
            self.routing_shadow = None
        if self.routing_shadow is None:
            from .routing_shadow import RoutingShadowSession
            self.routing_shadow = RoutingShadowSession(model_id, self)
            self.routing_shadow.job.changed.connect(self._refresh_check)
        if self.routing_shadow.package is not self.checker.result:
            self.routing_shadow.set_package(self.checker.result)
        self.routing_shadow.show()
        if (self._shadow_snapshot is not None and self._routing_received > 0
                and time.monotonic() - self._routing_received < 3.5):
            from .routing_shadow import routing_frame
            try:
                incoming, identity, stamp, text = routing_frame(self._shadow_snapshot)
            except (ValueError, TypeError, OverflowError):
                pass
            else:
                if (identity, stamp, text, incoming.state_version) == self._routing_input_key:
                    self.routing_shadow.accept_snapshot(self._shadow_snapshot)
        self.routing_shadow.raise_()
        self.routing_shadow.activateWindow()

    def _open_keyrate_shadow(self):
        model_id = self.checker.result.model_id
        if self.keyrate_shadow is not None and self.keyrate_shadow.model_id != model_id:
            self.keyrate_shadow.job.changed.disconnect(self._refresh_check)
            self.keyrate_shadow.close()
            self.keyrate_shadow.deleteLater()
            self.keyrate_shadow = None
        if self.keyrate_shadow is None:
            from .keyrate_shadow import KeyRateShadowSession
            self.keyrate_shadow = KeyRateShadowSession(model_id, self)
            self.keyrate_shadow.job.changed.connect(self._refresh_check)
        if self.keyrate_shadow.package is not self.checker.result:
            self.keyrate_shadow.set_package(self.checker.result)
        self.keyrate_shadow.show()
        if (self._shadow_snapshot is not None and self._keyrate_received > 0
                and time.monotonic() - self._keyrate_received < 3.5):
            from .keyrate_shadow import keyrate_frame
            try:
                _, identity, stamp, text = keyrate_frame(self._shadow_snapshot)
            except (ValueError, TypeError, OverflowError):
                pass
            else:
                if (identity, stamp, text) == self._keyrate_input_key:
                    self.keyrate_shadow.accept_snapshot(self._shadow_snapshot)
        self.keyrate_shadow.raise_()
        self.keyrate_shadow.activateWindow()

    def hideEvent(self, event):
        self.checker.clear()
        super().hideEvent(event)

    def _render_details(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        option = self.options[rows[0].row()]
        package_check = "Weights / installation: NOT CHECKED; no model loaded."
        if self.checker.active:
            package_check = "Package check finishing in background; no model loaded or activated."
        elif self.checker.error:
            package_check = f"PACKAGE REFUSED: {self.checker.error}\nNo model loaded or activated."
        elif self.checker.result and self.checker.result.model_id == option.model_id:
            package_check = self.checker.result.describe()
        self.detail.set_evidence(
            f"{option.model_id}\n\n"
            f"{compatibility_notes(option.model_id)}\n\n"
            f"Purpose: {option.purpose}\n"
            f"Source package: {option.archive_kind.upper()} (external, not bundled)\n"
            f"Adapter admission: {option.status}\n"
            f"{package_check}\n"
            "Selection: INSPECTION ONLY; no runtime activation.\n\n"
            f"{model_notes(option)}\n\n"
            "Evidence boundary: simulated_offline only. Replay parity is not experimental "
            "validation or a claim of predictive gain. BER, QBER and SKR are not converted "
            "to measured Bell fidelity.\n\n"
            "Control-policy use remains pending: component-specific inputs, independent supervisor and "
            "explicit Q05 review / registration / readback. Optional weight distribution "
            "also requires licensing review. No model is downloaded or installed here."
        )
