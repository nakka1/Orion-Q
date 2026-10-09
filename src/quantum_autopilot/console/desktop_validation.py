"""Bounded native Windows icon and desktop-ownership diagnostic.

Every child uses only MockProvider and ConsoleWindow, a private mutex/event
namespace and directories under the requested output. No service, acquisition,
operator journal, authentication token or physical device is constructed.
The private child arguments are accepted only by this explicitly selected test.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


REQUIRED_CASES = (
    "resource-icon-32", "resource-icon-256", "app-window-taskbar-identity",
    "initial-load-activation", "second-instance-exclusion", "repeated-activation",
    "minimized-restore", "crash-restart",
)
SESSION_SCHEMA = "orion.desktop-behavior-session.v1"
REPORT_SCHEMA = "orion.desktop-behavior.v1"


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    # Windows readers may briefly deny rename while reading the previous
    # status. Retry this transient sharing violation without publishing a
    # partially written JSON document.
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.005)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _within_output(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("diagnostic fixture must be a child of the isolated output directory")
    return path


def _child_environment():
    environment = dict(os.environ, QT_QPA_PLATFORM="windows", QT_SCALE_FACTOR="1",
                       PYINSTALLER_RESET_ENVIRONMENT="1")
    # A frozen child must prove it can load its own plugins and package assets.
    for key in list(environment):
        if key.upper() in {"PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"}:
            del environment[key]
    if getattr(sys, "frozen", False):
        for key in list(environment):
            if key.upper() == "PYTHONPATH":
                del environment[key]
        system = environment.get("SystemRoot", r"C:\Windows")
        environment["PATH"] = os.pathsep.join((str(Path(system) / "System32"), system))
    else:
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    return environment


class NativeWindows:
    """Small Win32 adapter, also used by the coordinator to observe its children."""

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("desktop behavior qualification requires native Windows")
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        self.ole = ctypes.WinDLL("ole32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        pointer = ctypes.c_void_p
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = (
            (self.user.EnumWindows, [self.callback_type, wintypes.LPARAM], wintypes.BOOL),
            (self.user.GetWindowThreadProcessId, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            (self.user.GetWindowTextW, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.user.GetAncestor, [wintypes.HWND, wintypes.UINT], wintypes.HWND),
            (self.user.GetWindow, [wintypes.HWND, wintypes.UINT], wintypes.HWND),
            (self.user.GetWindowLongPtrW, [wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t),
            (self.user.GetClassLongPtrW, [wintypes.HWND, ctypes.c_int], ctypes.c_size_t),
            (self.user.SendMessageW, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], ctypes.c_ssize_t),
            (self.user.IsWindowVisible, [wintypes.HWND], wintypes.BOOL),
            (self.user.IsIconic, [wintypes.HWND], wintypes.BOOL),
            (self.user.ShowWindowAsync, [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            (self.user.DestroyIcon, [wintypes.HICON], wintypes.BOOL),
            (self.user.DrawIconEx, [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HBRUSH, wintypes.UINT], wintypes.BOOL),
            (self.user.GetIconInfo, [wintypes.HICON, pointer], wintypes.BOOL),
            (self.user.PrivateExtractIconsW, [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT),
                wintypes.UINT, wintypes.UINT], wintypes.UINT),
            (self.shell.ExtractIconExW, [wintypes.LPCWSTR, ctypes.c_int,
                ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.HICON), wintypes.UINT], wintypes.UINT),
            (self.shell.GetCurrentProcessExplicitAppUserModelID, [ctypes.POINTER(pointer)], ctypes.c_long),
            (self.ole.CoTaskMemFree, [pointer], None),
            (self.gdi.CreateCompatibleDC, [wintypes.HDC], wintypes.HDC),
            (self.gdi.CreateDIBSection, [wintypes.HDC, pointer, wintypes.UINT,
                                       ctypes.POINTER(pointer), wintypes.HANDLE, wintypes.DWORD], wintypes.HBITMAP),
            (self.gdi.SelectObject, [wintypes.HDC, wintypes.HGDIOBJ], wintypes.HGDIOBJ),
            (self.gdi.DeleteObject, [wintypes.HGDIOBJ], wintypes.BOOL),
            (self.gdi.DeleteDC, [wintypes.HDC], wintypes.BOOL),
            (self.gdi.GetObjectW, [wintypes.HGDIOBJ, ctypes.c_int, pointer], ctypes.c_int),
            (self.gdi.GdiFlush, [], wintypes.BOOL),
            (self.kernel.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (self.kernel.TerminateProcess, [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            (self.kernel.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
        )
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result

    def terminate_fixture_process(self, pid):
        """Terminate a PID reported by this coordinator's own child fixture."""
        process = self.kernel.OpenProcess(1, False, pid)  # PROCESS_TERMINATE
        if not process:
            return False
        try:
            return bool(self.kernel.TerminateProcess(process, 99))
        finally:
            self.kernel.CloseHandle(process)

    def main_windows(self, pid):
        found = []

        @self.callback_type
        def visit(hwnd, _):
            owner = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid:
                title = ctypes.create_unicode_buffer(256)
                self.user.GetWindowTextW(hwnd, title, len(title))
                if title.value == "OrionQ · Quantum Network Console":
                    found.append(int(hwnd))
            return True

        if not self.user.EnumWindows(visit, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return found

    def explicit_app_id(self):
        value = ctypes.c_void_p()
        result = self.shell.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(value))
        if result < 0 or not value.value:
            raise RuntimeError(f"GetCurrentProcessExplicitAppUserModelID failed: {result:#x}")
        try:
            return ctypes.wstring_at(value)
        finally:
            self.ole.CoTaskMemFree(value)

    def icon_size(self, handle):
        class IconInfo(ctypes.Structure):
            _fields_ = [("is_icon", wintypes.BOOL), ("x", wintypes.DWORD), ("y", wintypes.DWORD),
                        ("mask", wintypes.HBITMAP), ("colour", wintypes.HBITMAP)]

        class Bitmap(ctypes.Structure):
            _fields_ = [("kind", wintypes.LONG), ("width", wintypes.LONG), ("height", wintypes.LONG),
                        ("stride", wintypes.LONG), ("planes", wintypes.WORD),
                        ("depth", wintypes.WORD), ("bits", ctypes.c_void_p)]

        info = IconInfo()
        if not self.user.GetIconInfo(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            bitmap = Bitmap()
            if not self.gdi.GetObjectW(info.colour or info.mask, ctypes.sizeof(bitmap), ctypes.byref(bitmap)):
                raise ctypes.WinError(ctypes.get_last_error())
            return bitmap.width, bitmap.height if info.colour else bitmap.height // 2
        finally:
            for bitmap_handle in (info.colour, info.mask):
                if bitmap_handle:
                    self.gdi.DeleteObject(bitmap_handle)

    def render_icon(self, handle, size):
        """Capture DrawIconEx RGBA pixels in a top-down, transparent 32-bit DIB."""
        from PySide6.QtGui import QImage

        class BitmapHeader(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG),
                        ("planes", wintypes.WORD), ("bit_count", wintypes.WORD),
                        ("compression", wintypes.DWORD), ("image_size", wintypes.DWORD),
                        ("xppm", wintypes.LONG), ("yppm", wintypes.LONG),
                        ("colours", wintypes.DWORD), ("important", wintypes.DWORD)]

        header = BitmapHeader(ctypes.sizeof(BitmapHeader), size, -size, 1, 32, 0,
                              size * size * 4, 0, 0, 0, 0)
        dc = self.gdi.CreateCompatibleDC(None)
        if not dc:
            raise ctypes.WinError(ctypes.get_last_error())
        bits = ctypes.c_void_p()
        bitmap = self.gdi.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        if not bitmap:
            self.gdi.DeleteDC(dc)
            raise ctypes.WinError(ctypes.get_last_error())
        previous = self.gdi.SelectObject(dc, bitmap)
        try:
            ctypes.memset(bits, 0, size * size * 4)
            if not self.user.DrawIconEx(dc, 0, 0, handle, size, size, 0, None, 3):
                raise ctypes.WinError(ctypes.get_last_error())
            self.gdi.GdiFlush()
            raw = ctypes.string_at(bits, size * size * 4)
            # DrawIconEx returns premultiplied BGRA. Keep that precision:
            # unpremultiplying 8-bit edge pixels amplifies rounding in RGB.
            return QImage(raw, size, size, size * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
        finally:
            self.gdi.SelectObject(dc, previous)
            self.gdi.DeleteObject(bitmap)
            self.gdi.DeleteDC(dc)

    def extract_icon(self, path, size):
        icon = wintypes.HICON()
        if size == 32:
            count = self.shell.ExtractIconExW(str(path), 0, ctypes.byref(icon), None, 1)
        else:
            # ExtractIconExW selects system small/large sizes; request the actual
            # 256px resource separately instead of stretching its 32px result.
            count = self.user.PrivateExtractIconsW(str(path), 0, size, size,
                                                  ctypes.byref(icon), None, 1, 0)
        if count != 1 or not icon.value:
            raise RuntimeError(f"Windows could not extract a {size}px icon from {path.name}")
        return icon

    def window_facts(self, hwnd):
        root = int(self.user.GetAncestor(hwnd, 2) or 0)  # GA_ROOT
        style = int(self.user.GetWindowLongPtrW(root, -16)) & 0xFFFFFFFF
        extended = int(self.user.GetWindowLongPtrW(root, -20)) & 0xFFFFFFFF
        owner = int(self.user.GetWindow(root, 4) or 0)  # GW_OWNER
        return {"hwnd": int(hwnd), "root_hwnd": root, "style": style, "extended_style": extended,
                "owner_hwnd": owner, "visible": bool(self.user.IsWindowVisible(root)),
                "taskbar_eligible": bool(root == hwnd and not (style & 0x40000000)
                    and not (extended & 0x80) and (not owner or extended & 0x40000))}


def compare_icon_pixels(actual, expected, *, tolerance=2):
    """Compare quantized visual RGB and alpha in premultiplied RGBA space.

    Native DrawIconEx and Qt's ICO decoder can represent the same antialiased
    edge with different straight RGB after 8-bit unpremultiplication. Compare
    the actual colour contribution instead, with the same channel tolerance;
    alpha remains independently checked for every pixel.
    """
    from PySide6.QtGui import QImage
    if actual.size() != expected.size():
        return {"passed": False, "reason": "image dimensions differ"}
    actual = actual.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    expected = expected.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    a, b = bytes(actual.constBits()), bytes(expected.constBits())
    pixels = actual.width() * actual.height()
    bad_alpha = bad_colour = maximum_alpha = maximum_colour = coverage = 0
    for position in range(0, pixels * 4, 4):
        delta_alpha = abs(a[position + 3] - b[position + 3])
        maximum_alpha = max(maximum_alpha, delta_alpha)
        bad_alpha += delta_alpha > tolerance
        if b[position + 3] > tolerance:
            coverage += 1
        delta_colour = max(abs(a[position + c] - b[position + c]) for c in range(3))
        maximum_colour = max(maximum_colour, delta_colour)
        bad_colour += delta_colour > tolerance
    return {"passed": bool(coverage and not bad_alpha and not bad_colour), "size": actual.width(),
            "visible_pixels": coverage, "mismatched_alpha_pixels": bad_alpha,
            "mismatched_colour_pixels": bad_colour, "max_alpha_difference": maximum_alpha,
            "max_colour_difference": maximum_colour, "channel_tolerance": tolerance,
            "comparison_space": "RGBA8888_Premultiplied"}


def _native_icon_checks(app, window, directory):
    from .branding import ICON_PATH, WINDOWS_APP_ID, app_icon
    native, icon = NativeWindows(), app_icon()
    resource = Path(sys.executable) if getattr(sys, "frozen", False) else ICON_PATH
    cases = []
    for size in (32, 256):
        handle = native.extract_icon(resource, size)
        try:
            actual = native.render_icon(handle, size)
            expected = icon.pixmap(size, size).toImage()
            record = compare_icon_pixels(actual, expected)
            actual.save(str(directory / f"resource-icon-{size}.png"))
            expected.save(str(directory / f"expected-icon-{size}.png"))
            record.update(case=f"resource-icon-{size}", extractor="ExtractIconExW" if size == 32 else "PrivateExtractIconsW",
                          renderer="DrawIconEx", resource=str(resource), native_size=native.icon_size(handle))
            cases.append(record)
        finally:
            native.user.DestroyIcon(handle)
    hwnd = int(window.winId())
    facts = native.window_facts(hwnd)
    comparisons = []
    for size in (32, 256):
        expected = icon.pixmap(size, size).toImage()
        for name, current in (("application", app.windowIcon()), ("window", window.windowIcon()),
                              ("qwindow", window.windowHandle().icon())):
            result = compare_icon_pixels(current.pixmap(size, size).toImage(), expected)
            result["icon"] = name
            comparisons.append(result)
    # Qt supplies big/small per-window icons. The class fallback is recorded and
    # compared when present; a generic class icon cannot hide a wrong WM_GETICON.
    handles = []
    for name, wparam, class_index in (("big", 1, -14), ("small", 0, -34)):
        handle = int(native.user.SendMessageW(facts["root_hwnd"], 0x007F, wparam, 0) or 0)
        class_handle = int(native.user.GetClassLongPtrW(facts["root_hwnd"], class_index) or 0)
        chosen = handle or class_handle
        record = {"icon": name, "wm_geticon": handle, "class_fallback": class_handle,
                  "resolved_via": "WM_GETICON" if handle else "GetClassLongPtrW"}
        if chosen:
            width, height = native.icon_size(chosen)
            capture = native.render_icon(chosen, width)
            capture.save(str(directory / f"window-native-{name}-{width}.png"))
            record.update(compare_icon_pixels(capture, icon.pixmap(width, height).toImage()))
            record["native_size"] = [width, height]
        else:
            record.update(passed=False, reason="no native window or class icon")
        handles.append(record)
    identity = native.explicit_app_id()
    cases.append({"case": "app-window-taskbar-identity", "passed": bool(
        identity == WINDOWS_APP_ID and facts["visible"] and facts["taskbar_eligible"]
        and all(item["passed"] for item in comparisons + handles)),
        "application_id": identity, "window": facts, "qt_icons": comparisons, "native_icons": handles,
        "taskbar_shell_pixels_verified": False})
    return cases


def _child(args, output):
    session = _read_json(output / "session.json")
    if not session or session.get("schema") != SESSION_SCHEMA:
        raise ValueError("internal desktop child requires an isolated coordinator session")
    # A Windows venv interpreter can start a second Python process, so its
    # immediate parent PID is not necessarily the coordinator. Bind the child
    # to the coordinator's fresh fixture session instead of assuming a process
    # layout shared by source Python and PyInstaller.
    if args._session_id != session.get("run_id"):
        raise ValueError("internal desktop child requires its coordinator session ID")
    namespace = "ORION-Q.Desktop.Validation." + str(uuid.UUID(session["run_id"]))
    name = args._case_name
    if name not in {"primary", "restart", "loading-secondary", "secondary", "repeat-1", "repeat-2", "restore-secondary"}:
        raise ValueError("unknown internal desktop diagnostic case")
    directory = _within_output(output, output / name)
    directory.mkdir(parents=True, exist_ok=True)
    fixture = _within_output(output, args._fixture_state_root or output / name / "fixture-state")
    from quantum_autopilot.desktop_instance import acquire_desktop_instance, mark_isolated_diagnostic_window
    owner = acquire_desktop_instance(_namespace=namespace)
    try:
        record = {"pid": os.getpid(), "is_primary": owner.is_primary,
                  "fixture_state_root": str(fixture), "cwd": str(Path.cwd()),
                  "gui_created": False, "provider": "MockProvider", "operator_state_accessed": False}
        if args._role == "secondary":
            # This branch precedes every Qt import and every state mkdir.
            record["activation_sent"] = False if owner.is_primary else owner.request_activation()
            record["fixture_state_created"] = fixture.exists()
            record["passed"] = not owner.is_primary and record["activation_sent"] and not fixture.exists()
            _write_json(directory / "result.json", record)
            return 0 if record["passed"] else 1
        if not owner.is_primary:
            record.update(passed=False, error="isolated primary could not acquire ownership")
            _write_json(directory / "result.json", record)
            return 1
        _write_json(directory / "claimed.json", record)
        # An activation received before QApplication/window construction must be
        # delivered after the native window is attached to the normal guard.
        deadline = time.monotonic() + args._load_delay_ms / 1000
        while time.monotonic() < deadline:
            time.sleep(0.025)
        from PySide6.QtCore import QThread, QTimer
        from PySide6.QtWidgets import QApplication
        from . import theme
        from .branding import app_icon, configure_windows_app_identity
        from .display import configure_high_dpi, show_main_window
        from .providers import MockProvider
        from .shell import ConsoleWindow
        from .ui_gc import UiThreadCollector
        configure_high_dpi()
        if not configure_windows_app_identity():
            raise RuntimeError("the production taskbar application identity could not be configured")
        app = QApplication([])
        app.setApplicationName("ORION-Q Console")
        app.setOrganizationName("ORION-Q")
        app.setWindowIcon(app_icon())
        collector = UiThreadCollector(app)
        collector.start()
        provider, window = None, None
        failures = []
        try:
            theme.install(app)
            fixture.mkdir(parents=True, exist_ok=False)
            provider = MockProvider()
            window = ConsoleWindow(provider, skip_auth=True, state_root=fixture)
            mark_isolated_diagnostic_window(window)
            record.update(gui_created=True, activation_count=0, activations_on_ui_thread=True)
            consume = owner.consume_activation

            def observed_consume():
                result = consume()
                if result:
                    record["activation_count"] += 1
                    record["activations_on_ui_thread"] &= QThread.currentThread() == app.thread()
                return result

            owner.consume_activation = observed_consume
            show_main_window(window)
            owner.attach_qt_window(window)
            provider.start()
            start = time.monotonic()
            checked = False

            def poll():
                nonlocal checked
                try:
                    if not checked and time.monotonic() - start >= 0.4:
                        record["cases"] = _native_icon_checks(app, window, directory)
                        record["platform"] = app.platformName()
                        record["hwnd"] = int(window.winId())
                        record["passed"] = bool(record["platform"] == "windows" and
                                                all(item["passed"] for item in record["cases"]))
                        checked = True
                        _write_json(directory / "ready.json", record)
                    record.update(minimized=window.isMinimized(), visible=window.isVisible(),
                                  heartbeat_elapsed_s=time.monotonic() - start)
                    _write_json(directory / "status.json", record)
                    if time.monotonic() - start > 1.8 and not (directory / "console-window.png").is_file():
                        window.grab().save(str(directory / "console-window.png"))
                    if (directory / "stop.request").exists():
                        window.close()
                        app.quit()
                except Exception as error:
                    failures.append(f"{type(error).__name__}: {error}")
                    _write_json(directory / "result.json", dict(record, passed=False, errors=failures))
                    app.exit(1)

            timer = QTimer(app)
            timer.setInterval(40)
            timer.timeout.connect(poll)
            timer.start()
            QTimer.singleShot(90000, app, lambda: app.exit(2))
            code = app.exec()
            record.update(passed=bool(record.get("passed") and code == 0 and not failures), errors=failures)
            _write_json(directory / "result.json", record)
            return 0 if record["passed"] else 1
        finally:
            owner.close()
            if provider is not None:
                provider.stop()
            if window is not None:
                window.close()
            collector.stop()
    finally:
        owner.close()


def _await_record(path, process, predicate=lambda value: True, *, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = _read_json(path)
        if value is not None and predicate(value):
            return value
        if process.poll() is not None:
            failure = _read_json(Path(path).parent / "result.json")
            raise RuntimeError(f"desktop child exited {process.returncode} before {Path(path).name}: {failure}")
        time.sleep(0.04)
    raise TimeoutError(f"desktop child did not produce the required {Path(path).name} within {timeout}s")


def run_desktop_behavior_test(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("desktop diagnostic requires a new, empty isolated output directory")
    frozen = bool(getattr(sys, "frozen", False))
    executable = Path(sys.executable).resolve()
    report = {"schema": REPORT_SCHEMA, "passed": False, "frozen": frozen,
              "executable": str(executable), "executable_sha256": _digest(executable),
              "scope": "native Windows MockProvider ConsoleWindow; isolated state and ownership; no acquisition or physical actuation",
              "operator_state_accessed": False, "independent_machine_verified": False,
              "taskbar_shell_pixels_verified": False, "cases": [], "error": None}
    processes = []
    try:
        native = NativeWindows()
        session_id = str(uuid.uuid4())
        _write_json(output / "session.json", {"schema": SESSION_SCHEMA, "run_id": session_id,
                                             "coordinator_pid": os.getpid()})
        base = [str(executable)] if frozen else [str(executable), "-m", "quantum_autopilot.desktop"]
        environment = _child_environment()

        def launch(role, name, *, delay=0, alternate_cwd=False):
            directory = output / name
            directory.mkdir()
            fixture = output / "unused-secondary-state" / name if role == "secondary" else directory / "fixture-state"
            cwd = output / "alternate-working-directory" if alternate_cwd else output
            cwd.mkdir(exist_ok=True)
            command = [*base, "--desktop-behavior-test", str(output), "--_role", role,
                       "--_case-name", name, "--_session-id", session_id,
                       "--_fixture-state-root", str(fixture)]
            if delay:
                command += ["--_load-delay-ms", str(delay)]
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            with (directory / "stdout.log").open("w", encoding="utf-8") as stdout, (directory / "stderr.log").open("w", encoding="utf-8") as stderr:
                process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=stdout, stderr=stderr,
                    startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
            processes.append((process, directory))
            return process

        def secondary(name):
            process = launch("secondary", name, alternate_cwd=True)
            _await_record(output / name / "result.json", process, timeout=20)
            code = process.wait(timeout=5)
            record = _read_json(output / name / "result.json")
            record["exit_code"] = code
            record["main_window_count"] = len(native.main_windows(record["pid"]))
            record["passed"] &= code == 0 and record["main_window_count"] == 0
            if not record["passed"]:
                raise AssertionError(f"{name}: secondary process created state/window or did not activate its owner")
            return record

        primary = launch("primary", "primary", delay=2500)
        _await_record(output / "primary/claimed.json", primary)
        loading = secondary("loading-secondary")
        ready = _await_record(output / "primary/ready.json", primary, timeout=35)
        report["cases"].extend(ready["cases"])
        if not ready["passed"]:
            raise AssertionError("the native resource, Qt window icons or taskbar identity do not match")
        hwnd = ready["hwnd"]
        primary_pid = ready["pid"]
        status = _await_record(output / "primary/status.json", primary, lambda r: r["activation_count"] >= 1)
        report["cases"].append({"case": "initial-load-activation", "passed": bool(
            status["activation_count"] == 1 and status["activations_on_ui_thread"]), "secondary": loading,
            "activation_count": status["activation_count"], "load_delay_ms": 2500})
        count = status["activation_count"]
        result = secondary("secondary")
        status = _await_record(output / "primary/status.json", primary, lambda r: r["activation_count"] > count)
        report["cases"].append({"case": "second-instance-exclusion", "passed": bool(
            native.main_windows(primary_pid) == [hwnd] and status["activations_on_ui_thread"]), "secondary": result,
            "primary_main_window_count": len(native.main_windows(primary_pid))})
        repeats = []
        for name in ("repeat-1", "repeat-2"):
            count = status["activation_count"]
            repeats.append(secondary(name))
            status = _await_record(output / "primary/status.json", primary, lambda r: r["activation_count"] > count)
        report["cases"].append({"case": "repeated-activation", "passed": bool(
            status["activations_on_ui_thread"] and status["activation_count"] == 4), "requests": repeats,
            "activation_count": status["activation_count"]})
        native.user.ShowWindowAsync(hwnd, 6)  # SW_MINIMIZE, never another application's HWND
        _await_record(output / "primary/status.json", primary, lambda r: r["minimized"])
        was_iconic = bool(native.user.IsIconic(hwnd))
        count = status["activation_count"]
        restore = secondary("restore-secondary")
        status = _await_record(output / "primary/status.json", primary,
                               lambda r: r["activation_count"] > count and not r["minimized"])
        report["cases"].append({"case": "minimized-restore", "passed": bool(was_iconic
            and not native.user.IsIconic(hwnd) and native.user.IsWindowVisible(hwnd)
            and status["activations_on_ui_thread"]), "secondary": restore, "restored_hwnd": hwnd})
        # Terminate only the PID spawned by this diagnostic. Windows must reclaim
        # ownership; no cleanup path in the killed application can release it.
        if not native.terminate_fixture_process(primary_pid):
            raise RuntimeError("could not terminate the isolated primary fixture")
        primary.wait(timeout=10)
        restart = launch("primary", "restart")
        recovered = _await_record(output / "restart/ready.json", restart, timeout=35)
        report["cases"].append({"case": "crash-restart", "passed": bool(recovered["is_primary"]
            and recovered["passed"] and not native.main_windows(primary_pid)
            and len(native.main_windows(recovered["pid"])) == 1), "killed_pid": primary_pid,
            "new_pid": recovered["pid"], "recovered_hwnd": recovered["hwnd"]})
        # Let the full fixture render before the preserved screenshot and close.
        _await_record(output / "restart/status.json", restart,
                       lambda r: r["heartbeat_elapsed_s"] > 2)
        (output / "restart/stop.request").write_text("stop isolated diagnostic", encoding="utf-8")
        if restart.wait(timeout=10) != 0:
            raise AssertionError("the restarted diagnostic did not shut down cleanly")
        report["passed"] = all(item["passed"] for item in report["cases"]) and {
            item["case"] for item in report["cases"]} == set(REQUIRED_CASES)
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        for process, directory in processes:
            if process.poll() is None:
                child_record = _read_json(directory / "claimed.json")
                if child_record and child_record.get("pid"):
                    native.terminate_fixture_process(child_record["pid"])
                process.kill()
                process.wait(timeout=10)
        report["executable_sha256_after"] = _digest(executable)
        if report["executable_sha256_after"] != report["executable_sha256"]:
            report.update(passed=False, error="desktop executable changed during qualification")
        _write_json(output / "report.json", report)
        lines = ["ORION-Q native desktop behavior qualification", f"Passed: {report['passed']}",
                 f"Executable SHA-256: {report['executable_sha256']}", report["scope"], ""]
        lines.extend(f"{'PASS' if case['passed'] else 'FAIL'}: {case['case']}" for case in report["cases"])
        if report["error"]:
            lines.append("Error: " + report["error"])
        lines.extend(["", "Taskbar eligibility, identity and native icon pixels checked.",
                      "Desktop shell taskbar pixels and a second physical machine are not verified."])
        (output / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--_role", choices=("primary", "secondary"), help=argparse.SUPPRESS)
    parser.add_argument("--_case-name", help=argparse.SUPPRESS)
    parser.add_argument("--_session-id", help=argparse.SUPPRESS)
    parser.add_argument("--_fixture-state-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--_load-delay-ms", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._role:
        if not args._case_name or not 0 <= args._load_delay_ms <= 5000:
            parser.error("invalid internal isolated child options")
        try:
            return _child(args, args.output.resolve())
        except Exception as error:
            if args._case_name:
                directory = _within_output(args.output, args.output / args._case_name)
                _write_json(directory / "result.json", {"passed": False, "error": f"{type(error).__name__}: {error}"})
            return 1
    if args._case_name or args._session_id or args._fixture_state_root or args._load_delay_ms:
        parser.error("internal child options require an isolated diagnostic role")
    result = run_desktop_behavior_test(args.output)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
