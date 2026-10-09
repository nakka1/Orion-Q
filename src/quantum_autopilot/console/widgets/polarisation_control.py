"""Supervised polarisation loop and its Q05 lifecycle, in simulation only.

The panel drives ``PolarisationControlSlot``: it runs the loop with whatever
controller is live, and a learned checkpoint becomes live only through
PREPARE → REVIEW → APPROVE → ACTIVATE, bound to a verified weights package and
a closed-loop campaign summary. Every operation runs on a background worker;
changing the checkpoint, package or evidence discards the captured selection.
"""
import hashlib
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QLineEdit, QSpinBox, QWidget,
)

from quantum_autopilot.control_plane import polarisation_activation as activation
from quantum_autopilot.control_plane.policy_approval import PolicyApprovalRequest
from ..model_archive_check import LocalModelJob
from ..theme import C, mono
from .figures import LineChart
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText
from .prims import Btn, Prose, Section, hbox, vbox

CHECKPOINTS = ("qnet.compensation.calibrado.ridge", "qnet.compensation.calibrado.mlp",
               "qnet.compensation.desenvolvimento.ridge", "qnet.compensation.desenvolvimento.mlp")
DURATION_S = 20.0
FAULT_STEPS = range(2500, 2550)  # 100 ms of invalid proposals from t = 5 s
MAX_EVIDENCE_BYTES = activation.MAX_EVIDENCE_BYTES


def default_journal():
    from quantum_autopilot.desktop import default_state_root
    return default_state_root() / "polarisation" / "journal.sqlite"


def _proposal_lines(proposal):
    record = proposal["record"]
    current, proposed = record["current_policy"], record["proposed_policy"]
    lines = [f"Live controller: {current['controller_id']}",
             f"Proposed controller: {proposed['controller_id']}",
             f"Fallback alpha-beta: alpha={proposed['alpha_beta']['alpha']:g}, beta={proposed['alpha_beta']['beta']:g}",
             f"Proposal: {record['proposal_id']}", f"Proposal SHA-256: {proposal['record_sha256']}"]
    if record["support"]:
        lines.append(f"Weights: json {proposed['checkpoint_sha256']['json'][:16]}…, "
                     f"npz {proposed['checkpoint_sha256']['npz'][:16]}…")
        lines.append(f"Evidence SHA-256: {record['support']['evidence_sha256']}")
        lines += [f"  vs {row['baseline']}: {row['baseline_to_model_qber_ratio']:.2f}x lower QBER, "
                  f"{row['wins']} wins, Holm p {row['holm_p_value']:.2g}" for row in record["support"]["comparisons"]]
    if record["rollback_of"]:
        lines.insert(0, "ROLLBACK · recorded previous configuration")
    return lines


