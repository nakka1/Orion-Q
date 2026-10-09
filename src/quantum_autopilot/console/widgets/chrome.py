"""Application chrome owned by the shell: top bar, icon rail, status bar.

Screens render only their body. One instance of each widget below frames the
whole workspace, as in the approved prototype.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QSizePolicy, QWidget

from ..theme import C, RAIL_W, STATUSBAR_H, TOPBAR_H, current_theme, label_font, mono, qc, text
from .prims import (
    Btn, Chip, Clickable, Frame, L, SegBar, Title, clear_layout, hbox, vbox,
)
from .brand_mark import BlochQMark, OrionQWordmark

Mark = BlochQMark  # older imports

# key, tooltip title, accessible name — navigation order and numeric shortcuts.
ROUTES = (
    ("topology", "Topology", "1 Topology"),
    ("telemetry", "Fidelity & decoherence", "2 Telemetry"),
    ("keys", "Key management", "3 Key management"),
    ("node", "Nodes", "4 Nodes"),
    ("communicators", "Comunicadores", "5 Comunicadores"),
    ("fpga", "Edge control", "6 Edge control"),
    ("audit", "Access & audit", "7 Access and audit"),
)


class Overlay(Frame):
    """Never opens on hover. Escape/outside click dismiss within this screen."""

    def __init__(self, parent, width=240):
        super().__init__(C.bg, C.line, parent=parent)
        self.setFixedWidth(width)
        self.anchor = None
        self.hide()
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        if self.isVisible():
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self.hide()
                return True
            if event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
                inside = watched is self or self.isAncestorOf(watched)
                anchor = watched is self.anchor
                if not inside and not anchor:
                    self.hide()
        return False

    def open_at(self, x, y):
        self.adjustSize()
        self.move(max(0, min(int(x), self.parentWidget().width() - self.width())),
                  max(0, min(int(y), self.parentWidget().height() - self.height())))
        self.show()
        self.raise_()


# ------------------------------------------------------------------ icons
def paint_nav_icon(p: QPainter, kind: str, rect: QRectF, colour) -> None:
    """Draw one of the prototype's 18-unit line icons inside `rect`."""

    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(rect.left(), rect.top())
    p.scale(rect.width() / 18.0, rect.height() / 18.0)
    pen = QPen(qc(colour), 1.4)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    def line(x1, y1, x2, y2):
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def poly(points):
        p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in points]))

    if kind == "topology":
        poly(((4, 13), (9, 5), (14, 12)))
        for x, y in ((4, 13), (9, 5), (14, 12)):
            p.drawEllipse(QPointF(x, y), 2, 2)
    elif kind == "telemetry":
        poly(((1, 10), (5, 10), (7, 4), (10, 15), (12, 8), (17, 8)))
    elif kind == "keys":
        p.drawEllipse(QPointF(5, 8), 3, 3)
        line(8, 8, 17, 8)
        line(13, 8, 13, 12)
        line(16, 8, 16, 11)
    elif kind == "node":
        p.drawRect(QRectF(2, 3, 11, 5))
        p.drawRect(QRectF(2, 10, 11, 5))
        line(15, 5, 18, 5)
        line(16.5, 3.5, 16.5, 6.5)
    elif kind == "communicators":
        # Transceiver: antenna waves above an optical receiver, distinct from
        # the node inventory's stacked-server icon.
        p.drawRoundedRect(QRectF(3, 10, 12, 6), 1, 1)
        line(9, 10, 9, 4)
        p.drawEllipse(QPointF(9, 4), 1, 1)
        p.drawArc(QRectF(5, 0, 8, 8), 205*16, 130*16)
        p.drawArc(QRectF(2, -3, 14, 14), 205*16, 130*16)
        p.drawEllipse(QPointF(6, 13), .8, .8)
        line(10, 13, 13, 13)
    elif kind == "fpga":
        p.drawRect(QRectF(4, 4, 10, 10))
        for value in (6, 9, 12):
            line(value, 1, value, 4)
            line(value, 14, value, 17)
            line(1, value, 4, value)
            line(14, value, 17, value)
    elif kind == "audit":
        p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in ((9, 1), (16, 4), (15, 11), (9, 17), (3, 11), (2, 4))]))
        poly(((6, 9), (8, 11), (12, 6)))
    elif kind == "signout":
        poly(((7, 3), (3, 3), (3, 15), (7, 15)))
        line(7, 9, 16, 9)
        poly(((13, 6), (16, 9), (13, 12)))
    p.restore()


