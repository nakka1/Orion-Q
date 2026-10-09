"""Workspace primitives: the prototype's `.lbl`, `.btn`, `.chip`, `.seg`, `.card`,
`.kv`, `.rowbtn`, `.track` and `.inp` as composable Qt widgets.

Colours are read from `theme.C` when a widget is built. The shell rebuilds its
widgets on a theme change, so a primitive never has to restyle itself live.
"""
from __future__ import annotations

from itertools import count
from .button_help import button_description

from PySide6.QtCore import QEvent, QPointF, QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QPainter, QPalette, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy,
    QStyle, QStyleOption, QStylePainter, QToolTip, QVBoxLayout, QWidget,
)

from ..theme import (
    C, Tok, button_font, faded, label_font, mix, mono, qc, resolve, text as text_font, text_of,
    text_tone, tone,
)


# ------------------------------------------------------------------- layout
def vbox(parent=None, m=(0, 0, 0, 0), s=0) -> QVBoxLayout:
    lay = QVBoxLayout(parent) if parent is not None else QVBoxLayout()
    lay.setContentsMargins(*m)
    lay.setSpacing(s)
    return lay


def hbox(parent=None, m=(0, 0, 0, 0), s=0) -> QHBoxLayout:
    lay = QHBoxLayout(parent) if parent is not None else QHBoxLayout()
    lay.setContentsMargins(*m)
    lay.setSpacing(s)
    return lay


def spacer(w: int = 0, h: int = 0) -> QWidget:
    item = QWidget()
    if w:
        item.setFixedWidth(w)
    if h:
        item.setFixedHeight(h)
    if not w and not h:
        item.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    return item


def clear_layout(layout) -> None:
    """Remove and delete every widget of a layout (used by list rebuilds)."""

    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()


_frame_ids = count(1)


def scoped_style(widget: QWidget, rules: str) -> None:
    """Style one widget only.

    A type selector (`Frame{…}`) also matches every nested widget of that type,
    so a parent's border or translucent ground would repeat on its children.
    """

    ident = widget.property("orionFrame")
    if ident is None:
        ident = next(_frame_ids)
        widget.setProperty("orionFrame", ident)
    widget.setStyleSheet(f'*[orionFrame="{ident}"]{{{rules}}}')
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


class Frame(QWidget):
    """A panel: background, optional 1px borders on chosen sides."""

    def __init__(self, bg: str | None = None, border: str | None = None,
                 sides: str = "trbl", parent=None) -> None:
        super().__init__(parent)
        rules = []
        if bg:
            rules.append(f"background: {bg}")
        if border:
            names = {"t": "top", "r": "right", "b": "bottom", "l": "left"}
            for key in sides:
                rules.append(f"border-{names[key]}: 1px solid {border}")
        scoped_style(self, ";".join(rules))


def rule(color: str | None = None, height: int = 1) -> QWidget:
    line = QWidget()
    line.setFixedHeight(height)
    line.setStyleSheet(f"background: {color or C.soft};")
    return line


def vrule(height: int = 20, color: str | None = None) -> QWidget:
    """The 1px vertical divider between toolbar groups."""

    line = QWidget()
    line.setFixedSize(1, height)
    line.setStyleSheet(f"background: {color or C.line};")
    return line


# --------------------------------------------------------------------- type
class ElidedLabel(QLabel):
    """Truncate at paint time; resizing must not change layout text or hints."""

    def __init__(self, value):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(value)

    def setText(self, value):
        self.full_text = value
        self.setToolTip(value)
        self.setAccessibleName(value)
        if QLabel.text(self) != value:
            QLabel.setText(self, value)

    def displayed_text(self) -> str:
        """Return the text drawn at the current width, without changing hints."""
        return self.fontMetrics().elidedText(
            self.full_text, Qt.TextElideMode.ElideRight,
            max(0, self.contentsRect().width()))

    def paintEvent(self, event):
        # Changing QLabel.text from resizeEvent can make a layout alternate by
        # one pixel indefinitely. The full text stays stable; only pixels elide.
        painter = QStylePainter(self)
        option = QStyleOption()
        option.initFrom(self)
        painter.drawPrimitive(QStyle.PrimitiveElement.PE_Widget, option)
        painter.setFont(self.font())
        shown = self.displayed_text()
        painter.drawItemText(self.contentsRect(), self.alignment(), self.palette(),
                             self.isEnabled(), shown, QPalette.ColorRole.WindowText)


