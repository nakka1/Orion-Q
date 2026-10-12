"""Human session lifecycle is independent of the service processing authority."""
import json
import time

import pytest

from quantum_autopilot.service.client import CoreClient, CoreRequestError
from quantum_autopilot.service.clock import HeadlessClock
from quantum_autopilot.security import AuthenticationError
from quantum_autopilot.security.service_identity import ServiceIdentityBinding, service_credential_reference
from tests.test_mixed_controller import configuration
from tests.test_portable_core_service import provisioned, serving  # noqa: F401


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), "independent service clock did not progress"


def test_no_human_session_logout_and_user_revocation_preserve_core_and_audit_authorship(provisioned):
    config, runtime, administrator_runtime, administrator, pki, *_ = provisioned
    contexts, leaves = {}, {}
    scope = "workspace:" + config.state_root.as_posix()
    with administrator_runtime.identity_scope(administrator):
        for subject in ("operator-alice", "operator-bob"):
            administrator_runtime.grant_identity(subject, ["operator"], [scope, scope + "/*"], source="certificate")
            leaves[subject] = pki.provision(subject, kind="service", services=["dashboard"])
            contexts[subject] = pki.client_context(leaves[subject])
    with serving(config, runtime) as server:
        clock = HeadlessClock(server.service, period_s=.03).start()
        try:
            # There has been no operator connection or GUI login.
            wait_for(lambda: clock.status()["processed_steps"] >= 2)
            assert clock.status()["last_source_kind"] == "none"
            url = f"https://localhost:{server.server_port}"
            alice = CoreClient(url, contexts["operator-alice"])
            alice.network_command("configure_network_input", {"kind": "simulated", "seed": 17, "scenario": "quiet"})
            mixed = configuration()
            assert alice.mixed_configure(mixed)["status"] == "configured"
            before = clock.status()["processed_steps"]
            del alice  # No session/connection owns the processing thread.
            wait_for(lambda: clock.status()["processed_steps"] > before)
            with administrator_runtime.identity_scope(administrator):
                pki.revoke(leaves["operator-alice"]["serial"])
            with pytest.raises(CoreRequestError) as revoked:
                CoreClient(url, contexts["operator-alice"]).mixed_configure(mixed)
            assert revoked.value.status == 401
            before = clock.status()["processed_steps"]
            wait_for(lambda: clock.status()["processed_steps"] > before)
            bob = CoreClient(url, contexts["operator-bob"])
            mixed["freshness_s"] = 61
            assert bob.mixed_configure(mixed)["revision"] == 2
            assert bob.console_snapshot(include_static=False)["security"]["subject"] == "operator-bob"
            assert clock.status()["state"] != "blocked"
        finally:
            clock.stop()
            server.service._release_controller_io_resources()
    assert runtime.audit.verify()["valid"]
    rows = [json.loads(line) for line in runtime.audit.path.read_text(encoding="utf-8").splitlines()]
    changes = {row["identity"] for row in rows if row["operation"] == "network.configure" and row["result"] == "completed"}
    assert {"operator-alice", "operator-bob"} <= changes
    automatic = {row["identity"] for row in rows if row["operation"] == "network.operate" and row["result"] == "completed"}
    assert automatic == {"orion-core"}


def test_core_renews_own_session_without_humans_but_blocks_when_its_identity_is_revoked(provisioned):
    config, runtime, administrator_runtime, administrator, *_ = provisioned
    credential = administrator_runtime.vault.get_bytes(service_credential_reference("orion-core"))
    with serving(config, runtime) as server:
        # Explicit fixture setup crosses the same protected configuration
        # boundary; runtime startup still leaves acquisition disabled by default.
        server.service.network_command("configure_network_input", {
            "kind": "simulated", "seed": 17, "scenario": "quiet"})
        server.service.network_step(metadata_only=True)
        original = runtime.bind_service_identity(ServiceIdentityBinding(
            "orion-core", lambda: credential, ttl_seconds=2))
        clock = HeadlessClock(server.service, period_s=.03).start()
        try:
            wait_for(lambda: clock.status()["processed_steps"] >= 2)
            assert clock.status()["last_source_kind"] == "simulated"
            # No HTTP client or operator session drives these steps. Cross the
            # original expiry using real wall time, not a mocked assertion.
            wait_for(lambda: time.time() > original.expires_at + .1)
            before = clock.status()["processed_steps"]
            wait_for(lambda: clock.status()["processed_steps"] > before)
            renewed = runtime.current_identity()
            assert renewed.subject == "orion-core"
            assert renewed.session_id != original.session_id
            with pytest.raises(AuthenticationError):
                runtime.authorize("network.read", "network:all", original)
            with administrator_runtime.identity_scope(administrator):
                administrator_runtime.revoke_identity("orion-core")
            wait_for(lambda: clock.status()["state"] == "blocked")
            stopped_at = clock.status()["processed_steps"]
            time.sleep(.1)
            assert clock.status()["processed_steps"] == stopped_at
            with pytest.raises(AuthenticationError):
                runtime.current_identity()
        finally:
            clock.stop()
            server.service._release_controller_io_resources()
    assert runtime.audit.verify()["valid"]
    rows = [json.loads(line) for line in runtime.audit.path.read_text(encoding="utf-8").splitlines()]
    automatic = {row["identity"] for row in rows if row["operation"] == "network.operate" and row["result"] == "completed"}
    assert automatic == {"orion-core"}
    assert credential not in runtime.audit.path.read_bytes()
