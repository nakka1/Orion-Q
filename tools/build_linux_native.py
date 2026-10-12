"""Build and exercise OrionQ native headless/Qt profiles on actual Linux.

The builder and every fixture live in a new private /var/tmp directory. Native
artifacts share one onedir runtime; the previously released wheel is read-only.
Application source is never modified. Persistent operator state, credentials
and journals are excluded from the public output. This is software qualification on
the build host, not hardware, board firmware or hard realtime qualification.

Run using an isolated Linux Python with the matching headless wheel and its
packaging/core dependencies installed. The new builder installs PyInstaller
>=6.11,<7; --profile gui additionally installs PySide6>=6.6,<7, CPU-only
Torch>=2,<3 and scikit-learn>=1.3,<2. The headless profile excludes these GUI/ML
dependencies; both include public safe model exports. The GUI
profile snapshots checkout source/resources and uses packaging/console.spec;
the headless profile freezes the installed wheel. Exact resolved versions and
artifact hashes are recorded. Source operators remain responsible for offline
provisioning and the core's private runtime configuration.

From the checkout on Linux x86_64, prepare a non-root isolated environment::

    python3 -m venv .linux-build-venv
    .linux-build-venv/bin/python -m pip install -r packaging/core/requirements.txt pyte==0.8.2
    .linux-build-venv/bin/python -m pip install --no-deps /path/to/quantum_twin-5.64.2-py3-none-any.whl
    .linux-build-venv/bin/python -I tools/build_linux_native.py --repository "$PWD" \
        --wheel /path/to/quantum_twin-5.64.2-py3-none-any.whl --profile headless \
        --output "$PWD/release/linux/5.64.2-native-headless"

Repeat the final command with --profile gui and a fresh GUI output directory.
Both builds need network access only for the isolated builder's dependencies.
The produced tar preserves executable permissions; extract it on Linux and keep
the adjacent _internal directory with its entrypoint. tools/run_linux_gui_remote_smoke.py
can additionally verify the actual GUI/core ELF pair with private mTLS fixture
paths and a public report, without changing application modules.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import traceback
import venv
import zipfile


COMMANDS = ("orion-core", "orion-operator", "orion-pl-gateway")
APPLICATION_PACKAGES = ("quantum_autopilot/", "quantum_twin/", "qnet_network/", "qnet_qkd/")
PRIVATE_HEADER = re.compile(rb"(?m)^-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----\r?$")
SERVICE_TOKEN = re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{20,})")
PRIVATE_SUFFIXES = {".key", ".pem", ".sqlite", ".sqlite3", ".db", ".pfx", ".p12"}
RESOURCES = (
    "docs/thesis/tcc_claim_registry.json", "evidence/tcc/v1.0.0/tables",
    "configs/qkd_assurance", "src/quantum_autopilot/qkd/data",
    "outputs/qkd_coexistence_measured/experimental_cells.csv",
    "docs/product/orionq_background_service.md", "docs/product/orionq_petalinux.md", "LICENSE",
)
GUI_RESOURCES = (
    "configs/qkd_assurance", "docs/thesis", "evidence/tcc/v1.0.0",
    "outputs/qkd_coexistence_measured/experimental_cells.csv",
    "outputs/wdm_model_bundle_v527/edge_lstm_dual_head_uniform_v1.wdmbundle.npz",
    "experiments/configs/temporal_path_campaign_demo.json", "tests/fixtures/satellite_public",
    "configs/integrations/suomi100_public_satellite.json", "LICENSE",
)


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_installed_wheel(wheel, version):
    """Compare installed application source bytes with the selected wheel."""
    distribution = importlib.metadata.distribution("quantum-twin")
    if distribution.version != version:
        raise ValueError("installed distribution version differs from the selected wheel")
    checked = []
    with zipfile.ZipFile(wheel) as archive:
        metadata = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1 or f"Version: {version}" not in archive.read(metadata[0]).decode().splitlines():
            raise ValueError("selected wheel metadata does not match the expected version")
        for name in sorted(archive.namelist()):
            if not name.endswith(".py") or not name.startswith(APPLICATION_PACKAGES):
                continue
            if ".." in Path(name).parts:
                raise ValueError("unsafe wheel member path")
            installed = Path(distribution.locate_file(name))
            check_public_input(installed)
            expected = hashlib.sha256(archive.read(name)).hexdigest()
            if not installed.is_file() or digest(installed) != expected:
                raise ValueError("installed application source differs from the selected wheel: " + name)
            checked.append({"path": name, "sha256": expected})
    if not checked:
        raise ValueError("selected wheel contains no expected application sources")
    return {"schema": "orion.native-linux-installed-wheel-source.v1", "status": "PASS", "version": version,
            "wheel_sha256": digest(wheel), "checked_source_count": len(checked), "files": checked,
            "scope": "Installed application .py files only; GUI checkout snapshot and external dependencies are separate inputs"}


def check_public_input(path):
    """Reject private filenames/symlinks and bounded real credential signatures."""
    if path.is_symlink():
        raise ValueError("symlink is not an allowed public build input: " + str(path))
    if path.suffix.lower() in PRIVATE_SUFFIXES or path.name.endswith(("-wal", "-shm")):
        raise ValueError("operational state is not an allowed public build input: " + str(path))
    if ((path.name.startswith(".env") and path.name not in {".env.example", ".env.template"})
            or path.name.lower() in {"credentials.json", "credentials.yaml", "credentials.yml", "token.json"}):
        raise ValueError("credential store is not an allowed public build input: " + str(path))
    if not path.is_file() or path.suffix.lower() in {".png", ".gz", ".npz", ".pdf", ".ico", ".ttf", ".otf", ".wav"}:
        return
    with path.open("rb") as stream:
        tail = b""
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            material = tail + chunk
            if PRIVATE_HEADER.search(material) or SERVICE_TOKEN.search(material):
                raise ValueError("credential signature in public build input: " + str(path))
            tail = material[-256:]


def copy_public_bundle(source, destination, manifest):
    """Copy bytes without requesting unsupported POSIX metadata on NTFS."""
    destination.mkdir()
    for row in manifest:
        original, target = source / row["path"], destination / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        if digest(target) != row["sha256"]:
            raise ValueError("public bundle copy failed its byte-integrity check")


def progress(phase):
    print(json.dumps({"phase": phase}), flush=True)


def selected_version(repository, explicit=None):
    """Select canonical metadata unless an older wheel was requested explicitly."""
    version = explicit
    if version is None:
        version = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if not isinstance(version, str) or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise ValueError("a declared numeric release version is required")
    return version


def permitted_destination(repository, destination, version):
    """Keep public Linux outputs in the selected repository's release tree."""
    root = (repository / "release/linux").resolve()
    destination = destination.resolve()
    return destination.parent == root and (destination.name == version or destination.name.startswith(version + "-"))


