"""Set the location of a node: latitude, longitude, place — typed or picked on the map.

The dialog only builds the request of the backend verb `set_node_location`. The
backend validates it, persists it, recomputes the length of the node's fibres
(geodesic between located ends) and keeps the node's data and connections; the
node leaves the queue when the next snapshot says it is located.
"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget

from ..geodata import load_salvador
from ..theme import C, text_tone
from .adaptive_dialog import AdaptiveDialog
from .inspector import PlainBtn, op_button
from .network_views import format_lat, format_lon
from .prims import Input, Label, Prose, hbox, set_color, vbox

PLACE_MAX = 80


def parse_coordinate(value: str, low: float, high: float, name: str) -> float:
    try:
        number = float(value.strip().replace(",", "."))
    except ValueError:
        raise ValueError(f"{name} must be a number in decimal degrees") from None
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{name} must be between {low:g} and {high:g} degrees")
    return number


class LocationDialog(AdaptiveDialog):
    submitted = Signal(str, object)        # node id, {"latitude", "longitude", "place"}
    pick_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("OrionQ · Node location")
        self.setModal(False)
        self.resize(460, 470)
        self.setMinimumWidth(380)
        self.setStyleSheet(f"QDialog{{background:{C.raised};color:{C.ink};}}")
        self.node_id = ""
        lay = vbox(self, (20, 18, 20, 18), 10)
        self.heading = Label("Set location", C.acc_t)
        lay.addWidget(self.heading)
        self.intro = Prose("", 13, C.ink2)
        lay.addWidget(self.intro)
        self.fields: dict[str, Input] = {}
        for key, title, placeholder in (("latitude", "Latitude · decimal degrees (south is negative)", "-12.9777"),
                                        ("longitude", "Longitude · decimal degrees (west is negative)", "-38.5016"),
                                        ("place", f"Place · free label, up to {PLACE_MAX} characters", "Pelourinho")):
            lay.addWidget(Label(title))
            field = Input(placeholder, mono_font=key != "place")
            field.setAccessibleName(title)
            if key == "place":
                field.setMaxLength(PLACE_MAX)
            field.textChanged.connect(self._describe)
            self.fields[key] = field
            lay.addWidget(field)
        self.where = Prose("", 12.5, C.ink3)
        lay.addWidget(self.where)
        tools = QWidget()
        row = hbox(tools, (0, 0, 0, 0), 8)
        self.pick = PlainBtn("Pick on map")
        self.pick.setToolTip("Click a point on the GEO map to fill latitude and longitude")
        row.addWidget(self.pick)
        row.addStretch(1)
        lay.addWidget(tools)
        buttons = QWidget()
        row = hbox(buttons, (0, 0, 0, 0), 8)
        self.apply = op_button("Set location", "set_node_location", accent=True)
        self.unlocate = op_button("Return to queue", "set_node_location")
        self.cancel = PlainBtn("Close")
        for button in (self.apply, self.unlocate):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(self.cancel)
        lay.addWidget(buttons)
        self.note = Prose("", 12.5, C.ink3)
        lay.addWidget(self.note)
        lay.addStretch(1)
        self.pick.clicked.connect(lambda: self.pick_requested.emit(self.node_id))
        self.apply.clicked.connect(self.submit)
        self.unlocate.clicked.connect(self._unlocate)
        self.cancel.clicked.connect(self.close)
        self.fields["place"].returnPressed.connect(self.submit)

    def begin(self, node) -> None:
        """Open for `node` (anything with id, located, latitude, longitude, place)."""

        self.node_id = node.id
        self.heading.setText(f"Set location · {node.id}")
        located = bool(getattr(node, "located", False))
        self.intro.setText(
            ("This node is on the map. Changing its position keeps its data and connections; the backend "
             "recomputes the length of its fibres." if located else
             "This node waits in the queue. Once it has a position it moves onto the map with its data and "
             "its connections; the backend sets the length of its fibres to the geodesic distance.")
            + " The position is operator-declared, not measured.")
        self.fields["latitude"].setText(f"{node.latitude:.6f}" if located else "")
        self.fields["longitude"].setText(f"{node.longitude:.6f}" if located else "")
        self.fields["place"].setText(getattr(node, "place", None) or "")
        self.unlocate.setVisible(located)
        self.unlocate.setProperty("ready", located)
        self.set_note("")
        self._describe()
        self.show()
        self.raise_()
        self.activateWindow()
        self.fields["latitude"].setFocus(Qt.FocusReason.OtherFocusReason)

    def set_picked(self, latitude: float, longitude: float) -> None:
        """Fill the coordinates with a point picked on the map (six decimals, about 0.1 m)."""

        self.fields["latitude"].setText(f"{latitude:.6f}")
        self.fields["longitude"].setText(f"{longitude:.6f}")
        self.set_note("Coordinates filled from the map. Add a place label and confirm.", "dim")
        self.show()
        self.raise_()

    def set_note(self, value: str, tone_name: str = "dim") -> None:
        self.note.setText(value)
        set_color(self.note, text_tone(tone_name))

    def values(self) -> dict:
        latitude = parse_coordinate(self.fields["latitude"].text(), -90.0, 90.0, "Latitude")
        longitude = parse_coordinate(self.fields["longitude"].text(), -180.0, 180.0, "Longitude")
        return {"latitude": latitude, "longitude": longitude, "place": self.fields["place"].text().strip()}

    def _describe(self, *_args) -> None:
        try:
            values = self.values()
        except ValueError:
            self.where.setText("")
            return
        basemap = load_salvador()
        lon, lat = values["longitude"], values["latitude"]
        if basemap.in_municipality(lon, lat):
            where = "on land in the municipality of Salvador (base map)"
        elif basemap.contains_land(lon, lat):
            where = "on land outside the municipality of Salvador (base map)"
        elif basemap.data_extent.contains(lon, lat):
            where = "on water according to the base map — check the coordinates"
        else:
            where = "outside the area of the base map; the node will be off the default view"
        self.where.setText(f"{format_lat(lat)} · {format_lon(lon)} — {where}.")

    def submit(self) -> None:
        if not self.apply.isEnabled():
            return
        try:
            values = self.values()
        except ValueError as exc:
            self.set_note(str(exc), "red")
            return
        self.submitted.emit(self.node_id, values)

    def _unlocate(self) -> None:
        self.submitted.emit(self.node_id, {"latitude": None, "longitude": None, "place": ""})


__all__ = ["LocationDialog", "PLACE_MAX", "parse_coordinate"]
