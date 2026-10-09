"""Capabilities must gate both widgets and the command boundary."""
import json
import os
from dataclasses import asdict, replace
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console.capabilities import SourceCapabilities, synthetic_capabilities
from quantum_autopilot.console.input_sources import MeasuredFileProvider
from quantum_autopilot.console.providers import MockProvider
from quantum_autopilot.console.service_provider import ServiceProvider
from quantum_autopilot.console.shell import ConsoleWindow
from quantum_autopilot.console.source_switch import SwitchableProvider
from quantum_autopilot.console.synthetic import SyntheticProvider
from tests.test_console_service_provider import FakeService

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("kwargs", [
    {"mode": "physical"}, {"mode": "read_only", "operations": ("run_scenario",)},
    {"operations": ("SWAP",)}, {"operations": ("reload", "reload")},
    {"operations": ["reload"]}, {"health": "authenticated"}, {"limits": (("nodes", True),)},
])
def test_invalid_or_physical_declarations_are_rejected(kwargs):
    with pytest.raises(ValueError):
        SourceCapabilities(**kwargs)


def test_contract_is_serializable_and_deny_by_default():
    assert not SourceCapabilities().allows("run_scenario")
    encoded = json.dumps(asdict(synthetic_capabilities()))
    assert "orion.capabilities.v1" in encoded
    assert "shadow_diagnostic" not in encoded  # scopes are defined centrally, not inferred from UI text


def test_undeclared_adapter_cannot_dispatch_even_if_handler_exists(app):
    provider = MockProvider()
    plug = SwitchableProvider(provider)
    plug.start()
    try:
        with pytest.raises(ValueError, match="unavailable"):
            plug.dispatch("recalibrate", "N-01")
    finally:
        plug.stop()


def test_synthetic_refuses_fake_model_and_policy_commands(app, tmp_path):
    provider = SyntheticProvider(ROOT, tmp_path)
    before = len(provider.entries)
    for verb in ("replay_model", "set_decision_source", "isolate", "flash"):
        with pytest.raises(ValueError, match="unavailable"):
            provider.dispatch(verb, "ml:deterministic")
    assert len(provider.entries) == before
    assert provider.snapshot().capabilities.mode == "synthetic"


def test_service_declaration_uses_backend_flags_not_headline(app):
    service = FakeService()
    provider = ServiceProvider(service)
    assert provider.capabilities.allows("run_scenario")
    assert not provider.capabilities.allows("replay_model")
    with pytest.raises(ValueError, match="unavailable"):
        provider.dispatch("set_decision_source", "ml:deterministic")
    service._overrides["model"] = {"configured": True, "valid": True}
    assert not provider.capabilities.allows("replay_model")
    service._overrides["model"]["replay_available"] = True
    assert provider.capabilities.allows("replay_model")
    service._overrides["health"] = RuntimeError("offline")
    assert provider.capabilities.health == "unavailable"
    with pytest.raises(ValueError, match="unavailable"):
        provider.dispatch("run_scenario")


def test_source_switch_busy_refresh_and_headlines_cannot_unlock_commands(app, tmp_path):
    source = SyntheticProvider(ROOT, tmp_path / "demo")
    window = ConsoleWindow(source, skip_auth=True)
    window.provider.start()
    try:
        window.show_screen("topology")
        topology = window.screens["topology"]
        assert topology.recalibrate.isEnabled()
        assert not topology.isolate.isEnabled()
        window.show_screen("fpga")
        edge = window.screens["fpga"]
        # A headline, even in a service-labelled snapshot, is not a capability.
        edge.apply(replace(window._snap, input_kind="service"))
        window._on_busy(True)
        window._on_busy(False)
        assert not edge.ml.isEnabled() and not edge.deterministic.isEnabled()
        assert not edge.flash.isEnabled()
        assert not topology.isolate.isEnabled()
        assert window.switch_input(lambda: MeasuredFileProvider(tmp_path / "absent.json", tmp_path / "measured"))
        window.show_screen("topology")
        assert not topology.recalibrate.isEnabled()
        assert window._snap.capabilities.mode == "read_only"
        assert window._snap.capabilities.health == "unavailable"
        assert not window.screens["node"].provision.isEnabled()
        with pytest.raises(ValueError):
            window.provider.dispatch("run_scenario")
        assert "measured-json" in window.lab_button.toolTip()
    finally:
        window.close()


def test_stopped_or_busy_plug_refuses_commands(app, tmp_path):
    plug = SwitchableProvider(SyntheticProvider(ROOT, tmp_path))
    with pytest.raises(ValueError):
        plug.dispatch("pause")
    plug.start()
    try:
        plug.busy = True
        with pytest.raises(RuntimeError):
            plug.dispatch("pause")
        assert plug.dispatch("cancel")
    finally:
        plug.stop()
    assert plug.capabilities.mode == "unavailable"


def test_payload_cannot_grant_operations_to_undeclared_source(app):
    source = MockProvider()
    plug = SwitchableProvider(source)
    plug.start()
    try:
        source.snapshot_changed.emit(replace(source.snapshot(), capabilities=synthetic_capabilities()))
        assert not plug.snapshot().capabilities.allows("run_scenario")
    finally:
        plug.stop()


def test_measured_export_contains_read_only_declaration(app, tmp_path):
    provider = MeasuredFileProvider(tmp_path / "missing.json", tmp_path / "export")
    provider.refresh()
    provider.dispatch("export_record")
    exported = json.loads(next((tmp_path / "export").glob("*.json")).read_text())
    assert exported["snapshot"]["capabilities"]["mode"] == "read_only"
    assert "run_scenario" not in exported["snapshot"]["capabilities"]["operations"]
