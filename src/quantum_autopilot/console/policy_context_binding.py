"""Private cancellable observation session; GUI cancellation never waits for SQLite."""

from threading import Event

from quantum_autopilot.control_plane.policy_context import PolicyCompatibilitySession
from .policy_binding_authority import PolicyBindingAuthority


class PolicyContextBinding:
    def __init__(self, service, *, source=None):
        self.service = service
        self.authority = PolicyBindingAuthority(service, source=source)
        self.generation = service._policy_preparation_generation
        self.cancelled = Event()
        self.runtime = self.session = None

    def cancel(self):
        self.cancelled.set()

    def _validate(self):
        if (
            self.cancelled.is_set()
            or self.generation != self.service._policy_preparation_generation
            or self.runtime is not self.service._controller_runtime
        ):
            raise ValueError("context source invalidated; load a new context")

    def run(self, operation, *arguments):
        methods = {'inspect': 'policy_compatibility_context', 'observe': 'observe_policy_compatibility'}
        if operation not in methods:
            raise ValueError('unsupported contextual observation operation')
        try:
            return self.authority.execute(methods[operation],
                                          lambda validate: self._run(operation, arguments, validate))
        except Exception:
            if self.session is not None:
                self.session.invalidate()
            raise

    def dispatch(self, operation, *arguments):
        return self.authority.dispatch(self.run, operation, *arguments)

    def _run(self, operation, arguments, validate_authority):
        try:
            with self.service._controller_lock:
                runtime = self.service._controller_runtime
                if runtime is None:
                    raise ValueError("existing simulation runtime required")
                if self.runtime is None:
                    self.runtime = runtime
                self._validate()
            validate_authority()
            if self.session is None:
                if operation != "inspect":
                    raise ValueError("load context in this panel first")
                self.session = PolicyCompatibilitySession(runtime)
            if operation not in {"inspect", "observe"}:
                raise ValueError("unsupported contextual observation operation")
            with self.session._context._lock, runtime._cycle_lock:
                self._validate()
                validate_authority()  # A grant/deadline may change while locks were held elsewhere.
                result = getattr(self.session, operation)(*arguments)
            self._validate()
            return result
        except Exception:
            if self.session is not None:
                self.session.invalidate()
            raise
