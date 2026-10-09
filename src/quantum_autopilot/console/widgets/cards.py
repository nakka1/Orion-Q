"""Page scaffolding shared by the Telemetry, Keys, Nodes, Edge control and Audit screens.

The approved prototype lays every one of these screens out as a scrolling column
of `.card` rows on a three-column grid (16 px gaps, 20 × 24 px page padding).
This module holds that scaffolding and the formatting of live backend numbers.

Formatting only changes how a backend number is written (unit prefix, number of
digits). A value the backend did not produce is written "—"; nothing is filled in.
"""
from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QLayout, QScrollArea, QSizePolicy, QWidget

from ..theme import C, button_font, mono, text as text_font, text_tone, tone
from .prims import (
    Card, Chip, Clickable, KVRow, L, Label, Prose, Seg, Track, clear_layout, hbox, scoped_style, set_color,
    vbox,
)

DASH = "—"
NOT_SUPPLIED = "Not supplied by this source"

PROVENANCE_TONE = {"measured": "ok", "simulated": "sat", "derived": "warn", "assumed": "warn", "runtime": "mute"}
STATUS_CHIP_TONE = {"green": "ok", "amber": "warn", "red": "bad", "violet": "sat", "dim": "mute", "muted": "mute",
                    "ink": "ink"}


# --------------------------------------------------------------- formatting
def fmt_fidelity(value: float | None) -> str:
    return DASH if value is None else f"{value:.4f}"


def fmt_qber(value: float | None) -> str:
    return DASH if value is None else f"{value * 100:.2f} %"


def fmt_rate(value: float | None, unit: str = "bit/s") -> str:
    """A rate with an SI prefix; the number itself is the backend's."""

    if value is None:
        return DASH
    if abs(value) >= 1e9:
        return f"{value / 1e9:.3f} G{unit}"
    if abs(value) >= 1e6:
        return f"{value / 1e6:.3f} M{unit}"
    if abs(value) >= 1e3:
        return f"{value / 1e3:.2f} k{unit}"
    return f"{value:.1f} {unit}"


def fmt_bits(value: float | None) -> str:
    return fmt_rate(value, "bit")


def fmt_number(value: float | None, spec: str = ".3f", unit: str = "") -> str:
    if value is None:
        return DASH
    return f"{value:{spec}}{' ' + unit if unit else ''}"


def fmt_fraction(value: float | None) -> str:
    return DASH if value is None else f"{value:.4f}"


def fmt_percent(value: float | None, digits: int = 1) -> str:
    return DASH if value is None else f"{value * 100:.{digits}f} %"


def fmt_flag(value: bool | None, yes: str = "yes", no: str = "no") -> str:
    return DASH if value is None else yes if value else no


def clock_of(iso: str) -> str:
    """HH:MM:SS of an ISO-8601 timestamp (the backend's clock, not the UI's)."""

    return iso[11:19] if len(iso) >= 19 else (iso or DASH)


def unit_fraction(value: float | None) -> float:
    """Bar length for a quantity the backend defines on 0..1."""

    return 0.0 if value is None else max(0.0, min(1.0, float(value)))


# ----------------------------------------------------------------- scaffold
class Page(QScrollArea):
    """The scrolling page body: a column of rows with the prototype's padding."""

    def __init__(self, parent=None, *, margins=(24, 20, 24, 20), gap: int = 16) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.col = vbox(self.body, margins, gap)
        # A trailing stretch keeps the rows at the top. (Aligning the layout itself would lay the
        # rows out in its size hint and ignore the height wrapped text needs at the real width.)
        self.col.addStretch(1)
        self.setWidget(self.body)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.col.insertWidget(self.col.count() - 1, widget, stretch)
        return widget


def grid_row(items: Sequence, columns: int = 3, gap: int = 16) -> QWidget:
    """A row of the card grid. `items` are widgets or (widget, column span) pairs."""

    from .adaptive_layout import AdaptiveGrid
    return AdaptiveGrid(items, columns=columns, gap=gap)


