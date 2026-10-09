"""Published transmission geometry reuses camera projections and tracks latest state.

Published transmission records are explicitly synthetic UI fixtures, never
evidence of physical photon transmission. This verifies rendering cache reuse
and replacement of received state, not protocol or optical performance.
"""
import os
from copy import deepcopy
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console.widgets.orbit_globe import OrbitGlobe
from tests.test_console_geo_performance import model


def test_transmission_cache_reuses_projections_and_invalidates_camera_and_received_state(monkeypatch):
    app = QApplication.instance() or QApplication([])
    globe = OrbitGlobe(); globe.resize(1280, 900)
    received = model()
    states = deepcopy(received.satellites)
    # Exercise both OGS and ISL receiver identities from actual published records.
    states["SAT-0"]["transmitting_links"].append(
        {"link_id": "ISL-01", "peer_id": "SAT-1", "endpoint_kind": "satellite"})
    isl = replace(received.links[0], key="ISL-01", id="ISL-01",
                  a="SAT-0", b="SAT-1", kind="satellite_isl", style="space")
    received = replace(received, satellites=states, links=(*received.links, isl))
    globe.set_model(received)
    projections = []
    original = globe.project
    def project(*args, **kwargs):
        projections.append(args[0]); return original(*args, **kwargs)
    monkeypatch.setattr(globe, "project", project)
    try:
        first = globe.transmission_geometry()
        assert {line[0]["link_id"] for line in first} == {
            "OBS-SAT-0", "OBS-SAT-1", "OBS-SAT-2", "ISL-01"}
        projected = len(projections)
        assert projected == 8
        for _ in range(20):
            assert globe.transmission_geometry() is first
        assert len(projections) == projected

        globe.set_camera(30., 10., 1.)
        camera = globe.transmission_geometry()
        assert camera is not first and camera[0][1] != first[0][1]
        assert len(projections) == projected + 8
        assert globe.transmission_geometry() is camera

        latest_states = deepcopy(states)
        latest_states["SAT-0"]["ecef_km"] = [0., 6900., 0.]
        latest_states["SAT-0"]["transmitting_links"] = [
            {"link_id": "ISL-01", "peer_id": "SAT-1", "endpoint_kind": "satellite",
             "origin": "new received synthetic observation"}]
        latest = replace(received, satellites=latest_states, network_time_s=2.)
        globe.set_model(latest)
        current = globe.transmission_geometry()
        assert current is not camera
        assert {line[0]["link_id"] for line in current} == {"OBS-SAT-1", "OBS-SAT-2", "ISL-01"}
        isl = next(line for line in current if line[0]["link_id"] == "ISL-01")
        assert isl[0] is latest_states["SAT-0"]["transmitting_links"][0]
        assert isl[1] == original(latest_states["SAT-0"]["ecef_km"])[0]
        assert globe.transmission_geometry() is current

        empty = {key: {**state, "transmitting_links": []} for key, state in latest_states.items()}
        globe.set_model(replace(latest, satellites=empty))
        no_transmissions = globe.transmission_geometry()
        assert not no_transmissions and globe.transmission_geometry() is no_transmissions
    finally:
        globe.shutdown(); globe.close(); globe.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); app.processEvents()
