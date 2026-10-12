"""Regressions from the design2 review, using the actual application service."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from quantum_autopilot.console.screens import SCREENS  # noqa: E402
from quantum_autopilot.console.service_provider import ServiceProvider  # noqa: E402
from quantum_autopilot.control_plane.physical import PhysicalAuthorization, authorize_physical_action  # noqa: E402
from quantum_autopilot.control_plane.journal import ControlJournal  # noqa: E402
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def provider(tmp_path):
    return ServiceProvider(
        AssuranceDashboardService(repository_root=ROOT, control_journal_path=tmp_path / "journal.sqlite"),
        state_root=tmp_path, replay_limit=3)


def screen(key):
    return next(cls for cls in SCREENS if cls.key == key)()


def test_empty_service_clears_demo_values_and_previous_widgets(app, provider):
    from quantum_autopilot.console.providers import MockProvider
    fixture = MockProvider().snapshot()
    empty = provider.build()
    views = {key: screen(key) for key in ("telemetry", "node", "audit", "keys")}
    for view in views.values():
        view.apply(fixture)
        view.apply(empty)
    # The idle engine publishes its fibres without observables: series exist, no point is drawn.
    assert all(points == [] for _label, points in views["telemetry"].chart.plotted())
    assert [label for label, _points in views["telemetry"].chart.plotted()] == [link.id for link in empty.live.links][:3]
    assert {card.value.text() for card in views["telemetry"].live_cards} == {"—"}
    assert [row["id"] for row in views["node"]._base_nodes] == [node.id for node in empty.topology.nodes]
    assert views["node"]._shadow_nodes == []
    assert list(views["node"].rows) == [node.id for node in empty.topology.nodes]
    assert views["audit"]._entries == []
    assert views["audit"].entry_scroll.model().rowCount() == 0
    assert "No entries" in views["audit"].entry_empty.text()
    assert not views["audit"].entry_empty.isHidden()
    assert views["keys"].consumers.meters == [] and not views["keys"].consumers_empty.isHidden()
    assert views["keys"].session_scroll.model().rowCount() == len(empty.keys.sessions)
    shown = [label.text() for view in views.values() for label in view.findChildren(type(views["audit"].entry_empty))]
    assert not any(fixture.operator in text or fixture.campaign in text for text in shown)
    assert len(views["telemetry"].card_hosts) == 1


def test_real_model_metrics_keep_correct_titles_units_and_count(app, provider, tmp_path):
    from tests.test_wdm_model_bundle import _export as export_test_bundle

    bundle, exported = export_test_bundle(tmp_path)
    provider.service.wdm_model_bundle_path = bundle
    provider.service.expected_wdm_bundle_sha256 = exported["bundle_sha256"]
    snap = provider.build()
    view = screen("telemetry")
    view.apply(snap)
    assert [v.text() for v in view.card_titles] == [m.label for m in snap.telemetry.headline]
    assert [v[0].text() for v in view.card_hosts] == [m.value for m in snap.telemetry.headline]
    assert len(view.card_hosts) == 4
    assert view.chart.series
    assert "QBER" not in [v.text() for v in view.card_titles]


def test_graph_and_node_selection_use_actual_service_ids(app, provider):
    snap = provider.build()
    view = screen("topology")
    view.apply(snap)
    view.show()
    app.processEvents()
    ids = ["alice", "repeater-a", "bob"]
    # GEO is the default view; the service's nodes have no declared location, so they wait in
    # the queue — and the matrix and the logical graph show the same identifiers.
    assert view.view == "GEO" and view.queue.node_ids() == ids and view.geo.placed_ids() == []
    assert view.matrix.node_ids() == ids
    view.set_view("GRAPH")
    app.processEvents()
    assert [node_id for _, node_id in view.graph._boxes] == ids
    # the header names the topology and the input the backend reports (none is configured here)
    assert snap.live is not None and snap.live.source_kind == "none"
    assert view.ring_label.text() == f"{snap.topology.ring} · no input feed".upper()
    provider.select_node("repeater-a")
    assert provider.snapshot().node.id == "repeater-a"
    with pytest.raises(ValueError, match="node unavailable"):
        provider.select_node("R-07")
    view.close()


def test_qkd_mapping_uses_a_verified_published_cell(app, provider):
    snapshot = provider.build()
    values = {v.key: v.value for v in snapshot.keys.finite_key}
    assert values["QBER REPORTED"] != "—"
    assert values["SKR"] != "—"
    assert values["DECISION"] != "—"
    view = screen("keys")
    view.apply(snapshot)
    view.show()
    app.processEvents()
    positions = []
    table = view.session_scroll
    model = table.model()
    assert model.rowCount() == len(snapshot.keys.sessions)
    for row_index in range(model.rowCount()):
        positions.append([table.visualRect(model.index(row_index, i)).x() for i in range(model.columnCount())])
    assert len(positions) > 1 and all(row == positions[0] for row in positions)
    view.close()


def test_commands_persist_and_exports_do_not_overwrite(provider):
    provider.run_scenario()
    assert provider.service.control_plane_topology()["persisted"]
    provider.acknowledge("repeater-a")
    provider.export_record("audit")
    provider.export_record("audit")
    exports = list(provider.state_root.glob("console_export_*.json"))
    assert len(exports) == 2
    assert json.loads(exports[0].read_text())["format"] == "unsigned_console_evidence_export_v1"
    fresh = ControlJournal(provider.service.control_journal_path)
    assert sum(e["kind"] == "console_exported" for e in fresh.list_recent_safety_events()) == 2
    provider.select_node("alice")
    assert all(e["node_id"] != "alice" for e in provider.service.control_plane_events()["events"])
    assert provider.snapshot().node.log == []


def test_initialization_does_not_block_the_qt_thread(app, provider, monkeypatch):
    original = provider.build
    def slow_build():
        time.sleep(0.2)
        return original()
    monkeypatch.setattr(provider, "build", slow_build)
    received = []
    provider.snapshot_changed.connect(received.append)
    start = time.monotonic()
    provider.start()
    try:
        assert time.monotonic() - start < 0.15
        deadline = time.monotonic() + 15
        while not received and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert received
    finally:
        provider.stop()
        app.processEvents()


def test_authorization_time_and_revocation_are_checked_across_instances(tmp_path):
    now = datetime.now(timezone.utc)
    future = PhysicalAuthorization("a", "b", (now + timedelta(hours=1)).isoformat(),
                                   (now + timedelta(hours=2)).isoformat())
    assert not future.active(now)
    with pytest.raises(ValueError, match="timezone"):
        PhysicalAuthorization("a", "b", "2026-09-09T00:00:00", "2026-09-09T01:00:00")
    journal = ControlJournal(tmp_path / "auth.sqlite")
    auth = authorize_physical_action("a", "b", journal=journal)
    auth.revoke(revoked_by="b")
    assert not auth.active()
    restored = PhysicalAuthorization("a", "b", auth.authorized_at_utc, auth.expires_at_utc,
                                    authorization_id=auth.authorization_id)
    # Clear the process-local cache to prove that the journal supplies revocation.
    PhysicalAuthorization._revoked_ids.discard(auth.authorization_id)
    assert not restored.active(journal=ControlJournal(journal.path))
