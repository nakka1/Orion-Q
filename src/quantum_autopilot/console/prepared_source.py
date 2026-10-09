"""Prepare pure service state off Qt, then attach a provider on its owner thread."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from contextvars import copy_context
from dataclasses import replace

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from .capabilities import SourceCapabilities
from .input_sources import empty_snapshot
from .providers import DataProvider


_UNSPECIFIED = object()
_LABELS = {
    "service": "SERVICE · MIXED EVIDENCE / SIMULATION",
    "demo": "DEMONSTRATION · SIMULATED INPUT / REAL PIPELINE",
}


class DeferredProvider(DataProvider):
    """A cheap Qt source whose ``prepare`` callback must create no Qt objects.

    ``create(prepared)`` runs on the owning Qt thread and returns a fresh
    DataProvider. Preparation and creation use the same explicit security
    principal, including an anonymous scope; no worker falls back to OS roles.
    ``snapshot_steps`` declares the delegate's measured initial-read contract.
    """

    busy_changed = Signal(bool)
    error = Signal(str)
    progress = Signal(str)
    source_ready = Signal(object)
    source_failure = Signal(str)
    source_changed = Signal(str)

    def __init__(self, prepare, create, *, kind="service", source_label=None,
                 security=None, principal=_UNSPECIFIED, snapshot_steps=5, state_root=None, parent=None):
        super().__init__(parent)
        if not callable(prepare) or not callable(create):
            raise TypeError("prepare and create must be callable")
        if kind not in _LABELS:
            raise ValueError("deferred service kind must be service or demo")
        if not isinstance(snapshot_steps, int) or isinstance(snapshot_steps, bool) or snapshot_steps < 1:
            raise ValueError("snapshot_steps must be a positive integer")
        self._prepare, self._create = prepare, create
        self._kind = kind
        self._source_label = source_label or _LABELS[kind]
        self._state_root = state_root
        self.security = security
        if principal is _UNSPECIFIED:
            if security is None:
                principal = None
            elif security.has_identity_scope():
                principal = security.capture_identity_for_dispatch()
            else:
                principal = security.current_identity()
        self._identity = principal
        self._snapshot_steps = snapshot_steps
        self._completed_steps = 0
        self._delegate = None
        self._delegate_busy = False
        self._ready = False
        self._generation = 0
        self._connections = []
        self._executor = self._future = self._context = None
        self._preparing_identity = None
        self.running = self.busy = False
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._poll_preparation)
        self._snap = self._pending_snapshot("Source has not started.")

    @property
    def delegate(self):
        return self._delegate

    @property
    def ready(self):
        return self.running and self._ready

    @property
    def loading_state(self):
        """Measured progress can be restored when a loading view is reopened."""
        return {"completed": self._completed_steps if self.running else 0,
                "total": 2 + self._snapshot_steps}

    @property
    def is_demo(self):
        return self._kind == "demo"

    @property
    def is_synthetic(self):
        return False

    @property
    def is_read_only(self):
        return False

    @property
    def source_label(self):
        return getattr(self._delegate, "source_label", self._source_label)

    @property
    def dispatch_validate_backend_permissions(self):
        return getattr(self._delegate, "dispatch_validate_backend_permissions", False) is True

    @property
    def capabilities(self):
        if self._delegate is not None and self.running and self._ready:
            return self._delegate.capabilities
        return self._pending_capabilities()

    @property
    def service(self):
        return getattr(self._delegate, "service", None) if self.ready else None

    @property
    def state_root(self):
        return getattr(self._delegate, "state_root", self._state_root)

    @property
    def has_network_engine(self):
        return getattr(self._delegate, "has_network_engine", False)

    def _pending_capabilities(self):
        return SourceCapabilities(adapter_id="deferred-" + self._kind,
                                  health="unavailable",
                                  note="Source preparation has not committed a snapshot.")

    def _pending_snapshot(self, message):
        return replace(empty_snapshot(self._source_label, message, self._kind),
                       capabilities=self._pending_capabilities())

    def _identity_scope(self, principal):
        return self.security.identity_scope(principal) if self.security is not None else nullcontext()

    def _prepare_service(self, principal):
        with self._identity_scope(principal):
            if self.security is not None:
                self.security.current_identity()  # Reject anonymous/revoked scopes before preparation.
            prepared = self._prepare()
            if isinstance(prepared, QObject):
                raise TypeError("service preparation must not construct a QObject")
            return prepared

    def _set_busy(self, value):
        value = bool(value)
        if value != self.busy:
            self.busy = value
            self.busy_changed.emit(value)

    def _loading(self, completed, message):
        self._completed_steps = max(self._completed_steps, completed)
        self.loading_progress.emit({"completed": self._completed_steps,
                                    "total": 2 + self._snapshot_steps, "message": message})

    def start(self):
        if self.running:
            return
        self._generation += 1
        generation = self._generation
        self.running = True
        self._ready = False
        self._delegate_busy = False
        self._completed_steps = 0
        self._context = copy_context()
        principal = self._identity
        if self.security is not None and self.security.has_identity_scope():
            principal = self.security.capture_identity_for_dispatch()
        self._preparing_identity = principal
        self._identity = principal
        self._snap = self._pending_snapshot("Preparing source service…")
        self._set_busy(True)
        self.snapshot_changed.emit(self._snap)
        if generation != self._generation or not self.running:
            return
        self._loading(0, "Preparing source service…")
        if generation != self._generation or not self.running:
            return
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="console-source-prepare")
        self._future = self._executor.submit(self._context.run, self._prepare_service, principal)
        self._timer.start()

    @Slot()
    def _poll_preparation(self):
        future = self._future
        if not self.running or future is None or not future.done():
            return
        generation = self._generation
        context, principal = self._context, self._preparing_identity
        self._timer.stop()
        self._future = None
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        candidate = None
        try:
            prepared = future.result()
            self._loading(1, "Source service prepared; attaching its provider…")
            if generation != self._generation or not self.running:
                return

            def create():
                with self._identity_scope(principal):
                    if self.security is not None:
                        self._identity = self.security.current_identity()
                    return self._create(prepared)

            candidate = context.run(create)
            if not isinstance(candidate, DataProvider) or candidate is self:
                raise TypeError("create must return a fresh DataProvider")
            if candidate.thread() != self.thread():
                raise TypeError("created provider must belong to the owner Qt thread")
            if generation != self._generation or not self.running:
                candidate.stop()
                candidate.deleteLater()
                return
            if candidate.parent() is None:
                candidate.setParent(self)
            self._delegate = candidate
            self._connect_delegate(generation)
            self._loading(2, "Source provider attached; reading its first snapshot…")
            if generation != self._generation or not self.running:
                return
            candidate.start()
        except Exception as exc:
            if generation == self._generation:
                if candidate is not None and self._delegate is not candidate and isinstance(candidate, DataProvider):
                    candidate.stop()
                    candidate.deleteLater()
                self._fail(type(exc).__name__)
        finally:
            if generation == self._generation:
                self._context = None

    def _connect_delegate(self, generation):
        def guard(callback):
            def receive(value):
                if self.running and generation == self._generation:
                    callback(value)
            return receive

        def snapshot(value):
            self._snap = value
            committed = value.capabilities.health not in {"unavailable", "starting"}
            if committed and not self._ready:
                self._ready = True
                self._loading(2 + self._snapshot_steps, "First source snapshot committed.")
                self._set_busy(self._delegate_busy)
                self.source_ready.emit(self._delegate)
                if self.running and generation == self._generation:
                    self.source_changed.emit(self.source_label)
            if self.running and generation == self._generation:
                self.snapshot_changed.emit(value)

        def busy(value):
            self._delegate_busy = bool(value)
            self._set_busy(not self._ready or value)

        def loading(value):
            if not isinstance(value, dict):
                return
            completed, total = value.get("completed"), value.get("total")
            if (type(completed) is not int or type(total) is not int
                    or total != self._snapshot_steps or not 0 <= completed <= total):
                return
            # The terminal step is committed by snapshot(), not by an early
            # delegate progress signal before Qt receives that snapshot.
            self._loading(2 + min(completed, total - 1), str(value.get("message", "Reading source…")))

        def error(message):
            if self._ready:
                self.error.emit(message)
            else:
                self._fail("initial snapshot unavailable")

        for name, callback in (("snapshot_changed", snapshot), ("busy_changed", busy),
                               ("loading_progress", loading), ("error", error),
                               ("progress", self.progress.emit), ("export_completed", self.export_completed.emit)):
            signal = getattr(self._delegate, name, None)
            if signal is not None:
                slot = guard(callback)
                signal.connect(slot)
                self._connections.append((signal, slot))

    def _fail(self, error_type):
        self._generation += 1
        self.running = False
        self._ready = False
        self._context = None
        self._detach_delegate()
        self._delegate_busy = False
        self._snap = self._pending_snapshot("Source preparation failed.")
        self._set_busy(False)
        self.snapshot_changed.emit(self._snap)
        message = "Source preparation failed (" + error_type + ")."
        self.source_failure.emit(message)
        self.error.emit(message)

    def stop(self):
        self._generation += 1
        self.running = False
        self._ready = False
        self._timer.stop()
        future, self._future = self._future, None
        executor, self._executor = self._executor, None
        if future is not None:
            future.cancel()
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        self._context = None
        self._detach_delegate()
        self._delegate_busy = False
        self._set_busy(False)
        self._snap = self._pending_snapshot("Source stopped.")

    def _detach_delegate(self):
        for signal, slot in self._connections:
            signal.disconnect(slot)
        self._connections.clear()
        delegate, self._delegate = self._delegate, None
        if delegate is not None:
            delegate.stop()
            delegate.deleteLater()

    def _call_delegate(self, name, *args):
        if self._delegate is None or not self.running or not self._ready:
            raise RuntimeError("Source has not committed a snapshot yet")
        callback = getattr(self._delegate, name, None)
        if not callable(callback):
            raise ValueError("Active source does not expose " + name)
        return callback(*args)

    def dispatch(self, verb, target=""):
        return self._call_delegate("dispatch", verb, target)

    def request_refresh(self):
        if not self.running:
            self.start()
            return True
        callback = getattr(self._delegate, "request_refresh", None)
        if callable(callback):
            return callback()
        return False

    def recalibrate(self, target):
        return self.dispatch("recalibrate", target)

    def isolate(self, target):
        return self.dispatch("isolate", target)

    def acknowledge(self, target):
        return self.dispatch("acknowledge", target)

    def export_record(self, scope):
        return self.dispatch("export_record", scope)

    def policy_decision_store_path(self):
        return self._call_delegate("policy_decision_store_path")

    def policy_context_source(self):
        return self._call_delegate("policy_context_source")

    def policy_review_source(self):
        return self._call_delegate("policy_review_source")

    def command_reconciliation_store_path(self):
        return self._call_delegate("command_reconciliation_store_path")

    def temporal_campaign_store_path(self):
        return self._call_delegate("temporal_campaign_store_path")

    def temporal_campaign_history_store_path(self):
        return self._call_delegate("temporal_campaign_history_store_path")


__all__ = ["DeferredProvider"]