class _Confirmation(AdaptiveDialog):
    """Common frame: the captured record, one unchecked declaration, optional operator label."""

    def __init__(self, title, heading, lines, declaration, button, parent=None, *, label=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(680, 520)
        self.setStyleSheet(f"QDialog{{background:{C.raised};}} QCheckBox{{color:{C.ink};}} "
                           f"QPlainTextEdit,QLineEdit{{background:{C.void};color:{C.ink};border:1px solid {C.line};}}")
        layout = vbox(self, (18, 16, 18, 16), 10)
        layout.addWidget(Section(heading, C.amber))
        details = EvidenceText("\n".join(lines))
        details.setReadOnly(True)
        details.setFont(mono(10))
        layout.addWidget(details, 1)
        self.operator_label = QLineEdit()
        self.operator_label.setMaxLength(80)
        self.operator_label.setPlaceholderText("Operator label (declared; not authenticated)")
        self.operator_label.setVisible(label)
        layout.addWidget(self.operator_label)
        self.confirmed = QCheckBox(declaration)
        layout.addWidget(self.confirmed)
        layout.addWidget(Prose("Simulation only; no physical action. Context and expiry are checked again.", 11))
        row_widget = QWidget()
        row = hbox(row_widget, (0, 0, 0, 0), 10)
        cancel, self.accept_button = Btn("CANCEL", C.muted), Btn(button, C.amber)
        row.addWidget(cancel)
        row.addWidget(self.accept_button)
        layout.addWidget(row_widget)
        cancel.clicked.connect(self.reject)
        self.accept_button.clicked.connect(self.accept)
        self.confirmed.toggled.connect(self._validate)
        self.operator_label.textChanged.connect(self._validate)
        self._label = label
        self._validate()

    def _valid(self):
        text = self.operator_label.text()
        return self.confirmed.isChecked() and (not self._label or (text == text.strip() and 1 <= len(text) <= 80))

    def _validate(self, *_):
        self.accept_button.setEnabled(self._valid())

    def accept(self):
        if self._valid():
            super().accept()


class PolarisationApprovalConfirmation(_Confirmation):
    def __init__(self, proposal, review, parent=None):
        self._proposal, self._review = proposal, review
        super().__init__("OrionQ · Confirm polarisation approval", "APPROVE CONTROLLER · SIMULATION ONLY",
                         _proposal_lines(proposal) + [f"Review SHA-256: {review['record_sha256']}"],
                         "I approve this controller proposal for simulation only.", "RECORD APPROVAL", parent,
                         label=True)

    def request(self):
        if not self._valid():
            raise ValueError("operator label and explicit approval required")
        payload = activation.approval_payload(self._proposal, self._review, self.operator_label.text())
        PolicyApprovalRequest.from_dict(payload)
        return payload


class PolarisationActivationConfirmation(_Confirmation):
    def __init__(self, proposal, approval, parent=None):
        self._approval = approval
        record = approval["record"]
        super().__init__("OrionQ · Confirm polarisation activation", "ACTIVATE APPROVED CONTROLLER · SIMULATION",
                         _proposal_lines(proposal) + [f"Approval: {record['approval_id']}",
                                                      f"Operator (declared): {record['operator_label']}"],
                         "I activate this approved controller in this simulator.", "ACTIVATE IN SIMULATION", parent)

    def request(self):
        if not self._valid():
            raise ValueError("explicit activation checkbox required")
        return activation.activation_payload(self._approval)


class PolarisationControl(AdaptiveDialog):
    def __init__(self, parent=None, *, journal_path=None):
        super().__init__(parent)
        self._journal_path = journal_path
        self.slot = None
        self.job = LocalModelJob(self)
        self.package = None
        self.evidence = None  # (path, sha256)
        self.proposal = self.review = self.approval = None
        self.last_receipt = None
        self.runs = []
        self._operation = None
        self.setWindowTitle("OrionQ · Polarisation loop · Simulation")
        self.resize(1060, 780)
        self.setMinimumSize(720, 560)
        self.setStyleSheet(f"QDialog {{background:{C.raised};}} QCheckBox {{color:{C.ink};}} "
                           f"QPlainTextEdit, QComboBox, QSpinBox {{background:{C.void};color:{C.ink};border:1px solid {C.line};}}")
        layout = vbox(self, (18, 16, 18, 16), 10)
        layout.addWidget(Section("POLARISATION LOOP · polarisation.compensation.v1 · SIMULATION", C.amber))
        self.state = Prose("", 12, C.ink)
        self.state.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.state)

        sources = QWidget()
        row = hbox(sources, (0, 0, 0, 0), 8)
        self.checkpoint = QComboBox()
        self.checkpoint.addItems(CHECKPOINTS)
        self.checkpoint.setFont(mono(11))
        self.checkpoint.setAccessibleName("Compensation checkpoint to propose")
        self.package_button = Btn("VERIFY QNET ZIP", C.violet)
        self.evidence_button = Btn("SELECT EVIDENCE", C.violet)
        row.addWidget(self.checkpoint, 2)
        row.addWidget(self.package_button, 1)
        row.addWidget(self.evidence_button, 1)
        layout.addWidget(sources)

        lifecycle = QWidget()
        row = hbox(lifecycle, (0, 0, 0, 0), 8)
        self.prepare_button = Btn("PREPARE", C.amber)
        self.review_button = Btn("REVIEW", C.amber)
        self.approve_button = Btn("APPROVE…", C.amber)
        self.activate_button = Btn("ACTIVATE…", C.amber)
        self.read_button = Btn("READ ACTIVE", C.violet)
        self.rollback_button = Btn("PREPARE ROLLBACK", C.muted)
        for button in (self.prepare_button, self.review_button, self.approve_button, self.activate_button,
                       self.read_button, self.rollback_button):
            row.addWidget(button, 1)
        layout.addWidget(lifecycle)

        loop = QWidget()
        row = hbox(loop, (0, 0, 0, 0), 8)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2_147_483_647)
        self.seed.setValue(2026)
        self.seed.setPrefix("plant seed ")
        self.seed.setFont(mono(11))
        self.fault = QCheckBox("Inject 100 ms of invalid proposals at t = 5 s")
        self.run_button = Btn("RUN LOOP · 20 s", C.green)
        self.export_button = Btn("EXPORT RECEIPT", C.muted)
        row.addWidget(self.seed, 1)
        row.addWidget(self.fault, 2)
        row.addWidget(self.run_button, 1)
        row.addWidget(self.export_button, 1)
        layout.addWidget(loop)

        self.comparison_button = Btn("COMPARE CONTROLLERS · REVIEWED SCENARIOS", C.violet)
        self.comparison_button.clicked.connect(self._compare_controllers)
        layout.addWidget(self.comparison_button)

        layout.addWidget(Section("QBER PER 100 ms · LAST THREE RUNS · DASHED LINE = 11 % (NO KEY ABOVE)"))
        self.chart = LineChart()
        self.chart.setMinimumHeight(170)
        self.chart.setAccessibleName("QBER over time for the last three loop runs")
        self.chart.set_chart([], 0, 0.15, 0.11, [])
        layout.addWidget(self.chart, 1)
        self.legend = Prose("", 10.5)
        self.legend.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.legend)
        self.output = EvidenceText()
        self.output.setReadOnly(True)
        self.output.setFont(mono(11))
        self.output.setAccessibleName("Loop runs and lifecycle receipts")
        layout.addWidget(self.output, 1)
        layout.addWidget(Prose("SIMULATION ONLY · calibrated aerial-fibre plant, 2 ms control, 40 ms latency, ideal "
                               "actuator. The supervisor refuses invalid corrections and falls back to alpha-beta. "
                               "No physical action; models stay OFF until activated here.", 11, C.amber))
        close = Btn("CLOSE", C.muted)
        close.clicked.connect(self.reject)
        layout.addWidget(close)

        self.package_button.clicked.connect(self._choose_package)
        self.evidence_button.clicked.connect(self._choose_evidence)
        self.checkpoint.currentIndexChanged.connect(self._discard_selection)
        self.prepare_button.clicked.connect(self._prepare)
        self.review_button.clicked.connect(self._review)
        self.approve_button.clicked.connect(self._approve)
        self.activate_button.clicked.connect(self._activate)
        self.read_button.clicked.connect(lambda: self._submit("read", self._slot().status))
        self.rollback_button.clicked.connect(lambda: self._submit("rollback", self._slot().prepare_rollback))
        self.run_button.clicked.connect(self._run)
        self.export_button.clicked.connect(self._export)
        self.job.changed.connect(self._finished)
        self._message = "Classical alpha-beta baseline. RUN LOOP, or verify a package and select evidence to propose a model."
        self._refresh()

    # -- plumbing -----------------------------------------------------------

    def _compare_controllers(self):
        from .controller_comparison import ControllerComparison
        archive = None if self.package is None else self.package.path
        dialog = ControllerComparison(self, model_id=self.checkpoint.currentText(), archive_path=archive)
        dialog.exec()
        dialog.deleteLater()

    def _slot(self):
        if self.slot is None:
            path = Path(self._journal_path or default_journal())
            path.parent.mkdir(parents=True, exist_ok=True)
            self.slot = activation.PolarisationControlSlot(path)
        return self.slot

    def _submit(self, operation, function, *arguments):
        if self.job.active:
            return
        self._slot()
        # submit() first clears (and announces) the previous result; name the
        # operation only afterwards so that announcement is not taken as its end.
        self._operation = None
        self.job.submit(function, *arguments)
        self._operation = operation
        if not self.job.active:
            self._finished()

    def _discard_selection(self, *_):
        self.proposal = self.review = self.approval = None
        if self.slot is not None and not self.job.active:
            self.slot.discard_selection()
        self._refresh()

    def _live(self):
        return None if self.slot is None else self.slot.policy["controller_id"]

    # -- selections ---------------------------------------------------------

    def _choose_package(self):
        if self.job.active:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Reviewed QNET package (read only, max 32 MiB)", "",
                                              "Reviewed ZIP (*.zip)")
        if path and self.isVisible() and not self.job.active:
            from quantum_autopilot.model_intake.verification import verify_model_archive
            self._discard_selection()
            self._submit("package", verify_model_archive, self.checkpoint.currentText(), path)

    def _choose_evidence(self):
        if self.job.active:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Closed-loop campaign summary (max 1 MiB)", "", "JSON (*.json)")
        if not path or not self.isVisible():
            return
        self._discard_selection()
        try:
            with open(path, "rb") as stream:
                raw = stream.read(MAX_EVIDENCE_BYTES + 1)
            if len(raw) > MAX_EVIDENCE_BYTES:
                raise ValueError("evidence exceeds 1 MiB")
            self.evidence = (path, hashlib.sha256(raw).hexdigest())
            self._message = f"Evidence selected: {path}\nSHA-256 {self.evidence[1]} (checked again at every step)."
        except (OSError, ValueError) as exc:
            self.evidence = None
            self._message = "EVIDENCE REFUSED · " + str(exc)
        self._refresh()

    # -- lifecycle ----------------------------------------------------------

    def _prepare(self):
        if self.evidence is None:
            return
        target = self.checkpoint.currentText()
        self._submit("prepare", lambda: self._slot().prepare(target, evidence_path=self.evidence[0],
                                                              evidence_sha256=self.evidence[1]))

    def _review(self):
        if self.proposal is not None:
            record = self.proposal
            self._submit("review", self._slot().review, record["record"]["proposal_id"], record["record_sha256"])

    def _approve(self):
        if self.proposal is None or self.review is None or self.job.active:
            return
        dialog = PolarisationApprovalConfirmation(self.proposal, self.review, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            payload = dialog.request()
        except ValueError as exc:
            self._message = "APPROVAL NOT RECORDED · " + str(exc)
            self._refresh()
            return
        self._submit("approve", self._slot().approve, payload)

    def _activate(self):
        if self.approval is None or self.job.active:
            return
        dialog = PolarisationActivationConfirmation(self.proposal, self.approval, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._submit("activate", self._slot().activate, dialog.request())

    def _run(self):
        if self.job.active:
            return
        from quantum_autopilot.control_plane import polarisation_plant as plant

        slot, seed, faults = self._slot(), int(self.seed.value()), FAULT_STEPS if self.fault.isChecked() else ()

        def run():
            trajectory = plant.simulate(plant.OPERATING_POINTS[slot.operating_point], DURATION_S, seed)
            return slot.run(trajectory, plant_seed=seed, monitoring_seed=seed + 6000, fault_steps=faults)

        self._submit("run", run)

    def _finished(self):
        if self.job.active or self._operation is None:
            self._refresh()
            return
        operation, self._operation = self._operation, None
        if self.job.error:
            self._message = f"REFUSED · {operation.upper()} · {self.job.error}"
            if operation in {"activate", "approve", "review"}:
                self.proposal = self.review = self.approval = None
        elif self.job.result is not None:
            result = self.job.result
            if operation == "package":
                try:
                    self._slot().select_archive(result.path)
                except ValueError as exc:
                    self._message = "PACKAGE NOT APPLIED · " + str(exc)
                else:
                    self.package = result
                    self._message = result.describe()
            elif operation in {"prepare", "rollback"}:
                self.proposal, self.review, self.approval = result, None, None
                self.last_receipt = result
                self._message = "PROPOSAL CAPTURED · not approved, not active\n" + "\n".join(_proposal_lines(result))
            elif operation == "review":
                self.review, self.last_receipt = result, result
                self._message = f"REVIEW CURRENT at {result['record']['reviewed_at_utc']} · not an approval"
            elif operation == "approve":
                self.approval, self.last_receipt = result, result
                self._message = f"APPROVAL RECORDED · {result['record']['approval_id']} · not active yet"
            elif operation == "activate":
                self.proposal = self.review = self.approval = None
                self.last_receipt = result
                record = result["record"]
                self._message = (f"COMMITTED · {record['transition_kind'].upper()} · "
                                 f"{record['active_policy']['controller_id']} · READ ACTIVE to observe the live state")
            elif operation == "read":
                self.last_receipt = result
                record = result["record"]
                self._message = f"READBACK · {record['status']} · live {record['live_policy']['controller_id']}"
            elif operation == "run":
                loop_record, receipt = result
                self.last_receipt = receipt
                self.runs.append(loop_record)
                self._message = f"RUN RECORDED · journal cycle under {receipt['record']['activation_status']}"
        self._refresh()

    # -- export and view ----------------------------------------------------

    def _export(self):
        receipt = self.last_receipt
        if receipt is None or self.job.active:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save receipt (new file only)", "polarisation-receipt.json",
                                              "JSON (*.json)")
        if not path or self.last_receipt is not receipt:
            return
        try:
            with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(receipt, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
            self._message = "EXPORTED · " + path
        except OSError as exc:
            self._message = "EXPORT REFUSED · " + str(exc)
        self._refresh()

    def _table(self):
        lines = ["RUN  CONTROLLER                           SEED   QBER    KEY kbit/s  FAULTS  DEGRADED AT"]
        for index, run in enumerate(self.runs, start=1):
            degraded = run.supervisor["degraded_at_s"]
            lines.append(f"{index:<4} {run.controller_id:<36} {run.plant_seed:<6} {run.summary['mean_qber']:.4f}  "
                         f"{run.summary['mean_key_rate_hz'] / 1e3:>10.1f}  {run.supervisor['faults']:>6}  "
                         f"{'—' if degraded is None else f'{degraded:.3f} s'}")
        return "\n".join(lines) if self.runs else "No loop runs yet."

    def _draw(self):
        recent = self.runs[-3:]
        colours = []
        for run in recent:
            colours.append(C.amber if run.supervisor["faults"] else
                           C.muted if run.controller_id.startswith("baseline.") else C.green)
        series = [(list(run.qber_trace_100ms), colour, 2.0 if run is recent[-1] else 1.2, run is not recent[-1])
                  for run, colour in zip(recent, colours)]
        peak = max((max(run.qber_trace_100ms, default=0.0) for run in recent), default=0.0)
        duration = recent[-1].duration_s if recent else DURATION_S
        self.chart.set_chart(series, 0.0, min(max(0.15, peak * 1.1), 0.5), 0.11,
                             [f"{duration * i / 4:g} s" for i in range(5)])
        names = {C.amber: "amber = fault injected", C.muted: "grey = classical", C.green: "green = learned"}
        self.legend.setText(("Runs " + ", ".join(str(len(self.runs) - len(recent) + i + 1) for i in range(len(recent)))
                             + " · solid = latest · " + " · ".join(dict.fromkeys(names[c] for c in colours)))
                            if recent else "No loop runs yet.")

    def _refresh(self):
        self._draw()
        busy = self.job.active
        live = self._live() or "baseline.alpha_beta (not started)"
        package = "none" if self.package is None else f"{self.package.sha256[:16]}…"
        evidence = "none" if self.evidence is None else f"{self.evidence[1][:16]}…"
        self.state.setText(f"Live controller: {live}\nWeights package: {package} · Evidence: {evidence}\n"
                           f"Selection: {'approved' if self.approval else 'reviewed' if self.review else 'prepared' if self.proposal else 'none'}")
        for widget in (self.package_button, self.evidence_button, self.checkpoint, self.seed, self.fault,
                       self.run_button, self.read_button, self.comparison_button):
            widget.setEnabled(not busy)
        self.prepare_button.setEnabled(not busy and self.evidence is not None)
        self.review_button.setEnabled(not busy and self.proposal is not None and self.review is None)
        self.approve_button.setEnabled(not busy and self.review is not None and self.approval is None)
        self.activate_button.setEnabled(not busy and self.approval is not None)
        self.rollback_button.setEnabled(not busy and self._live() not in (None, "baseline.alpha_beta"))
        self.export_button.setEnabled(not busy and self.last_receipt is not None)
        status = "RUNNING / FINISHING…" if busy else self._message
        self.output.set_evidence(f"{status}\n\n{self._table()}")

    def hideEvent(self, event):
        self._discard_selection()
        super().hideEvent(event)


__all__ = ["CHECKPOINTS", "PolarisationActivationConfirmation", "PolarisationApprovalConfirmation",
           "PolarisationControl", "default_journal"]
