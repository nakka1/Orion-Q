"""Export a fresh working snapshot and curated evidence; never copy Git history.

Git initialization, commits and ZIP creation deliberately remain separate. Large
raw experiments remain in the original workspace and can be reproduced with the
campaign tools. This is an evidence selection, not a new scientific acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
MAX_PROJECTION_SOURCE_BYTES = 64 * 1024 * 1024
SOURCE_ROOTS = {
    ".github", "acquisition", "configs", "dashboard", "data", "docs", "evidence",
    "experiments", "external", "legacy", "packaging", "runtime_assets", "src",
    "tests", "tools",
}
PUBLIC_SOURCE_FILES = {
    "work/mixed_concurrency_audit_20261011.py",
    "work/legacy_pair_race_audit_20261011.py",
    "work/measure_ledger_batch_release.py",
    "work/publish_windows_native_5_64_2.py",
    "work/validate_linux_gui_native_bootstrap.py",
    "work/resume_windows_native_release.py",
    "fpga/orionq_pl/orionq_correlation.v",
    "fpga/orionq_pl/orionq_fast_gate.v",
    "fpga/orionq_pl/tb_correlation.sv",
    "fpga/orionq_pl/tb_fast_gate.sv",
    "fpga/orionq_pl/abi.md",
}
ROOT_FILES = {
    ".gitattributes", ".gitignore", "BASELINE_BEFORE.md", "LICENSE",
    "pyproject.toml", "pytest.ini", "README.md", "requirements.txt",
}
SCRATCH_PARTS = {
    ".git", ".agents", ".codex", ".aws", "__pycache__", "node_modules",
    ".vinext", ".wrangler", ".next", ".turbo", ".parcel-cache", ".cache",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".venv", "venv",
    "site-packages", "build", "dist", "state", "validation-state", "pytest-temp",
    ".security", ".dpapi", "sequence-current-inventory-state",
    "preflight-v4", "OrionQ-git", "Git-verification",
    "frozen-verifier-communicator-temp", "frozen-verifier-communicator-temp2",
    "repro-live-network-temp", "topology-login-final-temp", "OrionQ-git-rejected-20261010",
    "delivery-longpaths-temp",
}
SENSITIVE_SUFFIXES = {".pem", ".pfx", ".p12", ".key", ".sqlite", ".sqlite3", ".db"}
SOURCE_DISTRIBUTION_SUFFIXES = {".exe", ".dll", ".pyd", ".so", ".dylib", ".whl", ".zip", ".7z",
                                ".tar", ".gz", ".bit", ".vvp"}
# Two reviewed public test archives; arbitrary source ZIPs remain excluded.
# Hashes bind the existing inputs independently of their editable provenance file.
PINNED_TEST_ARCHIVES = {
    "tests/fixtures/qualification/zenodo-6384558-v1.1.0.zip":
        "895b3b093b33587d08c9d878c94597825ad0d83dae564a34e6455b673146f482",
    "tests/fixtures/qualification/sequence-network-20261010-public-fixture.zip":
        "e82235dbafd4d70515f0f9cec6722ee9c2667fb8129094536ee99c12680230c5",
}
EVIDENCE_SUFFIXES = {".json", ".xml", ".csv", ".md", ".png", ".gz"}
CURATED_JSONL = {
    "outputs/trl5_continuation_20261009/run1/performance-baseline/resources.jsonl",
}
PERFORMANCE_RESOURCE_FILES = {
    f"outputs/release-{version}-20261011/performance/{run}/resources.jsonl"
    for version in ("5.66.0", "5.66.1")
    for run in ("before-native", "after-native", "after-soak")
}
CURATED_JSONL |= PERFORMANCE_RESOURCE_FILES
CURATED_AUDIT_FILES = {
    "outputs/orionq_release_5_61_0_20261010/resume_windows_build.ps1",
    "outputs/orionq_release_5_61_0_20261010/resume-official-suffix.diff",
    "outputs/orionq_release_5_61_0_20261010/resume_windows_build_v2.ps1",
    "outputs/orionq_release_5_61_0_20261010/resume-official-suffix-v2.diff",
    "outputs/orionq_release_5_61_0_20261010/resume_windows_build_v3.ps1",
    "outputs/orionq_release_5_61_0_20261010/resume-official-suffix-v3.diff",
    "outputs/orionq_release_5_61_0_20261010/resume_windows_build_v4.ps1",
    "outputs/orionq_release_5_61_0_20261010/resume-official-suffix-v4.diff",
    "outputs/orionq_release_5_61_0_20261010/resume_windows_build_v5.ps1",
    "outputs/orionq_release_5_61_0_20261010/resume-official-suffix-v5.diff",
    "outputs/orionq_release_5_61_0_20261010/orionq_build_5_61_0_20261010_resume.log",
    "outputs/orionq_release_5_61_0_20261010/orionq_build_5_61_0_20261010_resume_v2.log",
    "outputs/orionq_release_5_61_0_20261010/orionq_build_5_61_0_20261010_resume_v3.log",
    "outputs/orionq_release_5_61_0_20261010/orionq_build_5_61_0_20261010_resume_v4.log",
    "outputs/orionq_release_5_61_0_20261010/orionq_build_5_61_0_20261010_resume_v5.log",
    "outputs/terminal_linux_20261011/pty-v3/terminal-campaign.txt",
    "outputs/terminal_linux_20261011/pty-v4/terminal-campaign.txt",
    "outputs/terminal_linux_20261011/full-campaign.txt",
    "outputs/terminal_linux_20261011/linux-gate/dependency-freeze.log",
    "outputs/terminal_brand_linux_20261011/terminal-campaign.txt",
    "outputs/terminal_brand_linux_20261011/tests.log",
    "outputs/terminal_brand_linux_20261011/artifact.log",
    "release/linux/5.64.2-native-headless-release/evidence/native-terminal-campaign.txt",
    "release/linux/5.64.2-native-headless-release/evidence/native-recheck/native-terminal-campaign.txt",
    "outputs/native_release_5_64_2_20261011/windows/bootstrap-continuation/official-tail.ps1",
    "outputs/native_release_5_64_2_20261011/windows/relocation-exit-code.txt",
    "outputs/native_release_5_64_2_20261011/windows/live-lifecycle-original-test.py",
}
CURATED_HISTORICAL_JUNITS = {
    "outputs/native_release_5_64_2_20261011/windows/bootstrap-continuation/failed-attempt/pytest-live-network-results.xml",
}
EVIDENCE_TREES = (
    "outputs/trl4_consolidation_20261009/run3",
    "outputs/trl5_continuation_20261009/run1",
    "outputs/trl5_initiation_20261009/run5",
    "outputs/cross_simulators_20261010/run2",
    "outputs/sequence_network_20261010/run1",
    "outputs/hybrid_quantum_20261010/run1",
    "outputs/hybrid_remediation_20261010/run3",
    "outputs/orionq_release_5_61_0_20261010",
)
EXTRA_EVIDENCE = (
    "outputs/hybrid_remediation_preparation_20261010/integration-remediation-final.xml",
    "outputs/hybrid_remediation_preparation_20261010/integration-review-summary.json",
    "outputs/hybrid_remediation_preparation_20261010/runner-checks1/tests.xml",
    "outputs/hybrid_remediation_preparation_20261010/space-regression2.xml",
    "outputs/hybrid_remediation_preparation_20261010/space-remediation-evidence.json",
    "outputs/hybrid_remediation_preparation_20261010/space-tests3.xml",
    "outputs/hybrid_remediation_regression_20261010/run1/regression.xml",
    "outputs/hybrid_remediation_regression_20261010/global-run1/regression.xml",
    "outputs/hybrid_remediation_regression_20261010/migration-run2/regression.xml",
    "outputs/hybrid_remediation_regression_20261010/final-run2/regression.xml",
    "outputs/git_release_preparation_20261010/tests3.xml",
    "outputs/git_release_preparation_20261010/tests6.xml",
    "outputs/git_release_preparation_20261010/tests7.xml",
    "outputs/git_release_preparation_20261010/tests8.xml",
    "outputs/git_release_preparation_20261010/tests10.xml",
    "outputs/git_release_preparation_20261010/tests12.xml",
    "outputs/git_release_preparation_20261010/tests13.xml",
    "outputs/git_release_preparation_20261010/tests14.xml",
    "outputs/git_release_preparation_20261010/tests15.xml",
    "outputs/git_release_preparation_20261010/tests16.xml",
    "outputs/git_release_preparation_20261010/tests17.xml",
    "outputs/git_release_preparation_20261010/tests18.xml",
    "outputs/trl5_continuation_20261009/run1/performance-baseline/resources.jsonl",
    "outputs/orionq_release_5_61_0_20261010/resume_windows_build.ps1",
    "outputs/orionq_release_5_61_0_20261010/resume-official-suffix.diff",
    "outputs/orionq_release_5_61_0_20261010/resume_windows_build_v2.ps1",
    "outputs/orionq_release_5_61_0_20261010/resume-official-suffix-v2.diff",
    "outputs/orionq_release_5_61_0_20261010/resume_windows_build_v3.ps1",
    "outputs/orionq_release_5_61_0_20261010/resume-official-suffix-v3.diff",
    "outputs/orionq_release_5_61_0_20261010/resume_windows_build_v4.ps1",
    "outputs/orionq_release_5_61_0_20261010/resume-official-suffix-v4.diff",
    "outputs/orionq_release_5_61_0_20261010/resume_windows_build_v5.ps1",
    "outputs/orionq_release_5_61_0_20261010/resume-official-suffix-v5.diff",
    "outputs/orionq_release_5_61_0_20261010/orionq_build_5_61_0_20261010_resume.log",
    "outputs/orionq_release_5_61_0_20261010/orionq_build_5_61_0_20261010_resume_v2.log",
    "outputs/orionq_release_5_61_0_20261010/orionq_build_5_61_0_20261010_resume_v3.log",
    "outputs/orionq_release_5_61_0_20261010/orionq_build_5_61_0_20261010_resume_v4.log",
    "outputs/orionq_release_5_61_0_20261010/orionq_build_5_61_0_20261010_resume_v5.log",
    # Public results are selected nominally; adjacent service state stays local.
    "outputs/controller_contention_20261011/controller-benchmark.json",
    "outputs/controller_contention_20261011/global-scheduler.json",
    "outputs/controller_contention_20261011/mixed-atomicity.json",
    "outputs/controller_contention_20261011/legacy-pair-race.json",
    "outputs/controller_contention_20261011/delivery-integrity.json",
    "outputs/terminal_linux_20261011/baseline-tests.xml",
    "outputs/terminal_linux_20261011/baseline-expanded-tests.xml",
    "outputs/terminal_linux_20261011/windows-terminal.xml",
    "outputs/terminal_linux_20261011/full-campaign.json",
    "outputs/terminal_linux_20261011/full-campaign.txt",
    "outputs/terminal_linux_20261011/linux-gate/report.json",
    "outputs/terminal_linux_20261011/linux-gate/tests.xml",
    "outputs/terminal_linux_20261011/linux-gate/source-hashes.json",
    "outputs/terminal_linux_20261011/linux-gate/core-artifact.json",
    "outputs/terminal_linux_20261011/linux-gate/installed-core-report.json",
    "outputs/terminal_linux_20261011/linux-gate/cli-smoke.json",
    "outputs/terminal_linux_20261011/linux-gate/dependency-freeze.log",
    "outputs/terminal_linux_20261011/pty-v3/terminal-campaign.json",
    "outputs/terminal_linux_20261011/pty-v3/terminal-campaign.txt",
    "outputs/terminal_linux_20261011/pty-v4/terminal-campaign.json",
    "outputs/terminal_linux_20261011/pty-v4/terminal-campaign.txt",
    "outputs/terminal_linux_20261011/delivery-integrity.json",
    "outputs/terminal_brand_linux_20261011/report.json",
    "outputs/terminal_brand_linux_20261011/tests.xml",
    "outputs/terminal_brand_linux_20261011/tests.log",
    "outputs/terminal_brand_linux_20261011/artifact.log",
    "outputs/terminal_brand_linux_20261011/terminal-campaign.json",
    "outputs/terminal_brand_linux_20261011/terminal-campaign.txt",
    "outputs/terminal_brand_linux_20261011/delivery-integrity.json",
    "outputs/terminal_brand_linux_20261011/previews/overview.png",
    "outputs/terminal_brand_linux_20261011/previews/satellites.png",
    "outputs/terminal_brand_linux_20261011/previews/preview-integrity.json",
    "outputs/git_release_preparation_20261011/package-tests.xml",
    "outputs/git_release_preparation_20261011/native-evidence-preflight.json",
    "outputs/git_release_preparation_20261011/package-tests.xml",
    "release/linux/5.64.2-native-headless-release/build-report.json",
    "release/linux/5.64.2-native-headless-release/evidence/native-terminal-campaign.json",
    "release/linux/5.64.2-native-headless-release/evidence/native-terminal-campaign.txt",
    "release/linux/5.64.2-native-headless-release/evidence/gateway-smoke.json",
    "release/linux/5.64.2-native-headless-release/evidence/campaign-provenance.json",
    "release/linux/5.64.2-native-headless-release/evidence/bundle-files.json",
    "release/linux/5.64.2-native-headless-release/evidence/build-environment.json",
    "release/linux/5.64.2-native-headless-release/evidence/archive-smoke.json",
    "release/linux/5.64.2-native-headless-release/evidence/wheel-source-check.json",
    "release/linux/5.64.2-native-headless-release/evidence/installed-wheel-source.json",
    "release/linux/5.64.2-native-headless-release/evidence/tool-safety-checks.json",
    "release/linux/5.64.2-native-headless-release/evidence/native-bundle-recheck.json",
    "release/linux/5.64.2-native-headless-release/evidence/native-recheck/bundle-files-before.json",
    "release/linux/5.64.2-native-headless-release/evidence/native-recheck/campaign-provenance.json",
    "release/linux/5.64.2-native-headless-release/evidence/native-recheck/gateway-smoke.json",
    "release/linux/5.64.2-native-headless-release/evidence/native-recheck/native-terminal-campaign.json",
    "release/linux/5.64.2-native-headless-release/evidence/native-recheck/native-terminal-campaign.txt",
    "release/linux/5.64.2-native-gui-bootstrap-release/build-report.json",
    "release/linux/5.64.2-native-gui-bootstrap-release/evidence/archive-smoke.json",
    "release/linux/5.64.2-native-gui-bootstrap-release/evidence/build-environment.json",
    "release/linux/5.64.2-native-gui-bootstrap-release/evidence/bundle-files.json",
    "release/linux/5.64.2-native-gui-bootstrap-release/evidence/gui-self-test-checks.json",
    "release/linux/5.64.2-native-gui-bootstrap-release/evidence/gui-startup.json",
    "release/linux/5.64.2-native-gui-bootstrap-release/evidence/gui-screenshots.json",
    "release/linux/5.64.2-native-gui-bootstrap-release/evidence/gui-frozen-source-linux.json",
    "release/linux/5.64.2-native-gui-bootstrap-release/evidence/gui-frozen-source-windows-verifier.json",
    "release/linux/5.64.2-native-gui-bootstrap-release/evidence/gui-public-input-scan.json",
    "release/linux/5.64.2-native-gui-bootstrap-release/evidence/installed-wheel-source.json",
    "release/linux/5.64.2-native-gui-bootstrap-release/evidence/gui-remote-smoke-final.json",
    "release/linux/5.64.2-native-gui-bootstrap-release/evidence/gui-bootstrap-source-binding.json",
    "release/linux/5.64.2-native-gui-bootstrap-release/evidence/gui-bootstrap-qualification.json",
    "release/linux/5.64.2-native-gui-bootstrap-release/evidence/gui-console-boot.png",
    "outputs/native_release_5_64_2_20261011/windows/environment.json",
    "outputs/native_release_5_64_2_20261011/windows/dependencies.json",
    "outputs/native_release_5_64_2_20261011/windows/selected-dependencies.json",
    "outputs/native_release_5_64_2_20261011/windows/ledger-batch-timing.json",
    "outputs/native_release_5_64_2_20261011/windows/lab-multiprocess-reproduction.xml",
    "outputs/native_release_5_64_2_20261011/windows/lab-multiprocess-os-temp.xml",
    "outputs/native_release_5_64_2_20261011/windows/bootstrap-regressions.xml",
    "outputs/native_release_5_64_2_20261011/windows/bootstrap-regressions-attempt-fixture-api.xml",
    "outputs/native_release_5_64_2_20261011/windows/frozen-tool-regressions.xml",
    "outputs/native_release_5_64_2_20261011/windows/build-bootstrap-context.json",
    "outputs/native_release_5_64_2_20261011/windows/campaign-topology-diagnostic.json",
    "outputs/native_release_5_64_2_20261011/windows/live-lifecycle-original.xml",
    "outputs/native_release_5_64_2_20261011/windows/live-lifecycle-fixed.xml",
    "outputs/native_release_5_64_2_20261011/windows/live-lifecycle-original-source.json",
    "outputs/native_release_5_64_2_20261011/windows/live-lifecycle-original-test.py",
    "outputs/native_release_5_64_2_20261011/windows/live-network-interruption.json",
    "outputs/native_release_5_64_2_20261011/windows/bootstrap-continuation/provenance.json",
    "outputs/native_release_5_64_2_20261011/windows/bootstrap-continuation/official-tail.ps1",
    "outputs/native_release_5_64_2_20261011/windows/bootstrap-continuation/failed-attempt-resolution.json",
    "outputs/native_release_5_64_2_20261011/windows/bootstrap-continuation/failed-attempt/pytest-live-network-results.xml",
    "outputs/native_release_5_64_2_20261011/windows/frozen-source.json",
    "outputs/native_release_5_64_2_20261011/windows/native-release-consolidation.json",
    "outputs/native_release_5_64_2_20261011/windows/native-release-selected-test-summary.json",
    "outputs/native_release_5_64_2_20261011/windows/native-delivery-summary.json",
    "outputs/native_release_5_64_2_20261011/windows/relocation-context.json",
    "outputs/native_release_5_64_2_20261011/windows/relocation-exit-code.txt",
    "outputs/native_release_5_64_2_20261011/windows/supplementary-bootstrap/tests.xml",
    "outputs/native_release_5_64_2_20261011/windows/supplementary-bootstrap/context.json",
)
# New releases add exact public reports, never an output directory glob. A
# missing optional report is not treated as a current validation approval.
EXTRA_EVIDENCE += (
    "outputs/release-5.65.0-20261011/windows/environment.json",
    "outputs/release-5.65.0-20261011/windows/runtime-versions.json",
    "outputs/release-5.65.0-20261011/windows/source-freeze.json",
    "outputs/release-5.65.0-20261011/windows/source-security-scan.json",
    "outputs/release-5.65.0-20261011/windows/dashboard-validation.json",
    "outputs/release-5.65.0-20261011/windows/dashboard-validation-generated-cache-comparison.json",
    "outputs/release-5.65.0-20261011/windows/geo-metadata-encoding-audit.json",
    "outputs/release-5.65.0-20261011/git/notes-cache-regressions.xml",
    "outputs/release-5.65.0-20261011/git/package-contracts-final.json",
    *(f"outputs/release-5.65.0-20261011/windows/native-ui-campaign/{name}"
      for name in (
          "report.json", "manifest-before.json", "evidence-copy.json",
          "communicator-satellite.png", "communicator-terrestrial.png", "detail-CBPF.png",
          "detail-CIMATEC.png", "detail-GEO-AMERICAS-SIM.png", "fpga-emulated.png",
          "native-entry.png", "screen-audit.png", "screen-communicators.png", "screen-fpga.png",
          "screen-keys.png", "screen-node.png", "screen-telemetry.png", "screen-topology.png",
      )),
    "outputs/release-5.65.0-20261011/windows/native-release-consolidation.json",
    "outputs/release-5.65.0-20261011/windows/native-release-selected-test-summary.json",
    "outputs/release-5.65.0-20261011/windows/frozen-source.json",
    "outputs/release-5.65.0-20261011/windows/native-delivery-summary.json",
    "outputs/release-5.65.0-20261011/audit/collection-summary.json",
    "outputs/release-5.65.0-20261011/audit/supplementary-summary.json",
    "outputs/release-5.65.0-20261011/audit/optimized-assert-audit.json",
    "outputs/release-5.65.0-20261011/audit/final-audit.json",
    "outputs/release-5.65.0-20261011/audit/concurrency-summary.json",
    "outputs/release-5.65.0-20261011/audit/legacy-pair-race.json",
    "outputs/release-5.65.0-20261011/audit/mixed-atomicity.json",
    "outputs/release-5.65.0-20261011/audit/source-security-audit.json",
    "outputs/release-5.65.0-20261011/audit/durability-final-summary.json",
    "outputs/release-5.65.0-20261011/audit/scientific-summary.json",
    *(f"outputs/release-5.65.0-20261011/audit/native-viewport/{name}"
      for name in (
          "summary.json", "viewport-CIMATEC.json", "viewport-CBPF.json",
          "viewport-GEO-AMERICAS-SIM.json", "evidence-copy.json", "native-entry.png",
          "detail-CIMATEC.png", "detail-CBPF.png", "detail-GEO-AMERICAS-SIM.png",
          "node-CIMATEC-top.png", "node-CIMATEC-bottom.png", "node-CIMATEC-top-restored.png",
          "node-CBPF-top.png", "node-CBPF-bottom.png", "node-CBPF-top-restored.png",
          "node-GEO-AMERICAS-SIM-top.png", "node-GEO-AMERICAS-SIM-bottom.png",
          "node-GEO-AMERICAS-SIM-top-restored.png", "housekeeping-GEO-AMERICAS-SIM-top.png",
          "housekeeping-GEO-AMERICAS-SIM-bottom.png", "housekeeping-GEO-AMERICAS-SIM-top-restored.png",
      )),
    "outputs/release-5.65.0-20261011/audit/security.xml",
    "outputs/release-5.65.0-20261011/audit/security-console.xml",
    "outputs/release-5.65.0-20261011/audit/portable-core.xml",
    "outputs/release-5.65.0-20261011/audit/terminal.xml",
    "outputs/release-5.65.0-20261011/audit/durability.xml",
    "outputs/release-5.65.0-20261011/audit/scientific-invariants.xml",
    "outputs/release-5.65.0-20261011/audit/package-contracts.xml",
    *(f"outputs/release-5.65.0-20261011/audit/final-junit/{name}.xml"
      for name in (
          "security", "security-console", "portable-core", "terminal", "durability",
          "scientific-invariants", "package-contracts", "ui-console_disconnect_signal",
          "ui-console_emergency_signal", "ui-console_explicit_exports", "ui-console_floating_tick",
          "ui-console_fpga_devices", "ui-console_geo_update_cadence", "ui-console_link_text_widths",
          "ui-console_loading_progress", "ui-console_local_job_identity", "ui-console_login_flow",
          "ui-console_metric_table", "ui-console_node_incremental", "ui-console_node_window_lifecycle",
          "ui-console_presentation_wait", "ui-console_product_entry", "ui-console_record_tables",
          "ui-console_remote", "ui-console_satellite_incremental", "ui-console_signal_brand",
          "ui-console_signal_geo", "ui-console_startup_login", "ui-console_telemetry_controls",
          "ui-console_transmission_cache", "ui-console_wrapping_fields", "ui-native_login",
      )),
    "outputs/release-5.65.0-20261011/linux/version-regression-windows.xml",
    "outputs/release-5.65.0-20261011/linux/version-regression-final-linux.xml",
    "outputs/release-5.65.0-20261011/linux/version-regression-final-windows.xml",
    "outputs/release-5.65.0-20261011/linux/source-tests-summary.json",
    "outputs/release-5.65.0-20261011/linux/focused-tests.xml",
    "outputs/release-5.65.0-20261011/linux/optional-tests.xml",
    "outputs/release-5.65.0-20261011/linux/linux-preparation.json",
    "outputs/release-5.65.0-20261011/linux/linux-release-qualification.json",
    "outputs/release-5.65.0-20261011/linux/linux-delivery-summary.json",
    "outputs/release-5.65.0-20261011/linux/native-eva-campaign.json",
    "outputs/release-5.65.0-20261011/linux/native-eva-provenance.json",
    "outputs/release-5.65.0-20261011/linux/gui-frozen-source-linux.json",
    "outputs/release-5.65.0-20261011/linux/archive-headless-smoke.json",
    "outputs/release-5.65.0-20261011/linux/archive-gui-smoke.json",
    "outputs/release-5.65.0-20261011/linux/gui-remote-smoke.json",
    "outputs/release-5.65.0-20261011/linux/gui-wayland-startup.json",
    "outputs/release-5.65.0-20261011/linux/gui-default-display.json",
    "outputs/release-5.65.0-20261011/linux/core/report.json",
    "outputs/release-5.65.0-20261011/linux/core/tests.xml",
    "outputs/release-5.65.0-20261011/linux/core/source-hashes.json",
    "outputs/release-5.65.0-20261011/linux/core/core-artifact.json",
    "outputs/release-5.65.0-20261011/linux/core/cli-smoke.json",
    "outputs/release-5.65.0-20261011/linux/core/installed-core-report.json",
    "release/linux/5.65.0/core-artifact.json",
    *(f"release/linux/5.65.0-native-{profile}-20261011/{name}"
      for profile in ("headless", "gui")
      for name in (
          "build-report.json", "evidence/installed-wheel-source.json",
          "evidence/bundle-files.json", "evidence/build-environment.json",
          "evidence/gateway-smoke.json", "evidence/native-terminal-campaign.json",
          "evidence/campaign-provenance.json", "evidence/gui-self-test-checks.json",
          "evidence/gui-startup.json", "evidence/gui-screenshots.json",
          "evidence/gui-public-input-scan.json",
      )),
)
RELEASE_566_PUBLIC = {
    *(f"outputs/release-5.66.0-20261011/concurrency/{name}" for name in (
        "summary.json", "windows-qualified.xml", "linux-qualified.xml",
        "windows-final-regressions.xml", "linux-final-regressions.xml",
        "api-binding-before.xml",
    )),
    *(f"outputs/release-5.66.0-20261011/science/{name}" for name in (
        "science-summary.json", "before-source-hashes.json", "before-normal.json",
        "before-optimized.json", "after-profile-scientific-0.json",
        "after-profile-scientific-1.json", "after-profile-scientific-2.json",
        "regression-approved.xml", "regression-final.xml",
        "compatibility-approved.xml", "compatibility-native-capture.xml",
    )),
    *(f"outputs/release-5.66.0-20261011/visual/{name}" for name in (
        "summary.json", "component-junit-final4.xml",
        "readability-scale-125.xml", "readability-scale-150.xml", "readability-scale-200.xml",
        "baseline-565-regression.xml", "geodata-metadata-repair.json",
        "theme-restore-regression.xml", "theme-restore-regression-final.xml",
        "theme-restore-diagnostic.json", "theme-restore-official-test-final.xml",
        "windows-visual-topology-final.xml", "summary-corrected-r2.json",
        "windows-visual-topology-corrected-r2.xml", "calibration-readback-diagnostic.json",
        "calibration-readback-before-settle.xml",
        "display-audit-final.xml", "command-wait-diagnostic.json",
        "summary-corrected-r2-supplemental.json",
    )),
    *(f"outputs/release-5.66.0-20261011/visual/command-wait/{name}" for name in (
        "official-r2-display-audit-failed.xml", "original-qt20-failed.xml",
        "sleep20.xml", "qt1-sleep20.xml", "qt20-sleep100.xml",
    )),
    *(f"outputs/release-5.66.0-20261011/visual/{campaign}/{name}"
      for campaign in ("native-viewport-final", "native-scale-200",
                       "native-viewport-corrected-r2", "native-scale-200-corrected-r2") for name in (
        "report.json", "copy-proof.json", "native-entry.png", "detail-CIMATEC.png",
        "detail-CBPF.png", "detail-GEO-AMERICAS-SIM.png",
        *(f"node-{node}-{position}.png" for node in ("CIMATEC", "CBPF", "GEO-AMERICAS-SIM")
          for position in ("top", "bottom", "top-restored")),
        *(f"housekeeping-GEO-AMERICAS-SIM-row-{row}.png" for row in (0, 4, 5)),
    )),
    *(f"outputs/release-5.66.0-20261011/performance/{name}" for name in (
        "comparison.json", "pytest-layout-v2.xml", "kv-parent-prototype.json", "kv-parent-after.json",
        "kv-layout-final.json",
    )),
    *(f"outputs/release-5.66.0-20261011/performance/{run}/{name}"
      for run in ("before-profile", "before-local-profile", "after-local-profile", "before-native",
                  "after-source-native", "after-native", "after-soak")
      for name in ("report.json", "launcher.json", "configuration.json", "scientific-context.json",
                   "heartbeat.csv", "components.csv", "paints.csv", "methods.csv",
                   "action-plan.json", "actions.json", "actions.csv", "resources.csv", "collector-accounting.json")),
    *(f"outputs/release-5.66.0-20261011/windows/{name}" for name in (
        "environment.json", "runtime-versions.json", "source-freeze.json", "source-security-scan.json",
        "dashboard-validation.json", "native-release-consolidation.json",
        "source-freeze-corrected-r2.json", "source-freeze-corrected-r3.json",
        "source-freeze-corrected-r4.json", "attempt3-aborted-summary.json",
        "attempt1-failed-summary.json", "attempt2-failed-summary.json",
        "native-release-selected-test-summary.json", "frozen-source.json", "native-delivery-summary.json",
        "release-reader-tests-native.xml", "release-reader-tests.xml",
    )),
    *(f"outputs/release-5.66.0-20261011/linux/{name}" for name in (
        "environment-preflight.json", "source-tests-summary.json", "focused-tests.xml",
        "linux-release-qualification.json", "linux-delivery-summary.json",
        "native-eva-campaign.json", "native-eva-provenance.json", "gui-frozen-source-linux.json",
        "native-eva-campaign-retry-1.json", "native-eva-provenance-retry-1.json",
        "archive-headless-smoke.json", "archive-gui-smoke.json", "gui-remote-smoke.json",
        "gui-wayland-startup.json", "gui-default-display.json", "gui-ui-pipeline-native.json",
        "gui-xwayland-startup.json", "x11-private-dependencies.json",
        "gui-ui-pipeline-xwayland.json", "gui-ui-pipeline-xwayland-pixel-inspection.json",
        "gui-xvfb-diagnostic.json",
        "build-tool-contracts.xml",
        "visual-regression.xml", "visual-regression-summary.json", "wheel-preparation.json",
        "visual-regression-final.xml", "visual-regression-final-summary.json",
        "source-tests.xml", "source-tests-latest-summary.json",
        "gateway-short-path-regression.xml", "gateway-short-path-regression.json",
    )),
    *(f"outputs/release-5.66.0-20261011/linux/visual-captures/{name}" for name in (
        "housekeeping-dark-640.png", "housekeeping-dark-640-updated.png",
    )),
    *(f"outputs/release-5.66.0-20261011/linux/visual-captures-final/{name}" for name in (
        "housekeeping-dark-640.png", "housekeeping-dark-640-updated.png",
    )),
    *(f"outputs/release-5.66.0-20261011/linux/core/{name}" for name in (
        "report.json", "tests.xml", "source-hashes.json", "core-artifact.json", "cli-smoke.json",
        "installed-core-report.json",
    )),
    *(f"outputs/release-5.66.0-20261011/git/{name}" for name in (
        "package-contracts.xml", "package-contracts-final.xml", "package-contracts-final.json",
        "package-contracts-combined.xml", "package-contracts-linux.xml", "notes-cache-regressions.xml",
        "package-contracts-closure.xml",
        "package-contracts-resources.xml",
        "package-contracts-notes-final.xml",
        "package-contracts-linux-final.xml",
        "package-r2-nominal.xml",
        "package-r3-nominal.xml", "package-r4-nominal.xml", "original-git-external-change.json",
        "package-contracts-windows-current.xml", "package-contracts-linux-current.xml",
    )),
}
RELEASE_566_PUBLIC |= {name for name in PERFORMANCE_RESOURCE_FILES if '/release-5.66.0-' in name}
RELEASE_566_PUBLIC |= {
    f"outputs/release-5.66.0-20261011/windows/{attempt}/{name}"
    for attempt in ("corrected-r2", "corrected-r3", "corrected-r4") for name in (
        "environment.json", "runtime-versions.json", "source-security-scan.json",
        "source-freeze.json", "source-freeze-corrected-r2.json", "source-freeze-corrected-r3.json",
        "source-freeze-corrected-r4.json",
        "dashboard-validation.json", "dashboard-validation-final.json",
        "dashboard-validation-offline-diagnostic.json",
        "delivery-worker.json", "native-release-consolidation.json",
        "native-release-selected-test-summary.json", "frozen-source.json",
        "native-delivery-summary.json", "release-reader-tests-native.xml", "release-reader-regression.xml",
        "build-dispatch-native.json", "test-inputs-before.json", "test-inputs-after.json",
        "reader-static-contracts-final.xml", "reader-static-contracts-final-summary.json",
        "native-consolidation.json", "consolidation-rejected-summary.json",
        "scientific-input-validation-original.json", "official-build-worker-receipt.json",
    )
}
RELEASE_566_PUBLIC |= {
    f"outputs/release-5.66.0-20261011/windows/corrected-r4-final2/{name}"
    for name in ("delivery-worker.json", "frozen-source-verification.json", "native-consolidation.json")
}
RELEASE_566_PUBLIC |= {
    f"outputs/release-5.66.0-20261011/linux/corrected-r2/visual-captures-final/{name}"
    for name in ("housekeeping-dark-640.png", "housekeeping-dark-640-updated.png")
}
RELEASE_566_PUBLIC |= {
    f"outputs/release-5.66.0-20261011/linux/corrected-r2/{name}" for name in (
        "source-tests.xml", "source-tests-summary.json", "source-tests-latest-summary.json",
        "gateway-short-path-regression.xml", "gateway-short-path-regression.json",
        "visual-regression.xml", "visual-regression-summary.json", "wheel-preparation.json",
        "visual-regression-final.xml", "visual-regression-final-summary.json",
        "linux-release-qualification.json", "linux-delivery-summary.json",
        "native-eva-campaign.json", "native-eva-provenance.json",
        "native-eva-campaign-retry-1.json", "native-eva-provenance-retry-1.json",
        "archive-headless-smoke.json", "archive-gui-smoke.json", "gui-frozen-source-linux.json",
        "gui-remote-smoke.json", "gui-default-display.json", "gui-default-display-retry-1.json",
        "gui-wayland-startup.json",
        "gui-xwayland-startup.json", "x11-private-dependencies.json",
        "gui-ui-pipeline-native.json", "gui-ui-pipeline-xwayland.json",
        "gui-ui-pipeline-xwayland-pixel-inspection.json", "gui-xvfb-diagnostic.json",
        "build-tool-contracts.xml", "build-tool-contracts-r2.xml",
        "final-ui-fixture-regression.xml", "final-ui-fixture-regression-retry-1.xml",
        "final-ui-fixture-regression.json", "final-ui-fixture-regression-retry-1.json",
        "display-audit-fixture-final.xml", "display-audit-fixture-final-summary.json",
        "runtime-manifest-tool-final.xml", "runtime-manifest-tool-final-summary.json",
        "reader-static-contracts-final.xml", "reader-static-contracts-final-summary.json",
    )
}
EXTRA_EVIDENCE += tuple(sorted(RELEASE_566_PUBLIC)) + (
    "release/linux/5.66.0/core-artifact.json",
    *(f"release/linux/5.66.0-native-{profile}-20261011/{name}"
      for profile in ("headless", "gui")
      for name in (
          "build-report.json", "evidence/installed-wheel-source.json", "evidence/bundle-files.json",
          "evidence/build-environment.json", "evidence/gateway-smoke.json",
          "evidence/native-terminal-campaign.json", "evidence/campaign-provenance.json",
          "evidence/gui-self-test-checks.json", "evidence/gui-startup.json", "evidence/gui-screenshots.json",
          "evidence/gui-public-input-scan.json", "evidence/scientific-input-validation.json",
      )),
    *(f"release/linux/5.66.0-native-{profile}-20261011-corrected-r2/{name}"
      for profile in ("headless", "gui")
      for name in (
          "build-report.json", "evidence/installed-wheel-source.json", "evidence/bundle-files.json",
          "evidence/build-environment.json", "evidence/gateway-smoke.json",
          "evidence/native-terminal-campaign.json", "evidence/campaign-provenance.json",
          "evidence/gui-self-test-checks.json", "evidence/gui-startup.json", "evidence/gui-screenshots.json",
          "evidence/gui-public-input-scan.json", "evidence/scientific-input-validation.json",
      )),
)
BUILD_RESOURCES = (
    "outputs/qkd_coexistence_measured/experimental_cells.csv",
    "outputs/wdm_model_bundle_v527/edge_lstm_dual_head_uniform_v1.wdmbundle.npz",
)
REQUIRED_EVIDENCE = (
    "outputs/trl4_consolidation_20261009/run3/assessment.json",
    "outputs/trl4_consolidation_20261009/run3/freeze.json",
    "outputs/trl4_consolidation_20261009/run3/group-core.xml",
    "outputs/trl4_consolidation_20261009/run3/group-integration.xml",
    "outputs/trl4_consolidation_20261009/run3/group-qt.xml",
    "outputs/trl5_continuation_20261009/run1/assessment.json",
    "outputs/trl5_continuation_20261009/run1/freeze.json",
    "outputs/cross_simulators_20261010/run2/report.json",
    "outputs/cross_simulators_20261010/run2/freeze.json",
    "outputs/sequence_network_20261010/run1/report.json",
    "outputs/sequence_network_20261010/run1/freeze.json",
    "outputs/hybrid_quantum_20261010/run1/report.json",
    "outputs/hybrid_quantum_20261010/run1/freeze.json",
    "outputs/hybrid_remediation_20261010/run3/report.json",
    "outputs/hybrid_remediation_20261010/run3/source-freeze.json",
    "outputs/hybrid_remediation_20261010/run3/validation-manifest.json",
    "outputs/hybrid_remediation_20261010/run3/case-matrix.csv",
)
DOC_PATTERN = re.compile(r"(?:trl[45]_.*|cross_simulators.*|sequence_network.*|hybrid_.*|space_optical.*)")
MARKDOWN_LINK = re.compile(r"\]\(([^)]+)\)")
PRIVATE_HEADER = re.compile(rb"(?m)^-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----\r?$")
SERVICE_TOKEN = re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{20,})")
RAW_EVIDENCE_NAMES = {"live.json", "input.json", "observations.json", "transcript.json",
                      "global-events.json", "events.json", "raw.json", "snapshots.json",
                      "keys.json", "credentials.json"}
RELEASE_REPORTS = {
    "self-test.json", "portable-package.json", "branding-validation/branding-report.json",
    "desktop-behavior-validation/report.json", "roadmap-validation/report.json",
    "optional-models-validation/optional-models-report.json", "campaign-validation/report.json",
    "laboratory-validation/report.json", "satellite-validation/report.json",
    "satellite-mission-validation/report.json", "global-validation/report.json",
    "startup-validation/report.json", "native-acceptance/acceptance-report.json",
    "delivery-copy.json", "relocation-acceptance/acceptance-report.json",
    "scientific-input-validation.json",
}
RELEASE_SELF_TESTS = {"self-test.json", "native-acceptance/self-test.json", "relocation-acceptance/self-test.json"}
RELEASE_JUNIT_REPORTS = {
    "pytest-controller-concurrency-results.xml", "pytest-scientific-input-results.xml",
    "pytest-tick-layout-results.xml", "pytest-visual-readability-results.xml",
    "pytest-channel-intake-results.xml", "pytest-commands-results.xml",
    "pytest-communicator-forms-results.xml", "pytest-compensation-shadow-results.xml",
    "pytest-console-results.xml", "pytest-desktop-identity-results.xml",
    "pytest-display-audit-results.xml", "pytest-display-live-results.xml",
    "pytest-display-readers-results.xml", "pytest-display-results.xml",
    "pytest-geo-modes-results.xml", "pytest-global-network-results.xml",
    "pytest-institutional-results.xml", "pytest-laboratory-results.xml",
    "pytest-live-network-results.xml", "pytest-modular-results.xml",
    "pytest-optional-results.xml", "pytest-repeaters-results.xml", "pytest-results.xml",
    "pytest-roadmap-results.xml", "pytest-satellite-mission-results.xml",
    "pytest-satellite-v2-results.xml", "pytest-startup-results.xml",
    "pytest-sync-shadow-results.xml", "pytest-terminal-results.xml",
    "pytest-window-chrome-results.xml",
}
RELEASE_566_FAILED_ATTEMPTS = {
    *(f"outputs/release-5.66.0-20261011/windows/{attempt}/{name}"
      for attempt in ("attempt1", "attempt2", "attempt3")
      for name in ("build-worker-receipt.json", "delivery-worker.json", "source-freeze.json")),
    *(f"outputs/release-5.66.0-20261011/windows/{attempt}/{name.removeprefix('pytest-')}"
      for attempt in ("attempt1", "attempt2", "attempt3") for name in RELEASE_JUNIT_REPORTS),
}
RELEASE_566_FAILED_ATTEMPTS |= {
    f"outputs/release-5.66.0-20261011/windows/attempt3/{name}" for name in (
        "test-inputs-before.json", "diagnostic-provenance-restoration.json",
        "runtime-diagnostic-manifest-generated.json",
        "runtime-manifest-tool-tests.xml", "runtime-manifest-tool-tests-final.xml",
    )
}
RELEASE_566_PUBLIC |= RELEASE_566_FAILED_ATTEMPTS
EXTRA_EVIDENCE += tuple(sorted(RELEASE_566_FAILED_ATTEMPTS))
RELEASE_5661_PUBLIC = {
    *(f"outputs/release-5.66.1-20261011/watchdog/{name}" for name in (
        "summary.json", "before-source.json", "after-source.json",
        "focused-windows-attempt1.xml", "focused-windows-attempt2.xml",
        "focused-windows-attempt3.xml", "focused-windows-final.xml",
        "reproducer-contracts.xml", "reproducer-smoke/report.json",
        "build-gate.json", "build-gate-final.json", "build-gate-contracts.xml",
    )),
    *(f"outputs/release-5.66.1-20261011/visual/{name}" for name in (
        "controls-prototype-first.xml", "controls-prototype-final.xml",
        "controls-signals-regression.xml", "controls-signals-regression-final.xml",
        "controls-signals-summary.json",
    )),
    *(f"outputs/release-5.66.1-20261011/windows/{name}" for name in (
        "source-freeze.json", "reader-contracts.xml", "reader-contracts-native.xml",
        "display-audit-prerequisite.xml", "dashboard-validation.json",
    )),
    *(f"outputs/release-5.66.1-20261011/windows/build-1/{name}" for name in (
        "test-inputs-before.json", "test-inputs-after.json", "official-build-worker-receipt.json",
    )),
    *(f"outputs/release-5.66.1-20261011/windows/finalization/{name}" for name in (
        "frozen-source-verification.json", "native-consolidation.json", "delivery-worker.json",
    )),
    *(f"outputs/release-5.66.1-20261011/performance/{name}" for name in (
        "comparison.json", "comparison-review.json", "paint-science-review.json",
    )),
    *(f"outputs/release-5.66.1-20261011/performance/{run}/{name}"
      for run in ("before-native", "after-native", "after-soak") for name in (
        "report.json", "launcher.json", "configuration.json", "scientific-context.json",
        "heartbeat.csv", "components.csv", "paints.csv", "methods.csv",
        "action-plan.json", "actions.json", "actions.csv", "resources.csv", "collector-accounting.json",
    )),
    *(f"outputs/release-5.66.1-20261011/linux/{name}" for name in (
        "wheel-preparation.json", "source-tests.xml", "source-tests-summary.json",
        "visual-regression-final.xml", "visual-regression-final-summary.json",
        *(f"{group}-tests{suffix}" for group in ("policy", "audit", "tools", "package")
          for suffix in (".xml", "-summary.json")),
        "scientific-static-linked.json", "linux-release-qualification.json", "linux-delivery-summary.json",
        "gui-build-wrapper.json", "headless-build-wrapper.json", "qualification-wrapper.json",
        "gui-new-fixture-cleanup.json", "gui-ui-pipeline-xwayland.json",
        "gui-ui-pipeline-xwayland-pixel-inspection.json", "x11-private-dependencies.json",
        "environment-preflight.json", "source-tests-latest-summary.json", "campaign-execution.json",
        "archive-headless-smoke.json", "archive-gui-smoke.json", "gui-frozen-source-linux.json",
        "gui-remote-smoke.json", "gui-wayland-startup.json", "gui-xwayland-startup.json",
        "gui-default-display.json", "native-eva-campaign.json", "native-eva-provenance.json",
    )),
    *(f"outputs/release-5.66.1-20261011/git/{name}" for name in (
        "package-contracts-windows.xml", "package-contracts-linux.xml", "package-contracts-summary.json",
        "package-contracts-windows-corrected-r2.xml", "package-contracts-summary-corrected-r2.json",
        "package-nominal.xml", "package-contracts-windows-before-final-paths.xml",
    )),
}
RELEASE_5661_PUBLIC |= {name for name in PERFORMANCE_RESOURCE_FILES if '/release-5.66.1-' in name}
RELEASE_5661_PUBLIC |= {
    *(f"outputs/release-5.66.1-20261011/windows/{name}" for name in (
        "attempt1-aborted-before-build.json", "source-freeze-corrected-r2.json",
        "production-delta-corrected-r2.json", "reader-contracts-corrected-r2.xml",
        "display-audit-prerequisite-corrected-r2.xml",
    )),
    *(f"outputs/release-5.66.1-20261011/windows/build-2/{name}" for name in (
        "test-inputs-before.json", "test-inputs-after.json", "official-build-worker-receipt.json",
    )),
    *(f"outputs/release-5.66.1-20261011/windows/finalization-corrected-r2/{name}" for name in (
        "frozen-source-verification.json", "native-consolidation.json", "delivery-worker.json",
    )),
    *(f"outputs/release-5.66.1-20261011/watchdog/corrected-r2/{name}" for name in (
        "binding-authority-attempt1.xml", "binding-authority-attempt2.xml",
        "binding-authority-collection-error.xml", "binding-authority-final.xml",
        "policy-first-failure.xml", "policy-wait-sleep20.xml", "policy-interrupted.json",
        "policy-final-attempt2.xml", "policy-final-attempt3.xml", "policy-final-attempt4.xml", "policy-final.xml",
        "before-source.json", "after-source.json", "summary.json",
        "recovery-capability-fixture-final.xml",
        "activation-final.xml",
        "authority-summary.json", "auth-reproduction-before.json", "build-gate-contracts.xml", "build-gate.json",
    )),
    "outputs/release-5.66.1-20261011/linux/policy-campaign-interruption.json",
    *(name.replace('/linux/', '/linux/corrected-r2/', 1)
      for name in RELEASE_5661_PUBLIC if name.startswith('outputs/release-5.66.1-20261011/linux/')),
}
# Additional R3 fixture/environment diagnostics remain exact public names.
# Their presence preserves history; it never turns a failed attempt into PASS.
RELEASE_5661_PUBLIC |= {
    *(f"outputs/release-5.66.1-20261011/git/{name}" for name in (
        "package-contracts-windows-final-r3.xml", "package-contracts-linux-final-r3.xml",
        "package-contracts-summary-final-r3.json",
    )),
    "outputs/release-5.66.1-20261011/linux/corrected-r3/preparation-failure.json",
    *(f"outputs/release-5.66.1-20261011/linux/{attempt}/{name}"
      for attempt in ("corrected-r3-candidate", "corrected-r3-candidate-rerun")
      for name in ("fixture-override.json", "policy-and-explicit-exports-tests.xml",
                   "policy-and-explicit-exports-tests-summary.json")),
    "outputs/release-5.66.1-20261011/linux/corrected-r3-candidate-rerun/snapshot-production-after.json",
    "outputs/release-5.66.1-20261011/linux/corrected-r2/continuation-execution.json",
    "outputs/release-5.66.1-20261011/linux/corrected-r2/gui-x11-external-readback.json",
    "outputs/release-5.66.1-20261011/linux/corrected-r2/drvfs-chmod-failure.json",
    *(f"outputs/release-5.66.1-20261011/linux/r2-native-retry/{name}" for name in (
        "headless-build-wrapper.json", "gui-build-wrapper.json", "native-retry-execution.json",
        "gui-build-space-estimate.json",
        "linux-release-qualification.json", "qualification-wrapper.json", "scientific-static-linked.json",
        "archive-gui-smoke.json", "archive-headless-smoke.json", "gui-frozen-source-linux.json",
        "native-eva-campaign.json", "native-eva-provenance.json", "gui-default-display.json",
        "gui-wayland-startup.json", "gui-remote-smoke.json", "x11-private-dependencies.json",
        "gui-xwayland-startup.json", "gui-ui-pipeline-xwayland.json",
        "gui-ui-pipeline-xwayland-pixel-inspection.json", "gui-x11-external-readback.json",
        "linux-delivery-summary.json", "gui-new-fixture-cleanup.json",
        "headless-new-passed-build-bundle-relocation.json",
        "gui-new-passed-build-bundle-relocation.json", "source-tests-latest-summary.json",
    )),
}
# Final third-attempt metadata is exact and bounded like earlier attempts.
RELEASE_5661_PUBLIC |= {
    *(f"outputs/release-5.66.1-20261011/windows/{name}" for name in (
        "attempt2-frozen-selftest-failure.json", "source-freeze-corrected-r3.json",
        "production-delta-corrected-r3.json", "build-3/test-inputs-before.json",
        "build-3/test-inputs-after.json", "finalization-corrected-r3/delivery-worker.json",
        "build-2/test-inputs-after-failed-selftest.json",
        "display-audit-prerequisite-corrected-r3.xml", "reader-contracts-corrected-r3.xml",
    )),
    *(f"outputs/release-5.66.1-20261011/watchdog/corrected-r3/{name}"
      for name in ("export-contracts.xml", "text-scrolling.xml")),
    *(name.replace('/linux/r2-native-retry/', '/linux/r3-native-final/', 1)
      for name in RELEASE_5661_PUBLIC
      if name.startswith('outputs/release-5.66.1-20261011/linux/r2-native-retry/')),
    *(name.replace('/linux/', '/linux/corrected-r3-final/', 1)
      for name in RELEASE_5661_PUBLIC
      if name.startswith('outputs/release-5.66.1-20261011/linux/')
      and '/' not in name.split('/linux/', 1)[1]),
}
EXTRA_EVIDENCE += tuple(sorted(RELEASE_5661_PUBLIC)) + (
    "release/linux/5.66.1/core-artifact.json",
    *(f"release/linux/5.66.1-native-{profile}-20261011{attempt}/{name}"
      for profile in ("headless", "gui") for attempt in ('', '-corrected-r2', '-r2-native-retry', '-r3-native-final') for name in (
        "build-report.json", "evidence/installed-wheel-source.json", "evidence/bundle-files.json",
        "evidence/build-environment.json", "evidence/gateway-smoke.json",
        "evidence/native-terminal-campaign.json", "evidence/campaign-provenance.json",
        "evidence/gui-self-test-checks.json", "evidence/gui-startup.json", "evidence/gui-screenshots.json",
        "evidence/gui-public-input-scan.json", "evidence/scientific-input-validation.json",
    )),
)

CORRECTIONS_20261012_PUBLIC = {
    'outputs/defect-corrections-20261012/asset-inventory-before.json',
    'outputs/defect-corrections-20261012/asset-licensing-qualification.json',
    'outputs/defect-corrections-20261012/asset-package-validation.json',
    'outputs/defect-corrections-20261012/osm-attribution-dark-1200.png',
    'outputs/defect-corrections-20261012/osm-attribution-dark-320.png',
    'outputs/defect-corrections-20261012/osm-attribution-light-1200.png',
    'outputs/defect-corrections-20261012/pair-atomicity-qualification.json',
    'outputs/defect-corrections-20261012/processing-labels/labels-cost.json',
    'outputs/defect-corrections-20261012/processing-labels/labels-linux.xml',
    'outputs/defect-corrections-20261012/processing-labels/labels-qualification.json',
    'outputs/defect-corrections-20261012/processing-labels/labels-qualified.xml',
    'outputs/defect-corrections-20261012/provider-stop-measurements.json',
    'outputs/defect-corrections-20261012/provider-stop-qualification.json',
    'outputs/defect-corrections-20261012/root-source-receipt.json',
    'outputs/defect-corrections-20261012/routing-performance-qualification.json',
    'outputs/defect-corrections-20261012/sequence-control/qualification.json',
    'outputs/open_defects_20261012/test-portability-summary.json',
}
RELEASE_5662_PUBLIC = {
    "outputs/release-5.66.2-20261012/windows/source-freeze-delivery-r3.json",
    "outputs/release-5.66.2-20261012/windows/delivery-delta-r3.json",
    *(f"outputs/release-5.66.2-20261012/linux/final-native/{name}" for name in (
        "linux-release-qualification.json", "archive-gui-smoke.json", "archive-headless-smoke.json",
        "gui-frozen-source-linux.json", "gui-remote-smoke.json", "gui-wayland-startup.json",
    )),
    "outputs/release-5.66.2-20261012/windows/consolidation-final.json",
    "release/linux/5.66.2-native-gui-20261012-r2-native-final/build-report.json",
    'outputs/release-5.66.2-20261012/supplementary-tests/build-watchdogs.xml',
    'outputs/release-5.66.2-20261012/supplementary-tests/consolidation-compatibility.xml',
    'outputs/release-5.66.2-20261012/supplementary-tests/git-publication-final.xml',
    'outputs/release-5.66.2-20261012/supplementary-tests/native-shutdown-contract.xml',
    'outputs/release-5.66.2-20261012/supplementary-tests/packaging-preflight-r2.xml',
    'outputs/release-5.66.2-20261012/supplementary-tests/readback-fixed.xml',
    'outputs/release-5.66.2-20261012/supplementary-tests/readback-regressions-r2.xml',
    'outputs/release-5.66.2-20261012/supplementary-tests/watchdog-fixed.xml',
    'outputs/release-5.66.2-20261012/linux/build-watchdogs.xml',
    *(f"release/linux/5.66.2-native-gui-20261012-r3-native-final/{name}" for name in (
        "build-report.json", "evidence/installed-wheel-source.json", "evidence/bundle-files.json",
        "evidence/build-environment.json", "evidence/gui-self-test-checks.json",
        "evidence/gui-startup.json", "evidence/gui-screenshots.json",
        "evidence/gui-public-input-scan.json", "evidence/scientific-input-validation.json",
    )),
    *(f"outputs/release-5.66.2-20261012/linux/{name}" for name in (
        "archive-gui-smoke.json", "archive-headless-smoke.json", "gui-frozen-source-linux.json",
        "gui-remote-smoke.json", "gui-wayland-startup.json",
    )),
    "outputs/release-5.66.2-20261012/linux/headless-repro-hdd.json",
    "outputs/release-5.66.2-20261012/linux/headless-default30-hdd.json",
    "outputs/release-5.66.2-20261012/linux/native-storage-profile.json",
    *(f"release/linux/5.66.2-native-headless-20261012-r2-native-final/{name}" for name in (
        "build-report.json", "evidence/installed-wheel-source.json", "evidence/bundle-files.json",
        "evidence/build-environment.json", "evidence/gateway-smoke.json",
        "evidence/native-terminal-campaign.json", "evidence/campaign-provenance.json",
    )),
    "outputs/release-5.66.2-20261012/windows/source-freeze-delivery-r2.json",
    "outputs/release-5.66.2-20261012/windows/delivery-delta-r2.json",
    "outputs/release-5.66.2-20261012/linux/ram-timing-reproduction.xml",
    "outputs/release-5.66.2-20261012/linux/attempt1-hdd-interrupted/attempt.json",
    "outputs/release-5.66.2-20261012/linux/attempt1-hdd-interrupted/regression-summary.json",
    "outputs/release-5.66.2-20261012/linux/attempt1-hdd-interrupted/regression-results.xml",
    *(f"outputs/release-5.66.2-20261012/windows/{name}" for name in (
        "source-freeze.json", "frozen-source.json", "consolidation.json", "dashboard-validation.json",
        "initial-attempt-summary.json",
        "source-freeze-corrected-r2.json", "source-delta-r2.json",
        "source-freeze-corrected-r3.json", "source-delta-r3.json", "source-gates-summary.json",
        "source-freeze-delivery-final.json", "delivery-delta.json",
    )),
    "outputs/release-5.66.2-20261012/packaging-preflight-r2.xml",
    "outputs/release-5.66.2-20261012/kme-timeout-reproduction.xml",
    *(f"outputs/release-5.66.2-20261012/linux/{name}" for name in (
        "linux-release-qualification.json", "regression-results.xml", "regression-summary.json",
        "linux-wheel-source.json", "frozen-source.json",
    )),
    *(f"release/linux/5.66.2-native-{profile}-20261012/{name}"
      for profile in ("gui", "headless") for name in (
        "build-report.json", "evidence/bundle-files.json", "evidence/installed-wheel-source.json",
        "evidence/build-environment.json", "evidence/gateway-smoke.json",
        "evidence/native-terminal-campaign.json", "evidence/campaign-provenance.json",
        "evidence/gui-self-test-checks.json", "evidence/gui-startup.json", "evidence/gui-screenshots.json",
        "evidence/gui-public-input-scan.json", "evidence/scientific-input-validation.json",
    )),
}
NOMINAL_RELEASE_ROOTS = ('outputs/release-5.66.0-20261011', 'outputs/release-5.66.1-20261011',
                         'outputs/release-5.66.2-20261012')
NOMINAL_RELEASE_PUBLIC = RELEASE_566_PUBLIC | RELEASE_5661_PUBLIC | RELEASE_5662_PUBLIC
EXTRA_EVIDENCE += tuple(sorted(CORRECTIONS_20261012_PUBLIC))
SELF_TEST_PROJECTIONS = {
    "outputs/orionq_release_5_61_0_20261010/self-test-second-failure.json":
        "outputs/orionq_release_5_61_0_20261010/self-test-second-failure-summary.json",
}
SELF_TEST_SECTIONS = {
    "window_chrome", "terminal_workspace", "background_polling", "responsive_ui",
    "functional_topology", "demonstration", "institutional_demonstration",
    "modular_quantum_technologies", "global_network_operations", "communicators_geo",
    "satellite_ogs_v2", "satellite_ogs_ui", "network_regressions", "usage_feedback",
    "text_scrolling", "live_updates", "wave_continuity",
}
SUMMARY_METADATA = {
    "passed", "check_count", "schema", "version", "frozen", "qt_platform",
    "executable_sha256", "executable_sha256_before", "executable_sha256_after",
    "physical_actuation", "physical_action_performed", "physical_hardware_validated",
    "security_certified", "mission_ready", "certified_secret_bits",
}
RELEASE_CHILDREN = {
    "native-acceptance/acceptance-report.json": ("native-acceptance/self-test.json",),
    "relocation-acceptance/acceptance-report.json": (
        "relocation-acceptance/self-test.json", "relocation-acceptance/stress-report.json",
        "relocation-acceptance/delivery-relocation-proof.json",
    ),
    "desktop-behavior-validation/report.json": ("desktop-behavior-validation/native/report.json",),
    "satellite-mission-validation/report.json": (
        "satellite-mission-validation/reception/report.json",
        "satellite-mission-validation/reception/native-mission-reception.png",
    ),
    "startup-validation/report.json": tuple(
        f"startup-validation/{case}/report.json"
        for case in ("service-100", "service-125", "service-150", "service-200", "demo-100", "demo-150")
    ),
    "roadmap-validation/report.json": (
        "roadmap-validation/comparison-30/comparison.json",
        "roadmap-validation/comparison-120/comparison.json",
        "roadmap-validation/comparison-250/comparison.json",
        "roadmap-validation/runtime/diagnostic.json",
    ),
    "global-validation/report.json": (
        "global-validation/component-matrix.json", "global-validation/component-matrix.csv",
        "global-validation/component-matrix.md", "global-validation/resource-summary.json",
        "global-validation/report.md",
    ),
}
RELEASE_OPTIONAL_CHILDREN = {
    "campaign-validation/report.json": (
        "campaign-validation/edited-manifest.json", "campaign-validation/operational-attempt.json",
    ),
}
STRESS_RUN_NAME = re.compile(r"synthetic-controller-[0-9a-f]{32}")
GLOBAL_RUN_NAME = re.compile(r"run-[0-9]{8}T[0-9]{12}")


def release_junit_path(relative: str) -> bool:
    """Only nominal official root JUnits bypass the pytest scratch prefix."""
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        return False
    parts = path.parts
    if len(parts) == 1:
        return parts[0] in RELEASE_JUNIT_REPORTS
    return (len(parts) == 4 and parts[:2] == ("evidence", "release")
            and not scratch("/".join(parts[:-1])) and parts[-1] in RELEASE_JUNIT_REPORTS)


def safe_release_path(release: Path, relative: str, *, required: bool = True) -> Path:
    """Resolve only curated children, rejecting junctions before reading contents."""
    name = PurePosixPath(relative)
    if name.is_absolute() or ".." in name.parts or (exclusion(relative) and not release_junit_path(relative)):
        raise ValueError(f"Unsafe release evidence path: {relative}")
    path = release.joinpath(*name.parts)
    for component in (path, *path.parents):
        if not component.is_relative_to(release):
            break
        if component.is_symlink() or (
            component.exists() and getattr(component.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError(f"Release evidence symlink/reparse point: {relative}")
    if not path.resolve().is_relative_to(release):
        raise ValueError(f"Release evidence escapes release root: {relative}")
    if required and not path.is_file():
        raise FileNotFoundError(f"Required release gate child missing: {relative}")
    return path


def release_candidates(release: Path, *, canonical_release_root: Path | None = None) -> set[Path]:
    """Close known native gate dependencies without importing private/raw state."""
    reference_root = release
    if canonical_release_root is not None:
        if not canonical_release_root.is_absolute():
            raise ValueError("Canonical release root must be absolute")
        reference_root = canonical_release_root.resolve()
    selected: set[Path] = set()
    for relative in RELEASE_REPORTS:
        parent = safe_release_path(release, relative, required=False)
        if parent.is_file():
            selected.add(parent)
            selected.update(safe_release_path(release, child) for child in RELEASE_CHILDREN.get(relative, ()))
            for child in RELEASE_OPTIONAL_CHILDREN.get(relative, ()):
                candidate = safe_release_path(release, child, required=False)
                if candidate.is_file():
                    selected.add(candidate)
    for relative in RELEASE_JUNIT_REPORTS:
        candidate = safe_release_path(release, relative, required=False)
        if candidate.is_file():
            selected.add(candidate)
    selected.update(safe_release_path(release, path.relative_to(release).as_posix())
                    for path in (release / "branding-validation").glob("*.png"))
    for relative in ("stress-validation", "native-acceptance/stress"):
        directory = safe_release_path(release, relative, required=False)
        required = relative == "native-acceptance/stress" and (release / "native-acceptance/acceptance-report.json").is_file()
        if not directory.exists() and not required:
            continue
        if not directory.is_dir():
            raise FileNotFoundError(f"Required release stress directory missing: {relative}")
        children = [path for path in directory.iterdir() if path.is_dir()]
        if len(children) != 1 or not STRESS_RUN_NAME.fullmatch(children[0].name):
            raise ValueError(f"Exactly one known direct stress run is required: {relative}")
        selected.add(safe_release_path(release, f"{relative}/{children[0].name}/report.json"))
    global_parent = release / "global-validation/report.json"
    if global_parent in selected:
        if global_parent.stat().st_size > MAX_EVIDENCE_BYTES:
            raise ValueError("Global gate report exceeds curated evidence limit")
        secret_check(global_parent)
        report = json.loads(global_parent.read_text(encoding="utf-8-sig"))
        run = Path(report.get("run_directory", ""))
        if not run.is_absolute():
            run = reference_root / "global-validation" / run
        if run.parent != reference_root / "global-validation" or not GLOBAL_RUN_NAME.fullmatch(run.name):
            raise ValueError("Global gate run_directory must be one direct timestamped run")
        relative = run.relative_to(reference_root).as_posix()
        timeline = safe_release_path(release, f"{relative}/timeline.json")
        scenario = safe_release_path(release, f"{relative}/isl-obstruction-scenarios.json")
        reference = Path(report.get("scenarios", {}).get("isl_obstruction_file", ""))
        if not reference.is_absolute():
            reference = reference_root / "global-validation" / reference
        if reference != reference_root / relative / "isl-obstruction-scenarios.json":
            raise ValueError("Global obstruction reference does not match its curated run")
        selected.update((timeline, scenario))
        for extension in ("json", "csv", "md"):
            candidate = safe_release_path(release, f"{relative}/final-operations/component-matrix.{extension}", required=False)
            if candidate.is_file():
                selected.add(candidate)
    return selected


def self_test_projection(path: Path) -> bytes:
    """Lossy, explicit checks-only evidence; the full original stays external."""
    if path.stat().st_size > MAX_PROJECTION_SOURCE_BYTES:
        raise ValueError("Self-test source exceeds bounded projection input limit")
    secret_check(path)
    raw = path.read_bytes()
    report = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(report, dict) or type(report.get("passed")) is not bool:
        raise ValueError("Self-test projection requires a report with a boolean passed field")

    def basic(value):
        summary = {key: item for key, item in value.items() if key in SUMMARY_METADATA
                   and type(item) in (bool, int, float, str) and (not isinstance(item, str) or len(item) <= 256)}
        checks = value.get("checks", {})
        if isinstance(checks, list):
            if len(checks) > 4096 or any(not isinstance(item, dict)
                    or not isinstance(item.get("name"), str) or len(item["name"]) > 256
                    or type(item.get("passed")) is not bool for item in checks):
                raise ValueError("Self-test projection list checks require bounded names and booleans")
            summary["checks"] = [{"name": item["name"], "passed": item["passed"]} for item in checks]
            return summary
        if not isinstance(checks, dict) or len(checks) > 4096 or any(
            not isinstance(key, str) or len(key) > 256 or type(item) is not bool for key, item in checks.items()):
            raise ValueError("Self-test projection checks must be a bounded boolean mapping")
        summary["checks"] = checks
        return summary

    summary = basic(report)
    summary["subsection_checks"] = {name: basic(report[name]) for name in sorted(SELF_TEST_SECTIONS)
                                    if isinstance(report.get(name), dict)}
    summary["projection"] = {
        "schema": "orion.self-test-checks-projection.v1", "derived_summary": True,
        "original_path": str(path.resolve()), "original_bytes": len(raw),
        "original_sha256": hashlib.sha256(raw).hexdigest(), "original_json_bytes_preserved": False,
        "full_original_preserved_external": True,
        "omitted": "Raw snapshots, receipts, key material, photon payloads and all non-whitelisted fields.",
        "qualification": "Checks summary only; not a replacement for full raw evidence or a test reexecution.",
    }
    rendered = (json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(rendered) > MAX_EVIDENCE_BYTES:
        raise ValueError("Self-test checks projection exceeds the unchanged curated evidence limit")
    return rendered


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def source_release_metadata(root: Path) -> tuple[str, str | None]:
    """Bind generated instructions to the declared source version, not a prior build."""
    text = (root / "pyproject.toml").read_text(encoding="utf-8-sig")
    project = re.search(r"(?ms)^\[project\]\s*\n(.*?)(?=^\[|\Z)", text)
    version = re.search(r'^version\s*=\s*[\"\']([0-9]+\.[0-9]+\.[0-9]+)[\"\']\s*$',
                        project.group(1) if project else "", re.MULTILINE)
    if version is None:
        raise ValueError("Source release requires a declared numeric project version")
    declared = version.group(1)
    report_prefix = f"orionq_release_{declared.replace('.', '_')}_"
    # Supplemental audit reports may share the version prefix. Only a dated
    # primary release report binds the generated delivery instructions.
    reports = sorted(path for path in (root / "docs/results").glob(f"{report_prefix}*.md")
                     if re.fullmatch(re.escape(report_prefix) + r"[0-9]{8}\.md", path.name))
    report = reports[-1].relative_to(root).as_posix() if reports else None
    return declared, report


def git(root: Path, *args: str) -> bytes:
    command = ["git", "-c", f"safe.directory={root.as_posix()}", "-c", "core.quotepath=false", *args]
    return subprocess.run(command, cwd=root, capture_output=True, check=True).stdout


def scratch(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return any(p in SCRATCH_PARTS or p.startswith((".audit-", ".pytest", "pytest-")) for p in parts)


def raw_evidence(path: Path) -> bool:
    return path.name in RAW_EVIDENCE_NAMES or path.name.endswith(".ogs-packets.json")


def exclusion(relative: str, source: bool = False) -> str | None:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        return "unsafe path"
    if scratch(relative) and not (not source and relative in CURATED_HISTORICAL_JUNITS):
        return "environment, graphics build, private state or test scratch"
    if path.suffix.lower() in SENSITIVE_SUFFIXES or path.name.endswith(("-wal", "-shm")):
        return "private keys, certificates or operational databases"
    if path.name.startswith(".env") and path.name not in {".env.example", ".env.template"}:
        return "environment credentials"
    if path.name.lower() in {"credentials.json", "credentials.yaml", "credentials.yml", "token.json"}:
        return "credential store"
    if relative.startswith(("data/raw/", "external/sequence/")) or "/vendor/" in relative:
        return "downloaded raw data or external SDK checkout"
    if path.suffix.lower() in {".pyc", ".pyo"}:
        return "bytecode"
    if path.suffix.lower() == ".tsbuildinfo":
        return "TypeScript incremental build cache"
    if source and path.suffix.lower() in SOURCE_DISTRIBUTION_SUFFIXES and relative not in PINNED_TEST_ARCHIVES:
        return "compiled distribution or nested archive; deliver separately"
    if source and relative not in PUBLIC_SOURCE_FILES and (
            len(path.parts) == 1 and relative not in ROOT_FILES
            or len(path.parts) > 1 and path.parts[0] not in SOURCE_ROOTS):
        return "outside release source roots"
    return None


def secret_check(path: Path) -> None:
    # Do not print candidate bytes. These signatures cover actual private-key
    # blocks and familiar service credentials; this is not a full secret audit.
    if path.suffix.lower() in {".png", ".gz", ".npz", ".pdf", ".ico", ".ttf", ".otf", ".wav"}:
        return
    with path.open("rb") as stream:
        tail = b""
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            material = tail + chunk
            if PRIVATE_HEADER.search(material) or SERVICE_TOKEN.search(material):
                raise ValueError(f"Possible private key/service token; review before packaging: {path}")
            tail = material[-256:]


def validate_public_resources(path: Path) -> None:
    """Admit bounded measurement records, never arbitrary process state/logs."""
    scalar_fields = {"elapsed_s", "wall_unix", "cpu_percent", "rss_bytes", "threads",
                     "host_cpu_percent", "host_available_bytes"}
    process_fields = {"pid", "name", "cpu_percent", "rss_bytes"}

    def number(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0

    count = 0
    with path.open(encoding="utf-8") as stream:
        for count, line in enumerate(stream, 1):
            try:
                record = json.loads(line)
                if (not isinstance(record, dict)
                        or record.keys() != scalar_fields | {"processes", "user_owned_orion_processes"}
                        or not all(number(record[name]) for name in scalar_fields)):
                    raise ValueError
                for name in ("processes", "user_owned_orion_processes"):
                    processes = record[name]
                    if not isinstance(processes, list) or len(processes) > 4096:
                        raise ValueError
                    for process in processes:
                        if (not isinstance(process, dict) or process.keys() != process_fields
                                or not all(number(process[field]) for field in process_fields - {"name"})
                                or not isinstance(process["name"], str) or not 1 <= len(process["name"]) <= 256
                                or any(ord(character) < 32 for character in process["name"])):
                            raise ValueError
            except (ValueError, TypeError, OverflowError):
                raise ValueError(f"Unexpected public resource measurement record at line {count}") from None
    if not count:
        raise ValueError("Public resource measurement file is empty")


def source_names(root: Path) -> list[str]:
    # Tracked removals still belong in the inventory, while untracked private
    # environments/work trees need not be traversed to reject them afterwards.
    tracked = git(root, "ls-files", "--cached", "-z")
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z", "--",
                    *sorted(SOURCE_ROOTS | ROOT_FILES | PUBLIC_SOURCE_FILES))
    return sorted(set(filter(None, (tracked + untracked).decode("utf-8").split("\0"))))


def artifact_candidates(root: Path) -> set[Path]:
    candidates: set[Path] = set()
    for relative in EVIDENCE_TREES:
        directory = root / relative
        if directory.is_dir():
            for parent, directories, names in os.walk(directory):
                directories[:] = [name for name in directories
                                  if not exclusion((Path(parent) / name).relative_to(root).as_posix())
                                  and not (Path(parent) / name).is_symlink()]
                candidates.update(Path(parent) / name for name in names)
    candidates.update(root / relative for relative in EXTRA_EVIDENCE if (root / relative).is_file())
    # Keep referenced failed attempts too; copying only PASS would distort the
    # scientific history. Preserve original paths so Markdown links still work.
    for document in (root / "docs/results").glob("*.md"):
        if not DOC_PATTERN.fullmatch(document.stem.removeprefix("orionq_")):
            continue
        for reference in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            if "outputs/" not in reference or "://" in reference:
                continue
            candidate = (document.parent / reference.split("#", 1)[0]).resolve()
            if (any(candidate.is_relative_to(root / prefix) for prefix in NOMINAL_RELEASE_ROOTS)
                    and candidate.relative_to(root).as_posix() not in NOMINAL_RELEASE_PUBLIC):
                continue
            if candidate.is_file() and candidate.is_relative_to(root):
                candidates.add(candidate)
    # JSON reports refer to nested independent comparators and artifact hashes.
    # Follow only bounded local files; state/DB/raw exclusions still apply later.
    pending = list(candidates)
    inspected: set[Path] = set()
    while pending:
        path = pending.pop()
        if (path in inspected or path.is_symlink()
                or not path.resolve().is_relative_to(root)
                or exclusion(path.relative_to(root).as_posix())
                or raw_evidence(path)
                or path.suffix.lower() != ".json" or path.stat().st_size > MAX_EVIDENCE_BYTES):
            continue
        inspected.add(path)
        try:
            content = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError):
            continue
        stack = [content]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
            elif isinstance(value, str) and "://" not in value and len(value) < 512:
                if PurePosixPath(value.replace("\\", "/")).suffix.lower() not in EVIDENCE_SUFFIXES:
                    continue
                for base in (path.parent, root):
                    candidate = (base / value).resolve()
                    if (any(path.is_relative_to(root / prefix) for prefix in NOMINAL_RELEASE_ROOTS)
                            and candidate not in candidates
                            and (not candidate.is_relative_to(root)
                                 or candidate.relative_to(root).as_posix() not in NOMINAL_RELEASE_PUBLIC)):
                        continue
                    if (any(candidate.is_relative_to(root / prefix) for prefix in NOMINAL_RELEASE_ROOTS)
                            and candidate.relative_to(root).as_posix() not in NOMINAL_RELEASE_PUBLIC):
                        continue
                    if (candidate.is_relative_to(root / "outputs") and candidate.is_file()
                            and candidate not in candidates):
                        relative = candidate.relative_to(root).as_posix()
                        if not exclusion(relative) and candidate.stat().st_size <= MAX_EVIDENCE_BYTES:
                            candidates.add(candidate)
                            pending.append(candidate)
    return candidates


def inventory(root: Path, release_root: Path | None = None, *,
              canonical_release_root: Path | None = None) -> tuple[dict[str, tuple[Path, str]], list[dict]]:
    root = root.resolve()
    if canonical_release_root is not None and release_root is None:
        raise ValueError("Canonical release root requires a working release root")
    included: dict[str, tuple[Path, str]] = {}
    excluded: list[dict] = []

    def consider(path: Path, relative: str, kind: str, maximum: int) -> None:
        reason = exclusion(relative, source=kind == "source")
        if kind == "release_evidence" and release_junit_path(relative):
            reason = None
        if not reason and path.is_symlink():
            reason = "symlink requires explicit review"
        if not reason and not path.resolve().is_relative_to(root):
            reason = "source escapes repository"
        if not path.is_file():
            excluded.append({"path": relative, "reason": "deleted/missing working file"})
            return
        size = path.stat().st_size
        if not reason and size > maximum:
            reason = f"above curated file limit ({maximum} bytes)"
        if (not reason and kind != "source" and kind != "build_resource"
                and path.suffix.lower() not in EVIDENCE_SUFFIXES
                and relative not in CURATED_JSONL and relative not in CURATED_AUDIT_FILES):
            reason = "raw data, log, cache or executable outside curated evidence formats"
        if not reason and kind == "qualification_evidence" and raw_evidence(path):
            reason = "raw observation/state snapshot; reproduce from campaign contracts"
        if reason:
            excluded.append({"path": relative, "bytes": size, "reason": reason})
            return
        if kind == "source" and relative in PINNED_TEST_ARCHIVES:
            if digest(path) != PINNED_TEST_ARCHIVES[relative]:
                raise ValueError(f"Pinned public test archive integrity mismatch: {relative}")
        secret_check(path)
        if relative in PERFORMANCE_RESOURCE_FILES:
            validate_public_resources(path)
        included[relative] = (path, kind)

    def project(path: Path, relative: str) -> None:
        source_relative = path.relative_to(root).as_posix()
        safe_release_path(root, source_relative)
        self_test_projection(path)  # Validate before creating any destination.
        included[relative] = (path, "self_test_projection")

    for relative in source_names(root):
        consider(root / relative, relative, "source", MAX_FILE_BYTES)
    for path in sorted(artifact_candidates(root)):
        relative = path.relative_to(root).as_posix()
        if relative in SELF_TEST_PROJECTIONS:
            project(path, SELF_TEST_PROJECTIONS[relative])
        elif relative not in SELF_TEST_PROJECTIONS.values():
            consider(path, relative, "qualification_evidence", MAX_EVIDENCE_BYTES)
    for relative in BUILD_RESOURCES:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"Required clean-build resource missing: {relative}")
        consider(path, relative, "build_resource", MAX_FILE_BYTES)
    for relative in REQUIRED_EVIDENCE:
        if relative not in included:
            raise FileNotFoundError(f"Required scientific evidence missing/excluded: {relative}")
    if release_root is not None:
        release_root = release_root.resolve()
        if not release_root.is_relative_to(root / "release") or not release_root.is_dir():
            raise ValueError("--release-root must be an existing release directory inside this repository")
        release_files = release_candidates(release_root, canonical_release_root=canonical_release_root)
        for path in sorted(release_files):
            relative = path.relative_to(release_root).as_posix()
            portable = f"evidence/release/{release_root.name}/{relative}"
            if relative in RELEASE_SELF_TESTS:
                portable = portable.removesuffix(".json") + "-summary.json"
                project(path, portable)
            else:
                consider(path, portable, "release_evidence", MAX_EVIDENCE_BYTES)
            if portable not in included:
                raise ValueError(f"Selected release gate evidence excluded by policy: {relative}")
    return included, excluded


def export(root: Path, destination: Path, release_root: Path | None = None, *,
           canonical_release_root: Path | None = None) -> dict:
    root = root.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite destination: {destination}")
    if root.is_relative_to(destination):
        raise ValueError("Destination cannot be the source directory or its ancestor")
    included, excluded = inventory(root, release_root, canonical_release_root=canonical_release_root)
    for required in ("pyproject.toml", "src/quantum_autopilot/console/app.py",
                     "tools/build_windows_console.ps1", "runtime_assets/native/v1/manifest.json"):
        if required not in included:
            raise ValueError(f"Required source missing from snapshot: {required}")
    release_version, current_report = source_release_metadata(root)
    if current_report is not None and current_report not in included:
        raise ValueError("Current release report must be included in the source snapshot")
    # Validate the entire plan before creating the output. Copy/hash verification
    # catches modifications that occur concurrently with release preparation.
    before = {name: digest(path) for name, (path, _) in included.items()}
    destination.mkdir(parents=True)
    files = []
    for name, (path, kind) in sorted(included.items()):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == "self_test_projection":
            rendered = self_test_projection(path)
            target.write_bytes(rendered)
            if digest(target) != hashlib.sha256(rendered).hexdigest() or digest(path) != before[name]:
                raise RuntimeError(f"Source changed during projection; snapshot not accepted: {name}")
            files.append({"path": name, "bytes": target.stat().st_size, "sha256": digest(target), "kind": kind,
                          "original_sha256": before[name], "original_bytes": path.stat().st_size,
                          "byte_preserved": False, "derived_summary": True})
        else:
            shutil.copyfile(path, target)
            if digest(target) != before[name] or digest(path) != before[name]:
                raise RuntimeError(f"Source changed during export; snapshot not accepted: {name}")
            files.append({"path": name, "bytes": target.stat().st_size, "sha256": before[name], "kind": kind,
                          "byte_preserved": True, "derived_summary": False})
    if release_root is not None:
        release = release_root.resolve()
        canonical = canonical_release_root.resolve() if canonical_release_root is not None else release
        portable_root = f"evidence/release/{release.name}"

        def origin(path: Path) -> tuple[Path, str]:
            if not path.is_relative_to(release):
                return path, "historical_repository_source"
            relative = path.relative_to(release)
            if relative.as_posix() == "delivery-copy.json" or relative.parts[0] == "relocation-acceptance":
                return path, "working_copy_validation"
            return canonical / relative, "canonical_build"

        evidence_map = {
            "schema": "orion.portable-release-evidence-map.v1",
            "original_source_release_root": str(canonical),
            "working_copy_release_root": str(release),
            "package_release_root": portable_root,
            "original_json_bytes_preserved": all(row.get("byte_preserved") for row in files
                                                  if row["kind"] in {"release_evidence", "self_test_projection"}),
            "full_self_test_originals_preserved_external": True,
            "qualifications_reexecuted_by_mapping": False,
            "files": [
                {"original_path": str(origin(included[row["path"]][0])[0]),
                 "origin": origin(included[row["path"]][0])[1],
                 "original_relative_path": included[row["path"]][0].relative_to(
                     release if included[row["path"]][0].is_relative_to(release) else root).as_posix(),
                 "path": row["path"], "sha256": row["sha256"], "bytes": row["bytes"],
                 "byte_preserved": row["byte_preserved"], "derived_summary": row["derived_summary"],
                 "original_sha256": row.get("original_sha256", row["sha256"]),
                 "original_bytes": row.get("original_bytes", row["bytes"])}
                for row in files if row["kind"] in {"release_evidence", "self_test_projection"}
            ],
            "reference_policy": "Resolve only entries in this map. Self-test projections are lossy checks summaries with separate original/output hashes; other selected reports preserve bytes. Never import adjacent raw state.",
        }
        map_path = destination / "release-evidence-map.json"
        map_path.write_text(json.dumps(evidence_map, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        files.append({"path": map_path.name, "bytes": map_path.stat().st_size,
                      "sha256": digest(map_path), "kind": "generated_evidence_map"})
    freeze_path = root / "outputs/hybrid_remediation_20261010/run3/source-freeze.json"
    delta = []
    if freeze_path.is_file():
        frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
        for name, expected in frozen.get("files", {}).items():
            current = before.get(name)
            if current != expected:
                category = ("omitted generated distribution metadata"
                            if ".egg-info/" in name else "changed release source or omitted source")
                delta.append({"path": name, "qualification_sha256": expected,
                              "release_sha256": current, "category": category})
    manifest = {
        "schema": "orion.git-working-release.v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_head": git(root, "rev-parse", "HEAD").decode().strip(),
        "source_history_included": False,
        "source_working_changes_preserved": True,
        "source_version": release_version,
        "current_release_report": current_report,
        "files": files,
        "counts_by_kind": dict(Counter(row["kind"] for row in files)),
        "bytes": sum(row["bytes"] for row in files),
        "excluded": excluded,
        "latest_qualification_release_delta": delta,
        "limits": {"source_file_bytes": MAX_FILE_BYTES, "curated_evidence_file_bytes": MAX_EVIDENCE_BYTES},
        "nominal_public_sources": sorted(PUBLIC_SOURCE_FILES),
        "untracked_scope": "Public source roots/root files and nominal public reproducer/RTL files only; private work trees are not traversed.",
        "qualifications_reexecuted_by_export": False,
        "physical_actuation": "locked",
        "certified_secret_bits": 0,
        "limitations": [
            "Curated reports, not full raw acquisition/database exports; original raw files remain local.",
            "Historical source freezes describe their original campaigns, not the changed release snapshot.",
            "No new TRL assignment, physical qualification, NetSquid SDK or proprietary instrument SDK.",
            "The v1 observation envelope does not attest serial/endpoint; physical source-to-resource association must be verified by the authorized adapter/service. No multi-host/device-side fencing is qualified.",
            "Secret signatures and filename exclusions are a bounded scan, not a full security audit.",
            "Third-party code, datasets, fonts and models retain their own provenance/license notices; the root Apache license does not replace them.",
            "Git initialization/commit and final archive checks must be completed separately.",
        ],
    }
    (destination / "release-source-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    notes = f"""# OrionQ {release_version} — snapshot Git e evidências de qualificação

