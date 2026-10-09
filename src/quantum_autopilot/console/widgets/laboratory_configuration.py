"""Bounded experimental forms emit validated backend configuration requests."""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QLabel, QSpinBox, QStackedWidget, QVBoxLayout,
)

from .adaptive_dialog import AdaptiveDialog


class LaboratoryConfigurationDialog(AdaptiveDialog):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuração experimental · emulação")
        self.resize(570, 620)
        self.revision = state["revision"]
        self.replays = {item["run_id"]: item for item in state.get("replay_candidates", [])}
        config = state["configuration"]
        layout = QVBoxLayout(self)
        scope = QLabel(f"Revisão {self.revision}. Parâmetros aplicados pelo backend após validação integral. "
                       "Alterações exigem uma nova execução; SDK e hardware têm validação separada.")
        scope.setWordWrap(True)
        layout.addWidget(scope)
        self.controls = {}
        self.field_labels = {}
        self.field_stacks = {}
        self.readonly_values = {}
        self._original_values = {}
        self._preserved_fields = set()
        self._emulation_channels = list(config["photon"]["parameters"]["channels"])
        self._emulation_pulse_level_v = config["photon"]["parameters"]["pulse_level_v"]
        for name, title in (("photon", "Aquisição de fótons"), ("qkd", "QKD e aplicações")):
            group = QGroupBox(title)
            form = QFormLayout(group)
            layout.addWidget(group)
            if name == "photon":
                self.adapter = QComboBox()
                for adapter in ("emulated", "emulated-reference"):
                    self.adapter.addItem(adapter, adapter)
                for run_id in self.replays:
                    self.adapter.addItem("Replay ORION · " + run_id[:8], "replay:" + run_id)
                self.adapter.setCurrentIndex(max(0, self.adapter.findData(config[name]["adapter_id"])))
                if config[name]["adapter_id"] == "orion-replay":
                    self.adapter.setCurrentIndex(max(0, self.adapter.findData("replay:" + config[name]["replay_run_id"])))
                self.adapter.setToolTip("Seleciona uma implementação registrada; fonte simulada identificada.")
                form.addRow("Adaptador", self.adapter)
            definitions = (
                (("seed", "Semente", 0, 2147483647, True),
                 ("duration_ps", "Duração (s)", .001, 60, False),
                 ("pair_rate_hz", "Emissão (pares/s)", 1, 1e7, False),
                 ("coincidence_window_ps", "Janela total (ps)", 1, 1000000, True),
                 ("trigger_0", "Limiar canal 1 (V)", -2.5, 2.5, False),
                 ("trigger_1", "Limiar canal 2 (V)", -2.5, 2.5, False)) if name == "photon" else
                (("seed", "Semente", 0, 2147483647, True),
                 ("emitted_pulses", "Pulsos emitidos", 100, 1000000, True),
                 ("acquisition_s", "Duração (s)", .001, 3600, False),
                 ("channel_loss_db", "Perda (dB)", 0, 100, False),
                 ("detector_efficiency", "Eficiência (0–1)", 0, 1, False),
                 ("bit_error_probability", "Probabilidade de erro (0–0,5)", 0, .5, False)))
            for key, label, minimum, maximum, integral in definitions:
                widget = QSpinBox() if integral else QDoubleSpinBox()
                if not integral:
                    widget.setDecimals(10 if key.startswith("trigger_") else 4)
                widget.setRange(minimum, maximum)
                values = config[name]["parameters"]
                value = (values["trigger_levels_v"][int(key[-1])] if key.startswith("trigger_")
                         else values[key] / 1e12 if key == "duration_ps" else values[key])
                self._original_values[(name, key)] = (values["trigger_levels_v"][int(key[-1])]
                                                      if key.startswith("trigger_") else values[key])
                # QSpinBox is a 32-bit editor; source replay values are rendered
                # separately and never coerced through its numeric limits.
                widget.setValue(min(widget.maximum(), max(widget.minimum(), value)))
                widget.setToolTip(label + "; limites locais do demonstrador, não especificação universal de hardware.")
                field_label = QLabel(label)
                self.field_labels[(name, key)] = field_label
                stack = QStackedWidget()
                stack.addWidget(widget)
                readonly = QLabel()
                readonly.setWordWrap(True)
                readonly.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                stack.addWidget(readonly)
                self.field_stacks[(name, key)] = stack
                self.readonly_values[(name, key)] = readonly
                form.addRow(field_label, stack)
                self.controls[(name, key)] = widget
        self.footer = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.footer.accepted.connect(self.accept)
        self.footer.rejected.connect(self.reject)
        layout.addWidget(self.footer)
        self.fixed_footer = (self.footer,)
        self._trigger_limits(self._emulation_channels, self._emulation_pulse_level_v)
        for key, original in self._original_values.items():
            represented = (int(round(self.controls[key].value() * 1e12))
                           if key[1] == "duration_ps" else self.controls[key].value())
            if represented != original:
                self._preserved_fields.add(key)
            readonly = self.readonly_values[key]
            readonly.setText(self._original_value(key[1], config[key[0]]["parameters"]))
            readonly.setToolTip("Valor original preservado. A precisão ou intervalo excede este editor; "
                                "mudanças avançadas usam a configuração validada pela API.")
            if key in self._preserved_fields:
                self.controls[key].setEnabled(False)
                self.field_stacks[key].setCurrentWidget(readonly)
        self._emulation_values = {key: widget.value() for key, widget in self.controls.items() if key[0] == "photon"}
        self._showing_replay = False
        self.adapter.currentIndexChanged.connect(self._adapter_changed)
        self._adapter_changed()

    def _trigger_limits(self, channels, pulse_level_v):
        for index in (0, 1):
            key = ("photon", f"trigger_{index}")
            self.field_labels[key].setText(f"Limiar canal {channels[index]} (V)")
            widget = self.controls[key]
            precision = 10 ** widget.decimals()
            ceiling = int((Decimal(str(pulse_level_v)) * precision).to_integral_value(rounding=ROUND_CEILING))
            widget.setRange(1 / precision, (ceiling - 1) / precision)
            widget.setToolTip(f"Pulso simulado: {pulse_level_v:g} V. Exige 0 V < limiar < {pulse_level_v:g} V. "
                              "O backend valida também as demais dependências antes de salvar ou executar.")

    @staticmethod
    def _original_value(key, parameters):
        if key.startswith("trigger_"):
            return str(parameters["trigger_levels_v"][int(key[-1])]) + " V"
        value = parameters[key]
        if key == "duration_ps":
            return f"{format(Decimal(value) / Decimal(10**12), 'f')} s · {value} ps"
        unit = {"pair_rate_hz": " pares/s", "coincidence_window_ps": " ps", "acquisition_s": " s",
                "channel_loss_db": " dB", "emitted_pulses": " pulsos"}.get(key, "")
        return str(value) + unit

    def _adapter_changed(self):
        selected = self.adapter.currentData()
        replay = selected.startswith("replay:")
        if replay:
            if not self._showing_replay:
                self._emulation_values = {key: widget.value() for key, widget in self.controls.items() if key[0] == "photon"}
            parameters = self.replays[selected.split(":", 1)[1]]["parameters"]
            self._trigger_limits(parameters["channels"], parameters["pulse_level_v"])
            for (group, key), widget in self.controls.items():
                if group != "photon":
                    continue
                value = (parameters["trigger_levels_v"][int(key[-1])] if key.startswith("trigger_")
                         else parameters[key] / 1e12 if key == "duration_ps" else parameters[key])
                widget.setValue(min(widget.maximum(), max(widget.minimum(), value)))
                widget.setEnabled(False)
                widget.setToolTip("Configuração original do registro; replay não altera os parâmetros adquiridos.")
                readonly = self.readonly_values[(group, key)]
                readonly.setText(self._original_value(key, parameters))
                readonly.setToolTip("Valor original preservado integralmente no backend; copiar texto não altera o registro.")
                self.field_stacks[(group, key)].setCurrentWidget(readonly)
        else:
            self._trigger_limits(self._emulation_channels, self._emulation_pulse_level_v)
            for key, value in self._emulation_values.items():
                if self._showing_replay:
                    self.controls[key].setValue(value)
                preserved = key in self._preserved_fields
                self.controls[key].setEnabled(not preserved)
                self.field_stacks[key].setCurrentWidget(self.readonly_values[key] if preserved else self.controls[key])
                if preserved:
                    self.readonly_values[key].setText(self._original_value(key[1], {
                        **{field: value for (group, field), value in self._original_values.items() if group == "photon"},
                        "trigger_levels_v": [self._original_values[("photon", f"trigger_{i}")] for i in (0, 1)]}))
        self._showing_replay = replay

    def request(self):
        parameters = {"photon": {}, "qkd": {}}
        for (group, name), widget in self.controls.items():
            if name.startswith("trigger_"):
                continue
            if (group, name) in self._preserved_fields:
                parameters[group][name] = self._original_values[(group, name)]
            else:
                value = widget.value()
                parameters[group][name] = int(round(value * 1e12)) if name == "duration_ps" else value
        parameters["photon"]["trigger_levels_v"] = [self._original_values[("photon", f"trigger_{i}")]
            if ("photon", f"trigger_{i}") in self._preserved_fields else self.controls[("photon", f"trigger_{i}")].value()
            for i in (0, 1)]
        selected = self.adapter.currentData()
        photon = {"adapter_id": selected, "parameters": parameters["photon"]}
        if selected.startswith("replay:"):
            run_id = selected.split(":", 1)[1]
            photon = {"adapter_id": "orion-replay", "replay_run_id": run_id,
                      "parameters": self.replays[run_id]["parameters"]}
        return {"expected_revision": self.revision,
                "photon": photon,
                "qkd": {"parameters": parameters["qkd"]}}
