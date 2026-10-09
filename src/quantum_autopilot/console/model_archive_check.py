"""Single asynchronous read-only verification with stale-result invalidation."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from PySide6.QtCore import QObject, QTimer, Signal

from ..model_intake.verification import verify_model_archive


class LocalModelJob(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result = None
        self.error = ""
        self._future = None
        self._generation = 0
        self._pending_generation = None
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self.poll)

    @property
    def active(self):
        return self._future is not None

    @property
    def generation(self):
        return self._generation

    def clear(self):
        self._generation += 1
        self.result = None
        self.error = ""
        # A running local read may finish, but it cannot repopulate a closed or
        # differently selected view. Do not enqueue more work until it finishes.
        if self._future is not None:
            self._future.cancel()
        self.changed.emit()

    def submit(self, operation, *arguments):
        if self.active:
            raise ValueError("a local model job is still finishing")
        self.clear()
        self._pending_generation = self._generation
        # Each bounded job retains its caller, including an explicitly anonymous
        # scope. The operation's normal backend guard still freshly authorizes
        # that caller after queueing; no roles or permission decisions are cached.
        context = copy_context()
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model-package-check")
        try:
            self._future = pool.submit(context.run, operation, *arguments)
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__
            self.changed.emit()
            return
        finally:
            # One bounded job, no persistent executor and no callback into a
            # potentially destroyed Qt object. The UI polls the Future instead.
            pool.shutdown(wait=False)
        self._timer.start()
        self.changed.emit()


    def poll(self):
        if self._future is None or not self._future.done():
            return
        future, self._future = self._future, None
        self._timer.stop()
        if self._pending_generation == self._generation:
            try:
                self.result = future.result()
            except Exception as exc:
                self.result = None
                self.error = str(exc) or type(exc).__name__
        self.changed.emit()


class ModelArchiveCheck(LocalModelJob):
    def start(self, model_id, path):
        self.submit(verify_model_archive, model_id, path)
