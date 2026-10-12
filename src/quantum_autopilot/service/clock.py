"""A bounded server-owned processing clock, independent of desktop clients.

One authorized service call processes one existing network tick. Scheduling
never synthesizes observations, skips protocol ticks, or queues catch-up work.
Wall-clock lateness slows counted network time and remains observable.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import Context
import math
import re
from threading import Condition, Lock, RLock, Thread, current_thread
import time
from weakref import WeakKeyDictionary


_owners = WeakKeyDictionary()
_owners_lock = Lock()


def _seconds(value, name, *, minimum=0.001, maximum=3600.0):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not minimum <= value <= maximum):
        raise ValueError(f"{name} must be finite seconds in [{minimum}, {maximum}]")
    return float(value)


class HeadlessClock:
    """Own a single processing thread for an operational service instance.

    ``hold`` is an exclusive, bounded handoff for a procedure that already
    advances the engine, such as blocking recalibration. It grants no authority:
    that procedure must still call its protected service boundary. The GUI is
    only an observer and cannot stop this clock by disconnecting.
    """

    def __init__(self, service, *, period_s=1.0, stop_timeout_s=10.0):
        security = getattr(service, "security", None)
        if (getattr(security, "identity_profile", None) != "service"
                or getattr(security, "profile", None) != "operational"):
            raise ValueError("headless clock requires an operational service identity")
        self.service = service
        self.period_s = _seconds(period_s, "period_s")
        self.stop_timeout_s = _seconds(stop_timeout_s, "stop_timeout_s", maximum=60.0)
        self._condition = Condition(RLock())
        self._hold_gate = RLock()
        self._thread = None
        self._stop_requested = False
        self._in_flight = False
        self._holds = 0
        self._hold_reasons = []
        self._hold_started = None
        self._hold_seconds = 0.0
        self._hold_count = 0
        self._resume_generation = 0
        self._state = "created"
        self._steps = 0
        self._attempts = 0
        self._missed_deadlines = 0
        self._overload_count = 0
        self._stop_timeout_count = 0
        self._last_duration_s = None
        self._last_lateness_s = None
        self._last_error_type = None
        self._last_metadata = {}
        self._started_at = None
        self._ended_at = None

    def start(self):
        with self._condition:
            if self._thread is not None or self._state != "created":
                raise RuntimeError("headless clock may only be started once")
            with _owners_lock:
                if self.service in _owners:
                    raise RuntimeError("this service already has a processing clock")
                _owners[self.service] = self
            self._started_at = time.monotonic()
            self._state = "paused" if self._holds else "waiting"
            # Do not inherit an HTTP caller's ContextVars or authority. Every
            # step resolves the process binding through the service boundary.
            self._thread = Thread(target=lambda: Context().run(self._run),
                                  name="orion-core-clock", daemon=False)
            try:
                self._thread.start()
            except BaseException:
                with _owners_lock:
                    _owners.pop(self.service, None)
                self._thread = None
                self._state = "created"
                raise
        return self

    def stop(self, *, timeout_s=None):
        timeout = self.stop_timeout_s if timeout_s is None else _seconds(timeout_s, "timeout_s", maximum=60.0)
        deadline = time.monotonic() + timeout
        with self._condition:
            thread = self._thread
            if thread is current_thread():
                raise RuntimeError("a clock worker cannot join itself")
            self._stop_requested = True
            if self._state not in {"blocked", "stopped"}:
                self._state = "stopping"
            self._condition.notify_all()
        if thread is not None:
            thread.join(max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                with self._condition:
                    self._stop_timeout_count += 1
                raise TimeoutError("core processing has not stopped; resources must remain open")
        with self._condition:
            while self._holds:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._stop_timeout_count += 1
                    raise TimeoutError("a procedure still owns the clock; resources must remain open")
                self._condition.wait(remaining)
            if self._state != "blocked":
                self._state = "stopped"
            self._ended_at = self._ended_at or time.monotonic()
        return self.status()

    @contextmanager
    def hold(self, reason, *, timeout_s=10.0):
        """Wait for an in-flight step and serialize temporary clock ownership.

        A timeout never enters the caller's procedure. Cancellation and stop
        remain independent of the hold; no queued processing work accumulates.
        """
        if not isinstance(reason, str) or re.fullmatch(r"[A-Za-z0-9 _:-]{1,64}", reason) is None:
            raise ValueError("a bounded non-secret hold reason is required")
        timeout = _seconds(timeout_s, "timeout_s", maximum=60.0)
        deadline = time.monotonic() + timeout
        if not self._hold_gate.acquire(timeout=timeout):
            raise TimeoutError("another procedure owns the processing clock")
        registered = False
        try:
            with self._condition:
                if self._state in {"blocked", "stopping", "stopped"}:
                    raise RuntimeError("processing clock is not available for a procedure")
                if self._holds == 0:
                    self._hold_started = time.monotonic()
                self._holds += 1
                self._hold_reasons.append(reason)
                registered = True
                self._condition.notify_all()
                while self._in_flight:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("in-flight processing has not yielded the clock")
                    self._condition.wait(remaining)
                if self._state in {"blocked", "stopping", "stopped"}:
                    raise RuntimeError("processing clock stopped during the handoff")
                self._hold_count += 1
                self._state = "paused"
            yield
        finally:
            if registered:
                with self._condition:
                    self._holds -= 1
                    self._hold_reasons.pop()
                    if self._holds == 0:
                        self._hold_seconds += time.monotonic() - self._hold_started
                        self._hold_started = None
                        self._resume_generation += 1
                        if self._state == "paused":
                            self._state = "waiting" if self._thread else "created"
                    self._condition.notify_all()
            self._hold_gate.release()

    def status(self):
        with self._condition:
            now = time.monotonic()
            paused = self._hold_seconds + (now - self._hold_started if self._hold_started is not None else 0.0)
            wall_s = None if self._started_at is None else (self._ended_at or now) - self._started_at
            return {"schema": "orion.core-clock.v1", "state": self._state,
                    "active": self._thread is not None and self._thread.is_alive(),
                    "in_flight": self._in_flight, "period_s": self.period_s,
                    "virtual_tick_s": 1.0, "nominal_time_scale": 1.0 / self.period_s,
                    "time_model": "one-counted-network-second-per-processed-tick",
                    "catch_up_queue_size": 0, "attempts": self._attempts, "processed_steps": self._steps,
                    "missed_scheduler_deadlines": self._missed_deadlines,
                    "overload_count": self._overload_count,
                    "last_duration_s": self._last_duration_s, "last_lateness_s": self._last_lateness_s,
                    "last_error_type": self._last_error_type, "wall_elapsed_s": wall_s,
                    "hold_count": self._hold_count, "hold_depth": self._holds,
                    "hold_reasons": list(self._hold_reasons), "hold_duration_s": paused,
                    "stop_timeout_count": self._stop_timeout_count, **self._last_metadata}

    def _run(self):
        deadline = time.monotonic() + self.period_s
        generation = self._resume_generation
        try:
            while True:
                # Maintain the process lease even when a long cadence or an
                # exclusive handoff delays the next scientific tick. This does
                # not renew equipment authority or advance the simulation.
                maintenance_delay = None
                if getattr(self.service.security, "process_identity_renewal", False):
                    try:
                        maintenance_delay = self.service.security.process_identity_maintenance_delay()
                        if maintenance_delay <= 0:
                            self.service.security.maintain_process_identity()
                            maintenance_delay = self.service.security.process_identity_maintenance_delay()
                    except Exception as error:
                        with self._condition:
                            self._last_error_type = type(error).__name__
                            self._state = "blocked"
                            self._condition.notify_all()
                        break
                with self._condition:
                    if self._stop_requested:
                        break
                    if generation != self._resume_generation:
                        generation = self._resume_generation
                        deadline = time.monotonic() + self.period_s
                    if self._holds:
                        self._state = "paused"
                        self._condition.wait(maintenance_delay)
                        continue
                    now = time.monotonic()
                    if now < deadline:
                        self._state = "waiting"
                        delay = deadline - now
                        self._condition.wait(min(delay, maintenance_delay) if maintenance_delay is not None else delay)
                        continue
                    self._state = "running"
                    self._in_flight = True
                    self._attempts += 1
                    started = time.monotonic()
                    lateness = max(0.0, started - deadline)
                try:
                    metadata = self.service.network_step(metadata_only=True)
                    selected = {"last_tick": metadata["tick"],
                                "last_network_time_s": metadata["network_time_s"],
                                "last_protocol_clock_s": metadata.get("protocol_clock_s"),
                                "last_protocol_backlog_s": metadata.get("protocol_backlog_s"),
                                "last_publication_revision": metadata["publication_revision"],
                                "last_source_kind": metadata["source"]["kind"]}
                except Exception as error:
                    with self._condition:
                        self._last_error_type = type(error).__name__
                        self._last_duration_s = time.monotonic() - started
                        self._last_lateness_s = lateness
                        self._state = "blocked"
                        self._in_flight = False
                        self._condition.notify_all()
                    break
                finished = time.monotonic()
                with self._condition:
                    duration = finished - started
                    self._steps += 1
                    self._last_duration_s, self._last_lateness_s = duration, lateness
                    self._last_metadata = selected
                    self._in_flight = False
                    deadline += self.period_s
                    missed = 0
                    if deadline <= finished:
                        missed = math.floor((finished - deadline) / self.period_s) + 1
                        deadline += missed * self.period_s
                    self._missed_deadlines += missed
                    if duration > self.period_s or lateness > self.period_s:
                        self._overload_count += 1
                    self._condition.notify_all()
        finally:
            with self._condition:
                self._in_flight = False
                self._ended_at = time.monotonic()
                if self._state != "blocked":
                    self._state = "stopping" if self._holds else "stopped"
                self._condition.notify_all()
            with _owners_lock:
                if _owners.get(self.service) is self:
                    _owners.pop(self.service, None)
