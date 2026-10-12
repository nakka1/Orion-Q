"""Fresh service authority for private policy sessions, never a UI declaration.

Dispatch captures only a candidate principal, including explicit anonymity.
The worker revalidates its current grant, resource scope and deadline for each
operation and again after bounded waits / immediately before durable commit.
"""
from contextvars import ContextVar
import re

from quantum_autopilot.security.identity import AuthenticationError
from quantum_autopilot.security.service_boundary import (
    METHOD_ACTIONS, admit_service_call, request_deadline, service_resource,
    DurableResultAuditPending,
)


_MISSING = object()
_dispatched = ContextVar('orion_private_policy_caller', default=_MISSING)


class PolicyBindingAuthority:
    def __init__(self, service, *, source=None):
        self.service = service
        self.source = source

    def _capture(self):
        security = getattr(self.service, 'security', None)
        if security is None:
            return _MISSING
        if security.has_identity_scope():
            return security.capture_identity_for_dispatch()
        # The provider candidate is not a permission cache. No OS/service-account
        # fallback is allowed when a UI caller is absent or explicitly anonymous.
        return getattr(self.source, '_identity', _MISSING)

    def dispatch(self, callback, *arguments):
        """Prepare a bounded worker call without reading the vault on Qt."""
        principal = self._capture()
        deadline = request_deadline.get()

        def run():
            token = _dispatched.set((self.service, principal))
            deadline_token = request_deadline.set(deadline)
            try:
                return callback(*arguments)
            finally:
                request_deadline.reset(deadline_token)
                _dispatched.reset(token)

        return run

    def prepare(self, method, callback, *arguments):
        return self.dispatch(self.execute, method, lambda _validate: callback(*arguments))

    def execute(self, method, operation):
        security = getattr(self.service, 'security', None)
        if security is None:
            raise AuthenticationError('authenticated policy binding required')
        captured = _dispatched.get()
        principal = (captured[1] if captured is not _MISSING and captured[0] is self.service
                     else self._capture())
        action = METHOD_ACTIONS.get(method, 'service.manage')
        resource = service_resource(self.service, operation=method)
        if principal is _MISSING:
            security.audit.record('anonymous', action, resource, 'denied',
                                  {'reason': 'explicit_binding_caller_required'})
            raise AuthenticationError('explicit policy binding caller required')
        # Entering a candidate scope can itself refuse a revoked/expired grant.
        # Record that refusal; never substitute the process identity.
        try:
            scope = security.identity_scope(principal)
            scope.__enter__()
        except PermissionError:
            security.audit.record(principal or 'anonymous', action, resource, 'denied',
                                  {'reason': 'permission_denied'})
            raise
        try:
            identity, action, resource = admit_service_call(self.service, method)

            def validate():
                admit_service_call(self.service, method)

            try:
                result = operation(validate)
                if method not in {'activate_policy_change', 'recover_policy_change'}:
                    # Captures, observations and in-memory proposals can be
                    # discarded. Durable transitions instead validate at their
                    # existing last pre-commit callback; do not mislabel a
                    # committed outcome as an unperformed action afterwards.
                    validate()
            except BaseException as error:
                security.audit.record(identity, action, resource, 'failed',
                                      {'error_type': type(error).__name__})
                raise
            if not action.endswith('.read'):
                # No payloads, credentials or arbitrary error messages in audit.
                receipt = result.get('receipt', result) if isinstance(result, dict) else {}
                digest = receipt.get('record_sha256') if isinstance(receipt, dict) else None
                details = {'artifact_sha256': digest} if isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest) else {}
                try:
                    security.audit.record(identity, action, resource, 'completed', details)
                except Exception as error:
                    if method in {'activate_policy_change', 'recover_policy_change'}:
                        raise DurableResultAuditPending(method, receipt) from error
                    raise
            return result
        finally:
            scope.__exit__(None, None, None)


__all__ = ['PolicyBindingAuthority']
