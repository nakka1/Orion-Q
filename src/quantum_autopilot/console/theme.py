"""ORION-Q Workspace design tokens — dark and light, switchable at runtime.

The values are the `.theme-dark` / `.theme-light` custom properties of the
approved workspace prototype, copied verbatim. `C.<token>` always answers for
the *active* theme, so widgets built after `set_theme()` pick up the new skin;
the shell rebuilds its widgets on a theme change (see `shell.ConsoleWindow`).

Typography is Inter for text and JetBrains Mono for identifiers, labels,
buttons and tables. Both are bundled; no other family is used by the UI.
"""
from __future__ import annotations

from math import floor
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

FONT_DIR = Path(__file__).resolve().parent / "fonts"
BUNDLED_FONT_FILES = ("Inter.ttf", "JetBrainsMono.ttf")

# Reference frame of the prototype. Larger windows zoom relative to it.
DESIGN_W = 1440
DESIGN_H = 900
# Below this logical size the window scrolls instead of shrinking type.
MIN_CONTENT_W = 960
MIN_CONTENT_H = 480

# Shell chrome metrics (logical px), shared by the shell and its tests.
TOPBAR_H = 52
RAIL_W = 60
STATUSBAR_H = 30
JOURNAL_ROWS_H = 112
TOAST_MS = 2600

THEMES = ("dark", "light")

TOKENS: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#141413", "raised": "#1a1a18", "panel": "#1e1d1b", "deep": "#121110",
        "ink": "#faf9f5", "ink2": "#b0aea5", "ink3": "#8f8e87", "ink4": "#5e5d59",
        "acc": "#d97757", "acc-t": "#e08a6c",
        "line": "rgba(250,249,245,.16)", "soft": "rgba(250,249,245,.09)",
        "hover": "rgba(250,249,245,.05)", "sel": "rgba(217,119,87,.13)",
        "calm": "rgba(250,249,245,.36)",
        "green": "#7fb79a", "green-t": "#8fc4a8", "red": "#c05a6c", "red-t": "#e08a99",
        "violet": "#8b81c4", "violet-t": "#a59ce0",
        "shadow": "0 20px 60px rgba(0,0,0,.5)",
        "water": "#15181a", "land": "#201f1c", "road": "rgba(250,249,245,.11)",
    },
    "light": {
        "bg": "#faf9f5", "raised": "#f1efe8", "panel": "#ece9e0", "deep": "#f4f2ec",
        "ink": "#141413", "ink2": "#4c4b45", "ink3": "#5f5e58", "ink4": "#a8a69d",
        "acc": "#c15f3c", "acc-t": "#a94e2f",
        "line": "rgba(20,20,19,.22)", "soft": "rgba(20,20,19,.11)",
        "hover": "rgba(20,20,19,.045)", "sel": "rgba(193,95,60,.12)",
        "calm": "rgba(20,20,19,.38)",
        "green": "#4f8a6b", "green-t": "#3f7559", "red": "#b04459", "red-t": "#a23a4f",
        "violet": "#6a5fb0", "violet-t": "#5a50a0",
        "shadow": "0 20px 50px rgba(20,20,19,.18)",
        "water": "#e4e9ec", "land": "#f6f4ee", "road": "rgba(20,20,19,.10)",
    },
}

# Names used by console code written before the workspace tokens existed.
LEGACY_ALIASES = {
    "void": "bg", "panel2": "raised", "line2": "soft", "grid": "soft",
    "muted": "ink2", "dim": "ink3", "amber": "acc",
    "nav_bg": "sel", "tog_bg": "sel",
}

_theme = "dark"


class Tok(str):
    """A token value that remembers its name.

    A `Tok` formats as the value of the *active* theme, so one captured before
    a theme change (a default argument, a module constant) still paints with
    the current skin once it reaches an f-string, `qc()` or `resolve()`.
    """

    __slots__ = ("token",)

    def __new__(cls, token: str, value: str):
        item = super().__new__(cls, value)
        item.token = token
        return item

    def current(self) -> str:
        return TOKENS[_theme][self.token]

    def __str__(self) -> str:
        return self.current()

    def __format__(self, spec: str) -> str:
        return format(self.current(), spec)

    def __eq__(self, other) -> bool:
        if isinstance(other, Tok):
            return self.token == other.token
        return str.__eq__(self.current(), other)

    def __ne__(self, other) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return hash(("orion-token", self.token))

    def __reduce__(self):
        return (Tok, (self.token, str.__str__(self)))