Este diretório preserva as fontes atuais, inclusive alterações e arquivos novos,
sem importar o histórico Git original. `release-source-manifest.json` contém
hashes de cada arquivo copiado, exclusões e diferenças desde a campanha híbrida.
Uma versão nova não altera retroativamente os congelamentos históricos.

Os relatórios TRL 4, SOFT-DT/TRL 5, SeQUeNCe e híbridos conservam seus estados,
falhas anteriores e limitações. Esta entrega não atribui TRL 5 à rede física.
Atuação física permanece bloqueada e os modelos ML são opcionais.
As medições de interface não declaram eliminação de todas as pausas nem
sustentação universal da simulação científica a 1× tempo real. Consulte o
comparativo e os limites de G05 e do processamento no relatório desta versão;
perfis com fontes atuais e executável anterior são diagnósticos separados da
comparação entre os executáveis finais. Leituras de CPU/RSS selecionadas são
registros de medição de esquema limitado, sem argumentos de processos ou
estado operacional bruto.

O reproduzidor `tools/benchmark_policy_panels.py` executa Python a partir de um
checkout escolhido. Seus ensaios Qt/offscreen dos três painéis de política
usam uma apresentação identificada, sem a engine científica global em execução,
e verificam ações com o serviço/autorização reais. Não são medições do EXE nem
qualificação global, visual nativa ou física. Use um `--output` novo e um
`--private-state-root` novo, separado, para manter credenciais, auditoria e SQLite
fora do relatório público. O resumo distingue o helper privado da medição
original e o reproduzidor público com verificações adicionais de origem/path.