def run(command, *, cwd, log, timeout=600, env=None):
    """Keep potentially private process output inside the private build tree."""
    with Path(log).open("ab") as stream:
        result = subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=stream, stderr=stream, timeout=timeout, shell=False)
    if result.returncode:
        raise RuntimeError("subprocess failed; inspect the private build log")


def make_builder(work):
    builder = work / "builder"
    venv.EnvBuilder(with_pip=True).create(builder)
    python = builder / "bin/python"
    # Read-only access to dependencies of the explicitly selected installed
    # wheel. Builder packages are installed exclusively into the new venv.
    sites = [str(Path(value).resolve()) for value in sys.path
             if value and Path(value).name == "site-packages"]
    target = next((builder / "lib").glob("python*/site-packages"))
    (target / "orionq-build-dependencies.pth").write_text("\n".join(sites) + "\n", encoding="utf-8")
    run([str(python), "-I", "-m", "pip", "install", "pyinstaller>=6.11,<7"],
        cwd=work, log=work / "builder-install.log")
    return python


def make_spec(repository, work):
    for relative in RESOURCES:
        source = repository / relative
        if not source.exists():
            raise FileNotFoundError("Required public runtime resource missing: " + relative)
        for item in (source, *source.rglob("*")) if source.is_dir() else (source,):
            check_public_input(item)
    launcher = work / "headless_launcher.py"
    launcher.write_text('''import multiprocessing
import os
import sys
from quantum_autopilot.service.cli import main as core_main
from quantum_autopilot.service.operator_cli import main as operator_main
from quantum_autopilot.service.pl_gateway_cli import main as gateway_main
if __name__ == "__main__":
    multiprocessing.freeze_support()
    functions = {"orion-core": core_main, "orion-operator": operator_main,
                 "orion-pl-gateway": gateway_main}
    raise SystemExit(functions[os.path.basename(sys.executable)]())
''', encoding="utf-8")
    spec = work / "headless.spec"
    spec.write_text(f'''from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
root = Path({str(repository)!r})
datas = collect_data_files("quantum_autopilot")
for relative in {RESOURCES!r}:
    source = root / relative
    if not source.exists():
        raise FileNotFoundError("Required public runtime resource missing: " + relative)
    datas.append((str(source), relative if source.is_dir() else str(Path(relative).parent)))
for package in ("qiskit", "qiskit_aer"):
    datas += collect_data_files(package)
for distribution in ("quantum-twin", "qiskit", "qiskit-aer", "numpy", "cryptography", "sgp4", "websockets"):
    datas += copy_metadata(distribution)
hidden = collect_submodules("quantum_autopilot") + collect_submodules("sgp4") + collect_submodules("websockets.sync")
a = Analysis([{str(launcher)!r}], pathex=[], binaries=[], datas=datas,
    hiddenimports=hidden, hookspath=[], runtime_hooks=[],
    excludes=["PySide6", "PyQt5", "PyQt6", "torch", "sklearn", "IPython", "matplotlib",
              "notebook", "pytest", "pyarrow", "statsmodels", "xgboost", "qiskit_ibm_runtime",
              "quantum_autopilot.console", "quantum_autopilot.desktop", "quantum_autopilot.ibm"],
    module_collection_mode={{"quantum_autopilot": "py", "qnet_network": "py", "qnet_qkd": "py"}},
    noarchive=False, optimize=1)
pyz = PYZ(a.pure)
executables = [EXE(pyz, a.scripts, [], exclude_binaries=True, name=name,
                  debug=False, strip=False, upx=False, console=True)
               for name in {COMMANDS!r}]
coll = COLLECT(*executables, a.binaries, a.datas, strip=False, upx=False, name="OrionQ-headless")
''', encoding="utf-8")
    return spec