def stack_column(widgets: Sequence[QWidget], gap: int = 16) -> QWidget:
    column = QWidget()
    lay = vbox(column, (0, 0, 0, 0), gap)
    for widget in widgets:
        lay.addWidget(widget)
    lay.addStretch(1)
    return column


def head_row(*widgets, gap: int = 14) -> QWidget:
    """Card heading line: the first widget takes the free width."""

    from .adaptive_layout import AdaptiveRow
    return AdaptiveRow(widgets, gap=gap)


def shrinkable(widget: QWidget) -> QWidget:
    """Let a widget be narrower than its text, keeping its height-for-width flag."""

    widget.setMinimumWidth(0)
    policy = widget.sizePolicy()
    policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
    widget.setSizePolicy(policy)
    return widget


def note(value: str = "", size: float = 12.5, color=None) -> Prose:
    """Wrapped body text (`<p>` of a card)."""

    return shrinkable(Prose(value, size, color if color is not None else C.ink2))


def mono_header(table) -> None:
    """`.tbl th`: 11 px mono label headers for a record table."""

    table.setStyleSheet(table.styleSheet() + (
        f"QHeaderView::section{{font-family:'{mono(11).family()}';font-size:11px;}}"))


class FlowLayout(QLayout):
    """Left-to-right layout that wraps onto further lines (`flex-wrap: wrap`)."""

    def __init__(self, parent=None, *, gap: int = 8) -> None:
        super().__init__(parent)
        self._items: list = []
        self._gap = gap
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802 - Qt naming
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 - Qt naming
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802 - Qt naming
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802 - Qt naming
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt naming
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt naming
        return self._arrange(QRect(0, 0, width, 0), False)

    def setGeometry(self, rect) -> None:  # noqa: N802 - Qt naming
        super().setGeometry(rect)
        self._arrange(rect, True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt naming
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect, apply: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            hint = item.sizeHint()
            if rect.width() > 0 and hint.width() > rect.width():
                hint = QSize(rect.width(), hint.height())      # an item is never wider than its row
            if x > rect.x() and x + hint.width() > rect.right() + 1:
                x, y, line = rect.x(), y + line + self._gap, 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._gap
            line = max(line, hint.height())
        return y + line - rect.y()


class TwoLineList(QWidget):
    """Rows of a mono title with a toned tag on the right and a wrapped mono line underneath."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.lay = vbox(self, (0, 0, 0, 0), 0)
        self._parts: list[tuple] = []

    def set_rows(self, rows: Sequence[tuple]) -> None:
        """`rows` are (title, tag, tag tone, body)."""

        rows = list(rows)
        if len(rows) != len(self._parts):
            clear_layout(self.lay)
            self._parts = []
            for index in range(len(rows)):
                row = QWidget()
                if index < len(rows) - 1:
                    scoped_style(row, f"border-bottom:1px solid {C.soft};")
                lay = vbox(row, (0, 6, 0, 6), 2)
                top = QWidget()
                tl = hbox(top, (0, 0, 0, 0), 8)
                title = shrinkable(L("", mono(12.5), C.ink, elide=True))
                tag = L("", mono(11.5), C.ink3)
                tl.addWidget(title, 1)
                tl.addWidget(tag)
                lay.addWidget(top)
                body = shrinkable(Prose("", 12, C.ink2))
                body.setFont(mono(12))
                lay.addWidget(body)
                self.lay.addWidget(row)
                self._parts.append((title, tag, body))
        for (title, tag, body), (name, value, tone_name, text) in zip(self._parts, rows):
            set_text(title, name)
            set_text(tag, value)
            set_color(tag, text_tone(tone_name))
            set_text(body, text)

    def rows(self) -> list[tuple[str, str, str]]:
        return [(title.full_text, tag.text(), body.text()) for title, tag, body in self._parts]


def set_text(label, value: str) -> None:
    """Write a label only when its text changes (keeps polls from repainting)."""

    current = getattr(label, "full_text", None)
    if (current if current is not None else label.text()) != value:
        label.setText(value)


# ------------------------------------------------------------------ widgets
class KVList(QWidget):
    """A list of `.kv` rows that updates in place while its keys stay the same."""

    def __init__(self, parent=None, *, size: float = 13, mono_value: bool = True) -> None:
        super().__init__(parent)
        self.lay = vbox(self, (0, 0, 0, 0), 0)
        self._size, self._mono = size, mono_value
        self._keys: list[str] = []
        self.rows: list[KVRow] = []

    def set_rows(self, rows: Sequence[tuple]) -> None:
        """`rows` are (key, value) or (key, value, tone) tuples."""

        rows = [(row[0], row[1], row[2] if len(row) > 2 else "ink") for row in rows]
        keys = [row[0] for row in rows]
        if keys != self._keys:
            clear_layout(self.lay)
            self._keys = keys
            self.rows = []
            for index, (key, value, tone_name) in enumerate(rows):
                item = KVRow(key, value, tone_name=tone_name, size=self._size, mono_value=self._mono,
                             border=index < len(rows) - 1)
                item.value_label.setToolTip(value)
                self.lay.addWidget(item)
                self.rows.append(item)
            return
        for item, (_key, value, tone_name) in zip(self.rows, rows):
            item.set_value(value, tone_name)

    def values(self) -> dict[str, str]:
        return {key: row.value_label.full_text for key, row in zip(self._keys, self.rows)}


class Meter(QWidget):
    """Label left, mono value right, a `.track` underneath (omitted without a 0..1 scale)."""

    def __init__(self, label: str = "", value: str = DASH, fraction: float | None = None,
                 tone_name: str = "amber", tick: float | None = None, parent=None, *, mono_label: bool = False) -> None:
        super().__init__(parent)
        lay = vbox(self, (0, 0, 0, 0), 6)
        top = QWidget()
        tl = hbox(top, (0, 0, 0, 0), 12)
        self.label = L(label, mono(12.5) if mono_label else text_font(13.5), C.ink, elide=True)
        self.label.setMinimumWidth(0)
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.value = L(value, mono(13), C.ink, Qt.AlignmentFlag.AlignRight)
        tl.addWidget(self.label, 1)
        tl.addWidget(self.value)
        lay.addWidget(top)
        self.track = Track(0.0, tone_name, tick)
        lay.addWidget(self.track)
        self.set(label, value, fraction, tone_name, tick)

    def set(self, label: str, value: str, fraction: float | None, tone_name: str = "amber",
            tick: float | None = None) -> None:
        set_text(self.label, label)
        set_text(self.value, value)
        self.track.setVisible(fraction is not None)
        self.track.tick = tick
        self.track.set_fraction(fraction or 0.0, tone(tone_name))


class MeterList(QWidget):
    def __init__(self, parent=None, *, gap: int = 12, mono_label: bool = False) -> None:
        super().__init__(parent)
        self.lay = vbox(self, (0, 0, 0, 0), gap)
        self._mono = mono_label
        self.meters: list[Meter] = []

    def set_rows(self, rows: Sequence[tuple]) -> None:
        """`rows` are (label, value, fraction | None, tone[, tick])."""

        if len(rows) != len(self.meters):
            clear_layout(self.lay)
            self.meters = [Meter(mono_label=self._mono) for _ in rows]
            for meter in self.meters:
                self.lay.addWidget(meter)
        for meter, row in zip(self.meters, rows):
            meter.set(row[0], row[1], row[2], row[3] if len(row) > 3 else "amber", row[4] if len(row) > 4 else None)


class LogList(QWidget):
    """Rows of time (mono) and wrapped text; rebuilt only when the rows change."""

    def __init__(self, parent=None, *, empty: str = NOT_SUPPLIED, time_width: int = 62) -> None:
        super().__init__(parent)
        self.lay = vbox(self, (0, 0, 0, 0), 0)
        self._rows: list | None = None
        self._empty, self._time_width = empty, time_width
        self.set_rows([])

    def set_empty(self, value: str) -> None:
        if value != self._empty:
            self._empty = value
            if not self._rows:
                self._rows = None
                self.set_rows([])

    def set_rows(self, rows: Sequence[tuple]) -> None:
        """`rows` are (time, text) or (time, text, tone)."""

        rows = [(row[0], row[1], row[2] if len(row) > 2 else "ink") for row in rows]
        if rows == self._rows:
            return
        self._rows = rows
        clear_layout(self.lay)
        if not rows:
            self.lay.addWidget(note(self._empty, 12.5, C.ink3))
            return
        for index, (at, body, tone_name) in enumerate(rows):
            row = QWidget()
            if index < len(rows) - 1:
                scoped_style(row, f"border-bottom:1px solid {C.soft};")
            lay = hbox(row, (0, 6, 0, 6), 10)
            stamp = L(at, mono(12), C.ink3)
            stamp.setFixedWidth(self._time_width)
            lay.addWidget(stamp, 0, Qt.AlignmentFlag.AlignTop)
            lay.addWidget(note(body, 12.5, text_tone(tone_name) if tone_name != "ink" else C.ink), 1)
            self.lay.addWidget(row)

    def rows(self) -> list:
        return list(self._rows or [])


class LinkButton(Clickable):
    """The prototype's underlined `.lbl` button (expand / collapse a note)."""

    def __init__(self, value: str = "", parent=None) -> None:
        super().__init__(value, parent)
        font = button_font(11)
        font.setUnderline(True)
        self.setFont(font)
        self.setStyleSheet(
            f"QPushButton{{color:{C.ink3};background:transparent;border:0;padding:2px 0;text-align:left;}}"
            f"QPushButton:hover{{color:{C.ink};}}")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)


