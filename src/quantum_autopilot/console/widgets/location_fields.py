"""Optional location block of the node form: latitude, longitude, place.

The registration may be completed without a location. Leaving the three fields
empty is a valid answer: the request then carries `latitude: null`,
`longitude: null`, `place: ""` and the backend creates the node unlocated (it
waits in the queue of the Topology screen until it is positioned).

The block only reads and validates what the operator typed. It never computes a
position: the "known places" list copies the coordinates of a label point of the
packaged Salvador base map into the fields, where they stay editable.
"""
from __future__ import annotations

import math

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QWidget

from ..theme import C, mono
from .cards import LinkButton, note
from .prims import Input, Label, hbox, vbox

MAX_PLACE_CHARS = 80
PLACE_PROMPT = "Choose from known places…"
EMPTY_HINT = ("Leave empty to add without location — the node waits in the queue on the Topology screen "
              "until it is positioned.")


def known_places():
    """Label points of the packaged Salvador base map, by rank then name. () when unavailable."""

    try:
        from ..geodata import load_salvador
        return tuple(sorted(load_salvador().places, key=lambda place: (place.rank, place.name)))
    except Exception:
        return ()


def map_extent():
    """(south, north, west, east) of the packaged Salvador base map, or None."""

    try:
        from ..geodata import load_salvador
        box = load_salvador().bbox
        return box.south, box.north, box.west, box.east
    except Exception:
        return None


def parse_coordinate(text: str, name: str, limit: float) -> float:
    cleaned = text.strip().replace("−", "-").replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        raise ValueError(f"{name} must be a number in decimal degrees") from None
    if not math.isfinite(value) or not -limit <= value <= limit:
        raise ValueError(f"{name} must be between {-limit:g} and {limit:g} degrees")
    return value


def parse_location(latitude: str, longitude: str, place: str) -> tuple[float | None, float | None, str]:
    """Validated (latitude, longitude, place); (None, None, "") when no location is given.

    Raises ValueError for a half-filled pair, a value out of range or a place
    label without coordinates.
    """

    latitude, longitude, place = latitude.strip(), longitude.strip(), place.strip()
    if len(place) > MAX_PLACE_CHARS or not place.isprintable():
        raise ValueError(f"Place must be printable text of at most {MAX_PLACE_CHARS} characters")
    if not latitude and not longitude:
        if place:
            raise ValueError("A place label needs latitude and longitude; clear it to add the node without location")
        return None, None, ""
    if not latitude or not longitude:
        raise ValueError("Give both latitude and longitude, or leave both empty to add without location")
    return parse_coordinate(latitude, "Latitude", 90.0), parse_coordinate(longitude, "Longitude", 180.0), place


class LocationFields(QWidget):
    """Latitude / longitude / place inputs with a known-places list."""

    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = vbox(self, (0, 0, 0, 0), 8)
        lay.addWidget(Label("Location · optional"))
        self.places = known_places()
        self.place_combo = QComboBox()
        self.place_combo.setFont(mono(12))
        self.place_combo.setAccessibleName("Choose from known places")
        self.place_combo.setMinimumContentsLength(12)
        self.place_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.place_combo.addItem(PLACE_PROMPT)
        for place in self.places:
            self.place_combo.addItem(place.name)
        self.place_combo.activated.connect(self._place_chosen)
        self.place_combo.setVisible(bool(self.places))
        lay.addWidget(self.place_combo)
        pair = QWidget()
        pl = hbox(pair, (0, 0, 0, 0), 8)
        self.latitude = self._field(pl, "Latitude", "-12.9714")
        self.longitude = self._field(pl, "Longitude", "-38.5014")
        lay.addWidget(pair)
        lay.addWidget(Label("Place"))
        self.place = Input("label shown on the map")
        self.place.setMinimumWidth(0)
        self.place.setMaxLength(MAX_PLACE_CHARS)
        self.place.setAccessibleName("Place")
        self.place.textEdited.connect(lambda _text: self.changed.emit())
        lay.addWidget(self.place)
        extent = map_extent()
        ranges = ("Decimal degrees (WGS-84)." if extent is None else
                  f"Decimal degrees (WGS-84). Salvador base map: latitude {extent[0]:.2f} to {extent[1]:.2f}, "
                  f"longitude {extent[2]:.2f} to {extent[3]:.2f}.")
        self.range_note = note(ranges, 12, C.ink3)
        lay.addWidget(self.range_note)
        self.hint = note(EMPTY_HINT, 12, C.ink2)
        lay.addWidget(self.hint)
        self.clear_button = LinkButton("No location · clear fields")
        self.clear_button.clicked.connect(self.clear)
        lay.addWidget(self.clear_button)

    def _field(self, layout, title: str, placeholder: str) -> Input:
        box = QWidget()
        bl = vbox(box, (0, 0, 0, 0), 5)
        bl.addWidget(Label(title))
        field = Input(placeholder, mono_font=True)
        field.setMinimumWidth(0)
        field.setAccessibleName(title)
        field.textEdited.connect(lambda _text: self.changed.emit())
        bl.addWidget(field)
        layout.addWidget(box, 1)
        return field

    def _place_chosen(self, index: int) -> None:
        if index <= 0 or index > len(self.places):
            return
        place = self.places[index - 1]
        self.latitude.setText(f"{place.lat:.5f}")
        self.longitude.setText(f"{place.lon:.5f}")
        self.place.setText(place.name[:MAX_PLACE_CHARS])
        self.changed.emit()

    # ---------------------------------------------------------------- value
    def is_empty(self) -> bool:
        return not (self.latitude.text().strip() or self.longitude.text().strip() or self.place.text().strip())

    def value(self) -> tuple[float | None, float | None, str]:
        return parse_location(self.latitude.text(), self.longitude.text(), self.place.text())

    def outside_map(self) -> bool:
        """True when valid coordinates lie outside the packaged Salvador base map."""

        extent = map_extent()
        try:
            latitude, longitude, _place = self.value()
        except ValueError:
            return False
        if extent is None or latitude is None:
            return False
        return not (extent[0] <= latitude <= extent[1] and extent[2] <= longitude <= extent[3])

    def clear(self) -> None:
        for field in (self.latitude, self.longitude, self.place):
            field.clear()
        self.place_combo.setCurrentIndex(0)
        self.changed.emit()


__all__ = ["EMPTY_HINT", "LocationFields", "MAX_PLACE_CHARS", "known_places", "map_extent", "parse_coordinate",
           "parse_location"]
