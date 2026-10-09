"""Lightweight identity requirements, available without the optional lab SDKs."""
from __future__ import annotations


def required_kme_identities():
    """Administrator-facing TEST integration requirements; never auto-granted."""
    return [
        {"identity": "kme-alice", "roles": ["operator", "consumer"], "scopes": ["keys:kme-alice", "keys:kme-bob"]},
        {"identity": "kme-bob", "roles": ["operator", "consumer"], "scopes": ["keys:kme-alice", "keys:kme-bob"]},
        {"identity": "sae-alice", "roles": ["consumer"], "scopes": ["keys:kme-alice"]},
        {"identity": "sae-bob", "roles": ["consumer"], "scopes": ["keys:kme-bob"]},
    ]


def check_kme_security(security):
    from .policy import permits
    missing, incompatible = [], []
    for requirement in required_kme_identities():
        try:
            principal = security.principal(requirement["identity"])
        except PermissionError:
            missing.append(requirement["identity"])
            continue
        actions = ["keys.consume", "keys.status"]
        if requirement["identity"].startswith("kme-"):
            actions.extend(["keys.invalidate", "keys.provision"])
        if any(not permits(principal.roles, principal.scopes, action, resource)
               for action in actions for resource in requirement["scopes"]):
            incompatible.append(requirement["identity"])
    return {"available": not missing and not incompatible, "missing_identities": missing,
            "incompatible_identities": incompatible,
            "reason": "" if not missing and not incompatible else "administratively provisioned TEST SAE/KME identities and resource grants required"}
