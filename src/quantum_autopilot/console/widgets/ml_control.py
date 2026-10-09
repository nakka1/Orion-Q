"""Confirmed operator permission, displayed only from backend readback."""
import json

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QAbstractButton, QWidget

from ..theme import C, mono, qc
from .adaptive_dialog import AdaptiveDialog
from .prims import Btn, Prose, hbox, vbox


class MLControlSwitch(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled_mode = False
        self.setFixedSize(96, 30)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Automatic ML network control")
        self.setToolTip("Allow or block learned network controllers. Requires confirmation; reference controls and attack detection remain active.")

    def set_mode(self, enabled):
        if self.enabled_mode != enabled:
            self.enabled_mode = enabled
            self.update()
        self.setAccessibleDescription("ML control permitted" if enabled else "ML control disabled")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(1.0 if self.isEnabled() else 0.45)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(C.acc if self.enabled_mode else C.ink3))
        p.drawRoundedRect(QRectF(2, 6, 38, 18), 9, 9)
        p.setBrush(qc(C.bg))
        p.drawEllipse(QRectF(22 if self.enabled_mode else 4, 8, 14, 14))
        p.setFont(mono(11)); p.setPen(qc(C.ink))
        p.drawText(QRectF(43, 0, self.width() - 45, 30), Qt.AlignmentFlag.AlignVCenter, "ML: ON" if self.enabled_mode else "ML: OFF")
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(qc(C.acc)); p.drawRect(self.rect().adjusted(1, 1, -2, -2))
        p.end()


class MLControlConfirmation(AdaptiveDialog):
    def __init__(self, enabled, revision, parent=None):
        super().__init__(parent)
        self.enabled, self.revision = enabled, revision
        action = "habilitar" if enabled else "desabilitar"
        self.setWindowTitle(f"OrionQ · Confirmar controle ML: {action}")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(610, 420)
        self.setMinimumSize(420, 280)
        self.setStyleSheet(f"QDialog{{background:{C.raised};}}")
        layout = vbox(self, (20, 18, 20, 18), 12)
        layout.addWidget(Prose(f"Tem certeza de que deseja {action} o controle automático por ML?", 17))
        explanation = (
            "Ao habilitar, modelos com pesos verificados podem controlar compensação, sincronização, taxa de chaves, roteamento e alocação, quando seus contratos permitirem. "
            "Modelos ausentes, fora do domínio ou com falhas continuam usando os controladores de referência. "
            "Possíveis problemas: previsões inadequadas fora das condições de treinamento, falhas de inferência, maior custo de processamento e transientes ao inicializar controladores. "
            "O switch ligado indica permissão; consulte os recibos para saber qual modelo realmente atuou."
            if enabled else
            "Ao desabilitar, os controladores de referência assumem imediatamente e os modelos aprendidos deixam de controlar a rede. "
            "Possíveis problemas: menor adaptação, rotas menos eficientes ou redução de desempenho em condições nas quais o ML validado ajudava. "
            "Controladores podem ter um breve transiente de inicialização; desligar não desfaz decisões anteriores nem restaura automaticamente as configurações do transmissor."
        )
        layout.addWidget(Prose(explanation, 13), 1)
        layout.addWidget(Prose("A coleta, a detecção de ataques e as regras de segurança continuam ativas. A mudança vale apenas para a simulação: nenhuma atuação física.", 12))
        buttons = QWidget(); row = hbox(buttons, (0, 0, 0, 0), 12)
        self.cancel_button = Btn("CANCELAR")
        self.confirm_button = Btn("HABILITAR ML" if enabled else "DESABILITAR ML", variant="accent")
        self.cancel_button.clicked.connect(self.reject); self.confirm_button.clicked.connect(self.accept)
        row.addWidget(self.cancel_button); row.addWidget(self.confirm_button)
        layout.addWidget(buttons); self.fixed_footer = (buttons,)

    def request(self):
        return {"enabled": self.enabled, "expected_revision": self.revision, "confirmed": True}


class MLControlPanel(QWidget):
    command = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.switch = MLControlSwitch(self)
        layout = hbox(self, (0, 0, 0, 0), 0); layout.addWidget(self.switch)
        self._snapshot = self._context = self._pending = self.dialog = None
        self.switch.clicked.connect(self._confirm)
        self.switch.setEnabled(False)

    def set_snapshot(self, snap):
        context = (snap.input_kind, snap.campaign, snap.topology.id, snap.capabilities.adapter_id)
        if self._context != context:
            self._pending = None
            if self.dialog is not None:
                self.dialog.reject()
        self._snapshot, self._context = snap, context
        live = snap.live; policy = live.policy if live is not None else {}
        mode = policy.get("ml_control_enabled")
        valid = type(mode) is bool and type(policy.get("ml_control_revision")) is int
        if valid:
            self.switch.set_mode(mode)
        if self._pending is not None and valid and policy["ml_control_revision"] > self._pending[1]:
            self._pending = None
        ready = valid and live.source_kind == "simulated" and snap.capabilities.allows("set_ml_control")
        self.setVisible(snap.capabilities.allows("set_ml_control"))
        self.switch.setEnabled(ready and self._pending is None)
        active = [model.role for model in live.models if model.state == "active" and model.role != "detector"] if live else []
        self.switch.setToolTip(
            ("Waiting for backend confirmation. " if self._pending else "") +
            f"ML control {'permitted' if mode else 'disabled'}. Active learned controls: {', '.join(active) or 'none'}. "
            "Click to review effects and confirm a change. Reference controls, attack detection and safety rules remain active. Simulation only.")

    def _confirm(self):
        if self._snapshot is None or not self.switch.isEnabled():
            return
        snap = self._snapshot; context = self._context
        revision = snap.live.policy["ml_control_revision"]
        dialog = MLControlConfirmation(not self.switch.enabled_mode, revision, self)
        self.dialog = dialog
        try:
            accepted = dialog.exec() == AdaptiveDialog.DialogCode.Accepted
            if accepted and self._context == context:
                self._pending = (dialog.enabled, revision)
                self.switch.setEnabled(False)
                self.command.emit("set_ml_control", json.dumps(dialog.request()))
        finally:
            self.dialog = None; dialog.deleteLater()

    def command_failed(self, message):
        if self._pending is not None:
            self._pending = None
            self.set_snapshot(self._snapshot)
            self.switch.setToolTip(f"ML change refused: {message}")
