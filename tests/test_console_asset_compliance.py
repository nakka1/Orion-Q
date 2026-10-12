"""Distribution assets and readable mandatory cartographic attribution."""
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetricsF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console import theme
from quantum_autopilot.console.geodata import OSM_ATTRIBUTION, load_world
from quantum_autopilot.console.widgets import geo_map
from quantum_autopilot.console.widgets.geo_map import MapRenderer
from tools.verify_frozen_source import RESOURCES

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    theme.install(application)
    return application


def test_distributable_font_manifest_has_no_unused_quantum_font():
    fonts = ROOT / "src/quantum_autopilot/console/fonts"
    manifest = json.loads((fonts / "manifest.json").read_text(encoding="utf-8"))
    assert not (fonts / "Quantum.otf").exists()
    assert not (fonts / "quantum-NOTICE.txt").exists()
    assert all(item["file"] != "Quantum.otf" for item in manifest["fonts"])
    assert "Quantum" not in theme.BUNDLED_FONT_FILES
    assert "console/fonts/Quantum.otf" not in RESOURCES
    assert "console/fonts/quantum-NOTICE.txt" not in RESOURCES
    assert all((fonts / item["file"]).is_file() for item in manifest["fonts"])


@pytest.mark.parametrize("mode", ["dark", "light"])
@pytest.mark.parametrize("width", [240, 320, 640, 1200])
def test_world_attribution_is_legible_and_never_elides_osm(app, mode, width):
    theme.set_theme(mode)
    renderer = MapRenderer(basemap=load_world())
    renderer.resize(width, 320)
    renderer.show()
    try:
        app.processEvents()
        renderer.grab()
        assert OSM_ATTRIBUTION in renderer.attribution_text
        assert "\u00c2" not in renderer.attribution_text
        assert renderer._attribution_font.pixelSize() >= 12
        rect = renderer._attribution_rect
        assert 0 <= rect.left() < rect.right() <= renderer.width()
        assert 0 <= rect.top() < rect.bottom() <= renderer.height()
        bounds = QFontMetricsF(renderer._attribution_font).boundingRect(
            rect.adjusted(6, 4, -6, -4), int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
            renderer.attribution_text)
        assert bounds.height() <= rect.height() - 8 + 1
        assert renderer._attribution_licence_url == "https://www.openstreetmap.org/copyright"
    finally:
        renderer.close()
        renderer.deleteLater()
        app.processEvents()


def test_attribution_click_opens_odbl_info_without_panning_or_selecting(app, monkeypatch):
    renderer = MapRenderer(basemap=load_world())
    renderer.resize(320, 320)
    renderer.show()
    opened = []
    monkeypatch.setattr(geo_map.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    selected = []
    renderer.node_selected.connect(selected.append)
    renderer.link_selected.connect(selected.append)
    try:
        app.processEvents()
        renderer.grab()
        original_view = renderer._view
        QTest.mouseClick(renderer, Qt.MouseButton.LeftButton,
                         pos=renderer._attribution_rect.center().toPoint())
        assert opened == ["https://www.openstreetmap.org/copyright"]
        assert renderer._view == original_view and renderer._drag is None
        assert not selected
    finally:
        renderer.close()
        renderer.deleteLater()
        app.processEvents()


def test_freeze_spec_excludes_legacy_font_and_rejects_stale_collection():
    import ast
    tree = ast.parse((ROOT / "packaging/console.spec").read_text(encoding="utf-8"))
    required = next(node.iter for node in ast.walk(tree)
                    if isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                    and node.target.id == "relative" and isinstance(node.iter, ast.Tuple)
                    and any(isinstance(item, ast.Constant) and item.value == "console/assets/orion-q.ico"
                            for item in node.iter.elts))
    names = {item.value for item in required.elts if isinstance(item, ast.Constant)}
    assert "console/fonts/Quantum.otf" not in names
    assert "console/fonts/quantum-NOTICE.txt" not in names
    guard = next(node for node in tree.body if isinstance(node, ast.For)
                 and isinstance(node.target, ast.Tuple)
                 and [name.id for name in node.target.elts] == ["source", "destination"])
    code = compile(ast.Module(body=[guard], type_ignores=[]), "packaging-resource-guard", "exec")
    for filename in ("Quantum.otf", "quantum-NOTICE.txt"):
        with pytest.raises(ValueError, match="Excluded legacy font"):
            exec(code, {"Path": Path, "datas": [(f"stale/{filename}", "quantum_autopilot/console/fonts")]})
    exec(code, {"Path": Path, "datas": [("current/Inter.ttf", "quantum_autopilot/console/fonts")]})
