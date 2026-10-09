"""ORION-Q responsive layout, reproducible injection and controller regressions."""
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.shell import ConsoleWindow  # noqa: E402
from quantum_autopilot.console.synthetic import SyntheticProvider, artificial_snapshot  # noqa: E402
from quantum_autopilot.console.stress import run_controller_stress  # noqa: E402
from quantum_autopilot.console.widgets.figures import TopologyGraph  # noqa: E402
from quantum_autopilot.product import PRODUCT_NAME  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.install(instance)
    return instance


PROTOTYPE_TOKENS = {
    "dark": {"bg": "#141413", "raised": "#1a1a18", "panel": "#1e1d1b", "deep": "#121110", "ink": "#faf9f5",
             "ink2": "#b0aea5", "ink3": "#8f8e87", "ink4": "#5e5d59", "acc": "#d97757", "acc-t": "#e08a6c",
             "line": "rgba(250,249,245,.16)", "soft": "rgba(250,249,245,.09)", "hover": "rgba(250,249,245,.05)",
             "sel": "rgba(217,119,87,.13)", "calm": "rgba(250,249,245,.36)", "green": "#7fb79a",
             "green-t": "#8fc4a8", "red": "#c05a6c", "red-t": "#e08a99", "violet": "#8b81c4",
             "violet-t": "#a59ce0", "shadow": "0 20px 60px rgba(0,0,0,.5)", "water": "#15181a",
             "land": "#201f1c", "road": "rgba(250,249,245,.11)"},
    "light": {"bg": "#faf9f5", "raised": "#f1efe8", "panel": "#ece9e0", "deep": "#f4f2ec", "ink": "#141413",
              "ink2": "#4c4b45", "ink3": "#5f5e58", "ink4": "#a8a69d", "acc": "#c15f3c", "acc-t": "#a94e2f",
              "line": "rgba(20,20,19,.22)", "soft": "rgba(20,20,19,.11)", "hover": "rgba(20,20,19,.045)",
              "sel": "rgba(193,95,60,.12)", "calm": "rgba(20,20,19,.38)", "green": "#4f8a6b",
              "green-t": "#3f7559", "red": "#b04459", "red-t": "#a23a4f", "violet": "#6a5fb0",
              "violet-t": "#5a50a0", "shadow": "0 20px 50px rgba(20,20,19,.18)", "water": "#e4e9ec",
              "land": "#f6f4ee", "road": "rgba(20,20,19,.10)"},
}


def test_exact_prototype_tokens_both_themes_and_bundled_font_bytes(app):
    assert theme.TOKENS == PROTOTYPE_TOKENS
    assert theme.current_theme() == "dark"          # dark is the default
    try:
        for name, tokens in PROTOTYPE_TOKENS.items():
            theme.set_theme(name)
            assert {key: str(getattr(theme.C, key.replace("-", "_"))) for key in tokens} == tokens
            # Older names and the semantic tones resolve onto the new tokens.
            assert (str(theme.C.void), str(theme.C.amber), str(theme.C.muted), str(theme.C.dim), str(theme.C.line2)) == (
                tokens["bg"], tokens["acc"], tokens["ink2"], tokens["ink3"], tokens["soft"])
            assert [str(theme.tone(n)) for n in ("ink", "muted", "dim", "amber", "green", "red", "violet")] == [
                tokens[k] for k in ("ink", "ink2", "ink3", "acc", "green", "red", "violet")]
            assert str(theme.text_tone("amber")) == tokens["acc-t"]
    finally:
        theme.set_theme("dark")
    stale = theme.C.acc                             # captured before a switch, formats for the active theme
    theme.set_theme("light")
    try:
        assert f"{stale}" == "#c15f3c" and theme.qc(stale).name() == "#c15f3c" and stale == theme.C.amber
    finally:
        theme.set_theme("dark")
    with pytest.raises(ValueError):
        theme.set_theme("sepia")
    assert [theme.family(k) for k in ("mono", "text")] == ["JetBrains Mono", "Inter"]
    # Chakra Petch, Oswald and Noto Sans JP are no longer used by any font kind.
    assert {theme.family(k) for k in ("industrial", "display", "jp")} == {"Inter"}
    assert theme.text().pixelSize() == 14 and theme.label_font().pixelSize() == 11
    assert theme.button_font().pixelSize() == 12
    manifest = json.loads((theme.FONT_DIR / "manifest.json").read_text())
    for item in manifest["fonts"]:
        assert hashlib.sha256((theme.FONT_DIR / item["file"]).read_bytes()).hexdigest() == item["sha256"]


@pytest.mark.parametrize("size", [(1280, 800), (1920, 1080), (3440, 1440), (1024, 768)])
def test_real_window_fills_viewport_without_letterboxing(app, tmp_path, size):
    provider = SyntheticProvider(ROOT, tmp_path)
    window = ConsoleWindow(provider, skip_auth=True)
    window._on_snapshot(provider.snapshot())
    window.resize(*size)
    window.show()
    try:
        for _ in range(5):
            app.processEvents()
        stage = window.centralWidget()
        scale = stage.transform().m11()
        assert scale >= 1
        assert stage.sceneRect().width() * scale >= stage.viewport().width() - 2
        assert stage.sceneRect().height() * scale >= stage.viewport().height() - 2
        if stage.sceneRect().width() * scale > stage.viewport().width() + 2:
            assert stage.horizontalScrollBar().maximum() > 0
        if stage.sceneRect().height() * scale > stage.viewport().height() + 2:
            assert stage.verticalScrollBar().maximum() > 0
        assert window.windowTitle().startswith(PRODUCT_NAME + " ")
    finally:
        window.close()


