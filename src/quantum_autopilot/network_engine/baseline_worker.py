"""Run the existing SO(3) baseline in a local process for interactive clients.

The controller, samples and update order are unchanged. Only its execution
location differs: thousands of tiny NumPy operations otherwise contend with
Qt's Python paint callbacks for the parent interpreter. No journal, optional
archive, Qt widget or physical executor is opened by this worker.
"""
from __future__ import annotations

from contextlib import contextmanager
import multiprocessing
import threading

import numpy as np

from quantum_autopilot.control_plane import polarisation_loop as pol

_context = threading.local()
_trusted_proposals = {kind: kind.propose for kind in
                      (pol.AlphaBeta, pol.Persistence, pol.NoCompensation, pol.LearnedCheckpoint)}


def _calculate(request):
    times, measured, covariates, states, error_settings = request
    np.seterr(**error_settings)
    results = {}
    for key, state in states.items():
        alpha, beta, horizon, rotation, velocity, previous = state
        controller = pol.AlphaBeta(0, 0, horizon)
        controller.alpha, controller.beta = alpha, beta
        controller._state = rotation
        controller._velocity = velocity
        controller._previous_time = previous
        proposals = np.full((len(times), 3, 3), np.nan)
        reasons = [None] * len(times)
        for step in range(len(times)):
            try:
                proposals[step] = controller.propose(float(times[step]), measured[step], covariates[step])
            except (ValueError, ArithmeticError) as exc:
                reasons[step] = f"controller_error:{type(exc).__name__}"
        results[key] = (proposals, reasons, controller._state, controller._velocity, controller._previous_time)
    return results


def _worker(connection):
    try:
        while True:
            try:
                request = connection.recv()
            except EOFError:
                return
            if request is None:
                return
            try:
                connection.send((True, _calculate(request)))
            except Exception as exc:
                connection.send((False, f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


@contextmanager
def use_baseline_worker(worker):
    previous = getattr(_context, "worker", None)
    _context.worker = worker
    try:
        yield
    finally:
        _context.worker = previous


def batch_proposals(controllers, times, measured, covariates):
    """Return None outside an interactive worker context; preserve custom controllers."""
    worker = getattr(_context, "worker", None)
    if worker is None:
        return None
    # Only the built-in, input-preserving controllers may be reordered into
    # independent batches. Custom controllers keep their original interleaving.
    if any(type(controller) not in _trusted_proposals or "propose" in controller.__dict__
           or type(controller).propose is not _trusted_proposals[type(controller)]
           for controller in controllers.values()):
        return None
    # NumPy callback/log handlers belong to the calling process. Leave their
    # execution in place rather than losing a handler across the process boundary.
    if any(policy in ("call", "log") for policy in np.geterr().values()):
        return None
    candidates = {key: controller for key, controller in controllers.items() if type(controller) is pol.AlphaBeta}
    if not candidates or len({id(controller) for controller in controllers.values()}) != len(controllers):
        return None
    return worker.propose(candidates, times, measured, covariates)


class BaselineWorker:
    """One lazily started process, shared by a provider's serialized worker threads."""

    def __init__(self):
        self._connection = self._process = None
        self._lock = threading.Lock()
        self._closed = False

    @property
    def process_id(self):
        return self._process.pid if self._process is not None else None

    def _start(self):
        context = multiprocessing.get_context("spawn")
        connection, child = context.Pipe()
        process = context.Process(target=_worker, args=(child,), name="orion-so3-baseline", daemon=True)
        try:
            process.start()
        except BaseException:
            connection.close()
            child.close()
            raise
        child.close()
        self._connection, self._process = connection, process

    def propose(self, controllers, times, measured, covariates):
        states = {key: (controller.alpha, controller.beta, controller.horizon_s,
                        controller._state, controller._velocity, controller._previous_time)
                  for key, controller in controllers.items()}
        with self._lock:
            if self._closed:
                raise RuntimeError("SO(3) baseline worker is closed")
            if self._process is None:
                self._start()
            if not self._process.is_alive():
                raise RuntimeError("SO(3) baseline worker exited unexpectedly")
            self._connection.send((times, measured, covariates, states, np.geterr()))
            if not self._connection.poll(30):
                raise TimeoutError("SO(3) baseline worker did not return its batch")
            ok, results = self._connection.recv()
            if not ok:
                raise RuntimeError(results)
        # Publish only complete, validated results. The caller owns the engine lock.
        if set(results) != set(controllers):
            raise RuntimeError("SO(3) baseline worker returned different controllers")
        for key, result in results.items():
            proposals, reasons, _rotation, velocity, _previous = result
            if proposals.shape != (len(times), 3, 3) or len(reasons) != len(times) or velocity.shape != (3,):
                raise RuntimeError("SO(3) baseline worker returned an invalid batch")
        for key, (_proposals, _reasons, rotation, velocity, previous) in results.items():
            controller = controllers[key]
            controller._state, controller._velocity, controller._previous_time = rotation, velocity, previous
        return {key: (result[0], result[1]) for key, result in results.items()}

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            connection, process = self._connection, self._process
            if connection is not None:
                try:
                    if process.is_alive():
                        connection.send(None)
                except (BrokenPipeError, EOFError, OSError):
                    pass
                finally:
                    connection.close()
            if process is not None:
                process.join(timeout=5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
                process.close()
            self._connection = self._process = None


__all__ = ["BaselineWorker", "batch_proposals", "use_baseline_worker"]
