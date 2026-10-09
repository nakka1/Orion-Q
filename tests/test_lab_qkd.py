import json

import pytest

pytest.importorskip("cryptography", reason="optional lab extra supplies certificates and AES-GCM")

from quantum_autopilot.lab.qkd import QkdConfig, run_bb84  # noqa: E402 (optional dependency guard)
from quantum_autopilot.lab.kms import DemoKeyPool, KeyDeliveryError, run_qkd_demo  # noqa: E402


def test_bb84_reproducible_complete_processing_and_explicit_scope():
    first, second = run_bb84(), run_bb84()
    assert first.summary == second.summary
    assert first.delivery_material == second.delivery_material
    assert len(first.delivery_material) >= 256
    assert first.summary["demo_key_bits"] == len(first.delivery_material) * 8
    assert first.summary["certified_secret_bits"] == 0
    assert first.summary["reconciliation_verified"]
    assert "toeplitz_privacy_amplification" in first.summary["stages"]
    assert "delivery_material" not in repr(first)


@pytest.mark.parametrize("config", [QkdConfig(bit_error_probability=.3), QkdConfig(detector_efficiency=0)])
def test_nonsecure_or_unavailable_model_never_admits_key_material(config):
    result = run_bb84(config)
    assert result.delivery_material == b""
    assert result.summary["demo_key_bits"] == 0
    assert result.summary["abort_reason"]


@pytest.mark.parametrize("config", [QkdConfig(acquisition_s=float("nan")), QkdConfig(emitted_pulses=True),
                                    QkdConfig(detector_efficiency=2), QkdConfig(parameter_sample_fraction=1)])
def test_config_rejects_invalid_units_and_ranges(config):
    with pytest.raises(ValueError):
        run_bb84(config)


def test_atomic_dec_rejects_partial_and_duplicate_requests_without_consumption():
    session = run_bb84()
    pool = DemoKeyPool(session.delivery_material[:64], experiment_id="test", model_id="model")
    issued = pool.enc({"number": 2})["keys"]
    id0, id1 = (entry["key_ID"] for entry in issued)
    with pytest.raises(KeyDeliveryError) as duplicate:
        pool.dec({"key_IDs": [{"key_ID": id0}, {"key_ID": id0}]})
    assert duplicate.value.status == 400 and pool.slave_consumed == 0
    with pytest.raises(KeyDeliveryError):
        pool.dec({"key_IDs": [{"key_ID": id0}, {"key_ID": "00000000-0000-0000-0000-000000000000"}]})
    assert pool.slave_consumed == 0
    recovered = pool.dec({"key_IDs": [{"key_ID": id0}, {"key_ID": id1}]})
    assert recovered == {"keys": issued}
    assert pool.slave_consumed == 2
    with pytest.raises(KeyDeliveryError) as consumed:
        pool.dec({"key_IDs": [{"key_ID": id0}]})
    assert consumed.value.status == 400


def test_actual_https_mutual_certificates_consumers_and_failure_recovery(tmp_path):
    report = run_qkd_demo(tmp_path)
    target = tmp_path / "qkd_report.json"
    assert report["all_passed"], report["checks"]
    assert json.loads(target.read_text(encoding="utf-8")) == report
    serialized = target.read_text(encoding="utf-8")
    assert '"key"' not in serialized and '"key_ID"' not in serialized
    assert list(tmp_path.glob("qkd-pki-*")) == []
    assert report["delivery"]["master_consumed_keys"] == report["delivery"]["initial_keys"]
    assert report["delivery"]["slave_consumed_keys"] == 1
    assert report["delivery"]["buffer_remaining_keys"] == 0


def test_https_rejects_duplicate_json_fields_without_consuming_key(tmp_path):
    import http.client
    from quantum_autopilot.lab.kms import create_test_pki, client_context, serve_kme
    pki = create_test_pki(tmp_path)
    pool = DemoKeyPool(bytes(32), experiment_id="fixture", model_id="interface-fixture-only")
    with serve_kme(pool, pki, "kme-alice") as port:
        connection = http.client.HTTPSConnection("127.0.0.1", port, context=client_context(pki), timeout=5)
        try:
            connection.request("POST", "/api/v1/keys/sae-bob/enc_keys",
                               body=b'{"number":1,"number":2}', headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            assert response.status == 400
            response.read()
        finally:
            connection.close()
    assert pool.master_consumed == 0
