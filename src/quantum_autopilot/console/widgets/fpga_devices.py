"""Inventory and supervised controls for backend FPGA acquisition devices."""
from __future__ import annotations

import json

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGridLayout,
    QLineEdit, QSpinBox, QWidget,
)

from ..theme import C
from .cards import head_row, note, set_text
from .prims import Btn, Card, Label, hbox, vbox
from .records import RecordTable


def _number(value, suffix="", digits=3):
    return "—" if value is None else f"{value:.{digits}g}{suffix}"


class FpgaDevicesPanel(Card):
    """All status and feedback come from the published device runtime snapshot.

    Configuration requests carry the observed revision. Connection means desired
    state; successful acquisition is reported only by the backend after its
    paired-port validation, processing and persisted readback.
    """

    command = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._snapshot = None
        self._devices = {}
        self._form_key = None
        self._pending = None
        self._registration = None
        self._busy = False
        self.register_button = Btn("Register FPGA device", variant="default")
        self.register_button.setProperty("operation", "register_fpga_device")
        self.register_button.clicked.connect(self._register_dialog)
        self.add(head_row(Label("FPGA devices · acquisition and processing"), self.register_button))
        self.add(note("Simulated devices transfer raw receiver acquisitions through the network's paired optical/classical "
                      "validation and processing. Physical transport requires a validated adapter; no physical device is actuated."))
        self.table = RecordTable(["DEVICE", "NODE", "ADAPTER", "CONNECTION", "ACQUISITION", "ACCEPTED", "REJECTED", "TIMEOUTS"])
        self.table.setAccessibleName("FPGA state received from the backend")
        self.add(self.table)
        self.selector = QComboBox()
        self.selector.setAccessibleName("FPGA device to configure")
        self.selector.currentIndexChanged.connect(self._selected)
        self.add(self.selector)
        self.identity = note("No FPGA device is registered in this workspace.")
        self.add(self.identity)
        self.telemetry = note("Hardware temperature, measured FPGA latency, timing margin and bitstream attestation are unavailable.")
        self.add(self.telemetry)

        self.controls = QWidget()
        grid = QGridLayout(self.controls)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        self.clock = QDoubleSpinBox()
        self.clock.setRange(.001, 1000)
        self.clock.setDecimals(3)
        self.clock.setSuffix(" MHz · model")
        self.cycles = QSpinBox()
        self.cycles.setRange(1, 1_000_000_000)
        self.cycles.setSuffix(" cycles · model")
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(.01, 30)
        self.timeout.setDecimals(2)
        self.timeout.setSuffix(" s · network time")
        self.retry = QSpinBox()
        self.retry.setRange(1, 120)
        self.retry.setSuffix(" ticks")
        self.enabled = QCheckBox("Enable acquisition")
        self.reconnect = QCheckBox("Automatic reconnect")
        for index, (label, field) in enumerate((("Model clock", self.clock), ("Model pipeline", self.cycles),
                                              ("Response timeout", self.timeout), ("Retry interval", self.retry))):
            grid.addWidget(Label(label), (index // 2) * 2, index % 2)
            grid.addWidget(field, (index // 2) * 2 + 1, index % 2)
        grid.addWidget(self.enabled, 4, 0)
        grid.addWidget(self.reconnect, 4, 1)
        self.save = Btn("Save configuration", variant="accent")
        self.save.setProperty("operation", "configure_fpga_device")
        self.save.clicked.connect(self._configure)
        self.connect_button = Btn("Connect", variant="default")
        self.connect_button.setProperty("operation", "set_fpga_connection")
        self.connect_button.clicked.connect(self._connection)
        grid.addWidget(self.save, 5, 0)
        grid.addWidget(self.connect_button, 5, 1)
        self.add(self.controls)

        self.fault_controls = QWidget()
        fault = hbox(self.fault_controls, (0, 0, 0, 0), 8)
        self.fault_kind = QComboBox()
        for label, kind in (("No response / timeout", "timeout"), ("Lost connection", "disconnect"),
                            ("Invalid channel association", "invalid_payload"), ("Acquisition FIFO overrun", "overrun")):
            self.fault_kind.addItem(label, kind)
        self.fault_duration = QSpinBox()
        self.fault_duration.setRange(1, 120)
        self.fault_duration.setValue(4)
        self.fault_duration.setSuffix(" ticks")
        self.fault_button = Btn("Inject simulated fault", variant="default")
        self.fault_button.setProperty("operation", "inject_fpga_fault")
        self.fault_button.clicked.connect(self._fault)
        for widget in (self.fault_kind, self.fault_duration, self.fault_button):
            fault.addWidget(widget)
        fault.addStretch(1)
        self.add(self.fault_controls)
        self.feedback = note("No processing feedback received.")
        self.add(self.feedback)
        self.receipts = RecordTable(["TIME UTC", "DEVICE", "FEEDBACK", "NETWORK TICK", "RECORD SHA256"])
        self.receipts.setAccessibleName("Persisted FPGA feedback receipts")
        self.add(self.receipts)
        self.controls.setEnabled(False)
        self.register_button.setEnabled(False)
        self.fault_controls.hide()

    def _emit(self, verb, payload):
        self.command.emit(verb, json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False))

    def _device(self):
        return self._devices.get(self.selector.currentData())

    def _allows(self, verb):
        return not self._busy and self._snapshot is not None and self._snapshot.capabilities.allows(verb)

    def set_busy(self, busy):
        self._busy = bool(busy)
        self.register_button.setEnabled(self._snapshot is not None and self._snapshot.live is not None
                                        and self._allows("register_fpga_device"))
        self._selected()

    def _configure(self):
        device = self._device()
        if device is None or not self.save.isEnabled():
            return
        config = device["configuration"]
        changes = {"clock_hz": self.clock.value() * 1_000_000, "pipeline_cycles": self.cycles.value(),
                   "timeout_s": self.timeout.value(), "retry_ticks": self.retry.value(),
                   "enabled": self.enabled.isChecked(), "auto_reconnect": self.reconnect.isChecked()}
        self._pending = (config["device_id"], config["revision"])
        set_text(self.feedback, "Configuration requested; waiting for persisted backend readback.")
        self._emit("configure_fpga_device", {"device_id": config["device_id"], "expected_revision": config["revision"],
                                              "changes": changes})

    def _connection(self):
        device = self._device()
        if device is None or not self.connect_button.isEnabled():
            return
        config = device["configuration"]
        self._pending = (config["device_id"], config["revision"])
        set_text(self.feedback, "Connection request sent; a fresh acquisition handshake is required.")
        self._emit("set_fpga_connection", {"device_id": config["device_id"], "expected_revision": config["revision"],
                                            "connected": not config["connected"]})

    def _fault(self):
        device = self._device()
        if device is None or not self.fault_button.isEnabled():
            return
        self._emit("inject_fpga_fault", {"device_id": device["configuration"]["device_id"],
                                         "kind": self.fault_kind.currentData(),
                                         "duration_ticks": self.fault_duration.value()})

    def _selected(self, *_):
        device = self._device()
        available = device is not None
        self.controls.setEnabled(available and not self._busy)
        self.save.setEnabled(available and self._allows("configure_fpga_device"))
        self.connect_button.setEnabled(available and self._allows("set_fpga_connection")
                                       and device["mode"] == "simulated")
        simulated = available and device["mode"] == "simulated"
        active_simulator = self._snapshot is not None and self._snapshot.live is not None \
            and self._snapshot.live.source_kind == "simulated"
        self.fault_controls.setVisible(bool(simulated and active_simulator))
        self.fault_button.setEnabled(bool(simulated and active_simulator and self._allows("inject_fpga_fault")))
        if not available:
            self._form_key = None
            set_text(self.identity, "No FPGA device is registered in this workspace.")
            set_text(self.telemetry, "No device telemetry supplied.")
            set_text(self.feedback, "No processing feedback received.")
            return
        config, state, caps = device["configuration"], device["state"], device["capabilities"]
        key = (config["device_id"], config["revision"])
        if key != self._form_key:
            self._form_key = key
            self.clock.setValue(config["clock_hz"] / 1_000_000)
            self.cycles.setValue(config["pipeline_cycles"])
            self.timeout.setValue(config["timeout_s"])
            self.retry.setValue(config["retry_ticks"])
            self.enabled.setChecked(config["enabled"])
            self.reconnect.setChecked(config["auto_reconnect"])
        self.connect_button.setText("Disconnect" if config["connected"] else "Connect")
        set_text(self.identity, f"{config['label']} · {config['device_id']} · receiver {config['node_id']} · "
                 f"revision {config['revision']} · {device['mode']}\n"
                 f"Firmware: {caps.get('firmware') or '—'} · bitstream SHA256: {caps.get('bitstream_sha256') or '—'}\n"
                 f"Configuration SHA256: {device['configuration_sha256']}\n"
                 f"Acquisition session: {state.get('session_id') or '—'} · bound spans: {', '.join(state['bound_links']) or '—'}")
        model_latency = config["pipeline_cycles"] / config["clock_hz"] if simulated else None
        set_text(self.telemetry, f"Host acquisition read: {_number(state['host_read_ms'], ' ms')} · "
                 f"modeled pipeline latency: {_number(model_latency, ' s')}\n"
                 f"Measured FPGA latency: {_number(state['measured_fpga_latency_s'], ' s')} · "
                 f"temperature: {_number(state['temperature_c'], ' °C')} · timing margin: {_number(state['timing_margin_s'], ' s')}\n"
                 f"Last received network tick: {state['last_received_tick'] if state['last_received_tick'] is not None else '—'} · "
                 f"reconnect handshakes: {state['reconnections']} · {state.get('last_error') or caps.get('scope') or caps.get('reason') or ''}")
        pending = self._pending
        if pending is not None and pending[0] == config["device_id"] and pending[1] == config["revision"]:
            return
        self._pending = None
        feedback = state.get("last_feedback")
        if feedback is None:
            set_text(self.feedback, "No processing feedback received.")
        else:
            rows = state.get("last_receipts", [])
            summary = "\n".join(f"{row['link_id']}: {row['processing_status']} · QBER {_number(row['qber'])} · "
                                f"SKR {_number(row['skr_bps'], ' bit/s')} · "
                                f"{' → '.join(row['channel_receipt'].get('stages', []))}" for row in rows)
            set_text(self.feedback, f"Persisted readback: {feedback['kind']} · tick {feedback['tick']} · {feedback['at_utc']}\n"
                     f"Receipt SHA256: {feedback['record_sha256']}" + ("\n" + summary if summary else ""))

    def set_snapshot(self, snap):
        self._snapshot = snap
        data = {} if snap.live is None else snap.live.technologies.get("fpga_devices", {})
        self._devices = {row["configuration"]["device_id"]: row for row in data.get("devices", [])}
        ids = tuple(self._devices)
        old = self.selector.currentData()
        if tuple(self.selector.itemData(i) for i in range(self.selector.count())) != ids:
            self.selector.blockSignals(True)
            self.selector.clear()
            for device_id in ids:
                self.selector.addItem(device_id, device_id)
            if old in ids:
                self.selector.setCurrentIndex(ids.index(old))
            self.selector.blockSignals(False)
        self.register_button.setEnabled(snap.live is not None and self._allows("register_fpga_device"))
        rows = []
        for device_id, row in self._devices.items():
            config, state = row["configuration"], row["state"]
            values = (device_id, config["node_id"], row["mode"], state["connection_state"], state["acquisition_state"],
                      str(state["accepted_frames"]), str(state["rejected_frames"]), str(state["timeouts"]))
            rows.append([(value, C.ink) for value in values])
        self.table.set_rows(rows)
        self.receipts.set_rows([[(str(value), C.ink2) for value in
                                (receipt["at_utc"], receipt["device_id"], receipt["kind"], receipt["tick"], receipt["record_sha256"])]
                               for receipt in data.get("recent_feedback", [])])
        self._selected()

    def _register_dialog(self):
        if self._snapshot is None or self._snapshot.live is None or not self.register_button.isEnabled():
            return
        if self._registration is not None:
            self._registration.show()
            self._registration.raise_()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Register FPGA acquisition device")
        dialog.setMinimumWidth(440)
        layout = vbox(dialog, (18, 18, 18, 18), 12)
        layout.addWidget(note("Register a receiver against the persisted network inventory. A physical contract stays "
                              "unavailable until a validated transport is supplied. Registration performs no physical action."))
        form = QFormLayout()
        device_id, label, node, adapter = QLineEdit(), QLineEdit(), QComboBox(), QComboBox()
        device_id.setAccessibleName("New FPGA device identifier")
        label.setAccessibleName("New FPGA device label")
        device_id.setMaxLength(128)
        label.setMaxLength(160)
        node.setAccessibleName("FPGA optical receiver node")
        owned = {d["configuration"]["node_id"] for d in self._devices.values()}
        eligible = {link.b for link in self._snapshot.live.links if link.provenance.get("technology") is None}
        for item in self._snapshot.live.nodes:
            if item.id in eligible and item.id not in owned:
                node.addItem(item.id, item.id)
        adapter.addItem("Simulated raw acquisition adapter", "simulated")
        adapter.addItem("Physical contract · transport unavailable", "physical-contract")
        for name, widget in (("Device ID", device_id), ("Label", label), ("Receiver node", node), ("Adapter", adapter)):
            form.addRow(name, widget)
        layout.addLayout(form)
        error = note("No eligible unassigned optical receiver." if node.count() == 0 else "")
        layout.addWidget(error)
        register = Btn("Register configuration", variant="accent")
        register.setProperty("operation", "register_fpga_device")
        register.setEnabled(node.count() > 0)

        def submit():
            if not device_id.text().strip() or not label.text().strip():
                set_text(error, "Device ID and label are required.")
                return
            self._emit("register_fpga_device", {"expected_revision": 0, "device": {
                "device_id": device_id.text().strip(), "node_id": node.currentData(),
                "label": label.text().strip(), "adapter_id": adapter.currentData()}})
            dialog.accept()

        register.clicked.connect(submit)
        layout.addWidget(register)
        dialog.finished.connect(lambda *_: setattr(self, "_registration", None))
        dialog.finished.connect(dialog.deleteLater)
        self._registration = dialog
        dialog.show()


__all__ = ["FpgaDevicesPanel"]