class ToggleChip(Clickable):
    """A `.chip` that is a button: series toggles and feed filters."""

    toggled_on = Signal(bool)

    def __init__(self, value: str = "", tone_name: str = "mute", on: bool = True, parent=None) -> None:
        super().__init__(value, parent)
        self.setFont(button_font(11))
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._tone, self._on = tone_name, None
        self.clicked.connect(lambda: self.set_on(not self.on, emit=True))
        self.set_on(on)

    @property
    def on(self) -> bool:
        return bool(self._on)

    def set_tone(self, tone_name: str) -> None:
        if tone_name != self._tone:
            self._tone = tone_name
            self._style()

    def set_on(self, on: bool, emit: bool = False) -> None:
        if self._on != on:
            self._on = on
            self.setAccessibleDescription("pressed" if on else "not pressed")
            self._style()
        if emit:
            self.toggled_on.emit(on)

    def _style(self) -> None:
        colour = text_tone(self._tone) if self._on else C.ink3
        border = tone(self._tone) if self._on and self._tone not in ("mute", "ink") else C.line
        self.setStyleSheet(
            f"QPushButton{{color:{colour};border:1px solid {border};padding:2px 8px;background:transparent;}}"
            f"QPushButton:hover{{background:{C.hover};}}")


class MetricCard(Card):
    """Headline reading: label + provenance chip, 34 px value, unit, uncertainty, expandable note."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.title = Label("")
        self.title.setWordWrap(True)
        self.chip = Chip("", "mute")
        self.add(head_row(self.title, self.chip, gap=8))
        reading = QWidget()
        rl = hbox(reading, (0, 0, 0, 0), 8)
        self.value = L(DASH, text_font(34), C.ink)
        self.unit = L("", mono(13), C.ink3)
        self.uncertainty = L("", mono(12), C.ink3)
        rl.addWidget(self.value)
        rl.addWidget(self.unit, 0, Qt.AlignmentFlag.AlignBottom)
        rl.addWidget(self.uncertainty, 0, Qt.AlignmentFlag.AlignBottom)
        rl.addStretch(1)
        self.add(reading)
        self.toggle = LinkButton("What this means")
        self.toggle.clicked.connect(lambda: self.set_open(not self.open))
        self.add(self.toggle)
        self.note = note("", 13)
        self.note.setVisible(False)
        self.add(self.note)
        self.lay.addStretch(1)
        self.open = False
        self._has_note = False

    def set_open(self, value: bool) -> None:
        self.open = bool(value)
        self.note.setVisible(self.open and self._has_note)
        set_text(self.toggle, "Hide note" if self.open else "What this means")

    def set_metric(self, label: str, value: str, unit: str = "", uncertainty: str = "", provenance: str = "",
                   body: str = "", tone_name: str = "ink") -> None:
        set_text(self.title, label)
        set_text(self.value, value)
        set_color(self.value, text_tone(tone_name))
        set_text(self.unit, unit)
        set_text(self.uncertainty, uncertainty)
        self.unit.setVisible(bool(unit))
        self.uncertainty.setVisible(bool(uncertainty))
        if self.chip.text() != provenance.upper():
            self.chip.setText(provenance.upper())
            self.chip.set_tone(PROVENANCE_TONE.get(provenance, "mute"))
        self.chip.setVisible(bool(provenance))
        set_text(self.note, body)
        self._has_note = bool(body)
        self.toggle.setVisible(self._has_note)
        self.note.setVisible(self.open and self._has_note)


class LinkPicker(QWidget):
    """Choose one identifier: `.seg` buttons while they fit a row, a list beyond that."""

    picked = Signal(str)
    MAX_SEGMENTS = 12

    def __init__(self, parent=None, *, gap: int = 14) -> None:
        super().__init__(parent)
        self.lay = hbox(self, (0, 0, 0, 0), gap)
        self.ids: list[str] = []
        self.buttons: dict[str, Seg] = {}
        self.combo: QComboBox | None = None
        self._current = ""

    def current(self) -> str:
        return self._current

    def set_ids(self, ids: Sequence[str], current: str = "") -> None:
        ids = list(ids)
        if ids != self.ids:
            self.ids = ids
            clear_layout(self.lay)
            self.buttons, self.combo = {}, None
            if len(ids) > self.MAX_SEGMENTS:
                self.combo = QComboBox()
                self.combo.setFont(mono(12))
                self.combo.addItems(ids)
                self.combo.activated.connect(lambda index: self.picked.emit(self.ids[index]))
                self.lay.addWidget(self.combo)
            else:
                for ident in ids:
                    button = Seg(ident)
                    button.clicked.connect(lambda checked=False, value=ident: self.picked.emit(value))
                    self.lay.addWidget(button)
                    self.buttons[ident] = button
            self.lay.addStretch(1)
        self.set_current(current)

    def set_current(self, ident: str) -> None:
        self._current = ident if ident in self.ids else ""
        for name, button in self.buttons.items():
            button.set_active(name == self._current)
        if self.combo is not None and self._current and self.combo.currentText() != self._current:
            self.combo.setCurrentIndex(self.ids.index(self._current))


def status_chip(value: str = "", tone_name: str = "mute") -> Chip:
    return Chip(value, STATUS_CHIP_TONE.get(tone_name, tone_name), outline=True)


def set_chip(chip: Chip, value: str, tone_name: str) -> None:
    mapped = STATUS_CHIP_TONE.get(tone_name, tone_name)
    if chip.text() != value:
        chip.setText(value)
    if chip._tone != mapped:
        chip.set_tone(mapped)


def dot(tone_name: str = "dim", size: int = 10) -> QWidget:
    """The 10 px square status mark of inventory rows."""

    mark = QWidget()
    mark.setFixedSize(size, size)
    paint_dot(mark, tone_name)
    return mark


def paint_dot(mark: QWidget, tone_name: str) -> None:
    style = f"background:{tone(tone_name)};"
    if mark.styleSheet() != style:
        mark.setStyleSheet(style)


__all__ = [
    "DASH", "NOT_SUPPLIED", "PROVENANCE_TONE", "STATUS_CHIP_TONE", "FlowLayout", "KVList", "LinkButton", "LinkPicker", "LogList", "Meter",
    "MeterList",
    "MetricCard", "Page", "ToggleChip", "TwoLineList", "clock_of", "dot", "fmt_bits", "fmt_fidelity", "fmt_flag", "fmt_fraction",
    "fmt_number", "fmt_percent", "fmt_qber", "fmt_rate", "grid_row", "head_row", "mono_header", "note", "paint_dot", "set_chip",
    "set_text", "shrinkable", "stack_column", "status_chip", "unit_fraction",
]