class WrappingLabel(QLabel):
    """Full plain-text observations, including long identifiers without spaces.

    QLabel's plain word wrap keeps an unbroken identifier on one line. Use Qt's
    text layout with an explicit fallback at character boundaries, sharing the
    exact layout between height calculation and painting. At most eight widths live
    per field; telemetry histories and scientific events are never cached here.
    """

    geometry_updated = Signal()

    def __init__(self, value):
        super().__init__()
        self._text_layouts = {}
        self._layout_font = self.font()
        self._font_height = self.fontMetrics().height()
        # Fixed measured heights avoid Qt repeatedly asking Python heightForWidth
        # for every off-screen field whenever any telemetry label changes.
        self.setWordWrap(False)
        self.setTextFormat(Qt.TextFormat.PlainText)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setSizePolicy(policy)
        self.setMinimumWidth(0)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setText(value)

    def setText(self, value):  # noqa: N802
        previous = getattr(self, 'full_text', None)
        self.full_text = str(value)
        if previous != self.full_text:
            self.setToolTip(self.full_text)
            self.setAccessibleName(self.full_text)
            self._text_layouts.clear()
            # This widget paints its own plain layout. QLabel.setText would
            # invalidate parent layout even when the measured height is equal.
            self._fit_text()
            self.update()

    def text(self):
        return self.full_text

    def _fit_text(self):
        height = self._text_layout(self.contentsRect().width())[1]
        if self.height() != height:
            self.setFixedHeight(height)
            self.geometry_updated.emit()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._fit_text()

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if hasattr(self, '_text_layouts') and event.type() == QEvent.Type.FontChange:
            self._layout_font = self.font()
            self._font_height = self.fontMetrics().height()
            self._text_layouts.clear()
            self._fit_text()

    def setAlignment(self, alignment):  # noqa: N802
        super().setAlignment(alignment)
        if hasattr(self, '_text_layouts'):
            self._text_layouts.clear()
            self._fit_text()

    def _text_layout(self, width):
        width = max(1, int(width))
        key = width
        if key not in self._text_layouts:
            # QTextLayout handles paragraph separators, while retaining raw
            # received newlines in text(), the clipboard and accessibility.
            layout = QTextLayout(self.full_text.replace('\n', '\u2028'), self._layout_font)
            layout.setCacheEnabled(True)
            option = QTextOption()
            option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            option.setAlignment(self.alignment() & (Qt.AlignmentFlag.AlignLeft |
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignHCenter))
            layout.setTextOption(option)
            layout.beginLayout()
            height = 0.
            while True:
                line = layout.createLine()
                if not line.isValid():break
                line.setLineWidth(width)
                line.setPosition(QPointF(0., height))
                height += line.height()
            layout.endLayout()
            if len(self._text_layouts) >= 8:self._text_layouts.clear()
            self._text_layouts[key] = (layout, max(self._font_height, int(height+.999)))
        return self._text_layouts[key]

    def heightForWidth(self, width):  # noqa: N802
        return self._text_layout(width)[1]

    def minimumSizeHint(self):  # noqa: N802
        return QSize(0, self.height())

    def sizeHint(self):  # noqa: N802
        return QSize(240, self.height())

    def paintEvent(self, event):  # noqa: N802
        painter = QStylePainter(self)
        option = QStyleOption(); option.initFrom(self)
        painter.drawPrimitive(QStyle.PrimitiveElement.PE_Widget, option)
        rect = self.contentsRect()
        layout, height = self._text_layout(rect.width())
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        layout.draw(painter, QPointF(rect.left(), rect.top()+max(0., (rect.height()-height)/2)))

    def contextMenuEvent(self, event):  # noqa: N802
        menu = QMenu(self)
        action = menu.addAction('Copiar campo completo')
        action.triggered.connect(lambda: QApplication.clipboard().setText(self.full_text))
        menu.exec(event.globalPos())
        menu.deleteLater()

    def keyPressEvent(self, event):  # noqa: N802
        if event.matches(QKeySequence.StandardKey.Copy):
            QApplication.clipboard().setText(self.full_text)
            event.accept(); return
        super().keyPressEvent(event)


