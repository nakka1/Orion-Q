"""Qualify the real product-entry widgets with isolated mock observations."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import sys
from types import CodeType


SCHEMA = 'orion.branding-acceptance.v1'
SIZES = ((960, 480), (1280, 720), (1440, 900), (3440, 1440))
CASES = tuple(f'{kind}-{mode}-{width}x{height}'
              for mode in ('dark', 'light') for width, height in SIZES for kind in ('boot', 'splash'))
SPONSOR = 'Sponsored by SENAI CIMATEC - LAQCC/QuIIN'
WORDMARK_CLASS = 'quantum_autopilot.console.widgets.brand_mark.OrionQWordmark'
CHECKS = {'product_name', 'distribution_version_matches_metadata', 'source_build_is_independent',
          'boot_sponsor_exact', 'wordmark_class_and_name', 'all_entry_layouts', 'windows_version_resource'}
SOURCE_MODULES = ('quantum_autopilot.product', 'quantum_autopilot.console.branding',
                  'quantum_autopilot.console.branding_validation',
                  'quantum_autopilot.console.widgets.brand_mark',
                  'quantum_autopilot.console.screens.boot', 'quantum_autopilot.console.screens.splash')


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _brand_assets():
    """Identify the exact font, provenance and shared icon shipped in the build."""
    from .branding import ICON_PATH
    from .theme import FONT_DIR

    paths = (FONT_DIR / 'Inter.ttf', FONT_DIR / 'inter-OFL.txt', ICON_PATH)
    return {path.name: _sha256(path) for path in paths}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def code_fingerprint(code):
    """Hash code values, independent of filenames and marshal reference sharing."""
    def value(item):
        if isinstance(item, CodeType):
            return {'type': 'code', 'argcount': item.co_argcount,
                    'posonlyargcount': item.co_posonlyargcount, 'kwonlyargcount': item.co_kwonlyargcount,
                    'nlocals': item.co_nlocals, 'stacksize': item.co_stacksize, 'flags': item.co_flags,
                    'firstlineno': item.co_firstlineno, 'name': item.co_name,
                    'qualname': getattr(item, 'co_qualname', item.co_name),
                    'code': item.co_code.hex(), 'constants': [value(v) for v in item.co_consts],
                    'names': list(item.co_names), 'varnames': list(item.co_varnames),
                    'freevars': list(item.co_freevars), 'cellvars': list(item.co_cellvars),
                    'linetable': item.co_linetable.hex(),
                    'exceptiontable': getattr(item, 'co_exceptiontable', b'').hex()}
        if item is None or type(item) in (bool, int, str):
            return {'type': type(item).__name__, 'value': item}
        if type(item) is float:
            return {'type': 'float', 'value': item.hex()}
        if type(item) is complex:
            return {'type': 'complex', 'real': item.real.hex(), 'imag': item.imag.hex()}
        if type(item) is bytes:
            return {'type': 'bytes', 'value': item.hex()}
        if type(item) is tuple:
            return {'type': 'tuple', 'value': [value(v) for v in item]}
        if type(item) is frozenset:
            values = [value(v) for v in item]
            values.sort(key=lambda v: json.dumps(v, sort_keys=True, separators=(',', ':')))
            return {'type': 'frozenset', 'value': values}
        if item is Ellipsis:
            return {'type': 'ellipsis'}
        raise TypeError('Unsupported branding code constant: ' + type(item).__name__)

    encoded = json.dumps(value(code), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _code_sha256(module_name):
    """Compare code loaded from PYZ against build sources at optimize=1."""
    module = import_module(module_name)
    spec = module.__spec__
    if getattr(sys, 'frozen', False):
        code = spec.loader.get_code(spec.name)
    else:
        source = spec.loader.get_source(spec.name)
        _require(isinstance(source, str), f'Cannot inspect branding build source: {module_name}')
        # console.spec deliberately freezes optimize=1. Do not inherit this
        # verifier's future flags when compiling a different source module.
        code = compile(source, module_name, 'exec', dont_inherit=True, optimize=1)
    _require(isinstance(code, CodeType), f'Cannot inspect loaded branding code: {module_name}')

    return code_fingerprint(code)


def read_windows_version_resource(executable):
    """Read the actual PE resource exposed by Windows file properties."""
    import ctypes

    api = ctypes.WinDLL('version', use_last_error=True)
    api.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    api.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
    api.GetFileVersionInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    api.GetFileVersionInfoW.restype = ctypes.c_int
    api.VerQueryValueW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p),
                                 ctypes.POINTER(ctypes.c_uint32)]
    api.VerQueryValueW.restype = ctypes.c_int
    ignored = ctypes.c_uint32()
    size = api.GetFileVersionInfoSizeW(str(executable), ctypes.byref(ignored))
    _require(size > 0, 'Executable has no Windows version resource')
    buffer = ctypes.create_string_buffer(size)
    _require(api.GetFileVersionInfoW(str(executable), 0, size, buffer), 'Cannot read Windows version resource')
    pointer, length = ctypes.c_void_p(), ctypes.c_uint32()
    _require(api.VerQueryValueW(buffer, '\\VarFileInfo\\Translation', ctypes.byref(pointer), ctypes.byref(length))
             and length.value >= 4, 'Windows version resource lacks translation metadata')
    languages = (ctypes.c_uint16 * (length.value // 2)).from_address(pointer.value)
    result = {}
    for key in ('ProductName', 'OriginalFilename', 'FileVersion', 'ProductVersion'):
        for index in range(0, len(languages) - 1, 2):
            query = f'\\StringFileInfo\\{languages[index]:04x}{languages[index + 1]:04x}\\{key}'
            if api.VerQueryValueW(buffer, query, ctypes.byref(pointer), ctypes.byref(length)) and length.value:
                result[key] = ctypes.wstring_at(pointer.value, length.value).rstrip('\0')
                break
        _require(key in result, 'Windows version resource lacks ' + key)
    return result


def _version_resource_matches(values, product_version):
    return values == {'ProductName': 'OrionQ', 'OriginalFilename': 'OrionQ.exe',
                      'FileVersion': product_version, 'ProductVersion': product_version}


def validate_native_report(report, executable, *, evidence_root):
    """Reject incomplete, stale, source-only or escaped frozen qualification."""
    from quantum_autopilot.product import DISTRIBUTION_NAME, PRODUCT_NAME, product_version

    executable, evidence_root = Path(executable).resolve(), Path(evidence_root).resolve()
    _require(report.get('schema') == SCHEMA and report.get('passed') is True, 'Branding qualification failed')
    _require(report.get('frozen') is True and report.get('platform') == 'windows'
             and report.get('qt_platform') == 'windows', 'Native frozen Windows branding required')
    _require(report.get('product_name') == PRODUCT_NAME == 'OrionQ', 'Different public product name')
    _require(report.get('version') == product_version()
             and report.get('installed_distribution_version') == report.get('version')
             and report.get('installed_distribution_name') == DISTRIBUTION_NAME,
             'Branding version differs from distribution metadata')
    _require(report.get('operator_state_accessed') is False and report.get('service_started') is False
             and report.get('physical_action_performed') is False, 'Branding diagnostic scope changed')
    _require(not report.get('errors') and report.get('wordmark_class') == WORDMARK_CLASS,
             'Wrong wordmark source or diagnostic error')
    _require(report.get('source_code_sha256') == {name: _code_sha256(name) for name in SOURCE_MODULES},
             'Branding loaded code differs from the qualified build source')
    _require(report.get('brand_assets') == _brand_assets(), 'Branding font or icon differs from the build assets')
    _require(report.get('source_code_optimization') == 1
             and report.get('python_version') == list(sys.version_info[:2]),
             'Different frozen bytecode optimization or Python runtime')
    _require(Path(report.get('executable', '')).resolve() == executable
             and executable.name == 'OrionQ.exe', 'Branding refers to a different executable')
    actual_hash = _sha256(executable)
    _require(report.get('executable_sha256_before') == report.get('executable_sha256_after') == actual_hash,
             'Branding executable hash changed')
    version_resource = read_windows_version_resource(executable)
    _require(report.get('windows_version_resource_validated') is True
             and report.get('windows_version_info') == version_resource
             and _version_resource_matches(version_resource, report.get('version')),
             'Windows executable name/version resource differs from the product')
    checks = report.get('checks', {})
    _require(set(checks) == CHECKS and all(value is True for value in checks.values()), 'Missing branding checks')
    cases = report.get('cases', [])
    _require(len(cases) == len(CASES) and {case.get('case') for case in cases} == set(CASES),
             'Missing or duplicate branding layouts')
    for case in cases:
        expected_checks = {'wordmark', 'release_version', 'geometry'}
        if case['case'].startswith('boot-'):
            expected_checks.add('sponsor_readable')
        _require(case.get('passed') is True and set(case.get('checks', {})) == expected_checks
                 and all(value is True for value in case['checks'].values()), 'A branding layout failed')
        _require(case.get('brand_font_family') == 'Inter', 'A branding layout used a substitute font')
        captures = case.get('screenshots', [])
        _require(len(captures) == (2 if case['case'].startswith('boot-') else 1),
                 'Branding layout has incomplete captured evidence')
        ratio = case.get('device_pixel_ratio')
        _require(type(ratio) in (int, float) and 0 < ratio <= 8, 'Missing branding display pixel ratio')
        width, height = (int(part) for part in case['case'].rsplit('-', 1)[1].split('x'))
        for capture in captures:
            path = (evidence_root / capture.get('path', '')).resolve()
            _require(path.is_relative_to(evidence_root) and path.is_file()
                     and path.suffix.lower() == '.png', 'Branding screenshot escaped evidence directory')
            _require(capture.get('capture_method') == 'QScreen.grabWindow(HWND)'
                     and _sha256(path) == capture.get('sha256'), 'Branding screenshot changed or is not native')
            with path.open('rb') as handle:
                header = handle.read(24)
            _require(header[:8] == b'\x89PNG\r\n\x1a\n' and header[12:16] == b'IHDR'
                     and int.from_bytes(header[16:20], 'big') == round(width * ratio)
                     and int.from_bytes(header[20:24], 'big') == round(height * ratio),
                     'Branding screenshot does not match the native window size')


def run(output) -> int:
    """Render actual Boot/Splash views; never instantiate a service or journal."""
    from dataclasses import replace
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QFontInfo
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.desktop_instance import mark_isolated_diagnostic_window
    from quantum_autopilot.product import DISTRIBUTION_NAME, PRODUCT_NAME, product_version
    from . import theme
    from .branding import app_icon, configure_windows_app_identity
    from .providers import MockProvider
    from .screens.boot import BootScreen
    from .screens.splash import SplashScreen

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / 'branding-report.json'
    _require(not report_path.exists(), 'Use a new branding evidence directory')
    configure_windows_app_identity()
    app = QApplication.instance() or QApplication([])
    app.setWindowIcon(app_icon())
    original_theme, original_quit = theme.current_theme(), app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    executable = Path(sys.executable).resolve()
    frozen = bool(getattr(sys, 'frozen', False))
    try:
        installed_version = version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        installed_version = None
    declared_version = product_version()
    report = {'schema': SCHEMA, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'passed': False, 'frozen': frozen, 'platform': sys.platform.replace('win32', 'windows'),
              'qt_platform': app.platformName(), 'product_name': PRODUCT_NAME, 'version': declared_version,
              'installed_distribution_name': DISTRIBUTION_NAME, 'installed_distribution_version': installed_version,
              'executable': str(executable), 'executable_sha256_before': _sha256(executable),
              'executable_sha256_after': None, 'operator_state_accessed': False, 'service_started': False,
              'physical_action_performed': False, 'wordmark_class': WORDMARK_CLASS,
              'source_code_sha256': {name: _code_sha256(name) for name in SOURCE_MODULES},
              'brand_assets': _brand_assets(),
              'source_code_optimization': 1,
              'python_version': list(sys.version_info[:2]),
              'windows_version_info': None,
              'windows_version_resource_validated': frozen,
              'checks': {}, 'cases': [], 'errors': []}
    views = []

    def inside(widget):
        parent = widget.parentWidget()
        return parent.rect().contains(widget.mapTo(parent, QPoint())) and parent.rect().contains(
            widget.mapTo(parent, widget.rect().bottomRight()))

    def capture(view, filename):
        screen = view.screen()
        pixmap = screen.grabWindow(int(view.winId()))
        _require(not pixmap.isNull(), 'Native entry window capture is empty')
        path = output / filename
        _require(pixmap.save(str(path)), 'Could not retain branding screenshot')
        return {'path': filename, 'sha256': _sha256(path), 'capture_method': 'QScreen.grabWindow(HWND)'}

    try:
        _require(sys.platform == 'win32' and app.platformName() == 'windows', 'Use the Windows Qt platform for branding capture')
        snapshot = MockProvider().snapshot()
        report['checks']['product_name'] = PRODUCT_NAME == 'OrionQ' and BootScreen.title == PRODUCT_NAME
        report['checks']['distribution_version_matches_metadata'] = installed_version == declared_version
        if frozen:
            report['windows_version_info'] = read_windows_version_resource(executable)
        report['checks']['windows_version_resource'] = (not frozen or
            _version_resource_matches(report['windows_version_info'], declared_version))
        for mode in ('dark', 'light'):
            theme.set_theme(mode)
            theme.install(app)
            for width, height in SIZES:
                for kind, factory in (('boot', BootScreen), ('splash', SplashScreen)):
                    key = f'{kind}-{mode}-{width}x{height}'
                    view = factory()
                    views.append(view)
                    view.setWindowTitle(PRODUCT_NAME + ' · isolated branding diagnostic')
                    view.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
                    view.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                                        | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
                    view.setWindowIcon(app_icon())
                    if kind == 'boot':
                        view.apply(replace(snapshot, boot=replace(snapshot.boot, build='isolated-source-provenance-A')))
                        view.apply(replace(snapshot, boot=replace(snapshot.boot, build='isolated-source-provenance-B')))
                        report['checks']['source_build_is_independent'] = (view.build.text() == 'isolated-source-provenance-B'
                            and view.version_label.text() == 'Version ' + declared_version)
                        report['checks']['boot_sponsor_exact'] = view.sponsorship.text() == SPONSOR
                    view.resize(width, height)
                    view.move(app.primaryScreen().availableGeometry().topLeft())
                    mark_isolated_diagnostic_window(view)
                    view.show()
                    QTest.qWait(35)
                    app.processEvents()
                    klass = type(view.brand).__module__ + '.' + type(view.brand).__name__
                    checks = {'wordmark': klass == WORDMARK_CLASS and view.brand.accessibleName() == PRODUCT_NAME,
                              'release_version': view.version_label.text() == 'Version ' + declared_version,
                              'geometry': inside(view.brand) and inside(view.version_label)
                                          and view.size().width() == width and view.size().height() == height}
                    captures = [capture(view, key + '-entry.png')]
                    if kind == 'boot':
                        view.scroll.ensureWidgetVisible(view.sponsorship, 0, 20)
                        QTest.qWait(20)
                        app.processEvents()
                        viewport = view.scroll.viewport()
                        checks['sponsor_readable'] = (view.sponsorship.text() == SPONSOR
                            and inside(view.sponsorship)
                            and view.sponsorship.height() >= view.sponsorship.heightForWidth(view.sponsorship.width())
                            and all(viewport.rect().contains(widget.mapTo(viewport, point))
                                    for widget in (view.sponsorship, view.open_button, view.version_label)
                                    for point in (QPoint(), widget.rect().bottomRight())))
                        captures.append(capture(view, key + '-actions.png'))
                    report['cases'].append({'case': key, 'passed': all(checks.values()),
                                            'brand_font_family': QFontInfo(theme.text()).family(),
                                            'device_pixel_ratio': view.devicePixelRatioF(),
                                            'checks': checks, 'screenshots': captures})
                    view.close()
                    view.deleteLater()
                    views.remove(view)
                    app.processEvents()
        report['checks']['wordmark_class_and_name'] = all(case['checks']['wordmark'] for case in report['cases'])
        report['checks']['all_entry_layouts'] = len(report['cases']) == len(CASES) and all(case['passed'] for case in report['cases'])
        report['executable_sha256_after'] = _sha256(executable)
        report['passed'] = (set(report['checks']) == CHECKS and all(report['checks'].values())
                            and report['executable_sha256_before'] == report['executable_sha256_after'])
        if frozen:
            validate_native_report(report, executable, evidence_root=output)
    except Exception as exc:
        report['passed'] = False
        report['errors'].append(type(exc).__name__ + ': ' + str(exc))
    finally:
        for view in views:
            view.close()
            view.deleteLater()
        theme.set_theme(original_theme)
        theme.install(app)
        app.setQuitOnLastWindowClosed(original_quit)
        app.processEvents()
        with report_path.open('x', encoding='utf-8') as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
    return 0 if report['passed'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    return run(parser.parse_args(argv).output)


if __name__ == '__main__':
    raise SystemExit(main())
