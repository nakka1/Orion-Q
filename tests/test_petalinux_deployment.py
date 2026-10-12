"""Host checks of embedded staging and fail-closed prerequisites, not an ARM build."""
from __future__ import annotations

import configparser
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import tomllib
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAYER = ROOT / "packaging/petalinux/meta-orionq"
CORE_FILES = LAYER / "recipes-orionq/orionq-core/files"
GATEWAY_FILES = LAYER / "recipes-orionq/orionq-pl-gateway/files"


def load(relative):
    spec = importlib.util.spec_from_file_location("orion_petalinux_" + Path(relative).stem.replace("-", "_"), ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def staged(tmp_path_factory):
    destination = tmp_path_factory.mktemp("petalinux-host") / "meta-orionq"
    original = (ROOT / "pyproject.toml").read_bytes()
    source_tool = (ROOT / "packaging/core/prepare_source.py").read_bytes()
    report = load("packaging/petalinux/prepare_layer.py").prepare(ROOT, destination)
    assert (ROOT / "pyproject.toml").read_bytes() == original
    assert (ROOT / "packaging/core/prepare_source.py").read_bytes() == source_tool
    return destination, report


def read_archive(staged):
    destination, report = staged
    archive = destination / f"recipes-orionq/orionq-core/files/orionq-core-{report['version']}.tar.gz"
    return archive, tarfile.open(archive)


def sample_runtime():
    configuration = json.loads((ROOT / "packaging/petalinux/examples/platform-emulated.json").read_text())
    configuration["architecture"] = "x86_64"
    requirements = ["cryptography>=43,<51", "numpy>=1.26,<3", "networkx>=3.0,<4"]
    artifact = {"schema": "orion.core.artifact.v1", "profile": "headless-service",
                "distribution": "quantum-twin", "version": "5.63.0", "dependencies": requirements}
    observed = {"system": "Linux", "architecture": "x86_64", "python": "3.12.3", "systemd": 255,
                "kernel_release": "fixture-kernel", "distributions": {"quantum-twin": "5.63.0",
                    "cryptography": "45.0.7", "numpy": "2.1.3", "networkx": "3.4.2"}}
    return configuration, artifact, observed


def preflight():
    return load("packaging/petalinux/meta-orionq/recipes-orionq/orionq-core/files/platform-preflight.py")


def test_staging_is_headless_complete_and_preserves_required_resources(staged):
    destination, report = staged
    assert report["version"] == tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert not report["cross_compiled"] and not report["arm_image_built"] and not report["hardware_validated"]
    assert not report["build_contains_credentials"] and not report["services_auto_enabled"]
    assert set(row["provider"] for row in report["runtime_dependencies"]) == {
        "python3-cryptography", "python3-networkx", "python3-numpy", "python3-pandas", "python3-pyyaml",
        "python3-qiskit", "python3-qiskit-aer", "python3-scipy", "python3-sgp4", "python3-websockets"}
    archive, opened = read_archive(staged)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == report["source_archive_sha256"]
    with opened:
        names = opened.getnames()
        assert not any("/console/" in name or name.endswith("/desktop.py") for name in names)
        assert not any(Path(name).suffix in {".pem", ".key", ".db", ".sqlite", ".pyc"} for name in names)
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in names)
        for relative in report["runtime_resource_paths"]:
            assert any(name.startswith(f"orionq-core-{report['version']}/resources/{relative}") for name in names)
        for relative, digest in report["source_files"].items():
            assert hashlib.sha256(opened.extractfile(f"orionq-core-{report['version']}/{relative}").read()).hexdigest() == digest
    assert (destination / "recipes-orionq/orionq-core/orionq-artifact.inc").is_file()


def test_staging_is_deterministic_and_rejects_overwrite(staged, tmp_path):
    tool = load("packaging/petalinux/prepare_layer.py")
    second = tool.prepare(ROOT, tmp_path / "second")
    assert second["source_archive_sha256"] == staged[1]["source_archive_sha256"]
    with pytest.raises(ValueError, match="must be new"):
        tool.prepare(ROOT, staged[0])


@pytest.mark.parametrize("requirement", ["torch>=2", "numpy @ https://invalid.test/x.whl", "numpy", "numpy!=2.0"])
def test_unreviewed_or_nonreproducible_dependencies_are_not_silently_dropped(requirement):
    with pytest.raises(ValueError):
        load("packaging/petalinux/prepare_layer.py").dependency_map([requirement])


