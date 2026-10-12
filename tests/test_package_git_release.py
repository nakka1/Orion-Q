"""Release snapshot checks protect scientific history and private state."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil

import pytest


SPEC = importlib.util.spec_from_file_location("package_git_release", Path(__file__).resolve().parents[1] / "tools/package_git_release.py")
packager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packager)
PUBLISHER_SPEC = importlib.util.spec_from_file_location(
    "publish_windows_native", Path(__file__).resolve().parents[1] / "tools/publish_windows_native_5_64_2.py")
publisher = importlib.util.module_from_spec(PUBLISHER_SPEC)
PUBLISHER_SPEC.loader.exec_module(publisher)


@pytest.mark.parametrize("relative", [
    "work/laboneq-env/Lib/site-packages/sdk.py",
    ".pytest_security_ogs_full/test_case/state.json",
    "src/__pycache__/module.pyc",
    "src/private.key",
    "src/certificate.pfx",
    "data/raw/source.json",
    "external/sequence/README.md",
    "tests/state/credentials.json",
    "src/.env",
    "dashboard/.vinext/fonts/generated/style.css",
    "dashboard/.wrangler/state/fixture.json",
    "dashboard/.next/cache/font.json",
    "dashboard/tsconfig.tsbuildinfo",
    ".git/config",
    "../outside.py",
])
def test_unsafe_source_paths_are_excluded(relative):
    assert packager.exclusion(relative, source=True)


def test_regular_source_and_secret_vault_implementation_are_preserved():
    for name in ("src/quantum_autopilot/security/vault.py", "tests/test_secret_store.py",
                 "tools/package_git_release.py", ".github/workflows/ci.yml"):
        assert packager.exclusion(name, source=True) is None


def test_actual_private_key_block_refuses_export_without_exposing_bytes(tmp_path):
    private = tmp_path / "plausible-config.json"
    private.write_bytes(b"-----BEGIN PRIVATE KEY-----\nPRIVATE_VALUE\n-----END PRIVATE KEY-----\n")
    with pytest.raises(ValueError, match="Possible private key/service token") as failure:
        packager.secret_check(private)
    assert "PRIVATE_VALUE" not in str(failure.value)


def test_service_token_refuses_export(tmp_path):
    path = tmp_path / "settings.py"
    path.write_text("token = 'ghp_" + "a" * 36 + "'", encoding="utf-8")
    with pytest.raises(ValueError, match="Possible private key/service token"):
        packager.secret_check(path)


def minimal_inputs(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    files = ["src/sample.py", "src/deleted.py", "work/venv/settings.py", "src/too-large.bin"]
    for name, raw in ((files[0], b"print('current working edit')\n"),
                      (files[2], b"PRIVATE_ENV"), (files[3], b"x" * 50)):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    resource = "outputs/pinned/model.npz"
    path = root / resource
    path.parent.mkdir(parents=True)
    path.write_bytes(b"PINNED")
    report = "outputs/test-science/run1/report.json"
    path = root / report
    path.parent.mkdir(parents=True)
    path.write_text('{"status":"partial_verified"}', encoding="utf-8")
    monkeypatch.setattr(packager, "source_names", lambda _: files)
    monkeypatch.setattr(packager, "BUILD_RESOURCES", (resource,))
    monkeypatch.setattr(packager, "REQUIRED_EVIDENCE", (report,))
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: {root / report})
    monkeypatch.setattr(packager, "MAX_FILE_BYTES", 30)
    return root, files, report


def test_inventory_preserves_working_edit_and_deletion_without_copying_private_env(tmp_path, monkeypatch):
    root, files, _ = minimal_inputs(tmp_path, monkeypatch)
    included, excluded = packager.inventory(root)
    assert included[files[0]][0].read_bytes() == b"print('current working edit')\n"
    assert files[1] not in included
    assert files[2] not in included
    assert files[3] not in included
    reasons = {item["path"]: item["reason"] for item in excluded}
    assert reasons[files[1]] == "deleted/missing working file"
    assert "limit" in reasons[files[3]]


@pytest.mark.parametrize("relative", sorted(packager.PINNED_TEST_ARCHIVES))
def test_reviewed_test_archives_are_included_only_with_their_pinned_bytes(tmp_path, monkeypatch, relative):
    root, files, _ = minimal_inputs(tmp_path, monkeypatch)
    # This test uses the tracked fixture itself, not any outputs/work artifact.
    source = Path(__file__).resolve().parents[1] / relative
    target = root / relative
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    files.append(relative)
    monkeypatch.setattr(packager, "MAX_FILE_BYTES", source.stat().st_size + 1)
    included, _ = packager.inventory(root)
    assert relative in included and included[relative][0].read_bytes() == source.read_bytes()
    assert packager.digest(target) == packager.PINNED_TEST_ARCHIVES[relative]


@pytest.mark.parametrize("relative", sorted(packager.PINNED_TEST_ARCHIVES))
def test_tampered_test_archive_refuses_inventory_before_export(tmp_path, monkeypatch, relative):
    root, files, _ = minimal_inputs(tmp_path, monkeypatch)
    target = root / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(b"UNREVIEWED_ARCHIVE")
    files.append(relative)
    with pytest.raises(ValueError, match="Pinned public test archive integrity mismatch"):
        packager.inventory(root)


def test_unreviewed_archives_remain_excluded_even_inside_fixture_directory():
    assert packager.exclusion("tests/fixtures/qualification/unreviewed.zip", source=True)
    for relative in packager.PINNED_TEST_ARCHIVES:
        assert packager.exclusion(relative + ".zip", source=True)


def test_missing_scientific_report_is_a_hard_failure(tmp_path, monkeypatch):
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    (root / report).unlink()
    with pytest.raises(FileNotFoundError, match="Required scientific evidence"):
        packager.inventory(root)


def test_symlink_is_not_followed(tmp_path, monkeypatch):
    root, files, _ = minimal_inputs(tmp_path, monkeypatch)
    linked = root / "src/linked.py"
    # A platform-independent mock exercises the path policy without requiring
    # Windows symlink privileges in a clean installation.
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == linked or original(self))
    linked.write_text("not exported", encoding="utf-8")
    files.append("src/linked.py")
    included, excluded = packager.inventory(root)
    assert "src/linked.py" not in included
    assert any(item["reason"] == "symlink requires explicit review" for item in excluded)


def test_existing_destination_is_never_overwritten(tmp_path):
    marker = tmp_path / "keep.txt"
    marker.write_text("unchanged", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        packager.export(tmp_path / "source", tmp_path)
    assert marker.read_text(encoding="utf-8") == "unchanged"


def test_destination_cannot_replace_source_ancestor(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        packager.export(root, root)


def test_release_selection_never_ingests_nearby_private_state(tmp_path, monkeypatch):
    root, _, _ = minimal_inputs(tmp_path, monkeypatch)
    release = root / "release/build-test"
    for name, raw in (
        ("laboratory-validation/report.json", b'{"passed":true}'),
        ("laboratory-validation/keys.json", b"-----BEGIN PRIVATE KEY-----\nPRIVATE\n"),
        ("some-unknown-security/report.json", b"-----BEGIN PRIVATE KEY-----\nPRIVATE\n"),
        ("pytest-sample/test_case/receipt.json", b"PRIVATE_CONFIG"),
    ):
        path = release / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    included, _ = packager.inventory(root, release)
    release_names = [name for name, (_, kind) in included.items() if kind == "release_evidence"]
    assert release_names == ["evidence/release/build-test/laboratory-validation/report.json"]


@pytest.mark.parametrize("filename", ["live.json", "snapshots.json", "satellite-seed.ogs-packets.json"])
def test_raw_live_state_is_excluded_even_when_small(tmp_path, monkeypatch, filename):
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    raw = root / "outputs/test-science/run1" / filename
    raw.write_text('{"private_state":"fixture"}', encoding="utf-8")
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: {root / report, raw})
    included, excluded = packager.inventory(root)
    assert raw.relative_to(root).as_posix() not in included
    assert any("raw observation/state" in row["reason"] for row in excluded)


def test_export_verifies_every_copy_and_classifies_generated_metadata(tmp_path, monkeypatch):
    import json

    root, files, _ = minimal_inputs(tmp_path, monkeypatch)
    required = ("pyproject.toml", "src/quantum_autopilot/console/app.py",
                "tools/build_windows_console.ps1", "runtime_assets/native/v1/manifest.json")
    for name in required:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[project]\nversion="5.65.0"\n' if name == "pyproject.toml" else "release",
                        encoding="utf-8")
        files.append(name)
    freeze = root / "outputs/hybrid_remediation_20261010/run3/source-freeze.json"
    freeze.parent.mkdir(parents=True)
    freeze.write_text(json.dumps({"files": {"pyproject.toml": "old-version-hash",
                                            "src/generated.egg-info/PKG-INFO": "old-metadata"}}), encoding="utf-8")
    monkeypatch.setattr(packager, "git", lambda *args: b"SOURCE_HEAD")
    destination = tmp_path / "snapshot"
    manifest = packager.export(root, destination)
    assert manifest["source_history_included"] is False
    assert manifest["source_head"] == "SOURCE_HEAD"
    assert {row["category"] for row in manifest["latest_qualification_release_delta"]} == {
        "changed release source or omitted source", "omitted generated distribution metadata"}
    for row in manifest["files"]:
        assert packager.digest(destination / row["path"]) == row["sha256"]
    assert (root / files[0]).read_bytes() == b"print('current working edit')\n"
    assert not (destination / ".git").exists()


def test_release_notes_bind_current_version_and_preserve_terminal_sources(tmp_path, monkeypatch):
    root, files, _ = minimal_inputs(tmp_path, monkeypatch)
    current_report = "docs/results/orionq_release_5_65_0_20261011.md"
    terminal_sources = ("src/quantum_autopilot/service/terminal_effect.py",
                        "src/quantum_autopilot/service/terminal_brand_art.py")
    for name in ("pyproject.toml", "src/quantum_autopilot/console/app.py",
                 "tools/build_windows_console.ps1", "runtime_assets/native/v1/manifest.json",
                 current_report, *terminal_sources):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[project]\nversion="5.65.0"\n' if name == "pyproject.toml" else "current bytes\n",
                        encoding="utf-8")
        files.append(name)
    monkeypatch.setattr(packager, "git", lambda *_: b"source-head\n")
    destination = tmp_path / "current-export"
    manifest = packager.export(root, destination)
    assert manifest["source_version"] == "5.65.0"
    assert manifest["current_release_report"] == current_report
    notes = (destination / "SOURCE_RELEASE.md").read_text(encoding="utf-8")
    assert "clone OrionQ-5.65.0-github.bundle OrionQ-5.65.0" in notes
    assert current_report in notes
    assert "OrionQ-5.64.2" not in notes
    assert "orionq_release_5_64_2_20261011.md" not in notes
    for name in terminal_sources:
        assert (destination / name).read_bytes() == (root / name).read_bytes()
    historical = (destination / "GIT-RELEASE.md").read_text(encoding="utf-8")
    assert "perfil Qt histórico 5.64.2" in historical
    assert "seus resultados não qualificam o snapshot 5.65.0" in historical


def test_missing_current_report_never_falls_back_to_previous_release(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="6.0.1"\n', encoding="utf-8")
    previous = tmp_path / "docs/results/orionq_release_5_64_2_20261011.md"
    previous.parent.mkdir(parents=True)
    previous.write_text("historical", encoding="utf-8")
    assert packager.source_release_metadata(tmp_path) == ("6.0.1", None)


def test_supplemental_audit_does_not_replace_primary_current_release_report(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="5.65.0"\n', encoding="utf-8")
    reports = tmp_path / "docs/results"
    reports.mkdir(parents=True)
    primary = reports / "orionq_release_5_65_0_20261011.md"
    primary.write_text("primary", encoding="utf-8")
    (reports / "orionq_release_5_65_0_audit_20261011.md").write_text("supplemental", encoding="utf-8")
    assert packager.source_release_metadata(tmp_path) == (
        "5.65.0", "docs/results/orionq_release_5_65_0_20261011.md")
    primary.unlink()
    assert packager.source_release_metadata(tmp_path) == ("5.65.0", None)


def test_release_version_must_come_from_project_metadata(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[other]\nversion="5.65.0"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="declared numeric project version"):
        packager.source_release_metadata(tmp_path)


def test_current_release_selection_keeps_exact_reports_without_reading_private_neighbors(tmp_path, monkeypatch):
    public_names = (
        "outputs/release-5.65.0-20261011/windows/source-freeze.json",
        "outputs/release-5.65.0-20261011/audit/terminal.xml",
        "outputs/release-5.65.0-20261011/audit/final-junit/terminal.xml",
        "outputs/release-5.65.0-20261011/audit/final-audit.json",
        "outputs/release-5.65.0-20261011/audit/native-viewport/summary.json",
        "outputs/release-5.65.0-20261011/audit/native-viewport/node-CIMATEC-top-restored.png",
        "outputs/release-5.65.0-20261011/linux/gui-wayland-startup.json",
        "outputs/release-5.65.0-20261011/linux/source-tests-summary.json",
        "outputs/release-5.65.0-20261011/linux/version-regression-final-linux.xml",
        "outputs/release-5.65.0-20261011/linux/core/report.json",
        "release/linux/5.65.0-native-headless-20261011/evidence/bundle-files.json",
    )
    for name in public_names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    private = tmp_path / "outputs/release-5.65.0-20261011/audit/state/credentials.json"
    private.parent.mkdir(parents=True)
    private.write_text("private adjacent input", encoding="utf-8")
    original_open = Path.open

    def public_open(path, *args, **kwargs):
        assert path != private, "Adjacent private state must never be read"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", public_open)
    selected = packager.artifact_candidates(tmp_path)
    assert {tmp_path / name for name in public_names}.issubset(selected)
    assert private not in selected


def test_exact_performance_resource_jsonl_is_preserved(tmp_path, monkeypatch):
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    relative = "outputs/trl5_continuation_20261009/run1/performance-baseline/resources.jsonl"
    resource = root / relative
    resource.parent.mkdir(parents=True)
    original = b'{"cpu_percent":12.5,"rss_bytes":1048576}\r\n'
    resource.write_bytes(original)
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: {root / report, resource})
    included, _ = packager.inventory(root)
    assert included[relative][0].read_bytes() == original
    assert included[relative][1] == "qualification_evidence"


@pytest.mark.parametrize("relative", [
    "outputs/trl5_continuation_20261009/run1/performance-baseline/events.jsonl",
    "outputs/trl5_continuation_20261009/run1/performance-baseline/raw-source-simulated.jsonl",
    "outputs/trl5_continuation_20261009/run1/other/resources.jsonl",
    "outputs/hybrid_remediation_20261010/run3/events.jsonl",
])
def test_other_raw_jsonl_is_never_selected_as_scientific_evidence(tmp_path, monkeypatch, relative):
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    raw = root / relative
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b'{"raw_event":"must_remain_local"}\n')
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: {root / report, raw})
    included, excluded = packager.inventory(root)
    assert relative not in included
    assert any(row["path"] == relative and "outside curated evidence formats" in row["reason"]
               for row in excluded)


@pytest.mark.parametrize("relative", [
    "outputs/orionq_release_5_61_0_20261010/sequence-current-inventory-state/demo/vault.json",
    "outputs/orionq_release_5_61_0_20261010/sequence-current-inventory-state/demo/.security/vault.json",
    "outputs/orionq_release_5_61_0_20261010/other/.security/vault.json",
    "outputs/orionq_release_5_61_0_20261010/other/.dpapi/protected.json",
])
def test_private_state_vault_and_dpapi_folders_are_not_read_or_exported(tmp_path, monkeypatch, relative):
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    private = root / relative
    private.parent.mkdir(parents=True)
    private.write_bytes(b'{"synthetic_dpapi_blob":"private_fixture"}')
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: {root / report, private})
    original_check = packager.secret_check

    def reject_read(path):
        if path == private:
            raise AssertionError("Private state must be excluded before secret/content inspection")
        return original_check(path)

    monkeypatch.setattr(packager, "secret_check", reject_read)
    included, excluded = packager.inventory(root)
    assert relative not in included
    assert any(row["path"] == relative and "private state" in row["reason"] for row in excluded)


@pytest.mark.parametrize("relative", sorted(packager.CURATED_AUDIT_FILES))
def test_exact_build_resume_audit_files_are_selected(tmp_path, monkeypatch, relative):
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    audit = root / relative
    audit.parent.mkdir(parents=True)
    audit.write_bytes(b"archived continuation evidence\n")
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: {root / report, audit})
    included, _ = packager.inventory(root)
    assert included[relative][1] == "qualification_evidence"


@pytest.mark.parametrize("relative", [
    "outputs/orionq_release_5_61_0_20261010/other_script.ps1",
    "outputs/orionq_release_5_61_0_20261010/other.diff",
    "outputs/orionq_release_5_61_0_20261010/untrusted/resume_windows_build.ps1",
])
def test_other_evidence_scripts_and_diffs_are_not_selected(tmp_path, monkeypatch, relative):
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    script = root / relative
    script.parent.mkdir(parents=True)
    script.write_bytes(b"must not broaden audit allowlist\n")
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: {root / report, script})
    included, excluded = packager.inventory(root)
    assert relative not in included
    assert any(row["path"] == relative and "outside curated evidence formats" in row["reason"]
               for row in excluded)


TRANSCRIPTS = sorted(name for name in packager.CURATED_AUDIT_FILES if name.endswith(".log"))


@pytest.mark.parametrize("relative", TRANSCRIPTS)
def test_exact_transcripts_cannot_bypass_secret_scanning(tmp_path, monkeypatch, relative):
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    transcript = root / relative
    transcript.parent.mkdir(parents=True)
    transcript.write_bytes(b"-----BEGIN PRIVATE KEY-----\nPRIVATE_FIXTURE\n-----END PRIVATE KEY-----\n")
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: {root / report, transcript})
    with pytest.raises(ValueError, match="Possible private key/service token") as failure:
        packager.inventory(root)
    assert "PRIVATE_FIXTURE" not in str(failure.value)


@pytest.mark.parametrize("relative", TRANSCRIPTS)
def test_exact_transcripts_above_8mib_are_excluded_before_content_inspection(tmp_path, monkeypatch, relative):
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    transcript = root / relative
    transcript.parent.mkdir(parents=True)
    assert packager.MAX_EVIDENCE_BYTES == 8 * 1024 * 1024
    with transcript.open("wb") as stream:
        stream.seek(packager.MAX_EVIDENCE_BYTES)
        stream.write(b"x")
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: {root / report, transcript})
    original_check = packager.secret_check

    def reject_oversize_read(path):
        if path == transcript:
            raise AssertionError("Oversize transcript must be excluded before content inspection")
        return original_check(path)

    monkeypatch.setattr(packager, "secret_check", reject_oversize_read)
    included, excluded = packager.inventory(root)
    assert relative not in included
    assert any(row["path"] == relative and "limit" in row["reason"] for row in excluded)


@pytest.mark.parametrize("relative", [
    "outputs/orionq_release_5_61_0_20261010/other.log",
    "outputs/orionq_release_5_61_0_20261010/orionq_build_other_resume.log",
    "outputs/orionq_release_5_61_0_20261010/nested/orionq_build_5_61_0_20261010_resume_v3.log",
    "outputs/orionq_release_5_61_0_20261010/.security/orionq_build_5_61_0_20261010_resume_v3.log",
    "outputs/orionq_release_5_61_0_20261010/.dpapi/orionq_build_5_61_0_20261010_resume_v3.log",
    "outputs/orionq_release_5_61_0_20261010/sequence-current-inventory-state/orionq_build_5_61_0_20261010_resume_v3.log",
])
def test_other_logs_and_private_state_transcripts_are_never_read(tmp_path, monkeypatch, relative):
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    transcript = root / relative
    transcript.parent.mkdir(parents=True)
    transcript.write_bytes(b"-----BEGIN PRIVATE KEY-----\nPRIVATE_FIXTURE\n")
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: {root / report, transcript})
    original_check = packager.secret_check

    def reject_private_read(path):
        if path == transcript:
            raise AssertionError("Noncurated/private log must be excluded before content inspection")
        return original_check(path)

    monkeypatch.setattr(packager, "secret_check", reject_private_read)
    included, excluded = packager.inventory(root)
    assert relative not in included
    assert any(row["path"] == relative for row in excluded)


def release_fixture(root):
    release = root / "release/build-test"
    run = "global-validation/run-20261010T171000123456"
    stress = "synthetic-controller-" + "a" * 32
    names = set(packager.RELEASE_REPORTS)
    names.update(child for children in packager.RELEASE_CHILDREN.values() for child in children)
    names.update(child for children in packager.RELEASE_OPTIONAL_CHILDREN.values() for child in children)
    names.update(f"{parent}/{stress}/report.json"
                 for parent in ("stress-validation", "native-acceptance/stress"))
    names.update(f"{run}/{child}" for child in ("timeline.json", "isl-obstruction-scenarios.json"))
    for name in names:
        path = release / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"passed":true}\r\n')
    global_report = {
        "run_directory": str(release / run),
        "scenarios": {"isl_obstruction_file": str(release / run / "isl-obstruction-scenarios.json")},
    }
    (release / "global-validation/report.json").write_text(json.dumps(global_report), encoding="utf-8")
    return release, names, run, stress


def test_native_gate_closure_selects_exact_children_without_raw_adjacent_state(tmp_path, monkeypatch):
    root, _, _ = minimal_inputs(tmp_path, monkeypatch)
    release, expected, run, _ = release_fixture(root)
    raw_names = (
        "campaign-validation/confirmed-receipt.json", "campaign-validation/operational-page.zip",
        "satellite-mission-validation/reception/acquisitions.jsonl",
        "satellite-mission-validation/reception/.security/vault.json",
        f"{run}/global-operations.json", f"{run}/isolated-isl-events.jsonl",
        f"{run}/state.sqlite", f"{run}/final-operations/live.json",
        "native-acceptance/stress/credentials.json",
    )
    for name in raw_names:
        path = release / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"-----BEGIN PRIVATE KEY-----\nPRIVATE_FIXTURE\n")
    included, _ = packager.inventory(root, release)
    selected = {name.removeprefix("evidence/release/build-test/")
                for name, (_, kind) in included.items() if kind in {"release_evidence", "self_test_projection"}}
    expected.difference_update(packager.RELEASE_SELF_TESTS)
    expected.update(name.removesuffix(".json") + "-summary.json" for name in packager.RELEASE_SELF_TESTS)
    assert selected == expected
    assert not selected.intersection(raw_names)


FIXED_CHILDREN = sorted(child for children in packager.RELEASE_CHILDREN.values() for child in children)


@pytest.mark.parametrize("missing", FIXED_CHILDREN)
def test_missing_required_native_gate_child_refuses_snapshot(tmp_path, monkeypatch, missing):
    root, _, _ = minimal_inputs(tmp_path, monkeypatch)
    release, _, _, _ = release_fixture(root)
    (release / missing).unlink()
    with pytest.raises(FileNotFoundError, match="Required release gate child missing"):
        packager.inventory(root, release)


@pytest.mark.parametrize("case", ["zero", "two", "unknown", "nested", "symlink", "reparse"])
def test_stress_gate_requires_exactly_one_safe_direct_run(tmp_path, monkeypatch, case):
    root, _, _ = minimal_inputs(tmp_path, monkeypatch)
    release, _, _, stress = release_fixture(root)
    parent = release / "stress-validation"
    original = parent / stress
    if case == "zero":
        (original / "report.json").unlink()
        original.rmdir()
    elif case in {"two", "unknown"}:
        (parent / ("synthetic-controller-" + "b" * 32 if case == "two" else "unknown")).mkdir()
    elif case == "nested":
        (original / "report.json").rename(original / "nested.json")
        (original / "nested").mkdir()
        (original / "nested/report.json").write_text("{}", encoding="utf-8")
    elif case == "symlink":
        original_is_symlink = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda self: self == original or original_is_symlink(self))
    else:
        original_stat = Path.stat

        def reparse_stat(path, *, follow_symlinks=True):
            if path == original and not follow_symlinks:
                return type("Reparse", (), {"st_file_attributes": 0x400, "st_mode": original_stat(path).st_mode})()
            return original_stat(path, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(Path, "stat", reparse_stat)
    with pytest.raises((ValueError, FileNotFoundError)):
        packager.inventory(root, release)


@pytest.mark.parametrize("case", ["escape", "private", "wrong_reference", "missing_timeline", "symlink", "reparse"])
def test_global_gate_only_follows_bounded_safe_timestamp_run(tmp_path, monkeypatch, case):
    root, _, _ = minimal_inputs(tmp_path, monkeypatch)
    release, _, run, _ = release_fixture(root)
    report_path = release / "global-validation/report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    run_path = release / run
    if case == "escape":
        report["run_directory"] = str(root / "outside" / run_path.name)
    elif case == "private":
        report["run_directory"] = str(release / "global-validation/.security" / run_path.name)
    elif case == "wrong_reference":
        report["scenarios"]["isl_obstruction_file"] = str(run_path / "events.jsonl")
    elif case == "missing_timeline":
        (run_path / "timeline.json").unlink()
    elif case == "symlink":
        original_is_symlink = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda self: self == run_path or original_is_symlink(self))
    else:
        original_stat = Path.stat

        def reparse_stat(path, *, follow_symlinks=True):
            if path == run_path and not follow_symlinks:
                return type("Reparse", (), {"st_file_attributes": 0x400, "st_mode": original_stat(path).st_mode})()
            return original_stat(path, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(Path, "stat", reparse_stat)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        packager.inventory(root, release)


def test_native_evidence_map_preserves_original_json_bytes_and_hashes(tmp_path, monkeypatch):
    root, files, _ = minimal_inputs(tmp_path, monkeypatch)
    release, _, _, _ = release_fixture(root)
    desktop = release / "desktop-behavior-validation/report.json"
    original = json.dumps({"native_report": str(release / "desktop-behavior-validation/native/report.json")}).encode() + b"\r\n"
    desktop.write_bytes(original)
    for name in ("pyproject.toml", "src/quantum_autopilot/console/app.py",
                 "tools/build_windows_console.ps1", "runtime_assets/native/v1/manifest.json"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[project]\nversion="5.65.0"\n' if name == "pyproject.toml" else "release",
                        encoding="utf-8")
        files.append(name)
    monkeypatch.setattr(packager, "git", lambda *args: b"SOURCE_HEAD")
    destination = tmp_path / "portable"
    manifest = packager.export(root, destination, release)
    mapping = json.loads((destination / "release-evidence-map.json").read_text(encoding="utf-8"))
    assert not mapping["original_json_bytes_preserved"]
    assert not mapping["qualifications_reexecuted_by_mapping"]
    assert mapping["original_source_release_root"] == str(release)
    assert mapping["package_release_root"] == "evidence/release/build-test"
    row = next(row for row in mapping["files"] if row["original_path"] == str(desktop))
    assert (destination / row["path"]).read_bytes() == desktop.read_bytes() == original
    assert packager.digest(destination / row["path"]) == row["sha256"] == packager.digest(desktop)
    for copied in manifest["files"]:
        assert packager.digest(destination / copied["path"]) == copied["sha256"]
    assert "release-evidence-map.json" in {row["path"] for row in manifest["files"]}


def test_native_gate_child_cannot_bypass_evidence_size_limit(tmp_path, monkeypatch):
    root, _, _ = minimal_inputs(tmp_path, monkeypatch)
    release, _, _, _ = release_fixture(root)
    child = release / "desktop-behavior-validation/native/report.json"
    with child.open("wb") as stream:
        stream.seek(packager.MAX_EVIDENCE_BYTES)
        stream.write(b"x")
    with pytest.raises(ValueError, match="Selected release gate evidence excluded by policy"):
        packager.inventory(root, release)


def test_only_30_official_root_junits_bypass_pytest_scratch_policy(tmp_path, monkeypatch):
    root, _, _ = minimal_inputs(tmp_path, monkeypatch)
    release = root / "release/build-test"
    release.mkdir(parents=True)
    assert len(packager.RELEASE_JUNIT_REPORTS) == 30
    for name in packager.RELEASE_JUNIT_REPORTS:
        (release / name).write_bytes(b'<testsuites><testsuite tests="1" failures="0" errors="0"><testcase name="ok"/></testsuite></testsuites>\r\n')
    included, _ = packager.inventory(root, release)
    actual = {Path(name).name for name, (_, kind) in included.items() if kind == "release_evidence"}
    assert actual == packager.RELEASE_JUNIT_REPORTS
    for name in actual:
        mapped = f"evidence/release/build-test/{name}"
        assert packager.release_junit_path(name) and packager.release_junit_path(mapped)
        assert included[mapped][0].read_bytes() == (release / name).read_bytes()


@pytest.mark.parametrize("relative", [
    "pytest-unknown-results.xml", "pytest-case/test_case/pytest-channel-intake-results.xml",
    ".security/pytest-channel-intake-results.xml", ".dpapi/pytest-channel-intake-results.xml",
    "native-acceptance/pytest-channel-intake-results.xml",
])
def test_unknown_or_nested_junit_names_never_import_private_state(tmp_path, monkeypatch, relative):
    root, _, _ = minimal_inputs(tmp_path, monkeypatch)
    release = root / "release/build-test"
    path = release / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b"-----BEGIN PRIVATE KEY-----\nPRIVATE_FIXTURE\n")
    original_check = packager.secret_check

    def reject_private_read(candidate):
        if candidate == path:
            raise AssertionError("Nonofficial or nested JUnit must not be inspected")
        return original_check(candidate)

    monkeypatch.setattr(packager, "secret_check", reject_private_read)
    included, _ = packager.inventory(root, release)
    assert not packager.release_junit_path(relative)
    assert not any(candidate == path for candidate, _ in included.values())


@pytest.mark.parametrize("relative", [
    "outputs/pytest-channel-intake-results.xml", "evidence/release/.security/pytest-channel-intake-results.xml",
    "evidence/release/build-test/nested/pytest-channel-intake-results.xml",
    "evidence/release/build-test/pytest-unknown-results.xml", "../pytest-channel-intake-results.xml",
])
def test_official_junit_exception_cannot_expand_its_path_scope(relative):
    assert not packager.release_junit_path(relative)


def test_17mib_selftest_projection_preserves_checks_but_not_raw_state(tmp_path):
    original = tmp_path / "self-test.json"
    raw = {"passed": False, "check_count": 2, "checks": {"first": True, "second": False},
           "communicators_geo": {"passed": False, "checks": {"transmission": False}, "state": "DO_NOT_EXPORT_STATE"},
           "global_network_operations": {"passed": True, "checks": [{"name": "causal", "passed": True,
                                         "detail": "RAW_PAYLOAD_MARKER" + "x" * (17 * 1024 * 1024)}]},
           "keys": {"password": "DO_NOT_EXPORT_KEYS"}, "receipts": ["DO_NOT_EXPORT_RECEIPTS"],
           "photon_payloads": "DO_NOT_EXPORT_PHOTONS", "unknown_section": {"checks": {"ignored": True}}}
    original.write_text(json.dumps(raw), encoding="utf-8")
    before = packager.digest(original)
    rendered = packager.self_test_projection(original)
    summary = json.loads(rendered)
    assert len(rendered) < packager.MAX_EVIDENCE_BYTES == 8 * 1024 * 1024
    assert original.stat().st_size > 17 * 1024 * 1024
    assert summary["checks"] == raw["checks"] and summary["passed"] is False
    assert summary["subsection_checks"]["communicators_geo"]["checks"] == {"transmission": False}
    assert summary["subsection_checks"]["global_network_operations"]["checks"] == [{"name": "causal", "passed": True}]
    assert "unknown_section" not in summary["subsection_checks"]
    assert summary["projection"]["original_sha256"] == before == packager.digest(original)
    assert summary["projection"]["original_bytes"] == original.stat().st_size
    assert summary["projection"]["derived_summary"]
    assert not summary["projection"]["original_json_bytes_preserved"]
    for marker in ("DO_NOT_EXPORT", "RAW_PAYLOAD_MARKER"):
        assert marker.encode() not in rendered


@pytest.mark.parametrize("bad", [
    {"passed": "yes", "checks": {}}, {"passed": True, "checks": {"result": 1}},
    {"passed": True, "checks": [{"name": "bad", "passed": "true"}]},
])
def test_selftest_projection_rejects_malformed_check_contract(tmp_path, bad):
    path = tmp_path / "self-test.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="projection"):
        packager.self_test_projection(path)


def test_projection_input_limit_is_bounded_before_secret_or_json_reads(tmp_path, monkeypatch):
    path = tmp_path / "self-test.json"
    with path.open("wb") as stream:
        stream.seek(packager.MAX_PROJECTION_SOURCE_BYTES)
        stream.write(b"x")
    monkeypatch.setattr(packager, "secret_check", lambda _: pytest.fail("Oversize original must not be read"))
    with pytest.raises(ValueError, match="bounded projection input limit"):
        packager.self_test_projection(path)


def test_only_exact_historical_large_selftest_gets_projection(tmp_path, monkeypatch):
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    original_relative, summary_relative = next(iter(packager.SELF_TEST_PROJECTIONS.items()))
    original = root / original_relative
    original.parent.mkdir(parents=True)
    original.write_text(json.dumps({"passed": False, "checks": {"failure": False},
                                    "raw_live": "x" * (9 * 1024 * 1024)}), encoding="utf-8")
    unknown = original.with_name("another-self-test.json")
    unknown.write_bytes(original.read_bytes())
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: {root / report, original, unknown})
    included, excluded = packager.inventory(root)
    assert summary_relative in included and included[summary_relative][1] == "self_test_projection"
    assert original_relative not in included and unknown.relative_to(root).as_posix() not in included
    assert any(row["path"] == unknown.relative_to(root).as_posix() and "limit" in row["reason"] for row in excluded)


def test_projection_manifest_and_map_distinguish_original_and_derived_hashes(tmp_path, monkeypatch):
    root, files, _ = minimal_inputs(tmp_path, monkeypatch)
    release, _, _, _ = release_fixture(root)
    original = release / "self-test.json"
    original.write_text(json.dumps({"passed": True, "checks": {"operational": True},
                                    "raw_live": "RAW_PAYLOAD_MARKER" + "x" * (17 * 1024 * 1024)}), encoding="utf-8")
    before = packager.digest(original)
    for name in ("pyproject.toml", "src/quantum_autopilot/console/app.py",
                 "tools/build_windows_console.ps1", "runtime_assets/native/v1/manifest.json"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[project]\nversion="5.65.0"\n' if name == "pyproject.toml" else "release",
                        encoding="utf-8")
        files.append(name)
    monkeypatch.setattr(packager, "git", lambda *args: b"SOURCE_HEAD")
    destination = tmp_path / "projected"
    manifest = packager.export(root, destination, release)
    target = "evidence/release/build-test/self-test-summary.json"
    row = next(row for row in manifest["files"] if row["path"] == target)
    assert row["kind"] == "self_test_projection" and row["derived_summary"] and not row["byte_preserved"]
    assert row["sha256"] != row["original_sha256"] == before == packager.digest(original)
    assert row["bytes"] < 8 * 1024 * 1024 < row["original_bytes"]
    assert not (destination / "evidence/release/build-test/self-test.json").exists()
    assert b"RAW_PAYLOAD_MARKER" not in (destination / target).read_bytes()
    mapping = json.loads((destination / "release-evidence-map.json").read_text())
    mapped = next(row for row in mapping["files"] if row["path"] == target)
    assert mapped["original_path"] == str(original) and mapped["original_sha256"] == before
    assert mapped["sha256"] == packager.digest(destination / target) and not mapped["byte_preserved"]
    assert not mapping["original_json_bytes_preserved"]


def test_relocation_proofs_are_closed_and_raw_selftest_gets_checks_only_projection(tmp_path, monkeypatch):
    root, _, _ = minimal_inputs(tmp_path, monkeypatch)
    release, _, _, _ = release_fixture(root)
    original = release / "relocation-acceptance/self-test.json"
    original.write_text(json.dumps({"passed": True, "checks": {"relocated": True},
                                    "raw_operator_frame": "PRIVATE_RAW_FRAME_MARKER"}), encoding="utf-8")
    included, _ = packager.inventory(root, release)
    prefix = "evidence/release/build-test/"
    assert prefix + "delivery-copy.json" in included
    assert prefix + "relocation-acceptance/acceptance-report.json" in included
    assert prefix + "relocation-acceptance/stress-report.json" in included
    assert prefix + "relocation-acceptance/delivery-relocation-proof.json" in included
    raw = prefix + "relocation-acceptance/self-test.json"
    projected = prefix + "relocation-acceptance/self-test-summary.json"
    assert raw not in included and included[projected] == (original, "self_test_projection")
    summary = json.loads(packager.self_test_projection(original))
    assert summary["checks"] == {"relocated": True}
    assert "raw_operator_frame" not in summary and b"PRIVATE_RAW_FRAME_MARKER" not in packager.self_test_projection(original)


@pytest.mark.parametrize("directory", [
    "preflight-v4", "OrionQ-git", "Git-verification",
    "frozen-verifier-communicator-temp", "frozen-verifier-communicator-temp2",
    "repro-live-network-temp", "topology-login-final-temp", "OrionQ-git-rejected-20261010",
    "delivery-longpaths-temp",
])
def test_generated_preflight_and_git_trees_are_pruned_without_content_reads(tmp_path, monkeypatch, directory):
    original_candidates = packager.artifact_candidates
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    evidence = root / "outputs/orionq_release_5_61_0_20261010"
    private = evidence / directory / "nested/global-operations.json"
    private.parent.mkdir(parents=True)
    private.write_bytes(b"-----BEGIN PRIVATE KEY-----\nDO_NOT_READ_PRIVATE_FIXTURE\n")
    public = evidence / "preflight-v4-summary.json"
    public.write_text('{"passed":true,"original_path":"preflight-v4/summary.json"}', encoding="utf-8")
    monkeypatch.setattr(packager, "EVIDENCE_TREES", (evidence.relative_to(root).as_posix(),))
    monkeypatch.setattr(packager, "EXTRA_EVIDENCE", ())
    original_read = Path.read_text
    original_check = packager.secret_check

    def guarded_read(path, *args, **kwargs):
        if path.is_relative_to(evidence / directory):
            raise AssertionError("Generated/private tree must be pruned before reading JSON")
        return original_read(path, *args, **kwargs)

    def guarded_secret(path):
        if path.is_relative_to(evidence / directory):
            raise AssertionError("Generated/private tree must be pruned before secret inspection")
        return original_check(path)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    monkeypatch.setattr(packager, "secret_check", guarded_secret)
    candidates = original_candidates(root)
    assert public in candidates and not any(path.is_relative_to(evidence / directory) for path in candidates)
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: candidates | {root / report})
    included, _ = packager.inventory(root)
    assert public.relative_to(root).as_posix() in included
    assert not any(path.is_relative_to(evidence / directory) for path, _ in included.values())


@pytest.mark.parametrize("relative", sorted(packager.PUBLIC_SOURCE_FILES))
def test_public_reproducers_and_rtl_are_preserved_nominally(relative):
    assert packager.exclusion(relative, source=True) is None


@pytest.mark.parametrize("relative", [
    "work/operator-state.json", "work/another_script.py", "work/venv/settings.py",
    "fpga/orionq_pl/implementation.bit", "fpga/orionq_pl/generated.bin", "fpga/other_design.v",
])
def test_nominal_source_allowlist_does_not_admit_neighboring_work_or_builds(relative):
    assert packager.exclusion(relative, source=True)


def test_source_enumeration_preserves_tracked_deletions_without_traversing_private_work(monkeypatch, tmp_path):
    calls = []

    def inventory_git(root, *arguments):
        calls.append(arguments)
        if "--cached" in arguments:
            return b"src/deleted.py\0src/edited.py\0"
        assert "--others" in arguments
        paths = set(arguments[arguments.index("--") + 1:])
        assert {"src", "tests", "packaging"}.issubset(paths)
        assert packager.PUBLIC_SOURCE_FILES.issubset(paths)
        assert not {"work", "fpga", "outputs", "release", ".git", ".audit-winvenv"}.intersection(paths)
        return b"src/new.py\0work/mixed_concurrency_audit_20261011.py\0"

    monkeypatch.setattr(packager, "git", inventory_git)
    assert packager.source_names(tmp_path) == [
        "src/deleted.py", "src/edited.py", "src/new.py", "work/mixed_concurrency_audit_20261011.py",
    ]
    assert len(calls) == 2


def test_recent_public_evidence_includes_failed_race_and_real_pty_without_private_neighbors(tmp_path, monkeypatch):
    original_candidates = packager.artifact_candidates
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    selected = {
        "outputs/controller_contention_20261011/legacy-pair-race.json": b'{"double_success":true}',
        "outputs/controller_contention_20261011/mixed-atomicity.json": b'{"atomic":true}',
        "outputs/terminal_brand_linux_20261011/terminal-campaign.txt": b"real PTY viewport\n",
        "outputs/terminal_brand_linux_20261011/tests.xml": b"<testsuite/>\n",
        "outputs/terminal_brand_linux_20261011/previews/overview.png": b"PUBLIC_PREVIEW",
    }
    for relative, content in selected.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    private = root / "outputs/controller_contention_20261011/operator-material.json"
    private.write_bytes(b"-----BEGIN PRIVATE KEY-----\nDO_NOT_READ_NEIGHBOR\n")
    monkeypatch.setattr(packager, "EVIDENCE_TREES", ())
    candidates = original_candidates(root)
    assert {path.relative_to(root).as_posix() for path in candidates} == set(selected)
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: candidates | {root / report})
    included, _ = packager.inventory(root)
    for relative, content in selected.items():
        assert included[relative][0].read_bytes() == content
        assert included[relative][1] == "qualification_evidence"
    assert private.relative_to(root).as_posix() not in included


@pytest.mark.parametrize("relative", [
    "packaging/core/prepare_source.py", "packaging/core/requirements.txt",
    "packaging/petalinux/meta-orionq/recipes-orionq/orionq-core/orionq-core.bb",
    "tests/test_portable_core_service.py", "tests/security_fixtures.py",
    "tests/fixtures/polarisation_plant_reference_v1.npz",
    "tests/fixtures/satellite_public/suomi100-frame-20261008-135420.bin",
])
def test_public_linux_contracts_and_scientific_fixtures_are_source_inputs(relative):
    assert packager.exclusion(relative, source=True) is None


@pytest.mark.parametrize("relative", [
    "docs/thesis/monografia/TCC_prebanca_v5_overleaf.zip", "src/generated.dll",
    "packaging/core/quantum_twin-5.64.2-py3-none-any.whl", "src/device.bit",
])
def test_distribution_and_nested_archives_are_not_source_inputs(relative):
    assert "deliver separately" in packager.exclusion(relative, source=True)


def test_terminal_delivery_hash_map_inputs_are_selected_without_bundling_wheel(tmp_path, monkeypatch):
    original_candidates = packager.artifact_candidates
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    names = (
        "linux-gate/source-hashes.json", "linux-gate/core-artifact.json",
        "linux-gate/installed-core-report.json", "linux-gate/cli-smoke.json",
        "linux-gate/dependency-freeze.log", "full-campaign.json", "full-campaign.txt",
    )
    base = root / "outputs/terminal_linux_20261011"
    for name in names:
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n" if path.suffix == ".json" else "public gate evidence\n", encoding="utf-8")
    wheel = root / "release/linux/5.64.1/quantum_twin-5.64.1-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(b"SEPARATE_DISTRIBUTION")
    manifest = base / "delivery-integrity.json"
    manifest.write_text(json.dumps({"exported_sha256": {
        **{path.relative_to(root).as_posix(): packager.digest(path) for path in base.rglob("*") if path.is_file()},
        wheel.relative_to(root).as_posix(): packager.digest(wheel),
    }}), encoding="utf-8")
    monkeypatch.setattr(packager, "EVIDENCE_TREES", ())
    candidates = original_candidates(root)
    assert {base / name for name in names}.issubset(candidates)
    assert wheel not in candidates
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: candidates | {root / report})
    included, _ = packager.inventory(root)
    assert {f"outputs/terminal_linux_20261011/{name}" for name in names}.issubset(included)
    assert wheel.relative_to(root).as_posix() not in included


def test_native_linux_public_manifest_does_not_import_binary_or_private_neighbors(tmp_path, monkeypatch):
    original_candidates = packager.artifact_candidates
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    base = root / "release/linux/5.64.2-native-headless-release"
    selected = {
        "build-report.json": b'{"status":"PASS","hardware_validated":false}',
        "evidence/bundle-files.json": b'{"files":[{"path":"bin/orion-core","sha256":"public_hash"}]}',
        "evidence/native-terminal-campaign.txt": b"real native PTY viewport\n",
    }
    for name, content in selected.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    private = base / "evidence/operator-state.json"
    private.write_bytes(b"-----BEGIN PRIVATE KEY-----\nDO_NOT_READ_NEIGHBOR\n")
    binary = base / "bin/orion-core"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"NATIVE_BINARY_DELIVERED_SEPARATELY")
    archive = base / "OrionQ-5.64.2-linux-x86_64-headless.tar.gz"
    archive.write_bytes(b"NATIVE_ARCHIVE_DELIVERED_SEPARATELY")
    gui_base = root / "release/linux/5.64.2-native-gui-bootstrap-release/evidence"
    gui_base.mkdir(parents=True)
    gui_selected = {
        "gui-remote-smoke-final.json": b'{"status":"PASS","physical_display_or_mouse_validated":false}',
        "gui-bootstrap-source-binding.json": b'{"current_modules":52,"auxiliary_delta":"SOURCES.txt"}',
    }
    for name, content in gui_selected.items():
        (gui_base / name).write_bytes(content)
    raw_gui = gui_base / "gui-self-test.json"
    raw_gui.write_bytes(b"-----BEGIN PRIVATE KEY-----\nDO_NOT_READ_RAW_SELF_TEST\n")
    monkeypatch.setattr(packager, "EVIDENCE_TREES", ())
    candidates = original_candidates(root)
    assert candidates == {base / name for name in selected} | {gui_base / name for name in gui_selected}
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: candidates | {root / report})
    included, _ = packager.inventory(root)
    for name, content in selected.items():
        assert included[(base / name).relative_to(root).as_posix()][0].read_bytes() == content
    for name, content in gui_selected.items():
        assert included[(gui_base / name).relative_to(root).as_posix()][0].read_bytes() == content
    assert not {private, binary, archive, raw_gui}.intersection(path for path, _ in included.values())


def test_bootstrap_public_selection_omits_previous_gui_candidate_before_reads(tmp_path, monkeypatch):
    original_candidates = packager.artifact_candidates
    root, _, _ = minimal_inputs(tmp_path, monkeypatch)
    current = root / "release/linux/5.64.2-native-gui-bootstrap-release/build-report.json"
    current.parent.mkdir(parents=True)
    current.write_text('{"status":"PASS","version":"5.64.2"}', encoding="utf-8")
    previous = root / "release/linux/5.64.2-native-gui-release/build-report.json"
    previous.parent.mkdir(parents=True)
    previous.write_bytes(b"-----BEGIN PRIVATE KEY-----\nDO_NOT_READ_OLD_CANDIDATE\n")
    expected = {current}
    for name in ("bootstrap-regressions.xml", "frozen-tool-regressions.xml", "build-bootstrap-context.json",
                 "bootstrap-regressions-attempt-fixture-api.xml", "campaign-topology-diagnostic.json",
                 "native-release-selected-test-summary.json", "native-delivery-summary.json",
                 "relocation-context.json", "relocation-exit-code.txt"):
        path = root / "outputs/native_release_5_64_2_20261011/windows" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "<testsuite/>" if path.suffix == ".xml" else '{"version":"5.64.2"}'
        path.write_text("0\n" if path.suffix == ".txt" else text, encoding="utf-8")
        expected.add(path)
    original_open = Path.open

    def current_public_only(path, *args, **kwargs):
        assert path != previous, "Previous candidate must not be read as current evidence"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", current_public_only)
    monkeypatch.setattr(packager, "EVIDENCE_TREES", ())
    assert original_candidates(root) == expected


def test_continuation_and_lifecycle_proofs_keep_original_failure_bytes_without_logs(tmp_path, monkeypatch):
    original_candidates = packager.artifact_candidates
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    base = root / "outputs/native_release_5_64_2_20261011/windows"
    contents = {
        "live-lifecycle-original.xml": b'<testsuite failures="1"><testcase name="legacy"><failure>obsolete lifecycle assertion</failure></testcase></testsuite>',
        "live-lifecycle-fixed.xml": b'<testsuite failures="0"><testcase name="admitted_bootstrap"/></testsuite>',
        "live-lifecycle-original-source.json": b'{"original_test_preserved":true}',
        "live-lifecycle-original-test.py": b"def test_legacy_lifecycle():\n    assert immediate_stepping\n",
        "live-network-interruption.json": b'{"original_full_script_passed":false}',
        "bootstrap-continuation/provenance.json": b'{"status":"PREPARED","original_full_script_passed":false}',
        "bootstrap-continuation/official-tail.ps1": b"$ErrorActionPreference = 'Stop'\n# literal public official tail\n",
        "bootstrap-continuation/failed-attempt-resolution.json": b'{"status":"FAILED","automatic_passed":false}',
        "bootstrap-continuation/failed-attempt/pytest-live-network-results.xml": b'<testsuite failures="1"><testcase name="previous"><failure>historical failure</failure></testcase></testsuite>',
    }
    for relative, content in contents.items():
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    private_paths = [base / "bootstrap-continuation/resume.log",
                     base / "bootstrap-continuation/failed-attempt/build-bootstrap.log",
                     base / "bootstrap-continuation/private/pytest-live-network-results.xml"]
    for path in private_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"-----BEGIN PRIVATE KEY-----\nDO_NOT_READ_NEIGHBOR\n")
    original_open = Path.open

    def public_only(path, *args, **kwargs):
        assert path not in private_paths, "Unselected logs or neighboring JUnits must not be read"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", public_only)
    monkeypatch.setattr(packager, "EVIDENCE_TREES", ())
    candidates = original_candidates(root)
    assert candidates == {base / relative for relative in contents}
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: candidates | {root / report})
    included, _ = packager.inventory(root)
    for relative, content in contents.items():
        path = base / relative
        assert included[path.relative_to(root).as_posix()][0].read_bytes() == content


@pytest.mark.parametrize("relative", [
    "outputs/native_release_5_64_2_20261011/windows/bootstrap-continuation/failed-attempt/private/pytest-live-network-results.xml",
    "outputs/native_release_5_64_2_20261011/windows/bootstrap-continuation/private/pytest-live-network-results.xml",
    "outputs/native_release_5_64_2_20261011/windows/bootstrap-continuation/failed-attempt/pytest-unknown-results.xml",
    "outputs/native_release_5_64_2_20261011/windows/.security/pytest-live-network-results.xml",
])
def test_historical_junit_exception_is_exact_and_keeps_private_prefix_guards(relative):
    assert packager.exclusion(relative)
    for allowed in packager.CURATED_HISTORICAL_JUNITS:
        assert packager.exclusion(allowed) is None
        assert packager.exclusion(allowed, source=True)


def test_relocated_native_reports_preserve_canonical_paths_without_reading_original(tmp_path, monkeypatch):
    root, files, _ = minimal_inputs(tmp_path, monkeypatch)
    canonical, _, _, _ = release_fixture(root)
    public = root / "release/published-copy"
    shutil.copytree(canonical, public)
    global_bytes = (public / "global-validation/report.json").read_bytes()
    for name in ("pyproject.toml", "src/quantum_autopilot/console/app.py",
                 "tools/build_windows_console.ps1", "runtime_assets/native/v1/manifest.json"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[project]\nversion="5.65.0"\n' if name == "pyproject.toml" else "public source\n",
                        encoding="utf-8")
        files.append(name)
    monkeypatch.setattr(packager, "git", lambda *_: b"original-head\n")
    monkeypatch.setattr(packager, "MAX_FILE_BYTES", 4096)
    original_open = Path.open

    def refuse_original_reads(path, *args, **kwargs):
        if path.is_relative_to(canonical):
            raise AssertionError("Only the public working copy may be opened")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse_original_reads)
    destination = tmp_path / "fresh-export"
    packager.export(root, destination, public, canonical_release_root=canonical)
    portable = "evidence/release/published-copy/global-validation/report.json"
    assert (destination / portable).read_bytes() == global_bytes
    assert (public / "global-validation/report.json").read_bytes() == global_bytes
    mapping = json.loads((destination / "release-evidence-map.json").read_text(encoding="utf-8"))
    assert mapping["original_source_release_root"] == str(canonical)
    assert mapping["working_copy_release_root"] == str(public)
    entry = next(row for row in mapping["files"] if row["path"] == portable)
    assert entry["original_path"] == str(canonical / "global-validation/report.json")
    assert entry["byte_preserved"] is True and entry["origin"] == "canonical_build"
    for relative in ("delivery-copy.json", "relocation-acceptance/acceptance-report.json",
                     "relocation-acceptance/self-test-summary.json"):
        post_copy = next(row for row in mapping["files"] if row["path"].endswith("/" + relative))
        original_relative = relative.replace("-summary.json", ".json")
        assert post_copy["original_path"] == str(public / original_relative)
        assert post_copy["origin"] == "working_copy_validation"


@pytest.mark.parametrize("case", ["wrong_root", "private_run", "wrong_reference", "relative_root"])
def test_canonical_mapping_keeps_global_reference_guards(tmp_path, monkeypatch, case):
    root, _, _ = minimal_inputs(tmp_path, monkeypatch)
    canonical, _, run, _ = release_fixture(root)
    public = root / "release/published-copy"
    shutil.copytree(canonical, public)
    report_path = public / "global-validation/report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    declared_root = canonical
    if case == "wrong_root":
        declared_root = root / "another-original"
    elif case == "private_run":
        report["run_directory"] = str(canonical / "global-validation/.security" / Path(run).name)
    elif case == "wrong_reference":
        report["scenarios"]["isl_obstruction_file"] = str(canonical / run / "state.sqlite")
    else:
        declared_root = Path("relative-original")
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError):
        packager.inventory(root, public, canonical_release_root=declared_root)


def publication_fixture(root):
    source, expected, run, _ = release_fixture(root)
    package = source / "OrionQ"
    package.mkdir()
    (package / "OrionQ.exe").write_bytes(b"FROZEN_FIXTURE")
    for name in ("OrionQ-5.64.2-native-windows-x64.zip",
                 "OrionQ-5.64.2-native-windows-x64.zip.sha256"):
        (source / name).write_bytes(b"PUBLIC_DISTRIBUTION_FIXTURE")
        expected.add(name)
    (source / "pytest-results.xml").write_bytes(b"<testsuite/>\n")
    expected.add("pytest-results.xml")
    expected.add("OrionQ/OrionQ.exe")
    return source, expected, run


def test_windows_publication_closes_public_children_without_reading_private_neighbors(tmp_path, monkeypatch):
    root, _, _ = minimal_inputs(tmp_path, monkeypatch)
    source, expected, run = publication_fixture(root)
    private_paths = [source / name for name in (
        ".security/client.pem", "pytest-private.xml", "runtime/state.sqlite",
        f"{run}/events.json", "roadmap-validation/private/report.json")]
    for path in private_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"-----BEGIN PRIVATE KEY-----\nPRIVATE_FIXTURE\n")
    original_open = Path.open

    def refuse_private_reads(path, *args, **kwargs):
        if path in private_paths:
            raise AssertionError("Private neighbor must never be opened")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse_private_reads)
    selected = publisher.public_candidates(source)
    assert {path.relative_to(source).as_posix() for path in selected} == expected
    assert not set(private_paths).intersection(selected)


@pytest.mark.parametrize("missing", [
    "roadmap-validation/runtime/diagnostic.json",
    "global-validation/run-20261010T171000123456/timeline.json",
    "satellite-mission-validation/reception/native-mission-reception.png",
])
def test_windows_publication_refuses_incomplete_public_gate_closure(tmp_path, monkeypatch, missing):
    root, _, _ = minimal_inputs(tmp_path, monkeypatch)
    source, _, _ = publication_fixture(root)
    (source / missing).unlink()
    with pytest.raises(FileNotFoundError):
        publisher.public_candidates(source)


@pytest.mark.parametrize("private", [
    "state/keys.json", "STATE/session.json", "client.pem", "store.sqlite", "store.sqlite-wal", ".ENV",
])
def test_windows_publication_rejects_private_state_inside_distribution(tmp_path, monkeypatch, private):
    root, _, _ = minimal_inputs(tmp_path, monkeypatch)
    source, _, _ = publication_fixture(root)
    path = source / "OrionQ" / private
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PRIVATE_FIXTURE")
    with pytest.raises(ValueError, match="private state or link"):
        publisher.public_candidates(source)


def test_windows_publication_retains_only_public_dependency_ca(tmp_path, monkeypatch):
    root, _, _ = minimal_inputs(tmp_path, monkeypatch)
    source, _, _ = publication_fixture(root)
    ca = source / "OrionQ/_internal/certifi/cacert.pem"
    ca.parent.mkdir(parents=True)
    ca.write_bytes(b"-----BEGIN CERTIFICATE-----\nPUBLIC_CA_FIXTURE\n")
    assert ca in publisher.public_candidates(source)
    ca.write_bytes(b"-----BEGIN PRIVATE KEY-----\nPRIVATE_FIXTURE\n")
    with pytest.raises(ValueError, match="Possible private key/service token") as failure:
        publisher.public_candidates(source)
    assert "PRIVATE_FIXTURE" not in str(failure.value)


def qualification_fixture(tmp_path):
    repository = tmp_path / "repository"
    source = tmp_path / "canonical-build"
    source.mkdir()
    tool = repository / "tools/package_git_release.py"
    tool.parent.mkdir(parents=True)
    shutil.copyfile(Path(packager.__file__), tool)
    build_tool = tool.with_name("build_windows_console.ps1")
    build_tool.write_text("public build recipe", encoding="utf-8")
    gate = source / "self-test.json"
    gate.write_text('{"passed":true}', encoding="utf-8")
    junit = source / "pytest-results.xml"
    junit.write_text("<testsuite/>", encoding="utf-8")
    public = repository / "outputs/native_release_5_64_2_20261011/windows"
    frozen = public / "frozen-source.json"
    frozen.parent.mkdir(parents=True)
    frozen.write_text('{"passed":true}', encoding="utf-8")
    supplementary = public / "supplementary-ssd"
    supplementary.mkdir()
    extra = supplementary / "terminal.xml"
    extra.write_text("<testsuite/>", encoding="utf-8")
    qualification = {
        "artifacts": [{"path": str(build_tool)}, {"path": "self-test.json"}, {"path": str(frozen)}],
        "build_tests": {"reports": [{"path": str(junit)}]},
        "supplementary_tests": {"reports": [{"path": str(extra)}]},
    }
    return repository, source, frozen, supplementary, qualification, {gate, junit}


def test_qualification_plan_preserves_explicit_public_external_reports(tmp_path):
    repository, source, frozen, supplementary, qualification, selected = qualification_fixture(tmp_path)
    planned = publisher.qualification_candidates(
        source, qualification, selected, repository=repository,
        frozen_source_report=frozen, supplementary_tests=supplementary)
    assert {path for path, _ in planned} == {
        repository / "tools/build_windows_console.ps1", source / "self-test.json",
        source / "pytest-results.xml", frozen, supplementary / "terminal.xml",
    }


@pytest.mark.parametrize("kind,relative", [
    ("artifacts", ".security/vault.json"), ("artifacts", "runtime/state.sqlite"),
    ("artifacts", "self-test.json/../../.security/vault.json"),
    ("build", "pytest-private.xml"), ("supplementary", "private.xml"),
])
def test_qualification_private_rows_are_rejected_before_any_row_content_reads(tmp_path, monkeypatch, kind, relative):
    repository, source, frozen, supplementary, qualification, selected = qualification_fixture(tmp_path)
    target = source / relative
    groups = {"artifacts": qualification["artifacts"], "build": qualification["build_tests"]["reports"],
              "supplementary": qualification["supplementary_tests"]["reports"]}
    groups[kind].append({"path": str(target)})
    original_stat = Path.stat

    def refuse_private_stat(path, *args, **kwargs):
        if path == target:
            raise AssertionError("Out-of-plan row must be rejected before stat")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", refuse_private_stat)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("Rows must be fully validated before content reads"))
    with pytest.raises(ValueError, match="outside the public plan"):
        publisher.qualification_candidates(
            source, qualification, selected, repository=repository,
            frozen_source_report=frozen, supplementary_tests=supplementary)


@pytest.mark.parametrize("missing", ["frozen", "supplementary"])
def test_qualification_cannot_authorize_its_own_external_paths(tmp_path, monkeypatch, missing):
    repository, source, frozen, supplementary, qualification, selected = qualification_fixture(tmp_path)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("Rejected external evidence must not be read"))
    with pytest.raises(ValueError, match="outside the public plan"):
        publisher.qualification_candidates(
            source, qualification, selected, repository=repository,
            frozen_source_report=None if missing == "frozen" else frozen,
            supplementary_tests=None if missing == "supplementary" else supplementary)


def test_explicit_frozen_report_cannot_name_private_state(tmp_path, monkeypatch):
    repository, source, _, supplementary, qualification, selected = qualification_fixture(tmp_path)
    private = repository / "outputs/native_release_5_64_2_20261011/.security/vault.json"
    original_stat, original_open = Path.stat, Path.open

    def public_stat(path, *args, **kwargs):
        assert path != private, "Private explicit path must not be stat'ed"
        return original_stat(path, *args, **kwargs)

    def public_open(path, *args, **kwargs):
        assert path != private, "Private explicit path must not be read"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", public_stat)
    monkeypatch.setattr(Path, "open", public_open)
    with pytest.raises(ValueError, match="outside the public plan"):
        publisher.qualification_candidates(
            source, qualification, selected, repository=repository,
            frozen_source_report=private, supplementary_tests=supplementary)


def test_release_566_public_evidence_preserves_failed_history_without_adjacent_state(tmp_path, monkeypatch):
    original_candidates = packager.artifact_candidates
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    public = {
        "concurrency/summary.json": b'{"status":"PASS","preserved_attempts":{"api-binding-before.xml":"failed before correction"}}',
        "concurrency/api-binding-before.xml": b'<testsuite tests="1" failures="1"><testcase name="binding"><failure>HTTP500 instead of HTTP409</failure></testcase></testsuite>',
        "concurrency/windows-final-regressions.xml": b'<testsuite tests="16" failures="0"/>',
        "science/science-summary.json": b'{"status":"PASS","initial_attempts_preserved":[{"junit":"regression-final.xml","outcome":"failed tolerance"}]}',
        "science/regression-final.xml": b'<testsuite tests="219" failures="3"/>',
        "science/regression-approved.xml": b'<testsuite tests="226" failures="0"/>',
        "science/after-profile-scientific-2.json": b'{"passed":true,"optimization":2}',
        "visual/component-junit-final4.xml": b'<testsuite tests="81" failures="0"/>',
        "visual/command-wait-diagnostic.json": b'{"cause":"test harness Qt wait / GIL starvation","product_changed":false}',
        "visual/command-wait/original-qt20-failed.xml": b'<testsuite tests="3" failures="3"/>',
        "visual/display-audit-final.xml": b'<testsuite tests="42" failures="0"/>',
        "visual/native-viewport-final/node-CIMATEC-top-restored.png": b"PUBLIC_CAPTURE",
        "performance/after-source-native/report.json": b'{"errors":[],"suspicious":"operator-material.json","snapshot":"snapshots.json","foreign":"outputs/unapproved-campaign/operator-material.json"}',
        "performance/after-source-native/heartbeat.csv": b"time_s,interval_ms\n1,16\n",
        "performance/kv-parent-after.json": b'{"schema":"benchmark","mean_ms":1}',
        "linux/environment-preflight.json": b'{"uid":10001}',
        "linux/native-eva-campaign.json": b'{"passed":false,"status":"FAIL","error":"initial timeout"}',
        "linux/native-eva-campaign-retry-1.json": b'{"passed":true,"status":"PASS","retry":1}',
        "linux/gui-ui-pipeline-native.json": b'{"passed":false,"status":"FAIL","isolated_diagnostic":true}',
        "linux/gui-ui-pipeline-xwayland.json": b'{"passed":true,"scope":"functional contracts only"}',
        "linux/gui-ui-pipeline-xwayland-pixel-inspection.json": b'{"status":"NOT_QUALIFIED","captures":[{"name":"ui-pipeline-xwayland-captures/black.png","nonblank_pixels":false}]}',
        "windows/attempt1-failed-summary.json": b'{"passed":false,"completed_groups":21,"approved_groups":20,"failed_groups":1}',
        "windows/attempt1/live-network-results.xml": b'<testsuite tests="570" failures="1"/>',
        "windows/attempt1/build-worker-receipt.json": b'{"passed":false,"exit_code":1}',
        "windows/attempt2-failed-summary.json": b'{"passed":false,"completed_groups":15,"approved_groups":14,"failed_groups":1}',
        "windows/attempt2/display-audit-results.xml": b'<testsuite tests="42" failures="3"/>',
        "windows/attempt2/build-worker-receipt.json": b'{"passed":false,"exit_code":1}',
        "windows/corrected-r2/delivery-worker.json": b'{"passed":true,"attempt":"corrected-r2"}',
        "windows/corrected-r3/delivery-worker.json": b'{"passed":false,"attempt":"corrected-r3"}',
        "windows/corrected-r4/delivery-worker.json": b'{"passed":true,"attempt":"corrected-r4"}',
        "windows/attempt3-aborted-summary.json": b'{"passed":false,"status":"ABORTED","reason":"build metadata changed"}',
        "windows/attempt3/build-worker-receipt.json": b'{"passed":false,"status":"ABORTED"}',
        "windows/attempt3/diagnostic-provenance-restoration.json": b'{"source_hashes_identical":true,"provenance_changed":true,"release_approved":false}',
        "windows/attempt3/runtime-manifest-tool-tests-final.xml": b'<testsuite tests="19" failures="0"/>',
        "linux/corrected-r2/linux-delivery-summary.json": b'{"status":"PASS_SELECTED_SOFTWARE_SCOPE_WITH_LIMITATIONS"}',
        "linux/corrected-r2/gui-default-display-retry-1.json": b'{"passed":true,"checks":15}',
        "linux/corrected-r2/final-ui-fixture-regression.json": b'{"passed":false,"diagnostic":"missing pre-login"}',
        "linux/corrected-r2/final-ui-fixture-regression-retry-1.json": b'{"passed":true,"checks":3}',
        "linux/corrected-r2/display-audit-fixture-final-summary.json": b'{"status":"PASS","union_passed":1627,"skipped":1}',
        "linux/corrected-r2/display-audit-fixture-final.xml": b'<testsuite tests="42" failures="0"/>',
        "linux/corrected-r2/runtime-manifest-tool-final.xml": b'<testsuite tests="19" failures="0"/>',
        "linux/corrected-r2/runtime-manifest-tool-final-summary.json": b'{"status":"PASS","union_passed":1646,"skipped":1}',
        "linux/corrected-r2/reader-static-contracts-final.xml": b'<testsuite tests="116" failures="0"/>',
        "linux/corrected-r2/reader-static-contracts-final-summary.json": b'{"passed":true,"checks":116}',
        "windows/corrected-r4/reader-static-contracts-final.xml": b'<testsuite tests="116" failures="0"/>',
        "windows/corrected-r4/reader-static-contracts-final-summary.json": b'{"passed":true,"checks":116}',
        "windows/corrected-r4/dashboard-validation.json": b'{"passed":false,"scope":"offline cache admission only"}',
        "windows/corrected-r4/dashboard-validation-offline-diagnostic.json": b'{"passed":false,"scope":"offline cache admission only"}',
        "windows/corrected-r4/dashboard-validation-final.json": b'{"passed":true,"scope":"isolated web lint/build only"}',
        "windows/corrected-r4/consolidation-rejected-summary.json": b'{"official_build_passed":true,"reader_rejection_preserved":true,"publication_performed":false}',
        "windows/corrected-r4/native-consolidation.json": b'{"passed":false,"reason":"reader scope mismatch"}',
        "windows/corrected-r4/scientific-input-validation-original.json": b'{"passed":true,"checks_count":113,"optimization":0}',
        "windows/corrected-r4/official-build-worker-receipt.json": b'{"passed":true,"exit_code":0}',
        "windows/corrected-r4-final2/delivery-worker.json": b'{"passed":true,"attempt":"reader corrected final2"}',
        "windows/corrected-r4-final2/frozen-source-verification.json": b'{"passed":true,"modules_verified":62}',
        "windows/corrected-r4-final2/native-consolidation.json": b'{"passed":true,"historical_reader_rejection_retained":true}',
    }
    base = root / "outputs/release-5.66.0-20261011"
    for relative, raw in public.items():
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    private = [base / name for name in (
        "concurrency/operator-material.json", "concurrency/state/controller.sqlite",
        "science/regression-approved.log", "performance/after-source-native/operator-material.json",
        "performance/after-source-native/snapshots.json", "performance/after-source-native/resources.jsonl",
        "linux/ui-pipeline-xwayland-captures/black.png",
    )]
    private.append(root / "outputs/unapproved-campaign/operator-material.json")
    for path in private:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"-----BEGIN PRIVATE KEY-----\nDO_NOT_READ_PRIVATE_NEIGHBOR\n")
    historical_document = root / "docs/results/orionq_hybrid_selected_fixture.md"
    historical_document.parent.mkdir(parents=True, exist_ok=True)
    historical_document.write_text(
        "[Unqualified capture](../../outputs/release-5.66.0-20261011/linux/ui-pipeline-xwayland-captures/black.png)\n",
        encoding="utf-8")
    original_open = Path.open

    def public_only(path, *args, **kwargs):
        assert path not in private, "Adjacent private state must not be inspected"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", public_only)
    monkeypatch.setattr(packager, "EVIDENCE_TREES", ())
    candidates = original_candidates(root)
    assert candidates == {base / name for name in public}
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: candidates | {root / report})
    included, _ = packager.inventory(root)
    for relative, raw in public.items():
        key = (base / relative).relative_to(root).as_posix()
        assert included[key][0].read_bytes() == raw
        assert included[key][1] == "qualification_evidence"
    assert not set(private).intersection(path for path, _ in included.values())


def test_release_566_scientific_diagnostic_and_new_junits_are_preserved_nominally(tmp_path, monkeypatch):
    root, _, _ = minimal_inputs(tmp_path, monkeypatch)
    release = root / "release/build-566"
    release.mkdir(parents=True)
    reports = {
        "scientific-input-validation.json": b'{"passed":false,"failed":["boundary"],"hardware_validated":false}',
        "pytest-controller-concurrency-results.xml": b'<testsuite tests="16" failures="0"/>',
        "pytest-scientific-input-results.xml": b'<testsuite tests="112" failures="0"/>',
        "pytest-tick-layout-results.xml": b'<testsuite tests="2" failures="0"/>',
        "pytest-visual-readability-results.xml": b'<testsuite tests="14" failures="0"/>',
    }
    for name, raw in reports.items():
        (release / name).write_bytes(raw)
    included, _ = packager.inventory(root, release)
    for name, raw in reports.items():
        path, kind = included[f"evidence/release/build-566/{name}"]
        assert path.read_bytes() == raw and kind == "release_evidence"
    assert json.loads(included["evidence/release/build-566/scientific-input-validation.json"][0].read_bytes())["passed"] is False


def test_release_566_notes_do_not_claim_historical_pair_race_remains_open(tmp_path, monkeypatch):
    root, files, _ = minimal_inputs(tmp_path, monkeypatch)
    current_report = "docs/results/orionq_release_5_66_0_20261011.md"
    for name in ("pyproject.toml", "src/quantum_autopilot/console/app.py",
                 "tools/build_windows_console.ps1", "runtime_assets/native/v1/manifest.json", current_report):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[project]\nversion="5.66.0"\n' if name == "pyproject.toml" else "public source\n", encoding="utf-8")
        files.append(name)
    monkeypatch.setattr(packager, "git", lambda *_: b"source-head\n")
    destination = tmp_path / "export-566"
    manifest = packager.export(root, destination)
    notes = (destination / "SOURCE_RELEASE.md").read_text(encoding="utf-8")
    historical = (destination / "GIT-RELEASE.md").read_text(encoding="utf-8")
    assert manifest["source_version"] == "5.66.0"
    assert manifest["current_release_report"] == current_report
    assert "clone OrionQ-5.66.0-github.bundle OrionQ-5.66.0" in notes
    assert "A entrega não encerra a corrida legada" not in notes
    assert "no caminho legado permanece aberto" not in historical
    assert "Na campanha original" in historical
    assert "envelope v1 não atesta" in notes
    assert "não qualifica hardware" in notes
    assert "não afirmam eliminação de todas as pausas" in notes
    assert "diagnósticos mistos e limites de G05" in notes
    assert "Cada tentativa deve ser lida com seu freeze" in notes
    assert "`NOT_QUALIFIED` não certificam pintura" in notes
    assert any("serial/endpoint" in limit for limit in manifest["limitations"])


@pytest.mark.parametrize("version", ["5.66.0", "5.66.1"])
@pytest.mark.parametrize("run", ["before-native", "after-native", "after-soak"])
def test_nominal_performance_resource_measurements_preserve_bytes(tmp_path, monkeypatch, run, version):
    original_candidates = packager.artifact_candidates
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    relative = f"outputs/release-{version}-20261011/performance/{run}/resources.jsonl"
    path = root / relative
    path.parent.mkdir(parents=True)
    record = {"elapsed_s": 1.2, "wall_unix": 100, "cpu_percent": 200.0, "rss_bytes": 65536,
              "threads": 3, "host_cpu_percent": 55.0, "host_available_bytes": 999999,
              "processes": [{"pid": 123, "name": "OrionQ.exe", "cpu_percent": 200.0, "rss_bytes": 65536}],
              "user_owned_orion_processes": []}
    raw = (json.dumps(record) + "\r\n").encode()
    path.write_bytes(raw)
    private = path.with_name("gpu-context.jsonl")
    private.write_bytes(b"PRIVATE_MEASUREMENT_NEIGHBOR")
    monkeypatch.setattr(packager, "EVIDENCE_TREES", ())
    candidates = original_candidates(root)
    assert candidates == {path} and private not in candidates
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: candidates | {root / report})
    included, _ = packager.inventory(root)
    assert included[relative][0].read_bytes() == raw
    assert included[relative][1] == "qualification_evidence"


@pytest.mark.parametrize("version", ["5.66.0", "5.66.1"])
@pytest.mark.parametrize("change", ["unknown_record", "credential_process", "nonfinite"])
def test_public_performance_resource_schema_rejects_private_or_invalid_state(tmp_path, monkeypatch, change, version):
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    relative = f"outputs/release-{version}-20261011/performance/after-soak/resources.jsonl"
    path = root / relative
    path.parent.mkdir(parents=True)
    record = {"elapsed_s": 1.2, "wall_unix": 100, "cpu_percent": 100, "rss_bytes": 65536,
              "threads": 3, "host_cpu_percent": 55, "host_available_bytes": 999999,
              "processes": [], "user_owned_orion_processes": []}
    if change == "unknown_record":
        record["credential"] = "DO_NOT_EXPOSE_PRIVATE_FIXTURE"
    elif change == "credential_process":
        record["processes"] = [{"pid": 1, "name": "OrionQ", "cpu_percent": 0, "rss_bytes": 1,
                                "credential": "DO_NOT_EXPOSE_PRIVATE_FIXTURE"}]
    else:
        record["rss_bytes"] = float("nan")
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    monkeypatch.setattr(packager, "artifact_candidates", lambda _: {root / report, path})
    with pytest.raises(ValueError, match="Unexpected public resource measurement") as failure:
        packager.inventory(root)
    assert "DO_NOT_EXPOSE_PRIVATE_FIXTURE" not in str(failure.value)


def test_release_5661_preserves_scoped_proofs_and_failures_without_following_private_neighbors(tmp_path, monkeypatch):
    original_candidates = packager.artifact_candidates
    root, _, report = minimal_inputs(tmp_path, monkeypatch)
    base = root/'outputs/release-5.66.1-20261011'
    public = {
        'watchdog/summary.json': b'{"scope":"source Qt, not frozen EXE","private":"operator-material.json","foreign":"outputs/unapproved-campaign/operator-material.json"}',
        'watchdog/before-source.json': b'{"label":"sealed5.66.0","count":36}',
        'watchdog/after-source.json': b'{"label":"source5.66.1","count":0}',
        'watchdog/focused-windows-attempt2.xml': b'<testsuite tests="100" failures="61" errors="15"/>',
        'watchdog/focused-windows-final.xml': b'<testsuite tests="112" failures="0"/>',
        'watchdog/reproducer-contracts.xml': b'<testsuite tests="27" failures="0"/>',
        'watchdog/reproducer-smoke/report.json': b'{"scope":"functional0.2s,not performance","private":"private-state/journal.sqlite"}',
        'visual/controls-prototype-first.xml': b'<testsuite tests="4" failures="1"/>',
        'visual/controls-signals-summary.json': b'{"selected":true,"capture":"unqualified.png"}',
        'windows/reader-contracts.xml': b'<testsuite tests="116" errors="56"/>',
        'windows/reader-contracts-native.xml': b'<testsuite tests="116" failures="0"/>',
        'windows/build-1/test-inputs-before.json': b'{"scope":"full source/test/tools freeze"}',
        'windows/finalization/delivery-worker.json': b'{"passed":false,"reason":"diagnostic"}',
        'windows/attempt1-aborted-before-build.json': b'{"status":"ABORTED_SECURITY_REVIEW","exe_created":false}',
        'windows/source-freeze-corrected-r2.json': b'{"source_count":680}',
        'windows/build-2/test-inputs-after.json': b'{"status":"PASS"}',
        'windows/finalization-corrected-r2/delivery-worker.json': b'{"passed":true,"scope":"software qualification"}',
        'watchdog/corrected-r2/policy-first-failure.xml': b'<testsuite tests="1" failures="1"/>',
        'watchdog/corrected-r2/policy-wait-sleep20.xml': b'<testsuite tests="1" failures="0"/>',
        'watchdog/corrected-r2/binding-authority-collection-error.xml': b'<testsuite tests="1" errors="1"/>',
        'watchdog/corrected-r2/authority-summary.json': b'{"scope":"loaded actions, real authorization","private":"operator-material.json"}',
        'watchdog/corrected-r2/auth-reproduction-before.json': b'{"historical":true,"status":"CONFIRMED_BYPASS"}',
        'git/package-contracts-windows-corrected-r2.xml': b'<testsuite tests="382" failures="0"/>',
        'git/package-contracts-summary-corrected-r2.json': b'{"scope":"package contracts corrected R2"}',
        'linux/visual-regression-final-summary.json': b'{"scope":"widgets, not native painting"}',
        'linux/policy-tests.xml': b'<testsuite tests="33" failures="0"/>',
        'linux/tools-tests-summary.json': b'{"scope":"reproducer contracts"}',
        'linux/source-tests-latest-summary.json': b'{"scope":"unique latest cases with historical attempts retained"}',
        'linux/gui-ui-pipeline-xwayland-pixel-inspection.json': b'{"status":"NOT_QUALIFIED","capture":"unqualified.png"}',
        'linux/qualification-wrapper.json': b'{"scope":"bounded artifact receipts"}',
        'linux/policy-campaign-interruption.json': b'{"status":"ABORTED_SECURITY_REVIEW"}',
        'linux/corrected-r2/policy-tests.xml': b'<testsuite tests="116" failures="0"/>',
        'linux/corrected-r2/linux-delivery-summary.json': b'{"scope":"fresh corrected R2, not R1 approval"}',
        'performance/comparison.json': b'{"scope":"native binary comparison","private":"unapproved.json"}',
        'performance/paint-science-review.json': b'{"scope":"read only metrics/context review","private":"private-review-state.json"}',
        'performance/after-soak/heartbeat.csv': b'time_s,interval_ms\n1,16\n',
    }
    for relative, raw in public.items():
        path = base/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    private = [base/name for name in (
        'watchdog/operator-material.json', 'watchdog/reproducer-smoke/private-state/journal.sqlite',
        'visual/unqualified.png', 'performance/unapproved.json', 'windows/worker.log',
        'watchdog/corrected-r2/operator-material.json', 'linux/corrected-r2/journal.sqlite',
        'windows/build-2/worker.log', 'performance/private-review-state.json')]
    private.append(root/'outputs/unapproved-campaign/operator-material.json')
    for path in private:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'-----BEGIN PRIVATE KEY-----\nDO_NOT_INSPECT_PRIVATE_NEIGHBOR\n')
    document = root/'docs/results/orionq_hybrid_new_fixture.md'
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text('[Unqualified](../../outputs/release-5.66.1-20261011/visual/unqualified.png)\n', encoding='utf-8')
    original_open = Path.open
    def public_only(path, *args, **kwargs):
        assert path not in private, 'Private adjacent state must not be inspected'
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', public_only)
    monkeypatch.setattr(packager, 'EVIDENCE_TREES', ())
    candidates = original_candidates(root)
    assert candidates == {base/name for name in public}
    monkeypatch.setattr(packager, 'artifact_candidates', lambda _: candidates | {root/report})
    included, _ = packager.inventory(root)
    for relative, raw in public.items():
        assert included[(base/relative).relative_to(root).as_posix()][0].read_bytes() == raw
    assert not set(private).intersection(path for path, _ in included.values())


def test_release_5661_notes_keep_version_and_scope_without_promoting_source_benchmarks(tmp_path, monkeypatch):
    root, files, _ = minimal_inputs(tmp_path, monkeypatch)
    current_report = 'docs/results/orionq_release_5_66_1_20261011.md'
    for name in ('pyproject.toml', 'src/quantum_autopilot/console/app.py',
                 'tools/build_windows_console.ps1', 'tools/benchmark_policy_panels.py',
                 'runtime_assets/native/v1/manifest.json', current_report):
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[project]\nversion="5.66.1"\n' if name == 'pyproject.toml' else 'public source\n', encoding='utf-8')
        files.append(name)
    monkeypatch.setattr(packager, 'git', lambda *_: b'source-head\n')
    destination = tmp_path/'export-5661'
    manifest = packager.export(root, destination)
    notes = (destination/'SOURCE_RELEASE.md').read_text(encoding='utf-8')
    assert manifest['source_version'] == '5.66.1'
    assert manifest['current_release_report'] == current_report
    assert 'clone OrionQ-5.66.1-github.bundle OrionQ-5.66.1' in notes
    assert 'não afirmam eliminação de todas as pausas' in notes
    assert 'G05' in notes and 'não qualifica hardware' in notes
    assert (destination/'tools/benchmark_policy_panels.py').is_file()
    historical = (destination/'GIT-RELEASE.md').read_text(encoding='utf-8')
    assert 'Não são medições do EXE' in historical
    assert '--private-state-root' in historical


RELEASE_5661_R3_REPORTS = (
    'corrected-r3/preparation-failure.json',
    *(f'{attempt}/{name}'
      for attempt in ('corrected-r3-candidate', 'corrected-r3-candidate-rerun')
      for name in ('fixture-override.json', 'policy-and-explicit-exports-tests.xml',
                   'policy-and-explicit-exports-tests-summary.json')),
    'corrected-r3-candidate-rerun/snapshot-production-after.json',
    'corrected-r2/continuation-execution.json',
    'corrected-r2/gui-x11-external-readback.json',
    'corrected-r2/drvfs-chmod-failure.json',
    *(f'r2-native-retry/{name}' for name in (
        'headless-build-wrapper.json', 'gui-build-wrapper.json', 'native-retry-execution.json',
        'gui-build-space-estimate.json',
        'linux-release-qualification.json', 'qualification-wrapper.json', 'scientific-static-linked.json',
        'archive-gui-smoke.json', 'archive-headless-smoke.json', 'gui-frozen-source-linux.json',
        'native-eva-campaign.json', 'native-eva-provenance.json', 'gui-default-display.json',
        'gui-wayland-startup.json', 'gui-remote-smoke.json', 'x11-private-dependencies.json',
        'gui-xwayland-startup.json', 'gui-ui-pipeline-xwayland.json',
        'gui-ui-pipeline-xwayland-pixel-inspection.json', 'gui-x11-external-readback.json',
        'linux-delivery-summary.json',
    )),
)


@pytest.mark.parametrize('relative', RELEASE_5661_R3_REPORTS)
def test_release_5661_r3_nominal_metadata_preserves_failures_without_inspecting_neighbors(
    tmp_path, monkeypatch, relative,
):
    original_candidates = packager.artifact_candidates
    root, _, required = minimal_inputs(tmp_path, monkeypatch)
    base = root/'outputs/release-5.66.1-20261011/linux'
    public = base/relative
    public.parent.mkdir(parents=True)
    if public.suffix == '.xml':
        raw = b'<testsuite tests="348" failures="10" errors="300"/>'
    else:
        raw = json.dumps({
            'status': 'MEASURED_ESTIMATE_ONLY' if relative.endswith('gui-build-space-estimate.json') else 'FAIL_ENVIRONMENT_DIAGNOSTIC', 'accepted': False,
            'private_state_exported': False,
            'neighbors': ['journal.sqlite', 'credentials.json', 'worker.log',
                          'unapproved.jsonl', 'x11-private-state/window-state.json',
                          '.Xauthority', 'unapproved-capture.png'],
        }, sort_keys=True).encode('utf-8')
    public.write_bytes(raw)
    private = [public.parent/name for name in (
        'journal.sqlite', 'credentials.json', 'worker.log', 'unapproved.jsonl',
        'x11-private-state/window-state.json', '.Xauthority', 'unapproved-capture.png',
    )]
    for path in private:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'-----BEGIN PRIVATE KEY-----\nDO_NOT_INSPECT_R3_NEIGHBOR\n')
    original_open = Path.open
    def public_only(path, *args, **kwargs):
        assert path not in private, 'Private adjacent R3 state must not be inspected'
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', public_only)
    monkeypatch.setattr(packager, 'EVIDENCE_TREES', ())
    candidates = original_candidates(root)
    assert candidates == {public}
    monkeypatch.setattr(packager, 'artifact_candidates', lambda _: candidates | {root/required})
    included, _ = packager.inventory(root)
    nominal = public.relative_to(root).as_posix()
    assert included[nominal][0].read_bytes() == raw
    assert included[nominal][1] == 'qualification_evidence'
    assert not set(private).intersection(path for path, _ in included.values())
    if relative.endswith('gui-build-space-estimate.json'):
        # An estimate is copied verbatim, without upgrading it to runtime PASS.
        assert json.loads(included[nominal][0].read_bytes())['status'] == 'MEASURED_ESTIMATE_ONLY'
        assert packager.MAX_EVIDENCE_BYTES == 8 * 1024 * 1024
        with public.open('wb') as stream:
            stream.seek(packager.MAX_EVIDENCE_BYTES)
            stream.write(b'x')
        original_check = packager.secret_check
        def forbid_estimate_read(path):
            assert path != public, 'Oversized disk estimate must be rejected before inspection'
            return original_check(path)
        monkeypatch.setattr(packager, 'secret_check', forbid_estimate_read)
        included, excluded = packager.inventory(root)
        assert nominal not in included
        assert any(row['path'] == nominal and 'limit' in row['reason'] for row in excluded)


def test_release_5661_r3_nominal_reports_keep_curated_size_bound_before_inspection(tmp_path, monkeypatch):
    root, _, required = minimal_inputs(tmp_path, monkeypatch)
    relative = ('outputs/release-5.66.1-20261011/linux/corrected-r3-candidate-rerun/'
                'policy-and-explicit-exports-tests-summary.json')
    public = root/relative
    public.parent.mkdir(parents=True)
    assert packager.MAX_EVIDENCE_BYTES == 8 * 1024 * 1024
    with public.open('wb') as stream:
        stream.seek(packager.MAX_EVIDENCE_BYTES)
        stream.write(b'x')
    monkeypatch.setattr(packager, 'artifact_candidates', lambda _: {root/required, public})
    original_check = packager.secret_check
    def forbid_oversized_read(path):
        assert path != public, 'Oversized R3 report must be excluded before inspection'
        return original_check(path)
    monkeypatch.setattr(packager, 'secret_check', forbid_oversized_read)
    included, excluded = packager.inventory(root)
    assert relative not in included
    assert any(row['path'] == relative and 'limit' in row['reason'] for row in excluded)


@pytest.mark.parametrize('profile', ['headless', 'gui'])
@pytest.mark.parametrize('attempt', ['r2-native-retry', 'r3-native-final'])
def test_release_5661_ext4_retry_artifact_metadata_uses_exact_prefix_without_private_neighbors(
    tmp_path, monkeypatch, profile, attempt,
):
    original_candidates = packager.artifact_candidates
    root, _, required = minimal_inputs(tmp_path, monkeypatch)
    base = root/f'release/linux/5.66.1-native-{profile}-20261011-{attempt}'
    metadata = base/'build-report.json'
    metadata.parent.mkdir(parents=True)
    raw = b'{"scope":"native ext4 retry metadata","status":"not qualification by selection"}'
    metadata.write_bytes(raw)
    retry = root/f'outputs/release-5.66.1-20261011/linux/{attempt}'
    release_bookkeeping = [retry/name for name in (
        'gui-new-fixture-cleanup.json', 'headless-new-passed-build-bundle-relocation.json',
        'gui-new-passed-build-bundle-relocation.json', 'source-tests-latest-summary.json',
    )]
    for path in release_bookkeeping:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"scope":"new passed workspace release receipt only"}')
    private = [base/name for name in ('journal.sqlite', 'credentials.json', 'worker.log',
                                     'x11-private-state/window-state.json', 'OrionQ.tar.gz')]
    private.append(root/f'release/linux/5.66.1-native-{profile}-20261011-{attempt}-unapproved/build-report.json')
    private.extend(retry/name for name in ('x11-private-state/credentials.json',
                                          'private-new-bundle-relocation.json', 'cleanup.log'))
    for path in private:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'-----BEGIN PRIVATE KEY-----\nDO_NOT_INSPECT_RETRY_NEIGHBOR\n')
    original_open = Path.open
    def metadata_only(path, *args, **kwargs):
        assert path not in private, 'Private retry state or neighboring artifact prefix must not be inspected'
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', metadata_only)
    monkeypatch.setattr(packager, 'EVIDENCE_TREES', ())
    candidates = original_candidates(root)
    assert candidates == {metadata, *release_bookkeeping}
    monkeypatch.setattr(packager, 'artifact_candidates', lambda _: candidates | {root/required})
    included, _ = packager.inventory(root)
    assert included[metadata.relative_to(root).as_posix()][0].read_bytes() == raw
    for path in release_bookkeeping:
        assert included[path.relative_to(root).as_posix()][0].read_bytes() == path.read_bytes()
    assert not set(private).intersection(path for path, _ in included.values())


@pytest.mark.parametrize('name', [
    'package-contracts-windows-final-r3.xml', 'package-contracts-linux-final-r3.xml',
    'package-contracts-summary-final-r3.json',
])
def test_release_5661_final_r3_package_contract_metadata_is_nominal_and_bounded(
    tmp_path, monkeypatch, name,
):
    original_candidates = packager.artifact_candidates
    root, _, required = minimal_inputs(tmp_path, monkeypatch)
    base = root/'outputs/release-5.66.1-20261011/git'
    public = base/name
    public.parent.mkdir(parents=True)
    raw = (b'<testsuite tests="419" failures="0"/>' if public.suffix == '.xml' else
           b'{"scope":"package contracts, not scientific or hardware qualification","private":"private-state/credentials.json"}')
    public.write_bytes(raw)
    private = [base/relative for relative in ('private-state/credentials.json', 'journal.db',
                                             'worker.log', 'unapproved-package-contracts.xml')]
    for path in private:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'-----BEGIN PRIVATE KEY-----\nDO_NOT_INSPECT_PACKAGE_NEIGHBOR\n')
    original_open = Path.open
    def public_only(path, *args, **kwargs):
        assert path not in private, 'Private package neighbors must not be inspected'
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', public_only)
    monkeypatch.setattr(packager, 'EVIDENCE_TREES', ())
    candidates = original_candidates(root)
    assert candidates == {public}
    monkeypatch.setattr(packager, 'artifact_candidates', lambda _: candidates | {root/required})
    included, _ = packager.inventory(root)
    relative = public.relative_to(root).as_posix()
    assert included[relative][0].read_bytes() == raw
    assert not set(private).intersection(path for path, _ in included.values())
    assert packager.MAX_EVIDENCE_BYTES == 8 * 1024 * 1024
    with public.open('wb') as stream:
        stream.seek(packager.MAX_EVIDENCE_BYTES)
        stream.write(b'x')
    original_check = packager.secret_check
    def forbid_oversized_read(path):
        assert path != public, 'Oversized final R3 package report must not be read'
        return original_check(path)
    monkeypatch.setattr(packager, 'secret_check', forbid_oversized_read)
    included, excluded = packager.inventory(root)
    assert relative not in included
    assert any(row['path'] == relative and 'limit' in row['reason'] for row in excluded)


@pytest.mark.parametrize('relative', [
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/archive-gui-smoke.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/archive-headless-smoke.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/audit-tests-summary.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/audit-tests.xml',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/campaign-execution.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/environment-preflight.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/gui-build-wrapper.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/gui-default-display.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/gui-frozen-source-linux.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/gui-new-fixture-cleanup.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/gui-remote-smoke.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/gui-ui-pipeline-xwayland-pixel-inspection.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/gui-ui-pipeline-xwayland.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/gui-wayland-startup.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/gui-xwayland-startup.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/headless-build-wrapper.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/linux-delivery-summary.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/linux-release-qualification.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/native-eva-campaign.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/native-eva-provenance.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/package-tests-summary.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/package-tests.xml',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/policy-campaign-interruption.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/policy-tests-summary.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/policy-tests.xml',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/qualification-wrapper.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/scientific-static-linked.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/source-tests-latest-summary.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/source-tests-summary.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/source-tests.xml',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/tools-tests-summary.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/tools-tests.xml',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/visual-regression-final-summary.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/visual-regression-final.xml',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/wheel-preparation.json',
    'outputs/release-5.66.1-20261011/linux/corrected-r3-final/x11-private-dependencies.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/archive-gui-smoke.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/archive-headless-smoke.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/gui-build-space-estimate.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/gui-build-wrapper.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/gui-default-display.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/gui-frozen-source-linux.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/gui-new-fixture-cleanup.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/gui-new-passed-build-bundle-relocation.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/gui-remote-smoke.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/gui-ui-pipeline-xwayland-pixel-inspection.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/gui-ui-pipeline-xwayland.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/gui-wayland-startup.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/gui-x11-external-readback.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/gui-xwayland-startup.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/headless-build-wrapper.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/headless-new-passed-build-bundle-relocation.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/linux-delivery-summary.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/linux-release-qualification.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/native-eva-campaign.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/native-eva-provenance.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/native-retry-execution.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/qualification-wrapper.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/scientific-static-linked.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/source-tests-latest-summary.json',
    'outputs/release-5.66.1-20261011/linux/r3-native-final/x11-private-dependencies.json',
    'outputs/release-5.66.1-20261011/watchdog/corrected-r3/export-contracts.xml',
    'outputs/release-5.66.1-20261011/watchdog/corrected-r3/text-scrolling.xml',
    'outputs/release-5.66.1-20261011/windows/attempt2-frozen-selftest-failure.json',
    'outputs/release-5.66.1-20261011/windows/build-2/test-inputs-after-failed-selftest.json',
    'outputs/release-5.66.1-20261011/windows/build-3/test-inputs-after.json',
    'outputs/release-5.66.1-20261011/windows/build-3/test-inputs-before.json',
    'outputs/release-5.66.1-20261011/windows/display-audit-prerequisite-corrected-r3.xml',
    'outputs/release-5.66.1-20261011/windows/finalization-corrected-r3/delivery-worker.json',
    'outputs/release-5.66.1-20261011/windows/production-delta-corrected-r3.json',
    'outputs/release-5.66.1-20261011/windows/reader-contracts-corrected-r3.xml',
    'outputs/release-5.66.1-20261011/windows/source-freeze-corrected-r3.json',
])
def test_release_5661_third_attempt_metadata_is_exact_and_private_neighbors_unread(tmp_path, monkeypatch, relative):
    original_candidates = packager.artifact_candidates
    root, _, required = minimal_inputs(tmp_path, monkeypatch)
    public = root/relative
    public.parent.mkdir(parents=True)
    raw = b'{"scope":"preserved receipt, never approval inferred from selection","status":"NOT_QUALIFIED"}'
    public.write_bytes(raw)
    private = [public.parent/name for name in ('journal.sqlite','credentials.json','worker.log',
        'private-state/secret.json','x11-private-state/window.json','unapproved.jsonl','unknown.json')]
    for path in private:
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'-----BEGIN PRIVATE KEY-----\nDO_NOT_READ_NEIGHBORS\n')
    original_open = Path.open
    def public_only(path,*args,**kwargs):
        assert path not in private, 'Third-attempt neighboring private state must not be inspected'
        return original_open(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',public_only)
    monkeypatch.setattr(packager,'EVIDENCE_TREES',())
    assert original_candidates(root)=={public}
    monkeypatch.setattr(packager,'artifact_candidates',lambda _:{root/required,public})
    included,_=packager.inventory(root)
    assert included[relative][0].read_bytes()==raw
    assert included[relative][1]=='qualification_evidence'
    assert not set(private).intersection(path for path,_ in included.values())
