"""Private Q05 sessions with cancellation checks before simulation commit."""
from threading import Event

from quantum_autopilot.control_plane.policy_preparation import PolicyPreparationSession
from quantum_autopilot.control_plane.policy_activation import activate, activation_status
from quantum_autopilot.control_plane.policy_recovery import inspect_recovery, recover, prepare_rollback
from .policy_binding_authority import PolicyBindingAuthority


class PolicyReviewBinding:
    def __init__(self, service, *, source=None):
        self.service = service
        self.authority = PolicyBindingAuthority(service, source=source)
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
        methods = {
            'inspect': 'policy_preparation_context', 'prepare': 'prepare_policy_change',
            'review': 'review_policy_change', 'approve': 'approve_policy_change',
            'activate': 'activate_policy_change', 'read_active': 'policy_activation_status',
            'prepare_rollback': 'prepare_policy_rollback', 'inspect_recovery': 'policy_recovery_context',
            'recover': 'recover_policy_change',
        }
        if operation not in methods:
            raise ValueError('unsupported review operation')
        try:
            return self.authority.execute(methods[operation],
                                          lambda validate: self._run(operation, arguments, validate))
        except Exception:
            self.discard_recovery()
            if self.session is not None:
                self.session.invalidate()
            raise

    def dispatch(self, operation, *arguments):
        return self.authority.dispatch(self.run, operation, *arguments)

    def _run(self, operation, arguments, validate_authority):
        def validate():
            self._validate_source()
            validate_authority()

        try:
            runtime = self._runtime()
            validate_authority()
            extra = {}
            if operation == 'read_active':
                with runtime._cycle_lock:
                    validate()
                    result = activation_status(runtime)
            elif operation == 'inspect_recovery':
                self.discard_recovery()
                with runtime._cycle_lock:
                    validate()
                    result = inspect_recovery(runtime)
                    with runtime._policy_recovery_lock:
                        self._recovery_selection = runtime._policy_recovery_selection
            elif operation == 'recover':
                if self._recovery_selection is None:
                    raise ValueError('inspect recovery in this panel first')
                with runtime._cycle_lock:
                    validate()
                    with runtime._policy_recovery_lock:
                        if runtime._policy_recovery_selection is not self._recovery_selection:
                            raise ValueError('recovery selection replaced; inspect again in this panel')
                    result = recover(runtime, *arguments, validate_source=validate)
                # Recovery invalidates every prior approval, including other panels.
                # Announce only our own generation change so this panel can retain
                # its completed receipt without accepting unrelated source changes.
                with self.service._controller_lock:
                    current = (not self.cancelled.is_set()
                               and self.generation == self.service._policy_preparation_generation
                               and self.service._controller_runtime is runtime)
                    if current:
                        self.completed_recovery_generation = self.generation + 1
                    self.service._invalidate_policy_preparation()
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
                with self.session._lock, runtime._cycle_lock:
                    validate()
                    result = prepare_rollback(self.session, *arguments)
                    extra['context'] = self.session.inspect()
            else:
                if self.session is None:
                    if operation != 'inspect':
                        raise ValueError('load the baseline first')
                    self.session = PolicyPreparationSession(runtime)
                if operation not in {'inspect', 'prepare', 'review', 'approve', 'activate'}:
                    raise ValueError('unsupported review operation')
                with self.session._lock, runtime._cycle_lock:
                    validate()
                    if operation == 'activate':
                        result = activate(self.session, *arguments, validate_source=validate)
                    else:
                        result = getattr(self.session, operation)(*arguments)
            self._runtime()
            return {'operation': operation, 'receipt': result, **extra}
        except Exception:
            self.discard_recovery()
            if self.session is not None:
                self.session.invalidate()
            raise