def test_recipe_source_builds_real_host_wheel_with_headless_metadata(staged, tmp_path):
    archive, opened = read_archive(staged)
    with opened:
        opened.extractall(tmp_path / "unpacked", filter="data")
    source = tmp_path / "unpacked" / f"orionq-core-{staged[1]['version']}"
    wheels = tmp_path / "wheels"
    run = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                          "--wheel-dir", str(wheels), str(source)], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    wheel = list(wheels.glob("*.whl"))
    assert len(wheel) == 1
    with zipfile.ZipFile(wheel[0]) as package:
        names = package.namelist()
        metadata = package.read(next(name for name in names if name.endswith(".dist-info/METADATA"))).decode()
        entries = package.read(next(name for name in names if name.endswith(".dist-info/entry_points.txt"))).decode()
        assert "Requires-Dist: torch" not in metadata and "Requires-Dist: PySide6" not in metadata
        assert "Requires-Dist: scipy" in metadata and "Requires-Dist: cryptography" in metadata
        assert "orion-core = quantum_autopilot.service.cli:main" in entries
        assert "orion-pl-gateway = quantum_autopilot.service.pl_gateway_cli:main" in entries
        assert not any("/console/" in name or name.endswith("desktop.py") for name in names)
    assert archive.is_file()  # Input was not replaced by a host-specific wheel.


def test_software_preflight_does_not_claim_arm_hardware_or_open_devices():
    configuration, artifact, observed = sample_runtime()
    report = preflight().check(configuration, artifact, observed=observed)
    assert report["status"] == "ready-for-software"
    assert report["runtime_observation_source"] == "injected-test-fixture"
    assert report["physical_actuation_locked"]
    assert not any(report[key] for key in ("hardware_validated", "arm_image_built", "driver_device_opened", "bitstream_programmed"))


@pytest.mark.parametrize("change,error", [
    ("missing-crypto", "dependency:cryptography"), ("old-crypto", "dependency:cryptography"),
    ("numpy-upper-bound", "dependency:numpy"), ("wrong-python", "python-minimum"),
    ("wrong-architecture", "target-architecture"), ("old-systemd", "systemd-credential-support"),
    ("wrong-core-version", "installed-core-version")])
def test_preflight_blocks_actual_missing_or_incompatible_dependencies(change, error):
    configuration, artifact, observed = sample_runtime()
    if change == "missing-crypto":
        observed["distributions"].pop("cryptography")
    elif change == "old-crypto":
        observed["distributions"]["cryptography"] = "42.0.5"
    elif change == "numpy-upper-bound":
        observed["distributions"]["numpy"] = "3.0.0"
    elif change == "wrong-python":
        observed["python"] = "3.9.20"
    elif change == "wrong-architecture":
        observed["architecture"] = "aarch64"
    elif change == "old-systemd":
        observed["systemd"] = 252
    else:
        observed["distributions"]["quantum-twin"] = "5.62.0"
    report = preflight().check(configuration, artifact, observed=observed)
    assert report["status"] == "blocked" and error in report["errors"]


@pytest.mark.parametrize("key,value", [("slots", True), ("slots", 1025), ("max_events", 65537),
                                     ("ring_budget_bytes", 67108865), ("ring_budget_bytes", 4096),
                                     ("abi_major", 2), ("abi_minor", True)])
def test_preflight_rejects_resource_and_abi_incompatibilities(key, value):
    configuration, artifact, observed = sample_runtime()
    configuration["limits"][key] = value
    with pytest.raises(ValueError):
        preflight().check(configuration, artifact, observed=observed)


def hardware_fixture(tmp_path):
    files = {}
    for name in ("bsp", "hardware_export", "bitstream", "kernel"):
        item = tmp_path / name
        item.write_bytes(b"CONFIG_DMA_ENGINE=y\nCONFIG_FPGA=y\n" if name == "kernel" else b"fixture-not-real-hardware")
        files[name] = {"path": str(item), "sha256": hashlib.sha256(item.read_bytes()).hexdigest()}
    device = tmp_path / "not-a-driver"
    device.write_bytes(b"not-a-character-device")
    return {**{name: files[name] for name in ("bsp", "hardware_export", "bitstream")},
            "kernel": {"release": "fixture-kernel", "configuration": files["kernel"],
                       "required_options": ["CONFIG_DMA_ENGINE=y", "CONFIG_FPGA=y"]},
            "driver": {"device": str(device), "module": "orionq_fixture_not_installed", "abi_major": 1,
                       "abi_minor": 0, "design_id": "fixture-design"}}


