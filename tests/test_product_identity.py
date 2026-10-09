"""Release version and compatibility boundaries during the OrionQ rename."""
from importlib.metadata import PackageNotFoundError

import pytest

from quantum_autopilot import desktop, desktop_instance, product
from quantum_autopilot.console.branding import WINDOWS_APP_ID


@pytest.fixture(autouse=True)
def clear_version_cache():
    product.product_version.cache_clear()
    yield
    product.product_version.cache_clear()


@pytest.mark.parametrize("version_line", ['version = "9.8.7"', "version = '9.8.7' # next release"])
def test_source_version_follows_pyproject_without_stale_editable_metadata(monkeypatch, tmp_path, version_line):
    fake_module = tmp_path / "src/quantum_autopilot/product.py"
    fake_module.parent.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "quantum-twin"\n' + version_line + '\n', encoding="utf-8")
    monkeypatch.setattr(product, "__file__", str(fake_module))
    monkeypatch.setattr(product.sys, "frozen", False, raising=False)
    monkeypatch.setattr(product, "version", lambda _: "old-editable-version")
    assert product.product_version() == "9.8.7"


def test_frozen_version_uses_bundled_metadata_instead_of_checkout(monkeypatch):
    monkeypatch.setattr(product.sys, "frozen", True, raising=False)
    monkeypatch.setattr(product, "version", lambda name: "7.6.5" if name == "quantum-twin" else None)
    assert product.product_version() == "7.6.5"


def test_frozen_release_missing_metadata_is_not_silently_mislabelled(monkeypatch):
    monkeypatch.setattr(product.sys, "frozen", True, raising=False)
    def missing(_):
        raise PackageNotFoundError("quantum-twin")
    monkeypatch.setattr(product, "version", missing)
    with pytest.raises(RuntimeError, match="release version metadata is missing"):
        product.product_version()


def test_rename_preserves_data_and_machine_wide_ownership(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert desktop.default_state_root() == (tmp_path / "ORION-Q").resolve()
    assert product.PRODUCT_NAME == "OrionQ"
    assert product.EXECUTABLE_NAME == "OrionQ.exe"
    assert WINDOWS_APP_ID == "ORION-Q.Desktop"
    assert desktop_instance._PRODUCT_NAMESPACE == "ORION-Q.Desktop.7BE09401-981D-49EC-B157-6BC04F6AC80A"
    assert desktop_instance._COMPATIBLE_IMAGE_NAMES == {"orionq.exe", "orion-q.exe"}
    assert desktop_instance._COMPATIBLE_WINDOW_TITLES == {
        "OrionQ · Quantum Network Console", "ORION-Q · Quantum Network Console"}


def test_cli_reports_current_release_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        desktop.build_parser().parse_args(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"OrionQ {product.product_version()}"