Os arquivos pequenos de evidências mantêm os caminhos `outputs/` para preservar
links da documentação. Acrescente somente essa seleção ao novo Git com
`git add -f -- outputs`; novas execuções continuam ignoradas. Dados brutos,
SQLite, ambientes virtuais, SDKs proprietários, segredos e binários do aplicativo
não são distribuídos no Git. Consulte as exclusões do manifesto e os runners em
`tools/` para reproduzir aquisições e recibos omitidos; os resumos sozinhos não
substituem a conferência completa desses dados. O build Windows restaura recursos
fixados por `tools/prepare_console_resources.py`.

Os scripts `resume_windows_build.ps1`, `resume_windows_build_v2.ps1` e
`resume_windows_build_v3.ps1`, `resume_windows_build_v4.ps1` e
`resume_windows_build_v5.ps1`, com seus respectivos
`resume-official-suffix.diff`, `resume-official-suffix-v2.diff`,
`resume-official-suffix-v3.diff`, `resume-official-suffix-v4.diff` e
`resume-official-suffix-v5.diff`, são
evidências históricas das continuações da entrega 5.61.0; exigem a árvore e os gates originais. Eles
não substituem o build completo de um checkout novo por
`tools/build_windows_console.ps1`.

A continuação histórica da versão 5.64.2 usa o reproduzidor público
`work/resume_windows_native_release.py` e as provas nominais em
`outputs/native_release_5_64_2_20261011/windows/bootstrap-continuation/`.
O tail oficial preserva os comandos e substitui somente o basetemp do corpus 21.
Proveniência, teste original de lifecycle, reprodução corrigida e JUnit falho
conservam seus resultados distintos. Uma continuação aprovada não transforma a
tentativa original em execução integral aprovada. Logs e estados privados ficam
fora da seleção; referências a eles nos registros preservam apenas a proveniência.