def test_identified_hardware_files_do_not_substitute_driver_or_qualification(tmp_path):
    configuration, artifact, observed = sample_runtime()
    configuration["backend_mode"] = "linux-pl-driver"
    configuration["hardware"] = hardware_fixture(tmp_path)
    report = preflight().check(configuration, artifact, observed=observed)
    assert report["status"] == "blocked"
    assert "driver-character-device" in report["errors"]
    assert "trusted-physical-driver-composition-and-qualification" in report["errors"]
    assert len(report["artifact_fingerprints"]) == 4
    assert not report["driver_device_opened"] and not report["hardware_validated"]
    Path(configuration["hardware"]["bitstream"]["path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="fingerprint"):
        preflight().check(configuration, artifact, observed=observed)


def test_software_profile_does_not_accept_hardware_or_unbounded_claims(tmp_path):
    configuration, artifact, observed = sample_runtime()
    configuration["hardware"] = hardware_fixture(tmp_path)
    with pytest.raises(ValueError):
        preflight().check(configuration, artifact, observed=observed)
    configuration.pop("hardware")
    configuration["hardware_validated"] = True
    with pytest.raises(ValueError):
        preflight().check(configuration, artifact, observed=observed)


@pytest.mark.parametrize("data", [b'{"schema":"a","schema":"b"}', b'{"time":NaN}', b"x" * 65537],
                         ids=["duplicate", "nonfinite", "oversized"])
def test_strict_configuration_rejects_duplicate_nonfinite_or_oversized_json(tmp_path, data):
    item = tmp_path / "config.json"
    item.write_bytes(data)
    with pytest.raises(ValueError):
        preflight()._json(item)


def test_missing_configuration_is_sanitized_and_not_accepted(tmp_path, capsys):
    assert preflight().main(["--config", str(tmp_path / "private-location.json"), "--artifact", str(tmp_path / "absent")]) == 2
    output = capsys.readouterr().out
    assert "private-location" not in output and "Traceback" not in output
    assert json.loads(output)["status"] == "blocked"


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO behavior is checked on Linux")
def test_fifo_and_symlink_artifacts_cannot_block_or_bypass_regular_file_requirement(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError):
        preflight()._json(fifo)
    ordinary = tmp_path / "ordinary"
    ordinary.write_text("{}")
    link = tmp_path / "link"
    link.symlink_to(ordinary)
    with pytest.raises(ValueError):
        preflight()._json(link)


def test_separate_units_preserve_service_auth_and_socket_limits():
    core = (CORE_FILES / "orionq-core.service").read_text()
    gateway = (GATEWAY_FILES / "orionq-pl-gateway.service").read_text()
    for unit in (core, gateway):
        assert "User=orionq" in unit and "Group=orionq" in unit
        assert "NoNewPrivileges=yes" in unit and "PrivateDevices=yes" in unit
        assert "RestartPreventExitStatus=2" in unit and "UMask=0077" in unit
        assert " -I " in unit and "--config /etc/orionq/" in unit
        assert "provision" not in unit and "--enable" not in unit
    assert "LoadCredential=orion-service:" in core and "LoadCredential=orion-vault:" in core
    assert "quantum_autopilot.service.cli" in core and "quantum_autopilot.service.pl_gateway_cli" in gateway
    assert "RuntimeDirectory=orionq-pl" in gateway and "RuntimeDirectoryMode=0700" in gateway
    assert "StateDirectory=orionq-pl" in gateway and "StateDirectoryMode=0700" in gateway
    assert "RuntimeDirectoryPreserve=yes" not in gateway
    assert "RestrictAddressFamilies=AF_UNIX\n" in gateway
    for recipe in LAYER.rglob("*.bb"):
        assert 'SYSTEMD_AUTO_ENABLE:${PN} = "disable"' in recipe.read_text()
    assert "python_setuptools_build_meta" in (LAYER / "recipes-orionq/orionq-core/orionq-core.bb").read_text()


def test_examples_are_external_references_and_do_not_provision_authority():
    example_root = ROOT / "packaging/petalinux/examples"
    core = json.loads((example_root / "core.json").read_text())
    gateway = json.loads((example_root / "pl-gateway.json").read_text())
    assert "provisioning_identity" not in core
    assert core["clock"]["enabled"]
    assert core["repository_root"] == "/usr/share/orionq/resources"
    assert gateway["schema"] == "orion.pl-gateway.v1"
    assert gateway["socket_path"] == "/run/orionq-pl/worker.sock"
    assert gateway["state_directory"] == "/var/lib/orionq-pl"
    assert "password" not in core and "credential" not in core["identity"]
    assert not list(LAYER.rglob("*.pem")) and not list(LAYER.rglob("*.key"))


def _serve_deployment_gateway(path, identity, state, stop, failures):
    from quantum_autopilot.lab.pl_transport import PLBackendServer
    from quantum_autopilot.lab.processing_node import CPUReferenceWorker

    try:
        PLBackendServer(path, identity, CPUReferenceWorker(), allowed_uid=os.geteuid(),
                        timeout_s=.5, state_directory=state).serve(stop)
    except BaseException as error:
        failures.put(type(error).__name__)


@contextmanager
def _deployment_gateway(runtime, state, identity, *, refused=False):
    context = multiprocessing.get_context("spawn")
    stop, failures = context.Event(), context.Queue()
    path = runtime / "worker.sock"
    process = context.Process(target=_serve_deployment_gateway,
                              args=(path, identity, state, stop, failures))
    process.start()
    try:
        if refused:
            assert failures.get(timeout=5) == "PSPLStateError"
            process.join(3)
            assert not process.is_alive() and not path.exists()
        else:
            deadline = time.monotonic() + 5
            while not path.is_socket() and process.is_alive() and time.monotonic() < deadline:
                time.sleep(.01)
            assert path.is_socket(), "Linux deployment gateway did not bind"
        yield path
    finally:
        stop.set()
        process.join(3)
        if process.is_alive():
            process.terminate()
            process.join(2)
        assert not process.is_alive()
        failures.close()


@pytest.mark.skipif(not sys.platform.startswith("linux") or not hasattr(socket, "SO_PEERCRED"),
                    reason="real Linux process credentials and durable ledgers required")
def test_gateway_state_survives_runtime_directory_cleanup_and_process_restart(tmp_path):
    from quantum_autopilot.lab.pl_transport import BackendIdentity, UnixPLCorrelationWorker
    from quantum_autopilot.lab.pspl import CorrelationRequest, PSPLStateError, TimestampBlock, TimestampEvent

    runtime = tmp_path / "run" / "orionq-pl"
    state = tmp_path / "var" / "lib" / "orionq-pl"
    runtime.mkdir(parents=True, mode=0o700)
    state.mkdir(parents=True, mode=0o700)
    identity = BackendIdentity("deployment-fixture", "cpu-external", 1,
                               hashlib.sha256(b"deployment-fixture-software-only").hexdigest(), 256)
    block = TimestampBlock("deployment-old-session", 0, "fixture-clock", "fixture-config", 0, 100,
                           (TimestampEvent(1, 1), TimestampEvent(4, 2), TimestampEvent(4, 2)),
                           "simulated", "deployment-fixture", "deployment-fixture")
    request = CorrelationRequest(1, 2, bin_width_ps=1, radius_ps=5)
    with _deployment_gateway(runtime, state, identity) as path:
        client = UnixPLCorrelationWorker(path, identity, peer_uid=os.geteuid(), timeout_s=1)
        try:
            first = client.correlate(block.to_wire(), request)
            assert first["pairs"] == 2 and sum(first["counts"]) == 2
        finally:
            client.close()
    assert not list(runtime.iterdir())
    ledgers = {item.name: item.read_bytes() for item in state.iterdir()}
    assert len(ledgers) == 2
    assert all(item.stat().st_mode & 0o077 == 0 for item in state.iterdir())
    runtime.rmdir()  # Only the empty fixture runtime directory, matching systemd cleanup.
    runtime.mkdir(mode=0o700)
    with _deployment_gateway(runtime, state, identity, refused=True):
        assert ledgers == {item.name: item.read_bytes() for item in state.iterdir()}
    generation2 = replace(identity, generation=2)
    with _deployment_gateway(runtime, state, generation2) as path:
        rejected = UnixPLCorrelationWorker(path, generation2, peer_uid=os.geteuid(), timeout_s=1)
        try:
            with pytest.raises(PSPLStateError):
                rejected.correlate(block.to_wire(), request)
        finally:
            rejected.close()
        accepted = UnixPLCorrelationWorker(path, generation2, peer_uid=os.geteuid(), timeout_s=1)
        try:
            result = accepted.correlate(replace(block, session_id="deployment-new-session").to_wire(), request)
            assert result["pairs"] == first["pairs"] and result["counts"] == first["counts"]
        finally:
            accepted.close()


@pytest.mark.skipif(os.name == "nt" or shutil.which("systemd-analyze") is None, reason="Linux systemd parser required")
def test_units_are_accepted_by_real_systemd_parser_without_installing_services(tmp_path):
    for source in (CORE_FILES / "orionq-core.service", GATEWAY_FILES / "orionq-pl-gateway.service"):
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.read_string(source.read_text())
        assert parser["Service"]["User"] == "orionq"
        # Parser validation only: /usr/bin/true replaces missing target-only
        # executables, and target resource directory is replaced by existing /tmp.
        text = source.read_text().replace("WorkingDirectory=/usr/share/orionq/resources", "WorkingDirectory=/tmp")
        lines = [key.split("=", 1)[0] + "=/usr/bin/true" if key.startswith(("ExecStart=", "ExecStartPre="))
                 else key for key in text.splitlines()]
        copied = tmp_path / source.name
        copied.write_text("\n".join(lines) + "\n")
        run = subprocess.run(["systemd-analyze", "verify", str(copied)], capture_output=True, text=True, timeout=20)
        assert run.returncode == 0, run.stdout + run.stderr
