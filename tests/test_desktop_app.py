"""Tests for the Windows/local desktop product boundary."""

from pathlib import Path

import pytest

from quantum_autopilot.desktop import (
    main,
    resolve_desktop_paths,
    resolve_native_paths,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.unit
def test_desktop_paths_separate_bundle_resources_from_mutable_state(tmp_path):
    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    (dashboard / "index.html").write_text("<!doctype html>", encoding="utf-8")
    state = tmp_path / "state"

    paths = resolve_desktop_paths(
        repository_root=REPOSITORY_ROOT,
        dashboard_root=dashboard,
        state_root=state,
    )

    assert paths.repository_root == REPOSITORY_ROOT
    assert paths.dashboard_root == dashboard
    assert paths.state_root == state
    assert paths.control_journal == state / "sandbox_journal.sqlite"
    assert paths.wdm_model_bundle is not None
    assert paths.wdm_model_bundle.suffix == ".npz"


@pytest.mark.unit
def test_desktop_paths_require_built_ui_and_explicit_model(tmp_path):
    with pytest.raises(ValueError, match="index.html"):
        resolve_desktop_paths(
            repository_root=REPOSITORY_ROOT,
            dashboard_root=tmp_path / "missing-dashboard",
            state_root=tmp_path / "state-a",
        )

    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    (dashboard / "index.html").write_text("<!doctype html>", encoding="utf-8")
    with pytest.raises(ValueError, match="wdm_model_bundle"):
        resolve_desktop_paths(
            repository_root=REPOSITORY_ROOT,
            dashboard_root=dashboard,
            state_root=tmp_path / "state-b",
            wdm_model_bundle=tmp_path / "missing.npz",
        )


@pytest.mark.unit
def test_desktop_main_fails_closed_before_binding_on_missing_ui(tmp_path):
    status = main(
        [
            "--ui",
            "web",
            "--no-browser",
            "--repository-root",
            str(REPOSITORY_ROOT),
            "--dashboard-root",
            str(tmp_path / "missing"),
            "--state-root",
            str(tmp_path / "state"),
        ]
    )

    assert status == 2


@pytest.mark.unit
def test_native_paths_do_not_require_a_built_web_dashboard(tmp_path):
    state = tmp_path / "state"

    paths = resolve_native_paths(repository_root=REPOSITORY_ROOT, state_root=state)

    assert paths.repository_root == REPOSITORY_ROOT
    assert paths.control_journal == state / "sandbox_journal.sqlite"
    assert state.is_dir()


@pytest.mark.unit
def test_native_paths_reject_a_missing_explicit_model(tmp_path):
    with pytest.raises(ValueError, match="wdm_model_bundle"):
        resolve_native_paths(
            repository_root=REPOSITORY_ROOT,
            state_root=tmp_path / "state",
            wdm_model_bundle=tmp_path / "missing.npz",
        )


def test_self_test_uses_unique_diagnostic_state_by_default(tmp_path, monkeypatch):
    from quantum_autopilot.console import validation

    states = []

    def capture(paths, report):
        states.append(paths.state_root)
        assert report == tmp_path / "report.json"
        return 0

    monkeypatch.setattr(validation, "run_self_test", capture)
    for _ in range(2):
        assert main(["--self-test", str(tmp_path / "report.json")]) == 0
    assert states[0] != states[1]
    assert all(path.parent == tmp_path and path.name.startswith("self-test-state-") for path in states)
