"""Read-only historical recommendations, separate from the live Q05 slot."""
from pathlib import Path

from PySide6.QtWidgets import QComboBox, QFileDialog, QWidget

from quantum_autopilot.control_plane import controller_evidence as evidence
from ..model_archive_check import LocalModelJob
from ..theme import C, mono
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText
from .prims import Btn, Prose, hbox, vbox


def display_report(report):
    record = evidence.verify_report(report)
    scenario, comparison = record["request"]["scenario"], record["comparison"]
    controller = record["recommended_controller"] or "No recommendation"
    slew = scenario["slew_rate_rad_per_s"]
    actuator = "Ideal actuator" if slew is None else f"Actuator limit: {slew:g} rad/s"
    lines = [record["status"], f"Recommended: {controller}", "", record["reason"], "",
             f"Operating point: {scenario['operating_point']} · {actuator}",
             f"Historical model: {record['request']['model_id']}", ""]
    if comparison is not None:
        lines += [f"Mean QBER · model: {comparison['model_mean_qber']:.6%}",
                  f"Mean QBER · alpha-beta: {comparison['baseline_mean_qber']:.6%}",
                  f"Reference/model ratio: {comparison['baseline_to_model_qber_ratio']:.6g}",
                  f"95% CI: {comparison['ratio_ci95']}",
                  f"Seeds: {comparison['n_seeds']} · Model wins: {comparison['model_wins']}",
                  f"Holm-adjusted p: {comparison['holm_p_value']:.6g}", ""]
    lines += [f"Duration: {scenario['duration_s']:g} s · Control period: {scenario['control_period_s'] * 1000:g} ms",
              f"Nominal latency: {scenario['latency_s'] * 1000:g} ms",
              f"Weights: {record['weights_verification']}", "", "Validity:",
              *[f"• {item}" for item in record["limitations"]], "", "Reviewed sources:",
              *record["sources"], "", f"Receipt SHA256: {report['record_sha256']}"]
    return "\n".join(lines)


class ControllerComparison(AdaptiveDialog):
    def __init__(self, parent=None, *, model_id="qnet.compensation.calibrado.ridge", archive_path=None):
        super().__init__(parent)
        self.setWindowTitle("OrionQ · Controller comparison")
        self.resize(850, 690)
        self.archive_path = archive_path
        self.report = None
        self._operation = None
        self.job = LocalModelJob(self)
        layout = vbox(self, (18, 16, 18, 16), 10)
        layout.addWidget(Prose("Compare a reviewed simulated scenario and export its evidence.", 13))
        layout.addWidget(Prose("Historical comparison · Recommendations do not change the active controller. "
                               "The active loop uses its existing ideal actuator.", 11, C.amber))
        self.profile = QComboBox()
        self.profile.setAccessibleName("Reviewed controller and actuator profile")
        self.profile.setFont(mono(10))
        try:
            catalog = evidence.load_catalog()
            for profile in catalog["profiles"]:
                scenario = profile["scenario"]
                slew = scenario["slew_rate_rad_per_s"]
                device = "ideal actuator" if slew is None else f"{slew:g} rad/s"
                self.profile.addItem(f"{profile['model_id']} · {device}", profile)
            index = next((i for i in range(self.profile.count())
                          if self.profile.itemData(i)["model_id"] == model_id), 0)
            self.profile.setCurrentIndex(index)
        except (OSError, ValueError) as exc:
            self.profile.setEnabled(False)
            self._catalog_error = str(exc)
        layout.addWidget(self.profile)
        actions = QWidget()
        row = hbox(actions, (0, 0, 0, 0), 10)
        self.compare_button = Btn("COMPARE CONTROLLERS", C.violet)
        self.export_button = Btn("EXPORT COMPARISON", C.muted)
        row.addWidget(self.compare_button)
        row.addWidget(self.export_button)
        layout.addWidget(actions)
        self.output = EvidenceText()
        self.output.setReadOnly(True)
        self.output.setFont(mono(10))
        self.output.setAccessibleName("Historical controller recommendation and evidence")
        self.output.set_evidence(getattr(self, "_catalog_error", "Select a reviewed profile and compare."))
        layout.addWidget(self.output, 1)
        close = Btn("CLOSE", C.muted)
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        self.profile.currentIndexChanged.connect(self._invalidate)
        self.compare_button.clicked.connect(self._compare)
        self.export_button.clicked.connect(self._export)
        self.job.changed.connect(self._finished)
        self._refresh()

    def _invalidate(self, *_):
        self.report = None
        self._operation = None
        self.job.clear()
        self.output.set_evidence("Selection changed; compare the selected profile again.")
        self._refresh()

    def _submit(self, operation, function):
        if self.job.active:
            return
        self._operation = None
        self.job.submit(function)
        self._operation = operation
        if not self.job.active:
            self._finished()

    def _compare(self):
        profile = self.profile.currentData()
        if profile is None or self.job.active:
            return
        request = evidence.profile_request(profile)
        archive_path = self.archive_path
        self.report = None

        def evaluate():
            verified = False
            if archive_path is not None:
                from quantum_autopilot.control_plane.polarisation_loop import check_checkpoint
                from quantum_autopilot.model_intake.artifacts import ReviewedArchive

                decision = check_checkpoint(request["model_id"], ReviewedArchive(archive_path, "qnet"),
                                            request["scenario"]["operating_point"])
                request["checkpoint_sha256"] = decision.checkpoint_sha256
                verified = decision.status == "ADMITTED"
            return evidence.recommend(request, weights_verified=verified)

        self._submit("compare", evaluate)

    def _export(self):
        captured = self.report
        if captured is None or self.job.active:
            return
        parent = QFileDialog.getExistingDirectory(self, "Choose parent folder for a new comparison package")
        if not parent or self.report is not captured:
            return
        target = Path(parent) / f"orion-comparison-{captured['record_sha256'][:12]}"
        self._submit("export", lambda: evidence.export_report(captured, target))

    def _finished(self):
        if self.job.active or self._operation is None:
            self._refresh()
            return
        operation, self._operation = self._operation, None
        if self.job.error:
            self.output.set_evidence(f"{operation.upper()} REFUSED · {self.job.error}")
        elif self.job.result is not None:
            if operation == "compare":
                self.report = self.job.result
                self.output.set_evidence(display_report(self.report))
            else:
                self.output.set_evidence(f"COMPARISON EXPORTED · {self.job.result}")
        self._refresh()

    def _refresh(self):
        self.compare_button.setEnabled(not self.job.active and self.profile.count() > 0)
        self.export_button.setEnabled(not self.job.active and self.report is not None)

    def hideEvent(self, event):
        self._invalidate()
        super().hideEvent(event)
