"""One stable UI plug with generation-guarded, mutually exclusive inputs."""
from PySide6.QtCore import Signal
from dataclasses import replace
from .capabilities import SourceCapabilities
from .providers import DataProvider
from .input_sources import empty_snapshot


class SwitchableProvider(DataProvider):
    busy_changed = Signal(bool)
    error = Signal(str)
    progress = Signal(str)
    source_changed = Signal(str)

    def __init__(self, initial):
        super().__init__()
        self.active = initial
        self._generation, self._connections = 0, []
        self.busy, self.running = False, False
        self._connect()

    @property
    def is_synthetic(self):
        return getattr(self.active, "is_synthetic", False)

    @property
    def is_demo(self):
        """True for the demonstration: the real service pipeline on simulated input."""
        return getattr(self.active, "is_demo", False) is True

    @property
    def capabilities(self):
        return self.active.capabilities if self.running else SourceCapabilities()

    def _presentation_capabilities(self):
        # The cached, committed frame is sufficient for UI affordances. Sources
        # declaring this contract authorize each dispatched operation again in
        # their worker; the public getter remains fresh for service callers.
        if getattr(self.active, "dispatch_validate_backend_permissions", False) is True:
            return self._snap.capabilities if self.running and self._snap is not None else SourceCapabilities()
        return self.capabilities

    @property
    def is_read_only(self):
        return getattr(self.active, "is_read_only", False)

    @property
    def source_label(self):
        return "SYNTHETIC · UI FIXTURE" if self.is_synthetic else getattr(self.active, "source_label", "SERVICE · MIXED EVIDENCE / SIMULATION")

    def _connect(self):
        generation = self._generation
        def guard(callback):
            def receive(value):
                if generation == self._generation:
                    callback(value)
            return receive
        def snapshot(value):
            # The source has already authorized and committed this frame and
            # its capabilities. Forwarding to Qt must not repeat backend IO.
            self._snap = value
            self.snapshot_changed.emit(self._snap)
        def busy(value):
            self.busy = value
            self.busy_changed.emit(value)
        def error(message):
            if self._snap is not None:
                self._snap = replace(self._snap, capabilities=self._presentation_capabilities())
                self.snapshot_changed.emit(self._snap)
            self.error.emit(message)
        for name, callback in (("snapshot_changed", snapshot), ("busy_changed", busy),
                               ("error", error), ("progress", self.progress.emit),
                               ("loading_progress", self.loading_progress.emit),
                               ("source_changed", self.source_changed.emit),
                               ("export_completed", self.export_completed.emit)):
            signal = getattr(self.active, name, None)
            if signal is not None:
                slot = guard(callback)
                signal.connect(slot)
                self._connections.append((signal, slot))

    def start(self):
        if self.running:
            return
        if not self._connections:
            self._connect()
        self.running = True
        try:
            self.active.start()
        except Exception:
            self.active.stop()
            self.running = False
            raise

    def stop(self):
        self._generation += 1
        for signal, slot in self._connections:
            signal.disconnect(slot)
        self._connections.clear()
        self.active.stop()
        self.running = False

    def switch(self, factory):
        if self.busy:
            raise RuntimeError("Finish or cancel the current operation before switching inputs")
        candidate = factory()  # Failed construction leaves the existing input intact.
        if not isinstance(candidate, DataProvider) or candidate is self.active:
            raise TypeError("source factory must create a fresh DataProvider")
        was_running = self.running
        self.stop()
        self.active = candidate
        kind = "synthetic" if self.is_synthetic else "measured" if self.is_read_only else \
            "demo" if self.is_demo else "service"
        self._snap = empty_snapshot(self.source_label, "Switching input; previous values cleared", kind)
        self._connect()
        self.source_changed.emit(self.source_label)
        self.snapshot_changed.emit(self._snap)
        if was_running:
            self.start()

    def dispatch(self, verb, target=""):
        verb = "export_record" if verb == "export" else verb
        self._presentation_capabilities().require(verb)
        if self.busy and verb != "cancel":
            raise RuntimeError("Finish or cancel the current operation before issuing another command")
        dispatch = getattr(self.active, "dispatch", None)
        if not callable(dispatch):
            if verb not in {"select_node", "run_scenario", "replay_model", "export_record", "acknowledge", "recalibrate", "reload", "set_decision_source"}:
                raise ValueError("active input does not support commands")
            handler = getattr(self.active, verb, None)
            if not callable(handler):
                raise ValueError("active input does not support commands")
            return handler(target)
        return dispatch(verb, target)