Os gates nativos compactos ficam em `evidence/release/<build>/`. Os self-tests
principal, de aceitação original, de relocação e o segundo insucesso histórico ficam em projeções
`*-summary.json`: somente checks booleanos e metadados permitidos, sem estados
brutos, recibos completos, chaves ou payloads. Essas projeções NÃO preservam os
bytes dos relatórios originais; incluem seu SHA e tamanho, e os originais completos
continuam fora deste Git. Os demais JSONs selecionados preservam seus bytes e
caminhos originais como proveniência;
`release-evidence-map.json` relaciona cada referência local original ao arquivo
portátil e ao SHA-256. Para conferir dependências em outra máquina, resolva
somente entradas desse índice sem reescrever os JSONs. A conferência dos gates
congelados também requer o executável correspondente entregue separadamente.
Os registros da cópia e da nova validação após relocação identificam a origem
pública no working-copy; não são atribuídos a um caminho inexistente do build
canônico. A proveniência interna desses JSONs continua preservada.
O índice não copia estados privados, não reexecuta testes e não autentica autoria.

As evidências históricas de terminal e contenção conservam os resultados de cada
rodada. Consulte `docs/results/orionq_terminal_linux_20261011.md`,
`docs/results/orionq_terminal_brand_linux_20261011.md` e
`docs/results/orionq_controller_contention_20261011.md`. Na campanha original,
o consumo duplicado de par no caminho legado permanecia aberto. O estado atual,
as regressões de correção e os limites devem ser conferidos no relatório desta
versão e em `docs/product/orionq_controller_concurrency.md`; exportar esses
registros não reexecuta nem amplia a qualificação. O envelope v1 não atesta
série/endpoint: cabe ao adaptador/serviço autorizado verificar a associação
fonte → identidade configurada. Não há qualificação física ou de fencing
distribuído externo. Somente os reproduzidores públicos nomeados de
`work/` e os cinco fontes/contratos RTL nomeados de `fpga/orionq_pl/` entram na
seleção. Ambientes e demais arquivos dessas árvores ficam fora. Arquivos compactados da
tese e distribuições compiladas não são fontes deste snapshot.