def test_seeded_injection_is_explicit_reproducible_and_clears_on_loss():
    a, b = artificial_snapshot("load", seed=42), artificial_snapshot("load", seed=42)
    assert asdict(a) == asdict(b)
    assert asdict(a) != asdict(artificial_snapshot("load", seed=43))
    assert len(a.topology.nodes) == 32 and len(a.topology.links) == 256
    assert all(m.provenance == "simulated" for m in a.telemetry.headline)
    assert "SYNTHETIC" in a.operator
    missing = artificial_snapshot("load", dropout=True)
    assert not missing.topology.links and not missing.telemetry.fidelity.series
    assert not missing.node.registers and not missing.keys.sessions and not missing.ledger.entries


def test_last_link_remains_reachable_and_has_untruncated_tooltip(app, tmp_path):
    provider = SyntheticProvider(ROOT, tmp_path, profile="load")
    window = ConsoleWindow(provider, skip_auth=True)
    window._on_snapshot(provider.snapshot())
    window.resize(1280, 800)
    window.show()
    try:
        app.processEvents()
        table = window.screens["topology"].link_table
        assert table.model().rowCount() == 256
        table.scrollToBottom()
        app.processEvents()
        index = table.model().index(255, 0)
        rect = table.visualRect(index)
        assert rect.top() >= 0 and rect.bottom() < table.viewport().height()
        assert table.model().data(index, Qt.ItemDataRole.ToolTipRole) == table.model().data(index)
        provider.dispatch("dropout")
        # Explicit duplicate delivery must also remain harmless.
        window._on_snapshot(provider.snapshot())
        assert table.model().rowCount() == 0
        provider.dispatch("dropout")
        window._on_snapshot(provider.snapshot())
        assert table.model().rowCount() == 256
    finally:
        window.close()


@pytest.mark.parametrize("fault", ["duplicate", "nan", "dangling"])
def test_invalid_graph_data_is_rejected_and_prior_graph_is_cleared(app, fault):
    graph = TopologyGraph()
    snap = artificial_snapshot()
    graph.set_topology(snap.topology.nodes, snap.topology.links)
    nodes, links = list(snap.topology.nodes), list(snap.topology.links)
    if fault == "duplicate":
        nodes.append(nodes[0])
    elif fault == "nan":
        nodes[0] = replace(nodes[0], x=float("nan"))
    else:
        links[0] = replace(links[0], a="missing")
    with pytest.raises(ValueError):
        graph.set_topology(nodes, links)
    assert not graph.nodes and not graph.links


def test_missing_provenance_time_does_not_break_request_idempotency(tmp_path):
    from quantum_autopilot.control_plane import ControllerRuntime, NetworkStateSnapshot
    from tests.test_control_plane_sandbox import snapshot
    payload = snapshot().to_dict()
    payload["provenance"].pop("timestamp")
    first = NetworkStateSnapshot.from_dict(payload)
    second = NetworkStateSnapshot.from_dict(payload)
    assert first.to_dict() == second.to_dict()
    assert first.provenance.timestamp == payload["timestamp_utc"]
    runtime = ControllerRuntime(tmp_path / "journal.sqlite")
    a = runtime.cycle(payload, mode="shadow")
    b = runtime.cycle(payload, mode="shadow")
    assert b["idempotent_replay"] and a["record_sha256"] == b["record_sha256"]


def test_artificial_campaign_exercises_failures_and_isolates_each_run(tmp_path):
    reports = [run_controller_stress(ROOT, tmp_path, rounds=1) for _ in range(2)]
    assert reports[0]["journal"] != reports[1]["journal"]
    for report in reports:
        assert report["passed"] and report["check_count"] >= 40
        assert report["cycle_count"] == 7
        assert not report["physical_action_performed"]
        assert all(item["passed"] for item in report["checks"])


def test_synthetic_provider_campaign_keeps_qt_thread_available(app, tmp_path, monkeypatch):
    import quantum_autopilot.console.stress as stress
    def slow(*args, **kwargs):
        time.sleep(.2)
        return {"passed": True, "report_path": "isolated-test-report"}
    monkeypatch.setattr(stress, "run_controller_stress", slow)
    provider = SyntheticProvider(ROOT, tmp_path)
    provider.start()
    try:
        started = time.monotonic()
        assert provider.dispatch("run_scenario")
        assert time.monotonic() - started < .15
        assert not provider.dispatch("run_scenario")
        deadline = time.monotonic() + 5
        while provider.last_report is None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert provider.last_report["passed"]
    finally:
        provider.stop()


def test_frozen_entrypoint_logs_unexpected_headless_failure(tmp_path, monkeypatch):
    import runpy
    import sys
    import quantum_autopilot.desktop as desktop
    def fail(_argv):
        raise RuntimeError("injected startup failure")
    monkeypatch.setattr(desktop, "main", fail)
    monkeypatch.setattr(sys, "_orion_temporal_worker_v1", False, raising=False)
    entry = runpy.run_path(str(ROOT / "packaging/console_launcher.py"))
    assert entry["launch"](["--stress-test", str(tmp_path)]) == 2
    logs = list(tmp_path.glob("orion-error-*.log"))
    assert len(logs) == 1
    assert "injected startup failure" in logs[0].read_text()
