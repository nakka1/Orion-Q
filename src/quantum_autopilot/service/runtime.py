"""Compose the portable backend with explicit identity and secret dependencies."""
from __future__ import annotations

from quantum_autopilot.security import SecurityRuntime
from quantum_autopilot.security.identity import OIDCConfig
from quantum_autopilot.security.secret_providers import MountedSecretProvider
from quantum_autopilot.security.service_identity import ServiceIdentityBinding

from .config import CoreConfig
from .tls import ServiceTLS, load_leaf


def mounted_provider(config: CoreConfig):
    references = {(config.vault["key_id"], 1): config.vault["key_file"]}
    references.update({(name, 1): path for name, path in config.vault.get("previous_keys", {}).items()})
    return MountedSecretProvider(references, active_key_id=config.vault["key_id"], active_version=1)


def build_runtime(config: CoreConfig, *, secret_provider=None, identity_binding=None):
    """Injected providers support external secret services without OS login.

    The normal CLI uses private mounted secrets. Overrides are trusted local
    composition dependencies, never values selectable in an HTTP request.
    """
    binding = identity_binding or ServiceIdentityBinding.from_mounted_file(
        config.identity.subject, config.identity.credential_file, ttl_seconds=86400)
    return SecurityRuntime(config.security_root, profile="operational", identity_profile="service", bootstrap=False,
                           secret_provider=secret_provider or mounted_provider(config), service_identity=binding,
                           process_identity_renewal=True,
                           oidc=OIDCConfig(**config.oidc) if config.oidc else None)


def create_core_server(config: CoreConfig, *, security=None):
    from quantum_autopilot.qkd.dashboard_api import create_server
    runtime = security if security is not None else build_runtime(config)
    if runtime.profile != "operational" or runtime.identity_profile != "service":
        raise ValueError("core server requires an operational service runtime")
    runtime.current_identity()  # A verified process binding is mandatory at startup.
    tls = ServiceTLS(config.tls["root"], runtime, load_leaf(config.tls["server_leaf"]),
                     identity=config.identity.subject)
    server = create_server(config.repository_root, host=config.http["host"], port=config.http["port"],
                         control_journal_path=config.journal, security=runtime,
                         http_policy=config.http_policy, ssl_context=tls.server_context(),
                         peer_authorizer=tls.authorize_peer)
    # Core shutdown uses an explicit bounded drain. A timeout must not allow
    # process exit to kill a handler still writing persistent state.
    server.daemon_threads = False
    server.block_on_close = False
    if config.resource_authority_root is not None:
        from quantum_autopilot.security.equipment import EquipmentAuthority
        server.service._mixed_resource_authority = EquipmentAuthority(config.resource_authority_root)
    return server
