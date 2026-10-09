"""Workspace entry: authenticated OS identity and source selection.

Authentication and authorization belong to the backend. This view shows its
identity metadata; selecting an input source never grants permissions. The
operator line shows `snap.operator`, the
sequence rows are `snap.boot.checks`, the build label is `snap.boot.build` and
the execution boundary is derived from the active source's capabilities.
Selecting a data-source row only marks it. Sign in requests backend verification
before the shell applies the marked source and opens Topology.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLineEdit, QSizePolicy, QWidget

from quantum_autopilot.product import PRODUCT_NAME, product_version
from ..capabilities import OPERATIONS
from ..contract import Snapshot
from ..theme import C, MIN_CONTENT_H, MIN_CONTENT_W, mono, text, text_tone
from ..widgets import Btn, Frame, KVRow, L, Label, Prose, RowBtn, clear_layout, hbox, vbox
from ..widgets.adaptive_layout import AdaptiveGrid
from ..widgets.brand_mark import OrionQWordmark
from ..widgets.prims import scroll_panel
from .base import Screen

AUDIT_SENTENCE = ("Backend permissions apply to every call. Security records identify the authenticated user "
                  "and use an HMAC chain with an OS-protected checkpoint. Physical actuation remains locked.")
OPERATOR_NOTE = "Identity and roles come from the backend; choosing a source does not authenticate or grant access."
SPONSORSHIP = "Sponsored by SENAI CIMATEC - LAQCC/QuIIN"
SEQUENCE_LOGIN = "s. ikari"
SEQUENCE_PASSWORD = "thirdpilot"

SIMULATION_CLASSES = {"simulation", "simulation_inventory", "synthetic_stream"}
SHADOW_CLASSES = {"shadow_replay", "shadow_diagnostic"}


@dataclass(frozen=True)
class SourceOption:
    key: str                # "demo" | "measured" | "service"
    label: str
    note: str
    short: str              # top-bar label of the source
    enabled: bool = True


SOURCE_OPTIONS = (
    SourceOption("demo", "Demonstration", "simulated input · real pipeline", "Demonstration"),
    SourceOption("measured", "Measured telemetry (local JSON plug)", "read only", "Measured input"),
    SourceOption("service", "Local service", "operator workspace", "Local service"),
)


def execution_boundary(snap: Snapshot | None) -> list[tuple[str, str, str]]:
    """(label, value, tone) rows from what the active source declares.

    Simulation and Shadow are ENABLED only when the source declares an
    operation of that class. Physical actuation and hardware access are not
    offered by any adapter of this product.
    """

    operations = snap.capabilities.operations if snap is not None else ()
    classes = {OPERATIONS.get(operation) for operation in operations}
    simulation = bool(classes & SIMULATION_CLASSES)
    shadow = bool(classes & SHADOW_CLASSES)
    return [
        ("Simulation", "ENABLED" if simulation else "UNAVAILABLE", "ok" if simulation else "mute"),
        ("Shadow", "ENABLED" if shadow else "UNAVAILABLE", "ok" if shadow else "mute"),
        ("Physical", "LOCKED", "warn"),
        ("Hardware access", "DISABLED", "mute"),
    ]


class SourceRow(RowBtn):
    def __init__(self, option: SourceOption, parent=None) -> None:
        super().__init__(parent, padding=(14, 12, 14, 12), gap=10, boxed=True)
        self.option = option
        self.dot = QWidget()
        self.dot.setFixedSize(10, 10)
        self.add(self.dot)
        self.add(L(option.label, text(13.5), C.ink), 1)
        self.note = self.add(Label(option.note))
        self.setAccessibleName(f"Data source: {option.label} ({option.note})")
        self.setEnabled(option.enabled)
        if not option.enabled:
            self.setToolTip("This source is not configured for this session.")
        self.mark(False)

    def mark(self, on: bool) -> None:
        self.set_on(on)
        style = f"background:{C.acc if on else C.ink4};"
        if self.dot.styleSheet() != style:
            self.dot.setStyleSheet(style)


class _BootColumn(Frame):
    def resizeEvent(self, event):
        compact = self.width() < 680
        if self.layout() is not None:
            margin = 32 if compact else 64
            side = 32 if compact else 72
            self.layout().setContentsMargins(side, margin, side, margin)
            self.layout().setSpacing(18 if compact else 28)
        super().resizeEvent(event)


class BootScreen(Screen):
    open_requested = Signal(str)   # key of the marked data source
    authenticated = Signal()       # older name: the workspace was requested (not authentication)
    login_requested = Signal(str, object)  # account and ephemeral password, None for current OS session
    effect_requested = Signal()
    unplug_requested = Signal()
    key = "boot"
    title = PRODUCT_NAME
    heading = "Sign in"

    def __init__(self, parent=None, options=SOURCE_OPTIONS):
        super().__init__(parent)
        self.setMinimumSize(MIN_CONTENT_W, MIN_CONTENT_H)
        self.selected_source: str | None = None
        self._login_available = False
        self._fixture_entry = False
        self._login_busy = False
        self._login_verified = False

        # ---- left: what started ------------------------------------------
        left = _BootColumn(border=C.soft, sides="r")
        ll = vbox(left, (72, 64, 72, 64), 28)
        self.brand = OrionQWordmark(72)
        ll.addWidget(self.brand, 0, Qt.AlignmentFlag.AlignLeft)
        names = QWidget()
        nl = vbox(names, (0, 0, 0, 0), 10)
        nl.addWidget(L("Integrated quantum network control.", text(18), C.ink2))
        nl.addWidget(Label("N.A.K.A. · Network Autonomy & Key Assurance"))
        ll.addWidget(names)
        sequence = QWidget()
        sl = vbox(sequence, (0, 0, 0, 0), 0)
        sl.addWidget(Label("Initialisation sequence"))
        sl.addSpacing(8)
        self.check_host = QWidget()
        self.check_lay = vbox(self.check_host, (0, 0, 0, 0), 0)
        sl.addWidget(self.check_host)
        ll.addWidget(sequence)
        self._checks: list | None = None
        self.audit = Prose(AUDIT_SENTENCE, 13.5, C.ink2)
        self.audit.setMaximumWidth(520)
        ll.addWidget(self.audit)
        self.notice = Prose("", 12.5, C.ink3)
        self.notice.setMaximumWidth(520)
        ll.addWidget(self.notice)
        ll.addStretch(1)
        self.build = Label("")
        self.build.setAccessibleName("Selected source build provenance")
        self.build.setToolTip("Build provenance reported by the selected data source.")
        ll.addWidget(self.build)

        # ---- right: operator, data source, boundary ------------------------
        right = _BootColumn(bg=C.raised)
        rl = vbox(right, (72, 64, 72, 64), 28)
        operator = QWidget()
        ol = vbox(operator, (0, 0, 0, 0), 6)
        ol.addWidget(L("Sign in", text(24), C.ink))
        ol.addWidget(Prose("Use your Windows account or its current authenticated session. "
                          "Backend roles apply to every operation; physical actuation remains locked.", 13.5, C.ink2))
        ol.addWidget(Label("Operator ID / Windows account"))
        self.operator_id = QLineEdit()
        self.operator_id.setMaxLength(256)
        self.operator_id.setPlaceholderText("DOMAIN\\account or account@example.org")
        self.operator_id.setAccessibleName("Operator ID / Windows account")
        self.operator_id.setMinimumHeight(38)
        self.operator_id.textChanged.connect(self._update_login_actions)
        ol.addWidget(self.operator_id)
        ol.addWidget(Label("Password"))
        password_row = QWidget()
        pl = hbox(password_row, (0, 0, 0, 0), 8)
        self.password = QLineEdit()
        self.password.setMaxLength(1024)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setAccessibleName("Windows password")
        self.password.setMinimumHeight(38)
        self.password.setPlaceholderText("Windows account password")
        self.password.textChanged.connect(self._update_login_actions)
        self.password.returnPressed.connect(self._open)
        pl.addWidget(self.password, 1)
        self.show_password = Btn("Show", height=38)
        self.show_password.setToolTip("Show or hide the password while entering it.")
        self.show_password.clicked.connect(self._toggle_password)
        pl.addWidget(self.show_password)
        ol.addWidget(password_row)
        self.operator = L("—", mono(22), C.ink, elide=True)
        self.operator.setMinimumWidth(0)
        self.operator.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.operator.hide()  # Kept for identity inspectors; account entry is separate.
        self.identity_note = Prose(OPERATOR_NOTE, 13.5, C.ink2)
        ol.addWidget(self.identity_note)
        self.login_feedback = Prose("Accounts and permissions are managed by your organisation. "
                                    "There is no sign-up or password reset here.", 12.5, C.ink3)
        self.login_feedback.setAccessibleName("Sign-in result")
        ol.addWidget(self.login_feedback)
        rl.addWidget(operator)

        sources = QWidget()
        self.source_lay = vbox(sources, (0, 0, 0, 0), 8)
        self.source_lay.addWidget(Label("Data source"))
        self.source_rows: dict[str, SourceRow] = {}
        rl.addWidget(sources)

        boundary = QWidget()
        bl = vbox(boundary, (0, 0, 0, 0), 0)
        bl.addWidget(Label("Execution boundary"))
        bl.addSpacing(6)
        self.active_source = KVRow("Active source", "—")
        bl.addWidget(self.active_source)
        self.boundary_rows: dict[str, KVRow] = {}
        for label, value, tone_name in execution_boundary(None):
            row = KVRow(label, value, tone_name=tone_name)
            self.boundary_rows[label] = row
            bl.addWidget(row)
        rl.addWidget(boundary)
        rl.addStretch(1)

        actions = QWidget()
        al = vbox(actions, (0, 0, 0, 0), 8)
        opening = QWidget()
        opening_row = hbox(opening, (0, 0, 0, 0), 12)
        self.open_button = Btn("Sign in →", variant="solid", height=42)
        self.open_button.setStyleSheet(self.open_button.styleSheet().replace("padding:0 12px", "padding:0 18px"))
        self.open_button.clicked.connect(self._open)
        self.sign_in = self.open_button     # older attribute name
        opening_row.addWidget(self.open_button)
        opening_row.addStretch(1)
        self.version_label = L(f"Version {product_version()}", mono(12), C.ink2)
        self.version_label.setAccessibleName("Distribution version")
        self.version_label.setToolTip("Application distribution version, independent of source build provenance.")
        opening_row.addWidget(self.version_label)
        al.addWidget(opening)
        self.os_session_button = Btn("Use current Windows session", height=32)
        self.os_session_button.setToolTip("Confirm the identity already authenticated by Windows. "
                                         "The backend checks its current roles and revocation status.")
        self.os_session_button.clicked.connect(self._open_os_session)
        self.os_session_button.hide()
        self.sponsorship = Prose(SPONSORSHIP, 12.5, C.ink2)
        self.sponsorship.setTextFormat(Qt.TextFormat.PlainText)
        self.sponsorship.setAccessibleName(SPONSORSHIP)
        al.addWidget(self.sponsorship)
        al.addWidget(self.os_session_button)
        al.addWidget(Prose("Local journal · no physical actuation", 11.5, C.ink3))
        self.effect_status = L("", mono(11), C.ink3)
        self.effect_status.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.effect_status.setToolTip("Cosmetic interface sequence only; no identity or connection is established.")
        self.effect_status.hide()
        rl.addWidget(actions)
        self.columns = AdaptiveGrid([left, right], columns=2, gap=0, minimum_column=560)
        self.scroll = scroll_panel(self.columns)
        self.root.addWidget(self.scroll, 1)
        # Keep the session marker visible at the lower right when the form
        # needs scrolling on a compact display.
        self.effect_footer = Frame(border=C.soft, sides="t")
        effect_row = hbox(self.effect_footer, (16, 6, 16, 6), 12)
        self.unplug_button = Btn("Unplug from E.V.A.", height=32)
        self.unplug_button.setToolTip("End the cosmetic E.V.A. mode and return to the standard sign-in screen.")
        self.unplug_button.clicked.connect(self.unplug_requested.emit)
        effect_row.addWidget(self.unplug_button)
        effect_row.addStretch(1)
        effect_row.addWidget(self.effect_status)
        self.effect_footer.hide()
        self.root.addWidget(self.effect_footer)
        self.set_sources(options)

    # ------------------------------------------------------------- sources
    def set_sources(self, options, selected: str | None = None) -> None:
        """Replace the selectable rows (order kept) and mark one of them."""

        for row in self.source_rows.values():
            self.source_lay.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.source_rows = {}
        for option in options:
            row = SourceRow(option)
            row.clicked.connect(lambda checked=False, key=option.key: self.select_source(key))
            self.source_lay.addWidget(row)
            self.source_rows[option.key] = row
        self.select_source(selected if selected is not None else self.selected_source)

    def select_source(self, key: str | None) -> None:
        """Mark a row. Nothing is applied until the workspace is opened."""

        row = self.source_rows.get(key) if key is not None else None
        if row is None or not row.isEnabled():
            key = None
        self.selected_source = key
        for name, item in self.source_rows.items():
            item.mark(name == key)
        self._update_login_actions()

    def _open(self) -> None:
        if self._login_busy:
            return
        if self.operator_id.text().strip().casefold() == SEQUENCE_LOGIN:
            accepted = self.password.text() == SEQUENCE_PASSWORD
            self.password.clear()
            if accepted:
                self.effect_requested.emit()
            else:
                self.login_feedback.setText("Display access phrase not accepted.")
            return
        if self.selected_source is not None and (self._fixture_entry or self._login_verified):
            self.open_requested.emit(self.selected_source)
            self.authenticated.emit()
        elif self._login_available and self.selected_source is not None:
            password = self.password.text()
            if not self.operator_id.text().strip() or not password:
                self.login_feedback.setText("Enter a Windows account and password, or use the current Windows session.")
                return
            self.password.clear()
            self.login_requested.emit(self.operator_id.text().strip(), password)

    def _open_os_session(self):
        if self._login_available and self.selected_source is not None and not self._login_busy:
            self.password.clear()
            self.login_requested.emit(self.operator_id.text().strip(), None)

    def configure_login(self, *, available=False, display_name="", fixture=False):
        self._login_available, self._fixture_entry = available, fixture
        if display_name and not self.operator_id.text():
            self.operator_id.setText(display_name)
        self.os_session_button.setVisible(available)
        if fixture:
            self.open_button.setText("Open workspace →")
            self.login_feedback.setText("Developer UI fixture · no backend authentication is provided by this view.")
        self._update_login_actions()

    def set_login_busy(self, busy):
        self._login_busy = bool(busy)
        self.operator_id.setEnabled(not busy)
        self.password.setEnabled(not busy)
        self.show_password.setEnabled(not busy)
        self._update_login_actions()

    def login_succeeded(self):
        self._login_verified = True
        self.login_feedback.setText("Windows identity confirmed. Backend permissions remain enforced.")
        self._open()

    def login_failed(self, message):
        self._login_verified = False
        self.login_feedback.setText(message)

    def reset_login(self):
        self._login_verified = False
        self.password.clear()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_password.setText("Show")
        self._update_login_actions()

    def set_effect_connected(self, connected):
        self.effect_status.setText("eva operator connected" if connected else "")
        self.effect_status.setVisible(bool(connected))

    def set_effect_mode(self, enabled):
        self.effect_footer.setVisible(bool(enabled))

    def _toggle_password(self):
        visible = self.password.echoMode() == QLineEdit.EchoMode.Password
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self.show_password.setText("Hide" if visible else "Show")

    def _update_login_actions(self, *_args):
        # Cosmetic trigger never enters the authenticated workspace.
        trigger = (self.operator_id.text().strip().casefold() == SEQUENCE_LOGIN
                   and self.password.text() == SEQUENCE_PASSWORD)
        chosen = self.selected_source is not None
        credentials = bool(self.operator_id.text().strip() and self.password.text())
        self.open_button.setEnabled(not self._login_busy and (trigger or chosen and (
            self._fixture_entry or self._login_verified or self._login_available and credentials)))
        self.os_session_button.setEnabled(not self._login_busy and chosen and self._login_available)
        self.open_button.setToolTip("Sign in with backend-verified Windows credentials." if chosen
                                   else "Select a data source first.")

    def boundary_values(self) -> list[tuple[str, str]]:
        return [(label, row.value_label.full_text) for label, row in self.boundary_rows.items()]

    def save_state(self) -> dict:
        return {"selected_source": self.selected_source}

    def restore_state(self, state: dict) -> None:
        if state.get("selected_source") in self.source_rows:
            self.select_source(state["selected_source"])

    # ---------------------------------------------------------------- data
    def refresh(self, snap: Snapshot):
        self.operator.setText(snap.operator or "—")
        identity = snap.security or {}
        if identity.get("subject") and identity.get("source") != "anonymous":
            roles = ", ".join(identity.get("roles") or ())
            self.identity_note.setText(f"Authenticated by {identity.get('source', 'backend')} · {roles}\n"
                                       f"{identity['subject']}\nPhysical qualification remains pending.")
            self.audit.setText(AUDIT_SENTENCE)
        else:
            self.identity_note.setText("Developer fixture or unbound input viewer: no authenticated backend identity is declared.")
            self.audit.setText("This source does not declare authenticated security evidence. Physical actuation remains locked.")
        self.build.setText(snap.boot.build)
        self.notice.setText(snap.boot.notice)
        self.notice.setVisible(bool(snap.boot.notice))
        checks = [(check.at, check.body, check.status, check.tone) for check in snap.boot.checks]
        if checks != self._checks:
            self._checks = checks
            clear_layout(self.check_lay)
            for at, body, status, tone_name in checks:
                row = Frame(border=C.soft, sides="b")
                layout = hbox(row, (0, 9, 0, 9), 16)
                stamp = L(at, mono(12.5), C.ink3)
                stamp.setFixedWidth(76)
                layout.addWidget(stamp)
                text_label = L(body, text(13.5), C.ink, elide=True)
                text_label.setMinimumWidth(0)
                text_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                layout.addWidget(text_label, 1)
                layout.addWidget(L(status, mono(12), text_tone(tone_name)))
                self.check_lay.addWidget(row)
        self.active_source.set_value(f"{snap.capabilities.adapter_id} · {snap.capabilities.mode}".upper())
        for label, value, tone_name in execution_boundary(snap):
            self.boundary_rows[label].set_value(value, tone_name)


__all__ = ["AUDIT_SENTENCE", "BootScreen", "OPERATOR_NOTE", "SOURCE_OPTIONS", "SPONSORSHIP", "SourceOption",
           "execution_boundary"]
