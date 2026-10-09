"""Laboratory controls emit requests; all acquisition and analysis run in the backend."""
from __future__ import annotations

import json
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QLabel, QWidget

from .evidence_text import EvidenceText
from .prims import Btn, hbox, vbox


class LaboratoryPanel(QWidget):
    command = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = vbox(self, (0, 0, 0, 0), 8)
        self.scope = QLabel("LABORATÓRIO · Emulação identificada e replay com origem preservada. "
                            "Entrega HTTPS/mTLS de chaves de teste; segurança QKD certificada e hardware não validados.")
        self.scope.setWordWrap(True)
        layout.addWidget(self.scope)
        self.summary = QLabel("Nenhuma execução de laboratório nesta fonte.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        controls = QWidget()
        row = hbox(controls, (0, 0, 0, 0), 8)
        self.buttons = {}
        for experiment, label in (("photon", "AQUISIÇÃO DE FÓTONS"), ("qkd", "QKD + APLICAÇÕES"), ("all", "BANCADA COMPLETA")):
            button = Btn(label)
            button.setProperty("operation", "run_lab_experiment")
            button.clicked.connect(lambda checked=False, value=experiment: self.command.emit(
                "run_lab_experiment", json.dumps({"experiment": value})))
            button.setEnabled(False)
            row.addWidget(button)
            self.buttons[experiment] = button
        layout.addWidget(controls)
        self.configure_button = Btn("CONFIGURAR EXPERIMENTO")
        self.configure_button.setToolTip("Validar e salvar adaptador e parâmetros da bancada simulada.")
        self.configure_button.clicked.connect(self._configure)
        self.configure_button.setEnabled(False)
        layout.addWidget(self.configure_button)
        self._configuration = None
        self.evidence = EvidenceText()
        self.evidence.setMinimumHeight(180)
        layout.addWidget(self.evidence)
        self._text = None

    def _configure(self):
        if self._configuration is None:
            return
        from .laboratory_configuration import LaboratoryConfigurationDialog
        dialog = LaboratoryConfigurationDialog(self._configuration, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.command.emit("configure_laboratory", json.dumps(dialog.request()))
        dialog.deleteLater()

    def set_snapshot(self, snapshot):
        enabled = snapshot.capabilities.allows("run_lab_experiment")
        laboratory = snapshot.laboratory or {}
        self._configuration = laboratory.get("configuration")
        availability = (laboratory.get("capabilities") or {}).get("availability", {})
        if not availability:
            availability = (self._configuration or {}).get("availability", {})
        for name, button in self.buttons.items():
            experiment = availability.get("experiments", {}).get(name, {})
            button.setEnabled(enabled and experiment.get("available", True))
            missing = experiment.get("missing_dependencies", [])
            key_security = experiment.get("security", {})
            identity_missing = key_security.get("missing_identities", []) + key_security.get("incompatible_identities", [])
            button.setToolTip("Dependências ausentes: " + ", ".join(missing) if missing else
                              "Identidades SAE/KME não provisionadas ou incompatíveis: " + ", ".join(identity_missing)
                              if identity_missing else
                              "Executa o pipeline local de emulação e guarda recibos verificáveis.")
        self.configure_button.setEnabled(snapshot.capabilities.allows("configure_laboratory") and
                                         self._configuration is not None)
        runs = (snapshot.laboratory or {}).get("runs", [])
        lines = []
        if runs:
            lines.append(f"Último recibo: {runs[0]['run_id']} · {runs[0]['status']} · {runs[0]['mode']}")
            photon = next((run.get("results", {}).get("photon") for run in runs
                           if run.get("results", {}).get("photon")), None)
            if photon and "nominal" in photon:
                value = photon["nominal"]
                lines.append(f"Fótons emulados: {value['counts']} contagens · {value['coincidences']} coincidências · "
                             f"janela total {value['coincidence_window_total_width_ps']} ps · entrelaçamento não caracterizado.")
            qkd = next((run.get("results", {}).get("qkd") for run in runs
                        if run.get("results", {}).get("qkd")), None)
            if qkd and "qkd_session" in qkd:
                value = qkd["qkd_session"]
                qber = value.get("qber")
                lines.append(f"QKD emulado: QBER {qber:.2%} · {value['demo_key_bits']} bits de teste · "
                             f"{value['certified_secret_bits']} bits certificados · AES-GCM e mTLS verificados."
                             if qber is not None else "QKD: sem amostra válida para estimação de QBER.")
        self.summary.setText("\n".join(lines) or "Nenhuma execução de laboratório nesta fonte.")
        text = json.dumps(snapshot.laboratory or {"runs": [], "note": "Nenhum recibo de laboratório nesta fonte"},
                          ensure_ascii=False, indent=2)
        if text != self._text:
            self.evidence.set_evidence(text)
            self._text = text