def token(name: str) -> Tok:
    """Value of a prototype token (`acc-t`, `soft`, …) in the active theme."""

    name = LEGACY_ALIASES.get(name, name).replace("_", "-")
    return Tok(name, TOKENS[_theme][name])


class _Tokens(type):
    def __getattr__(cls, name: str) -> Tok:
        if name.startswith("__"):
            raise AttributeError(name)
        try:
            return token(name)
        except KeyError:
            raise AttributeError(f"unknown colour token: {name}") from None

    def __setattr__(cls, name, value):
        raise AttributeError("colour tokens are read-only; use theme.set_theme()")


class C(metaclass=_Tokens):
    """`C.acc`, `C.ink2`, `C.acc_t` … plus the legacy names (`C.void`, `C.amber`)."""


def current_theme() -> str:
    return _theme


def set_theme(name: str) -> bool:
    """Select the active token set. Returns True when the theme changed.

    Only the tokens change here. Widgets already on screen keep the strings
    they were built with; `ConsoleWindow.set_theme()` rebuilds them and
    re-installs the application style.
    """

    global _theme
    if name not in TOKENS:
        raise ValueError(f"unknown theme: {name!r}")
    changed = name != _theme
    _theme = name
    return changed


def resolve(value) -> str:
    """Plain string for a colour, following the active theme for tokens."""

    return value.current() if isinstance(value, Tok) else value


# Semantic tones. The contract's names stay; `amber` is the accent.
_TONE_TOKENS = {
    "ink": "ink", "muted": "ink2", "dim": "ink3", "amber": "acc",
    "green": "green", "red": "red", "violet": "violet",
    # prototype class names (b-ok, b-warn, …)
    "ok": "green", "warn": "acc", "bad": "red", "sat": "violet", "mute": "ink4",
}
_TEXT_TONE_TOKENS = {
    "ink": "ink", "muted": "ink2", "dim": "ink3", "amber": "acc-t",
    "green": "green-t", "red": "red-t", "violet": "violet-t",
    # prototype class names (t-ok, t-warn, …)
    "ok": "green-t", "warn": "acc-t", "bad": "red-t", "sat": "violet-t", "mute": "ink3",
}


class _ToneMap(dict):
    """Live view kept for callers that index `theme.TONE[name]`."""

    def __getitem__(self, name):
        return token(_TONE_TOKENS[name])

    def get(self, name, default=None):
        return token(_TONE_TOKENS[name]) if name in _TONE_TOKENS else default


TONE = _ToneMap({name: None for name in ("ink", "muted", "dim", "amber", "green", "red", "violet")})


def tone(name: str) -> Tok:
    """Fill / stroke colour of a semantic tone (`b-*` and `c-*` in the prototype)."""

    return token(_TONE_TOKENS.get(name, "ink"))


def text_tone(name: str) -> Tok:
    """Text colour of a semantic tone (`t-*` in the prototype)."""

    return token(_TEXT_TONE_TOKENS.get(name, "ink"))


_TEXT_OF_FILL = {"acc": "acc-t", "green": "green-t", "red": "red-t", "violet": "violet-t"}


def text_of(color) -> str:
    """Text variant of a fill token (`acc` → `acc-t`); other colours unchanged."""

    if isinstance(color, Tok) and color.token in _TEXT_OF_FILL:
        return TOKENS[_theme][_TEXT_OF_FILL[color.token]]
    return resolve(color)


def _rgba(value: str) -> tuple[int, int, int, float]:
    value = resolve(value).strip()
    if value.startswith("rgba"):
        parts = value[value.index("(") + 1:value.rindex(")")].split(",")
        r, g, b = (int(float(p)) for p in parts[:3])
        return r, g, b, float(parts[3])
    if value.startswith("rgb"):
        parts = value[value.index("(") + 1:value.rindex(")")].split(",")
        r, g, b = (int(float(p)) for p in parts[:3])
        return r, g, b, 1.0
    colour = QColor(value)
    return colour.red(), colour.green(), colour.blue(), colour.alphaF()


def qc(value) -> QColor:
    """QColor for a hex or `rgba(r,g,b,a)` token, following the active theme."""

    r, g, b, a = _rgba(value)
    return QColor(r, g, b, max(0, min(255, round(a * 255))))


def mix(color, over=None, alpha: float | None = None) -> str:
    """Opaque `#rrggbb` of `color` composited on `over` (default: page ground)."""

    r, g, b, a = _rgba(color)
    if alpha is not None:
        a = alpha
    br, bg_, bb, _ = _rgba(over if over is not None else TOKENS[_theme]["bg"])
    return "#{:02x}{:02x}{:02x}".format(
        round(br + (r - br) * a), round(bg_ + (g - bg_) * a), round(bb + (b - bb) * a))


