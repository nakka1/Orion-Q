"""Version-pinned simulator configuration form; never measured telemetry."""
import json
from dataclasses import asdict

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit, QWidget

from quantum_autopilot.control_plane.inventory import ConfigureSimulatedLink
from quantum_autopilot.control_plane.channels import ChannelPair, ClassicalChannel, QuantumChannel
from ..theme import C, mono
from .adaptive_dialog import AdaptiveDialog
from .prims import Btn, Prose, Section, hbox, vbox


class LinkEditor(AdaptiveDialog):
    command = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OrionQ · Simulated link configuration")
        self.resize(600, 730)
        self.setMinimumWidth(440)
        self.setFont(mono(10))
        self.setStyleSheet(f"QDialog{{background:{C.raised};color:{C.ink};}}"
                          f"QLineEdit,QComboBox{{background:{C.void};color:{C.ink};border:1px solid {C.line};padding:7px;}}"
                          f"QCheckBox{{color:{C.ink};}}")
        self.snap = self.context = self.pending = self.version = None
        self.busy = False
        layout = vbox(self, (20, 18, 20, 18), 10)
        layout.addWidget(Section("LINK CONFIGURATION · SIMULATOR ONLY", C.amber))
        layout.addWidget(Prose("Nominal fidelity is an assumed input, not a measurement. Saving does not generate entanglement, recompute stored pairs or run a service.", 11))
        self.form = QWidget()
        form = vbox(self.form, (0, 0, 0, 0), 7)
        self.selection = QComboBox()
        form.addWidget(Section("NEW LINK / EXISTING CONFIGURATION"))
        form.addWidget(self.selection)
        self.fields = {}
        for key, label in (("link_id", "LINK ID"), ("left_node_id", "ENDPOINT A"),
                           ("right_node_id", "ENDPOINT B"), ("distance_km", "FIBRE LENGTH · km (0–10000)"),
                           ("nominal_pair_fidelity", "NOMINAL PAIR FIDELITY · fraction (0–1)")):
            form.addWidget(Section(label))
            field = QComboBox() if key.endswith("node_id") else QLineEdit()
            field.setFont(mono(10))
            self.fields[key] = field
            form.addWidget(field)
        form.addWidget(Section("PAIRED CHANNELS · SAME LINK AND ENDPOINTS", C.amber))
        form.addWidget(Prose("Classical: receiver observations and control. Quantum: optical statistical records. "
                            "Local software ports only; no physical qubits or network socket are transmitted.", 11))
        self.channel_fields = {}
        for key, label, default in (
                ("classical_id", "CLASSICAL CHANNEL ID · required", ""),
                ("max_message_bytes", "CLASSICAL MESSAGE LIMIT · bytes", "524288"),
                ("max_age_s", "CLASSICAL MESSAGE MAXIMUM AGE · seconds", "2"),
                ("quantum_id", "QUANTUM CHANNEL ID · required and distinct", "")):
            form.addWidget(Section(label))
            field = QLineEdit(default)
            field.setAccessibleName(label)
            field.setToolTip(label + ". Validated together with both link endpoints before saving.")
            self.channel_fields[key] = field
            form.addWidget(field)
        self.association = Prose("Both channel IDs are associated with this link and endpoint B as receiver.", 11)
        form.addWidget(self.association)
        form.addWidget(Section('QUANTUM RECORD PROTOCOL'))
        self.quantum_protocol = QComboBox()
        self.quantum_protocol.addItems(('bb84-statistical-record-v1','quantum-operation-record-v1'))
        self.quantum_protocol.setToolTip('BB84 statistics for legacy links; operation records for a registered modular protocol session. No physical transport.')
        form.addWidget(self.quantum_protocol)
        self.classical_active = QCheckBox("Classical port enabled in configuration")
        self.quantum_active = QCheckBox("Quantum statistical port enabled in configuration")
        form.addWidget(self.classical_active)
        form.addWidget(self.quantum_active)
        self.active = QCheckBox("Enabled in configuration (not observed link health)")
        form.addWidget(self.active)
        layout.addWidget(self.form)
        self.version_label = Section("VERSION · —")
        layout.addWidget(self.version_label)
        buttons = QWidget()
        row = hbox(buttons, (0, 0, 0, 0), 8)
        self.reload = Btn("RELOAD FORM", C.muted)
        self.save = Btn("SAVE CONFIGURATION", C.amber, filled=True)
        row.addWidget(self.reload)
        row.addWidget(self.save)
        layout.addWidget(buttons)
        self.note = Prose("No physical action. Closing this window does not cancel an accepted write.", 11)
        layout.addWidget(self.note)
        self.fixed_footer = (self.version_label, buttons, self.note)
        self._suggested = ("", "")
        self.fields["link_id"].textChanged.connect(self._suggest_channels)
        self.fields["right_node_id"].currentTextChanged.connect(self._suggest_channels)
        self.fields["left_node_id"].currentTextChanged.connect(self._suggest_channels)
        self.selection.currentIndexChanged.connect(self._load_selection)
        self.reload.clicked.connect(lambda: self._populate(self.selection.currentData()))
        self.save.clicked.connect(self.submit)

    def allowed(self):
        return self.snap is not None and self.snap.capabilities.allows("configure_simulated_link")

    def controls(self):
        enabled = self.allowed() and not self.busy and self.pending is None
        self.form.setEnabled(enabled)
        self.save.setEnabled(enabled)
        self.reload.setEnabled(enabled)

    def set_busy(self, busy):
        self.busy = busy
        self.controls()

    def set_snapshot(self, snap):
        context = (snap.campaign, snap.input_kind, snap.capabilities.adapter_id, snap.topology.id)
        changed = context != self.context
        self.context, self.snap = context, snap
        if changed or not self.allowed():
            self.pending = None
            self.hide()
            self.selection.blockSignals(True)
            self.selection.clear()
            self.selection.blockSignals(False)
            for field in self.fields.values():
                field.clear()
            self.version = None
            self.note.setText("Source changed; reopen the editor. No request was resubmitted.")
        pending = self.pending
        if pending and (snap.topology.persisted or snap.input_kind == 'synthetic') and type(snap.topology.version) is int and snap.topology.version > pending.expected_version:
            match = next((link for link in snap.topology.links if link.id == pending.link_id
                          and (link.a, link.b) == (pending.left_node_id, pending.right_node_id)
                          and link.distance_km == pending.distance_km
                          and link.nominal_pair_fidelity == pending.nominal_pair_fidelity
                          and link.configured_active is pending.active), None)
            if match is not None and match.channels != pending.channels:
                match = None
            if match:
                self.pending = None
                self._populate(pending.link_id)
                self.note.setText(f"CONFIRMED · {pending.link_id} · version {snap.topology.version}. Configuration only; no physical action.")
            else:
                self.command_failed("Readback differs from the requested configuration")
        self.controls()

    def begin(self):
        if not self.allowed():
            return
        if self.pending is None:
            self._populate()
        self.show()
        self.raise_()

    def _populate(self, selected=None):
        if self.snap is None:
            return
        self.selection.blockSignals(True)
        self.selection.clear()
        self.selection.addItem("NEW LINK", None)
        for link in self.snap.topology.links:
            self.selection.addItem(f"{link.id} · {link.a} ↔ {link.b}", link.id)
        self.selection.setCurrentIndex(max(0, self.selection.findData(selected)))
        self.selection.blockSignals(False)
        self._load_selection()

    def _load_selection(self, *_):
        if self.snap is None:
            return
        link = next((link for link in self.snap.topology.links if link.id == self.selection.currentData()), None)
        self.version = self.snap.topology.version
        self.version_label.setText(f"EDIT BASE VERSION · {self.version}")
        self.fields["link_id"].setText(link.id if link else "")
        self.fields["link_id"].setEnabled(link is None)
        for key, value in (("left_node_id", link.a if link else None), ("right_node_id", link.b if link else None)):
            combo = self.fields[key]
            combo.clear()
            combo.addItems([node.id for node in self.snap.topology.nodes])
            combo.setCurrentIndex(combo.findText(value) if value else -1)
            combo.setEnabled(link is None)
        for key in ("distance_km", "nominal_pair_fidelity"):
            value = getattr(link, key) if link else None
            self.fields[key].setText(repr(value) if value is not None else "")
        self.active.setChecked(link.configured_active is not False if link else True)
        pair = ChannelPair.from_dict(link.channels) if link and link.channels else None
        self._suggested = ("", "")
        self.channel_fields["classical_id"].setText(pair.classical.channel_id if pair else "")
        self.channel_fields["quantum_id"].setText(pair.quantum.channel_id if pair else "")
        self.channel_fields["max_message_bytes"].setText(str(pair.classical.max_message_bytes) if pair else "524288")
        self.channel_fields["max_age_s"].setText(str(pair.classical.max_age_s) if pair else "2")
        self.classical_active.setChecked(pair.classical.enabled if pair else True)
        self.quantum_active.setChecked(pair.quantum.enabled if pair else True)
        self.quantum_protocol.setCurrentText(pair.quantum.protocol if pair else 'bb84-statistical-record-v1')
        self._suggest_channels()
        self.note.setText("Edit base is pinned. Reload the form after a version conflict; no automatic retry.")
        self.controls()

    def _suggest_channels(self, *_):
        link_id = self.fields["link_id"].text().strip()
        defaults = (link_id + ".classical", link_id + ".quantum") if link_id else ("", "")
        for key, previous, value in zip(("classical_id", "quantum_id"), self._suggested, defaults):
            field = self.channel_fields[key]
            if not field.text() or field.text() == previous:
                field.setText(value)
        self._suggested = defaults
        self.association.setText(f"Association · {link_id or '—'} · "
                                 f"{self.fields['left_node_id'].currentText() or '—'} → "
                                 f"{self.fields['right_node_id'].currentText() or '—'} · software statistical ports")

    def submit(self):
        if not self.allowed() or self.pending is not None or self.busy:
            return
        try:
            pair = ChannelPair(self.fields["link_id"].text().strip(),
                ClassicalChannel(self.channel_fields["classical_id"].text().strip(),
                    max_message_bytes=int(self.channel_fields["max_message_bytes"].text()),
                    max_age_s=float(self.channel_fields["max_age_s"].text()), enabled=self.classical_active.isChecked()),
                QuantumChannel(self.channel_fields["quantum_id"].text().strip(),
                    self.fields["right_node_id"].currentText(), enabled=self.quantum_active.isChecked(),
                    protocol=self.quantum_protocol.currentText()))
            request = ConfigureSimulatedLink(
                mode="update" if self.selection.currentData() else "create",
                link_id=self.fields["link_id"].text().strip(),
                left_node_id=self.fields["left_node_id"].currentText(),
                right_node_id=self.fields["right_node_id"].currentText(),
                distance_km=float(self.fields["distance_km"].text()),
                nominal_pair_fidelity=float(self.fields["nominal_pair_fidelity"].text()),
                active=self.active.isChecked(), expected_version=self.version, topology_id=self.snap.topology.id,
                channels=pair.to_dict())
        except (ValueError, OverflowError) as exc:
            self.note.setText(f"REFUSED · {exc}")
            return
        self.pending = request
        self.note.setText("PENDING · Waiting for configuration readback from the simulation.")
        self.controls()
        self.command.emit("configure_simulated_link", json.dumps(asdict(request), allow_nan=False))

    def command_failed(self, message):
        if self.pending is not None:
            self.pending = None
            self.note.setText(f"UNCONFIRMED · {message}. Refresh inventory before retrying.")
            self.controls()