def L(value: str, font=None, color: str = C.ink, align=Qt.AlignmentFlag.AlignLeft,
      *, elide: bool = False) -> QLabel:
    label = ElidedLabel(value) if elide else QLabel(value)
    label.setFont(font or mono(12))
    label.setStyleSheet(f"color: {text_of(color)}; background: transparent;")
    label.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    return label


def Prose(value: str, size: float = 13, color: str = C.muted, width: int | None = None) -> QLabel:
    label = QLabel(value)
    label.setFont(text_font(size))
    label.setStyleSheet(f"color: {text_of(color)}; background: transparent;")
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    if width:
        label.setFixedWidth(width)
    return label


def Label(value: str, color: str | None = None, *, wrap: bool = False) -> QLabel:
    """`.lbl` — 11 px mono, uppercase, .08em tracking, `ink3` unless told otherwise."""

    label = L(value, label_font(11), color if color is not None else C.ink3)
    label.setWordWrap(wrap)
    return label


def Section(value: str, color: str | None = None) -> QLabel:
    """Panel heading. Same type as `Label`, wrapping enabled."""

    return Label(value, color, wrap=True)


def Title(value: str, size: float = 22) -> QLabel:
    """Screen heading (`h2`): Inter 22 px, medium."""

    return L(value, text_font(size, 500), C.ink)


def set_color(label: QWidget, color: str) -> None:
    """Change a label's colour without re-installing an identical style."""

    style = f"color: {text_of(color)}; background: transparent;"
    if label.styleSheet() != style:
        label.setStyleSheet(style)


# ----------------------------------------------------------------- controls
_KEYBOARD_REASONS = (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                     Qt.FocusReason.ShortcutFocusReason)