class NavButton(Clickable):
    """`.nav` — 44 × 44 icon button; active = accent left border + selected ground."""

    def __init__(self, kind: str, tooltip: str, accessible: str, parent=None) -> None:
        super().__init__("", parent)
        self.kind = kind
        self.active = False
        self.setFixedSize(44, 44)
        from .button_help import navigation_description
        self.setToolTip(tooltip + "\n" + navigation_description(kind))
        self.setAccessibleName(accessible)
        self.setStyleSheet("QPushButton{border:0;background:transparent;}")

    def set_active(self, active: bool) -> None:
        if active != self.active:
            self.active = active
            self.update()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        hover = self.underMouse() and self.isEnabled()
        if self.active:
            p.fillRect(self.rect(), qc(C.sel))
            p.fillRect(0, 0, 2, self.height(), qc(C.acc))
        elif hover:
            p.fillRect(self.rect(), qc(C.hover))
        colour = C.acc if self.active else C.ink if hover else C.ink3
        # 2 px left border + centred 20 px icon, as the flex-centred prototype button.
        paint_nav_icon(p, self.kind, QRectF(13, 12, 20, 20), colour)
        if self._focus_visible and self.hasFocus():
            p.setPen(QPen(qc(C.acc), 2))
            p.drawRect(self.rect().adjusted(1, 1, -1, -1))
        p.end()


class IconRail(Frame):
    """60 px rail: screens in fixed order, and Sign out at the bottom."""

    navigate = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(C.raised, C.soft, sides="r", parent=parent)
        self.setFixedWidth(RAIL_W)
        self.setAccessibleName("Screens")
        lay = vbox(self, (0, 12, 0, 12), 6)
        lay.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.buttons: dict[str, NavButton] = {}
        for index, (key, title, accessible) in enumerate(ROUTES, start=1):
            button = NavButton(key, f"[{index}] {title}", accessible)
            button.clicked.connect(lambda checked=False, k=key: self.navigate.emit(k))
            lay.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
            self.buttons[key] = button
        lay.addStretch(1)
        self.sign_out = NavButton("signout", "Sign out", "Sign out")
        self.sign_out.clicked.connect(lambda: self.navigate.emit("boot"))
        lay.addWidget(self.sign_out, 0, Qt.AlignmentFlag.AlignHCenter)

    def set_active(self, key: str) -> None:
        for name, button in self.buttons.items():
            button.set_active(name == key)


class PaletteButton(Clickable):
    """Top-bar launcher: `Ctrl K` chip and the "Run a command…" hint."""

    def __init__(self, parent=None) -> None:
        super().__init__("", parent)
        self.setMinimumSize(300, 34)
        self.setFixedHeight(34)
        self.setAccessibleName("Run a command (Ctrl+K)")
        from .button_help import navigation_description
        self.setToolTip(navigation_description("palette"))
        self.setStyleSheet(
            f"QPushButton{{background:{C.raised};border:1px solid {C.line};text-align:left;}}"
            f"QPushButton:hover{{background:{C.hover};}}")
        lay = hbox(self, (12, 0, 12, 0), 10)
        chip = Chip("Ctrl K", "ink", size=10.5)
        chip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(chip)
        hint = L("Run a command…", text(13.5), C.ink2)
        hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(hint, 1)


class TopBar(Frame):
    """52 px bar: mark, name, breadcrumb, command launcher, theme, data source."""

    palette_requested = Signal()
    theme_selected = Signal(str)
    source_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(border=C.soft, sides="b", parent=parent)
        self.setFixedHeight(TOPBAR_H)
        lay = hbox(self, (18, 0, 16, 0), 18)
        self.brand = OrionQWordmark(30)
        lay.addWidget(self.brand)
        self.crumb = L("", mono(13), C.ink3, elide=True)
        self.crumb.setMinimumWidth(0)
        self.crumb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.crumb, 1)
        self.palette_button = PaletteButton()
        self.palette_button.clicked.connect(self.palette_requested.emit)
        lay.addWidget(self.palette_button)
        self.theme_toggle = SegBar((("light", "Light"), ("dark", "Dark")), current_theme())
        self.theme_toggle.selected.connect(self.theme_selected.emit)
        lay.addWidget(self.theme_toggle)
        self.source_button = Btn("Data source ▾", variant="solid")
        self.source_button.setToolTip("Choose the demonstration, local service or measured input used by the workspace.")
        self.source_button.clicked.connect(self.source_requested.emit)
        lay.addWidget(self.source_button)
        self._source_short = "Data source"
        self._effect_mode = False

    def set_crumb(self, value: str) -> None:
        if self.crumb.full_text != value:
            self.crumb.setText(value)

    def set_source(self, short: str) -> None:
        self._source_short = short
        value = "Unplug from E.V.A." if self._effect_mode else f"{short} ▾"
        if self.source_button.text() != value:
            self.source_button.setText(value)

    def set_effect_mode(self, enabled: bool) -> None:
        self._effect_mode = bool(enabled)
        self.source_button.setToolTip(
            "End the cosmetic E.V.A. mode and return to the standard sign-in screen." if enabled else
            "Choose the demonstration, local service or measured input used by the workspace.")
        self.set_source(self._source_short)


