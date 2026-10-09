"""Nodes: inventory, node detail and the "Add node" form.

Layout of the approved prototype: inventory list on the left (status mark, id,
role), the selected node in the centre, the provisioning form on the right.

Data. The centre is bound to the backend record of the selected node:
`LiveNode` and its incident `LiveLink`s when the source carries a network engine
(`snap.live`), otherwise the `Node` of `snap.topology` and — when the provider
selected the same node — `snap.node`. The prototype's memory-register map,
swap-throughput bars and equipment serials have no backend counterpart; their
places are taken by the real links, memory, calibration report, routes and log.

The form asks for the node's location and lets the registration finish without
one. Three request kinds exist, chosen by what the active source declares:

* `add_network_node` — live network: `{node_id, role, address, port, latitude,
  longitude, place}` (both coordinates null and an empty place when no location
  is given), plus the topology version the operator was looking at;
* `add_simulated_node` — persistent simulator inventory without location;
* `stage_node` — local shadow record, for sources that only allow staging.

Nothing is shown as done before the backend's readback: the note stays PENDING
until the node is in the delivered topology (and, where readable, its command is
CONFIRMED), then says where the node went — onto the map or into the queue.
"""
from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import asdict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFrame, QMessageBox, QScrollArea, QSizePolicy, QWidget

from ..contract import (
    DEMAND_STATUS_TONE, HEALTH_TONE, LINK_STATUS_TONE, NODE_STATUS_TONE, SEVERITY_TONE, Snapshot,
)
from ..theme import C, mono, text as text_font, tone
from ..widgets import Btn, Card, Cells, Frame, Input, L, Label, RowBtn, Toggle, Track, hbox, vbox
from ..widgets.cards import (
    DASH, NOT_SUPPLIED, KVList, LogList, MeterList, Page, TwoLineList, clock_of, dot, fmt_fidelity, fmt_flag, fmt_number,
    fmt_qber, fmt_rate, grid_row, head_row, note, paint_dot, set_chip, set_text, status_chip,
)
from ..widgets.location_fields import LocationFields
from ..widgets.prims import clear_layout, scoped_style
from .base import Screen

PROTOCOLS = ("QNET-TLS", "QNET-REPEATER", "QNET-CORE", "QNET-SAT")
ROLE_NAMES = {"node": "ENDPOINT", "repeater": "REPEATER", "core": "CORE"}
ORIGIN_LABEL = {"orion-console": "PERSISTED", "orion-preset": "REFERENCE PRESET", "shadow": "SHADOW"}
NODE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
MAX_LOG_ROWS = 12
MODE_TEXT = {
    "network": ("Add node", "Add to network", "add_network_node",
                "Persists a simulator node through the backend. Address and port are metadata only; no device "
                "is contacted."),
    "persistent": ("Add simulated node", "Add to simulator", "add_simulated_node",
                   "Persists an isolated simulator node. IP/port are metadata only; no link or hardware "
                   "connection."),
    "shadow": ("Provision node · shadow only", "Provision shadow node", "stage_node",
               "Adds a local shadow record only. No device is contacted."),
}


