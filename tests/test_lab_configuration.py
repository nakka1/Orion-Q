"""Validated API/forms and per-experiment dependency gates."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread

import pytest

from quantum_autopilot.lab.configuration import LaboratoryConfigurationStore
from quantum_autopilot.lab.service import LaboratoryService


@pytest.mark.parametrize("missing", ["numpy", "scipy", "cryptography"])
def test_missing_dependencies_gate_only_affected_experiments(tmp_path, monkeypatch, missing):
    from quantum_autopilot.lab import configuration
    monkeypatch.setattr(configuration, "module_available", lambda name: name != missing)
    service = LaboratoryService(tmp_path / "lab")
    availability = service.capabilities()["availability"]["experiments"]
    assert availability["photon"]["available"]
    assert not availability["qkd"]["available"] and not availability["all"]["available"]
    with pytest.raises(ValueError, match="dependencies_unavailable"):
        service.run({"experiment": "qkd"})
    assert not service.root.exists()


@pytest.mark.parametrize("missing", ["numpy", "scipy", "cryptography"])
def test_actual_import_blocked_environment_still_reports_availability(tmp_path, missing):
    program = '''
import importlib.abc, sys, json
class AbsentDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] == sys.argv[2]:
            raise ImportError('removed optional experiment dependency')
sys.meta_path.insert(0, AbsentDependencies())
from quantum_autopilot.lab.service import LaboratoryService
service=LaboratoryService(sys.argv[1])
value=service.capabilities()['availability']['experiments']
assert value['photon']['available'] and not value['qkd']['available']
assert service.configuration_store.state()['revision']==0
try:
    service.run({'experiment':'qkd'})
except ValueError as error:
    assert 'dependencies_unavailable' in str(error)
else:
    raise AssertionError('missing dependencies permitted operation')
print(json.dumps(value))
'''
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path / "absent"), missing],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("payload", [
    {"photon": {"parameters": {"trigger_levels_v": [.35, float("nan")]}}},
    {"photon": {"parameters": {"channels": [1, 1]}}},
    {"photon": {"parameters": {"channels": None}}},
    {"photon": {"parameters": {"duration_ps": True}}},
    {"photon": {"parameters": {"invented": 42}}},
    {"photon": {"adapter_id": "swabian-native"}},
    {"qkd": {"parameters": {"detector_efficiency": 1.5}}},
    {"qkd": {"parameters": {"bit_error_probability": .6}}},
    {"qkd": {"parameters": {"emitted_pulses": True}}},
    {"qkd": {"parameters": {"channel_loss_db": 4}}, "output": "../escape"},
])
def test_configuration_rejected_before_any_journal_or_config_change(tmp_path, payload):
    store = LaboratoryConfigurationStore(tmp_path / "lab")
    with pytest.raises(ValueError):
        store.configure(payload)
    assert not store.path.parent.exists()


def test_versioned_config_applied_and_bound_to_run(tmp_path, monkeypatch):
    from quantum_autopilot.lab import photon
    received = []
    def execute(folder, *, config, adapter_id, security=None):
        received.append((config.seed, config.trigger_levels_v, adapter_id))
        return {"passed": True}
    monkeypatch.setattr(photon, "run_photon_demo", execute)
    service = LaboratoryService(tmp_path)
    result = service.configure({"expected_revision": 0,
        "photon": {"adapter_id": "emulated-reference", "parameters": {"seed": 42, "trigger_levels_v": [.5, .6]}}})
    assert result["revision"] == 1
    record = service.run({"experiment": "photon"})
    assert received == [(42, (.5, .6), "emulated-reference")]
    assert record["configuration_revision"] == 1
    assert record["configuration_sha256"] == result["sha256"]
    with pytest.raises(ValueError, match="revision_conflict"):
        service.configure({"expected_revision": 0, "qkd": {"parameters": {"seed": 81}}})
    assert service.configuration_store.state()["revision"] == 1


def test_http_validates_and_persists_config_without_acquiring(tmp_path):
    from http.server import ThreadingHTTPServer
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService, make_handler
    root = Path(__file__).resolve().parents[1]
    service = AssuranceDashboardService(root, control_journal_path=tmp_path / "journal.sqlite")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=10)
    authentication = {"Authorization": "Bearer " + service.security.issue_local_session()}
    try:
        connection.request("POST", "/api/v1/laboratory/configuration", json.dumps({"photon": {"parameters": {"seed": 64}}}),
                           headers={"Content-Type": "application/json", **authentication})
        response = connection.getresponse()
        configured = json.loads(response.read())
        assert response.status == 200 and configured["revision"] == 1
        assert service.laboratory.history()["runs"] == []
        connection.request("POST", "/api/v1/laboratory/configuration", '{"qkd":{},"qkd":{}}',
                           headers={"Content-Type": "application/json", **authentication})
        response = connection.getresponse()
        assert response.status == 400 and json.loads(response.read()) == {"error": "invalid_json"}
        connection.request("GET", "/api/v1/laboratory", headers=authentication)
        response = connection.getresponse()
        assert json.loads(response.read())["configuration"]["configuration"]["photon"]["parameters"]["seed"] == 64
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(5)


def test_form_request_and_per_experiment_button_gates(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.console.widgets.laboratory_configuration import LaboratoryConfigurationDialog
    from quantum_autopilot.console.widgets.laboratory import LaboratoryPanel
    from quantum_autopilot.console.service_provider import ServiceProvider
    from quantum_autopilot.lab import configuration
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
    app = QApplication.instance() or QApplication([])
    state = LaboratoryConfigurationStore(tmp_path).state()
    dialog = LaboratoryConfigurationDialog(state)
    dialog.controls[("photon", "seed")].setValue(91)
    dialog.controls[("qkd", "channel_loss_db")].setValue(20)
    request = dialog.request()
    assert request["photon"]["parameters"]["seed"] == 91
    assert request["qkd"]["parameters"]["channel_loss_db"] == 20
    LaboratoryConfigurationStore(tmp_path).configure(request)
    monkeypatch.setattr(configuration, "module_available", lambda name: name != "cryptography")
    service = AssuranceDashboardService(Path(__file__).resolve().parents[1], control_journal_path=tmp_path / "journal.sqlite")
    provider = ServiceProvider(service, state_root=tmp_path)
    panel = LaboratoryPanel()
    try:
        panel.set_snapshot(provider.build())
        assert panel.buttons["photon"].isEnabled()
        assert not panel.buttons["qkd"].isEnabled() and not panel.buttons["all"].isEnabled()
        assert "cryptography" in panel.buttons["qkd"].toolTip()
    finally:
        panel.close()
        dialog.close()
        provider.stop()
        app.processEvents()


def test_service_selects_distinct_replay_adapter_and_preserves_configuration(tmp_path):
    service = LaboratoryService(tmp_path)
    source = service.run({"experiment": "photon"})
    config = source["results"]["photon"]["configuration"]
    service.configure({"photon": {"adapter_id": "orion-replay", "replay_run_id": source["run_id"], "parameters": config}})
    replay = service.run({"experiment": "photon"})["results"]["photon"]
    assert replay["passed"]
    assert replay["origin"] == "simulated" and replay["mode"] == "replay"
    assert replay["nominal"]["counts"] == source["results"]["photon"]["nominal"]["counts"]
    assert replay["nominal"]["coincidences"] == source["results"]["photon"]["nominal"]["coincidences"]
    previous = service.configuration_store.state()
    with pytest.raises(ValueError, match="original_experimental_configuration"):
        service.configure({"photon": {"parameters": {"seed": 92}}})
    assert service.configuration_store.state()["sha256"] == previous["sha256"]


def test_registry_replay_selector_locks_original_controls(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.console.widgets.laboratory_configuration import LaboratoryConfigurationDialog
    app = QApplication.instance() or QApplication([])
    state = LaboratoryConfigurationStore(tmp_path).state()
    parameters = dict(state["configuration"]["photon"]["parameters"])
    parameters["seed"] = 99
    run_id = "c6699e34-eae5-47da-ac3a-7887a41a4d92"
    state["replay_candidates"] = [{"run_id": run_id, "parameters": parameters}]
    dialog = LaboratoryConfigurationDialog(state)
    try:
        dialog.adapter.setCurrentIndex(dialog.adapter.findData("replay:" + run_id))
        assert not dialog.controls[("photon", "seed")].isEnabled()
        assert dialog.controls[("photon", "seed")].value() == 99
        assert dialog.request()["photon"]["replay_run_id"] == run_id
        dialog.adapter.setCurrentIndex(0)
        assert dialog.controls[("photon", "seed")].isEnabled()
        assert dialog.controls[("photon", "seed")].value() == 810
    finally:
        dialog.close()
        app.processEvents()


def test_form_replay_renders_exact_original_units_seed64_channels_and_restores_emulated_draft(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.console.widgets.laboratory_configuration import LaboratoryConfigurationDialog
    app = QApplication.instance() or QApplication([])
    state = LaboratoryConfigurationStore(tmp_path).state()
    parameters = dict(state["configuration"]["photon"]["parameters"])
    parameters.update(seed=2**40, duration_ps=100000, coincidence_window_ps=2000000, channels=[3, 4],
                      trigger_levels_v=[.123456789, .234567891])
    run_id = "01ac5e37-2df8-4fbb-826e-32facba3f4c8"
    state["replay_candidates"] = [{"run_id": run_id, "parameters": parameters}]
    state["revision"] = 17
    dialog = LaboratoryConfigurationDialog(state)
    try:
        dialog.controls[("photon", "seed")].setValue(97)
        dialog.adapter.setCurrentIndex(dialog.adapter.findData("replay:" + run_id))
        assert dialog.readonly_values[("photon", "seed")].text() == str(2**40)
        assert dialog.readonly_values[("photon", "duration_ps")].text() == "0.0000001 s · 100000 ps"
        assert dialog.readonly_values[("photon", "coincidence_window_ps")].text() == "2000000 ps"
        assert dialog.readonly_values[("photon", "trigger_0")].text() == "0.123456789 V"
        assert dialog.field_labels[("photon", "trigger_0")].text() == "Limiar canal 3 (V)"
        assert dialog.field_labels[("photon", "trigger_1")].text() == "Limiar canal 4 (V)"
        for key, label in dialog.readonly_values.items():
            if key[0] == "photon":
                assert dialog.field_stacks[key].currentWidget() is label
        assert dialog.request()["photon"]["parameters"] == parameters
        assert dialog.request()["expected_revision"] == 17
        dialog.adapter.setCurrentIndex(0)
        assert dialog.controls[("photon", "seed")].value() == 97
        assert dialog.field_labels[("photon", "trigger_0")].text() == "Limiar canal 1 (V)"
    finally:
        dialog.close()
        app.processEvents()
    # Also construct an already selected replay whose seed exceeds QSpinBox's int32.
    state["configuration"]["photon"] = {"adapter_id": "orion-replay", "replay_run_id": run_id, "parameters": parameters}
    initial_replay = LaboratoryConfigurationDialog(state)
    try:
        assert initial_replay.readonly_values[("photon", "seed")].text() == str(2**40)
        assert initial_replay.request()["photon"]["parameters"] == parameters
    finally:
        initial_replay.close()
        app.processEvents()


@pytest.mark.parametrize("pulse", [.1, .75000001, 1.5])
def test_form_trigger_bounds_match_positive_pulse_and_api_rejects_boundaries(tmp_path, pulse):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.console.widgets.laboratory_configuration import LaboratoryConfigurationDialog
    app = QApplication.instance() or QApplication([])
    state = LaboratoryConfigurationStore(tmp_path).state()
    state["configuration"]["photon"]["parameters"].update(pulse_level_v=pulse, trigger_levels_v=[pulse/2, pulse/2])
    dialog = LaboratoryConfigurationDialog(state)
    try:
        for name in ("trigger_0", "trigger_1"):
            widget = dialog.controls[("photon", name)]
            assert 0 < widget.minimum() <= widget.value() <= widget.maximum() < pulse
            assert "backend" in widget.toolTip()
    finally:
        dialog.close()
        app.processEvents()
    store = LaboratoryConfigurationStore(tmp_path)
    for boundary in (0, -.1, pulse):
        with pytest.raises(ValueError):
            store.configure({"photon": {"parameters": {"pulse_level_v": pulse, "trigger_levels_v": [boundary, boundary]}}})
        assert not store.path.exists()


@pytest.mark.parametrize("trigger", [.123456789, .00001, 1e-13])
def test_emulated_advanced_config_is_exact_and_save_does_not_coerce_unrepresentable_fields(tmp_path, trigger):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.console.widgets.laboratory_configuration import LaboratoryConfigurationDialog
    app = QApplication.instance() or QApplication([])
    service = LaboratoryService(tmp_path)
    service.configure({"photon": {"parameters": {"seed": 2**40, "duration_ps": 100000,
                       "coincidence_window_ps": 2000000, "channels": [3, 4], "trigger_levels_v": [trigger, trigger]}},
                       "qkd": {"parameters": {"seed": 2**40, "acquisition_s": .00001, "detector_efficiency": .7123456789}}})
    before = service.configuration_store.state()
    dialog = LaboratoryConfigurationDialog(before)
    try:
        assert dialog.readonly_values[("photon", "seed")].text() == str(2**40)
        assert dialog.readonly_values[("photon", "duration_ps")].text() == "0.0000001 s · 100000 ps"
        assert dialog.field_labels[("photon", "trigger_0")].text() == "Limiar canal 3 (V)"
        for key in (("photon", "seed"), ("photon", "duration_ps"), ("photon", "coincidence_window_ps"),
                    ("qkd", "seed"), ("qkd", "acquisition_s"), ("qkd", "detector_efficiency")):
            assert not dialog.controls[key].isEnabled()
            assert dialog.field_stacks[key].currentWidget() is dialog.readonly_values[key]
        if trigger == 1e-13:
            assert not dialog.controls[("photon", "trigger_0")].isEnabled()
            assert dialog.readonly_values[("photon", "trigger_0")].text() == "1e-13 V"
        else:
            assert dialog.controls[("photon", "trigger_0")].isEnabled()
            assert dialog.controls[("photon", "trigger_0")].value() == trigger
        request = dialog.request()
        service.configure(request)
        after = service.configuration_store.state()
        assert after["configuration"] == before["configuration"]
        assert after["revision"] == before["revision"] + 1
        # Adapter substitution keeps the same advanced configuration unchanged.
        dialog.adapter.setCurrentIndex(1)
        updated = dialog.request()
        assert updated["photon"]["parameters"] == request["photon"]["parameters"]
    finally:
        dialog.close()
        app.processEvents()
