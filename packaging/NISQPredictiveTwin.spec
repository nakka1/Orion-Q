# -*- mode: python ; coding: utf-8 -*-
"""Legacy 5.34 web-only build, retained for historical reproducibility.

Current OrionQ releases use packaging/console.spec. Its executable retains
the web entry point through OrionQ.exe --ui web.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

print("Deprecated legacy 5.34 builder: use packaging/console.spec for OrionQ; existing historical releases are preserved.")


project_root = Path(SPECPATH).resolve().parent
source_root = project_root / "src"

datas = [
    (str(project_root / "dashboard" / "dist" / "client"), "dashboard/dist/client"),
    (str(project_root / "docs" / "thesis"), "docs/thesis"),
    (str(project_root / "evidence" / "tcc" / "v1.0.0"), "evidence/tcc/v1.0.0"),
    (str(project_root / "configs" / "qkd_assurance"), "configs/qkd_assurance"),
    (
        str(
            project_root
            / "outputs"
            / "wdm_model_bundle_v527"
            / "edge_lstm_dual_head_uniform_v1.wdmbundle.npz"
        ),
        "outputs/wdm_model_bundle_v527",
    ),
]
datas += collect_data_files("qiskit")
datas += collect_data_files("qiskit_aer")

a = Analysis(
    [str(source_root / "quantum_autopilot" / "desktop.py")],
    pathex=[str(source_root), str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "matplotlib",
        "notebook",
        "pyarrow",
        "pytest",
        "quantum_autopilot.ibm",
        "qiskit_ibm_runtime",
        "statsmodels",
        "xgboost",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NISQPredictiveTwin",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="NISQPredictiveTwin",
)
