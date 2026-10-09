"""Independent axes and sources for received technology-specific observables."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QSizePolicy, QWidget

from ..protocol_series import finite, protocol_series_from_state
from ..theme import C
from .prims import Label, Prose, vbox
from .wave_chart import WaveChart, WaveSeries


TECHNOLOGY_LABELS = {"tf_qkd": "TF-QKD", "entanglement_swapping": "Entrelaçamento/BSM",
                     "quantum_memory": "Memória quântica", "purification": "Purificação",
                     "satellite_doppler": "Satélite/OGS", "satellite_isl": "Satélite/ISL"}


class ProtocolSeriesPanel(QWidget):
    """Only the selected observable is plotted, preserving its own physical unit."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = vbox(self, (0, 0, 0, 0), 6)
        self.title = Label("Séries temporais · observáveis dos protocolos", wrap=True)
        layout.addWidget(self.title)
        self.selector = QComboBox()
        self.selector.setMinimumWidth(0)
        self.selector.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.selector.setAccessibleName("Selecionar tecnologia, sessão e observável temporal")
        layout.addWidget(self.selector)
        self.metadata = Prose("Histórico não publicado pelo backend.", 11.5, C.ink3)
        layout.addWidget(self.metadata)
        self.chart = WaveChart(window_s=120., value_bounds=(None, None), animate=False,
                               empty_message="Aguardando observações recebidas")
        self.chart.setAccessibleName("Observável de protocolo no tempo; pontos recebidos, sem resampling")
        self.chart.setFixedHeight(170)
        layout.addWidget(self.chart)
        self._series = ()
        self._clock = None
        self.selector.currentIndexChanged.connect(self._render)
        self.hide()

    def set_state(self, technologies, *, node_id):
        self._series = protocol_series_from_state(technologies, node_id=node_id)
        self._clock = finite(technologies.get("clock_s"))
        choices = tuple((item.key, f"{TECHNOLOGY_LABELS.get(item.technology, item.technology)} · "
                         f"{item.label} [{item.unit}] · {item.session_id}") for item in self._series)
        prior = tuple((self.selector.itemData(i), self.selector.itemText(i)) for i in range(self.selector.count()))
        if prior != choices:
            selected = self.selector.currentData()
            self.selector.blockSignals(True)
            self.selector.clear()
            for key, label in choices:
                self.selector.addItem(label, key)
            index = self.selector.findData(selected)
            if index < 0:
                index = next((i for i, item in enumerate(self._series)
                              if any(value is not None for _at, value in item.points)), 0)
            self.selector.setCurrentIndex(max(0, index))
            self.selector.blockSignals(False)
        configured = any(node_id in session.get("config", {}).get("path", ())
                         for session in technologies.get("sessions", {}).values())
        self.setVisible(bool(choices) or configured)
        self.selector.setVisible(bool(choices))
        self._render()

    def _render(self, *_):
        selected = next((item for item in self._series if item.key == self.selector.currentData()), None)
        if selected is None:
            self.metadata.setText("Histórico não publicado pelo backend; nenhuma série foi gerada pela interface.")
            self.chart.set_series((), self._clock)
            return
        missing = sum(value is None for _at, value in selected.points)
        values = sum(value is not None for _at, value in selected.points)
        text = (f"{selected.label} · unidade {selected.unit} · escopo {selected.scope} · {selected.session_id}\n"
                f"Origem: {selected.origin} · fonte {selected.source_id}\n"
                f"Tempo: {selected.time_domain} ({selected.clock_kind}) · {values} pontos recebidos · {missing} lacunas")
        if selected.reason:
            text += " · " + selected.reason
        if self.metadata.text() != text:
            self.metadata.setText(text)
        self.selector.setToolTip(self.selector.currentText())
        self.chart.window_s = selected.window_s
        self.chart.empty_message = selected.reason or "Aguardando observações recebidas"
        self.chart.set_value_bounds(selected.bounds)
        self.chart.set_series((WaveSeries(selected.key, selected.label,
            tuple(value for _at, value in selected.points), 0., selected.reason,
            tuple(at for at, _value in selected.points)),), self._clock)

    def content_texts(self):
        return {"selected": self.selector.currentText(), "metadata": self.metadata.text(),
                "choices": tuple(self.selector.itemText(i) for i in range(self.selector.count()))}