def faded(color, over=None) -> str:
    """The prototype's disabled look: the colour at 45 % opacity."""

    return mix(color, over, 0.45)


def tint(color, alpha: float) -> str:
    """`rgba()` wash of a colour, for selected / emphasised grounds."""

    r, g, b, _ = _rgba(color)
    return f"rgba({r},{g},{b},{alpha:g})"


def shadow() -> tuple[QColor, int, int]:
    """(colour, blur radius, y offset) parsed from the `shadow` token."""

    spec = TOKENS[_theme]["shadow"]
    head, colour = spec[:spec.index("rgba")].split(), spec[spec.index("rgba"):]
    return qc(colour), int(head[2].rstrip("px")), int(head[1].rstrip("px"))


# ------------------------------------------------------------------- type
_FAMILIES: dict[str, str] = {}
_STACKS = {
    "text": ["Inter", "Segoe UI Variable Text", "Segoe UI", "DejaVu Sans"],
    "mono": ["JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New"],
}
# Kinds requested by older call sites. They resolve to the two workspace
# families; Chakra Petch, Oswald and Noto Sans JP are no longer used.
_KIND_ALIASES = {"industrial": "text", "display": "text", "jp": "text"}
FONT_KINDS = ("text", "mono")


def load_bundled_fonts() -> None:
    """Register the two bundled workspace families with Qt."""

    for name in BUNDLED_FONT_FILES:
        path = FONT_DIR / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    _FAMILIES.clear()


def family(kind: str) -> str:
    kind = _KIND_ALIASES.get(kind, kind)
    if kind not in _FAMILIES:
        available = set(QFontDatabase.families())
        _FAMILIES[kind] = next(
            (name for name in _STACKS[kind] if name in available), _STACKS[kind][-1]
        )
    return _FAMILIES[kind]


def px(size: float) -> int:
    """Whole pixel size for a CSS size; halves round up (13.5 → 14)."""

    return int(floor(size + 0.5))


def _font(kind: str, size: float, weight: int, spacing: float, upper: bool = False) -> QFont:
    f = QFont(family(kind))
    minimum = 11 if kind == "mono" else 12
    f.setPixelSize(max(minimum, px(size)))
    f.setWeight(QFont.Weight(weight))
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, size * spacing)
    if upper:
        f.setCapitalization(QFont.Capitalization.AllUppercase)
    return f


def text(size: float = 13.5, weight: int = 400) -> QFont:
    """Inter. 13.5 px is the workspace base size."""

    return _font("text", size, weight, 0.0)


def mono(size: float = 12, spacing: float = 0.0, weight: int = 400, *, upper: bool = False) -> QFont:
    """JetBrains Mono for identifiers, values and tables."""

    return _font("mono", size, min(weight, 500), spacing, upper)


def label_font(size: float = 11) -> QFont:
    """`.lbl`: 11 px mono, uppercase, .08em tracking."""

    return _font("mono", size, 400, 0.08, True)


def button_font(size: float = 11.5) -> QFont:
    """`.btn` / `.seg` / `.chip`: mono, uppercase, .06em tracking."""

    return _font("mono", size, 400, 0.06, True)


def display(size: float, spacing: float = 0.0, weight: int = 400) -> QFont:
    """Large readings and headings. Inter, regular (500 for former bold use)."""

    return _font("text", size, 500 if weight >= 600 else 400, spacing)


def jp(size: float = 12, spacing: float = 0.0) -> QFont:
    """Kept for old imports; renders with the text family."""

    return _font("text", size, 400, spacing)


