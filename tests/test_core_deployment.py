"""The core artifact must remain usable without desktop code or GUI/ML packages."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tomllib
import zipfile

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_RESOURCE_PATHS = (
    "docs/thesis/tcc_claim_registry.json",
    "evidence/tcc/v1.0.0/tables",
    "configs/qkd_assurance/irelandqci_example_operating_point.json",
    "src/quantum_autopilot/qkd/data/ireland_qci_grid_v1.json",
    "outputs/qkd_coexistence_measured/experimental_cells.csv",
)

INSTALLED_CORE_PROGRAM = r'''
import importlib.abc
import json
import math
import os
from pathlib import Path
import sys

installed, resources, state = map(Path, sys.argv[1:])
sys.path = [str(installed)] + [item for item in sys.path if Path(item or '.').resolve() not in
    {resources, resources / 'src', resources / 'experiments'}]
forbidden_names = ('PySide6', 'PyQt6', 'torch', 'sklearn', 'quantum_autopilot.console',
                   'quantum_autopilot.desktop', 'ctypes.wintypes')

class NoDesktopImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in forbidden_names):
            # On a Windows host pandas/dateutil probes its optional timezone
            # extension. Make the Windows ABI unavailable as on Linux, allowing
            # that third party's documented ImportError fallback. SID/DPAPI
            # authority remains separately trapped below, not emulated.
            if fullname == 'ctypes.wintypes':
                raise ModuleNotFoundError('Windows ABI is unavailable in this portability fixture')
            raise AssertionError('headless artifact attempted a forbidden import: ' + fullname)
        return None

sys.meta_path.insert(0, NoDesktopImports())
assert not any(any(name == blocked or name.startswith(blocked + '.') for blocked in forbidden_names)
               for name in sys.modules)
import quantum_autopilot.service.cli
import quantum_autopilot.security.runtime as runtime_module
import quantum_autopilot.security.vault as vault_module
from quantum_autopilot.security import SecurityRuntime
from quantum_autopilot.security.secret_providers import AESGCMSecretProvider
from quantum_autopilot.security.service_identity import ServiceIdentityBinding, ServiceIdentityProvision
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
from quantum_twin.quantum.swapping import WernerStateSwapping
from quantum_twin.simulation.network import QuantumRepeaterNode

def forbidden_os_authority(*args, **kwargs):
    raise AssertionError('installed core consulted Windows or interactive OS authority')

runtime_module.operating_system_identity = forbidden_os_authority
vault_module._dpapi = forbidden_os_authority
vault_module._get_dpapi_bindings = forbidden_os_authority

class FixtureKeySource:
    def __init__(self):
        self.key = os.urandom(32)
    def read_key(self, key_id, version):
        assert (key_id, version) == ('fixture', 1)
        return self.key

fixture_keys = FixtureKeySource()
provider = AESGCMSecretProvider(fixture_keys, active_key_id='fixture', active_version=1)
admin_credential, service_credential = os.urandom(48), os.urandom(48)
admin = SecurityRuntime(state / 'security', profile='operational', identity_profile='service',
    secret_provider=provider, bootstrap=True,
    service_bootstrap=ServiceIdentityProvision('fixture-admin', ('administrator',), ('*',), admin_credential))
administrator = admin.authenticate_service('fixture-admin', admin_credential)
with admin.identity_scope(administrator):
    admin.grant_service_identity('fixture-core', ('operator',), ('*',), service_credential)
runtime = SecurityRuntime(state / 'security', profile='operational', identity_profile='service',
    bootstrap=False, secret_provider=provider,
    service_identity=ServiceIdentityBinding('fixture-core', lambda: service_credential))
service = AssuranceDashboardService(resources, control_journal_path=state / 'workspace.sqlite', security=runtime)
try:
    initial = service.network_live_state()
    assert initial['source']['kind'] == 'none'
    service.configure_network_input('simulated', seed=17, scenario='quiet')
    result = service.network_step()
    assert result['tick'] == initial['tick'] + 1
    assert result['source']['kind'] == 'simulated'
    assert any(link['qber'] is not None for link in result['links'])
    health = service.health()
    assert health['physical_read_only'] is True and health['automation_allowed'] is False
    full = service.console_snapshot()
    lightweight = service.console_snapshot(include_static=False)
    for snapshot in (full, lightweight):
        assert snapshot['schema'] == 'orion.console.remote.v1'
        assert snapshot['security']['subject'] == 'fixture-core'
        assert snapshot['security']['identity_profile'] == 'service'
        assert snapshot['security']['roles'] == ['operator']
        assert snapshot['security']['physical_qualification'] is False
        assert snapshot['health']['physical_read_only'] is True
        assert snapshot['live']['tick'] == result['tick']
        assert snapshot['live']['publication_revision'] == result['publication_revision']
        assert snapshot['live']['engine_session_id'] == result['engine_session_id']
        assert snapshot['live']['topology_version'] == snapshot['topology']['topology']['topology_version']
        assert snapshot['live']['links'] == result['links']
        assert snapshot['live']['nodes'] == result['nodes']
        # Exercise the actual JSON transport contract rather than only imports.
        serialized = json.dumps(snapshot, allow_nan=False)
        assert all(secret.hex() not in serialized for secret in
                   (admin_credential, service_credential, fixture_keys.key))
    assert full['summary']['integrity']['valid'] is True
    assert full['summary']['integrity']['failed_check_count'] == 0
    assert full['model']['configured'] is False
    assert full['evidence_lanes']['qkd_grid_integrity_required'] is True
    assert all(key in full and key not in lightweight for key in ('summary', 'model', 'evidence_lanes'))
    assert service.network_live_state()['tick'] == result['tick']
    assert runtime.audit.verify()['valid']
    swapped = WernerStateSwapping().swap({'F_t': .9, 'success': True}, {'F_t': .9, 'success': True})
    # Qiskit's density-matrix square roots have a floating-point residual;
    # this packaging smoke compares the known Werner result at 1e-7 absolute.
    assert swapped['success'] and math.isclose(swapped['F_t'], .9 ** 2 + .1 ** 2 / 3,
                                             rel_tol=0, abs_tol=1e-7)
    # Purification is not a guaranteed fidelity gain. Compare a declared
    # zero-CNOT-noise fixture with the existing noisy default at the same draw.
    purification_input = dict(
        fidelity_pair_1=.9, fidelity_pair_2=.9, memory_age_1_s=0,
        memory_age_2_s=0, random_value=0, pair_1_id='fixture-a', pair_2_id='fixture-b')
    purified = QuantumRepeaterNode(seed=7, depol_prob=0).run_purification_event(**purification_input)
    degraded = QuantumRepeaterNode(seed=7).run_purification_event(**purification_input)
    assert purified['sampled_success'] and purified['F_after'] > .9
    assert degraded['sampled_success'] and degraded['F_after'] < purified['F_after']
    modules = {name: module for name, module in sys.modules.items() if
               name.startswith(('quantum_autopilot', 'quantum_twin')) and getattr(module, '__file__', None)}
    assert modules and all(Path(module.__file__).resolve().is_relative_to(installed.resolve())
                           for module in modules.values())
    assert not any(any(name == blocked or name.startswith(blocked + '.') for blocked in forbidden_names)
                   for name in sys.modules)
    print(json.dumps({'status': 'PASS', 'identity_profile': runtime.identity_profile,
        'tick': result['tick'], 'nodes': len(result['nodes']), 'links': len(result['links']),
        'installed_modules': len(modules), 'physical_action_performed': False,
        'gui_ml_windows_imports': False, 'swap_fidelity': swapped['F_t'],
        'snapshot_schema': full['schema'], 'snapshot_subject': full['security']['subject'],
        'snapshot_static_integrity': full['summary']['integrity']['valid'],
        'snapshot_lightweight_static_omitted': True, 'snapshot_advances_engine': False,
        'snapshot_json_transport_valid': True, 'runtime_resources': 'docker-copy-allowlist',
        'swap_reference_abs_tolerance': 1e-7,
        'purification_fidelity': purified['F_after'],
        'purification_default_noise_fidelity': degraded['F_after']}))
finally:
    service._release_controller_io_resources()
    if service._network_engine is not None:
        service._network_engine.mission_receiver.close()
'''


def load_tool(name):
    spec = importlib.util.spec_from_file_location(f"orion_core_{name}", ROOT / f"packaging/core/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / "packaging/core").mkdir(parents=True)
    (root / "packaging/core/requirements.txt").write_bytes((ROOT / "packaging/core/requirements.txt").read_bytes())
    (root / "pyproject.toml").write_text(
        '[project]\nname="quantum-twin"\nversion="5.61.0"\nrequires-python=">=3.10"\n'
        'dependencies=["torch>=2", "PySide6>=6"]\n'
        '[tool.setuptools.package-data]\n"quantum_autopilot.qkd"=["data/*.json"]\n'
        '"quantum_autopilot.geography"=["geo/*.json"]\n'
        '"quantum_autopilot.console"=["assets/*.png"]\n', encoding="utf-8")
    files = {"quantum_autopilot/__init__.py": '',
             "quantum_autopilot/service/__init__.py": '',
             "quantum_autopilot/service/cli.py": 'def main(): return 0\n',
             "quantum_autopilot/desktop.py": 'import PySide6\n',
             "quantum_autopilot/console/__init__.py": 'import PySide6\n',
             "quantum_autopilot/console/assets/logo.png": 'not-runtime',
             "quantum_autopilot/qkd/__init__.py": '',
             "quantum_autopilot/qkd/data/grid.json": '{}',
             "quantum_autopilot/geography/__init__.py": '',
             "quantum_autopilot/geography/geo/world.json": '{}',
             "quantum_autopilot/security/private.key": 'private',
             "quantum_autopilot/state.sqlite": 'state',
             "quantum_autopilot/__pycache__/temp.pyc": 'cache'}
    for name, content in files.items():
        path = root / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def test_source_profile_preserves_checkout_and_excludes_gui_and_state(project, tmp_path):
    original = (project / "pyproject.toml").read_bytes()
    destination = tmp_path / "core"
    manifest = load_tool("prepare_source").prepare(project, destination)
    metadata = tomllib.loads((destination / "pyproject.toml").read_text())
    assert metadata["project"]["version"] == "5.61.0"
    assert metadata["project"]["scripts"] == {
        "orion-core": "quantum_autopilot.service.cli:main",
        "orion-operator": "quantum_autopilot.service.operator_cli:main",
        "orion-pl-gateway": "quantum_autopilot.service.pl_gateway_cli:main",
    }
    assert metadata["project"]["dependencies"] == manifest["dependencies"]
    assert not any("torch" in dep or "PySide" in dep or "scikit" in dep for dep in manifest["dependencies"])
    assert not (destination / "src/quantum_autopilot/console").exists()
    assert not (destination / "src/quantum_autopilot/desktop.py").exists()
    assert not (destination / "src/quantum_autopilot/security/private.key").exists()
    assert not (destination / "src/quantum_autopilot/state.sqlite").exists()
    assert (destination / "src/quantum_autopilot/qkd/data/grid.json").is_file()
    assert (project / "pyproject.toml").read_bytes() == original
    assert (project / "src/quantum_autopilot/console/assets/logo.png").is_file()


def test_source_profile_builds_wheel_with_real_metadata(project, tmp_path):
    destination = tmp_path / "wheel-source"
    load_tool("prepare_source").prepare(project, destination)
    result = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                             "--wheel-dir", str(tmp_path / "wheels"), str(destination)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list((tmp_path / "wheels").glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        assert "quantum_autopilot/qkd/data/grid.json" in names
        assert "quantum_autopilot/geography/geo/world.json" in names
        assert not any("console/" in name or name.endswith("desktop.py") or name.endswith(".key") for name in names)
        metadata = wheel.read(next(name for name in names if name.endswith(".dist-info/METADATA"))).decode()
        assert "Requires-Dist: qiskit" in metadata
        assert "Requires-Dist: torch" not in metadata
        assert "Requires-Dist: PySide6" not in metadata


def test_installed_real_core_runs_network_and_physics_without_gui_ml_or_windows_authority(tmp_path):
    source = tmp_path / "real-core-source"
    load_tool("prepare_source").prepare(ROOT, source)
    wheels = tmp_path / "real-wheels"
    build = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                            "--wheel-dir", str(wheels), str(source)], capture_output=True, text=True, timeout=120)
    assert build.returncode == 0, build.stdout + build.stderr
    wheel = list(wheels.glob("*.whl"))
    assert len(wheel) == 1
    installed = tmp_path / "installed"
    install = subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(installed),
                              str(wheel[0])], capture_output=True, text=True, timeout=60)
    assert install.returncode == 0, install.stdout + install.stderr
    working = tmp_path / "runtime"
    working.mkdir()
    resources = working / "resources"
    # Mirror the image's immutable public resources, not the complete checkout.
    # This catches packaged snapshots accidentally depending on absent reports.
    docker_runtime = (ROOT / "packaging/core/Dockerfile").read_text().split(" AS runtime\n", 1)[1]
    for relative in RUNTIME_RESOURCE_PATHS:
        assert f"COPY {relative} " in docker_runtime
        original, destination = ROOT / relative, resources / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if original.is_dir():
            shutil.copytree(original, destination)
        else:
            shutil.copyfile(original, destination)
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    run = subprocess.run([sys.executable, "-c", INSTALLED_CORE_PROGRAM, str(installed), str(resources),
                          str(working / "state")], cwd=working, env=environment,
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(run.stdout)
    assert report["status"] == "PASS" and report["installed_modules"] > 20
    assert report["nodes"] > 0 and report["links"] > 0 and report["tick"] == 1
    assert report["gui_ml_windows_imports"] is False
    assert report["snapshot_static_integrity"] is True
    assert report["snapshot_advances_engine"] is False
    assert report["runtime_resources"] == "docker-copy-allowlist"
    (tmp_path / "installed-core-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


@pytest.mark.parametrize("existing", ["project", "src", "new-staging", "git"])
def test_staging_never_overwrites_existing_or_source(project, tmp_path, existing):
    destination = {"project": project, "src": project / "src/new", "new-staging": tmp_path / "occupied",
                   "git": project / ".git/new"}[existing]
    if existing == "new-staging":
        destination.mkdir()
    with pytest.raises(ValueError):
        load_tool("prepare_source").prepare(project, destination)


def test_liveness_reports_open_and_closed_socket_without_http_authentication():
    probe = load_tool("healthcheck")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen(1)
        assert probe.check("127.0.0.1", port)
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(.1)
            assert connection.recv(1) == b""  # The probe sends no HTTP/token/key request.
    assert not probe.check("127.0.0.1", port, .1)


@pytest.mark.parametrize("port,timeout", [(0, 1), (65536, 1), (8765, 0), (8765, 11)])
def test_liveness_rejects_invalid_target(port, timeout):
    with pytest.raises(ValueError):
        load_tool("healthcheck").check("127.0.0.1", port, timeout)


def test_compose_has_separate_readonly_secrets_and_durable_state():
    service = yaml.safe_load((ROOT / "packaging/core/compose.yaml").read_text())["services"]["core"]
    assert service["read_only"] is True
    assert service["user"] == "10001:10001"
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    mounts = {row["target"]: row for row in service["volumes"]}
    assert mounts["/etc/orionq"]["read_only"] is True
    assert mounts["/run/secrets"]["read_only"] is True
    assert not mounts["/var/lib/orionq"].get("read_only", False)
    assert all(":?" in row["source"] for row in mounts.values())
    assert "127.0.0.1" in service["ports"][0]


def test_image_has_explicit_build_context_and_no_default_credential():
    dockerfile = (ROOT / "packaging/core/Dockerfile").read_text()
    assert "COPY . " not in dockerfile
    assert "pip check" in dockerfile
    assert '"quantum_autopilot.service.cli"' in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert '"serve"]' in dockerfile
    ignore = (ROOT / "packaging/core/Dockerfile.dockerignore").read_text()
    assert ignore.splitlines()[1] == "*"
    assert "!outputs/**" not in ignore
    assert "**/*.key" in ignore
    assert "!outputs/qkd_coexistence_measured/experimental_cells.csv" in ignore
    assert "!src/**" in ignore
