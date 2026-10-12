"""Actual mTLS admission into the source-neutral controller, without desktop."""
import pytest

from tests.test_portable_core_service import provisioned, serving  # noqa: F401
from tests.test_mixed_controller import configuration, demand, ready_records, record

from quantum_autopilot.service.client import CoreClient, CoreRequestError


def test_mixed_eleven_node_flow_over_authenticated_api(provisioned):
    config, runtime, _, _, _, _, auditor, operator = provisioned
    with serving(config, runtime) as server:
        writer = CoreClient(f"https://localhost:{server.server_port}", operator)
        reader = CoreClient(f"https://localhost:{server.server_port}", auditor)
        mixed = configuration(11)
        assert writer.mixed_configure(mixed)["status"] == "configured"
        assert writer.mixed_demand(demand("no-input", 11))["status"] == "blocked"
        records = ready_records(mixed)
        assert writer.mixed_ingest({"records": records})["status"] == "recorded"
        result = writer.mixed_demand(demand(count=11))
        assert result["status"] == "reserved" and len(result["route"]) == 11
        assert reader.mixed_status()["reservations"][-1]["quantum_result"] is None
        with pytest.raises(CoreRequestError) as forbidden:
            reader.mixed_demand(demand("auditor-demand", 11))
        assert forbidden.value.status == 403
        assert writer.mixed_release({"request_id": "demand-1"})["status"] == "released"
        assert runtime.audit.verify()["valid"]
        server.service._release_controller_io_resources()


def test_source_scopes_cannot_report_other_nodes_or_reconfigure(provisioned):
    config, runtime, administrator_runtime, administrator, pki, _, _, operator = provisioned
    scope = "workspace:" + config.state_root.as_posix() + "/nodes/N01"
    with administrator_runtime.identity_scope(administrator):
        administrator_runtime.grant_identity("node-one-source", ["operator"], [scope], source="certificate")
        leaf = pki.provision("node-one-source", kind="service", services=["dashboard"])
        source_context = pki.client_context(leaf)
    with serving(config, runtime) as server:
        writer = CoreClient(f"https://localhost:{server.server_port}", operator)
        source = CoreClient(f"https://localhost:{server.server_port}", source_context)
        mixed = configuration()
        writer.mixed_configure(mixed)
        first = record(mixed, "N01", 0, "session_started", {"configuration_id": "a" * 64, "verified": True})
        assert source.mixed_ingest({"records": [first]})["records"][0]["status"] == "accepted"
        second = record(mixed, "N02", 0, "session_started", {"configuration_id": "a" * 64, "verified": True})
        for call in (lambda: source.mixed_ingest({"records": [second]}),
                     lambda: source.mixed_configure(mixed),
                     lambda: source.mixed_demand(demand())):
            with pytest.raises(CoreRequestError) as denied:
                call()
            assert denied.value.status == 403
        server.service._release_controller_io_resources()


def test_revoked_ingestion_identity_and_bad_contract_do_not_mutate(provisioned):
    config, runtime, admin_runtime, admin, pki, audit_leaf, audit_context, operator = provisioned
    with serving(config, runtime) as server:
        writer = CoreClient(f"https://localhost:{server.server_port}", operator)
        mixed = configuration()
        writer.mixed_configure(mixed)
        with pytest.raises(CoreRequestError) as invalid:
            writer.mixed_ingest({"records": [], "node_id": "N01"})
        assert invalid.value.status == 422
        assert writer.mixed_status()["audit_origin_counts"] == {}
        with admin_runtime.identity_scope(admin):
            pki.revoke(audit_leaf["serial"])
        with pytest.raises(CoreRequestError) as revoked:
            CoreClient(f"https://localhost:{server.server_port}", audit_context).mixed_status()
        assert revoked.value.status == 401
        server.service._release_controller_io_resources()