Os binários Windows/Linux e os wheels são entregues separadamente e devem ser
anexados manualmente como assets ao GitHub Releases; não entram no Git. Este
snapshot inclui somente os manifests, hashes e evidências de qualificação
selecionados. Links relativos dos relatórios para tar/ZIP/wheel só funcionam
quando esses assets estão nos mesmos caminhos locais indicados. Nenhuma URL
GitHub é presumida e nenhum upload é realizado. Consulte `SOURCE_RELEASE.md`.
O perfil Qt histórico 5.64.2 usa `release/linux/5.64.2-native-gui-bootstrap-release`;
seus resultados não qualificam o snapshot {release_version}. Os resultados de bootstrap
e do comparador incluem o provider entre os 52 módulos e 25 recursos exigidos.
A prova `gui-bootstrap-source-binding.json` identifica separadamente qualquer
diferença auxiliar de `SOURCES.txt`; isso não implica mudança dos módulos,
recursos ou metadados de versão executados. Leia as condições e limites no
relatório final antes de interpretar históricos como aprovação do candidato.
Para reconstruir o perfil
Linux sem GUI, use `packaging/core/prepare_source.py` em um destino novo e faça
o build desse destino com Python; `packaging/core/requirements.txt` define
seu envelope. `tools/build_windows_console.ps1` continua sendo o build Windows
completo. Os links históricos para `release/` não trazem binários ao novo Git.