def make_gui_spec(repository, work):
    """Snapshot public inputs and adapt the maintained console resource contract."""
    snapshot = Path(tempfile.mkdtemp(prefix="gui-source-", dir=work))
    excluded = {"__pycache__", ".git", ".security", ".agents", ".codex"}
    for source in sorted((repository / "src").rglob("*")):
        relative = source.relative_to(repository)
        if excluded.intersection(relative.parts):
            continue
        check_public_input(source)
        if not source.is_file():
            continue
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for relative in GUI_RESOURCES:
        source, target = repository / relative, snapshot / relative
        for item in (source, *source.rglob("*")) if source.is_dir() else (source,):
            check_public_input(item)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    shutil.copyfile(repository / "pyproject.toml", snapshot / "pyproject.toml")
    (snapshot / "packaging").mkdir()
    shutil.copyfile(repository / "packaging/console_launcher.py", snapshot / "packaging/console_launcher.py")
    template = (repository / "packaging/console.spec").read_text(encoding="utf-8")
    template, count = re.subn(r"from PyInstaller.utils.win32.versioninfo import \([\s\S]*?\)\n", "", template)
    if count != 1:
        raise ValueError("Windows versioninfo import contract changed")
    start, end = template.index("version_parts ="), template.index("required = [")
    template = template[:start] + template[end:]
    template = template.replace("ROOT = Path(SPECPATH).resolve().parent",
        f"ROOT = Path({str(snapshot)!r})\nimport sys\nsys.path.insert(0, str(ROOT / 'src'))")
    template = template.replace('    icon=str(ROOT / "packaging/console.ico"),\n    version=windows_version,\n', "")
    if "win32.versioninfo" in template or "version=windows_version" in template:
        raise ValueError("Windows-only metadata remains in Linux spec")
    spec = work / "gui.spec"
    spec.write_text(template, encoding="utf-8")
    return spec, snapshot


