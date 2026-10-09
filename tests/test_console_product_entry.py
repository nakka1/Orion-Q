"""Public product entry keeps release identity separate from source evidence."""
from dataclasses import replace
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6.QtWidgets')
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.providers import MockProvider  # noqa: E402
from quantum_autopilot.console.screens import boot, splash  # noqa: E402
from quantum_autopilot.product import PRODUCT_NAME, product_version  # noqa: E402


SPONSOR = 'Sponsored by SENAI CIMATEC - LAQCC/QuIIN'


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def reset_theme(app):
    original = theme.current_theme()
    yield
    theme.set_theme(original)
    theme.install(app)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def inside_parent(widget):
    parent = widget.parentWidget()
    return parent.rect().contains(widget.mapTo(parent, QPoint())) and parent.rect().contains(
        widget.mapTo(parent, widget.rect().bottomRight()))


@pytest.mark.parametrize('mode', ['dark', 'light'])
@pytest.mark.parametrize('width,height', [
    (960, 480), (1120, 700), (1280, 720), (1440, 900),
    (1920, 1080), (2560, 1440), (3440, 1440),
])
def test_boot_sponsorship_and_release_version_fit_the_workspace_action(app, mode, width, height):
    theme.set_theme(mode)
    theme.install(app)
    screen = boot.BootScreen()
    screen.apply(MockProvider().snapshot())
    screen.resize(width, height)
    screen.show()
    try:
        app.processEvents()
        screen.scroll.ensureWidgetVisible(screen.sponsorship, 0, 20)
        app.processEvents()
        assert screen.sponsorship.text() == SPONSOR
        assert screen.sponsorship.textFormat() == Qt.TextFormat.PlainText
        assert screen.sponsorship.wordWrap()
        assert screen.sponsorship.height() >= screen.sponsorship.heightForWidth(screen.sponsorship.width())
        assert screen.version_label.text() == 'Version ' + product_version()
        assert screen.brand.accessibleName() == PRODUCT_NAME
        assert inside_parent(screen.sponsorship) and inside_parent(screen.version_label)
        assert inside_parent(screen.open_button) and inside_parent(screen.brand)
        # The sponsor belongs to the same action group, immediately below it.
        group = screen.sponsorship.parentWidget()
        button_bottom = screen.open_button.mapTo(group, screen.open_button.rect().bottomLeft()).y()
        assert 0 <= screen.sponsorship.y() - button_bottom <= 12
        viewport = screen.scroll.viewport()
        for widget in (screen.open_button, screen.sponsorship, screen.version_label):
            assert viewport.rect().contains(widget.mapTo(viewport, QPoint()))
            assert viewport.rect().contains(widget.mapTo(viewport, widget.rect().bottomRight()))
        assert not screen.grab().isNull()
    finally:
        screen.close()
        screen.deleteLater()


@pytest.mark.parametrize('module,screen_type', [(boot, boot.BootScreen), (splash, splash.SplashScreen)])
def test_entry_version_is_read_from_product_api_without_source_build_override(app, monkeypatch, module, screen_type):
    monkeypatch.setattr(module, 'product_version', lambda: '98.76.54+distribution-test')
    screen = screen_type()
    try:
        assert screen.version_label.text() == 'Version 98.76.54+distribution-test'
        if isinstance(screen, boot.BootScreen):
            snap = MockProvider().snapshot()
            first = replace(snap, boot=replace(snap.boot, build='source-provenance-one'))
            second = replace(snap, boot=replace(snap.boot, build='source-provenance-two'))
            screen.apply(first)
            assert screen.build.text() == 'source-provenance-one'
            screen.apply(second)
            assert screen.build.text() == 'source-provenance-two'
            assert screen.version_label.text() == 'Version 98.76.54+distribution-test'
    finally:
        screen.close()
        screen.deleteLater()


def test_branding_does_not_change_source_selection_or_workspace_signals(app):
    screen = boot.BootScreen()
    screen.configure_login(fixture=True)
    screen.show()
    requested, authenticated, commands = [], [], []
    screen.open_requested.connect(requested.append)
    screen.authenticated.connect(lambda: authenticated.append(True))
    screen.command.connect(lambda *args: commands.append(args))
    try:
        app.processEvents()
        assert not screen.open_button.isEnabled()
        screen.source_rows['demo'].click()
        assert not requested and not authenticated and not commands
        QTest.mouseClick(screen.open_button, Qt.MouseButton.LeftButton)
        assert requested == ['demo'] and authenticated == [True] and not commands
        state = screen.save_state()
        screen.set_sources((boot.SourceOption('demo', 'Unavailable', 'read only', 'Demo', False),))
        assert screen.selected_source is None and not screen.open_button.isEnabled()
        screen.set_sources(boot.SOURCE_OPTIONS)
        screen.restore_state(state)
        assert screen.selected_source == 'demo' and screen.sponsorship.text() == SPONSOR
    finally:
        screen.close()
        screen.deleteLater()


@pytest.mark.parametrize('mode', ['dark', 'light'])
@pytest.mark.parametrize('width,height', [(960, 480), (1440, 900), (3440, 1440)])
def test_splash_brand_and_version_keep_real_loading_feedback(app, mode, width, height):
    theme.set_theme(mode)
    theme.install(app)
    screen = splash.SplashScreen()
    screen.resize(width, height)
    screen.show()
    completed = []
    screen.finished.connect(lambda: completed.append(True))
    try:
        app.processEvents()
        assert inside_parent(screen.brand) and inside_parent(screen.version_label)
        assert screen.brand.accessibleName() == PRODUCT_NAME
        assert screen.version_label.text() == 'Version ' + product_version()
        screen.start()
        assert screen.percent.text() == 'WAIT' and not completed
        screen.note('Provider supplied message')
        assert screen.message.text() == 'Provider supplied message'
        screen.complete()
        assert screen.percent.text() == 'READY' and completed == [True]
    finally:
        screen.close()
        screen.deleteLater()