Licenças de terceiros devem ser conferidas antes de publicação. A licença
principal é Apache 2.0. Os manifests de projeto dos componentes qnet declaram
MIT, mas suas árvores locais não contêm LICENSE/COPYING upstream. Os dados
Cambridge conservam o aviso CC BY 4.0 e a referência no LEIAME. As fontes
conservam seus avisos OFL. A fonte Quantum não integra os recursos atuais nem
os novos pacotes; distribuições históricas permanecem identificadas como tais.
Esses registros não atribuem Apache automaticamente aos componentes, fontes
ou modelos.

Depois de inicializar e conferir o repositório, crie um remoto próprio no GitHub
e envie a branch. Nenhuma publicação é realizada por este exportador.
"""
    (destination / "GIT-RELEASE.md").write_text(notes, encoding="utf-8")
    report_instructions = (f"Consulte `{current_report}` para os perfis, resultados e limites desta rodada."
                           if current_report else
                           "Ainda não há relatório final desta versão selecionado neste snapshot; consulte os manifests de entrega e `docs/results/`.")
    source_release = f"""# OrionQ {release_version} — publicação da entrega de fontes

Este repositório contém o snapshot público de fontes e a seleção de manifests,
hashes e evidências de qualificação descrita em `GIT-RELEASE.md` e
`release-source-manifest.json`. O histórico e o Git de origem não são importados.

