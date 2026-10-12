# -*- mode: python ; coding: utf-8 -*-
"""Native Windows console. Every required evidence resource is mandatory."""
from pathlib import Path
from importlib.metadata import version as distribution_version
import runpy
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo,
)

ROOT = Path(SPECPATH).resolve().parent
product = runpy.run_path(str(ROOT / "src/quantum_autopilot/product.py"))
product_name = product["PRODUCT_NAME"]
executable_name = product["EXECUTABLE_NAME"]
package_directory = product["PACKAGE_DIRECTORY"]
release_version = product["product_version"]()
distribution_name = product["DISTRIBUTION_NAME"]
# Frozen product_version() reads this metadata. Never silently ship the previous
# editable installation's version after changing the checkout's pyproject.
if distribution_version(distribution_name) != release_version:
    raise ValueError("Build distribution metadata is stale; reinstall the checkout before freezing")
version_parts = tuple(int(part) for part in release_version.split("."))
if not 1 <= len(version_parts) <= 4 or any(not 0 <= part <= 65535 for part in version_parts):
    raise ValueError("Windows release version must contain one to four 16-bit integers")
version_tuple = version_parts + (0,) * (4 - len(version_parts))
windows_version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_tuple, prodvers=version_tuple,
                      mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("CompanyName", product_name),
        StringStruct("FileDescription", product_name + " - Quantum Network Console"),
        StringStruct("FileVersion", release_version),
        StringStruct("InternalName", product_name),
        StringStruct("OriginalFilename", executable_name),
        StringStruct("ProductName", product_name),
        StringStruct("ProductVersion", release_version),
    ])]), VarFileInfo([VarStruct("Translation", [0x0409, 1200])])],
)
required = [
    "configs/qkd_assurance",
    "src/quantum_autopilot/qkd/data",
    "docs/thesis",
    "evidence/tcc/v1.0.0",
    "outputs/qkd_coexistence_measured/experimental_cells.csv",
    "outputs/wdm_model_bundle_v527/edge_lstm_dual_head_uniform_v1.wdmbundle.npz",
    "experiments/configs/temporal_path_campaign_demo.json",
]
datas = []
for relative in required:
    source = ROOT / relative
    if not source.exists():
        raise FileNotFoundError(f"Required runtime resource missing: {source}")
    destination = relative if source.is_dir() else str(Path(relative).parent)
    datas.append((str(source), destination))
datas += collect_data_files("quantum_autopilot")
datas += [(str(ROOT/'tests/fixtures/satellite_public'), 'satellite_public'),
          (str(ROOT/'configs/integrations/suomi100_public_satellite.json'), 'configs/integrations')]
# These contracts are mandatory even when a developer freezes from sources
# without an editable installation. Their package-relative location matters.
for name in ("controller_evidence_v1.json", "runtime_diagnostic_manifest_v1.json"):
    source = ROOT / "src/quantum_autopilot/control_plane/data" / name
    if not source.is_file():
        raise FileNotFoundError(f"Required roadmap evidence missing: {source}")
    datas.append((str(source), "quantum_autopilot/control_plane/data"))
# The live network view cannot open without these: the Salvador base map of the GEO view (with its
# source/licence notes), the two bundled type families and the safe detector export the network
# engine loads. `collect_data_files` above only finds them when the package is importable in the
# build environment, so they are also listed (and checked) explicitly.
for relative in (
    "console/assets/orion-q.ico",
    "console/assets/emergency-interface.wav",
    "console/assets/disconnect-interface.wav",
    "console/geo/salvador_v1.json",
    "console/geo/brazil_v1.json",
    "console/geo/world_v1.json",
    "quantum_protocols/data/sites_v1.json",
    "quantum_protocols/data/sources_v1.json",
    "console/geo/SOURCES.md",
    "console/fonts/Inter.ttf",
    "console/fonts/JetBrainsMono.ttf",
    "console/fonts/Oswald.ttf",
    "console/fonts/manifest.json",
    "model_intake/derived/qkd_detector_boosting_v1.json",
    "model_intake/derived/qkd_detector_boosting_v1.npz",
):
    source = ROOT / "src/quantum_autopilot" / relative
    if not source.is_file():
        raise FileNotFoundError(f"Required console resource missing: {source}")
    entry = (str(source), str(Path("quantum_autopilot") / Path(relative).parent))
    if entry not in datas:
        datas.append(entry)
# Reject stale installed resources rather than silently redistributing a removed font.
for source, destination in datas:
    if Path(source).name.casefold() in {"quantum.otf", "quantum-notice.txt"}:
        raise ValueError(f"Excluded legacy font in collected runtime resources: {source}")
datas += collect_data_files("qiskit")
datas += collect_data_files("qiskit_aer")
# Transpilation discovers its built-in stage plugins through distribution
# entry points. Importing Qiskit alone does not exercise this dependency.
datas += copy_metadata("qiskit")
datas += copy_metadata("qiskit-aer")
datas += copy_metadata("numpy")
datas += copy_metadata(distribution_name)

# The network engine is created lazily by the application service (`service.network_engine`) and
# the console screens are imported by name: list both packages so a refactoring of those imports
# can never drop them from the bundle. The author's vendored packages are reached from the engine.
hiddenimports = (collect_submodules("quantum_autopilot.network_engine")
                 + collect_submodules("quantum_autopilot.quantum_protocols")
                 + collect_submodules("quantum_autopilot.console")
                 + collect_submodules("quantum_autopilot.control_plane")
                 + ["quantum_autopilot.control_plane.geodesy", "quantum_autopilot.control_plane.inventory"])
# The portable desktop ships the observation/orbit adapters. Source installations
# may opt into the satellite extra; instrument SDKs and ML packages stay optional.
hiddenimports += collect_submodules('sgp4') + collect_submodules('websockets.sync')
hiddenimports += collect_submodules('quantum_autopilot.lab')
hiddenimports += collect_submodules('quantum_autopilot.security')
datas += copy_metadata('cryptography')
datas += copy_metadata('sgp4') + copy_metadata('websockets')

a = Analysis(
    [str(ROOT / "packaging/console_launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[], datas=datas, hiddenimports=hiddenimports, hookspath=[], runtime_hooks=[],
    excludes=["IPython", "matplotlib", "notebook", "pytest", "pyarrow",
              "quantum_autopilot.ibm", "qiskit_ibm_runtime", "statsmodels", "xgboost", "PyQt5", "PyQt6"],
    noarchive=False, optimize=1,
)
# A host's unrelated Poppler installation may place a private ICU on PATH.
# Qt's Windows ICU imports require the OS ABI, not version-suffixed Poppler
# exports (e.g. ucnv_open_78). Do not let that accidental host DLL shadow the OS.
a.binaries = [entry for entry in a.binaries if not (
    Path(entry[0]).name.lower().startswith("icu")
    and "/poppler/" in entry[1].replace("\\", "/").lower()
)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name=product_name,
    debug=False, strip=False, upx=False, console=False,
    icon=str(ROOT / "packaging/console.ico"),
    version=windows_version,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=package_directory)
