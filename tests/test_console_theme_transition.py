"""Appearance changes must preserve state without repolishing unrelated Qt windows."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from quantum_autopilot.console import theme
from quantum_autopilot.console.providers import MockProvider
from quantum_autopilot.console.shell import ConsoleWindow


@pytest.mark.parametrize('page', ['topology', 'telemetry', 'keys', 'node', 'fpga', 'audit'])
def test_theme_switches_keep_input_geometry_and_scope_styles_to_current_window(page):
    app = QApplication.instance() or QApplication([])
    previous = theme.current_theme()
    theme.install(app)
    application_stylesheet = app.styleSheet()
    provider = MockProvider()
    window = ConsoleWindow(provider, skip_auth=True)
    provider.start(); window.show_screen(page)
    window.resize(1147, 673); window.show(); app.processEvents()
    window.journal.prompt.setText('help')
    geometry = window.geometry()
    try:
        for _ in range(4):
            assert window.toggle_theme()
            app.processEvents()
            assert app.styleSheet() == application_stylesheet
            assert theme.build_qss() in window.styleSheet()
            assert window.current_screen == page
            assert window.journal.prompt.text() == 'help'
            assert window.geometry() == geometry
            assert window.updatesEnabled()
    finally:
        window.close(); window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        theme.set_theme(previous); theme.install(app)
