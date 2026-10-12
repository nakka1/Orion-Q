import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

import pytest

from quantum_autopilot.lab.processing_node import NodeProcessingError, NodeProcessingPipeline, ProcessingNodeConfig
from dataclasses import replace

from quantum_autopilot.lab.pspl import CorrelationRequest, DMAConfig, PSPLStateError, TimestampBlock, TimestampEvent
from quantum_autopilot.service.pl_gateway_cli import PLGatewayConfig, load_config, main, registry_for_gateway, software_image_digest


LINUX = platform.system() == "Linux" and hasattr(os, "geteuid") and os.geteuid() != 0


def value(tmp_path, mode="emulated-pl-external", generation=1):
    return {"schema": "orion.pl-gateway.v1", "socket_path": str(tmp_path / "worker.sock"),
            "state_directory": str(tmp_path / "state"),
            "backend": {"backend_id": "reference-gateway", "mode": mode, "generation": generation,
                        "image_sha256": software_image_digest()["image_sha256"], "max_events": 256},
            "allowed_uid": os.geteuid() if hasattr(os, "geteuid") else 10001, "request_timeout_s": 1}


@pytest.mark.parametrize("patch", [{"schema": "unknown"}, {"allowed_uid": True},
    {"request_timeout_s": True}, {"request_timeout_s": float("nan")}, {"request_timeout_s": 61},
    {"socket_path": "relative.sock"}, {"state_directory": "../state"}, {"plugin": "malicious"}])
def test_strict_gateway_configuration(tmp_path, patch):
    with pytest.raises(ValueError):
        PLGatewayConfig.parse({**value(tmp_path), **patch})


def test_config_duplicate_fields_and_symlink_rejected(tmp_path):
    config = tmp_path / "gateway.json"
    config.write_text('{"schema":"x","schema":"y"}')
    with pytest.raises(ValueError):
        load_config(config)
    if os.name == "posix":
        alias = tmp_path / "alias"
        alias.symlink_to(config)
        with pytest.raises(ValueError):
            load_config(alias)


def test_describe_does_not_claim_an_fpga(capsys):
    assert main(["describe"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["image_sha256"]) == 64 and len(result["components"]) == 4
    assert not result["hardware_validated"] and result["physical_actuation_locked"]
    assert result["available_modes"] == ["cpu-external", "emulated-pl-external"]


@pytest.mark.skipif(not LINUX, reason="kernel peer credentials and non-root Linux required")
@pytest.mark.parametrize("mode", ["cpu-external", "emulated-pl-external"])
def test_real_cli_gateway_pipeline_persistence_cancel_and_conservative_restart(tmp_path, mode):
    # Keep sockaddr_un below 108 bytes on deeply nested pytest work directories.
    with tempfile.TemporaryDirectory(prefix="oq-pl-", dir="/var/tmp") as directory:
        root = Path(directory)
        settings = value(root, mode)
        Path(settings["state_directory"]).mkdir(mode=0o700)
        config_path = root / "gateway.json"
        config_path.write_text(json.dumps(settings))
        config = load_config(config_path)
        assert config.check()["status"] == "PASS"
        command = [sys.executable, "-I", "-m", "quantum_autopilot.service.pl_gateway_cli",
                   "--config", str(config_path), "serve"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        records = []
        try:
            until = time.monotonic() + 10
            while not config.socket_path.exists() and process.poll() is None and time.monotonic() < until:
                time.sleep(.02)
            assert config.socket_path.is_socket(), process.communicate(timeout=2)
            block = TimestampBlock("session-1", 0, "clock", "config", 2**53, 100,
                (TimestampEvent(2**53+2, -1), TimestampEvent(2**53+4, 2)),
                "simulated", "same-ingress", "fixture")
            node = ProcessingNodeConfig("N01", block.session_id, block.clock_domain, block.configuration_id,
                CorrelationRequest(-1, 2, bin_width_ps=1, radius_ps=5), "external-pl",
                DMAConfig(max_events_per_block=256))
            pipeline = NodeProcessingPipeline(node, registry=registry_for_gateway(config), on_record=records.append)
            receipt = pipeline.process(block, now_ps=0)
            assert receipt["accepted"] and receipt["metrics"]["coincidence_pairs"] == 1
            assert receipt["processing"]["mode"] == mode
            exchange = receipt["processing"]["accelerator_transport"]
            assert exchange["peer"]["uid"] == os.geteuid() and exchange["last_exchange"]["accepted"]
            assert records[0]["block_wire_hex"] == block.to_wire().hex()
            assert not receipt["transport"]["hardware_validated"] and receipt["physical_actuation_locked"]
            pipeline.cancel()
            assert pipeline.worker.snapshot()["state"] == "uncertain"
            with pytest.raises(NodeProcessingError):
                pipeline.process(block, now_ps=1)
            with pytest.raises(PSPLStateError, match="newer"):
                pipeline.recover(pipeline.snapshot()["dma"]["configuration_readback"], new_session_id="session-2",
                                 replacement_registry=registry_for_gateway(config))
        finally:
            process.terminate()
            out, err = process.communicate(timeout=5)
        assert process.returncode == 0, out + err
        assert not config.socket_path.exists()
        # Reusing the same generation after restart must not clear the replay fence.
        restart = subprocess.run(command, capture_output=True, text=True, timeout=5)
        assert restart.returncode != 0
        assert not config.socket_path.exists()
        settings["backend"]["generation"] = 2
        config_path.write_text(json.dumps(settings))
        config = load_config(config_path)
        replacement = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            until = time.monotonic() + 10
            while not config.socket_path.exists() and replacement.poll() is None and time.monotonic() < until:
                time.sleep(.02)
            assert config.socket_path.is_socket()
            pipeline.recover(pipeline.snapshot()["dma"]["configuration_readback"], new_session_id="session-2",
                             replacement_registry=registry_for_gateway(config))
            restored = pipeline.process(replace(block, session_id="session-2"), now_ps=2)
            assert restored["accepted"] and restored["processing"]["accelerator_transport"]["backend"]["generation"] == 2
            pipeline.worker.close()
        finally:
            replacement.terminate()
            out, err = replacement.communicate(timeout=5)
        assert replacement.returncode == 0, out + err


@pytest.mark.skipif(not LINUX, reason="non-root Linux configuration qualification")
@pytest.mark.parametrize("mode,digest", [("linux-pl-driver", None), ("cpu-external", "0" * 64)])
def test_unavailable_driver_and_mismatched_image_never_start(tmp_path, mode, digest):
    settings = value(tmp_path, mode)
    if digest is not None:
        settings["backend"]["image_sha256"] = digest
    with pytest.raises(ValueError):
        PLGatewayConfig.parse(settings).check()
