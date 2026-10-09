"""Device controls request the real backend and wait for persisted readback."""
from dataclasses import replace
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console.capabilities import SourceCapabilities  # noqa: E402
from quantum_autopilot.console.screens.fpga import EdgeControlScreen  # noqa: E402
from quantum_autopilot.console.service_provider import live_view_from_state  # noqa: E402
from quantum_autopilot.console.synthetic import artificial_snapshot  # noqa: E402
from quantum_autopilot.console.widgets.fpga_devices import FpgaDevicesPanel  # noqa: E402
from tests.test_fpga_devices import make_engine, register  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def snapshot(engine, *, controls=True):
    operations = ("register_fpga_device", "configure_fpga_device", "set_fpga_connection", "inject_fpga_fault")
    return replace(artificial_snapshot(), input_kind="demo",
                   live=live_view_from_state(engine.live_state().to_dict()),
                   capabilities=SourceCapabilities(adapter_id="fpga-test", mode="simulation" if controls else "read_only",
                                                  health="ready", operations=operations if controls else ()))


def test_configuration_connection_and_fault_controls_wait_for_real_readback(app, tmp_path):
    engine = make_engine(tmp_path)
    register(engine)
    panel = FpgaDevicesPanel()
    calls = []

    def apply(verb, target):
        calls.append((verb, json.loads(target)))
        engine.command(verb, json.loads(target))

    panel.command.connect(apply)
    panel.set_snapshot(snapshot(engine))
    assert "measured fpga latency: —" in panel.telemetry.text().lower()
    assert panel.table.records.rows[0][4][0] == "no_input"
    panel.clock.setValue(80)
    panel.save.click()
    assert calls[-1][1]["expected_revision"] == 1
    assert "waiting for persisted backend readback" in panel.feedback.text()
    assert panel._devices["fpga-b"]["configuration"]["revision"] == 1
    panel.set_snapshot(snapshot(engine))
    assert panel._devices["fpga-b"]["configuration"]["revision"] == 2
    assert "Persisted readback: configuration" in panel.feedback.text()
    engine.step()
    panel.set_snapshot(snapshot(engine))
    assert "Persisted readback: processing_feedback" in panel.feedback.text()
    assert "quantum_send → quantum_receive → classical_send" in panel.feedback.text()
    assert panel.table.records.rows[0][4][0] == "processed"
    panel.connect_button.click()
    engine.step()
    panel.set_snapshot(snapshot(engine))
    assert panel.table.records.rows[0][3][0] == "disconnected"
    assert panel.connect_button.text() == "Connect"
    panel.connect_button.click()
    engine.step()
    panel.set_snapshot(snapshot(engine))
    assert panel.table.records.rows[0][4][0] == "processed"
    panel.fault_kind.setCurrentIndex(panel.fault_kind.findData("invalid_payload"))
    panel.fault_duration.setValue(1)
    panel.fault_button.click()
    engine.step()
    panel.set_snapshot(snapshot(engine))
    assert panel.table.records.rows[0][4][0] == "rejected"
    assert panel.receipts.records.rowCount() > 0
    panel.set_busy(True)
    assert not panel.save.isEnabled() and not panel.connect_button.isEnabled()
    assert not panel.register_button.isEnabled() and not panel.fault_button.isEnabled()
    panel.set_busy(False)
    assert panel.save.isEnabled() and panel.connect_button.isEnabled()
    panel.close()


def test_read_only_and_physical_contract_controls_cannot_claim_connection(app, tmp_path):
    engine = make_engine(tmp_path)
    register(engine, adapter_id="physical-contract")
    engine.step()
    panel = FpgaDevicesPanel()
    panel.set_snapshot(snapshot(engine))
    assert not panel.connect_button.isEnabled()
    assert not panel.fault_button.isEnabled()
    assert panel.table.records.rows[0][3][0] == "unavailable"
    assert "No validated physical FPGA transport" in panel.telemetry.text()
    panel.set_snapshot(snapshot(engine, controls=False))
    assert not panel.save.isEnabled() and not panel.register_button.isEnabled()
    calls = []
    panel.command.connect(lambda *args: calls.append(args))
    panel.save.click()
    panel.connect_button.click()
    assert calls == []
    panel.close()
    screen = EdgeControlScreen()
    screen.apply(snapshot(engine))
    assert not screen.fpga_devices.connect_button.isEnabled()
    assert not screen.fpga_devices.fault_button.isEnabled()
    screen.commands_busy = True
    screen.apply_command_capabilities()
    assert not screen.fpga_devices.save.isEnabled()
    screen.shutdown()
    screen.close()