class Clickable(QPushButton):
    """Button base: pointer cursor, Enter activates, accent ring on keyboard focus."""

    def __init__(self, value: str = "", parent=None) -> None:
        super().__init__(value, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoDefault(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._focus_visible = False
        self.setToolTip(button_description(value))
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)

    def setText(self, value):  # noqa: N802
        automatic = not self.toolTip() or self.toolTip() == button_description(self.text())
        super().setText(value)
        if automatic:
            self.setToolTip(button_description(value))

    def setToolTip(self, text):  # noqa: N802
        super().setToolTip(text or button_description(self.text()))

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip and self.toolTip():
            QToolTip.showText(event.globalPos(), self.toolTip(), self)
            event.accept()
            return True
        return super().event(event)

    def focusInEvent(self, event) -> None:
        self._focus_visible = event.reason() in _KEYBOARD_REASONS
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self._focus_visible = False
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:
        if not self._focus_visible:
            self._focus_visible = True
            self.update()
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.isEnabled() and not event.isAutoRepeat():
                self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._focus_visible and self.hasFocus():
            p = QPainter(self)
            p.setPen(QPen(qc(C.acc), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self.rect().adjusted(1, 1, -1, -1))
            p.end()


class Seg(Clickable):
    """`.seg` — text tab; the active one carries a 2 px accent underline."""

    def __init__(self, value: str, parent=None, *, size: float = 11.5) -> None:
        super().__init__(value, parent)
        self.setFont(button_font(size))
        self.setMinimumHeight(28)
        self._active: bool | None = None
        self.set_active(False)

    @property
    def active(self) -> bool:
        return bool(self._active)

    def set_active(self, active: bool) -> None:
        if self._active == active:
            return
        self._active = active
        fg = C.ink if active else C.ink3
        bd = C.acc if active else "transparent"
        self.setStyleSheet(
            f"QPushButton{{color:{fg};background:transparent;border:0;"
            f"border-bottom:2px solid {bd};padding:6px 2px;text-align:center;}}"
            f"QPushButton:hover:enabled{{color:{C.ink};}}"
            f"QPushButton:disabled{{color:{faded(fg)};border-bottom-color:{faded(bd) if active else 'transparent'};}}"
        )

    set_on = set_active


class Tab(Seg):
    """Kept for older imports; identical to `Seg`."""


class Toggle(Seg):
    """View / filter selector (GRAPH · GEO · MATRIX). A `Seg` with a fixed height."""

    def __init__(self, value: str, parent=None) -> None:
        super().__init__(value, parent)
        self.setFixedHeight(28)


class SegBar(QWidget):
    """A row of `Seg` buttons with one active key."""

    selected = Signal(str)

    def __init__(self, options, current: str | None = None, parent=None, *, gap: int = 14) -> None:
        super().__init__(parent)
        lay = hbox(self, (0, 0, 0, 0), gap)
        self.buttons: dict[str, Seg] = {}
        for key, title in options:
            button = Seg(title)
            button.clicked.connect(lambda checked=False, k=key: self.selected.emit(k))
            lay.addWidget(button)
            self.buttons[key] = button
        if current is not None:
            self.set_current(current)

    def set_current(self, key: str) -> None:
        for name, button in self.buttons.items():
            button.set_active(name == key)

    def current(self) -> str | None:
        return next((name for name, button in self.buttons.items() if button.active), None)


_OUTLINE_TOKENS = {"ink", "ink2", "ink3", "ink4", "line", "soft"}


class Btn(Clickable):
    """`.btn` — 11.5 px mono uppercase, 1 px border, 34 px tall.

    `variant`: "default" (line border, ink text), "accent" (`.btn-acc`),
    "solid" (`.btn-solid`) or "tone" (outlined in `color`). Without `variant`
    the legacy arguments decide: the accent colour gives "accent", a neutral
    colour gives "default", `filled=True` gives "solid" (or a `color` fill).
    """

    def __init__(self, value: str, color: str = C.amber, filled: bool = False,
                 height: int = 31, parent=None, *, variant: str | None = None) -> None:
        super().__init__(value, parent)
        self.setFont(button_font(11.5))
        self.setFixedHeight(max(34, height))
        self._color = color
        self._filled = filled
        self._variant = variant
        self._apply_style()

    @property
    def variant(self) -> str:
        if self._variant:
            return self._variant
        name = self._color.token if isinstance(self._color, Tok) else None
        if self._filled:
            return "solid" if name in {"acc", "ink"} else "fill"
        if name == "acc":
            return "accent"
        return "default" if name in _OUTLINE_TOKENS else "tone"

    def set_variant(self, variant: str) -> None:
        if variant != self._variant:
            self._variant = variant
            self._apply_style()

    def _apply_style(self) -> None:
        variant = self.variant
        if variant in {"solid", "fill"}:
            ground = C.ink if variant == "solid" else resolve(self._color)
            style = (f"QPushButton{{background:{ground};color:{C.bg};border:1px solid {ground};padding:0 12px;}}"
                     f"QPushButton:hover:enabled{{background:{mix(ground, None, 0.88)};}}"
                     f"QPushButton:disabled{{background:{faded(ground)};border-color:{faded(ground)};}}")
        else:
            if variant == "accent":
                fg, bd = C.acc_t, C.acc
            elif variant == "tone":
                fg, bd = text_of(self._color), resolve(self._color)
            else:
                fg, bd = C.ink, C.line
            style = (f"QPushButton{{color:{fg};border:1px solid {bd};background:transparent;padding:0 12px;}}"
                     f"QPushButton:hover:enabled{{background:{C.hover};}}"
                     f"QPushButton:pressed:enabled{{background:{C.sel};}}"
                     f"QPushButton:disabled{{color:{faded(fg)};border-color:{faded(bd)};}}")
        if style != self.styleSheet():
            self.setStyleSheet(style)


class Chip(QLabel):
    """`.chip` — small bordered status tag. `tone` is a text tone (ok, warn, …)."""

    def __init__(self, value: str = "", tone_name: str = "mute", *, outline: bool = False,
                 size: float = 11, parent=None) -> None:
        super().__init__(value, parent)
        self.setFont(button_font(size))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._tone, self._outline = None, outline
        self.set_tone(tone_name)

    def set_tone(self, tone_name: str, outline: bool | None = None) -> None:
        if outline is not None:
            self._outline = outline
        self._tone = tone_name
        border = tone(tone_name) if self._outline else C.line
        style = (f"color:{text_tone(tone_name)};border:1px solid {border};"
                 f"padding:2px 8px;background:transparent;")
        if style != self.styleSheet():
            self.setStyleSheet(style)


class Card(Frame):
    """`.card` — raised ground, 1 px soft border, 16 px padding, square corners."""

    def __init__(self, parent=None, *, tone_name: str | None = None, padding: int = 16,
                 gap: int = 12, bg: str | None = None) -> None:
        QWidget.__init__(self, parent)
        rules = [f"background:{bg or C.raised}", f"border:1px solid {C.soft}"]
        if tone_name:
            rules.append(f"border-left:3px solid {tone(tone_name)}")
        scoped_style(self, ";".join(rules))
        self.lay = vbox(self, (padding, padding, padding, padding), gap)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.lay.addWidget(widget, stretch)
        return widget


class KVRow(QWidget):
    """`.kv` — key left in `ink2`, value right, 1 px soft rule underneath."""

    def __init__(self, key: str, value: str = "—", *, tone_name: str = "ink", mono_value: bool = True,
                 size: float = 13, border: bool = True, wrap: bool = False, parent=None) -> None:
        super().__init__(parent)
        scoped_style(self, f"border-bottom:1px solid {C.soft};" if border else "")
        lay = hbox(self, (0, 6, 0, 6), 12)
        self._wrap = wrap
        if wrap:
            self.key_label, self.value_label = WrappingLabel(key), WrappingLabel(value)
            self.key_label.setFont(text_font(size))
            self.value_label.setFont(mono(size-.5) if mono_value else text_font(size))
            set_color(self.key_label, C.ink2);set_color(self.value_label, text_tone(tone_name))
            self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        else:
            self.key_label = L(key, text_font(size), C.ink2)
            self.value_label = L(value, mono(size - 0.5) if mono_value else text_font(size),
                                 text_tone(tone_name), Qt.AlignmentFlag.AlignRight, elide=True)
        self.value_label.setMinimumWidth(0)
        if not wrap:self.value_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.key_label, 2 if wrap else 0)
        lay.addWidget(self.value_label, 1)
        if wrap:
            lay.setStretch(1, 3)
            policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.setSizePolicy(policy)
            # Resize events can arrive inside the parent's active QHBoxLayout.
            # Refit after that pass finishes, avoiding positions from old heights.
            self.key_label.geometry_updated.connect(self._fit_wrapped_row, Qt.ConnectionType.QueuedConnection)
            self.value_label.geometry_updated.connect(self._fit_wrapped_row, Qt.ConnectionType.QueuedConnection)
            self._fit_wrapped_row()

    def _fit_wrapped_row(self):
        height = max(self.key_label.height(), self.value_label.height())+12
        if self.height() != height:
            self.setFixedHeight(height)
            self.layout().invalidate()
            self.layout().activate()

    def set_value(self, value: str, tone_name: str | None = None) -> None:
        if self.value_label.full_text != value:
            self.value_label.setText(value)
        if tone_name is not None:
            set_color(self.value_label, text_tone(tone_name))


class RowBtn(Clickable):
    """`.rowbtn` — a full-width selectable row; children go in `.lay`."""

    def __init__(self, parent=None, *, padding=(10, 8, 10, 8), gap: int = 10, boxed: bool = False) -> None:
        super().__init__("", parent)
        self._boxed = boxed
        self._on: bool | None = None
        self.lay = hbox(self, padding, gap)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.set_on(False)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.lay.addWidget(widget, stretch)
        self.updateGeometry()
        return widget

    @property
    def on(self) -> bool:
        return bool(self._on)

    def set_on(self, on: bool) -> None:
        if self._on == on:
            return
        self._on = on
        border = f"border:1px solid {C.soft};" if self._boxed else f"border:0;border-bottom:1px solid {C.soft};"
        self.setStyleSheet(
            f"QPushButton{{{border}background:{C.sel if on else 'transparent'};text-align:left;}}"
            f"QPushButton:hover:enabled{{background:{C.sel if on else C.hover};}}")

    def sizeHint(self) -> QSize:
        return self.lay.sizeHint()

    def minimumSizeHint(self) -> QSize:
        return self.lay.minimumSize()


class Input(QLineEdit):
    """`.inp` — page-ground field with a 1 px line border; accent border on focus."""

    def __init__(self, placeholder: str = "", parent=None, *, mono_font: bool = False) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setFont(mono(13) if mono_font else text_font(13))
        self.setMinimumHeight(34)


# ------------------------------------------------------------------ figures
class PixelMark(QWidget):
    """The ORION-Q pixel mark (10 × 5 grid), filled with the accent."""

    RECTS = ((4, 0, 2, 1), (3, 1, 1, 1), (6, 1, 1, 1), (0, 2, 2, 3), (4, 2, 2, 3),
             (8, 2, 2, 3), (2, 3, 2, 1), (6, 3, 2, 1))

    def __init__(self, width: int = 30, color: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.unit = max(1, round(width / 10))
        self.color = color
        self.setFixedSize(self.unit * 10, self.unit * 5)
        self.setAccessibleName("ORION-Q pixel mark")

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        colour = qc(self.color if self.color is not None else C.acc)
        u = self.unit
        for x, y, w, h in self.RECTS:
            p.fillRect(x * u, y * u, w * u, h * u, colour)
        p.end()


class Hatch(QWidget):
    """45° repeating stripe band — the warning / claim-check marker."""

    def __init__(self, color: str = C.amber, thickness: int = 12,
                 vertical: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.color = color
        self.vertical = vertical
        if vertical:
            self.setFixedWidth(thickness)
        else:
            self.setFixedHeight(thickness)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qc(C.bg))
        pen = QPen(qc(self.color))
        pen.setWidth(7)
        p.setPen(pen)
        step = 14
        w, h = self.width(), self.height()
        x = -h
        while x < w + h:
            p.drawLine(x, h, x + h, 0)
            x += step
        p.end()


class Bar(QWidget):
    """`.track` + `.fill`, with the optional green reference `.tick`."""

    def __init__(self, fraction: float = 0.0, color: str = C.amber,
                 tick: float | None = None, height: int = 6, parent=None) -> None:
        super().__init__(parent)
        self.fraction = fraction
        self.color = color
        self.tick = tick
        self.setFixedHeight(height + 6)
        self.setMinimumWidth(20)

    def set_fraction(self, fraction: float, color: str | None = None) -> None:
        self.fraction = max(0.0, min(1.0, fraction))
        if color:
            self.color = color
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        top = 3
        h = self.height() - 6
        w = self.width()
        p.fillRect(0, top, w, h, qc(C.soft))
        fill = int(w * max(0.0, min(1.0, self.fraction)))
        if fill > 0:
            p.fillRect(0, top, fill, h, qc(self.color))
        if self.tick is not None:
            x = min(w - 2, int(w * self.tick))
            p.fillRect(x, 0, 2, self.height(), qc(C.green))
        p.end()


class Track(Bar):
    """`Bar` addressed by tone name (`amber`, `violet`, `red`, …)."""

    def __init__(self, fraction: float = 0.0, tone_name: str = "amber",
                 tick: float | None = None, height: int = 6, parent=None) -> None:
        super().__init__(fraction, tone(tone_name), tick, height, parent)

    def set_tone(self, tone_name: str) -> None:
        self.color = tone(tone_name)
        self.update()


class Cells(QWidget):
    """The key-pool and memory-register block grids."""

    def __init__(self, tones: list[str], columns: int = 48, cell_h: int = 14,
                 gap: int = 3, parent=None) -> None:
        super().__init__(parent)
        self.tones = tones
        self.columns = columns
        self.cell_h = cell_h
        self.gap = gap
        self._resize()

    def set_tones(self, tones: list[str]) -> None:
        self.tones = tones
        self._resize()
        self.update()

    def _resize(self) -> None:
        rows = max(1, -(-len(self.tones) // self.columns))
        self.setFixedHeight(rows * self.cell_h + (rows - 1) * self.gap)

    def paintEvent(self, _event) -> None:
        if not self.tones:
            return
        p = QPainter(self)
        span = (self.width() + self.gap) / self.columns
        cw = max(1.0, span - self.gap)
        for i, name in enumerate(self.tones):
            col, row = i % self.columns, i // self.columns
            x = col * span
            y = row * (self.cell_h + self.gap)
            colour = C.soft if name in ("empty", "idle", "dim") else tone(name)
            p.fillRect(int(x), y, max(1, int(cw)), self.cell_h, qc(colour))
        p.end()


class Dots(QWidget):
    """Scanline / graph-paper backgrounds painted behind a screen."""

    def __init__(self, mode: str = "scan", parent=None) -> None:
        super().__init__(parent)
        self.mode = mode
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setPen(QPen(qc(C.soft), 1))
        if self.mode == "scan":
            for y in range(0, self.height(), 4):
                p.drawLine(0, y, self.width(), y)
        else:
            for y in range(0, self.height(), 48):
                p.drawLine(0, y, self.width(), y)
            for x in range(0, self.width(), 48):
                p.drawLine(x, 0, x, self.height())
        p.end()


# -------------------------------------------------------------------- rows
def kv(left: str, right: str, left_color: str = C.muted, right_color: str = C.ink,
       pad: int = 6, border: bool = True, font=None) -> QWidget:
    row = Frame(border=C.soft if border else None, sides="b")
    lay = hbox(row, (0, pad, 0, pad), 0)
    lay.addWidget(L(left, font or mono(12), left_color))
    lay.addSpacing(12)
    reading = L(right, font or mono(12), right_color, Qt.AlignmentFlag.AlignRight, elide=True)
    reading.setMinimumWidth(0)
    reading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    lay.addWidget(reading, 1)
    return row


def columns(values: list[tuple[str, float, str]], pad: int = 8, gap: int = 6,
            border: str | None = C.line2, bg: str | None = None, font=None) -> QWidget:
    """A table row: (text, stretch, colour) per column."""

    row = Frame(bg=bg, border=border, sides="b") if (border or bg) else QWidget()
    lay = hbox(row, (0, pad, 0, pad), gap)
    for value, stretch, colour in values:
        label = L(value, font or mono(12), colour, elide=True)
        # Long campaign IDs must not move values into another column.
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setToolTip(value)
        lay.addWidget(label, int(stretch * 100))
    return row


def scroll_panel(content: QWidget, minimum_height: int = 80) -> QScrollArea:
    if content.layout():
        content.layout().setAlignment(Qt.AlignmentFlag.AlignTop)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setMinimumHeight(minimum_height)
    area.setWidget(content)
    return area


def grid(rows: int = 0) -> QGridLayout:
    lay = QGridLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setHorizontalSpacing(0)
    lay.setVerticalSpacing(0)
    return lay


class FrameBox(QFrame):
    """Bordered box with padding — the metric cards and log panels."""

    def __init__(self, border: str = C.line, bg: str | None = None,
                 padding=(12, 14, 12, 14), parent=None) -> None:
        super().__init__(parent)
        rules = [f"border: 1px solid {border}"]
        if bg:
            rules.append(f"background: {bg}")
        self.setStyleSheet("FrameBox{" + ";".join(rules) + "}")
        self.lay = vbox(self, (padding[1], padding[0], padding[3], padding[2]), 0)


__all__ = [
    "Bar", "Btn", "Card", "Cells", "Chip", "Clickable", "Dots", "ElidedLabel", "Frame", "FrameBox",
    "Hatch", "Input", "KVRow", "L", "Label", "PixelMark", "Prose", "RowBtn", "Section", "Seg", "SegBar",
    "Tab", "Title", "Toggle", "Track", "clear_layout", "columns", "grid", "hbox", "kv", "rule",
    "scoped_style", "scroll_panel", "set_color", "spacer", "vbox", "vrule",
]
