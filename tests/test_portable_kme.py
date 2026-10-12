"""KME subprocess composition carries secret references and independent identity."""
import json
import os
import secrets
from uuid import uuid4

import pytest

from quantum_autopilot.lab.independent_kme import IndependentKme, independent_pair
from quantum_autopilot.lab.kms import client_context, create_test_pki, request_kme
from quantum_autopilot.security import SecurityRuntime
from quantum_autopilot.security.secret_providers import MountedSecretProvider
from quantum_autopilot.security.service_identity import ServiceIdentityProvision


def test_kme_subprocess_configuration_preserves_service_profile(monkeypatch, tmp_path):
    configuration = {"schema": "orion.security.worker.v1", "root": str(tmp_path), "profile": "operational",
                     "identity_profile": "service", "secret_provider_configuration": {
                         "active_key_id": "fixture", "active_version": 1,
                         "keys": [{"key_id": "fixture", "version": 1, "path": str(tmp_path / "secret")}]}}
    class Runtime:
        def worker_configuration(self):
            return configuration
    monkeypatch.setattr(IndependentKme, "start", lambda self, pairs=None: None)
    kme = IndependentKme(tmp_path / "kme.sqlite", b"t" * 32, {}, "kme-alice", {}, security_runtime=Runtime())
    assert kme.security_configuration == configuration
    assert kme.security_configuration["identity_profile"] == "service"
    assert "credential" not in json.dumps(configuration) and "token" not in json.dumps(configuration)


@pytest.mark.skipif(os.name != "posix", reason="native secret mounts and KME reconstruction require a Linux/POSIX host")
def test_two_independent_kmes_without_os_session_and_conservative_restart(tmp_path):
    key_file = tmp_path / "master.key"
    key_file.write_bytes(secrets.token_bytes(32))
    key_file.chmod(0o600)
    provider = MountedSecretProvider({("fixture", 1): key_file}, active_key_id="fixture", active_version=1)
    credential = secrets.token_bytes(48)
    runtime = SecurityRuntime(tmp_path / "security", profile="operational", identity_profile="service",
        secret_provider=provider, bootstrap=True,
        service_bootstrap=ServiceIdentityProvision("test-admin", ("administrator",), ("*",), credential))
    admin = runtime.authenticate_service("test-admin", credential)
    with runtime.identity_scope(admin):
        for subject in ("kme-alice", "kme-bob"):
            runtime.grant_identity(subject, ["operator", "consumer"], ["keys:*"], source="service")
        runtime.grant_identity("sae-alice", ["consumer"], ["keys:kme-alice"], source="service")
        runtime.grant_identity("sae-bob", ["consumer"], ["keys:kme-bob"], source="service")
        pki = create_test_pki(tmp_path / "pki")
        pairs = [(str(uuid4()), secrets.token_bytes(32)) for _ in range(3)]
        with independent_pair(tmp_path / "kmes", pki, pairs,
                              {"experiment_id": "portable-test", "model_id": "emulated-only.v1"},
                              security_runtime=runtime) as (alice, bob):
            assert alice.pid != bob.pid and alice.pid != os.getpid()
            assert alice.security_configuration["identity_profile"] == "service"
            response = request_kme(alice.port, client_context(pki), "kme-alice", "POST", "sae-bob", "enc_keys",
                                   {}, delivery_token="portable-first")
            assert response["status"] == 200
            key_id = response["body"]["keys"][0]["key_ID"]
            matched = request_kme(bob.port, client_context(pki, "sae-bob"), "kme-bob", "POST", "sae-alice", "dec_keys",
                                  {"key_IDs": [{"key_ID": key_id}]})
            assert matched["status"] == 200
            assert matched["body"]["keys"][0]["key"] == response["body"]["keys"][0]["key"]
            alice.kill()
            alice.start()
            counts = alice.command("summary")["counts"]
            assert counts.get("available") == 2
            assert sum(counts.values()) == 3
            runtime.revoke_identity("sae-alice")
            assert request_kme(alice.port, client_context(pki), "kme-alice", "GET", "sae-bob", "status")["status"] == 403
