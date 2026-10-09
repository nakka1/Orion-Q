"""The two required channel configurations are saved and read back together."""
import os
from pathlib import Path

import pytest


@pytest.fixture
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def editor(tmp_path):
    from quantum_autopilot.console.service_provider import ServiceProvider
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
    from quantum_autopilot.console.widgets.link_editor import LinkEditor
    provider = ServiceProvider(AssuranceDashboardService(repository_root=Path(__file__).resolve().parents[1],
        control_journal_path=tmp_path / "journal.sqlite"), state_root=tmp_path)
    widget = LinkEditor()
    widget.set_snapshot(provider.build())
    widget.begin()
    widget.fields["link_id"].setText("direct")
    widget.fields["left_node_id"].setCurrentText("alice")
    widget.fields["right_node_id"].setCurrentText("bob")
    widget.fields["distance_km"].setText("12")
    widget.fields["nominal_pair_fidelity"].setText(".93")
    return provider, widget


@pytest.mark.parametrize("fault", ["classical_missing", "quantum_missing", "same_id", "invalid_limit", "invalid_age"])
def test_form_refuses_incomplete_or_invalid_pairs(app, tmp_path, fault):
    provider, widget = editor(tmp_path)
    sent = []
    widget.command.connect(lambda *args: sent.append(args))
    try:
        field, text = {"classical_missing": ("classical_id", ""), "quantum_missing": ("quantum_id", ""),
                       "same_id": ("quantum_id", "direct.classical"), "invalid_limit": ("max_message_bytes", "0"),
                       "invalid_age": ("max_age_s", "nan")}[fault]
        widget.channel_fields[field].setText(text)
        widget.submit()
        assert not sent and widget.pending is None and "REFUSED" in widget.note.text()
    finally:
        widget.close()
        widget.deleteLater()
        provider.stop()


def test_pair_configuration_is_confirmed_only_after_exact_readback(app, tmp_path):
    provider, widget = editor(tmp_path)
    sent = []
    widget.command.connect(lambda *args: sent.append(args))
    provider.snapshot_changed.connect(widget.set_snapshot)
    try:
        widget.channel_fields["classical_id"].setText("receiver-control")
        widget.channel_fields["quantum_id"].setText("optical-statistics")
        widget.channel_fields["max_message_bytes"].setText("262144")
        widget.submit()
        assert widget.pending is not None
        provider.dispatch(*sent.pop())
        link = next(item for item in provider.snapshot().topology.links if item.id == "direct")
        assert widget.pending is None and "CONFIRMED" in widget.note.text()
        assert link.channels["association_id"] == "direct"
        assert link.channels["classical"]["channel_id"] == "receiver-control"
        assert link.channels["quantum"]["channel_id"] == "optical-statistics"
        assert link.channels["quantum"]["receiver_id"] == "bob"
        assert link.channels["classical"]["max_message_bytes"] == 262144
        assert widget.content_scroll is not None and widget.note.parentWidget() != widget.content_scroll.widget()
    finally:
        widget.close()
        widget.deleteLater()
        provider.stop()
