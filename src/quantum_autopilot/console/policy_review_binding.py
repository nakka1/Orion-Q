"""Private Q05 sessions with cancellation checks before simulation commit."""
from threading import Event

from quantum_autopilot.control_plane.policy_preparation import PolicyPreparationSession
from quantum_autopilot.control_plane.policy_activation import activate, activation_status
from quantum_autopilot.control_plane.policy_recovery import inspect_recovery, recover, prepare_rollback


class PolicyReviewBinding:
    def __init__(self, service):
        self.service = service
        self.generation = service._policy_preparation_generation
        self.cancelled = Event()
        self.session = self.runtime = None
        self._recovery_selection = None
        self.completed_recovery_generation = None

    def discard_recovery(self):
        # Identity comparison avoids deleting a newer selection owned by another panel.
        if self.runtime is not None and self._recovery_selection is not None:
            with self.runtime._policy_recovery_lock:
                if self.runtime._policy_recovery_selection is self._recovery_selection:
                    self.runtime._policy_recovery_selection = None
        self._recovery_selection = None

    def cancel(self):
        # GUI invalidation must never wait on a running SQLite read/runtime lock.
        self.cancelled.set()
        self.discard_recovery()

    def _validate_source(self):
        # No service lock under cycle/session locks: stop stays nonblocking.
        if (self.cancelled.is_set() or self.generation != self.service._policy_preparation_generation
                or self.service._controller_runtime is not self.runtime):
            raise ValueError('policy source invalidated before commit')

    def _runtime(self):
        with self.service._controller_lock:
            if self.cancelled.is_set() or self.generation != self.service._policy_preparation_generation:
                raise ValueError('policy review source invalidated')
            runtime = self.service._controller_runtime
            if runtime is None:
                raise ValueError('initialize the simulation independently before review')
            if self.runtime is not None and self.runtime is not runtime:
                raise ValueError('runtime replaced; load a fresh baseline')
            self.runtime = runtime
            return runtime

    def run(self, operation, *arguments):
        try:
            runtime = self._runtime()
            extra = {}
            if operation == 'read_active':
                result = activation_status(runtime)
            elif operation == 'inspect_recovery':
                self.discard_recovery()
                with runtime._cycle_lock:
                    self._validate_source()
                    result = inspect_recovery(runtime)
                    with runtime._policy_recovery_lock:
                        self._recovery_selection = runtime._policy_recovery_selection
            elif operation == 'recover':
                if self._recovery_selection is None:
                    raise ValueError('inspect recovery in this panel first')
                with runtime._cycle_lock:
                    with runtime._policy_recovery_lock:
                        if runtime._policy_recovery_selection is not self._recovery_selection:
                            raise ValueError('recovery selection replaced; inspect again in this panel')
                    result = recover(runtime, *arguments, validate_source=self._validate_source)
                # Recovery invalidates every prior approval, including other panels.
                # Announce only our own generation change so this panel can retain
                # its completed receipt without accepting unrelated source changes.
                with self.service._controller_lock:
                    current = (not self.cancelled.is_set()
                               and self.generation == self.service._policy_preparation_generation
                               and self.service._controller_runtime is runtime)
                    if current:
                        self.completed_recovery_generation = self.generation + 1
                    self.service.invalidate_policy_preparation()
                    if not current:
                        raise ValueError('recovery committed after source invalidation; read active state again')
                    self.generation = self.service._policy_preparation_generation
                self.discard_recovery()
                if self.session is not None:
                    self.session.invalidate()
                    self.session = None
            elif operation == 'prepare_rollback':
                self.discard_recovery()
                if self.session is not None:
                    self.session.invalidate()
                self.session = PolicyPreparationSession(runtime)
                result = prepare_rollback(self.session, *arguments)
                extra['context'] = self.session.inspect()
            else:
                if self.session is None:
                    if operation != 'inspect':
                        raise ValueError('load the baseline first')
                    self.session = PolicyPreparationSession(runtime)
                if operation not in {'inspect', 'prepare', 'review', 'approve', 'activate'}:
                    raise ValueError('unsupported review operation')
                self._validate_source()
                if operation == 'activate':
                    result = activate(self.session, *arguments, validate_source=self._validate_source)
                else:
                    result = getattr(self.session, operation)(*arguments)
            self._runtime()
            return {'operation': operation, 'receipt': result, **extra}
        except Exception:
            self.discard_recovery()
            if self.session is not None:
                self.session.invalidate()
            raise
