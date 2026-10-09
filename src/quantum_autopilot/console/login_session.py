"""Native credential confirmation stays off Qt's interaction thread."""
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal, Slot


class NativeLoginController(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)
    _completed = Signal(object)

    def __init__(self, runtime, principal, resource, parent=None):
        super().__init__(parent)
        self.runtime, self.principal, self.resource = runtime, principal, resource
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="orion-login")
        self._future = None
        self._closed = False
        self._completed.connect(self._deliver)

    @property
    def active(self):
        return self._future is not None

    def submit(self, username, password=None):
        if self._closed or self.active:
            return False
        from quantum_autopilot.security.native_login import authenticate_native_login
        self.busy_changed.emit(True)
        self._future = self._executor.submit(
            authenticate_native_login, self.runtime, self.principal, username, password, self.resource)
        self._future.add_done_callback(self._notify)
        return True

    def _notify(self, future):
        if not self._closed:
            try:
                self._completed.emit(future)
            except RuntimeError:
                pass  # The window was disposed before the worker returned.

    @Slot(object)
    def _deliver(self, future):
        if self._closed or future is not self._future:
            return
        self._future = None
        self.busy_changed.emit(False)
        try:
            identity = future.result()
        except Exception:
            # Exceptions/tracebacks and entered credentials never reach logs or widgets.
            self.failed.emit("Sign-in denied. Check the Windows account, credentials and backend permissions.")
        else:
            self.succeeded.emit(identity)

    def shutdown(self):
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)


__all__ = ["NativeLoginController"]
