"""Garbage collection confined to the UI thread.

CPython's cyclic collector runs on whichever thread crosses the allocation threshold.
The console has worker threads that allocate a lot (the service worker, the
network-engine stepping thread, model checks). When a collection on one of them finds
garbage that contains PySide6 wrappers, Shiboken queues the C++ deletion for the main
thread; if Qt has already deleted the object through its parent, that deferred deletion
is a double free and the process dies with SIGSEGV in an unrelated place.

So the console turns automatic collection off and collects from a timer on the UI
thread instead: the young generation every second, the middle one every ten seconds
and a full collection once a minute. Reference counting is unaffected; only cycles
wait, at most one period.
"""
from __future__ import annotations

import gc

from PySide6.QtCore import QObject, QTimer

YOUNG_PERIOD_MS = 1000
MIDDLE_EVERY = 10
FULL_EVERY = 60


class UiThreadCollector(QObject):
    """Owns the collection timer. `start()` on the UI thread; `stop()` restores automatic collection."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._was_enabled = False
        self._count = 0
        self.collections = 0
        self._timer = QTimer(self)
        self._timer.setInterval(YOUNG_PERIOD_MS)
        self._timer.timeout.connect(self.collect)

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        if self.active:
            return
        self._was_enabled = gc.isenabled()
        gc.disable()
        self._timer.start()

    def collect(self) -> None:
        self._count += 1
        generation = 2 if self._count % FULL_EVERY == 0 else 1 if self._count % MIDDLE_EVERY == 0 else 0
        gc.collect(generation)
        self.collections += 1

    def stop(self) -> None:
        if not self.active:
            return
        self._timer.stop()
        gc.collect()
        if self._was_enabled:
            gc.enable()


__all__ = ["UiThreadCollector"]