def scan_gui_snapshot(spec, work):
    match = re.search(r"(?m)^ROOT = Path\((.+)\)$", spec.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError("GUI snapshot path is missing from the adapted spec")
    snapshot = Path(ast.literal_eval(match.group(1))).resolve(strict=True)
    if snapshot.parent != work or not snapshot.name.startswith("gui-source-"):
        raise ValueError("GUI snapshot must belong to the selected private build")
    inventory = []
    for path in sorted(snapshot.rglob("*")):
        check_public_input(path)
        if path.is_file():
            inventory.append({"path": path.relative_to(snapshot).as_posix(),
                              "bytes": path.stat().st_size, "sha256": digest(path)})
    return {"schema": "orion.native-linux-gui-public-input-scan.v1", "status": "PASS", "files": inventory,
            "file_count": len(inventory), "symlinks_or_operational_state_found": False,
            "credential_signatures_found": False,
            "limitations": "Bounded private filename/key/token signature scan; not a comprehensive secret audit"}


def validate_gui(bundle, work, report_directory, version, *, self_test_timeout_s=600):
    command = bundle / "OrionQ"
    validation_work = Path(tempfile.mkdtemp(prefix="gui-validation-", dir=work))
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_RUNTIME_DIR=str(validation_work / "xdg-runtime"))
    Path(environment["XDG_RUNTIME_DIR"]).mkdir(mode=0o700)
    result = subprocess.run([str(command), "--version"], cwd=work, capture_output=True,
                            text=True, timeout=30, env=environment, shell=False)
    assert result.returncode == 0 and result.stdout.strip() == "OrionQ " + version
    scientific_path = validation_work / "scientific-input-validation.json"
    run([str(command), "--scientific-input-test", str(scientific_path)], cwd=work,
        log=work / "gui-scientific-input.log", timeout=120, env=environment)
    scientific = json.loads(scientific_path.read_text(encoding="utf-8"))
    require(scientific.get("schema") == "orion.scientific-input-validation.v1"
            and scientific.get("passed") is True and scientific.get("frozen") is True
            and scientific.get("checks_count", 0) >= 113,
            "Frozen GUI scientific input validation did not pass all required checks")
    require(len(scientific["checks"]) == scientific["checks_count"]
            and len({row.get("name") for row in scientific["checks"]}) == scientific["checks_count"]
            and all(row.get("passed") is True for row in scientific["checks"])
            and scientific.get("physical_action_performed") is False
            and scientific.get("hardware_validated") is False,
            "Scientific diagnostic has incomplete checks or claims physical qualification")
    write_json(report_directory / "scientific-input-validation.json", scientific)
    self_test = validation_work / "self-test"
    self_test.mkdir(mode=0o700)
    report_path = self_test / "self-test.json"
    run([str(command), "--self-test", str(report_path), "--state-root", str(validation_work / "gui-fixture")],
        cwd=work, log=work / "gui-self-test.log", timeout=self_test_timeout_s, env=environment)
    value = json.loads(report_path.read_text(encoding="utf-8"))
    assert value["passed"] is True
    write_json(report_directory / "gui-self-test.json", value)
    checks = value["checks"]
    assert len(checks) == value["check_count"] and all(item is True for item in checks.values())
    write_json(report_directory / "gui-self-test-checks.json", {
        "schema": "orion.native-linux-gui-self-test-checks.v1", "passed": value["passed"],
        "check_count": value["check_count"], "checks": checks, "artifact_version": version,
        "display_backend": value["qt_platform"], "physical_display_or_mouse_validated": False,
        "raw_report_name": "gui-self-test.json", "raw_report_sha256": digest(report_directory / "gui-self-test.json"),
        "scope": "Boolean checks from the actual frozen GUI self-test; no raw simulation snapshots",
    })
    startup_report = self_test / "startup.json"
    run([str(command), "--startup-test", str(startup_report), "--startup-size", "1600x900",
         "--state-root", str(validation_work / "gui-startup-fixture")], cwd=work, log=work / "gui-startup.log",
        timeout=90, env=environment)
    startup = json.loads(startup_report.read_text(encoding="utf-8"))
    assert startup["passed"] is True and startup["platform"] == "offscreen"
    write_json(report_directory / "gui-startup.json", startup)
    # Copy only rendered images, never the isolated databases or security state.
    images = []
    for source in sorted(self_test.rglob("*.png")):
        target = report_directory / ("gui-" + source.relative_to(self_test).as_posix().replace("/", "-"))
        shutil.copyfile(source, target)
        images.append({"name": target.name, "sha256": digest(target)})
    write_json(report_directory / "gui-screenshots.json", {
        "schema": "orion.native-linux-gui-screenshots.v1", "display_backend": "offscreen", "files": images,
    })
    return {"status": "PASS", "version_stdout": result.stdout.strip(), "self_test_passed": True, "startup_passed": True,
            "scientific_input_validation_passed": True, "scientific_input_checks": scientific["checks_count"],
            "scientific_input_report": "scientific-input-validation.json",
            "display_backend": "offscreen", "physical_display_validated": False, "screenshots": images}


