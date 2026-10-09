"""Windows machine-wide ownership of the interactive ORION-Q application.

The mutex identity never includes a release, extraction directory, user, journal
or working directory. Windows owns the handles, so a crash cannot leave a stale
lock file. Headless tools and explicitly isolated diagnostics do not use this
guard. No command line or environment variable can change its production name.

An auto-reset event carries only an activation request: it cannot forward
arguments, change input sources, authenticate, or issue a network command.
Its signal survives the first window's loading and is consumed on the Qt thread.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
import threading
import time
from ctypes import wintypes
from quantum_autopilot.product import EXECUTABLE_NAME, PRODUCT_NAME


_PRODUCT_NAMESPACE = "ORION-Q.Desktop.7BE09401-981D-49EC-B157-6BC04F6AC80A"
_DIAGNOSTIC_PROPERTY = "ORION-Q.Desktop.IsolatedDiagnostic"
_MAIN_WINDOW_TITLE = f"{PRODUCT_NAME} · Quantum Network Console"
_COMPATIBLE_WINDOW_TITLES = {_MAIN_WINDOW_TITLE, "ORION-Q · Quantum Network Console"}
_COMPATIBLE_IMAGE_NAMES = {EXECUTABLE_NAME.casefold(), "orion-q.exe"}
_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x80
_WAIT_TIMEOUT = 0x102
_WAIT_FAILED = 0xFFFFFFFF
_ERROR_FILE_NOT_FOUND = 2
_ERROR_ACCESS_DENIED = 5
_ERROR_ALREADY_EXISTS = 183
_EVENT_MODIFY_STATE = 0x0002
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_active_names: set[str] = set()
_registry_lock = threading.Lock()


class DesktopInstanceError(OSError):
    """The operating system could not safely establish desktop ownership."""


class _WindowsApi:
    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = (
            (self.kernel.CreateMutexW, [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            (self.kernel.CreateEventW,
             [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            (self.kernel.OpenEventW, [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            (self.kernel.WaitForSingleObject, [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            (self.kernel.SetEvent, [wintypes.HANDLE], wintypes.BOOL),
            (self.kernel.ResetEvent, [wintypes.HANDLE], wintypes.BOOL),
            (self.kernel.ReleaseMutex, [wintypes.HANDLE], wintypes.BOOL),
            (self.kernel.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
            (self.kernel.OpenProcess,
             [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (self.kernel.QueryFullProcessImageNameW,
             [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)],
             wintypes.BOOL),
            (self.user.EnumWindows, [self.callback_type, wintypes.LPARAM], wintypes.BOOL),
            (self.user.SetPropW, [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE], wintypes.BOOL),
            (self.user.GetPropW, [wintypes.HWND, wintypes.LPCWSTR], wintypes.HANDLE),
            (self.user.RemovePropW, [wintypes.HWND, wintypes.LPCWSTR], wintypes.HANDLE),
            (self.user.GetWindowTextW, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.user.GetWindowThreadProcessId,
             [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            (self.user.AllowSetForegroundWindow, [wintypes.DWORD], wintypes.BOOL),
            (self.user.SetForegroundWindow, [wintypes.HWND], wintypes.BOOL),
            (self.user.IsIconic, [wintypes.HWND], wintypes.BOOL),
            (self.user.ShowWindowAsync, [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            (self.user.MessageBoxW,
             [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT], ctypes.c_int),
        )
        for function, arguments, result in signatures:
            function.argtypes = arguments
            function.restype = result

    @staticmethod
    def error(operation):
        code = ctypes.get_last_error()
        return DesktopInstanceError(code, f"{operation}: {ctypes.FormatError(code).strip()}")

    def windows(self):
        result = []

        @self.callback_type
        def collect(hwnd, _):
            result.append(hwnd)
            return True

        if not self.user.EnumWindows(collect, 0):
            raise self.error("EnumWindows")
        return result

    def process_id(self, hwnd):
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def legacy_window(self):
        """Recognize already-visible older official executables on this desktop.

        This is compatibility assistance, not the exclusion mechanism. Older
        binaries do not participate in the mutex and cannot be retrofitted in
        other sessions. Both image name and title must match; diagnostic windows
        of the current distribution are explicitly marked and excluded.
        """
        for hwnd in self.windows():
            if self.user.GetPropW(hwnd, _DIAGNOSTIC_PROPERTY):
                continue
            title = ctypes.create_unicode_buffer(256)
            self.user.GetWindowTextW(hwnd, title, len(title))
            if title.value not in _COMPATIBLE_WINDOW_TITLES:
                continue
            pid = self.process_id(hwnd)
            if not pid or pid == os.getpid():
                continue
            process = self.kernel.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not process:
                continue
            try:
                path = ctypes.create_unicode_buffer(32768)
                length = wintypes.DWORD(len(path))
                if self.kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(length)):
                    if path.value.replace("/", "\\").rsplit("\\", 1)[-1].casefold() in _COMPATIBLE_IMAGE_NAMES:
                        return hwnd
            finally:
                self.kernel.CloseHandle(process)
        return None


class DesktopInstance:
    """Owned handles, held until the interactive application's event loop ends."""

    def __init__(self, namespace, api=None):
        self._namespace = namespace
        self._api = api
        self._mutex = self._event = self._timer = self._window = None
        self._legacy_hwnd = None
        self._activation_thread = None
        self._activation_stop = threading.Event()
        self._closed = False
        self.is_primary = False
        self.activation_error = None

    @property
    def _event_name(self):
        return "Global\\" + self._namespace + ".Activate"

    @property
    def _window_property(self):
        return self._namespace + ".Window"

    def _allow_foreground(self):
        # The launching process can transfer its foreground permission only to
        # the identified owner. Windows may still deny focus, or the owner may
        # be in another user's session; neither case permits a second instance.
        try:
            for hwnd in self._api.windows():
                marker_pid = self._api.user.GetPropW(hwnd, self._window_property)
                if marker_pid and marker_pid == self._api.process_id(hwnd):
                    self._api.user.AllowSetForegroundWindow(marker_pid)
                    return
        except OSError:
            pass

    def request_activation(self, *, timeout=2.0) -> bool:
        """Signal an existing owner, including while its UI is still loading."""
        if self._closed or self.is_primary or self._api is None:
            return False
        if self._legacy_hwnd:
            pid = self._api.process_id(self._legacy_hwnd)
            self._api.user.AllowSetForegroundWindow(pid)
            if self._api.user.IsIconic(self._legacy_hwnd):
                self._api.user.ShowWindowAsync(self._legacy_hwnd, 9)  # SW_RESTORE
            self._api.user.SetForegroundWindow(self._legacy_hwnd)
            return True
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            event = self._api.kernel.OpenEventW(_EVENT_MODIFY_STATE, False, self._event_name)
            if event:
                try:
                    self._allow_foreground()
                    if not self._api.kernel.SetEvent(event):
                        self.activation_error = self._api.error("SetEvent")
                        return False
                    return True
                finally:
                    self._api.kernel.CloseHandle(event)
            error = self._api.error("OpenEventW")
            if error.errno != _ERROR_FILE_NOT_FOUND or time.monotonic() >= deadline:
                self.activation_error = error
                return False
            # The first process may have acquired the mutex but not created its
            # event yet. This bounded retry has no dependency on Qt startup time.
            time.sleep(0.025)

    def consume_activation(self) -> bool:
        """Consume one request; called only by the owner's Qt timer."""
        if not self.is_primary or not self._event or self._closed:
            return False
        result = self._api.kernel.WaitForSingleObject(self._event, 0)
        if result == _WAIT_FAILED:
            raise self._api.error("WaitForSingleObject(activation)")
        return result == _WAIT_OBJECT_0

    def attach_qt_window(self, window):
        """Restore and activate the owner on the UI thread without changing modes."""
        if not self.is_primary or self._closed or self._api is None:
            return None
        from PySide6.QtCore import QTimer, Qt

        self._window = window
        self._api.user.SetPropW(int(window.winId()), self._window_property, os.getpid())
        timer = QTimer(window)
        timer.setInterval(100)

        def poll():
            if not self.consume_activation():
                return
            if window.isMinimized():
                # Removing only the minimized flag preserves maximized/fullscreen
                # state and geometry, unlike showNormal().
                window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
            window.show()
            window.raise_()
            window.activateWindow()
            if window.windowHandle() is not None:
                window.windowHandle().requestActivate()

        timer.timeout.connect(poll)
        timer.start()
        self._timer = timer
        return timer

    def attach_web_dashboard(self, index_path):
        """Use the same activation event for the browser-based interactive mode.

        Browser focus is best-effort (including Windows foreground restrictions).
        Repeated launches never open another browser window or create a session.
        """
        if not self.is_primary or self._closed or self._api is None:
            return
        match = re.search(r"<title>(.*?)</title>", index_path.read_text(encoding="utf-8"), re.DOTALL)
        if match is None:
            return
        from html import unescape
        title = unescape(match.group(1))

        def focus_dashboard():
            for hwnd in self._api.windows():
                buffer = ctypes.create_unicode_buffer(512)
                self._api.user.GetWindowTextW(hwnd, buffer, len(buffer))
                if buffer.value == title or buffer.value.startswith(title + " - "):
                    pid = self._api.process_id(hwnd)
                    self._api.user.AllowSetForegroundWindow(pid)
                    if self._api.user.IsIconic(hwnd):
                        self._api.user.ShowWindowAsync(hwnd, 9)
                    self._api.user.SetForegroundWindow(hwnd)
                    return True
            return False

        def poll():
            pending_until = 0.0
            while not self._activation_stop.wait(0.1):
                try:
                    if self.consume_activation():
                        pending_until = time.monotonic() + 10
                    # The initial browser window may still be opening when the
                    # second launch arrives. Retain its request while it loads.
                    if pending_until and focus_dashboard():
                        pending_until = 0
                    if pending_until < time.monotonic():
                        pending_until = 0
                except OSError:
                    return

        self._activation_thread = threading.Thread(target=poll, name="orion-desktop-activation", daemon=True)
        self._activation_thread.start()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._activation_stop.set()
        if self._activation_thread is not None:
            self._activation_thread.join(timeout=1)
        if self._timer is not None:
            try:
                self._timer.stop()
            except RuntimeError:  # Qt may already have destroyed its parent.
                pass
        if self._window is not None:
            try:
                self._api.user.RemovePropW(int(self._window.winId()), self._window_property)
            except RuntimeError:
                pass
        if self._api is not None:
            if self._event:
                self._api.kernel.CloseHandle(self._event)
                self._event = None
            if self._mutex:
                # Ownership belongs to the launching/UI thread. Both entry
                # points retain and release this guard on that same thread.
                self._api.kernel.ReleaseMutex(self._mutex)
                self._api.kernel.CloseHandle(self._mutex)
                self._mutex = None
            if self.is_primary:
                with _registry_lock:
                    _active_names.discard(self._namespace)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def acquire_desktop_instance(*, _namespace=None) -> DesktopInstance:
    """Acquire Windows machine ownership atomically; never fall back on failure.

    The private namespace injection is solely for isolated multiprocess tests;
    production entry points always use the fixed product identity. The Windows
    guard leaves other platforms' existing application behavior unchanged.
    """
    namespace = _PRODUCT_NAMESPACE if _namespace is None else _namespace
    if os.name != "nt":
        result = DesktopInstance(namespace)
        result.is_primary = True
        return result
    api = _WindowsApi()
    result = DesktopInstance(namespace, api)
    with _registry_lock:
        # Windows mutexes are recursive for the same thread; an in-process
        # second acquisition must not create a second application window.
        if namespace in _active_names:
            return result
        mutex = api.kernel.CreateMutexW(None, False, "Global\\" + namespace + ".Owner")
        if not mutex:
            raise api.error("CreateMutexW(Global ORION-Q)")
        wait = api.kernel.WaitForSingleObject(mutex, 0)
        if wait == _WAIT_TIMEOUT:
            api.kernel.CloseHandle(mutex)
            return result
        if wait not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
            error = api.error("WaitForSingleObject(ownership)")
            api.kernel.CloseHandle(mutex)
            raise error
        # A killed owner can leave an abandoned mutex while another process is
        # briefly holding a handle. Waiting atomically recovers it in that case.
        result._mutex = mutex
        try:
            if _namespace is None:
                result._legacy_hwnd = api.legacy_window()
                if result._legacy_hwnd:
                    api.kernel.ReleaseMutex(mutex)
                    api.kernel.CloseHandle(mutex)
                    result._mutex = None
                    return result
            event = api.kernel.CreateEventW(None, False, False, result._event_name)
            if not event:
                raise api.error("CreateEventW(Global ORION-Q)")
            result._event = event
            if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
                if not api.kernel.ResetEvent(event):
                    raise api.error("ResetEvent")
            result.is_primary = True
            _active_names.add(namespace)
        except BaseException:
            # No registry entry exists yet, so cleanup cannot re-enter its lock.
            if result._event:
                api.kernel.CloseHandle(result._event)
            api.kernel.ReleaseMutex(mutex)
            api.kernel.CloseHandle(mutex)
            raise
    return result


def mark_isolated_diagnostic_window(window):
    """Exclude a deliberately isolated diagnostic from old-binary compatibility."""
    if os.name == "nt":
        _WindowsApi().user.SetPropW(int(window.winId()), _DIAGNOSTIC_PROPERTY, 1)


def report_instance_error(error):
    """Make a failed ownership check visible in console-less Windows releases."""
    message = (
        f"O {PRODUCT_NAME} não pôde abrir outra janela nesta máquina. "
        "Se ele já estiver em execução em outra sessão do Windows, use a sessão original.\n\n"
        f"Detalhe: {error}"
    )
    print(f"{PRODUCT_NAME}: {message}", file=sys.stderr)
    if os.name == "nt" and getattr(sys, "frozen", False):
        _WindowsApi().user.MessageBoxW(None, message, PRODUCT_NAME, 0x30)