class StatusBar(Frame):
    """30 px bar: status text left; source, physical lock and snapshot clock right."""

    capabilities_requested = Signal()
    terminal_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(border=C.soft, sides="t", parent=parent)
        self.setFixedHeight(STATUSBAR_H)
        lay = hbox(self, (18, 0, 18, 0), 18)
        self._message = ""
        self.message = L("", mono(12), C.ink2, elide=True)
        self.message.setMinimumWidth(0)
        self.message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.message, 1)
        self.terminal_button = Clickable("Terminal")
        self.terminal_button.setCheckable(True)
        self.terminal_button.setFont(mono(12))
        self.terminal_button.setStyleSheet(
            f"QPushButton{{color:{C.ink3};border:0;background:transparent;padding:2px 8px;}}"
            f"QPushButton:hover{{color:{C.ink};background:{C.hover};}}"
            f"QPushButton:checked{{color:{C.acc_t};background:{C.sel};}}")
        self.terminal_button.setAccessibleName("Show or hide terminal (Ctrl+J)")
        self.terminal_button.setToolTip("Show or hide the terminal (Ctrl+J). The screen uses the freed space; command history and draft are kept.")
        self.terminal_button.clicked.connect(self.terminal_requested.emit)
        lay.addWidget(self.terminal_button)
        self.source = Clickable("")
        self.source.setFont(mono(12))
        self.source.setStyleSheet(
            f"QPushButton{{color:{C.ink3};border:0;background:transparent;padding:0;}}"
            f"QPushButton:hover{{color:{C.ink};}}")
        self.source.setAccessibleName("Data source and input capabilities")
        self.source.clicked.connect(self.capabilities_requested.emit)
        lay.addWidget(self.source)
        self.lock = L("Physical locked", mono(12), C.acc_t)
        lay.addWidget(self.lock)
        self.clock = L("", mono(12), C.ink3)
        lay.addWidget(self.clock)
        self.effect_status = L("", mono(11), C.ink3, elide=True)
        self.effect_status.setMinimumWidth(0)
        self.effect_status.setToolTip("Cosmetic interface sequence only; no authenticated identity or satellite connection is established.")
        self.effect_status.hide()
        lay.addWidget(self.effect_status)

    # QStatusBar-compatible surface used by the shell and by validators.
    def showMessage(self, value: str, _timeout: int = 0) -> None:  # noqa: N802 - Qt naming
        self._message = value
        if self.message.full_text != value:
            self.message.setText(value)

    def currentMessage(self) -> str:  # noqa: N802 - Qt naming
        return self._message

    def set_source(self, label: str, tooltip: str | None = None) -> None:
        if self.source.text() != label:
            self.source.setText(label)
        if tooltip is not None and self.source.toolTip() != tooltip:
            self.source.setToolTip(tooltip)

    def set_clock(self, value: str) -> None:
        if self.clock.text() != value:
            self.clock.setText(value)

    def set_effect_connected(self, connected: bool) -> None:
        self.effect_status.setText("eva operator connected" if connected else "")
        self.effect_status.setVisible(bool(connected))


class ScreenHeader(Frame):
    """Body header of a screen: heading, status label, optional status widgets."""

    def __init__(self, heading: str, parent=None) -> None:
        super().__init__(border=C.soft, sides="b", parent=parent)
        lay = hbox(self, (24, 14, 24, 14), 16)
        self.heading = Title(heading)
        lay.addWidget(self.heading)
        self.status_host = QWidget()
        self.status_host.setMinimumWidth(0)
        self.status_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.status_lay = hbox(self.status_host, (0, 0, 0, 0), 14)
        lay.addWidget(self.status_host, 1)
        self.actions = hbox(None, (0, 0, 0, 0), 8)
        lay.addLayout(self.actions)
        self._status: tuple | None = None

    def set_status(self, value: str, color: str | None = None) -> None:
        key = (value, str(color))
        if self._status == key:
            return
        self._status = key
        clear_layout(self.status_lay)
        label = L(value, label_font(11), color if color is not None else C.ink3, elide=True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setToolTip(value)
        self.status_lay.addWidget(label, 1)

    def set_status_widgets(self, widgets: list[QWidget]) -> None:
        self._status = None
        clear_layout(self.status_lay)
        for widget in widgets:
            self.status_lay.addWidget(widget)
        self.status_lay.addStretch(1)


__all__ = ["IconRail", "Mark", "NavButton", "Overlay", "PaletteButton", "ROUTES", "ScreenHeader",
           "StatusBar", "TopBar", "paint_nav_icon"]