# -------------------------------------------------------------- app style
def build_qss() -> str:
    """Application style sheet for the active theme."""

    t = TOKENS[_theme]
    thumb = mix(t["ink"], t["bg"], 0.28)
    return f"""
QWidget {{ color: {t['ink']}; background: transparent; }}
QToolTip {{ background: {t['raised']}; color: {t['ink']}; border: 1px solid {mix(t['line'])}; padding: 4px 6px; }}
QDialog, QMessageBox, QInputDialog {{ background: {t['raised']}; color: {t['ink']}; }}
QMenu {{ background: {t['bg']}; color: {t['ink']}; border: 1px solid {t['line']}; padding: 4px 0; }}
QMenu::item {{ padding: 6px 16px; background: transparent; }}
QMenu::item:selected {{ background: {t['sel']}; }}
QMenu::separator {{ height: 1px; background: {t['soft']}; margin: 4px 0; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {t['bg']}; color: {t['ink']}; border: 1px solid {t['line']};
    padding: 7px 10px; selection-background-color: {t['acc']}; selection-color: {t['bg']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus {{ border: 1px solid {t['acc']}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {faded(t['ink'])}; border-color: {t['soft']}; }}
QComboBox QAbstractItemView {{ background: {t['bg']}; color: {t['ink']}; border: 1px solid {t['line']};
    selection-background-color: {mix(t['sel'])}; selection-color: {t['ink']}; outline: 0; }}
QCheckBox, QRadioButton {{ color: {t['ink']}; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {faded(t['ink'])}; }}
QAbstractItemView {{ selection-background-color: {mix(t['sel'])}; selection-color: {t['ink']}; }}
QHeaderView::section {{ background: {t['bg']}; color: {t['ink3']}; border: 0;
    border-bottom: 1px solid {t['line']}; padding: 6px 8px; }}
QDialog QPushButton, QMessageBox QPushButton, QInputDialog QPushButton {{
    color: {t['ink']}; background: transparent; border: 1px solid {t['line']}; padding: 8px 12px; }}
QDialog QPushButton:hover, QMessageBox QPushButton:hover, QInputDialog QPushButton:hover {{ background: {t['hover']}; }}
QDialog QPushButton:focus, QMessageBox QPushButton:focus, QInputDialog QPushButton:focus {{ border: 1px solid {t['acc']}; }}
QDialog QPushButton:disabled, QMessageBox QPushButton:disabled {{ color: {faded(t['ink'])}; border-color: {t['soft']}; }}
QScrollBar:vertical {{ width: 8px; background: transparent; border: none; margin: 0; }}
QScrollBar::handle:vertical {{ background: {thumb}; min-height: 24px; }}
QScrollBar:horizontal {{ height: 8px; background: transparent; border: none; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {thumb}; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {t['acc']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def build_palette() -> QPalette:
    t = TOKENS[_theme]
    palette = QPalette()
    disabled = QColor(faded(t["ink"]))
    for role, value in (
        (QPalette.ColorRole.Window, t["bg"]), (QPalette.ColorRole.WindowText, t["ink"]),
        (QPalette.ColorRole.Base, t["bg"]), (QPalette.ColorRole.AlternateBase, t["raised"]),
        (QPalette.ColorRole.Text, t["ink"]), (QPalette.ColorRole.Button, t["raised"]),
        (QPalette.ColorRole.ButtonText, t["ink"]), (QPalette.ColorRole.ToolTipBase, t["raised"]),
        (QPalette.ColorRole.ToolTipText, t["ink"]), (QPalette.ColorRole.Highlight, t["acc"]),
        (QPalette.ColorRole.HighlightedText, t["bg"]), (QPalette.ColorRole.PlaceholderText, t["ink3"]),
        (QPalette.ColorRole.Link, t["acc-t"]),
    ):
        palette.setColor(role, QColor(value))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return palette


def __getattr__(name: str):
    # `theme.QSS` stays readable for older callers and always matches the theme.
    if name == "QSS":
        return build_qss()
    raise AttributeError(name)


def install(app, *, root=None) -> None:
    """Install fonts, palette and style for the active theme (idempotent).

    Diagnostics and additional windows share the same QApplication. Loading
    fonts or reinstalling an unchanged theme would repolish every widget.
    """

    if not app.property("orionBundledFontsLoaded"):
        load_bundled_fonts()
        app.setProperty("orionBundledFontsLoaded", True)
    qss = build_qss()
    target = root if root is not None else app
    if target.styleSheet() != qss:
        target.setPalette(build_palette())
        target.setStyleSheet(qss)
    font = text(13.5)
    if app.font() != font:
        app.setFont(font)


__all__ = [
    "BUNDLED_FONT_FILES", "C", "DESIGN_H", "DESIGN_W", "FONT_DIR", "FONT_KINDS", "JOURNAL_ROWS_H",
    "MIN_CONTENT_H", "MIN_CONTENT_W", "RAIL_W", "STATUSBAR_H", "THEMES", "TOAST_MS", "TOKENS", "TONE",
    "TOPBAR_H", "Tok", "build_palette", "build_qss", "button_font", "current_theme", "display", "faded",
    "family", "install", "jp", "label_font", "load_bundled_fonts", "mix", "mono", "px", "qc", "resolve",
    "set_theme", "shadow", "text", "text_of", "text_tone", "tint", "token", "tone",
]