Os pacotes binários Windows/Linux e os wheels são entregues separadamente.
Após conferir as licenças/proveniência indicadas em `GIT-RELEASE.md`, anexe esses
arquivos manualmente como assets ao GitHub Releases; não os acrescente ao Git.
Esta preparação não cria remoto, publicação ou URL GitHub.

O repositório preparado e o ZIP preservam os bytes selecionados. Para restaurar
o bundle com a mesma política, execute:

```sh
git -c core.autocrlf=false -c core.longpaths=true clone OrionQ-{release_version}-github.bundle OrionQ-{release_version}
git -C OrionQ-{release_version} config core.autocrlf false
git -C OrionQ-{release_version} config core.longpaths true
```

Essas opções valem somente para a cópia nova. Os padrões `-text` de
`.gitattributes` protegem evidências e referências congeladas; fontes fora desses
padrões podem mudar de fim de linha sob outras configurações de checkout.
A prova externa da entrega distingue o snapshot, o ZIP e o clone com a política
de bytes preservados de qualquer ensaio com normalização automática.

Os relatórios preservam seus links relativos e hashes originais. Links para
tar/ZIP/wheel ausentes deste arquivo de fontes somente funcionam quando os
assets são colocados nos mesmos caminhos locais, inclusive `release/windows/`
e `release/linux/`, conforme cada relatório. A ausência desses binários no Git
é intencional; os manifests não substituem a entrega compilada correspondente.

