"""One authorization boundary for native, HTTP, service and adapter calls."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, replace
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
from .service_identity import (ServiceIdentityBinding, ServiceIdentityProvision,
                               service_credential_reference, validate_service_credential)
from .vault import SecretVault, VaultError


_UNSET = object()
_identity: ContextVar = ContextVar("orion_authenticated_identity", default=_UNSET)
_POLICY = "identity-policy-v1"


class SecurityRuntime:
    def __init__(self, root, *, profile="local", bootstrap=None, oidc: OIDCConfig | None = None,
                 identity_profile="desktop-os", secret_provider=None,
                 secret_provider_configuration=None, service_identity: ServiceIdentityBinding | None = None,
                 service_bootstrap: ServiceIdentityProvision | None = None, process_identity_renewal=False):
        if identity_profile not in {"desktop-os", "service"}:
            raise ValueError("unknown identity profile")
        if bootstrap is None:
            bootstrap = identity_profile == "desktop-os"
        if not isinstance(bootstrap, bool):
            raise ValueError("explicit boolean bootstrap policy required")
        if not isinstance(process_identity_renewal, bool) or (process_identity_renewal
                and (identity_profile != "service" or profile != "operational")):
            raise ValueError("process identity renewal requires an operational service profile")
        if identity_profile == "desktop-os" and (service_identity is not None or service_bootstrap is not None):
            raise ValueError("service identity requires the service identity profile")
        if secret_provider_configuration is not None:
            if secret_provider is not None:
                raise ValueError("configure one secret protection provider")
            from .secret_providers import MountedSecretProvider
            secret_provider = MountedSecretProvider.from_config(secret_provider_configuration)
        if identity_profile == "service" and secret_provider is None:
            raise VaultError("service profile requires an explicitly configured secret protection provider")
        if identity_profile == "service" and (getattr(secret_provider, "operational_ready", False) is not True
                or getattr(secret_provider, "mode", None) in {"windows-dpapi-current-user", "unix-0600-local-only"}):
            raise VaultError("service profile requires portable protected secrets independent of an interactive OS session")
        if identity_profile == "service" and bootstrap and not isinstance(service_bootstrap, ServiceIdentityProvision):
            raise AuthenticationError("service bootstrap requires explicit offline identity provisioning")
        if service_bootstrap is not None and not bootstrap:
            raise ValueError("service bootstrap material is only admitted during explicit provisioning")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.profile = profile
        self.identity_profile = identity_profile
        self._secret_provider = secret_provider
        if profile == "operational" and identity_profile == "desktop-os" and secret_provider is None:
            # The desktop composition may explicitly use its OS vault. The core
            # service profile never imports or selects a Windows provider.
            import os
            if os.name == "nt":
                from .vault import WindowsDPAPIProvider
                self._secret_provider = secret_provider = WindowsDPAPIProvider()
        self.vault = SecretVault(self.root / "vault.json", profile=profile, provider=secret_provider)
        self._proof_key = secrets.token_bytes(32)
        self._session_lock = threading.RLock()
        # Always acquire this before _session_lock. Explicit caller identities
        # do not use it, and no HTTP input can enable process renewal.
        self._process_lock = threading.RLock()
        self._sessions = {}
        self._device_session_token = None
        self._device_service_identity = None
        self._process_identity = None
        self.process_identity_renewal = process_identity_renewal
        self._process_binding = None
        self._process_grant_generation = None
        self._process_refresh_at = None
        self._process_renewal_blocked = False
        self._process_sessions = set()
        self.oidc = OIDCVerifier(oidc) if oidc is not None else None
        owner, display = operating_system_identity() if identity_profile == "desktop-os" else (None, None)
        with self.vault.transaction() as rows:
            if _POLICY not in rows:
                if not bootstrap:
                    raise AuthenticationError("security policy has not been provisioned")
                if identity_profile == "service":
                    owner, display = service_bootstrap.subject, service_bootstrap.display_name
                    owner_row = {"roles": list(service_bootstrap.roles), "scopes": list(service_bootstrap.scopes),
                        "display_name": display, "source": "service", "revoked": False,
                        "generation": 1, "expires_at": None,
                        "credential_reference": service_bootstrap.credential_reference}
                    if "administrator" not in service_bootstrap.roles:
                        raise AuthenticationError("initial service provisioning requires an administrator identity")
                    if service_bootstrap.credential_reference in rows:
                        raise AuthenticationError("initial service credential reference already exists")
                    rows[service_bootstrap.credential_reference] = base64.b64encode(
                        service_bootstrap.credential).decode("ascii")
                else:
                    owner_row = {"roles": ["administrator"], "scopes": ["*"], "display_name": display,
                        "source": "os", "revoked": False, "generation": 1, "expires_at": None}
                policy = {"schema": "orion.security.identities.v1", "owner": owner,
                          "generation": 1, "identities": {owner: owner_row}}
                rows[_POLICY] = base64.b64encode(json.dumps(policy, sort_keys=True, allow_nan=False).encode()).decode("ascii")
            else:
                if service_bootstrap is not None:
                    raise AuthenticationError("existing security policy cannot be provisioned again")
                self._decode_policy(rows[_POLICY])
        self.audit = AuthenticatedAudit(self.root / "audit.jsonl", self.vault)
        self.audit.verify()
        if service_identity is not None:
            self.bind_service_identity(service_identity)

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
                credential_reference = row.get("credential_reference")
                if credential_reference is not None and (row["source"] != "service"
                        or credential_reference != service_credential_reference(subject)):
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
        if self.identity_profile != "desktop-os":
            raise AuthenticationError("interactive OS identity is disabled in the service profile")
        subject, _ = operating_system_identity()
        principal = self.principal(subject)
        if principal.source != "os":
            raise AuthenticationError("operating system identity binding is invalid")
        return principal

    def current_identity(self) -> Principal:
        candidate = _identity.get()
        if candidate is _UNSET:
            if self.identity_profile == "service":
                return self._current_process_identity()
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

    def _issue_session(self, identity, ttl_seconds, *, expected_generation=None):
        session_id, token = uuid4().hex, secrets.token_urlsafe(48)
        digest = hashlib.sha256(token.encode()).hexdigest()
        policy = self._policy()
        grant = policy["identities"][identity.subject]
        if expected_generation is not None and (grant["generation"] != expected_generation or grant["revoked"]):
            raise AuthenticationError("service identity authorization changed during authentication")
        with self._session_lock:
            self._sessions[session_id] = {"digest": digest, "subject": identity.subject,
                "expires": time.time() + ttl_seconds, "revoked": False,
                "grant_generation": grant["generation"]}
        try:
            self.audit.record(identity, "session.issue", "security:sessions", "created")
        except BaseException:
            # An unaudited lease is never returned or kept as hidden authority.
            with self._session_lock:
                self._sessions.pop(session_id, None)
            raise
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
        if self.identity_profile == "service":
            # Keep a device's original lease, including its expiry/revocation.
            # Process renewal neither clears nor replaces this authority.
            with self._process_lock:
                if self._device_service_identity is None or new_session:
                    identity = self.current_identity()
                    if identity.session_id is None:
                        raise AuthenticationError("equipment control requires an explicit revocable service session")
                    self._device_service_identity = identity
                return self._revalidate(self._device_service_identity)
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
            existing = policy["identities"].get(subject, {})
            policy["identities"][subject] = {"roles": sorted(roles), "scopes": list(scopes),
                "display_name": str(display_name)[:256], "source": source, "revoked": False,
                "generation": policy["generation"], "expires_at": expires_at}
            if source == "service" and existing.get("source") == "service" and existing.get("credential_reference"):
                policy["identities"][subject]["credential_reference"] = existing["credential_reference"]
            rows[_POLICY] = base64.b64encode(json.dumps(policy, sort_keys=True, allow_nan=False).encode()).decode("ascii")
        self.audit.record(requester, "identity.grant", "security:identities", "success")
        return self.principal(subject)

    def grant_service_identity(self, subject, roles, scopes, credential, *, display_name="", expires_at=None):
        """Privileged atomic grant + credential binding; never credential logging."""
        requester = self.authorize("security.manage", "security:identities")
        provision = ServiceIdentityProvision(subject, tuple(roles), tuple(scopes),
                                             validate_service_credential(credential), display_name)
        if expires_at is not None and (isinstance(expires_at, bool) or not isinstance(expires_at, (int, float))
                                       or not math.isfinite(expires_at) or expires_at <= time.time()):
            raise ValueError("identity expiry must be in the future")
        with self.vault.transaction() as rows:
            policy = self._decode_policy(rows[_POLICY])
            policy["generation"] += 1
            policy["identities"][subject] = {"roles": list(provision.roles), "scopes": list(provision.scopes),
                "display_name": provision.display_name, "source": "service", "revoked": False,
                "generation": policy["generation"], "expires_at": expires_at,
                "credential_reference": provision.credential_reference}
            rows[provision.credential_reference] = base64.b64encode(provision.credential).decode("ascii")
            rows[_POLICY] = base64.b64encode(json.dumps(policy, sort_keys=True, allow_nan=False).encode()).decode("ascii")
        self.audit.record(requester, "identity.grant", "security:identities", "success")
        return self.principal(subject)

    def authenticate_service(self, subject: str, credential: bytes, *, ttl_seconds=3600) -> Principal:
        """Prove a provisioned service secret; authority comes only from policy."""
        return self._authenticate_service(subject, credential, ttl_seconds=ttl_seconds)

    def _authenticate_service(self, subject, credential, *, ttl_seconds, expected_generation=None):
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)) or not 1 <= ttl_seconds <= 86400:
            raise ValueError("session lifetime must be between 1 and 86400 seconds")
        validate_service_credential(credential)
        identity = self.principal(subject)
        row = self._policy()["identities"][subject]
        if expected_generation is not None and row["generation"] != expected_generation:
            raise AuthenticationError("service identity authorization has changed")
        reference = row.get("credential_reference")
        if identity.source != "service" or reference != service_credential_reference(subject):
            raise AuthenticationError("service credential binding is unavailable")
        try:
            expected = self.vault.get_bytes(reference)
        except VaultError as exc:
            raise AuthenticationError("service credential binding is unavailable") from exc
        if not hmac.compare_digest(expected, credential):
            self.audit.record("anonymous", "service.authenticate", "security:sessions", "denied")
            raise AuthenticationError("service authentication rejected")
        lifetime = min(ttl_seconds, identity.expires_at - time.time()) if identity.expires_at is not None else ttl_seconds
        if lifetime <= 0:
            raise AuthenticationError("service identity authorization has expired")
        token = self._issue_session(identity, lifetime, expected_generation=row["generation"])
        try:
            return self.session(token)
        except BaseException:
            # A policy change can race between issue/audit and proof validation.
            # The token was never returned; remove its provisional session even
            # when the caller has not yet received a Principal to clean up.
            digest = hashlib.sha256(token.encode()).hexdigest()
            with self._session_lock:
                for session_id, session in tuple(self._sessions.items()):
                    if hmac.compare_digest(session["digest"], digest):
                        self._sessions.pop(session_id, None)
            raise

    def bind_service_identity(self, binding: ServiceIdentityBinding) -> Principal:
        """Explicit daemon composition, not ambient authority from USER/headers."""
        if self.identity_profile != "service" or not isinstance(binding, ServiceIdentityBinding):
            raise AuthenticationError("service process identity binding is required")
        with self._process_lock:
            # Deliberate composition/recovery retains its existing replacement
            # semantics. Automatic maintenance below never calls this method.
            previous = self._process_identity
            self._process_identity = None
            self._process_binding = None
            self._process_refresh_at = None
            self._process_renewal_blocked = True
            self._device_service_identity = None
            with self._session_lock:
                if previous is not None and previous.session_id is not None:
                    session = self._sessions.get(previous.session_id)
                    if session is not None:
                        session["revoked"] = True
            identity = self.authenticate_service(binding.subject, binding.read_credential(), ttl_seconds=binding.ttl_seconds)
            with self._session_lock:
                generation = self._sessions[identity.session_id]["grant_generation"]
            self._process_grant_generation = generation
            self._process_binding = binding
            self._set_process_identity(identity)
            self._process_renewal_blocked = False
            self._prune_process_sessions()
            return identity

    def _set_process_identity(self, identity):
        """Publish under _process_lock; do not revoke any earlier device lease."""
        self._process_identity = identity
        self._process_sessions.add(identity.session_id)
        grant_expiry = self._policy()["identities"][identity.subject].get("expires_at")
        # A session capped by the grant cannot be extended. Wake at expiry and
        # block rather than issue successively shorter leases in a busy loop.
        if grant_expiry is not None and identity.expires_at >= grant_expiry:
            self._process_refresh_at = identity.expires_at
        else:
            remaining = max(0.0, identity.expires_at - time.time())
            self._process_refresh_at = identity.expires_at - min(300.0, remaining / 2.0)

    def _prune_process_sessions(self):
        now = time.time()
        active = self._process_identity.session_id if self._process_identity is not None else None
        device = self._device_service_identity.session_id if self._device_service_identity is not None else None
        with self._session_lock:
            for session_id in tuple(self._process_sessions):
                row = self._sessions.get(session_id)
                if session_id not in {active, device} and (row is None or row["expires"] <= now or row["revoked"]):
                    self._sessions.pop(session_id, None)
                    self._process_sessions.discard(session_id)

    def process_identity_maintenance_delay(self):
        """Scheduling hint only; maintenance must freshly authenticate authority."""
        if not self.process_identity_renewal:
            return None
        with self._process_lock:
            if self._process_renewal_blocked or self._process_identity is None or self._process_refresh_at is None:
                raise AuthenticationError("service process identity is blocked")
            return max(0.0, self._process_refresh_at - time.time())

    def maintain_process_identity(self) -> Principal:
        """Renew only the core's own lease in a context without a human caller.

        A revoked/expired lease or changed grant requires deliberate recovery.
        Neither credentials nor a later new grant can silently resurrect it.
        """
        if self.identity_profile != "service" or self.has_identity_scope():
            raise AuthenticationError("process maintenance requires its own service context")
        return self._current_process_identity()

    def _current_process_identity(self):
        with self._process_lock:
            if self._process_renewal_blocked or self._process_identity is None:
                raise AuthenticationError("authenticated service process identity is required")
            previous = self._process_identity
            issued = None
            try:
                identity = self._revalidate(previous)
                if not self.process_identity_renewal or time.time() < self._process_refresh_at:
                    return identity
                # Validate the old lease and original grant before reading the
                # credential. authenticate_service alone is not renewal proof.
                generation = self._process_grant_generation
                binding = self._process_binding
                row = self._policy()["identities"][previous.subject]
                if binding is None or binding.subject != previous.subject or row["generation"] != generation:
                    raise AuthenticationError("service process authorization has changed")
                issued = self._authenticate_service(binding.subject, binding.read_credential(),
                    ttl_seconds=binding.ttl_seconds, expected_generation=generation)
                # Revocation may race with credential I/O or session issuance.
                # Recheck both leases while serializing same-runtime revocation.
                with self._session_lock:
                    self._revalidate(previous)
                    self._revalidate(issued)
                    if self._sessions[issued.session_id]["grant_generation"] != generation:
                        raise AuthenticationError("service process authorization has changed")
                    self._set_process_identity(issued)
                self._prune_process_sessions()
                self.audit.record(issued, "service.process-renew", "security:process", "success",
                                  {"sequence": generation, "state": "active"})
                return issued
            except Exception:
                # A failed renewal stays blocked even if a credential source or
                # grant later changes. Only explicit bind is recovery authority.
                if self.process_identity_renewal:
                    self._process_renewal_blocked = True
                    self._process_identity = None
                    self._process_refresh_at = None
                    if issued is not None:
                        with self._session_lock:
                            # No new lease was returned to a caller. Remove this
                            # failed provisional session, rather than accumulate
                            # inaccessible revocation entries after recovery.
                            self._sessions.pop(issued.session_id, None)
                            self._process_sessions.discard(issued.session_id)
                    self._prune_process_sessions()
                    self.audit.record(previous, "service.process-renew", "security:process", "denied",
                                      {"reason": "authentication_failed", "state": "blocked"})
                raise AuthenticationError("service process identity is unavailable; explicit recovery is required") from None

    def worker_configuration(self) -> dict:
        """Reconstruct authentication boundary without tokens, credentials or OS fallback.

        Workers authenticate their own requests (mTLS/OIDC/service credentials).
        A parent's process session and signing proof never cross this boundary.
        """
        configuration = {"schema": "orion.security.worker.v1", "root": str(self.root.resolve()), "profile": self.profile,
                         "identity_profile": self.identity_profile}
        if self._secret_provider is not None:
            public_configuration = getattr(self._secret_provider, "public_configuration", None)
            from .vault import WindowsDPAPIProvider
            desktop_provider = self.identity_profile == "desktop-os" and isinstance(self._secret_provider, WindowsDPAPIProvider)
            if not callable(public_configuration) and not desktop_provider:
                raise ValueError("secret provider cannot be reconstructed from non-secret worker configuration")
            if callable(public_configuration):
                configuration["secret_provider_configuration"] = public_configuration()
        if self.oidc is not None:
            configuration["oidc_configuration"] = asdict(self.oidc.config)
        return configuration

    @classmethod
    def from_worker_configuration(cls, configuration: dict):
        """Only provisioned protected state + explicit public provider references."""
        allowed = {"schema", "root", "profile", "identity_profile", "secret_provider_configuration", "oidc_configuration"}
        if (not isinstance(configuration, dict) or set(configuration) - allowed
                or configuration.get("schema") != "orion.security.worker.v1"
                or not isinstance(configuration.get("root"), str)
                or not configuration["root"]
                or configuration.get("profile") not in {"local", "operational"}
                or configuration.get("identity_profile") not in {"desktop-os", "service"}):
            raise ValueError("invalid non-secret worker security configuration")
        options = {name: configuration[name] for name in ("root", "profile", "identity_profile", "secret_provider_configuration")
                   if name in configuration}
        if "oidc_configuration" in configuration:
            value = configuration["oidc_configuration"]
            if not isinstance(value, dict) or set(value) - {"issuer", "audience", "jwks", "algorithms",
                    "clock_skew_seconds", "max_token_lifetime_seconds"}:
                raise ValueError("invalid worker OIDC configuration")
            options["oidc"] = OIDCConfig(**value)
        return cls(**options, bootstrap=False)

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
                "identity_profile": self.identity_profile,
                "oidc_configured": self.oidc is not None, "physical_qualification": False}