def validate_gateway(bundle, work, report_directory):
    command = bundle / "orion-pl-gateway"
    described = subprocess.run([str(command), "describe"], cwd=work, capture_output=True,
                               text=True, timeout=30, shell=False)
    (work / "gateway-describe.log").write_text(described.stdout + described.stderr, encoding="utf-8")
    assert described.returncode == 0
    value = json.loads(described.stdout)
    assert value["platform"] == "Linux" and value["architecture"] == "x86_64"
    assert value["hardware_validated"] is False and value["physical_actuation_locked"] is True
    private = work / "gateway-fixture"
    private.mkdir(mode=0o700)
    socket_directory, replay = private / "socket", private / "replay"
    socket_directory.mkdir(mode=0o700)
    replay.mkdir(mode=0o700)
    backend = {"backend_id": "native-cpu-fixture", "mode": "cpu-external", "generation": 1,
               "image_sha256": value["image_sha256"], "max_events": 4096}
    config = private / "gateway.json"
    write_json(config, {"schema": "orion.pl-gateway.v1", "socket_path": str(socket_directory / "pl.sock"),
                       "state_directory": str(replay), "backend": backend, "allowed_uid": os.geteuid(),
                       "request_timeout_s": 2.0})
    arguments = [str(command), "--config", str(config)]
    checked = subprocess.run([*arguments, "check"], capture_output=True, text=True, timeout=30, shell=False)
    assert checked.returncode == 0 and json.loads(checked.stdout)["status"] == "PASS"
    process = subprocess.Popen([*arguments, "serve"], cwd=work, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, shell=False)
    try:
        deadline = time.monotonic() + 20
        while not (socket_directory / "pl.sock").exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.05)
        probed = subprocess.run([*arguments, "probe"], capture_output=True, text=True, timeout=30, shell=False)
        assert probed.returncode == 0
        readback = json.loads(probed.stdout)
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
        (work / "gateway-serve.log").write_text(stdout + stderr, encoding="utf-8")
        assert process.returncode == 0
        report = {"schema": "orion.native-linux-gateway-smoke.v1", "status": "PASS", "uid": os.geteuid(),
                  "describe": value, "check": json.loads(checked.stdout), "probe": readback,
                  "shutdown_exit_code": process.returncode, "fixture_only": True,
                  "hardware_validated": False, "physical_action_performed": False}
        write_json(report_directory / "gateway-smoke.json", report)
        return report
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def native_campaign(repository, bundle, work, report_directory):
    """Reuse the real campaign, changing only the two process entrypoints.

    This is an inspectable test-harness copy, never an application-module
    replacement. The compiled core and compiled operator load their own bytes.
    """
    original = repository / "tools/run_terminal_live_campaign.py"
    source = original.read_text(encoding="utf-8")
    changes = {
        'cli = [sys.executable, "-m", "quantum_autopilot.service.cli", "--config", str(config_path)]':
        f'cli = [{str(bundle / "orion-core")!r}, "--config", str(config_path)]',
        'pty_command = [sys.executable, "-I", "-m", "quantum_autopilot.service.operator_cli",':
        f'pty_command = [{str(bundle / "orion-operator")!r},',
    }
    for old, new in changes.items():
        if source.count(old) != 1:
            raise ValueError("native campaign entrypoint patch no longer matches the source")
        source = source.replace(old, new)
    runner = work / "run_terminal_native_campaign.py"
    runner.write_text(source, encoding="utf-8")
    # The CLI's auto layout now selects the dashboard for a capable PTY.
    # Preserve its navigation/parser/scroll/termios checks. Its old corner-mark
    # assertion predates header_brand preserving the three operational rows;
    # qualify the supplied header spans' actual ink, weight and coral instead.
    probe_original = repository / "tools/terminal_dashboard_pty_probe.py"
    probe_source = probe_original.read_text(encoding="utf-8")
    start = probe_source.index("            # pyte pads each viewport row")
    end = probe_source.index("\n\n    def view", start)
    probe_source = probe_source[:start] + '''            for row, column, text, role in brand_spans(terminal.screen.display[:3]):
                cells = [terminal.screen.buffer[row][column + offset] for offset in range(len(text))]
                require("".join(cell.data for cell in cells) == text and all(cell.bold for cell in cells),
                        "current_brand_ink_and_weight_" + role + "_" + label.replace(" ", "_"))
                if role == "accent":
                    require(all(cell.fg == "d7875f" for cell in cells),
                            "current_brand_coral_" + label.replace(" ", "_"))
''' + probe_source[end:]
    retired_axis = 'require("samples; order / autoscale" in terminal.text(), "sample_order_axis_explicit")'
    if probe_source.count(retired_axis) != 1:
        raise ValueError("dashboard chart-axis probe contract changed")
    probe_source = probe_source.replace(retired_axis,
        'require("60s" in terminal.text() and "0s" in terminal.text() and "gaps" in terminal.text(), "received_time_window_axis_and_gaps_explicit")')
    (work / "terminal_pty_probe.py").write_text(probe_source, encoding="utf-8")
    report = report_directory / "native-terminal-campaign.json"
    run([sys.executable, "-I", str(runner), "--repository", str(bundle / "_internal"),
         "--state", str(work / "terminal-fixture"), "--report", str(report), "--pty"],
        cwd=work, log=work / "terminal-campaign.log", timeout=300)
    value = json.loads(report.read_text(encoding="utf-8"))
    assert value["status"] == "PASS" and value["pty"]["status"] == "PASS"
    capture = report.with_suffix(".txt").read_text(encoding="utf-8")
    brand_binding = validate_native_brand(capture, value, bundle)
    write_json(report_directory / "campaign-provenance.json", {
        "source_harness_sha256": digest(original), "adapted_harness_sha256": digest(runner),
        "probe_sha256": digest(work / "terminal_pty_probe.py"),
        "probe_source_sha256": digest(probe_original),
        "changes": ["core subprocess uses native orion-core", "PTY subprocess uses native orion-operator"],
        "probe_adaptation": "Current dashboard probe; verified header span ink, weight and coral in place of retired corner glyph; required received 60s/0s time axis and gaps in place of retired sample-order/autoscale label",
        "application_modules_replaced": False, "bundled_resources_used": True,
        "product_header_and_logo_observed": True, "native_brand_source_binding": brand_binding})
    return value