{report_instructions}
Confira as garantias e limites de concorrência em
`docs/product/orionq_controller_concurrency.md` e suas regressões selecionadas.
O envelope v1 não atesta origem física; o adaptador/serviço autorizado deve
verificar a associação fonte → série/endpoint. A entrega não qualifica hardware,
fencing distribuído externo ou disponibilidade em qualquer armazenamento.
Os ganhos locais de layout não afirmam eliminação de todas as pausas nem
sustentação universal do processamento científico a 1× tempo real. O relatório
desta versão distingue medidas finais, diagnósticos mistos e limites de G05.
Cada tentativa deve ser lida com seu freeze e resultado próprios; um corpus
anterior não aprova automaticamente uma fonte corrigida. Contratos funcionais
Qt e inspeção de pixels são provas distintas. Capturas marcadas como
`NOT_QUALIFIED` não certificam pintura ou legibilidade na sessão nativa.
"""
    (destination / "SOURCE_RELEASE.md").write_text(source_release, encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--release-root", type=Path)
    parser.add_argument("--canonical-release-root", type=Path,
                        help="Original absolute build root for byte-preserved relocated reports; reads only the working --release-root")
    parser.add_argument("--inventory-only", action="store_true", help="Validate and report sizes without copying or writing")
    args = parser.parse_args()
    if args.inventory_only:
        included, excluded = inventory(args.source_root, args.release_root,
                                       canonical_release_root=args.canonical_release_root)
        print(json.dumps({"files": len(included),
                          "bytes": sum(len(self_test_projection(path)) if kind == "self_test_projection"
                                       else path.stat().st_size for path, kind in included.values()),
                          "counts_by_kind": dict(Counter(kind for _, kind in included.values())),
                          "excluded_files": len(excluded),
                          "largest": sorted(({"path": name, "bytes": len(self_test_projection(path))
                                              if kind == "self_test_projection" else path.stat().st_size}
                                             for name, (path, kind) in included.items()),
                                            key=lambda item: -item["bytes"])[:10]}, indent=2))
        return
    if args.destination is None:
        parser.error("--destination is required unless --inventory-only is selected")
    result = export(args.source_root, args.destination, args.release_root,
                    canonical_release_root=args.canonical_release_root)
    print(json.dumps({"destination": str(args.destination.resolve()), "files": len(result["files"]),
                      "bytes": result["bytes"], "counts_by_kind": result["counts_by_kind"],
                      "latest_qualification_release_delta": result["latest_qualification_release_delta"]}, indent=2))


if __name__ == "__main__":
    main()
