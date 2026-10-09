"""Protocol observations remain distinguishable from legacy visibility samples."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.contract import Link, LiveLink, LiveNode, LiveView, Node  # noqa: E402
from quantum_autopilot.console.widgets.inspector import CommandStatus, percent, rate  # noqa: E402
from quantum_autopilot.console.widgets.network_views import build_net_model  # noqa: E402
from quantum_autopilot.console.widgets.node_window import NodeWindow  # noqa: E402

pytestmark = pytest.mark.unit
NODE_ID = "IBM-WATSON"
SESSION_IDS = {"tf": "IBM-IQC-TF", "memory": "IBM-ENTANGLEMENT", "satellite": "IBM-SATELLITE"}


def snapshot(*, modes=("tf", "memory", "satellite"), tick=20, legacy=False,
             source="identified-demo-A", origin="simulated", received=True,
             session_status="operational", zero_pairs=False, duplicate_tf=False):
    """Small published records; there is no protocol-generated visibility wave."""
    nodes = {NODE_ID: Node(NODE_ID, "ENDPOINT", "up", .5, .5)}
    links, live_links, sessions = [], [], {}
    for mode in modes:
        session_id = SESSION_IDS[mode]
        if mode == "tf":
            technologies = ["tf_qkd"]
            path, endpoints = [NODE_ID, "TF-IBM-IQC-CENTER", "IQC"], [NODE_ID, "IQC"]
            peer = path[1]
            metrics = {"qber": .03125, "phase_reference_residual_rad": .0045,
                       "conditional_key_estimate_bps": 37.5, "certified_secret_bits": 0,
                       "finite_key": {"abort_reason": "insufficient finite block"}}
            counters = {"observations": 7, "sifted_bits": 23}
            link_status = "degraded" if received else "no_input"
        elif mode == "memory":
            technologies = ["entanglement_swapping", "quantum_memory", "purification"]
            path, endpoints = ["IQMP", "R-MEMORY", NODE_ID], ["IQMP", NODE_ID]
            peer = path[-2]
            metrics = {"generation_pair_rate_hz": 0. if zero_pairs else 12.5,
                       "delivered_pair_rate_hz": 0. if zero_pairs else 1.25,
                       "bsm_success_fraction": 0. if zero_pairs else .25,
                       "purification_yield": 0. if zero_pairs else .125,
                       "memory_qubit_occupancy": {NODE_ID: 0 if zero_pairs else 2},
                       "segments": {f"{peer}|{NODE_ID}": {
                           "heralds": 0 if zero_pairs else 4, "fidelity_estimate": .9375}}}
            counters = {"observations": 11, "generation_attempts": 19,
                        "generated_pairs": 0 if zero_pairs else 4,
                        "delivered_pairs": 0 if zero_pairs else 1,
                        "bsm_attempts": 4, "bsm_successes": 0 if zero_pairs else 1}
            link_status = ("down" if zero_pairs else "up") if received else "no_input"
        else:
            technologies = ["satellite_doppler"]
            path, endpoints = [NODE_ID, "SAT-FIXTURE", "OGS-PEER"], [NODE_ID, "OGS-PEER"]
            peer = path[1]
            metrics = {"qber": .046875, "coincidences": 13,
                       "conditional_key_estimate_bps": 2.5, "certified_secret_bits": 0,
                       "observation_source_id": source + ":tdc",
                       "observation_origin": origin}
            counters = {"observations": 5, "sifted_bits": 9}
            link_status = "up" if received else "no_input"
        for name in path:
            nodes.setdefault(name, Node(name, "ENDPOINT", "up", .5, .5))
        if received:
            metrics["last_observed_at_s"] = tick - .25
        else:
            # Runtime can publish zero counters/rates before any acquisition.
            metrics = {key: 0 for key in metrics if key.endswith("_hz")}
            counters = {key: 0 for key in counters}
        config = {"session_id": session_id, "technologies": technologies, "link_ids": [],
                  "path": path, "endpoints": endpoints, "enabled": True,
                  "parameters": {"memory_capacity": 4}}
        sessions[session_id] = {
            "config": config, "status": session_status,
            "reason": "observed operations and classical receipts processed" if received
                      else "waiting for new source acquisition",
            "source": {"origin": origin}, "metrics": metrics, "counters": counters,
            "pending_commands": 0, "last_sequence": counters["observations"],
        }
        copies = 2 if mode == "tf" and duplicate_tf else 1
        for index in range(copies):
            link_id = f"TECH-{session_id}-{index + 1:02d}"
            config["link_ids"].append(link_id)
            channels = {"classical": {"channel_id": link_id + ".classical"},
                        "quantum": {"channel_id": link_id + ".quantum"}}
            links.append(Link(NODE_ID, peer, link_id, id=link_id,
                              configured_active=True, distance_km=2.5))
            live_links.append(LiveLink(link_id, NODE_ID, peer, 2.5, link_status,
                configured_active=True, effective=received, wave=(),
                input="none", display_provenance="assumed",
                provenance={"technology": {"session_id": session_id, "technologies": technologies},
                            "session_status": session_status, "source": origin,
                            "source_id": source, "protocol_metrics": metrics,
                            "security_status": "protocol test; no certified key material",
                            "channels": {"configuration": channels,
                                         "status": "received" if received else "waiting"}}))
    if legacy:
        nodes["LEGACY-PEER"] = Node("LEGACY-PEER", "ENDPOINT", "up", .5, .5)
        links.append(Link(NODE_ID, "LEGACY-PEER", "LEGACY-FIBRE", id="LEGACY-FIBRE",
                          configured_active=True, distance_km=3.5))
        live_links.append(LiveLink("LEGACY-FIBRE", NODE_ID, "LEGACY-PEER", 3.5, "up",
            configured_active=True, effective=True, qber=.025, fidelity=.9625,
            stability=.95, wave=(.94, .95, .96), wave_rate_hz=20,
            input=origin, display_provenance=origin))
    live_node = LiveNode(NODE_ID, "endpoint", "node", "up", links=tuple(link.id for link in links))
    live = LiveView(tick, float(tick), origin, source, nodes=(live_node,), links=tuple(live_links),
                    policy={"wave_rate_hz": 20, "wave_window_s": 12},
                    technologies={"clock_s": float(tick), "origin": origin,
                                  "source_id": source, "sessions": sessions})
    return SimpleNamespace(topology=SimpleNamespace(nodes=list(nodes.values()), links=links), live=live)


@pytest.fixture
def gui():
    app = QApplication.instance() or QApplication([])
    previous = theme.current_theme()
    theme.set_theme("dark"); theme.install(app)
    host = QWidget(); host.resize(1100, 800); host.show()
    yield app, host
    host.close(); host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    theme.set_theme(previous); theme.install(app)


def open_window(gui, snap, *, incremental=False):
    app, host = gui
    window = NodeWindow(NODE_ID, host, incremental=incremental)
    net = build_net_model(snap)
    assert window.refresh(snap, net, CommandStatus())
    window.show(); app.processEvents()
    return window, net


def wait_ready(window):
    for _ in range(500):
        if window.presentation_status()["ready"]:
            return
        QTest.qWait(10)
    assert window.presentation_status()["ready"], window.presentation_status()


def field(window, session_id, name):
    return window.protocols.texts()[f"{session_id} · {name}"]


def test_ibm_protocols_have_received_metrics_without_invented_visibility(gui):
    snap = snapshot()
    published = deepcopy(snap.live)
    window, _net = open_window(gui, snap)
    text = window.protocols.texts()
    assert text and window.protocols.isVisible() and window.protocol_label.isVisible()
    assert not window.chart.isVisible() and window.chart.keys() == []
    assert not window.chart._received and not window.wave_label.isVisible()
    assert window.content_texts()["protocols"] == text
    assert "recebidas pelo canal clássico" in window.protocol_label.text()
    for mode, observed in (("tf", 7), ("memory", 11), ("satellite", 5)):
        ident = SESSION_IDS[mode]
        assert "operational" in field(window, ident, "Estado")
        assert str(observed) in field(window, ident, "Recepção clássica")
        assert "19.75" in field(window, ident, "Recepção clássica")
        assert "simulated" in field(window, ident, "Origem")
    tf = field(window, SESSION_IDS["tf"], "TF-QKD")
    keys = field(window, SESSION_IDS["tf"], "Chaves · sessão")
    assert percent(.03125) in tf and rate(37.5) in keys
    assert "0" in keys and "certific" in keys.lower()
    assert "IQC" in str(text) and "IQMP" in str(text) and "OGS-PEER" in str(text)
    assert "13" in field(window, SESSION_IDS["satellite"], "Satélite/OGS")
    assert all(not link.wave for link in snap.live.links)
    assert snap.live == published


def test_configured_zero_metrics_are_not_receipts_or_synthetic_samples(gui):
    snap = snapshot(received=False, session_status="configured")
    window, _net = open_window(gui, snap)
    assert not window.chart.isVisible() and window.chart.keys() == []
    for ident in SESSION_IDS.values():
        state = field(window, ident, "Estado")
        reception = field(window, ident, "Recepção clássica")
        assert "configured" in state and "waiting for new source acquisition" in state
        assert "no input" in field(window, ident, "Enlaces locais").replace("_", " ").lower()
        assert "20" not in reception and "19.75" not in reception
        assert "aguard" in reception.lower() or "waiting" in reception.lower()
    science_suffixes = (" · TF-QKD", " · Pares e operações", " · Satélite/OGS", " · Chaves · sessão")
    assert not any(key.endswith(science_suffixes) for key in window.protocols.texts())
    assert all(not link.wave for link in snap.live.links)


def test_zero_heralds_after_a_real_receipt_remain_an_observed_zero_outcome(gui):
    snap = snapshot(modes=("memory",), zero_pairs=True)
    window, _net = open_window(gui, snap)
    state = field(window, SESSION_IDS["memory"], "Estado")
    reception = field(window, SESSION_IDS["memory"], "Recepção clássica")
    assert "operational" in state
    assert "down" in field(window, SESSION_IDS["memory"], "Enlaces locais")
    assert "11" in reception and "19.75" in reception
    assert "aguard" not in reception.lower() and "no input" not in reception.replace("_", " ").lower()
    pairs = field(window, SESSION_IDS["memory"], "Pares e operações")
    assert "19" in pairs and "0" in pairs
    segment = field(window, SESSION_IDS["memory"], f"Segmento R-MEMORY ↔ {NODE_ID}")
    assert "heralds 0" in segment and "fidelidade estimada —" in segment
    assert "0.9375" not in segment
    assert not window.chart.isVisible() and not window.chart._received


def test_mixed_links_keep_only_received_legacy_samples_in_visibility_chart(gui):
    snap = snapshot(legacy=True)
    published = deepcopy(snap.live)
    window, net = open_window(gui, snap)
    assert window.chart.isVisible() and window.wave_label.isVisible()
    assert window.protocols.isVisible()
    assert window.chart.keys() == ["LEGACY-FIBRE"]
    assert tuple(window.chart.series[0].samples) == snap.live.link("LEGACY-FIBRE").wave
    assert window.chart.series[0].rate_hz == 20
    assert window.chart.network_time_s == snap.live.network_time_s
    assert set(window.connection_texts()) == {link.key for link in net.links_of(NODE_ID)}
    assert len(window.legend_labels()) == len(net.links_of(NODE_ID))
    for index, series in enumerate(window.chart.series):
        assert window._legend[series.key].swatch.index == index
        assert not window._legend[series.key].swatch.isHidden()
    assert all(window._legend[link.id].swatch.isHidden()
               for link in snap.live.links if link.id != "LEGACY-FIBRE")
    window.chart.freeze(1)
    window._pin("LEGACY-FIBRE")
    before = window.chart.visible_samples("LEGACY-FIBRE")
    picked = []
    window.select.connect(lambda *args: picked.append(args))
    technological_key = snap.live.links[0].id
    window._hover(technological_key)
    assert window.chart._highlight == window._pinned == "LEGACY-FIBRE"
    window._pin(technological_key)
    assert picked == [("link", technological_key)]
    assert window.chart._highlight == window._pinned == "LEGACY-FIBRE"
    assert window.chart.visible_samples("LEGACY-FIBRE") == before
    assert snap.live == published


def test_two_links_in_one_session_do_not_duplicate_session_observations(gui):
    snap = snapshot(modes=("tf",), duplicate_tf=True)
    window, _net = open_window(gui, snap)
    keys = window.protocols.texts()
    assert sum(key.endswith(" · Recepção clássica") for key in keys) == 1
    assert "7" in field(window, SESSION_IDS["tf"], "Recepção clássica")
    assert len(window.connection_texts()) == 2


def test_unavailable_session_retains_observation_history_and_current_cause(gui):
    snap = snapshot(modes=("tf",), session_status="unavailable")
    sessions = snap.live.technologies["sessions"]
    sessions[SESSION_IDS["tf"]]["reason"] = "receiver blocked after the last observed block"
    link = snap.live.links[0]
    snap.live = replace(snap.live, links=(replace(link, status="down", effective=False),))
    window, _net = open_window(gui, snap)
    assert "unavailable" in field(window, SESSION_IDS["tf"], "Estado")
    assert "receiver blocked after the last observed block" in field(window, SESSION_IDS["tf"], "Estado")
    assert "19.75" in field(window, SESSION_IDS["tf"], "Recepção clássica")
    assert not window.chart.isVisible()


def test_source_change_drops_previous_receipts_and_legacy_visibility(gui):
    first = snapshot(legacy=True)
    window, _net = open_window(gui, first)
    assert window.chart._received
    latest = snapshot(modes=("tf",), tick=1, source="fresh-input-B", origin="measured",
                      received=False, session_status="configured")
    latest_net = build_net_model(latest)
    assert window.refresh(latest, latest_net, CommandStatus())
    assert not window.chart.isVisible() and window.chart.keys() == [] and not window.chart._received
    text = window.protocols.texts()
    assert not any(SESSION_IDS[key] in str(text) for key in ("memory", "satellite"))
    assert "19.75" not in str(text) and "identified-demo-A" not in str(text)
    assert "measured" in field(window, SESSION_IDS["tf"], "Origem")
    assert window.presentation_status()["source_label"] == "fresh-input-B"
    assert window.presentation_snapshot() == (latest, latest_net)


def test_absent_certified_bits_and_observation_counts_do_not_gain_zero_defaults(gui):
    snap = snapshot(modes=("tf",))
    session = snap.live.technologies["sessions"][SESSION_IDS["tf"]]
    session["metrics"].pop("certified_secret_bits")
    session["counters"].pop("observations")
    window, _net = open_window(gui, snap)
    keys = field(window, SESSION_IDS["tf"], "Chaves · sessão")
    reception = field(window, SESSION_IDS["tf"], "Recepção clássica")
    assert "bits certificados —" in keys and "bits certificados 0" not in keys
    assert "— observações" in reception and "0 observações" not in reception
    assert "19.75" in reception


def test_partial_protocol_publication_is_not_a_legacy_wave_source(gui):
    snap = snapshot(modes=("tf",), received=False, session_status="configured")
    snap.live.technologies["sessions"] = {}
    window, _net = open_window(gui, snap)
    text = window.protocols.texts()
    assert "Registro da sessão não publicado" in str(text)
    assert "no input" in str(text)
    assert not window.chart.isVisible() and window.chart.keys() == []
    assert not window.chart._received and not snap.live.links[0].wave


def test_observation_at_zero_is_a_valid_receipt_with_published_session_origin(gui):
    snap = snapshot(modes=("tf",), tick=0, origin="measured")
    session = snap.live.technologies["sessions"][SESSION_IDS["tf"]]
    session["metrics"]["last_observed_at_s"] = 0.
    session["metrics"]["observation_origin"] = "measurement_file"
    session["metrics"]["observation_source_id"] = "identified-record-000"
    session["counters"]["observations"] = 1
    window, _net = open_window(gui, snap)
    reception = field(window, SESSION_IDS["tf"], "Recepção clássica")
    assert "1 observações" in reception and "última 0.000000 s" in reception
    assert "aguard" not in reception.lower()
    assert "measurement_file" in field(window, SESSION_IDS["tf"], "Origem")
    assert "identified-record-000" in field(window, SESSION_IDS["tf"], "Origem")
    assert f"{SESSION_IDS['tf']} · TF-QKD" in window.protocols.texts()
    assert window.presentation_status()["source_kind"] == "measured"
    assert not window.chart.isVisible() and not snap.live.links[0].wave


def test_provenance_association_uses_existing_session_when_link_ids_are_absent(gui):
    snap = snapshot(modes=("tf",))
    snap.live.technologies["sessions"][SESSION_IDS["tf"]]["config"].pop("link_ids")
    window, _net = open_window(gui, snap)
    text = window.protocols.texts()
    assert snap.live.links[0].id in field(window, SESSION_IDS["tf"], "Enlaces locais")
    assert not any(key.endswith(" · Protocolo") for key in text)
    assert "Registro da sessão não publicado" not in str(text)
    assert "7" in field(window, SESSION_IDS["tf"], "Recepção clássica")
    assert not window.chart.isVisible() and window.chart.keys() == []


def test_incremental_protocol_section_participates_in_readiness_and_coalescing(gui):
    snap = snapshot(modes=("tf", "memory"))
    window, _net = open_window(gui, snap, incremental=True)
    status = window.presentation_status()
    assert not status["ready"] and "protocols" in status["expected_sections"]
    assert "protocols" not in status["completed_sections"]
    assert window.presentation_snapshot() is None
    latest = snapshot(modes=("tf", "memory"), tick=24)
    latest.live.technologies["sessions"][SESSION_IDS["tf"]]["counters"]["observations"] = 17
    latest_net = build_net_model(latest)
    window.refresh(latest, latest_net, CommandStatus())
    assert not window.presentation_status()["ready"] and window.presentation_snapshot() is None
    wait_ready(window)
    reference = NodeWindow(NODE_ID, gui[1]); reference.refresh(latest, latest_net, CommandStatus())
    assert window.protocols.texts() == reference.protocols.texts()
    assert "17" in field(window, SESSION_IDS["tf"], "Recepção clássica")
    assert "23.75" in field(window, SESSION_IDS["tf"], "Recepção clássica")
    status = window.presentation_status()
    assert "protocols" in status["completed_sections"] and status["pending_core_rows"] == 0
    assert status["presented_tick"] == 24 and window.presentation_snapshot() == (latest, latest_net)
    assert window.content_texts()["protocols"] == reference.protocols.texts()
    assert all(not link.wave for link in latest.live.links)
