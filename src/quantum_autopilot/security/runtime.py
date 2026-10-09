"""One authorization boundary for native, HTTP, service and adapter calls."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
import hashlib
import hmac
import json
import math
from pathlib import Path
import secrets
import threading
import time
from uuid import uuid4

from .audit import AuthenticatedAudit
from .identity import (AuthenticationError, AuthorizationError, OIDCConfig, OIDCVerifier,
                       Principal, operating_system_identity)
from .policy import require_permission, validate_grant
from .vault import SecretVault, VaultError


_UNSET = object()
_identity: ContextVar = ContextVar("orion_authenticated_identity", default=_UNSET)
_POLICY = "identity-policy-v1"


class SecurityRuntime:
    def __init__(self, root, *, profile="local", bootstrap=True, oidc: OIDCConfig | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.profile = profile
        self.vault = SecretVault(self.root / "vault.json", profile=profile)
        self._proof_key = secrets.token_bytes(32)
        self._session_lock = threading.RLock()
        self._sessions = {}
        self._device_session_token = None
        self.oidc = OIDCVerifier(oidc) if oidc is not None else None
        owner, display = operating_system_identity()
        with self.vault.transaction() as rows:
            if _POLICY not in rows:
                if not bootstrap:
                    raise AuthenticationError("security policy has not been provisioned")
                policy = {"schema": "orion.security.identities.v1", "owner": owner,
                          "generation": 1, "identities": {owner: {"roles": ["administrator"],
                          "scopes": ["*"], "display_name": display, "source": "os", "revoked": False,
                          "generation": 1, "expires_at": None}}}
                rows[_POLICY] = base64.b64encode(json.dumps(policy, sort_keys=True, allow_nan=False).encode()).decode("ascii")
            else:
                self._decode_policy(rows[_POLICY])
        self.audit = AuthenticatedAudit(self.root / "audit.jsonl", self.vault)
        self.audit.verify()

    @staticmethod
    def _decode_policy(encoded):
        try:
            policy = json.loads(base64.b64decode(encoded, validate=True))
            if not isinstance(policy, dict) or policy.get("schema") != "orion.security.identities.v1":
                raise ValueError()
            if (not isinstance(policy.get("identities"), dict) or not isinstance(policy.get("owner"), str)
                    or isinstance(policy.get("generation"), bool) or not isinstance(policy.get("generation"), int)
                    or policy["generation"] < 1):
                raise ValueError()
            for subject, row in policy["identities"].items():
                if not isinstance(subject, str) or not 1 <= len(subject) <= 512 or not isinstance(row, dict):
                    raise ValueError()
                validate_grant(row["roles"], row["scopes"])
                if (row.get("source") not in {"os", "service", "oidc", "certificate"}
                        or not isinstance(row.get("revoked"), bool)
                        or not isinstance(row.get("display_name"), str)
                        or isinstance(row.get("generation"), bool) or not isinstance(row.get("generation"), int)
                        or not 1 <= row["generation"] <= policy["generation"]):
                    raise ValueError()
                expiry = row.get("expires_at")
                if expiry is not None and (isinstance(expiry, bool) or not isinstance(expiry, (int, float))
                                           or not math.isfinite(expiry) or expiry <= 0):
                    raise ValueError()
            return policy
        except (ValueError, KeyError, TypeError) as exc:
            raise AuthenticationError("protected identity policy is invalid") from exc

    def _policy(self):
        try:
            return self._decode_policy(base64.b64encode(self.vault.get_bytes(_POLICY)).decode("ascii"))
        except VaultError as exc:
            raise AuthenticationError("protected identity policy is unavailable") from exc

    def _proof(self, principal):
        values = [principal.subject, principal.source, principal.session_id, principal.expires_at]
        return hmac.new(self._proof_key, json.dumps(values).encode(), hashlib.sha256).hexdigest()

    def principal(self, subject: str) -> Principal:
        """Trusted in-process provisioning helper, not a remote impersonation API."""
        policy = self._policy()
        row = policy["identities"].get(subject)
        if row is None or row.get("revoked"):
            raise AuthenticationError("identity is not provisioned or has been revoked")
        expiry = row.get("expires_at")
        if expiry is not None and time.time() >= expiry:
            raise AuthenticationError("identity authorization has expired")
        principal = Principal(subject, row.get("display_name", ""), frozenset(row["roles"]),
                              tuple(row["scopes"]), row.get("source", "service"), expires_at=expiry)
        return replace(principal, _proof=self._proof(principal))

    def local_identity(self) -> Principal:
        subject, _ = operating_system_identity()
        principal = self.principal(subject)
        if principal.source != "os":
            raise AuthenticationError("operating system identity binding is invalid")
        return principal

    def current_identity(self) -> Principal:
        candidate = _identity.get()
        if candidate is _UNSET:
            candidate = self.local_identity()
        if candidate is None:
            raise AuthenticationError("authenticated identity is required")
        return self._revalidate(candidate)

    @staticmethod
    def has_identity_scope() -> bool:
        return _identity.get() is not _UNSET

    @staticmethod
    def capture_identity_for_dispatch():
        """Capture caller context only; this is never an authorization decision.

        Interactive dispatch can transport the candidate to a worker without
        reading the vault on Qt. identity_scope/authorize must freshly validate
        it in that worker. Anonymous contexts remain explicitly anonymous.
        """
        candidate = _identity.get()
        if candidate is _UNSET:
            raise AuthenticationError("no explicit identity context to capture")
        return candidate

    def _revalidate(self, candidate):
        if not isinstance(candidate, Principal) or not candidate.authenticated or not hmac.compare_digest(
                self._proof(candidate), candidate._proof):
            raise AuthenticationError("authenticated identity is required")
        if candidate.expires_at is not None and time.time() >= candidate.expires_at:
            raise AuthenticationError("identity session has expired")
        registered = self.principal(candidate.subject)
        if registered.source != candidate.source:
            raise AuthenticationError("identity source binding is invalid")
        if candidate.session_id is not None:
            with self._session_lock:
                session = self._sessions.get(candidate.session_id)
                if session is None or session["revoked"] or session["expires"] <= time.time():
                    raise AuthenticationError("identity session is unavailable or revoked")
                policy = self._policy()
                if session["grant_generation"] != policy["identities"][candidate.subject]["generation"]:
                    raise AuthenticationError("identity session authorization has changed")
        # Frozen caller-supplied roles/scopes are never used as current authority.
        return replace(registered, session_id=candidate.session_id, expires_at=candidate.expires_at,
                       _proof=candidate._proof)

    def authorize(self, action: str, resource: str, principal: Principal | None = None) -> Principal:
        identity = None
        try:
            identity = self.current_identity() if principal is None else self._revalidate(principal)
            require_permission(identity.roles, identity.scopes, action, resource)
        except (AuthenticationError, AuthorizationError):
            actor = identity or (principal if isinstance(principal, Principal) else None)
            self.audit.record(actor or "anonymous", action, resource, "denied", {"reason": "permission_denied"})
            raise
        self.audit.record(identity, action, resource, "allowed")
        return identity

    @contextmanager
    def identity_scope(self, principal: Principal | None):
        # HTTP must always establish this scope, including anonymous requests.
        if principal is not None:
            principal = self._revalidate(principal)
        token = _identity.set(principal)
        try:
            yield principal
        finally:
            _identity.reset(token)

    def issue_local_session(self, *, ttl_seconds=3600) -> str:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)) or not 1 <= ttl_seconds <= 86400:
            raise ValueError("session lifetime must be between 1 and 86400 seconds")
        # Called by the native authenticated OS process. Never an unauthenticated HTTP route.
        identity = self.local_identity()
        requester = self.current_identity()
        if requester.subject != identity.subject or requester.source != "os":
            raise AuthorizationError("only the authenticated local OS identity may issue its local session")
        return self._issue_session(identity, ttl_seconds)

    def _issue_session(self, identity, ttl_seconds):
        session_id, token = uuid4().hex, secrets.token_urlsafe(48)
        digest = hashlib.sha256(token.encode()).hexdigest()
        policy = self._policy()
        with self._session_lock:
            self._sessions[session_id] = {"digest": digest, "subject": identity.subject,
                "expires": time.time() + ttl_seconds, "revoked": False,
                "grant_generation": policy["identities"][identity.subject]["generation"]}
        self.audit.record(identity, "session.issue", "security:sessions", "created")
        return token

    def issue_service_session(self, subject: str, *, ttl_seconds=3600) -> str:
        self.authorize("security.manage", "security:sessions")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)) or not 1 <= ttl_seconds <= 86400:
            raise ValueError("session lifetime must be between 1 and 86400 seconds")
        identity = self.principal(subject)
        if identity.source == "os":
            raise AuthorizationError("OS sessions must be issued by their authenticated local user")
        return self._issue_session(identity, ttl_seconds)

    def device_session_identity(self, *, new_session=False) -> Principal:
        """Bind native instrument control to an explicit revocable session.

        Revocation/expiry requires deliberate reconnection with new_session=True;
        it never silently renews an existing device-control authority.
        """
        with self._session_lock:
            if self._device_session_token is None or new_session:
                self._device_session_token = self.issue_local_session(ttl_seconds=86400)
            return self.session(self._device_session_token)

    def session(self, token: str) -> Principal:
        if not isinstance(token, str) or not 16 <= len(token) <= 512:
            raise AuthenticationError("identity session is unavailable or revoked")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._session_lock:
            found = [(sid, row.copy()) for sid, row in self._sessions.items()
                     if hmac.compare_digest(row["digest"], digest)]
        if len(found) != 1:
            raise AuthenticationError("identity session is unavailable or revoked")
        sid, row = found[0]
        principal = replace(self.principal(row["subject"]), session_id=sid, expires_at=row["expires"])
        principal = replace(principal, _proof=self._proof(principal))
        return self._revalidate(principal)

    def revoke_session(self, token: str):
        requester = self.authorize("security.manage", "security:sessions")
        if not isinstance(token, str) or len(token) > 512:
            raise ValueError("invalid session reference")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._session_lock:
            for row in self._sessions.values():
                if hmac.compare_digest(row["digest"], digest):
                    row["revoked"] = True
        self.audit.record(requester, "session.revoke", "security:sessions", "revoked")

    def grant_identity(self, subject, roles, scopes, *, display_name="", source="service", expires_at=None):
        requester = self.authorize("security.manage", "security:identities")
        roles, scopes = validate_grant(roles, scopes)
        if not isinstance(subject, str) or not 1 <= len(subject) <= 512 or any(c in subject for c in "\r\n\x00"):
            raise ValueError("invalid identity subject")
        if source not in {"os", "service", "oidc", "certificate"}:
            raise ValueError("invalid identity source")
        if source == "os" and not (subject.startswith("os:sid:") or subject.startswith("os:uid:")):
            raise ValueError("OS identity requires a real SID/UID subject")
        if expires_at is not None and (isinstance(expires_at, bool) or not isinstance(expires_at, (int, float))
                                       or not math.isfinite(expires_at) or expires_at <= time.time()):
            raise ValueError("identity expiry must be in the future")
        with self.vault.transaction() as rows:
            policy = self._decode_policy(rows[_POLICY])
            policy["generation"] += 1
            policy["identities"][subject] = {"roles": sorted(roles), "scopes": list(scopes),
                "display_name": str(display_name)[:256], "source": source, "revoked": False,
                "generation": policy["generation"], "expires_at": expires_at}
            rows[_POLICY] = base64.b64encode(json.dumps(policy, sort_keys=True, allow_nan=False).encode()).decode("ascii")
        self.audit.record(requester, "identity.grant", "security:identities", "success")
        return self.principal(subject)

    def revoke_identity(self, subject):
        requester = self.authorize("security.manage", "security:identities")
        with self.vault.transaction() as rows:
            policy = self._decode_policy(rows[_POLICY])
            if subject not in policy["identities"]:
                raise ValueError("identity is not provisioned")
            policy["generation"] += 1
            policy["identities"][subject]["revoked"] = True
            policy["identities"][subject]["generation"] = policy["generation"]
            rows[_POLICY] = base64.b64encode(json.dumps(policy, sort_keys=True, allow_nan=False).encode()).decode("ascii")
        self.audit.record(requester, "identity.revoke", "security:identities", "revoked")

    def authenticate_oidc(self, token: str) -> Principal:
        if self.oidc is None:
            raise AuthenticationError("institutional identity provider is not configured")
        claims = self.oidc.verify(token)
        subject = f"oidc:{self.oidc.config.issuer}:{claims['sub']}"
        principal = self.principal(subject)
        if principal.source != "oidc":
            raise AuthenticationError("institutional identity binding is invalid")
        # Bearer expiry is strict even when verifier allows time skew.
        expiry = min(claims["exp"], principal.expires_at or claims["exp"])
        principal = replace(principal, expires_at=expiry)
        return self._revalidate(replace(principal, _proof=self._proof(principal)))

    def rotate_secret(self, name: str, *, size=32) -> str:
        requester = self.authorize("vault.rotate", "vault:" + name)
        reference = self.vault.rotate(name, size)
        self.audit.record(requester, "vault.rotate", "vault:" + name, "success")
        return reference

    def recover_secret(self, name: str, value: bytes):
        requester = self.authorize("state.restore", "vault:" + name)
        # Recovery is an explicit privileged operation, never inferred from a backup.
        self.vault.put_bytes(name, value)
        self.audit.record(requester, "vault.recover", "vault:" + name, "success",
                          {"reason": "explicit_recovery"})

    def snapshot(self):
        principal = self.authorize("security.read", "security:identity")
        return {"subject": principal.subject, "display_name": principal.display_name,
                "roles": sorted(principal.roles), "scopes": list(principal.scopes),
                "source": principal.source, "expires_at": principal.expires_at,
                "profile": self.profile, "vault_mode": self.vault.mode,
                "oidc_configured": self.oidc is not None, "physical_qualification": False}
