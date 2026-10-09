"""Backend roles and resource scopes, denied by default."""
from __future__ import annotations

from .identity import AuthorizationError

ROLES = frozenset({"administrator", "operator", "researcher", "auditor", "consumer"})
_READ = frozenset({"network.read", "communicators.read", "adapters.read", "experiments.read",
    "protocol.read", "security.read", "evidence.read", "policy.read", "audit.read", "laboratory.read",
    "topology.read", "nodes.read", "receipts.read", "telemetry.read", "state.read", "models.read",
    "devices.read", "sources.read", "satellites.read", "keys.status"})
_OPERATOR = frozenset({"network.configure", "network.operate", "communicators.configure",
    "adapters.configure", "adapters.acquire", "experiments.run", "protocol.process",
    "simulation.run", "evidence.export", "policy.prepare", "policy.approve", "policy.activate",
    "ingestion.write", "keys.provision", "keys.invalidate", "keys.inspect", "policy.recover", "credentials.use"})


def validate_grant(roles, scopes):
    if isinstance(roles, str) or isinstance(scopes, str):
        raise ValueError("roles and resource scopes require collections")
    try:
        roles, scopes = frozenset(roles), tuple(scopes)
    except TypeError as exc:
        raise ValueError("roles and resource scopes require valid collections") from exc
    if not roles or roles - ROLES or not scopes:
        raise ValueError("known role and resource scope required")
    for scope in scopes:
        if not isinstance(scope, str) or not 1 <= len(scope) <= 256:
            raise ValueError("invalid resource scope")
        if "*" in scope[:-1] or any(c in scope for c in "\r\n\x00"):
            raise ValueError("scope permits only a terminal wildcard")
    return roles, scopes


def permits(roles, scopes, action: str, resource: str) -> bool:
    if not isinstance(action, str) or not isinstance(resource, str) or not action or not resource:
        return False
    if any(c in action + resource for c in "\r\n\x00") or len(action) > 128 or len(resource) > 512:
        return False
    covered = any(scope == "*" or scope == resource or
                  (scope.endswith("*") and resource.startswith(scope[:-1])) for scope in scopes)
    if not covered:
        return False
    if "administrator" in roles:
        return True
    if "operator" in roles and (action in _READ or action in _OPERATOR):
        return True
    if "researcher" in roles and (action in _READ or action in {"simulation.run", "evidence.export", "keys.inspect"}):
        return True
    if "auditor" in roles and (action in _READ or action == "keys.inspect"):
        return True
    return "consumer" in roles and action in {"keys.consume", "keys.deliver", "keys.status", "credentials.use"}


def require_permission(roles, scopes, action, resource):
    if not permits(roles, scopes, action, resource):
        raise AuthorizationError("operation is not permitted for this identity/resource")