class NodeScreen(Screen):
    key = "node"
    title = "NODES"
    heading = "Nodes"
    status = ""

    def breadcrumb(self, snap, selection=None) -> str:
        ident = self._selected or (snap.node.id if snap is not None else "")
        return "/ nodes" + (f" / {ident}" if ident and ident != DASH else "")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected = ""
        self.add_header()
        self._campaign, self._base_nodes, self._shadow_nodes = None, [], []
        self._pending_add = None
        self._pending_stage = None
        self._pending_network = None
        self._persistent = False
        self._mode = ""
        self._row_ids: list[str] = []
        self.rows: dict[str, RowBtn] = {}
        self._row_parts: dict[str, tuple] = {}

        middle = QWidget()
        mid = hbox(middle, (0, 0, 0, 0), 0)
        self.root.addWidget(middle, 1)

        # ------------------------------------------------------- inventory
        inventory = Frame(C.raised, C.soft, sides="r")
        inventory.setFixedWidth(250)
        il = vbox(inventory, (0, 0, 0, 0), 0)
        head = QWidget()
        hl = hbox(head, (14, 14, 14, 8), 8)
        hl.addWidget(Label("Inventory"), 1)
        self.inventory_title = Label("0")
        hl.addWidget(self.inventory_title)
        il.addWidget(head)
        self.inventory_host = QWidget()
        self.inventory_lay = vbox(self.inventory_host, (0, 0, 0, 0), 0)
        self.inventory_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.inventory_scroll = QScrollArea()
        self.inventory_scroll.setWidgetResizable(True)
        self.inventory_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.inventory_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inventory_scroll.setWidget(self.inventory_host)
        self.inventory_scroll.setAccessibleName("Node inventory")
        il.addWidget(self.inventory_scroll, 1)
        self.inventory_note = note("", 12, C.ink3)
        self.inventory_note.setContentsMargins(14, 8, 14, 12)
        il.addWidget(self.inventory_note)
        self.log_scroll = self.inventory_scroll
        mid.addWidget(inventory)

        # ---------------------------------------------------------- detail
        self.page = Page()
        mid.addWidget(self.page, 1)
        self.crumb = Label("")
        self.page.add(self.crumb)
        self.node_id = L(DASH, mono(32), C.ink)
        self.subtitle = L("", text_font(13.5), C.ink2, elide=True)
        self.subtitle.setMinimumWidth(0)
        self.subtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status_chip = status_chip("", "dim")
        self.origin_chip = status_chip("", "mute")
        self.remove_button = Btn("Remove shadow record", C.red, variant="tone")
        self.remove_button.setProperty("operation", "remove_staged_node")
        self.remove_button.clicked.connect(self._confirm_remove_selected)
        self.map_button = Btn("Show on map", variant="default")
        self.map_button.setToolTip("Open Topology with this node selected")
        self.map_button.clicked.connect(self._show_on_map)
        title = head_row(self.node_id, self.subtitle, self.status_chip, self.origin_chip,
                         self.remove_button, self.map_button, gap=16)
        self.page.add(title)

        self.ops_card = Card()
        self.ops_plain_style = self.ops_card.styleSheet()
        text_col = QWidget()
        tc = vbox(text_col, (0, 0, 0, 0), 4)
        self.alert_title = Label("", wrap=True)
        self.alert_body = note("", 13)
        tc.addWidget(self.alert_title)
        tc.addWidget(self.alert_body)
        self.recalibrate = Btn("Recalibrate", variant="accent")
        self.recalibrate.setProperty("operation", "recalibrate_node")
        self.recalibrate.clicked.connect(self._recalibrate)
        self.isolate = Btn("Isolate", variant="default")
        self.isolate.setProperty("operation", "set_node_isolation")
        self.isolate.clicked.connect(self._toggle_isolation)
        self.acknowledge = Btn("Acknowledge", variant="default")
        self.acknowledge.setProperty("operation", "acknowledge")
        self.acknowledge.clicked.connect(lambda: self._selected and self.command.emit("acknowledge", self._selected))
        ops_row = head_row(text_col, self.recalibrate, self.isolate, self.acknowledge, gap=16)
        self.ops_card.add(ops_row)
        self.page.add(self.ops_card)
        self._alert_tone: str | None = "unset"

        links = Card()
        self.links_title = Label("Links")
        links.add(self.links_title)
        self.links = TwoLineList()
        links.add(self.links)
        self.links_note = note("", 12.5, C.ink3)
        links.add(self.links_note)
        links.lay.addStretch(1)

        memory = Card()
        self.memory_title = Label("Memory · stored pairs", wrap=True)
        memory.add(self.memory_title)
        self.registers = Cells([], columns=16, cell_h=14, gap=3)
        memory.add(self.registers)
        self.memory_values = KVList(size=12.5)
        memory.add(self.memory_values)
        self.memory_meters = MeterList()
        memory.add(self.memory_meters)
        self.memory_note = note("", 12.5, C.ink3)
        memory.add(self.memory_note)
        memory.lay.addStretch(1)

        record = Card()
        record.add(Label("Node record"))
        self.record = KVList(size=12.5)
        record.add(self.record)
        record.lay.addStretch(1)
        self.page.add(grid_row([links, memory, record]))

        calibration = Card()
        self.calibration_title = Label("Calibration", wrap=True)
        calibration.add(self.calibration_title)
        self.calibration_track = Track(0.0, "violet")
        calibration.add(self.calibration_track)
        self.calibration = KVList(size=12.5)
        calibration.add(self.calibration)
        self.calibration_note = note("", 12.5, C.ink3)
        calibration.add(self.calibration_note)
        calibration.lay.addStretch(1)

        routes = Card()
        routes.add(Label("Routes through this node"))
        self.routes = TwoLineList()
        routes.add(self.routes)
        self.routes_note = note("", 12.5, C.ink3)
        routes.add(self.routes_note)
        routes.lay.addStretch(1)

        log = Card()
        log.add(Label("Node log"))
        self.log = LogList(empty="No event recorded for this node.")
        log.add(self.log)
        log.lay.addStretch(1)
        self.page.add(grid_row([calibration, routes, log]))

        # ------------------------------------------------------------ form
        form_frame = Frame(C.raised, C.soft, sides="l")
        form_frame.setFixedWidth(300)
        outer = vbox(form_frame, (0, 0, 0, 0), 0)
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        form = QWidget()
        fl = vbox(form, (16, 16, 12, 16), 10)
        form_scroll.setWidget(form)
        outer.addWidget(form_scroll)
        self.form_scroll = form_scroll
        self.form_title = Label("Add node", wrap=True)
        fl.addWidget(self.form_title)
        self.fields = {}

        def add_field(label, name, placeholder, layout=fl, mono_font=True):
            box = QWidget()
            box_lay = vbox(box, (0, 0, 0, 0), 5)
            box_lay.addWidget(Label(label))
            field = Input(placeholder, mono_font=mono_font)
            field.setMinimumWidth(0)
            field.setAccessibleName(label)
            box_lay.addWidget(field)
            layout.addWidget(box)
            self.fields[name] = field

        add_field("Node ID", "id", "N-14")
        fl.addWidget(Label("Kind"))
        roles = QWidget()
        rl = hbox(roles, (0, 0, 0, 0), 14)
        self.role_value = "ENDPOINT"
        self.roles = {}
        for value in ("ENDPOINT", "REPEATER", "CORE"):
            button = Toggle(value)
            button.clicked.connect(lambda checked=False, selected=value: self._set_role(selected))
            rl.addWidget(button)
            self.roles[value] = button
        rl.addStretch(1)
        self._set_role("ENDPOINT")
        fl.addWidget(roles)
        add_field("Connection address", "address", "10.71.0.14")
        connection = QWidget()
        connection_lay = hbox(connection, (0, 0, 0, 0), 8)
        add_field("Port", "port", "4433", layout=connection_lay)
        self.protocol_box = QWidget()
        protocol_lay = vbox(self.protocol_box, (0, 0, 0, 0), 5)
        protocol_lay.addWidget(Label("Protocol"))
        self.protocol = QComboBox()
        self.protocol.addItems(PROTOCOLS)
        self.protocol.setFont(mono(12))
        self.protocol.setMinimumContentsLength(8)
        self.protocol.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        protocol_lay.addWidget(self.protocol)
        connection_lay.addWidget(self.protocol_box)
        fl.addWidget(connection)

        self.location = LocationFields()
        self.fields["latitude"] = self.location.latitude
        self.fields["longitude"] = self.location.longitude
        self.fields["place"] = self.location.place
        fl.addWidget(self.location)

        self.channel_note = note("A new node has no quantum channel until a fibre is configured on the Topology "
                                 "screen, so it cannot take part in pairs yet.", 12, C.ink3)
        fl.addWidget(self.channel_note)
        self.auth_row = KVList(size=12)
        self.auth_row.set_rows([("Auth token / cert fingerprint", "NOT COLLECTED", "dim")])
        fl.addWidget(self.auth_row)
        self.provision = Btn("Add to network", variant="solid")
        self.provision.setProperty("operation", "add_network_node")
        self.provision.clicked.connect(self._provision)
        fl.addWidget(self.provision)
        self.form_note = note("", 12)
        fl.addWidget(self.form_note)
        fl.addStretch(1)
        mid.addWidget(form_frame)

    # ---------------------------------------------------------- view state
    def save_state(self) -> dict:
        state = super().save_state()
        state.update(selected=self._selected, role=self.role_value)
        return state

    def restore_state(self, state: dict) -> None:
        super().restore_state(state)
        if state.get("role") in self.roles:
            self._set_role(state["role"])
        self._selected = state.get("selected", self._selected)
        if self._snap is not None:
            self._render_detail(self._snap)

    def on_selection(self, selection) -> None:
        if getattr(selection, "kind", None) == "node" and selection.id and selection.id != self._selected:
            self._selected = selection.id
            if self._snap is not None:
                self._render_inventory(self._snap)
                self._render_detail(self._snap)

    def cache_selection(self, selection) -> None:
        if getattr(selection, "kind", None) == "node" and selection.id:
            self._selected = selection.id

    def _pick(self, node_id: str) -> None:
        self._selected = node_id
        if self._snap is not None:
            self._render_inventory(self._snap)
            self._render_detail(self._snap)
        self.select.emit("node", node_id)

    def _show_on_map(self) -> None:
        if not self._selected:
            return
        self.select.emit("node", self._selected)
        self.navigate.emit("topology")

    # ------------------------------------------------------------ commands
    def _bookkeeping(self) -> dict:
        topology = self._snap.topology
        return {"expected_version": topology.version, "topology_id": topology.id}

    def _recalibrate(self) -> None:
        if self._snap is None or not self._selected or not self._snap.capabilities.allows("recalibrate_node"):
            return
        self.command.emit("recalibrate_node", json.dumps({"node_id": self._selected, **self._bookkeeping()}))

    def _toggle_isolation(self) -> None:
        if self._snap is None or not self._selected or not self._snap.capabilities.allows("set_node_isolation"):
            return
        node = self._topology_node(self._snap, self._selected)
        isolated = bool(node is not None and node.isolated)
        self.command.emit("set_node_isolation", json.dumps(
            {"node_id": self._selected, "isolated": not isolated, **self._bookkeeping()}))

    def _confirm_remove_selected(self) -> None:
        self._confirm_remove(self._selected)

    def _confirm_remove(self, ident):
        if not ident or self._snap is None or not self._snap.capabilities.allows("remove_staged_node"):
            return
        answer = QMessageBox.question(self, "OrionQ · Remove shadow node",
            f"Remove the local shadow record {ident}? No physical node will be touched.")
        if answer == QMessageBox.StandardButton.Yes and self._snap.capabilities.allows("remove_staged_node"):
            self.command.emit("remove_staged_node", ident)

    # ---------------------------------------------------------------- form
    def _set_role(self, value):
        self.role_value = value
        for name, button in self.roles.items():
            button.set_active(name == value)

    def _refuse(self, message) -> None:
        self.form_note.setText(f"REFUSED · {message}")
        self.form_note.setStyleSheet(f"color:{C.red_t};background:transparent;")
        self._reveal_note()

    def _say(self, message, *, reveal: bool = True) -> None:
        self.form_note.setText(message)
        self.form_note.setStyleSheet(f"color:{C.ink2};background:transparent;")
        if reveal:
            self._reveal_note()

    def _reveal_note(self) -> None:
        """The answer to a form action must be on screen, not below the fold of the form column."""

        if self.isVisible():
            self.form_scroll.widget().layout().activate()
            self.form_scroll.ensureWidgetVisible(self.form_note, 0, 12)

    def _port(self) -> int:
        port = self.fields["port"].text().strip()
        if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("Port must be an integer in 1..65535")
        return int(port)

    def _provision(self):
        if self._pending_stage is not None or self._pending_add is not None or self._pending_network is not None:
            return
        if self._mode == "network":
            self._add_network()
        elif self._persistent:
            self._add_persistent()
        else:
            self._stage()

    def _stage(self):
        if self._snap is None or not self._snap.capabilities.allows("stage_node"):
            self._refuse("This input does not allow local staging.")
            return
        ident, address = (self.fields[name].text().strip() for name in ("id", "address"))
        existing = {row["id"] for row in [*self._base_nodes, *self._shadow_nodes]}
        try:
            if not NODE_ID.fullmatch(ident) or ident in existing:
                raise ValueError("Node ID is empty, duplicated or invalid.")
            ipaddress.ip_address(address)
            port = self._port()
        except ValueError as exc:
            self._refuse(exc)
            return
        self._pending_stage = {'id': ident, 'role': self.role_value, 'address': address,
                               'port': port, 'protocol': self.protocol.currentText()}
        self._say('PENDING · Waiting for shared simulation inventory. No device contacted.')
        self.apply_command_capabilities()
        self.command.emit('stage_node', json.dumps(self._pending_stage))

    def _add_persistent(self):
        if self._pending_add or self._snap is None or not self._snap.capabilities.allows("add_simulated_node"):
            return
        from quantum_autopilot.control_plane.inventory import AddSimulatedNode
        try:
            request = AddSimulatedNode(
                node_id=self.fields["id"].text().strip(), role=self.role_value.lower(),
                address=self.fields["address"].text().strip(), port=self._port(),
                expected_version=self._snap.topology.version, topology_id=self._snap.topology.id)
            if any(row["id"] == request.node_id for row in self._base_nodes):
                raise ValueError("Duplicate node ID")
        except ValueError as exc:
            self._refuse(exc)
            return
        self._pending_add = request
        self._say("PENDING · Waiting for persisted inventory readback. No device connection.")
        self.apply_command_capabilities()
        self.command.emit("add_simulated_node", json.dumps(asdict(request)))

    def network_request(self) -> dict:
        """The `add_network_node` request for the form as filled. Raises ValueError when invalid."""

        snap = self._snap
        node_id = self.fields["id"].text().strip()
        address = self.fields["address"].text().strip()
        port = self._port()
        latitude, longitude, place = self.location.value()
        if any(row["id"] == node_id for row in self._base_nodes):
            raise ValueError("Duplicate node ID")
        payload = {"node_id": node_id, "role": self.role_value.lower(), "address": address, "port": port,
                   "latitude": latitude, "longitude": longitude, "place": place}
        version, topology_id = snap.topology.version, snap.topology.id
        if type(version) is int and isinstance(topology_id, str):
            from quantum_autopilot.control_plane.inventory import AddNetworkNode
            return asdict(AddNetworkNode(**payload, expected_version=version, topology_id=topology_id))
        # No persisted topology yet: the provider fills the bookkeeping from the store.
        if not NODE_ID.fullmatch(node_id):
            raise ValueError("Node ID is empty or invalid")
        if payload["role"] not in ("endpoint", "repeater"):
            raise ValueError("only endpoint and repeater roles are implemented")
        ipaddress.ip_address(address)
        return payload

    def _add_network(self):
        if self._snap is None or not self._snap.capabilities.allows("add_network_node"):
            return
        try:
            request = self.network_request()
        except ValueError as exc:
            self._refuse(exc)
            return
        self._pending_network = request
        where = ("without location" if request["latitude"] is None else
                 f"at {request['latitude']:.5f}, {request['longitude']:.5f}")
        self._say(f"PENDING · {request['node_id']} {where} · waiting for the backend readback. No device contacted.")
        self.apply_command_capabilities()
        self.command.emit("add_network_node", json.dumps(request))

    def command_failed(self, message):
        if self._pending_add is not None or self._pending_stage is not None or self._pending_network is not None:
            self._pending_add = None
            self._pending_stage = None
            self._pending_network = None
            self.form_note.setText(f"UNCONFIRMED · {message}. Refresh inventory before retrying.")
            self.form_note.setStyleSheet(f"color:{C.red_t};background:transparent;")
            self._reveal_note()
            self.apply_command_capabilities()

    def apply_command_capabilities(self):
        super().apply_command_capabilities()
        if self._pending_add is not None or self._pending_stage is not None or self._pending_network is not None:
            self.provision.setEnabled(False)
        if self._snap is not None and self._snap.input_kind == "measured":
            self.provision.setEnabled(False)
        has_node = bool(self._selected)
        if self.map_button.isEnabled() != has_node:
            self.map_button.setEnabled(has_node)        # a view action: no backend operation behind it
        if not has_node:
            for button in (self.recalibrate, self.isolate, self.acknowledge):
                button.setEnabled(False)

    def _clear_form(self) -> None:
        for field in self.fields.values():
            field.clear()
        self.location.clear()

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._persistent = mode in ("network", "persistent")
        title, button, operation, text = MODE_TEXT[mode]
        self.form_title.setText(title)
        self.provision.setText(button)
        self.provision.setProperty("operation", operation)
        self.protocol_box.setVisible(mode == "shadow")
        self.location.setVisible(mode == "network")
        self.roles["CORE"].setVisible(mode == "shadow")
        self.roles["CORE"].setEnabled(mode == "shadow")
        if mode != "shadow" and self.role_value == "CORE":
            self._set_role("ENDPOINT")
        self._say(text, reveal=False)

    def _confirm_network(self, snap: Snapshot) -> None:
        pending = self._pending_network
        node = next((item for item in snap.topology.nodes if item.id == pending["node_id"]), None)
        if node is None:
            return
        if snap.capabilities.allows("read_console_commands"):
            needle = json.dumps(pending["node_id"])
            command = next((item for item in snap.ledger.commands
                            if item.verb == "add_network_node" and needle in item.serialized), None)
            if command is not None and command.state in ("REQUESTED", "RUNNING"):
                return
            if command is not None and command.state != "CONFIRMED":
                self.command_failed(f"Command {command.command_id} ended {command.state}")
                return
        wanted = pending["latitude"] is not None
        matches = (node.connection_address == pending["address"] and node.connection_port == pending["port"]
                   and bool(node.located) == wanted
                   and (not wanted or (abs(node.latitude - pending["latitude"]) < 1e-9
                                       and abs(node.longitude - pending["longitude"]) < 1e-9)))
        if not matches:
            self.command_failed("Readback does not match the requested node")
            return
        self._pending_network = None
        self._clear_form()
        version = f" at topology version {snap.topology.version}" if snap.topology.version is not None else ""
        if node.located:
            place = f" ({node.place})" if node.place else ""
            where = f"placed on the map at {node.latitude:.5f}, {node.longitude:.5f}{place}"
        else:
            where = "no location given: it waits in the queue on the Topology screen until it is positioned"
        self._say(f"CONFIRMED · {node.id} persisted{version} · {where}. No device contacted.")
        self._selected = node.id
        self.notify.emit(f"{node.id} confirmed · " + ("on the map" if node.located else "in the Topology queue"))

    # ----------------------------------------------------------- inventory
    @staticmethod
    def _topology_node(snap: Snapshot, node_id: str):
        return next((item for item in snap.topology.nodes if item.id == node_id), None)

    def _status_of(self, snap: Snapshot, node) -> tuple[str, str]:
        live = snap.live.node(node.id) if snap.live is not None else None
        if live is not None and live.status:
            return live.status.upper(), NODE_STATUS_TONE.get(live.status, "dim")
        if node.inventory_origin == "shadow":
            return "PROVISIONING", "amber"
        if node.isolated:
            return "ISOLATED", "red"
        if node.calibration_count:
            return "CALIBRATED", "green"
        if node.inventory_origin == "orion-console":
            return "SIMULATED", "dim"
        return node.health.upper(), HEALTH_TONE.get(node.health, "dim")

    def _render_inventory(self, snap: Snapshot) -> None:
        nodes = list(snap.topology.nodes)
        ids = [node.id for node in nodes]
        if ids != self._row_ids:
            clear_layout(self.inventory_lay)
            self.rows, self._row_parts, self._row_ids = {}, {}, ids
            for node in nodes:
                row = RowBtn()
                mark = dot("dim")
                name = L(node.id, mono(12.5), C.ink)
                role = L("", text_font(12), C.ink2, elide=True)
                role.setMinimumWidth(0)
                role.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                row.add(mark)
                row.add(name)
                row.add(role, 1)
                row.clicked.connect(lambda checked=False, ident=node.id: self._pick(ident))
                row.setAccessibleName(f"Node {node.id}")
                self.inventory_lay.addWidget(row)
                self.rows[node.id] = row
                self._row_parts[node.id] = (mark, role)
        for node in nodes:
            mark, role = self._row_parts[node.id]
            status, tone_name = self._status_of(snap, node)
            paint_dot(mark, tone_name)
            kind = ROLE_NAMES.get(node.kind, str(node.kind).upper()).lower()
            located = "" if snap.live is None or node.located else " · unlocated"
            set_text(role, f"{kind}{located}")
            self.rows[node.id].setToolTip(f"{node.id} · {kind} · {status}")
            self.rows[node.id].set_on(node.id == self._selected)
        set_text(self.inventory_title, str(len(nodes)))

    # -------------------------------------------------------------- detail
    def _conditions(self, snap: Snapshot, node, live_node, detail) -> tuple[str, list[str]]:
        """(tone, reasons) the backend reports for the node; ("", []) when none."""

        reasons, level = [], ""
        if live_node is not None:
            if live_node.status and live_node.status != "up":
                reasons.append(f"Node status {live_node.status}.")
                level = NODE_STATUS_TONE.get(live_node.status, "amber")
            calibration = live_node.calibration
            if calibration is not None and calibration.status == "running":
                reasons.append(f"Full recalibration running · phase {calibration.phase}.")
                level = level or "violet"
            if calibration is not None and calibration.status == "failed":
                reasons.append("The last full recalibration failed; nothing was recorded.")
                level = "red"
            for link in snap.live.node_links(live_node.id):
                if link.status not in ("up", "no_input"):
                    why = "; ".join(link.status_reasons)
                    reasons.append(f"Fibre {link.id} is {link.status}" + (f" ({why})" if why else "") + ".")
                    level = level or LINK_STATUS_TONE.get(link.status, "amber")
                if link.detector.alarm:
                    reasons.append(f"Attack alarm held on fibre {link.id}.")
                    level = "red"
        elif detail is not None and detail.alert is not None:
            reasons.append(f"{detail.alert.headline} · {detail.alert.body}")
            level = detail.alert.tone
        elif node is not None and node.isolated:
            reasons.append("Node is isolated.")
            level = "red"
        return ("amber" if level in ("dim", "") and reasons else level), reasons

    def _set_alert(self, level: str, reasons: list[str], isolated: bool) -> None:
        tone_name = level if reasons else None
        if tone_name != self._alert_tone:
            self._alert_tone = tone_name
            rules = [f"background:{C.raised}", f"border:1px solid {C.soft}"]
            if tone_name:
                rules.append(f"border-left:3px solid {tone(tone_name)}")
            scoped_style(self.ops_card, ";".join(rules))
            self.alert_title.setStyleSheet(
                f"color:{tone(tone_name) if tone_name else C.ink3};background:transparent;")
        set_text(self.alert_title, f"Condition reported by the backend · {len(reasons)}" if reasons
                 else "No condition reported by the backend for this node")
        set_text(self.alert_body, " ".join(reasons) if reasons else
                 "Recalibration and isolation are executed by the backend; their result is read back here.")
        set_text(self.isolate, "Restore" if isolated else "Isolate")
        self.acknowledge.setVisible(bool(reasons))

    def _render_detail(self, snap: Snapshot) -> None:
        ids = [node.id for node in snap.topology.nodes]
        if self._selected not in ids:
            self._selected = snap.node.id if snap.node.id in ids else (ids[0] if ids else "")
        node = self._topology_node(snap, self._selected)
        for ident, row in self.rows.items():
            row.set_on(ident == self._selected)
        if node is None:
            set_text(self.crumb, snap.node.breadcrumb or "")
            set_text(self.node_id, DASH)
            set_text(self.subtitle, snap.node.name_local if snap.node.id == DASH else "No node in this source")
            set_chip(self.status_chip, "NO NODE", "dim")
            self.origin_chip.setVisible(False)
            self.remove_button.setVisible(False)
            self._set_alert("", [], False)
            self.links.set_rows([])
            set_text(self.links_note, NOT_SUPPLIED)
            self.registers.setVisible(False)
            self.memory_values.set_rows([])
            self.memory_meters.set_rows([])
            set_text(self.memory_note, NOT_SUPPLIED)
            self.record.set_rows([])
            self.calibration.set_rows([])
            self.calibration_track.setVisible(False)
            set_text(self.calibration_note, NOT_SUPPLIED)
            self.routes.set_rows([])
            set_text(self.routes_note, NOT_SUPPLIED)
            self.log.set_rows([])
            return
        live = snap.live
        live_node = live.node(node.id) if live is not None else None
        detail = snap.node if snap.node.id == node.id else None
        status, status_tone = self._status_of(snap, node)
        kind = ROLE_NAMES.get(node.kind, str(node.kind).upper())
        origin = node.inventory_origin or "source"

        set_text(self.crumb, f"{snap.topology.id or snap.topology.ring or 'inventory'} / {node.id}")
        set_text(self.node_id, node.id)
        place = (live_node.place if live_node is not None else node.place) or ""
        set_text(self.subtitle, " · ".join(part for part in (kind.lower(), place) if part))
        set_chip(self.status_chip, status, status_tone)
        set_chip(self.origin_chip, ORIGIN_LABEL.get(origin, "SOURCE OWNED"), "mute")
        self.origin_chip.setVisible(True)
        self.remove_button.setVisible(origin == "shadow")

        level, reasons = self._conditions(snap, node, live_node, detail)
        isolated = bool(node.isolated or (live_node is not None and live_node.status == "isolated"))
        self._set_alert(level, reasons, isolated)

        # links
        if live is not None:
            rows = [(f"{link.id} · {link.b if link.a == node.id else link.a}", link.status.upper(),
                     LINK_STATUS_TONE.get(link.status, "ink"),
                     f"F {fmt_fidelity(link.fidelity)} · QBER {fmt_qber(link.qber)} · SKR {fmt_rate(link.skr_bps)}")
                    for link in live.node_links(node.id)]
        else:
            rows = [(f"{link.id or link.label} · {link.b if link.a == node.id else link.a}",
                     (link.status or link.kind).upper(), link.tone,
                     f"F {link.fidelity} · QBER {link.qber} · SKR {link.skr}")
                    for link in snap.topology.links if node.id in (link.a, link.b)]
        self.links.set_rows(rows)
        set_text(self.links_title, f"Links · {len(rows)}")
        set_text(self.links_note, "" if rows else
                 "No fibre is configured on this node, so it cannot take part in pairs yet.")
        self.links_note.setVisible(not rows)

        # memory
        if live_node is not None:
            pairs = list(live_node.pairs)
            self.registers.set_tones(["violet"] * len(pairs))
            self.registers.setVisible(bool(pairs))
            self.memory_values.set_rows([
                ("Memory state", live_node.memory_state or DASH),
                ("Stored pairs", str(len(pairs))),
                ("Pair fidelities", ", ".join(f"{value:.4f}" for value in pairs) or DASH),
                ("Temperature", fmt_number(live_node.temperature_c, ".2f", "°C")),
            ])
            self.memory_meters.set_rows([])
            set_text(self.memory_note, "One cell per stored pair reported by the backend engine."
                     if pairs else "No stored pair reported by the backend.")
        elif detail is not None:
            self.registers.set_tones(list(detail.registers))
            self.registers.setVisible(bool(detail.registers))
            self.memory_values.set_rows([(f"{item.key}", item.value, item.tone) for item in detail.register_legend]
                                        if detail.registers else [])
            self.memory_meters.set_rows([(m.label, m.value, m.fraction, m.tone, m.tick) for m in detail.calibration])
            set_text(self.memory_note, detail.calibration_note if detail.calibration or detail.registers
                     else "No stored pair reported by this source.")
        else:
            self.registers.setVisible(False)
            self.memory_values.set_rows([])
            self.memory_meters.set_rows([])
            set_text(self.memory_note, NOT_SUPPLIED)

        # record
        latitude = live_node.latitude if live_node is not None else node.latitude
        longitude = live_node.longitude if live_node is not None else node.longitude
        located = latitude is not None and longitude is not None
        record = [
            ("Kind", kind.lower()),
            ("Status", status, status_tone),
            ("Location", f"{latitude:.5f}, {longitude:.5f}" if located else "not located · queue"
             if snap.live is not None else DASH, "ink" if located else "amber" if snap.live is not None else "dim"),
            ("Place", place or DASH),
            ("Connection address", node.connection_address or DASH),
            ("Port", str(node.connection_port) if node.connection_port is not None else DASH),
            ("Connection", "metadata only"),
            ("Inventory origin", origin),
            ("Calibrations", str(live_node.calibration_count if live_node is not None else node.calibration_count)),
        ]
        if node.connection_protocol:
            record.append(("Protocol", node.connection_protocol))
        if detail is not None:
            present = {key.upper() for key, *_ in record} | {"ROLE", "NETWORK STATUS", "FIBRES", "DEMANDS THROUGH",
                                                             "LAST CALIBRATION"}
            record += [(item.key.capitalize(), item.value, item.tone) for item in detail.equipment
                       if item.key.upper() not in present]
        self.record.set_rows(record)

        # calibration
        calibration = live_node.calibration if live_node is not None else None
        count = live_node.calibration_count if live_node is not None else node.calibration_count
        set_text(self.calibration_title, f"Calibration · {count} recorded")
        if calibration is not None:
            rows = [
                ("Calibration id", calibration.calibration_id or DASH),
                ("Status", calibration.status, {"running": "violet", "failed": "red"}.get(calibration.status, "green")),
                ("Phase", calibration.phase),
                ("Progress", DASH if calibration.progress is None else f"{calibration.progress * 100:.0f} %"),
                ("Duration", fmt_number(calibration.duration_s, ".1f", "s network time")),
                ("Completed", clock_of(calibration.completed_at) + " UTC" if calibration.completed_at else DASH),
                ("Memory reset", fmt_flag(calibration.memory_reset)),
                ("Report SHA-256", (calibration.report_sha256 or DASH)[:16], "violet"),
            ]
            for item in calibration.links:
                rows.append((f"{item.link_id} QBER", f"{fmt_qber(item.qber_before)} → {fmt_qber(item.qber_after)}"))
                rows.append((f"{item.link_id} sync residual",
                             f"{fmt_number(item.sync_residual_ps_before, '.2f')} → "
                             f"{fmt_number(item.sync_residual_ps_after, '.2f', 'ps')}"))
                rows.append((f"{item.link_id} key rate after", fmt_rate(item.key_rate_hz_after)))
            self.calibration.set_rows(rows)
            self.calibration_track.setVisible(calibration.progress is not None)
            self.calibration_track.set_fraction(calibration.progress or 0.0)
            set_text(self.calibration_note, "Report of the last full recalibration, written by the backend engine.")
        else:
            self.calibration.set_rows([])
            self.calibration_track.setVisible(False)
            set_text(self.calibration_note, "No full recalibration report for this node."
                     if live_node is not None else NOT_SUPPLIED)

        # routes
        if live is not None:
            rows = []
            for demand_id in live_node.routes_through if live_node is not None else ():
                demand = live.demand(demand_id)
                if demand is None:
                    rows.append((demand_id, DASH, "dim", "not in the backend demand list"))
                    continue
                rows.append((f"{demand.id} · {demand.a} ↔ {demand.b}", demand.status.upper(),
                             DEMAND_STATUS_TONE.get(demand.status, "ink"),
                             f"{' → '.join(demand.route) or 'no route'} · F {fmt_fidelity(demand.fidelity)}"))
            self.routes.set_rows(rows)
            set_text(self.routes_note, "" if rows else "No demand is routed through this node.")
            self.routes_note.setVisible(not rows)
        else:
            self.routes.set_rows([])
            set_text(self.routes_note, NOT_SUPPLIED)
            self.routes_note.setVisible(True)

        # log
        log_rows = []
        if live is not None and live_node is not None:
            subjects = {node.id, *live_node.links}
            log_rows = [(clock_of(item.at), f"{item.kind} · {item.message}", SEVERITY_TONE.get(item.severity, "ink"))
                        for item in reversed(live.events) if item.subject in subjects][:MAX_LOG_ROWS]
        if detail is not None and not log_rows:
            log_rows = [(line.at, " · ".join(part for part in (line.body, line.status) if part), line.tone)
                        for line in detail.log][:MAX_LOG_ROWS]
        self.log.set_rows(log_rows)

    # --------------------------------------------------------------- state
    def refresh(self, snap: Snapshot):
        context = (snap.campaign, snap.input_kind, snap.capabilities.adapter_id, snap.topology.id)
        source_changed = self._campaign != context
        if source_changed:
            self._campaign, self._shadow_nodes = context, []
            self._pending_add = None
            self._pending_stage = None
            self._pending_network = None
            self._clear_form()
        capabilities = snap.capabilities
        mode = ("network" if capabilities.allows("add_network_node") else
                "persistent" if capabilities.allows("add_simulated_node") else "shadow")
        if source_changed or mode != self._mode:
            self._set_mode(mode)
        pending = self._pending_add
        if pending and snap.topology.persisted and type(snap.topology.version) is int and snap.topology.version > pending.expected_version:
            confirmed = next((n for n in snap.topology.nodes if n.id == pending.node_id
                              and n.inventory_origin == "orion-console" and n.connection_address == pending.address
                              and n.connection_port == pending.port
                              and n.kind == ("node" if pending.role == "endpoint" else "repeater")), None)
            if confirmed:
                self._pending_add = None
                self._clear_form()
                self._say(f"CONFIRMED · {pending.node_id} persisted at topology version {snap.topology.version}. No device contacted.")
                self._selected = pending.node_id
            else:
                self.command_failed("Readback does not match the requested node")
        staged = self._pending_stage
        if staged and any(n.id == staged['id'] and n.inventory_origin == 'shadow'
                          and n.connection_address == staged['address'] and n.connection_port == staged['port']
                          for n in snap.topology.nodes):
            self._pending_stage = None
            self._clear_form()
            self._say(f"CONFIRMED · {staged['id']} added to Nodes and Topology. No device contacted.")
            self._selected = staged['id']
        if self._pending_network is not None:
            self._confirm_network(snap)
        self._base_nodes = [{"id": n.id, "role": ROLE_NAMES.get(n.kind, str(n.kind).upper()),
                             "address": n.connection_address or "NOT SUPPLIED",
                             "port": str(n.connection_port) if n.connection_port is not None else "—",
                             "protocol": n.connection_protocol or ("SIMULATOR" if n.inventory_origin == "orion-console" else "SOURCE"),
                             "status": self._status_of(snap, n)[0],
                             "origin": n.inventory_origin or "source"} for n in snap.topology.nodes]
        measured = snap.input_kind == "measured"
        set_text(self.inventory_note, (
            "Persistent simulator inventory · configure fibres in Topology. " if self._persistent else
            "Measured topology is source-owned and read only. " if measured else
            "Snapshot inventory plus local shadow records. ") + "No physical device is contacted.")
        self._render_inventory(snap)
        self._render_detail(snap)
        located = sum(bool(n.located) for n in snap.topology.nodes)
        scope = ("NETWORK INVENTORY" if self._mode == "network" else
                 "SIMULATOR INVENTORY" if self._persistent else "LOCAL SHADOW")
        where = f" · {located} LOCATED · {len(snap.topology.nodes) - located} IN QUEUE" if snap.live is not None else ""
        self.bar.set_status(f"NODES · {len(snap.topology.nodes)} REGISTERED{where} · {scope}")


__all__ = ["NodeScreen"]
