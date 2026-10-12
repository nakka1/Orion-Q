"""Compare packaged bytecode, resources and identity without running the EXE.

Run with the same CPython minor version used to build the package. This is
static packaging evidence, independent of native GUI and protocol execution
checks. No bundled code is imported or executed, and no application state is
opened. Only the requested, previously nonexistent JSON report is written.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.parser import Parser
import hashlib
from importlib.util import MAGIC_NUMBER
import json
from pathlib import Path
import re
import sys
from types import CodeType


SOURCE_ROOT = Path(__file__).resolve().parents[1]
MODULES = tuple("quantum_autopilot." + name for name in (
    "quantum_protocols.classical_transcript", "quantum_protocols.hybrid_upgrade",
    "quantum_protocols.tf_processing", "quantum_protocols.tf_simulation",
    "quantum_protocols.orbital_relay", "quantum_protocols.orbital_relay_simulation",
    "quantum_protocols.orbital_relay_topology", "quantum_protocols.physics",
    "quantum_protocols.orbits", "quantum_protocols.isl", "quantum_protocols.runtime",
    "quantum_protocols.sources", "quantum_protocols.protocols", "quantum_protocols.registry",
    "quantum_protocols.contracts", "network_engine.engine", "network_engine.global_operations",
    "network_engine.institutional", "network_engine.global_validation", "product", "desktop",
    "desktop_instance", "console.app", "console.service_provider", "console.branding", "console.institutional_validation",
    "console.communicators_validation",
    "console.prepared_source", "console.source_switch", "console.processing_labels",
    "console.widgets.network_views", "control_plane.keyrate_loop",
    "console.shell", "console.screens.boot", "console.screens.topology",
    "console.screens.splash", "console.widgets.chrome", "console.widgets.brand_mark",
    "console.widgets.emergency_signal", "console.widgets.disconnect_signal",
    "console.widgets.loading_state", "console.widgets.signal_geography", "console.widgets.geo_view",
    "console.widgets.geo_map", "console.widgets.orbit_globe", "console.widgets.node_window",
    "console.theme", "geography.geodata",
    "console.widgets.prims", "console.widgets.inspector", "console.widgets.satellite_telemetry",
    "console.widgets.policy_decisions", "console.widgets.policy_context", "console.widgets.policy_review",
    "console.policy_context_binding", "console.policy_review_binding",
    "console.policy_binding_authority",
    "qkd.dashboard_api",
    "console.performance_validation", "console.live_update_validation",
    "console.ui_pipeline_validation", "console.text_scroll_validation",
    "network_engine.routing", "control_plane.mixed_controller",
    "security.identity", "security.native_login", "security.policy", "security.runtime",
    "security.service_boundary", "security.protocol", "security.pki", "security.vault",
    "security.key_lifecycle", "security.audit", "security.equipment", "windows_acceptance",
)) + tuple("quantum_twin." + name for name in (
    "core.config", "core.validation", "core.runtime_validation", "quantum.channel",
))
RESOURCES = (
    "quantum_protocols/data/sources_v1.json", "quantum_protocols/data/sites_v1.json",
    "console/assets/orion-q.ico", "console/assets/emergency-interface.wav",
    "console/assets/disconnect-interface.wav", "console/fonts/Inter.ttf",
    "console/fonts/JetBrainsMono.ttf", "console/fonts/Oswald.ttf",
    "console/fonts/ChakraPetch-Bold.ttf", "console/fonts/ChakraPetch-SemiBold.ttf",
    "console/fonts/NotoSansJP.ttf", "console/fonts/inter-OFL.txt",
    "console/fonts/jetbrainsmono-OFL.txt", "console/fonts/oswald-OFL.txt",
    "console/fonts/chakrapetch-OFL.txt", "console/fonts/notosansjp-OFL.txt",
    "console/fonts/manifest.json", "console/geo/SOURCES.md",
    "console/geo/world_v1.json", "console/geo/brazil_v1.json", "console/geo/salvador_v1.json",
    "geography/geo/world_v1.json", "geography/geo/brazil_v1.json",
    "geography/geo/salvador_v1.json", "geography/geo/SOURCES.md",
    "control_plane/data/controller_evidence_v1.json",
    "control_plane/data/runtime_diagnostic_manifest_v1.json",
)

EXCLUDED_RESOURCES = ("console/fonts/Quantum.otf", "console/fonts/quantum-NOTICE.txt")


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _source_helpers():
    if str(SOURCE_ROOT / "src") not in sys.path:
        sys.path.insert(0, str(SOURCE_ROOT / "src"))
    from quantum_autopilot.console.branding_validation import (
        code_fingerprint, read_windows_version_resource,
    )
    from quantum_autopilot.product import (
        DISTRIBUTION_NAME, EXECUTABLE_NAME, PRODUCT_NAME, product_version,
    )
    return (code_fingerprint, read_windows_version_resource, DISTRIBUTION_NAME,
            EXECUTABLE_NAME, PRODUCT_NAME, product_version())


def _archive_reader(executable):
    from PyInstaller.archive.readers import CArchiveReader
    return CArchiveReader(str(executable))


def _pyz_header(executable, archive, name):
    # PyInstaller <7 stores the uncompressed PYZ at this CArchive offset.
    # Read only its eight-byte header before unmarshalling any module; a
    # different interpreter must fail explicitly rather than compare code.
    offset, _, _, compressed, _ = archive.toc[name]
    if compressed:
        raise ValueError("Expected an uncompressed embedded PYZ archive")
    with executable.open("rb") as stream:
        stream.seek(archive._start_offset + offset)
        return stream.read(8)


def _distribution_metadata(internal, distribution_name):
    # copy_metadata preserves the installation's metadata format. Editable
    # setuptools builds use .egg-info/PKG-INFO, wheels use .dist-info/METADATA.
    # Name normalization follows Python distribution-name equivalence without
    # choosing a preferred record when multiple active copies are present.
    def normalize(name):
        return re.sub(r"[-_.]+", "-", name).lower()
    expected = normalize(distribution_name)
    candidates = set(internal.glob("*.dist-info/METADATA"))
    candidates.update(internal.glob("*.egg-info/PKG-INFO"))
    rows = []
    for path in sorted(candidates):
        fields = Parser().parsestr(path.read_text(encoding="utf-8"))
        if normalize(fields.get("Name", "")) == expected:
            rows.append({"path": str(path), "name": fields.get("Name"),
                         "normalized_name": expected,
                         "version": fields.get("Version"), "sha256": digest(path)})
    return rows


def verify(executable: Path, *, source_root: Path = SOURCE_ROOT) -> dict:
    """Read a trusted local PyInstaller package; never execute extracted code."""
    executable, source_root = executable.resolve(), source_root.resolve()
    report = {
        "schema": "orion.frozen-source-verification.v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "passed": False,
        "scope": "Static code/resource/identity comparison; native execution is a separate gate.",
        "source_root": str(source_root), "executable": str(executable),
        "executable_sha256_before": None, "executable_sha256_after": None,
        "source_version": None, "source_optimization": 1,
        "python_version": list(sys.version_info[:3]), "frozen_code_executed": False,
        "gui_started": False, "operator_state_accessed": False,
        "physical_action_performed": False, "modules": [], "resources": [],
        "distribution_metadata": [], "windows_version_info": None, "checks": {}, "errors": [],
        "limitations": ["Does not validate GUI responsiveness, sound playback, hardware or TRL maturity.",
                        "Bytecode comparison requires the build's CPython minor version.",
                        "Inspect only trusted local packages; this is not a malicious-archive sandbox."],
    }
    try:
        fingerprint, read_pe, distribution, executable_name, product_name, release_version = _source_helpers()
        report["source_version"] = release_version
        if not release_version or release_version == "development":
            raise ValueError("A declared release version is required")
        if not executable.is_file():
            raise FileNotFoundError("Frozen executable was not found")
        report["executable_sha256_before"] = digest(executable)
        archive = _archive_reader(executable)
        names = [name for name, entry in archive.toc.items() if entry[-1] == "z"]
        if len(names) != 1:
            raise ValueError("Expected precisely one embedded PYZ archive")
        header = _pyz_header(executable, archive, names[0])
        report["embedded_archive"] = names[0]
        report["python_bytecode_magic"] = header[4:].hex()
        if header != b"PYZ\0" + MAGIC_NUMBER:
            raise ValueError("Embedded PYZ header differs from this CPython bytecode version")
        pyz = archive.open_embedded_archive(names[0])
        for name in MODULES:
            source = source_root / "src" / Path(*name.split(".")).with_suffix(".py")
            comparison = {"module": name, "source": str(source), "passed": False}
            try:
                frozen_code = pyz.extract(name)
                if not isinstance(frozen_code, CodeType):
                    raise TypeError("Bundled entry is not a compiled Python module")
                source_code = compile(source.read_text(encoding="utf-8"), name, "exec",
                                      dont_inherit=True, optimize=1)
                comparison.update(source_file_sha256=digest(source),
                                  source_code_sha256=fingerprint(source_code),
                                  frozen_code_sha256=fingerprint(frozen_code))
                comparison["passed"] = comparison["source_code_sha256"] == comparison["frozen_code_sha256"]
            except Exception as error:
                comparison["error"] = f"{type(error).__name__}: {error}"
            report["modules"].append(comparison)
        internal = executable.parent / "_internal"
        for relative in RESOURCES:
            source = source_root / "src/quantum_autopilot" / relative
            bundled = internal / "quantum_autopilot" / relative
            comparison = {"resource": relative, "source": str(source), "bundled": str(bundled), "passed": False}
            try:
                comparison.update(source_sha256=digest(source), bundled_sha256=digest(bundled))
                comparison["passed"] = comparison["source_sha256"] == comparison["bundled_sha256"]
            except Exception as error:
                comparison["error"] = f"{type(error).__name__}: {error}"
            report["resources"].append(comparison)
        report["distribution_metadata"] = _distribution_metadata(internal, distribution)
        version_matches = False
        if sys.platform == "win32":
            report["windows_version_info"] = read_pe(executable)
            version_matches = report["windows_version_info"] == {
                "ProductName": product_name, "OriginalFilename": executable_name,
                "FileVersion": release_version, "ProductVersion": release_version,
            }
        else:
            report["errors"].append("Windows PE identity validation requires a Windows host")
        report["executable_sha256_after"] = digest(executable)
        report["checks"] = {
            "executable_name": executable.name == executable_name,
            "all_required_modules_match": len(report["modules"]) == len(MODULES)
                                          and all(row["passed"] for row in report["modules"]),
            "all_required_resources_match": len(report["resources"]) == len(RESOURCES)
                                            and all(row["passed"] for row in report["resources"]),
            "excluded_resources_absent": all(not (internal / "quantum_autopilot" / name).exists()
                                             for name in EXCLUDED_RESOURCES),
            "distribution_metadata_matches": len(report["distribution_metadata"]) == 1
                                             and report["distribution_metadata"][0]["version"] == release_version,
            "windows_version_matches": version_matches,
            "executable_unchanged": report["executable_sha256_before"] == report["executable_sha256_after"],
        }
        report["passed"] = all(value is True for value in report["checks"].values()) and not report["errors"]
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.suffix.lower() != ".json":
        parser.error("--output must name a JSON report")
    if output.exists():
        parser.error("--output must be new; existing evidence is never overwritten")
    report = verify(args.exe)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"passed": report["passed"], "report": str(output),
                      "modules": len(report["modules"]), "resources": len(report["resources"]),
                      "errors": report["errors"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