def validate_native_brand(capture, campaign, bundle):
    """Require current viewport ink/colors and the actual selected wheel bytes."""
    assert "OrionQ TERMINAL" in capture
    checks = {row["name"]: row["passed"] for row in campaign["pty"]["checks"]}
    assert checks.get("visible_brand_startup") is True and checks.get("colored_brand_startup") is True
    distribution = importlib.metadata.distribution("quantum-twin")
    binding = []
    for name in ("terminal_live.py", "terminal_brand.py", "terminal_brand_art.py", "terminal_effect.py"):
        relative = "quantum_autopilot/service/" + name
        installed = Path(distribution.locate_file(relative))
        if not installed.is_file():
            assert name not in {"terminal_live.py", "terminal_brand.py"}
            continue
        frozen = bundle / "_internal" / relative
        assert frozen.is_file() and digest(frozen) == digest(installed)
        binding.append({"path": relative, "sha256": digest(frozen)})
    return binding


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--expected-version", help="default: canonical repository project.version; explicit older wheels remain supported")
    parser.add_argument("--profile", choices=("headless", "gui"), default="headless")
    parser.add_argument("--resume-headless", type=Path,
                        help="reuse an already frozen private build after a packaging/harness failure")
    parser.add_argument("--reuse-gui-builder", type=Path,
                        help="rebuild GUI with the owned private builder from a prior attempt")
    parser.add_argument("--resume-gui", type=Path,
                        help="reuse an already frozen GUI after a fixture or export failure")
    parser.add_argument("--reuse-evidence", type=Path,
                        help="reuse passed native tests only after verifying complete bundle hashes, version and profile")
    parser.add_argument("--gui-self-test-timeout-s", type=int, default=600,
                        help="Aggregate campaign watchdog (60..3600); per-interaction/protocol criteria are unchanged")
    parser.add_argument("--dependency-probe-timeout-s", type=int, default=30,
                        help="Build-time dependency import watchdog (30..300); never alters runtime/API deadlines")
    args = parser.parse_args(argv)
    if not 60 <= args.gui_self_test_timeout_s <= 3600 or not 30 <= args.dependency_probe_timeout_s <= 300:
        parser.error("build watchdogs outside allowed range")
    if sys.flags.optimize or os.environ.get("PYTHONOPTIMIZE", "") not in {"", "0"}:
        parser.error("validation must run without Python optimization so assertion gates remain enabled")
    repository, destination, wheel = (path.resolve() for path in (args.repository, args.output, args.wheel))
    if platform.system() != "Linux" or platform.machine() != "x86_64" or os.geteuid() == 0:
        parser.error("actual Linux x86_64 with a non-root UID required")
    try:
        args.expected_version = selected_version(repository, args.expected_version)
    except (KeyError, OSError, ValueError) as error:
        parser.error(str(error))
    if not permitted_destination(repository, destination, args.expected_version):
        parser.error("output must be a new selected-version directory inside the repository Linux release root")
    if destination.exists() or not wheel.is_file():
        parser.error("fresh output and existing read-only wheel required")
    if importlib.metadata.version("quantum-twin") != args.expected_version:
        parser.error("selected installed wheel is not the expected version")
    os.umask(0o077)
    if sum(bool(value) for value in (args.resume_headless, args.reuse_gui_builder, args.resume_gui)) > 1:
        parser.error("choose at most one existing builder")
    previous_work = args.resume_headless or args.reuse_gui_builder or args.resume_gui
    if previous_work:
        work = previous_work.resolve(strict=True)
        if ((bool(args.resume_headless) != (args.profile == "headless")) or work.parent != Path("/var/tmp")
                or not work.name.startswith("orionq-native-linux-") or work.stat().st_uid != os.geteuid()):
            parser.error("reuse requires an owned private build of the selected profile under /var/tmp")
    else:
        work = Path(tempfile.mkdtemp(prefix="orionq-native-linux-", dir="/var/tmp"))
    destination.mkdir(parents=True)
    report_directory = destination / "evidence"
    report_directory.mkdir()
    start, wheel_before = time.monotonic(), digest(wheel)
    report = {"schema": "orion.native-linux-build.v1", "status": "running", "product": "OrionQ",
              "version": args.expected_version, "profile": "headless-service" if args.profile == "headless" else "qt-gui-cpu-ml",
              "machine": platform.machine(),
              "platform": platform.platform(), "python_version": platform.python_version(), "uid": os.geteuid(),
              "build_script_sha256": digest(Path(__file__)), "wheel_sha256_before": wheel_before,
              "build_watchdogs": {"gui_campaign_s": args.gui_self_test_timeout_s,
                                  "dependency_import_s": args.dependency_probe_timeout_s,
                                  "runtime_or_protocol_deadlines_modified": False},
              "hardware_validated": False, "physical_action_performed": False,
              "known_limitations": ["Qualified on Ubuntu 24.04 under WSL2 x86_64/glibc 2.39 only.",
                  "Headless excludes Qt/ML; GUI includes CPU Torch, never CUDA or hardware qualification.",
                  "Concurrency qualification is reported separately; packaging smoke tests do not establish race freedom.",
                  "CPU PL transport validation does not qualify a physical accelerator."]}
    try:
        progress("installed_application_wheel_identity")
        write_json(report_directory / "installed-wheel-source.json", validate_installed_wheel(wheel, args.expected_version))
        progress("isolated_builder_install" if not args.resume_headless else "resume_private_headless_build")
        builder = work / "builder/bin/python" if previous_work else make_builder(work)
        builder_version = subprocess.check_output([str(builder), "-I", "-c",
            "import PyInstaller; print(PyInstaller.__version__)"], text=True, timeout=args.dependency_probe_timeout_s).strip()
        report["pyinstaller_version"] = builder_version
        if args.profile == "gui":
            if args.resume_gui:
                spec = work / "gui.spec"
            else:
                progress("isolated_qt_install")
                run([str(builder), "-I", "-m", "pip", "install", "PySide6>=6.6,<7"],
                    cwd=work, log=work / "builder-qt-install.log")
                progress("isolated_cpu_ml_install")
                run([str(builder), "-I", "-m", "pip", "install", "--index-url", "https://download.pytorch.org/whl/cpu", "torch>=2,<3"],
                    cwd=work, log=work / "builder-torch-install.log")
                run([str(builder), "-I", "-m", "pip", "install", "scikit-learn>=1.3,<2"],
                    cwd=work, log=work / "builder-ml-install.log")
                spec, _snapshot = make_gui_spec(repository, work)
            report["gui_source_snapshot"] = {"public_source_only": True, "console_spec_sha256": digest(repository / "packaging/console.spec"),
                                             "adapted_spec_sha256": digest(spec)}
            write_json(report_directory / "gui-public-input-scan.json", scan_gui_snapshot(spec, work))
            report["pyside6_version"] = subprocess.check_output([str(builder), "-I", "-c",
                "import PySide6; print(PySide6.__version__)"], text=True, timeout=args.dependency_probe_timeout_s).strip()
            ml = json.loads(subprocess.check_output([str(builder), "-I", "-c",
                "import json,torch,sklearn; print(json.dumps(dict(torch=torch.__version__,cuda=torch.version.cuda,sklearn=sklearn.__version__)))"],
                text=True, timeout=args.dependency_probe_timeout_s))
            assert ml["cuda"] is None
            report["cpu_ml_versions"] = ml
            bundle = work / "dist/OrionQ"
            names = ("OrionQ",)
        else:
            spec = work / "headless.spec" if args.resume_headless else make_spec(repository, work)
            bundle = work / "dist/OrionQ-headless"
            names = COMMANDS
        if not (args.resume_headless or args.resume_gui):
            progress("freeze_" + args.profile)
            run([str(builder), "-I", "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(work / "dist"),
                 "--workpath", str(work / "pyinstaller-work"), str(spec)], cwd=work,
                log=work / "pyinstaller.log", timeout=1200)
        report["commands"] = []
        for name in names:
            executable = bundle / name
            assert executable.read_bytes()[:4] == b"\x7fELF"
            result = subprocess.run([str(executable), "--help"], cwd=work, capture_output=True,
                                    text=True, timeout=30, shell=False)
            assert result.returncode == 0 and "usage:" in result.stdout
            report["commands"].append({"name": name, "bytes": executable.stat().st_size,
                                       "sha256": digest(executable), "help_exit_code": result.returncode})
        manifest = [{"path": file.relative_to(bundle).as_posix(), "bytes": file.stat().st_size,
                     "sha256": digest(file)} for file in sorted(bundle.rglob("*")) if file.is_file()]
        write_json(report_directory / "bundle-files.json", {"schema": "orion.native-bundle-files.v1",
                    "version": report["version"], "profile": report["profile"], "files": manifest})
        if args.profile == "gui":
            progress("native_qt_offscreen_self_test")
            report["gui_validation"] = validate_gui(bundle, work, report_directory, args.expected_version,
                                                    self_test_timeout_s=args.gui_self_test_timeout_s)
        else:
            if args.reuse_evidence:
                previous = args.reuse_evidence.resolve(strict=True)
                old_report = json.loads((previous.parent / "build-report.json").read_text(encoding="utf-8"))
                assert old_report["version"] == report["version"] and old_report["profile"] == report["profile"]
                assert old_report["commands"] == report["commands"]
                old_manifest = json.loads((previous / "bundle-files.json").read_text(encoding="utf-8"))
                assert old_manifest["schema"] == "orion.native-bundle-files.v1" and old_manifest["files"] == manifest
                gateway = json.loads((previous / "gateway-smoke.json").read_text(encoding="utf-8"))
                campaign = json.loads((previous / "native-terminal-campaign.json").read_text(encoding="utf-8"))
                assert gateway["status"] == campaign["status"] == campaign["pty"]["status"] == "PASS"
                capture = (previous / "native-terminal-campaign.txt").read_text(encoding="utf-8")
                brand_binding = validate_native_brand(capture, campaign, bundle)
                for name in ("gateway-smoke.json", "native-terminal-campaign.json", "native-terminal-campaign.txt"):
                    shutil.copyfile(previous / name, report_directory / name)
                write_json(report_directory / "campaign-provenance.json", {
                    "passed_native_evidence_reused": True, "matching_executable_hashes_verified": True,
                    "complete_bundle_hashes_verified": True, "matching_version_and_profile_verified": True,
                    "source_bundle_manifest_sha256": digest(previous / "bundle-files.json"),
                    "source_build_report_sha256": digest(previous.parent / "build-report.json"),
                    "product_header_and_logo_observed": True, "application_modules_replaced": False,
                    "native_brand_source_binding": brand_binding})
            else:
                validation_work = Path(tempfile.mkdtemp(prefix="headless-validation-", dir=work))
                progress("native_gateway_smoke")
                validate_gateway(bundle, validation_work, report_directory)
                progress("native_core_operator_tls_pty_campaign")
                campaign = native_campaign(repository, bundle, validation_work, report_directory)
            report["native_terminal_status"] = campaign["status"]
            report["native_pty_status"] = campaign["pty"]["status"]
            report["native_pty_key_count"] = campaign["pty"]["key_presses"]
        progress("export_public_artifacts")
        after_validation = [{"path": file.relative_to(bundle).as_posix(), "bytes": file.stat().st_size,
                             "sha256": digest(file)} for file in sorted(bundle.rglob("*")) if file.is_file()]
        assert after_validation == manifest, "native validation changed the frozen bundle"
        archive = destination / f"OrionQ-{args.expected_version}-linux-x86_64-{args.profile}.tar.gz"
        # Executable mode is preserved inside the archive across Windows mounts.
        with tarfile.open(archive, "w:gz") as stream:
            stream.add(bundle, arcname=bundle.name)
        copy_public_bundle(bundle, destination / bundle.name, manifest)
        for source in (repository / "packaging/core/core.example.json", repository / "packaging/core/systemd/orionq-core.service"):
            shutil.copyfile(source, destination / source.name)
        report["archive"] = {"name": archive.name, "bytes": archive.stat().st_size, "sha256": digest(archive)}
        report["bundle_file_count"] = len(manifest)
        report["bundle_bytes"] = sum(row["bytes"] for row in manifest)
        installed = subprocess.check_output([str(builder), "-I", "-m", "pip", "list", "--format=json"],
                                           text=True, timeout=30)
        write_json(report_directory / "build-environment.json", {"packages": json.loads(installed),
                    "profile": report["profile"], "inherited_environments_modified": False})
        report["wheel_sha256_after"] = digest(wheel)
        assert report["wheel_sha256_after"] == wheel_before
        report.update(status="PASS", wheel_preserved=True, secrets_in_public_output=False)
    except Exception as error:
        (work / "build-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        report.update(status="blocked", error_type=type(error).__name__, private_build_directory=str(work))
    finally:
        report["duration_s"] = time.monotonic() - start
        report["created_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(destination / "build-report.json", report)
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
