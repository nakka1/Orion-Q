"""Nodes awaiting declared coordinates, without geographic connection lines.

Click a node to inspect its real connections and state. The inventory and
logical views retain those connections while the node awaits a location.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..theme import C, mono, text as text_font
from .figures import marker_kind, paint_marker
from .inspector import op_button
from .network_views import NetModel
from .prims import Clickable, Frame, L, Label, hbox, scoped_style, vbox

WIRES_H = 0
CHIP_H = 40


class _ChipMarker(QWidget):
    def __init__(self, node, parent=None) -> None:
        super().__init__(parent)
        self.node = node
        self.setFixedSize(32, 32)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.node.isolated:
            p.setOpacity(0.4)
        paint_marker(p, QPointF(self.width() / 2, self.height() / 2), marker_kind(self.node), self.node.health,
                     calibrating=self.node.status == "calibrating")
        p.end()


class QueueChip(Frame):
    """One queued node: marker, id, location state and "Set location…"."""

    clicked = Signal(str)
    locate = Signal(str)

    def __init__(self, node, summary: str, parent=None) -> None:
        QWidget.__init__(self, parent)
        self.hide()
        self.node_id = node.id
        self._on: bool | None = None
        self.setFixedHeight(CHIP_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"Node {node.id}, awaiting location, {summary}")
        lay = hbox(self, (8, 0, 6, 0), 8)
        self.marker = _ChipMarker(node)
        lay.addWidget(self.marker)
        self.name = L(node.id, mono(12), C.ink)
        lay.addWidget(self.name)
        self.summary = L(summary, text_font(12), C.ink3)
        lay.addWidget(self.summary)
        self.button = op_button("Set location…", "set_node_location", height=28)
        self.button.setFixedHeight(28)
        self.button.clicked.connect(lambda: self.locate.emit(self.node_id))
        lay.addWidget(self.button)
        for label in (self.name, self.summary):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.set_on(False)

    def set_on(self, on: bool) -> None:
        if on != self._on:
            self._on = on
            scoped_style(self, f"background:{C.sel if on else C.bg};border:1px solid {C.acc if on else C.line};")

    @property
    def on(self) -> bool:
        return bool(self._on)

    def update_node(self, node, summary: str) -> None:
        self.marker.node = node
        self.marker.update()
        if self.summary.text() != summary:
            self.summary.setText(summary)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.node_id)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self.node_id)
            event.accept()
            return
        super().keyPressEvent(event)


class _Pager(Clickable):
    def __init__(self, value: str, name: str, parent=None) -> None:
        super().__init__(value, parent)
        self.setFont(mono(12))
        self.setFixedSize(26, CHIP_H)
        self.setAccessibleName(name)
        self.setToolTip(name)
        self.setStyleSheet(f"QPushButton{{color:{C.ink};border:1px solid {C.line};background:{C.bg};}}"
                           f"QPushButton:hover{{border-color:{C.acc};}}")


class _ChipHost(QWidget):
    resized = Signal()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.resized.emit()


class UnplacedQueue(QWidget):
    node_selected = Signal(str)
    link_selected = Signal(str)
    locate = Signal(str)
    anchors_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.net = NetModel()
        self.map_visible = True
        self.chips: dict[str, QueueChip] = {}
        self._first = 0                         # index of the first chip shown (paging, never wider than the band)
        self._shown: list = []
        lay = vbox(self, (0, 0, 0, 0), 0)
        self.wires = QWidget(self)
        self.wires.setFixedHeight(WIRES_H)
        lay.addWidget(self.wires)
        band = Frame(bg=C.raised, border=C.soft)
        row = hbox(band, (10, 6, 10, 6), 12)
        self.title = Label("Awaiting location · 0", C.ink)
        row.addWidget(self.title)
        self.host = _ChipHost()
        self.host.resized.connect(self._layout_chips)
        self.host.setFixedHeight(CHIP_H)
        self.host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        row.addWidget(self.host, 1)
        self.previous = _Pager("‹", "Previous nodes awaiting location", self.host)
        self.next = _Pager("›", "More nodes awaiting location", self.host)
        self.previous.clicked.connect(lambda: self._page(-1))
        self.next.clicked.connect(lambda: self._page(1))
        lay.addWidget(band)
        self.band = band
        self.hide()

    # ------------------------------------------------------------------ data
    def node_ids(self) -> list:
        return list(self.chips)

    def shown_ids(self) -> list:
        """Chips visible on the current page of the band."""

        return list(self._shown)

    @staticmethod
    def _summary(net: NetModel, node) -> str:
        return "No location"

    @staticmethod
    def _tooltip(net: NetModel, node) -> str:
        return f"Open {node.id} to inspect its status, connections and actions. No location has been declared."

    def set_model(self, net: NetModel, map_visible: bool = True) -> None:
        self.net, self.map_visible = net, map_visible
        nodes = net.unlocated
        ids = [node.id for node in nodes]
        if ids != list(self.chips):
            for chip in self.chips.values():
                chip.hide()
                chip.setParent(None)
                chip.deleteLater()
            self.chips = {}
            for node in nodes:
                chip = QueueChip(node, self._summary(net, node), self.host)
                chip.clicked.connect(self.node_selected.emit)
                chip.locate.connect(self.locate.emit)
                self.chips[node.id] = chip
            self._first = min(self._first, max(0, len(ids) - 1))
        for node in nodes:
            chip = self.chips[node.id]
            chip.update_node(node, self._summary(net, node))
            chip.setToolTip(self._tooltip(net, node))
            chip.set_on(net.selected("node", node.id))
        if net.selection[0] == "node" and net.selection[1] in ids and net.selection[1] not in self._shown:
            self._first = ids.index(net.selection[1])
        title = f"Awaiting location · {len(nodes)}"
        if self.title.text() != title:
            self.title.setText(title)
        self.setVisible(bool(nodes))
        self._layout_chips()

    def reveal(self, node_id: str) -> None:
        """Page to the chip of `node_id`."""

        ids = list(self.chips)
        if node_id in ids and node_id not in self._shown:
            self._first = ids.index(node_id)
            self._layout_chips()

    def _page(self, step: int) -> None:
        count = max(1, len(self._shown))
        self._first = max(0, min(len(self.chips) - 1, self._first + step * count))
        self._layout_chips()

    def _layout_chips(self) -> None:
        """Place the chips of the current page; the rest are reached with ‹ ›."""

        chips = list(self.chips.values())
        width = self.host.width()
        gap, pager = 10, 26 + 8
        total = sum(chip.sizeHint().width() for chip in chips) + gap * max(0, len(chips) - 1)
        paged = total > width
        if not paged:
            self._first = 0
        x = pager if paged else 0
        limit = width - (pager if paged else 0)
        shown, full = [], False
        for index, chip in enumerate(chips):
            hint = chip.sizeHint().width()
            if index < self._first or full or (shown and x + hint > limit):
                full = full or index >= self._first
                chip.hide()
                continue
            chip.setGeometry(x, 0, min(hint, max(40, limit - x)), CHIP_H)
            chip.show()
            shown.append(chip.node_id)
            x += hint + gap
        self._shown = shown
        self.previous.setVisible(paged)
        self.next.setVisible(paged)
        if paged:
            self.previous.move(0, 0)
            self.next.move(width - 26, 0)
            self.previous.setEnabled(self._first > 0)
            self.next.setEnabled(self._first + len(shown) < len(chips))
            self.next.setToolTip(f"{len(chips) - self._first - len(shown)} more nodes awaiting location")
        self.wires.update()
        self.anchors_changed.emit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_chips()

    # --------------------------------------------------------------- geometry
    def wires_for_map(self) -> tuple:
        """No geographic anchors exist until both endpoints are located."""
        return {}, {}

    def wire_geometry(self) -> list:
        """Connections belong in the node details, not the unlocated band."""
        return []

    def drawn_link_keys(self) -> set:
        return set()


__all__ = ["CHIP_H", "QueueChip", "UnplacedQueue", "WIRES_H"]
